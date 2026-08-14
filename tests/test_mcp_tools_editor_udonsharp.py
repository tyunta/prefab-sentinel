"""Issue #119 — contract tests for the three UdonSharp authoring MCP
tools.

The tools' boundaries are:

* ``editor_add_udonsharp_component`` forwards its arguments to the
  bridge as the ``editor_add_udonsharp_component`` action; an empty
  ``fields_json`` must not appear in the forwarded payload at all so
  the bridge's "skip initial-field assignment" branch is selected.
* ``editor_set_udonsharp_field`` rejects the value-vs-reference
  conflict and the both-empty case before contacting the bridge,
  forwards the in-range payload as ``editor_set_udonsharp_field``,
  and never sends both ``property_value`` and ``object_reference``
  in the same request.
* ``editor_wire_persistent_listener`` forwards every argument as the
  ``editor_wire_persistent_listener`` action.

Tests patch ``prefab_sentinel.mcp_tools_editor_udonsharp.send_action``
so no real Editor Bridge is required, and pop the editor-bridge env
vars in ``setUp`` so a host shell exporting ``editor`` mode does not
route requests to a live bridge mid-test (issue #88, #89).
"""

from __future__ import annotations

import unittest
from typing import Any
from unittest.mock import patch

from mcp.server.mcpserver.exceptions import ToolError

from prefab_sentinel import mcp_tools_editor_udonsharp
from prefab_sentinel.mcp_server import create_server
from tests._mcp_test_support import call_tool_result, structured_payload

_BRIDGE_OK = {
    "success": True,
    "severity": "info",
    "code": "EDITOR_CTRL_UDON_OK",
    "message": "ok",
    "data": {},
    "diagnostics": [],
}


class _UdonSharpToolHarness(unittest.TestCase):
    """Common harness that invokes UdonSharp tools through MCPServer."""

    def setUp(self) -> None:
        self.server = create_server()

    def _call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        result = call_tool_result(self.server, name, arguments)
        self.assertIs(result.is_error, False)
        return structured_payload(result)

    def add_tool(self, **arguments: Any) -> dict[str, Any]:
        return self._call_tool("editor_add_udonsharp_component", arguments)

    def set_tool(self, **arguments: Any) -> dict[str, Any]:
        return self._call_tool("editor_set_udonsharp_field", arguments)

    def wire_tool(self, **arguments: Any) -> dict[str, Any]:
        return self._call_tool("editor_wire_persistent_listener", arguments)


class AddUdonSharpComponentForwardingTests(_UdonSharpToolHarness):
    """Contract tests for ``editor_add_udonsharp_component`` forwarding."""

    def test_forwards_payload(self) -> None:
        with patch.object(mcp_tools_editor_udonsharp, "send_action") as send:
            send.return_value = _BRIDGE_OK
            self.add_tool(
                hierarchy_path="/UI/Play",
                type_full_name="VVMW.PlayController",
                fields_json='{"defaultUrl": "https://example.com/clip.m3u8"}',
                confirm=True,
                change_reason="add play controller",
            )
        send.assert_called_once()
        kwargs = send.call_args.kwargs
        self.assertEqual("editor_add_udonsharp_component", kwargs["action"])
        self.assertEqual("/UI/Play", kwargs["hierarchy_path"])
        self.assertEqual("VVMW.PlayController", kwargs["component_type"])
        self.assertEqual(
            '{"defaultUrl": "https://example.com/clip.m3u8"}',
            kwargs["fields_json"],
        )

    def test_omits_fields_json_when_empty(self) -> None:
        """An empty ``fields_json`` must not appear in the payload —
        the bridge's "skip initial-field assignment" branch is keyed off
        the field's absence, not its emptiness, so the client must omit
        it explicitly to keep the surface unambiguous.
        """
        with patch.object(mcp_tools_editor_udonsharp, "send_action") as send:
            send.return_value = _BRIDGE_OK
            self.add_tool(
                hierarchy_path="/UI/Play",
                type_full_name="VVMW.PlayController",
                confirm=True,
                change_reason="add play controller",
            )
        send.assert_called_once()
        kwargs = send.call_args.kwargs
        self.assertNotIn("fields_json", kwargs)

    def test_passes_through_bridge_envelope(self) -> None:
        """The client tool must not rewrite the bridge envelope."""
        with patch.object(mcp_tools_editor_udonsharp, "send_action") as send:
            send.return_value = _BRIDGE_OK
            resp = self.add_tool(
                hierarchy_path="/UI/Play",
                type_full_name="VVMW.PlayController",
                confirm=True,
                change_reason="add play controller",
            )
        self.assertEqual(_BRIDGE_OK, resp)

    def test_requires_audit_pair(self) -> None:
        with patch.object(mcp_tools_editor_udonsharp, "send_action") as send:
            response = self.add_tool(
                hierarchy_path="/UI/Play",
                type_full_name="VVMW.PlayController",
            )

        send.assert_not_called()
        self.assertEqual(
            (False, "error", "CHANGE_REASON_REQUIRED"),
            (response["success"], response["severity"], response["code"]),
            msg=f"add UdonSharp audit rejection mismatch: {response!r}",
        )


class SetUdonSharpFieldForwardingTests(_UdonSharpToolHarness):
    """Contract tests for ``editor_set_udonsharp_field`` forwarding."""

    def test_value_branch_forwards_property_value(self) -> None:
        with patch.object(mcp_tools_editor_udonsharp, "send_action") as send:
            send.return_value = _BRIDGE_OK
            self.set_tool(
                hierarchy_path="/UI/Play",
                property_name="defaultUrl",
                value="https://example.com/clip.m3u8",
                confirm=True,
                change_reason="set default url",
            )
        send.assert_called_once()
        kwargs = send.call_args.kwargs
        self.assertEqual("editor_set_udonsharp_field", kwargs["action"])
        self.assertEqual("/UI/Play", kwargs["hierarchy_path"])
        # Wire DTO field stays ``field_name``; MCP arg is property_name.
        self.assertEqual("defaultUrl", kwargs["field_name"])
        self.assertEqual(
            "https://example.com/clip.m3u8",
            kwargs["property_value"],
        )
        # Issue #52: the value-present marker accompanies the value.
        self.assertTrue(kwargs["property_value_present"])
        self.assertNotIn("object_reference", kwargs)

    def test_reference_branch_forwards_object_reference(self) -> None:
        with patch.object(mcp_tools_editor_udonsharp, "send_action") as send:
            send.return_value = _BRIDGE_OK
            self.set_tool(
                hierarchy_path="/UI/Play",
                property_name="targetUdon",
                object_reference="/Logic/UdonController",
                confirm=True,
                change_reason="wire target udon",
            )
        send.assert_called_once()
        kwargs = send.call_args.kwargs
        self.assertEqual("/Logic/UdonController", kwargs["object_reference"])
        self.assertNotIn("property_value", kwargs)

    def test_requires_audit_pair_for_valid_write(self) -> None:
        with patch.object(mcp_tools_editor_udonsharp, "send_action") as send:
            response = self.set_tool(
                hierarchy_path="/UI/Play",
                property_name="defaultUrl",
                value="https://example.com/clip.m3u8",
            )

        send.assert_not_called()
        self.assertEqual(
            (False, "error", "CHANGE_REASON_REQUIRED"),
            (response["success"], response["severity"], response["code"]),
            msg=f"set UdonSharp field audit rejection mismatch: {response!r}",
        )


class SetUdonSharpFieldArrayTests(_UdonSharpToolHarness):
    def test_values_json_forwards_array_payload_and_expected_length(self) -> None:
        with patch.object(
            mcp_tools_editor_udonsharp, "send_action", return_value=_BRIDGE_OK,
        ) as send:
            response = self.set_tool(
                hierarchy_path="/World/Udon",
                property_name="labels",
                values_json='["a","b"]',
                expected_length=2,
                confirm=True,
                change_reason="set label array",
            )

        self.assertEqual(response, _BRIDGE_OK)
        self.assertEqual(
            {
                "action": "editor_set_udonsharp_field",
                "hierarchy_path": "/World/Udon",
                "field_name": "labels",
                "values_json": '["a","b"]',
                "values_json_present": True,
                "expected_length": 2,
            },
            send.call_args.kwargs,
            msg=f"array payload was not forwarded exactly: {send.call_args.kwargs!r}",
        )

    def test_empty_values_json_is_treated_as_omitted(self) -> None:
        with patch.object(
            mcp_tools_editor_udonsharp, "send_action", return_value=_BRIDGE_OK,
        ) as send:
            response = self.set_tool(
                hierarchy_path="/World/Udon",
                property_name="labels",
                values_json="",
                confirm=True,
                change_reason="leave label array omitted",
            )

        send.assert_not_called()
        self.assertEqual(
            (False, "error", "EDITOR_CTRL_UDON_SET_FIELD_NO_VALUE"),
            (
                response["success"],
                response["severity"],
                response["code"],
            ),
        )

    def test_values_json_rejects_conflicts(self) -> None:
        with patch.object(
            mcp_tools_editor_udonsharp, "send_action", return_value=_BRIDGE_OK,
        ) as send:
            response = self.set_tool(
                hierarchy_path="/World/Udon",
                property_name="labels",
                value="scalar",
                values_json='["a"]',
            )

        send.assert_not_called()
        self.assertEqual(
            (False, "error", "EDITOR_CTRL_UDON_SET_FIELD_INPUT_CONFLICT", True),
            (
                response["success"],
                response["severity"],
                response["code"],
                "values_json" in response["message"],
            ),
            msg=f"array conflict envelope was not specific: {response!r}",
        )


class SetUdonSharpFieldValidationTests(_UdonSharpToolHarness):
    """Local-validation tests for ``editor_set_udonsharp_field``."""

    def test_rejects_both_inputs(self) -> None:
        with patch.object(mcp_tools_editor_udonsharp, "send_action") as send:
            resp = self.set_tool(
                hierarchy_path="/UI/Play",
                property_name="defaultUrl",
                value="x",
                object_reference="/Logic/UdonController",
            )
        self.assertFalse(resp["success"])
        self.assertEqual("error", resp["severity"])
        self.assertEqual("EDITOR_CTRL_UDON_SET_FIELD_INPUT_CONFLICT", resp["code"])
        send.assert_not_called()

    def test_omitted_values_json_is_not_supplied(self) -> None:
        with patch.object(mcp_tools_editor_udonsharp, "send_action") as send:
            resp = self.set_tool(
                hierarchy_path="/UI/Play",
                property_name="defaultUrl",
            )
        self.assertFalse(resp["success"])
        self.assertEqual("error", resp["severity"])
        self.assertEqual("EDITOR_CTRL_UDON_SET_FIELD_NO_VALUE", resp["code"])
        send.assert_not_called()

    def test_explicit_null_values_json_is_rejected_by_public_validation(self) -> None:
        with self.assertRaises(ToolError) as cm:
            self.set_tool(
                hierarchy_path="/UI/Play",
                property_name="labels",
                values_json=None,
            )

        self.assertIn("values_json", str(cm.exception))
        self.assertIn("valid string", str(cm.exception))


class WirePersistentListenerForwardingTests(_UdonSharpToolHarness):
    """Contract tests for ``editor_wire_persistent_listener`` forwarding."""

    def test_forwards_full_payload(self) -> None:
        with patch.object(mcp_tools_editor_udonsharp, "send_action") as send:
            send.return_value = _BRIDGE_OK
            self.wire_tool(
                hierarchy_path="/UI/Slider",
                property_name="onValueChanged",
                target_hierarchy_path="/Logic/UdonController",
                method="SendCustomEvent",
                arg="OnSliderChanged",
                confirm=True,
                change_reason="wire slider listener",
            )
        send.assert_called_once()
        kwargs = send.call_args.kwargs
        self.assertEqual(
            (
                "editor_wire_persistent_listener",
                "/UI/Slider",
                "onValueChanged",
                "/Logic/UdonController",
                "SendCustomEvent",
                "OnSliderChanged",
            ),
            (
                kwargs["action"],
                kwargs["hierarchy_path"],
                kwargs["event_property_name"],
                kwargs["target_path"],
                kwargs["method"],
                kwargs["arg"],
            ),
            msg=(
                "editor_wire_persistent_listener must forward every argument "
                "on the correct wire keys; event_property_name carries the "
                "property_name value (issue #61)."
            ),
        )

    def test_requires_audit_pair(self) -> None:
        with patch.object(mcp_tools_editor_udonsharp, "send_action") as send:
            response = self.wire_tool(
                hierarchy_path="/UI/Slider",
                property_name="onValueChanged",
                target_hierarchy_path="/Logic/UdonController",
                method="SendCustomEvent",
                arg="OnSliderChanged",
            )

        send.assert_not_called()
        self.assertEqual(
            (False, "error", "CHANGE_REASON_REQUIRED"),
            (response["success"], response["severity"], response["code"]),
            msg=f"wire persistent listener audit rejection mismatch: {response!r}",
        )


if __name__ == "__main__":
    unittest.main()
