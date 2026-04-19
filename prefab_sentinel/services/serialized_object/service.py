"""Facade for the serialized-object service.

``SerializedObjectService`` is a thin coordinator that keeps per-target
state (project root, Prefab Variant resolver, Unity bridge config,
before-value cache, resource adapters) and delegates operation
validation, apply flows, and resource-plan dispatch to sibling modules.

See the package-level modules for the actual logic:
``patch_dispatch`` for dry-run / apply-and-save, ``patch_validator``
for per-op validation, ``resource_plan`` / ``resource_adapters`` for
the resource-scoped plan API, and ``resource_bridge`` for Unity Editor
bridge configuration.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from prefab_sentinel.contracts import ToolResponse
from prefab_sentinel.services.prefab_variant import PrefabVariantService
from prefab_sentinel.services.serialized_object import (
    patch_dispatch,
    resource_bridge,
    resource_plan,
)
from prefab_sentinel.services.serialized_object.resource_adapters import (
    _ResourceAdapter,
    build_default_adapters,
)
from prefab_sentinel.unity_assets import find_project_root
from prefab_sentinel.unity_assets_path import resolve_scope_path


class SerializedObjectService:
    """Entry-point facade for dry-run / apply / resource-plan flows."""

    TOOL_NAME = "serialized-object"

    def __init__(
        self,
        bridge_command: tuple[str, ...] | None = None,
        bridge_timeout_sec: float = 120.0,
        project_root: Path | None = None,
        prefab_variant: PrefabVariantService | None = None,
    ) -> None:
        self.bridge = resource_bridge.build_bridge_state(
            bridge_command, bridge_timeout_sec
        )
        self.project_root = find_project_root(project_root or Path.cwd())
        self._prefab_variant = prefab_variant
        self._before_cache: dict[str, str] | None = None
        self._resource_adapters: tuple[_ResourceAdapter, ...] = build_default_adapters()

    def invalidate_before_cache(self) -> None:
        """Reset the before-value cache used by JSON-target dry-run previews."""
        self._before_cache = None

    def _resolve_target_path(self, target: str) -> Path:
        """Resolve *target* against the service project root."""
        return resolve_scope_path(target, self.project_root)

    def dry_run_patch(
        self,
        target: str,
        ops: list[dict[str, Any]],
    ) -> ToolResponse:
        """Validate *ops* against *target* and return a preview envelope."""
        return patch_dispatch.dry_run_patch(self, target, ops)

    def apply_and_save(
        self,
        target: str,
        ops: list[dict[str, Any]],
    ) -> ToolResponse:
        """Validate *ops*, apply them to *target*, and persist the result."""
        return patch_dispatch.apply_and_save(self, target, ops)

    def dry_run_resource_plan(
        self,
        resource: dict[str, Any],
        ops: list[dict[str, Any]],
    ) -> ToolResponse:
        """Validate a resource-level plan and generate a diff preview."""
        return resource_plan.dry_run_resource_plan(self, resource, ops)

    def apply_resource_plan(
        self,
        resource: dict[str, Any],
        ops: list[dict[str, Any]],
    ) -> ToolResponse:
        """Validate and apply a resource-level plan, persisting changes."""
        return resource_plan.apply_resource_plan(self, resource, ops)


__all__ = ["SerializedObjectService"]
