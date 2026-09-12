"""Bounded worktree-local transport for Unity Bridge acceptance."""

from __future__ import annotations

import asyncio
import os
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager, suppress
from importlib.metadata import version
from pathlib import Path
from tempfile import TemporaryFile
from typing import Any, Protocol, cast

from mcp import Client, StdioServerParameters
from mcp.client.stdio import stdio_client

from prefab_sentinel.bridge_constants import BRIDGE_INSTANCE_ID_ENV
from prefab_sentinel.bridge_response import is_bridge_response_envelope
from prefab_sentinel.mcp_server import SERVER_NAME

MCP_PROTOCOL_VERSION = "2026-07-28"
UNITY_ENV_ALLOWLIST = ("UNITYTOOL_BRIDGE_WATCH_DIR",)
_DEFAULT_CALL_TIMEOUT_SEC = 30.0
_MAX_CALL_TIMEOUT_SEC = 300.0


class AcceptanceTransport(Protocol):
    """Operations the acceptance controller performs in its fixed phase order."""

    async def activate(self, project_root: str, scope: str) -> dict[str, Any]: ...

    async def project_status(self) -> dict[str, Any]: ...

    async def deploy(self, target_dir: str) -> dict[str, Any]: ...

    async def recompile(self, timeout_sec: int) -> dict[str, Any]: ...

    async def console_errors(self, since_seconds: float) -> dict[str, Any]: ...

    async def reflect_game_object(self) -> dict[str, Any]: ...

    async def runtime_console_probe(self, scene_path: str) -> dict[str, Any]: ...

    async def run_acceptance(self, run_id: str, timeout_sec: int) -> dict[str, Any]: ...

    async def cleanup_acceptance(self, run_id: str, timeout_sec: int) -> dict[str, Any]: ...

    async def acceptance_status(self, run_id: str) -> dict[str, Any]: ...


    def connection_identity(self) -> dict[str, str | None]: ...


class _McpClient(Protocol):
    protocol_version: str
    server_info: Any

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> Any: ...


_ClientFactory = Callable[[StdioServerParameters], AbstractAsyncContextManager[_McpClient]]
_BridgeAction = Callable[..., dict[str, Any]]


def _transport_error(code: str, message: str) -> dict[str, Any]:
    return {
        "success": False,
        "severity": "error",
        "code": code,
        "message": message,
        "data": {},
        "diagnostics": [],
    }


def _bounded_timeout(timeout_sec: float) -> float:
    return min(max(timeout_sec, 0.001), _MAX_CALL_TIMEOUT_SEC)


class McpAcceptanceTransport:
    """Own one worktree-local MCP client and route private actions to the Bridge."""

    def __init__(
        self,
        *,
        worktree_root: Path,
        watch_dir: str,
        _client_factory: _ClientFactory | None = None,
        _bridge_action: _BridgeAction | None = None,
        _call_timeout_sec: float = _DEFAULT_CALL_TIMEOUT_SEC,
    ) -> None:
        if _call_timeout_sec <= 0.0:
            raise ValueError("_call_timeout_sec must be positive.")
        self._watch_dir = watch_dir
        child_environment = {"UNITYTOOL_BRIDGE_WATCH_DIR": watch_dir}
        instance_id = os.environ.get(BRIDGE_INSTANCE_ID_ENV)
        if instance_id is not None:
            child_environment[BRIDGE_INSTANCE_ID_ENV] = instance_id
        self._parameters = StdioServerParameters(
            command="uv",
            args=[
                "--directory",
                str(worktree_root),
                "run",
                "--extra",
                "mcp",
                "prefab-sentinel-mcp",
            ],
            env=child_environment,
        )
        self._client_factory = _client_factory
        self._bridge_action = _bridge_action
        self._call_timeout_sec = _bounded_timeout(_call_timeout_sec)
        # The transport closes this stderr descriptor after the MCP child exits.
        self._stderr = TemporaryFile(mode="w+t", encoding="utf-8")  # noqa: SIM115
        self._client_context: AbstractAsyncContextManager[_McpClient] | None = None
        self._client: _McpClient | None = None
        self._startup_error: dict[str, Any] | None = None

    async def __aenter__(self) -> McpAcceptanceTransport:
        if self._client_context is not None:
            raise RuntimeError("McpAcceptanceTransport cannot be entered twice.")
        try:
            if self._client_factory is None:
                self._client_context = cast(
                    AbstractAsyncContextManager[_McpClient],
                    Client(
                        stdio_client(self._parameters, errlog=self._stderr),
                        mode="auto",
                    ),
                )
            else:
                self._client_context = self._client_factory(self._parameters)
            self._client = await self._client_context.__aenter__()
        except Exception:
            await self._close_client()
            self._startup_error = _transport_error(
                "ACCEPTANCE_TRANSPORT_UNAVAILABLE",
                "The worktree-local MCP process could not be started.",
            )
            return self

        server_info = self._client.server_info
        if self._client.protocol_version != MCP_PROTOCOL_VERSION:
            self._startup_error = _transport_error(
                "ACCEPTANCE_TRANSPORT_PROTOCOL_MISMATCH",
                "The worktree-local MCP process did not use the required protocol version.",
            )
        elif (
            server_info is None
            or server_info.name != SERVER_NAME
            or server_info.version != version("prefab-sentinel")
        ):
            self._startup_error = _transport_error(
                "ACCEPTANCE_TRANSPORT_IDENTITY_MISMATCH",
                "The worktree-local MCP process identity did not match this checkout.",
            )
        if self._startup_error is not None:
            await self._close_client()
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self._close_client()

    async def activate(self, project_root: str, scope: str) -> dict[str, Any]:
        return await self._call_public(
            "activate_project",
            {"project_root": project_root, "scope": scope},
            timeout_sec=_MAX_CALL_TIMEOUT_SEC,
        )

    async def project_status(self) -> dict[str, Any]:
        return await self._call_public("get_project_status", {})

    async def deploy(self, target_dir: str) -> dict[str, Any]:
        return await self._call_public("deploy_bridge", {"target_dir": target_dir})

    async def recompile(self, timeout_sec: int) -> dict[str, Any]:
        return await self._call_public(
            "editor_recompile",
            {"timeout_sec": timeout_sec},
            timeout_sec=_bounded_timeout(float(timeout_sec) + 5.0),
        )

    async def console_errors(self, since_seconds: float) -> dict[str, Any]:
        return await self._call_public(
            "editor_console",
            {"log_type_filter": "error", "since_seconds": since_seconds},
        )

    async def reflect_game_object(self) -> dict[str, Any]:
        return await self._call_public(
            "editor_reflect",
            {
                "action": "get_type",
                "class_name": "UnityEngine.GameObject",
                "scope": "unity",
            },
        )

    async def runtime_console_probe(self, scene_path: str) -> dict[str, Any]:
        return await self._call_public(
            "validate_runtime",
            {"asset_path": scene_path, "profile": "editor_console_only"},
        )

    async def run_acceptance(self, run_id: str, timeout_sec: int) -> dict[str, Any]:
        return await self._call_bridge(
            "run_integration_tests",
            timeout_sec,
            test_profile="bridge_acceptance",
            run_live_probes=True,
            run_id=run_id,
        )

    async def cleanup_acceptance(self, run_id: str, timeout_sec: int) -> dict[str, Any]:
        return await self._call_bridge(
            "cleanup_integration_tests",
            timeout_sec,
            run_id=run_id,
        )

    async def acceptance_status(self, run_id: str) -> dict[str, Any]:
        return await self._call_bridge(
            "acceptance_status",
            int(_DEFAULT_CALL_TIMEOUT_SEC),
            run_id=run_id,
        )


    def connection_identity(self) -> dict[str, str | None]:
        if self._client is None or self._client.server_info is None:
            return {
                "protocol_revision": None,
                "server_name": None,
                "server_version": None,
            }
        return {
            "protocol_revision": self._client.protocol_version,
            "server_name": self._client.server_info.name,
            "server_version": self._client.server_info.version,
        }

    async def _call_public(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        *,
        timeout_sec: float | None = None,
    ) -> dict[str, Any]:
        if self._startup_error is not None:
            return dict(self._startup_error)
        if self._client is None:
            return _transport_error(
                "ACCEPTANCE_TRANSPORT_UNAVAILABLE",
                "The worktree-local MCP process is not connected.",
            )
        try:
            result = await asyncio.wait_for(
                self._client.call_tool(tool_name, arguments),
                timeout=self._call_timeout_sec if timeout_sec is None else timeout_sec,
            )
        except TimeoutError:
            return _transport_error(
                "ACCEPTANCE_TRANSPORT_TIMEOUT",
                "The worktree-local MCP call exceeded its deadline.",
            )
        except Exception:
            return _transport_error(
                "ACCEPTANCE_TRANSPORT_UNAVAILABLE",
                "The worktree-local MCP process became unavailable.",
            )
        if getattr(result, "is_error", False) is not False:
            return _transport_error(
                "ACCEPTANCE_TRANSPORT_TOOL_ERROR",
                "The MCP tool returned a transport-level error.",
            )
        payload = getattr(result, "structured_content", None)
        if not is_bridge_response_envelope(payload):
            return _transport_error(
                "ACCEPTANCE_TRANSPORT_RESPONSE_INVALID",
                "MCP tool returned an invalid public response envelope.",
            )
        return dict(payload)

    async def _call_bridge(
        self,
        action: str,
        timeout_sec: int,
        **fields: Any,
    ) -> dict[str, Any]:
        if self._startup_error is not None:
            return dict(self._startup_error)
        if self._bridge_action is None:
            from prefab_sentinel.editor_bridge import send_private_acceptance_action

            bridge_action = send_private_acceptance_action
        else:
            bridge_action = self._bridge_action
        bounded_timeout_sec = max(1, int(_bounded_timeout(float(timeout_sec))))
        try:
            response = await asyncio.to_thread(
                bridge_action,
                action=action,
                timeout_sec=bounded_timeout_sec,
                watch_dir=self._watch_dir,
                **fields,
            )
        except Exception:
            return _transport_error(
                "ACCEPTANCE_TRANSPORT_UNAVAILABLE",
                "The private Editor Bridge action became unavailable.",
            )
        if not is_bridge_response_envelope(response):
            return _transport_error(
                "ACCEPTANCE_TRANSPORT_RESPONSE_INVALID",
                "The private Editor Bridge action returned an invalid response envelope.",
            )
        return dict(response)

    async def _close_client(self) -> None:
        context = self._client_context
        self._client_context = None
        self._client = None
        if context is not None:
            with suppress(Exception):
                await context.__aexit__(None, None, None)
        if not self._stderr.closed:
            self._stderr.close()
