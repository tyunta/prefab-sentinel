from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import BinaryIO
from unittest import TestCase
from unittest.mock import patch

import pytest

from prefab_sentinel.contracts import ToolResponse
from prefab_sentinel.unity_acceptance import compile_observer
from prefab_sentinel.unity_acceptance.compile_observer import (
    capture_compile_baseline,
    wait_for_compile,
)
from prefab_sentinel.unity_acceptance.model import CompileBaseline

_COMPILE_COMPLETED_LOG = "Reloading assemblies after finishing script compilation.\n"
_GRAPH = "Library/Bee/1900b0aE.dag"
_SUPERSEDED_ERROR = "error CS2001"


def _generation_start(graph: str = _GRAPH) -> str:
    return (
        "Starting: <UNITY_EDITOR>/Data/bee_backend.exe --ipc "
        f'--dagfile="{graph}" --profile="Library/Bee/backend.traceevents" '
        "ScriptAssemblies\n"
    )


def _failed_generation(graph: str = _GRAPH) -> str:
    return (
        _generation_start(graph)
        + "[790/841 0s] Csc Library/Bee/artifacts/1900b0aE.dag/"
        + "PrefabSentinel.Editor.dll (+2 others)\n"
        + "Assets/Editor/Retired.cs(4,2): error CS2001: Source file not found\n"
    )


def _additional_run() -> str:
    return (
        "*** Tundra requires additional run (0.71 seconds), 2 items updated, "
        "767 evaluatedStarting: <UNITY_EDITOR>/Data/Tools/netcorerun/"
        "netcorerun.exe ScriptCompilationBuildProgram.exe "
        "Library/Bee/1900b0aE.dag.json\n"
    )


def _successful_generation(graph: str = _GRAPH) -> str:
    return (
        _generation_start(graph)
        + "[833/841 1s] Csc Library/Bee/artifacts/1900b0aE.dag/"
        + "PrefabSentinel.Editor.dll (+2 others)\n"
        + "[840/841 0s] CopyFiles Library/ScriptAssemblies/"
        + "PrefabSentinel.Editor.dll\n"
        + "*** Tundra build success (1.23 seconds), 1 items updated\n"
        + _COMPILE_COMPLETED_LOG
    )


@dataclass
class _ObserverFixture:
    dll_path: Path
    log_path: Path
    deadline: float = 4.0
    now: float = 0.0
    sleeps: int = 0
    sleep_durations: list[float] = field(default_factory=list)

    def append_log(self, text: str) -> None:
        with self.log_path.open("ab") as log_file:
            log_file.write(text.encode("utf-8"))

    def replace_dll(self, contents: bytes) -> None:
        replacement = self.dll_path.with_suffix(".replacement")
        replacement.write_bytes(contents)
        replacement.replace(self.dll_path)

    def replace_log(self, contents: bytes) -> None:
        replacement = self.log_path.with_suffix(".replacement")
        replacement.write_bytes(contents)
        replacement.replace(self.log_path)

    def truncate_log(self, contents: bytes) -> None:
        self.log_path.write_bytes(contents)

    def clock(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps += 1
        self.sleep_durations.append(seconds)
        self.now += seconds


def _unity_log(project_root: Path, tail: bytes) -> bytes:
    return (
        b"COMMAND LINE ARGUMENTS:\n"
        b"Unity\n"
        b"-projectPath\n"
        + str(project_root).encode("utf-8")
        + b"\n"
        + tail
    )


def _baseline(tmp_path: Path, *, dll: bytes, log: bytes) -> CompileBaseline:
    project_root = tmp_path / "project"
    dll_path = project_root / "Library/ScriptAssemblies/PrefabSentinel.Editor.dll"
    dll_path.parent.mkdir(parents=True)
    dll_path.write_bytes(dll)
    log_path = tmp_path / "Editor.log"
    log_path.write_bytes(_unity_log(project_root, log))

    baseline = capture_compile_baseline(project_root, log_path)

    assert isinstance(baseline, CompileBaseline)
    return baseline


def _observer_fixture(_tmp_path: Path, baseline: CompileBaseline) -> _ObserverFixture:
    return _ObserverFixture(baseline.dll_path, baseline.log_path)


def test_capture_compile_baseline_records_dll_and_log_identity(tmp_path: Path) -> None:
    baseline = _baseline(tmp_path, dll=b"old", log=b"before\n")

    assert baseline.dll_size == 3
    assert baseline.dll_sha256 is not None
    assert baseline.log_size == len(_unity_log(tmp_path / "project", b"before\n"))
    assert baseline.log_device >= 0
    assert baseline.log_inode >= 0


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="FIFO requires POSIX")
@pytest.mark.parametrize("with_header_writer", [False, True])
def test_capture_compile_baseline_rejects_fifo_without_waiting(
    tmp_path: Path,
    with_header_writer: bool,
) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    log_path = tmp_path / "Editor.log"
    os.mkfifo(log_path)
    writer = None
    try:
        if with_header_writer:
            writer = subprocess.Popen(
                [
                    sys.executable,
                    "-c",
                    "from pathlib import Path; import sys; Path(sys.argv[1]).write_bytes(bytes.fromhex(sys.argv[2]))",
                    str(log_path),
                    _unity_log(project_root, b"before\n").hex(),
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        observed = subprocess.run(
            [
                sys.executable,
                "-c",
                "import json, sys; from pathlib import Path; "
                "from prefab_sentinel.contracts import ToolResponse; "
                "from prefab_sentinel.unity_acceptance.compile_observer "
                "import capture_compile_baseline; "
                "result = capture_compile_baseline(Path(sys.argv[1]), Path(sys.argv[2])); "
                "print(json.dumps(result.to_dict() if isinstance(result, ToolResponse) "
                "else {'baseline': True}))",
                str(project_root),
                str(log_path),
            ],
            capture_output=True,
            text=True,
            timeout=5,
            check=True,
        )
    finally:
        # Early rejection may leave the producer waiting for a reader. Reap it
        # even when the bounded reader fails, so a regression cannot leak tasks.
        if writer is not None:
            if writer.poll() is None:
                writer.kill()
            writer.wait(timeout=5)

    response = json.loads(observed.stdout)
    assert response == {
        "success": False,
        "severity": "error",
        "code": "ACCEPTANCE_CONFIG_ERROR",
        "message": "Unity Editor log must be a regular file.",
        "data": {},
        "diagnostics": [],
    }


def test_capture_compile_baseline_rejects_a_different_unity_project_log(
    tmp_path: Path,
) -> None:
    project_root = tmp_path / "project"
    dll_path = project_root / "Library/ScriptAssemblies/PrefabSentinel.Editor.dll"
    dll_path.parent.mkdir(parents=True)
    dll_path.write_bytes(b"old")
    log_path = tmp_path / "Editor.log"
    log_path.write_bytes(_unity_log(tmp_path / "other-project", b"before\n"))

    response = capture_compile_baseline(project_root, log_path)

    assert isinstance(response, ToolResponse)
    assert (response.success, response.code, response.message) == (
        False,
        "ACCEPTANCE_CONFIG_ERROR",
        "Unity Editor log belongs to a different project.",
    )
    assert str(project_root) not in str(response.to_dict())


def test_capture_compile_baseline_requires_log_project_identity(
    tmp_path: Path,
) -> None:
    project_root = tmp_path / "project"
    dll_path = project_root / "Library/ScriptAssemblies/PrefabSentinel.Editor.dll"
    dll_path.parent.mkdir(parents=True)
    dll_path.write_bytes(b"old")
    log_path = tmp_path / "Editor.log"
    log_path.write_bytes(b"Compilation finished\n")

    response = capture_compile_baseline(project_root, log_path)

    assert isinstance(response, ToolResponse)
    assert (response.success, response.code, response.message) == (
        False,
        "ACCEPTANCE_CONFIG_ERROR",
        "Unity Editor log project identity is unavailable.",
    )


def test_changed_deploy_requires_new_stable_dll_and_clean_appended_log(
    tmp_path: Path,
) -> None:
    baseline = _baseline(tmp_path, dll=b"old", log=b"before\n")
    fixture = _observer_fixture(tmp_path, baseline)
    fixture.append_log("Compilation started\n")
    fixture.replace_dll(b"new")
    fixture.append_log(_COMPILE_COMPLETED_LOG)

    result = wait_for_compile(
        baseline,
        changed_deploy=True,
        deadline=fixture.deadline,
        clock=fixture.clock,
        sleep=fixture.sleep,
    )

    assert (result.success, result.code) == (
        True,
        "ACCEPTANCE_COMPILE_OK",
    )
    assert result.dll_sha256 != baseline.dll_sha256
    assert result.compiler_errors == ()
    assert fixture.sleeps == 1


def test_changed_dll_and_unrelated_log_bytes_do_not_prove_compile_completion(
    tmp_path: Path,
) -> None:
    baseline = _baseline(tmp_path, dll=b"old", log=b"before\n")
    fixture = _observer_fixture(tmp_path, baseline)
    fixture.deadline = 2.0
    fixture.append_log("Unrelated file watcher activity\n")
    fixture.replace_dll(b"new")

    result = wait_for_compile(
        baseline,
        changed_deploy=True,
        deadline=fixture.deadline,
        clock=fixture.clock,
        sleep=fixture.sleep,
    )

    assert (result.success, result.code) == (
        False,
        "ACCEPTANCE_COMPILE_TIMEOUT",
    )


def test_compile_log_reads_are_bounded(
    tmp_path: Path,
) -> None:
    baseline = _baseline(tmp_path, dll=b"old", log=b"before\n")
    fixture = _observer_fixture(tmp_path, baseline)
    fixture.append_log(("x" * 1000 + "\n") * 70 + _COMPILE_COMPLETED_LOG)
    fixture.replace_dll(b"new")
    read_sizes: list[int] = []
    original_open = open

    class RecordingReader:
        def __init__(self, stream: BinaryIO) -> None:
            self._stream = stream

        def __enter__(self) -> RecordingReader:
            return self

        def __exit__(self, *args: object) -> None:
            self._stream.close()

        def fileno(self) -> int:
            return self._stream.fileno()

        def seek(self, offset: int) -> int:
            return self._stream.seek(offset)

        def read(self, size: int = -1) -> bytes:
            read_sizes.append(size)
            return self._stream.read(size)

    def recording_open(path: Path, *args, **kwargs):
        stream = original_open(path, *args, **kwargs)
        if path == baseline.log_path and args == ("rb",):
            return RecordingReader(stream)
        return stream

    with patch("builtins.open", recording_open):
        result = wait_for_compile(
            baseline,
            changed_deploy=True,
            deadline=fixture.deadline,
            clock=fixture.clock,
            sleep=fixture.sleep,
        )

    assert (result.success, result.code) == (
        True,
        "ACCEPTANCE_COMPILE_OK",
    )
    assert len(read_sizes) >= 2
    assert all(0 < size <= 65536 for size in read_sizes)


def test_compile_log_backlog_is_drained_before_poll_sleep(
    tmp_path: Path,
) -> None:
    baseline = _baseline(tmp_path, dll=b"old", log=b"before\n")
    fixture = _observer_fixture(tmp_path, baseline)
    fixture.deadline = 2.0
    fixture.append_log(("x" * 1024 + "\n") * 193 + _COMPILE_COMPLETED_LOG)
    fixture.replace_dll(b"new")

    result = wait_for_compile(
        baseline,
        changed_deploy=True,
        deadline=fixture.deadline,
        clock=fixture.clock,
        sleep=fixture.sleep,
    )

    assert (result.success, result.code) == (
        True,
        "ACCEPTANCE_COMPILE_OK",
    )
    assert fixture.sleeps == 1


@pytest.mark.parametrize(
    ("completion_time", "success", "code"),
    [
        (1.999, True, "ACCEPTANCE_COMPILE_OK"),
        (2.0, False, "ACCEPTANCE_COMPILE_TIMEOUT"),
        (2.001, False, "ACCEPTANCE_COMPILE_TIMEOUT"),
    ],
)
def test_stable_dll_completion_respects_final_deadline(
    tmp_path: Path,
    completion_time: float,
    success: bool,
    code: str,
) -> None:
    baseline = _baseline(tmp_path, dll=b"old", log=b"before\n")
    fixture = _observer_fixture(tmp_path, baseline)
    fixture.deadline = 2.0
    fixture.replace_dll(b"new")
    fixture.append_log(_COMPILE_COMPLETED_LOG)
    original_identity = compile_observer._dll_identity
    observations = 0

    def observe_dll(path: Path) -> tuple[int, int, str] | None:
        nonlocal observations
        identity = original_identity(path)
        observations += 1
        if observations == 2:
            # Reach the success-ready observation before consuming the final
            # budget in DLL I/O, past both loop-entry deadline checks.
            fixture.now = completion_time
        return identity

    with patch.object(compile_observer, "_dll_identity", observe_dll):
        result = wait_for_compile(
            baseline,
            changed_deploy=True,
            deadline=fixture.deadline,
            clock=fixture.clock,
            sleep=fixture.sleep,
        )

    assert (result.success, result.code) == (success, code)
    assert result.dll_sha256 != baseline.dll_sha256
    assert result.compiler_errors == ()
    assert observations == 2
    assert fixture.sleeps == 1


def test_poll_sleep_does_not_exceed_remaining_deadline_budget(
    tmp_path: Path,
) -> None:
    baseline = _baseline(tmp_path, dll=b"old", log=b"before\\n")
    fixture = _observer_fixture(tmp_path, baseline)
    fixture.deadline = 0.25
    fixture.append_log("Compilation started\\n")

    result = wait_for_compile(
        baseline,
        changed_deploy=True,
        deadline=fixture.deadline,
        clock=fixture.clock,
        sleep=fixture.sleep,
    )

    assert (result.success, result.code) == (
        False,
        "ACCEPTANCE_COMPILE_TIMEOUT",
    )
    assert fixture.sleep_durations == [0.25]


def test_compile_error_in_appended_log_fails(tmp_path: Path) -> None:
    baseline = _baseline(tmp_path, dll=b"old", log=b"before\n")
    fixture = _observer_fixture(tmp_path, baseline)
    fixture.append_log("Assets/Test.cs(4,2): error CS1002: ; expected\n")
    fixture.replace_dll(b"failed-build")

    result = wait_for_compile(
        baseline,
        changed_deploy=True,
        deadline=fixture.deadline,
        clock=fixture.clock,
        sleep=fixture.sleep,
    )

    assert (result.success, result.code) == (
        False,
        "ACCEPTANCE_COMPILE_FAILED",
    )
    assert result.compiler_errors == ("error CS1002",)


def test_compiler_errors_expose_only_stable_error_codes(tmp_path: Path) -> None:
    baseline = _baseline(tmp_path, dll=b"old", log=b"before\\n")
    fixture = _observer_fixture(tmp_path, baseline)
    fixture.append_log(
        "C:/Users/private/Assets/Test.cs(4,2): error CS1002: secret exception text\\n"
    )
    fixture.replace_dll(b"failed-build")

    result = wait_for_compile(
        baseline,
        changed_deploy=True,
        deadline=fixture.deadline,
        clock=fixture.clock,
        sleep=fixture.sleep,
    )

    assert result.compiler_errors == ("error CS1002",)


def _check_same_graph_additional_run_supersedes_prior_generation_error(
    tmp_path: Path,
) -> None:
    baseline = _baseline(tmp_path, dll=b"old", log=b"before\n")
    fixture = _observer_fixture(tmp_path, baseline)
    fixture.append_log(
        _failed_generation()
        + _additional_run()
        + _successful_generation()
    )
    fixture.replace_dll(b"new")

    result = wait_for_compile(
        baseline,
        changed_deploy=True,
        deadline=fixture.deadline,
        clock=fixture.clock,
        sleep=fixture.sleep,
    )

    assert (result.success, result.code) == (True, "ACCEPTANCE_COMPILE_OK")
    assert result.compiler_errors == ()
    assert result.superseded_compiler_errors == (_SUPERSEDED_ERROR,)


def _check_generation_evidence_survives_chunk_and_utf8_boundaries(
    tmp_path: Path,
) -> None:
    baseline = _baseline(tmp_path, dll=b"old", log=b"before\n")
    fixture = _observer_fixture(tmp_path, baseline)
    fixture.append_log(
        "日本語\n"
        + _failed_generation()
        + _additional_run()
        + _successful_generation()
    )
    fixture.replace_dll(b"new")

    with patch.object(compile_observer, "_LOG_APPEND_CHUNK_BYTES", 17):
        result = wait_for_compile(
            baseline,
            changed_deploy=True,
            deadline=fixture.deadline,
            clock=fixture.clock,
            sleep=fixture.sleep,
        )

    assert (result.success, result.code) == (True, "ACCEPTANCE_COMPILE_OK")
    assert result.compiler_errors == ()
    assert result.superseded_compiler_errors == (_SUPERSEDED_ERROR,)


def _check_error_is_not_superseded_without_exact_generation_binding(
    tmp_path: Path,
    appended_log: str,
    expected_errors: tuple[str, ...],
) -> None:
    baseline = _baseline(tmp_path, dll=b"old", log=b"before\n")
    fixture = _observer_fixture(tmp_path, baseline)
    fixture.append_log(appended_log)
    fixture.replace_dll(b"new")

    result = wait_for_compile(
        baseline,
        changed_deploy=True,
        deadline=fixture.deadline,
        clock=fixture.clock,
        sleep=fixture.sleep,
    )

    assert (result.success, result.code) == (False, "ACCEPTANCE_COMPILE_FAILED")
    assert result.compiler_errors == expected_errors
    assert result.superseded_compiler_errors == ()


def _check_superseded_error_path_requires_every_ordered_final_generation_evidence(
    tmp_path: Path,
    omitted_evidence: str,
) -> None:
    evidence = {
        "csc": (
            "[833/841 1s] Csc Library/Bee/artifacts/1900b0aE.dag/"
            "PrefabSentinel.Editor.dll (+2 others)\n"
        ),
        "copy": (
            "[840/841 0s] CopyFiles Library/ScriptAssemblies/"
            "PrefabSentinel.Editor.dll\n"
        ),
        "build": "*** Tundra build success (1.23 seconds), 1 items updated\n",
        "reload": _COMPILE_COMPLETED_LOG,
    }
    final_generation = _generation_start() + "".join(
        text for name, text in evidence.items() if name != omitted_evidence
    )
    baseline = _baseline(tmp_path, dll=b"old", log=b"before\n")
    fixture = _observer_fixture(tmp_path, baseline)
    fixture.append_log(
        _failed_generation()
        + _additional_run()
        + final_generation
    )
    fixture.replace_dll(b"new")

    result = wait_for_compile(
        baseline,
        changed_deploy=True,
        deadline=fixture.deadline,
        clock=fixture.clock,
        sleep=fixture.sleep,
    )

    assert (result.success, result.code) == (False, "ACCEPTANCE_COMPILE_TIMEOUT")
    assert result.compiler_errors == ()
    assert result.superseded_compiler_errors == (_SUPERSEDED_ERROR,)


def _check_partial_tundra_success_prefix_does_not_complete_superseding_generation(
    tmp_path: Path,
) -> None:
    final_generation = (
        _generation_start()
        + "[833/841 1s] Csc Library/Bee/artifacts/1900b0aE.dag/"
        + "PrefabSentinel.Editor.dll (+2 others)\n"
        + "[840/841 0s] CopyFiles Library/ScriptAssemblies/"
        + "PrefabSentinel.Editor.dll\n"
        + "*** Tundra build suc"
        + _COMPILE_COMPLETED_LOG
    )
    baseline = _baseline(tmp_path, dll=b"old", log=b"before\n")
    fixture = _observer_fixture(tmp_path, baseline)
    fixture.append_log(
        _failed_generation()
        + _additional_run()
        + final_generation
    )
    fixture.replace_dll(b"new")

    result = wait_for_compile(
        baseline,
        changed_deploy=True,
        deadline=fixture.deadline,
        clock=fixture.clock,
        sleep=fixture.sleep,
    )

    assert (result.success, result.code) == (False, "ACCEPTANCE_COMPILE_TIMEOUT")
    assert result.superseded_compiler_errors == (_SUPERSEDED_ERROR,)


def _check_error_after_final_generation_evidence_remains_terminal(
    tmp_path: Path,
) -> None:
    baseline = _baseline(tmp_path, dll=b"old", log=b"before\n")
    fixture = _observer_fixture(tmp_path, baseline)
    fixture.append_log(
        _failed_generation()
        + _additional_run()
        + _successful_generation()
        + "Assets/Editor/New.cs(5,3): error CS1003: Syntax error\n"
    )
    fixture.replace_dll(b"new")

    result = wait_for_compile(
        baseline,
        changed_deploy=True,
        deadline=fixture.deadline,
        clock=fixture.clock,
        sleep=fixture.sleep,
    )

    assert (result.success, result.code) == (False, "ACCEPTANCE_COMPILE_FAILED")
    assert result.compiler_errors == ("error CS1003",)
    assert result.superseded_compiler_errors == (_SUPERSEDED_ERROR,)


def _check_error_appended_after_final_evidence_prevents_second_stable_sample(
    tmp_path: Path,
) -> None:
    baseline = _baseline(tmp_path, dll=b"old", log=b"before\n")
    fixture = _observer_fixture(tmp_path, baseline)
    fixture.append_log(
        _failed_generation()
        + _additional_run()
        + _successful_generation()
    )
    fixture.replace_dll(b"new")

    def append_late_error(seconds: float) -> None:
        fixture.sleep(seconds)
        if fixture.sleeps == 1:
            fixture.append_log(
                "Assets/Editor/New.cs(5,3): error CS1003: Syntax error\n"
            )

    result = wait_for_compile(
        baseline,
        changed_deploy=True,
        deadline=fixture.deadline,
        clock=fixture.clock,
        sleep=append_late_error,
    )

    assert (result.success, result.code) == (False, "ACCEPTANCE_COMPILE_FAILED")
    assert result.compiler_errors == ("error CS1003",)
    assert result.superseded_compiler_errors == (_SUPERSEDED_ERROR,)


def _check_continuity_loss_retains_known_superseded_error_history(
    tmp_path: Path,
) -> None:
    baseline = _baseline(tmp_path, dll=b"old", log=b"before\n")
    fixture = _observer_fixture(tmp_path, baseline)
    fixture.append_log(
        _failed_generation()
        + _additional_run()
        + _generation_start()
    )

    def lose_continuity(seconds: float) -> None:
        fixture.sleep(seconds)
        fixture.replace_log(b"replacement\n")

    result = wait_for_compile(
        baseline,
        changed_deploy=True,
        deadline=fixture.deadline,
        clock=fixture.clock,
        sleep=lose_continuity,
    )

    assert (result.success, result.code) == (
        False,
        "ACCEPTANCE_COMPILE_LOG_CONTINUITY_LOST",
    )
    assert result.compiler_errors == ()
    assert result.superseded_compiler_errors == (_SUPERSEDED_ERROR,)


def test_log_replacement_fails_before_parsing_appended_bytes(tmp_path: Path) -> None:
    baseline = _baseline(tmp_path, dll=b"old", log=b"before\n")
    fixture = _observer_fixture(tmp_path, baseline)
    fixture.replace_log(b"Compilation finished\n")
    fixture.replace_dll(b"new")

    result = wait_for_compile(
        baseline,
        changed_deploy=True,
        deadline=fixture.deadline,
        clock=fixture.clock,
        sleep=fixture.sleep,
    )

    assert (result.success, result.code) == (
        False,
        "ACCEPTANCE_COMPILE_LOG_CONTINUITY_LOST",
    )


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="FIFO requires POSIX")
def test_post_baseline_fifo_replacement_returns_continuity_lost_without_waiting(
    tmp_path: Path,
) -> None:
    baseline = _baseline(tmp_path, dll=b"old", log=b"before\n")
    baseline.log_path.replace(baseline.log_path.with_suffix(".baseline"))
    os.mkfifo(baseline.log_path)
    baseline_payload = {
        "dll_path": str(baseline.dll_path),
        "dll_size": baseline.dll_size,
        "dll_mtime_ns": baseline.dll_mtime_ns,
        "dll_sha256": baseline.dll_sha256,
        "log_path": str(baseline.log_path),
        "log_device": baseline.log_device,
        "log_inode": baseline.log_inode,
        "log_size": baseline.log_size,
    }

    observed = subprocess.run(
        [
            sys.executable,
            "-c",
            "import json, sys, time; from pathlib import Path; "
            "from prefab_sentinel.unity_acceptance.compile_observer "
            "import wait_for_compile; "
            "from prefab_sentinel.unity_acceptance.model import CompileBaseline; "
            "payload = json.loads(sys.argv[1]); "
            "payload['dll_path'] = Path(payload['dll_path']); "
            "payload['log_path'] = Path(payload['log_path']); "
            "baseline = CompileBaseline(**payload); "
            "result = wait_for_compile(baseline, changed_deploy=True, "
            "deadline=time.monotonic() + 1.0, clock=time.monotonic, sleep=time.sleep); "
            "print(json.dumps([result.success, result.code]))",
            json.dumps(baseline_payload),
        ],
        capture_output=True,
        text=True,
        timeout=5,
        check=True,
    )

    assert json.loads(observed.stdout) == [
        False,
        "ACCEPTANCE_COMPILE_LOG_CONTINUITY_LOST",
    ]


def test_post_baseline_regular_log_uses_supported_platform_open_flags(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    baseline = _baseline(tmp_path, dll=b"old", log=b"before\n")
    fixture = _observer_fixture(tmp_path, baseline)
    fixture.append_log(_COMPILE_COMPLETED_LOG)
    fixture.replace_dll(b"new")
    monkeypatch.delattr(os, "O_NONBLOCK", raising=False)

    result = wait_for_compile(
        baseline,
        changed_deploy=True,
        deadline=fixture.deadline,
        clock=fixture.clock,
        sleep=fixture.sleep,
    )

    assert (result.success, result.code) == (
        True,
        "ACCEPTANCE_COMPILE_OK",
    )


def test_log_replacement_between_path_stat_and_open_fails_closed(
    tmp_path: Path,
) -> None:
    baseline = _baseline(tmp_path, dll=b"old", log=b"before\n")
    fixture = _observer_fixture(tmp_path, baseline)
    fixture.append_log("Compilation started\n")
    fixture.replace_dll(b"new")
    log_open_count = 0
    original_open = open

    def replace_before_second_open(path: Path, *args, **kwargs):
        nonlocal log_open_count
        if path == baseline.log_path:
            log_open_count += 1
            if log_open_count == 2:
                fixture.replace_log(b"Compilation finished\n")
        return original_open(path, *args, **kwargs)

    with patch("builtins.open", replace_before_second_open):
        result = wait_for_compile(
            baseline,
            changed_deploy=True,
            deadline=fixture.deadline,
            clock=fixture.clock,
            sleep=fixture.sleep,
        )

    assert log_open_count == 2
    assert (result.success, result.code) == (
        False,
        "ACCEPTANCE_COMPILE_LOG_CONTINUITY_LOST",
    )


def test_log_truncation_fails_before_parsing_appended_bytes(tmp_path: Path) -> None:
    baseline = _baseline(tmp_path, dll=b"old", log=b"before\n")
    fixture = _observer_fixture(tmp_path, baseline)
    fixture.truncate_log(b"new\n")
    fixture.replace_dll(b"new")

    result = wait_for_compile(
        baseline,
        changed_deploy=True,
        deadline=fixture.deadline,
        clock=fixture.clock,
        sleep=fixture.sleep,
    )

    assert (result.success, result.code) == (
        False,
        "ACCEPTANCE_COMPILE_LOG_CONTINUITY_LOST",
    )


def test_undecodable_appended_log_fails_closed(tmp_path: Path) -> None:
    baseline = _baseline(tmp_path, dll=b"old", log=b"before\n")
    fixture = _observer_fixture(tmp_path, baseline)
    with fixture.log_path.open("ab") as log_file:
        log_file.write(b"\xff")
    fixture.replace_dll(b"new")

    result = wait_for_compile(
        baseline,
        changed_deploy=True,
        deadline=fixture.deadline,
        clock=fixture.clock,
        sleep=fixture.sleep,
    )

    assert (result.success, result.code) == (
        False,
        "ACCEPTANCE_COMPILE_LOG_CONTINUITY_LOST",
    )


def test_incomplete_utf8_append_remains_pending_until_completed(
    tmp_path: Path,
) -> None:
    baseline = _baseline(tmp_path, dll=b"old", log=b"before\n")
    fixture = _observer_fixture(tmp_path, baseline)
    with fixture.log_path.open("ab") as log_file:
        log_file.write(b"Compilation started \xe3")
    fixture.replace_dll(b"new")

    def append_utf8_remainder(seconds: float) -> None:
        fixture.sleep(seconds)
        if fixture.sleeps == 1:
            with fixture.log_path.open("ab") as log_file:
                log_file.write(
                    b"\x81\x82\n" + _COMPILE_COMPLETED_LOG.encode("utf-8")
                )

    result = wait_for_compile(
        baseline,
        changed_deploy=True,
        deadline=fixture.deadline,
        clock=fixture.clock,
        sleep=append_utf8_remainder,
    )

    assert (result.success, result.code) == (
        True,
        "ACCEPTANCE_COMPILE_OK",
    )


def test_deadline_expires_without_a_new_stable_dll(tmp_path: Path) -> None:
    baseline = _baseline(tmp_path, dll=b"old", log=b"before\n")
    fixture = _observer_fixture(tmp_path, baseline)
    fixture.deadline = 2.0
    fixture.append_log("Compilation started\n")

    result = wait_for_compile(
        baseline,
        changed_deploy=True,
        deadline=fixture.deadline,
        clock=fixture.clock,
        sleep=fixture.sleep,
    )

    assert (result.success, result.code) == (
        False,
        "ACCEPTANCE_COMPILE_TIMEOUT",
    )
    assert fixture.sleeps == 2


def test_changed_deploy_does_not_succeed_without_appended_log_evidence(
    tmp_path: Path,
) -> None:
    baseline = _baseline(tmp_path, dll=b"old", log=b"before\n")
    fixture = _observer_fixture(tmp_path, baseline)
    fixture.deadline = 2.0
    fixture.replace_dll(b"new")

    result = wait_for_compile(
        baseline,
        changed_deploy=True,
        deadline=fixture.deadline,
        clock=fixture.clock,
        sleep=fixture.sleep,
    )

    assert (result.success, result.code) == (
        False,
        "ACCEPTANCE_COMPILE_TIMEOUT",
    )


def test_no_op_deploy_returns_not_required_without_waiting(tmp_path: Path) -> None:
    baseline = _baseline(tmp_path, dll=b"old", log=b"before\n")

    result = wait_for_compile(
        baseline,
        changed_deploy=False,
        deadline=0.0,
        clock=lambda: 0.0,
        sleep=lambda _seconds: (_ for _ in ()).throw(AssertionError("must not wait")),
    )

    assert (result.success, result.code) == (
        True,
        "ACCEPTANCE_COMPILE_NOT_REQUIRED",
    )
    assert result.dll_sha256 == baseline.dll_sha256


class TestCompileGenerationEvidence(TestCase):
    def test_same_graph_additional_run_supersedes_prior_generation_error(self) -> None:
        with TemporaryDirectory() as directory:
            _check_same_graph_additional_run_supersedes_prior_generation_error(
                Path(directory)
            )

    def test_generation_evidence_survives_chunk_and_utf8_boundaries(self) -> None:
        with TemporaryDirectory() as directory:
            _check_generation_evidence_survives_chunk_and_utf8_boundaries(
                Path(directory)
            )

    def test_error_is_not_superseded_without_exact_generation_binding(self) -> None:
        cases = (
            (
                "unscoped-error",
                "Assets/Editor/Test.cs(4,2): error CS1002: ; expected\n"
                + _additional_run()
                + _successful_generation(),
                ("error CS1002",),
            ),
            (
                "different-graph",
                _failed_generation()
                + _additional_run()
                + _successful_generation("Library/Bee/different.dag"),
                (_SUPERSEDED_ERROR,),
            ),
            (
                "missing-additional-run",
                _failed_generation() + _successful_generation(),
                (_SUPERSEDED_ERROR,),
            ),
        )
        for name, appended_log, expected_errors in cases:
            with self.subTest(name=name), TemporaryDirectory() as directory:
                _check_error_is_not_superseded_without_exact_generation_binding(
                    Path(directory),
                    appended_log,
                    expected_errors,
                )

    def test_superseded_path_requires_all_ordered_final_evidence(self) -> None:
        for omitted_evidence in ("csc", "copy", "build", "reload"):
            with (
                self.subTest(omitted_evidence=omitted_evidence),
                TemporaryDirectory() as directory,
            ):
                _check_superseded_error_path_requires_every_ordered_final_generation_evidence(
                    Path(directory),
                    omitted_evidence,
                )

    def test_partial_tundra_success_prefix_does_not_complete_generation(self) -> None:
        with TemporaryDirectory() as directory:
            _check_partial_tundra_success_prefix_does_not_complete_superseding_generation(
                Path(directory)
            )

    def test_error_after_final_generation_evidence_remains_terminal(self) -> None:
        with TemporaryDirectory() as directory:
            _check_error_after_final_generation_evidence_remains_terminal(
                Path(directory)
            )

    def test_error_before_second_stable_sample_remains_terminal(self) -> None:
        with TemporaryDirectory() as directory:
            _check_error_appended_after_final_evidence_prevents_second_stable_sample(
                Path(directory)
            )

    def test_late_delimited_error_is_terminal_with_or_without_newline(self) -> None:
        for terminator in ("", "\n"):
            with self.subTest(terminator=repr(terminator)), TemporaryDirectory() as directory:
                root = Path(directory)
                baseline = _baseline(root, dll=b"old", log=b"before\n")
                fixture = _observer_fixture(root, baseline)
                fixture.append_log(
                    _failed_generation()
                    + _additional_run()
                    + _successful_generation()
                )
                fixture.replace_dll(b"new")

                def append_late_error(
                    seconds: float,
                    fixture: _ObserverFixture = fixture,
                    terminator: str = terminator,
                ) -> None:
                    fixture.sleep(seconds)
                    if fixture.sleeps == 1:
                        fixture.append_log(
                            "Assets/Editor/New.cs(5,3): error CS1003: Syntax error"
                            + terminator
                        )

                result = wait_for_compile(
                    baseline,
                    changed_deploy=True,
                    deadline=fixture.deadline,
                    clock=fixture.clock,
                    sleep=append_late_error,
                )

                self.assertEqual(
                    (result.success, result.code),
                    (False, "ACCEPTANCE_COMPILE_FAILED"),
                )
                self.assertEqual(result.compiler_errors, ("error CS1003",))
                self.assertEqual(
                    result.superseded_compiler_errors,
                    (_SUPERSEDED_ERROR,),
                )

    def test_error_before_exact_eof_marker_is_terminal(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            baseline = _baseline(root, dll=b"old", log=b"before\n")
            fixture = _observer_fixture(root, baseline)
            fixture.append_log(
                "Assets/Editor/New.cs(5,3): error CS1003: Syntax error"
                + _COMPILE_COMPLETED_LOG.rstrip("\n")
            )
            fixture.replace_dll(b"new")

            result = wait_for_compile(
                baseline,
                changed_deploy=True,
                deadline=fixture.deadline,
                clock=fixture.clock,
                sleep=fixture.sleep,
            )

            self.assertEqual(
                (result.success, result.code),
                (False, "ACCEPTANCE_COMPILE_FAILED"),
            )
            self.assertEqual(result.compiler_errors, ("error CS1003",))

    def test_every_nonempty_error_prefix_at_second_sample_blocks_success(
        self,
    ) -> None:
        complete_token = "error CS1003:"
        for prefix_length in range(1, len(complete_token)):
            pending = complete_token[:prefix_length]
            with (
                self.subTest(pending=pending),
                TemporaryDirectory() as directory,
            ):
                root = Path(directory)
                baseline = _baseline(root, dll=b"old", log=b"before\n")
                fixture = _observer_fixture(root, baseline)
                fixture.append_log(_COMPILE_COMPLETED_LOG)
                fixture.replace_dll(b"new")

                def append_pending(
                    seconds: float,
                    fixture: _ObserverFixture = fixture,
                    pending: str = pending,
                ) -> None:
                    fixture.sleep(seconds)
                    if fixture.sleeps == 1:
                        fixture.append_log(pending)

                result = wait_for_compile(
                    baseline,
                    changed_deploy=True,
                    deadline=fixture.deadline,
                    clock=fixture.clock,
                    sleep=append_pending,
                )

                self.assertEqual(
                    (result.success, result.code),
                    (False, "ACCEPTANCE_COMPILE_TIMEOUT"),
                )
                self.assertEqual(result.compiler_errors, ())

    def test_unrelated_unterminated_text_blocks_success(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            baseline = _baseline(root, dll=b"old", log=b"before\n")
            fixture = _observer_fixture(root, baseline)
            fixture.append_log(_COMPILE_COMPLETED_LOG)
            fixture.replace_dll(b"new")

            def append_pending(seconds: float) -> None:
                fixture.sleep(seconds)
                if fixture.sleeps == 1:
                    fixture.append_log("unrelated unterminated line")

            result = wait_for_compile(
                baseline,
                changed_deploy=True,
                deadline=fixture.deadline,
                clock=fixture.clock,
                sleep=append_pending,
            )

            self.assertEqual(
                (result.success, result.code),
                (False, "ACCEPTANCE_COMPILE_TIMEOUT"),
            )

    def test_whitespace_only_pending_text_does_not_block_success(self) -> None:
        for pending in (" ", "\t", "\r", " \t\r"):
            with self.subTest(pending=repr(pending)), TemporaryDirectory() as directory:
                root = Path(directory)
                baseline = _baseline(root, dll=b"old", log=b"before\n")
                fixture = _observer_fixture(root, baseline)
                fixture.append_log(_COMPILE_COMPLETED_LOG)
                fixture.append_log(pending)
                fixture.replace_dll(b"new")

                result = wait_for_compile(
                    baseline,
                    changed_deploy=True,
                    deadline=fixture.deadline,
                    clock=fixture.clock,
                    sleep=fixture.sleep,
                )

                self.assertEqual(
                    (result.success, result.code),
                    (True, "ACCEPTANCE_COMPILE_OK"),
                )

    def test_no_final_newline_error_exposes_only_stable_code(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            baseline = _baseline(root, dll=b"old", log=b"before\n")
            fixture = _observer_fixture(root, baseline)
            fixture.append_log(
                "C:/Users/private/Assets/Test.cs(4,2): error CS1002: "
                "secret exception text"
            )

            result = wait_for_compile(
                baseline,
                changed_deploy=True,
                deadline=fixture.deadline,
                clock=fixture.clock,
                sleep=fixture.sleep,
            )

            self.assertEqual(
                (result.success, result.code),
                (False, "ACCEPTANCE_COMPILE_FAILED"),
            )
            self.assertEqual(result.compiler_errors, ("error CS1002",))

    def test_continuity_loss_retains_superseded_error_history(self) -> None:
        with TemporaryDirectory() as directory:
            _check_continuity_loss_retains_known_superseded_error_history(
                Path(directory)
            )

    def test_invalid_token_continuation_is_chunk_invariant(self) -> None:
        valid_prefix = (
            _failed_generation()
            + _additional_run()
            + _generation_start()
            + "[833/841 1s] Csc Library/Bee/artifacts/1900b0aE.dag/"
            + "PrefabSentinel.Editor.dll (+2 others)\n"
            + "[840/841 0s] CopyFiles Library/ScriptAssemblies/"
            + "PrefabSentinel.Editor.dll\n"
            + "*** Tundra build success"
        )
        appended_log = valid_prefix + "ful\n" + _COMPILE_COMPLETED_LOG
        split_after_success = len(valid_prefix.encode("utf-8"))

        for chunk_size in (split_after_success, split_after_success - 1):
            with self.subTest(chunk_size=chunk_size), TemporaryDirectory() as directory:
                root = Path(directory)
                baseline = _baseline(root, dll=b"old", log=b"before\n")
                fixture = _observer_fixture(root, baseline)
                fixture.append_log(appended_log)
                fixture.replace_dll(b"new")

                with patch.object(
                    compile_observer,
                    "_LOG_APPEND_CHUNK_BYTES",
                    chunk_size,
                ):
                    result = wait_for_compile(
                        baseline,
                        changed_deploy=True,
                        deadline=fixture.deadline,
                        clock=fixture.clock,
                        sleep=fixture.sleep,
                    )

                self.assertEqual(
                    (result.success, result.code),
                    (False, "ACCEPTANCE_COMPILE_TIMEOUT"),
                )
                self.assertEqual(
                    result.superseded_compiler_errors,
                    (_SUPERSEDED_ERROR,),
                )

    def test_unfinished_line_limit_is_fail_closed_at_exact_boundaries(self) -> None:
        cases = (
            (4095, "ACCEPTANCE_COMPILE_TIMEOUT"),
            (4096, "ACCEPTANCE_COMPILE_TIMEOUT"),
            (4097, "ACCEPTANCE_COMPILE_LOG_CONTINUITY_LOST"),
        )
        for line_length, expected_code in cases:
            with self.subTest(line_length=line_length), TemporaryDirectory() as directory:
                root = Path(directory)
                baseline = _baseline(root, dll=b"old", log=b"before\n")
                fixture = _observer_fixture(root, baseline)
                fixture.deadline = 1.0
                fixture.append_log("x" * line_length)

                result = wait_for_compile(
                    baseline,
                    changed_deploy=True,
                    deadline=fixture.deadline,
                    clock=fixture.clock,
                    sleep=fixture.sleep,
                )

                self.assertEqual((result.success, result.code), (False, expected_code))

    def test_exact_reload_marker_without_final_newline_remains_accepted(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            baseline = _baseline(root, dll=b"old", log=b"before\n")
            fixture = _observer_fixture(root, baseline)
            fixture.append_log(_COMPILE_COMPLETED_LOG.rstrip("\n"))
            fixture.replace_dll(b"new")

            result = wait_for_compile(
                baseline,
                changed_deploy=True,
                deadline=fixture.deadline,
                clock=fixture.clock,
                sleep=fixture.sleep,
            )

            self.assertEqual(
                (result.success, result.code),
                (True, "ACCEPTANCE_COMPILE_OK"),
            )


class TestCompileObservationStability(TestCase):
    def test_late_marker_accepts_an_already_stable_new_dll(self) -> None:
        for marker_poll in (3, 4):
            with self.subTest(marker_poll=marker_poll), TemporaryDirectory() as directory:
                root = Path(directory)
                baseline = _baseline(root, dll=b"old", log=b"before\n")
                fixture = _observer_fixture(root, baseline)
                fixture.replace_dll(b"new")

                def sleep(
                    seconds: float,
                    fixture: _ObserverFixture = fixture,
                    marker_poll: int = marker_poll,
                ) -> None:
                    fixture.sleep(seconds)
                    if fixture.sleeps == marker_poll - 1:
                        fixture.append_log(_COMPILE_COMPLETED_LOG)

                result = wait_for_compile(
                    baseline,
                    changed_deploy=True,
                    deadline=fixture.deadline,
                    clock=fixture.clock,
                    sleep=sleep,
                )

                self.assertEqual((result.success, result.code), (True, "ACCEPTANCE_COMPILE_OK"))
                self.assertNotEqual(result.dll_sha256, baseline.dll_sha256)
                self.assertEqual(result.compiler_errors, ())
                self.assertEqual(fixture.now, marker_poll - 1)

    def test_stable_new_dll_requires_a_post_baseline_marker(self) -> None:
        for old_log in (b"before\n", _COMPILE_COMPLETED_LOG.encode("utf-8")):
            with self.subTest(old_log=old_log), TemporaryDirectory() as directory:
                root = Path(directory)
                baseline = _baseline(root, dll=b"old", log=old_log)
                fixture = _observer_fixture(root, baseline)
                fixture.replace_dll(b"new")

                result = wait_for_compile(
                    baseline,
                    changed_deploy=True,
                    deadline=fixture.deadline,
                    clock=fixture.clock,
                    sleep=fixture.sleep,
                )

                self.assertEqual((result.success, result.code), (False, "ACCEPTANCE_COMPILE_TIMEOUT"))
                self.assertEqual(fixture.now, 4.0)

    def test_early_marker_still_requires_two_identical_new_dll_samples(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            baseline = _baseline(root, dll=b"old", log=b"before\n")
            fixture = _observer_fixture(root, baseline)
            fixture.replace_dll(b"new")
            fixture.append_log(_COMPILE_COMPLETED_LOG)

            result = wait_for_compile(
                baseline,
                changed_deploy=True,
                deadline=fixture.deadline,
                clock=fixture.clock,
                sleep=fixture.sleep,
            )

            self.assertEqual((result.success, result.code), (True, "ACCEPTANCE_COMPILE_OK"))
            self.assertEqual(fixture.now, 1.0)

    def test_dll_identity_change_restarts_consecutive_stability(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            baseline = _baseline(root, dll=b"old", log=b"before\n")
            fixture = _observer_fixture(root, baseline)
            fixture.replace_dll(b"intermediate")
            fixture.append_log(_COMPILE_COMPLETED_LOG)

            def sleep(seconds: float) -> None:
                fixture.sleep(seconds)
                if fixture.sleeps == 1:
                    fixture.replace_dll(b"final")

            result = wait_for_compile(
                baseline,
                changed_deploy=True,
                deadline=fixture.deadline,
                clock=fixture.clock,
                sleep=sleep,
            )

            self.assertEqual((result.success, result.code), (True, "ACCEPTANCE_COMPILE_OK"))
            self.assertEqual(result.dll_size, 5)
            self.assertEqual(fixture.now, 2.0)

    def test_late_marker_cannot_succeed_at_or_after_the_deadline(self) -> None:
        read_dll_identity = compile_observer._dll_identity
        for observed_at in (4.0, 4.25):
            with self.subTest(observed_at=observed_at), TemporaryDirectory() as directory:
                root = Path(directory)
                baseline = _baseline(root, dll=b"old", log=b"before\n")
                fixture = _observer_fixture(root, baseline)
                fixture.replace_dll(b"new")

                def sleep(seconds: float, fixture: _ObserverFixture = fixture) -> None:
                    fixture.sleep(seconds)
                    if fixture.sleeps == 2:
                        fixture.append_log(_COMPILE_COMPLETED_LOG)

                def observe_dll(
                    path: Path,
                    fixture: _ObserverFixture = fixture,
                    observed_at: float = observed_at,
                ) -> tuple[int, int, str] | None:
                    identity = read_dll_identity(path)
                    if fixture.sleeps == 2:
                        fixture.now = observed_at
                    return identity

                with patch.object(compile_observer, "_dll_identity", observe_dll):
                    result = wait_for_compile(
                        baseline,
                        changed_deploy=True,
                        deadline=fixture.deadline,
                        clock=fixture.clock,
                        sleep=sleep,
                    )

                self.assertEqual((result.success, result.code), (False, "ACCEPTANCE_COMPILE_TIMEOUT"))
                self.assertEqual(fixture.now, observed_at)
