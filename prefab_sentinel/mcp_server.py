"""MCP server for Prefab Sentinel — thin router.

Exposes Unity asset inspection tools via the Model Context Protocol,
enabling AI agents to address Unity objects by human-readable names.

Requires the ``mcp`` optional dependency::

    pip install prefab-sentinel[mcp]
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from importlib.metadata import version
from pathlib import Path

try:
    from mcp.server import MCPServer
except ImportError as exc:
    raise ImportError("MCP server requires the 'mcp' extra: pip install prefab-sentinel[mcp]") from exc

import prefab_sentinel.editor_bridge as editor_bridge
from prefab_sentinel.mcp_protocol import (
    ProtocolContractMiddleware,
    SerializeToolCallsMiddleware,
)
from prefab_sentinel.mcp_tools_components import register_component_tools
from prefab_sentinel.mcp_tools_components_copy import register_copy_component_fields_tool
from prefab_sentinel.mcp_tools_editor_advanced import register_editor_advanced_tools
from prefab_sentinel.mcp_tools_editor_animation import register_editor_animation_tools
from prefab_sentinel.mcp_tools_editor_assets import register_editor_asset_tools
from prefab_sentinel.mcp_tools_editor_batch import register_editor_batch_tools
from prefab_sentinel.mcp_tools_editor_exec import register_editor_exec_tools
from prefab_sentinel.mcp_tools_editor_geometry import register_editor_geometry_tools
from prefab_sentinel.mcp_tools_editor_ops import register_editor_ops_tools
from prefab_sentinel.mcp_tools_editor_prefab_stage import (
    register_editor_prefab_stage_tools,
)
from prefab_sentinel.mcp_tools_editor_serialized_property import (
    register_editor_serialized_property_tools,
)
from prefab_sentinel.mcp_tools_editor_udonsharp import (
    register_editor_udonsharp_tools,
)
from prefab_sentinel.mcp_tools_editor_view import register_editor_view_tools
from prefab_sentinel.mcp_tools_editor_write import register_editor_write_tools
from prefab_sentinel.mcp_tools_inspector_profiles import register_inspector_profile_tools
from prefab_sentinel.mcp_tools_patch import register_patch_tools
from prefab_sentinel.mcp_tools_session import register_session_tools
from prefab_sentinel.mcp_tools_set_property import register_set_property_tools
from prefab_sentinel.mcp_tools_symbols import register_symbol_tools
from prefab_sentinel.mcp_tools_validation import register_validation_tools
from prefab_sentinel.session import ProjectSession

__all__ = ["create_server"]

logger = logging.getLogger(__name__)

SERVER_NAME = "prefab-sentinel"


def _server_instructions() -> str:
    return (
        "activate_project selects the process-wide active Unity project. "
        "One process represents one logical client/project scope. "
        "Tool calls execute serially. "
        "Normal inspection, dry-run, and confirm entry points remain unchanged."
    )


def create_server(
    project_root: str | Path | None = None,
) -> MCPServer[ProjectSession]:
    """Create and configure the Prefab Sentinel MCP server.

    Args:
        project_root: Unity project root. Auto-detected when None.

    Returns:
        A configured MCPServer instance ready to run.
    """
    root = Path(project_root).resolve() if project_root else None
    session = ProjectSession(project_root=root)
    server_version = version("prefab-sentinel")
    instructions = _server_instructions()

    @asynccontextmanager
    async def lifespan(
        _server: MCPServer[ProjectSession],
    ) -> AsyncIterator[ProjectSession]:
        editor_bridge._set_expected_project_root_provider(
            lambda: str(session.project_root) if session.project_root is not None else None
        )
        try:
            yield session
        finally:
            editor_bridge._set_expected_project_root_provider(None)
            await session.shutdown()

    server = MCPServer(
        name=SERVER_NAME,
        version=server_version,
        instructions=instructions,
        lifespan=lifespan,
        middleware=[
            ProtocolContractMiddleware(
                server_name=SERVER_NAME,
                server_version=server_version,
                instructions=instructions,
            ),
            SerializeToolCallsMiddleware(),
        ],
    )

    # Register tool modules
    register_session_tools(server, session)
    register_symbol_tools(server, session)
    register_set_property_tools(server, session)
    register_component_tools(server, session)
    register_copy_component_fields_tool(server, session)
    register_validation_tools(server, session)
    register_inspector_profile_tools(server, session)
    register_patch_tools(server, session)
    register_editor_view_tools(server)
    register_editor_write_tools(server)
    register_editor_batch_tools(server)
    register_editor_ops_tools(server)
    register_editor_serialized_property_tools(server)
    register_editor_asset_tools(server)
    register_editor_advanced_tools(server)
    register_editor_exec_tools(server)
    register_editor_geometry_tools(server)
    register_editor_udonsharp_tools(server)
    # Issue #236 / #243: dedicated registration hooks for the Prefab
    # Stage open/close primitives and the three AnimationClip surfaces.
    register_editor_prefab_stage_tools(server)
    register_editor_animation_tools(server)

    return server


def _port_number(value: str) -> int:
    """Parse a TCP port accepted by the loopback HTTP CLI."""
    import argparse

    try:
        port = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("PORT must be an integer from 1 through 65535") from exc
    if not 1 <= port <= 65535:
        raise argparse.ArgumentTypeError("PORT must be an integer from 1 through 65535")
    return port


def main() -> None:
    """Entry point for the MCP server."""
    import argparse

    import uvicorn

    from prefab_sentinel.mcp_http import build_http_app

    parser = argparse.ArgumentParser(description="Prefab Sentinel MCP Server")
    parser.add_argument(
        "--transport",
        choices=["stdio", "streamable-http"],
        default="stdio",
        help="MCP transport protocol (default: stdio)",
    )
    parser.add_argument(
        "--project-root",
        default=None,
        help="Unity project root directory (auto-detected if omitted)",
    )
    parser.add_argument(
        "--port",
        type=_port_number,
        default=8000,
        metavar="PORT",
        help="Streamable HTTP loopback port (default: %(default)s)",
    )
    args = parser.parse_args()

    server = create_server(project_root=args.project_root)
    if args.transport == "stdio":
        server.run("stdio")
        return

    app = build_http_app(server)
    uvicorn.run(app, host="127.0.0.1", port=args.port)


if __name__ == "__main__":
    main()
