"""High-level ``dry_run_patch`` / ``apply_and_save`` flows.

Entry points that ``SerializedObjectService`` forwards to.  They
coordinate the property-path pre-validator, per-op validators (scene /
asset-open / json), the JSON apply backend, and the Unity bridge handoff
for non-JSON targets.  The service class is passed explicitly so these
helpers stay free functions that don't import ``service``.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from prefab_sentinel.contracts import Diagnostic, Severity, ToolResponse, error_response, success_response
from prefab_sentinel.services.property_path import validate_property_path
from prefab_sentinel.services.serialized_object import resource_bridge
from prefab_sentinel.services.serialized_object.asset_open_ops import (
    validate_asset_open_ops,
)
from prefab_sentinel.services.serialized_object.patch_json_apply import (
    apply_json_target,
    propagate_dry_run_failure,
)
from prefab_sentinel.services.serialized_object.patch_preview import (
    dry_run_ok,
    plan_invalid,
    soft_warnings_for_preview,
)
from prefab_sentinel.services.serialized_object.patch_validator import validate_op
from prefab_sentinel.services.serialized_object.scene_dispatch import (
    validate_scene_ops,
)

if TYPE_CHECKING:
    from prefab_sentinel.services.serialized_object.service import (
        SerializedObjectService,
    )


def prevalidate_property_paths(
    target: str,
    ops: list[dict[str, Any]],
) -> ToolResponse | None:
    """Emit a ``SER001`` / ``SER002`` envelope on the first bad ``path``.

    Ops without a ``path`` field (caught downstream as ``schema_error``)
    and ops with an empty path (handled by the per-op validators) are
    skipped here so those diagnostics keep their existing shape.
    """
    for index, op in enumerate(ops):
        if not isinstance(op, dict):
            continue
        raw_path = op.get("path")
        if not isinstance(raw_path, str):
            continue
        property_path = raw_path.strip()
        if not property_path:
            continue
        result = validate_property_path(property_path)
        if not result.success:
            data = dict(result.data)
            data.update(
                {
                    "target": target,
                    "op_index": index,
                    "op_count": len(ops),
                    "read_only": True,
                }
            )
            return error_response(
                result.code,
                result.message,
                severity=result.severity,
                data=data,
            )
    return None


def _validate_target_and_ops(
    target: str,
    ops: list[dict[str, Any]],
) -> ToolResponse | None:
    """Reject empty target / empty ops with a ``SER_PLAN_INVALID``."""
    if not str(target).strip():
        return plan_invalid(
            target,
            [
                Diagnostic(
                    path="",
                    location="target",
                    detail="schema_error",
                    evidence="target is required",
                )
            ],
            0,
        )
    if not ops:
        return plan_invalid(
            target,
            [
                Diagnostic(
                    path=target,
                    location="ops",
                    detail="schema_error",
                    evidence="ops must contain at least one operation",
                )
            ],
            0,
        )
    return None


def _dry_run_json_ops(
    service: SerializedObjectService,
    target: str,
    ops: list[dict[str, Any]],
) -> ToolResponse:
    """Dry-run the JSON-target per-op validator and build a preview."""
    diagnostics: list[Diagnostic] = []
    preview: list[dict[str, Any]] = []
    for index, op in enumerate(ops):
        if not isinstance(op, dict):
            diagnostics.append(
                Diagnostic(
                    path=target,
                    location=f"ops[{index}]",
                    detail="schema_error",
                    evidence="operation must be an object",
                )
            )
            continue
        diff_entry = validate_op(service, target, index, op, diagnostics)
        if diff_entry is not None:
            preview.append(diff_entry)

    if diagnostics:
        return plan_invalid(target, diagnostics, len(ops))

    soft_warnings = soft_warnings_for_preview(target, preview)
    if soft_warnings:
        return success_response(
            "SER_DRY_RUN_OK",
            "dry_run_patch generated a patch preview with warnings.",
            severity=Severity.WARNING,
            data={
                "target": target,
                "op_count": len(ops),
                "applied": 0,
                "diff": preview,
                "read_only": True,
            },
            diagnostics=soft_warnings,
        )
    return dry_run_ok(target, ops, preview)


def dry_run_patch(
    service: SerializedObjectService,
    target: str,
    ops: list[dict[str, Any]],
) -> ToolResponse:
    """Validate *ops* against *target* and return a preview-only envelope."""
    service.invalidate_before_cache()
    prevalidation = prevalidate_property_paths(target, ops)
    if prevalidation is not None:
        return prevalidation

    schema_failure = _validate_target_and_ops(target, ops)
    if schema_failure is not None:
        return schema_failure

    target_path = Path(str(target).strip())
    suffix = target_path.suffix.lower()
    inferred_kind = (
        resource_bridge.infer_bridge_resource_kind(target_path)
        if suffix in {".mat", ".asset", ".unity"}
        else ""
    )

    if inferred_kind == "scene":
        diagnostics, preview = validate_scene_ops(target=target, mode="open", ops=ops)
        if diagnostics:
            return plan_invalid(target, diagnostics, len(ops))
        return dry_run_ok(target, ops, preview)

    if inferred_kind in {"asset", "material"}:
        diagnostics, preview = validate_asset_open_ops(
            target=target, kind=inferred_kind, ops=ops
        )
        if diagnostics:
            return plan_invalid(target, diagnostics, len(ops))
        return dry_run_ok(target, ops, preview)

    return _dry_run_json_ops(service, target, ops)


def apply_and_save(
    service: SerializedObjectService,
    target: str,
    ops: list[dict[str, Any]],
) -> ToolResponse:
    """Validate *ops*, apply them to *target*, and persist the result."""
    dry_run_response = dry_run_patch(service, target, ops)
    if not dry_run_response.success:
        return propagate_dry_run_failure(target, ops, dry_run_response)

    target_path = service._resolve_target_path(target)
    if target_path.suffix.lower() != ".json":
        if resource_bridge.is_unity_bridge_target(target_path):
            return resource_bridge.apply_with_unity_bridge(
                service.bridge,
                target_path=target_path,
                ops=ops,
            )
        return error_response(
            "SER_UNSUPPORTED_TARGET",
            "Phase 1 apply backend supports .json or Unity bridge targets only.",
            data={
                "target": str(target_path),
                "op_count": len(ops),
                "applied": 0,
                "read_only": False,
                "executed": False,
            },
        )

    return apply_json_target(target_path, ops)


__all__ = [
    "prevalidate_property_paths",
    "dry_run_patch",
    "apply_and_save",
]
