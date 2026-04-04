from __future__ import annotations

import math
from typing import Any

from prefab_sentinel.smoke_history import _pass_pct, _to_float, _to_int


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
        observed_timeout_coverage_pct = _pass_pct(observed_timeout_sample_count, observed_timeout_breach_count)
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
        code_assertion_pass_pct = _pass_pct(code_assertion_runs, code_assertion_mismatches)
        applied_assertion_runs = len(applied_matches_values)
        applied_assertion_mismatches = len(
            [value for value in applied_matches_values if value is False]
        )
        applied_assertion_pass_pct = _pass_pct(applied_assertion_runs, applied_assertion_mismatches)
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
        timeout_coverage_pct = _pass_pct(duration_sample_count, timeout_breach_count)

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
    code_pass_pct = _pass_pct(code_assertions, code_mismatches)
    return code_assertions, code_mismatches, code_pass_pct


def _compute_applied_assertion_metrics(
    rows: list[dict[str, Any]]
) -> tuple[int, int, float | None]:
    applied_matches_values = [
        value for value in (row.get("applied_matches") for row in rows) if isinstance(value, bool)
    ]
    applied_assertions = len(applied_matches_values)
    applied_mismatches = len([value for value in applied_matches_values if value is False])
    applied_pass_pct = _pass_pct(applied_assertions, applied_mismatches)
    return applied_assertions, applied_mismatches, applied_pass_pct


def _compute_observed_timeout_metrics(
    stats: list[dict[str, Any]],
) -> tuple[int, int, float | None]:
    by_target = _compute_observed_timeout_metrics_by_target(stats)
    if not by_target:
        return 0, 0, None

    observed_timeout_samples = sum(
        _to_int(item.get("observed_timeout_sample_count")) or 0 for item in by_target.values()
    )
    observed_timeout_breaches = sum(
        _to_int(item.get("observed_timeout_breach_count")) or 0 for item in by_target.values()
    )
    observed_timeout_coverage_pct = _pass_pct(observed_timeout_samples, observed_timeout_breaches)
    return observed_timeout_samples, observed_timeout_breaches, observed_timeout_coverage_pct


def _compute_observed_timeout_metrics_by_target(
    stats: list[dict[str, Any]],
) -> dict[str, dict[str, float | int | None]]:
    by_target: dict[str, dict[str, float | int | None]] = {}
    for item in stats:
        target = str(item.get("target", ""))
        observed_timeout_sample_count = _to_int(item.get("observed_timeout_sample_count")) or 0
        observed_timeout_breach_count = _to_int(item.get("observed_timeout_breach_count")) or 0
        observed_timeout_coverage_pct = _pass_pct(observed_timeout_sample_count, observed_timeout_breach_count)
        by_target[target] = {
            "observed_timeout_sample_count": observed_timeout_sample_count,
            "observed_timeout_breach_count": observed_timeout_breach_count,
            "observed_timeout_coverage_pct": observed_timeout_coverage_pct,
        }
    return by_target


def _compute_profile_timeout_metrics_by_target(
    profile_payload: dict[str, Any] | None,
) -> dict[str, dict[str, float | int | None]]:
    if not isinstance(profile_payload, dict):
        return {}
    profiles_raw = profile_payload.get("profiles", [])
    if not isinstance(profiles_raw, list):
        return {}

    by_target: dict[str, dict[str, float | int | None]] = {}
    for profile in profiles_raw:
        if not isinstance(profile, dict):
            continue
        target = str(profile.get("target", ""))
        evidence = profile.get("evidence", {})
        if not isinstance(evidence, dict):
            continue
        duration_sample_count = _to_int(evidence.get("duration_sample_count")) or 0
        timeout_breach_count = _to_int(evidence.get("timeout_breach_count")) or 0
        timeout_coverage_pct = _pass_pct(duration_sample_count, timeout_breach_count)
        by_target[target] = {
            "duration_sample_count": duration_sample_count,
            "timeout_breach_count": timeout_breach_count,
            "timeout_coverage_pct": timeout_coverage_pct,
        }
    return by_target


def _compute_profile_timeout_metrics(
    profile_payload: dict[str, Any] | None,
) -> tuple[int, int, float | None]:
    by_target = _compute_profile_timeout_metrics_by_target(profile_payload)
    if not by_target:
        return 0, 0, None

    profile_timeout_samples = sum(
        _to_int(item.get("duration_sample_count")) or 0 for item in by_target.values()
    )
    profile_timeout_breaches = sum(
        _to_int(item.get("timeout_breach_count")) or 0 for item in by_target.values()
    )
    profile_timeout_coverage_pct = _pass_pct(profile_timeout_samples, profile_timeout_breaches)
    return profile_timeout_samples, profile_timeout_breaches, profile_timeout_coverage_pct
