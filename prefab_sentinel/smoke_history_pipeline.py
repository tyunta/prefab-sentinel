from __future__ import annotations

import argparse
import glob
import sys
from pathlib import Path
from typing import Any

from prefab_sentinel.json_io import load_json_file
from prefab_sentinel.smoke_history import _to_float, _to_int
from prefab_sentinel.smoke_history_report import _case_to_row, _is_smoke_batch_summary
from prefab_sentinel.smoke_history_stats import (
    _compute_applied_assertion_metrics,
    _compute_code_assertion_metrics,
    _compute_observed_timeout_metrics,
    _compute_observed_timeout_metrics_by_target,
    _compute_profile_timeout_metrics,
    _compute_profile_timeout_metrics_by_target,
)


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


def _load_history_rows(
    args: argparse.Namespace, parser: argparse.ArgumentParser
) -> list[dict[str, Any]]:
    """Load input files, parse JSON, filter rows. Returns the row list."""
    input_paths = _expand_inputs(args.inputs)
    if not input_paths:
        parser.error("No input JSON files were found.")

    include_targets = {target for target in args.target}
    rows: list[dict[str, Any]] = []
    for source in input_paths:
        payload = load_json_file(source)
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
    return rows


def _check_pct_threshold(
    actual: float | None,
    threshold: float | None,
    metric_name: str,
    no_data_msg: str,
) -> str | None:
    """Return a violation message if *actual* violates *threshold*, else None."""
    if threshold is None:
        return None
    if actual is None:
        return f"{metric_name} threshold configured but {no_data_msg}"
    if actual < threshold:
        return (
            f"{metric_name} below threshold: {actual:.2f} < {threshold:.2f}"
        )
    return None


def _check_by_target_breaches(
    by_target: dict[str, dict[str, Any]],
    threshold: int | None,
    metric_key: str,
    metric_name: str,
    no_data_msg: str,
) -> list[str]:
    """Return violation messages for per-target breach count checks."""
    if threshold is None:
        return []
    if not by_target:
        return [
            f"{metric_name} per-target breach threshold configured but {no_data_msg}"
        ]
    violations: list[str] = []
    for target in sorted(by_target):
        breaches = _to_int(by_target[target].get(metric_key)) or 0
        if breaches > threshold:
            violations.append(
                f"{metric_name} breach threshold exceeded for target "
                f"'{target}': {breaches} > {threshold}"
            )
    return violations


def _check_by_target_pct(
    by_target: dict[str, dict[str, Any]],
    threshold: float | None,
    metric_key: str,
    metric_name: str,
    no_targets_msg: str,
    no_target_data_msg: str,
) -> list[str]:
    """Return violation messages for per-target coverage pct checks."""
    if threshold is None:
        return []
    if not by_target:
        return [no_targets_msg]
    violations: list[str] = []
    for target in sorted(by_target):
        pct = _to_float(by_target[target].get(metric_key))
        if pct is None:
            violations.append(
                f"{metric_name} threshold configured but target "
                f"'{target}' has no {no_target_data_msg}"
            )
        elif pct < threshold:
            violations.append(
                f"{metric_name} below threshold for target "
                f"'{target}': {pct:.2f} < {threshold:.2f}"
            )
    return violations


def _evaluate_thresholds(
    args: argparse.Namespace,
    rows: list[dict[str, Any]],
    stats: list[dict[str, Any]] | None,
    profile_payload: dict[str, Any] | None,
) -> int:
    """Check threshold violations and return exit code (0 = pass, 1 = fail)."""
    _, code_mismatches, code_pass_pct = _compute_code_assertion_metrics(rows)
    _, applied_mismatches, applied_pass_pct = _compute_applied_assertion_metrics(rows)
    observed_timeout_breaches = 0
    observed_timeout_coverage_pct = None
    observed_timeout_by_target: dict[str, dict[str, float | int | None]] = {}
    if stats is not None:
        _, observed_timeout_breaches, observed_timeout_coverage_pct = _compute_observed_timeout_metrics(stats)
        observed_timeout_by_target = _compute_observed_timeout_metrics_by_target(stats)
        observed_timeout_coverage_pct = round(observed_timeout_coverage_pct, 2) if observed_timeout_coverage_pct is not None else None
    _, profile_timeout_breaches, profile_timeout_coverage_pct = _compute_profile_timeout_metrics(profile_payload)
    profile_timeout_by_target = _compute_profile_timeout_metrics_by_target(profile_payload)
    profile_timeout_coverage_pct = round(profile_timeout_coverage_pct, 2) if profile_timeout_coverage_pct is not None else None

    violations: list[str] = []
    if args.max_code_mismatches is not None and code_mismatches > args.max_code_mismatches:
        violations.append(f"code mismatch threshold exceeded: {code_mismatches} > {args.max_code_mismatches}")
    if v := _check_pct_threshold(code_pass_pct, args.min_code_pass_pct, "code pass percentage", "no code assertion rows exist"):
        violations.append(v)
    if args.max_applied_mismatches is not None and applied_mismatches > args.max_applied_mismatches:
        violations.append(f"applied mismatch threshold exceeded: {applied_mismatches} > {args.max_applied_mismatches}")
    if v := _check_pct_threshold(applied_pass_pct, args.min_applied_pass_pct, "applied pass percentage", "no applied assertion rows exist"):
        violations.append(v)
    if args.max_observed_timeout_breaches is not None and observed_timeout_breaches > args.max_observed_timeout_breaches:
        violations.append(f"observed timeout breach threshold exceeded: {observed_timeout_breaches} > {args.max_observed_timeout_breaches}")
    if v := _check_pct_threshold(observed_timeout_coverage_pct, args.min_observed_timeout_coverage_pct, "observed timeout coverage", "no duration/timeout rows exist"):
        violations.append(v)
    violations.extend(_check_by_target_breaches(
        observed_timeout_by_target,
        args.max_observed_timeout_breaches_per_target,
        "observed_timeout_breach_count",
        "observed timeout",
        "no duration/timeout target rows exist",
    ))
    violations.extend(_check_by_target_pct(
        observed_timeout_by_target,
        args.min_observed_timeout_coverage_pct_per_target,
        "observed_timeout_coverage_pct",
        "observed timeout coverage",
        "observed timeout per-target coverage threshold configured"
        " but no duration/timeout target rows exist",
        "duration/timeout rows",
    ))
    if args.max_profile_timeout_breaches is not None and profile_timeout_breaches > args.max_profile_timeout_breaches:
        violations.append(f"profile timeout breach threshold exceeded: {profile_timeout_breaches} > {args.max_profile_timeout_breaches}")
    if v := _check_pct_threshold(profile_timeout_coverage_pct, args.min_profile_timeout_coverage_pct, "profile timeout coverage", "no timeout profile duration rows exist"):
        violations.append(v)
    violations.extend(_check_by_target_breaches(
        profile_timeout_by_target,
        args.max_profile_timeout_breaches_per_target,
        "timeout_breach_count",
        "profile timeout",
        "no timeout profile target rows exist",
    ))
    violations.extend(_check_by_target_pct(
        profile_timeout_by_target,
        args.min_profile_timeout_coverage_pct_per_target,
        "timeout_coverage_pct",
        "profile timeout coverage",
        "profile timeout per-target coverage threshold configured"
        " but no timeout profile target rows exist",
        "timeout profile duration rows",
    ))

    for message in violations:
        print(f"SMOKE_HISTORY_THRESHOLD_FAILED: {message}", file=sys.stderr)
    return 0 if not violations else 1
