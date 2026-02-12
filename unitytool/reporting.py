from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def _extract_ref_scan_data(payload_data: dict[str, Any]) -> dict[str, Any]:
    if "categories_occurrences" in payload_data or "top_missing_asset_guids" in payload_data:
        return payload_data

    steps = payload_data.get("steps", [])
    if not isinstance(steps, list):
        return {}

    for step in steps:
        if not isinstance(step, dict):
            continue
        if step.get("step") != "scan_broken_references":
            continue
        result = step.get("result", {})
        if not isinstance(result, dict):
            continue
        data = result.get("data", {})
        if isinstance(data, dict):
            return data
    return {}


def _extract_runtime_validation_data(payload_data: dict[str, Any]) -> dict[str, Any]:
    steps = payload_data.get("steps", [])
    if not isinstance(steps, list):
        return {}

    runtime: dict[str, Any] = {}
    for step in steps:
        if not isinstance(step, dict):
            continue
        step_name = step.get("step")
        result = step.get("result", {})
        if not isinstance(result, dict):
            continue
        data = result.get("data", {})
        if not isinstance(data, dict):
            data = {}

        if step_name == "classify_errors":
            runtime["classification"] = {
                "code": result.get("code"),
                "success": result.get("success"),
                "severity": result.get("severity"),
                "line_count": data.get("line_count", 0),
                "matched_issue_count": data.get("matched_issue_count", 0),
                "categories": data.get("categories", {}),
                "categories_by_severity": data.get("categories_by_severity", {}),
            }
        elif step_name == "assert_no_critical_errors":
            runtime["assertion"] = {
                "code": result.get("code"),
                "success": result.get("success"),
                "severity": result.get("severity"),
                "critical_count": data.get("critical_count", 0),
                "error_count": data.get("error_count", 0),
                "warning_count": data.get("warning_count", 0),
                "allow_warnings": data.get("allow_warnings", False),
            }
        elif step_name in {"compile_udonsharp", "run_clientsim", "collect_unity_console"}:
            runtime[step_name] = {
                "code": result.get("code"),
                "success": result.get("success"),
                "severity": result.get("severity"),
            }
    return runtime


def _limit_usages_for_markdown(value: Any, max_usages: int) -> Any:
    if isinstance(value, dict):
        limited: dict[str, Any] = {}
        for key, item in value.items():
            if key == "usages" and isinstance(item, list):
                keep = item[:max_usages]
                limited[key] = [_limit_usages_for_markdown(entry, max_usages) for entry in keep]
                if len(item) > max_usages:
                    limited["usages_total"] = len(item)
                    limited["usages_truncated_for_markdown"] = len(item) - len(keep)
                continue
            limited[key] = _limit_usages_for_markdown(item, max_usages)
        return limited
    if isinstance(value, list):
        return [_limit_usages_for_markdown(item, max_usages) for item in value]
    return value


def render_markdown_report(
    payload: dict[str, Any],
    md_max_usages: int | None = None,
) -> str:
    diagnostics = payload.get("diagnostics", [])
    payload_data = payload.get("data", {})
    if not isinstance(payload_data, dict):
        payload_data = {}
    if md_max_usages is not None:
        payload_data = _limit_usages_for_markdown(payload_data, max(0, md_max_usages))

    ref_scan = _extract_ref_scan_data(payload_data)
    categories_occ = ref_scan.get("categories_occurrences", {})
    if not isinstance(categories_occ, dict):
        categories_occ = {}
    top_missing = ref_scan.get("top_missing_asset_guids", [])
    if not isinstance(top_missing, list):
        top_missing = []
    top_ignored = ref_scan.get("top_ignored_missing_asset_guids", [])
    if not isinstance(top_ignored, list):
        top_ignored = []
    runtime = _extract_runtime_validation_data(payload_data)

    lines = [
        "# UnityTool Validation Report",
        f"- Success: {payload.get('success')}",
        f"- Severity: {payload.get('severity')}",
        f"- Code: {payload.get('code')}",
        f"- Message: {payload.get('message')}",
        "",
    ]

    if ref_scan:
        lines.extend(
            [
                "## Noise Reduction",
                f"- Missing Asset Occurrences: {categories_occ.get('missing_asset', 0)}",
                f"- Missing Local ID Occurrences: {categories_occ.get('missing_local_id', 0)}",
                f"- Ignored Missing Asset Occurrences: {ref_scan.get('ignored_missing_asset_occurrences', 0)}",
                f"- Skipped External Prefab FileID Checks: {ref_scan.get('skipped_external_prefab_fileid_checks', 0)}",
            ]
        )
        if top_missing:
            top = top_missing[0]
            lines.append(
                "- Top Missing Asset GUID: "
                f"{top.get('guid', '')} ({top.get('occurrences', 0)})"
            )
        if top_ignored:
            top = top_ignored[0]
            lines.append(
                "- Top Ignored Missing Asset GUID: "
                f"{top.get('guid', '')} ({top.get('occurrences', 0)})"
            )
        lines.append("")

    classification = runtime.get("classification", {})
    assertion = runtime.get("assertion", {})
    if classification or assertion:
        categories = classification.get("categories", {})
        if not isinstance(categories, dict):
            categories = {}
        categories_by_severity = classification.get("categories_by_severity", {})
        if not isinstance(categories_by_severity, dict):
            categories_by_severity = {}
        lines.extend(
            [
                "## Runtime Validation",
                f"- Compile Step: {runtime.get('compile_udonsharp', {}).get('code', 'n/a')}",
                f"- ClientSim Step: {runtime.get('run_clientsim', {}).get('code', 'n/a')}",
                f"- Log Collect Step: {runtime.get('collect_unity_console', {}).get('code', 'n/a')}",
                f"- Matched Issues: {classification.get('matched_issue_count', 0)}",
                f"- Log Line Count: {classification.get('line_count', 0)}",
                (
                    "- Severity Counts: "
                    f"critical={categories_by_severity.get('critical', 0)}, "
                    f"error={categories_by_severity.get('error', 0)}, "
                    f"warning={categories_by_severity.get('warning', 0)}"
                ),
                (
                    "- Assertion: "
                    f"{assertion.get('code', 'n/a')} "
                    f"(allow_warnings={assertion.get('allow_warnings', False)})"
                ),
            ]
        )
        if categories:
            lines.extend(
                [
                    "",
                    "| Runtime Category | Count |",
                    "| --- | ---: |",
                ]
            )
            for category, count in sorted(
                categories.items(),
                key=lambda item: (-int(item[1]), str(item[0])),
            ):
                lines.append(f"| {category} | {count} |")
        lines.append("")

    lines.extend(
        [
        "## Data",
        "```json",
        json.dumps(payload_data, ensure_ascii=False, indent=2),
        "```",
        "",
        "## Diagnostics",
    ]
    )
    if diagnostics:
        for index, diag in enumerate(diagnostics, start=1):
            lines.append(f"{index}. {diag.get('detail', 'detail-missing')}")
            lines.append(f"   - Path: {diag.get('path', '')}")
            lines.append(f"   - Location: {diag.get('location', '')}")
            lines.append(f"   - Evidence: {diag.get('evidence', '')}")
    else:
        lines.append("No diagnostics.")
    lines.append("")
    return "\n".join(lines)


def export_report(
    payload: dict[str, Any],
    output_path: str,
    fmt: str,
    md_max_usages: int | None = None,
) -> Path:
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    if fmt == "json":
        out.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    elif fmt == "md":
        out.write_text(
            render_markdown_report(payload, md_max_usages=md_max_usages),
            encoding="utf-8",
        )
    else:
        raise ValueError(f"Unsupported format: {fmt}")
    return out
