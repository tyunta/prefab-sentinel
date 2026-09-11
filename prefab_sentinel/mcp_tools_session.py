"""MCP tools for session lifecycle management."""

from __future__ import annotations

import logging
import os
import time
import uuid
from pathlib import Path
from typing import Any

from mcp.server import MCPServer

import prefab_sentinel.bridge_deploy as bridge_deploy
from prefab_sentinel.bridge_constants import (
    BRIDGE_INSTANCE_ID_ENV,
    BRIDGE_WATCH_DIR_ENV,
)
from prefab_sentinel.bridge_watch_identity import (
    WatchIdentityObservation,
    WatchIdentityTracker,
)
from prefab_sentinel.contracts import Severity, max_severity
from prefab_sentinel.editor_bridge import (
    bridge_status,
    get_last_bridge_version,
    send_action,
    send_private_deploy_action,
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
    code = (
        raw_code
        if raw_code == "EDITOR_STATE_ENUMERATION_LIMITED"
        else "BRIDGE_DIAGNOSTIC"
    )
    logger.warning(
        (
            "Private Bridge status diagnostic %s: "
            "message=%s detail=%s path=%s evidence=%s"
        ),
        raw_code,
        diagnostic.get("message"),
        diagnostic.get("detail"),
        diagnostic.get("path"),
        diagnostic.get("evidence"),
    )

    stable_messages = {
        "EDITOR_STATE_ENUMERATION_LIMITED": (
            "Unity Editor state enumeration was limited."
        ),
        "BRIDGE_DIAGNOSTIC": "BRIDGE_DIAGNOSTIC",
    }
    data: dict[str, Any] = {}
    raw_location = diagnostic.get("location")
    if raw_location in {
        "active_scene",
        "prefab_stage",
        "open_scenes",
        "dirty_scene_paths",
        "dirty_prefab_paths",
        "dirty_material_paths",
        "dirty_asset_paths",
    }:
        data["location"] = raw_location
    return _build_session_diagnostic(
        code,
        stable_messages[code],
        severity=severity,
        data=data,
    )


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


def _observe_current_bridge_version(project_root: Path) -> str | None:
    expected_instance_id = os.environ.get(BRIDGE_INSTANCE_ID_ENV, "").strip()
    if not expected_instance_id:
        return None

    response = send_action(
        action="get_editor_state",
        expected_project_root=str(project_root),
    )
    if response.get("success") is not True:
        return None

    context = _operator_context(response)
    if _context_string(context, "bridge_instance_id") != expected_instance_id:
        return None
    return _context_string(context, "bridge_version")


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


def _copy_editor_state_summary(
    status: dict[str, Any],
    editor_state: object,
) -> dict[str, Any] | None:
    if not isinstance(editor_state, dict):
        return None

    def is_public_asset_path(value: object) -> bool:
        if not isinstance(value, str):
            return False
        if not value:
            return True
        if "\\" in value or not value.startswith("Assets/"):
            return False
        parts = value.split("/")
        return (
            len(parts) > 1
            and parts[0] == "Assets"
            and all(part not in {"", ".", ".."} for part in parts[1:])
        )

    projected: dict[str, Any] = {}
    for key in (
        "state_source",
        "unity_version",
        "active_stage_kind",
        "active_scene_name",
        "prefab_stage_root_name",
    ):
        value = editor_state.get(key)
        if isinstance(value, str):
            projected[key] = value

    raw_packages = editor_state.get("required_packages")
    if isinstance(raw_packages, list):
        package_names = (
            "com.vrchat.base",
            "com.vrchat.worlds",
            "udonsharp",
        )
        packages_by_name = {
            item.get("name"): item
            for item in raw_packages
            if isinstance(item, dict) and item.get("name") in package_names
        }
        projected["required_packages"] = [
            {
                "name": name,
                "ready": item.get("ready") is True,
                "version": (
                    item.get("version")
                    if isinstance(item.get("version"), str)
                    else ""
                ),
            }
            for name in package_names
            if isinstance((item := packages_by_name.get(name)), dict)
        ]

    for key in (
        "is_playing",
        "is_will_change_playmode",
        "is_compiling",
        "is_building_player",
        "prefab_stage_is_dirty",
        "has_unsaved_changes",
    ):
        value = editor_state.get(key)
        if isinstance(value, bool):
            projected[key] = value

    for key in (
        "active_scene_path",
        "prefab_stage_asset_path",
    ):
        value = editor_state.get(key)
        if is_public_asset_path(value):
            projected[key] = value

    for key in (
        "dirty_scene_paths",
        "dirty_prefab_paths",
        "dirty_material_paths",
        "dirty_asset_paths",
    ):
        value = editor_state.get(key)
        if isinstance(value, list):
            projected[key] = [
                item for item in value if is_public_asset_path(item)
            ]

    raw_open_scenes = editor_state.get("open_scenes")
    if isinstance(raw_open_scenes, list):
        open_scenes: list[dict[str, Any]] = []
        for raw_scene in raw_open_scenes:
            if not isinstance(raw_scene, dict):
                continue
            path = raw_scene.get("path")
            if not is_public_asset_path(path):
                continue
            scene: dict[str, Any] = {"path": path}
            name = raw_scene.get("name")
            if isinstance(name, str):
                scene["name"] = name
            is_dirty = raw_scene.get("is_dirty")
            if isinstance(is_dirty, bool):
                scene["is_dirty"] = is_dirty
            open_scenes.append(scene)
        projected["open_scenes"] = open_scenes

    status.update(projected)
    return projected


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


def _watch_identity_bridge_projection(
    observation: WatchIdentityObservation,
) -> dict[str, Any] | None:
    if observation.state == "match":
        return None
    if observation.state == "mismatch":
        return {
            "connected": False,
            "connection_state": "misconfigured",
            "code": "EDITOR_BRIDGE_WATCH_DIR_MISMATCH",
            "blocker_class": "watch_dir",
            "suggested_next_action": (
                "Use the same watch directory for Codex and the Unity Editor Bridge."
            ),
        }

    transient = observation.reason == "status_transient"
    return {
        "connected": False,
        "connection_state": "unavailable",
        "code": (
            "EDITOR_BRIDGE_STATUS_TRANSIENT"
            if transient
            else "EDITOR_BRIDGE_STATUS_UNAVAILABLE"
        ),
        "blocker_class": None if transient else BRIDGE_CONNECTION,
        "suggested_next_action": (
            None
            if transient
            else (
                "Confirm Unity is running and the PrefabSentinel Editor "
                "Bridge watcher is active."
            )
        ),
    }


def register_session_tools(server: MCPServer, session: ProjectSession) -> None:
    """Register session management tools on *server*."""

    watch_identity_tracker = WatchIdentityTracker()

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

        watch_identity_tracker.reset()
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
        """Deploy a complete Bridge bundle through the safe transaction path."""
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

        target_path = (
            Path(to_wsl_path(target_dir))
            if target_dir
            else project_root / bridge_deploy.DEFAULT_TARGET
        )
        plugin_tools = Path(__file__).parent / "_bridge_files"
        if not plugin_tools.is_dir():
            plugin_tools = Path(__file__).parent.parent / "tools" / "unity"

        run_id = uuid.uuid4().hex
        acquired_lock = bridge_deploy._acquire_deploy_lock(
            project_root,
            run_id,
        )
        if not isinstance(acquired_lock, int):
            return acquired_lock.to_dict()
        try:
            prepared = bridge_deploy.prepare_bridge_deploy(
                project_root,
                target_path,
                plugin_tools,
                run_id=run_id,
            )
            if not isinstance(prepared, bridge_deploy.DeployPreparation):
                return prepared.to_dict()

            is_fresh_target = (
                prepared.previous_manifest is None
                and not prepared.ownership.managed_entries
            )
            if is_fresh_target:
                promotion = bridge_deploy.install_fresh_target(
                    prepared,
                    acquired_lock=acquired_lock,
                )
                return bridge_deploy.complete_bridge_deploy(
                    prepared,
                    promotion,
                    lock_held=True,
                ).to_dict()

            target_already_current = (
                not prepared.ownership.legacy_import
                and prepared.previous_manifest == prepared.source_manifest
                and bridge_deploy.verify_deployed_target(
                    prepared.target_path,
                    prepared.source_manifest,
                ).success
                and _observe_current_bridge_version(project_root)
                == prepared.source_manifest.bridge_version
            )
            if target_already_current:
                return bridge_deploy.complete_bridge_deploy(
                    prepared,
                    bridge_deploy.ExistingTargetReuse(),
                    lock_held=True,
                ).to_dict()

            def promote_manifest(
                manifest: bridge_deploy.BridgeBundleManifest,
            ) -> dict[str, Any]:
                try:
                    return send_private_deploy_action(
                        action="promote_bridge_bundle",
                        deploy_run_id=prepared.run_id,
                        deploy_target_path=prepared.target_path.relative_to(
                            project_root
                        ).as_posix(),
                        deploy_transaction_path=(
                            prepared.transaction_path.relative_to(
                                project_root
                            ).as_posix()
                        ),
                        deploy_manifest_sha256=manifest.sha256,
                        deploy_bridge_version=manifest.bridge_version,
                    )
                except Exception:
                    logger.exception(
                        "Private Bridge promotion transport failed"
                    )
                    return {
                        "success": False,
                        "severity": "error",
                        "code": "EDITOR_BRIDGE_TRANSPORT_EXCEPTION",
                        "message": "Private Bridge promotion transport failed.",
                        "data": {},
                        "diagnostics": [],
                        "_request_published": True,
                    }

            promotion_response = promote_manifest(
                prepared.source_manifest
            )
            return bridge_deploy.complete_bridge_deploy(
                prepared,
                promotion_response,
                recovery_action=promote_manifest,
                lock_held=True,
            ).to_dict()
        finally:
            bridge_deploy._release_deploy_lock(acquired_lock)

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

        watch_dir_raw = os.environ.get(BRIDGE_WATCH_DIR_ENV, "")
        captured_watch_dir = (
            Path(to_wsl_path(watch_dir_raw)) if watch_dir_raw else None
        )
        public_bridge = bridge_status(captured_watch_dir)
        status = session.status(bridge_projection=public_bridge)
        status["python_version"] = python_version
        status["bridge_version"] = bridge_ver
        status["actual_project_root"] = None
        status["project_root_consistent"] = None

        identity_projection: dict[str, Any] | None = None
        if captured_watch_dir is not None and session.project_root is not None:
            observation = watch_identity_tracker.observe(
                watch_dir=captured_watch_dir,
                project_root=session.project_root,
                now_unix_ms=time.time_ns() // 1_000_000,
            )
            identity_projection = _watch_identity_bridge_projection(observation)
        if identity_projection is not None:
            status["bridge"] = identity_projection
            identity_code = identity_projection["code"]
            if identity_code in {
                "EDITOR_BRIDGE_STATUS_TRANSIENT",
                "EDITOR_BRIDGE_STATUS_UNAVAILABLE",
            }:
                transient = identity_code == "EDITOR_BRIDGE_STATUS_TRANSIENT"
                diagnostics.append(
                    _build_session_diagnostic(
                        identity_code,
                        (
                            "Editor Bridge status is temporarily unavailable "
                            "during a Unity reload."
                            if transient
                            else "Editor Bridge status artifact is unavailable."
                        ),
                        severity=Severity.WARNING,
                        data={
                            "connection_state": "unavailable",
                            "blocker_class": identity_projection["blocker_class"],
                            "suggested_next_action": identity_projection[
                                "suggested_next_action"
                            ],
                        },
                    )
                )

        current_bridge_value = status.get("bridge")
        if not isinstance(current_bridge_value, dict):
            raise TypeError("ProjectSession.status bridge projection must be an object.")
        current_bridge = current_bridge_value
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
                editor_state = _copy_editor_state_summary(status, raw_editor_state)
                context = _operator_context(bridge_resp)
                _copy_operator_context(status, context)
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
                    classify_status_blockers(
                        status,
                        current_bridge,
                        editor_state,
                        configured_watch_dir=captured_watch_dir,
                    )
                )
            else:
                raw_bridge_code = bridge_resp.get("code")
                public_bridge_codes = {
                    "EDITOR_BRIDGE_ERROR",
                    "EDITOR_BRIDGE_PROJECT_ROOT_MISMATCH",
                    "EDITOR_BRIDGE_RESPONSE_PUBLICATION_FAILED",
                    "EDITOR_BRIDGE_RESPONSE_READ",
                    "EDITOR_BRIDGE_TIMEOUT",
                    "EDITOR_BRIDGE_WATCH_DIR_MISSING",
                    "EDITOR_BRIDGE_WATCH_DIR_NOT_FOUND",
                    "EDITOR_BRIDGE_WRITE",
                    "EDITOR_CTRL_HANDLER_EXCEPTION",
                }
                public_bridge_code = (
                    raw_bridge_code
                    if raw_bridge_code in public_bridge_codes
                    else "EDITOR_BRIDGE_ERROR"
                )
                public_bridge_failure = dict(bridge_resp)
                public_bridge_failure["code"] = public_bridge_code
                logger.error(
                    "Private get_editor_state failure code=%s message=%s data=%r diagnostics=%r",
                    raw_bridge_code,
                    bridge_resp.get("message"),
                    bridge_resp.get("data"),
                    bridge_resp.get("diagnostics"),
                )
                blocker = classify_tool_error_blocker(public_bridge_failure)
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
                            f"{public_bridge_code}"
                        ),
                        severity=Severity.WARNING,
                        data={
                            "bridge_code": public_bridge_code,
                            "blocker_class": blocker["blocker_class"],
                            "state_source": blocker["state_source"],
                            "suggested_next_action": blocker[
                                "suggested_next_action"
                            ],
                        },
                    )
                )
        else:
            blockers.extend(classify_status_blockers(
                status,
                current_bridge,
                None,
                configured_watch_dir=captured_watch_dir,
            ))
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
