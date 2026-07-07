from __future__ import annotations

import unittest

from tests._mcp_tool_recorder import ToolRecorderServer


class ToolRecorderServerTests(unittest.TestCase):
    def test_tool_decorator_records_unnamed_and_named_tools(self) -> None:
        server = ToolRecorderServer()

        @server.tool()
        def default_name() -> str:
            return "default"

        @server.tool(name="external_name")
        def internal_name() -> str:
            return "external"

        self.assertEqual(
            {"default_name": default_name, "external_name": internal_name},
            server.registered,
        )

    def test_get_returns_registered_callable_and_names_missing_tools(self) -> None:
        server = ToolRecorderServer()

        @server.tool()
        def present() -> str:
            return "ok"

        self.assertEqual("ok", server.get("present")())
        with self.assertRaises(AssertionError) as cm:
            server.get("missing")

        message = str(cm.exception)
        self.assertIn("missing", message)
        self.assertIn("present", message)
