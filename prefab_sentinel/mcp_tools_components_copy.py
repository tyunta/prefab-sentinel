"""MCP tool for cross-asset component field duplication."""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP

from prefab_sentinel.mcp_helpers import (
    COPY_SKIP_FIELDS,
    find_block_by_file_id,
    read_asset,
    resolve_component_with_type,
)
from prefab_sentinel.mcp_validation import require_change_reason
from prefab_sentinel.patch_plan import PLAN_VERSION
from prefab_sentinel.session import ProjectSession
from prefab_sentinel.yaml_field_extraction import extract_block_fields, parse_yaml_scalar

__all__ = ["register_copy_component_fields_tool"]


def register_copy_component_fields_tool(server: FastMCP, session: ProjectSession) -> None:
    """Register the copy_component_fields tool on *server*."""

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
        err = require_change_reason(confirm, change_reason)
        if err is not None:
            return err
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
