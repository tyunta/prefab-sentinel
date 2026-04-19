"""Resource-plan adapter classes.

One adapter per ``resource.kind``; each adapter dispatches on
``resource.mode`` to either a pre-existing validator (open mode), a
create-mode validator chain, or a Unity bridge invocation (for apply).
Adapters carry no state — they receive a ``SerializedObjectService``
instance plus a ``_ResourcePlanContext`` on each call.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from prefab_sentinel.contracts import ToolResponse
from prefab_sentinel.services.serialized_object import resource_bridge
from prefab_sentinel.services.serialized_object.asset_create_ops import (
    validate_asset_create_ops,
)
from prefab_sentinel.services.serialized_object.prefab_create_dispatch import (
    validate_prefab_create_ops,
)
from prefab_sentinel.services.serialized_object.resource_plan import (
    _ResourcePlanContext,
    resource_plan_apply_invalid_response,
    resource_plan_invalid_response,
    resource_plan_preview_response,
)
from prefab_sentinel.services.serialized_object.scene_dispatch import (
    validate_scene_ops,
)

if TYPE_CHECKING:
    from prefab_sentinel.services.serialized_object.service import (
        SerializedObjectService,
    )


class _ResourceAdapter:
    supported_kind = ""
    supported_modes: frozenset[str] = frozenset({"open"})

    def supports(self, context: _ResourcePlanContext) -> bool:
        return (
            context.kind == self.supported_kind
            and context.mode in self.supported_modes
        )

    def dry_run(
        self,
        service: SerializedObjectService,
        context: _ResourcePlanContext,
    ) -> ToolResponse:
        raise NotImplementedError

    def apply(
        self,
        service: SerializedObjectService,
        context: _ResourcePlanContext,
    ) -> ToolResponse:
        raise NotImplementedError


class _BridgeBackedAdapter(_ResourceAdapter):
    """Adapters whose ``apply`` delegates to ``resource_bridge.apply_with_unity_bridge``.

    Concrete subclasses implement ``dry_run``; ``apply`` is shared because
    every bridge-backed kind runs the same sequence: dry-run, propagate
    schema failures, then invoke the Unity bridge with the same arguments.
    """

    def apply(self, service, context):  # type: ignore[override]
        dry_run = self.dry_run(service, context)
        if not dry_run.success:
            return resource_plan_apply_invalid_response(
                context=context, diagnostics=dry_run.diagnostics
            )
        return resource_bridge.apply_with_unity_bridge(
            service.bridge,
            target_path=context.target_path,
            ops=context.ops,
            resource_kind=context.kind,
            resource_mode=context.mode,
        )


class _JsonResourceAdapter(_ResourceAdapter):
    supported_kind = "json"

    def dry_run(self, service, context):  # type: ignore[override]
        return service.dry_run_patch(target=context.target, ops=context.ops)

    def apply(self, service, context):  # type: ignore[override]
        return service.apply_and_save(target=context.target, ops=context.ops)


class _PrefabResourceAdapter(_BridgeBackedAdapter):
    supported_kind = "prefab"
    supported_modes = frozenset({"open", "create"})

    def dry_run(self, service, context):  # type: ignore[override]
        if context.mode == "open":
            return service.dry_run_patch(target=context.target, ops=context.ops)
        diagnostics, preview = validate_prefab_create_ops(
            context.target, context.ops
        )
        if diagnostics:
            return resource_plan_invalid_response(
                context=context, diagnostics=diagnostics, read_only=True
            )
        return resource_plan_preview_response(context=context, preview=preview)


class _BridgeBackedAssetResourceAdapter(_BridgeBackedAdapter):
    supported_modes = frozenset({"open", "create"})

    def dry_run(self, service, context):  # type: ignore[override]
        if context.mode == "open":
            return service.dry_run_patch(target=context.target, ops=context.ops)
        diagnostics, preview = validate_asset_create_ops(
            target=context.target, kind=context.kind, ops=context.ops
        )
        if diagnostics:
            return resource_plan_invalid_response(
                context=context, diagnostics=diagnostics, read_only=True
            )
        return resource_plan_preview_response(context=context, preview=preview)


class _AssetResourceAdapter(_BridgeBackedAssetResourceAdapter):
    supported_kind = "asset"


class _MaterialResourceAdapter(_BridgeBackedAssetResourceAdapter):
    supported_kind = "material"


class _SceneResourceAdapter(_BridgeBackedAdapter):
    supported_kind = "scene"
    supported_modes = frozenset({"open", "create"})

    def dry_run(self, service, context):  # type: ignore[override]
        diagnostics, preview = validate_scene_ops(
            target=context.target, mode=context.mode, ops=context.ops
        )
        if diagnostics:
            return resource_plan_invalid_response(
                context=context, diagnostics=diagnostics, read_only=True
            )
        return resource_plan_preview_response(context=context, preview=preview)


def build_default_adapters() -> tuple[_ResourceAdapter, ...]:
    return (
        _JsonResourceAdapter(),
        _PrefabResourceAdapter(),
        _AssetResourceAdapter(),
        _MaterialResourceAdapter(),
        _SceneResourceAdapter(),
    )


__all__ = ["build_default_adapters"]
