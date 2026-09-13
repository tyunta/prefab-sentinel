from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from prefab_sentinel.unity_acceptance.controller import run_acceptance
from tests.test_unity_acceptance_controller import (
    FakeClock,
    RecordingTransport,
    _config,
    _envelope,
    _identity,
)


def _editor_state(*, active_scene: str = "Acceptance") -> dict[str, object]:
    return {
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
        "unity_version": "2022.3.22f1",
        "required_packages": [
            {"name": "com.vrchat.base", "ready": True, "version": "3.10.2"},
            {"name": "com.vrchat.worlds", "ready": True, "version": "3.10.2"},
            {"name": "udonsharp", "ready": True, "version": "3.10.2"},
        ],
        "state_source": "bridge",
        "active_stage_kind": "main",
        "active_scene_name": active_scene,
        "prefab_stage_root_name": "",
        "is_playing": False,
        "is_will_change_playmode": False,
        "is_compiling": False,
        "is_building_player": False,
        "prefab_stage_is_dirty": False,
        "has_unsaved_changes": False,
        "active_scene_path": "Assets/Acceptance.unity",
        "prefab_stage_asset_path": "",
        "dirty_scene_paths": [],
        "dirty_prefab_paths": [],
        "dirty_material_paths": [],
        "dirty_asset_paths": [],
        "open_scenes": [
            {"path": "Assets/Acceptance.unity", "name": active_scene, "is_dirty": False}
        ],
    }


@pytest.fixture
def clean_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "prefab_sentinel.unity_acceptance.controller.collect_source_identity",
        lambda repo_root: _identity(),
    )


def test_final_editor_state_must_exactly_match_preflight(
    tmp_path: Path, clean_identity: None
) -> None:
    transport = RecordingTransport(
        initial_status=_envelope(code="SESSION_STATUS", data=_editor_state()),
        final_status=_envelope(
            code="SESSION_STATUS", data=_editor_state(active_scene="Different")
        ),
    )
    config = _config(tmp_path)
    transport.prepare_compile_evidence(config.project_root, config.unity_log_file)
    clock = FakeClock()

    result = asyncio.run(run_acceptance(config, transport, clock, clock.sleep))

    assert (result.code, result.failed_phase) == (
        "ACCEPTANCE_POSTCONDITION_FAILED",
        "postconditions",
    )
    assert transport.calls.count("console_errors") == 2


def test_success_console_without_an_error_collection_fails_closed(
    tmp_path: Path, clean_identity: None
) -> None:
    class MalformedConsoleTransport(RecordingTransport):
        async def console_errors(self, since_seconds: float) -> dict[str, object]:
            self.calls.append("console_errors")
            return _envelope(code="CONSOLE_OK", data={})

    transport = MalformedConsoleTransport(
        initial_status=_envelope(code="SESSION_STATUS", data=_editor_state())
    )
    config = _config(tmp_path)
    transport.prepare_compile_evidence(config.project_root, config.unity_log_file)
    clock = FakeClock()

    result = asyncio.run(run_acceptance(config, transport, clock, clock.sleep))

    assert (result.code, result.failed_phase) == (
        "ACCEPTANCE_COMPILE_FAILED",
        "console",
    )
    assert "reflect_game_object" not in transport.calls


@pytest.mark.parametrize("interruption", [asyncio.CancelledError, KeyboardInterrupt])
def test_interruption_after_fixture_ownership_cleans_up_and_publishes(
    tmp_path: Path,
    clean_identity: None,
    interruption: type[BaseException],
) -> None:
    class InterruptedSmokeTransport(RecordingTransport):
        async def run_acceptance(self, run_id: str, timeout_sec: int) -> dict[str, object]:
            self.calls.append("run_acceptance")
            self.acceptance_calls.append(("run_acceptance", run_id, timeout_sec))
            raise interruption

        async def acceptance_status(self, run_id: str) -> dict[str, object]:
            self.calls.append("acceptance_status")
            self.acceptance_calls.append(("acceptance_status", run_id, None))
            return _envelope(
                code="ACCEPTANCE_STATUS_OK",
                data={
                    "run_id": run_id,
                    "phase": "fixture_created",
                    "cleanup_required": True,
                    "cleanup_performed": False,
                },
            )

    transport = InterruptedSmokeTransport(
        initial_status=_envelope(code="SESSION_STATUS", data=_editor_state())
    )
    config = _config(tmp_path)
    transport.prepare_compile_evidence(config.project_root, config.unity_log_file)
    clock = FakeClock()

    result = asyncio.run(run_acceptance(config, transport, clock, clock.sleep))

    assert (result.code, result.failed_phase) == (
        "ACCEPTANCE_SMOKE_TIMEOUT",
        "smoke",
    )
    assert transport.calls[-4:] == [
        "acceptance_status",
        "cleanup",
        "project_status",
        "console_errors",
    ]
    report = config.project_root / config.out_report
    assert report.exists()
    assert json.loads(report.read_text(encoding="utf-8"))["result"]["code"] == (
        "ACCEPTANCE_SMOKE_TIMEOUT"
    )



def test_report_reservation_failure_stops_before_activation(
    tmp_path: Path, clean_identity: None
) -> None:
    config = _config(tmp_path)
    report = config.project_root / config.out_report
    report.write_text("exists", encoding="utf-8")
    transport = RecordingTransport()
    clock = FakeClock()

    result = asyncio.run(run_acceptance(config, transport, clock, clock.sleep))

    assert (result.code, result.failed_phase, transport.calls) == (
        "ACCEPTANCE_CONFIG_ERROR",
        "report_reservation",
        [],
    )


@pytest.mark.parametrize(
    ("mode", "expected_code"),
    [
        ("timeout", "ACCEPTANCE_COMPILE_TIMEOUT"),
        ("continuity", "ACCEPTANCE_COMPILE_LOG_CONTINUITY_LOST"),
    ],
)
def test_compile_failure_matrix_blocks_reloaded_bridge(
    tmp_path: Path,
    clean_identity: None,
    mode: str,
    expected_code: str,
) -> None:
    class CompileFailureTransport(RecordingTransport):
        async def deploy(self, target_dir: str) -> dict[str, object]:
            self.calls.append("deploy")
            assert self._unity_log is not None
            if mode == "continuity":
                self._unity_log.unlink()
                self._unity_log.write_text("", encoding="utf-8")
            return _envelope(
                code="DEPLOY_OK",
                data={
                    "promotion_state": "promoted",
                    "manifest_sha256": "b" * 64,
                    "bridge_version": "0.9.0",
                },
            )

    transport = CompileFailureTransport(
        initial_status=_envelope(code="SESSION_STATUS", data=_editor_state())
    )
    config = _config(tmp_path)
    transport.prepare_compile_evidence(config.project_root, config.unity_log_file)
    clock = FakeClock()

    result = asyncio.run(run_acceptance(config, transport, clock, clock.sleep))

    assert (result.code, result.failed_phase) == (expected_code, "compile_observer")
    assert "recompile" not in transport.calls


def test_smoke_timeout_status_failure_fails_cleanup_after_postcheck(
    tmp_path: Path, clean_identity: None
) -> None:
    transport = RecordingTransport(
        initial_status=_envelope(code="SESSION_STATUS", data=_editor_state()),
        smoke=_envelope(
            success=False,
            code="EDITOR_BRIDGE_TIMEOUT",
            data={"fixture_owned": True},
        ),
        acceptance_status=_envelope(success=False, code="STATUS_FAILED"),
    )
    config = _config(tmp_path)
    transport.prepare_compile_evidence(config.project_root, config.unity_log_file)
    clock = FakeClock()

    result = asyncio.run(run_acceptance(config, transport, clock, clock.sleep))

    assert (result.code, result.failed_phase) == (
        "ACCEPTANCE_CLEANUP_FAILED",
        "cleanup",
    )
    assert transport.calls[-4:] == [
        "acceptance_status",
        "cleanup",
        "project_status",
        "console_errors",
    ]
    assert transport.calls.count("project_status") == 3


def test_final_console_failure_stops_the_terminal_phase(
    tmp_path: Path, clean_identity: None
) -> None:
    class FinalConsoleFailureTransport(RecordingTransport):
        async def console_errors(self, since_seconds: float) -> dict[str, object]:
            self.calls.append("console_errors")
            if self.calls.count("console_errors") == 2:
                return _envelope(code="CONSOLE_OK", data={"entries": ["error"]})
            return _envelope(code="CONSOLE_OK", data={"entries": []})

    transport = FinalConsoleFailureTransport(
        initial_status=_envelope(code="SESSION_STATUS", data=_editor_state())
    )
    config = _config(tmp_path)
    transport.prepare_compile_evidence(config.project_root, config.unity_log_file)
    clock = FakeClock()

    result = asyncio.run(run_acceptance(config, transport, clock, clock.sleep))

    assert (result.code, result.failed_phase) == (
        "ACCEPTANCE_POSTCONDITION_FAILED",
        "postconditions",
    )
    assert transport.calls.count("console_errors") == 2
