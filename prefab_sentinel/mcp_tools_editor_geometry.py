from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP

from prefab_sentinel.editor_bridge import send_action

__all__ = [
    "editor_get_bounds",
    "editor_get_transform",
    "editor_measure_distance",
    "register_editor_geometry_tools",
]


def editor_get_transform(hierarchy_path: str) -> dict[str, Any]:
    return send_action(action="get_transform", hierarchy_path=hierarchy_path)


def editor_get_bounds(
    hierarchy_path: str,
    source: str = "auto",
    include_children: bool = True,
) -> dict[str, Any]:
    return send_action(
        action="get_bounds",
        hierarchy_path=hierarchy_path,
        bounds_source=source,
        include_children=include_children,
    )


def editor_measure_distance(
    a: str,
    b: str,
    mode: str = "pivot",
    bounds_source: str = "auto",
) -> dict[str, Any]:
    return send_action(
        action="measure_distance",
        hierarchy_path=a,
        target_path=b,
        distance_mode=mode,
        bounds_source=bounds_source,
    )


def register_editor_geometry_tools(server: FastMCP) -> None:
    server.tool()(editor_get_transform)
    server.tool()(editor_get_bounds)
    server.tool()(editor_measure_distance)
