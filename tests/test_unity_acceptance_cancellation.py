from __future__ import annotations

import asyncio
import importlib
import json
from pathlib import Path

import pytest

from prefab_sentinel.unity_acceptance.controller import run_acceptance
from prefab_sentinel.unity_acceptance.model import AcceptanceResult
from tests.test_unity_acceptance_controller import (
    FakeClock,
    RecordingTransport,
    _config,
    _envelope,
    _identity,
)
from tests.test_unity_acceptance_controller_regressions import _editor_state


@pytest.fixture
def clean_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "prefab_sentinel.unity_acceptance.controller.collect_source_identity",
        lambda repo_root: _identity(),
    )


def test_repeated_task_cancellation_is_fully_converted_to_terminal_result(
    tmp_path: Path, clean_identity: None
) -> None:
    class RepeatedCancellationTransport(RecordingTransport):
        async def run_acceptance(self, run_id: str, timeout_sec: int) -> dict[str, object]:
            self.calls.append("run_acceptance")
            self.acceptance_calls.append(("run_acceptance", run_id, timeout_sec))
            task = asyncio.current_task()
            assert task is not None
            task.cancel()
            task.cancel()
            await asyncio.sleep(0)
            raise AssertionError("unreachable")

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

    async def scenario() -> tuple[AcceptanceResult, int]:
        transport = RepeatedCancellationTransport(
            initial_status=_envelope(code="SESSION_STATUS", data=_editor_state())
        )
        config = _config(tmp_path)
        transport.prepare_compile_evidence(config.project_root, config.unity_log_file)
        clock = FakeClock()
        result = await run_acceptance(config, transport, clock, clock.sleep)
        return result, asyncio.current_task().cancelling()  # type: ignore[union-attr]

    result, remaining_cancellations = asyncio.run(scenario())

    assert (result.code, result.failed_phase, remaining_cancellations) == (
        "ACCEPTANCE_SMOKE_TIMEOUT",
        "smoke",
        0,
    )


def test_cli_main_cancellation_publishes_terminal_report_and_closes_context(
    tmp_path: Path,
    clean_identity: None,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    script = importlib.import_module("scripts.run_unity_bridge_acceptance")
    controller = importlib.import_module(
        "prefab_sentinel.unity_acceptance.controller"
    )
    config = _config(tmp_path)
    report_path = config.project_root / config.out_report
    fixture_path = config.project_root / "Assets/Acceptance.fixture"
    expected_run_id = "18618618618618618618618618618618"
    operations: list[tuple[str, str]] = []
    reports_at_context_exit: list[str] = []
    fixture_present_at_context_exit: list[bool] = []

    class FixedUuid:
        hex = expected_run_id

    class InterruptingContextTransport(RecordingTransport):
        def __init__(self, *, worktree_root: Path, watch_dir: str) -> None:
            super().__init__(
                initial_status=_envelope(code="SESSION_STATUS", data=_editor_state())
            )

        async def __aenter__(self) -> InterruptingContextTransport:
            return self

        async def __aexit__(
            self,
            exc_type: object,
            exc: object,
            traceback: object,
        ) -> None:
            reports_at_context_exit.append(report_path.read_text(encoding="utf-8"))
            fixture_present_at_context_exit.append(fixture_path.exists())

        async def deploy(self, target_dir: str) -> dict[str, object]:
            self.calls.append("deploy")
            dll = (
                config.project_root
                / "Library/ScriptAssemblies/PrefabSentinel.Editor.dll"
            )
            dll.write_bytes(b"new bridge")
            with config.unity_log_file.open("a", encoding="utf-8") as unity_log:
                unity_log.write(
                    "Reloading assemblies after finishing script compilation.\n"
                )
            return _envelope(
                code="DEPLOY_OK",
                data={
                    "promotion_state": "promoted",
                    "manifest_sha256": "b" * 64,
                    "bridge_version": "0.9.0",
                },
            )

        async def run_acceptance(
            self, run_id: str, timeout_sec: int
        ) -> dict[str, object]:
            self.calls.append("run_acceptance")
            operations.append(("run_acceptance", run_id))
            fixture_path.write_text(run_id, encoding="utf-8")
            task = asyncio.current_task()
            assert task is not None
            task.cancel()
            await asyncio.sleep(0)
            raise AssertionError("unreachable")

        async def acceptance_status(self, run_id: str) -> dict[str, object]:
            self.calls.append("acceptance_status")
            operations.append(("acceptance_status", run_id))
            return _envelope(
                code="ACCEPTANCE_STATUS_OK",
                data={
                    "run_id": run_id,
                    "phase": "fixture_created",
                    "cleanup_required": True,
                    "cleanup_performed": False,
                },
            )

        async def cleanup_acceptance(
            self, run_id: str, timeout_sec: int
        ) -> dict[str, object]:
            self.calls.append("cleanup")
            operations.append(("cleanup_acceptance", run_id))
            assert fixture_path.read_text(encoding="utf-8") == run_id
            fixture_path.unlink()
            return _envelope(
                code="ACCEPTANCE_CLEANUP_OK",
                data={
                    "run_id": run_id,
                    "phase": "cleaned",
                    "cleanup_required": False,
                    "cleanup_performed": True,
                    "scene_setup_restored": True,
                    "deleted_fixture_count": 1,
                    "deleted_request_artifact_count": 5,
                    "lease_removed": True,
                },
            )

    monkeypatch.setattr(controller, "uuid4", FixedUuid)
    monkeypatch.setattr(script, "McpAcceptanceTransport", InterruptingContextTransport)
    clock = FakeClock()
    monkeypatch.setattr(script.time, "monotonic", clock)
    monkeypatch.setattr(script.time, "sleep", clock.sleep)

    exit_code = script.main(
        [
            "--project-root",
            str(config.project_root),
            "--scope",
            config.scope,
            "--target-dir",
            str(config.target_dir),
            "--watch-dir",
            config.watch_dir,
            "--unity-log-file",
            str(config.unity_log_file),
            "--out-report",
            config.out_report,
            "--confirm-live",
        ]
    )

    stdout, stderr = capsys.readouterr()
    terminal_json = json.loads(stdout)
    report_json = json.loads(report_path.read_text(encoding="utf-8"))
    phase_data = {
        phase["name"]: phase["data"]
        for phase in terminal_json["result"]["phases"]
    }

    assert (
        exit_code,
        terminal_json["result"]["code"],
        terminal_json["result"]["failed_phase"],
    ) == (
        1,
        "ACCEPTANCE_SMOKE_TIMEOUT",
        "smoke",
    )
    assert operations == [
        ("run_acceptance", expected_run_id),
        ("acceptance_status", expected_run_id),
        ("cleanup_acceptance", expected_run_id),
    ]
    assert phase_data["acceptance_status"] == {
        "success": True,
        "code": "ACCEPTANCE_STATUS_OK",
        "run_id": expected_run_id,
        "phase": "fixture_created",
        "cleanup_required": True,
        "cleanup_performed": False,
    }
    assert phase_data["cleanup"] == {
        "success": True,
        "code": "ACCEPTANCE_CLEANUP_OK",
        "run_id": expected_run_id,
        "phase": "cleaned",
        "cleanup_required": False,
        "cleanup_performed": True,
        "scene_setup_restored": True,
        "deleted_fixture_count": 1,
        "deleted_request_artifact_count": 5,
        "lease_removed": True,
    }
    assert not fixture_path.exists()
    assert fixture_present_at_context_exit == [False]
    assert stdout.count("\n") == 1
    assert stderr == ""
    assert report_json == terminal_json
    assert [json.loads(report) for report in reports_at_context_exit] == [
        terminal_json
    ]



@pytest.mark.parametrize("field", ["dirty_prefab_paths", "dirty_material_paths", "dirty_asset_paths"])
def test_dirty_asset_surfaces_are_exact_preflight_postconditions(
    tmp_path: Path, clean_identity: None, field: str
) -> None:
    final_state = _editor_state()
    final_state[field] = ["Assets/Changed.asset"]
    transport = RecordingTransport(
        initial_status=_envelope(code="SESSION_STATUS", data=_editor_state()),
        final_status=_envelope(code="SESSION_STATUS", data=final_state),
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


def test_missing_dirty_asset_surface_fails_closed_before_deploy(
    tmp_path: Path, clean_identity: None
) -> None:
    initial_state = _editor_state()
    del initial_state["dirty_material_paths"]
    transport = RecordingTransport(
        initial_status=_envelope(code="SESSION_STATUS", data=initial_state)
    )
    config = _config(tmp_path)
    clock = FakeClock()

    result = asyncio.run(run_acceptance(config, transport, clock, clock.sleep))

    assert (result.code, result.failed_phase, transport.calls) == (
        "ACCEPTANCE_EDITOR_STATE_BLOCKED",
        "editor_state",
        ["activate", "project_status"],
    )
