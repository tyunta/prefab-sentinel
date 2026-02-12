from __future__ import annotations

import argparse
import csv
import glob
import json
from pathlib import Path
from typing import Any


def _normalize_scope(scope: Any) -> str:
    return str(scope).replace("\\", "/")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="benchmark_regression_report",
        description="Compare baseline/latest benchmark JSON sets and detect regressions.",
    )
    parser.add_argument(
        "--baseline-inputs",
        nargs="+",
        required=True,
        help="Baseline benchmark JSON paths or glob patterns.",
    )
    parser.add_argument(
        "--latest-inputs",
        nargs="+",
        required=True,
        help="Latest benchmark JSON paths or glob patterns.",
    )
    parser.add_argument(
        "--avg-ratio-threshold",
        type=float,
        default=1.1,
        help="Regression threshold for avg ratio (latest/baseline).",
    )
    parser.add_argument(
        "--p90-ratio-threshold",
        type=float,
        default=1.1,
        help="Regression threshold for p90 ratio (latest/baseline).",
    )
    parser.add_argument(
        "--min-absolute-delta-sec",
        type=float,
        default=0.0,
        help="Minimum delta seconds required to classify as regression/improvement.",
    )
    parser.add_argument(
        "--baseline-pinning-file",
        default=None,
        help=(
            "Optional JSON file mapping scope -> baseline JSON path. "
            "When provided, pinned scopes override auto-selected baseline entries."
        ),
    )
    parser.add_argument(
        "--sort-by",
        choices=("scope", "avg_ratio", "p90_ratio"),
        default="avg_ratio",
        help="Sort key for comparison rows.",
    )
    parser.add_argument(
        "--sort-order",
        choices=("asc", "desc"),
        default="desc",
        help="Sort order for comparison rows.",
    )
    parser.add_argument(
        "--alerts-only",
        action="store_true",
        help="Print compact regression alert lines instead of full JSON payload.",
    )
    parser.add_argument(
        "--fail-on-regression",
        action="store_true",
        help="Return non-zero exit code when regressions are detected.",
    )
    parser.add_argument("--out-json", default=None, help="Optional JSON output path.")
    parser.add_argument("--out-csv", default=None, help="Optional CSV output path.")
    parser.add_argument(
        "--out-md",
        default=None,
        help="Optional Markdown summary output path.",
    )
    parser.add_argument(
        "--out-csv-append",
        action="store_true",
        help="Append rows to --out-csv when the file already exists.",
    )
    return parser


def _expand_inputs(patterns: list[str]) -> list[Path]:
    paths: set[Path] = set()
    for pattern in patterns:
        candidate_patterns = [pattern]
        if "/" in pattern or "\\" in pattern:
            candidate_patterns.append(pattern.replace("/", "\\"))
            candidate_patterns.append(pattern.replace("\\", "/"))

        matched: list[str] = []
        for candidate in candidate_patterns:
            matched.extend(glob.glob(candidate))
        matched = sorted(set(matched))

        if matched:
            for entry in matched:
                paths.add(Path(entry))
        else:
            paths.add(Path(pattern))
    return sorted(path.resolve() for path in paths if path.exists())


def _to_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _latest_sort_key(summary: dict[str, Any], source: Path) -> tuple[str, str]:
    return str(summary.get("generated_at_utc", "")), str(source)


def _pick_latest_by_scope(paths: list[Path]) -> dict[str, tuple[Path, dict[str, Any]]]:
    latest_by_scope: dict[str, tuple[tuple[str, str], Path, dict[str, Any]]] = {}
    for path in paths:
        payload = json.loads(path.read_text(encoding="utf-8"))
        scope = _normalize_scope(payload.get("scope", ""))
        if not scope:
            continue

        candidate_key = _latest_sort_key(payload, path)
        selected = latest_by_scope.get(scope)
        if selected is None or candidate_key > selected[0]:
            latest_by_scope[scope] = (candidate_key, path, payload)

    return {scope: (value[1], value[2]) for scope, value in latest_by_scope.items()}


def _load_baseline_pinning(path: Path) -> dict[str, Path]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("baseline pinning file root must be an object")

    mapping: dict[str, Path] = {}
    for raw_scope, raw_source in payload.items():
        scope = _normalize_scope(raw_scope)
        if not scope:
            continue
        source_path = Path(str(raw_source))
        if not source_path.is_absolute():
            source_path = path.parent / source_path
        mapping[scope] = source_path.resolve()
    return mapping


def _apply_baseline_pinning(
    baseline_map: dict[str, tuple[Path, dict[str, Any]]],
    pinning_map: dict[str, Path],
) -> tuple[dict[str, tuple[Path, dict[str, Any]]], list[str]]:
    applied: list[str] = []
    for scope, source_path in pinning_map.items():
        if not source_path.exists():
            raise FileNotFoundError(f"Pinned baseline file not found for scope '{scope}': {source_path}")
        payload = json.loads(source_path.read_text(encoding="utf-8"))
        baseline_map[scope] = (source_path, payload)
        applied.append(scope)
    return baseline_map, sorted(applied)


def _compute_delta_ratio(
    baseline_value: float | None,
    latest_value: float | None,
) -> tuple[float | None, float | None]:
    if baseline_value is None or latest_value is None:
        return None, None

    delta = round(latest_value - baseline_value, 6)
    if baseline_value == 0:
        return delta, None
    ratio = round(latest_value / baseline_value, 6)
    return delta, ratio


def _classify_status(
    avg_delta: float | None,
    avg_ratio: float | None,
    p90_delta: float | None,
    p90_ratio: float | None,
    avg_ratio_threshold: float,
    p90_ratio_threshold: float,
    min_absolute_delta_sec: float,
) -> str:
    is_regressed = False
    is_improved = False

    if avg_delta is not None and avg_ratio is not None:
        if avg_ratio >= avg_ratio_threshold and avg_delta >= min_absolute_delta_sec:
            is_regressed = True
        if avg_ratio <= (1.0 / avg_ratio_threshold) and avg_delta <= -min_absolute_delta_sec:
            is_improved = True

    if p90_delta is not None and p90_ratio is not None:
        if p90_ratio >= p90_ratio_threshold and p90_delta >= min_absolute_delta_sec:
            is_regressed = True
        if p90_ratio <= (1.0 / p90_ratio_threshold) and p90_delta <= -min_absolute_delta_sec:
            is_improved = True

    if is_regressed:
        return "regressed"
    if is_improved:
        return "improved"
    return "stable"


def _compare_scope(
    scope: str,
    baseline_entry: tuple[Path, dict[str, Any]],
    latest_entry: tuple[Path, dict[str, Any]],
    avg_ratio_threshold: float,
    p90_ratio_threshold: float,
    min_absolute_delta_sec: float,
) -> dict[str, Any]:
    baseline_path, baseline_payload = baseline_entry
    latest_path, latest_payload = latest_entry

    baseline_avg = _to_float(baseline_payload.get("seconds", {}).get("avg"))
    latest_avg = _to_float(latest_payload.get("seconds", {}).get("avg"))
    avg_delta, avg_ratio = _compute_delta_ratio(baseline_avg, latest_avg)

    baseline_p90 = _to_float(baseline_payload.get("seconds", {}).get("p90"))
    latest_p90 = _to_float(latest_payload.get("seconds", {}).get("p90"))
    p90_delta, p90_ratio = _compute_delta_ratio(baseline_p90, latest_p90)

    status = _classify_status(
        avg_delta=avg_delta,
        avg_ratio=avg_ratio,
        p90_delta=p90_delta,
        p90_ratio=p90_ratio,
        avg_ratio_threshold=avg_ratio_threshold,
        p90_ratio_threshold=p90_ratio_threshold,
        min_absolute_delta_sec=min_absolute_delta_sec,
    )

    return {
        "scope": scope,
        "status": status,
        "baseline_source": str(baseline_path),
        "latest_source": str(latest_path),
        "baseline_avg_sec": baseline_avg,
        "latest_avg_sec": latest_avg,
        "avg_delta_sec": avg_delta,
        "avg_ratio": avg_ratio,
        "baseline_p90_sec": baseline_p90,
        "latest_p90_sec": latest_p90,
        "p90_delta_sec": p90_delta,
        "p90_ratio": p90_ratio,
        "latest_severity": str(
            latest_payload.get("validate_result", {}).get("severity", "")
        ),
    }


def _sort_results(
    results: list[dict[str, Any]],
    sort_by: str,
    sort_order: str,
) -> list[dict[str, Any]]:
    reverse = sort_order == "desc"
    if sort_by == "scope":
        return sorted(results, key=lambda item: str(item.get("scope", "")), reverse=reverse)

    field = "avg_ratio" if sort_by == "avg_ratio" else "p90_ratio"
    if reverse:
        return sorted(
            results,
            key=lambda item: (
                item.get(field) if item.get(field) is not None else float("-inf")
            ),
            reverse=True,
        )
    return sorted(
        results,
        key=lambda item: item.get(field) if item.get(field) is not None else float("inf"),
    )


def _comparison_to_csv_row(result: dict[str, Any]) -> list[str]:
    return [
        str(result.get("scope", "")),
        str(result.get("status", "")),
        str(result.get("baseline_source", "")),
        str(result.get("latest_source", "")),
        str(result.get("baseline_avg_sec", "")),
        str(result.get("latest_avg_sec", "")),
        str(result.get("avg_delta_sec", "")),
        str(result.get("avg_ratio", "")),
        str(result.get("baseline_p90_sec", "")),
        str(result.get("latest_p90_sec", "")),
        str(result.get("p90_delta_sec", "")),
        str(result.get("p90_ratio", "")),
        str(result.get("latest_severity", "")),
    ]


def _write_comparison_csv(
    path: Path, results: list[dict[str, Any]], append: bool = False
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    mode = "a" if append and path.exists() else "w"
    needs_header = mode == "w"
    with path.open(mode, encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        if needs_header:
            writer.writerow(
                [
                    "scope",
                    "status",
                    "baseline_source",
                    "latest_source",
                    "baseline_avg_sec",
                    "latest_avg_sec",
                    "avg_delta_sec",
                    "avg_ratio",
                    "baseline_p90_sec",
                    "latest_p90_sec",
                    "p90_delta_sec",
                    "p90_ratio",
                    "latest_severity",
                ]
            )
        writer.writerows(_comparison_to_csv_row(result) for result in results)


def _render_alert_lines(results: list[dict[str, Any]]) -> list[str]:
    regressions = [result for result in results if result.get("status") == "regressed"]
    if not regressions:
        return ["NO_REGRESSIONS"]
    return [
        "REGRESSION "
        f"scope={result.get('scope', '')} "
        f"avg_ratio={result.get('avg_ratio', '')} "
        f"p90_ratio={result.get('p90_ratio', '')} "
        f"baseline={result.get('baseline_source', '')} "
        f"latest={result.get('latest_source', '')}"
        for result in regressions
    ]


def _render_markdown_summary(payload: dict[str, Any]) -> str:
    thresholds = payload.get("thresholds", {})
    results = payload.get("results", [])
    regressed_scopes = payload.get("regressed_scopes", [])

    stable_count = sum(1 for item in results if item.get("status") == "stable")
    improved_count = sum(1 for item in results if item.get("status") == "improved")
    regressed_count = sum(1 for item in results if item.get("status") == "regressed")

    def _fmt(value: Any) -> str:
        if value is None:
            return "-"
        return str(value)

    lines = [
        "# Benchmark Regression Summary",
        f"- Baseline Files: {payload.get('baseline_file_count', 0)}",
        f"- Latest Files: {payload.get('latest_file_count', 0)}",
        f"- Compared Scopes: {payload.get('compared_scope_count', 0)}",
        f"- Regressed: {regressed_count}",
        f"- Improved: {improved_count}",
        f"- Stable: {stable_count}",
        (
            "- Thresholds: "
            f"avg_ratio={thresholds.get('avg_ratio_threshold', '')}, "
            f"p90_ratio={thresholds.get('p90_ratio_threshold', '')}, "
            f"min_delta_sec={thresholds.get('min_absolute_delta_sec', '')}"
        ),
        "",
        "## Regressions",
    ]

    if regressed_scopes:
        lines.extend([f"- `{scope}`" for scope in regressed_scopes])
    else:
        lines.append("- None")

    lines.extend(
        [
            "",
            "## Scope Results",
            "| Scope | Status | Avg Ratio | P90 Ratio | Avg Delta(s) | P90 Delta(s) |",
            "| --- | --- | ---: | ---: | ---: | ---: |",
        ]
    )
    for result in results:
        lines.append(
            "| "
            f"{result.get('scope', '')} | "
            f"{result.get('status', '')} | "
            f"{_fmt(result.get('avg_ratio'))} | "
            f"{_fmt(result.get('p90_ratio'))} | "
            f"{_fmt(result.get('avg_delta_sec'))} | "
            f"{_fmt(result.get('p90_delta_sec'))} |"
        )
    lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    baseline_paths = _expand_inputs(args.baseline_inputs)
    latest_paths = _expand_inputs(args.latest_inputs)
    if not baseline_paths:
        parser.error("No baseline JSON files were found.")
    if not latest_paths:
        parser.error("No latest JSON files were found.")

    baseline_map = _pick_latest_by_scope(baseline_paths)
    baseline_pinning_applied_scopes: list[str] = []
    if args.baseline_pinning_file:
        pinning_file = Path(args.baseline_pinning_file).resolve()
        try:
            pinning_map = _load_baseline_pinning(pinning_file)
            baseline_map, baseline_pinning_applied_scopes = _apply_baseline_pinning(
                baseline_map, pinning_map
            )
        except (OSError, ValueError, json.JSONDecodeError, FileNotFoundError) as exc:
            parser.error(f"Failed to load --baseline-pinning-file: {exc}")

    latest_map = _pick_latest_by_scope(latest_paths)

    baseline_scopes = set(baseline_map)
    latest_scopes = set(latest_map)
    compared_scopes = sorted(baseline_scopes & latest_scopes)

    results = [
        _compare_scope(
            scope=scope,
            baseline_entry=baseline_map[scope],
            latest_entry=latest_map[scope],
            avg_ratio_threshold=args.avg_ratio_threshold,
            p90_ratio_threshold=args.p90_ratio_threshold,
            min_absolute_delta_sec=args.min_absolute_delta_sec,
        )
        for scope in compared_scopes
    ]
    results = _sort_results(results, sort_by=args.sort_by, sort_order=args.sort_order)

    payload = {
        "baseline_file_count": len(baseline_paths),
        "latest_file_count": len(latest_paths),
        "baseline_scope_count": len(baseline_scopes),
        "latest_scope_count": len(latest_scopes),
        "compared_scope_count": len(compared_scopes),
        "baseline_pinning_file": args.baseline_pinning_file,
        "baseline_pinning_applied_scopes": baseline_pinning_applied_scopes,
        "missing_in_latest_scopes": sorted(baseline_scopes - latest_scopes),
        "new_in_latest_scopes": sorted(latest_scopes - baseline_scopes),
        "thresholds": {
            "avg_ratio_threshold": args.avg_ratio_threshold,
            "p90_ratio_threshold": args.p90_ratio_threshold,
            "min_absolute_delta_sec": args.min_absolute_delta_sec,
        },
        "regressed_scopes": [
            result["scope"] for result in results if result.get("status") == "regressed"
        ],
        "results": results,
    }

    output = json.dumps(payload, ensure_ascii=False, indent=2)
    if args.alerts_only:
        for line in _render_alert_lines(results):
            print(line)
    else:
        print(output)

    if args.out_json:
        out_json = Path(args.out_json)
        out_json.parent.mkdir(parents=True, exist_ok=True)
        out_json.write_text(output + "\n", encoding="utf-8")

    if args.out_csv:
        _write_comparison_csv(
            Path(args.out_csv), results, append=args.out_csv_append
        )
    if args.out_md:
        out_md = Path(args.out_md)
        out_md.parent.mkdir(parents=True, exist_ok=True)
        out_md.write_text(_render_markdown_summary(payload), encoding="utf-8")

    if args.fail_on_regression and payload["regressed_scopes"]:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
