from __future__ import annotations

import argparse
import csv
import glob
import json
import math
from pathlib import Path
from typing import Any


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="smoke_summary_to_csv",
        description="Convert bridge_smoke_samples summary JSON files into a decision table CSV.",
    )
    parser.add_argument(
        "--inputs",
        nargs="+",
        required=True,
        help="Input summary JSON paths or glob patterns.",
    )
    parser.add_argument(
        "--target",
        action="append",
        choices=("avatar", "world"),
        default=[],
        help="Only include rows for selected targets (repeatable).",
    )
    parser.add_argument(
        "--matched-only",
        action="store_true",
        help="Only include rows where matched_expectation is true.",
    )
    parser.add_argument(
        "--duration-percentile",
        type=float,
        default=90.0,
        help="Percentile used in markdown decision table (0-100, default: 90).",
    )
    parser.add_argument(
        "--out-md",
        default=None,
        help="Optional markdown decision table output path.",
    )
    parser.add_argument(
        "--out",
        required=True,
        help="Output CSV path.",
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


def _to_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _is_smoke_batch_summary(payload: Any) -> bool:
    if not isinstance(payload, dict):
        return False
    if payload.get("code") not in {"SMOKE_BATCH_OK", "SMOKE_BATCH_FAILED"}:
        return False
    data = payload.get("data")
    if not isinstance(data, dict):
        return False
    cases = data.get("cases")
    if not isinstance(cases, list):
        return False
    return True


def _case_to_row(source: Path, payload: dict[str, Any], case: dict[str, Any]) -> dict[str, Any]:
    return {
        "source": str(source),
        "batch_success": bool(payload.get("success", False)),
        "batch_severity": str(payload.get("severity", "")),
        "target": str(case.get("name", "")),
        "matched_expectation": bool(case.get("matched_expectation", False)),
        "attempts": _to_int(case.get("attempts")),
        "duration_sec": _to_float(case.get("duration_sec")),
        "unity_timeout_sec": _to_int(case.get("unity_timeout_sec")),
        "exit_code": _to_int(case.get("exit_code")),
        "response_code": str(case.get("response_code", "")),
        "response_severity": str(case.get("response_severity", "")),
        "response_path": str(case.get("response_path", "")),
        "unity_log_file": str(case.get("unity_log_file", "")),
        "plan": str(case.get("plan", "")),
        "project_path": str(case.get("project_path", "")),
    }


def _percentile(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    if percentile <= 0:
        return ordered[0]
    if percentile >= 100:
        return ordered[-1]

    position = (percentile / 100.0) * (len(ordered) - 1)
    lower_index = int(math.floor(position))
    upper_index = int(math.ceil(position))
    lower = ordered[lower_index]
    upper = ordered[upper_index]
    if lower_index == upper_index:
        return lower
    ratio = position - lower_index
    return lower + (upper - lower) * ratio


def _build_target_stats(
    rows: list[dict[str, Any]], duration_percentile: float
) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        target = str(row.get("target", ""))
        grouped.setdefault(target, []).append(row)

    stats: list[dict[str, Any]] = []
    for target in sorted(grouped):
        target_rows = grouped[target]
        durations = [
            value
            for value in (row.get("duration_sec") for row in target_rows)
            if isinstance(value, float)
        ]
        attempts = [
            value
            for value in (row.get("attempts") for row in target_rows)
            if isinstance(value, int)
        ]
        timeouts = [
            value
            for value in (row.get("unity_timeout_sec") for row in target_rows)
            if isinstance(value, int)
        ]
        failures = [row for row in target_rows if not bool(row.get("matched_expectation", False))]
        duration_avg = sum(durations) / len(durations) if durations else None
        stats.append(
            {
                "target": target,
                "runs": len(target_rows),
                "failures": len(failures),
                "attempts_max": max(attempts) if attempts else None,
                "duration_avg_sec": duration_avg,
                "duration_p_sec": _percentile(durations, duration_percentile),
                "duration_max_sec": max(durations) if durations else None,
                "timeout_max_sec": max(timeouts) if timeouts else None,
            }
        )
    return stats


def _write_csv(out_path: Path, header: list[str], rows: list[dict[str, Any]]) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=header)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def _render_markdown_summary(rows: list[dict[str, Any]], duration_percentile: float) -> str:
    percentile_label = f"duration_p{int(round(duration_percentile))}_sec"
    stats = _build_target_stats(rows, duration_percentile)
    lines = [
        "# Bridge Smoke Timeout Decision Table",
        "",
        f"- Cases: {len(rows)}",
        f"- Duration percentile: p{duration_percentile:g}",
        "",
        f"| target | runs | failures | attempts_max | duration_avg_sec | {percentile_label} | duration_max_sec | timeout_max_sec |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for item in stats:
        lines.append(
            "| {target} | {runs} | {failures} | {attempts_max} | {duration_avg_sec} | {duration_p_sec} | {duration_max_sec} | {timeout_max_sec} |".format(
                target=item.get("target", ""),
                runs=item.get("runs", 0),
                failures=item.get("failures", 0),
                attempts_max=item.get("attempts_max", ""),
                duration_avg_sec=item.get("duration_avg_sec", ""),
                duration_p_sec=item.get("duration_p_sec", ""),
                duration_max_sec=item.get("duration_max_sec", ""),
                timeout_max_sec=item.get("timeout_max_sec", ""),
            )
        )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.duration_percentile < 0.0 or args.duration_percentile > 100.0:
        parser.error("--duration-percentile must be in range 0..100.")

    input_paths = _expand_inputs(args.inputs)
    if not input_paths:
        parser.error("No input JSON files were found.")

    include_targets = {target for target in args.target}
    rows: list[dict[str, Any]] = []
    for source in input_paths:
        payload = json.loads(source.read_text(encoding="utf-8"))
        if not _is_smoke_batch_summary(payload):
            continue
        cases = payload.get("data", {}).get("cases", [])
        if not isinstance(cases, list):
            continue
        for case in cases:
            if not isinstance(case, dict):
                continue
            row = _case_to_row(source, payload, case)
            if include_targets and row["target"] not in include_targets:
                continue
            if args.matched_only and not row["matched_expectation"]:
                continue
            rows.append(row)

    if not rows:
        parser.error("No smoke case rows were available after filtering.")

    header = [
        "source",
        "batch_success",
        "batch_severity",
        "target",
        "matched_expectation",
        "attempts",
        "duration_sec",
        "unity_timeout_sec",
        "exit_code",
        "response_code",
        "response_severity",
        "response_path",
        "unity_log_file",
        "plan",
        "project_path",
    ]
    out_csv = Path(args.out)
    _write_csv(out_csv, header, rows)
    print(out_csv)

    if args.out_md:
        out_md = Path(args.out_md)
        out_md.parent.mkdir(parents=True, exist_ok=True)
        out_md.write_text(
            _render_markdown_summary(rows, args.duration_percentile) + "\n",
            encoding="utf-8",
        )
        print(out_md)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
