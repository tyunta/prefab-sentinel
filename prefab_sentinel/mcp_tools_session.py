"""MCP tools for session lifecycle management."""

from __future__ import annotations

import logging
from typing import Any

from mcp.server.fastmcp import FastMCP

from prefab_sentinel.contracts import Severity, max_severity
from prefab_sentinel.editor_bridge import (
    bridge_status,
    get_last_bridge_version,
    send_action,
)
from prefab_sentinel.mcp_helpers import KNOWLEDGE_URI_PREFIX
from prefab_sentinel.session import InvalidProjectRootError, ProjectSession

__all__ = ["register_session_tools"]

logger = logging.getLogger(__name__)


def _build_session_diagnostic(
    code: str,
    message: str,
    *,
    severity: Severity,
    data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a session-level diagnostic in the unified MCP wire shape.

    Issue #304: ``deploy_bridge`` and ``get_project_status`` previously
    emitted ad-hoc diagnostic dicts in three different shapes
    (``{severity, message}``, ``{severity, detail, evidence}``, etc.).
    Every session-level diagnostic now flows through this helper so
    the four-key wire contract holds regardless of construction path.
    """
    return {
        "severity": severity.value,
        "code": code,
        "message": message,
        "data": data if data is not None else {},
    }


def _compose_envelope_severity(
    diagnostics: list[dict[str, Any]],
) -> str:
    """Return the highest severity present in *diagnostics* with info floor.

    Issue #244: the activation envelope's overall severity reflects the
    most severe diagnostic carried in the response.  Unknown severity
    strings (defensive) are mapped to ``info`` so an unexpected value
    in a diagnostic does not silently escalate the envelope.
    """
    levels: list[Severity] = []
    for diag in diagnostics:
        raw = diag.get("severity")
        if not isinstance(raw, str):
            levels.append(Severity.INFO)
            continue
        try:
            levels.append(Severity(raw))
        except ValueError:
            levels.append(Severity.INFO)
    return max_severity(levels).value


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
                (e.g. "Assets/MyProject/MyFeature").
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
        # Issue #309: the canonical knowledge channel is the MCP resource
        # URI scheme — wheel-installed deployments have no ``knowledge/``
        # directory in cwd, so the Glob path is a source-tree-checkout
        # affordance only.
        result["knowledge_hint"] = (
            f"Knowledge files are available as MCP resources under {KNOWLEDGE_URI_PREFIX}; "
            "source-tree development checkouts can additionally use Glob('knowledge/*.md')."
        )
        diagnostics: list[dict[str, Any]] = [
            _build_session_diagnostic(
                "SESSION_SCOPE_DEFAULT_NOTE",
                (
                    f"Scope '{scope}' will be used as default for: "
                    "validate_refs, find_referencing_assets, "
                    "validate_field_rename, check_field_coverage."
                ),
                severity=Severity.INFO,
            ),
        ]
        bridge_diag = session.check_bridge_version()
        if bridge_diag:
            diagnostics.append(bridge_diag)
        return {
            "success": True,
            # Issue #244: the envelope's overall severity reflects the
            # most severe diagnostic with an informational floor, so a
            # bridge-version mismatch (or missing bridge) surfaces as a
            # warning on the envelope itself rather than only inside
            # the diagnostics list.
            "severity": _compose_envelope_severity(diagnostics),
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
            diagnostics.append(
                _build_session_diagnostic(
                    "DEPLOY_REMOVED_OLD_BRIDGE_FILES",
                    (
                        f"Removed {len(removed_old_files)} old Bridge file(s) from "
                        f"{parent_dir} to prevent CS0101 duplicate definitions"
                    ),
                    severity=Severity.WARNING,
                    data={
                        "removed_count": len(removed_old_files),
                        "parent_dir": str(parent_dir),
                    },
                )
            )

        old_version = session.detect_bridge_version()

        removed_stale_files: list[str] = []
        for stale in sorted(target_path.iterdir()):
            if stale.is_file():
                stale.unlink()
                removed_stale_files.append(stale.name)

        if removed_stale_files:
            diagnostics.append(
                _build_session_diagnostic(
                    "DEPLOY_CLEARED_STALE_FILES",
                    (
                        f"Cleared {len(removed_stale_files)} file(s) from "
                        f"{target_dir} before redeploy"
                    ),
                    severity=Severity.INFO,
                    data={
                        "removed_count": len(removed_stale_files),
                        "target_dir": str(target_dir),
                    },
                )
            )

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
        needs to be called. Also reports bridge version mismatch if
        detected and (issue #239) the live editor-state snapshot
        (``is_playing`` / ``is_will_change_playmode`` / ``is_compiling``
        / ``is_building_player``) when the bridge is currently
        connected.
        """
        from importlib.metadata import version as pkg_version

        python_version = pkg_version("prefab-sentinel")
        bridge_ver = get_last_bridge_version()

        diagnostics: list[dict[str, Any]] = []
        if bridge_ver and bridge_ver != python_version:
            diagnostics.append(
                _build_session_diagnostic(
                    "BRIDGE_VERSION_MISMATCH",
                    (
                        f"Bridge version mismatch: Bridge={bridge_ver}, "
                        f"Python={python_version}. Update Bridge C# "
                        f"files and run editor_recompile."
                    ),
                    severity=Severity.WARNING,
                    data={
                        "bridge_version": bridge_ver,
                        "package_version": python_version,
                    },
                )
            )

        status = session.status()
        status["python_version"] = python_version
        status["bridge_version"] = bridge_ver

        # Issue #239: surface the live editor-state snapshot when the
        # bridge is currently connected. When disconnected the field is
        # absent (None) — we do not fabricate ``false`` values for a
        # bridge we cannot reach. On a bridge-action failure the field
        # is similarly absent and a warning diagnostic names the
        # underlying bridge code so the caller can act.
        editor_state: dict[str, bool] | None = None
        if bridge_status().get("connected"):
            bridge_resp = send_action(action="get_editor_state")
            if bridge_resp.get("success"):
                editor_state = bridge_resp.get("data", {}).get("editor_state")
            else:
                diagnostics.append(
                    _build_session_diagnostic(
                        "BRIDGE_GET_EDITOR_STATE_FAILED",
                        (
                            f"get_editor_state bridge action failed: "
                            f"{bridge_resp.get('code')}"
                        ),
                        severity=Severity.WARNING,
                        data={
                            "bridge_code": bridge_resp.get("code"),
                            "bridge_message": str(bridge_resp.get("message", "")),
                        },
                    )
                )
        status["editor_state"] = editor_state

        return {
            "success": True,
            "severity": _compose_envelope_severity(diagnostics),
            "code": "SESSION_STATUS",
            "message": "Current session status",
            "data": status,
            "diagnostics": diagnostics,
        }
