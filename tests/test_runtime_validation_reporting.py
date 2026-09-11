"""Regression tests for runtime audit report reservation and publication."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest

from prefab_sentinel.contracts import Severity, ToolResponse
from prefab_sentinel.services.runtime_validation.reporting import (
    RuntimeReportReservation,
    discard_runtime_report,
    publish_runtime_report,
    reserve_runtime_report,
    runtime_report_skeleton,
)


def _unity_project(tmp_path: Path) -> Path:
    project = tmp_path / "UnityProject"
    (project / "Assets").mkdir(parents=True)
    return project


def _assert_report_error(
    result: RuntimeReportReservation | ToolResponse,
    code: str,
    message: str,
) -> ToolResponse:
    assert isinstance(result, ToolResponse)
    assert (
        result.success,
        result.severity,
        result.code,
        result.message,
        result.diagnostics[0].location,
        result.diagnostics[0].detail,
    ) == (
        False,
        Severity.ERROR,
        code,
        message,
        "out_report",
        "invalid_field",
    )
    return result


def test_relative_project_local_report_is_reserved(tmp_path: Path) -> None:
    """Catches a reservation wrapper that rejects a valid relative report path."""
    project = _unity_project(tmp_path)
    (project / "Audit").mkdir()

    reservation = reserve_runtime_report(project, "Audit/runtime.json")

    assert isinstance(reservation, RuntimeReportReservation)
    assert (reservation.path, reservation.project_root, reservation.path.read_bytes()) == (
        project / "Audit" / "runtime.json",
        project.resolve(strict=True),
        b"",
    )


def test_absolute_project_local_report_is_reserved(tmp_path: Path) -> None:
    """Catches a reservation wrapper that rejects a valid absolute report path."""
    project = _unity_project(tmp_path)
    report_path = project / "runtime.json"

    reservation = reserve_runtime_report(project, str(report_path))

    assert isinstance(reservation, RuntimeReportReservation)
    assert (reservation.path, reservation.project_root, report_path.read_bytes()) == (
        report_path,
        project.resolve(strict=True),
        b"",
    )


def test_outside_project_traversal_is_rejected_before_reservation(tmp_path: Path) -> None:
    """Catches a wrapper that reserves a report after path traversal."""
    project = _unity_project(tmp_path)

    response = reserve_runtime_report(project, "../outside-runtime.json")

    _assert_report_error(
        response,
        "OUT_REPORT_OUTSIDE_PROJECT",
        "out_report must resolve inside the project root.",
    )
    assert not (tmp_path / "outside-runtime.json").exists()


def test_outside_project_symlink_is_rejected_before_reservation(tmp_path: Path) -> None:
    """Catches a wrapper that accepts a project path whose symlink escapes it."""
    project = _unity_project(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    try:
        os.symlink(outside, project / "linked")
    except OSError as exc:
        pytest.skip(f"symlink unavailable: {exc}")

    response = reserve_runtime_report(project, "linked/runtime.json")

    _assert_report_error(
        response,
        "OUT_REPORT_OUTSIDE_PROJECT",
        "out_report must resolve inside the project root.",
    )
    assert not (outside / "runtime.json").exists()


def test_assets_report_is_rejected_before_reservation(tmp_path: Path) -> None:
    """Catches a runtime report that can be published inside Unity Assets/."""
    project = _unity_project(tmp_path)

    response = reserve_runtime_report(project, "Assets/runtime.json")

    _assert_report_error(
        response,
        "OUT_REPORT_INVALID",
        "out_report must be outside Assets/.",
    )
    assert not (project / "Assets" / "runtime.json").exists()


def test_missing_parent_is_rejected_before_reservation(tmp_path: Path) -> None:
    """Catches a wrapper that creates a missing parent directory."""
    project = _unity_project(tmp_path)

    response = reserve_runtime_report(project, "missing/runtime.json")

    _assert_report_error(
        response,
        "OUT_REPORT_WRITE_FAILED",
        "out_report parent could not be resolved.",
    )
    assert not (project / "missing").exists()


def test_preexisting_final_path_is_rejected_without_replacement(tmp_path: Path) -> None:
    """Catches a wrapper that overwrites a pre-existing final report."""
    project = _unity_project(tmp_path)
    report_path = project / "runtime.json"
    report_path.write_text("occupied", encoding="utf-8")

    response = reserve_runtime_report(project, str(report_path))

    _assert_report_error(
        response,
        "OUT_REPORT_WRITE_FAILED",
        "out_report could not be reserved.",
    )
    assert report_path.read_text(encoding="utf-8") == "occupied"


def test_atomic_probe_failure_cleans_hidden_sibling_and_final_report(
    tmp_path: Path,
) -> None:
    """Catches a failed capability probe that leaves hidden or final report files."""
    project = _unity_project(tmp_path)
    report_path = project / "runtime.json"

    with patch(
        "prefab_sentinel.services.runtime_validation.reporting.os.replace",
        side_effect=OSError("host filesystem detail"),
    ):
        response = reserve_runtime_report(project, "runtime.json")

    response = _assert_report_error(
        response,
        "OUT_REPORT_WRITE_FAILED",
        "out_report parent does not support atomic publication.",
    )
    assert (
        tuple(project.glob(".prefab-sentinel-runtime-report-probe-*")),
        report_path.exists(),
        "host filesystem detail" in response.message,
    ) == ((), False, False)


def test_atomic_probe_cleanup_oserror_blocks_report_reservation(
    tmp_path: Path,
) -> None:
    """Catches cleanup failure being mistaken for writable report capability."""
    project = _unity_project(tmp_path)
    report_path = project / "runtime.json"
    real_replace = os.replace
    published_probe: Path | None = None

    def replace_probe_with_directory(source: str, target: str) -> None:
        nonlocal published_probe
        real_replace(source, target)
        published_probe = Path(target)
        published_probe.unlink()
        published_probe.mkdir()

    try:
        with patch(
            "prefab_sentinel.services.runtime_validation.reporting.os.replace",
            side_effect=replace_probe_with_directory,
        ):
            response = reserve_runtime_report(project, "runtime.json")

        response = _assert_report_error(
            response,
            "OUT_REPORT_WRITE_FAILED",
            "out_report parent does not support atomic publication.",
        )
        assert (
            report_path.exists(),
            "host filesystem detail" in response.message,
        ) == (False, False)
    finally:
        if published_probe is not None and published_probe.is_dir():
            published_probe.rmdir()
        report_path.unlink(missing_ok=True)



def test_atomic_probe_create_failure_returns_sanitized_error(tmp_path: Path) -> None:
    """Catches a capability probe that leaks its filesystem exception on create."""
    project = _unity_project(tmp_path)
    report_path = project / "runtime.json"

    with patch(
        "prefab_sentinel.services.runtime_validation.reporting.tempfile.mkstemp",
        side_effect=OSError("host filesystem detail"),
    ):
        response = reserve_runtime_report(project, "runtime.json")

    response = _assert_report_error(
        response,
        "OUT_REPORT_WRITE_FAILED",
        "out_report parent does not support atomic publication.",
    )
    assert (report_path.exists(), "host filesystem detail" in response.message) == (
        False,
        False,
    )


def test_publish_then_discard_runtime_report_uses_reserved_final_path(
    tmp_path: Path,
) -> None:
    """Catches publication or cleanup that targets a path other than the reservation."""
    project = _unity_project(tmp_path)
    reservation = reserve_runtime_report(project, "runtime.json")
    assert isinstance(reservation, RuntimeReportReservation)

    publish_runtime_report(reservation, {"status": "complete"})

    assert reservation.path.read_text(encoding="utf-8") == (
        "{\n"
        '  "status": "complete"\n'
        "}\n"
    )

    discard_runtime_report(reservation)

    assert (reservation.path.exists(), reservation.project_root) == (
        False,
        project.resolve(strict=True),
    )



def test_failed_publish_discards_reserved_final_report(tmp_path: Path) -> None:
    """Catches failed publication that leaves any reservation or temp sibling."""
    project = _unity_project(tmp_path)
    reservation = reserve_runtime_report(project, "runtime.json")
    assert isinstance(reservation, RuntimeReportReservation)

    with patch(
        "prefab_sentinel.patch_transaction_io.os.replace",
        side_effect=OSError("publish filesystem detail"),
    ):
        with pytest.raises(OSError) as captured:
            publish_runtime_report(reservation, {"status": "complete"})

    artifacts = sorted(
        path.name
        for path in project.iterdir()
        if path.name != "Assets"
    )
    assert (str(captured.value), reservation.path.exists(), artifacts) == (
        "publish filesystem detail",
        False,
        [],
    )


def test_runtime_report_skeleton_has_complete_audited_top_level_shape() -> None:
    """Catches missing audit transaction stages or generated-asset partitions."""
    audit = {"request_id": "audit-167"}

    skeleton = runtime_report_skeleton("compile_only", audit)

    assert skeleton == {
        "schema_version": "runtime_validation_report.v1",
        "profile": "compile_only",
        "audit": audit,
        "preflight": {"completed": False, "diagnostics": []},
        "compile": {
            "executed": False,
            "before": {},
            "after": {},
            "delta": {},
            "generated_assets": {
                "planned_created_paths": [],
                "planned_deleted_paths": [],
                "actual_created_paths": [],
                "actual_deleted_paths": [],
            },
        },
        "clientsim": {
            "executed": False,
            "before": {},
            "runtime": {},
            "after": {},
            "side_effect_report": {},
        },
        "result": {},
    }
