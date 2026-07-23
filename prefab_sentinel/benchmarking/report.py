from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from prefab_sentinel.benchmarking.environment import EnvironmentFingerprint
from prefab_sentinel.benchmarking.fixture import GeneratedFixture
from prefab_sentinel.benchmarking.manifest import BenchmarkManifest
from prefab_sentinel.benchmarking.model import BenchmarkCase, CaseMeasurement


class BenchmarkReportWriteError(OSError):
    pass


def measurement_payload(
    measurement: CaseMeasurement,
    case: BenchmarkCase,
) -> dict[str, Any]:
    statistics = measurement.statistics
    return {
        "id": measurement.case_id,
        "method": case.method,
        "state": case.state,
        "arguments": dict(case.arguments),
        "measured_trials": case.measured_trials,
        "budget_sec": case.budget_sec,
        "status": measurement.status,
        "complete": measurement.complete,
        "samples_sec": list(measurement.samples_sec),
        "statistics": {
            "median_sec": statistics.median_sec,
            "p95_sec": statistics.p95_sec,
            "min_sec": statistics.minimum_sec,
            "max_sec": statistics.maximum_sec,
            "mad_sec": statistics.median_absolute_deviation_sec,
        },
        "response_codes": list(measurement.response_codes),
    }


def _comparison_payload(
    cases: Sequence[Mapping[str, Any]],
    baseline: Mapping[str, Any] | None,
) -> list[dict[str, Any]]:
    if baseline is None:
        return []
    raw_baseline_cases = baseline.get("cases")
    if not isinstance(raw_baseline_cases, list):
        raise ValueError("baseline cases are missing")
    baseline_by_id = {
        item["id"]: item for item in raw_baseline_cases if isinstance(item, dict)
    }
    comparisons: list[dict[str, Any]] = []
    for current in cases:
        case_id = current["id"]
        baseline_case = baseline_by_id[case_id]
        baseline_statistics = baseline_case["statistics"]
        current_statistics = current["statistics"]
        baseline_median = float(baseline_statistics["median_sec"])
        current_median = float(current_statistics["median_sec"])
        comparable = baseline_case["current_budget_comparable"] is True
        delta = current_median - baseline_median if comparable else None
        if delta is None:
            status = "not_comparable"
        elif delta < 0:
            status = "faster"
        elif delta > 0:
            status = "slower"
        else:
            status = "unchanged"
        comparisons.append(
            {
                "case_id": case_id,
                "comparable": comparable,
                "baseline_median_sec": baseline_median,
                "current_median_sec": current_median,
                "delta_sec": delta,
                "status": status,
            }
        )
    return comparisons


def build_report(
    manifest: BenchmarkManifest,
    fixture: GeneratedFixture,
    environment: EnvironmentFingerprint,
    measurements: Sequence[CaseMeasurement],
    baseline: Mapping[str, Any] | None,
) -> dict[str, Any]:
    by_id = {item.case_id: item for item in measurements}
    cases = [measurement_payload(by_id[case.case_id], case) for case in manifest.cases]
    return {
        "schema_version": "inspection-performance-report.v1",
        "environment": environment.to_dict(),
        "fixture": {
            "version": manifest.fixture_version,
            "hash": fixture.fixture_hash,
            "cardinalities": dict(fixture.cardinalities),
        },
        "cases": cases,
        "dependency_mapping": manifest.dependency_mapping,
        "baseline": baseline,
        "comparisons": _comparison_payload(cases, baseline),
    }


def _has_symlink_component(path: Path) -> bool:
    current = path
    while True:
        if current.is_symlink():
            return True
        parent = current.parent
        if parent == current:
            return False
        current = parent


def _absolute_report_path(path: Path) -> Path:
    if path.is_absolute():
        return path

    physical_cwd = Path.cwd()
    pwd_raw = os.environ.get("PWD")
    if pwd_raw is None:
        return physical_cwd / path

    lexical_cwd = Path(pwd_raw)
    try:
        if not lexical_cwd.is_absolute() or not lexical_cwd.samefile(physical_cwd):
            raise BenchmarkReportWriteError(
                "report current working directory could not be verified"
            )
    except OSError as exc:
        raise BenchmarkReportWriteError(
            "report current working directory could not be verified"
        ) from exc
    return lexical_cwd / path


def validate_report_destination(path: Path) -> Path:
    try:
        absolute_path = _absolute_report_path(path)
        if _has_symlink_component(absolute_path.parent):
            raise BenchmarkReportWriteError(
                "report parent must not contain symbolic links"
            )
        parent = absolute_path.parent.resolve(strict=True)
        if not parent.is_dir():
            raise BenchmarkReportWriteError(
                "report parent must be a regular directory"
            )
        validated_path = parent / absolute_path.name
        if validated_path.is_symlink() or (
            validated_path.exists() and validated_path.is_dir()
        ):
            raise BenchmarkReportWriteError(
                "report destination must be a regular file path"
            )
        return validated_path
    except BenchmarkReportWriteError:
        raise
    except (OSError, RuntimeError) as exc:
        raise BenchmarkReportWriteError(
            "report destination status could not be read"
        ) from exc


def write_json_report(path: Path, payload: Mapping[str, Any]) -> None:
    validated_path = validate_report_destination(path)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=validated_path.parent,
            prefix=f".{validated_path.name}.",
            suffix=".tmp",
            delete=False,
        ) as stream:
            temporary_path = Path(stream.name)
            json.dump(payload, stream, ensure_ascii=False, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_path, validated_path)
        directory_fd = os.open(validated_path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    except (OSError, TypeError, ValueError) as exc:
        if temporary_path is not None:
            try:
                temporary_path.unlink(missing_ok=True)
            except OSError as cleanup_error:
                raise BenchmarkReportWriteError(
                    "performance benchmark report cleanup failed"
                ) from cleanup_error
        raise BenchmarkReportWriteError("performance benchmark report write failed") from exc


ReportWriter = Callable[[Path, Mapping[str, Any]], None]
