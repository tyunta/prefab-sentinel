from __future__ import annotations

import argparse
from typing import Any


def _pass_pct(total: int, failures: int) -> float | None:
    """Return (total - failures) / total * 100 rounded to 2 dp; None if total <= 0."""
    if total <= 0:
        return None
    return round(((total - failures) / float(total)) * 100.0, 2)


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


def _to_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    return None


def add_arguments(parser: argparse.ArgumentParser) -> None:
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
        "--max-code-mismatches",
        type=int,
        default=None,
        help="Fail with exit code 1 if code assertion mismatches exceed this count.",
    )
    parser.add_argument(
        "--min-code-pass-pct",
        type=float,
        default=None,
        help="Fail with exit code 1 if code assertion pass percentage falls below this value (0-100).",
    )
    parser.add_argument(
        "--max-applied-mismatches",
        type=int,
        default=None,
        help="Fail with exit code 1 if applied assertion mismatches exceed this count.",
    )
    parser.add_argument(
        "--min-applied-pass-pct",
        type=float,
        default=None,
        help="Fail with exit code 1 if applied assertion pass percentage falls below this value (0-100).",
    )
    parser.add_argument(
        "--max-observed-timeout-breaches",
        type=int,
        default=None,
        help="Fail with exit code 1 if observed duration>timeout breaches exceed this count.",
    )
    parser.add_argument(
        "--min-observed-timeout-coverage-pct",
        type=float,
        default=None,
        help="Fail with exit code 1 if observed timeout coverage percentage falls below this value (0-100).",
    )
    parser.add_argument(
        "--max-observed-timeout-breaches-per-target",
        type=int,
        default=None,
        help="Fail with exit code 1 if any observed duration>timeout target breaches exceed this count.",
    )
    parser.add_argument(
        "--min-observed-timeout-coverage-pct-per-target",
        type=float,
        default=None,
        help="Fail with exit code 1 if any observed timeout target coverage percentage falls below this value (0-100).",
    )
    parser.add_argument(
        "--max-profile-timeout-breaches",
        type=int,
        default=None,
        help="Fail with exit code 1 if timeout-profile breaches exceed this count for the selected timeout policy.",
    )
    parser.add_argument(
        "--min-profile-timeout-coverage-pct",
        type=float,
        default=None,
        help="Fail with exit code 1 if timeout-profile coverage percentage falls below this value (0-100) for the selected timeout policy.",
    )
    parser.add_argument(
        "--max-profile-timeout-breaches-per-target",
        type=int,
        default=None,
        help="Fail with exit code 1 if any timeout-profile target breaches exceed this count for the selected timeout policy.",
    )
    parser.add_argument(
        "--min-profile-timeout-coverage-pct-per-target",
        type=float,
        default=None,
        help="Fail with exit code 1 if any timeout-profile target coverage percentage falls below this value (0-100) for the selected timeout policy.",
    )
    parser.add_argument(
        "--duration-percentile",
        type=float,
        # p90: covers typical variance while excluding long-tail outliers
        default=90.0,
        help="Percentile used in markdown decision table (0-100, default: 90).",
    )
    parser.add_argument(
        "--out-md",
        default=None,
        help="Optional markdown decision table output path.",
    )
    parser.add_argument(
        "--out-timeout-profile",
        default=None,
        help="Optional timeout profile JSON output path.",
    )
    parser.add_argument(
        "--timeout-multiplier",
        type=float,
        # 1.5x: provides 50% headroom above percentile for load variance
        default=1.5,
        help="Multiplier applied to duration percentile for timeout recommendation.",
    )
    parser.add_argument(
        "--timeout-slack-sec",
        type=int,
        # 60s: buffer for Unity editor startup jitter and GC pauses
        default=60,
        help="Additional seconds added to duration_max and failure cases.",
    )
    parser.add_argument(
        "--timeout-min-sec",
        type=int,
        # 300s (5min): ensures adequate time for initial scene load
        default=300,
        help="Minimum timeout recommendation in seconds.",
    )
    parser.add_argument(
        "--timeout-round-sec",
        type=int,
        # 30s: balances readability with precision in timeout values
        default=30,
        help="Round timeout recommendation up by this step in seconds.",
    )
    parser.add_argument(
        "--out",
        required=True,
        help="Output CSV path.",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="smoke_summary_to_csv",
        description="Convert bridge_smoke_samples summary JSON files into a decision table CSV.",
    )
    add_arguments(parser)
    return parser


def _validate_history_args(args: argparse.Namespace, parser: argparse.ArgumentParser) -> None:
    """Validate range constraints on all history CLI arguments."""
    if args.duration_percentile < 0.0 or args.duration_percentile > 100.0:
        parser.error("--duration-percentile must be in range 0..100.")
    if args.timeout_multiplier < 1.0:
        parser.error("--timeout-multiplier must be greater than or equal to 1.0.")
    if args.timeout_slack_sec < 0:
        parser.error("--timeout-slack-sec must be greater than or equal to 0.")
    if args.timeout_min_sec <= 0:
        parser.error("--timeout-min-sec must be greater than 0.")
    if args.timeout_round_sec <= 0:
        parser.error("--timeout-round-sec must be greater than 0.")
    if args.max_code_mismatches is not None and args.max_code_mismatches < 0:
        parser.error("--max-code-mismatches must be greater than or equal to 0.")
    if args.min_code_pass_pct is not None and (
        args.min_code_pass_pct < 0.0 or args.min_code_pass_pct > 100.0
    ):
        parser.error("--min-code-pass-pct must be in range 0..100.")
    if args.max_applied_mismatches is not None and args.max_applied_mismatches < 0:
        parser.error("--max-applied-mismatches must be greater than or equal to 0.")
    if args.min_applied_pass_pct is not None and (
        args.min_applied_pass_pct < 0.0 or args.min_applied_pass_pct > 100.0
    ):
        parser.error("--min-applied-pass-pct must be in range 0..100.")
    if (
        args.max_observed_timeout_breaches is not None
        and args.max_observed_timeout_breaches < 0
    ):
        parser.error("--max-observed-timeout-breaches must be greater than or equal to 0.")
    if args.min_observed_timeout_coverage_pct is not None and (
        args.min_observed_timeout_coverage_pct < 0.0
        or args.min_observed_timeout_coverage_pct > 100.0
    ):
        parser.error("--min-observed-timeout-coverage-pct must be in range 0..100.")
    if (
        args.max_observed_timeout_breaches_per_target is not None
        and args.max_observed_timeout_breaches_per_target < 0
    ):
        parser.error(
            "--max-observed-timeout-breaches-per-target must be greater than or equal to 0."
        )
    if args.min_observed_timeout_coverage_pct_per_target is not None and (
        args.min_observed_timeout_coverage_pct_per_target < 0.0
        or args.min_observed_timeout_coverage_pct_per_target > 100.0
    ):
        parser.error(
            "--min-observed-timeout-coverage-pct-per-target must be in range 0..100."
        )
    if (
        args.max_profile_timeout_breaches is not None
        and args.max_profile_timeout_breaches < 0
    ):
        parser.error("--max-profile-timeout-breaches must be greater than or equal to 0.")
    if args.min_profile_timeout_coverage_pct is not None and (
        args.min_profile_timeout_coverage_pct < 0.0
        or args.min_profile_timeout_coverage_pct > 100.0
    ):
        parser.error("--min-profile-timeout-coverage-pct must be in range 0..100.")
    if (
        args.max_profile_timeout_breaches_per_target is not None
        and args.max_profile_timeout_breaches_per_target < 0
    ):
        parser.error(
            "--max-profile-timeout-breaches-per-target must be greater than or equal to 0."
        )
    if args.min_profile_timeout_coverage_pct_per_target is not None and (
        args.min_profile_timeout_coverage_pct_per_target < 0.0
        or args.min_profile_timeout_coverage_pct_per_target > 100.0
    ):
        parser.error(
            "--min-profile-timeout-coverage-pct-per-target must be in range 0..100."
        )


def run_from_args(args: argparse.Namespace, parser: argparse.ArgumentParser) -> int:
    # Local imports to avoid circular dependency:
    # smoke_history_pipeline/report → smoke_history_stats → smoke_history
    from prefab_sentinel.smoke_history_pipeline import (  # noqa: PLC0415
        _evaluate_thresholds,
        _load_history_rows,
    )
    from prefab_sentinel.smoke_history_report import (  # noqa: PLC0415
        _compute_and_write_outputs,
    )

    _validate_history_args(args, parser)
    rows = _load_history_rows(args, parser)
    stats, profile_payload = _compute_and_write_outputs(args, parser, rows)
    return _evaluate_thresholds(args, rows, stats, profile_payload)


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return run_from_args(args, parser)


if __name__ == "__main__":
    raise SystemExit(main())
