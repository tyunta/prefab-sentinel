"""MCP tools for editor write/mutation operations."""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP

from prefab_sentinel.editor_bridge import send_action
from prefab_sentinel.json_io import dump_json

__all__ = ["register_editor_write_tools"]


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
        material_guid: str = "",
        material_path: str = "",
    ) -> dict[str, Any]:
        """Replace a material slot on a Renderer at runtime (Undo-able).

        Specify either material_guid or material_path (not both).

        Args:
            hierarchy_path: Hierarchy path to the GameObject with a Renderer.
            material_index: Material slot index (0-based).
            material_guid: GUID of the replacement Material asset (32-char hex).
            material_path: Asset path of the replacement Material (e.g. "Assets/Materials/Foo.mat").
        """
        kwargs: dict[str, Any] = {
            "hierarchy_path": hierarchy_path,
            "material_index": material_index,
        }
        if material_guid:
            kwargs["material_guid"] = material_guid
        if material_path:
            kwargs["material_path"] = material_path
        return send_action(action="set_material", **kwargs)

    @server.tool()
    def editor_find_renderers_by_material(
        material_guid: str = "",
        material_path: str = "",
    ) -> dict[str, Any]:
        """Find all renderers using a specific material in the current scene.

        Returns renderer paths and slot indices. Specify either material_guid
        or material_path (not both).

        Args:
            material_guid: GUID of the material to search for.
            material_path: Asset path of the material (e.g. "Assets/Materials/Foo.mat").
        """
        kwargs: dict[str, Any] = {}
        if material_guid:
            kwargs["material_guid"] = material_guid
        if material_path:
            kwargs["material_path"] = material_path
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
        script_path: str,
        output_path: str = "",
    ) -> dict[str, Any]:
        """Create an UdonSharpProgramAsset (.asset) for an UdonSharp C# script.

        Requires UdonSharp to be installed in the Unity project.

        Args:
            script_path: Asset path to the .cs file (e.g. "Assets/Scripts/MyBehaviour.cs").
            output_path: Output .asset path. Defaults to same directory as script with .asset extension.
        """
        kwargs: dict[str, Any] = {"asset_path": script_path}
        if output_path:
            kwargs["description"] = output_path
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

    @server.tool()
    def editor_get_blend_shapes(
        hierarchy_path: str,
        filter: str = "",
    ) -> dict[str, Any]:
        """Get BlendShape names and current weight values from a SkinnedMeshRenderer.

        Args:
            hierarchy_path: Hierarchy path to the GameObject with a SkinnedMeshRenderer.
            filter: Substring filter on BlendShape names (empty = return all).
        """
        return send_action(
            action="get_blend_shapes",
            hierarchy_path=hierarchy_path,
            filter=filter,
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
    ) -> dict[str, Any]:
        """Execute a Unity Editor menu item by path.

        Some menu items may display modal dialogs that block the Editor.
        Dangerous paths (File/New Scene, File/New Project, Assets/Delete) are denied.

        Args:
            menu_path: Full menu path (e.g. "Tools/NDMF/Manual Bake").
        """
        return send_action(
            action="execute_menu_item",
            menu_path=menu_path,
        )
