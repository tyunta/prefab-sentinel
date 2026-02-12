from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path
import subprocess
import sys

SCRIPT_DIR = Path(__file__).resolve().parent
BENCHMARK_REFS_SCRIPT = SCRIPT_DIR / "benchmark_refs.py"
BENCHMARK_HISTORY_SCRIPT = SCRIPT_DIR / "benchmark_history_to_csv.py"
BENCHMARK_REGRESSION_SCRIPT = SCRIPT_DIR / "benchmark_regression_report.py"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="benchmark_samples",
        description="Run benchmark_refs/history for sample avatar/world projects.",
    )
    parser.add_argument(
        "--targets",
        nargs="+",
        choices=("avatar", "world", "all"),
        default=["all"],
        help="Benchmark targets. 'all' expands to avatar + world.",
    )
    parser.add_argument(
        "--runs",
        type=int,
        default=1,
        help="Measured run count passed to benchmark_refs.py.",
    )
    parser.add_argument(
        "--warmup-runs",
        type=int,
        default=0,
        help="Warm-up run count passed to benchmark_refs.py.",
    )
    parser.add_argument(
        "--tag",
        default=None,
        help="Tag for output JSON filename bench_<tag>.json (default: current UTC timestamp).",
    )
    parser.add_argument(
        "--exclude",
        action="append",
        default=[],
        help="Exclude glob pattern forwarded to benchmark_refs.py (repeatable).",
    )
    parser.add_argument(
        "--ignore-guid",
        action="append",
        default=[],
        help="Missing-asset GUID to ignore, forwarded to benchmark_refs.py (repeatable).",
    )
    parser.add_argument(
        "--ignore-guid-file",
        default=None,
        help="Ignore GUID file path forwarded to benchmark_refs.py.",
    )
    parser.add_argument(
        "--history-generated-date-prefix",
        default=None,
        help="Optional generated_at_utc prefix for history CSV filtering.",
    )
    parser.add_argument(
        "--history-min-p90",
        type=float,
        default=None,
        help="Optional p90 threshold for history CSV filtering.",
    )
    parser.add_argument(
        "--history-latest-per-scope",
        action="store_true",
        help="Keep latest row per scope in generated history CSV.",
    )
    parser.add_argument(
        "--history-split-by-severity",
        action="store_true",
        help="Write additional severity-split CSV files in history generation.",
    )
    parser.add_argument(
        "--history-write-md",
        action="store_true",
        help="Write benchmark history markdown snapshot (benchmark_trend.md).",
    )
    parser.add_argument(
        "--run-regression",
        action="store_true",
        help="Run benchmark_regression_report.py after benchmark generation.",
    )
    parser.add_argument(
        "--regression-baseline-inputs",
        nargs="+",
        default=None,
        help="Baseline JSON paths/globs for regression comparison.",
    )
    parser.add_argument(
        "--regression-baseline-auto-latest",
        type=int,
        default=0,
        help="Auto-discover latest N benchmark JSON files in target config as regression baseline.",
    )
    parser.add_argument(
        "--regression-baseline-pinning-file",
        default=None,
        help="Optional scope->baseline mapping JSON passed to benchmark_regression_report.py.",
    )
    parser.add_argument(
        "--regression-avg-ratio-threshold",
        type=float,
        default=1.1,
        help="Regression threshold for avg ratio.",
    )
    parser.add_argument(
        "--regression-p90-ratio-threshold",
        type=float,
        default=1.1,
        help="Regression threshold for p90 ratio.",
    )
    parser.add_argument(
        "--regression-min-absolute-delta-sec",
        type=float,
        default=0.0,
        help="Minimum absolute delta sec for regression/improvement classification.",
    )
    parser.add_argument(
        "--regression-alerts-only",
        action="store_true",
        help="Pass --alerts-only to regression report for CI-friendly output.",
    )
    parser.add_argument(
        "--regression-fail-on-regression",
        action="store_true",
        help="Pass --fail-on-regression to regression report.",
    )
    parser.add_argument(
        "--regression-out-csv-append",
        action="store_true",
        help="Pass --out-csv-append to regression report.",
    )
    parser.add_argument(
        "--regression-out-md",
        action="store_true",
        help="Write benchmark_regression.md via regression report --out-md.",
    )
    parser.add_argument(
        "--no-history",
        action="store_false",
        dest="generate_history",
        help="Skip benchmark_history_to_csv.py generation.",
    )
    parser.add_argument(
        "--no-csv-append",
        action="store_false",
        dest="csv_append",
        help="Overwrite benchmark_refs.csv instead of appending.",
    )
    parser.add_argument(
        "--no-generated-date",
        action="store_false",
        dest="include_generated_date",
        help="Do not include generated_at_utc in benchmark_refs outputs.",
    )
    parser.set_defaults(
        generate_history=True,
        csv_append=True,
        include_generated_date=True,
    )
    return parser


def _resolve_targets(raw_targets: list[str]) -> list[str]:
    if "all" in raw_targets:
        return ["avatar", "world"]

    resolved: list[str] = []
    seen: set[str] = set()
    for target in raw_targets:
        if target not in seen:
            seen.add(target)
            resolved.append(target)
    return resolved


def _build_benchmark_refs_command(
    scope: Path,
    out_json: Path,
    out_csv: Path,
    runs: int,
    warmup_runs: int,
    csv_append: bool,
    include_generated_date: bool,
    excludes: list[str],
    ignore_guids: list[str],
    ignore_guid_file: str | None,
) -> list[str]:
    cmd = [
        sys.executable,
        str(BENCHMARK_REFS_SCRIPT),
        "--scope",
        scope.as_posix(),
        "--runs",
        str(runs),
        "--warmup-runs",
        str(warmup_runs),
        "--out",
        str(out_json),
        "--out-csv",
        str(out_csv),
    ]
    if csv_append:
        cmd.append("--csv-append")
    if include_generated_date:
        cmd.append("--include-generated-date")
    for pattern in excludes:
        cmd.extend(["--exclude", pattern])
    for guid in ignore_guids:
        cmd.extend(["--ignore-guid", guid])
    if ignore_guid_file:
        cmd.extend(["--ignore-guid-file", ignore_guid_file])
    return cmd


def _build_history_command(
    inputs_glob: str,
    out_csv: Path,
    out_md: Path | None,
    generated_date_prefix: str | None,
    min_p90: float | None,
    latest_per_scope: bool,
    split_by_severity: bool,
) -> list[str]:
    cmd = [
        sys.executable,
        str(BENCHMARK_HISTORY_SCRIPT),
        "--inputs",
        inputs_glob,
        "--sort-by",
        "avg_sec",
        "--sort-order",
        "desc",
        "--include-date-column",
        "--out",
        str(out_csv),
    ]
    if generated_date_prefix:
        cmd.extend(["--generated-date-prefix", generated_date_prefix])
    if min_p90 is not None:
        cmd.extend(["--min-p90", str(min_p90)])
    if latest_per_scope:
        cmd.append("--latest-per-scope")
    if split_by_severity:
        cmd.append("--split-by-severity")
    if out_md is not None:
        cmd.extend(["--out-md", str(out_md)])
    return cmd


def _build_regression_command(
    baseline_inputs: list[str],
    latest_input: Path,
    out_json: Path,
    out_csv: Path,
    out_md: Path | None,
    baseline_pinning_file: str | None,
    avg_ratio_threshold: float,
    p90_ratio_threshold: float,
    min_absolute_delta_sec: float,
    alerts_only: bool,
    fail_on_regression: bool,
    out_csv_append: bool,
) -> list[str]:
    cmd = [
        sys.executable,
        str(BENCHMARK_REGRESSION_SCRIPT),
        "--baseline-inputs",
    ]
    cmd.extend(baseline_inputs)
    cmd.extend(
        [
            "--latest-inputs",
            str(latest_input),
            "--avg-ratio-threshold",
            str(avg_ratio_threshold),
            "--p90-ratio-threshold",
            str(p90_ratio_threshold),
            "--min-absolute-delta-sec",
            str(min_absolute_delta_sec),
            "--out-json",
            str(out_json),
            "--out-csv",
            str(out_csv),
        ]
    )
    if out_md is not None:
        cmd.extend(["--out-md", str(out_md)])
    if baseline_pinning_file:
        cmd.extend(["--baseline-pinning-file", baseline_pinning_file])
    if alerts_only:
        cmd.append("--alerts-only")
    if fail_on_regression:
        cmd.append("--fail-on-regression")
    if out_csv_append:
        cmd.append("--out-csv-append")
    return cmd


def _discover_baseline_inputs(
    config_dir: Path, current_bench_json: Path, limit: int
) -> list[str]:
    if limit <= 0:
        return []
    candidates = [
        path
        for path in config_dir.glob("bench_*.json")
        if path != current_bench_json and not path.name.startswith("bench_regression")
    ]
    candidates = sorted(candidates, key=lambda path: path.stat().st_mtime, reverse=True)
    return [str(path) for path in candidates[:limit]]


def _run_or_raise(command: list[str]) -> None:
    proc = subprocess.run(command)
    if proc.returncode != 0:
        raise RuntimeError(f"Command failed with exit {proc.returncode}: {' '.join(command)}")


def _build_tag(custom_tag: str | None) -> str:
    if custom_tag:
        return custom_tag
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    targets = _resolve_targets(args.targets)
    tag = _build_tag(args.tag)

    for target in targets:
        scope = Path("sample") / target / "Assets"
        if not scope.exists():
            raise FileNotFoundError(f"Scope not found: {scope}")

        config_dir = Path("sample") / target / "config"
        config_dir.mkdir(parents=True, exist_ok=True)

        bench_json = config_dir / f"bench_{tag}.json"
        bench_csv = config_dir / "benchmark_refs.csv"

        refs_cmd = _build_benchmark_refs_command(
            scope=scope,
            out_json=bench_json,
            out_csv=bench_csv,
            runs=args.runs,
            warmup_runs=args.warmup_runs,
            csv_append=args.csv_append,
            include_generated_date=args.include_generated_date,
            excludes=args.exclude,
            ignore_guids=args.ignore_guid,
            ignore_guid_file=args.ignore_guid_file,
        )
        _run_or_raise(refs_cmd)

        if args.generate_history:
            history_cmd = _build_history_command(
                inputs_glob=str(config_dir / "bench_*.json"),
                out_csv=config_dir / "benchmark_trend.csv",
                out_md=(config_dir / "benchmark_trend.md") if args.history_write_md else None,
                generated_date_prefix=args.history_generated_date_prefix,
                min_p90=args.history_min_p90,
                latest_per_scope=args.history_latest_per_scope,
                split_by_severity=args.history_split_by_severity,
            )
            _run_or_raise(history_cmd)

        if args.run_regression:
            baseline_inputs = args.regression_baseline_inputs
            if not baseline_inputs:
                baseline_inputs = _discover_baseline_inputs(
                    config_dir,
                    bench_json,
                    args.regression_baseline_auto_latest,
                )
            if not baseline_inputs:
                parser.error(
                    "--run-regression requires --regression-baseline-inputs or "
                    "--regression-baseline-auto-latest."
                )

            regression_cmd = _build_regression_command(
                baseline_inputs=baseline_inputs,
                latest_input=bench_json,
                out_json=config_dir / "benchmark_regression.json",
                out_csv=config_dir / "benchmark_regression.csv",
                out_md=(
                    config_dir / "benchmark_regression.md"
                    if args.regression_out_md
                    else None
                ),
                baseline_pinning_file=args.regression_baseline_pinning_file,
                avg_ratio_threshold=args.regression_avg_ratio_threshold,
                p90_ratio_threshold=args.regression_p90_ratio_threshold,
                min_absolute_delta_sec=args.regression_min_absolute_delta_sec,
                alerts_only=args.regression_alerts_only,
                fail_on_regression=args.regression_fail_on_regression,
                out_csv_append=args.regression_out_csv_append,
            )
            _run_or_raise(regression_cmd)

        print(bench_json)
        if args.generate_history:
            print(config_dir / "benchmark_trend.csv")
            if args.history_write_md:
                print(config_dir / "benchmark_trend.md")
        if args.run_regression:
            print(config_dir / "benchmark_regression.json")
            print(config_dir / "benchmark_regression.csv")
            if args.regression_out_md:
                print(config_dir / "benchmark_regression.md")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
