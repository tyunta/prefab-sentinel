from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from unittest import TestCase

import pytest

from prefab_sentinel.bridge_deploy import BridgeBundleManifest
from prefab_sentinel.contracts import Severity, ToolResponse
from prefab_sentinel.unity_acceptance.controller import (
    AcceptanceConfig,
    _compile_phase,
    _evidence_from_phases,
    run_acceptance,
)
from prefab_sentinel.unity_acceptance.model import (
    AcceptanceSourceIdentity,
    CompileObservation,
)


@dataclass
class FakeClock:
    now: float = 0.0

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


def _envelope(
    *,
    success: bool = True,
    code: str = "OK",
    data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "success": success,
        "severity": "info" if success else "error",
        "code": code,
        "message": code,
        "data": {} if data is None else data,
        "diagnostics": [],
    }


class RecordingTransport:
    def __init__(
        self,
        *,
        initial_status: dict[str, Any] | None = None,
        environment_status: dict[str, Any] | None = None,
        final_status: dict[str, Any] | None = None,
        deploy: dict[str, Any] | None = None,
        recompile: dict[str, Any] | None = None,
        smoke: dict[str, Any] | None = None,
        acceptance_status: dict[str, Any] | None = None,
        cleanup: dict[str, Any] | None = None,
    ) -> None:
        self.calls: list[str] = []
        self.acceptance_calls: list[tuple[str, str, int | None]] = []
        self.initial_status = initial_status or _clean_status()
        self.environment_status = environment_status or _clean_status()
        self.final_status = final_status or _clean_status()
        self.deploy_result = deploy or _envelope(
            code="DEPLOY_OK",
            data={
                "promotion_state": "promoted",
                "manifest_sha256": "b" * 64,
                "bridge_version": "0.9.0",
            },
        )
        self.recompile_result = recompile or _envelope(code="RECOMPILE_OK")
        self.smoke_result = smoke or _envelope(
            code="ACCEPTANCE_RUN_OK",
            data={
                "fixture_owned": True,
                "lease_phase": "smoke_complete",
                "acceptance_total": 6,
                "acceptance_passed": 6,
                "acceptance_failed": 0,
                "acceptance_cases": [
                    {"name": name, "passed": True, "code": "ACCEPTANCE_CASE_OK"}
                    for name in (
                        "Acceptance_SavedSceneAndPrefabFixture",
                        "Acceptance_DuplicateSameNameObjects",
                        "Acceptance_TransformMutationReadback",
                        "Acceptance_PrimitivePropertyOverride",
                        "Acceptance_EditorBridgeRequest",
                        "Acceptance_ConsoleErrorZero",
                    )
                ],
            },
        )
        self.acceptance_status_result = acceptance_status or _envelope(
            code="ACCEPTANCE_STATUS_OK",
            data={
                "phase": "smoke_complete",
                "cleanup_required": True,
                "cleanup_performed": False,
            },
        )
        self.cleanup_result = cleanup or _envelope(
            code="ACCEPTANCE_CLEANUP_OK",
            data={
                "phase": "cleaned",
                "cleanup_required": False,
                "cleanup_performed": True,
                "scene_setup_restored": True,
                "deleted_fixture_count": 1,
                "deleted_request_artifact_count": 5,
                "lease_removed": True,
            },
        )
        self._project_root: Path | None = None
        self._unity_log: Path | None = None

    @staticmethod
    def _for_run(response: dict[str, Any], run_id: str) -> dict[str, Any]:
        data = response.get("data")
        copied_data = dict(data) if isinstance(data, dict) else {}
        copied_data.setdefault("run_id", run_id)
        return {**response, "data": copied_data}

    def prepare_compile_evidence(self, project_root: Path, unity_log: Path) -> None:
        self._project_root = project_root
        self._unity_log = unity_log

    async def activate(self, project_root: str, scope: str) -> dict[str, Any]:
        self.calls.append("activate")
        return _envelope(code="ACTIVATE_OK", data={"activated": True})

    async def project_status(self) -> dict[str, Any]:
        self.calls.append("project_status")
        status_call = self.calls.count("project_status")
        if status_call == 1:
            return self.initial_status
        if status_call == 2:
            return self.environment_status
        return self.final_status

    async def deploy(self, target_dir: str) -> dict[str, Any]:
        self.calls.append("deploy")
        if (
            self.deploy_result["success"]
            and self.deploy_result["data"].get("promotion_state")
            in {"installed_fresh", "promoted"}
        ):
            assert self._project_root is not None
            assert self._unity_log is not None
            dll = self._project_root / "Library/ScriptAssemblies/PrefabSentinel.Editor.dll"
            dll.write_bytes(b"new bridge")
            line = (
                "Assets/Test.cs(4,2): error CS1002: ; expected\n"
                if self.deploy_result["data"].get("append_compile_error")
                else "Reloading assemblies after finishing script compilation.\n"
            )
            self._unity_log.write_text(
                self._unity_log.read_text(encoding="utf-8") + line,
                encoding="utf-8",
            )
        return self.deploy_result

    async def recompile(self, timeout_sec: int) -> dict[str, Any]:
        self.calls.append("recompile")
        return self.recompile_result

    async def console_errors(self, since_seconds: float) -> dict[str, Any]:
        self.calls.append("console_errors")
        return _envelope(code="CONSOLE_OK", data={"entries": []})

    async def reflect_game_object(self) -> dict[str, Any]:
        self.calls.append("reflect_game_object")
        return _envelope(code="REFLECT_OK")

    async def runtime_console_probe(self, scene_path: str) -> dict[str, Any]:
        self.calls.append("runtime_console_probe")
        return _envelope(code="RUNTIME_OK")

    async def run_acceptance(self, run_id: str, timeout_sec: int) -> dict[str, Any]:
        self.calls.append("run_acceptance")
        self.acceptance_calls.append(("run_acceptance", run_id, timeout_sec))
        return self._for_run(self.smoke_result, run_id)

    async def cleanup_acceptance(self, run_id: str, timeout_sec: int) -> dict[str, Any]:
        self.calls.append("cleanup")
        self.acceptance_calls.append(("cleanup", run_id, timeout_sec))
        return self._for_run(self.cleanup_result, run_id)

    async def acceptance_status(self, run_id: str) -> dict[str, Any]:
        self.calls.append("acceptance_status")
        self.acceptance_calls.append(("acceptance_status", run_id, None))
        return self._for_run(self.acceptance_status_result, run_id)

    def connection_identity(self) -> dict[str, str | None]:
        return {
            "protocol_revision": "2026-07-28",
            "server_name": "prefab-sentinel",
            "server_version": "0.9.0",
        }


def _clean_status() -> dict[str, Any]:
    return _envelope(
        code="SESSION_STATUS",
        data={
            "blockers": [],
            "bridge": {
                "connected": True,
                "connection_state": "connected",
                "code": None,
                "blocker_class": None,
                "suggested_next_action": None,
            },
            "session_id": "session-1",
            "bridge_session_id": "bridge-session-1",
            "bridge_instance_id": "bridge-instance-1",
            "project_root_consistent": True,
            "bridge_version": "0.9.0",
            "unity_version": "2022.3.22f1",
            "required_packages": [
                {"name": "com.vrchat.base", "ready": True, "version": "3.10.2"},
                {"name": "com.vrchat.worlds", "ready": True, "version": "3.10.2"},
                {"name": "udonsharp", "ready": True, "version": "3.10.2"},
            ],
            "active_stage_kind": "main",
            "active_scene_name": "Acceptance",
            "active_scene_path": "Assets/Acceptance.unity",
            "open_scenes": [
                {
                    "path": "Assets/Acceptance.unity",
                    "name": "Acceptance",
                    "is_dirty": False,
                }
            ],
            "has_unsaved_changes": False,
            "dirty_scene_paths": [],
            "dirty_prefab_paths": [],
            "dirty_material_paths": [],
            "dirty_asset_paths": [],
            "prefab_stage_is_dirty": False,
            "prefab_stage_asset_path": "",
        },
    )


def _unavailable_bridge_status() -> dict[str, Any]:
    return _envelope(
        code="SESSION_STATUS",
        data={
            "blockers": [{"blocker_class": "bridge_connection"}],
            "bridge": {
                "connected": False,
                "connection_state": "unavailable",
                "code": "EDITOR_BRIDGE_STATUS_UNAVAILABLE",
                "blocker_class": "bridge_connection",
                "suggested_next_action": "wait for reload",
            },
        },
    )


def _dirty_scene_status() -> dict[str, Any]:
    return _envelope(
        code="SESSION_STATUS",
        data={
            "blockers": [
                {
                    "blocker_class": "scene_dirty",
                    "message": "A loaded Scene has unsaved changes.",
                }
            ]
        },
    )


def _config(tmp_path: Path, *, confirm_live: bool = True) -> AcceptanceConfig:
    project_root = tmp_path / "UnityProject"
    (project_root / "Assets").mkdir(parents=True)
    (project_root / "Library/ScriptAssemblies").mkdir(parents=True)
    (project_root / "Library/ScriptAssemblies/PrefabSentinel.Editor.dll").write_bytes(
        b"old bridge"
    )
    (project_root / "reports").mkdir()
    unity_log = tmp_path / "Editor.log"
    unity_log.write_text(
        (
            "COMMAND LINE ARGUMENTS:\n"
            "Unity\n"
            "-projectPath\n"
            f"{project_root}\n"
            "before\n"
        ),
        encoding="utf-8",
    )
    watch_dir = tmp_path / "watch"
    watch_dir.mkdir()
    return AcceptanceConfig(
        repo_root=tmp_path / "repo",
        project_root=project_root,
        scope="Assets/Acceptance",
        target_dir=project_root / "Assets/Editor/PrefabSentinel",
        watch_dir=str(watch_dir),
        unity_log_file=unity_log,
        out_report="reports/acceptance.json",
        confirm_live=confirm_live,
    )


def _identity() -> AcceptanceSourceIdentity:
    return AcceptanceSourceIdentity(
        head="a" * 40,
        branch="issue-186",
        managed_dirty_paths=(),
        package_versions={
            "python": "0.9.0",
            "claude_plugin": "0.9.0",
            "codex_plugin": "0.9.0",
            "bridge": "0.9.0",
        },
        bridge_files=(),
        bridge_manifest_sha256="b" * 64,
    )


@pytest.fixture
def clean_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "prefab_sentinel.unity_acceptance.controller.collect_source_identity",
        lambda repo_root: _identity(),
    )

@pytest.fixture
def fixed_run_id(monkeypatch: pytest.MonkeyPatch) -> str:
    run_id = "1" * 32

    class FixedUuid:
        hex = run_id

    monkeypatch.setattr(
        "prefab_sentinel.unity_acceptance.controller.uuid4",
        FixedUuid,
    )
    return run_id


def test_missing_opt_in_stops_before_report_or_transport(tmp_path: Path) -> None:
    transport = RecordingTransport()
    clock = FakeClock()

    result = asyncio.run(
        run_acceptance(
            _config(tmp_path, confirm_live=False),
            transport=transport,
            clock=clock,
            sleep=clock.sleep,
        )
    )

    assert (result.success, result.code, transport.calls) == (
        False,
        "ACCEPTANCE_OPT_IN_REQUIRED",
        [],
    )
    assert result.to_dict()["audit"] == {
        "executed": True,
        "success": False,
        "code": "ACCEPTANCE_OPT_IN_REQUIRED",
        "data": {
            "opt_in": False,
            "mode": "acceptance",
            "arguments": {
                "project_root": "<project-root>",
                "scope": "<redacted>",
                "target_dir": "<redacted>",
                "watch_dir": "<redacted>",
                "unity_log_file": "<redacted>",
                "out_report": "<redacted>",
                "recover_run_id": "",
            },
            "deadlines": {
                "recompile_timeout_sec": 120,
                "environment_timeout_sec": 120,
                "smoke_timeout_sec": 300,
            },
        },
        "diagnostics": [],
    }


def test_dirty_editor_stops_before_deploy(
    tmp_path: Path,
    clean_identity: None,
) -> None:
    transport = RecordingTransport(initial_status=_dirty_scene_status())
    clock = FakeClock()

    result = asyncio.run(
        run_acceptance(_config(tmp_path), transport, clock, clock.sleep)
    )

    assert result.code == "ACCEPTANCE_EDITOR_STATE_BLOCKED"
    assert "deploy" not in transport.calls


def test_success_calls_transport_in_fixed_order(
    tmp_path: Path,
    clean_identity: None,
) -> None:
    transport = RecordingTransport()
    config = _config(tmp_path)
    transport.prepare_compile_evidence(config.project_root, config.unity_log_file)
    clock = FakeClock()

    result = asyncio.run(run_acceptance(config, transport, clock, clock.sleep))

    assert result.code == "ACCEPTANCE_OK"
    assert transport.calls == [
        "activate",
        "project_status",
        "deploy",
        "recompile",
        "project_status",
        "console_errors",
        "reflect_game_object",
        "runtime_console_probe",
        "run_acceptance",
        "acceptance_status",
        "cleanup",
        "project_status",
        "console_errors",
    ]
    assert [phase.name for phase in result.phases] == [
        "opt_in",
        "config",
        "report_reservation",
        "source_identity",
        "connection_identity",
        "activate",
        "editor_state",
        "compile_baseline",
        "deploy",
        "compile_observer",
        "recompile",
        "environment",
        "console",
        "reflection",
        "runtime_probe",
        "smoke",
        "acceptance_status",
        "cleanup",
        "postconditions",
    ]


def test_predeploy_status_does_not_require_fields_owned_by_the_new_bridge(
    tmp_path: Path,
    clean_identity: None,
) -> None:
    """Moving package readiness before reload would deadlock an old-to-new Bridge update."""
    predeploy_status = _clean_status()
    predeploy_status["data"].pop("unity_version")
    predeploy_status["data"].pop("required_packages")
    transport = RecordingTransport(initial_status=predeploy_status)
    config = _config(tmp_path)
    transport.prepare_compile_evidence(config.project_root, config.unity_log_file)

    result = asyncio.run(
        run_acceptance(config, transport, FakeClock(), lambda _: None)
    )

    assert result.code == "ACCEPTANCE_OK"
    assert transport.calls == [
        "activate",
        "project_status",
        "deploy",
        "recompile",
        "project_status",
        "console_errors",
        "reflect_game_object",
        "runtime_console_probe",
        "run_acceptance",
        "acceptance_status",
        "cleanup",
        "project_status",
        "console_errors",
    ]
    environment_phase = next(
        phase for phase in result.phases if phase.name == "environment"
    )
    assert environment_phase.data["unity_version"] == "2022.3.22f1"
    assert all(
        package["ready"] is True
        for package in environment_phase.data["required_packages"]
    )


def test_post_reload_environment_waits_for_fresh_status(
    tmp_path: Path,
    clean_identity: None,
) -> None:
    class ReloadingStatusTransport(RecordingTransport):
        async def project_status(self) -> dict[str, Any]:
            self.calls.append("project_status")
            status_call = self.calls.count("project_status")
            if status_call == 1:
                return self.initial_status
            if status_call == 2:
                return _unavailable_bridge_status()
            if status_call == 3:
                return self.environment_status
            return self.final_status

    transport = ReloadingStatusTransport()
    config = _config(tmp_path)
    transport.prepare_compile_evidence(config.project_root, config.unity_log_file)
    clock = FakeClock()

    result = asyncio.run(
        run_acceptance(config, transport, clock, clock.sleep)
    )

    assert result.code == "ACCEPTANCE_OK"
    assert clock.now == 2.0
    assert transport.calls.count("project_status") == 4


def test_post_reload_environment_unavailable_stops_at_fixed_deadline(
    tmp_path: Path,
    clean_identity: None,
) -> None:
    class UnavailableStatusTransport(RecordingTransport):
        async def project_status(self) -> dict[str, Any]:
            self.calls.append("project_status")
            if self.calls.count("project_status") == 1:
                return self.initial_status
            return _unavailable_bridge_status()

    transport = UnavailableStatusTransport()
    config = _config(tmp_path)
    transport.prepare_compile_evidence(config.project_root, config.unity_log_file)
    clock = FakeClock()

    result = asyncio.run(
        run_acceptance(config, transport, clock, clock.sleep)
    )

    assert (result.success, result.code, result.failed_phase) == (
        False,
        "ACCEPTANCE_EDITOR_STATE_BLOCKED",
        "environment",
    )
    assert clock.now == 121.0
    assert "console_errors" not in transport.calls

def test_postconditions_wait_for_fresh_status_after_cleanup(
    tmp_path: Path,
    clean_identity: None,
) -> None:
    class ReloadingFinalStatusTransport(RecordingTransport):
        async def project_status(self) -> dict[str, Any]:
            self.calls.append("project_status")
            status_call = self.calls.count("project_status")
            if status_call == 1:
                return self.initial_status
            if status_call == 2:
                return self.environment_status
            if status_call == 3:
                return _unavailable_bridge_status()
            return self.final_status

    transport = ReloadingFinalStatusTransport()
    config = _config(tmp_path)
    transport.prepare_compile_evidence(config.project_root, config.unity_log_file)
    clock = FakeClock()

    result = asyncio.run(
        run_acceptance(config, transport, clock, clock.sleep)
    )

    assert result.code == "ACCEPTANCE_OK"
    assert clock.now == 2.0
    assert transport.calls.count("project_status") == 4


def test_post_reload_environment_requires_unity_2022_3(
    tmp_path: Path,
    clean_identity: None,
) -> None:
    environment_status = _clean_status()
    environment_status["data"]["unity_version"] = "2023.2.0f1"
    transport = RecordingTransport(environment_status=environment_status)
    config = _config(tmp_path)
    transport.prepare_compile_evidence(config.project_root, config.unity_log_file)
    clock = FakeClock()

    result = asyncio.run(
        run_acceptance(config, transport, clock, clock.sleep)
    )

    assert (result.success, result.code, result.failed_phase) == (
        False,
        "ACCEPTANCE_EDITOR_STATE_BLOCKED",
        "environment",
    )
    assert transport.calls == [
        "activate",
        "project_status",
        "deploy",
        "recompile",
        "project_status",
    ]


def test_controller_owns_safe_semantic_evidence_sections(
    tmp_path: Path,
    clean_identity: None,
) -> None:
    transport = RecordingTransport()
    config = _config(tmp_path)
    transport.prepare_compile_evidence(config.project_root, config.unity_log_file)

    payload = asyncio.run(
        run_acceptance(config, transport, FakeClock(), lambda _: None)
    ).to_dict()

    assert payload["audit"]["data"] == {
        "opt_in": True,
        "mode": "acceptance",
        "arguments": {
            "project_root": "<project-root>",
            "scope": "Assets/Acceptance",
            "target_dir": "Assets/Editor/PrefabSentinel",
            "watch_dir": "<redacted>",
            "unity_log_file": "<redacted>",
            "out_report": "reports/acceptance.json",
            "recover_run_id": "",
        },
        "deadlines": {
            "recompile_timeout_sec": 120,
            "environment_timeout_sec": 120,
            "smoke_timeout_sec": 300,
        },
    }
    assert "editor_state" not in payload["environment"]["data"]
    assert payload["preflight"]["data"]["editor_state"] == {
        "active_stage_kind": "main",
        "has_unsaved_changes": False,
        "prefab_stage_is_dirty": False,
        "open_scene_count": 1,
        "dirty_counts": {
            "dirty_scene_paths": 0,
            "dirty_prefab_paths": 0,
            "dirty_material_paths": 0,
            "dirty_asset_paths": 0,
        },
    }
    serialized = repr(payload)
    assert str(config.project_root) not in serialized
    assert str(config.unity_log_file) not in serialized
    assert config.watch_dir not in serialized


def test_deploy_without_canonical_manifest_evidence_fails_closed(
    tmp_path: Path,
    clean_identity: None,
) -> None:
    transport = RecordingTransport(
        deploy=_envelope(code="DEPLOY_OK", data={"changed_deploy": True})
    )
    config = _config(tmp_path)
    transport.prepare_compile_evidence(config.project_root, config.unity_log_file)

    result = asyncio.run(run_acceptance(config, transport, FakeClock(), lambda _: None))

    assert (result.success, result.code, result.failed_phase) == (
        False,
        "ACCEPTANCE_DEPLOY_FAILED",
        "deploy",
    )
    assert transport.calls == ["activate", "project_status", "deploy"]
    assert result.to_dict()["deploy"]["data"]["manifest_equal"] is False


@pytest.mark.parametrize(
    ("promotion_state", "expected_recompile"),
    [("already_current", False), ("promoted", True)],
)
def test_deploy_consumes_the_safe_deploy_canonical_manifest_field(
    tmp_path: Path,
    clean_identity: None,
    monkeypatch: pytest.MonkeyPatch,
    promotion_state: str,
    expected_recompile: bool,
) -> None:
    """Reading #193 manifest/state fields must keep no-op compile evidence honest."""
    monkeypatch.setattr(
        "prefab_sentinel.unity_acceptance.controller.build_bridge_manifest",
        lambda target_dir: BridgeBundleManifest(
            bridge_version="0.9.0",
            files=(),
            sha256="b" * 64,
        ),
        raising=False,
    )
    transport = RecordingTransport(
        deploy=_envelope(
            code="DEPLOY_OK",
            data={
                "promotion_state": promotion_state,
                "manifest_sha256": "b" * 64,
                "bridge_version": "0.9.0",
            },
        )
    )
    config = _config(tmp_path)
    transport.prepare_compile_evidence(config.project_root, config.unity_log_file)
    clock = FakeClock()

    result = asyncio.run(
        run_acceptance(config, transport, clock, clock.sleep)
    )

    assert result.code == "ACCEPTANCE_OK"
    assert result.to_dict()["deploy"]["data"] == {
        "changed_deploy": False,
        "expected_manifest_sha256": "b" * 64,
        "observed_manifest_sha256": "b" * 64,
        "manifest_equal": True,
        "expected_bridge_version": "0.9.0",
        "observed_bridge_version": "0.9.0",
        "bridge_version_equal": True,
    }
    compile_data = result.to_dict()["compile"]["data"]["observation"]
    assert compile_data["compile_observation"] == "not_required"
    assert ("recompile" in transport.calls) is expected_recompile
    secondary = result.to_dict()["compile"]["data"]["secondary_recompile"]
    assert bool(secondary) is expected_recompile


def test_matching_files_with_an_old_running_bridge_still_require_compile_evidence(
    tmp_path: Path,
    clean_identity: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    initial_status = _clean_status()
    initial_status["data"]["bridge_version"] = "0.8.0"
    monkeypatch.setattr(
        "prefab_sentinel.unity_acceptance.controller.build_bridge_manifest",
        lambda target_dir: BridgeBundleManifest(
            bridge_version="0.9.0",
            files=(),
            sha256="b" * 64,
        ),
    )
    transport = RecordingTransport(initial_status=initial_status)
    config = _config(tmp_path)
    transport.prepare_compile_evidence(config.project_root, config.unity_log_file)
    clock = FakeClock()

    result = asyncio.run(
        run_acceptance(config, transport, clock, clock.sleep)
    )

    assert result.code == "ACCEPTANCE_OK"
    deploy_data = result.to_dict()["deploy"]["data"]
    assert deploy_data["changed_deploy"] is True
    compile_data = result.to_dict()["compile"]["data"]["observation"]
    assert compile_data["compile_observation"] == "observed"


def test_deploy_without_canonical_promotion_state_fails_closed(
    tmp_path: Path,
    clean_identity: None,
) -> None:
    """Inventing changed-deploy defaults would contradict #193 deployment evidence."""
    transport = RecordingTransport(
        deploy=_envelope(
            code="DEPLOY_OK",
            data={
                "manifest_sha256": "b" * 64,
                "bridge_version": "0.9.0",
            },
        )
    )
    config = _config(tmp_path)
    transport.prepare_compile_evidence(config.project_root, config.unity_log_file)
    clock = FakeClock()

    result = asyncio.run(
        run_acceptance(config, transport, clock, clock.sleep)
    )

    assert (result.success, result.code, result.failed_phase) == (
        False,
        "ACCEPTANCE_DEPLOY_FAILED",
        "deploy",
    )
    deploy_data = result.to_dict()["deploy"]["data"]
    assert deploy_data["changed_deploy"] is True
    assert deploy_data["manifest_equal"] is True


def test_recovery_only_run_stops_after_same_run_cleanup_and_postcheck(
    tmp_path: Path,
) -> None:
    ordinary = _config(tmp_path)
    run_id = "a" * 32
    config = AcceptanceConfig(
        repo_root=ordinary.repo_root,
        project_root=ordinary.project_root,
        scope=ordinary.scope,
        target_dir=ordinary.target_dir,
        watch_dir=ordinary.watch_dir,
        unity_log_file=ordinary.unity_log_file,
        out_report=ordinary.out_report,
        confirm_live=True,
        recover_run_id=run_id,
    )
    transport = RecordingTransport(
        acceptance_status=_envelope(
            code="ACCEPTANCE_STATUS_OK",
            data={
                "run_id": run_id,
                "phase": "reserved",
                "cleanup_required": True,
                "cleanup_performed": False,
            },
        )
    )

    result = asyncio.run(run_acceptance(config, transport, FakeClock(), lambda _: None))

    assert (result.success, result.code) == (True, "ACCEPTANCE_OK")
    assert transport.calls == [
        "activate",
        "acceptance_status",
        "cleanup",
        "project_status",
        "console_errors",
    ]
    assert {"deploy", "recompile", "run_acceptance"}.isdisjoint(transport.calls)
    audit = result.to_dict()["audit"]["data"]
    assert audit["mode"] == "recovery"
    assert audit["arguments"]["recover_run_id"] == "<redacted>"
    assert audit["deadlines"] == {
        "recompile_timeout_sec": 120,
        "environment_timeout_sec": 120,
        "smoke_timeout_sec": 300,
    }


def test_recovery_only_run_postchecks_even_when_cleanup_fails(tmp_path: Path) -> None:
    ordinary = _config(tmp_path)
    config = AcceptanceConfig(
        repo_root=ordinary.repo_root,
        project_root=ordinary.project_root,
        scope=ordinary.scope,
        target_dir=ordinary.target_dir,
        watch_dir=ordinary.watch_dir,
        unity_log_file=ordinary.unity_log_file,
        out_report=ordinary.out_report,
        confirm_live=True,
        recover_run_id="a" * 32,
    )
    transport = RecordingTransport(
        cleanup=_envelope(
            success=False,
            code="EDITOR_CTRL_ACCEPTANCE_CLEANUP_FAILED",
        )
    )

    result = asyncio.run(run_acceptance(config, transport, FakeClock(), lambda _: None))

    assert (result.success, result.code) == (False, "ACCEPTANCE_CLEANUP_FAILED")
    assert transport.calls == [
        "activate",
        "acceptance_status",
        "cleanup",
        "project_status",
        "console_errors",
    ]
    assert {"deploy", "recompile", "run_acceptance"}.isdisjoint(transport.calls)


@pytest.mark.parametrize(
    (
        "case",
        "transport_kwargs",
        "identity_failure",
        "expected_code",
        "failed_phase",
        "later_call",
        "expected_later_call_count",
    ),
    [
        (
            "source dirty",
            {},
            ToolResponse(False, Severity.ERROR, "ACCEPTANCE_SOURCE_DIRTY", "dirty"),
            "ACCEPTANCE_SOURCE_DIRTY",
            "source_identity",
            "activate",
            0,
        ),
        (
            "deploy failure",
            {"deploy": _envelope(success=False, code="DEPLOY_FAILED")},
            None,
            "ACCEPTANCE_DEPLOY_FAILED",
            "deploy",
            "recompile",
            0,
        ),
        (
            "secondary recompile failure",
            {"recompile": _envelope(success=False, code="RECOMPILE_FAILED")},
            None,
            "ACCEPTANCE_COMPILE_FAILED",
            "recompile",
            "console_errors",
            0,
        ),
        (
            "smoke failure",
            {
                "smoke": _envelope(
                    success=False,
                    code="SMOKE_FAILED",
                    data={"fixture_owned": True},
                ),
                "acceptance_status": _envelope(
                    code="ACCEPTANCE_STATUS_OK",
                    data={"phase": "smoke_complete", "cleanup_required": True, "cleanup_performed": False},
                ),
            },
            None,
            "ACCEPTANCE_SMOKE_FAILED",
            "smoke",
            "project_status",
            3,
        ),
        (
            "cleanup failure overrides smoke success",
            {
                "smoke": _envelope(
                    code="SMOKE_OK",
                    data={"fixture_owned": True},
                ),
                "acceptance_status": _envelope(
                    code="ACCEPTANCE_STATUS_OK",
                    data={"phase": "smoke_complete", "cleanup_required": True, "cleanup_performed": False},
                ),
                "cleanup": _envelope(success=False, code="CLEANUP_FAILED"),
            },
            None,
            "ACCEPTANCE_CLEANUP_FAILED",
            "cleanup",
            "project_status",
            3,
        ),
        (
            "final scene mismatch",
            {
                "final_status": _envelope(
                    code="SESSION_STATUS",
                    data={"blockers": [{"blocker_class": "scene_dirty"}]},
                )
            },
            None,
            "ACCEPTANCE_POSTCONDITION_FAILED",
            "postconditions",
            "console_errors",
            2,
        ),
    ],
)
def test_failure_matrix_stops_at_the_first_terminal_phase(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    case: str,
    transport_kwargs: dict[str, dict[str, Any]],
    identity_failure: ToolResponse | None,
    expected_code: str,
    failed_phase: str,
    later_call: str,
    expected_later_call_count: int,
) -> None:
    monkeypatch.setattr(
        "prefab_sentinel.unity_acceptance.controller.collect_source_identity",
        lambda repo_root: identity_failure or _identity(),
    )
    transport = RecordingTransport(**transport_kwargs)
    config = _config(tmp_path)
    transport.prepare_compile_evidence(config.project_root, config.unity_log_file)
    clock = FakeClock()

    result = asyncio.run(run_acceptance(config, transport, clock, clock.sleep))

    assert (result.success, result.code, result.failed_phase) == (
        False,
        expected_code,
        failed_phase,
    )
    assert transport.calls.count(later_call) == expected_later_call_count


@pytest.mark.parametrize("surface", ["returned", "published"])
def test_source_dirty_report_keeps_unexecuted_sections_neutral(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    surface: str,
) -> None:
    monkeypatch.setattr(
        "prefab_sentinel.unity_acceptance.controller.collect_source_identity",
        lambda repo_root: ToolResponse(
            False, Severity.ERROR, "ACCEPTANCE_SOURCE_DIRTY", "dirty",
        ),
    )
    transport = RecordingTransport()
    config = _config(tmp_path)
    clock = FakeClock()

    result = asyncio.run(run_acceptance(config, transport, clock, clock.sleep))
    returned = result.to_dict()
    published = json.loads(
        (config.project_root / config.out_report).read_text(encoding="utf-8")
    )
    assert returned == published
    payload = returned if surface == "returned" else published

    # Literal expectations are independent of the model and controller
    # projection: equality between two identically wrong reports is not enough.
    neutral: dict[str, Any] = {
        "executed": False,
        "success": None,
        "code": None,
        "data": {},
        "diagnostics": [],
    }
    for name in ("environment", "deploy", "compile", "smoke", "cleanup"):
        assert payload[name] == neutral, (surface, name)
    assert payload["source"] == {
        "executed": True,
        "success": False,
        "code": "ACCEPTANCE_SOURCE_DIRTY",
        "data": {"managed_source_clean": False, "managed_dirty_paths": []},
        "diagnostics": [],
    }
    assert payload["preflight"] == {
        "executed": True,
        "success": True,
        "code": "ACCEPTANCE_REPORT_RESERVED",
        "data": {
            "config_validation": {"success": True, "code": "ACCEPTANCE_CONFIG_OK"},
            "report_reservation": {"success": True, "code": "ACCEPTANCE_REPORT_RESERVED"},
            "editor_state": None,
            "blockers": [],
        },
        "diagnostics": [],
    }
    assert (
        payload["schema_version"],
        payload["result"]["success"],
        payload["result"]["severity"],
        payload["result"]["code"],
        payload["result"]["failed_phase"],
    ) == (
        "unity_bridge_acceptance.v1",
        False,
        "error",
        "ACCEPTANCE_SOURCE_DIRTY",
        "source_identity",
    )
    assert [phase["name"] for phase in payload["result"]["phases"]] == [
        "opt_in", "config", "report_reservation", "source_identity",
    ]
    assert transport.calls == []
    assert transport.acceptance_calls == []


def test_compile_observer_failure_does_not_reconnect_the_new_bridge(
    tmp_path: Path,
    clean_identity: None,
) -> None:
    transport = RecordingTransport(
        deploy=_envelope(
            code="DEPLOY_OK",
            data={
                "promotion_state": "promoted",
                "append_compile_error": True,
                "manifest_sha256": "b" * 64,
                "bridge_version": "0.9.0",
            },
        )
    )
    config = _config(tmp_path)
    transport.prepare_compile_evidence(config.project_root, config.unity_log_file)
    clock = FakeClock()

    result = asyncio.run(run_acceptance(config, transport, clock, clock.sleep))

    assert (result.code, result.failed_phase) == (
        "ACCEPTANCE_COMPILE_FAILED",
        "compile_observer",
    )
    assert "recompile" not in transport.calls


def test_report_publication_failure_is_terminal_and_sanitized(
    tmp_path: Path,
    clean_identity: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    transport = RecordingTransport()
    config = _config(tmp_path)
    transport.prepare_compile_evidence(config.project_root, config.unity_log_file)
    clock = FakeClock()

    def fail_publication(*args: object, **kwargs: object) -> None:
        raise OSError("private path is unavailable")

    monkeypatch.setattr(
        "prefab_sentinel.unity_acceptance.controller.publish_acceptance_report",
        fail_publication,
    )

    result = asyncio.run(run_acceptance(config, transport, clock, clock.sleep))

    assert (result.success, result.code, result.failed_phase) == (
        False,
        "ACCEPTANCE_REPORT_WRITE_FAILED",
        "report_publication",
    )
    assert "private path" not in result.message


_PRIVATE_PATH = "/home/alice/UnityProject/Library/PrefabSentinel/private-response.json"
_PRIVATE_EXCEPTION = "OSError: /home/alice/UnityProject/secret/request.json"


def _hostile(response: dict[str, Any]) -> dict[str, Any]:
    return {
        **response,
        "message": _PRIVATE_EXCEPTION,
        "data": {
            **response.get("data", {}),
            "raw_absolute_path": _PRIVATE_PATH,
            "exception": {
                "type": "OSError",
                "message": _PRIVATE_EXCEPTION,
                "short_stack": _PRIVATE_PATH,
            },
        },
        "diagnostics": [
            {
                "severity": "error",
                "code": "HOSTILE_DIAGNOSTIC",
                "message": _PRIVATE_EXCEPTION,
                "data": {"path": _PRIVATE_PATH},
            }
        ],
    }




def _documented_report_contract() -> dict[str, Any]:
    document = (
        Path(__file__).resolve().parents[1] / "docs/execution-reference.md"
    ).read_text(encoding="utf-8")
    start = document.index("### `unity_bridge_acceptance.v1` report")
    end = document.index("\n### Unresolved lease recovery", start)
    match = re.search(r"```json\n(.*?)\n```", document[start:end], re.DOTALL)
    assert match is not None
    contract = json.loads(match.group(1))
    assert isinstance(contract, dict)
    return contract

class HostileEvidenceTransport(RecordingTransport):
    async def activate(self, project_root: str, scope: str) -> dict[str, Any]:
        return _hostile(await super().activate(project_root, scope))

    async def project_status(self) -> dict[str, Any]:
        return _hostile(await super().project_status())

    async def deploy(self, target_dir: str) -> dict[str, Any]:
        return _hostile(await super().deploy(target_dir))

    async def recompile(self, timeout_sec: int) -> dict[str, Any]:
        return _hostile(await super().recompile(timeout_sec))

    async def console_errors(self, since_seconds: float) -> dict[str, Any]:
        return _hostile(await super().console_errors(since_seconds))

    async def reflect_game_object(self) -> dict[str, Any]:
        return _hostile(await super().reflect_game_object())

    async def runtime_console_probe(self, scene_path: str) -> dict[str, Any]:
        return _hostile(await super().runtime_console_probe(scene_path))

    async def run_acceptance(self, run_id: str, timeout_sec: int) -> dict[str, Any]:
        return _hostile(await super().run_acceptance(run_id, timeout_sec))

    async def acceptance_status(self, run_id: str) -> dict[str, Any]:
        return _hostile(await super().acceptance_status(run_id))

    async def cleanup_acceptance(self, run_id: str, timeout_sec: int) -> dict[str, Any]:
        return _hostile(await super().cleanup_acceptance(run_id, timeout_sec))


class TestCompileReportEvidence(TestCase):
    def test_forwards_sanitized_superseded_error_history(self) -> None:
        phase = _compile_phase(
            CompileObservation(
                success=False,
                code="ACCEPTANCE_COMPILE_FAILED",
                dll_size=10,
                dll_mtime_ns=20,
                dll_sha256="a" * 64,
                compiler_errors=("error CS1003",),
                superseded_compiler_errors=("error CS2001",),
            )
        )

        evidence = _evidence_from_phases((phase,))

        self.assertEqual(phase.data["compiler_error_count"], 1)
        self.assertEqual(phase.data["superseded_compiler_error_count"], 1)
        self.assertEqual(
            phase.data["superseded_compiler_errors"],
            ["error CS2001"],
        )
        self.assertEqual(evidence.compile.data["observation"], phase.data)


def test_normal_report_carries_complete_phase_owned_evidence(
    tmp_path: Path,
    clean_identity: None,
    fixed_run_id: str,
) -> None:
    transport = RecordingTransport()
    config = _config(tmp_path)
    transport.prepare_compile_evidence(config.project_root, config.unity_log_file)

    payload = asyncio.run(
        run_acceptance(config, transport, FakeClock(), lambda _: None)
    ).to_dict()

    assert payload["source"]["data"] == {
        "head": "a" * 40,
        "branch": "issue-186",
        "managed_source_clean": True,
        "managed_dirty_paths": [],
        "package_versions": {
            "python": "0.9.0",
            "claude_plugin": "0.9.0",
            "codex_plugin": "0.9.0",
            "bridge": "0.9.0",
        },
        "bridge_manifest_sha256": "b" * 64,
        "bridge_files": [],
        "mcp_protocol_revision": "2026-07-28",
        "mcp_server": {"name": "prefab-sentinel", "version": "0.9.0"},
    }
    assert payload["environment"]["data"] == {
        "unity_version": "2022.3.22f1",
        "required_packages": [
            {"name": "com.vrchat.base", "ready": True, "version": "3.10.2"},
            {"name": "com.vrchat.worlds", "ready": True, "version": "3.10.2"},
            {"name": "udonsharp", "ready": True, "version": "3.10.2"},
        ],
        "connection_identity": {
            "protocol_revision": "2026-07-28",
            "server_name": "prefab-sentinel",
            "server_version": "0.9.0",
        },
        "project_session": {
            "connection_state": "connected",
            "project_active": True,
            "project_root_consistent": True,
            "session_id": "session-1",
            "bridge_session_id": "bridge-session-1",
            "bridge_instance_id": "bridge-instance-1",
        },
    }
    assert payload["preflight"]["data"] == {
        "config_validation": {"success": True, "code": "ACCEPTANCE_CONFIG_OK"},
        "report_reservation": {"success": True, "code": "ACCEPTANCE_REPORT_RESERVED"},
        "editor_state": {
            "active_stage_kind": "main",
            "has_unsaved_changes": False,
            "prefab_stage_is_dirty": False,
            "open_scene_count": 1,
            "dirty_counts": {
                "dirty_scene_paths": 0,
                "dirty_prefab_paths": 0,
                "dirty_material_paths": 0,
                "dirty_asset_paths": 0,
            },
        },
        "blockers": [],
    }
    assert payload["deploy"]["data"] == {
        "changed_deploy": True,
        "expected_manifest_sha256": "b" * 64,
        "observed_manifest_sha256": "b" * 64,
        "manifest_equal": True,
        "expected_bridge_version": "0.9.0",
        "observed_bridge_version": "0.9.0",
        "bridge_version_equal": True,
    }
    assert payload["compile"]["data"]["baseline"]["log"]["continuity"] == "captured"
    assert payload["compile"]["data"]["observation"]["code"] == "ACCEPTANCE_COMPILE_OK"
    assert payload["compile"]["data"]["observation"]["compiler_error_count"] == 0
    assert payload["compile"]["data"]["observation"]["superseded_compiler_error_count"] == 0
    assert payload["compile"]["data"]["observation"]["superseded_compiler_errors"] == []
    assert payload["compile"]["data"]["secondary_recompile"] == {
        "success": True,
        "code": "RECOMPILE_OK",
    }
    assert payload["compile"]["data"]["console"] == {
        "success": True,
        "code": "CONSOLE_OK",
        "error_count": 0,
    }
    assert payload["smoke"]["data"]["suite"] == {
        "success": True,
        "code": "ACCEPTANCE_RUN_OK",
        "total": 6,
        "passed": 6,
        "failed": 0,
        "cases": [
            {"name": name, "passed": True, "code": "ACCEPTANCE_CASE_OK"}
            for name in (
                "Acceptance_SavedSceneAndPrefabFixture",
                "Acceptance_DuplicateSameNameObjects",
                "Acceptance_TransformMutationReadback",
                "Acceptance_PrimitivePropertyOverride",
                "Acceptance_EditorBridgeRequest",
                "Acceptance_ConsoleErrorZero",
            )
        ],
        "run_ownership": {"fixture_owned": True, "run_id": fixed_run_id, "lease_phase": "smoke_complete"},
    }
    assert payload["cleanup"]["data"]["cleanup"]["cleanup_performed"] is True
    assert payload["cleanup"]["data"]["postconditions"]["editor_state_equal"] is True
    assert payload["cleanup"]["data"]["postconditions"]["final_console_zero"] is True


def test_document_contract_binds_controller_success_shape(
    tmp_path: Path,
    clean_identity: None,
) -> None:
    contract = _documented_report_contract()
    assert contract["deadline_values"] == {
        "recompile_timeout_sec": 120,
        "environment_timeout_sec": 120,
        "smoke_timeout_sec": 300,
    }
    assert contract["recovery_only_sections"] == [
        "audit",
        "environment",
        "preflight",
        "cleanup",
        "result",
    ]
    assert contract["terminal_precedence"] == [
        "cleanup_failure",
        "postcondition_failure",
        "smoke_failure",
        "success",
    ]

    transport = RecordingTransport()
    config = _config(tmp_path)
    transport.prepare_compile_evidence(config.project_root, config.unity_log_file)
    payload = asyncio.run(
        run_acceptance(config, transport, FakeClock(), lambda _: None)
    ).to_dict()

    for section, expected_keys in contract["section_data_keys"].items():
        assert list(payload[section]["data"]) == expected_keys
    assert payload["audit"]["data"]["deadlines"] == contract["deadline_values"]


def test_recovery_keeps_non_recovery_sections_neutral(
    tmp_path: Path,
) -> None:
    ordinary = _config(tmp_path)
    config = AcceptanceConfig(
        repo_root=ordinary.repo_root,
        project_root=ordinary.project_root,
        scope=ordinary.scope,
        target_dir=ordinary.target_dir,
        watch_dir=ordinary.watch_dir,
        unity_log_file=ordinary.unity_log_file,
        out_report=ordinary.out_report,
        confirm_live=True,
        recover_run_id="a" * 32,
    )
    transport = RecordingTransport()

    payload = asyncio.run(
        run_acceptance(config, transport, FakeClock(), lambda _: None)
    ).to_dict()

    neutral: dict[str, Any] = {
        "executed": False,
        "success": None,
        "code": None,
        "data": {},
        "diagnostics": [],
    }
    assert {name: payload[name] for name in ("source", "deploy", "compile", "smoke")} == {
        name: neutral for name in ("source", "deploy", "compile", "smoke")
    }
    assert payload["environment"]["data"]["connection_identity"]["protocol_revision"] == "2026-07-28"
    assert payload["preflight"]["executed"] is True
    assert payload["cleanup"]["data"]["cleanup"]["cleanup_performed"] is True
    assert transport.calls[-2:] == ["project_status", "console_errors"]


@pytest.mark.parametrize(
    ("smoke", "cleanup", "expected_code"),
    [
        (
            _envelope(
                success=False,
                code="SMOKE_FAILED",
                data={"fixture_owned": True, "lease_phase": "fixture_created"},
            ),
            _envelope(
                code="ACCEPTANCE_CLEANUP_OK",
                data={
                    "phase": "cleaned",
                    "cleanup_required": False,
                    "cleanup_performed": True,
                    "scene_setup_restored": True,
                    "deleted_fixture_count": 1,
                    "deleted_request_artifact_count": 5,
                    "lease_removed": True,
                },
            ),
            "ACCEPTANCE_SMOKE_FAILED",
        ),
        (
            _envelope(
                code="ACCEPTANCE_RUN_OK",
                data={"fixture_owned": True, "lease_phase": "smoke_complete"},
            ),
            _envelope(
                success=False,
                code="EDITOR_CTRL_ACCEPTANCE_CLEANUP_FAILED",
                data={
                    "phase": "cleanup_started",
                    "cleanup_required": True,
                    "cleanup_performed": False,
                },
            ),
            "ACCEPTANCE_CLEANUP_FAILED",
        ),
    ],
)
def test_fixture_owned_failures_still_observe_final_state_and_console(
    tmp_path: Path,
    clean_identity: None,
    smoke: dict[str, Any],
    cleanup: dict[str, Any],
    expected_code: str,
) -> None:
    transport = RecordingTransport(smoke=smoke, cleanup=cleanup)
    config = _config(tmp_path)
    transport.prepare_compile_evidence(config.project_root, config.unity_log_file)

    result = asyncio.run(
        run_acceptance(config, transport, FakeClock(), lambda _: None)
    )

    assert result.code == expected_code
    assert transport.calls[-2:] == ["project_status", "console_errors"]
    postconditions = result.to_dict()["cleanup"]["data"]["postconditions"]
    assert postconditions["editor_state_equal"] is True
    assert postconditions["final_console_zero"] is True


def test_every_published_phase_and_section_is_path_free(
    tmp_path: Path,
    clean_identity: None,
) -> None:
    ordinary = _config(tmp_path)
    absolute_report = ordinary.project_root / "reports/hostile.json"
    config = AcceptanceConfig(
        repo_root=ordinary.repo_root,
        project_root=ordinary.project_root,
        scope=ordinary.scope,
        target_dir=ordinary.target_dir,
        watch_dir=ordinary.watch_dir,
        unity_log_file=ordinary.unity_log_file,
        out_report=str(absolute_report),
        confirm_live=True,
    )
    transport = HostileEvidenceTransport()
    transport.prepare_compile_evidence(config.project_root, config.unity_log_file)

    payload = asyncio.run(
        run_acceptance(config, transport, FakeClock(), lambda _: None)
    ).to_dict()

    serialized = repr(payload)
    assert _PRIVATE_PATH not in serialized
    assert _PRIVATE_EXCEPTION not in serialized
    assert str(absolute_report) not in serialized
    assert payload["audit"]["data"]["arguments"]["out_report"] == "reports/hostile.json"
    assert all(
        set(phase["diagnostics"][0]) <= {"severity", "code"}
        if phase["diagnostics"]
        else True
        for phase in payload["result"]["phases"]
    )

def test_early_opt_in_failure_redacts_every_unvalidated_path_argument(
    tmp_path: Path,
) -> None:
    ordinary = _config(tmp_path)
    config = AcceptanceConfig(
        repo_root=ordinary.repo_root,
        project_root=ordinary.project_root,
        scope="Assets/../../TOP_SECRET_SCOPE",
        target_dir=Path("/private/TOP_SECRET_TARGET"),
        watch_dir="TOP_SECRET_WATCH",
        unity_log_file=Path("/private/TOP_SECRET_LOG"),
        out_report="../TOP_SECRET_REPORT.json",
        confirm_live=False,
    )

    payload = asyncio.run(
        run_acceptance(config, RecordingTransport(), FakeClock(), lambda _: None)
    ).to_dict()

    expected_audit = {
        "executed": True,
        "success": False,
        "code": "ACCEPTANCE_OPT_IN_REQUIRED",
        "data": {
            "opt_in": False,
            "mode": "acceptance",
            "arguments": {
                "project_root": "<project-root>",
                "scope": "<redacted>",
                "target_dir": "<redacted>",
                "watch_dir": "<redacted>",
                "unity_log_file": "<redacted>",
                "out_report": "<redacted>",
                "recover_run_id": "",
            },
            "deadlines": {
                "recompile_timeout_sec": 120,
                "environment_timeout_sec": 120,
                "smoke_timeout_sec": 300,
            },
        },
        "diagnostics": [],
    }
    neutral: dict[str, Any] = {
        "executed": False,
        "success": None,
        "code": None,
        "data": {},
        "diagnostics": [],
    }
    assert payload["audit"] == expected_audit
    assert {
        name: payload[name]
        for name in (
            "source",
            "environment",
            "preflight",
            "deploy",
            "compile",
            "smoke",
            "cleanup",
        )
    } == {
        "source": neutral,
        "environment": neutral,
        "preflight": neutral,
        "deploy": neutral,
        "compile": neutral,
        "smoke": neutral,
        "cleanup": neutral,
    }
    assert payload["result"]["phases"] == [
        {
            "name": "opt_in",
            "success": False,
            "code": "ACCEPTANCE_OPT_IN_REQUIRED",
            "data": expected_audit["data"],
            "diagnostics": [],
        }
    ]
    serialized = json.dumps(payload, sort_keys=True)
    for hostile in (
        "TOP_SECRET_SCOPE",
        "TOP_SECRET_TARGET",
        "TOP_SECRET_WATCH",
        "TOP_SECRET_LOG",
        "TOP_SECRET_REPORT",
    ):
        assert hostile not in serialized


def test_recovery_success_accepts_zero_fixture_deletions_and_serializes_same_run(
    tmp_path: Path,
    fixed_run_id: str,
) -> None:
    ordinary = _config(tmp_path)
    config = AcceptanceConfig(
        repo_root=ordinary.repo_root,
        project_root=ordinary.project_root,
        scope=ordinary.scope,
        target_dir=ordinary.target_dir,
        watch_dir=ordinary.watch_dir,
        unity_log_file=ordinary.unity_log_file,
        out_report=ordinary.out_report,
        confirm_live=True,
        recover_run_id=fixed_run_id,
    )
    transport = RecordingTransport(
        acceptance_status=_envelope(
            code="ACCEPTANCE_STATUS_OK",
            data={
                "run_id": fixed_run_id,
                "phase": "reserved",
                "cleanup_required": True,
                "cleanup_performed": False,
            },
        ),
        cleanup=_envelope(
            code="ACCEPTANCE_CLEANUP_OK",
            data={
                "run_id": fixed_run_id,
                "phase": "cleaned",
                "cleanup_required": False,
                "cleanup_performed": True,
                "scene_setup_restored": True,
                "deleted_fixture_count": 0,
                "deleted_request_artifact_count": 5,
                "lease_removed": True,
            },
        ),
    )

    payload = asyncio.run(
        run_acceptance(config, transport, FakeClock(), lambda _: None)
    ).to_dict()

    neutral: dict[str, Any] = {
        "executed": False,
        "success": None,
        "code": None,
        "data": {},
        "diagnostics": [],
    }
    assert (payload["result"]["success"], payload["result"]["code"]) == (
        True,
        "ACCEPTANCE_OK",
    )
    assert {
        name: payload[name] for name in ("source", "deploy", "compile", "smoke")
    } == {
        "source": neutral,
        "deploy": neutral,
        "compile": neutral,
        "smoke": neutral,
    }
    assert payload["environment"]["data"]["project_session"] == {
        "connection_state": "connected",
        "project_active": True,
        "project_root_consistent": True,
        "session_id": "session-1",
        "bridge_session_id": "bridge-session-1",
        "bridge_instance_id": "bridge-instance-1",
    }
    assert payload["cleanup"]["data"]["status"] == {
        "success": True,
        "code": "ACCEPTANCE_STATUS_OK",
        "run_id": fixed_run_id,
        "phase": "reserved",
        "cleanup_required": True,
        "cleanup_performed": False,
    }
    assert payload["cleanup"]["data"]["cleanup"] == {
        "success": True,
        "code": "ACCEPTANCE_CLEANUP_OK",
        "run_id": fixed_run_id,
        "phase": "cleaned",
        "cleanup_required": False,
        "cleanup_performed": True,
        "scene_setup_restored": True,
        "deleted_fixture_count": 0,
        "deleted_request_artifact_count": 5,
        "lease_removed": True,
    }
    assert transport.calls == [
        "activate",
        "acceptance_status",
        "cleanup",
        "project_status",
        "console_errors",
    ]
    assert transport.acceptance_calls == [
        ("acceptance_status", fixed_run_id, None),
        ("cleanup", fixed_run_id, 300),
    ]


def test_recovery_success_envelope_without_cleanup_evidence_fails_closed(
    tmp_path: Path,
    fixed_run_id: str,
) -> None:
    ordinary = _config(tmp_path)
    config = AcceptanceConfig(
        repo_root=ordinary.repo_root,
        project_root=ordinary.project_root,
        scope=ordinary.scope,
        target_dir=ordinary.target_dir,
        watch_dir=ordinary.watch_dir,
        unity_log_file=ordinary.unity_log_file,
        out_report=ordinary.out_report,
        confirm_live=True,
        recover_run_id=fixed_run_id,
    )
    transport = RecordingTransport(
        acceptance_status=_envelope(
            code="ACCEPTANCE_STATUS_OK",
            data={
                "run_id": fixed_run_id,
                "phase": "reserved",
                "cleanup_required": True,
                "cleanup_performed": False,
            },
        ),
        cleanup=_envelope(
            code="ACCEPTANCE_CLEANUP_OK",
            data={"run_id": fixed_run_id},
        ),
    )

    payload = asyncio.run(
        run_acceptance(config, transport, FakeClock(), lambda _: None)
    ).to_dict()

    assert (payload["result"]["success"], payload["result"]["code"]) == (
        False,
        "ACCEPTANCE_CLEANUP_FAILED",
    )
    assert transport.calls[-2:] == ["project_status", "console_errors"]
    assert payload["cleanup"]["data"]["cleanup"] == {
        "success": True,
        "code": "ACCEPTANCE_CLEANUP_OK",
        "run_id": fixed_run_id,
        "phase": "",
        "cleanup_required": False,
        "cleanup_performed": False,
        "scene_setup_restored": False,
        "deleted_fixture_count": 0,
        "deleted_request_artifact_count": 0,
        "lease_removed": False,
    }


@pytest.mark.parametrize(
    ("fixture_count", "request_count", "expected_success"),
    [
        (-1, 0, False),
        (0, 0, True),
        (1, 0, True),
        (2, 0, False),
        (0, -1, False),
        (0, 5, True),
        (0, 6, False),
        (True, 0, False),
        (0, "0", False),
    ],
)
def test_recovery_cleanup_rejects_deletion_counts_outside_literal_bounds(
    tmp_path: Path,
    fixed_run_id: str,
    fixture_count: object,
    request_count: object,
    expected_success: bool,
) -> None:
    ordinary = _config(tmp_path)
    config = AcceptanceConfig(
        repo_root=ordinary.repo_root,
        project_root=ordinary.project_root,
        scope=ordinary.scope,
        target_dir=ordinary.target_dir,
        watch_dir=ordinary.watch_dir,
        unity_log_file=ordinary.unity_log_file,
        out_report=ordinary.out_report,
        confirm_live=True,
        recover_run_id=fixed_run_id,
    )
    transport = RecordingTransport(
        acceptance_status=_envelope(
            code="ACCEPTANCE_STATUS_OK",
            data={
                "run_id": fixed_run_id,
                "phase": "reserved",
                "cleanup_required": True,
                "cleanup_performed": False,
            },
        ),
        cleanup=_envelope(
            code="ACCEPTANCE_CLEANUP_OK",
            data={
                "run_id": fixed_run_id,
                "phase": "cleaned",
                "cleanup_required": False,
                "cleanup_performed": True,
                "scene_setup_restored": True,
                "deleted_fixture_count": fixture_count,
                "deleted_request_artifact_count": request_count,
                "lease_removed": True,
            },
        ),
    )

    payload = asyncio.run(
        run_acceptance(config, transport, FakeClock(), lambda _: None)
    ).to_dict()

    assert payload["result"]["success"] is expected_success
    assert payload["cleanup"]["data"]["cleanup"]["run_id"] == fixed_run_id
    assert transport.acceptance_calls == [
        ("acceptance_status", fixed_run_id, None),
        ("cleanup", fixed_run_id, 300),
    ]


def test_six_passed_cases_without_fixture_ownership_are_not_accepted(
    tmp_path: Path,
    clean_identity: None,
    fixed_run_id: str,
) -> None:
    transport = RecordingTransport()
    transport.smoke_result["data"]["fixture_owned"] = False
    config = _config(tmp_path)
    transport.prepare_compile_evidence(config.project_root, config.unity_log_file)

    payload = asyncio.run(
        run_acceptance(config, transport, FakeClock(), lambda _: None)
    ).to_dict()

    assert (payload["result"]["success"], payload["result"]["code"]) == (
        False,
        "ACCEPTANCE_SMOKE_FAILED",
    )
    assert payload["smoke"]["data"]["suite"]["run_ownership"] == {
        "fixture_owned": False,
        "run_id": fixed_run_id,
        "lease_phase": "smoke_complete",
    }
    assert transport.acceptance_calls == [
        ("run_acceptance", fixed_run_id, 300),
        ("acceptance_status", fixed_run_id, None),
        ("cleanup", fixed_run_id, 300),
    ]


@pytest.mark.parametrize(
    ("mutation", "cases"),
    [
        (
            "unknown passing seventh case",
            [
                {"name": "Acceptance_SavedSceneAndPrefabFixture", "passed": True, "code": "ACCEPTANCE_CASE_OK"},
                {"name": "Acceptance_DuplicateSameNameObjects", "passed": True, "code": "ACCEPTANCE_CASE_OK"},
                {"name": "Acceptance_TransformMutationReadback", "passed": True, "code": "ACCEPTANCE_CASE_OK"},
                {"name": "Acceptance_PrimitivePropertyOverride", "passed": True, "code": "ACCEPTANCE_CASE_OK"},
                {"name": "Acceptance_EditorBridgeRequest", "passed": True, "code": "ACCEPTANCE_CASE_OK"},
                {"name": "Acceptance_ConsoleErrorZero", "passed": True, "code": "ACCEPTANCE_CASE_OK"},
                {"name": "Acceptance_Unknown", "passed": True, "code": "ACCEPTANCE_CASE_OK"},
            ],
        ),
        (
            "unknown failing seventh case",
            [
                {"name": "Acceptance_SavedSceneAndPrefabFixture", "passed": True, "code": "ACCEPTANCE_CASE_OK"},
                {"name": "Acceptance_DuplicateSameNameObjects", "passed": True, "code": "ACCEPTANCE_CASE_OK"},
                {"name": "Acceptance_TransformMutationReadback", "passed": True, "code": "ACCEPTANCE_CASE_OK"},
                {"name": "Acceptance_PrimitivePropertyOverride", "passed": True, "code": "ACCEPTANCE_CASE_OK"},
                {"name": "Acceptance_EditorBridgeRequest", "passed": True, "code": "ACCEPTANCE_CASE_OK"},
                {"name": "Acceptance_ConsoleErrorZero", "passed": True, "code": "ACCEPTANCE_CASE_OK"},
                {"name": "Acceptance_Unknown", "passed": False, "code": "ACCEPTANCE_CASE_FAILED"},
            ],
        ),
        (
            "duplicate canonical case omits another canonical case",
            [
                {"name": "Acceptance_SavedSceneAndPrefabFixture", "passed": True, "code": "ACCEPTANCE_CASE_OK"},
                {"name": "Acceptance_DuplicateSameNameObjects", "passed": True, "code": "ACCEPTANCE_CASE_OK"},
                {"name": "Acceptance_TransformMutationReadback", "passed": True, "code": "ACCEPTANCE_CASE_OK"},
                {"name": "Acceptance_PrimitivePropertyOverride", "passed": True, "code": "ACCEPTANCE_CASE_OK"},
                {"name": "Acceptance_EditorBridgeRequest", "passed": True, "code": "ACCEPTANCE_CASE_OK"},
                {"name": "Acceptance_EditorBridgeRequest", "passed": True, "code": "ACCEPTANCE_CASE_OK"},
            ],
        ),
    ],
)
def test_smoke_rejects_raw_case_sequence_with_unknown_or_duplicate_case(
    tmp_path: Path,
    clean_identity: None,
    fixed_run_id: str,
    mutation: str,
    cases: list[dict[str, object]],
) -> None:
    transport = RecordingTransport()
    transport.smoke_result["data"]["acceptance_cases"] = cases
    config = _config(tmp_path)
    transport.prepare_compile_evidence(config.project_root, config.unity_log_file)

    payload = asyncio.run(
        run_acceptance(config, transport, FakeClock(), lambda _: None)
    ).to_dict()

    assert mutation
    assert (payload["result"]["success"], payload["result"]["code"]) == (
        False,
        "ACCEPTANCE_SMOKE_FAILED",
    )
    assert payload["smoke"]["data"]["suite"]["run_ownership"] == {
        "fixture_owned": True,
        "run_id": fixed_run_id,
        "lease_phase": "smoke_complete",
    }
    assert transport.acceptance_calls == [
        ("run_acceptance", fixed_run_id, 300),
        ("acceptance_status", fixed_run_id, None),
        ("cleanup", fixed_run_id, 300),
    ]


@pytest.mark.parametrize(
    ("mutation", "expected_success"),
    [
        ("missing fifth item", False),
        ("nonmapping item", False),
        ("missing code field", False),
        ("duplicate canonical name", False),
        ("unknown canonical name", False),
        ("canonical case failed", False),
        ("wrong stable success code", False),
        ("reordered canonical cases", True),
    ],
)
def test_smoke_raw_case_collection_requires_complete_unordered_canonical_set(
    tmp_path: Path,
    clean_identity: None,
    fixed_run_id: str,
    mutation: str,
    expected_success: bool,
) -> None:
    cases: list[object] = [
        {"name": "Acceptance_SavedSceneAndPrefabFixture", "passed": True, "code": "ACCEPTANCE_CASE_OK"},
        {"name": "Acceptance_DuplicateSameNameObjects", "passed": True, "code": "ACCEPTANCE_CASE_OK"},
        {"name": "Acceptance_TransformMutationReadback", "passed": True, "code": "ACCEPTANCE_CASE_OK"},
        {"name": "Acceptance_PrimitivePropertyOverride", "passed": True, "code": "ACCEPTANCE_CASE_OK"},
        {"name": "Acceptance_EditorBridgeRequest", "passed": True, "code": "ACCEPTANCE_CASE_OK"},
        {"name": "Acceptance_ConsoleErrorZero", "passed": True, "code": "ACCEPTANCE_CASE_OK"},
    ]
    if mutation == "missing fifth item":
        cases.pop()
    elif mutation == "nonmapping item":
        cases[-1] = "not-a-case-mapping"
    elif mutation == "missing code field":
        cases[-1] = {"name": "Acceptance_ConsoleErrorZero", "passed": True}
    elif mutation == "duplicate canonical name":
        cases[-1] = {"name": "Acceptance_EditorBridgeRequest", "passed": True, "code": "ACCEPTANCE_CASE_OK"}
    elif mutation == "unknown canonical name":
        cases[-1] = {"name": "Acceptance_Unknown", "passed": True, "code": "ACCEPTANCE_CASE_OK"}
    elif mutation == "canonical case failed":
        cases[-1] = {"name": "Acceptance_ConsoleErrorZero", "passed": False, "code": "ACCEPTANCE_CASE_OK"}
    elif mutation == "wrong stable success code":
        cases[-1] = {"name": "Acceptance_ConsoleErrorZero", "passed": True, "code": "ACCEPTANCE_CASE_FAILED"}
    elif mutation == "reordered canonical cases":
        cases.reverse()
    else:
        raise AssertionError(f"unknown smoke mutation: {mutation}")

    transport = RecordingTransport()
    transport.smoke_result["data"]["acceptance_cases"] = cases
    config = _config(tmp_path)
    transport.prepare_compile_evidence(config.project_root, config.unity_log_file)

    payload = asyncio.run(
        run_acceptance(config, transport, FakeClock(), lambda _: None)
    ).to_dict()

    assert payload["result"]["success"] is expected_success
    assert payload["smoke"]["data"]["suite"]["run_ownership"] == {
        "fixture_owned": True,
        "run_id": fixed_run_id,
        "lease_phase": "smoke_complete",
    }
    assert transport.acceptance_calls == [
        ("run_acceptance", fixed_run_id, 300),
        ("acceptance_status", fixed_run_id, None),
        ("cleanup", fixed_run_id, 300),
    ]


def test_owned_smoke_and_failed_status_still_clean_up_and_observe_postconditions(
    tmp_path: Path,
    clean_identity: None,
    fixed_run_id: str,
) -> None:
    transport = RecordingTransport(
        acceptance_status=_envelope(
            success=False,
            code="EDITOR_CTRL_ACCEPTANCE_LEASE_INVALID",
            data={},
        )
    )
    config = _config(tmp_path)
    transport.prepare_compile_evidence(config.project_root, config.unity_log_file)

    payload = asyncio.run(
        run_acceptance(config, transport, FakeClock(), lambda _: None)
    ).to_dict()

    assert (payload["result"]["code"], payload["result"]["failed_phase"]) == (
        "ACCEPTANCE_CLEANUP_FAILED",
        "cleanup",
    )
    assert transport.acceptance_calls == [
        ("run_acceptance", fixed_run_id, 300),
        ("acceptance_status", fixed_run_id, None),
        ("cleanup", fixed_run_id, 300),
    ]
    assert transport.calls[-2:] == ["project_status", "console_errors"]
    assert payload["cleanup"]["data"]["postconditions"] == {
        "editor_state_equal": True,
        "final_console_zero": True,
        "editor_state": {
            "active_stage_kind": "main",
            "has_unsaved_changes": False,
            "prefab_stage_is_dirty": False,
            "open_scene_count": 1,
            "dirty_counts": {
                "dirty_scene_paths": 0,
                "dirty_prefab_paths": 0,
                "dirty_material_paths": 0,
                "dirty_asset_paths": 0,
            },
        },
        "final_console": {
            "success": True,
            "code": "CONSOLE_OK",
            "error_count": 0,
        },
    }


def test_status_failure_serializes_literal_cleanup_evidence_and_redacts_recursively(
    tmp_path: Path,
    clean_identity: None,
    fixed_run_id: str,
) -> None:
    transport = RecordingTransport(
        acceptance_status=_hostile(
            _envelope(
                success=False,
                code="EDITOR_CTRL_ACCEPTANCE_LEASE_INVALID",
                data={},
            )
        )
    )
    config = _config(tmp_path)
    transport.prepare_compile_evidence(config.project_root, config.unity_log_file)

    payload = asyncio.run(
        run_acceptance(config, transport, FakeClock(), lambda _: None)
    ).to_dict()

    assert (payload["result"]["code"], payload["result"]["failed_phase"]) == (
        "ACCEPTANCE_CLEANUP_FAILED",
        "cleanup",
    )
    assert payload["cleanup"]["data"] == {
        "status": {
            "success": False,
            "code": "EDITOR_CTRL_ACCEPTANCE_LEASE_INVALID",
            "run_id": fixed_run_id,
            "phase": "",
            "cleanup_required": False,
            "cleanup_performed": False,
        },
        "cleanup": {
            "success": True,
            "code": "ACCEPTANCE_CLEANUP_OK",
            "run_id": fixed_run_id,
            "phase": "cleaned",
            "cleanup_required": False,
            "cleanup_performed": True,
            "scene_setup_restored": True,
            "deleted_fixture_count": 1,
            "deleted_request_artifact_count": 5,
            "lease_removed": True,
        },
        "postconditions": {
            "editor_state_equal": True,
            "final_console_zero": True,
            "editor_state": {
                "active_stage_kind": "main",
                "has_unsaved_changes": False,
                "prefab_stage_is_dirty": False,
                "open_scene_count": 1,
                "dirty_counts": {
                    "dirty_scene_paths": 0,
                    "dirty_prefab_paths": 0,
                    "dirty_material_paths": 0,
                    "dirty_asset_paths": 0,
                },
            },
            "final_console": {
                "success": True,
                "code": "CONSOLE_OK",
                "error_count": 0,
            },
        },
    }
    assert {
        name: payload[name]["executed"]
        for name in ("source", "environment", "preflight", "deploy", "compile", "smoke", "cleanup")
    } == {
        "source": True,
        "environment": True,
        "preflight": True,
        "deploy": True,
        "compile": True,
        "smoke": True,
        "cleanup": True,
    }
    assert transport.acceptance_calls == [
        ("run_acceptance", fixed_run_id, 300),
        ("acceptance_status", fixed_run_id, None),
        ("cleanup", fixed_run_id, 300),
    ]
    serialized = json.dumps(payload, sort_keys=True)
    assert _PRIVATE_PATH not in serialized
    assert _PRIVATE_EXCEPTION not in serialized


def test_cancelled_status_still_runs_cleanup_and_terminal_observation(
    tmp_path: Path,
    clean_identity: None,
    fixed_run_id: str,
) -> None:
    class CancellingStatusTransport(RecordingTransport):
        async def acceptance_status(self, run_id: str) -> dict[str, Any]:
            self.calls.append("acceptance_status")
            self.acceptance_calls.append(("acceptance_status", run_id, None))
            task = asyncio.current_task()
            assert task is not None
            task.cancel()
            await asyncio.sleep(0)
            raise AssertionError("cancelled await unexpectedly resumed")

    transport = CancellingStatusTransport()
    config = _config(tmp_path)
    transport.prepare_compile_evidence(config.project_root, config.unity_log_file)

    payload = asyncio.run(
        run_acceptance(config, transport, FakeClock(), lambda _: None)
    ).to_dict()

    assert (payload["result"]["code"], payload["result"]["failed_phase"]) == (
        "ACCEPTANCE_CLEANUP_FAILED",
        "cleanup",
    )
    assert payload["cleanup"]["data"] == {
        "status": {
            "success": False,
            "code": "ACCEPTANCE_CLEANUP_FAILED",
            "run_id": "",
            "phase": "",
            "cleanup_required": False,
            "cleanup_performed": False,
        },
        "cleanup": {
            "success": True,
            "code": "ACCEPTANCE_CLEANUP_OK",
            "run_id": fixed_run_id,
            "phase": "cleaned",
            "cleanup_required": False,
            "cleanup_performed": True,
            "scene_setup_restored": True,
            "deleted_fixture_count": 1,
            "deleted_request_artifact_count": 5,
            "lease_removed": True,
        },
        "postconditions": {
            "editor_state_equal": True,
            "final_console_zero": True,
            "editor_state": {
                "active_stage_kind": "main",
                "has_unsaved_changes": False,
                "prefab_stage_is_dirty": False,
                "open_scene_count": 1,
                "dirty_counts": {
                    "dirty_scene_paths": 0,
                    "dirty_prefab_paths": 0,
                    "dirty_material_paths": 0,
                    "dirty_asset_paths": 0,
                },
            },
            "final_console": {
                "success": True,
                "code": "CONSOLE_OK",
                "error_count": 0,
            },
        },
    }
    assert {
        name: payload[name]["executed"]
        for name in ("source", "environment", "preflight", "deploy", "compile", "smoke", "cleanup")
    } == {
        "source": True,
        "environment": True,
        "preflight": True,
        "deploy": True,
        "compile": True,
        "smoke": True,
        "cleanup": True,
    }
    assert transport.acceptance_calls == [
        ("run_acceptance", fixed_run_id, 300),
        ("acceptance_status", fixed_run_id, None),
        ("cleanup", fixed_run_id, 300),
    ]
    assert transport.calls[-2:] == ["project_status", "console_errors"]


def test_cancelled_smoke_redacts_hostile_status_cleanup_and_postconditions(
    tmp_path: Path,
    clean_identity: None,
    fixed_run_id: str,
) -> None:
    class CancellingSmokeTransport(RecordingTransport):
        async def run_acceptance(self, run_id: str, timeout_sec: int) -> dict[str, Any]:
            self.calls.append("run_acceptance")
            self.acceptance_calls.append(("run_acceptance", run_id, timeout_sec))
            task = asyncio.current_task()
            assert task is not None
            task.cancel()
            await asyncio.sleep(0)
            raise AssertionError("cancelled await unexpectedly resumed")

        async def acceptance_status(self, run_id: str) -> dict[str, Any]:
            return _hostile(await super().acceptance_status(run_id))

        async def cleanup_acceptance(
            self, run_id: str, timeout_sec: int
        ) -> dict[str, Any]:
            return _hostile(await super().cleanup_acceptance(run_id, timeout_sec))

        async def project_status(self) -> dict[str, Any]:
            return _hostile(await super().project_status())

        async def console_errors(self, since_seconds: float) -> dict[str, Any]:
            return _hostile(await super().console_errors(since_seconds))

    transport = CancellingSmokeTransport()
    config = _config(tmp_path)
    transport.prepare_compile_evidence(config.project_root, config.unity_log_file)

    payload = asyncio.run(
        run_acceptance(config, transport, FakeClock(), lambda _: None)
    ).to_dict()

    assert (payload["result"]["code"], payload["result"]["failed_phase"]) == (
        "ACCEPTANCE_SMOKE_TIMEOUT",
        "smoke",
    )
    assert payload["cleanup"]["data"] == {
        "status": {
            "success": True,
            "code": "ACCEPTANCE_STATUS_OK",
            "run_id": fixed_run_id,
            "phase": "smoke_complete",
            "cleanup_required": True,
            "cleanup_performed": False,
        },
        "cleanup": {
            "success": True,
            "code": "ACCEPTANCE_CLEANUP_OK",
            "run_id": fixed_run_id,
            "phase": "cleaned",
            "cleanup_required": False,
            "cleanup_performed": True,
            "scene_setup_restored": True,
            "deleted_fixture_count": 1,
            "deleted_request_artifact_count": 5,
            "lease_removed": True,
        },
        "postconditions": {
            "editor_state_equal": True,
            "final_console_zero": True,
            "editor_state": {
                "active_stage_kind": "main",
                "has_unsaved_changes": False,
                "prefab_stage_is_dirty": False,
                "open_scene_count": 1,
                "dirty_counts": {
                    "dirty_scene_paths": 0,
                    "dirty_prefab_paths": 0,
                    "dirty_material_paths": 0,
                    "dirty_asset_paths": 0,
                },
            },
            "final_console": {
                "success": True,
                "code": "CONSOLE_OK",
                "error_count": 0,
            },
        },
    }
    assert transport.calls == [
        "activate",
        "project_status",
        "deploy",
        "recompile",
        "project_status",
        "console_errors",
        "reflect_game_object",
        "runtime_console_probe",
        "run_acceptance",
        "acceptance_status",
        "cleanup",
        "project_status",
        "console_errors",
    ]
    assert transport.acceptance_calls == [
        ("run_acceptance", fixed_run_id, 300),
        ("acceptance_status", fixed_run_id, None),
        ("cleanup", fixed_run_id, 300),
    ]
    for evidence in (
        payload["cleanup"],
        payload["cleanup"]["data"]["status"],
        payload["cleanup"]["data"]["postconditions"],
        payload["cleanup"]["data"]["postconditions"]["final_console"],
        payload["result"]["phases"],
    ):
        serialized = json.dumps(evidence, sort_keys=True)
        assert _PRIVATE_PATH not in serialized
        assert _PRIVATE_EXCEPTION not in serialized


def test_normal_success_requires_complete_same_run_cleanup_evidence(
    tmp_path: Path,
    clean_identity: None,
    fixed_run_id: str,
) -> None:
    transport = RecordingTransport()
    config = _config(tmp_path)
    transport.prepare_compile_evidence(config.project_root, config.unity_log_file)

    payload = asyncio.run(
        run_acceptance(config, transport, FakeClock(), lambda _: None)
    ).to_dict()

    assert (payload["result"]["success"], payload["result"]["code"]) == (
        True,
        "ACCEPTANCE_OK",
    )
    assert payload["audit"]["data"] == {
        "opt_in": True,
        "mode": "acceptance",
        "arguments": {
            "project_root": "<project-root>",
            "scope": "Assets/Acceptance",
            "target_dir": "Assets/Editor/PrefabSentinel",
            "watch_dir": "<redacted>",
            "unity_log_file": "<redacted>",
            "out_report": "reports/acceptance.json",
            "recover_run_id": "",
        },
        "deadlines": {
            "recompile_timeout_sec": 120,
            "environment_timeout_sec": 120,
            "smoke_timeout_sec": 300,
        },
    }
    assert payload["smoke"]["data"]["suite"]["run_ownership"] == {
        "fixture_owned": True,
        "run_id": fixed_run_id,
        "lease_phase": "smoke_complete",
    }
    assert payload["cleanup"]["data"]["status"] == {
        "success": True,
        "code": "ACCEPTANCE_STATUS_OK",
        "run_id": fixed_run_id,
        "phase": "smoke_complete",
        "cleanup_required": True,
        "cleanup_performed": False,
    }
    assert payload["cleanup"]["data"]["cleanup"] == {
        "success": True,
        "code": "ACCEPTANCE_CLEANUP_OK",
        "run_id": fixed_run_id,
        "phase": "cleaned",
        "cleanup_required": False,
        "cleanup_performed": True,
        "scene_setup_restored": True,
        "deleted_fixture_count": 1,
        "deleted_request_artifact_count": 5,
        "lease_removed": True,
    }


@pytest.mark.parametrize(
    ("fixture_count", "request_count", "expected_success"),
    [
        (0, 5, False),
        (1, 5, True),
        (2, 5, False),
        (1, 4, False),
        (1, 6, False),
        (True, 5, False),
        (1, "5", False),
    ],
)
def test_normal_cleanup_requires_exact_literal_deletion_counts(
    tmp_path: Path,
    clean_identity: None,
    fixed_run_id: str,
    fixture_count: object,
    request_count: object,
    expected_success: bool,
) -> None:
    transport = RecordingTransport(
        cleanup=_envelope(
            code="ACCEPTANCE_CLEANUP_OK",
            data={
                "phase": "cleaned",
                "cleanup_required": False,
                "cleanup_performed": True,
                "scene_setup_restored": True,
                "deleted_fixture_count": fixture_count,
                "deleted_request_artifact_count": request_count,
                "lease_removed": True,
            },
        )
    )
    config = _config(tmp_path)
    transport.prepare_compile_evidence(config.project_root, config.unity_log_file)

    payload = asyncio.run(
        run_acceptance(config, transport, FakeClock(), lambda _: None)
    ).to_dict()

    assert payload["result"]["success"] is expected_success
    assert payload["cleanup"]["data"]["cleanup"]["run_id"] == fixed_run_id
    assert transport.acceptance_calls == [
        ("run_acceptance", fixed_run_id, 300),
        ("acceptance_status", fixed_run_id, None),
        ("cleanup", fixed_run_id, 300),
    ]


def test_empty_cleanup_success_and_failed_postconditions_keep_cleanup_precedence(
    tmp_path: Path,
    clean_identity: None,
    fixed_run_id: str,
) -> None:
    final_status = _dirty_scene_status()
    transport = RecordingTransport(
        final_status=final_status,
        cleanup=_envelope(code="ACCEPTANCE_CLEANUP_OK", data={}),
    )
    config = _config(tmp_path)
    transport.prepare_compile_evidence(config.project_root, config.unity_log_file)

    payload = asyncio.run(
        run_acceptance(config, transport, FakeClock(), lambda _: None)
    ).to_dict()

    assert (payload["result"]["code"], payload["result"]["failed_phase"]) == (
        "ACCEPTANCE_CLEANUP_FAILED",
        "cleanup",
    )
    assert transport.acceptance_calls == [
        ("run_acceptance", fixed_run_id, 300),
        ("acceptance_status", fixed_run_id, None),
        ("cleanup", fixed_run_id, 300),
    ]
    assert payload["cleanup"]["data"]["postconditions"]["editor_state_equal"] is False
    assert payload["cleanup"]["data"]["cleanup"] == {
        "success": True,
        "code": "ACCEPTANCE_CLEANUP_OK",
        "run_id": fixed_run_id,
        "phase": "",
        "cleanup_required": False,
        "cleanup_performed": False,
        "scene_setup_restored": False,
        "deleted_fixture_count": 0,
        "deleted_request_artifact_count": 0,
        "lease_removed": False,
    }


def test_hostile_config_error_is_redacted_recursively_in_every_report_section(
    tmp_path: Path,
) -> None:
    ordinary = _config(tmp_path)
    config = AcceptanceConfig(
        repo_root=ordinary.repo_root,
        project_root=ordinary.project_root,
        scope="Assets/private/../../TOP_SECRET_SCOPE",
        target_dir=Path("/private/TOP_SECRET_TARGET"),
        watch_dir=ordinary.watch_dir,
        unity_log_file=ordinary.unity_log_file,
        out_report="../TOP_SECRET_REPORT.json",
        confirm_live=True,
    )

    payload = asyncio.run(
        run_acceptance(config, RecordingTransport(), FakeClock(), lambda _: None)
    ).to_dict()

    assert payload["result"]["code"] == "ACCEPTANCE_CONFIG_ERROR"
    assert payload["audit"]["data"]["arguments"] == {
        "project_root": "<project-root>",
        "scope": "<redacted>",
        "target_dir": "<redacted>",
        "watch_dir": "<redacted>",
        "unity_log_file": "<redacted>",
        "out_report": "<redacted>",
        "recover_run_id": "",
    }
    for section in (
        "audit",
        "source",
        "environment",
        "preflight",
        "deploy",
        "compile",
        "smoke",
        "cleanup",
        "result",
    ):
        serialized = json.dumps(payload[section], sort_keys=True)
        assert "TOP_SECRET_SCOPE" not in serialized
        assert "TOP_SECRET_TARGET" not in serialized
        assert "TOP_SECRET_REPORT" not in serialized
