"""Application-boundary tests for tool-discovery benchmark execution."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest

from prefab_sentinel.benchmarking.report import BenchmarkReportWriteError
from prefab_sentinel.tool_discovery_benchmark import application
from prefab_sentinel.tool_discovery_benchmark.application import (
    CONFIGURATION_CODE,
    REPORT_WRITE_CODE,
    ToolDiscoveryBenchmarkResult,
    run_tool_discovery_benchmark,
)
from prefab_sentinel.tool_discovery_benchmark.models import (
    BenchmarkMeasurements,
    LexicalRanking,
    QueryCase,
    QueryFixture,
    RankedTool,
    ToolRecord,
)
from tests._tool_discovery_test_support import sample_inputs


def test_invalid_fixture_returns_configuration_exit(tmp_path: Path) -> None:
    result = run_tool_discovery_benchmark(
        fixture_path=tmp_path / "missing.json",
        tools_doc_path=Path("docs/tools.md"),
        out_report=tmp_path / "report.json",
        repository_root=Path.cwd(),
    )

    assert (result.exit_code, result.code, result.report) == (
        2,
        CONFIGURATION_CODE,
        None,
    )


def _invalid_destination_result(
    tmp_path: Path,
    out_report: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> ToolDiscoveryBenchmarkResult:
    def unexpected_registry_load(*_args: object) -> object:
        raise AssertionError("invalid report destination reached registry loading")

    monkeypatch.setattr(application.anyio, "run", unexpected_registry_load)
    return run_tool_discovery_benchmark(
        fixture_path=tmp_path / "fixture.json",
        tools_doc_path=tmp_path / "tools.md",
        out_report=out_report,
        repository_root=tmp_path,
    )


def test_missing_report_parent_returns_configuration_exit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = _invalid_destination_result(
        tmp_path,
        tmp_path / "missing" / "report.json",
        monkeypatch,
    )

    assert (result.exit_code, result.code, result.report) == (
        2,
        CONFIGURATION_CODE,
        None,
    )


def test_non_directory_report_parent_returns_configuration_exit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    non_directory = tmp_path / "not-a-directory"
    non_directory.write_text("not a directory", encoding="utf-8")
    result = _invalid_destination_result(
        tmp_path,
        non_directory / "report.json",
        monkeypatch,
    )

    assert (result.exit_code, result.code, result.report) == (
        2,
        CONFIGURATION_CODE,
        None,
    )


def test_symlinked_report_parent_returns_configuration_exit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real_parent = tmp_path / "real"
    real_parent.mkdir()
    alias_parent = tmp_path / "alias"
    alias_parent.symlink_to(real_parent, target_is_directory=True)
    result = _invalid_destination_result(
        tmp_path,
        alias_parent / "report.json",
        monkeypatch,
    )

    assert (result.exit_code, result.code, result.report) == (
        2,
        CONFIGURATION_CODE,
        None,
    )


def test_directory_report_destination_returns_configuration_exit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    destination = tmp_path / "report-directory"
    destination.mkdir()
    result = _invalid_destination_result(tmp_path, destination, monkeypatch)

    assert (result.exit_code, result.code, result.report) == (
        2,
        CONFIGURATION_CODE,
        None,
    )


def test_low_recall_is_success_not_enforcement_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    environment, registry, fixture, measurements = sample_inputs()

    def return_registry(*_args: object) -> tuple[ToolRecord, ...]:
        return registry

    def return_fixture(*_args: object) -> QueryFixture:
        return fixture

    def return_measurements(*_args: object) -> BenchmarkMeasurements:
        return measurements

    def return_environment(*_args: object) -> dict[str, str]:
        return environment

    def discard_report(_path: Path, _payload: Mapping[str, Any]) -> None:
        return None

    monkeypatch.setattr(application.anyio, "run", return_registry)
    monkeypatch.setattr(application, "load_query_fixture", return_fixture)
    monkeypatch.setattr(application, "measure_benchmark", return_measurements)
    monkeypatch.setattr(application, "environment_payload", return_environment)

    result = run_tool_discovery_benchmark(
        fixture_path=tmp_path / "fixture.json",
        tools_doc_path=tmp_path / "tools.md",
        out_report=tmp_path / "report.json",
        repository_root=tmp_path,
        report_writer=discard_report,
    )

    assert (result.exit_code, result.code) == (0, "TOOL_DISCOVERY_BENCHMARK_OK")


def test_report_writer_failure_exposes_only_stable_report_code(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    environment, registry, fixture, measurements = sample_inputs()

    monkeypatch.setattr(application.anyio, "run", lambda *_args: registry)
    monkeypatch.setattr(application, "load_query_fixture", lambda *_args: fixture)
    monkeypatch.setattr(application, "measure_benchmark", lambda *_args: measurements)
    monkeypatch.setattr(application, "environment_payload", lambda *_args: environment)

    def fail_report(_path: Path, _payload: Mapping[str, Any]) -> None:
        raise BenchmarkReportWriteError("performance benchmark report write failed")

    result = run_tool_discovery_benchmark(
        fixture_path=tmp_path / "fixture.json",
        tools_doc_path=tmp_path / "tools.md",
        out_report=tmp_path / "report.json",
        repository_root=tmp_path,
        report_writer=fail_report,
    )

    assert (result.exit_code, result.code, result.message, result.report) == (
        2,
        REPORT_WRITE_CODE,
        "Tool discovery benchmark report could not be written.",
        None,
    )


@pytest.mark.parametrize("score", (float("nan"), float("inf"), float("-inf")))
def test_nonfinite_rankings_return_configuration_exit_before_publication(
    score: float,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    environment, _, _, _ = sample_inputs()
    registry = tuple(
        ToolRecord(
            name=f"tool_{index}",
            description="test",
            category="test",
            wire_definition={"name": f"tool_{index}", "inputSchema": {}},
        )
        for index in range(9)
    )
    case = QueryCase(
        "query-en",
        "query",
        "en",
        "find a tool",
        ("tool_0",),
        (),
        False,
        "test",
    )
    fixture = QueryFixture("fixture.v1", (case,), "a" * 64)
    ranking = LexicalRanking(
        (),
        tuple(
            RankedTool(f"tool_{index}", score if index == 8 else 1.0)
            for index in range(9)
        ),
        False,
    )

    class NonfiniteRanker:
        def __init__(self, _registry: object) -> None:
            return None

        def rank(self, _query: str) -> LexicalRanking:
            return ranking

    monkeypatch.setattr(application.anyio, "run", lambda *_args: registry)
    monkeypatch.setattr(application, "load_query_fixture", lambda *_args: fixture)
    monkeypatch.setattr(application, "DeterministicLexicalRanker", NonfiniteRanker)
    monkeypatch.setattr(application, "environment_payload", lambda *_args: environment)

    def unexpected_report_writer(_path: Path, _payload: Mapping[str, Any]) -> None:
        raise AssertionError("non-finite score reached report publication")

    result = run_tool_discovery_benchmark(
        fixture_path=tmp_path / "fixture.json",
        tools_doc_path=tmp_path / "tools.md",
        out_report=tmp_path / "report.json",
        repository_root=tmp_path,
        report_writer=unexpected_report_writer,
    )

    assert (result.exit_code, result.code, result.report) == (
        2,
        CONFIGURATION_CODE,
        None,
    )
