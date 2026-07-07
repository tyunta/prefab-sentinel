"""MCP tools for editor write/mutation operations."""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP

from prefab_sentinel.editor_bridge import send_action
from prefab_sentinel.json_io import dump_json
from prefab_sentinel.mcp_helpers import normalize_material_value
from prefab_sentinel.mcp_validation import require_write_audit

__all__ = [
    "editor_get_blend_shapes",
    "register_editor_write_tools",
    "BLEND_SHAPE_LIMIT_MIN",
    "BLEND_SHAPE_LIMIT_MAX",
    "BLEND_SHAPE_LIMIT_DEFAULT",
]

# Issue #241: inclusive bounds for the paginated blend-shape enumeration
# wrapper's ``limit`` parameter.  The upper bound matches the bridge's
# practical page cap (one thousand entries per call); the lower bound
# rejects zero / negative values that degenerate into a never-progressing
# pagination.
BLEND_SHAPE_LIMIT_MIN = 1
BLEND_SHAPE_LIMIT_MAX = 1000
BLEND_SHAPE_LIMIT_DEFAULT = 200


def _blend_shape_pagination_out_of_range_envelope(
    *, offset: int, limit: int, offending: str,
) -> dict[str, Any]:
    """Return the canonical ``BLEND_SHAPE_PAGINATION_OUT_OF_RANGE`` envelope.

    The message names the supplied value, both bounds, and which of
    ``offset`` or ``limit`` was the offending knob so the caller can fix
    the request without consulting external docs.
    """
    return {
        "success": False,
        "severity": "error",
        "code": "BLEND_SHAPE_PAGINATION_OUT_OF_RANGE",
        "message": (
            f"{offending} out of range: offset={offset}, limit={limit}. "
            f"Required: offset>=0 and {BLEND_SHAPE_LIMIT_MIN}<=limit<="
            f"{BLEND_SHAPE_LIMIT_MAX}."
        ),
        "data": {
            "offset": offset,
            "limit": limit,
            "offending": offending,
            "limit_min": BLEND_SHAPE_LIMIT_MIN,
            "limit_max": BLEND_SHAPE_LIMIT_MAX,
        },
        "diagnostics": [],
    }


def editor_get_blend_shapes(
    hierarchy_path: str,
    filter: str = "",
    offset: int = 0,
    limit: int = BLEND_SHAPE_LIMIT_DEFAULT,
) -> dict[str, Any]:
    """Get BlendShape names and current weight values, paginated (issue #241).

    Negative ``offset`` or an out-of-range ``limit`` is rejected
    pre-bridge with the ``BLEND_SHAPE_PAGINATION_OUT_OF_RANGE`` envelope.
    In-range requests forward the addressing target, the substring
    filter, and both pagination knobs to the bridge verbatim; the
    success envelope carries a ``next_cursor`` string whose value is the
    offset of the next unread match when at least one entry remains past
    the returned page, and the empty string when the page exhausted the
    matching set.
    """
    if offset < 0:
        return _blend_shape_pagination_out_of_range_envelope(
            offset=offset, limit=limit, offending="offset",
        )
    if limit < BLEND_SHAPE_LIMIT_MIN or limit > BLEND_SHAPE_LIMIT_MAX:
        return _blend_shape_pagination_out_of_range_envelope(
            offset=offset, limit=limit, offending="limit",
        )
    return send_action(
        action="get_blend_shapes",
        hierarchy_path=hierarchy_path,
        filter=filter,
        offset=offset,
        limit=limit,
    )


def register_editor_write_tools(server: FastMCP) -> None:
    """Register editor write/mutation tools on *server*."""

    @server.tool()
    def editor_instantiate(
        asset_path: str,
        hierarchy_path: str = "",
        position: str = "",
    ) -> dict[str, Any]:
        """Instantiate a Prefab into the current Scene.

        Args:
            asset_path: Asset path of the prefab (e.g. Assets/Prefabs/Mic.prefab).
            hierarchy_path: Hierarchy path of the parent GameObject (empty = scene root).
            position: Local position as "x,y,z" string (e.g. "0,1.5,0"). Empty = default.
        """
        kwargs: dict[str, Any] = {"asset_path": asset_path, "hierarchy_path": hierarchy_path}
        if position:
            try:
                parts = [float(v) for v in position.split(",")]
            except ValueError:
                return {
                    "success": False, "severity": "error", "code": "INVALID_POSITION",
                    "message": f"Non-numeric position values: {position} (expected x,y,z)",
                    "data": {}, "diagnostics": [],
                }
            if len(parts) != 3:
                return {
                    "success": False, "severity": "error", "code": "INVALID_POSITION",
                    "message": f"position requires exactly 3 values (x,y,z), got {len(parts)}",
                    "data": {}, "diagnostics": [],
                }
            kwargs["position"] = parts
        return send_action(action="instantiate_to_scene", **kwargs)

    @server.tool()
    def editor_set_material(
        hierarchy_path: str,
        material_index: int,
        material_asset_guid: str = "",
        material_asset_path: str = "",
    ) -> dict[str, Any]:
        """Replace a material slot on a Renderer at runtime (Undo-able).

        Specify either material_asset_guid or material_asset_path (not both).

        Args:
            hierarchy_path: Hierarchy path to the GameObject with a Renderer.
            material_index: Material slot index (0-based).
            material_asset_guid: GUID of the replacement Material asset (32-char hex).
            material_asset_path: Asset path of the replacement Material (e.g. "Assets/Materials/Foo.mat").
        """
        kwargs: dict[str, Any] = {
            "hierarchy_path": hierarchy_path,
            "material_index": material_index,
        }
        if material_asset_guid:
            kwargs["material_guid"] = material_asset_guid
        if material_asset_path:
            kwargs["material_path"] = material_asset_path
        return send_action(action="set_material", **kwargs)

    @server.tool()
    def editor_set_material_property(
        hierarchy_path: str,
        material_index: int,
        property_name: str,
        value: str | list | int | float,
        confirm: bool = False,
        change_reason: str | None = None,
    ) -> dict[str, Any]:
        """Set a shader property value on a material at runtime.

        Type is determined from shader definition (not from the value format).

        Args:
            hierarchy_path: Hierarchy path to the GameObject with a Renderer.
            material_index: Material slot index (0-based).
            property_name: Shader property name (e.g. "_Color", "_MainTex").
            value: Value as string. Format depends on shader type:
                Float/Range: "0.5"
                Int: "2"
                Color: "[1, 0.8, 0.6, 1]" (RGBA)
                Vector: "[0, 1, 0, 0]" (XYZW)
                Texture: "guid:abc123..." or "path:Assets/Tex/foo.png" or "" (null)
            confirm: Must be ``True`` for this write-class tool.
            change_reason: Non-empty audit reason recorded with the write.
        """
        audit_err = require_write_audit(
            "editor_set_material_property", confirm, change_reason,
        )
        if audit_err is not None:
            return audit_err
        return send_action(
            action="set_material_property",
            hierarchy_path=hierarchy_path,
            material_index=material_index,
            property_name=property_name,
            property_value=normalize_material_value(value),
        )

    @server.tool()
    def editor_find_renderers_by_material(
        material_asset_guid: str = "",
        material_asset_path: str = "",
    ) -> dict[str, Any]:
        """Find all renderers using a specific material in the current scene.

        Returns renderer paths and slot indices. Specify either
        material_asset_guid or material_asset_path (not both).

        Args:
            material_asset_guid: GUID of the material to search for.
            material_asset_path: Asset path of the material (e.g. "Assets/Materials/Foo.mat").
        """
        kwargs: dict[str, Any] = {}
        if material_asset_guid:
            kwargs["material_guid"] = material_asset_guid
        if material_asset_path:
            kwargs["material_path"] = material_asset_path
        return send_action(action="find_renderers_by_material", **kwargs)

    @server.tool()
    def editor_rename(
        hierarchy_path: str,
        new_name: str,
    ) -> dict[str, Any]:
        """Rename a GameObject in the scene (Undo-able).

        Args:
            hierarchy_path: Hierarchy path to the GameObject.
            new_name: New name for the GameObject.
        """
        return send_action(
            action="editor_rename",
            hierarchy_path=hierarchy_path,
            new_name=new_name,
        )

    @server.tool()
    def editor_add_component(
        hierarchy_path: str,
        component_type: str,
        properties: list[dict[str, str]] | None = None,
    ) -> dict[str, Any]:
        """Add a component to a GameObject at runtime (Undo-able).

        Type resolution: tries fully qualified name, then searches all assemblies
        by simple name.

        Args:
            hierarchy_path: Hierarchy path to the target GameObject.
            component_type: Component type name (e.g. "BoxCollider", "UnityEngine.AudioSource").
            properties: Optional initial property values. Each dict has "name" and
                "value" (or "object_reference") keys. Applied after component is added.
        """
        kwargs: dict[str, Any] = {
            "hierarchy_path": hierarchy_path,
            "component_type": component_type,
        }
        if properties:
            kwargs["properties_json"] = dump_json(properties, indent=None)
        return send_action(action="editor_add_component", **kwargs)

    @server.tool()
    def editor_remove_component(
        hierarchy_path: str,
        component_type: str,
        index: int | None = None,
    ) -> dict[str, Any]:
        """Remove a component from a GameObject at runtime (Undo-able).

        Type resolution: tries fully qualified name, then searches all assemblies
        by simple name.

        When multiple components of the same type exist, specify index to select
        which one to remove.  If omitted and the type is ambiguous (count > 1),
        the call fails with EDITOR_CTRL_REM_COMP_AMBIGUOUS.

        Args:
            hierarchy_path: Hierarchy path to the target GameObject.
            component_type: Component type name (e.g. "BoxCollider").
            index: 0-based index when multiple components of the same type exist.
        """
        kwargs: dict[str, Any] = {
            "hierarchy_path": hierarchy_path,
            "component_type": component_type,
        }
        if index is not None:
            kwargs["component_index"] = index
        return send_action(action="editor_remove_component", **kwargs)

    @server.tool()
    def editor_create_udon_program_asset(
        asset_path: str,
        output_asset_path: str = "",
        confirm: bool = False,
        change_reason: str = "",
    ) -> dict[str, Any]:
        """Create an UdonSharpProgramAsset (.asset) for an UdonSharp C# script.

        Requires UdonSharp to be installed in the Unity project.

        Issue #49: creating a program asset writes a new ``.asset`` to
        disk in a form Unity's Undo cannot reverse, so it requires the
        writer audit pair (``confirm=True`` AND a non-empty
        ``change_reason``).

        Args:
            asset_path: Asset path to the .cs file (e.g. "Assets/Scripts/MyBehaviour.cs").
            output_asset_path: Output .asset path. Defaults to same directory as script with .asset extension.
            confirm: Required ``True`` to apply (writer audit gate).
            change_reason: Required non-empty audit reason.
        """
        audit_err = require_write_audit(
            "editor_create_udon_program_asset", confirm, change_reason,
        )
        if audit_err is not None:
            return audit_err
        kwargs: dict[str, Any] = {
            "asset_path": asset_path,
            "confirm": True,
            "change_reason": change_reason.strip(),
        }
        if output_asset_path:
            kwargs["description"] = output_asset_path
        return send_action(action="create_udon_program_asset", **kwargs)

    @server.tool()
    def editor_delete(
        hierarchy_path: str,
    ) -> dict[str, Any]:
        """Delete a GameObject from the scene hierarchy (Undo-able).

        Args:
            hierarchy_path: Hierarchy path to the GameObject to delete.
        """
        return send_action(action="delete_object", hierarchy_path=hierarchy_path)

    @server.tool(name="editor_get_blend_shapes")
    def _editor_get_blend_shapes(
        hierarchy_path: str,
        filter: str = "",
        offset: int = 0,
        limit: int = BLEND_SHAPE_LIMIT_DEFAULT,
    ) -> dict[str, Any]:
        """Get BlendShape names and weights, paginated (issue #241).

        Args:
            hierarchy_path: Hierarchy path to the GameObject with a SkinnedMeshRenderer.
            filter: Substring filter on BlendShape names (empty = return all).
            offset: Non-negative starting index into the filtered match list.
            limit: Inclusive ``[1, 1000]`` upper bound on returned entries.
                The success envelope carries ``next_cursor`` (offset of
                the next unread match, or empty string at end of stream).
        """
        return editor_get_blend_shapes(
            hierarchy_path=hierarchy_path,
            filter=filter,
            offset=offset,
            limit=limit,
        )

    @server.tool()
    def editor_set_blend_shape(
        hierarchy_path: str,
        name: str,
        weight: float,
    ) -> dict[str, Any]:
        """Set a BlendShape weight by name on a SkinnedMeshRenderer (Undo-able).

        Args:
            hierarchy_path: Hierarchy path to the GameObject with a SkinnedMeshRenderer.
            name: BlendShape name (exact match).
            weight: Weight value (0-100).
        """
        return send_action(
            action="set_blend_shape",
            hierarchy_path=hierarchy_path,
            blend_shape_name=name,
            blend_shape_weight=weight,
        )

    @server.tool()
    def editor_list_menu_items(
        prefix: str = "",
    ) -> dict[str, Any]:
        """List Unity Editor menu items registered via [MenuItem] attribute.

        Args:
            prefix: Path prefix filter (e.g. "Tools/", "CONTEXT/"). Empty = all items.
        """
        return send_action(
            action="list_menu_items",
            filter=prefix,
        )

    @server.tool()
    def editor_execute_menu_item(
        menu_path: str,
        assume_compiled: bool = False,
        confirm: bool = False,
        change_reason: str = "",
    ) -> dict[str, Any]:
        """Execute a Unity Editor menu item by path.

        Some menu items may display modal dialogs that block the Editor.
        Dangerous paths (File/New Scene, File/New Project, Assets/Delete) are denied.

        Issue #49: a menu item runs caller-unverifiable arbitrary editor
        code, so it requires the writer audit pair (``confirm=True`` AND
        a non-empty ``change_reason``).

        Issue #225: when the caller has not asserted compile state, the
        bridge runs an implicit recompile barrier before invoking the
        menu item if the Editor is currently compiling or if any
        editor source has changed since the prior menu execution. When
        ``assume_compiled=True`` the bridge skips the barrier and runs
        the menu item synchronously — only safe when the caller is
        confident the Editor is up-to-date. The response data carries a
        ``recompile_waited`` flag so the caller can tell whether the
        slow path actually fired.

        Args:
            menu_path: Full menu path (e.g. "Tools/NDMF/Manual Bake").
            assume_compiled: When ``True``, opt out of the implicit
                recompile barrier (issue #225). Defaults to ``False`` so
                accidental omission stays safe.
            confirm: Required ``True`` to apply (writer audit gate).
            change_reason: Required non-empty audit reason.
        """
        audit_err = require_write_audit(
            "editor_execute_menu_item", confirm, change_reason,
        )
        if audit_err is not None:
            return audit_err
        return send_action(
            action="execute_menu_item",
            menu_path=menu_path,
            assume_compiled=assume_compiled,
            confirm=True,
            change_reason=change_reason.strip(),
        )
