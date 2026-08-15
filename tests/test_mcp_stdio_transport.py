"""Subprocess contract tests for the real MCP stdio CLI transport."""

from __future__ import annotations

import json
import unittest
from typing import Any, cast

from tests._mcp_wire_support import (
    LEGACY_PROTOCOL_VERSIONS,
    assert_jsonrpc_error,
    assert_jsonrpc_result,
    legacy_initialize_request,
    modern_request,
    running_mcp_cli,
)


class TestMCPStdioTransport(unittest.TestCase):
    def _assert_legacy_wire_contract(self, protocol_version: str) -> None:
        with running_mcp_cli("--transport", "stdio", pipe_stdin=True) as child:
            initialized_response = child.request_json_line(
                legacy_initialize_request(request_id=1, version=protocol_version),
            )
            child.write_json_line(
                {
                    "jsonrpc": "2.0",
                    "method": "notifications/initialized",
                    "params": {},
                },
            )
            listed_response = child.request_json_line(
                {
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "tools/list",
                    "params": {},
                },
            )
            tool_response = child.request_json_line(
                {
                    "jsonrpc": "2.0",
                    "id": 3,
                    "method": "tools/call",
                    "params": {
                        "name": "get_project_status",
                        "arguments": {},
                    },
                },
            )
            resources_response = child.request_json_line(
                {
                    "jsonrpc": "2.0",
                    "id": 4,
                    "method": "resources/list",
                    "params": {},
                },
            )
            modern_response = child.request_json_line(
                modern_request("tools/list", request_id=5),
            )
            listed_after_modern_response = child.request_json_line(
                {
                    "jsonrpc": "2.0",
                    "id": 6,
                    "method": "tools/list",
                    "params": {},
                },
            )
            decoded = [
                initialized_response,
                listed_response,
                tool_response,
                resources_response,
                modern_response,
                listed_after_modern_response,
            ]
            child.communicate()
            self.assertEqual(0, child.returncode)

            wire_responses = [json.loads(line) for line in child.stdout.splitlines()]
            self.assertEqual(decoded, wire_responses)
            self.assertEqual(6, len(decoded), f"responses={decoded!r}")

        initialized = assert_jsonrpc_result(self, initialized_response, request_id=1)
        self.assertEqual(protocol_version, initialized["protocolVersion"])
        self.assertEqual({"tools": {"listChanged": False}}, initialized["capabilities"])
        self.assertEqual("prefab-sentinel", initialized["serverInfo"]["name"])
        self.assertEqual(
            "activate_project selects the process-wide active Unity project. "
            "One process represents one logical client/project scope. "
            "Tool calls execute serially. Normal inspection, dry-run, and confirm "
            "entry points remain unchanged.",
            initialized["instructions"],
        )

        listed = assert_jsonrpc_result(self, listed_response, request_id=2)
        self.assertIn("get_project_status", {tool["name"] for tool in listed["tools"]})

        tool_result = assert_jsonrpc_result(self, tool_response, request_id=3)
        self.assertIs(tool_result["isError"], False)
        self.assertEqual("SESSION_STATUS", tool_result["structuredContent"]["code"])

        for payload in (initialized, listed, tool_result):
            self.assertNotIn("resultType", payload)
            self.assertNotIn("ttlMs", payload)
            self.assertNotIn("cacheScope", payload)

        assert_jsonrpc_error(
            self,
            resources_response,
            request_id=4,
            code=-32601,
            message="Method not found: resources/list",
        )
        modern_error = assert_jsonrpc_error(
            self,
            modern_response,
            request_id=5,
            code=-32600,
            message=None,
        )
        self.assertNotIn("data", modern_error)

        listed_after_modern = assert_jsonrpc_result(
            self,
            listed_after_modern_response,
            request_id=6,
        )
        self.assertIn(
            "get_project_status",
            {tool["name"] for tool in listed_after_modern["tools"]},
        )

    def test_each_legacy_wire_contract_and_graceful_eof(self) -> None:
        for protocol_version in LEGACY_PROTOCOL_VERSIONS:
            with self.subTest(protocol_version=protocol_version):
                self._assert_legacy_wire_contract(protocol_version)

    def test_legacy_unsupported_initialize_version_is_product_error(self) -> None:
        with running_mcp_cli("--transport", "stdio", pipe_stdin=True) as child:
            response = child.request_json_line(
                legacy_initialize_request(version="2025-03-26"),
            )
            child.communicate()
            self.assertEqual(0, child.returncode)

        error = assert_jsonrpc_error(
            self,
            response,
            request_id=1,
            code=-32022,
            message="Unsupported protocol version: 2025-03-26",
        )
        self.assertEqual(
            {
                "supported": ["2026-07-28", "2025-11-25", "2025-06-18"],
                "requested": "2025-03-26",
            },
            error.get("data"),
        )

    def test_modern_wire_contract_and_graceful_eof(self) -> None:
        requests: list[dict[str, object]] = [
            modern_request("server/discover", request_id=1),
            modern_request("tools/list", request_id=2),
            modern_request(
                "tools/call",
                request_id=3,
                params={"name": "get_project_status", "arguments": {}},
            ),
            modern_request("initialize", request_id=4),
            modern_request("resources/list", request_id=5),
            modern_request("tools/list", request_id=6),
            {
                "jsonrpc": "2.0",
                "method": "notifications/initialized",
                "params": {},
            },
        ]

        with running_mcp_cli(
            "--transport",
            "stdio",
            "--port",
            "65535",
            pipe_stdin=True,
        ) as child:
            decoded = [child.request_json_line(request) for request in requests[:6]]
            child.write_json_line(requests[6])
            child.communicate()
            self.assertEqual(0, child.returncode)

            wire_responses = [json.loads(line) for line in child.stdout.splitlines()]
            self.assertEqual(decoded, wire_responses)
            self.assertEqual(6, len(decoded), f"responses={decoded!r}")
            responses = {cast(int, item["id"]): cast(dict[str, Any], item) for item in decoded}
            self.assertEqual({1, 2, 3, 4, 5, 6}, set(responses))

            discovery = assert_jsonrpc_result(self, responses[1], request_id=1)
            self.assertEqual(["2026-07-28"], discovery["supportedVersions"])
            self.assertEqual({"tools": {"listChanged": False}}, discovery["capabilities"])

            listed = assert_jsonrpc_result(self, responses[2], request_id=2)
            self.assertIn("get_project_status", {tool["name"] for tool in listed["tools"]})

            tool_result = assert_jsonrpc_result(self, responses[3], request_id=3)
            self.assertIs(tool_result["isError"], False)
            self.assertEqual("SESSION_STATUS", tool_result["structuredContent"]["code"])

            # Pinned MCP SDK v2.0.0 handles initialize inside its modern stdio
            # loop before product middleware:
            # https://github.com/modelcontextprotocol/python-sdk/blob/v2.0.0/src/mcp/server/runner.py#L701-L755
            sdk_initialize_error = assert_jsonrpc_error(
                self,
                responses[4],
                request_id=4,
                code=-32022,
                message=None,
            )
            self.assertEqual(
                {"supported": ["2026-07-28"]},
                sdk_initialize_error.get("data"),
            )
            assert_jsonrpc_error(
                self,
                responses[5],
                request_id=5,
                code=-32601,
                message="Method not found: resources/list",
            )

            listed_after_legacy_initialize = assert_jsonrpc_result(
                self,
                responses[6],
                request_id=6,
            )
            self.assertIn(
                "get_project_status",
                {tool["name"] for tool in listed_after_legacy_initialize["tools"]},
            )

    def test_read_timeout_reaps_child_and_joins_process_readers(self) -> None:
        with running_mcp_cli("--transport", "stdio", pipe_stdin=True) as child:
            with self.assertRaisesRegex(AssertionError, "timed out waiting for one MCP stdio response"):
                child.read_json_line(timeout=0.0)
            child.stop()
            self.assertIsNotNone(child.returncode)
            self.assertFalse(child.reader_threads_alive)

    def test_port_option_is_documented_bounded_and_stdio_inert(self) -> None:
        with running_mcp_cli("--help") as child:
            help_text, _ = child.communicate()
            self.assertEqual(0, child.returncode)
            self.assertIn("--port PORT", help_text)
            self.assertIn("Streamable HTTP loopback port (default: 8000)", help_text)
            for forbidden in (
                "--host",
                "--oauth",
                "--public-bind",
                "--stateless",
                "--session",
                "--json-response",
                "--sse",
            ):
                self.assertNotIn(forbidden, help_text)

        with running_mcp_cli(
            "--transport",
            "stdio",
            "--port",
            "1",
            pipe_stdin=True,
        ) as child:
            stdout, _ = child.communicate("")
            self.assertEqual(0, child.returncode)
            self.assertEqual("", stdout)

        for invalid_port in ("0", "65536", "not-an-integer"):
            with self.subTest(port=invalid_port):
                with running_mcp_cli(
                    "--transport",
                    "stdio",
                    "--port",
                    invalid_port,
                    pipe_stdin=True,
                ) as child:
                    stdout, _ = child.communicate("")
                    self.assertEqual(2, child.returncode)
                    self.assertEqual("", stdout)


if __name__ == "__main__":
    unittest.main()
