"""MCP tools for editor batch operations and scene management."""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP

from prefab_sentinel.editor_bridge import send_action
from prefab_sentinel.editor_bridge_builders import build_create_empty_kwargs
from prefab_sentinel.json_io import dump_json
from prefab_sentinel.mcp_helpers import normalize_material_value

__all__ = ["register_editor_batch_tools"]


def register_editor_batch_tools(server: FastMCP) -> None:
    """Register editor batch and scene management tools on *server*."""

    @server.tool()
    def editor_create_empty(
        name: str,
        parent_path: str = "",
        position: str = "",
    ) -> dict[str, Any]:
        """Create an empty GameObject with name, optional parent and position.

        Args:
            name: Name for the new GameObject.
            parent_path: Hierarchy path to parent. Empty = scene root.
            position: Local position as "x,y,z". Empty = origin.
        """
        return send_action(
            action="editor_create_empty",
            **build_create_empty_kwargs(name=name, parent_path=parent_path, position=position),
        )

    @server.tool()
    def editor_create_primitive(
        primitive_type: str,
        name: str = "",
        parent_path: str = "",
        position: str = "",
        scale: str = "",
        rotation: str = "",
    ) -> dict[str, Any]:
        """Create a primitive GameObject (Cube, Sphere, Cylinder, Capsule, Plane, Quad).

        Args:
            primitive_type: Primitive shape. One of: Cube, Sphere, Cylinder, Capsule, Plane, Quad.
            name: Name for the object. Empty = default Unity name.
            parent_path: Hierarchy path to parent. Empty = scene root.
            position: Local position as "x,y,z".
            scale: Local scale as "x,y,z".
            rotation: Euler angles as "x,y,z".
        """
        kwargs: dict[str, Any] = {"primitive_type": primitive_type}
        if name:
            kwargs["new_name"] = name
        if parent_path:
            kwargs["hierarchy_path"] = parent_path
        if position:
            kwargs["property_value"] = position
        if scale:
            kwargs["scale"] = scale
        if rotation:
            kwargs["rotation"] = rotation
        return send_action(action="editor_create_primitive", **kwargs)

    @server.tool()
    def editor_create_ui_element(
        name: str,
        type: str,
        parent_path: str = "",
        rect: dict[str, list[float]] | None = None,
        properties: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Create a uGUI element under the requested parent (issue #195).

        ``editor_create_primitive`` only supports the six built-in mesh
        ``PrimitiveType`` values; this tool is the dedicated surface for
        Canvas elements. Allowed ``type`` values are exactly
        ``Image``, ``TextMeshProUGUI``, ``Button``, ``Slider``, ``Toggle``;
        the Bridge handler owns the canonical type validation and emits
        the typed envelope.

        ``rect`` accepts ``anchorMin`` / ``anchorMax`` / ``sizeDelta``
        as ``[x, y]`` pairs and is forwarded as JSON; the Bridge handler
        applies them to the new RectTransform.

        ``properties`` accepts the recognized graphic property keys
        ``color`` (RGBA tuple, applied to the primary Graphic) and
        ``font`` (TextMeshPro font asset path, applied to TextMeshPro
        components). When ``font`` is omitted on TextMeshPro elements
        the Bridge assigns the canonical default font asset
        (LiberationSans SDF) per the
        ``knowledge/prefab-sentinel-saveasprefabasset-pitfalls.md`` §3
        guidance. Unknown keys are silently ignored.

        Args:
            name: Name for the new GameObject. Empty rejected by Bridge.
            type: One of the canonical allowed type tokens.
            parent_path: Hierarchy path to parent. Empty = scene root.
            rect: Optional ``{"anchorMin": [x, y], "anchorMax": [x, y],
                "sizeDelta": [x, y]}`` payload.
            properties: Optional graphic property payload.
        """
        kwargs: dict[str, Any] = {
            "new_name": name,
            "component_type": type,
        }
        if parent_path:
            kwargs["hierarchy_path"] = parent_path
        # Always forward the rect / properties payloads as JSON, even
        # when empty, so the Bridge sees a stable shape and can apply
        # the documented defaults uniformly.
        kwargs["ui_rect_json"] = dump_json(rect or {}, indent=None)
        kwargs["ui_properties_json"] = dump_json(properties or {}, indent=None)
        return send_action(action="editor_create_ui_element", **kwargs)

    @server.tool()
    def editor_batch_create(
        objects: list[dict[str, str | list[str]]],
    ) -> dict[str, Any]:
        """Create multiple GameObjects in a single request (Undo-grouped).

        Each object dict may contain: type, name, parent, position, scale, rotation, components.
        type can be "Empty", "Cube", "Sphere", "Cylinder", "Capsule", "Plane", "Quad".
        components is an optional list of component type strings (e.g., ["BoxCollider", "AudioSource"]).

        Args:
            objects: List of object specifications.
        """
        return send_action(
            action="editor_batch_create",
            batch_objects_json=dump_json(objects, indent=None),
        )

    @server.tool()
    def editor_batch_set_property(
        operations: list[dict[str, str]],
    ) -> dict[str, Any]:
        """Set multiple properties in a single request (Undo-grouped).

        Each operation dict must contain: hierarchy_path, component_type, property_name.
        Plus either value (for primitives) or object_reference (for ObjectReference).

        Args:
            operations: List of set-property operations.
        """
        return send_action(
            action="editor_batch_set_property",
            batch_operations_json=dump_json(operations, indent=None),
        )

    @server.tool()
    def editor_batch_set_material_property(
        properties: list[dict[str, str | list | int | float]],
        hierarchy_path: str = "",
        material_index: int = -1,
        material_path: str = "",
        material_guid: str = "",
    ) -> dict[str, Any]:
        """Set multiple shader properties on one material in a single request (Undo-grouped).

        Target the material by ONE of:
        - Renderer: hierarchy_path + material_index
        - Direct: material_path or material_guid

        Args:
            properties: List of property dicts, each with "name" and "value".
                Value formats are the same as editor_set_material_property.
            hierarchy_path: Hierarchy path to the GameObject with a Renderer.
            material_index: Material slot index (0-based). Required with hierarchy_path.
            material_path: Asset path to .mat file (e.g. "Assets/Materials/Hair.mat").
            material_guid: GUID of the Material asset (32-char hex).
        """
        normalized = [
            {"name": prop["name"], "value": normalize_material_value(prop["value"])}
            for prop in properties
        ]

        kwargs: dict[str, Any] = {
            "batch_operations_json": dump_json(normalized, indent=None),
        }
        if hierarchy_path:
            kwargs["hierarchy_path"] = hierarchy_path
            kwargs["material_index"] = material_index
        if material_path:
            kwargs["material_path"] = material_path
        if material_guid:
            kwargs["material_guid"] = material_guid

        return send_action(
            action="editor_batch_set_material_property",
            **kwargs,
        )

    @server.tool()
    def editor_batch_add_component(
        operations: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Add components to multiple GameObjects in a single request (Undo-grouped).

        Each operation dict must contain: hierarchy_path, component_type.
        Optional: properties (list of {name, value/object_reference} dicts) —
        automatically serialized to properties_json for the bridge.

        Args:
            operations: List of add-component operations.
        """
        serialized_ops = []
        for op in operations:
            op_copy = dict(op)
            props = op_copy.pop("properties", None)
            if props and "properties_json" not in op_copy:
                op_copy["properties_json"] = dump_json(props, indent=None)
            serialized_ops.append(op_copy)

        return send_action(
            action="editor_batch_add_component",
            batch_operations_json=dump_json(serialized_ops, indent=None),
        )

    @server.tool()
    def editor_open_scene(
        scene_path: str,
        mode: str = "single",
    ) -> dict[str, Any]:
        """Open a Unity scene by asset path.

        Args:
            scene_path: Asset path to .unity file (e.g. "Assets/Scenes/Main.unity").
            mode: "single" (replace current) or "additive" (add to current).
        """
        return send_action(
            action="editor_open_scene",
            asset_path=scene_path,
            open_scene_mode=mode,
        )

    @server.tool()
    def editor_save_scene(
        path: str = "",
    ) -> dict[str, Any]:
        """Save the current scene. If path is empty, saves all open scenes in place.

        Args:
            path: Asset path to save to. Empty = save all open scenes.
        """
        kwargs: dict[str, Any] = {}
        if path:
            kwargs["asset_path"] = path
        return send_action(action="editor_save_scene", **kwargs)

    @server.tool()
    def editor_create_scene(
        scene_path: str,
    ) -> dict[str, Any]:
        """Create a new empty Unity scene and save it to the specified path.

        Replaces the current scene with a new empty one. Use editor_save_scene
        first if you need to preserve the current scene.

        Args:
            scene_path: Asset path for the new scene (e.g. "Assets/Scenes/NewScene.unity").
        """
        return send_action(
            action="editor_create_scene",
            asset_path=scene_path,
        )
