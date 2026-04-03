from __future__ import annotations

import csv
import io
from pathlib import Path
from typing import Any

from prefab_sentinel.json_io import dump_json


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


def _limit_list_field_for_markdown(value: Any, field_name: str, max_items: int) -> Any:
    """Recursively truncate lists under *field_name* to *max_items* entries."""
    if isinstance(value, dict):
        limited: dict[str, Any] = {}
        for key, item in value.items():
            if key == field_name and isinstance(item, list):
                keep = item[:max_items]
                limited[key] = [_limit_list_field_for_markdown(entry, field_name, max_items) for entry in keep]
                if len(item) > max_items:
                    limited[f"{field_name}_total"] = len(item)
                    limited[f"{field_name}_truncated_for_markdown"] = len(item) - len(keep)
                continue
            limited[key] = _limit_list_field_for_markdown(item, field_name, max_items)
        return limited
    if isinstance(value, list):
        return [_limit_list_field_for_markdown(item, field_name, max_items) for item in value]
    return value


def render_markdown_report(
    payload: dict[str, Any],
    md_max_usages: int | None = None,
    md_max_steps: int | None = None,
) -> str:
    diagnostics = payload.get("diagnostics", [])
    payload_data = payload.get("data", {})
    if not isinstance(payload_data, dict):
        payload_data = {}
    if md_max_usages is not None:
        payload_data = _limit_list_field_for_markdown(payload_data, "usages", max(0, md_max_usages))
    if md_max_steps is not None:
        payload_data = _limit_list_field_for_markdown(payload_data, "steps", max(0, md_max_steps))

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
        "# Prefab Sentinel Validation Report",
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
            top_name = top.get("asset_name", "")
            top_label = f"{top.get('guid', '')}"
            if top_name:
                top_label += f" ({top_name})"
            lines.append(
                f"- Top Missing Asset GUID: {top_label} ({top.get('occurrences', 0)})"
            )
        if top_ignored:
            top = top_ignored[0]
            top_name = top.get("asset_name", "")
            top_label = f"{top.get('guid', '')}"
            if top_name:
                top_label += f" ({top_name})"
            lines.append(
                f"- Top Ignored Missing Asset GUID: {top_label} ({top.get('occurrences', 0)})"
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
            dump_json(payload_data),
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


def render_csv_report(
    payload: dict[str, Any],
    *,
    include_summary: bool = False,
) -> str:
    """Render a ToolResponse payload as CSV text.

    Diagnostics table columns: ``path, location, detail, evidence``.
    When *include_summary* is True, a header row with envelope metadata is
    prepended (separated by one blank line from the diagnostics table).
    """
    buf = io.StringIO()

    if include_summary:
        summary_writer = csv.writer(buf)
        summary_writer.writerow(["key", "value"])
        summary_writer.writerow(["success", payload.get("success", "")])
        summary_writer.writerow(["severity", payload.get("severity", "")])
        summary_writer.writerow(["code", payload.get("code", "")])
        summary_writer.writerow(["message", payload.get("message", "")])
        data = payload.get("data", {})
        if isinstance(data, dict):
            for key in (
                "scanned_files",
                "scanned_references",
                "broken_count",
                "broken_occurrences",
            ):
                if key in data:
                    summary_writer.writerow([key, data[key]])
        buf.write("\n")

    diag_writer = csv.writer(buf)
    diag_writer.writerow(["path", "location", "detail", "evidence"])
    for diag in payload.get("diagnostics", []):
        if not isinstance(diag, dict):
            continue
        diag_writer.writerow([
            diag.get("path", ""),
            diag.get("location", ""),
            diag.get("detail", ""),
            diag.get("evidence", ""),
        ])

    return buf.getvalue()


def export_report(
    payload: dict[str, Any],
    output_path: str,
    fmt: str,
    md_max_usages: int | None = None,
    md_max_steps: int | None = None,
    csv_include_summary: bool = False,
) -> Path:
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    if fmt == "json":
        out.write_text(dump_json(payload) + "\n", encoding="utf-8")
    elif fmt == "md":
        out.write_text(
            render_markdown_report(
                payload,
                md_max_usages=md_max_usages,
                md_max_steps=md_max_steps,
            ),
            encoding="utf-8",
        )
    elif fmt == "csv":
        out.write_text(
            render_csv_report(payload, include_summary=csv_include_summary),
            encoding="utf-8",
        )
    else:
        raise ValueError(f"Unsupported format: {fmt}")
    return out
