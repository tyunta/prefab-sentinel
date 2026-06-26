"""MCP server for Prefab Sentinel — thin router.

Exposes Unity asset inspection tools via the Model Context Protocol,
enabling AI agents to address Unity objects by human-readable names.

Requires the ``mcp`` optional dependency::

    pip install prefab-sentinel[mcp]
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

try:
    from mcp.server.fastmcp import FastMCP
except ImportError as exc:
    raise ImportError(
        "MCP server requires the 'mcp' extra: "
        "pip install prefab-sentinel[mcp]"
    ) from exc

import prefab_sentinel.editor_bridge as editor_bridge
from prefab_sentinel.mcp_tools_components import register_component_tools
from prefab_sentinel.mcp_tools_components_copy import register_copy_component_fields_tool
from prefab_sentinel.mcp_tools_editor_advanced import register_editor_advanced_tools
from prefab_sentinel.mcp_tools_editor_animation import register_editor_animation_tools
from prefab_sentinel.mcp_tools_editor_batch import register_editor_batch_tools
from prefab_sentinel.mcp_tools_editor_exec import register_editor_exec_tools
from prefab_sentinel.mcp_tools_editor_geometry import register_editor_geometry_tools
from prefab_sentinel.mcp_tools_editor_ops import register_editor_ops_tools
from prefab_sentinel.mcp_tools_editor_prefab_stage import (
    register_editor_prefab_stage_tools,
)
from prefab_sentinel.mcp_tools_editor_udonsharp import (
    register_editor_udonsharp_tools,
)
from prefab_sentinel.mcp_tools_editor_view import register_editor_view_tools
from prefab_sentinel.mcp_tools_editor_write import register_editor_write_tools
from prefab_sentinel.mcp_tools_patch import register_patch_tools
from prefab_sentinel.mcp_tools_session import register_session_tools
from prefab_sentinel.mcp_tools_set_property import register_set_property_tools
from prefab_sentinel.mcp_tools_symbols import register_symbol_tools
from prefab_sentinel.mcp_tools_validation import register_validation_tools
from prefab_sentinel.session import ProjectSession

__all__ = ["create_server"]

logger = logging.getLogger(__name__)










def create_server(
    project_root: str | Path | None = None,
) -> FastMCP:
    """Create and configure the Prefab Sentinel MCP server.

    Args:
        project_root: Unity project root. Auto-detected when ``None``.

    Returns:
        A configured ``FastMCP`` server instance ready to run.
    """
    _root = Path(project_root) if project_root else None
    session = ProjectSession(project_root=_root)

    @asynccontextmanager
    async def _lifespan(_app: FastMCP):  # type: ignore[type-arg]
        editor_bridge._set_expected_project_root_provider(
            lambda: str(session.project_root) if session.project_root is not None else None
        )
        try:
            yield
        finally:
            editor_bridge._set_expected_project_root_provider(None)
            await session.shutdown()

    server = FastMCP(
        name="prefab-sentinel",
        instructions=(
            "Unity asset inspection and editing tools. "
            "Use activate_project to set scope, "
            "get_unity_symbols to explore asset structure, "
            "find_unity_symbol to locate specific objects by name, "
            "and validate_refs to check for broken references."
        ),
        lifespan=_lifespan,
    )

    # Register tool modules
    register_session_tools(server, session)
    register_symbol_tools(server, session)
    register_set_property_tools(server, session)
    register_component_tools(server, session)
    register_copy_component_fields_tool(server, session)
    register_validation_tools(server, session)
    register_patch_tools(server, session)
    register_editor_view_tools(server)
    register_editor_write_tools(server)
    register_editor_batch_tools(server)
    register_editor_ops_tools(server)
    register_editor_advanced_tools(server)
    register_editor_exec_tools(server)
    register_editor_geometry_tools(server)
    register_editor_udonsharp_tools(server)
    # Issue #236 / #243: dedicated registration hooks for the Prefab
    # Stage open/close primitives and the three AnimationClip surfaces.
    register_editor_prefab_stage_tools(server)
    register_editor_animation_tools(server)

    return server


def main() -> None:
    """Entry point for the MCP server."""
    import argparse

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
    args = parser.parse_args()

    server = create_server(project_root=args.project_root)
    server.run(transport=args.transport)


if __name__ == "__main__":
    main()
