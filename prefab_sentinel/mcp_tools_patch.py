"""MCP tools for patch application and asset operations."""

from __future__ import annotations

import logging
from typing import Any

from mcp.server import MCPServer

from prefab_sentinel.json_io import load_json
from prefab_sentinel.mcp_patch_writer_boundary import (
    dispatch_patch_writer,
    execute_patch_writer,
    project_patch_writer_refresh,
)
from prefab_sentinel.mcp_validation import require_change_reason
from prefab_sentinel.patch_revert import revert_overrides as revert_overrides_impl
from prefab_sentinel.patch_transaction_results import boundary_failure
from prefab_sentinel.session import ProjectSession

__all__ = ["register_patch_tools"]

_LOGGER = logging.getLogger(__name__)





def register_patch_tools(server: MCPServer, session: ProjectSession) -> None:
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
        return execute_patch_writer(
            "set_material_property",
            confirm,
            session.get_orchestrator,
            lambda orch: orch.set_material_property(
                target_path=asset_path,
                property_name=property_name,
                value=value,
                dry_run=not confirm,
                change_reason=change_reason or None,
            ),
            session.invalidate_all,
        )

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
        return execute_patch_writer(
            "copy_asset",
            confirm,
            session.get_orchestrator,
            lambda orch: orch.copy_asset(
                source_path=source_path,
                dest_path=dest_path,
                dry_run=not confirm,
                change_reason=change_reason or None,
            ),
            session.invalidate_all,
        )

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
        return execute_patch_writer(
            "rename_asset",
            confirm,
            session.get_orchestrator,
            lambda orch: orch.rename_asset(
                asset_path=asset_path,
                new_name=new_name,
                dry_run=not confirm,
                change_reason=change_reason or None,
            ),
            session.invalidate_all,
        )

    @server.tool()
    def delete_asset(
        asset_path: str,
        scope: str | None = None,
        dry_run: bool = True,
        confirm: bool = False,
        change_reason: str = "",
    ) -> dict[str, Any]:
        """Plan or delete one Unity asset through the AssetDatabase bridge.

        Two-phase workflow:
        - dry_run=True (default): returns deletion impact only.
        - dry_run=False and confirm=True: applies through Unity AssetDatabase.

        Args:
            asset_path: Project-relative asset path under Assets/.
            scope: Optional scope for reference impact and post-delete scan.
            dry_run: Keep the request read-only when True.
            confirm: Explicit apply gate.
            change_reason: Required for effective apply.
        """
        effective_apply = not dry_run and confirm
        err = require_change_reason(effective_apply, change_reason)
        if err is not None:
            return err
        return execute_patch_writer(
            "delete_asset",
            effective_apply,
            lambda: (session.get_orchestrator(), session.resolve_scope(scope)),
            lambda acquired: acquired[0].delete_assets(
                [asset_path],
                scope=acquired[1],
                dry_run=dry_run,
                confirm=confirm,
                change_reason=change_reason or None,
            ),
            session.invalidate_all,
        )

    @server.tool()
    def delete_assets(
        asset_paths: list[str],
        scope: str | None = None,
        dry_run: bool = True,
        confirm: bool = False,
        change_reason: str = "",
    ) -> dict[str, Any]:
        """Plan or delete a batch of Unity assets through AssetDatabase.

        Two-phase workflow:
        - dry_run=True (default): returns deletion impact only.
        - dry_run=False and confirm=True: applies through Unity AssetDatabase.

        Args:
            asset_paths: Project-relative asset paths under Assets/.
            scope: Optional scope for reference impact and post-delete scan.
            dry_run: Keep the request read-only when True.
            confirm: Explicit apply gate.
            change_reason: Required for effective apply.
        """
        effective_apply = not dry_run and confirm
        err = require_change_reason(effective_apply, change_reason)
        if err is not None:
            return err
        return execute_patch_writer(
            "delete_assets",
            effective_apply,
            lambda: (session.get_orchestrator(), session.resolve_scope(scope)),
            lambda acquired: acquired[0].delete_assets(
                asset_paths,
                scope=acquired[1],
                dry_run=dry_run,
                confirm=confirm,
                change_reason=change_reason or None,
            ),
            session.invalidate_all,
        )

    @server.tool()
    def patch_apply(
        plan: Any,
        confirm: bool = False,
        change_reason: str = "",
        out_report: str | None = None,
        scope: str | None = None,
    ) -> dict[str, Any]:
        """Validate and apply a patch plan to Unity assets.

        Runtime validation is a separate validate_runtime operation.
        """
        err = require_change_reason(confirm, change_reason)
        if err is not None:
            return err
        if isinstance(plan, dict):
            plan_dict = plan
        elif isinstance(plan, str):
            try:
                plan_dict = load_json(plan)
            except (ValueError, TypeError):
                _LOGGER.debug("Invalid patch plan JSON", exc_info=True)
                return {
                    "success": False,
                    "severity": "error",
                    "code": "INVALID_PLAN_JSON",
                    "message": "Patch plan JSON is invalid.",
                    "data": {},
                    "diagnostics": [],
                }
        else:
            plan_dict = plan
        if not isinstance(plan_dict, dict):
            return {
                "success": False,
                "severity": "error",
                "code": "INVALID_PLAN_SCHEMA",
                "message": "Plan validation failed: Patch plan root must be an object.",
                "data": {},
                "diagnostics": [],
            }

        try:
            orch = session.get_orchestrator()
        except Exception as exc:
            return boundary_failure("apply", exc, state_unknown=False).to_dict()

        try:
            resp = orch.patch_apply(
                plan=plan_dict,
                dry_run=not confirm,
                confirm=confirm,
                plan_sha256=None,
                plan_signature=None,
                change_reason=change_reason or None,
                out_report=out_report,
                scope=scope,
                transactional=True,
            )
        except ValueError:
            _LOGGER.debug("Invalid patch plan schema", exc_info=True)
            return {
                "success": False,
                "severity": "error",
                "code": "INVALID_PLAN_SCHEMA",
                "message": "Patch plan schema is invalid.",
                "data": {},
                "diagnostics": [],
            }
        except Exception as exc:
            return boundary_failure("apply", exc, state_unknown=confirm).to_dict()

        result = resp.to_dict()
        result_data = result.get("data")
        transaction_finalized = (
            isinstance(result_data, dict)
            and isinstance(result_data.get("transaction"), dict)
        )
        if confirm and resp.success and not transaction_finalized:
            try:
                result["auto_refresh"] = orch.maybe_auto_refresh()
            except Exception as exc:
                return boundary_failure("apply", exc, state_unknown=True).to_dict()
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
        def dispatch_revert() -> dict[str, Any]:
            response = revert_overrides_impl(
                variant_path=asset_path,
                target_file_id=target_file_id,
                property_path=property_path,
                dry_run=not confirm,
                confirm=confirm,
                change_reason=change_reason or None,
            )
            if confirm and response.success:
                response = project_patch_writer_refresh(
                    "revert_overrides",
                    response,
                    lambda: session.get_orchestrator().maybe_auto_refresh(),
                    session.invalidate_all,
                )
            return response.to_dict()

        result, failure = dispatch_patch_writer(
            "revert_overrides",
            confirm,
            dispatch_revert,
            session.invalidate_all,
        )
        if failure is not None:
            return failure
        assert result is not None
        return result
