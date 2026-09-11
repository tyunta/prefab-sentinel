"""CLI contract tests for the tool-discovery benchmark command."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import ModuleType

import pytest

from prefab_sentinel.tool_discovery_benchmark.application import (
    CONFIGURATION_CODE,
    SUCCESS_CODE,
    ToolDiscoveryBenchmarkResult,
)


def _load_script_module() -> ModuleType:
    script_path = Path(__file__).parents[1] / "scripts" / "run_tool_discovery_benchmark.py"
    specification = importlib.util.spec_from_file_location(
        "run_tool_discovery_benchmark",
        script_path,
    )
    assert specification is not None
    assert specification.loader is not None
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


def test_cli_prints_only_compact_success_status(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    module = _load_script_module()
    monkeypatch.setattr(
        module,
        "run_tool_discovery_benchmark",
        lambda **_kwargs: ToolDiscoveryBenchmarkResult(
            0,
            SUCCESS_CODE,
            "Tool discovery benchmark completed.",
            {},
        ),
    )

    assert module.main(
        ["--fixture", "fixture.json", "--tools-doc", "tools.md", "--out-report", "report.json"]
    ) == 0
    captured = capsys.readouterr()

    assert captured.out == json.dumps(
        {"code": SUCCESS_CODE, "message": "Tool discovery benchmark completed."}
    ) + "\n"
    assert captured.err == ""


def test_cli_prints_only_compact_failure_status(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    module = _load_script_module()
    monkeypatch.setattr(
        module,
        "run_tool_discovery_benchmark",
        lambda **_kwargs: ToolDiscoveryBenchmarkResult(
            2,
            CONFIGURATION_CODE,
            "Tool discovery benchmark configuration is invalid.",
            None,
        ),
    )

    assert module.main(
        ["--fixture", "fixture.json", "--tools-doc", "tools.md", "--out-report", "report.json"]
    ) == 2
    captured = capsys.readouterr()

    assert captured.out == ""
    assert captured.err == json.dumps(
        {
            "code": CONFIGURATION_CODE,
            "message": "Tool discovery benchmark configuration is invalid.",
        }
    ) + "\n"



def test_cli_invalid_arguments_emit_argparse_usage_to_stderr(
    capsys: pytest.CaptureFixture[str],
) -> None:
    module = _load_script_module()

    with pytest.raises(SystemExit) as raised:
        module.main([])

    captured = capsys.readouterr()
    assert raised.value.code == 2
    assert captured.out == ""
    assert "usage: run_tool_discovery_benchmark" in captured.err
    assert "error: the following arguments are required" in captured.err
