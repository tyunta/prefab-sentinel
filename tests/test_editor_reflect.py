"""Tests for editor_reflect MCP tool — parameter validation and response unwrapping."""

from __future__ import annotations

import json
import unittest
from typing import Any
from unittest.mock import patch

from prefab_sentinel.mcp_server import create_server
from tests.test_mcp_server import _run


class TestEditorReflectValidation(unittest.TestCase):
    """Pre-bridge parameter validation returns errors without calling bridge."""

    def setUp(self) -> None:
        self.server = create_server()

    def test_should_reject_unknown_action(self) -> None:
        with patch("prefab_sentinel.mcp_server.send_action") as mock_send:
            _, result = _run(self.server.call_tool("editor_reflect", {"action": "bogus"}))
        mock_send.assert_not_called()
        self.assertFalse(result["success"])
        self.assertEqual("EDITOR_REFLECT_UNKNOWN_ACTION", result["code"])

    def test_should_reject_invalid_scope(self) -> None:
        with patch("prefab_sentinel.mcp_server.send_action") as mock_send:
            _, result = _run(
                self.server.call_tool("editor_reflect", {"action": "search", "query": "Foo", "scope": "invalid"})
            )
        mock_send.assert_not_called()
        self.assertFalse(result["success"])
        self.assertEqual("EDITOR_REFLECT_INVALID_SCOPE", result["code"])

    def test_should_reject_search_without_query(self) -> None:
        with patch("prefab_sentinel.mcp_server.send_action") as mock_send:
            _, result = _run(self.server.call_tool("editor_reflect", {"action": "search"}))
        mock_send.assert_not_called()
        self.assertFalse(result["success"])
        self.assertEqual("EDITOR_REFLECT_MISSING_PARAM", result["code"])

    def test_should_reject_get_type_without_class_name(self) -> None:
        with patch("prefab_sentinel.mcp_server.send_action") as mock_send:
            _, result = _run(self.server.call_tool("editor_reflect", {"action": "get_type"}))
        mock_send.assert_not_called()
        self.assertFalse(result["success"])
        self.assertEqual("EDITOR_REFLECT_MISSING_PARAM", result["code"])

    def test_should_reject_get_member_without_class_name(self) -> None:
        with patch("prefab_sentinel.mcp_server.send_action") as mock_send:
            _, result = _run(
                self.server.call_tool("editor_reflect", {"action": "get_member", "member_name": "Position"})
            )
        mock_send.assert_not_called()
        self.assertFalse(result["success"])
        self.assertEqual("EDITOR_REFLECT_MISSING_PARAM", result["code"])

    def test_should_reject_get_member_without_member_name(self) -> None:
        with patch("prefab_sentinel.mcp_server.send_action") as mock_send:
            _, result = _run(
                self.server.call_tool("editor_reflect", {"action": "get_member", "class_name": "Transform"})
            )
        mock_send.assert_not_called()
        self.assertFalse(result["success"])
        self.assertEqual("EDITOR_REFLECT_MISSING_PARAM", result["code"])


class TestEditorReflectResponseUnwrap(unittest.TestCase):
    """Post-bridge reflect_result_json unwrapping."""

    def setUp(self) -> None:
        self.server = create_server()

    def _make_bridge_response(self, reflect_data: dict[str, Any]) -> dict[str, Any]:
        return {
            "success": True,
            "severity": "info",
            "code": "EDITOR_REFLECT_OK",
            "message": "OK",
            "data": {"reflect_result_json": json.dumps(reflect_data)},
            "diagnostics": [],
        }

    def test_should_unwrap_reflect_result_json_into_data(self) -> None:
        inner = {"found": True, "name": "Transform", "full_name": "UnityEngine.Transform"}
        bridge_resp = self._make_bridge_response(inner)
        with patch("prefab_sentinel.mcp_server.send_action", return_value=bridge_resp):
            _, result = _run(
                self.server.call_tool("editor_reflect", {"action": "get_type", "class_name": "Transform"})
            )
        self.assertTrue(result["success"])
        self.assertEqual("Transform", result["data"]["name"])
        self.assertNotIn("reflect_result_json", result["data"])

    def test_should_return_parse_error_on_malformed_json(self) -> None:
        bridge_resp = {
            "success": True,
            "severity": "info",
            "code": "EDITOR_REFLECT_OK",
            "message": "OK",
            "data": {"reflect_result_json": "{broken json!!!"},
            "diagnostics": [],
        }
        with patch("prefab_sentinel.mcp_server.send_action", return_value=bridge_resp):
            _, result = _run(
                self.server.call_tool("editor_reflect", {"action": "get_type", "class_name": "Transform"})
            )
        self.assertFalse(result["success"])
        self.assertEqual("EDITOR_REFLECT_PARSE", result["code"])

    def test_should_pass_through_bridge_error_response(self) -> None:
        bridge_resp = {
            "success": False,
            "severity": "error",
            "code": "EDITOR_BRIDGE_TIMEOUT",
            "message": "Timed out",
            "data": {},
            "diagnostics": [],
        }
        with patch("prefab_sentinel.mcp_server.send_action", return_value=bridge_resp):
            _, result = _run(
                self.server.call_tool("editor_reflect", {"action": "search", "query": "Foo"})
            )
        self.assertFalse(result["success"])
        self.assertEqual("EDITOR_BRIDGE_TIMEOUT", result["code"])

    def test_should_accept_valid_scope_values(self) -> None:
        for scope in ("unity", "packages", "project", "all"):
            inner = {"query": "Foo", "scope": scope, "count": 0, "results": [], "truncated": False}
            bridge_resp = self._make_bridge_response(inner)
            with patch("prefab_sentinel.mcp_server.send_action", return_value=bridge_resp):
                _, result = _run(
                    self.server.call_tool("editor_reflect", {"action": "search", "query": "Foo", "scope": scope})
                )
            self.assertTrue(result["success"], f"scope={scope} should be accepted")


if __name__ == "__main__":
    unittest.main()
