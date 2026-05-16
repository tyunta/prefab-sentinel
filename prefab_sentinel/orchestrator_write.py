"""Write-operation functions extracted from Phase1Orchestrator."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from prefab_sentinel.asset_file_ops import (
    copy_asset as _copy_asset,
    rename_asset as _rename_asset,
)
from prefab_sentinel.contracts import (
    Diagnostic,
    Severity,
    ToolResponse,
    error_response,
)
from prefab_sentinel.material_asset_writer import write_material_property as _write_material_property

if TYPE_CHECKING:
    from prefab_sentinel.orchestrator import Phase1Orchestrator


def _execute_write_op(
    orch: Phase1Orchestrator,
    core_fn: Callable[..., dict],
    core_kwargs: dict[str, Any],
    diag_path: str,
    reason_error_code: str,
    dry_run: bool,
    change_reason: str | None,
) -> ToolResponse:
    if not dry_run and not change_reason:
        return error_response(
            reason_error_code,
            "change_reason is required when confirm=True",
        )

    result = core_fn(**core_kwargs, dry_run=dry_run)

    if not dry_run and result.get("success"):
        result["data"]["auto_refresh"] = orch.maybe_auto_refresh()

    severity = Severity.INFO if result["success"] else Severity.ERROR
    return ToolResponse(
        success=result["success"],
        severity=severity,
        code=result["code"],
        message=result["message"],
        data=result.get("data", {}),
        diagnostics=[
            Diagnostic(
                path=diag_path,
                location="",
                detail=d.get("detail", ""),
                evidence=d.get("evidence", ""),
            )
            for d in result.get("diagnostics", [])
        ],
    )


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
        {"source_path": str(source_path), "dest_path": str(dest_path)},
        diag_path=source_path,
        reason_error_code="ASSET_OP_REASON_REQUIRED",
        dry_run=dry_run,
        change_reason=change_reason,
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
        {"asset_path": str(asset_path), "new_name": str(new_name)},
        diag_path=asset_path,
        reason_error_code="ASSET_OP_REASON_REQUIRED",
        dry_run=dry_run,
        change_reason=change_reason,
    )
