from __future__ import annotations

import json
import subprocess
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from math import isfinite
from pathlib import Path
from sys import float_info
from typing import Any, NoReturn, cast

from prefab_sentinel.benchmarking.environment import (
    EnvironmentFingerprint,
    assert_same_host_fixture,
    capture_environment,
)
from prefab_sentinel.benchmarking.fixture import GeneratedFixture, generate_fixture
from prefab_sentinel.benchmarking.manifest import (
    BenchmarkConfigurationError,
    BenchmarkManifest,
    load_manifest,
)
from prefab_sentinel.benchmarking.model import CaseMeasurement
from prefab_sentinel.benchmarking.report import (
    BenchmarkReportWriteError,
    ReportWriter,
    build_report,
    validate_report_destination,
    write_json_report,
)
from prefab_sentinel.benchmarking.runner import BenchmarkRunner
from prefab_sentinel.parallel_scan import default_worker_count
from prefab_sentinel.session_cache import SessionCacheManager

CONFIGURATION_CODE = "BENCHMARK_CONFIGURATION_INVALID"
CONFIGURATION_MESSAGE = "Performance benchmark configuration is invalid."
BUDGET_CODE = "BENCHMARK_BUDGET_FAILED"
BUDGET_MESSAGE = "One or more enforced performance benchmarks failed."
REPORT_WRITE_CODE = "BENCHMARK_REPORT_WRITE_FAILED"
REPORT_WRITE_MESSAGE = "The performance benchmark report could not be written."
SUCCESS_CODE = "BENCHMARK_COMPLETED"


@dataclass(frozen=True, slots=True)
class BenchmarkApplicationResult:
    exit_code: int
    code: str
    message: str
    report: Mapping[str, Any] | None


def _current_commit(repository_root: Path) -> str:
    completed = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repository_root,
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def _session_factory(project_root: Path):
    cache = SessionCacheManager()
    cache.project_root = project_root
    return cache.get_orchestrator()


def _environment_from_payload(payload: object) -> EnvironmentFingerprint:
    if not isinstance(payload, Mapping):
        raise TypeError("historical baseline environment must be an object")
    string_fields = (
        "commit",
        "operating_system",
        "cpu",
        "python_version",
        "fixture_hash",
    )
    if any(type(payload.get(field)) is not str for field in string_fields):
        raise TypeError("historical baseline environment string fields are invalid")
    worker_count = payload.get("worker_count")
    if type(worker_count) is not int:
        raise TypeError("historical baseline environment worker_count is invalid")
    return EnvironmentFingerprint(
        commit=payload["commit"],
        operating_system=payload["operating_system"],
        cpu=payload["cpu"],
        python_version=payload["python_version"],
        worker_count=worker_count,
        fixture_hash=payload["fixture_hash"],
    )


def _reject_nonfinite_json_constant(value: str) -> NoReturn:
    raise ValueError(f"non-finite JSON number {value} is not allowed")


def _validate_baseline_cases(
    payload: Mapping[str, Any],
    manifest: BenchmarkManifest,
) -> None:
    raw_cases = payload.get("cases")
    if not isinstance(raw_cases, list):
        raise BenchmarkConfigurationError("historical baseline cases are missing")
    expected_ids = tuple(case.case_id for case in manifest.cases)
    observed_ids = tuple(case.get("id") if isinstance(case, dict) else None for case in raw_cases)
    if observed_ids != expected_ids:
        raise BenchmarkConfigurationError("historical baseline must cover the canonical seven cases in order")
    for case in raw_cases:
        case_id = case["id"]
        statistics = case.get("statistics")
        median = statistics.get("median_sec") if isinstance(statistics, dict) else None
        comparable = case.get("current_budget_comparable")
        classification = case.get("invocation_classification")
        historical_equivalent = case_id.startswith("inspect_material_asset_summary_")
        expected_comparable = not historical_equivalent
        expected_classification = "historical-equivalent" if historical_equivalent else "native"
        if (
            not isinstance(median, (int, float))
            or isinstance(median, bool)
            or median < 0
            or median > float_info.max
            or not isfinite(median)
            or comparable is not expected_comparable
            or classification != expected_classification
        ):
            raise BenchmarkConfigurationError(f"historical baseline case {case_id} is invalid")


def _load_baseline(
    path: Path,
    baseline_ref: str,
    current: EnvironmentFingerprint,
    manifest: BenchmarkManifest,
) -> Mapping[str, Any]:
    try:
        payload: object = json.loads(
            path.read_text(encoding="utf-8"),
            parse_constant=_reject_nonfinite_json_constant,
        )
    except (OSError, TypeError, ValueError) as exc:
        raise BenchmarkConfigurationError("historical baseline could not be loaded") from exc
    if not isinstance(payload, dict):
        raise BenchmarkConfigurationError("historical baseline must be a JSON object")
    try:
        environment = _environment_from_payload(payload["environment"])
        substitutions = payload["historical_invocations"]
    except (KeyError, TypeError, ValueError) as exc:
        raise BenchmarkConfigurationError("historical baseline could not be loaded") from exc
    if environment.commit != baseline_ref:
        raise BenchmarkConfigurationError("historical baseline commit does not match --baseline-ref")
    assert_same_host_fixture(environment, current)
    if not isinstance(substitutions, list) or not substitutions:
        raise BenchmarkConfigurationError("historical baseline must disclose legacy invocations")
    _validate_baseline_cases(payload, manifest)
    return payload


def _prepare_benchmark(
    manifest_path: Path,
    repository_root: Path,
    temporary_root: Path,
) -> tuple[BenchmarkManifest, GeneratedFixture, EnvironmentFingerprint]:
    manifest = load_manifest(manifest_path)
    fixture = generate_fixture(temporary_root, manifest)
    environment = capture_environment(
        _current_commit(repository_root),
        default_worker_count(),
        fixture.fixture_hash,
    )
    return manifest, fixture, environment


def _measure_cases(
    manifest: BenchmarkManifest,
    fixture: GeneratedFixture,
) -> tuple[CaseMeasurement, ...]:
    runner = BenchmarkRunner(_session_factory)
    return tuple(runner.measure_case(case, fixture.project_root) for case in manifest.cases)


def _validate_application_preflight(
    out_report: Path,
    baseline_ref: str | None,
    baseline_out: Path | None,
) -> None:
    try:
        validate_report_destination(out_report)
    except BenchmarkReportWriteError as exc:
        raise BenchmarkConfigurationError("benchmark report destination is invalid") from exc
    if (baseline_ref is None) != (baseline_out is None):
        raise BenchmarkConfigurationError("--baseline-ref and --baseline-out must be supplied together")
    if baseline_out is not None:
        try:
            baseline_identity = baseline_out.resolve(strict=True)
            output_identity = out_report.resolve(strict=False)
        except OSError as exc:
            raise BenchmarkConfigurationError("benchmark baseline or output path could not be resolved") from exc
        if baseline_identity == output_identity:
            raise BenchmarkConfigurationError("benchmark output must not overwrite the baseline input")


def run_benchmark_application(
    *,
    manifest_path: Path,
    out_report: Path,
    repository_root: Path,
    enforce: bool,
    baseline_ref: str | None,
    baseline_out: Path | None,
    report_writer: ReportWriter = write_json_report,
) -> BenchmarkApplicationResult:
    try:
        _validate_application_preflight(out_report, baseline_ref, baseline_out)
        with tempfile.TemporaryDirectory(prefix="prefab-sentinel-benchmark-") as temp_dir:
            manifest, fixture, environment = _prepare_benchmark(
                manifest_path,
                repository_root,
                Path(temp_dir),
            )
            baseline = (
                None
                if baseline_out is None
                else _load_baseline(
                    baseline_out,
                    cast(str, baseline_ref),
                    environment,
                    manifest,
                )
            )
            measurements = _measure_cases(manifest, fixture)
            report = build_report(
                manifest,
                fixture,
                environment,
                measurements,
                baseline,
            )
            report_writer(out_report, report)
    except BenchmarkReportWriteError:
        return BenchmarkApplicationResult(
            2,
            REPORT_WRITE_CODE,
            REPORT_WRITE_MESSAGE,
            None,
        )
    except (OSError, subprocess.SubprocessError, ValueError):
        return BenchmarkApplicationResult(
            2,
            CONFIGURATION_CODE,
            CONFIGURATION_MESSAGE,
            None,
        )
    failed = any(item.status == "failed" for item in measurements)
    if enforce and failed:
        return BenchmarkApplicationResult(1, BUDGET_CODE, BUDGET_MESSAGE, report)
    return BenchmarkApplicationResult(
        0,
        SUCCESS_CODE,
        "Performance benchmarks completed.",
        report,
    )
