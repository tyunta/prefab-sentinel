from __future__ import annotations

import asyncio
import json
import unittest
from typing import Any

from httpx import Response
from mcp.server import MCPServer
from starlette.testclient import TestClient
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from prefab_sentinel.mcp_http import MCP20260728HTTPGate, build_http_app, local_transport_security
from prefab_sentinel.mcp_protocol import MCP_PROTOCOL_VERSION, ProtocolContractMiddleware

_EXPECTED_BODY_LIMIT = 4 * 1024 * 1024
_LEGACY_VERSION = "2025-11-25"
_UNKNOWN_VERSION = "2099-01-01"


def modern_meta(version: str = "2026-07-28") -> dict[str, object]:
    return {
        "io.modelcontextprotocol/protocolVersion": version,
        "io.modelcontextprotocol/clientInfo": {"name": "tests", "version": "1"},
        "io.modelcontextprotocol/clientCapabilities": {},
    }


def _request(
    method: str,
    *,
    request_id: int | str | None = 1,
    version: str = MCP_PROTOCOL_VERSION,
    params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "jsonrpc": "2.0",
        "method": method,
        "params": {"_meta": modern_meta(version), **(params or {})},
    }
    if request_id is not None:
        payload["id"] = request_id
    return payload


def _headers(
    method: str,
    *,
    version: str = MCP_PROTOCOL_VERSION,
    name: str | None = None,
    extra: dict[str, str] | None = None,
) -> dict[str, str]:
    headers = {
        "content-type": "application/json",
        "accept": "application/json, text/event-stream",
        "mcp-protocol-version": version,
        "mcp-method": method,
    }
    if name is not None:
        headers["mcp-name"] = name
    if extra is not None:
        headers.update(extra)
    return headers


def _client(app: ASGIApp) -> TestClient:
    return TestClient(app, base_url="http://localhost:8000")


def _assert_jsonrpc_error(
    testcase: unittest.TestCase,
    response: Response,
    *,
    status: int,
    code: int,
    request_id: int | str | None,
    data: Any = None,
) -> None:
    testcase.assertEqual(status, response.status_code)
    payload = response.json()
    testcase.assertEqual("2.0", payload["jsonrpc"])
    testcase.assertIn("id", payload)
    testcase.assertEqual(request_id, payload["id"])
    testcase.assertEqual(code, payload["error"]["code"])
    testcase.assertEqual(data, payload["error"].get("data"))
    testcase.assertNotIn("mcp-session-id", response.headers)


class _SpyASGI:
    def __init__(self) -> None:
        self.http_scopes: list[Scope] = []
        self.bodies: list[bytes] = []
        self.lifespan_started = False

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "lifespan":
            while True:
                message = await receive()
                if message["type"] == "lifespan.startup":
                    self.lifespan_started = True
                    await send({"type": "lifespan.startup.complete"})
                elif message["type"] == "lifespan.shutdown":
                    await send({"type": "lifespan.shutdown.complete"})
                    return

        self.http_scopes.append(scope)
        chunks: list[bytes] = []
        while True:
            message = await receive()
            if message["type"] != "http.request":
                break
            chunks.append(message.get("body", b""))
            if not message.get("more_body", False):
                break
        body = b"".join(chunks)
        self.bodies.append(body)
        request_id = json.loads(body).get("id") if body else None
        response_body = json.dumps(
            {"jsonrpc": "2.0", "id": request_id, "result": {"resultType": "complete"}},
            separators=(",", ":"),
        ).encode()
        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [(b"content-type", b"application/json")],
            }
        )
        await send({"type": "http.response.body", "body": response_body})


def _spy_gate(spy: _SpyASGI) -> MCP20260728HTTPGate:
    return MCP20260728HTTPGate(spy, security_settings=local_transport_security())


def _product_app() -> ASGIApp:
    server = MCPServer(
        "http-test",
        version="1",
        instructions="HTTP test server",
        middleware=[
            ProtocolContractMiddleware(
                server_name="http-test",
                server_version="1",
                instructions="HTTP test server",
            )
        ],
    )

    @server.tool()
    def echo(value: str) -> str:
        return value

    return build_http_app(server)


def _body_of_size(size: int) -> bytes:
    payload = _request("tools/list")
    payload["padding"] = ""
    empty = json.dumps(payload, separators=(",", ":")).encode()
    padding_size = size - len(empty)
    if padding_size < 0:
        raise ValueError("requested body size is smaller than the JSON envelope")
    payload["padding"] = "x" * padding_size
    body = json.dumps(payload, separators=(",", ":")).encode()
    if len(body) != size:
        raise AssertionError("test body construction drifted from the requested size")
    return body


class TestMCPHTTPModernBoundary(unittest.TestCase):
    def test_exact_endpoint_rejects_every_non_post_method_after_security(self) -> None:
        spy = _SpyASGI()
        with _client(_spy_gate(spy)) as client:
            for method in ("GET", "DELETE", "PUT", "PATCH"):
                with self.subTest(method=method):
                    response = client.request(method, "/mcp")
                    self.assertEqual(405, response.status_code)
                    self.assertEqual("POST", response.headers["allow"])
                    self.assertNotIn("mcp-session-id", response.headers)
        self.assertEqual([], spy.http_scopes)

    def test_sdk_app_lifespan_serves_the_three_product_methods(self) -> None:
        requests: tuple[tuple[str, dict[str, Any], str | None], ...] = (
            ("server/discover", {}, None),
            ("tools/list", {}, None),
            ("tools/call", {"name": "echo", "arguments": {"value": "ok"}}, "echo"),
        )
        with _client(_product_app()) as client:
            for index, (method, params, name) in enumerate(requests, start=1):
                with self.subTest(method=method):
                    response = client.post(
                        "/mcp",
                        json=_request(method, request_id=index, params=params),
                        headers=_headers(method, name=name),
                    )
                    self.assertEqual(200, response.status_code)
                    self.assertEqual(index, response.json()["id"])
                    self.assertIn("result", response.json())
                    self.assertNotIn("mcp-session-id", response.headers)

    def test_legacy_transport_headers_are_ignored_and_never_echoed(self) -> None:
        with _client(_product_app()) as client:
            response = client.post(
                "/mcp",
                json=_request("tools/list"),
                headers=_headers(
                    "tools/list",
                    extra={"mcp-session-id": "legacy-session", "last-event-id": "legacy-event"},
                ),
            )
        self.assertEqual(200, response.status_code)
        self.assertNotIn("mcp-session-id", response.headers)
        self.assertNotIn("last-event-id", response.headers)

    def test_only_the_exact_mcp_path_is_gated(self) -> None:
        spy = _SpyASGI()
        with _client(_spy_gate(spy)) as client:
            slash_response = client.get("/mcp/")
            other_response = client.get("/other")
        self.assertEqual((200, 200), (slash_response.status_code, other_response.status_code))
        self.assertTrue(spy.lifespan_started)
        self.assertEqual(["/mcp/", "/other"], [scope["path"] for scope in spy.http_scopes])


class TestMCPHTTPGateErrors(unittest.TestCase):
    def test_parse_error_and_invalid_request_shapes_are_jsonrpc_errors(self) -> None:
        spy = _SpyASGI()
        with _client(_spy_gate(spy)) as client:
            parse_response = client.post(
                "/mcp",
                content=b'{"jsonrpc":',
                headers=_headers("tools/list"),
            )
            _assert_jsonrpc_error(self, parse_response, status=400, code=-32700, request_id=None)

            invalid_values: tuple[Any, ...] = (
                [],
                None,
                "request",
                {"jsonrpc": "2.0", "id": 7, "params": {}},
            )
            for value in invalid_values:
                with self.subTest(value=value):
                    response = client.post(
                        "/mcp",
                        content=json.dumps(value).encode(),
                        headers=_headers("tools/list"),
                    )
                    _assert_jsonrpc_error(self, response, status=400, code=-32600, request_id=None)
        self.assertEqual([], spy.http_scopes)

    def test_value_error_and_recursion_error_json_boundaries_are_parse_errors(self) -> None:
        spy = _SpyASGI()
        oversized_integer = b'{"jsonrpc":"2.0","id":' + (b"9" * 5000) + b',"method":"tools/list","params":{"_meta":{}}}'
        deeply_nested = (b"[" * 100_000) + b"0" + (b"]" * 100_000)

        with TestClient(
            _spy_gate(spy),
            base_url="http://localhost:8000",
            raise_server_exceptions=False,
        ) as client:
            for body in (oversized_integer, deeply_nested):
                with self.subTest(kind="integer" if body is oversized_integer else "nesting"):
                    response = client.post("/mcp", content=body, headers=_headers("tools/list"))
                    _assert_jsonrpc_error(self, response, status=400, code=-32700, request_id=None)
        self.assertEqual([], spy.http_scopes)

    def test_modern_initialize_is_removed_method_without_delegation(self) -> None:
        spy = _SpyASGI()
        requests: tuple[dict[str, Any], ...] = (
            {
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
            },
            {
                "jsonrpc": "2.0",
                "id": 501,
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
                    "protocolVersion": _LEGACY_VERSION,
                },
            },
        )

        with _client(_spy_gate(spy)) as client:
            for body in requests:
                with self.subTest(request_id=body["id"]):
                    response = client.post(
                        "/mcp",
                        json=body,
                        headers=_headers("initialize"),
                    )
                    _assert_jsonrpc_error(
                        self,
                        response,
                        status=404,
                        code=-32601,
                        request_id=body["id"],
                    )
        self.assertEqual([], spy.http_scopes)


    def test_initialize_version_errors_precede_removed_method_resolution(self) -> None:
        spy = _SpyASGI()
        cases = (
            (
                _request(
                    "initialize",
                    version=_UNKNOWN_VERSION,
                    params={"protocolVersion": _LEGACY_VERSION},
                ),
                _headers("initialize", version=_UNKNOWN_VERSION),
                -32022,
                {
                    "supported": ["2026-07-28"],
                    "requested": _UNKNOWN_VERSION,
                },
            ),
            (
                _request("initialize", version=_UNKNOWN_VERSION),
                _headers("initialize"),
                -32020,
                None,
            ),
        )

        with _client(_spy_gate(spy)) as client:
            for body, headers, code, data in cases:
                with self.subTest(code=code):
                    response = client.post("/mcp", json=body, headers=headers)
                    _assert_jsonrpc_error(
                        self,
                        response,
                        status=400,
                        code=code,
                        request_id=1,
                        data=data,
                    )
        self.assertEqual([], spy.http_scopes)

    def test_initialize_requires_namespaced_metadata_before_method_resolution(self) -> None:
        spy = _SpyASGI()
        malformed_params: tuple[dict[str, Any], ...] = (
            {},
            {"_meta": []},
            {
                "_meta": {
                    "io.modelcontextprotocol/clientCapabilities": {},
                },
            },
            {
                "_meta": {
                    "io.modelcontextprotocol/protocolVersion": MCP_PROTOCOL_VERSION,
                },
            },
        )

        with _client(_spy_gate(spy)) as client:
            for params in malformed_params:
                with self.subTest(params=params):
                    body = {
                        "jsonrpc": "2.0",
                        "id": 502,
                        "method": "initialize",
                        "params": params,
                    }
                    response = client.post(
                        "/mcp",
                        json=body,
                        headers=_headers("initialize"),
                    )
                    _assert_jsonrpc_error(
                        self,
                        response,
                        status=400,
                        code=-32602,
                        request_id=502,
                    )
        self.assertEqual([], spy.http_scopes)

    def test_classifier_rejections_pin_status_code_and_data(self) -> None:
        spy = _SpyASGI()
        cases = (
            (
                _request("tools/list", version=_UNKNOWN_VERSION),
                _headers("tools/list", version=_UNKNOWN_VERSION),
                -32022,
                {"supported": ["2026-07-28"], "requested": _UNKNOWN_VERSION},
            ),
            (
                _request("tools/list", version=_LEGACY_VERSION),
                _headers("tools/list", version=_LEGACY_VERSION),
                -32022,
                {"supported": ["2026-07-28"], "requested": _LEGACY_VERSION},
            ),
            (_request("tools/list", version=_UNKNOWN_VERSION), _headers("tools/list"), -32020, None),
            (_request("tools/list"), {"content-type": "application/json", "mcp-method": "tools/list"}, -32020, None),
            (
                {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
                {"content-type": "application/json"},
                -32602,
                None,
            ),
        )

        with _client(_spy_gate(spy)) as client:
            for body, headers, code, data in cases:
                with self.subTest(code=code, body=body, headers=headers):
                    response = client.post("/mcp", json=body, headers=headers)
                    _assert_jsonrpc_error(self, response, status=400, code=code, request_id=1, data=data)
        self.assertEqual([], spy.http_scopes)

    def test_method_and_name_headers_must_match_the_body(self) -> None:
        spy = _SpyASGI()
        body = _request("tools/call", params={"name": "echo", "arguments": {}})
        cases = (
            {"mcp-protocol-version": MCP_PROTOCOL_VERSION},
            _headers("tools/list"),
            _headers("tools/call"),
            _headers("tools/call", name="different"),
        )

        with _client(_spy_gate(spy)) as client:
            for headers in cases:
                with self.subTest(headers=headers):
                    normalized = {"content-type": "application/json", **headers}
                    response = client.post("/mcp", json=body, headers=normalized)
                    _assert_jsonrpc_error(self, response, status=400, code=-32020, request_id=1)
        self.assertEqual([], spy.http_scopes)

    def test_schema_invalid_allowed_calls_are_rejected_before_delegation(self) -> None:
        spy = _SpyASGI()
        cases = (
            (
                _request("tools/list", params={"cursor": 17}),
                _headers("tools/list"),
            ),
            (
                _request("tools/call", params={"name": "echo", "arguments": "not-an-object"}),
                _headers("tools/call", name="echo"),
            ),
        )

        with _client(_spy_gate(spy)) as client:
            for body, headers in cases:
                with self.subTest(method=body["method"]):
                    response = client.post("/mcp", json=body, headers=headers)
                    _assert_jsonrpc_error(self, response, status=400, code=-32602, request_id=1)
        self.assertEqual([], spy.http_scopes)

    def test_every_sdk_default_non_tools_request_is_product_method_not_found(self) -> None:
        rejected_methods = (
            "resources/list",
            "resources/read",
            "resources/templates/list",
            "prompts/get",
            "prompts/list",
            "subscriptions/listen",
            "ping",
            "completion/complete",
        )
        spy = _SpyASGI()
        with _client(_spy_gate(spy)) as client:
            for method in rejected_methods:
                with self.subTest(method=method):
                    response = client.post(
                        "/mcp",
                        json=_request(method),
                        headers=_headers(method),
                    )
                    _assert_jsonrpc_error(self, response, status=404, code=-32601, request_id=1)
        self.assertEqual([], spy.http_scopes)

    def test_http_notifications_are_rejected_without_delegation(self) -> None:
        spy = _SpyASGI()
        with _client(_spy_gate(spy)) as client:
            for method in ("notifications/cancelled", "notifications/initialized"):
                with self.subTest(method=method):
                    response = client.post(
                        "/mcp",
                        json=_request(method, request_id=None),
                        headers=_headers(method),
                    )
                    _assert_jsonrpc_error(self, response, status=400, code=-32600, request_id=None)
        self.assertEqual([], spy.http_scopes)


class TestMCPHTTPSecurityAndLimits(unittest.TestCase):
    def test_security_prevalidation_precedes_gate_owned_responses(self) -> None:
        spy = _SpyASGI()
        legacy = {
            "jsonrpc": "2.0",
            "id": 4,
            "method": "initialize",
            "params": {"protocolVersion": _LEGACY_VERSION},
        }
        with _client(_spy_gate(spy)) as client:
            invalid_host_method = client.get("/mcp", headers={"host": "evil.example"})
            self.assertEqual(421, invalid_host_method.status_code)

            invalid_content_type = client.post(
                "/mcp",
                content=json.dumps(legacy).encode(),
                headers={"mcp-protocol-version": _LEGACY_VERSION, "mcp-method": "initialize"},
            )
            self.assertEqual(400, invalid_content_type.status_code)
            self.assertEqual("Invalid Content-Type header", invalid_content_type.text)

            legacy_invalid_host = client.post(
                "/mcp",
                json=legacy,
                headers={**_headers("initialize", version=_LEGACY_VERSION), "host": "evil.example"},
            )
            self.assertEqual(421, legacy_invalid_host.status_code)

            legacy_invalid_origin = client.post(
                "/mcp",
                json=legacy,
                headers={**_headers("initialize", version=_LEGACY_VERSION), "origin": "https://evil.example"},
            )
            self.assertEqual(403, legacy_invalid_origin.status_code)

            oversized_invalid_origin = client.post(
                "/mcp",
                content=b"x" * (_EXPECTED_BODY_LIMIT + 1),
                headers={**_headers("tools/list"), "origin": "https://evil.example"},
            )
            self.assertEqual(403, oversized_invalid_origin.status_code)
        self.assertEqual([], spy.http_scopes)

    def test_default_four_mib_limit_admits_cap_minus_one_and_cap_but_rejects_cap_plus_one(self) -> None:
        spy = _SpyASGI()
        gate = _spy_gate(spy)
        admitted = (_EXPECTED_BODY_LIMIT - 1, _EXPECTED_BODY_LIMIT)

        with _client(gate) as client:
            expected_bodies = []
            for size in admitted:
                with self.subTest(size=size):
                    body = _body_of_size(size)
                    expected_bodies.append(body)
                    response = client.post("/mcp", content=body, headers=_headers("tools/list"))
                    self.assertEqual(200, response.status_code)
                    self.assertNotIn("mcp-session-id", response.headers)

            oversized = client.post(
                "/mcp",
                content=_body_of_size(_EXPECTED_BODY_LIMIT + 1),
                headers=_headers("tools/list"),
            )
            self.assertEqual(413, oversized.status_code)
            self.assertNotIn("mcp-session-id", oversized.headers)

        self.assertEqual(expected_bodies, spy.bodies)


class TestMCPHTTPDirectASGIBoundary(unittest.TestCase):
    @staticmethod
    def _scope(headers: list[tuple[bytes, bytes]]) -> Scope:
        return {
            "type": "http",
            "asgi": {"version": "3.0", "spec_version": "2.3"},
            "http_version": "1.1",
            "method": "POST",
            "scheme": "http",
            "path": "/mcp",
            "raw_path": b"/mcp",
            "query_string": b"",
            "root_path": "",
            "headers": headers,
            "client": ("127.0.0.1", 50000),
            "server": ("localhost", 8000),
        }

    def test_incomplete_body_followed_by_disconnect_never_reaches_downstream(self) -> None:
        spy = _SpyASGI()
        gate = _spy_gate(spy)
        body = json.dumps(_request("tools/list"), separators=(",", ":")).encode()
        incoming: list[Message] = [
            {"type": "http.request", "body": body, "more_body": True},
            {"type": "http.disconnect"},
        ]
        sent: list[Message] = []
        headers = [(key.encode(), value.encode()) for key, value in _headers("tools/list").items()]
        headers.append((b"host", b"localhost:8000"))

        async def receive() -> Message:
            return incoming.pop(0)

        async def send(message: Message) -> None:
            sent.append(message)

        asyncio.run(gate(self._scope(headers), receive, send))

        self.assertEqual([], spy.http_scopes)
        self.assertEqual([], spy.bodies)

    def test_duplicate_routing_header_is_rejected_before_dict_folding(self) -> None:
        spy = _SpyASGI()
        gate = _spy_gate(spy)
        body = json.dumps(_request("tools/list"), separators=(",", ":")).encode()
        incoming: list[Message] = [{"type": "http.request", "body": body, "more_body": False}]
        sent: list[Message] = []
        headers = [(key.encode(), value.encode()) for key, value in _headers("tools/list").items()]
        headers.extend(
            (
                (b"host", b"localhost:8000"),
                (b"mcp-method", b"tools/list"),
            )
        )

        async def receive() -> Message:
            return incoming.pop(0)

        async def send(message: Message) -> None:
            sent.append(message)

        asyncio.run(gate(self._scope(headers), receive, send))

        self.assertEqual(400, sent[0]["status"])
        response_body = b"".join(message.get("body", b"") for message in sent[1:])
        payload = json.loads(response_body)
        self.assertEqual("2.0", payload["jsonrpc"])
        self.assertIn("id", payload)
        self.assertEqual(1, payload["id"])
        self.assertEqual(-32020, payload["error"]["code"])
        self.assertEqual([], spy.http_scopes)
        self.assertEqual([], spy.bodies)


if __name__ == "__main__":
    unittest.main()
