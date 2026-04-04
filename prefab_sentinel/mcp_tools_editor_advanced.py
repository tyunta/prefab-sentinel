"""MCP tools for VRC SDK upload and runtime reflection."""

from __future__ import annotations

import json
from typing import Any

from mcp.server.fastmcp import FastMCP

from prefab_sentinel.editor_bridge import send_action
from prefab_sentinel.json_io import load_json

__all__ = ["register_editor_advanced_tools"]

_VALID_REFLECT_ACTIONS = frozenset({"search", "get_type", "get_member"})
_VALID_REFLECT_SCOPES = frozenset({"unity", "packages", "project", "all"})


def _reflect_error(code: str, message: str) -> dict[str, Any]:
    return {
        "success": False,
        "severity": "error",
        "code": code,
        "message": message,
        "data": {},
        "diagnostics": [],
    }


def register_editor_advanced_tools(server: FastMCP) -> None:
    """Register VRC SDK and reflection tools on *server*."""

    @server.tool()
    def vrcsdk_upload(
        target_type: str,
        asset_path: str,
        blueprint_id: str,
        platforms: list[str] | None = None,
        description: str = "",
        tags: str = "",
        release_status: str = "",
        confirm: bool = False,
        change_reason: str = "",
        timeout_sec: int = 600,
    ) -> dict[str, Any]:
        """Build and upload an avatar or world to VRChat via VRC SDK.

        Existing asset update only (blueprint_id required).

        Two-phase workflow:
        - confirm=False (default): validates SDK login, asset, descriptor.
        - confirm=True: builds and uploads to VRChat.

        Args:
            target_type: "avatar" or "world".
            asset_path: Prefab path (avatar) or Scene path (world).
            blueprint_id: Existing VRC asset ID (e.g. "avtr_xxx..."). Required.
            platforms: List of target platforms (default: ["windows"]).
                Valid values: "windows", "android", "ios".
            description: Description text (empty = no change).
            tags: JSON array of tag strings (empty = no change).
            release_status: "public" or "private" (empty = no change).
            confirm: Set True to build + upload (False = validation only).
            change_reason: Required when confirm=True. Audit log reason.
            timeout_sec: Bridge timeout in seconds (default: 600).
                For multi-platform, recommend 600 * len(platforms).
        """
        if platforms is None:
            platforms = ["windows"]

        _valid_platforms = {"windows", "android", "ios"}
        if not platforms:
            return {
                "success": False,
                "severity": "error",
                "code": "VRCSDK_INVALID_PLATFORMS",
                "message": "platforms must not be empty",
                "data": {},
                "diagnostics": [],
            }
        invalid = [p for p in platforms if p not in _valid_platforms]
        if invalid:
            return {
                "success": False,
                "severity": "error",
                "code": "VRCSDK_INVALID_PLATFORMS",
                "message": f"Invalid platform(s): {invalid}. Valid: {sorted(_valid_platforms)}",
                "data": {},
                "diagnostics": [],
            }
        if len(platforms) != len(set(platforms)):
            return {
                "success": False,
                "severity": "error",
                "code": "VRCSDK_INVALID_PLATFORMS",
                "message": f"Duplicate platform(s) in: {platforms}",
                "data": {},
                "diagnostics": [],
            }

        if confirm and not change_reason:
            return {
                "success": False,
                "severity": "error",
                "code": "VRCSDK_REASON_REQUIRED",
                "message": "change_reason is required when confirm=True",
                "data": {},
                "diagnostics": [],
            }

        result = send_action(
            action="vrcsdk_upload",
            timeout_sec=timeout_sec,
            target_type=target_type,
            asset_path=asset_path,
            blueprint_id=blueprint_id,
            platforms=json.dumps(platforms),
            description=description,
            tags=tags,
            release_status=release_status,
            confirm=confirm,
        )

        data = result.setdefault("data", {})
        if isinstance(data, dict):
            prj = data.pop("platform_results_json", "")
            if prj:
                data["platform_results"] = load_json(prj)
            if not confirm:
                data["platforms"] = platforms

        return result

    @server.tool()
    def editor_reflect(
        action: str,
        class_name: str = "",
        member_name: str = "",
        query: str = "",
        scope: str = "all",
    ) -> dict[str, Any]:
        """Inspect Unity C# APIs via runtime reflection.

        Three actions:
        - search: Find types by name. Returns ranked matches (exact > starts_with > contains), max 25.
        - get_type: List members (methods/properties/fields/events), base class, interfaces, extensions.
        - get_member: Detailed info for a specific member (signature, overloads, type, etc.).

        Args:
            action: "search", "get_type", or "get_member".
            class_name: Type name (required for get_type, get_member). Short or fully qualified.
            member_name: Member name (required for get_member).
            query: Search query string (required for search).
            scope: Assembly scope filter for search: "unity", "packages", "project", or "all".
        """
        if action not in _VALID_REFLECT_ACTIONS:
            return _reflect_error(
                "EDITOR_REFLECT_UNKNOWN_ACTION",
                f"Unknown action: '{action}'. Supported: search, get_type, get_member.",
            )

        if scope not in _VALID_REFLECT_SCOPES:
            return _reflect_error(
                "EDITOR_REFLECT_INVALID_SCOPE",
                f"Invalid scope: '{scope}'. Supported: unity, packages, project, all.",
            )

        if action == "search" and not query:
            return _reflect_error(
                "EDITOR_REFLECT_MISSING_PARAM",
                "'query' is required for search.",
            )

        if action in ("get_type", "get_member") and not class_name:
            return _reflect_error(
                "EDITOR_REFLECT_MISSING_PARAM",
                "'class_name' is required for get_type/get_member.",
            )

        if action == "get_member" and not member_name:
            return _reflect_error(
                "EDITOR_REFLECT_MISSING_PARAM",
                "'member_name' is required for get_member.",
            )

        result = send_action(
            action="editor_reflect",
            reflect_action=action,
            query=query,
            scope=scope,
            class_name=class_name,
            member_name=member_name,
        )

        if not result.get("success"):
            return result

        data = result.setdefault("data", {})
        if isinstance(data, dict):
            raw_json = data.pop("reflect_result_json", "")
            if raw_json:
                try:
                    data.update(load_json(raw_json))
                except json.JSONDecodeError as exc:
                    return _reflect_error(
                        "EDITOR_REFLECT_PARSE",
                        f"Failed to parse reflect_result_json: {exc}",
                    )

        return result
