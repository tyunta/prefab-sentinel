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
    from prefab_sentinel.reporting_markdown import render_markdown_report

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
