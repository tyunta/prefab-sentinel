"""Straight-line controller for explicit local Unity Bridge acceptance."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

from prefab_sentinel.bridge_deploy import BridgeBundleManifest, build_bridge_manifest
from prefab_sentinel.contracts import ToolResponse

from .compile_observer import capture_compile_baseline, wait_for_compile
from .model import (
    AcceptanceEvidence,
    AcceptanceEvidenceSection,
    AcceptancePhaseResult,
    AcceptanceResult,
    AcceptanceSourceIdentity,
    CompileBaseline,
    CompileObservation,
)
from .report import (
    AcceptanceReportReservation,
    publish_acceptance_report,
    reserve_acceptance_report,
)
from .source_identity import collect_source_identity
from .transport import AcceptanceTransport

_RECOMPILE_TIMEOUT_SEC = 120
_ENVIRONMENT_TIMEOUT_SEC = 120
_ENVIRONMENT_STATUS_POLL_INTERVAL_SEC = 1.0
_SMOKE_TIMEOUT_SEC = 300
_EDITOR_STATE_FIELDS = (
    "active_stage_kind",
    "active_scene_name",
    "active_scene_path",
    "open_scenes",
    "has_unsaved_changes",
    "dirty_scene_paths",
    "dirty_prefab_paths",
    "dirty_material_paths",
    "dirty_asset_paths",
    "prefab_stage_is_dirty",
    "prefab_stage_asset_path",
)


@dataclass(frozen=True, slots=True)
class AcceptanceConfig:
    repo_root: Path
    project_root: Path
    scope: str
    target_dir: Path
    watch_dir: str
    unity_log_file: Path
    out_report: str
    confirm_live: bool
    recover_run_id: str = ""


async def run_acceptance(
    config: AcceptanceConfig,
    transport: AcceptanceTransport,
    clock: Callable[[], float],
    sleep: Callable[[float], None],
) -> AcceptanceResult:
    """Run the fixed acceptance phases and publish one terminal report."""
    phases: list[AcceptancePhaseResult] = []

    if not config.confirm_live:
        phases.append(
            _phase(
                "opt_in",
                False,
                "ACCEPTANCE_OPT_IN_REQUIRED",
                _audit_data(config),
            )
        )
        return _failure(
            phases,
            code="ACCEPTANCE_OPT_IN_REQUIRED",
            message="Explicit --confirm-live authorization is required.",
            failed_phase="opt_in",
        )

    phases.append(
        _phase(
            "opt_in",
            True,
            "ACCEPTANCE_OPT_IN_CONFIRMED",
            _audit_data(config),
        )
    )

    config_error = _validate_config(config)
    if config_error is not None:
        phases.append(_phase("config", False, "ACCEPTANCE_CONFIG_ERROR", {}))
        return _failure(
            phases,
            code="ACCEPTANCE_CONFIG_ERROR",
            message="Acceptance configuration is invalid.",
            failed_phase="config",
        )
    phases.append(
        _phase(
            "config",
            True,
            "ACCEPTANCE_CONFIG_OK",
            {"validated": True},
        )
    )

    reserved = reserve_acceptance_report(config.project_root, config.out_report)
    if isinstance(reserved, ToolResponse):
        phases.append(_response_phase("report_reservation", reserved.to_dict()))
        return _failure(
            phases,
            code="ACCEPTANCE_CONFIG_ERROR",
            message="The acceptance report path could not be reserved.",
            failed_phase="report_reservation",
            diagnostics=reserved.to_dict()["diagnostics"],
        )
    reservation = reserved
    phases.append(
        _phase(
            "report_reservation",
            True,
            "ACCEPTANCE_REPORT_RESERVED",
            {"reserved": True},
        )
    )
    phases[0] = _phase(
        "opt_in",
        True,
        "ACCEPTANCE_OPT_IN_CONFIRMED",
        _audit_data(config, paths_validated=True),
    )

    if config.recover_run_id:
        return await _run_recovery_only(
            config,
            transport,
            reservation,
            phases,
            clock=clock,
            sleep=sleep,
        )

    identity_result = collect_source_identity(config.repo_root)
    if isinstance(identity_result, ToolResponse):
        identity_response = identity_result.to_dict()
        phases.append(_response_phase("source_identity", identity_response))
        result = _failure(
            phases,
            code=(
                "ACCEPTANCE_SOURCE_DIRTY"
                if identity_result.code == "ACCEPTANCE_SOURCE_DIRTY"
                else "ACCEPTANCE_CONFIG_ERROR"
            ),
            message=(
                "Managed acceptance source files differ from HEAD."
                if identity_result.code == "ACCEPTANCE_SOURCE_DIRTY"
                else "Acceptance source identity could not be determined."
            ),
            failed_phase="source_identity",
            diagnostics=identity_response["diagnostics"],
        )
        return _publish_or_report_failure(reservation, result, phases)
    identity = identity_result
    phases.append(
        _phase(
            "source_identity",
            True,
            "ACCEPTANCE_SOURCE_IDENTIFIED",
            _source_data(identity),
        )
    )

    connection_identity = _connection_data(transport.connection_identity())
    connection_ok = all(value is not None for value in connection_identity.values())
    phases.append(
        _phase(
            "connection_identity",
            connection_ok,
            (
                "ACCEPTANCE_CONNECTION_IDENTIFIED"
                if connection_ok
                else "ACCEPTANCE_EDITOR_UNAVAILABLE"
            ),
            connection_identity,
        )
    )
    if not connection_ok:
        return _publish_or_report_failure(
            reservation,
            _failure(
                phases,
                code="ACCEPTANCE_EDITOR_UNAVAILABLE",
                message="The worktree-local MCP connection identity is unavailable.",
                failed_phase="connection_identity",
            ),
            phases,
        )

    activated = await transport.activate(str(config.project_root), config.scope)
    phases.append(_response_phase("activate", activated))
    if not _success(activated):
        return _publish_or_report_failure(
            reservation,
            _failure(
                phases,
                code="ACCEPTANCE_EDITOR_UNAVAILABLE",
                message="The Unity project could not be activated.",
                failed_phase="activate",
                diagnostics=_diagnostics(activated),
            ),
            phases,
        )

    initial_status = await transport.project_status()
    initial_editor_state = _editor_snapshot(initial_status)
    phases.append(_response_phase("editor_state", initial_status))
    if (
        not _success(initial_status)
        or _has_blockers(initial_status)
        or initial_editor_state is None
    ):
        return _publish_or_report_failure(
            reservation,
            _failure(
                phases,
                code="ACCEPTANCE_EDITOR_STATE_BLOCKED",
                message="The running Unity Editor is not in an acceptance-safe state.",
                failed_phase="editor_state",
                diagnostics=_diagnostics(initial_status),
            ),
            phases,
        )

    baseline_result = capture_compile_baseline(
        config.project_root,
        config.unity_log_file,
    )
    if isinstance(baseline_result, ToolResponse):
        phases.append(_response_phase("compile_baseline", baseline_result.to_dict()))
        return _publish_or_report_failure(
            reservation,
            _failure(
                phases,
                code="ACCEPTANCE_CONFIG_ERROR",
                message="Compile observation inputs are unavailable.",
                failed_phase="compile_baseline",
                diagnostics=baseline_result.to_dict()["diagnostics"],
            ),
            phases,
        )
    baseline = baseline_result
    phases.append(
        _phase(
            "compile_baseline",
            True,
            "ACCEPTANCE_COMPILE_BASELINE_OK",
            _compile_baseline_data(baseline),
        )
    )

    target_already_matched = _target_already_matches(
        config.target_dir,
        _data(initial_status).get("bridge_version"),
        identity,
    )
    deployed = await transport.deploy(str(config.target_dir))
    deploy_manifest_equal = _deploy_manifest_equal(deployed, identity)
    deployment_applied = _deployment_applied(_data(deployed))
    deployment_changed = not target_already_matched
    deployed = {
        **deployed,
        "data": {
            **_data(deployed),
            "expected_manifest_sha256": identity.bridge_manifest_sha256,
            "expected_bridge_version": identity.package_versions["bridge"],
            "manifest_equal": deploy_manifest_equal,
            "changed_deploy": deployment_changed,
        },
    }
    deploy_public = _deploy_data(_data(deployed))
    deploy_equal = (
        deploy_manifest_equal
        and deployment_applied
        and deploy_public["bridge_version_equal"] is True
    )
    phases.append(_response_phase("deploy", deployed))
    if not _success(deployed) or not deploy_equal:
        return _publish_or_report_failure(
            reservation,
            _failure(
                phases,
                code="ACCEPTANCE_DEPLOY_FAILED",
                message="Bridge deployment did not complete.",
                failed_phase="deploy",
                diagnostics=_diagnostics(deployed),
            ),
            phases,
        )

    compile_observation = wait_for_compile(
        baseline,
        changed_deploy=deployment_changed,
        deadline=clock() + _RECOMPILE_TIMEOUT_SEC,
        clock=clock,
        sleep=sleep,
    )
    phases.append(_compile_phase(compile_observation))
    if not compile_observation.success:
        return _publish_or_report_failure(
            reservation,
            _failure(
                phases,
                code=compile_observation.code,
                message="Independent Unity script compilation did not complete cleanly.",
                failed_phase="compile_observer",
            ),
            phases,
        )

    recompiled = await transport.recompile(_RECOMPILE_TIMEOUT_SEC)
    phases.append(_response_phase("recompile", recompiled))
    if not _success(recompiled):
        return _publish_or_report_failure(
            reservation,
            _failure(
                phases,
                code="ACCEPTANCE_COMPILE_FAILED",
                message="The reloaded Editor Bridge did not recompile cleanly.",
                failed_phase="recompile",
                diagnostics=_diagnostics(recompiled),
            ),
            phases,
        )

    environment_status = await _await_environment_status(
        transport,
        deadline=clock() + _ENVIRONMENT_TIMEOUT_SEC,
        clock=clock,
        sleep=sleep,
    )
    phases.append(_response_phase("environment", environment_status))
    if (
        not _success(environment_status)
        or _has_blockers(environment_status)
        or not _environment_ready(_data(environment_status))
    ):
        return _publish_or_report_failure(
            reservation,
            _failure(
                phases,
                code="ACCEPTANCE_EDITOR_STATE_BLOCKED",
                message="The reloaded Unity environment is not acceptance-ready.",
                failed_phase="environment",
                diagnostics=_diagnostics(environment_status),
            ),
            phases,
        )

    console = await transport.console_errors(0.0)
    phases.append(_response_phase("console", console))
    if not _success(console) or _console_has_errors(console):
        return _publish_or_report_failure(
            reservation,
            _failure(
                phases,
                code="ACCEPTANCE_COMPILE_FAILED",
                message="Unity Console contains errors after Bridge recompilation.",
                failed_phase="console",
                diagnostics=_diagnostics(console),
            ),
            phases,
        )

    reflection = await transport.reflect_game_object()
    phases.append(_response_phase("reflection", reflection))
    if not _success(reflection):
        return _publish_or_report_failure(
            reservation,
            _failure(
                phases,
                code="ACCEPTANCE_SMOKE_FAILED",
                message="The reloaded Bridge reflection probe failed.",
                failed_phase="reflection",
                diagnostics=_diagnostics(reflection),
            ),
            phases,
        )

    runtime_probe = await transport.runtime_console_probe(config.scope)
    phases.append(_response_phase("runtime_probe", runtime_probe))
    if not _success(runtime_probe):
        return _publish_or_report_failure(
            reservation,
            _failure(
                phases,
                code="ACCEPTANCE_SMOKE_FAILED",
                message="The runtime console probe failed.",
                failed_phase="runtime_probe",
                diagnostics=_diagnostics(runtime_probe),
            ),
            phases,
        )

    run_id = uuid4().hex
    smoke = await _await_terminal_response(
        transport.run_acceptance(run_id, _SMOKE_TIMEOUT_SEC),
        cancelled_code="ACCEPTANCE_TRANSPORT_TIMEOUT",
    )
    phases.append(_response_phase("smoke", smoke))
    smoke_failure = (
        not _success(smoke)
        or not _smoke_results_valid(smoke, run_id)
    )
    smoke_code = (
        _smoke_code(smoke)
        if not _success(smoke)
        else "ACCEPTANCE_SMOKE_FAILED"
    )

    status = await _await_terminal_response(
        transport.acceptance_status(run_id),
        cancelled_code="ACCEPTANCE_CLEANUP_FAILED",
    )
    phases.append(_response_phase("acceptance_status", status))
    cleanup = await _await_terminal_response(
        transport.cleanup_acceptance(run_id, _SMOKE_TIMEOUT_SEC),
        cancelled_code="ACCEPTANCE_CLEANUP_FAILED",
    )
    phases.append(_response_phase("cleanup", cleanup))

    final_status, final_console, postconditions_ok = (
        await _observe_terminal_postconditions(
            transport,
            initial_editor_state,
            phases,
            clock=clock,
            sleep=sleep,
        )
    )

    cleanup_failure = (
        not _acceptance_status_valid(
            status,
            run_id,
            normal_run=True,
            completed_smoke=not smoke_failure,
        )
        or not _cleanup_result_valid(
            cleanup,
            run_id,
            normal_run=True,
        )
    )
    if cleanup_failure:
        return _publish_or_report_failure(
            reservation,
            _failure(
                phases,
                code="ACCEPTANCE_CLEANUP_FAILED",
                message="Acceptance fixture cleanup did not complete.",
                failed_phase="cleanup",
                diagnostics=(
                    _diagnostics(status)
                    + _diagnostics(cleanup)
                    + _diagnostics(final_status)
                    + _diagnostics(final_console)
                ),
            ),
            phases,
        )

    if not postconditions_ok:
        return _publish_or_report_failure(
            reservation,
            _failure(
                phases,
                code="ACCEPTANCE_POSTCONDITION_FAILED",
                message="Unity Editor postconditions do not match the acceptance preflight.",
                failed_phase="postconditions",
                diagnostics=_diagnostics(final_status) + _diagnostics(final_console),
            ),
            phases,
        )

    if smoke_failure:
        return _publish_or_report_failure(
            reservation,
            _failure(
                phases,
                code=smoke_code,
                message=(
                    "The bounded Unity acceptance smoke exceeded its deadline."
                    if smoke_code == "ACCEPTANCE_SMOKE_TIMEOUT"
                    else "The bounded Unity acceptance smoke failed."
                ),
                failed_phase="smoke",
                diagnostics=_diagnostics(smoke),
            ),
            phases,
        )

    result = AcceptanceResult(
        success=True,
        severity="info",
        code="ACCEPTANCE_OK",
        message="Unity Bridge acceptance completed.",
        failed_phase=None,
        phases=tuple(phases),
        evidence=_evidence_from_phases(phases),
    )
    return _publish_or_report_failure(reservation, result, phases)




async def _await_terminal_response(
    operation: Awaitable[dict[str, Any]],
    *,
    cancelled_code: str,
) -> dict[str, Any]:
    """Convert interruption into evidence so mandatory cleanup can continue."""
    try:
        return await operation
    except (asyncio.CancelledError, KeyboardInterrupt):
        task = asyncio.current_task()
        while task is not None and task.cancelling():
            task.uncancel()
        return {
            "success": False,
            "severity": "error",
            "code": cancelled_code,
            "message": "A mandatory acceptance operation was interrupted.",
            "data": {},
            "diagnostics": [],
        }


async def _observe_terminal_postconditions(
    transport: AcceptanceTransport,
    initial_editor_state: dict[str, Any] | None,
    phases: list[AcceptancePhaseResult],
    *,
    clock: Callable[[], float],
    sleep: Callable[[float], None],
) -> tuple[dict[str, Any], dict[str, Any], bool]:
    final_status = await _await_terminal_response(
        _await_environment_status(
            transport,
            deadline=clock() + _ENVIRONMENT_TIMEOUT_SEC,
            clock=clock,
            sleep=sleep,
        ),
        cancelled_code="ACCEPTANCE_POSTCONDITION_FAILED",
    )
    final_editor_state = _editor_snapshot(final_status)
    editor_state_equal = (
        _success(final_status)
        and not _has_blockers(final_status)
        and final_editor_state is not None
        and (
            initial_editor_state is None
            or final_editor_state == initial_editor_state
        )
    )
    final_console = await _await_terminal_response(
        transport.console_errors(0.0),
        cancelled_code="ACCEPTANCE_POSTCONDITION_FAILED",
    )
    final_console_zero = (
        _success(final_console) and not _console_has_errors(final_console)
    )
    postconditions_ok = editor_state_equal and final_console_zero
    phases.append(
        _phase(
            "postconditions",
            postconditions_ok,
            (
                "ACCEPTANCE_POSTCONDITION_OK"
                if postconditions_ok
                else "ACCEPTANCE_POSTCONDITION_FAILED"
            ),
            {
                "editor_state_equal": editor_state_equal,
                "final_console_zero": final_console_zero,
                "editor_state": _safe_editor_state(_data(final_status)),
                "final_console": _console_data(final_console),
                "environment": _environment_data(_data(final_status)),
            },
            _diagnostics(final_status) + _diagnostics(final_console),
        )
    )
    return final_status, final_console, postconditions_ok

async def _run_recovery_only(
    config: AcceptanceConfig,
    transport: AcceptanceTransport,
    reservation: AcceptanceReportReservation,
    phases: list[AcceptancePhaseResult],
    *,
    clock: Callable[[], float],
    sleep: Callable[[float], None],
) -> AcceptanceResult:
    """Clean one existing lease and stop without starting an acceptance run."""
    activated = await transport.activate(str(config.project_root), config.scope)
    phases.append(_response_phase("activate", activated))
    if not _success(activated):
        return _publish_or_report_failure(
            reservation,
            _failure(
                phases,
                code="ACCEPTANCE_EDITOR_UNAVAILABLE",
                message="The Unity project could not be activated for recovery.",
                failed_phase="activate",
                diagnostics=_diagnostics(activated),
            ),
            phases,
        )

    connection_identity = _connection_data(transport.connection_identity())
    connection_ok = all(value is not None for value in connection_identity.values())
    phases.append(
        _phase(
            "connection_identity",
            connection_ok,
            (
                "ACCEPTANCE_CONNECTION_IDENTIFIED"
                if connection_ok
                else "ACCEPTANCE_EDITOR_UNAVAILABLE"
            ),
            connection_identity,
        )
    )
    if not connection_ok:
        return _publish_or_report_failure(
            reservation,
            _failure(
                phases,
                code="ACCEPTANCE_EDITOR_UNAVAILABLE",
                message="The worktree-local MCP connection identity is unavailable.",
                failed_phase="connection_identity",
            ),
            phases,
        )

    status = await _await_terminal_response(
        transport.acceptance_status(config.recover_run_id),
        cancelled_code="ACCEPTANCE_CLEANUP_FAILED",
    )
    phases.append(_response_phase("acceptance_status", status))
    cleanup = await _await_terminal_response(
        transport.cleanup_acceptance(
            config.recover_run_id,
            _SMOKE_TIMEOUT_SEC,
        ),
        cancelled_code="ACCEPTANCE_CLEANUP_FAILED",
    )
    phases.append(_response_phase("cleanup", cleanup))
    final_status, final_console, postconditions_ok = (
        await _observe_terminal_postconditions(
            transport,
            None,
            phases,
            clock=clock,
            sleep=sleep,
        )
    )

    cleanup_failed = (
        not _acceptance_status_valid(
            status,
            config.recover_run_id,
            normal_run=False,
        )
        or not _cleanup_result_valid(
            cleanup,
            config.recover_run_id,
            normal_run=False,
        )
    )
    if cleanup_failed:
        return _publish_or_report_failure(
            reservation,
            _failure(
                phases,
                code="ACCEPTANCE_CLEANUP_FAILED",
                message="Acceptance lease recovery did not complete.",
                failed_phase="cleanup",
                diagnostics=(
                    _diagnostics(status)
                    + _diagnostics(cleanup)
                    + _diagnostics(final_status)
                    + _diagnostics(final_console)
                ),
            ),
            phases,
        )
    if not postconditions_ok:
        return _publish_or_report_failure(
            reservation,
            _failure(
                phases,
                code="ACCEPTANCE_POSTCONDITION_FAILED",
                message="Unity Editor recovery postconditions are not acceptance-safe.",
                failed_phase="postconditions",
                diagnostics=_diagnostics(final_status) + _diagnostics(final_console),
            ),
            phases,
        )

    result = AcceptanceResult(
        success=True,
        severity="info",
        code="ACCEPTANCE_OK",
        message="Acceptance lease recovery completed.",
        failed_phase=None,
        phases=tuple(phases),
        evidence=_evidence_from_phases(phases),
    )
    return _publish_or_report_failure(reservation, result, phases)


def _normalized_scope(scope: str) -> str | None:
    if scope.strip() != scope or "\\" in scope:
        return None
    parts = scope.split("/")
    if (
        len(parts) < 2
        or parts[0] != "Assets"
        or any(part in {"", ".", ".."} for part in parts)
    ):
        return None
    return "/".join(parts)


def _project_relative_path(
    project_root: Path,
    candidate: Path,
    *,
    require_assets: bool,
) -> str | None:
    if any(part == ".." for part in candidate.parts):
        return None
    try:
        root = project_root.resolve(strict=True)
        absolute_candidate = candidate if candidate.is_absolute() else root / candidate
        relative = absolute_candidate.resolve(strict=False).relative_to(root)
    except (OSError, ValueError):
        return None
    if not relative.parts:
        return None
    if require_assets and relative.parts[0] != "Assets":
        return None
    return relative.as_posix()


def _normalized_report_argument(project_root: Path, out_report: str) -> str | None:
    if not out_report or out_report.strip() != out_report or "\\" in out_report:
        return None
    candidate = Path(out_report)
    if candidate.as_posix() != out_report:
        return None
    relative = _project_relative_path(
        project_root,
        candidate,
        require_assets=False,
    )
    if relative is None or relative.split("/", 1)[0] == "Assets":
        return None
    return relative


def _validate_config(config: AcceptanceConfig) -> str | None:
    if not config.project_root.is_dir():
        return "Unity project root is unavailable."
    if _normalized_scope(config.scope) is None:
        return "Acceptance scope must be normalized under Assets/."
    if (
        not config.target_dir.is_absolute()
        or _project_relative_path(
            config.project_root,
            config.target_dir,
            require_assets=True,
        )
        is None
    ):
        return "Bridge deployment target must be contained under project Assets/."
    if not Path(config.watch_dir).is_dir():
        return "Editor Bridge watch directory is unavailable."
    if not config.unity_log_file.is_file():
        return "Unity Editor log is unavailable."
    if _normalized_report_argument(config.project_root, config.out_report) is None:
        return "Acceptance report path must be normalized inside the project."
    if config.recover_run_id and not _valid_run_id(config.recover_run_id):
        return "Recovery run ID must be exactly 32 lowercase hexadecimal characters."
    return None


def _source_data(identity: AcceptanceSourceIdentity) -> dict[str, Any]:
    return {
        "head": identity.head,
        "branch": identity.branch,
        "managed_source_clean": not identity.managed_dirty_paths,
        "managed_dirty_paths": list(identity.managed_dirty_paths),
        "package_versions": dict(identity.package_versions),
        "bridge_manifest_sha256": identity.bridge_manifest_sha256,
        "bridge_files": [
            {"path": item.path, "size": item.size, "sha256": item.sha256}
            for item in identity.bridge_files
        ],
    }


def _target_already_matches(
    target_dir: Path,
    running_bridge_version: object,
    identity: AcceptanceSourceIdentity,
) -> bool:
    expected_version = identity.package_versions["bridge"]
    manifest = build_bridge_manifest(target_dir)
    return (
        isinstance(running_bridge_version, str)
        and running_bridge_version == expected_version
        and isinstance(manifest, BridgeBundleManifest)
        and manifest.sha256 == identity.bridge_manifest_sha256
        and manifest.bridge_version == expected_version
    )


def _deploy_manifest_equal(
    response: dict[str, Any],
    identity: AcceptanceSourceIdentity,
) -> bool:
    data = _data(response)
    return (
        data.get("manifest_sha256") == identity.bridge_manifest_sha256
        and data.get("bridge_version") == identity.package_versions["bridge"]
    )


def _deployment_applied(data: dict[str, Any]) -> bool:
    return data.get("promotion_state") in {
        "already_current",
        "installed_fresh",
        "promoted",
    }


def _success(response: dict[str, Any]) -> bool:
    return response.get("success") is True


def _data(response: dict[str, Any]) -> dict[str, Any]:
    data = response.get("data")
    return dict(data) if isinstance(data, dict) else {}


def _diagnostics(response: dict[str, Any]) -> tuple[dict[str, Any], ...]:
    diagnostics = response.get("diagnostics")
    if not isinstance(diagnostics, list):
        return ()
    return tuple(dict(item) for item in diagnostics if isinstance(item, dict))


def _has_blockers(response: dict[str, Any]) -> bool:
    blockers = _data(response).get("blockers")
    return not isinstance(blockers, list) or bool(blockers)



def _editor_snapshot(response: dict[str, Any]) -> dict[str, Any] | None:
    data = _data(response)
    if any(field not in data for field in _EDITOR_STATE_FIELDS):
        return None

    string_fields = (
        "active_stage_kind",
        "active_scene_name",
        "active_scene_path",
        "prefab_stage_asset_path",
    )
    if any(not isinstance(data[field], str) for field in string_fields):
        return None
    bool_fields = ("has_unsaved_changes", "prefab_stage_is_dirty")
    if any(not isinstance(data[field], bool) for field in bool_fields):
        return None

    dirty_paths: dict[str, list[str]] = {}
    for field in (
        "dirty_scene_paths",
        "dirty_prefab_paths",
        "dirty_material_paths",
        "dirty_asset_paths",
    ):
        value = data[field]
        if not isinstance(value, list) or not all(
            isinstance(path, str) for path in value
        ):
            return None
        dirty_paths[field] = list(value)

    open_scenes = data["open_scenes"]
    if not isinstance(open_scenes, list):
        return None
    normalized_scenes: list[dict[str, Any]] = []
    for scene in open_scenes:
        if not isinstance(scene, dict):
            return None
        if (
            not isinstance(scene.get("path"), str)
            or not isinstance(scene.get("name"), str)
            or not isinstance(scene.get("is_dirty"), bool)
        ):
            return None
        normalized_scenes.append(
            {
                "path": scene["path"],
                "name": scene["name"],
                "is_dirty": scene["is_dirty"],
            }
        )

    return {
        "active_stage_kind": data["active_stage_kind"],
        "active_scene_name": data["active_scene_name"],
        "active_scene_path": data["active_scene_path"],
        "open_scenes": normalized_scenes,
        "has_unsaved_changes": data["has_unsaved_changes"],
        **dirty_paths,
        "prefab_stage_is_dirty": data["prefab_stage_is_dirty"],
        "prefab_stage_asset_path": data["prefab_stage_asset_path"],
    }


def _console_has_errors(response: dict[str, Any]) -> bool:
    data = _data(response)
    if "entries" in data:
        entries = data["entries"]
        return not isinstance(entries, list) or bool(entries)
    if "errors" in data:
        errors = data["errors"]
        return not isinstance(errors, list) or bool(errors)
    return True


def _smoke_code(response: dict[str, Any]) -> str:
    return (
        "ACCEPTANCE_SMOKE_TIMEOUT"
        if response.get("code") in {"ACCEPTANCE_TRANSPORT_TIMEOUT", "EDITOR_BRIDGE_TIMEOUT"}
        else "ACCEPTANCE_SMOKE_FAILED"
    )


def _phase(
    name: str,
    success: bool,
    code: str,
    data: dict[str, Any],
    diagnostics: Sequence[dict[str, Any]] = (),
) -> AcceptancePhaseResult:
    return AcceptancePhaseResult(
        name=name,
        success=success,
        code=_stable_code(code),
        data=data,
        diagnostics=_public_diagnostics(diagnostics),
    )



_ACCEPTANCE_CASE_NAMES = (
    "Acceptance_SavedSceneAndPrefabFixture",
    "Acceptance_DuplicateSameNameObjects",
    "Acceptance_TransformMutationReadback",
    "Acceptance_PrimitivePropertyOverride",
    "Acceptance_EditorBridgeRequest",
    "Acceptance_ConsoleErrorZero",
)
_REQUIRED_PACKAGE_NAMES = (
    "com.vrchat.base",
    "com.vrchat.worlds",
    "udonsharp",
)
_LEASE_PHASES = {
    "reserved",
    "fixture_created",
    "smoke_complete",
    "cleanup_started",
    "cleaned",
}
_NORMAL_DELETED_FIXTURE_COUNT = 1
_NORMAL_DELETED_REQUEST_ARTIFACT_COUNT = 5


def _stable_code(value: object) -> str:
    if (
        isinstance(value, str)
        and 0 < len(value) <= 128
        and all(character.isupper() or character.isdigit() or character == "_" for character in value)
    ):
        return value
    return "ACCEPTANCE_RESPONSE_CODE_REDACTED"


def _connection_data(identity: dict[str, str | None]) -> dict[str, str | None]:
    protocol = identity.get("protocol_revision")
    name = identity.get("server_name")
    version = identity.get("server_version")
    return {
        "protocol_revision": protocol if protocol == "2026-07-28" else None,
        "server_name": name if name == "prefab-sentinel" else None,
        "server_version": (
            version
            if isinstance(version, str)
            and 0 < len(version) <= 64
            and all(character.isalnum() or character in ".-+" for character in version)
            else None
        ),
    }


def _public_diagnostics(
    diagnostics: Sequence[dict[str, Any]],
) -> tuple[dict[str, Any], ...]:
    public: list[dict[str, Any]] = []
    for diagnostic in diagnostics:
        severity = diagnostic.get("severity")
        code = diagnostic.get("code")
        item: dict[str, Any] = {}
        if severity in {"info", "warning", "error", "critical"}:
            item["severity"] = severity
        if isinstance(code, str):
            item["code"] = _stable_code(code)
        if item:
            public.append(item)
    return tuple(public)


def _safe_blockers(data: dict[str, Any]) -> list[dict[str, Any]]:
    raw = data.get("blockers")
    if not isinstance(raw, list):
        return []
    blockers: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        blocker: dict[str, Any] = {}
        for key in ("blocker_class", "state_source"):
            value = item.get(key)
            if isinstance(value, str) and value and "/" not in value and "\\" not in value:
                blocker[key] = value
        if blocker:
            blockers.append(blocker)
    return blockers


def _required_packages(data: dict[str, Any]) -> list[dict[str, Any]]:
    raw = data.get("required_packages")
    by_name = {
        item.get("name"): item
        for item in raw
        if isinstance(raw, list) and isinstance(item, dict)
    } if isinstance(raw, list) else {}
    packages: list[dict[str, Any]] = []
    for name in _REQUIRED_PACKAGE_NAMES:
        item = by_name.get(name)
        version = item.get("version") if isinstance(item, dict) else None
        packages.append(
            {
                "name": name,
                "ready": bool(item.get("ready")) if isinstance(item, dict) else False,
                "version": version if isinstance(version, str) else "",
            }
        )
    return packages


def _safe_project_session(data: dict[str, Any]) -> dict[str, Any]:
    bridge = data.get("bridge")
    connection_state = (
        bridge.get("connection_state") if isinstance(bridge, dict) else None
    )
    return {
        "connection_state": connection_state,
        "project_active": True,
        "project_root_consistent": data.get("project_root_consistent"),
        "session_id": data.get("session_id"),
        "bridge_session_id": data.get("bridge_session_id"),
        "bridge_instance_id": data.get("bridge_instance_id"),
    }


def _environment_data(data: dict[str, Any]) -> dict[str, Any]:
    unity_version = data.get("unity_version")
    return {
        "unity_version": unity_version if isinstance(unity_version, str) else "",
        "required_packages": _required_packages(data),
        "project_session": _safe_project_session(data),
    }


def _environment_status_unavailable(response: dict[str, Any]) -> bool:
    bridge = _data(response).get("bridge")
    return (
        _success(response)
        and isinstance(bridge, dict)
        and bridge.get("connection_state") == "unavailable"
    )


async def _await_environment_status(
    transport: AcceptanceTransport,
    *,
    deadline: float,
    clock: Callable[[], float],
    sleep: Callable[[float], None],
) -> dict[str, Any]:
    status = await transport.project_status()
    while _environment_status_unavailable(status):
        remaining = deadline - clock()
        if remaining <= 0:
            return status
        sleep(min(_ENVIRONMENT_STATUS_POLL_INTERVAL_SEC, remaining))
        status = await transport.project_status()
    return status


def _environment_ready(data: dict[str, Any]) -> bool:
    environment = _environment_data(data)
    unity_version = environment["unity_version"]
    return (
        isinstance(unity_version, str)
        and unity_version.startswith("2022.3.")
        and all(
            package["ready"] and bool(package["version"])
            for package in environment["required_packages"]
        )
    )


def _deploy_data(data: dict[str, Any]) -> dict[str, Any]:
    expected_manifest = data.get("expected_manifest_sha256")
    observed_manifest = data.get("manifest_sha256")
    expected_version = data.get("expected_bridge_version")
    observed_version = data.get("bridge_version")
    return {
        "changed_deploy": data.get("changed_deploy") is True,
        "expected_manifest_sha256": expected_manifest,
        "observed_manifest_sha256": observed_manifest,
        "manifest_equal": data.get("manifest_equal") is True,
        "expected_bridge_version": expected_version,
        "observed_bridge_version": observed_version,
        "bridge_version_equal": (
            isinstance(expected_version, str)
            and isinstance(observed_version, str)
            and expected_version == observed_version
        ),
    }


def _console_data(response: dict[str, Any]) -> dict[str, Any]:
    entries = _data(response).get("entries")
    return {
        "success": _success(response),
        "code": _stable_code(response.get("code")),
        "error_count": len(entries) if isinstance(entries, list) else -1,
    }


def _valid_run_id(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 32
        and all(character in "0123456789abcdef" for character in value)
    )


def _smoke_data(response: dict[str, Any]) -> dict[str, Any]:
    data = _data(response)
    raw_cases = data.get("acceptance_cases")
    cases: list[dict[str, Any]] = []
    if isinstance(raw_cases, list):
        for item in raw_cases:
            if (
                not isinstance(item, Mapping)
                or item.get("name") not in _ACCEPTANCE_CASE_NAMES
            ):
                continue
            cases.append(
                {
                    "name": item["name"],
                    "passed": item.get("passed") is True,
                    "code": _stable_code(item.get("code")),
                }
            )
    run_id = data.get("run_id")
    return {
        "success": _success(response),
        "code": _stable_code(response.get("code")),
        "total": data.get("acceptance_total"),
        "passed": data.get("acceptance_passed"),
        "failed": data.get("acceptance_failed"),
        "cases": cases,
        "run_ownership": {
            "fixture_owned": data.get("fixture_owned") is True,
            "run_id": run_id if _valid_run_id(run_id) else "",
            "lease_phase": (
                data.get("lease_phase")
                if data.get("lease_phase") in _LEASE_PHASES
                else ""
            ),
        },
    }


def _smoke_results_valid(
    response: dict[str, Any],
    expected_run_id: str,
) -> bool:
    raw_cases = _data(response).get("acceptance_cases")
    if not isinstance(raw_cases, list) or len(raw_cases) != len(_ACCEPTANCE_CASE_NAMES):
        return False
    names: set[str] = set()
    for item in raw_cases:
        if (
            not isinstance(item, Mapping)
            or not isinstance(name := item.get("name"), str)
            or name not in _ACCEPTANCE_CASE_NAMES
            or name in names
            or item.get("passed") is not True
            or item.get("code") != "ACCEPTANCE_CASE_OK"
        ):
            return False
        names.add(name)
    if names != set(_ACCEPTANCE_CASE_NAMES):
        return False

    suite = _smoke_data(response)
    total = suite["total"]
    passed = suite["passed"]
    failed = suite["failed"]
    if (
        not isinstance(total, int)
        or isinstance(total, bool)
        or not isinstance(passed, int)
        or isinstance(passed, bool)
        or not isinstance(failed, int)
        or isinstance(failed, bool)
    ):
        return False
    ownership = suite["run_ownership"]
    return (
        total == 6
        and passed == 6
        and failed == 0
        and ownership == {
            "fixture_owned": True,
            "run_id": expected_run_id,
            "lease_phase": "smoke_complete",
        }
    )


def _lease_data(response: dict[str, Any]) -> dict[str, Any]:
    data = _data(response)
    run_id = data.get("run_id")
    phase = data.get("phase")
    return {
        "success": _success(response),
        "code": _stable_code(response.get("code")),
        "run_id": run_id if _valid_run_id(run_id) else "",
        "phase": phase if phase in _LEASE_PHASES else "",
        "cleanup_required": data.get("cleanup_required") is True,
        "cleanup_performed": data.get("cleanup_performed") is True,
    }


def _cleanup_data(response: dict[str, Any]) -> dict[str, Any]:
    lease = _lease_data(response)
    data = _data(response)
    fixture_count = data.get("deleted_fixture_count")
    request_count = data.get("deleted_request_artifact_count")
    return {
        **lease,
        "scene_setup_restored": data.get("scene_setup_restored") is True,
        "deleted_fixture_count": (
            fixture_count
            if isinstance(fixture_count, int) and not isinstance(fixture_count, bool)
            else 0
        ),
        "deleted_request_artifact_count": (
            request_count
            if isinstance(request_count, int) and not isinstance(request_count, bool)
            else 0
        ),
        "lease_removed": data.get("lease_removed") is True,
    }


def _acceptance_status_valid(
    response: dict[str, Any],
    expected_run_id: str,
    *,
    normal_run: bool,
    completed_smoke: bool = False,
) -> bool:
    data = _data(response)
    phase = data.get("phase")
    cleanup_required = data.get("cleanup_required")
    cleanup_performed = data.get("cleanup_performed")
    if (
        not _success(response)
        or data.get("run_id") != expected_run_id
        or phase not in _LEASE_PHASES
        or not isinstance(cleanup_required, bool)
        or not isinstance(cleanup_performed, bool)
    ):
        return False
    if normal_run:
        return (
            (not completed_smoke or phase == "smoke_complete")
            and phase != "cleaned"
            and cleanup_required is True
            and cleanup_performed is False
        )
    return (
        cleanup_required is (phase != "cleaned")
        and cleanup_performed is (phase == "cleaned")
    )


def _cleanup_result_valid(
    response: dict[str, Any],
    expected_run_id: str,
    *,
    normal_run: bool,
) -> bool:
    data = _data(response)
    fixture_count = data.get("deleted_fixture_count")
    request_count = data.get("deleted_request_artifact_count")
    if (
        not _success(response)
        or data.get("run_id") != expected_run_id
        or data.get("phase") != "cleaned"
        or data.get("cleanup_required") is not False
        or data.get("cleanup_performed") is not True
        or data.get("scene_setup_restored") is not True
        or data.get("lease_removed") is not True
        or not isinstance(fixture_count, int)
        or isinstance(fixture_count, bool)
        or fixture_count < 0
        or not isinstance(request_count, int)
        or isinstance(request_count, bool)
        or request_count < 0
    ):
        return False
    if normal_run:
        return (
            fixture_count == _NORMAL_DELETED_FIXTURE_COUNT
            and request_count == _NORMAL_DELETED_REQUEST_ARTIFACT_COUNT
        )
    return (
        fixture_count <= _NORMAL_DELETED_FIXTURE_COUNT
        and request_count <= _NORMAL_DELETED_REQUEST_ARTIFACT_COUNT
    )


def _compile_baseline_data(baseline: CompileBaseline) -> dict[str, Any]:
    return {
        "dll": {
            "exists": baseline.dll_sha256 is not None,
            "size": baseline.dll_size,
            "mtime_ns": baseline.dll_mtime_ns,
            "sha256": baseline.dll_sha256,
        },
        "log": {
            "device": baseline.log_device,
            "inode": baseline.log_inode,
            "size": baseline.log_size,
            "continuity": "captured",
        },
    }

def _response_phase(name: str, response: dict[str, Any]) -> AcceptancePhaseResult:
    data = _data(response)
    public_data: dict[str, Any]
    if name == "report_reservation":
        public_data = {"reserved": False}
    elif name == "source_identity":
        paths = data.get("paths")
        public_data = {
            "managed_source_clean": False,
            "managed_dirty_paths": (
                [path for path in paths if isinstance(path, str) and not Path(path).is_absolute()]
                if isinstance(paths, list)
                else []
            ),
        }
    elif name == "activate":
        public_data = {"activated": _success(response)}
    elif name == "editor_state":
        public_data = {
            "editor_state": _safe_editor_state(data),
            "blockers": _safe_blockers(data),
            "environment": _environment_data(data),
        }
    elif name == "environment":
        public_data = _environment_data(data)
    elif name == "deploy":
        public_data = _deploy_data(data)
    elif name == "recompile":
        public_data = {
            "success": _success(response),
            "code": _stable_code(response.get("code")),
        }
    elif name == "console":
        public_data = _console_data(response)
    elif name in {"reflection", "runtime_probe"}:
        public_data = {
            "success": _success(response),
            "code": _stable_code(response.get("code")),
        }
    elif name == "smoke":
        public_data = _smoke_data(response)
    elif name == "acceptance_status":
        public_data = _lease_data(response)
    elif name == "cleanup":
        public_data = _cleanup_data(response)
    else:
        public_data = {}
    return _phase(
        name,
        _success(response),
        _stable_code(response.get("code")),
        public_data,
        _diagnostics(response),
    )


def _compile_phase(observation: CompileObservation) -> AcceptancePhaseResult:
    return _phase(
        "compile_observer",
        observation.success,
        observation.code,
        {
            "code": observation.code,
            "compile_observation": observation.compile_observation,
            "dll": {
                "exists": observation.dll_sha256 is not None,
                "size": observation.dll_size,
                "mtime_ns": observation.dll_mtime_ns,
                "sha256": observation.dll_sha256,
            },
            "compiler_error_count": len(observation.compiler_errors),
            "superseded_compiler_error_count": len(
                observation.superseded_compiler_errors
            ),
            "superseded_compiler_errors": list(
                observation.superseded_compiler_errors
            ),
            "log_continuity": (
                "lost"
                if observation.code == "ACCEPTANCE_COMPILE_LOG_CONTINUITY_LOST"
                else "preserved"
            ),
        },
    )


def _audit_data(
    config: AcceptanceConfig,
    *,
    paths_validated: bool = False,
) -> dict[str, Any]:
    safe_scope = _normalized_scope(config.scope) if paths_validated else None
    safe_target = (
        _project_relative_path(
            config.project_root,
            config.target_dir,
            require_assets=True,
        )
        if paths_validated
        else None
    )
    safe_report = (
        _normalized_report_argument(config.project_root, config.out_report)
        if paths_validated
        else None
    )
    return {
        "opt_in": config.confirm_live,
        "mode": "recovery" if config.recover_run_id else "acceptance",
        "arguments": {
            "project_root": "<project-root>",
            "scope": safe_scope or "<redacted>",
            "target_dir": safe_target or "<redacted>",
            "watch_dir": "<redacted>",
            "unity_log_file": "<redacted>",
            "out_report": safe_report or "<redacted>",
            "recover_run_id": "<redacted>" if config.recover_run_id else "",
        },
        "deadlines": {
            "recompile_timeout_sec": _RECOMPILE_TIMEOUT_SEC,
            "environment_timeout_sec": _ENVIRONMENT_TIMEOUT_SEC,
            "smoke_timeout_sec": _SMOKE_TIMEOUT_SEC,
        },
    }


def _evidence_from_phases(
    phases: Sequence[AcceptancePhaseResult],
) -> AcceptanceEvidence:
    by_name = {phase.name: phase for phase in phases}

    def section(
        data: dict[str, Any],
        *phase_names: str,
    ) -> AcceptanceEvidenceSection:
        related = [by_name[item] for item in phase_names if item in by_name]
        if not related:
            return AcceptanceEvidenceSection()
        return AcceptanceEvidenceSection(
            executed=True,
            success=all(item.success for item in related),
            code=related[-1].code,
            data=data,
            diagnostics=tuple(
                diagnostic for item in related for diagnostic in item.diagnostics
            ),
        )

    connection = by_name.get("connection_identity")
    source = by_name.get("source_identity")
    source_data = dict(source.data) if source is not None else {}
    if source is not None and connection is not None:
        source_data["mcp_protocol_revision"] = connection.data.get("protocol_revision")
        source_data["mcp_server"] = {
            "name": connection.data.get("server_name"),
            "version": connection.data.get("server_version"),
        }

    editor = by_name.get("editor_state")
    environment = by_name.get("environment")
    postconditions = by_name.get("postconditions")
    if environment is not None:
        environment_from_status = environment.data
    else:
        status_evidence = editor if editor is not None else postconditions
        environment_from_status = (
            status_evidence.data.get("environment", {})
            if status_evidence is not None
            else {}
        )
    environment_data = {
        "unity_version": environment_from_status.get("unity_version", ""),
        "required_packages": environment_from_status.get("required_packages", []),
        "connection_identity": dict(connection.data) if connection is not None else {},
        "project_session": environment_from_status.get("project_session", {}),
    }

    config_phase = by_name.get("config")
    reservation = by_name.get("report_reservation")
    preflight_data = {
        "config_validation": (
            {"success": config_phase.success, "code": config_phase.code}
            if config_phase is not None
            else {"success": False, "code": None}
        ),
        "report_reservation": (
            {"success": reservation.success, "code": reservation.code}
            if reservation is not None
            else {"success": False, "code": None}
        ),
        "editor_state": (
            editor.data.get("editor_state") if editor is not None else None
        ),
        "blockers": editor.data.get("blockers", []) if editor is not None else [],
    }

    deploy = by_name.get("deploy")
    baseline = by_name.get("compile_baseline")
    observation = by_name.get("compile_observer")
    recompile = by_name.get("recompile")
    console = by_name.get("console")
    compile_data = {
        "baseline": dict(baseline.data) if baseline is not None else {},
        "observation": dict(observation.data) if observation is not None else {},
        "secondary_recompile": dict(recompile.data) if recompile is not None else {},
        "console": dict(console.data) if console is not None else {},
    }

    reflection = by_name.get("reflection")
    runtime_probe = by_name.get("runtime_probe")
    smoke = by_name.get("smoke")
    smoke_data = {
        "reflection": dict(reflection.data) if reflection is not None else {},
        "runtime_probe": dict(runtime_probe.data) if runtime_probe is not None else {},
        "suite": dict(smoke.data) if smoke is not None else {},
    }

    status = by_name.get("acceptance_status")
    cleanup = by_name.get("cleanup")
    cleanup_data = {
        "status": dict(status.data) if status is not None else {},
        "cleanup": dict(cleanup.data) if cleanup is not None else {},
        "postconditions": (
            {
                key: value
                for key, value in postconditions.data.items()
                if key != "environment"
            }
            if postconditions is not None
            else {}
        ),
    }

    return AcceptanceEvidence(
        audit=section(
            dict(by_name["opt_in"].data),
            "opt_in",
        ) if "opt_in" in by_name else AcceptanceEvidenceSection(),
        source=section(source_data, "source_identity"),
        environment=section(
            environment_data,
            "connection_identity",
            "activate",
            *(
                ("environment",)
                if environment is not None
                else ("editor_state",) if editor is not None else ("postconditions",)
            ),
        ),
        preflight=section(
            preflight_data,
            "config",
            "report_reservation",
            *(("editor_state",) if editor is not None else ()),
        ),
        deploy=section(dict(deploy.data), "deploy")
        if deploy is not None
        else AcceptanceEvidenceSection(),
        compile=section(
            compile_data,
            "compile_baseline",
            "compile_observer",
            "recompile",
            "console",
        ),
        smoke=section(smoke_data, "reflection", "runtime_probe", "smoke"),
        cleanup=section(
            cleanup_data,
            "acceptance_status",
            "cleanup",
            "postconditions",
        ),
    )


def _safe_editor_state(data: dict[str, Any]) -> dict[str, Any]:
    return {
        "active_stage_kind": data.get("active_stage_kind"),
        "has_unsaved_changes": data.get("has_unsaved_changes"),
        "prefab_stage_is_dirty": data.get("prefab_stage_is_dirty"),
        "open_scene_count": len(data.get("open_scenes", []))
        if isinstance(data.get("open_scenes"), list)
        else 0,
        "dirty_counts": {
            field: len(data.get(field, []))
            if isinstance(data.get(field), list)
            else 0
            for field in (
                "dirty_scene_paths",
                "dirty_prefab_paths",
                "dirty_material_paths",
                "dirty_asset_paths",
            )
        },
    }


def _failure(
    phases: Sequence[AcceptancePhaseResult],
    *,
    code: str,
    message: str,
    failed_phase: str,
    diagnostics: Sequence[dict[str, Any]] = (),
) -> AcceptanceResult:
    return AcceptanceResult.failure(
        code=code,
        message=message,
        failed_phase=failed_phase,
        phases=phases,
        diagnostics=_public_diagnostics(diagnostics),
        evidence=_evidence_from_phases(phases),
    )


def _publish_or_report_failure(
    reservation: AcceptanceReportReservation,
    result: AcceptanceResult,
    phases: list[AcceptancePhaseResult],
) -> AcceptanceResult:
    try:
        publish_acceptance_report(reservation, result)
    except (OSError, TypeError, ValueError):
        phases.append(
            _phase(
                "report_publication",
                False,
                "ACCEPTANCE_REPORT_WRITE_FAILED",
                {},
            )
        )
        return _failure(
            phases,
            code="ACCEPTANCE_REPORT_WRITE_FAILED",
            message="The terminal acceptance report could not be published.",
            failed_phase="report_publication",
        )
    return result
