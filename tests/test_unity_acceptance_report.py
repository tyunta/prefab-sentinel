from __future__ import annotations

import json
from pathlib import Path

import pytest

from prefab_sentinel.contracts import ToolResponse
from prefab_sentinel.unity_acceptance import (
    AcceptancePhaseResult,
    AcceptanceResult,
    publish_acceptance_report,
    reserve_acceptance_report,
)


def test_report_path_is_required(tmp_path: Path) -> None:
    project = tmp_path / "UnityProject"
    project.mkdir()

    response = reserve_acceptance_report(project, None)

    assert isinstance(response, ToolResponse)
    assert (response.success, response.code) == (False, "OUT_REPORT_REQUIRED")


def test_report_must_resolve_inside_project(tmp_path: Path) -> None:
    project = tmp_path / "UnityProject"
    project.mkdir()

    response = reserve_acceptance_report(project, "../acceptance-report.json")

    assert isinstance(response, ToolResponse)
    assert (response.success, response.code) == (
        False,
        "OUT_REPORT_OUTSIDE_PROJECT",
    )


def test_report_must_be_outside_assets(tmp_path: Path) -> None:
    project = tmp_path / "UnityProject"
    (project / "Assets").mkdir(parents=True)

    response = reserve_acceptance_report(
        project,
        "Assets/acceptance-report.json",
    )

    assert isinstance(response, ToolResponse)
    assert (response.success, response.code) == (
        False,
        "OUT_REPORT_INVALID",
    )


def test_preexisting_final_report_path_is_not_replaced(tmp_path: Path) -> None:
    project = tmp_path / "UnityProject"
    report_path = project / "Reports" / "acceptance-report.json"
    report_path.parent.mkdir(parents=True)
    report_path.write_text("existing report", encoding="utf-8")

    response = reserve_acceptance_report(project, "Reports/acceptance-report.json")

    assert isinstance(response, ToolResponse)
    assert (response.success, response.code) == (
        False,
        "OUT_REPORT_WRITE_FAILED",
    )
    assert report_path.read_text(encoding="utf-8") == "existing report"


def test_publish_writes_result_to_reserved_terminal_path(tmp_path: Path) -> None:
    project = tmp_path / "UnityProject"
    report_path = project / "Reports" / "acceptance-report.json"
    report_path.parent.mkdir(parents=True)

    reservation = reserve_acceptance_report(project, "Reports/acceptance-report.json")

    assert not isinstance(reservation, ToolResponse)
    result = AcceptanceResult.failure(
        code="ACCEPTANCE_COMPILE_FAILED",
        message="Unity script compilation failed.",
        failed_phase="compile",
        phases=[],
    )

    publish_acceptance_report(reservation, result)

    assert reservation.final_path == report_path
    assert json.loads(report_path.read_text(encoding="utf-8")) == result.to_dict()
    assert list(report_path.parent.iterdir()) == [report_path]


def test_failed_publication_discards_reserved_report(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = tmp_path / "UnityProject"
    report_path = project / "Reports" / "acceptance-report.json"
    report_path.parent.mkdir(parents=True)
    reservation = reserve_acceptance_report(project, "Reports/acceptance-report.json")

    assert not isinstance(reservation, ToolResponse)

    def raise_write_failure(*_args: object, **_kwargs: object) -> None:
        raise OSError("atomic replacement failed")

    monkeypatch.setattr(
        "prefab_sentinel.unity_acceptance.report.write_report_payload",
        raise_write_failure,
    )

    with pytest.raises(OSError, match="atomic replacement failed"):
        publish_acceptance_report(
            reservation,
            AcceptanceResult.failure(
                code="ACCEPTANCE_COMPILE_FAILED",
                message="Unity script compilation failed.",
                failed_phase="compile",
                phases=[],
            ),
        )

    assert not report_path.exists()


def test_json_serialization_failure_discards_reserved_report(tmp_path: Path) -> None:
    project = tmp_path / "UnityProject"
    report_path = project / "Reports" / "acceptance-report.json"
    report_path.parent.mkdir(parents=True)
    reservation = reserve_acceptance_report(project, "Reports/acceptance-report.json")

    assert not isinstance(reservation, ToolResponse)

    with pytest.raises(TypeError):
        publish_acceptance_report(
            reservation,
            AcceptanceResult.failure(
                code="ACCEPTANCE_COMPILE_FAILED",
                message="Unity script compilation failed.",
                failed_phase="compile",
                phases=[
                    AcceptancePhaseResult(
                        "compile",
                        False,
                        "ACCEPTANCE_COMPILE_FAILED",
                        {"unserializable": object()},
                    )
                ],
            ),
        )

    assert not report_path.exists()
