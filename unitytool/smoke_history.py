from __future__ import annotations

import argparse
import csv
import glob
import json
import math
import sys
from pathlib import Path
from typing import Any


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
        help=(
            "Fail with exit code 1 if code assertion pass percentage falls below this value "
            "(0-100)."
        ),
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
        help=(
            "Fail with exit code 1 if applied assertion pass percentage falls below this value "
            "(0-100)."
        ),
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
        help=(
            "Fail with exit code 1 if observed timeout coverage percentage falls below this value "
            "(0-100)."
        ),
    )
    parser.add_argument(
        "--max-profile-timeout-breaches",
        type=int,
        default=None,
        help=(
            "Fail with exit code 1 if timeout-profile breaches exceed this count "
            "for the selected timeout policy."
        ),
    )
    parser.add_argument(
        "--min-profile-timeout-coverage-pct",
        type=float,
        default=None,
        help=(
            "Fail with exit code 1 if timeout-profile coverage percentage falls below this "
            "value (0-100) for the selected timeout policy."
        ),
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
        "--out-timeout-profile",
        default=None,
        help="Optional timeout profile JSON output path.",
    )
    parser.add_argument(
        "--timeout-multiplier",
        type=float,
        default=1.5,
        help="Multiplier applied to duration percentile for timeout recommendation.",
    )
    parser.add_argument(
        "--timeout-slack-sec",
        type=int,
        default=60,
        help="Additional seconds added to duration_max and failure cases.",
    )
    parser.add_argument(
        "--timeout-min-sec",
        type=int,
        default=300,
        help="Minimum timeout recommendation in seconds.",
    )
    parser.add_argument(
        "--timeout-round-sec",
        type=int,
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


def _to_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
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
    expected_code_value = case.get("expected_code")
    actual_code_value = case.get("actual_code")
    return {
        "source": str(source),
        "batch_success": bool(payload.get("success", False)),
        "batch_severity": str(payload.get("severity", "")),
        "target": str(case.get("name", "")),
        "matched_expectation": bool(case.get("matched_expectation", False)),
        "expected_code": (
            str(expected_code_value) if isinstance(expected_code_value, str) else ""
        ),
        "actual_code": str(actual_code_value) if isinstance(actual_code_value, str) else "",
        "code_matches": _to_bool(case.get("code_matches")),
        "expected_applied": _to_int(case.get("expected_applied")),
        "expected_applied_source": str(case.get("expected_applied_source", "")),
        "actual_applied": _to_int(case.get("actual_applied")),
        "applied_matches": _to_bool(case.get("applied_matches")),
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
        observed_timeout_pairs = [
            (duration, timeout)
            for duration, timeout in (
                (row.get("duration_sec"), row.get("unity_timeout_sec")) for row in target_rows
            )
            if isinstance(duration, float) and isinstance(timeout, int)
        ]
        observed_timeout_sample_count = len(observed_timeout_pairs)
        observed_timeout_breach_count = len(
            [pair for pair in observed_timeout_pairs if pair[0] > float(pair[1])]
        )
        observed_timeout_coverage_pct = (
            round(
                (
                    (
                        observed_timeout_sample_count
                        - observed_timeout_breach_count
                    )
                    / float(observed_timeout_sample_count)
                )
                * 100.0,
                2,
            )
            if observed_timeout_sample_count > 0
            else None
        )
        code_matches_values = [
            value
            for value in (row.get("code_matches") for row in target_rows)
            if isinstance(value, bool)
        ]
        applied_matches_values = [
            value
            for value in (row.get("applied_matches") for row in target_rows)
            if isinstance(value, bool)
        ]
        failures = [row for row in target_rows if not bool(row.get("matched_expectation", False))]
        code_assertion_runs = len(code_matches_values)
        code_assertion_mismatches = len(
            [value for value in code_matches_values if value is False]
        )
        code_assertion_pass_pct = (
            round(
                (
                    (code_assertion_runs - code_assertion_mismatches)
                    / float(code_assertion_runs)
                )
                * 100.0,
                2,
            )
            if code_assertion_runs > 0
            else None
        )
        applied_assertion_runs = len(applied_matches_values)
        applied_assertion_mismatches = len(
            [value for value in applied_matches_values if value is False]
        )
        applied_assertion_pass_pct = (
            round(
                (
                    (applied_assertion_runs - applied_assertion_mismatches)
                    / float(applied_assertion_runs)
                )
                * 100.0,
                2,
            )
            if applied_assertion_runs > 0
            else None
        )
        duration_avg = sum(durations) / len(durations) if durations else None
        stats.append(
            {
                "target": target,
                "runs": len(target_rows),
                "failures": len(failures),
                "code_assertion_runs": code_assertion_runs,
                "code_assertion_mismatches": code_assertion_mismatches,
                "code_assertion_pass_pct": code_assertion_pass_pct,
                "applied_assertion_runs": applied_assertion_runs,
                "applied_assertion_mismatches": applied_assertion_mismatches,
                "applied_assertion_pass_pct": applied_assertion_pass_pct,
                "observed_timeout_sample_count": observed_timeout_sample_count,
                "observed_timeout_breach_count": observed_timeout_breach_count,
                "observed_timeout_coverage_pct": observed_timeout_coverage_pct,
                "attempts_max": max(attempts) if attempts else None,
                "duration_avg_sec": duration_avg,
                "duration_p_sec": _percentile(durations, duration_percentile),
                "duration_max_sec": max(durations) if durations else None,
                "timeout_max_sec": max(timeouts) if timeouts else None,
                "duration_values_sec": durations,
            }
        )
    return stats


def _round_up_timeout(value_sec: float, step_sec: int) -> int:
    if step_sec <= 0:
        raise ValueError("step_sec must be greater than 0")
    return int(math.ceil(value_sec / step_sec) * step_sec)


def _build_timeout_profiles(
    stats: list[dict[str, Any]],
    *,
    duration_percentile: float,
    timeout_multiplier: float,
    timeout_slack_sec: int,
    timeout_min_sec: int,
    timeout_round_sec: int,
) -> dict[str, Any]:
    profiles: list[dict[str, Any]] = []
    for item in stats:
        target = str(item.get("target", ""))
        duration_p = _to_float(item.get("duration_p_sec"))
        duration_max = _to_float(item.get("duration_max_sec"))
        observed_timeout_max = _to_int(item.get("timeout_max_sec"))
        failures = _to_int(item.get("failures")) or 0
        duration_values_raw = item.get("duration_values_sec", [])
        duration_values: list[float] = []
        if isinstance(duration_values_raw, list):
            for raw in duration_values_raw:
                value = _to_float(raw)
                if value is not None:
                    duration_values.append(value)
        duration_sample_count = len(duration_values)

        candidates: list[float] = [float(timeout_min_sec)]
        if duration_p is not None:
            candidates.append(duration_p * timeout_multiplier)
        if duration_max is not None:
            candidates.append(duration_max + float(timeout_slack_sec))
        if observed_timeout_max is not None:
            candidates.append(float(observed_timeout_max))

        # Recommendation formula is evidence-based and conservative:
        # keep at least min timeout, cover percentile/max durations with safety slack,
        # and never go below the largest observed timeout in history.
        base_timeout_sec = max(candidates)
        if failures > 0:
            base_timeout_sec += float(timeout_slack_sec)
        recommended_timeout_sec = _round_up_timeout(base_timeout_sec, timeout_round_sec)
        timeout_breach_count = len(
            [value for value in duration_values if value > float(recommended_timeout_sec)]
        )
        timeout_coverage_pct = (
            round(
                (
                    (duration_sample_count - timeout_breach_count)
                    / float(duration_sample_count)
                )
                * 100.0,
                2,
            )
            if duration_sample_count > 0
            else None
        )

        profiles.append(
            {
                "target": target,
                "recommended_timeout_sec": recommended_timeout_sec,
                "recommended_cli_arg": f"--{target}-unity-timeout-sec {recommended_timeout_sec}",
                "evidence": {
                    "runs": _to_int(item.get("runs")),
                    "failures": failures,
                    "duration_p_sec": duration_p,
                    "duration_max_sec": duration_max,
                    "observed_timeout_max_sec": observed_timeout_max,
                    "duration_sample_count": duration_sample_count,
                    "timeout_breach_count": timeout_breach_count,
                    "timeout_coverage_pct": timeout_coverage_pct,
                },
            }
        )

    return {
        "version": 1,
        "generated_by": "smoke_summary_to_csv",
        "duration_percentile": duration_percentile,
        "timeout_policy": {
            "timeout_multiplier": timeout_multiplier,
            "timeout_slack_sec": timeout_slack_sec,
            "timeout_min_sec": timeout_min_sec,
            "timeout_round_sec": timeout_round_sec,
            "formula": "round_up(max(min_timeout, duration_p*multiplier, duration_max+slack, observed_timeout_max) + failure_slack, round_sec)",
        },
        "profiles": profiles,
    }


def _compute_code_assertion_metrics(
    rows: list[dict[str, Any]]
) -> tuple[int, int, float | None]:
    code_matches_values = [
        value for value in (row.get("code_matches") for row in rows) if isinstance(value, bool)
    ]
    code_assertions = len(code_matches_values)
    code_mismatches = len([value for value in code_matches_values if value is False])
    code_pass_pct = (
        ((code_assertions - code_mismatches) / float(code_assertions)) * 100.0
        if code_assertions > 0
        else None
    )
    return code_assertions, code_mismatches, code_pass_pct


def _compute_applied_assertion_metrics(
    rows: list[dict[str, Any]]
) -> tuple[int, int, float | None]:
    applied_matches_values = [
        value for value in (row.get("applied_matches") for row in rows) if isinstance(value, bool)
    ]
    applied_assertions = len(applied_matches_values)
    applied_mismatches = len([value for value in applied_matches_values if value is False])
    applied_pass_pct = (
        ((applied_assertions - applied_mismatches) / float(applied_assertions)) * 100.0
        if applied_assertions > 0
        else None
    )
    return applied_assertions, applied_mismatches, applied_pass_pct


def _compute_observed_timeout_metrics(
    stats: list[dict[str, Any]],
) -> tuple[int, int, float | None]:
    observed_timeout_samples = sum(
        [
            _to_int(item.get("observed_timeout_sample_count")) or 0
            for item in stats
        ]
    )
    observed_timeout_breaches = sum(
        [
            _to_int(item.get("observed_timeout_breach_count")) or 0
            for item in stats
        ]
    )
    observed_timeout_coverage_pct = (
        ((observed_timeout_samples - observed_timeout_breaches) / float(observed_timeout_samples))
        * 100.0
        if observed_timeout_samples > 0
        else None
    )
    return (
        observed_timeout_samples,
        observed_timeout_breaches,
        observed_timeout_coverage_pct,
    )


def _compute_profile_timeout_metrics(
    profile_payload: dict[str, Any] | None,
) -> tuple[int, int, float | None]:
    if not isinstance(profile_payload, dict):
        return 0, 0, None
    profiles_raw = profile_payload.get("profiles", [])
    if not isinstance(profiles_raw, list):
        return 0, 0, None

    profile_timeout_samples = 0
    profile_timeout_breaches = 0
    for profile in profiles_raw:
        if not isinstance(profile, dict):
            continue
        evidence = profile.get("evidence", {})
        if not isinstance(evidence, dict):
            continue
        profile_timeout_samples += _to_int(evidence.get("duration_sample_count")) or 0
        profile_timeout_breaches += _to_int(evidence.get("timeout_breach_count")) or 0

    profile_timeout_coverage_pct = (
        (
            (profile_timeout_samples - profile_timeout_breaches)
            / float(profile_timeout_samples)
        )
        * 100.0
        if profile_timeout_samples > 0
        else None
    )
    return (
        profile_timeout_samples,
        profile_timeout_breaches,
        profile_timeout_coverage_pct,
    )


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _write_csv(out_path: Path, header: list[str], rows: list[dict[str, Any]]) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=header)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def _render_markdown_summary(
    rows: list[dict[str, Any]],
    duration_percentile: float,
    stats: list[dict[str, Any]] | None = None,
    profile_payload: dict[str, Any] | None = None,
) -> str:
    percentile_label = f"duration_p{int(round(duration_percentile))}_sec"
    target_stats = stats if stats is not None else _build_target_stats(rows, duration_percentile)
    (
        code_assertions,
        code_mismatches,
        code_pass_pct_raw,
    ) = _compute_code_assertion_metrics(rows)
    code_pass_pct = (
        round(code_pass_pct_raw, 2)
        if code_pass_pct_raw is not None
        else None
    )
    (
        applied_assertions,
        applied_mismatches,
        applied_pass_pct_raw,
    ) = _compute_applied_assertion_metrics(rows)
    applied_pass_pct = (
        round(applied_pass_pct_raw, 2)
        if applied_pass_pct_raw is not None
        else None
    )
    (
        observed_timeout_samples,
        observed_timeout_breaches,
        observed_timeout_coverage_pct_raw,
    ) = _compute_observed_timeout_metrics(target_stats)
    observed_timeout_coverage_pct = (
        round(observed_timeout_coverage_pct_raw, 2)
        if observed_timeout_coverage_pct_raw is not None
        else None
    )
    (
        profile_timeout_samples,
        profile_timeout_breaches,
        profile_timeout_coverage_pct_raw,
    ) = _compute_profile_timeout_metrics(profile_payload)
    profile_timeout_coverage_pct = (
        round(profile_timeout_coverage_pct_raw, 2)
        if profile_timeout_coverage_pct_raw is not None
        else None
    )
    lines = [
        "# Bridge Smoke Timeout Decision Table",
        "",
        f"- Cases: {len(rows)}",
        f"- Duration percentile: p{duration_percentile:g}",
        f"- Code assertion runs: {code_assertions}",
        f"- Code assertion mismatches: {code_mismatches}",
        (
            f"- Code assertion pass pct: {code_pass_pct}"
            if code_pass_pct is not None
            else "- Code assertion pass pct: n/a"
        ),
        f"- Applied assertion runs: {applied_assertions}",
        f"- Applied assertion mismatches: {applied_mismatches}",
        (
            f"- Applied assertion pass pct: {applied_pass_pct}"
            if applied_pass_pct is not None
            else "- Applied assertion pass pct: n/a"
        ),
        f"- Observed timeout samples: {observed_timeout_samples}",
        f"- Observed timeout breaches: {observed_timeout_breaches}",
        (
            f"- Observed timeout coverage pct: {observed_timeout_coverage_pct}"
            if observed_timeout_coverage_pct is not None
            else "- Observed timeout coverage pct: n/a"
        ),
        f"- Profile timeout samples: {profile_timeout_samples}",
        f"- Profile timeout breaches: {profile_timeout_breaches}",
        (
            f"- Profile timeout coverage pct: {profile_timeout_coverage_pct}"
            if profile_timeout_coverage_pct is not None
            else "- Profile timeout coverage pct: n/a"
        ),
        "",
        f"| target | runs | failures | code_assertions | code_mismatches | code_pass_pct | applied_assertions | applied_mismatches | applied_pass_pct | observed_timeout_samples | observed_timeout_breaches | observed_timeout_coverage_pct | attempts_max | duration_avg_sec | {percentile_label} | duration_max_sec | timeout_max_sec |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for item in target_stats:
        lines.append(
            "| {target} | {runs} | {failures} | {code_assertion_runs} | {code_assertion_mismatches} | {code_assertion_pass_pct} | {applied_assertion_runs} | {applied_assertion_mismatches} | {applied_assertion_pass_pct} | {observed_timeout_sample_count} | {observed_timeout_breach_count} | {observed_timeout_coverage_pct} | {attempts_max} | {duration_avg_sec} | {duration_p_sec} | {duration_max_sec} | {timeout_max_sec} |".format(
                target=item.get("target", ""),
                runs=item.get("runs", 0),
                failures=item.get("failures", 0),
                code_assertion_runs=item.get("code_assertion_runs", 0),
                code_assertion_mismatches=item.get("code_assertion_mismatches", 0),
                code_assertion_pass_pct=item.get("code_assertion_pass_pct", ""),
                applied_assertion_runs=item.get("applied_assertion_runs", 0),
                applied_assertion_mismatches=item.get("applied_assertion_mismatches", 0),
                applied_assertion_pass_pct=item.get("applied_assertion_pass_pct", ""),
                observed_timeout_sample_count=item.get("observed_timeout_sample_count", 0),
                observed_timeout_breach_count=item.get("observed_timeout_breach_count", 0),
                observed_timeout_coverage_pct=item.get("observed_timeout_coverage_pct", ""),
                attempts_max=item.get("attempts_max", ""),
                duration_avg_sec=item.get("duration_avg_sec", ""),
                duration_p_sec=item.get("duration_p_sec", ""),
                duration_max_sec=item.get("duration_max_sec", ""),
                timeout_max_sec=item.get("timeout_max_sec", ""),
            )
        )
    return "\n".join(lines)


def run_from_args(args: argparse.Namespace, parser: argparse.ArgumentParser) -> int:
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
        args.max_profile_timeout_breaches is not None
        and args.max_profile_timeout_breaches < 0
    ):
        parser.error("--max-profile-timeout-breaches must be greater than or equal to 0.")
    if args.min_profile_timeout_coverage_pct is not None and (
        args.min_profile_timeout_coverage_pct < 0.0
        or args.min_profile_timeout_coverage_pct > 100.0
    ):
        parser.error("--min-profile-timeout-coverage-pct must be in range 0..100.")

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
        "expected_code",
        "actual_code",
        "code_matches",
        "expected_applied",
        "expected_applied_source",
        "actual_applied",
        "applied_matches",
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

    needs_stats = bool(
        args.out_md
        or args.out_timeout_profile
        or args.max_observed_timeout_breaches is not None
        or args.min_observed_timeout_coverage_pct is not None
        or args.max_profile_timeout_breaches is not None
        or args.min_profile_timeout_coverage_pct is not None
    )
    stats: list[dict[str, Any]] | None = (
        _build_target_stats(rows, args.duration_percentile) if needs_stats else None
    )

    needs_profile_payload = bool(
        args.out_md
        or args.out_timeout_profile
        or args.max_profile_timeout_breaches is not None
        or args.min_profile_timeout_coverage_pct is not None
    )
    profile_payload: dict[str, Any] | None = None
    if needs_profile_payload:
        profile_stats = (
            stats
            if stats is not None
            else _build_target_stats(rows, args.duration_percentile)
        )
        profile_payload = _build_timeout_profiles(
            profile_stats,
            duration_percentile=args.duration_percentile,
            timeout_multiplier=args.timeout_multiplier,
            timeout_slack_sec=args.timeout_slack_sec,
            timeout_min_sec=args.timeout_min_sec,
            timeout_round_sec=args.timeout_round_sec,
        )

    if args.out_md:
        out_md = Path(args.out_md)
        out_md.parent.mkdir(parents=True, exist_ok=True)
        out_md.write_text(
            _render_markdown_summary(
                rows,
                args.duration_percentile,
                stats=stats,
                profile_payload=profile_payload,
            )
            + "\n",
            encoding="utf-8",
        )
        print(out_md)

    if args.out_timeout_profile:
        out_timeout_profile = Path(args.out_timeout_profile)
        if profile_payload is None:
            parser.error("timeout profile payload was not generated.")
        _write_json(out_timeout_profile, profile_payload)
        print(out_timeout_profile)

    (
        _code_assertions,
        code_mismatches,
        code_pass_pct,
    ) = _compute_code_assertion_metrics(rows)
    (
        _applied_assertions,
        applied_mismatches,
        applied_pass_pct,
    ) = _compute_applied_assertion_metrics(rows)
    observed_timeout_breaches = 0
    observed_timeout_coverage_pct = None
    if stats is not None:
        (
            _observed_timeout_samples,
            observed_timeout_breaches,
            observed_timeout_coverage_pct,
        ) = _compute_observed_timeout_metrics(stats)
        observed_timeout_coverage_pct = (
            round(observed_timeout_coverage_pct, 2)
            if observed_timeout_coverage_pct is not None
            else None
        )
    (
        _profile_timeout_samples,
        profile_timeout_breaches,
        profile_timeout_coverage_pct,
    ) = _compute_profile_timeout_metrics(profile_payload)
    profile_timeout_coverage_pct = (
        round(profile_timeout_coverage_pct, 2)
        if profile_timeout_coverage_pct is not None
        else None
    )

    violations: list[str] = []
    if (
        args.max_code_mismatches is not None
        and code_mismatches > args.max_code_mismatches
    ):
        violations.append(
            "code mismatch threshold exceeded: "
            f"{code_mismatches} > {args.max_code_mismatches}"
        )
    if args.min_code_pass_pct is not None:
        if code_pass_pct is None:
            violations.append(
                "code pass percentage threshold configured but no code assertion rows exist"
            )
        elif code_pass_pct < args.min_code_pass_pct:
            violations.append(
                "code pass percentage below threshold: "
                f"{code_pass_pct:.2f} < {args.min_code_pass_pct:.2f}"
            )
    if (
        args.max_applied_mismatches is not None
        and applied_mismatches > args.max_applied_mismatches
    ):
        violations.append(
            "applied mismatch threshold exceeded: "
            f"{applied_mismatches} > {args.max_applied_mismatches}"
        )
    if args.min_applied_pass_pct is not None:
        if applied_pass_pct is None:
            violations.append(
                "applied pass percentage threshold configured but no applied assertion rows exist"
            )
        elif applied_pass_pct < args.min_applied_pass_pct:
            violations.append(
                "applied pass percentage below threshold: "
                f"{applied_pass_pct:.2f} < {args.min_applied_pass_pct:.2f}"
            )
    if (
        args.max_observed_timeout_breaches is not None
        and observed_timeout_breaches > args.max_observed_timeout_breaches
    ):
        violations.append(
            "observed timeout breach threshold exceeded: "
            f"{observed_timeout_breaches} > {args.max_observed_timeout_breaches}"
        )
    if args.min_observed_timeout_coverage_pct is not None:
        if observed_timeout_coverage_pct is None:
            violations.append(
                "observed timeout coverage threshold configured but no duration/timeout rows exist"
            )
        elif observed_timeout_coverage_pct < args.min_observed_timeout_coverage_pct:
            violations.append(
                "observed timeout coverage below threshold: "
                f"{observed_timeout_coverage_pct:.2f} < {args.min_observed_timeout_coverage_pct:.2f}"
            )
    if (
        args.max_profile_timeout_breaches is not None
        and profile_timeout_breaches > args.max_profile_timeout_breaches
    ):
        violations.append(
            "profile timeout breach threshold exceeded: "
            f"{profile_timeout_breaches} > {args.max_profile_timeout_breaches}"
        )
    if args.min_profile_timeout_coverage_pct is not None:
        if profile_timeout_coverage_pct is None:
            violations.append(
                "profile timeout coverage threshold configured but no timeout profile duration rows exist"
            )
        elif profile_timeout_coverage_pct < args.min_profile_timeout_coverage_pct:
            violations.append(
                "profile timeout coverage below threshold: "
                f"{profile_timeout_coverage_pct:.2f} < {args.min_profile_timeout_coverage_pct:.2f}"
            )

    for message in violations:
        print(f"SMOKE_HISTORY_THRESHOLD_FAILED: {message}", file=sys.stderr)
    return 0 if not violations else 1

def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return run_from_args(args, parser)


if __name__ == "__main__":
    raise SystemExit(main())
