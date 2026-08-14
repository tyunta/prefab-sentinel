"""Subprocess contract tests for the real MCP stdio CLI transport."""

from __future__ import annotations

import json
import unittest
from typing import Any, cast

from tests._mcp_wire_support import (
    assert_jsonrpc_error,
    assert_jsonrpc_result,
    modern_request,
    running_mcp_cli,
)


class TestMCPStdioTransport(unittest.TestCase):
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
            decoded = [child.request_json_line(request) for request in requests[:5]]
            child.write_json_line(requests[5])
            child.communicate()
            self.assertEqual(0, child.returncode)

            wire_responses = [json.loads(line) for line in child.stdout.splitlines()]
            self.assertEqual(decoded, wire_responses)
            self.assertEqual(5, len(decoded), f"responses={decoded!r}")
            responses = {cast(int, item["id"]): cast(dict[str, Any], item) for item in decoded}
            self.assertEqual({1, 2, 3, 4, 5}, set(responses))

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
                message=(
                    "connection is serving the 2026-07-28 protocol; "
                    "the initialize handshake is not accepted"
                ),
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
