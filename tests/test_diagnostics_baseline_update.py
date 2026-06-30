from __future__ import annotations

from prefab_sentinel.diagnostics_baseline import (
    DiagnosticsBaseline,
    load_diagnostics_baseline,
)
from prefab_sentinel.diagnostics_baseline_update import (
    compute_diagnostics_baseline_update,
    write_diagnostics_baseline,
)


def _classification(
    *,
    new: tuple[str, ...] = (),
    resolved: tuple[str, ...] = (),
) -> dict[str, object]:
    return {
        "new": [
            {
                "key": key,
                "severity": "warning",
                "message": f"{key} diagnostic",
                "data": {"key": key},
            }
            for key in new
        ],
        "known": [],
        "resolved": [{"key": key} for key in resolved],
    }


def test_preview_absent_baseline_reports_create_without_writing(tmp_path) -> None:
    baseline = load_diagnostics_baseline(tmp_path).baseline

    response = compute_diagnostics_baseline_update(
        baseline=baseline,
        classification=_classification(new=("zeta", "alpha", "alpha")),
    )

    assert response.to_dict() == {
        "success": True,
        "severity": "info",
        "code": "DIAGNOSTICS_BASELINE_UPDATE_PREVIEW",
        "message": "Diagnostics baseline update preview computed.",
        "data": {
            "path": str(tmp_path / "config" / "diagnostics_baseline.json"),
            "mode": "preview",
            "baseline_status": "absent",
            "written": False,
            "would_create": True,
            "known_count_before": 0,
            "known_count_after": 2,
            "added_count": 2,
            "pruned_count": 0,
            "added_sample": ["alpha", "zeta"],
            "pruned_sample": [],
            "known_diagnostics": ["alpha", "zeta"],
        },
        "diagnostics": [],
    }
    assert not (tmp_path / "config").exists()


def test_preview_with_no_new_keys_is_successful_noop() -> None:
    baseline = DiagnosticsBaseline(
        known_diagnostics=("known",),
        path="/project/config/diagnostics_baseline.json",
        status="loaded",
    )

    response = compute_diagnostics_baseline_update(
        baseline=baseline,
        classification=_classification(),
    )

    assert (response.success, response.code, response.data["added_count"]) == (
        True,
        "DIAGNOSTICS_BASELINE_UPDATE_PREVIEW",
        0,
    )
    assert response.data["known_diagnostics"] == ["known"]


def test_prune_resolved_only_removes_resolved_keys_when_requested() -> None:
    baseline = DiagnosticsBaseline(
        known_diagnostics=("keep", "resolved"),
        path="/project/config/diagnostics_baseline.json",
        status="loaded",
    )

    kept = compute_diagnostics_baseline_update(
        baseline=baseline,
        classification=_classification(new=("new",), resolved=("resolved",)),
    )
    pruned = compute_diagnostics_baseline_update(
        baseline=baseline,
        classification=_classification(new=("new",), resolved=("resolved",)),
        prune_resolved=True,
    )

    assert (
        kept.data["known_diagnostics"],
        kept.data["pruned_count"],
        pruned.data["known_diagnostics"],
        pruned.data["pruned_count"],
        pruned.data["pruned_sample"],
    ) == (
        ["keep", "new", "resolved"],
        0,
        ["keep", "new"],
        1,
        ["resolved"],
    )


def test_invalid_mode_returns_specific_error() -> None:
    baseline = DiagnosticsBaseline((), "/project/config/diagnostics_baseline.json", "loaded")

    response = compute_diagnostics_baseline_update(
        baseline=baseline,
        classification=_classification(),
        mode="replace",
    )

    assert response.to_dict() == {
        "success": False,
        "severity": "error",
        "code": "DIAGNOSTICS_BASELINE_MODE_INVALID",
        "message": "diagnostics baseline update mode must be preview or write.",
        "data": {"mode": "replace"},
        "diagnostics": [],
    }


def test_malformed_classification_returns_specific_error() -> None:
    baseline = DiagnosticsBaseline((), "/project/config/diagnostics_baseline.json", "loaded")

    response = compute_diagnostics_baseline_update(
        baseline=baseline,
        classification={"new": [{"key": ""}], "resolved": []},
    )

    assert response.to_dict() == {
        "success": False,
        "severity": "error",
        "code": "DIAGNOSTICS_BASELINE_SOURCE_MISSING_CLASSIFICATION",
        "message": "source response data.diagnostics_baseline is missing or malformed.",
        "data": {"field": "data.diagnostics_baseline"},
        "diagnostics": [],
    }


def test_write_rejects_broken_config_symlink_parent_without_creating_target(tmp_path) -> None:
    config_path = tmp_path / "config"
    missing_target = tmp_path / "missing-config-target"
    baseline_path = config_path / "diagnostics_baseline.json"
    config_path.symlink_to(missing_target, target_is_directory=True)

    error = write_diagnostics_baseline(tmp_path, ("alpha",))

    assert error is not None
    assert error.to_dict()["code"] == "DIAGNOSTICS_BASELINE_INVALID"
    assert error.to_dict()["data"] == {"path": str(baseline_path), "read_only": True}
    assert config_path.is_symlink()
    assert not missing_target.exists()


def test_write_rejects_config_symlink_swap_after_validation(tmp_path, monkeypatch) -> None:
    import shutil

    import prefab_sentinel.diagnostics_baseline as diagnostics_baseline_module

    config_path = tmp_path / "config"
    config_path.mkdir()
    outside_config = tmp_path / "outside-config"
    outside_config.mkdir()
    baseline_path = config_path / "diagnostics_baseline.json"
    outside_baseline = outside_config / "diagnostics_baseline.json"
    original_path_check = diagnostics_baseline_module.diagnostics_baseline_path

    def swap_config_after_validation(project_root):
        result = original_path_check(project_root)
        shutil.rmtree(config_path)
        config_path.symlink_to(outside_config, target_is_directory=True)
        return result

    monkeypatch.setattr(
        diagnostics_baseline_module,
        "diagnostics_baseline_path",
        swap_config_after_validation,
    )

    error = write_diagnostics_baseline(tmp_path, ("alpha",))

    assert error is not None
    assert error.to_dict()["code"] == "DIAGNOSTICS_BASELINE_INVALID"
    assert error.to_dict()["data"] == {"path": str(baseline_path), "read_only": True}
    assert config_path.is_symlink()
    assert not outside_baseline.exists()


def test_write_replace_failure_returns_structured_error(tmp_path, monkeypatch) -> None:
    import os

    config_path = tmp_path / "config"
    config_path.mkdir()
    baseline_path = config_path / "diagnostics_baseline.json"

    def fail_replace(*_args, **_kwargs):
        raise OSError("boom")

    monkeypatch.setattr(os, "replace", fail_replace)

    error = write_diagnostics_baseline(tmp_path, ("alpha",))

    assert error is not None
    assert error.to_dict()["code"] == "DIAGNOSTICS_BASELINE_WRITE_FAILED"
    assert error.to_dict()["severity"] == "error"
    assert error.to_dict()["data"] == {"path": str(baseline_path), "read_only": False}
    assert not baseline_path.exists()
    assert list(config_path.iterdir()) == []

def test_write_rejects_symlinked_baseline_without_following_it(tmp_path) -> None:
    outside = tmp_path / "outside.json"
    outside.write_text("outside", encoding="utf-8")
    baseline_path = tmp_path / "config" / "diagnostics_baseline.json"
    baseline_path.parent.mkdir()
    baseline_path.symlink_to(outside)

    error = write_diagnostics_baseline(tmp_path, ("alpha",))

    assert error is not None
    assert error.to_dict()["code"] == "DIAGNOSTICS_BASELINE_INVALID"
    assert outside.read_text(encoding="utf-8") == "outside"
    assert baseline_path.is_symlink()
