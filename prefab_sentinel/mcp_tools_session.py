"""MCP tools for session lifecycle management."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP

from prefab_sentinel.contracts import Severity, max_severity
from prefab_sentinel.editor_bridge import (
    bridge_status,
    get_last_bridge_version,
    send_action,
)
from prefab_sentinel.editor_status_blockers import (
    BRIDGE_CONNECTION,
    classify_status_blockers,
    classify_tool_error_blocker,
)
from prefab_sentinel.session import InvalidProjectRootError, ProjectSession
from prefab_sentinel.wsl_compat import to_wsl_path

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


def _coerce_bridge_severity(raw: object) -> Severity:
    if isinstance(raw, str):
        try:
            return Severity(raw)
        except ValueError:
            return Severity.INFO
    return Severity.INFO


def _bridge_diagnostic_to_session_wire(
    diagnostic: dict[str, Any],
    bridge_severity: Severity,
) -> dict[str, Any]:
    raw_severity = diagnostic.get("severity")
    severity = (
        _coerce_bridge_severity(raw_severity)
        if isinstance(raw_severity, str)
        else bridge_severity
    )

    raw_code = diagnostic.get("code")
    code = raw_code if isinstance(raw_code, str) and raw_code else "BRIDGE_DIAGNOSTIC"
    raw_message = diagnostic.get("message")
    raw_detail = diagnostic.get("detail")
    message = (
        raw_message
        if isinstance(raw_message, str) and raw_message
        else raw_detail
        if isinstance(raw_detail, str) and raw_detail
        else code
    )
    data = {
        key: diagnostic[key]
        for key in (
            "path",
            "location",
            "evidence",
            "blocker_class",
            "state_source",
            "suggested_next_action",
        )
        if key in diagnostic and diagnostic[key] not in (None, "")
    }
    return _build_session_diagnostic(code, message, severity=severity, data=data)


def _bridge_diagnostics_to_session_wire(
    bridge_resp: dict[str, Any],
) -> list[dict[str, Any]]:
    bridge_severity = _coerce_bridge_severity(bridge_resp.get("severity"))
    raw_diagnostics = bridge_resp.get("diagnostics")
    if not isinstance(raw_diagnostics, list):
        return []
    return [
        _bridge_diagnostic_to_session_wire(diagnostic, bridge_severity)
        for diagnostic in raw_diagnostics
        if isinstance(diagnostic, dict)
    ]


def _operator_context(payload: dict[str, Any]) -> dict[str, Any]:
    context = payload.get("operator_context")
    return context if isinstance(context, dict) else {}


def _context_string(context: dict[str, Any], key: str) -> str | None:
    value = context.get(key)
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None


def _project_root_identity(root: str) -> str:
    return str(Path(to_wsl_path(root)).expanduser().resolve())


def _project_roots_consistent(
    expected_project_root: str | None,
    actual_project_root: str | None,
) -> bool | None:
    if expected_project_root is None or actual_project_root is None:
        return None
    return _project_root_identity(expected_project_root) == _project_root_identity(
        actual_project_root
    )


def _copy_editor_state_summary(status: dict[str, Any], editor_state: object) -> None:
    if not isinstance(editor_state, dict):
        return
    for key in (
        "state_source",
        "is_playing",
        "is_will_change_playmode",
        "is_compiling",
        "is_building_player",
        "active_stage_kind",
        "active_scene_path",
        "active_scene_name",
        "prefab_stage_asset_path",
        "prefab_stage_root_name",
        "prefab_stage_is_dirty",
        "open_scenes",
        "has_unsaved_changes",
        "dirty_scene_paths",
        "dirty_prefab_paths",
        "dirty_material_paths",
        "dirty_asset_paths",
    ):
        if key in editor_state:
            status[key] = editor_state[key]


def _copy_operator_context(status: dict[str, Any], context: dict[str, Any]) -> None:
    actual_project_root = _context_string(context, "project_root")
    if actual_project_root is not None:
        status["actual_project_root"] = actual_project_root
    else:
        status["actual_project_root"] = None
    for key in (
        "bridge_session_id",
        "bridge_instance_id",
        "plugin_version",
        "bridge_version",
    ):
        value = _context_string(context, key)
        if value is not None:
            status[key] = value


def _append_project_root_mismatch_diagnostic(
    diagnostics: list[dict[str, Any]],
    *,
    expected_project_root: str,
    actual_project_root: str | None,
) -> None:
    data: dict[str, Any] = {"expected_project_root": expected_project_root}
    if actual_project_root is not None:
        data["actual_project_root"] = actual_project_root
        message = (
            "Editor bridge reached Unity project root "
            f"{actual_project_root!r}, expected {expected_project_root!r}."
        )
    else:
        message = (
            "Editor bridge response did not include the actual Unity project root "
            f"required to verify expected root {expected_project_root!r}."
        )
    diagnostics.append(
        _build_session_diagnostic(
            "EDITOR_BRIDGE_PROJECT_ROOT_MISMATCH",
            message,
            severity=Severity.WARNING,
            data=data,
        )
    )


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
            refresh_response = send_action(action="refresh_asset_database")
        except Exception:
            logger.debug("Post-deploy asset database refresh failed", exc_info=True)
        else:
            if refresh_response.get("success") is not True:
                return refresh_response

        return {
            "success": True,
            "severity": _compose_envelope_severity(diagnostics),
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
        status["actual_project_root"] = None
        status["project_root_consistent"] = None

        current_bridge = bridge_status()
        editor_state: dict[str, Any] | None = None
        blockers: list[dict[str, Any]] = []
        if current_bridge.get("connected"):
            bridge_resp = send_action(
                action="get_editor_state",
                expected_project_root=None,
            )
            if bridge_resp.get("success"):
                diagnostics.extend(_bridge_diagnostics_to_session_wire(bridge_resp))
                raw_editor_state = bridge_resp.get("data", {}).get("editor_state")
                editor_state = raw_editor_state if isinstance(raw_editor_state, dict) else None
                context = _operator_context(bridge_resp)
                _copy_operator_context(status, context)
                _copy_editor_state_summary(status, editor_state)
                actual_project_root = status.get("actual_project_root")
                consistent = _project_roots_consistent(
                    status.get("expected_project_root"),
                    actual_project_root if isinstance(actual_project_root, str) else None,
                )
                status["project_root_consistent"] = consistent
                expected_root = status.get("expected_project_root")
                if expected_root is not None and consistent is not True:
                    _append_project_root_mismatch_diagnostic(
                        diagnostics,
                        expected_project_root=expected_root,
                        actual_project_root=(
                            actual_project_root
                            if isinstance(actual_project_root, str)
                            else None
                        ),
                    )
                blockers.extend(
                    classify_status_blockers(status, current_bridge, editor_state)
                )
            else:
                blocker = classify_tool_error_blocker(bridge_resp)
                if blocker is None:
                    blocker = {
                        "blocker_class": BRIDGE_CONNECTION,
                        "state_source": "bridge_transport",
                        "message": "get_editor_state did not return live Editor state.",
                        "suggested_next_action": (
                            "Confirm Unity is running and the PrefabSentinel Editor "
                            "Bridge watcher is active."
                        ),
                    }
                blockers.append(blocker)
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
                            "blocker_class": blocker["blocker_class"],
                            "state_source": blocker["state_source"],
                            "suggested_next_action": blocker[
                                "suggested_next_action"
                            ],
                        },
                    )
                )
        else:
            blockers.extend(classify_status_blockers(status, current_bridge, None))
        status["editor_state"] = editor_state
        status["blockers"] = blockers

        severity = _compose_envelope_severity(diagnostics)
        if blockers and severity == Severity.INFO.value:
            severity = Severity.WARNING.value

        return {
            "success": True,
            "severity": severity,
            "code": "SESSION_STATUS",
            "message": "Current session status",
            "data": status,
            "diagnostics": diagnostics,
        }
