from __future__ import annotations

import argparse
import csv
from datetime import datetime, timezone
import json
import math
import statistics
import subprocess
import sys
import time
from pathlib import Path
from typing import Any


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="benchmark_refs",
        description="Benchmark unitytool validate refs execution time.",
    )
    parser.add_argument("--scope", required=True, help="Scope path for validate refs.")
    parser.add_argument("--runs", type=int, default=3, help="Number of benchmark runs.")
    parser.add_argument(
        "--warmup-runs",
        type=int,
        default=0,
        help="Warm-up run count (excluded from measured summary).",
    )
    parser.add_argument(
        "--exclude",
        action="append",
        default=[],
        help="Glob-style path pattern to exclude from scan (can be specified multiple times).",
    )
    parser.add_argument(
        "--ignore-guid",
        action="append",
        default=[],
        help="Missing-asset GUID to ignore during validation (can be specified multiple times).",
    )
    parser.add_argument(
        "--ignore-guid-file",
        default=None,
        help="UTF-8 text file with one missing-asset GUID per line (# comment supported).",
    )
    parser.add_argument(
        "--out",
        default=None,
        help="Optional output JSON path for benchmark summary.",
    )
    parser.add_argument(
        "--out-csv",
        default=None,
        help="Optional output CSV path for benchmark summary.",
    )
    parser.add_argument(
        "--csv-append",
        action="store_true",
        help="Append a row when --out-csv already exists (default: overwrite).",
    )
    parser.add_argument(
        "--include-generated-date",
        action="store_true",
        help="Include generated_at_utc in JSON and CSV outputs.",
    )
    return parser


def _build_command(args: argparse.Namespace) -> list[str]:
    cmd = [
        sys.executable,
        "-m",
        "unitytool",
        "validate",
        "refs",
        "--scope",
        args.scope,
        "--format",
        "json",
    ]
    for pattern in args.exclude:
        cmd.extend(["--exclude", pattern])
    for guid in args.ignore_guid:
        cmd.extend(["--ignore-guid", guid])
    if args.ignore_guid_file:
        cmd.extend(["--ignore-guid-file", args.ignore_guid_file])
    return cmd


def _run_once(command: list[str]) -> tuple[float, dict[str, Any]]:
    started = time.perf_counter()
    proc = subprocess.run(
        command,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    elapsed = time.perf_counter() - started
    if proc.returncode != 0:
        raise RuntimeError(
            f"Command failed with exit {proc.returncode}: {proc.stderr.strip()}"
        )
    try:
        payload = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError("validate refs did not return valid JSON output.") from exc
    return elapsed, payload


def _summary_to_csv_row(summary: dict[str, Any]) -> list[str]:
    seconds = summary.get("seconds", {})
    validate = summary.get("validate_result", {})
    return [
        str(summary.get("scope", "")),
        str(summary.get("generated_at_utc", "")),
        str(summary.get("warmup_runs", "")),
        str(summary.get("runs", "")),
        str(seconds.get("avg", "")),
        str(seconds.get("min", "")),
        str(seconds.get("max", "")),
        str(seconds.get("p50", "")),
        str(seconds.get("p90", "")),
        str(validate.get("success", "")),
        str(validate.get("severity", "")),
        str(validate.get("code", "")),
    ]


def _write_summary_csv(path: Path, summary: dict[str, Any], append: bool) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    mode = "a" if append and path.exists() else "w"
    needs_header = mode == "w"

    with path.open(mode, encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        if needs_header:
            writer.writerow(
                [
                    "scope",
                    "generated_at_utc",
                    "warmup_runs",
                    "runs",
                    "avg_sec",
                    "min_sec",
                    "max_sec",
                    "p50_sec",
                    "p90_sec",
                    "success",
                    "severity",
                    "code",
                ]
            )
        writer.writerow(_summary_to_csv_row(summary))


def _normalize_run_counts(runs: int, warmup_runs: int) -> tuple[int, int]:
    return max(1, runs), max(0, warmup_runs)


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        raise ValueError("values must not be empty")
    if percentile <= 0:
        return min(values)
    if percentile >= 1:
        return max(values)

    sorted_values = sorted(values)
    rank = (len(sorted_values) - 1) * percentile
    lower_idx = math.floor(rank)
    upper_idx = math.ceil(rank)
    if lower_idx == upper_idx:
        return sorted_values[lower_idx]
    lower = sorted_values[lower_idx]
    upper = sorted_values[upper_idx]
    return lower + (upper - lower) * (rank - lower_idx)


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    runs, warmup_runs = _normalize_run_counts(args.runs, args.warmup_runs)
    command = _build_command(args)

    for _ in range(warmup_runs):
        _run_once(command)

    elapsed_list: list[float] = []
    last_payload: dict[str, Any] = {}
    for _ in range(runs):
        elapsed, payload = _run_once(command)
        elapsed_list.append(elapsed)
        last_payload = payload

    summary: dict[str, Any] = {
        "scope": args.scope,
        "warmup_runs": warmup_runs,
        "runs": runs,
        "seconds": {
            "avg": round(statistics.fmean(elapsed_list), 6),
            "min": round(min(elapsed_list), 6),
            "max": round(max(elapsed_list), 6),
            "p50": round(_percentile(elapsed_list, 0.5), 6),
            "p90": round(_percentile(elapsed_list, 0.9), 6),
            "all": [round(value, 6) for value in elapsed_list],
        },
        "validate_result": {
            "success": last_payload.get("success"),
            "severity": last_payload.get("severity"),
            "code": last_payload.get("code"),
        },
        "command": command,
    }
    if args.include_generated_date:
        summary["generated_at_utc"] = datetime.now(timezone.utc).replace(
            microsecond=0
        ).isoformat().replace("+00:00", "Z")

    output = json.dumps(summary, ensure_ascii=False, indent=2)
    print(output)

    if args.out:
        path = Path(args.out)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(output + "\n", encoding="utf-8")

    if args.out_csv:
        _write_summary_csv(Path(args.out_csv), summary, append=args.csv_append)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
