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

import os
import unittest
from unittest.mock import patch

from prefab_sentinel import mcp_tools_editor_udonsharp

_BRIDGE_ENV_VARS = ("UNITYTOOL_BRIDGE_MODE", "UNITYTOOL_BRIDGE_WATCH_DIR")


def _drop_bridge_env() -> None:
    for var in _BRIDGE_ENV_VARS:
        os.environ.pop(var, None)


_BRIDGE_OK = {
    "success": True,
    "severity": "info",
    "code": "EDITOR_CTRL_UDON_OK",
    "message": "ok",
    "data": {},
    "diagnostics": [],
}


def _resolve_tool(name: str):
    """Resolve a registered FastMCP tool by *name* by registering tools
    against a stub server and returning the underlying callable.

    The registration entry point uses ``@server.tool()`` decorators
    which the FastMCP server records in its ``_tool_manager``; the
    underlying callable is exposed as ``Tool.fn`` (or the equivalent
    attribute on the FastMCP version under test).
    """
    from mcp.server.fastmcp import FastMCP

    server = FastMCP(name="udonsharp-test")
    mcp_tools_editor_udonsharp.register_editor_udonsharp_tools(server)
    tools = server._tool_manager._tools  # type: ignore[attr-defined]
    return tools[name].fn


class _UdonSharpToolHarness(unittest.TestCase):
    """Common harness: resolve the three tools once per test class.

    The tools are stored as staticmethods so unittest does not treat
    them as bound methods of the test class (which would inject
    ``self`` as the first argument and break the tool signature).
    """

    add_tool = staticmethod(_resolve_tool("editor_add_udonsharp_component"))
    set_tool = staticmethod(_resolve_tool("editor_set_udonsharp_field"))
    wire_tool = staticmethod(_resolve_tool("editor_wire_persistent_listener"))

    def setUp(self) -> None:
        _drop_bridge_env()


class AddUdonSharpComponentForwardingTests(_UdonSharpToolHarness):
    """Contract tests for ``editor_add_udonsharp_component`` forwarding."""

    def test_forwards_payload(self) -> None:
        with patch.object(mcp_tools_editor_udonsharp, "send_action") as send:
            send.return_value = _BRIDGE_OK
            self.add_tool(
                hierarchy_path="/UI/Play",
                type_full_name="VVMW.PlayController",
                fields_json='{"defaultUrl": "https://example.com/clip.m3u8"}',
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
            )
        self.assertIs(_BRIDGE_OK, resp)


class SetUdonSharpFieldForwardingTests(_UdonSharpToolHarness):
    """Contract tests for ``editor_set_udonsharp_field`` forwarding."""

    def test_value_branch_forwards_property_value(self) -> None:
        with patch.object(mcp_tools_editor_udonsharp, "send_action") as send:
            send.return_value = _BRIDGE_OK
            self.set_tool(
                hierarchy_path="/UI/Play",
                field_name="defaultUrl",
                value="https://example.com/clip.m3u8",
            )
        send.assert_called_once()
        kwargs = send.call_args.kwargs
        self.assertEqual("editor_set_udonsharp_field", kwargs["action"])
        self.assertEqual("/UI/Play", kwargs["hierarchy_path"])
        self.assertEqual("defaultUrl", kwargs["field_name"])
        self.assertEqual(
            "https://example.com/clip.m3u8",
            kwargs["property_value"],
        )
        self.assertNotIn("object_reference", kwargs)

    def test_reference_branch_forwards_object_reference(self) -> None:
        with patch.object(mcp_tools_editor_udonsharp, "send_action") as send:
            send.return_value = _BRIDGE_OK
            self.set_tool(
                hierarchy_path="/UI/Play",
                field_name="targetUdon",
                object_reference="/Logic/UdonController",
            )
        send.assert_called_once()
        kwargs = send.call_args.kwargs
        self.assertEqual("/Logic/UdonController", kwargs["object_reference"])
        self.assertNotIn("property_value", kwargs)


class SetUdonSharpFieldValidationTests(_UdonSharpToolHarness):
    """Local-validation tests for ``editor_set_udonsharp_field``."""

    def test_rejects_both_inputs(self) -> None:
        with patch.object(mcp_tools_editor_udonsharp, "send_action") as send:
            resp = self.set_tool(
                hierarchy_path="/UI/Play",
                field_name="defaultUrl",
                value="x",
                object_reference="/Logic/UdonController",
            )
        self.assertFalse(resp["success"])
        self.assertEqual("error", resp["severity"])
        self.assertEqual("EDITOR_CTRL_SET_PROP_BOTH_VALUE", resp["code"])
        send.assert_not_called()

    def test_rejects_neither_input(self) -> None:
        with patch.object(mcp_tools_editor_udonsharp, "send_action") as send:
            resp = self.set_tool(
                hierarchy_path="/UI/Play",
                field_name="defaultUrl",
            )
        self.assertFalse(resp["success"])
        self.assertEqual("error", resp["severity"])
        self.assertEqual("EDITOR_CTRL_UDON_SET_FIELD_NO_VALUE", resp["code"])
        send.assert_not_called()


class WirePersistentListenerForwardingTests(_UdonSharpToolHarness):
    """Contract tests for ``editor_wire_persistent_listener`` forwarding."""

    def test_forwards_full_payload(self) -> None:
        with patch.object(mcp_tools_editor_udonsharp, "send_action") as send:
            send.return_value = _BRIDGE_OK
            self.wire_tool(
                hierarchy_path="/UI/Slider",
                event_path="onValueChanged",
                target_path="/Logic/UdonController",
                method="SendCustomEvent",
                arg="OnSliderChanged",
            )
        send.assert_called_once()
        kwargs = send.call_args.kwargs
        self.assertEqual(
            "editor_wire_persistent_listener", kwargs["action"]
        )
        self.assertEqual("/UI/Slider", kwargs["hierarchy_path"])
        self.assertEqual("onValueChanged", kwargs["event_path"])
        self.assertEqual("/Logic/UdonController", kwargs["target_path"])
        self.assertEqual("SendCustomEvent", kwargs["method"])
        self.assertEqual("OnSliderChanged", kwargs["arg"])


if __name__ == "__main__":
    unittest.main()
