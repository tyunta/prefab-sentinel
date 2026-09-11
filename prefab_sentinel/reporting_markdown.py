from __future__ import annotations

from typing import Any

from prefab_sentinel.json_io import dump_json
from prefab_sentinel.reporting import extract_ref_scan_data, extract_runtime_validation_data

__all__ = ["render_markdown_report"]


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

    ref_scan = extract_ref_scan_data(payload_data)
    categories_occ = ref_scan.get("categories_occurrences", {})
    if not isinstance(categories_occ, dict):
        categories_occ = {}
    top_missing = ref_scan.get("top_missing_asset_guids", [])
    if not isinstance(top_missing, list):
        top_missing = []
    top_ignored = ref_scan.get("top_ignored_missing_asset_guids", [])
    if not isinstance(top_ignored, list):
        top_ignored = []
    runtime = extract_runtime_validation_data(payload_data)

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

    if runtime:
        lines.append("## Runtime Validation")
        preflight = runtime.get("preflight")
        if preflight is not None:
            lines.extend([
                f"- Preflight Completed: {preflight.get('completed')}",
                f"- Preflight Diagnostics: {len(preflight.get('diagnostics', []))}",
            ])
        compile_report = runtime.get("compile")
        if compile_report is not None:
            lines.append(f"- Compile Executed: {compile_report.get('executed')}")
            if "code" in compile_report:
                lines.append(f"- Compile Result: {compile_report['code']}")
            if compile_report.get("executed") is True:
                if "program_count" in compile_report:
                    lines.append(f"- Compiled Programs: {compile_report['program_count']}")
                generated = compile_report.get("generated_assets", {})
                lines.append(
                    "- Generated Assets: "
                    f"created={len(generated.get('actual_created_paths', []))}, "
                    f"deleted={len(generated.get('actual_deleted_paths', []))}"
                )
        clientsim = runtime.get("clientsim")
        if clientsim is not None:
            lines.append(f"- ClientSim Executed: {clientsim.get('executed')}")
            side_effect_report = clientsim.get("side_effect_report")
            if isinstance(side_effect_report, dict) and "diff_complete" in side_effect_report:
                lines.append(f"- ClientSim Diff Complete: {side_effect_report['diff_complete']}")
        for collect_step in ("collect_unity_console", "collect_editor_console"):
            if collect_step in runtime:
                lines.append(f"- Log Collect Step: {runtime[collect_step].get('code')}")

    classification = runtime.get("classification", {})
    assertion = runtime.get("assertion", {})
    if assertion:
        lines.append(
            "- Assertion: "
            f"{assertion.get('code', 'n/a')} "
            f"(allow_warnings={assertion.get('allow_warnings', False)})"
        )
    if classification:
        count_by_category = classification.get("count_by_category", {})
        if not isinstance(count_by_category, dict):
            count_by_category = {}
        categories_by_severity = classification.get("categories_by_severity", {})
        if not isinstance(categories_by_severity, dict):
            categories_by_severity = {}
        lines.extend(
            [
                f"- Matched Issues: {classification.get('count_total', 0)}",
                f"- Log Line Count: {classification.get('line_count', 0)}",
                (
                    "- Severity Counts: "
                    f"critical={categories_by_severity.get('critical', 0)}, "
                    f"error={categories_by_severity.get('error', 0)}, "
                    f"warning={categories_by_severity.get('warning', 0)}"
                ),
            ]
        )
        if count_by_category:
            lines.extend(
                [
                    "",
                    "| Runtime Category | Count |",
                    "| --- | ---: |",
                ]
            )
            for category, count in sorted(
                count_by_category.items(),
                key=lambda item: (-int(item[1]), str(item[0])),
            ):
                lines.append(f"| {category} | {count} |")
    if runtime:
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
            diagnostic_data = diag["data"]
            lines.append(f"{index}. {diag['code']}")
            lines.append(f"   - Severity: {diag['severity']}")
            lines.append(f"   - Path: {diagnostic_data.get('path', '')}")
            lines.append(f"   - Location: {diagnostic_data.get('location', '')}")
            lines.append(f"   - Message: {diag['message']}")
    else:
        lines.append("No diagnostics.")
    lines.append("")
    return "\n".join(lines)
