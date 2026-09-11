"""Regression tests for the bounded local Unity acceptance transport."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from importlib.metadata import version
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from mcp import StdioServerParameters

from prefab_sentinel.unity_acceptance.transport import McpAcceptanceTransport


@dataclass
class _Result:
    structured_content: object
    is_error: bool = False


@dataclass
class _FakeClient:
    protocol_version: str = "2026-07-28"
    server_info: Any = field(
        default_factory=lambda: SimpleNamespace(
            name="prefab-sentinel",
            version=version("prefab-sentinel"),
        )
    )
    responses: dict[str, _Result] = field(default_factory=dict)
    calls: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    entered: bool = False
    exited: bool = False
    call: Callable[[str, dict[str, Any]], Awaitable[_Result]] | None = None

    async def __aenter__(self) -> _FakeClient:
        self.entered = True
        return self

    async def __aexit__(self, *_: object) -> None:
        self.exited = True

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> _Result:
        self.calls.append((name, arguments))
        if self.call is not None:
            return await self.call(name, arguments)
        return self.responses.get(name, _Result(_envelope()))


def _envelope(**overrides: Any) -> dict[str, Any]:
    return {
        "success": True,
        "severity": "info",
        "code": "OK",
        "message": "ok",
        "data": {},
        "diagnostics": [],
        **overrides,
    }


def _transport(
    client: _FakeClient,
    bridge_calls: list[tuple[str, dict[str, Any]]],
) -> McpAcceptanceTransport:
    def bridge_action(*, action: str, **kwargs: Any) -> dict[str, Any]:
        bridge_calls.append((action, kwargs))
        return _envelope()

    return McpAcceptanceTransport(
        worktree_root=Path("/workspace/current-worktree"),
        watch_dir="D:/Unity/bridge-watch",
        _client_factory=lambda _: client,
        _bridge_action=bridge_action,
        _call_timeout_sec=0.01,
    )


def test_public_operations_use_the_existing_mcp_tool_contracts() -> None:
    """A wrong public tool name or argument would bypass the established MCP surface."""
    client = _FakeClient()
    bridge_calls: list[tuple[str, dict[str, Any]]] = []

    async def scenario() -> None:
        async with _transport(client, bridge_calls) as transport:
            assert (await transport.activate("/project", "Assets/Scope"))["success"] is True
            assert (await transport.project_status())["success"] is True
            assert (await transport.deploy("/project/Assets/Editor/PrefabSentinel"))["success"] is True
            assert (await transport.recompile(17))["success"] is True
            assert (await transport.console_errors(12.5))["success"] is True
            assert (await transport.reflect_game_object())["success"] is True
            assert (await transport.runtime_console_probe("Assets/Smoke.unity"))["success"] is True

    asyncio.run(scenario())

    assert client.calls == [
        ("activate_project", {"project_root": "/project", "scope": "Assets/Scope"}),
        ("get_project_status", {}),
        ("deploy_bridge", {"target_dir": "/project/Assets/Editor/PrefabSentinel"}),
        ("editor_recompile", {"timeout_sec": 17}),
        (
            "editor_console",
            {"log_type_filter": "error", "since_seconds": 12.5},
        ),
        (
            "editor_reflect",
            {
                "action": "get_type",
                "class_name": "UnityEngine.GameObject",
                "scope": "unity",
            },
        ),
        (
            "validate_runtime",
            {"asset_path": "Assets/Smoke.unity", "profile": "editor_console_only"},
        ),
    ]
    assert bridge_calls == []


def test_activation_has_a_separate_deadline_for_project_cache_warmup() -> None:
    """Removing the activation override would cancel a valid full-project cache warmup."""
    async def delayed_call(name: str, _: dict[str, Any]) -> _Result:
        if name == "activate_project":
            await asyncio.sleep(0.02)
            return _Result(_envelope())
        await asyncio.Event().wait()
        raise AssertionError("unreachable")

    client = _FakeClient(call=delayed_call)
    transport = McpAcceptanceTransport(
        worktree_root=Path("/workspace/current-worktree"),
        watch_dir="D:/Unity/bridge-watch",
        _client_factory=lambda _: client,
        _bridge_action=lambda **_: _envelope(),
        _call_timeout_sec=0.01,
    )

    async def scenario() -> tuple[dict[str, Any], dict[str, Any]]:
        async with transport:
            activated = await transport.activate("/project", "Assets/Scope")
            status = await transport.project_status()
            return activated, status

    activated, status = asyncio.run(scenario())

    assert activated["success"] is True
    assert (status["success"], status["code"]) == (
        False,
        "ACCEPTANCE_TRANSPORT_TIMEOUT",
    )
    assert client.calls == [
        ("activate_project", {"project_root": "/project", "scope": "Assets/Scope"}),
        ("get_project_status", {}),
    ]


def test_private_acceptance_actions_never_become_public_mcp_tools() -> None:
    """Moving these actions onto MCP would accidentally widen the public tool surface."""
    client = _FakeClient()
    bridge_calls: list[tuple[str, dict[str, Any]]] = []

    async def scenario() -> None:
        async with _transport(client, bridge_calls) as transport:
            assert (await transport.run_acceptance("a" * 32, 41))["success"] is True
            assert (await transport.acceptance_status("a" * 32))["success"] is True
            assert (await transport.cleanup_acceptance("a" * 32, 23))["success"] is True

    asyncio.run(scenario())

    assert client.calls == []
    assert bridge_calls == [
        (
            "run_integration_tests",
            {
                "timeout_sec": 41,
                "watch_dir": "D:/Unity/bridge-watch",
                "test_profile": "bridge_acceptance",
                "run_live_probes": True,
                "run_id": "a" * 32,
            },
        ),
        (
            "acceptance_status",
            {
                "timeout_sec": 30,
                "watch_dir": "D:/Unity/bridge-watch",
                "run_id": "a" * 32,
            },
        ),
        (
            "cleanup_integration_tests",
            {
                "timeout_sec": 23,
                "watch_dir": "D:/Unity/bridge-watch",
                "run_id": "a" * 32,
            },
        ),
    ]

def test_default_transport_routes_private_actions_through_the_private_file_ipc_helper(
    monkeypatch: Any,
) -> None:
    """The default path must not send private actions through SUPPORTED_ACTIONS."""
    client = _FakeClient()
    calls: list[tuple[str, dict[str, Any]]] = []

    def private_action(*, action: str, **kwargs: Any) -> dict[str, Any]:
        calls.append((action, kwargs))
        return _envelope()

    def public_action(**_: Any) -> dict[str, Any]:
        raise AssertionError("private acceptance action used public send_action")

    monkeypatch.setattr(
        "prefab_sentinel.editor_bridge.send_private_acceptance_action",
        private_action,
        raising=False,
    )
    monkeypatch.setattr(
        "prefab_sentinel.editor_bridge.send_action",
        public_action,
    )
    transport = McpAcceptanceTransport(
        worktree_root=Path("/workspace/current-worktree"),
        watch_dir="D:/Unity/bridge-watch",
        _client_factory=lambda _: client,
    )

    async def scenario() -> dict[str, Any]:
        async with transport:
            return await transport.acceptance_status("a" * 32)

    response = asyncio.run(scenario())

    assert response["success"] is True
    assert calls == [
        (
            "acceptance_status",
            {
                "timeout_sec": 30,
                "watch_dir": "D:/Unity/bridge-watch",
                "run_id": "a" * 32,
            },
        ),
    ]


def test_protocol_mismatch_blocks_deploy_before_the_mutating_tool_call() -> None:
    """Changing the pinned protocol check would let deployment use an unknown wire contract."""
    client = _FakeClient(protocol_version="2025-11-25")
    bridge_calls: list[tuple[str, dict[str, Any]]] = []

    async def scenario() -> dict[str, Any]:
        async with _transport(client, bridge_calls) as transport:
            return await transport.deploy("/project/Assets/Editor/PrefabSentinel")

    result = asyncio.run(scenario())

    assert (result["success"], result["code"], client.calls) == (
        False,
        "ACCEPTANCE_TRANSPORT_PROTOCOL_MISMATCH",
        [],
    )


def test_malformed_mcp_content_returns_a_sanitized_transport_error() -> None:
    """Replacing envelope validation with direct structured-content use would leak malformed data."""
    client = _FakeClient(responses={"get_project_status": _Result({"private": "stderr"})})
    bridge_calls: list[tuple[str, dict[str, Any]]] = []

    async def scenario() -> dict[str, Any]:
        async with _transport(client, bridge_calls) as transport:
            return await transport.project_status()

    result = asyncio.run(scenario())

    assert result == _envelope(
        success=False,
        severity="error",
        code="ACCEPTANCE_TRANSPORT_RESPONSE_INVALID",
        message="MCP tool returned an invalid public response envelope.",
    )


def test_timeout_keeps_private_details_out_of_the_result_and_allows_cleanup() -> None:
    """Removing the deadline or rethrowing a child failure would strand fixtures or disclose stderr."""
    async def hang(_: str, __: dict[str, Any]) -> _Result:
        await asyncio.Event().wait()
        raise AssertionError("unreachable")

    client = _FakeClient(call=hang)
    bridge_calls: list[tuple[str, dict[str, Any]]] = []

    async def scenario() -> tuple[dict[str, Any], dict[str, Any]]:
        async with _transport(client, bridge_calls) as transport:
            timed_out = await transport.project_status()
            cleaned = await transport.cleanup_acceptance("a" * 32, 1)
            return timed_out, cleaned

    timed_out, cleaned = asyncio.run(scenario())

    assert (timed_out["success"], timed_out["code"]) == (
        False,
        "ACCEPTANCE_TRANSPORT_TIMEOUT",
    )
    assert "Event" not in str(timed_out)
    assert cleaned["success"] is True
    assert bridge_calls == [
        (
            "cleanup_integration_tests",
            {
                "timeout_sec": 1,
                "watch_dir": "D:/Unity/bridge-watch",
                "run_id": "a" * 32,
            },
        ),
    ]


def test_child_failure_is_sanitized_and_context_exit_reaps_the_client() -> None:
    """Replacing the managed client lifetime would leak a child process and its stderr details."""
    async def fail(_: str, __: dict[str, Any]) -> _Result:
        raise BrokenPipeError("/private/worktree/stderr.log")

    client = _FakeClient(call=fail)
    bridge_calls: list[tuple[str, dict[str, Any]]] = []

    async def scenario() -> dict[str, Any]:
        async with _transport(client, bridge_calls) as transport:
            return await transport.project_status()

    result = asyncio.run(scenario())

    assert (result["success"], result["code"]) == (
        False,
        "ACCEPTANCE_TRANSPORT_UNAVAILABLE",
    )
    assert "/private/worktree" not in str(result)
    assert (client.entered, client.exited) == (True, True)


def test_only_the_configured_watch_directory_enters_the_child_environment() -> None:
    """Forwarding the parent environment would disclose unrelated Unity and host settings to the child."""
    client = _FakeClient()
    captured: list[StdioServerParameters] = []

    def factory(parameters: StdioServerParameters) -> _FakeClient:
        captured.append(parameters)
        return client

    transport = McpAcceptanceTransport(
        worktree_root=Path("/workspace/current-worktree"),
        watch_dir="D:/Unity/bridge-watch",
        _client_factory=factory,
        _bridge_action=lambda **_: _envelope(),
    )

    async def scenario() -> None:
        async with transport:
            pass

    asyncio.run(scenario())

    parameters = captured[0]
    assert parameters.command == "uv"
    assert parameters.args == [
        "--directory",
        "/workspace/current-worktree",
        "run",
        "--extra",
        "mcp",
        "prefab-sentinel-mcp",
    ]
    assert parameters.env == {"UNITYTOOL_BRIDGE_WATCH_DIR": "D:/Unity/bridge-watch"}


def test_wrong_server_name_or_version_blocks_deploy_before_the_mutating_tool_call() -> None:
    """Removing product identity verification would let another modern MCP server receive deploy."""
    for identity in (
        SimpleNamespace(name="other-server", version=version("prefab-sentinel")),
        SimpleNamespace(name="prefab-sentinel", version="0.0.0"),
    ):
        client = _FakeClient(server_info=identity)
        bridge_calls: list[tuple[str, dict[str, Any]]] = []

        async def scenario(
            fake_client: _FakeClient = client,
            fake_bridge_calls: list[tuple[str, dict[str, Any]]] = bridge_calls,
        ) -> dict[str, Any]:
            async with _transport(fake_client, fake_bridge_calls) as transport:
                return await transport.deploy("/project/Assets/Editor/PrefabSentinel")

        result = asyncio.run(scenario())

        assert (result["success"], result["code"], client.calls) == (
            False,
            "ACCEPTANCE_TRANSPORT_IDENTITY_MISMATCH",
            [],
        )


def test_private_status_timeout_finishes_before_cleanup_begins() -> None:
    """Reintroducing an outer thread timeout would overlap a timed status action with cleanup."""
    status_finished = False
    bridge_calls: list[str] = []

    def bridge_action(*, action: str, **_: Any) -> dict[str, Any]:
        nonlocal status_finished
        bridge_calls.append(action)
        if action == "acceptance_status":
            time.sleep(0.02)
            status_finished = True
            return _envelope(success=False, severity="error", code="EDITOR_BRIDGE_TIMEOUT")
        assert status_finished is True
        return _envelope()

    client = _FakeClient()
    transport = McpAcceptanceTransport(
        worktree_root=Path("/workspace/current-worktree"),
        watch_dir="D:/Unity/bridge-watch",
        _client_factory=lambda _: client,
        _bridge_action=bridge_action,
        _call_timeout_sec=0.01,
    )

    async def scenario() -> tuple[dict[str, Any], dict[str, Any]]:
        async with transport:
            status = await transport.acceptance_status("a" * 32)
            cleanup = await transport.cleanup_acceptance("a" * 32, 1)
            return status, cleanup

    status, cleanup = asyncio.run(scenario())

    assert (status["success"], status["code"]) == (False, "EDITOR_BRIDGE_TIMEOUT")
    assert cleanup["success"] is True
    assert bridge_calls == ["acceptance_status", "cleanup_integration_tests"]


def test_default_sdk_client_uses_stdio_private_stderr_and_discovery_mode(
    monkeypatch: Any,
) -> None:
    """Changing SDK construction can silently drop process cleanup, stderr isolation, or identity discovery."""
    client = _FakeClient()
    captured: dict[str, object] = {}
    stdio_transport = object()

    def fake_stdio(
        parameters: StdioServerParameters,
        *,
        errlog: object,
    ) -> object:
        captured["parameters"] = parameters
        captured["errlog"] = errlog
        return stdio_transport

    def fake_client(transport: object, *, mode: str) -> _FakeClient:
        captured["transport"] = transport
        captured["mode"] = mode
        return client

    monkeypatch.setattr(
        "prefab_sentinel.unity_acceptance.transport.stdio_client",
        fake_stdio,
    )
    monkeypatch.setattr(
        "prefab_sentinel.unity_acceptance.transport.Client",
        fake_client,
    )
    transport = McpAcceptanceTransport(
        worktree_root=Path("/workspace/current-worktree"),
        watch_dir="D:/Unity/bridge-watch",
    )

    async def scenario() -> None:
        async with transport:
            pass

    asyncio.run(scenario())

    errlog = captured["errlog"]
    assert captured["transport"] is stdio_transport
    assert captured["mode"] == "auto"
    assert captured["parameters"] == StdioServerParameters(
        command="uv",
        args=[
            "--directory",
            "/workspace/current-worktree",
            "run",
            "--extra",
            "mcp",
            "prefab-sentinel-mcp",
        ],
        env={"UNITYTOOL_BRIDGE_WATCH_DIR": "D:/Unity/bridge-watch"},
    )
    assert callable(getattr(errlog, "fileno", None))
    assert getattr(errlog, "closed", None) is True
    assert (client.entered, client.exited) == (True, True)
