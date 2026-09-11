from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

_SCRIPT = Path(__file__).parents[1] / "scripts" / "run_unity_bridge_acceptance.py"


def _arguments(tmp_path: Path, *, confirm_live: bool) -> list[str]:
    project = tmp_path / "UnityProject"
    (project / "Assets").mkdir(parents=True)
    log = tmp_path / "Editor.log"
    log.write_text("log\n", encoding="utf-8")
    watch = tmp_path / "watch"
    watch.mkdir()
    arguments = [
        "--project-root",
        str(project),
        "--scope",
        "Assets/Acceptance",
        "--target-dir",
        str(project / "Assets/Editor/PrefabSentinel"),
        "--watch-dir",
        str(watch),
        "--unity-log-file",
        str(log),
        "--out-report",
        "reports/result.json",
    ]
    if confirm_live:
        arguments.append("--confirm-live")
    return arguments


def _run(arguments: list[str], environment: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(_SCRIPT), *arguments],
        check=False,
        capture_output=True,
        text=True,
        env=environment,
    )


def test_missing_required_arguments_is_an_argparse_failure() -> None:
    result = _run([])

    assert result.returncode == 2
    assert result.stdout == ""
    assert "required" in result.stderr


def test_help_states_that_the_safe_deploy_target_must_be_absolute() -> None:
    """Removing the absolute-path constraint from help would recreate a config-only failure."""
    result = _run(["--help"])

    assert result.returncode == 0
    assert "Absolute path under PROJECT_ROOT/Assets." in result.stdout


def test_missing_confirm_live_returns_one_terminal_json_object(tmp_path: Path) -> None:
    result = _run(_arguments(tmp_path, confirm_live=False))

    assert result.returncode != 0
    assert json.loads(result.stdout)["result"]["code"] == "ACCEPTANCE_OPT_IN_REQUIRED"
    assert result.stdout.count("\n") == 1
    assert "UNITYTOOL_" not in result.stdout


def test_recover_run_id_without_confirm_live_still_returns_opt_in_failure(
    tmp_path: Path,
) -> None:
    result = _run(
        _arguments(tmp_path, confirm_live=False)
        + ["--recover-run-id", "a" * 32]
    )

    assert result.returncode != 0
    assert result.stdout
    assert json.loads(result.stdout)["result"]["code"] == "ACCEPTANCE_OPT_IN_REQUIRED"
    assert "unrecognized arguments" not in result.stderr


def test_controller_success_is_one_json_object_and_exit_zero(tmp_path: Path) -> None:
    environment = _fake_controller_environment(tmp_path)

    result = _run(_arguments(tmp_path, confirm_live=True), environment)

    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert (payload["result"]["code"], payload["result"]["success"]) == (
        "ACCEPTANCE_OK",
        True,
    )
    assert set(payload) == {
        "schema_version",
        "audit",
        "source",
        "environment",
        "preflight",
        "deploy",
        "compile",
        "smoke",
        "cleanup",
        "result",
    }
    assert result.stderr == ""


def test_controller_failure_is_nonzero_and_transport_context_closes(tmp_path: Path) -> None:
    marker = tmp_path / "closed"
    environment = _fake_controller_environment(tmp_path, code="ACCEPTANCE_SMOKE_FAILED")
    environment["FAKE_TRANSPORT_CLOSED"] = str(marker)

    result = _run(_arguments(tmp_path, confirm_live=True), environment)

    assert result.returncode != 0
    assert json.loads(result.stdout)["result"]["code"] == "ACCEPTANCE_SMOKE_FAILED"
    assert marker.read_text(encoding="utf-8") == "closed"


def _fake_controller_environment(
    tmp_path: Path,
    *,
    code: str = "ACCEPTANCE_OK",
) -> dict[str, str]:
    site = tmp_path / "site"
    site.mkdir()
    (site / "sitecustomize.py").write_text(
        f"""
import os
import sys
import types

import prefab_sentinel.unity_acceptance as acceptance

class Result:
    def to_dict(self):
        sections = {{
            name: {{
                "executed": False,
                "success": None,
                "code": None,
                "data": {{}},
                "diagnostics": [],
            }}
            for name in (
                "audit", "source", "environment", "preflight", "deploy",
                "compile", "smoke", "cleanup",
            )
        }}
        return {{
            "schema_version": "unity_bridge_acceptance.v1",
            **sections,
            "result": {{
                "code": {code!r},
                "success": {code == "ACCEPTANCE_OK"!r},
                "severity": "info" if {code == "ACCEPTANCE_OK"!r} else "error",
                "message": "fake controller result",
                "failed_phase": None,
                "phases": [],
                "diagnostics": [],
            }},
        }}

async def controller(config, transport, clock, sleep):
    return Result()

class Transport:
    def __init__(self, **kwargs):
        pass
    async def __aenter__(self):
        return self
    async def __aexit__(self, exc_type, exc, traceback):
        marker = os.environ.get("FAKE_TRANSPORT_CLOSED")
        if marker:
            open(marker, "w", encoding="utf-8").write("closed")

acceptance.run_acceptance = controller
transport = types.ModuleType("prefab_sentinel.unity_acceptance.transport")
transport.AcceptanceTransport = object
transport.McpAcceptanceTransport = Transport
sys.modules["prefab_sentinel.unity_acceptance.transport"] = transport
""",
        encoding="utf-8",
    )
    environment = dict(os.environ)
    environment["PYTHONPATH"] = os.pathsep.join(
        [str(site), str(Path(__file__).parents[1])]
    )
    return environment
