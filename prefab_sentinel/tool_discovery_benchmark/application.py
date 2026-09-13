"""Application boundary and stable exit contract for discovery benchmarking."""

from __future__ import annotations

import importlib.metadata
import platform
import subprocess
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import anyio

from prefab_sentinel.benchmarking.report import (
    BenchmarkReportWriteError,
    validate_report_destination,
)
from prefab_sentinel.tool_discovery_benchmark.fixture import (
    FixtureConfigurationError,
    load_query_fixture,
)
from prefab_sentinel.tool_discovery_benchmark.metrics import measure_benchmark
from prefab_sentinel.tool_discovery_benchmark.models import ToolRecord
from prefab_sentinel.tool_discovery_benchmark.ranking import DeterministicLexicalRanker
from prefab_sentinel.tool_discovery_benchmark.registry import (
    RegistryConfigurationError,
    load_tool_registry,
)
from prefab_sentinel.tool_discovery_benchmark.report import (
    build_report,
    write_json_report,
)

CONFIGURATION_CODE = "TOOL_DISCOVERY_BENCHMARK_CONFIGURATION_INVALID"
CONFIGURATION_MESSAGE = "Tool discovery benchmark configuration is invalid."
REPORT_WRITE_CODE = "TOOL_DISCOVERY_BENCHMARK_REPORT_WRITE_FAILED"
REPORT_WRITE_MESSAGE = "Tool discovery benchmark report could not be written."
SUCCESS_CODE = "TOOL_DISCOVERY_BENCHMARK_OK"
SUCCESS_MESSAGE = "Tool discovery benchmark completed."

ReportWriter = Callable[[Path, Mapping[str, Any]], None]


@dataclass(frozen=True, slots=True)
class ToolDiscoveryBenchmarkResult:
    """The report outcome expressed with stable process-facing status fields."""

    exit_code: int
    code: str
    message: str
    report: Mapping[str, Any] | None


def environment_payload(repository_root: Path) -> dict[str, str]:
    """Collect the report's fixed, path-free execution environment fields."""
    return {
        "commit": subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=repository_root,
            text=True,
        ).strip(),
        "prefab_sentinel_version": importlib.metadata.version("prefab-sentinel"),
        "mcp_sdk_version": importlib.metadata.version("mcp"),
        "python_version": platform.python_version(),
        "platform": platform.system().lower(),
    }


def _validate_report_destination_preflight(out_report: Path) -> None:
    """Classify existing destination-rule rejection as invalid configuration."""
    try:
        validate_report_destination(out_report)
    except BenchmarkReportWriteError as error:
        raise ValueError(
            "tool discovery benchmark report destination is invalid"
        ) from error


def run_tool_discovery_benchmark(
    *,
    fixture_path: Path,
    tools_doc_path: Path,
    out_report: Path,
    repository_root: Path,
    report_writer: ReportWriter = write_json_report,
) -> ToolDiscoveryBenchmarkResult:
    """Run measurement and publication without enforcing a quality threshold."""
    try:
        _validate_report_destination_preflight(out_report)
        registry: Sequence[ToolRecord] = anyio.run(load_tool_registry, tools_doc_path)
        fixture = load_query_fixture(fixture_path, registry)
        ranker = DeterministicLexicalRanker(registry)
        rankings = {
            case.case_id: ranker.rank(case.query) for case in fixture.cases
        }
        measurements = measure_benchmark(registry, fixture, rankings)
        report = build_report(
            environment_payload(repository_root),
            registry,
            fixture,
            measurements,
        )
        report_writer(out_report, report)
    except BenchmarkReportWriteError:
        return ToolDiscoveryBenchmarkResult(
            2,
            REPORT_WRITE_CODE,
            REPORT_WRITE_MESSAGE,
            None,
        )
    except (
        FixtureConfigurationError,
        RegistryConfigurationError,
        importlib.metadata.PackageNotFoundError,
        OSError,
        ValueError,
        subprocess.SubprocessError,
    ):
        return ToolDiscoveryBenchmarkResult(
            2,
            CONFIGURATION_CODE,
            CONFIGURATION_MESSAGE,
            None,
        )
    return ToolDiscoveryBenchmarkResult(
        0,
        SUCCESS_CODE,
        SUCCESS_MESSAGE,
        report,
    )


__all__ = [
    "CONFIGURATION_CODE",
    "CONFIGURATION_MESSAGE",
    "REPORT_WRITE_CODE",
    "REPORT_WRITE_MESSAGE",
    "SUCCESS_CODE",
    "SUCCESS_MESSAGE",
    "ToolDiscoveryBenchmarkResult",
    "environment_payload",
    "run_tool_discovery_benchmark",
]
