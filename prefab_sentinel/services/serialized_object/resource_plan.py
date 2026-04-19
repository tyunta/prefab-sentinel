"""Resource-level plan context, formatters, and entry points.

Carries the ``_ResourcePlanContext`` dataclass shared by all adapters,
the four envelope composers used for plan responses, and the
``dry_run_resource_plan`` / ``apply_resource_plan`` entry points driven
by ``SerializedObjectService``.  Adapter classes live in
``resource_adapters`` to keep each file under the 300-line limit.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from prefab_sentinel.contracts import Diagnostic, ToolResponse, error_response, success_response
from prefab_sentinel.services.serialized_object import resource_bridge

if TYPE_CHECKING:
    from prefab_sentinel.services.serialized_object.resource_adapters import (
        _ResourceAdapter,
    )
    from prefab_sentinel.services.serialized_object.service import (
        SerializedObjectService,
    )


@dataclass(frozen=True)
class _ResourcePlanContext:
    target: str
    kind: str
    mode: str
    target_path: Path
    ops: list[dict[str, Any]]


def resource_plan_invalid_response(
    *,
    context: _ResourcePlanContext,
    diagnostics: list[Diagnostic],
    read_only: bool,
) -> ToolResponse:
    return error_response(
        "SER_PLAN_INVALID",
        "Patch plan schema validation failed.",
        data={
            "target": context.target,
            "kind": context.kind,
            "mode": context.mode,
            "op_count": len(context.ops),
            "read_only": read_only,
        },
        diagnostics=diagnostics,
    )


def resource_plan_apply_invalid_response(
    *,
    context: _ResourcePlanContext,
    diagnostics: list[Diagnostic],
) -> ToolResponse:
    return error_response(
        "SER_PLAN_INVALID",
        "Patch plan schema validation failed.",
        data={
            "target": context.target,
            "kind": context.kind,
            "mode": context.mode,
            "op_count": len(context.ops),
            "applied": 0,
            "read_only": False,
            "executed": False,
        },
        diagnostics=diagnostics,
    )


def resource_plan_preview_response(
    *,
    context: _ResourcePlanContext,
    preview: list[dict[str, Any]],
) -> ToolResponse:
    return success_response(
        "SER_DRY_RUN_OK",
        "dry_run_patch generated a patch preview.",
        data={
            "target": context.target,
            "kind": context.kind,
            "mode": context.mode,
            "op_count": len(context.ops),
            "applied": 0,
            "diff": preview,
            "read_only": True,
        },
    )


def unsupported_resource_plan_response(
    *,
    service: SerializedObjectService,
    context: _ResourcePlanContext,
    read_only: bool,
) -> ToolResponse:
    target_value = context.target
    if not read_only and context.target:
        target_value = str(service._resolve_target_path(context.target))
    data: dict[str, Any] = {
        "target": target_value,
        "kind": context.kind,
        "mode": context.mode,
        "op_count": len(context.ops),
        "read_only": read_only,
    }
    if not read_only:
        data.update({"applied": 0, "executed": False})
    return error_response(
        "SER_UNSUPPORTED_TARGET",
        "Resource mode/kind combination is not supported by the current backend.",
        data=data,
    )


def _resolve_resource_context(
    resource: dict[str, Any],
    ops: list[dict[str, Any]],
) -> _ResourcePlanContext:
    target = str(resource.get("path", "")).strip()
    kind = str(resource.get("kind", "")).strip().lower()
    target_path = Path(target) if target else Path()
    if not kind and target:
        kind = resource_bridge.infer_resource_kind(target_path)
    mode = str(resource.get("mode", "open")).strip().lower() or "open"
    return _ResourcePlanContext(
        target=target,
        kind=kind,
        mode=mode,
        target_path=target_path,
        ops=ops,
    )


def _select_adapter(
    adapters: tuple[_ResourceAdapter, ...],
    context: _ResourcePlanContext,
) -> _ResourceAdapter | None:
    for adapter in adapters:
        if adapter.supports(context):
            return adapter
    return None


def dry_run_resource_plan(
    service: SerializedObjectService,
    resource: dict[str, Any],
    ops: list[dict[str, Any]],
) -> ToolResponse:
    context = _resolve_resource_context(resource, ops)
    adapter = _select_adapter(service._resource_adapters, context)
    if adapter is None:
        return unsupported_resource_plan_response(
            service=service, context=context, read_only=True
        )
    return adapter.dry_run(service, context)


def apply_resource_plan(
    service: SerializedObjectService,
    resource: dict[str, Any],
    ops: list[dict[str, Any]],
) -> ToolResponse:
    context = _resolve_resource_context(resource, ops)
    if context.target:
        context = _ResourcePlanContext(
            target=context.target,
            kind=context.kind,
            mode=context.mode,
            target_path=service._resolve_target_path(context.target),
            ops=context.ops,
        )
    adapter = _select_adapter(service._resource_adapters, context)
    if adapter is None:
        return unsupported_resource_plan_response(
            service=service, context=context, read_only=False
        )
    return adapter.apply(service, context)


__all__ = [
    "dry_run_resource_plan",
    "apply_resource_plan",
    "resource_plan_invalid_response",
    "resource_plan_apply_invalid_response",
    "resource_plan_preview_response",
    "unsupported_resource_plan_response",
]
