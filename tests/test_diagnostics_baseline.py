from __future__ import annotations

import json

from prefab_sentinel.diagnostics_baseline import (
    DiagnosticKeyRecord,
    DiagnosticsBaseline,
    classify_current_keys,
    load_diagnostics_baseline,
)
from tests._assertion_helpers import assert_error_envelope


def test_missing_project_root_returns_empty_not_loaded_baseline() -> None:
    result = load_diagnostics_baseline(None)

    assert result.error is None
    assert (result.baseline.status, result.baseline.path, result.baseline.known_diagnostics) == (
        "not_loaded_no_project_root",
        None,
        (),
    )


def test_absent_project_config_returns_empty_absent_baseline(tmp_path) -> None:
    result = load_diagnostics_baseline(tmp_path)

    assert result.error is None
    assert (result.baseline.status, result.baseline.path, result.baseline.known_diagnostics) == (
        "absent",
        str(tmp_path / "config" / "diagnostics_baseline.json"),
        (),
    )


def test_valid_project_config_loads_exact_known_diagnostic_keys(tmp_path) -> None:
    baseline_path = tmp_path / "config" / "diagnostics_baseline.json"
    baseline_path.parent.mkdir()
    baseline_path.write_text(
        json.dumps({"version": 1, "known_diagnostics": ["known/a", "known/b"]}),
        encoding="utf-8",
    )

    result = load_diagnostics_baseline(tmp_path)

    assert result.error is None
    assert (result.baseline.status, result.baseline.path, result.baseline.known_diagnostics) == (
        "loaded",
        str(baseline_path),
        ("known/a", "known/b"),
    )


def test_invalid_json_returns_schema_error_without_loaded_baseline(tmp_path) -> None:
    baseline_path = tmp_path / "config" / "diagnostics_baseline.json"
    baseline_path.parent.mkdir()
    baseline_path.write_text("{", encoding="utf-8")

    result = load_diagnostics_baseline(tmp_path)

    assert result.error is not None
    assert_error_envelope(
        result.error,
        code="DIAGNOSTICS_BASELINE_INVALID",
        message_match="version 1.*known_diagnostics",
        data={"path": str(baseline_path), "read_only": True},
    )
    assert (result.baseline.status, result.baseline.known_diagnostics) == ("invalid", ())


def test_invalid_schema_returns_schema_error_without_loaded_baseline(tmp_path) -> None:
    baseline_path = tmp_path / "config" / "diagnostics_baseline.json"
    baseline_path.parent.mkdir()
    baseline_path.write_text(
        json.dumps({"version": 1, "known_diagnostics": ["known", ""]}),
        encoding="utf-8",
    )

    result = load_diagnostics_baseline(tmp_path)

    assert result.error is not None
    assert_error_envelope(
        result.error,
        code="DIAGNOSTICS_BASELINE_INVALID",
        message_match="non-empty string",
        data={"path": str(baseline_path), "read_only": True},
    )
    assert (result.baseline.status, result.baseline.known_diagnostics) == ("invalid", ())


def test_invalid_version_type_returns_schema_error(tmp_path) -> None:
    baseline_path = tmp_path / "config" / "diagnostics_baseline.json"
    baseline_path.parent.mkdir()

    for version in (True, 1.0):
        baseline_path.write_text(
            json.dumps({"version": version, "known_diagnostics": ["known"]}),
            encoding="utf-8",
        )

        result = load_diagnostics_baseline(tmp_path)

        assert result.error is not None
        assert_error_envelope(
            result.error,
            code="DIAGNOSTICS_BASELINE_INVALID",
            message_match="version 1",
            data={"path": str(baseline_path), "read_only": True},
        )
        assert (result.baseline.status, result.baseline.known_diagnostics) == ("invalid", ())


def test_project_config_status_error_returns_schema_error(tmp_path, monkeypatch) -> None:
    baseline_path = tmp_path / "config" / "diagnostics_baseline.json"
    baseline_path.parent.mkdir()
    baseline_path.write_text(
        json.dumps({"version": 1, "known_diagnostics": ["known"]}),
        encoding="utf-8",
    )
    original_exists = type(baseline_path).exists

    def raise_for_baseline_path(self):
        if self == baseline_path:
            raise OSError("permission denied")
        return original_exists(self)

    monkeypatch.setattr(type(baseline_path), "exists", raise_for_baseline_path)

    result = load_diagnostics_baseline(tmp_path)

    assert result.error is not None
    assert_error_envelope(
        result.error,
        code="DIAGNOSTICS_BASELINE_INVALID",
        message_match="version 1",
        data={"path": str(baseline_path), "read_only": True},
    )
    assert (result.baseline.status, result.baseline.known_diagnostics) == ("invalid", ())


def test_non_file_project_config_returns_schema_error_without_loaded_baseline(tmp_path) -> None:
    baseline_path = tmp_path / "config" / "diagnostics_baseline.json"
    baseline_path.parent.mkdir()
    baseline_path.mkdir()

    result = load_diagnostics_baseline(tmp_path)

    assert result.error is not None
    assert_error_envelope(
        result.error,
        code="DIAGNOSTICS_BASELINE_INVALID",
        message_match="non-empty string",
        data={"path": str(baseline_path), "read_only": True},
    )
    assert (result.baseline.status, result.baseline.known_diagnostics) == ("invalid", ())


def test_non_regular_project_config_returns_schema_error_without_reading(tmp_path, monkeypatch) -> None:
    baseline_path = tmp_path / "config" / "diagnostics_baseline.json"
    baseline_path.parent.mkdir()
    baseline_path.mkdir()

    def fail_if_read(*_args, **_kwargs):
        raise AssertionError("non-regular diagnostics baseline path must not be read")

    monkeypatch.setattr(type(baseline_path), "read_text", fail_if_read)

    result = load_diagnostics_baseline(tmp_path)

    assert result.error is not None
    assert_error_envelope(
        result.error,
        code="DIAGNOSTICS_BASELINE_INVALID",
        message_match="non-empty string",
        data={"path": str(baseline_path), "read_only": True},
    )
    assert (result.baseline.status, result.baseline.known_diagnostics) == ("invalid", ())


def test_classification_preserves_current_order_and_reports_resolved_baseline_keys() -> None:
    baseline = DiagnosticsBaseline(
        known_diagnostics=("known", "resolved"),
        path="/project/config/diagnostics_baseline.json",
        status="loaded",
    )
    current = (
        DiagnosticKeyRecord(
            key="known",
            severity="warning",
            message="known diagnostic",
            data={"field": "knownField"},
        ),
        DiagnosticKeyRecord(
            key="new",
            severity="error",
            message="new diagnostic",
            data={"field": "newField"},
        ),
    )

    classification = classify_current_keys(current, baseline)

    assert classification.to_dict() == {
        "status": "loaded",
        "path": "/project/config/diagnostics_baseline.json",
        "new_count": 1,
        "known_count": 1,
        "resolved_count": 1,
        "new": [
            {
                "key": "new",
                "severity": "error",
                "message": "new diagnostic",
                "data": {"field": "newField"},
            }
        ],
        "known": [
            {
                "key": "known",
                "severity": "warning",
                "message": "known diagnostic",
                "data": {"field": "knownField"},
            }
        ],
        "resolved": [
            {
                "key": "resolved",
                "severity": "warning",
                "message": "",
                "data": {},
            }
        ],
    }
