from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Final

from mcp.server import MCPServer
from mcp.server.transport_security import TransportSecurityMiddleware, TransportSecuritySettings
from mcp.shared.inbound import (
    ERROR_CODE_HTTP_STATUS,
    MCP_PROTOCOL_VERSION_HEADER,
    InboundLadderRejection,
    classify_inbound_request,
    find_duplicated_routing_header,
)
from mcp_types import (
    HEADER_MISMATCH,
    INVALID_PARAMS,
    INVALID_REQUEST,
    METHOD_NOT_FOUND,
    PARSE_ERROR,
    methods as mcp_methods,
)
from mcp_types.version import HANDSHAKE_PROTOCOL_VERSIONS
from pydantic import ValidationError
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from prefab_sentinel.mcp_protocol import (
    ALLOWED_REQUEST_METHODS,
    MCP_PROTOCOL_VERSION,
    SUPPORTED_PROTOCOL_VERSIONS,
    unsupported_protocol_version,
)

MCP_HTTP_PATH: Final = "/mcp"
MAX_HTTP_REQUEST_BODY_SIZE: Final = 4 * 1024 * 1024


def local_transport_security() -> TransportSecuritySettings:
    return TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=["127.0.0.1:*", "localhost:*", "[::1]:*"],
        allowed_origins=[
            "http://127.0.0.1:*",
            "http://localhost:*",
            "http://[::1]:*",
        ],
    )


@dataclass(frozen=True)
class _GateError:
    status_code: int
    code: int
    message: str
    request_id: int | str | None
    data: Any = None


@dataclass(frozen=True)
class _RequestTooLarge:
    pass


@dataclass(frozen=True)
class _BufferedBody:
    data: bytes
    complete: bool


_REQUEST_TOO_LARGE: Final = _RequestTooLarge()


class MCP20260728HTTPGate:
    def __init__(
        self,
        app: ASGIApp,
        *,
        path: str = MCP_HTTP_PATH,
        max_request_body_size: int = MAX_HTTP_REQUEST_BODY_SIZE,
        security_settings: TransportSecuritySettings,
    ) -> None:
        if max_request_body_size <= 0:
            raise ValueError("max_request_body_size must be a positive number of bytes")
        self._app = app
        self._path = path
        self._max_request_body_size = max_request_body_size
        self._security = TransportSecurityMiddleware(security_settings)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["path"] != self._path:
            await self._app(scope, receive, send)
            return

        request = Request(scope, receive=receive)
        method = request.method
        security_error = await self._security.validate_request(request, is_post=method == "POST")
        if security_error is not None:
            await security_error(scope, receive, send)
            return

        if method != "POST":
            response = Response("Method Not Allowed", status_code=405, headers={"Allow": "POST"})
            await response(scope, receive, send)
            return

        buffered = await self._bounded_body(receive)
        if isinstance(buffered, _RequestTooLarge):
            response = Response("Request body too large", status_code=413)
            await response(scope, receive, send)
            return
        if not buffered.complete:
            return

        decoded = self._decode_json_request(buffered.data)
        if isinstance(decoded, _GateError):
            await self._send_jsonrpc_error(decoded, scope, receive, send)
            return

        envelope_error = self._validate_request_envelope(decoded)
        if envelope_error is not None:
            await self._send_jsonrpc_error(envelope_error, scope, receive, send)
            return

        request_id = decoded.get("id")
        method_name = decoded["method"]
        if "id" not in decoded:
            notification_error = _GateError(
                status_code=400,
                code=INVALID_REQUEST,
                message="HTTP requests must carry a JSON-RPC request id",
                request_id=None,
            )
            await self._send_jsonrpc_error(notification_error, scope, receive, send)
            return

        duplicated = find_duplicated_routing_header(request.headers.items())
        if duplicated is not None:
            duplicate_error = _GateError(
                status_code=ERROR_CODE_HTTP_STATUS[HEADER_MISMATCH],
                code=HEADER_MISMATCH,
                message=f"{duplicated} header appears more than once",
                request_id=request_id,
            )
            await self._send_jsonrpc_error(duplicate_error, scope, receive, send)
            return

        header_version = request.headers.get(MCP_PROTOCOL_VERSION_HEADER)
        exact_modern_header = (
            header_version == MCP_PROTOCOL_VERSION and header_version not in HANDSHAKE_PROTOCOL_VERSIONS
        )
        verdict = classify_inbound_request(
            decoded,
            headers=dict(request.headers),
            supported_modern_versions=SUPPORTED_PROTOCOL_VERSIONS,
        )
        if isinstance(verdict, InboundLadderRejection):
            rejection = _GateError(
                status_code=ERROR_CODE_HTTP_STATUS.get(verdict.code, 400),
                code=verdict.code,
                message=verdict.message,
                request_id=request_id,
                data=verdict.data,
            )
            await self._send_jsonrpc_error(rejection, scope, receive, send)
            return

        if method_name not in ALLOWED_REQUEST_METHODS:
            method_error = _GateError(
                status_code=ERROR_CODE_HTTP_STATUS[METHOD_NOT_FOUND],
                code=METHOD_NOT_FOUND,
                message=f"Method not found: {method_name}",
                request_id=request_id,
            )
            await self._send_jsonrpc_error(method_error, scope, receive, send)
            return

        if not exact_modern_header:
            protocol_error = unsupported_protocol_version(verdict.protocol_version)
            rejection = _GateError(
                status_code=ERROR_CODE_HTTP_STATUS[protocol_error.code],
                code=protocol_error.code,
                message=protocol_error.message,
                request_id=request_id,
                data=protocol_error.data,
            )
            await self._send_jsonrpc_error(rejection, scope, receive, send)
            return

        try:
            mcp_methods.validate_client_request(
                method_name,
                verdict.protocol_version,
                decoded.get("params"),
            )
        except ValidationError:
            params_error = _GateError(
                status_code=ERROR_CODE_HTTP_STATUS[INVALID_PARAMS],
                code=INVALID_PARAMS,
                message="Invalid method parameters",
                request_id=request_id,
            )
            await self._send_jsonrpc_error(params_error, scope, receive, send)
            return

        await self._app(scope, self._replay_body(buffered, receive), send)

    async def _bounded_body(self, receive: Receive) -> _BufferedBody | _RequestTooLarge:
        body = bytearray()
        while True:
            message = await receive()
            if message["type"] != "http.request":
                return _BufferedBody(data=bytes(body), complete=False)
            chunk = message.get("body", b"")
            if len(body) + len(chunk) > self._max_request_body_size:
                return _REQUEST_TOO_LARGE
            body.extend(chunk)
            if not message.get("more_body", False):
                return _BufferedBody(data=bytes(body), complete=True)

    @staticmethod
    def _decode_json_request(body: bytes) -> dict[str, Any] | _GateError:
        try:
            decoded = json.loads(body)
        except (ValueError, RecursionError):
            return _GateError(
                status_code=ERROR_CODE_HTTP_STATUS[PARSE_ERROR],
                code=PARSE_ERROR,
                message="Parse error",
                request_id=None,
            )
        if not isinstance(decoded, dict):
            return _GateError(
                status_code=ERROR_CODE_HTTP_STATUS[INVALID_REQUEST],
                code=INVALID_REQUEST,
                message="Invalid Request",
                request_id=None,
            )
        return decoded

    @staticmethod
    def _validate_request_envelope(decoded: dict[str, Any]) -> _GateError | None:
        method = decoded.get("method")
        request_id = decoded.get("id")
        valid_id = isinstance(request_id, str | int) and not isinstance(request_id, bool)
        if (
            decoded.get("jsonrpc") != "2.0"
            or not isinstance(method, str)
            or not method
            or ("id" in decoded and not valid_id)
        ):
            return _GateError(
                status_code=ERROR_CODE_HTTP_STATUS[INVALID_REQUEST],
                code=INVALID_REQUEST,
                message="Invalid Request",
                request_id=None,
            )
        return None

    @staticmethod
    def _replay_body(body: _BufferedBody, receive: Receive) -> Receive:
        replayed = False

        async def replay() -> Message:
            nonlocal replayed
            if not replayed:
                replayed = True
                return {"type": "http.request", "body": body.data, "more_body": False}
            return await receive()

        return replay

    @staticmethod
    async def _send_jsonrpc_error(
        error: _GateError,
        scope: Scope,
        receive: Receive,
        send: Send,
    ) -> None:
        error_payload: dict[str, Any] = {"code": error.code, "message": error.message}
        if error.data is not None:
            error_payload["data"] = error.data
        response = JSONResponse(
            {"jsonrpc": "2.0", "id": error.request_id, "error": error_payload},
            status_code=error.status_code,
        )
        await response(scope, receive, send)


def build_http_app(server: MCPServer[Any]) -> ASGIApp:
    security = local_transport_security()
    sdk_app = server.streamable_http_app(
        streamable_http_path=MCP_HTTP_PATH,
        json_response=True,
        max_request_body_size=MAX_HTTP_REQUEST_BODY_SIZE,
        transport_security=security,
        host="127.0.0.1",
    )
    return MCP20260728HTTPGate(
        sdk_app,
        path=MCP_HTTP_PATH,
        max_request_body_size=MAX_HTTP_REQUEST_BODY_SIZE,
        security_settings=security,
    )
