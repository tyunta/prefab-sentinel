"""Product-owned MCP protocol contract and process-local tool serialization."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Final

import anyio
from mcp import MCPError
from mcp.server.context import CallNext, HandlerResult, ServerRequestContext
from mcp_types import METHOD_NOT_FOUND, UNSUPPORTED_PROTOCOL_VERSION

MCP_PROTOCOL_VERSION: Final = "2026-07-28"
LEGACY_PROTOCOL_VERSION: Final = "2025-11-25"
MCP_2025_06_18_PROTOCOL_VERSION: Final = "2025-06-18"

# Modern-only consumers: discovery and the HTTP gate.
SUPPORTED_PROTOCOL_VERSIONS: Final = (MCP_PROTOCOL_VERSION,)

SUPPORTED_LEGACY_PROTOCOL_VERSIONS: Final = (
    LEGACY_PROTOCOL_VERSION,
    MCP_2025_06_18_PROTOCOL_VERSION,
)

# Fresh stdio processes may start in either era; modern stays first.
SUPPORTED_STDIO_PROTOCOL_VERSIONS: Final = (
    MCP_PROTOCOL_VERSION,
    *SUPPORTED_LEGACY_PROTOCOL_VERSIONS,
)

ALLOWED_REQUEST_METHODS: Final = frozenset(
    {"server/discover", "tools/list", "tools/call"}
)
LEGACY_ALLOWED_REQUEST_METHODS: Final = frozenset(
    {"initialize", "tools/list", "tools/call"}
)
SERVER_INFO_META_KEY: Final = "io.modelcontextprotocol/serverInfo"


def unsupported_protocol_version(
    requested: str,
    *,
    supported: tuple[str, ...] = SUPPORTED_PROTOCOL_VERSIONS,
) -> MCPError:
    return MCPError(
        code=UNSUPPORTED_PROTOCOL_VERSION,
        message=f"Unsupported protocol version: {requested}",
        data={"supported": list(supported), "requested": requested},
    )


class ProtocolContractMiddleware:
    def __init__(
        self,
        *,
        server_name: str,
        server_version: str,
        instructions: str,
    ) -> None:
        self.server_name = server_name
        self.server_version = server_version
        self.instructions = instructions

    async def __call__(
        self,
        ctx: ServerRequestContext[Any, Any],
        call_next: CallNext,
    ) -> HandlerResult:
        if ctx.request_id is None:
            if ctx.method == "notifications/cancelled":
                return await call_next(ctx)
            if (
                ctx.protocol_version in SUPPORTED_LEGACY_PROTOCOL_VERSIONS
                and ctx.method == "notifications/initialized"
            ):
                return await call_next(ctx)
            return None

        if ctx.protocol_version not in SUPPORTED_STDIO_PROTOCOL_VERSIONS:
            raise unsupported_protocol_version(
                ctx.protocol_version,
                supported=SUPPORTED_STDIO_PROTOCOL_VERSIONS,
            )

        allowed_methods = (
            LEGACY_ALLOWED_REQUEST_METHODS
            if ctx.protocol_version in SUPPORTED_LEGACY_PROTOCOL_VERSIONS
            else ALLOWED_REQUEST_METHODS
        )
        if ctx.method not in allowed_methods:
            raise MCPError(
                code=METHOD_NOT_FOUND,
                message=f"Method not found: {ctx.method}",
            )

        initialize_protocol_version = ctx.protocol_version
        if ctx.method == "initialize":
            requested = (ctx.params or {}).get("protocolVersion")
            if isinstance(requested, str):
                if requested not in SUPPORTED_LEGACY_PROTOCOL_VERSIONS:
                    raise unsupported_protocol_version(
                        requested,
                        supported=SUPPORTED_STDIO_PROTOCOL_VERSIONS,
                    )
                initialize_protocol_version = requested

        result = await call_next(ctx)
        if ctx.method == "server/discover":
            return self._normalize_discovery(result)
        if ctx.method == "initialize":
            return self._normalize_initialize(
                result,
                protocol_version=initialize_protocol_version,
            )
        return result

    def _normalize_discovery(self, result: HandlerResult) -> dict[str, Any]:
        if not isinstance(result, dict):
            raise TypeError("server/discover handler must return a dictionary")

        payload = result.copy()
        existing_meta = payload.get("_meta")
        if existing_meta is None:
            normalized_meta: dict[str, Any] = {}
        elif isinstance(existing_meta, Mapping):
            normalized_meta = dict(existing_meta)
        else:
            raise TypeError("server/discover _meta must be a mapping")

        payload["supportedVersions"] = [MCP_PROTOCOL_VERSION]
        payload["capabilities"] = {"tools": {"listChanged": False}}
        payload["instructions"] = self.instructions
        payload["_meta"] = {
            **normalized_meta,
            SERVER_INFO_META_KEY: {
                "name": self.server_name,
                "version": self.server_version,
            },
        }
        return payload

    def _normalize_initialize(
        self,
        result: HandlerResult,
        *,
        protocol_version: str,
    ) -> dict[str, Any]:
        if not isinstance(result, dict):
            raise TypeError("initialize handler must return a dictionary")

        payload = result.copy()
        payload["protocolVersion"] = protocol_version
        payload["capabilities"] = {"tools": {"listChanged": False}}
        payload["serverInfo"] = {
            "name": self.server_name,
            "version": self.server_version,
        }
        payload["instructions"] = self.instructions
        return payload


class SerializeToolCallsMiddleware:
    def __init__(self) -> None:
        self._tool_lock = anyio.Lock()

    async def __call__(
        self,
        ctx: ServerRequestContext[Any, Any],
        call_next: CallNext,
    ) -> HandlerResult:
        if ctx.request_id is None or ctx.method != "tools/call":
            return await call_next(ctx)
        async with self._tool_lock:
            return await call_next(ctx)
