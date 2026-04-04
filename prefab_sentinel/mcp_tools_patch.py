"""MCP tools for patch application and asset operations."""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP

from prefab_sentinel.json_io import load_json
from prefab_sentinel.mcp_validation import require_change_reason
from prefab_sentinel.patch_revert import revert_overrides as revert_overrides_impl
from prefab_sentinel.session import ProjectSession

__all__ = ["register_patch_tools"]


def register_patch_tools(server: FastMCP, session: ProjectSession) -> None:
    """Register patch and asset operation tools on *server*."""

    @server.tool()
    def set_material_property(
        asset_path: str,
        property_name: str,
        value: str,
        confirm: bool = False,
        change_reason: str = "",
    ) -> dict[str, Any]:
        """Set a single property in a .mat file (offline YAML editing).

        Two-phase workflow:
        - confirm=False (default): dry-run preview showing before/after.
        - confirm=True: applies the change and writes back.

        Value format depends on property category:
        - Float: "0.5"
        - Int: "2"
        - Color: "[1, 0.8, 0.6, 1]" (RGBA)
        - Texture: "guid:abc123..." or "path:Assets/Tex/foo.png" or "" (null)

        Args:
            asset_path: Path to the .mat file.
            property_name: Property name (e.g. "_Glossiness", "_Color").
            value: New value as string.
            confirm: Set True to apply (False = dry-run only).
            change_reason: Required when confirm=True. Audit log reason.
        """
        err = require_change_reason(confirm, change_reason)
        if err is not None:
            return err
        orch = session.get_orchestrator()
        resp = orch.set_material_property(
            target_path=asset_path,
            property_name=property_name,
            value=value,
            dry_run=not confirm,
            change_reason=change_reason or None,
        )
        return resp.to_dict()

    @server.tool()
    def copy_asset(
        source_path: str,
        dest_path: str,
        confirm: bool = False,
        change_reason: str = "",
    ) -> dict[str, Any]:
        """Copy a Unity text asset with automatic m_Name sync and .meta generation.

        Two-phase workflow:
        - confirm=False (default): dry-run preview showing planned changes.
        - confirm=True: applies the copy and writes new .meta.

        Args:
            source_path: Path to the source asset file.
            dest_path: Path for the new copy.
            confirm: Set True to apply (False = dry-run only).
            change_reason: Required when confirm=True. Audit log reason.
        """
        err = require_change_reason(confirm, change_reason)
        if err is not None:
            return err
        orch = session.get_orchestrator()
        resp = orch.copy_asset(
            source_path=source_path,
            dest_path=dest_path,
            dry_run=not confirm,
            change_reason=change_reason or None,
        )
        return resp.to_dict()

    @server.tool()
    def rename_asset(
        asset_path: str,
        new_name: str,
        confirm: bool = False,
        change_reason: str = "",
    ) -> dict[str, Any]:
        """Rename a Unity text asset with automatic m_Name sync and .meta rename.

        Two-phase workflow:
        - confirm=False (default): dry-run preview showing planned changes.
        - confirm=True: applies the rename.

        Args:
            asset_path: Path to the asset file to rename.
            new_name: New filename (with extension, e.g. "NewName.mat").
            confirm: Set True to apply (False = dry-run only).
            change_reason: Required when confirm=True. Audit log reason.
        """
        err = require_change_reason(confirm, change_reason)
        if err is not None:
            return err
        orch = session.get_orchestrator()
        resp = orch.rename_asset(
            asset_path=asset_path,
            new_name=new_name,
            dry_run=not confirm,
            change_reason=change_reason or None,
        )
        return resp.to_dict()

    @server.tool()
    def patch_apply(
        plan: str | dict,
        confirm: bool = False,
        change_reason: str = "",
        scope: str | None = None,
        runtime_scene: str | None = None,
        runtime_profile: str = "default",
        runtime_log_file: str | None = None,
        runtime_since_timestamp: str | None = None,
        runtime_allow_warnings: bool = False,
        runtime_max_diagnostics: int = 200,
    ) -> dict[str, Any]:
        """Validate and apply a patch plan to Unity assets.

        Two-phase workflow:
        - confirm=False (default): dry-run validation only.
        - confirm=True: applies changes and runs post-apply checks.

        Args:
            plan: Patch plan as JSON string. Must conform to plan_version "2".
            confirm: Set True to apply (False = dry-run only).
            change_reason: Required when confirm=True. Audit log reason.
            scope: Directory for post-apply reference validation.
            runtime_scene: Scene path for post-apply runtime validation.
            runtime_profile: ClientSim profile for runtime validation.
            runtime_log_file: Unity log file path for runtime validation.
            runtime_since_timestamp: Log cursor for runtime validation.
            runtime_allow_warnings: Allow warnings in runtime validation.
            runtime_max_diagnostics: Max diagnostics for runtime validation.
        """
        err = require_change_reason(confirm, change_reason)
        if err is not None:
            return err
        if isinstance(plan, dict):
            plan_dict = plan
        else:
            try:
                plan_dict = load_json(plan)
            except (ValueError, TypeError) as exc:
                return {
                    "success": False, "severity": "error", "code": "INVALID_PLAN_JSON",
                    "message": f"Failed to parse plan JSON: {exc}",
                    "data": {}, "diagnostics": [],
                }

        orch = session.get_orchestrator()
        try:
            resp = orch.patch_apply(
                plan=plan_dict,
                dry_run=not confirm,
                confirm=confirm,
                plan_sha256=None,
                plan_signature=None,
                change_reason=change_reason or None,
                scope=scope,
                runtime_scene=runtime_scene,
                runtime_profile=runtime_profile,
                runtime_log_file=runtime_log_file,
                runtime_since_timestamp=runtime_since_timestamp,
                runtime_allow_warnings=runtime_allow_warnings,
                runtime_max_diagnostics=runtime_max_diagnostics,
            )
        except ValueError as exc:
            return {
                "success": False, "severity": "error",
                "code": "INVALID_PLAN_SCHEMA",
                "message": f"Plan validation failed: {exc}",
                "data": {}, "diagnostics": [],
            }
        result = resp.to_dict()
        if confirm and resp.success:
            orch_ref = session.get_orchestrator()
            result["auto_refresh"] = orch_ref.maybe_auto_refresh()
        return result

    @server.tool()
    def revert_overrides(
        asset_path: str,
        target_file_id: str,
        property_path: str,
        confirm: bool = False,
        change_reason: str = "",
    ) -> dict[str, Any]:
        """Remove a specific property override from a Prefab Variant.

        Two-phase workflow:
        - confirm=False (default): dry-run preview showing what would be removed.
        - confirm=True: applies the removal and writes back.

        Args:
            asset_path: Path to the Prefab Variant file.
            target_file_id: fileID of the target component in the parent prefab.
            property_path: propertyPath of the override to remove.
            confirm: Set True to apply (False = dry-run only).
            change_reason: Required when confirm=True. Audit log reason.
        """
        err = require_change_reason(confirm, change_reason)
        if err is not None:
            return err
        resp = revert_overrides_impl(
            variant_path=asset_path,
            target_file_id=target_file_id,
            property_path=property_path,
            dry_run=not confirm,
            confirm=confirm,
            change_reason=change_reason or None,
        )
        result = resp.to_dict()
        if confirm and resp.success:
            orch = session.get_orchestrator()
            result["auto_refresh"] = orch.maybe_auto_refresh()
        return result
