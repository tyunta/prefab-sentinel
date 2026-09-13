from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from prefab_sentinel.unity_acceptance.controller import (
    AcceptanceConfig,
    run_acceptance,
)
from prefab_sentinel.unity_acceptance.model import AcceptanceSourceIdentity

RUN_ID = "18618618618618618618618618618618"


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


def _config(tmp_path: Path) -> AcceptanceConfig:
    project_root = tmp_path / "UnityProject"
    (project_root / "Assets").mkdir(parents=True)
    assemblies = project_root / "Library/ScriptAssemblies"
    assemblies.mkdir(parents=True)
    (assemblies / "PrefabSentinel.Editor.dll").write_bytes(b"old bridge")
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
        confirm_live=True,
    )


@dataclass
class FakeClock:
    now: float = 0.0

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


class RecoveryTransport:
    def __init__(
        self,
        config: AcceptanceConfig,
        *,
        smoke: dict[str, Any],
        status: dict[str, Any] | None = None,
        cleanup: dict[str, Any] | None = None,
    ) -> None:
        self.config = config
        self.smoke = smoke
        self.status = status or _envelope(
            code="ACCEPTANCE_STATUS_OK",
            data={
                "phase": "smoke_complete",
                "cleanup_required": True,
                "cleanup_performed": False,
            },
        )
        self.cleanup = cleanup or _envelope(
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
        self.calls: list[tuple[str, str | None]] = []

    @staticmethod
    def _for_run(response: dict[str, Any], run_id: str) -> dict[str, Any]:
        data = response.get("data")
        copied_data = dict(data) if isinstance(data, dict) else {}
        copied_data.setdefault("run_id", run_id)
        return {**response, "data": copied_data}

    async def activate(self, project_root: str, scope: str) -> dict[str, Any]:
        self.calls.append(("activate", None))
        return _envelope(code="ACTIVATE_OK")

    async def project_status(self) -> dict[str, Any]:
        self.calls.append(("project_status", None))
        return _clean_status()

    def connection_identity(self) -> dict[str, str | None]:
        return {
            "protocol_revision": "2026-07-28",
            "server_name": "prefab-sentinel",
            "server_version": "0.9.0",
        }

    async def deploy(self, target_dir: str) -> dict[str, Any]:
        self.calls.append(("deploy", None))
        dll = (
            self.config.project_root
            / "Library/ScriptAssemblies/PrefabSentinel.Editor.dll"
        )
        dll.write_bytes(b"new bridge")
        with self.config.unity_log_file.open("a", encoding="utf-8") as unity_log:
            unity_log.write("Reloading assemblies after finishing script compilation.\n")
        return _envelope(
            code="DEPLOY_OK",
            data={
                "promotion_state": "promoted",
                "manifest_sha256": "b" * 64,
                "bridge_version": "0.9.0",
            },
        )

    async def recompile(self, timeout_sec: int) -> dict[str, Any]:
        self.calls.append(("recompile", None))
        return _envelope(code="RECOMPILE_OK")

    async def console_errors(self, since_seconds: float) -> dict[str, Any]:
        self.calls.append(("console_errors", None))
        return _envelope(code="CONSOLE_OK", data={"entries": []})

    async def reflect_game_object(self) -> dict[str, Any]:
        self.calls.append(("reflect_game_object", None))
        return _envelope(code="REFLECT_OK")

    async def runtime_console_probe(self, scene_path: str) -> dict[str, Any]:
        self.calls.append(("runtime_console_probe", None))
        return _envelope(code="RUNTIME_OK")

    async def run_acceptance(
        self,
        run_id: str,
        timeout_sec: int,
    ) -> dict[str, Any]:
        self.calls.append(("run_acceptance", run_id))
        return self._for_run(self.smoke, run_id)

    async def acceptance_status(self, run_id: str) -> dict[str, Any]:
        self.calls.append(("acceptance_status", run_id))
        return self._for_run(self.status, run_id)

    async def cleanup_acceptance(
        self,
        run_id: str,
        timeout_sec: int,
    ) -> dict[str, Any]:
        self.calls.append(("cleanup_acceptance", run_id))
        return self._for_run(self.cleanup, run_id)


def _run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    transport: RecoveryTransport,
) -> Any:
    monkeypatch.setattr(
        "prefab_sentinel.unity_acceptance.controller.collect_source_identity",
        lambda repo_root: _identity(),
    )
    monkeypatch.setattr(
        "prefab_sentinel.unity_acceptance.controller.uuid4",
        lambda: SimpleNamespace(hex=RUN_ID),
    )
    clock = FakeClock()
    return asyncio.run(
        run_acceptance(
            transport.config,
            transport,
            clock,
            clock.sleep,
        )
    )


def test_smoke_timeout_reconnects_status_cleans_and_postchecks_same_run_id(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _config(tmp_path)
    transport = RecoveryTransport(
        config,
        smoke=_envelope(
            success=False,
            code="ACCEPTANCE_TRANSPORT_TIMEOUT",
        ),
    )

    result = _run(tmp_path, monkeypatch, transport)

    assert (result.success, result.code, result.failed_phase) == (
        False,
        "ACCEPTANCE_SMOKE_TIMEOUT",
        "smoke",
    )
    assert transport.calls[-4:] == [
        ("acceptance_status", RUN_ID),
        ("cleanup_acceptance", RUN_ID),
        ("project_status", None),
        ("console_errors", None),
    ]
    cleanup_phase = next(phase for phase in result.phases if phase.name == "cleanup")
    assert cleanup_phase.success is True


def test_cleanup_failure_overrides_smoke_success(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _config(tmp_path)
    transport = RecoveryTransport(
        config,
        smoke=_envelope(
            code="ACCEPTANCE_RUN_OK",
            data={"fixture_owned": True},
        ),
        cleanup=_envelope(
            success=False,
            code="EDITOR_CTRL_ACCEPTANCE_CLEANUP_FAILED",
        ),
    )

    result = _run(tmp_path, monkeypatch, transport)

    assert (result.code, result.failed_phase) == (
        "ACCEPTANCE_CLEANUP_FAILED",
        "cleanup",
    )


def test_cleanup_failure_overrides_smoke_timeout(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _config(tmp_path)
    transport = RecoveryTransport(
        config,
        smoke=_envelope(
            success=False,
            code="EDITOR_BRIDGE_TIMEOUT",
        ),
        cleanup=_envelope(
            success=False,
            code="EDITOR_CTRL_ACCEPTANCE_CLEANUP_FAILED",
        ),
    )

    result = _run(tmp_path, monkeypatch, transport)

    assert (result.code, result.failed_phase) == (
        "ACCEPTANCE_CLEANUP_FAILED",
        "cleanup",
    )
    assert transport.calls[-4:] == [
        ("acceptance_status", RUN_ID),
        ("cleanup_acceptance", RUN_ID),
        ("project_status", None),
        ("console_errors", None),
    ]


def test_timeout_attempts_same_run_cleanup_even_when_status_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _config(tmp_path)
    transport = RecoveryTransport(
        config,
        smoke=_envelope(
            success=False,
            code="ACCEPTANCE_TRANSPORT_TIMEOUT",
        ),
        status=_envelope(
            success=False,
            code="EDITOR_CTRL_ACCEPTANCE_RUN_MISMATCH",
        ),
    )

    result = _run(tmp_path, monkeypatch, transport)

    assert (result.code, result.failed_phase) == (
        "ACCEPTANCE_CLEANUP_FAILED",
        "cleanup",
    )
    assert ("cleanup_acceptance", RUN_ID) in transport.calls
