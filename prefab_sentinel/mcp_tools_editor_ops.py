"""MCP tools for editor property and prefab operations."""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP

from prefab_sentinel.editor_bridge import send_action
from prefab_sentinel.json_io import dump_json

__all__ = ["register_editor_ops_tools"]


def register_editor_ops_tools(server: FastMCP) -> None:
    """Register editor property and prefab operation tools on *server*."""

    @server.tool()
    def editor_set_property(
        hierarchy_path: str,
        component_type: str,
        property_name: str,
        value: str = "",
        object_reference: str = "",
    ) -> dict[str, Any]:
        """Set a serialized property on a component via Unity's SerializedObject API.

        Supports all SerializedProperty types including UdonSharp fields.
        Type is auto-detected from the property. Use value for primitives/enum,
        object_reference for ObjectReference fields.

        For object_reference, specify a hierarchy path (e.g. "/ToggleTarget")
        for scene objects, or an asset path (e.g. "Assets/Materials/Red.mat")
        for project assets. Append :ComponentType to reference a specific
        component (e.g. "/MyObj:AudioSource").

        Note: Setting a String property to empty string is not supported
        (indistinguishable from "no value provided").

        Args:
            hierarchy_path: Hierarchy path to the GameObject.
            component_type: Component type name (simple or fully qualified).
            property_name: SerializedProperty path (e.g. "targetObject", "m_Speed").
            value: Value for primitive/enum properties (auto-parsed by type).
            object_reference: Hierarchy path or asset path for ObjectReference properties.
        """
        if value and object_reference:
            return {
                "success": False,
                "severity": "error",
                "code": "EDITOR_CTRL_SET_PROP_BOTH_VALUE",
                "message": "Provide value or object_reference, not both.",
                "data": {},
                "diagnostics": [],
            }
        kwargs: dict[str, Any] = {
            "hierarchy_path": hierarchy_path,
            "component_type": component_type,
            "property_name": property_name,
        }
        if object_reference:
            kwargs["object_reference"] = object_reference
        else:
            kwargs["property_value"] = value
        return send_action(action="editor_set_property", **kwargs)

    @server.tool()
    def editor_set_component_fields(
        hierarchy_path: str,
        component_type: str,
        fields: list[dict[str, str]],
    ) -> dict[str, Any]:
        """Set multiple serialized fields on a live Unity Editor component in a single Undo group.

        Each field must specify a name plus either value (for primitives) or
        object_reference (for ObjectReference properties).

        Args:
            hierarchy_path: Hierarchy path to the target GameObject
                (e.g. "/DualButtonController/Controller").
            component_type: Component type name (e.g. "DualButtonController").
            fields: List of field dicts, each with "name" and either "value"
                or "object_reference".
        """
        if not fields:
            return {
                "success": False,
                "severity": "error",
                "code": "EDITOR_SET_COMP_EMPTY_FIELDS",
                "message": "fields list must not be empty.",
                "data": {},
                "diagnostics": [],
            }

        operations: list[dict[str, str]] = []
        for field in fields:
            if "name" not in field:
                return {
                    "success": False,
                    "severity": "error",
                    "code": "EDITOR_SET_COMP_INVALID_FIELD",
                    "message": (
                        f"Each field must have a 'name' key. Got: {field!r}"
                    ),
                    "data": {"field": field},
                    "diagnostics": [],
                }
            if "value" in field and "object_reference" in field:
                return {
                    "success": False,
                    "severity": "error",
                    "code": "EDITOR_SET_COMP_INVALID_FIELD",
                    "message": (
                        f"Field {field['name']!r} must have either "
                        f"'value' or 'object_reference', not both."
                    ),
                    "data": {"field": field},
                    "diagnostics": [],
                }
            if "value" not in field and "object_reference" not in field:
                return {
                    "success": False,
                    "severity": "error",
                    "code": "EDITOR_SET_COMP_INVALID_FIELD",
                    "message": (
                        f"Field {field['name']!r} must have either "
                        f"'value' or 'object_reference'."
                    ),
                    "data": {"field": field},
                    "diagnostics": [],
                }
            op: dict[str, str] = {
                "hierarchy_path": hierarchy_path,
                "component_type": component_type,
                "property_name": field["name"],
            }
            if "value" in field:
                op["value"] = field["value"]
            else:
                op["object_reference"] = field["object_reference"]
            operations.append(op)

        return send_action(
            action="editor_batch_set_property",
            batch_operations_json=dump_json(operations, indent=None),
        )

    @server.tool()
    def editor_save_as_prefab(
        hierarchy_path: str,
        asset_path: str,
    ) -> dict[str, Any]:
        """Save a scene GameObject as a Prefab or Prefab Variant asset.

        If the GameObject is a Prefab instance (connected to a base),
        the result is automatically a Prefab Variant.
        If it's a plain GameObject, a new original Prefab is created.
        The scene instance is not reconnected to the new Prefab asset.

        Args:
            hierarchy_path: Hierarchy path to the GameObject to save.
            asset_path: Output .prefab path (e.g. "Assets/Prefabs/MyObj.prefab").
        """
        return send_action(
            action="save_as_prefab",
            hierarchy_path=hierarchy_path,
            asset_path=asset_path,
        )

    @server.tool()
    def editor_set_parent(
        hierarchy_path: str,
        parent_path: str = "",
    ) -> dict[str, Any]:
        """Set the parent of a GameObject in the scene hierarchy (Undo-able).

        Move an existing GameObject under a new parent, or to the scene root.

        Args:
            hierarchy_path: Hierarchy path to the child GameObject to move.
            parent_path: Hierarchy path to the new parent. Empty = move to scene root.
        """
        return send_action(
            action="editor_set_parent",
            hierarchy_path=hierarchy_path,
            new_name=parent_path,
        )
