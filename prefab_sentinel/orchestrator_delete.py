from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from prefab_sentinel.asset_delete import (
    build_delete_plan,
    compute_broken_reference_delta,
)
from prefab_sentinel.contracts import Diagnostic, Severity, ToolResponse, error_response
from prefab_sentinel.editor_bridge import send_action

__all__ = ["delete_assets"]

_UNSUPPORTED_BRIDGE_CODES = frozenset(
    {
        "BRIDGE_WATCH_DIR_MISSING",
        "EDITOR_BRIDGE_UNKNOWN_ACTION",
        "EDITOR_BRIDGE_WATCH_DIR_MISSING",
        "EDITOR_BRIDGE_WATCH_DIR_NOT_FOUND",
        "EDITOR_CTRL_UNKNOWN_ACTION",
    }
)


def delete_assets(
    orch: Any,
    asset_paths: Sequence[str],
    *,
    scope: str | None = None,
    dry_run: bool = True,
    confirm: bool = False,
    change_reason: str | None = None,
) -> ToolResponse:
    if not dry_run and confirm and not (change_reason or "").strip():
        return error_response(
            "CHANGE_REASON_REQUIRED",
            "change_reason is required when confirm=True.",
        )

    plan = build_delete_plan(
        asset_paths,
        project_root=_project_root(orch),
        reference_resolver=orch.reference_resolver,
        scope=scope,
    )
    plan_response = _plan_response(plan)
    if not plan_response.success:
        return plan_response
    if dry_run or not confirm:
        return plan_response

    plan_data = plan_response.data
    planned_asset_paths = [str(target["asset_path"]) for target in plan_data["targets"]]
    bridge_response = send_action(
        action="delete_assets",
        asset_paths_json=json.dumps(planned_asset_paths),
        confirm=True,
        change_reason=change_reason,
    )
    bridge_data = _dict_value(bridge_response.get("data"))
    deleted_paths = list(bridge_data.get("deleted_paths") or [])
    failed_paths = list(bridge_data.get("failed_paths") or [])
    if failed_paths:
        after_scan = _scan_after_delete(orch, scope=scope)
        after_scan_wire = after_scan.to_dict()
        data = {
            "failed_paths": failed_paths,
            "deleted_paths": deleted_paths,
            "plan": plan_data,
            "bridge_response": bridge_response,
        }
        if _is_failed_ref_scan(after_scan):
            data["post_delete_scan"] = after_scan_wire
        else:
            data["broken_reference_delta"] = compute_broken_reference_delta(
                plan_data["pre_delete_broken_references"],
                after_scan_wire,
            )
        return error_response(
            "ASSET_DELETE_FAILED",
            "AssetDatabase.DeleteAssets reported failed paths.",
            data=data,
        )
    if not bool(bridge_response.get("success")):
        return _bridge_failure_response(bridge_response, plan_data)

    after_scan = _scan_after_delete(orch, scope=scope)
    if _is_failed_ref_scan(after_scan):
        return after_scan
    delta = compute_broken_reference_delta(
        plan_data["pre_delete_broken_references"],
        after_scan.to_dict(),
    )
    severity = (
        Severity.WARNING
        if int(delta.get("broken_count_delta", 0)) > 0
        else Severity.INFO
    )
    return ToolResponse(
        True,
        severity,
        "ASSET_DELETE_APPLIED",
        "AssetDatabase delete completed.",
        {
            "plan": plan_data,
            "bridge_response": bridge_response,
            "deleted_paths": deleted_paths,
            "failed_paths": failed_paths,
            "broken_reference_delta": delta,
        },
    )


def _project_root(orch: Any) -> Path:
    if hasattr(orch, "project_root"):
        return Path(orch.project_root)
    return Path(orch.reference_resolver.project_root)


def _plan_response(plan: dict[str, Any]) -> ToolResponse:
    severity = Severity.INFO if plan.get("success") else Severity.ERROR
    return ToolResponse(
        bool(plan.get("success")),
        severity,
        str(plan.get("code", "")),
        str(plan.get("message", "")),
        _dict_value(plan.get("data")),
        [_diagnostic_from_dict(item) for item in plan.get("diagnostics", [])],
    )


def _diagnostic_from_dict(item: Any) -> Diagnostic:
    data = _dict_value(item)
    return Diagnostic(
        path=str(data.get("path", "")),
        location=str(data.get("location", "")),
        detail=str(data.get("code") or data.get("detail", "")),
        evidence=str(data.get("evidence") or data.get("message", "")),
        severity=data.get("severity"),
    )



def _is_failed_ref_scan(response: ToolResponse) -> bool:
    return not response.success and response.code != "REF_SCAN_BROKEN"

def _bridge_failure_response(
    bridge_response: dict[str, Any],
    plan_data: dict[str, Any],
) -> ToolResponse:
    bridge_code = str(bridge_response.get("code", ""))
    if bridge_code in _UNSUPPORTED_BRIDGE_CODES:
        code = "ASSET_DELETE_UNSUPPORTED"
    elif bridge_code == "DELETE_ASSETS_FAILED":
        code = "ASSET_DELETE_FAILED"
    else:
        code = bridge_code or "ASSET_DELETE_FAILED"
    return error_response(
        code,
        str(bridge_response.get("message", "delete_assets bridge action failed.")),
        data={
            "bridge_code": bridge_code,
            "bridge_message": bridge_response.get("message", ""),
            "bridge_response": bridge_response,
            "plan": plan_data,
        },
    )


def _scan_after_delete(orch: Any, *, scope: str | None) -> ToolResponse:
    _invalidate_delete_caches(orch)
    return orch.reference_resolver.scan_broken_references(
        scope=scope if scope is not None else "Assets",
        include_diagnostics=False,
    )


def _invalidate_delete_caches(orch: Any) -> None:
    for method_name in (
        "invalidate_text_cache",
        "invalidate_guid_index",
        "invalidate_scope_files_cache",
    ):
        method = getattr(orch, method_name, None)
        if method is not None:
            method()


def _dict_value(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}
