from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from typing import Any, TypeVar, cast

from mcp.server import MCPServer
from mcp_types import CallToolResult, InputRequiredResult

_T = TypeVar("_T")


def run(coro: Coroutine[Any, Any, _T]) -> _T:
    return asyncio.run(coro)


def call_tool_result(
    server: MCPServer[Any],
    name: str,
    arguments: dict[str, Any],
) -> CallToolResult:
    result = run(server.call_tool(name, arguments))
    if isinstance(result, InputRequiredResult):
        raise AssertionError(f"{name} unexpectedly returned input_required")
    return result


def structured_payload(result: CallToolResult) -> dict[str, Any]:
    if result.is_error is not False:
        raise AssertionError(
            f"expected CallToolResult with is_error=False, observed {result.is_error!r}"
        )
    if not isinstance(result.structured_content, dict):
        raise AssertionError(f"expected structuredContent object, observed {result!r}")
    return cast(dict[str, Any], result.structured_content)
