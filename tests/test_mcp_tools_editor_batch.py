"""Branch-coverage uplift for ``prefab_sentinel.mcp_tools_editor_batch`` (issue #188).

Pins the registered tool surface and each tool's downstream invocation
shape.  ``send_action`` is patched so the tests never contact a real
Editor Bridge.

Branches in the target module not covered: none.  Every ``if`` over an
optional input string and every batch / serialization branch is reached
by one of the rows below.
"""

from __future__ import annotations

import json
import unittest
from typing import Any
from unittest import mock

from prefab_sentinel import mcp_tools_editor_batch


class _RecorderServer:
    """Minimal ``FastMCP``-compatible recorder.

    The production module decorates each tool with ``@server.tool()``;
    the recorder collects the decorated callables by name so the tests
    can drive them directly.
    """

    def __init__(self) -> None:
        self.registered: dict[str, Any] = {}

    def tool(self, *args: Any, **kwargs: Any):  # noqa: D401, ANN401
        def _decorator(fn):  # noqa: ANN001
            self.registered[fn.__name__] = fn
            return fn

        return _decorator


def _register() -> _RecorderServer:
    server = _RecorderServer()
    mcp_tools_editor_batch.register_editor_batch_tools(server)  # type: ignore[arg-type]
    return server


_DOCUMENTED_TOOLS = {
    "editor_create_empty",
    "editor_create_primitive",
    "editor_batch_create",
    "editor_batch_set_property",
    "editor_batch_set_material_property",
    "editor_batch_add_component",
    "editor_open_scene",
    "editor_save_scene",
    "editor_create_scene",
}


class EditorBatchRegistrationTests(unittest.TestCase):
    def test_register_collects_documented_tool_surface(self) -> None:
        server = _register()
        self.assertEqual(_DOCUMENTED_TOOLS, set(server.registered.keys()))


class EditorBatchRoutingTests(unittest.TestCase):
    """Each tool's downstream ``send_action`` invocation is shape-pinned."""

    def setUp(self) -> None:
        self.server = _register()
        self.send_patcher = mock.patch.object(
            mcp_tools_editor_batch, "send_action", return_value={"ok": True}
        )
        self.mock_send = self.send_patcher.start()
        self.addCleanup(self.send_patcher.stop)

    # --- editor_create_empty ------------------------------------------------

    def test_create_empty_omits_optional_fields_when_blank(self) -> None:
        self.server.registered["editor_create_empty"](name="Root")
        self.assertEqual(1, self.mock_send.call_count)
        kwargs = self.mock_send.call_args.kwargs
        self.assertEqual("editor_create_empty", kwargs["action"])
        self.assertEqual("Root", kwargs["new_name"])
        self.assertNotIn("hierarchy_path", kwargs)
        self.assertNotIn("property_value", kwargs)

    def test_create_empty_forwards_optional_fields(self) -> None:
        self.server.registered["editor_create_empty"](
            name="Child", parent_path="Parent", position="1,2,3"
        )
        kwargs = self.mock_send.call_args.kwargs
        self.assertEqual("Parent", kwargs["hierarchy_path"])
        self.assertEqual("1,2,3", kwargs["property_value"])

    # --- editor_create_primitive -------------------------------------------

    def test_create_primitive_omits_blank_optionals(self) -> None:
        self.server.registered["editor_create_primitive"](primitive_type="Cube")
        kwargs = self.mock_send.call_args.kwargs
        self.assertEqual("editor_create_primitive", kwargs["action"])
        self.assertEqual("Cube", kwargs["primitive_type"])
        for key in ("new_name", "hierarchy_path", "property_value", "scale", "rotation"):
            self.assertNotIn(key, kwargs)

    def test_create_primitive_forwards_each_optional(self) -> None:
        self.server.registered["editor_create_primitive"](
            primitive_type="Sphere",
            name="S",
            parent_path="Root",
            position="0,1,0",
            scale="2,2,2",
            rotation="0,90,0",
        )
        kwargs = self.mock_send.call_args.kwargs
        self.assertEqual("S", kwargs["new_name"])
        self.assertEqual("Root", kwargs["hierarchy_path"])
        self.assertEqual("0,1,0", kwargs["property_value"])
        self.assertEqual("2,2,2", kwargs["scale"])
        self.assertEqual("0,90,0", kwargs["rotation"])

    # --- editor_batch_create -----------------------------------------------

    def test_batch_create_serializes_payload_to_json(self) -> None:
        objects = [{"type": "Cube", "name": "C"}]
        self.server.registered["editor_batch_create"](objects=objects)
        kwargs = self.mock_send.call_args.kwargs
        self.assertEqual("editor_batch_create", kwargs["action"])
        self.assertEqual(objects, json.loads(kwargs["batch_objects_json"]))

    # --- editor_batch_set_property -----------------------------------------

    def test_batch_set_property_serializes_operations(self) -> None:
        ops = [{"hierarchy_path": "A", "component_type": "Transform", "property_name": "scale", "value": "1,1,1"}]
        self.server.registered["editor_batch_set_property"](operations=ops)
        kwargs = self.mock_send.call_args.kwargs
        self.assertEqual("editor_batch_set_property", kwargs["action"])
        self.assertEqual(ops, json.loads(kwargs["batch_operations_json"]))

    # --- editor_batch_set_material_property: three target shapes -----------

    def _material_props(self) -> list[dict[str, Any]]:
        return [{"name": "_Color", "value": "0.5,0.5,0.5,1"}]

    def test_material_property_routing_by_renderer(self) -> None:
        self.server.registered["editor_batch_set_material_property"](
            properties=self._material_props(),
            hierarchy_path="Root/Renderer",
            material_index=2,
        )
        kwargs = self.mock_send.call_args.kwargs
        self.assertEqual("Root/Renderer", kwargs["hierarchy_path"])
        self.assertEqual(2, kwargs["material_index"])
        self.assertNotIn("material_path", kwargs)
        self.assertNotIn("material_guid", kwargs)
        normalized = json.loads(kwargs["batch_operations_json"])
        self.assertEqual([{"name": "_Color", "value": "0.5,0.5,0.5,1"}], normalized)

    def test_material_property_routing_by_path(self) -> None:
        self.server.registered["editor_batch_set_material_property"](
            properties=self._material_props(),
            material_path="Assets/Mat.mat",
        )
        kwargs = self.mock_send.call_args.kwargs
        self.assertEqual("Assets/Mat.mat", kwargs["material_path"])
        self.assertNotIn("hierarchy_path", kwargs)
        self.assertNotIn("material_guid", kwargs)

    def test_material_property_routing_by_guid(self) -> None:
        guid = "a" * 32
        self.server.registered["editor_batch_set_material_property"](
            properties=self._material_props(),
            material_guid=guid,
        )
        kwargs = self.mock_send.call_args.kwargs
        self.assertEqual(guid, kwargs["material_guid"])
        self.assertNotIn("hierarchy_path", kwargs)
        self.assertNotIn("material_path", kwargs)

    def test_material_property_normalizes_list_value_to_json(self) -> None:
        self.server.registered["editor_batch_set_material_property"](
            properties=[{"name": "_Vec", "value": [1, 2, 3]}],
            material_path="Assets/Mat.mat",
        )
        kwargs = self.mock_send.call_args.kwargs
        normalized = json.loads(kwargs["batch_operations_json"])
        self.assertEqual([{"name": "_Vec", "value": "[1, 2, 3]"}], normalized)

    # --- editor_batch_add_component: properties get serialized to JSON ------

    def test_add_component_serializes_inline_properties_to_json(self) -> None:
        ops = [
            {
                "hierarchy_path": "A",
                "component_type": "AudioSource",
                "properties": [{"name": "_Volume", "value": 0.5}],
            }
        ]
        self.server.registered["editor_batch_add_component"](operations=ops)
        kwargs = self.mock_send.call_args.kwargs
        self.assertEqual("editor_batch_add_component", kwargs["action"])
        decoded = json.loads(kwargs["batch_operations_json"])
        self.assertEqual(1, len(decoded))
        op0 = decoded[0]
        self.assertNotIn("properties", op0)
        self.assertEqual(
            [{"name": "_Volume", "value": 0.5}],
            json.loads(op0["properties_json"]),
        )

    def test_add_component_preserves_pre_serialized_properties_json(self) -> None:
        ops = [
            {
                "hierarchy_path": "A",
                "component_type": "AudioSource",
                "properties_json": json.dumps([{"name": "_Volume", "value": 0.5}]),
            }
        ]
        self.server.registered["editor_batch_add_component"](operations=ops)
        kwargs = self.mock_send.call_args.kwargs
        decoded = json.loads(kwargs["batch_operations_json"])
        # Pre-serialized field is left untouched.
        self.assertEqual(
            [{"name": "_Volume", "value": 0.5}],
            json.loads(decoded[0]["properties_json"]),
        )

    # --- editor_open_scene --------------------------------------------------

    def test_open_scene_forwards_mode(self) -> None:
        self.server.registered["editor_open_scene"](
            scene_path="Assets/Scenes/Main.unity", mode="additive"
        )
        kwargs = self.mock_send.call_args.kwargs
        self.assertEqual("editor_open_scene", kwargs["action"])
        self.assertEqual("Assets/Scenes/Main.unity", kwargs["asset_path"])
        self.assertEqual("additive", kwargs["open_scene_mode"])

    # --- editor_save_scene --------------------------------------------------

    def test_save_scene_omits_path_when_blank(self) -> None:
        self.server.registered["editor_save_scene"]()
        kwargs = self.mock_send.call_args.kwargs
        self.assertEqual("editor_save_scene", kwargs["action"])
        self.assertNotIn("asset_path", kwargs)

    def test_save_scene_forwards_path_when_supplied(self) -> None:
        self.server.registered["editor_save_scene"](path="Assets/Scenes/Other.unity")
        kwargs = self.mock_send.call_args.kwargs
        self.assertEqual("Assets/Scenes/Other.unity", kwargs["asset_path"])

    # --- editor_create_scene ------------------------------------------------

    def test_create_scene_forwards_path(self) -> None:
        self.server.registered["editor_create_scene"](
            scene_path="Assets/Scenes/New.unity"
        )
        kwargs = self.mock_send.call_args.kwargs
        self.assertEqual("editor_create_scene", kwargs["action"])
        self.assertEqual("Assets/Scenes/New.unity", kwargs["asset_path"])


if __name__ == "__main__":
    unittest.main()
