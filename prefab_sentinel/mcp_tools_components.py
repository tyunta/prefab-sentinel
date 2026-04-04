"""MCP tools for component add/remove/copy operations."""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP

from prefab_sentinel.mcp_helpers import (
    COPY_SKIP_FIELDS,
    find_block_by_file_id,
    read_asset,
    resolve_component_with_type,
    resolve_game_object_node,
)
from prefab_sentinel.patch_plan import PLAN_VERSION
from prefab_sentinel.session import ProjectSession
from prefab_sentinel.yaml_field_extraction import extract_block_fields, parse_yaml_scalar

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

    @server.tool()
    def copy_component_fields(
        src_asset_path: str,
        src_symbol_path: str,
        dst_asset_path: str,
        dst_symbol_path: str,
        fields: list[str] | None = None,
        confirm: bool = False,
        change_reason: str = "",
    ) -> dict[str, Any]:
        """Copy serialized field values between components of the same type.

        Reads field values from the source component and writes them to
        the destination via patch plan. Supports cross-asset and same-asset.

        Two-phase workflow:
        - confirm=False (default): dry-run preview of changes.
        - confirm=True: applies changes to disk.

        Args:
            src_asset_path: Source asset file path.
            src_symbol_path: Symbol path to the source component.
            dst_asset_path: Destination asset file path.
            dst_symbol_path: Symbol path to the destination component.
            fields: Specific field names to copy. Omit to copy all user
                fields. Warning: explicitly specifying system fields
                (m_Script, m_GameObject) can corrupt component identity.
            confirm: Set True to apply (False = dry-run only).
            change_reason: Human-readable reason for the change (audit trail).
        """
        src_text, _src_resolved = read_asset(src_asset_path, session.project_root)
        dst_text, dst_resolved = read_asset(dst_asset_path, session.project_root)

        src_tree = session.get_symbol_tree(
            _src_resolved, src_text, include_properties=False,
        )
        dst_tree = session.get_symbol_tree(
            dst_resolved, dst_text, include_properties=False,
        )

        src_node, src_type, src_err = resolve_component_with_type(
            src_tree, src_symbol_path, src_asset_path,
        )
        if src_err is not None:
            return src_err
        assert src_node is not None

        dst_node, dst_type, dst_err = resolve_component_with_type(
            dst_tree, dst_symbol_path, dst_asset_path,
        )
        if dst_err is not None:
            return dst_err
        assert dst_node is not None

        if src_type != dst_type:
            return {
                "success": False,
                "severity": "error",
                "code": "TYPE_MISMATCH",
                "message": (
                    f"Source component type {src_type!r} does not match "
                    f"destination type {dst_type!r}."
                ),
                "data": {"src_type": src_type, "dst_type": dst_type},
                "diagnostics": [],
            }

        src_block = find_block_by_file_id(src_text, src_node.file_id)
        all_fields = extract_block_fields(src_block)

        if fields is not None:
            all_field_names = [name for name, _ in all_fields]
            for f in fields:
                if f not in all_field_names:
                    return {
                        "success": False,
                        "severity": "error",
                        "code": "FIELD_NOT_FOUND",
                        "message": f"Field {f!r} not found on source component.",
                        "data": {
                            "field_name": f,
                            "available_fields": all_field_names,
                        },
                        "diagnostics": [],
                    }
            requested = set(fields)
            copy_pairs = [(n, v) for n, v in all_fields if n in requested]
        else:
            copy_pairs = [
                (n, v) for n, v in all_fields if n not in COPY_SKIP_FIELDS
            ]

        if not copy_pairs:
            return {
                "success": False,
                "severity": "error",
                "code": "NO_FIELDS_TO_COPY",
                "message": "No copyable fields found on source component.",
                "data": {
                    "src_asset_path": src_asset_path,
                    "src_symbol_path": src_symbol_path,
                },
                "diagnostics": [],
            }

        ops = [
            {
                "resource": "target",
                "op": "set",
                "component": dst_type,
                "path": prop_path,
                "value": parse_yaml_scalar(raw_value),
            }
            for prop_path, raw_value in copy_pairs
        ]
        plan: dict[str, object] = {
            "plan_version": PLAN_VERSION,
            "resources": [
                {"id": "target", "path": dst_asset_path, "mode": "open"},
            ],
            "ops": ops,
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
            session.invalidate_symbol_tree(dst_resolved)
            result["auto_refresh"] = orch.maybe_auto_refresh()
        result["copy_metadata"] = {
            "src_asset_path": src_asset_path,
            "src_symbol_path": src_symbol_path,
            "src_component": src_type,
            "src_file_id": src_node.file_id,
            "dst_asset_path": dst_asset_path,
            "dst_symbol_path": dst_symbol_path,
            "dst_component": dst_type,
            "dst_file_id": dst_node.file_id,
            "fields_copied": [n for n, _ in copy_pairs],
        }
        return result
