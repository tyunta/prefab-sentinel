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
        # Issue #260: honour the FastMCP ``@server.tool(name="...")``
        # convention.  When the caller supplies a non-empty ``name``
        # kwarg the registry key is that explicit external name;
        # otherwise the recorder falls back to the Python function
        # name so legacy ``@server.tool()`` registrations keep working.
        explicit_name = kwargs.get("name")
        def _decorator(fn):  # noqa: ANN001
            key = explicit_name if explicit_name else fn.__name__
            self.registered[key] = fn
            return fn

        return _decorator


def _register() -> _RecorderServer:
    server = _RecorderServer()
    mcp_tools_editor_batch.register_editor_batch_tools(server)  # type: ignore[arg-type]
    return server


_DOCUMENTED_TOOLS = {
    "editor_create_empty",
    "editor_create_primitive",
    "editor_create_ui_element",
    "editor_batch_create",
    "editor_batch_set_property",
    "editor_batch_set_material_property",
    "editor_batch_add_component",
    "editor_open_scene",
    "editor_save_scene",
    "editor_create_scene",
    # Issue #240: batch blend-shape write with single-Undo grouping.
    "editor_batch_set_blend_shape",
}


class RecorderServerNameKwargTests(unittest.TestCase):
    """Issue #260 — ``_RecorderServer.tool`` keys the registered
    callable by the explicit ``name=`` kwarg when supplied, otherwise
    by the Python function's own name.

    The production module uses ``@server.tool(name="editor_screenshot")``
    decorators whose Python function names diverge from the registered
    external names (``_editor_screenshot`` etc.).  If the recorder
    ignored ``name=``, the registration suite would observe the wrong
    keys and the bug would silently mask renames.
    """

    def test_explicit_name_kwarg_is_used_as_registration_key(self) -> None:
        server = _RecorderServer()

        @server.tool(name="alt")
        def _python_name_differs() -> None:
            return None

        # Pin: registered under the explicit kwarg value; the Python
        # function's own name is NOT a registry key.
        self.assertEqual(
            (True, False),
            (
                "alt" in server.registered,
                "_python_name_differs" in server.registered,
            ),
            msg=(
                "Recorder must key by the explicit name= kwarg when "
                "supplied; the Python function name must not appear as "
                "a fallback key alongside it (#260)."
            ),
        )

    def test_function_name_fallback_when_kwarg_omitted(self) -> None:
        server = _RecorderServer()

        @server.tool()
        def f() -> None:
            return None

        # When no name= kwarg is supplied the recorder must fall back
        # to the Python function name so legacy registrations keep
        # working.
        self.assertEqual(
            (True, f),
            ("f" in server.registered, server.registered.get("f")),
            msg=(
                "Recorder must fall back to the Python function name "
                "when no name= kwarg is supplied (#260)."
            ),
        )


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
            name="Child", parent_hierarchy_path="Parent", position="1,2,3"
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
            parent_hierarchy_path="Root",
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
        # Issue #52: each op is stamped with a value-present marker.
        sent = json.loads(kwargs["batch_operations_json"])
        self.assertEqual(1, len(sent))
        self.assertTrue(sent[0]["value_present"])
        self.assertEqual("1,1,1", sent[0]["value"])

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
            material_asset_path="Assets/Mat.mat",
        )
        kwargs = self.mock_send.call_args.kwargs
        self.assertEqual("Assets/Mat.mat", kwargs["material_path"])
        self.assertNotIn("hierarchy_path", kwargs)
        self.assertNotIn("material_guid", kwargs)

    def test_material_property_routing_by_guid(self) -> None:
        guid = "a" * 32
        self.server.registered["editor_batch_set_material_property"](
            properties=self._material_props(),
            material_asset_guid=guid,
        )
        kwargs = self.mock_send.call_args.kwargs
        self.assertEqual(guid, kwargs["material_guid"])
        self.assertNotIn("hierarchy_path", kwargs)
        self.assertNotIn("material_path", kwargs)

    def test_material_property_normalizes_list_value_to_json(self) -> None:
        self.server.registered["editor_batch_set_material_property"](
            properties=[{"name": "_Vec", "value": [1, 2, 3]}],
            material_asset_path="Assets/Mat.mat",
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
            asset_path="Assets/Scenes/Main.unity", mode="additive"
        )
        kwargs = self.mock_send.call_args.kwargs
        self.assertEqual("editor_open_scene", kwargs["action"])
        self.assertEqual("Assets/Scenes/Main.unity", kwargs["asset_path"])
        self.assertEqual("additive", kwargs["open_scene_mode"])

    # --- editor_save_scene --------------------------------------------------

    def test_save_scene_omits_path_when_blank(self) -> None:
        self.server.registered["editor_save_scene"](
            confirm=True, change_reason="save scene",
        )
        kwargs = self.mock_send.call_args.kwargs
        self.assertEqual("editor_save_scene", kwargs["action"])
        self.assertNotIn("asset_path", kwargs)

    def test_save_scene_forwards_path_when_supplied(self) -> None:
        self.server.registered["editor_save_scene"](
            asset_path="Assets/Scenes/Other.unity",
            confirm=True, change_reason="save scene",
        )
        kwargs = self.mock_send.call_args.kwargs
        self.assertEqual("Assets/Scenes/Other.unity", kwargs["asset_path"])

    def test_save_scene_without_audit_pair_rejected(self) -> None:
        # Issue #49: editor_save_scene gates on the audit pair.
        response = self.server.registered["editor_save_scene"]()
        self.assertEqual("CHANGE_REASON_REQUIRED", response["code"])
        self.mock_send.assert_not_called()

    # --- editor_create_scene ------------------------------------------------

    def test_create_scene_forwards_path(self) -> None:
        self.server.registered["editor_create_scene"](
            asset_path="Assets/Scenes/New.unity",
            confirm=True, change_reason="create scene",
        )
        kwargs = self.mock_send.call_args.kwargs
        self.assertEqual("editor_create_scene", kwargs["action"])
        self.assertEqual("Assets/Scenes/New.unity", kwargs["asset_path"])

    def test_create_scene_without_audit_pair_rejected(self) -> None:
        # Issue #49: editor_create_scene gates on the audit pair.
        response = self.server.registered["editor_create_scene"](
            asset_path="Assets/Scenes/New.unity",
        )
        self.assertEqual("CHANGE_REASON_REQUIRED", response["code"])
        self.mock_send.assert_not_called()

    # --- editor_create_ui_element (issue #195) -----------------------------

    def test_create_ui_element_image_with_anchor_size_delta_and_color(self) -> None:
        # Issue #195: rect anchor / sizeDelta are first-class parameters;
        # ``color`` is a recognized graphic property that the Bridge applies
        # to the primary Graphic component. The Python tool must serialize
        # both payloads and forward the typed action so the Bridge's typed
        # envelope governs the response.
        self.server.registered["editor_create_ui_element"](
            name="MyImage",
            type="Image",
            parent_hierarchy_path="Canvas",
            rect={
                "anchorMin": [0.0, 0.0],
                "anchorMax": [1.0, 1.0],
                "sizeDelta": [0.0, 0.0],
            },
            properties={"color": [0.9, 0.6, 0.2, 1.0]},
        )
        kwargs = self.mock_send.call_args.kwargs
        self.assertEqual("editor_create_ui_element", kwargs["action"])
        self.assertEqual("MyImage", kwargs["new_name"])
        self.assertEqual("Image", kwargs["component_type"])
        self.assertEqual("Canvas", kwargs["hierarchy_path"])
        rect_payload = json.loads(kwargs["ui_rect_json"])
        self.assertEqual([0.0, 0.0], rect_payload["anchorMin"])
        self.assertEqual([1.0, 1.0], rect_payload["anchorMax"])
        self.assertEqual([0.0, 0.0], rect_payload["sizeDelta"])
        properties = json.loads(kwargs["ui_properties_json"])
        self.assertEqual([0.9, 0.6, 0.2, 1.0], properties["color"])

    def test_create_ui_element_tmp_with_explicit_font(self) -> None:
        self.server.registered["editor_create_ui_element"](
            name="Label",
            type="TextMeshProUGUI",
            properties={
                "font": "Assets/TextMesh Pro/Resources/Fonts & Materials/LiberationSans SDF.asset",
            },
        )
        kwargs = self.mock_send.call_args.kwargs
        self.assertEqual("editor_create_ui_element", kwargs["action"])
        self.assertEqual("TextMeshProUGUI", kwargs["component_type"])
        properties = json.loads(kwargs["ui_properties_json"])
        self.assertEqual(
            "Assets/TextMesh Pro/Resources/Fonts & Materials/LiberationSans SDF.asset",
            properties["font"],
        )


class EditorBatchSetBlendShapeNoAuditTests(unittest.TestCase):
    """Issue #49 — ``editor_batch_set_blend_shape`` carries no audit pair.

    Blend-shape weight changes are Undo-reversible live scene edits, so
    the inverse-irreversibility principle does not gate the tool;
    passing a ``confirm`` argument is a ``TypeError``.
    """

    def test_confirm_argument_raises_type_error(self) -> None:
        with self.assertRaises(TypeError) as cm:
            mcp_tools_editor_batch.editor_batch_set_blend_shape(
                hierarchy_path="/Avatar/Body",
                shapes=[{"name": "Smile", "weight": 50.0}],
                confirm=True,
                change_reason="ship smile",
            )
        self.assertIn("confirm", str(cm.exception))


class EditorBatchSetBlendShapeForwardingTests(unittest.TestCase):
    """Issue #240 / #49 — ``editor_batch_set_blend_shape`` forwards every
    input (no audit pair after #49)."""

    def setUp(self) -> None:
        self.send_patcher = mock.patch.object(
            mcp_tools_editor_batch, "send_action",
            return_value={"success": True},
        )
        self.mock_send = self.send_patcher.start()
        self.addCleanup(self.send_patcher.stop)

    def test_two_entry_list_forwards_parseable_payload(self) -> None:
        mcp_tools_editor_batch.editor_batch_set_blend_shape(
            hierarchy_path="/Avatar/Body",
            shapes=[
                {"name": "Smile", "weight": 50.0},
                {"name": "Frown", "weight": 0.0},
            ],
        )
        self.mock_send.assert_called_once()
        kwargs = self.mock_send.call_args.kwargs
        decoded = json.loads(kwargs["shapes_json"])
        # Value-pin the action, hierarchy path, and decoded payload
        # length together so a regression in any single forwarded field
        # surfaces alongside the rest. Issue #49 removed the audit pair.
        self.assertEqual(
            ("batch_set_blend_shape", "/Avatar/Body", 2),
            (kwargs["action"], kwargs["hierarchy_path"], len(decoded)),
            msg=(
                "Batch blend-shape write must forward the batch action, "
                "the hierarchy path, and a JSON payload whose decoded "
                "length equals the input list length."
            ),
        )
        self.assertNotIn("confirm", kwargs)
        self.assertNotIn("change_reason", kwargs)

    def test_empty_list_still_calls_bridge(self) -> None:
        mcp_tools_editor_batch.editor_batch_set_blend_shape(
            hierarchy_path="/Avatar/Body",
            shapes=[],
        )
        self.mock_send.assert_called_once()
        kwargs = self.mock_send.call_args.kwargs
        decoded = json.loads(kwargs["shapes_json"])
        # Empty list must reach the bridge as a payload of length 0, not
        # short-circuit at the wrapper — the bridge owns the empty-batch
        # semantics so the envelope shape stays consistent for callers.
        self.assertEqual(
            ("batch_set_blend_shape", "/Avatar/Body", 0),
            (kwargs["action"], kwargs["hierarchy_path"], len(decoded)),
            msg=(
                "Empty shapes list must reach the bridge with a "
                "zero-length payload, not short-circuit at the wrapper."
            ),
        )


if __name__ == "__main__":
    unittest.main()
