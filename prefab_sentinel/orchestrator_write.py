"""Write-operation functions extracted from Phase1Orchestrator."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from prefab_sentinel.asset_file_ops import (
    copy_asset as _copy_asset,
    rename_asset as _rename_asset,
)
from prefab_sentinel.bridge_response import is_bridge_response_envelope
from prefab_sentinel.contracts import (
    Diagnostic,
    Severity,
    ToolResponse,
    error_response,
)
from prefab_sentinel.material_asset_writer import write_material_property as _write_material_property
from prefab_sentinel.mcp_patch_writer_boundary import project_patch_writer_refresh

if TYPE_CHECKING:
    from prefab_sentinel.orchestrator import Phase1Orchestrator


def _invalidate_write_caches(orch: Phase1Orchestrator) -> None:
    orch.invalidate_text_cache()
    orch.invalidate_guid_index()
    orch.invalidate_before_cache()
    orch.invalidate_scope_files_cache()


def _execute_write_op(
    orch: Phase1Orchestrator,
    core_fn: Callable[..., dict],
    core_kwargs: dict[str, Any],
    diag_path: str,
    reason_error_code: str,
    dry_run: bool,
    change_reason: str | None,
    operation: str,
) -> ToolResponse:
    if not dry_run and not change_reason:
        return error_response(
            reason_error_code,
            "change_reason is required when confirm=True",
        )

    result = core_fn(**core_kwargs, dry_run=dry_run)
    if not is_bridge_response_envelope(result):
        return error_response(
            "WRITE_RESPONSE_SCHEMA",
            "Write operation returned an invalid response envelope.",
        )
    if any(
        not isinstance(entry, dict)
        or not isinstance(entry.get("detail", ""), str)
        or not isinstance(entry.get("evidence", ""), str)
        for entry in result["diagnostics"]
    ):
        return error_response(
            "WRITE_RESPONSE_SCHEMA",
            "Write operation returned invalid diagnostics.",
        )

    response = ToolResponse(
        success=result["success"],
        severity=Severity(result["severity"]),
        code=result["code"],
        message=result["message"],
        data=result["data"],
        diagnostics=[
            Diagnostic(
                path=diag_path,
                location="",
                detail=d.get("detail", ""),
                evidence=d.get("evidence", ""),
            )
            for d in result["diagnostics"]
        ],
    )
    if not dry_run and response.success:
        return project_patch_writer_refresh(
            operation,
            response,
            orch.maybe_auto_refresh,
            lambda: _invalidate_write_caches(orch),
        )
    return response


def set_material_property(
    orch: Phase1Orchestrator,
    target_path: str,
    property_name: str,
    value: str,
    *,
    dry_run: bool = True,
    change_reason: str | None = None,
) -> ToolResponse:
    return _execute_write_op(
        orch,
        _write_material_property,
        {"target_path": target_path, "property_name": property_name, "value": value},
        diag_path=target_path,
        reason_error_code="MAT_PROP_REASON_REQUIRED",
        dry_run=dry_run,
        change_reason=change_reason,
        operation="set_material_property",
    )


def copy_asset(
    orch: Phase1Orchestrator,
    source_path: str,
    dest_path: str,
    *,
    dry_run: bool = True,
    change_reason: str | None = None,
) -> ToolResponse:
    return _execute_write_op(
        orch,
        _copy_asset,
        {
            "source_path": str(source_path),
            "dest_path": str(dest_path),
            "project_root": orch.reference_resolver.project_root,
        },
        diag_path=source_path,
        reason_error_code="ASSET_OP_REASON_REQUIRED",
        dry_run=dry_run,
        change_reason=change_reason,
        operation="copy_asset",
    )


def rename_asset(
    orch: Phase1Orchestrator,
    asset_path: str,
    new_name: str,
    *,
    dry_run: bool = True,
    change_reason: str | None = None,
) -> ToolResponse:
    return _execute_write_op(
        orch,
        _rename_asset,
        {
            "asset_path": str(asset_path),
            "new_name": str(new_name),
            "project_root": orch.reference_resolver.project_root,
        },
        diag_path=asset_path,
        reason_error_code="ASSET_OP_REASON_REQUIRED",
        dry_run=dry_run,
        change_reason=change_reason,
        operation="rename_asset",
    )
