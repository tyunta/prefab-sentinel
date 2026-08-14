"""MCP tools for editor property and prefab operations."""

from __future__ import annotations

from typing import Any

from mcp.server import MCPServer

from prefab_sentinel.editor_bridge import send_action
from prefab_sentinel.json_io import dump_json
from prefab_sentinel.mcp_validation import require_write_audit

__all__ = ["register_editor_ops_tools"]


def register_editor_ops_tools(server: MCPServer) -> None:
    """Register editor property and prefab operation tools on *server*."""

    @server.tool()
    def editor_set_property(
        hierarchy_path: str,
        component_type: str,
        property_name: str,
        value: str | None = None,
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

        Issue #52: ``value`` is typed ``str | None``. An empty-string
        write (``value=""``) is a deliberate, valid write distinct from
        an unspecified value (``value=None``); the bridge request carries
        a value-present marker so the empty-string write is not dropped.

        Args:
            hierarchy_path: Hierarchy path to the GameObject.
            component_type: Component type name (simple or fully qualified).
            property_name: SerializedProperty path (e.g. "targetObject", "m_Speed").
            value: Value for primitive/enum properties (auto-parsed by type).
                ``None`` (default) = unspecified; ``""`` = a deliberate
                empty-string write.
            object_reference: Hierarchy path or asset path for ObjectReference properties.
        """
        if value is not None and object_reference:
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
        elif value is not None:
            kwargs["property_value"] = value
            kwargs["property_value_present"] = True
        else:
            kwargs["property_value_present"] = False
        return send_action(action="editor_set_property", **kwargs)

    @server.tool()
    def editor_set_properties(
        hierarchy_path: str,
        component_type: str,
        properties: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Set multiple serialized properties on a live Unity Editor component in a single Undo group.

        Each entry must specify a ``property_name`` plus either ``value``
        (for primitives) or ``object_reference`` (for ObjectReference
        properties).

        Issue #52: each per-entry value carries a ``value_present``
        marker across the bridge boundary, so an entry with an
        empty-string ``value`` is distinct from one that omits ``value``.

        Args:
            hierarchy_path: Hierarchy path to the target GameObject
                (e.g. "/DualButtonController/Controller").
            component_type: Component type name (e.g. "DualButtonController").
            properties: List of entry dicts, each with "property_name" and
                either "value" or "object_reference".
        """
        if not properties:
            return {
                "success": False,
                "severity": "error",
                "code": "EDITOR_SET_COMP_EMPTY_FIELDS",
                "message": "properties list must not be empty.",
                "data": {},
                "diagnostics": [],
            }

        operations: list[dict[str, Any]] = []
        for entry in properties:
            if "property_name" not in entry:
                return {
                    "success": False,
                    "severity": "error",
                    "code": "EDITOR_SET_COMP_INVALID_FIELD",
                    "message": (
                        f"Each entry must have a 'property_name' key. "
                        f"Got: {entry!r}"
                    ),
                    "data": {"field": entry},
                    "diagnostics": [],
                }
            if "value" in entry and "object_reference" in entry:
                return {
                    "success": False,
                    "severity": "error",
                    "code": "EDITOR_SET_COMP_INVALID_FIELD",
                    "message": (
                        f"Entry {entry['property_name']!r} must have either "
                        f"'value' or 'object_reference', not both."
                    ),
                    "data": {"field": entry},
                    "diagnostics": [],
                }
            if "value" not in entry and "object_reference" not in entry:
                return {
                    "success": False,
                    "severity": "error",
                    "code": "EDITOR_SET_COMP_INVALID_FIELD",
                    "message": (
                        f"Entry {entry['property_name']!r} must have either "
                        f"'value' or 'object_reference'."
                    ),
                    "data": {"field": entry},
                    "diagnostics": [],
                }
            op: dict[str, Any] = {
                "hierarchy_path": hierarchy_path,
                "component_type": component_type,
                "property_name": entry["property_name"],
            }
            if "value" in entry:
                # Issue #52: the per-entry value-present marker keeps an
                # empty-string entry value distinct from an absent one
                # across the bridge boundary.
                op["value"] = entry["value"]
                op["value_present"] = True
            else:
                op["object_reference"] = entry["object_reference"]
                op["value_present"] = False
            operations.append(op)

        return send_action(
            action="editor_batch_set_property",
            batch_operations_json=dump_json(operations, indent=None),
        )

    @server.tool()
    def editor_safe_save_prefab(
        hierarchy_path: str,
        asset_path: str,
        protect_components: list[str],
        force_original: bool = False,
        confirm: bool = False,
        change_reason: str = "",
    ) -> dict[str, Any]:
        """Save a scene GameObject as a Prefab or Prefab Variant asset
        with optional strip-and-reattach protection for caller-named
        component types (issues #193, #228).

        Issue #49: saving a prefab writes a ``.prefab`` asset to disk in
        a form Unity's Undo cannot reverse, so it requires the writer
        audit pair (``confirm=True`` AND a non-empty ``change_reason``).

        When ``protect_components`` is non-empty, the bridge handler
        observes the saved asset, re-attaches any component type listed
        that the save stripped, and reports both the re-attached
        component types and the parent-prefab modification overrides
        that became orphan as a result of the save.

        When ``protect_components`` is an empty list, the bridge handler
        runs the underlying prefab-save call without the
        strip-and-reattach pipeline (raw-save mode); orphan-modification
        reporting and the console-classification snapshot still
        populate the response data so callers see noise-diagnostic
        counts including the U# OnBeforeSerialize NRE family seen in
        raw save.

        The protected-components field is required on the request
        payload — an absent field still raises
        ``EDITOR_CTRL_SAFE_SAVE_PREFAB_PROTECT_REQUIRED``; only an
        explicitly empty list selects raw-save mode.

        Args:
            hierarchy_path: Hierarchy path to the GameObject to save.
            asset_path: Output .prefab path (e.g. "Assets/Prefabs/MyObj.prefab").
            protect_components: List of component type names to preserve
                through the save (e.g. ``["VRC_UiShape"]``). An empty
                list is a valid request for raw-save mode (issue #228);
                the wrapper serialises the list verbatim and the bridge
                handler decides between the strip-and-reattach pipeline
                and the raw-save branch.
            force_original: If True, break any Prefab Instance connection
                before saving, forcing the result to be an original
                Prefab (not a Variant).  Warning: this unpacks the scene
                GameObject (destructive, but Undo-able).
            confirm: Required ``True`` to apply (writer audit gate).
            change_reason: Required non-empty audit reason.
        """
        audit_err = require_write_audit(
            "editor_safe_save_prefab", confirm, change_reason,
        )
        if audit_err is not None:
            return audit_err
        # Issue #228: always serialise the protected-components list onto
        # the request payload, including the empty case. The wrapper does
        # not gate on emptiness; the bridge handler treats an explicitly
        # empty parsed list as a request for raw-save mode and the
        # ``EDITOR_CTRL_SAFE_SAVE_PREFAB_PROTECT_REQUIRED`` envelope is
        # raised only when the field is absent from the request payload.
        kwargs: dict[str, Any] = {
            "hierarchy_path": hierarchy_path,
            "asset_path": asset_path,
            "protect_components_json": dump_json(
                list(protect_components), indent=None
            ),
            "confirm": True,
            "change_reason": change_reason.strip(),
        }
        if force_original:
            kwargs["force_original"] = True
        return send_action(action="safe_save_prefab", **kwargs)

    @server.tool()
    def editor_set_parent(
        hierarchy_path: str,
        parent_hierarchy_path: str = "",
    ) -> dict[str, Any]:
        """Set the parent of a GameObject in the scene hierarchy (Undo-able).

        Move an existing GameObject under a new parent, or to the scene root.

        Args:
            hierarchy_path: Hierarchy path to the child GameObject to move.
            parent_hierarchy_path: Hierarchy path to the new parent.
                Empty = move to scene root.
        """
        return send_action(
            action="editor_set_parent",
            hierarchy_path=hierarchy_path,
            parent_hierarchy_path=parent_hierarchy_path,
        )
