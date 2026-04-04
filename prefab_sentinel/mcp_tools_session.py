"""MCP tools for session lifecycle management."""

from __future__ import annotations

import logging
from typing import Any

from mcp.server.fastmcp import FastMCP

from prefab_sentinel.editor_bridge import get_last_bridge_version, send_action
from prefab_sentinel.mcp_helpers import KNOWLEDGE_URI_PREFIX
from prefab_sentinel.session import InvalidProjectRootError, ProjectSession

__all__ = ["register_session_tools"]

logger = logging.getLogger(__name__)


def register_session_tools(server: FastMCP, session: ProjectSession) -> None:
    """Register session management tools on *server*."""

    @server.tool()
    async def activate_project(
        scope: str,
        project_root: str = "",
    ) -> dict[str, Any]:
        """Set the project scope and warm caches for subsequent requests.

        Call this once at the start of a session to set the working scope.
        Subsequent tool calls will be faster due to cached GUID index and
        script name map.

        Args:
            scope: Path to the Assets subdirectory to work with
                (e.g. "Assets/Tyunta/SoulLinkerSystem").
            project_root: Unity project root directory. Optional.
                Priority: this argument > UNITYTOOL_UNITY_PROJECT_PATH env var
                > auto-detect from scope path.
        """
        try:
            result = await session.activate(
                scope,
                project_root=project_root or None,
            )
        except InvalidProjectRootError as exc:
            return {
                "success": False,
                "severity": "error",
                "code": "INVALID_PROJECT_ROOT",
                "message": str(exc),
                "data": {},
                "diagnostics": [],
            }
        result["suggested_reads"] = session.suggest_reads()
        result["knowledge_hint"] = (
            "Other knowledge files available via Glob('knowledge/*.md') "
            f"or MCP Resources ({KNOWLEDGE_URI_PREFIX})"
        )
        diagnostics: list[dict[str, Any]] = [
            {
                "message": (
                    f"Scope '{scope}' will be used as default for: "
                    "validate_refs, find_referencing_assets, "
                    "validate_field_rename, check_field_coverage."
                ),
                "severity": "info",
            },
        ]
        bridge_diag = session.check_bridge_version()
        if bridge_diag:
            diagnostics.append(bridge_diag)
        return {
            "success": True,
            "severity": "info",
            "code": "SESSION_ACTIVATED",
            "message": f"Project activated with scope: {scope}",
            "data": result,
            "diagnostics": diagnostics,
        }

    @server.tool()
    def deploy_bridge(
        target_dir: str = "",
    ) -> dict[str, Any]:
        """Deploy or update Bridge C# files to the Unity project.

        Copies Bridge C# and .asmdef files to the target directory. Source
        files are read from _bridge_files/ (wheel install) or tools/unity/
        (source tree). Cleans up old Bridge files from the parent directory
        to prevent CS0101 duplicate definition errors.
        Triggers editor_refresh after copying to reload assets.

        Args:
            target_dir: Target directory in Unity project.
                Default: {project_root}/Assets/Editor/PrefabSentinel/
        """
        import shutil
        from pathlib import Path as _Path

        project_root = session.project_root
        if project_root is None:
            return {
                "success": False,
                "severity": "error",
                "code": "DEPLOY_NO_PROJECT",
                "message": "No project activated. Call activate_project first.",
                "data": {},
                "diagnostics": [],
            }

        if not target_dir:
            target_dir = str(project_root / "Assets" / "Editor" / "PrefabSentinel")

        target_path = _Path(target_dir).resolve()

        project_resolved = project_root.resolve()
        if not target_path.is_relative_to(project_resolved):
            return {
                "success": False,
                "severity": "error",
                "code": "DEPLOY_OUTSIDE_PROJECT",
                "message": f"target_dir must be within the project: {project_resolved}",
                "data": {},
                "diagnostics": [],
            }

        target_path.mkdir(parents=True, exist_ok=True)

        plugin_tools = _Path(__file__).parent / "_bridge_files"
        if not plugin_tools.is_dir():
            plugin_tools = _Path(__file__).parent.parent / "tools" / "unity"
        if not plugin_tools.is_dir():
            return {
                "success": False,
                "severity": "error",
                "code": "DEPLOY_SOURCE_NOT_FOUND",
                "message": "Bridge source directory not found. "
                "Ensure tools/unity/ exists (source) or package includes "
                "_bridge_files/ (wheel install).",
                "data": {},
                "diagnostics": [],
            }

        diagnostics: list[dict[str, Any]] = []

        removed_old_files: list[str] = []
        parent_dir = target_path.parent
        if parent_dir.is_dir():
            for old_file in sorted(parent_dir.glob("PrefabSentinel.*.cs")):
                old_file.unlink()
                removed_old_files.append(old_file.name)
                meta_file = _Path(str(old_file) + ".meta")
                if meta_file.exists():
                    meta_file.unlink()
                    removed_old_files.append(meta_file.name)

        if removed_old_files:
            diagnostics.append({
                "severity": "warning",
                "message": (
                    f"Removed {len(removed_old_files)} old Bridge file(s) from "
                    f"{parent_dir} to prevent CS0101 duplicate definitions"
                ),
            })

        old_version = session.detect_bridge_version()

        removed_stale_files: list[str] = []
        for stale in sorted(target_path.iterdir()):
            if stale.is_file():
                stale.unlink()
                removed_stale_files.append(stale.name)

        if removed_stale_files:
            diagnostics.append({
                "severity": "info",
                "message": f"Cleared {len(removed_stale_files)} file(s) from {target_dir} before redeploy",
            })

        copied_files: list[str] = []

        for src_file in sorted(
            list(plugin_tools.glob("*.cs")) + list(plugin_tools.glob("*.asmdef"))
        ):
            dest = target_path / src_file.name
            shutil.copy2(src_file, dest)
            copied_files.append(src_file.name)

        new_version = session.detect_bridge_version()

        try:
            send_action(action="refresh_asset_database")
        except Exception:
            logger.debug("Post-deploy asset database refresh failed", exc_info=True)

        return {
            "success": True,
            "severity": "info",
            "code": "DEPLOY_OK",
            "message": f"Deployed {len(copied_files)} files to {target_dir}",
            "data": {
                "copied_files": copied_files,
                "removed_old_files": removed_old_files,
                "removed_stale_files": removed_stale_files,
                "old_version": old_version,
                "new_version": new_version,
                "target_dir": target_dir,
            },
            "diagnostics": diagnostics,
        }

    @server.tool()
    def get_project_status() -> dict[str, Any]:
        """Show current session state: cached items, scope, project root.

        Use this to check whether caches are warm or if activate_project
        needs to be called. Also reports bridge version mismatch if detected.
        """
        from importlib.metadata import version as pkg_version

        python_version = pkg_version("prefab-sentinel")
        bridge_ver = get_last_bridge_version()

        diagnostics: list[dict[str, str]] = []
        if bridge_ver and bridge_ver != python_version:
            diagnostics.append({
                "detail": f"Bridge version mismatch: Bridge={bridge_ver}, Python={python_version}. "
                          "Update Bridge C# files and run editor_recompile.",
                "evidence": f"bridge_version={bridge_ver}, package_version={python_version}",
            })

        status = session.status()
        status["python_version"] = python_version
        status["bridge_version"] = bridge_ver

        return {
            "success": True,
            "severity": "warning" if diagnostics else "info",
            "code": "SESSION_STATUS",
            "message": "Current session status",
            "data": status,
            "diagnostics": diagnostics,
        }
