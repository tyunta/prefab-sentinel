"""Subprocess contract tests for the real MCP Streamable HTTP CLI transport."""

from __future__ import annotations

import json
import threading
import unittest
from collections.abc import Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from tests._mcp_wire_support import (
    HTTPResult,
    assert_jsonrpc_error,
    assert_jsonrpc_result,
    http_request,
    modern_headers,
    modern_request,
    reserve_loopback_port,
    running_mcp_cli,
    wait_for_http_ready,
)


class _UnrelatedJSONRPCHandler(BaseHTTPRequestHandler):
    """Return a valid-looking but unrelated JSON-RPC response."""

    def do_GET(self) -> None:
        self._respond()

    def do_POST(self) -> None:
        content_length = int(self.headers.get("content-length", "0"))
        self.rfile.read(content_length)
        self._respond()

    def _respond(self) -> None:
        body = json.dumps(
            {
                "jsonrpc": "2.0",
                "id": "unrelated-listener",
                "result": {"supportedVersions": ["2026-07-28"]},
            }
        ).encode("utf-8")
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        del format, args


@contextmanager
def _unrelated_http_listener() -> Iterator[int]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _UnrelatedJSONRPCHandler)
    server.daemon_threads = True
    thread = threading.Thread(
        target=server.serve_forever,
        kwargs={"poll_interval": 0.01},
    )
    thread.start()
    try:
        yield server.server_port
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=1.0)
    if thread.is_alive():
        raise AssertionError("unrelated HTTP listener did not stop")


def _assert_no_session_headers(test: unittest.TestCase, response: HTTPResult) -> None:
    test.assertNotIn("mcp-session-id", response.headers)
    test.assertNotIn("last-event-id", response.headers)


def _assert_jsonrpc_error(
    test: unittest.TestCase,
    response: HTTPResult,
    *,
    code: int,
    message: str,
    request_id: int | str,
    status: int = 400,
) -> None:
    test.assertEqual(status, response.status)
    assert_jsonrpc_error(
        test,
        response.json_object(),
        request_id=request_id,
        code=code,
        message=message,
    )
    _assert_no_session_headers(test, response)


class TestMCPHTTPTransport(unittest.TestCase):
    def test_readiness_rejects_an_unrelated_http_listener(self) -> None:
        with _unrelated_http_listener() as port:
            with running_mcp_cli("--transport", "stdio", pipe_stdin=True) as child:
                with self.assertRaisesRegex(AssertionError, "did not become ready before timeout"):
                    wait_for_http_ready(child, port, timeout=0.1)

    def test_modern_product_methods_are_served_without_sessions(self) -> None:
        port = reserve_loopback_port()
        with running_mcp_cli(
            "--transport",
            "streamable-http",
            "--port",
            str(port),
        ) as child:
            wait_for_http_ready(child, port)
            cases: tuple[tuple[str, dict[str, object], str | None], ...] = (
                ("server/discover", {}, None),
                ("tools/list", {}, None),
                (
                    "tools/call",
                    {"name": "get_project_status", "arguments": {}},
                    "get_project_status",
                ),
            )
            responses: dict[str, dict[str, Any]] = {}
            for request_id, (method, params, name) in enumerate(cases, start=1):
                response = http_request(
                    port,
                    "POST",
                    "/mcp",
                    body=modern_request(method, request_id=request_id, params=params),
                    headers=modern_headers(method, name=name),
                )
                self.assertEqual(200, response.status)
                result = assert_jsonrpc_result(
                    self,
                    response.json_object(),
                    request_id=request_id,
                )
                _assert_no_session_headers(self, response)
                responses[method] = result

            self.assertEqual(
                ["2026-07-28"],
                responses["server/discover"]["supportedVersions"],
            )
            self.assertIn(
                "get_project_status",
                {tool["name"] for tool in responses["tools/list"]["tools"]},
            )
            self.assertIs(responses["tools/call"]["isError"], False)
            self.assertEqual(
                "SESSION_STATUS",
                responses["tools/call"]["structuredContent"]["code"],
            )

    def test_initialize_and_routing_mismatches_are_product_errors(self) -> None:
        port = reserve_loopback_port()
        with running_mcp_cli(
            "--transport",
            "streamable-http",
            "--port",
            str(port),
        ) as child:
            wait_for_http_ready(child, port)
            modern_initialize = {
                "jsonrpc": "2.0",
                "id": 500,
                "method": "initialize",
                "params": {
                    "_meta": {
                        "io.modelcontextprotocol/protocolVersion": "2026-07-28",
                        "io.modelcontextprotocol/clientInfo": {
                            "name": "conformance-client",
                            "version": "1.0.0",
                        },
                        "io.modelcontextprotocol/clientCapabilities": {},
                    },
                },
            }
            initialize = http_request(
                port,
                "POST",
                "/mcp",
                body=modern_initialize,
                headers=modern_headers("initialize"),
            )
            _assert_jsonrpc_error(
                self,
                initialize,
                status=404,
                code=-32601,
                message="Method not found: initialize",
                request_id=500,
            )

            version_mismatch = http_request(
                port,
                "POST",
                "/mcp",
                body=modern_request("tools/list", request_id=2, version="2099-01-01"),
                headers=modern_headers("tools/list", version="2099-01-01"),
            )
            _assert_jsonrpc_error(
                self,
                version_mismatch,
                code=-32022,
                message="Unsupported protocol version",
                request_id=2,
            )

            method_mismatch = http_request(
                port,
                "POST",
                "/mcp",
                body=modern_request("tools/list", request_id=3),
                headers=modern_headers("server/discover"),
            )
            _assert_jsonrpc_error(
                self,
                method_mismatch,
                code=-32020,
                message="mcp-method header does not match the request body's method",
                request_id=3,
            )

            name_mismatch = http_request(
                port,
                "POST",
                "/mcp",
                body=modern_request(
                    "tools/call",
                    request_id=4,
                    params={"name": "get_project_status", "arguments": {}},
                ),
                headers=modern_headers("tools/call", name="different-tool"),
            )
            _assert_jsonrpc_error(
                self,
                name_mismatch,
                code=-32020,
                message="mcp-name header does not match the request body's 'name' parameter",
                request_id=4,
            )

    def test_security_and_route_mapping_are_enforced_without_sessions(self) -> None:
        port = reserve_loopback_port()
        with running_mcp_cli(
            "--transport",
            "streamable-http",
            "--port",
            str(port),
        ) as child:
            wait_for_http_ready(child, port)

            invalid_host = http_request(
                port,
                "GET",
                "/mcp",
                headers={"host": "evil.example"},
            )
            self.assertEqual(421, invalid_host.status)

            invalid_origin = http_request(
                port,
                "POST",
                "/mcp",
                body=modern_request("tools/list", request_id=1),
                headers=modern_headers(
                    "tools/list",
                    extra={"origin": "https://evil.example"},
                ),
            )
            self.assertEqual(403, invalid_origin.status)

            missing_route = http_request(port, "GET", "/missing")
            self.assertEqual(404, missing_route.status)

            wrong_method = http_request(port, "GET", "/mcp")
            self.assertEqual(405, wrong_method.status)
            self.assertEqual("POST", wrong_method.headers["allow"])

            for response in (invalid_host, invalid_origin, missing_route, wrong_method):
                _assert_no_session_headers(self, response)


if __name__ == "__main__":
    unittest.main()
