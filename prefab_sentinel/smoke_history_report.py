from __future__ import annotations

import argparse
import csv
from pathlib import Path
from typing import Any

from prefab_sentinel.json_io import dump_json
from prefab_sentinel.smoke_history import _to_bool, _to_float, _to_int
from prefab_sentinel.smoke_history_stats import (
    _build_target_stats,
    _build_timeout_profiles,
    _compute_applied_assertion_metrics,
    _compute_code_assertion_metrics,
    _compute_observed_timeout_metrics,
    _compute_observed_timeout_metrics_by_target,
    _compute_profile_timeout_metrics,
    _compute_profile_timeout_metrics_by_target,
)


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


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(dump_json(payload), encoding="utf-8")


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
    code_assertions, code_mismatches, code_pass_pct_raw = _compute_code_assertion_metrics(rows)
    code_pass_pct = round(code_pass_pct_raw, 2) if code_pass_pct_raw is not None else None
    applied_assertions, applied_mismatches, applied_pass_pct_raw = _compute_applied_assertion_metrics(rows)
    applied_pass_pct = round(applied_pass_pct_raw, 2) if applied_pass_pct_raw is not None else None
    observed_timeout_by_target = _compute_observed_timeout_metrics_by_target(target_stats)
    observed_timeout_samples, observed_timeout_breaches, observed_timeout_coverage_pct_raw = _compute_observed_timeout_metrics(target_stats)
    observed_timeout_coverage_pct = round(observed_timeout_coverage_pct_raw, 2) if observed_timeout_coverage_pct_raw is not None else None
    profile_timeout_by_target = _compute_profile_timeout_metrics_by_target(profile_payload)
    profile_timeout_samples, profile_timeout_breaches, profile_timeout_coverage_pct_raw = _compute_profile_timeout_metrics(profile_payload)
    profile_timeout_coverage_pct = round(profile_timeout_coverage_pct_raw, 2) if profile_timeout_coverage_pct_raw is not None else None
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
        f"- Observed timeout targets: {len(observed_timeout_by_target)}",
        f"- Observed timeout samples: {observed_timeout_samples}",
        f"- Observed timeout breaches: {observed_timeout_breaches}",
        (
            f"- Observed timeout coverage pct: {observed_timeout_coverage_pct}"
            if observed_timeout_coverage_pct is not None
            else "- Observed timeout coverage pct: n/a"
        ),
        f"- Profile timeout targets: {len(profile_timeout_by_target)}",
        f"- Profile timeout samples: {profile_timeout_samples}",
        f"- Profile timeout breaches: {profile_timeout_breaches}",
        (
            f"- Profile timeout coverage pct: {profile_timeout_coverage_pct}"
            if profile_timeout_coverage_pct is not None
            else "- Profile timeout coverage pct: n/a"
        ),
        "",
        f"| target | runs | failures | code_assertions | code_mismatches | code_pass_pct | applied_assertions | applied_mismatches | applied_pass_pct | observed_timeout_samples | observed_timeout_breaches | observed_timeout_coverage_pct | profile_timeout_samples | profile_timeout_breaches | profile_timeout_coverage_pct | attempts_max | duration_avg_sec | {percentile_label} | duration_max_sec | timeout_max_sec |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for item in target_stats:
        profile_item = profile_timeout_by_target.get(str(item.get("target", "")), {})
        profile_target_samples = _to_int(profile_item.get("duration_sample_count")) or 0
        profile_target_breaches = _to_int(profile_item.get("timeout_breach_count")) or 0
        profile_target_coverage_raw = _to_float(profile_item.get("timeout_coverage_pct"))
        profile_target_coverage = (
            round(profile_target_coverage_raw, 2)
            if profile_target_coverage_raw is not None
            else "n/a"
        )
        lines.append(
            "| {target} | {runs} | {failures} | {code_assertion_runs} | {code_assertion_mismatches} | {code_assertion_pass_pct} | {applied_assertion_runs} | {applied_assertion_mismatches} | {applied_assertion_pass_pct} | {observed_timeout_sample_count} | {observed_timeout_breach_count} | {observed_timeout_coverage_pct} | {profile_timeout_sample_count} | {profile_timeout_breach_count} | {profile_timeout_coverage_pct} | {attempts_max} | {duration_avg_sec} | {duration_p_sec} | {duration_max_sec} | {timeout_max_sec} |".format(
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
                profile_timeout_sample_count=profile_target_samples,
                profile_timeout_breach_count=profile_target_breaches,
                profile_timeout_coverage_pct=profile_target_coverage,
                attempts_max=item.get("attempts_max", ""),
                duration_avg_sec=item.get("duration_avg_sec", ""),
                duration_p_sec=item.get("duration_p_sec", ""),
                duration_max_sec=item.get("duration_max_sec", ""),
                timeout_max_sec=item.get("timeout_max_sec", ""),
            )
        )
    return "\n".join(lines)


def _compute_and_write_outputs(
    args: argparse.Namespace,
    parser: argparse.ArgumentParser,
    rows: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]] | None, dict[str, Any] | None]:
    """Write CSV, compute stats, write MD/profile outputs; return (stats, profile_payload)."""
    header = [
        "source", "batch_success", "batch_severity", "target", "matched_expectation",
        "expected_code", "actual_code", "code_matches", "expected_applied",
        "expected_applied_source", "actual_applied", "applied_matches", "attempts",
        "duration_sec", "unity_timeout_sec", "exit_code", "response_code",
        "response_severity", "response_path", "unity_log_file", "plan", "project_path",
    ]
    out_csv = Path(args.out)
    _write_csv(out_csv, header, rows)
    print(out_csv)

    needs_stats = bool(
        args.out_md
        or args.out_timeout_profile
        or args.max_observed_timeout_breaches is not None
        or args.min_observed_timeout_coverage_pct is not None
        or args.max_observed_timeout_breaches_per_target is not None
        or args.min_observed_timeout_coverage_pct_per_target is not None
        or args.max_profile_timeout_breaches is not None
        or args.min_profile_timeout_coverage_pct is not None
        or args.max_profile_timeout_breaches_per_target is not None
        or args.min_profile_timeout_coverage_pct_per_target is not None
    )
    stats: list[dict[str, Any]] | None = (
        _build_target_stats(rows, args.duration_percentile) if needs_stats else None
    )

    needs_profile_payload = bool(
        args.out_md
        or args.out_timeout_profile
        or args.max_profile_timeout_breaches is not None
        or args.min_profile_timeout_coverage_pct is not None
        or args.max_profile_timeout_breaches_per_target is not None
        or args.min_profile_timeout_coverage_pct_per_target is not None
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

    return stats, profile_payload
