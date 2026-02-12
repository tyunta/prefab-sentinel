from __future__ import annotations

import argparse
import csv
import glob
import json
from pathlib import Path
from typing import Any

SEVERITY_ORDER = ("critical", "error", "warning", "info", "unknown")


def _normalize_scope(scope: Any) -> str:
    return str(scope).replace("\\", "/")


def _normalize_severity(severity: Any) -> str:
    normalized = str(severity).strip().lower()
    if normalized in {"info", "warning", "error", "critical"}:
        return normalized
    return "unknown"


def _to_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="benchmark_history_to_csv",
        description="Convert benchmark summary JSON files into a single CSV.",
    )
    parser.add_argument(
        "--inputs",
        nargs="+",
        required=True,
        help="Input JSON paths or glob patterns.",
    )
    parser.add_argument(
        "--scope-contains",
        default=None,
        help="Only include rows where scope contains this string.",
    )
    parser.add_argument(
        "--severity",
        action="append",
        choices=("info", "warning", "error", "critical"),
        default=[],
        help="Only include rows whose validate_result.severity matches (repeatable).",
    )
    parser.add_argument(
        "--sort-by",
        choices=("source", "scope", "avg_sec"),
        default="source",
        help="Sort key for output rows.",
    )
    parser.add_argument(
        "--sort-order",
        choices=("asc", "desc"),
        default="asc",
        help="Sort order for output rows.",
    )
    parser.add_argument(
        "--include-date-column",
        action="store_true",
        help="Include generated_date_utc column when generated_at_utc exists in summaries.",
    )
    parser.add_argument(
        "--generated-date-prefix",
        default=None,
        help="Only include rows whose generated_at_utc starts with this prefix (e.g. 2026-02-12).",
    )
    parser.add_argument(
        "--top-slowest",
        type=int,
        default=0,
        help="Keep only top N slowest rows by avg_sec before final sorting (0: disabled).",
    )
    parser.add_argument(
        "--min-p90",
        type=float,
        default=None,
        help="Only include rows whose p90_sec is greater than or equal to this value.",
    )
    parser.add_argument(
        "--latest-per-scope",
        action="store_true",
        help="Keep only the latest generated_at_utc row for each scope.",
    )
    parser.add_argument(
        "--split-by-severity",
        action="store_true",
        help="Write additional CSV files split by severity suffix (_error.csv, etc.).",
    )
    parser.add_argument(
        "--out-md",
        default=None,
        help="Optional Markdown snapshot summary output path.",
    )
    parser.add_argument("--out", required=True, help="Output CSV path.")
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


def _summary_to_row(
    source: Path,
    summary: dict[str, Any],
    include_date_column: bool = False,
) -> list[str]:
    seconds = summary.get("seconds", {})
    validate = summary.get("validate_result", {})
    row = [
        str(source),
        _normalize_scope(summary.get("scope", "")),
        str(summary.get("generated_at_utc", "")),
        str(summary.get("warmup_runs", "")),
        str(summary.get("runs", "")),
        str(seconds.get("avg", "")),
        str(seconds.get("p50", "")),
        str(seconds.get("p90", "")),
        str(seconds.get("min", "")),
        str(seconds.get("max", "")),
        str(validate.get("success", "")),
        str(validate.get("severity", "")),
        str(validate.get("code", "")),
    ]
    if include_date_column:
        generated_at = str(summary.get("generated_at_utc", ""))
        row.append(generated_at[:10] if generated_at else "")
    return row


def _is_benchmark_summary(summary: dict[str, Any]) -> bool:
    scope = _normalize_scope(summary.get("scope", ""))
    seconds = summary.get("seconds")
    validate = summary.get("validate_result")
    if not scope:
        return False
    if not isinstance(seconds, dict) or not isinstance(validate, dict):
        return False
    if _to_float(seconds.get("avg")) is None:
        return False
    return True


def _matches_filters(
    summary: dict[str, Any],
    scope_contains: str | None,
    severities: set[str],
    generated_date_prefix: str | None,
    min_p90: float | None,
) -> bool:
    scope = _normalize_scope(summary.get("scope", ""))
    if scope_contains and scope_contains not in scope:
        return False

    if generated_date_prefix:
        generated_at = str(summary.get("generated_at_utc", ""))
        if not generated_at.startswith(generated_date_prefix):
            return False

    if severities:
        severity = str(summary.get("validate_result", {}).get("severity", "")).lower()
        if severity not in severities:
            return False

    if min_p90 is not None:
        raw_p90 = summary.get("seconds", {}).get("p90")
        try:
            p90_value = float(raw_p90)
        except (TypeError, ValueError):
            return False
        if p90_value < min_p90:
            return False

    return True


def _sort_records(
    records: list[tuple[Path, dict[str, Any]]],
    sort_by: str,
    sort_order: str,
) -> list[tuple[Path, dict[str, Any]]]:
    reverse = sort_order == "desc"
    if sort_by == "source":
        return sorted(records, key=lambda item: str(item[0]), reverse=reverse)
    if sort_by == "scope":
        return sorted(
            records,
            key=lambda item: _normalize_scope(item[1].get("scope", "")),
            reverse=reverse,
        )

    def avg_key(item: tuple[Path, dict[str, Any]]) -> tuple[bool, float]:
        value = item[1].get("seconds", {}).get("avg", "")
        try:
            return False, float(value)
        except (TypeError, ValueError):
            return True, 0.0

    return sorted(records, key=avg_key, reverse=reverse)


def _pick_top_slowest(
    records: list[tuple[Path, dict[str, Any]]], top_n: int
) -> list[tuple[Path, dict[str, Any]]]:
    if top_n <= 0 or top_n >= len(records):
        return records
    return _sort_records(records, sort_by="avg_sec", sort_order="desc")[:top_n]


def _pick_latest_per_scope(
    records: list[tuple[Path, dict[str, Any]]],
) -> list[tuple[Path, dict[str, Any]]]:
    latest_by_scope: dict[str, tuple[str, str, tuple[Path, dict[str, Any]]]] = {}
    for path, payload in records:
        scope = _normalize_scope(payload.get("scope", ""))
        generated_at = str(payload.get("generated_at_utc", ""))
        source = str(path)
        selected = latest_by_scope.get(scope)
        candidate_key = (generated_at, source)
        if selected is None or candidate_key > (selected[0], selected[1]):
            latest_by_scope[scope] = (generated_at, source, (path, payload))
    return [item[2] for item in latest_by_scope.values()]


def _build_split_output_path(base_out: Path, severity: str) -> Path:
    return base_out.with_name(f"{base_out.stem}_{severity}{base_out.suffix}")


def _group_rows_by_severity(
    records: list[tuple[Path, dict[str, Any]]], rows: list[list[str]]
) -> dict[str, list[list[str]]]:
    grouped: dict[str, list[list[str]]] = {}
    for (_, payload), row in zip(records, rows):
        severity = _normalize_severity(payload.get("validate_result", {}).get("severity"))
        grouped.setdefault(severity, []).append(row)
    return grouped


def _write_csv(path: Path, header: list[str], rows: list[list[str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(header)
        writer.writerows(rows)


def _render_markdown_summary(records: list[tuple[Path, dict[str, Any]]]) -> str:
    lines = [
        "# Benchmark Trend Snapshot",
        "",
        f"- Rows: {len(records)}",
    ]

    generated = [
        str(payload.get("generated_at_utc", ""))
        for _, payload in records
        if payload.get("generated_at_utc")
    ]
    if generated:
        lines.append(f"- Generated Range (UTC): {min(generated)} .. {max(generated)}")
    else:
        lines.append("- Generated Range (UTC): n/a")

    severity_counts: dict[str, int] = {}
    for _, payload in records:
        severity = _normalize_severity(payload.get("validate_result", {}).get("severity"))
        severity_counts[severity] = severity_counts.get(severity, 0) + 1

    lines.extend(
        [
            "",
            "## Severity Counts",
            "| severity | count |",
            "| --- | ---: |",
        ]
    )
    for severity in SEVERITY_ORDER:
        count = severity_counts.get(severity, 0)
        if count > 0:
            lines.append(f"| {severity} | {count} |")
    if not severity_counts:
        lines.append("| n/a | 0 |")

    lines.extend(
        [
            "",
            "## Top Avg Sec (Top 5)",
            "| scope | avg_sec | p90_sec | severity | source |",
            "| --- | ---: | ---: | --- | --- |",
        ]
    )
    top_records = _sort_records(records, sort_by="avg_sec", sort_order="desc")[:5]
    for path, payload in top_records:
        seconds = payload.get("seconds", {})
        severity = _normalize_severity(payload.get("validate_result", {}).get("severity"))
        lines.append(
            f"| {_normalize_scope(payload.get('scope', ''))} | "
            f"{seconds.get('avg', '')} | {seconds.get('p90', '')} | "
            f"{severity} | {path} |"
        )
    if not top_records:
        lines.append("| n/a |  |  |  |  |")

    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    input_paths = _expand_inputs(args.inputs)
    if not input_paths:
        parser.error("No input JSON files were found.")

    severities = {severity.lower() for severity in args.severity}
    records: list[tuple[Path, dict[str, Any]]] = []
    for path in input_paths:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not _is_benchmark_summary(payload):
            continue
        if not _matches_filters(
            payload,
            args.scope_contains,
            severities,
            args.generated_date_prefix,
            args.min_p90,
        ):
            continue
        records.append((path, payload))

    if args.latest_per_scope:
        records = _pick_latest_per_scope(records)
    records = _pick_top_slowest(records, args.top_slowest)
    records = _sort_records(records, sort_by=args.sort_by, sort_order=args.sort_order)
    rows = [
        _summary_to_row(
            path, payload, include_date_column=args.include_date_column
        )
        for path, payload in records
    ]

    out = Path(args.out)
    header = [
        "source",
        "scope",
        "generated_at_utc",
        "warmup_runs",
        "runs",
        "avg_sec",
        "p50_sec",
        "p90_sec",
        "min_sec",
        "max_sec",
        "success",
        "severity",
        "code",
    ]
    if args.include_date_column:
        header.append("generated_date_utc")

    _write_csv(out, header, rows)
    print(out)

    if args.split_by_severity:
        grouped_rows = _group_rows_by_severity(records, rows)
        for severity in SEVERITY_ORDER:
            severity_rows = grouped_rows.get(severity)
            if not severity_rows:
                continue
            severity_out = _build_split_output_path(out, severity)
            _write_csv(severity_out, header, severity_rows)
            print(severity_out)

    if args.out_md:
        out_md = Path(args.out_md)
        out_md.parent.mkdir(parents=True, exist_ok=True)
        out_md.write_text(_render_markdown_summary(records) + "\n", encoding="utf-8")
        print(out_md)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
