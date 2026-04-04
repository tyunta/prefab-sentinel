"""MCP tools for component add/remove operations."""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP

from prefab_sentinel.mcp_helpers import (
    read_asset,
    resolve_component_with_type,
    resolve_game_object_node,
)
from prefab_sentinel.mcp_validation import require_change_reason
from prefab_sentinel.patch_plan import PLAN_VERSION
from prefab_sentinel.session import ProjectSession

__all__ = ["register_component_tools"]


def register_component_tools(server: FastMCP, session: ProjectSession) -> None:
    """Register component manipulation tools on *server*."""

    @server.tool()
    def add_component(
        asset_path: str,
        symbol_path: str,
        component_type: str,
        confirm: bool = False,
        change_reason: str = "",
    ) -> dict[str, Any]:
        """Add a component to an existing GameObject in an open-mode asset.

        Two-phase workflow:
        - confirm=False (default): dry-run preview.
        - confirm=True: applies the change.

        Args:
            asset_path: Asset file path (.prefab, .unity, .asset).
            symbol_path: Symbol path to the target GameObject
                (e.g. "Player" for the root, "Player/Body" for a child).
            component_type: Unity component type to add
                (e.g. "AudioSource", "BoxCollider", "VRC.Udon.UdonBehaviour").
            confirm: Set True to apply (False = dry-run only).
            change_reason: Human-readable reason for the change (audit trail).
        """
        err = require_change_reason(confirm, change_reason)
        if err is not None:
            return err
        text, resolved = read_asset(asset_path, session.project_root)
        tree = session.get_symbol_tree(resolved, text, include_properties=False)
        go_node, err = resolve_game_object_node(tree, symbol_path, asset_path)
        if err is not None:
            return err
        assert go_node is not None

        parts = [p for p in symbol_path.split("/") if p]
        hierarchy_target = "/" + "/".join(parts[1:]) if len(parts) > 1 else "/"

        plan: dict[str, object] = {
            "plan_version": PLAN_VERSION,
            "resources": [{"id": "target", "path": asset_path, "mode": "open"}],
            "ops": [
                {
                    "resource": "target",
                    "op": "add_component",
                    "target": hierarchy_target,
                    "type": component_type,
                },
            ],
        }

        orch = session.get_orchestrator()
        resp = orch.patch_apply(
            plan=plan,
            dry_run=(not confirm),
            confirm=confirm,
            change_reason=change_reason or None,
        )

        result = resp.to_dict()
        if confirm and resp.success:
            session.invalidate_symbol_tree(resolved)
            result["auto_refresh"] = orch.maybe_auto_refresh()
        result["symbol_resolution"] = {
            "symbol_path": symbol_path,
            "hierarchy_target": hierarchy_target,
            "component_type": component_type,
            "file_id": go_node.file_id,
        }
        return result

    @server.tool()
    def remove_component(
        asset_path: str,
        symbol_path: str,
        confirm: bool = False,
        change_reason: str = "",
    ) -> dict[str, Any]:
        """Remove a component from an existing GameObject in an open-mode asset.

        Two-phase workflow:
        - confirm=False (default): dry-run preview.
        - confirm=True: applies the removal.

        Args:
            asset_path: Asset file path (.prefab, .unity, .asset).
            symbol_path: Symbol path to the component to remove
                (e.g. "Player/AudioSource" or
                "Player/Body/MonoBehaviour(PlayerScript)").
            confirm: Set True to apply (False = dry-run only).
            change_reason: Human-readable reason for the change (audit trail).
        """
        err = require_change_reason(confirm, change_reason)
        if err is not None:
            return err
        text, resolved = read_asset(asset_path, session.project_root)
        tree = session.get_symbol_tree(resolved, text, include_properties=False)
        node, component_name, err = resolve_component_with_type(
            tree, symbol_path, asset_path,
        )
        if err is not None:
            return err
        assert node is not None

        plan: dict[str, object] = {
            "plan_version": PLAN_VERSION,
            "resources": [{"id": "target", "path": asset_path, "mode": "open"}],
            "ops": [
                {
                    "resource": "target",
                    "op": "remove_component",
                    "component": component_name,
                },
            ],
        }

        orch = session.get_orchestrator()
        resp = orch.patch_apply(
            plan=plan,
            dry_run=(not confirm),
            confirm=confirm,
            change_reason=change_reason or None,
        )

        result = resp.to_dict()
        if confirm and resp.success:
            session.invalidate_symbol_tree(resolved)
            result["auto_refresh"] = orch.maybe_auto_refresh()
        result["symbol_resolution"] = {
            "symbol_path": symbol_path,
            "resolved_component": component_name,
            "file_id": node.file_id,
            "class_id": node.class_id,
        }
        return result
