"""Protocol-boundary and process-local tool serialization tests."""

from __future__ import annotations

import copy
import json
import threading
import unittest
from collections.abc import Callable, Mapping
from importlib.metadata import version
from pathlib import Path
from typing import Any, cast
from unittest.mock import patch

import anyio
from mcp import Client, MCPError
from mcp.server import MCPServer
from mcp.server.context import CallNext, HandlerResult, ServerRequestContext
from mcp.server.session import ServerSession
from mcp_types import EmptyResult, Request, TextContent

from prefab_sentinel.mcp_protocol import (
    MCP_PROTOCOL_VERSION,
    ProtocolContractMiddleware,
    SerializeToolCallsMiddleware,
)
from prefab_sentinel.mcp_server import create_server


def _context(
    method: str,
    *,
    protocol_version: str = "2026-07-28",
    params: Mapping[str, Any] | None = None,
    request_id: int | str | None = 1,
) -> ServerRequestContext[Any, Any]:
    return ServerRequestContext(
        session=cast(ServerSession, None),
        lifespan_context={},
        protocol_version=protocol_version,
        method=method,
        params=params,
        request_id=request_id,
    )


def _contract() -> ProtocolContractMiddleware:
    return ProtocolContractMiddleware(
        server_name="prefab-sentinel-test",
        server_version="9.8.7",
        instructions="Use only the advertised tools.",
    )



class TestCreateServerComposition(unittest.TestCase):
    def test_public_discovery_is_tools_only_with_complete_tool_surface(self) -> None:
        fixture_path = (
            Path(__file__).parent / "fixtures" / "mcp_v1_tool_schemas.json"
        )
        golden = json.loads(fixture_path.read_text(encoding="utf-8"))
        provenance = golden["_provenance"]
        self.assertNotIn("source_commit", provenance)
        self.assertEqual("1.26.0", provenance["mcp_sdk_version"])
        expected_tools = golden["tools"]
        expected_by_name = {tool["name"]: tool for tool in expected_tools}

        def materialize_implicit_object_types(schema: Any) -> None:
            if not isinstance(schema, dict):
                return
            if "properties" in schema:
                schema.setdefault("type", "object")
                for property_schema in schema["properties"].values():
                    materialize_implicit_object_types(property_schema)
            for definitions_key in ("$defs", "definitions"):
                definitions = schema.get(definitions_key, {})
                for definition in definitions.values():
                    materialize_implicit_object_types(definition)
            for branch_key in ("anyOf", "oneOf", "allOf"):
                for branch in schema.get(branch_key, []):
                    materialize_implicit_object_types(branch)
            materialize_implicit_object_types(schema.get("items"))
            materialize_implicit_object_types(
                schema.get("additionalProperties")
            )

        async def scenario() -> None:
            server = create_server()
            self.assertIsInstance(server, MCPServer)
            self.assertIsInstance(
                server.middleware[-2],
                ProtocolContractMiddleware,
            )
            self.assertIsInstance(
                server.middleware[-1],
                SerializeToolCallsMiddleware,
            )

            async with Client(
                server,
                mode=MCP_PROTOCOL_VERSION,
                raise_exceptions=True,
            ) as client:
                discovery_payload = await client.session.send_discover(
                    MCP_PROTOCOL_VERSION
                )
                public_tools = (await client.list_tools()).tools
                self.assertEqual(MCP_PROTOCOL_VERSION, client.protocol_version)
                self.assertEqual(
                    [MCP_PROTOCOL_VERSION],
                    discovery_payload["supportedVersions"],
                )
                self.assertEqual(
                    {"tools": {"listChanged": False}},
                    discovery_payload["capabilities"],
                )
                self.assertEqual(
                    {
                        "name": "prefab-sentinel",
                        "version": version("prefab-sentinel"),
                    },
                    discovery_payload["_meta"][
                        "io.modelcontextprotocol/serverInfo"
                    ],
                )
                instructions = discovery_payload["instructions"]
                for statement in (
                    "activate_project selects the process-wide active Unity project",
                    "One process represents one logical client/project scope",
                    "Tool calls execute serially",
                    "inspection, dry-run, and confirm entry points remain unchanged",
                ):
                    self.assertIn(statement, instructions)

            self.assertEqual(101, len(expected_tools))
            self.assertEqual(101, len(public_tools))
            self.assertEqual(
                set(expected_by_name),
                {tool.name for tool in public_tools},
            )
            missing_descriptions = [
                tool.name
                for tool in public_tools
                if not (tool.description and tool.description.strip())
            ]
            self.assertEqual([], missing_descriptions)
            for public_tool in public_tools:
                expected = expected_by_name[public_tool.name]
                expected_input_schema = copy.deepcopy(expected["inputSchema"])
                materialize_implicit_object_types(expected_input_schema)
                if public_tool.name == "editor_set_udonsharp_field":
                    expected_input_schema["properties"]["values_json"] = {
                        "default": "",
                        "title": "Values Json",
                        "type": "string",
                    }
                self.assertEqual(
                    expected_input_schema,
                    public_tool.input_schema,
                    f"{public_tool.name} input schema",
                )
                expected_output_schema = copy.deepcopy(expected["outputSchema"])
                materialize_implicit_object_types(expected_output_schema)
                self.assertEqual(
                    expected_output_schema,
                    public_tool.output_schema,
                    f"{public_tool.name} output schema",
                )

        anyio.run(scenario)

    def test_domain_failure_and_validation_failure_keep_distinct_error_flags(self) -> None:
        async def scenario() -> None:
            server = create_server()
            async with Client(
                server,
                mode=MCP_PROTOCOL_VERSION,
                raise_exceptions=True,
            ) as client:
                domain_failure = await client.call_tool(
                    "activate_project",
                    {
                        "scope": "Assets",
                        "project_root": "/path/that/does/not/exist",
                    },
                )
                validation_failure = await client.call_tool(
                    "activate_project",
                    {},
                )

            self.assertIs(domain_failure.is_error, False)
            self.assertEqual(
                False,
                domain_failure.structured_content["success"],
            )
            self.assertTrue(validation_failure.is_error)

        anyio.run(scenario)

    def test_forbidden_non_tools_method_is_a_top_level_mcp_error(self) -> None:
        async def scenario() -> None:
            server = create_server()
            async with Client(
                server,
                mode=MCP_PROTOCOL_VERSION,
                raise_exceptions=True,
            ) as client:
                with self.assertRaises(MCPError) as caught:
                    await client.session.send_request(
                        Request(method="resources/list", params={}),
                        EmptyResult,
                    )
                error = caught.exception

            self.assertEqual(-32601, error.code)
            self.assertEqual("Method not found: resources/list", error.message)
            self.assertIsNone(error.data)

        anyio.run(scenario)

    def test_public_client_lifespan_clears_provider_before_failed_shutdown(self) -> None:
        expected_root = str(Path("/workspace/ExpectedProject").resolve())
        provider_events: list[Callable[[], str | None] | None] = []
        shutdown_sessions: list[object] = []

        def capture_provider(
            provider: Callable[[], str | None] | None,
        ) -> None:
            provider_events.append(provider)

        async def fail_shutdown(session: object) -> None:
            self.assertIsNone(provider_events[-1])
            shutdown_sessions.append(session)
            raise RuntimeError("shutdown failed")

        async def scenario() -> None:
            server = create_server(project_root=expected_root)
            with (
                patch(
                    "prefab_sentinel.mcp_server."
                    "editor_bridge._set_expected_project_root_provider",
                    capture_provider,
                ),
                patch(
                    "prefab_sentinel.session.ProjectSession.shutdown",
                    fail_shutdown,
                ),
            ):
                with self.assertRaisesRegex(RuntimeError, "shutdown failed"):
                    async with Client(
                        server,
                        mode=MCP_PROTOCOL_VERSION,
                        raise_exceptions=True,
                    ):
                        self.assertEqual(1, len(provider_events))
                        provider = provider_events[0]
                        self.assertIsNotNone(provider)
                        assert provider is not None
                        self.assertEqual(expected_root, provider())

        anyio.run(scenario)

        self.assertEqual(2, len(provider_events))
        self.assertIsNone(provider_events[1])
        self.assertEqual(1, len(shutdown_sessions))

class TestProtocolContractMiddleware(unittest.TestCase):

    def test_legacy_initialize_reaches_handler_and_normalizes_sdk_result(self) -> None:
        for protocol_version in ("2025-11-25", "2025-06-18"):
            with self.subTest(protocol_version=protocol_version):

                async def scenario(
                    bound_protocol_version: str = protocol_version,
                ) -> None:
                    sdk_initialize = {"vendor.example/preserved": True}
                    original_initialize = copy.deepcopy(sdk_initialize)
                    delegated = False

                    async def call_next(
                        _: ServerRequestContext[Any, Any],
                    ) -> HandlerResult:
                        nonlocal delegated
                        delegated = True
                        return sdk_initialize

                    result = await _contract()(
                        _context(
                            "initialize",
                            protocol_version=bound_protocol_version,
                            params={
                                "protocolVersion": bound_protocol_version,
                            },
                        ),
                        call_next,
                    )

                    self.assertTrue(delegated)
                    self.assertEqual(
                        {
                            "protocolVersion": bound_protocol_version,
                            "capabilities": {"tools": {"listChanged": False}},
                            "serverInfo": {
                                "name": "prefab-sentinel-test",
                                "version": "9.8.7",
                            },
                            "instructions": "Use only the advertised tools.",
                            "vendor.example/preserved": True,
                        },
                        result,
                    )
                    self.assertEqual(original_initialize, sdk_initialize)

                anyio.run(scenario)

    def test_legacy_initialize_rejects_unsupported_raw_version_before_delegation(self) -> None:
        requested = "2025-01-01"

        async def scenario() -> None:
            delegated = False

            async def call_next(_: ServerRequestContext[Any, Any]) -> HandlerResult:
                nonlocal delegated
                delegated = True
                return EmptyResult()

            with self.assertRaises(MCPError) as caught:
                await _contract()(
                    _context(
                        "initialize",
                        protocol_version="2025-11-25",
                        params={"protocolVersion": requested},
                    ),
                    call_next,
                )
            error = caught.exception

            self.assertFalse(delegated)
            self.assertEqual(-32022, error.code)
            self.assertEqual(
                f"Unsupported protocol version: {requested}",
                error.message,
            )
            self.assertEqual(
                {
                    "supported": ["2026-07-28", "2025-11-25", "2025-06-18"],
                    "requested": requested,
                },
                error.data,
            )

        anyio.run(scenario)

    def test_legacy_initialize_malformed_raw_version_reaches_sdk_validation(self) -> None:
        parameter_sets = (None, {"protocolVersion": 7})

        for protocol_version in ("2025-11-25", "2025-06-18"):
            for params in parameter_sets:
                with self.subTest(protocol_version=protocol_version, params=params):

                    async def scenario(
                        bound_protocol_version: str = protocol_version,
                        bound_params: Mapping[str, Any] | None = params,
                    ) -> None:
                        delegated = False

                        async def call_next(
                            _: ServerRequestContext[Any, Any],
                        ) -> HandlerResult:
                            nonlocal delegated
                            delegated = True
                            return {}

                        await _contract()(
                            _context(
                                "initialize",
                                protocol_version=bound_protocol_version,
                                params=bound_params,
                            ),
                            call_next,
                        )

                        self.assertTrue(delegated)

                    anyio.run(scenario)

    def test_unknown_context_version_uses_modern_first_stdio_supported_list(self) -> None:
        async def scenario() -> None:
            async def call_next(_: ServerRequestContext[Any, Any]) -> HandlerResult:
                self.fail("unsupported context version must not reach the handler")

            await _contract()(
                _context("tools/list", protocol_version="2026-08-01"),
                call_next,
            )

        with self.assertRaises(MCPError) as caught:
            anyio.run(scenario)
        self.assertEqual(-32022, caught.exception.code)
        self.assertEqual(
            "Unsupported protocol version: 2026-08-01",
            caught.exception.message,
        )
        self.assertEqual(
            {
                "supported": ["2026-07-28", "2025-11-25", "2025-06-18"],
                "requested": "2026-08-01",
            },
            caught.exception.data,
        )

    def test_legacy_tools_delegate_and_unsupported_methods_are_product_errors(self) -> None:
        delegated_methods = ("tools/list", "tools/call")
        rejected_methods = (
            "ping",
            "resources/list",
            "prompts/list",
            "server/discover",
        )

        for protocol_version in ("2025-11-25", "2025-06-18"):
            for method in delegated_methods:
                with self.subTest(protocol_version=protocol_version, method=method):

                    async def scenario(
                        bound_protocol_version: str = protocol_version,
                        bound_method: str = method,
                    ) -> None:
                        delegated = False
                        delegated_result = EmptyResult()

                        async def call_next(
                            _: ServerRequestContext[Any, Any],
                        ) -> HandlerResult:
                            nonlocal delegated
                            delegated = True
                            return delegated_result

                        result = await _contract()(
                            _context(
                                bound_method,
                                protocol_version=bound_protocol_version,
                            ),
                            call_next,
                        )

                        self.assertTrue(delegated)
                        self.assertIs(delegated_result, result)

                    anyio.run(scenario)

            for method in rejected_methods:
                with self.subTest(protocol_version=protocol_version, method=method):

                    async def scenario(
                        bound_protocol_version: str = protocol_version,
                        bound_method: str = method,
                    ) -> None:
                        delegated = False

                        async def call_next(
                            _: ServerRequestContext[Any, Any],
                        ) -> HandlerResult:
                            nonlocal delegated
                            delegated = True
                            return EmptyResult()

                        with self.assertRaises(MCPError) as caught:
                            await _contract()(
                                _context(
                                    bound_method,
                                    protocol_version=bound_protocol_version,
                                ),
                                call_next,
                            )
                        error = caught.exception

                        self.assertFalse(delegated)
                        self.assertEqual(-32601, error.code)
                        self.assertEqual(
                            f"Method not found: {bound_method}",
                            error.message,
                        )
                        self.assertIsNone(error.data)

                    anyio.run(scenario)

    def test_legacy_initialize_rejects_non_dictionary_result(self) -> None:
        for protocol_version in ("2025-11-25", "2025-06-18"):
            with self.subTest(protocol_version=protocol_version):

                async def scenario(
                    bound_protocol_version: str = protocol_version,
                ) -> None:
                    async def call_next(
                        _: ServerRequestContext[Any, Any],
                    ) -> HandlerResult:
                        return None

                    await _contract()(
                        _context(
                            "initialize",
                            protocol_version=bound_protocol_version,
                            params={
                                "protocolVersion": bound_protocol_version,
                            },
                        ),
                        call_next,
                    )

                with self.assertRaises(TypeError) as caught:
                    anyio.run(scenario)
                self.assertEqual(
                    "initialize handler must return a dictionary",
                    str(caught.exception),
                )

    def test_allowlisted_2026_request_reaches_handler(self) -> None:
        async def scenario() -> None:
            server = MCPServer("modern-contract", middleware=[_contract()])
            async with Client(
                server,
                mode=MCP_PROTOCOL_VERSION,
                raise_exceptions=True,
            ) as client:
                result = await client.list_tools()

            self.assertEqual([], result.tools)

        anyio.run(scenario)

    def test_initialize_version_is_validated_before_method_resolution(self) -> None:
        async def scenario() -> None:
            delegated = False

            async def call_next(
                _: ServerRequestContext[Any, Any],
            ) -> HandlerResult:
                nonlocal delegated
                delegated = True
                return EmptyResult()

            with self.assertRaises(MCPError) as caught:
                await _contract()(
                    _context(
                        "initialize",
                        protocol_version="2026-08-01",
                        params={"protocolVersion": "2025-11-25"},
                    ),
                    call_next,
                )
            error = caught.exception

            self.assertFalse(delegated)
            self.assertEqual(-32022, error.code)
            self.assertEqual(
                "Unsupported protocol version: 2026-08-01",
                error.message,
            )
            self.assertEqual(
                {"supported": ["2026-07-28", "2025-11-25", "2025-06-18"], "requested": "2026-08-01"},
                error.data,
            )

        anyio.run(scenario)

    def test_modern_initialize_is_method_not_found_without_handler_reach(self) -> None:
        modern_meta = {
            "io.modelcontextprotocol/protocolVersion": MCP_PROTOCOL_VERSION,
            "io.modelcontextprotocol/clientInfo": {
                "name": "conformance-client",
                "version": "1.0.0",
            },
            "io.modelcontextprotocol/clientCapabilities": {},
        }
        parameter_sets = (
            {"_meta": modern_meta},
            {
                "_meta": modern_meta,
                "protocolVersion": "2025-11-25",
            },
            {
                "_meta": modern_meta,
                "protocolVersion": "2026-07-28",
            },
        )

        for params in parameter_sets:
            with self.subTest(params=params):

                async def scenario(
                    bound_params: Mapping[str, Any] = params,
                ) -> None:
                    async def call_next(_: ServerRequestContext[Any, Any]) -> HandlerResult:
                        self.fail("removed initialize must not reach the SDK handler")

                    await _contract()(
                        _context("initialize", params=bound_params),
                        call_next,
                    )

                with self.assertRaises(MCPError) as caught:
                    anyio.run(scenario)
                self.assertEqual(-32601, caught.exception.code)
                self.assertEqual(
                    "Method not found: initialize",
                    caught.exception.message,
                )
                self.assertIsNone(caught.exception.data)

    def test_non_allowlisted_modern_version_is_rejected(self) -> None:
        async def scenario() -> None:
            async def call_next(_: ServerRequestContext[Any, Any]) -> HandlerResult:
                self.fail("unsupported modern version must not reach the handler")

            await _contract()(
                _context("tools/list", protocol_version="2026-08-01"),
                call_next,
            )

        with self.assertRaises(MCPError) as caught:
            anyio.run(scenario)
        self.assertEqual(-32022, caught.exception.code)
        self.assertEqual(
            {"supported": ["2026-07-28", "2025-11-25", "2025-06-18"], "requested": "2026-08-01"},
            caught.exception.data,
        )

    def test_non_product_request_methods_are_method_not_found(self) -> None:
        rejected_methods = (
            "initialize",
            "resources/list",
            "prompts/list",
            "subscriptions/listen",
            "ping",
            "completion/complete",
        )

        for method in rejected_methods:
            with self.subTest(method=method):

                async def scenario(bound_method: str) -> None:
                    server = MCPServer(
                        "method-contract",
                        middleware=[_contract()],
                    )
                    async with Client(
                        server,
                        mode=MCP_PROTOCOL_VERSION,
                        raise_exceptions=True,
                    ) as client:
                        with self.assertRaises(MCPError) as caught:
                            await client.session.send_request(
                                Request(method=bound_method, params={}),
                                EmptyResult,
                            )
                        error = caught.exception

                    self.assertEqual(-32601, error.code)
                    self.assertEqual(
                        f"Method not found: {bound_method}",
                        error.message,
                    )
                    self.assertIsNone(error.data)

                anyio.run(scenario, method)

    def test_discovery_replaces_only_product_owned_fields(self) -> None:
        sdk_discovery = {
            "resultType": "complete",
            "ttlMs": 4321,
            "cacheScope": "private",
            "supportedVersions": ["2025-11-25", "2026-07-28"],
            "capabilities": {"resources": {"subscribe": True}},
            "instructions": "SDK instructions",
            "_meta": {
                "vendor.example/cacheTag": "preserve-me",
                "io.modelcontextprotocol/serverInfo": {
                    "name": "sdk-name",
                    "version": "0.0.0",
                },
            },
            "vendorExtension": {"enabled": True},
        }
        original_discovery = copy.deepcopy(sdk_discovery)

        async def scenario() -> None:
            async def call_next(_: ServerRequestContext[Any, Any]) -> HandlerResult:
                return sdk_discovery

            result = await _contract()(_context("server/discover"), call_next)

            self.assertEqual(
                {
                    "resultType": "complete",
                    "ttlMs": 4321,
                    "cacheScope": "private",
                    "supportedVersions": ["2026-07-28"],
                    "capabilities": {"tools": {"listChanged": False}},
                    "instructions": "Use only the advertised tools.",
                    "_meta": {
                        "vendor.example/cacheTag": "preserve-me",
                        "io.modelcontextprotocol/serverInfo": {
                            "name": "prefab-sentinel-test",
                            "version": "9.8.7",
                        },
                    },
                    "vendorExtension": {"enabled": True},
                },
                result,
            )
            self.assertEqual(original_discovery, sdk_discovery)

        anyio.run(scenario)

    def test_non_dictionary_discovery_result_is_an_invariant_error(self) -> None:
        async def scenario() -> None:
            async def call_next(_: ServerRequestContext[Any, Any]) -> HandlerResult:
                return None

            await _contract()(_context("server/discover"), call_next)

        with self.assertRaises(TypeError) as caught:
            anyio.run(scenario)
        self.assertEqual("server/discover handler must return a dictionary", str(caught.exception))

    def test_legacy_initialized_notification_delegates_to_sdk_dispatcher(self) -> None:
        async def scenario() -> None:
            delegated_methods: list[str] = []

            async def call_next(ctx: ServerRequestContext[Any, Any]) -> HandlerResult:
                delegated_methods.append(ctx.method)
                return None

            for protocol_version in ("2025-11-25", "2025-06-18", "2026-07-28"):
                with self.subTest(protocol_version=protocol_version):
                    result = await _contract()(
                        _context(
                            "notifications/initialized",
                            protocol_version=protocol_version,
                            request_id=None,
                        ),
                        call_next,
                    )
                    self.assertIsNone(result)

            arbitrary_result = await _contract()(
                _context(
                    "notifications/roots/list_changed",
                    protocol_version="2025-11-25",
                    request_id=None,
                ),
                call_next,
            )
            self.assertIsNone(arbitrary_result)

            for protocol_version in (
                "2025-11-25",
                "2025-06-18",
                "2026-07-28",
            ):
                with self.subTest(cancelled_protocol_version=protocol_version):
                    result = await _contract()(
                        _context(
                            "notifications/cancelled",
                            protocol_version=protocol_version,
                            request_id=None,
                        ),
                        call_next,
                    )
                    self.assertIsNone(result)

            self.assertEqual(
                [
                    "notifications/initialized",
                    "notifications/initialized",
                    "notifications/cancelled",
                    "notifications/cancelled",
                    "notifications/cancelled",
                ],
                delegated_methods,
            )

        anyio.run(scenario)

    def test_cancellation_notification_delegates_to_sdk_dispatcher(self) -> None:
        async def scenario() -> None:
            for protocol_version in (
                "2026-07-28",
                "2025-11-25",
                "2025-06-18",
            ):
                with self.subTest(protocol_version=protocol_version):
                    delegated = False

                    async def call_next(
                        _: ServerRequestContext[Any, Any],
                    ) -> HandlerResult:
                        nonlocal delegated
                        delegated = True
                        return None

                    result = await _contract()(
                        _context(
                            "notifications/cancelled",
                            protocol_version=protocol_version,
                            request_id=None,
                        ),
                        call_next,
                    )

                    self.assertTrue(delegated)
                    self.assertIsNone(result)

        anyio.run(scenario)

    def test_other_notifications_are_ignored(self) -> None:
        async def scenario() -> None:
            delegated = False

            async def call_next(_: ServerRequestContext[Any, Any]) -> HandlerResult:
                nonlocal delegated
                delegated = True
                return None

            result = await _contract()(
                _context(
                    "notifications/roots/list_changed",
                    protocol_version="2025-11-25",
                    request_id=None,
                ),
                call_next,
            )

            self.assertFalse(delegated)
            self.assertIsNone(result)

        anyio.run(scenario)


class TestSerializeToolCallsMiddleware(unittest.TestCase):
    def test_two_clients_never_run_tool_handlers_concurrently(self) -> None:
        async def scenario() -> None:
            active = 0
            maximum_active = 0
            first_entered = anyio.Event()
            two_active = anyio.Event()
            release = anyio.Event()
            second_dispatched = anyio.Event()

            server = MCPServer("serialize-test", middleware=[SerializeToolCallsMiddleware()])

            @server.tool()
            async def gated_tool() -> str:
                nonlocal active, maximum_active
                active += 1
                maximum_active = max(maximum_active, active)
                if active == 1:
                    first_entered.set()
                if active == 2:
                    two_active.set()
                try:
                    await release.wait()
                    return "done"
                finally:
                    active -= 1

            async with (
                Client(server, mode=MCP_PROTOCOL_VERSION, raise_exceptions=True) as first,
                Client(server, mode=MCP_PROTOCOL_VERSION, raise_exceptions=True) as second,
            ):

                async def first_call() -> None:
                    await first.call_tool("gated_tool", {})

                async def second_call() -> None:
                    second_dispatched.set()
                    await second.call_tool("gated_tool", {})

                async with anyio.create_task_group() as task_group:
                    task_group.start_soon(first_call)
                    await first_entered.wait()
                    task_group.start_soon(second_call)
                    await second_dispatched.wait()
                    with anyio.move_on_after(0.05):
                        await two_active.wait()
                    release.set()

            self.assertFalse(two_active.is_set())
            self.assertEqual(1, maximum_active)

        anyio.run(scenario)

    def test_list_and_discovery_bypass_a_held_tool_lock(self) -> None:
        async def scenario() -> None:
            tool_entered = anyio.Event()
            release = anyio.Event()
            server = MCPServer("non-tool-concurrency", middleware=[SerializeToolCallsMiddleware()])

            @server.tool()
            async def gated_tool() -> str:
                tool_entered.set()
                await release.wait()
                return "done"

            async with (
                Client(server, mode=MCP_PROTOCOL_VERSION, raise_exceptions=True) as first,
                Client(server, mode=MCP_PROTOCOL_VERSION, raise_exceptions=True) as second,
            ):

                async def tool_call() -> None:
                    await first.call_tool("gated_tool", {})

                async with anyio.create_task_group() as task_group:
                    task_group.start_soon(tool_call)
                    await tool_entered.wait()
                    with anyio.fail_after(0.5):
                        listed = await second.list_tools()
                        discovered = await second.session.discover()
                    self.assertEqual(["gated_tool"], [tool.name for tool in listed.tools])
                    self.assertIn(MCP_PROTOCOL_VERSION, discovered.supported_versions)
                    release.set()

        anyio.run(scenario)

    def test_handler_exception_releases_tool_lock(self) -> None:
        async def scenario() -> None:
            attempts = 0
            server = MCPServer("handler-exception", middleware=[SerializeToolCallsMiddleware()])

            @server.tool()
            async def flaky_tool() -> str:
                nonlocal attempts
                attempts += 1
                if attempts == 1:
                    raise RuntimeError("handler failed")
                return "recovered"

            async with Client(
                server,
                mode=MCP_PROTOCOL_VERSION,
                raise_exceptions=True,
            ) as client:
                failed = await client.call_tool("flaky_tool", {})
                self.assertTrue(failed.is_error)
                self.assertIsInstance(failed.content[0], TextContent)
                error_content = cast(TextContent, failed.content[0])
                self.assertEqual(
                    "Error executing tool flaky_tool: handler failed",
                    error_content.text,
                )

                with anyio.fail_after(0.5):
                    recovered = await client.call_tool("flaky_tool", {})

            self.assertIs(recovered.is_error, False)
            self.assertEqual({"result": "recovered"}, recovered.structured_content)
            self.assertEqual(2, attempts)

        anyio.run(scenario)

    def test_async_handler_cancellation_releases_tool_lock(self) -> None:
        async def scenario() -> None:
            first_entered = anyio.Event()
            second_entered = anyio.Event()
            first_scope_ready = anyio.Event()
            first_scope: list[anyio.CancelScope] = []
            server = MCPServer("async-cancellation", middleware=[SerializeToolCallsMiddleware()])

            @server.tool()
            async def cancellable_tool(label: str) -> str:
                if label == "first":
                    first_entered.set()
                    await anyio.sleep_forever()
                second_entered.set()
                return label

            async with (
                Client(server, mode=MCP_PROTOCOL_VERSION, raise_exceptions=True) as first,
                Client(server, mode=MCP_PROTOCOL_VERSION, raise_exceptions=True) as second,
            ):

                async def cancelled_call() -> None:
                    with anyio.CancelScope() as scope:
                        first_scope.append(scope)
                        first_scope_ready.set()
                        await first.call_tool("cancellable_tool", {"label": "first"})

                async with anyio.create_task_group() as task_group:
                    task_group.start_soon(cancelled_call)
                    await first_scope_ready.wait()
                    await first_entered.wait()
                    first_scope[0].cancel()
                    with anyio.fail_after(0.5):
                        result = await second.call_tool("cancellable_tool", {"label": "second"})
                    self.assertIs(result.is_error, False)
                    self.assertEqual({"result": "second"}, result.structured_content)
                    self.assertTrue(second_entered.is_set())

        anyio.run(scenario)

    def test_cancelled_sync_worker_holds_lock_until_worker_returns(self) -> None:
        async def scenario() -> None:
            worker_entered = threading.Event()
            release_worker = threading.Event()
            worker_returned = threading.Event()
            second_entered = threading.Event()
            second_observations: list[bool] = []
            first_scope_ready = anyio.Event()
            second_reached_boundary = anyio.Event()
            second_completed = anyio.Event()
            first_scope: list[anyio.CancelScope] = []
            requests_at_boundary = 0

            async def boundary_probe(
                ctx: ServerRequestContext[Any, Any],
                call_next: CallNext,
            ) -> HandlerResult:
                nonlocal requests_at_boundary
                if ctx.method == "tools/call":
                    requests_at_boundary += 1
                    if requests_at_boundary == 2:
                        second_reached_boundary.set()
                return await call_next(ctx)

            server = MCPServer(
                "sync-cancellation",
                middleware=[boundary_probe, SerializeToolCallsMiddleware()],
            )

            @server.tool()
            def sync_tool(label: str) -> str:
                if label == "first":
                    worker_entered.set()
                    if not release_worker.wait(timeout=2.0):
                        raise RuntimeError("test worker release timed out")
                    worker_returned.set()
                else:
                    second_observations.append(worker_returned.is_set())
                    second_entered.set()
                return label

            async with (
                Client(server, mode=MCP_PROTOCOL_VERSION, raise_exceptions=True) as first,
                Client(server, mode=MCP_PROTOCOL_VERSION, raise_exceptions=True) as second,
            ):

                async def cancelled_call() -> None:
                    with anyio.CancelScope() as scope:
                        first_scope.append(scope)
                        first_scope_ready.set()
                        await first.call_tool("sync_tool", {"label": "first"})

                async def second_call() -> None:
                    await second.call_tool("sync_tool", {"label": "second"})
                    second_completed.set()

                async with anyio.create_task_group() as task_group:
                    task_group.start_soon(cancelled_call)
                    await first_scope_ready.wait()
                    with anyio.fail_after(0.5):
                        self.assertTrue(await anyio.to_thread.run_sync(worker_entered.wait, 0.5))
                    first_scope[0].cancel()
                    task_group.start_soon(second_call)
                    with anyio.fail_after(0.5):
                        await second_reached_boundary.wait()
                    self.assertFalse(second_entered.is_set())
                    release_worker.set()
                    with anyio.fail_after(0.5):
                        await second_completed.wait()

            self.assertTrue(worker_returned.is_set())
            self.assertTrue(second_entered.is_set())
            self.assertEqual([True], second_observations)

        anyio.run(scenario)
