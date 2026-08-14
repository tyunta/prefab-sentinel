"""MCP registration for last-saved Inspector profile workflows."""

from __future__ import annotations

from typing import Any

from mcp.server import MCPServer

from prefab_sentinel.session import ProjectSession

__all__ = ["register_inspector_profile_tools"]


def register_inspector_profile_tools(server: MCPServer, session: ProjectSession) -> None:
    @server.tool()
    def inspect_serialized_surface(
        asset_path: str,
        symbol_path: str | None = None,
        include_override_origin: bool = False,
    ) -> dict[str, Any]:
        """Inspect the last-saved serialized surface of a Unity asset target."""
        from prefab_sentinel.inspector_profiles.application import InspectorProfileApplication

        return InspectorProfileApplication(session).inspect_serialized_surface(
            asset_path,
            symbol_path,
            include_override_origin,
        )

    @server.tool()
    def inspect_with_profile(
        asset_path: str,
        view_name: str,
        symbol_path: str | None = None,
        include_override_origin: bool = False,
    ) -> dict[str, Any]:
        """Render a named inspector view for a Unity asset using its matching profile."""
        if not view_name:
            return {
                "success": False,
                "severity": "error",
                "code": "INSPECTOR_VIEW_NAME_REQUIRED",
                "message": "view_name is required.",
                "data": {},
                "diagnostics": [],
            }
        from prefab_sentinel.inspector_profiles.application import InspectorProfileApplication

        return InspectorProfileApplication(session).inspect_with_profile(
            asset_path,
            view_name,
            symbol_path,
            include_override_origin,
        )

    @server.tool()
    def validate_inspector_profile(
        profile_path: str,
        asset_path: str,
        symbol_path: str | None = None,
    ) -> dict[str, Any]:
        """Validate an inspector profile against a Unity asset's serialized surface."""
        from prefab_sentinel.inspector_profiles.application import InspectorProfileApplication

        return InspectorProfileApplication(session).validate_inspector_profile(
            profile_path,
            asset_path,
            symbol_path,
        )
