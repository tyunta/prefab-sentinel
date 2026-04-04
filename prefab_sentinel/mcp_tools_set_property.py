"""MCP tools for setting serialized field values."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP

from prefab_sentinel.json_io import dump_json
from prefab_sentinel.mcp_helpers import (
    find_component_on_go,
    read_asset,
    resolve_component_with_type,
    resolve_game_object_node,
)
from prefab_sentinel.patch_plan import PLAN_VERSION
from prefab_sentinel.session import ProjectSession

__all__ = ["register_set_property_tools"]


def register_set_property_tools(server: FastMCP, session: ProjectSession) -> None:
    """Register property-setting tools on *server*."""

    @server.tool()
    def set_property(
        asset_path: str,
        symbol_path: str,
        property_path: str,
        value: Any,
        confirm: bool = False,
        change_reason: str = "",
    ) -> dict[str, Any]:
        """Set a serialized field value on a component identified by symbol path.

        Two-phase workflow:
        - confirm=False (default): dry-run preview of changes.
        - confirm=True: applies changes to disk.

        Args:
            asset_path: Asset file path (.prefab, .unity, .asset, .mat).
            symbol_path: Human-readable path to a component
                (e.g. "CharacterBody/MeshRenderer" or
                "CharacterBody/MonoBehaviour(PlayerScript)").
            property_path: Serialized property path (e.g. "m_Speed",
                "m_Materials.Array.data[0]").
            value: New value to set (string, number, or object reference dict).
            confirm: Set True to apply changes (False = dry-run only).
            change_reason: Human-readable reason for the change (audit trail).
        """
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
                    "op": "set",
                    "component": component_name,
                    "path": property_path,
                    "value": value,
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
            "property_path": property_path,
        }
        return result

    @server.tool()
    def set_component_fields(
        asset_path: str,
        symbol_path: str,
        component: str,
        fields: dict[str, Any],
        dry_run: bool = False,
        confirm: bool = False,
        change_reason: str | None = None,
        out_report: str | None = None,
    ) -> dict[str, Any]:
        """Set multiple serialized field values on a component in a single transaction.

        Two-phase workflow:
        - confirm=False (default): dry-run preview of changes.
        - confirm=True: applies changes to disk (requires change_reason + out_report).
        - dry_run=True: explicit preview flag (overrides confirm if both are True).

        Args:
            asset_path: Asset file path (.prefab, .unity, .asset).
            symbol_path: Human-readable path to the target GameObject
                (e.g. "Controller" or "Body/Head").
            component: Component type name on the GameObject
                (e.g. "MeshRenderer" or "DualButtonController").
            fields: Mapping of property paths to new values
                ({property_path: value, ...}).
            dry_run: Explicit preview flag; overrides confirm when both are True.
            confirm: Set True to apply changes (requires change_reason + out_report).
            change_reason: Human-readable reason for the change (required when confirm=True).
            out_report: Path to write result JSON report (required when confirm=True).
        """
        if not fields:
            return {
                "success": False,
                "severity": "error",
                "code": "EMPTY_FIELDS",
                "message": "fields dict must not be empty.",
                "data": {},
                "diagnostics": [],
            }

        effective_dry_run = dry_run or not confirm
        effective_confirm = confirm and not dry_run

        if effective_confirm and not change_reason:
            return {
                "success": False,
                "severity": "error",
                "code": "CHANGE_REASON_REQUIRED",
                "message": "change_reason is required when confirm=True.",
                "data": {},
                "diagnostics": [],
            }
        if effective_confirm and not out_report:
            return {
                "success": False,
                "severity": "error",
                "code": "OUT_REPORT_REQUIRED",
                "message": "out_report is required when confirm=True.",
                "data": {},
                "diagnostics": [],
            }

        if effective_confirm and session.project_root is None:
            return {
                "success": False,
                "severity": "error",
                "code": "PROJECT_ROOT_REQUIRED",
                "message": "out_report requires a configured project_root for path containment.",
                "data": {},
                "diagnostics": [],
            }

        report_path = Path(out_report).resolve() if out_report else None
        if (
            report_path is not None
            and session.project_root is not None
            and not report_path.is_relative_to(Path(session.project_root).resolve())
        ):
            return {
                "success": False,
                "severity": "error",
                "code": "OUT_REPORT_OUTSIDE_PROJECT",
                "message": (
                    f"out_report must be within the project root: "
                    f"{Path(session.project_root).resolve()}"
                ),
                "data": {},
                "diagnostics": [],
            }

        text, resolved = read_asset(asset_path, session.project_root)
        tree = session.get_symbol_tree(resolved, text, include_properties=False)
        go_node, err = resolve_game_object_node(tree, symbol_path, asset_path)
        if err is not None:
            return err
        assert go_node is not None

        node, component_name, err = find_component_on_go(go_node, component, asset_path)
        if err is not None:
            return err
        assert node is not None

        ops = [
            {
                "resource": "target",
                "op": "set",
                "component": component_name,
                "path": field_path,
                "value": field_value,
            }
            for field_path, field_value in fields.items()
        ]
        plan: dict[str, object] = {
            "plan_version": PLAN_VERSION,
            "resources": [{"id": "target", "path": asset_path, "mode": "open"}],
            "ops": ops,
        }

        orch = session.get_orchestrator()
        resp = orch.patch_apply(
            plan=plan,
            dry_run=effective_dry_run,
            confirm=effective_confirm,
            change_reason=change_reason or None,
        )

        result = resp.to_dict()
        if effective_confirm and resp.success:
            session.invalidate_symbol_tree(resolved)
            result["auto_refresh"] = orch.maybe_auto_refresh()
        result["symbol_resolution"] = {
            "symbol_path": symbol_path,
            "resolved_component": component_name,
            "file_id": node.file_id,
            "class_id": node.class_id,
            "fields": list(fields.keys()),
        }

        if report_path is not None and effective_confirm:
            report_path.write_text(
                dump_json(result) + "\n",
                encoding="utf-8",
            )

        return result
