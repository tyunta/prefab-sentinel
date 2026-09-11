"""Branch-coverage uplift for ``prefab_sentinel.services.runtime_validation.protocol`` (issue #188).

Pins each runtime-payload failure path and the merged success path.  Every
``isinstance`` rejection branch in ``parse_runtime_response`` is exercised
by a row that names the failing field and asserts the envelope by value.

Branches in the target module not covered: none.  ``protocol_error`` and
``parse_runtime_response`` together comprise the full module surface; the
``_coerce_severity`` helper is exercised via the success-path row and the
severity-rejection row.
"""

from __future__ import annotations

import unittest
from pathlib import Path
from typing import Any, cast

from prefab_sentinel.bridge_constants import PROTOCOL_VERSION
from prefab_sentinel.contracts import Severity
from prefab_sentinel.services.runtime_validation import protocol
from tests._assertion_helpers import assert_error_envelope

_BASE_CONTEXT_KEYS = {
    "action",
    "project_root",
    "scene_path",
    "profile",
    "log_path",
}


def _identity_relative(path: Path) -> str:
    return str(path)


def _parse(payload: object) -> protocol.ToolResponse:
    return protocol.parse_runtime_response(
        payload,
        action="test_action",
        project_root=Path("/project"),
        scene_path="Assets/Scene.unity",
        profile="default",
        log_path=Path("/logs/run.log"),
        relative_fn=_identity_relative,
    )

def _parse_clientsim(payload: object) -> protocol.ToolResponse:
    return protocol.parse_runtime_response(
        payload,
        action="validate_runtime",
        project_root=Path("/project"),
        scene_path="Assets/Scene.unity",
        profile="clientsim",
        log_path=Path("/logs/run.log"),
        relative_fn=_identity_relative,
    )


def _parse_profile(payload: object, profile: str) -> protocol.ToolResponse:
    return protocol.parse_runtime_response(
        payload,
        action="validate_runtime",
        project_root=Path("/project"),
        scene_path="Assets/Scene.unity",
        profile=profile,
        log_path=Path("/logs/run.log"),
        relative_fn=_identity_relative,
    )


def _asset_identity() -> dict[str, object]:
    return {
        "guid": "0123456789abcdef0123456789abcdef",
        "local_file_id": 11400000,
        "path": "Assets/Program.asset",
        "type": "UdonSharp.UdonSharpProgramAsset",
        "dirty": False,
        "attribution_unknown": [],
    }


def _scene_identity() -> dict[str, object]:
    return {
        "path": "Assets/Scene.unity",
        "handle": 1,
        "dirty": False,
        "attribution_unknown": [],
    }


def _compile_snapshot() -> dict[str, object]:
    return {
        "inventory_stable": True,
        "prefab_repair_paths": [],
        "related_assets": [_asset_identity()],
        "loaded_scenes": [_scene_identity()],
        "generated_asset_plan": {
            "planned_created_paths": [],
            "planned_deleted_paths": [],
        },
        "project_dirty_paths": [],
    }


def _compile_delta() -> dict[str, object]:
    return {
        "newly_dirty_paths": [],
        "no_longer_dirty_paths": [],
        "newly_dirty_scene_paths": [],
        "no_longer_dirty_scene_paths": [],
        "planned_created_paths": [],
        "planned_deleted_paths": [],
        "actual_created_paths": [],
        "actual_deleted_paths": [],
        "unrelated_dirty_paths_before": [],
        "unrelated_dirty_paths_after": [],
        "attribution_unknown": [],
    }


def _compile_report() -> dict[str, object]:
    return {
        "executed": True,
        "success": True,
        "severity": "info",
        "code": "RUN_COMPILE_OK",
        "program_count": 1,
        "before": _compile_snapshot(),
        "after": _compile_snapshot(),
        "delta": _compile_delta(),
        "generated_assets": {
            "planned_created_paths": [],
            "planned_deleted_paths": [],
            "actual_created_paths": [],
            "actual_deleted_paths": [],
        },
        "diagnostics": [
            {
                "path": "Assets/Program.asset",
                "location": "validate_runtime.compile",
                "detail": "audited",
                "evidence": "complete",
            }
        ],
    }


def _clientsim_snapshot() -> dict[str, object]:
    return {
        "Roots": ["World"],
        "Hierarchy": ["World"],
        "Components": ["World:UnityEngine.Transform"],
        "AssetChangeCandidates": [],
        "Dirty": False,
        "DirtyCount": 0,
    }


def _side_effect_report() -> dict[str, object]:
    return {
        "diff_complete": True,
        "diff_warnings": [],
        "scene_path": "Assets/Scene.unity",
        "roots_before": ["World"],
        "roots_runtime": ["World"],
        "roots_after": ["World"],
        "hierarchy_before": ["World"],
        "hierarchy_runtime": ["World"],
        "hierarchy_after": ["World"],
        "components_before": ["World:UnityEngine.Transform"],
        "components_runtime": ["World:UnityEngine.Transform"],
        "components_after": ["World:UnityEngine.Transform"],
        "added_gameobjects": [],
        "removed_gameobjects": [],
        "added_components": [],
        "removed_components": [],
        "residual_added_gameobjects": [],
        "residual_removed_gameobjects": [],
        "residual_added_components": [],
        "residual_removed_components": [],
        "dirty_before": False,
        "dirty_runtime": False,
        "dirty_after": False,
        "dirty_count_before": 0,
        "dirty_count_runtime": 0,
        "dirty_count_after": 0,
        "asset_change_candidates": [],
    }


def _unity_default_clientsim_snapshot() -> dict[str, object]:
    return {
        "Roots": [],
        "Hierarchy": [],
        "Components": [],
        "AssetChangeCandidates": [],
        "Dirty": False,
        "DirtyCount": 0,
    }


def _unity_default_side_effect_report() -> dict[str, object]:
    return {
        "diff_complete": True,
        "diff_warnings": [],
        "scene_path": "",
        "roots_before": [],
        "roots_runtime": [],
        "roots_after": [],
        "hierarchy_before": [],
        "hierarchy_runtime": [],
        "hierarchy_after": [],
        "components_before": [],
        "components_runtime": [],
        "components_after": [],
        "added_gameobjects": [],
        "removed_gameobjects": [],
        "added_components": [],
        "removed_components": [],
        "residual_added_gameobjects": [],
        "residual_removed_gameobjects": [],
        "residual_added_components": [],
        "residual_removed_components": [],
        "dirty_before": False,
        "dirty_runtime": False,
        "dirty_after": False,
        "dirty_count_before": 0,
        "dirty_count_runtime": 0,
        "dirty_count_after": 0,
        "asset_change_candidates": [],
    }


def _clientsim_report() -> dict[str, object]:
    return {
        "executed": True,
        "initial_scene_snapshot": [_scene_identity()],
        "before": _clientsim_snapshot(),
        "runtime": _clientsim_snapshot(),
        "after": _clientsim_snapshot(),
        "side_effect_report": _side_effect_report(),
    }


def _valid_clientsim_payload() -> dict[str, object]:
    return {
        "protocol_version": PROTOCOL_VERSION,
        "success": True,
        "severity": "info",
        "code": "RUN_CLIENTSIM_OK",
        "message": "runtime validation ok",
        "data": {
            "project_root": "D:/Project",
            "scene_path": "Assets/Scene.unity",
            "profile": "clientsim",
            "timeout_sec": 120,
            "udon_program_count": 1,
            "clientsim_ready": True,
            "read_only": False,
            "executed": True,
            "side_effect_report": _side_effect_report(),
            "compile": _compile_report(),
            "clientsim": _clientsim_report(),
        },
        "diagnostics": [
            {
                "path": "Assets/Program.asset",
                "location": "validate_runtime.compile",
                "detail": "audited",
                "evidence": "complete",
            }
        ],
    }


def _value_at_path(payload: object, path: tuple[str | int, ...]) -> object:
    current = payload
    for part in path:
        current = current[part]  # type: ignore[index]
    return current


def _replace_at_path(
    payload: object,
    path: tuple[str | int, ...],
    replacement: object,
) -> None:
    parent = _value_at_path(payload, path[:-1])
    parent[path[-1]] = replacement  # type: ignore[index]


def _remove_at_path(payload: object, path: tuple[str | int, ...]) -> None:
    parent = cast(Any, _value_at_path(payload, path[:-1]))
    del parent[path[-1]]  # type: ignore[index]


def _wrong_type(value: object) -> object:
    if type(value) is bool:
        return 1
    if type(value) is int:
        return True
    if isinstance(value, str):
        return 1
    if isinstance(value, list):
        return "not-an-array"
    if isinstance(value, dict):
        return []
    raise AssertionError(f"No wrong-type fixture for {type(value).__name__}")


class RuntimeProtocolFailureTests(unittest.TestCase):
    """Malformed common envelopes map to the canonical protocol error."""

    def test_per_field_rejection_returns_protocol_error_envelope(self) -> None:
        rows = (
            ("non_object_root", None, "not an object", r"response envelope is invalid"),
            ("missing_success", ("success",), None, r"response envelope is invalid"),
            ("invalid_severity", ("severity",), "magenta", r"response envelope is invalid"),
            ("non_string_message", ("message",), 123, r"response envelope is invalid"),
            ("non_object_data", ("data",), "not-an-object", r"response envelope is invalid"),
            ("non_array_diagnostics", ("diagnostics",), "not-an-array", r"response envelope is invalid"),
            ("successful_error", ("severity",), "error", r"response envelope is invalid"),
            ("successful_critical", ("severity",), "critical", r"response envelope is invalid"),
            (
                "non_object_diagnostic_entry",
                ("diagnostics", 0),
                "not-an-object",
                r"diagnostics\[0\].*object",
            ),
        )
        for label, path, value, message_regex in rows:
            with self.subTest(label=label):
                if path is None:
                    payload: object = value
                else:
                    payload = _valid_clientsim_payload()
                    if value is None:
                        _remove_at_path(payload, path)
                    else:
                        _replace_at_path(payload, path, value)
                response = _parse_clientsim(payload)
                assert_error_envelope(
                    response,
                    code="RUN_PROTOCOL_ERROR",
                    severity="error",
                    message_match=message_regex,
                )

    def test_clientsim_requires_boolean_executed_field(self) -> None:
        for label, value in (
            ("missing", None),
            ("null", None),
            ("integer", 1),
            ("string", "true"),
        ):
            with self.subTest(label=label):
                payload = _valid_clientsim_payload()
                path = ("data", "executed")
                if label == "missing":
                    _remove_at_path(payload, path)
                else:
                    _replace_at_path(payload, path, value)
                response = _parse_clientsim(payload)
                assert_error_envelope(
                    response,
                    code="RUN_PROTOCOL_ERROR",
                    severity="error",
                    message_match=r"field 'data.executed'.*boolean|exactly fields",
                )

    def test_soft_negative_warning_preserves_response_fields(self) -> None:
        payload = _valid_clientsim_payload()
        payload.update(
            {
                "success": False,
                "severity": "warning",
                "code": "RUN_DEFERRED",
                "message": "waiting for Unity",
            }
        )
        response = _parse_clientsim(payload)
        self.assertEqual(False, response.success)
        self.assertEqual(Severity.WARNING, response.severity)
        self.assertEqual("RUN_DEFERRED", response.code)
        self.assertEqual("waiting for Unity", response.message)
        self.assertEqual(1, response.data["udon_program_count"])

    def test_failure_envelope_merges_base_context_and_stamps_flags(self) -> None:
        response = _parse("not an object")
        for key in _BASE_CONTEXT_KEYS:
            self.assertIn(key, response.data)
        self.assertEqual(True, response.data["read_only"])
        self.assertEqual(False, response.data["executed"])


class RuntimeProtocolStrictSchemaTests(unittest.TestCase):
    """Every Bridge evidence field is required and type-checked before use."""

    _TOP_LEVEL_PATHS = (
        ("protocol_version",),
        ("success",),
        ("severity",),
        ("code",),
        ("message",),
        ("data",),
        ("diagnostics",),
    )
    _DATA_PATHS = tuple(
        ("data", field)
        for field in (
            "project_root",
            "scene_path",
            "profile",
            "timeout_sec",
            "udon_program_count",
            "clientsim_ready",
            "read_only",
            "executed",
            "side_effect_report",
            "compile",
            "clientsim",
        )
    )
    _COMPILE_PATHS = tuple(
        ("data", "compile", field)
        for field in (
            "executed",
            "success",
            "severity",
            "code",
            "program_count",
            "before",
            "after",
            "delta",
            "generated_assets",
            "diagnostics",
        )
    )
    _SNAPSHOT_PATHS = tuple(
        ("data", "compile", phase, field)
        for phase in ("before", "after")
        for field in (
            "inventory_stable",
            "prefab_repair_paths",
            "related_assets",
            "loaded_scenes",
            "generated_asset_plan",
            "project_dirty_paths",
        )
    )
    _ASSET_IDENTITY_FIELD_PATHS = tuple(
        ("data", "compile", phase, "related_assets", 0, field)
        for phase in ("before", "after")
        for field in (
            "guid",
            "local_file_id",
            "path",
            "type",
            "dirty",
            "attribution_unknown",
        )
    )
    _SCENE_IDENTITY_FIELD_PATHS = tuple(
        ("data", "compile", phase, "loaded_scenes", 0, field)
        for phase in ("before", "after")
        for field in ("path", "handle", "dirty", "attribution_unknown")
    )
    _GENERATED_PLAN_PATHS = tuple(
        ("data", "compile", phase, "generated_asset_plan", field)
        for phase in ("before", "after")
        for field in ("planned_created_paths", "planned_deleted_paths")
    )
    _DELTA_PATHS = tuple(
        ("data", "compile", "delta", field)
        for field in (
            "newly_dirty_paths",
            "no_longer_dirty_paths",
            "newly_dirty_scene_paths",
            "no_longer_dirty_scene_paths",
            "planned_created_paths",
            "planned_deleted_paths",
            "actual_created_paths",
            "actual_deleted_paths",
            "unrelated_dirty_paths_before",
            "unrelated_dirty_paths_after",
            "attribution_unknown",
        )
    )
    _GENERATED_RESULT_PATHS = tuple(
        ("data", "compile", "generated_assets", field)
        for field in (
            "planned_created_paths",
            "planned_deleted_paths",
            "actual_created_paths",
            "actual_deleted_paths",
        )
    )
    _COMPILE_DIAGNOSTIC_PATHS = tuple(
        ("data", "compile", "diagnostics", 0, field)
        for field in ("path", "location", "detail", "evidence")
    )
    _CLIENTSIM_PATHS = tuple(
        ("data", "clientsim", field)
        for field in (
            "executed",
            "initial_scene_snapshot",
            "before",
            "runtime",
            "after",
            "side_effect_report",
        )
    )
    _INITIAL_SCENE_FIELD_PATHS = tuple(
        ("data", "clientsim", "initial_scene_snapshot", 0, field)
        for field in ("path", "handle", "dirty", "attribution_unknown")
    )
    _CLIENTSIM_SNAPSHOT_PATHS = tuple(
        ("data", "clientsim", phase, field)
        for phase in ("before", "runtime", "after")
        for field in (
            "Roots",
            "Hierarchy",
            "Components",
            "AssetChangeCandidates",
            "Dirty",
            "DirtyCount",
        )
    )
    _SIDE_EFFECT_FIELDS = (
        "diff_complete",
        "diff_warnings",
        "scene_path",
        "roots_before",
        "roots_runtime",
        "roots_after",
        "hierarchy_before",
        "hierarchy_runtime",
        "hierarchy_after",
        "components_before",
        "components_runtime",
        "components_after",
        "added_gameobjects",
        "removed_gameobjects",
        "added_components",
        "removed_components",
        "residual_added_gameobjects",
        "residual_removed_gameobjects",
        "residual_added_components",
        "residual_removed_components",
        "dirty_before",
        "dirty_runtime",
        "dirty_after",
        "dirty_count_before",
        "dirty_count_runtime",
        "dirty_count_after",
        "asset_change_candidates",
    )
    _SIDE_EFFECT_PATHS = tuple(
        (*prefix, field)
        for prefix in (
            ("data", "side_effect_report"),
            ("data", "clientsim", "side_effect_report"),
        )
        for field in _side_effect_report()
    )
    _OUTER_DIAGNOSTIC_PATHS = tuple(
        ("diagnostics", 0, field)
        for field in ("path", "location", "detail", "evidence")
    )
    _REQUIRED_FIELD_PATHS = (
        _TOP_LEVEL_PATHS
        + _DATA_PATHS
        + _COMPILE_PATHS
        + _SNAPSHOT_PATHS
        + _ASSET_IDENTITY_FIELD_PATHS
        + _SCENE_IDENTITY_FIELD_PATHS
        + _GENERATED_PLAN_PATHS
        + _DELTA_PATHS
        + _GENERATED_RESULT_PATHS
        + _COMPILE_DIAGNOSTIC_PATHS
        + _CLIENTSIM_PATHS
        + _INITIAL_SCENE_FIELD_PATHS
        + _CLIENTSIM_SNAPSHOT_PATHS
        + _SIDE_EFFECT_PATHS
        + _OUTER_DIAGNOSTIC_PATHS
    )
    _IDENTITY_OBJECT_PATHS = (
        ("data", "compile", "before", "related_assets", 0),
        ("data", "compile", "after", "related_assets", 0),
        ("data", "compile", "before", "loaded_scenes", 0),
        ("data", "compile", "after", "loaded_scenes", 0),
        ("data", "clientsim", "initial_scene_snapshot", 0),
    )

    def test_every_required_field_rejects_missing_and_wrong_type(self) -> None:
        for path in self._REQUIRED_FIELD_PATHS:
            with self.subTest(path=path, mutation="missing"):
                payload = _valid_clientsim_payload()
                _remove_at_path(payload, path)
                response = _parse_clientsim(payload)
                assert_error_envelope(
                    response,
                    code="RUN_PROTOCOL_ERROR",
                    severity="error",
                )

            with self.subTest(path=path, mutation="wrong_type"):
                payload = _valid_clientsim_payload()
                current = _value_at_path(payload, path)
                _replace_at_path(payload, path, _wrong_type(current))
                response = _parse_clientsim(payload)
                assert_error_envelope(
                    response,
                    code="RUN_PROTOCOL_ERROR",
                    severity="error",
                )

    def test_identity_array_entries_must_be_objects(self) -> None:
        for path in self._IDENTITY_OBJECT_PATHS:
            with self.subTest(path=path):
                payload = _valid_clientsim_payload()
                _replace_at_path(payload, path, "not-an-identity")
                response = _parse_clientsim(payload)
                assert_error_envelope(
                    response,
                    code="RUN_PROTOCOL_ERROR",
                    severity="error",
                )

    def test_integer_fields_reject_boolean_values(self) -> None:
        integer_paths = (
            ("protocol_version",),
            ("data", "timeout_sec"),
            ("data", "udon_program_count"),
            ("data", "compile", "program_count"),
            ("data", "compile", "before", "related_assets", 0, "local_file_id"),
            ("data", "compile", "before", "loaded_scenes", 0, "handle"),
            ("data", "clientsim", "initial_scene_snapshot", 0, "handle"),
            ("data", "clientsim", "before", "DirtyCount"),
            ("data", "side_effect_report", "dirty_count_before"),
        )
        for path in integer_paths:
            with self.subTest(path=path):
                payload = _valid_clientsim_payload()
                _replace_at_path(payload, path, True)
                response = _parse_clientsim(payload)
                assert_error_envelope(
                    response,
                    code="RUN_PROTOCOL_ERROR",
                    severity="error",
                )

    def test_unknown_compile_result_severity_is_rejected(self) -> None:
        payload = _valid_clientsim_payload()
        _replace_at_path(payload, ("data", "compile", "severity"), "magenta")

        response = _parse_clientsim(payload)

        assert_error_envelope(
            response,
            code="RUN_PROTOCOL_ERROR",
            severity="error",
        )


    def test_compile_warning_cannot_be_hidden_by_outer_result(self) -> None:
        payload = _valid_clientsim_payload()
        _replace_at_path(payload, ("data", "compile", "severity"), "warning")
        payload["severity"] = "info"

        response = _parse_clientsim(payload)

        assert_error_envelope(
            response,
            code="RUN_PROTOCOL_ERROR",
            severity="error",
        )

    def test_compile_failure_cannot_be_reported_as_outer_success(self) -> None:
        payload = _valid_clientsim_payload()
        _replace_at_path(payload, ("data", "compile", "success"), False)
        _replace_at_path(payload, ("data", "compile", "severity"), "error")
        _replace_at_path(payload, ("data", "compile", "code"), "RUN_COMPILE_FAILED")

        response = _parse_clientsim(payload)

        assert_error_envelope(
            response,
            code="RUN_PROTOCOL_ERROR",
            severity="error",
        )


    def test_contradictory_execution_lanes_are_rejected(self) -> None:
        rows = (
            (
                "compile_unexecuted_success",
                "clientsim",
                (
                    (("data", "compile", "executed"), False),
                ),
            ),
            (
                "compile_only_unexecuted_compile_success",
                "compile_only",
                (
                    (("data", "profile"), "compile_only"),
                    (("data", "executed"), False),
                    (("data", "side_effect_report"), None),
                    (("data", "compile", "executed"), False),
                    (("data", "clientsim", "executed"), False),
                    (("data", "clientsim", "before"), None),
                    (("data", "clientsim", "runtime"), None),
                    (("data", "clientsim", "after"), None),
                    (("data", "clientsim", "side_effect_report"), None),
                ),
            ),
            (
                "compile_only_clientsim_executed",
                "compile_only",
                (
                    (("data", "profile"), "compile_only"),
                ),
            ),
            (
                "clientsim_without_executed_compile",
                "clientsim",
                (
                    (("success",), False),
                    (("severity",), "error"),
                    (("code",), "RUN_COMPILE_NOT_EXECUTED"),
                    (("data", "compile", "executed"), False),
                    (("data", "compile", "success"), False),
                    (("data", "compile", "severity"), "info"),
                    (("data", "compile", "code"), ""),
                ),
            ),
            (
                "clientsim_after_compile_failure",
                "clientsim",
                (
                    (("success",), False),
                    (("severity",), "error"),
                    (("code",), "RUN_COMPILE_FAILED"),
                    (("data", "compile", "success"), False),
                    (("data", "compile", "severity"), "error"),
                    (("data", "compile", "code"), "RUN_COMPILE_FAILED"),
                ),
            ),
            (
                "unexecuted_clientsim_before_snapshot",
                "clientsim",
                (
                    (("success",), False),
                    (("severity",), "error"),
                    (("code",), "CLIENTSIM_DIRTY_SCENE"),
                    (("data", "executed"), False),
                    (("data", "side_effect_report"), None),
                    (("data", "clientsim", "executed"), False),
                    (("data", "clientsim", "runtime"), None),
                    (("data", "clientsim", "after"), None),
                    (("data", "clientsim", "side_effect_report"), None),
                ),
            ),
            (
                "unexecuted_clientsim_runtime_snapshot",
                "clientsim",
                (
                    (("success",), False),
                    (("severity",), "error"),
                    (("code",), "CLIENTSIM_DIRTY_SCENE"),
                    (("data", "executed"), False),
                    (("data", "side_effect_report"), None),
                    (("data", "clientsim", "executed"), False),
                    (("data", "clientsim", "before"), None),
                    (("data", "clientsim", "after"), None),
                    (("data", "clientsim", "side_effect_report"), None),
                ),
            ),
            (
                "unexecuted_clientsim_after_snapshot",
                "clientsim",
                (
                    (("success",), False),
                    (("severity",), "error"),
                    (("code",), "CLIENTSIM_DIRTY_SCENE"),
                    (("data", "executed"), False),
                    (("data", "side_effect_report"), None),
                    (("data", "clientsim", "executed"), False),
                    (("data", "clientsim", "before"), None),
                    (("data", "clientsim", "runtime"), None),
                    (("data", "clientsim", "side_effect_report"), None),
                ),
            ),
            (
                "unexecuted_clientsim_side_effect_report",
                "clientsim",
                (
                    (("success",), False),
                    (("severity",), "error"),
                    (("code",), "CLIENTSIM_DIRTY_SCENE"),
                    (("data", "executed"), False),
                    (("data", "clientsim", "executed"), False),
                    (("data", "clientsim", "before"), None),
                    (("data", "clientsim", "runtime"), None),
                    (("data", "clientsim", "after"), None),
                ),
            ),
        )
        for label, profile, replacements in rows:
            with self.subTest(label=label):
                payload = _valid_clientsim_payload()
                for path, value in replacements:
                    _replace_at_path(payload, path, value)

                response = _parse_profile(payload, profile)

                assert_error_envelope(
                    response,
                    code="RUN_PROTOCOL_ERROR",
                    severity="error",
                )


    def test_explicit_unexecuted_lanes_remain_valid_evidence(self) -> None:
        for label, compile_executed, compile_code in (
            ("preflight_rejection", False, ""),
            ("compile_failure", True, "RUN_COMPILE_FAILED"),
        ):
            with self.subTest(label=label):
                payload = _valid_clientsim_payload()
                payload.update(
                    {
                        "success": False,
                        "severity": "error",
                        "code": (
                            "CLIENTSIM_DIRTY_SCENE"
                            if not compile_executed
                            else "RUN_COMPILE_FAILED"
                        ),
                    }
                )
                compile_report = payload["data"]["compile"]  # type: ignore[index]
                compile_report.update(  # type: ignore[union-attr]
                    {
                        "executed": compile_executed,
                        "success": False,
                        "severity": "error" if compile_executed else "info",
                        "code": compile_code,
                        "program_count": 1 if compile_executed else 0,
                    }
                )
                clientsim_report = payload["data"]["clientsim"]  # type: ignore[index]
                clientsim_report.update(  # type: ignore[union-attr]
                    {
                        "executed": False,
                        "before": None,
                        "runtime": None,
                        "after": None,
                        "side_effect_report": None,
                    }
                )
                data = cast(dict[str, object], payload["data"])  # type: ignore[index]
                data.update(
                    {
                        "udon_program_count": 1 if compile_executed else 0,
                        "clientsim_ready": False,
                        "read_only": not compile_executed,
                        "executed": False,
                        "side_effect_report": None,
                    }
                )

                response = _parse_clientsim(payload)

                self.assertNotEqual("RUN_PROTOCOL_ERROR", response.code)
                self.assertEqual(
                    (compile_executed, False),
                    (
                        response.data["compile"]["executed"],
                        response.data["clientsim"]["executed"],
                    ),
                )

    def test_executed_compile_failure_requires_diagnostic_evidence(self) -> None:
        payload = _valid_clientsim_payload()
        payload.update(
            {
                "success": False,
                "severity": "error",
                "code": "RUN_COMPILE_FAILED",
            }
        )
        compile_report = payload["data"]["compile"]  # type: ignore[index]
        compile_report.update(  # type: ignore[union-attr]
            {
                "success": False,
                "severity": "error",
                "code": "RUN_COMPILE_FAILED",
                "diagnostics": [],
            }
        )
        clientsim_report = payload["data"]["clientsim"]  # type: ignore[index]
        clientsim_report.update(  # type: ignore[union-attr]
            {
                "executed": False,
                "before": None,
                "runtime": None,
                "after": None,
                "side_effect_report": None,
            }
        )
        data = cast(dict[str, object], payload["data"])  # type: ignore[index]
        data.update(
            {
                "clientsim_ready": False,
                "executed": False,
                "side_effect_report": None,
            }
        )

        response = _parse_clientsim(payload)

        assert_error_envelope(
            response,
            code="RUN_PROTOCOL_ERROR",
            severity="error",
        )


    def test_unexecuted_clientsim_unity_defaults_are_canonicalized_to_null(
        self,
    ) -> None:
        payload = _valid_clientsim_payload()
        payload.update(
            {
                "success": False,
                "severity": "error",
                "code": "UDON_COMPILE_DIRTY_PRECONDITION",
            }
        )
        compile_report = payload["data"]["compile"]  # type: ignore[index]
        compile_report.update(  # type: ignore[union-attr]
            {
                "executed": False,
                "success": False,
                "severity": "error",
                "code": "UDON_COMPILE_DIRTY_PRECONDITION",
                "program_count": 8,
            }
        )
        clientsim_report = payload["data"]["clientsim"]  # type: ignore[index]
        clientsim_report.update(  # type: ignore[union-attr]
            {
                "executed": False,
                "initial_scene_snapshot": [],
                "before": _unity_default_clientsim_snapshot(),
                "runtime": _unity_default_clientsim_snapshot(),
                "after": _unity_default_clientsim_snapshot(),
                "side_effect_report": (
                    _unity_default_side_effect_report()
                ),
            }
        )
        data = cast(dict[str, object], payload["data"])  # type: ignore[index]
        data.update(
            {
                "profile": "compile_only",
                "udon_program_count": 8,
                "clientsim_ready": False,
                "read_only": True,
                "executed": False,
                "side_effect_report": (
                    _unity_default_side_effect_report()
                ),
            }
        )

        response = _parse_profile(payload, "compile_only")

        self.assertEqual(
            "UDON_COMPILE_DIRTY_PRECONDITION",
            response.code,
        )
        self.assertIsNone(response.data["side_effect_report"])
        self.assertEqual(
            {
                "before": None,
                "runtime": None,
                "after": None,
                "side_effect_report": None,
            },
            {
                field: response.data["clientsim"][field]
                for field in (
                    "before",
                    "runtime",
                    "after",
                    "side_effect_report",
                )
            },
        )


    def test_successful_compile_requires_generated_plan_to_match_actual_delta(
        self,
    ) -> None:
        for label, planned_field, actual_field, planned, actual in (
            (
                "missing_create",
                "planned_created_paths",
                "actual_created_paths",
                ["Assets/SerializedUdonPrograms/A.asset"],
                [],
            ),
            (
                "unexpected_create",
                "planned_created_paths",
                "actual_created_paths",
                [],
                ["Assets/SerializedUdonPrograms/A.asset"],
            ),
            (
                "missing_delete",
                "planned_deleted_paths",
                "actual_deleted_paths",
                ["Assets/SerializedUdonPrograms/A.asset"],
                [],
            ),
            (
                "unexpected_delete",
                "planned_deleted_paths",
                "actual_deleted_paths",
                [],
                ["Assets/SerializedUdonPrograms/A.asset"],
            ),
        ):
            with self.subTest(label=label):
                payload = _valid_clientsim_payload()
                payload.update(
                    {
                        "severity": "warning",
                        "code": "RUN_COMPILE_OK",
                    }
                )
                data = cast(dict[str, object], payload["data"])  # type: ignore[index]
                data.update(
                    {
                        "profile": "compile_only",
                        "executed": True,
                        "side_effect_report": None,
                    }
                )
                clientsim_report = cast(dict[str, object], data["clientsim"])
                clientsim_report.update(
                    {
                        "executed": False,
                        "before": None,
                        "runtime": None,
                        "after": None,
                        "side_effect_report": None,
                    }
                )
                compile_report = cast(dict[str, object], data["compile"])
                compile_report.update(
                    {
                        "severity": "warning",
                        "code": "RUN_COMPILE_OK",
                    }
                )
                compile_report["delta"][planned_field] = planned  # type: ignore[index]
                compile_report["delta"][actual_field] = actual  # type: ignore[index]
                compile_report["generated_assets"][planned_field] = planned  # type: ignore[index]

                response = _parse_profile(payload, "compile_only")

                assert_error_envelope(
                    response,
                    code="RUN_PROTOCOL_ERROR",
                    severity="error",
                )

    def test_generated_asset_plan_must_match_compile_delta_plan(self) -> None:
        for label, planned_field in (
            ("created", "planned_created_paths"),
            ("deleted", "planned_deleted_paths"),
        ):
            with self.subTest(label=label):
                payload = _valid_clientsim_payload()
                payload.update(
                    {
                        "severity": "warning",
                        "code": "RUN_COMPILE_OK",
                    }
                )
                data = cast(dict[str, object], payload["data"])  # type: ignore[index]
                data.update(
                    {
                        "profile": "compile_only",
                        "executed": True,
                        "side_effect_report": None,
                    }
                )
                clientsim_report = cast(dict[str, object], data["clientsim"])
                clientsim_report.update(
                    {
                        "executed": False,
                        "before": None,
                        "runtime": None,
                        "after": None,
                        "side_effect_report": None,
                    }
                )
                compile_report = cast(dict[str, object], data["compile"])
                compile_report.update(
                    {
                        "severity": "warning",
                        "code": "RUN_COMPILE_OK",
                    }
                )
                compile_report["generated_assets"][planned_field] = [  # type: ignore[index]
                    "Assets/SerializedUdonPrograms/Planned.asset"
                ]

                response = _parse_profile(payload, "compile_only")

                assert_error_envelope(
                    response,
                    code="RUN_PROTOCOL_ERROR",
                    severity="error",
                )

    def test_nonempty_generated_plan_matching_compile_delta_is_valid(self) -> None:
        created = "Assets/SerializedUdonPrograms/Created.asset"
        deleted = "Assets/SerializedUdonPrograms/Deleted.asset"
        payload = _valid_clientsim_payload()
        payload.update(
            {
                "severity": "warning",
                "code": "RUN_COMPILE_OK",
            }
        )
        data = cast(dict[str, object], payload["data"])  # type: ignore[index]
        data.update(
            {
                "profile": "compile_only",
                "executed": True,
                "side_effect_report": None,
            }
        )
        clientsim_report = cast(dict[str, object], data["clientsim"])
        clientsim_report.update(
            {
                "executed": False,
                "before": None,
                "runtime": None,
                "after": None,
                "side_effect_report": None,
            }
        )
        compile_report = cast(dict[str, object], data["compile"])
        compile_report.update(
            {
                "severity": "warning",
                "code": "RUN_COMPILE_OK",
            }
        )
        generated_assets = cast(dict[str, object], compile_report["generated_assets"])
        generated_assets.update(
            {
                "planned_created_paths": [created],
                "planned_deleted_paths": [deleted],
                "actual_created_paths": [created],
                "actual_deleted_paths": [deleted],
            }
        )
        delta = cast(dict[str, object], compile_report["delta"])
        delta.update(
            {
                "planned_created_paths": [created],
                "planned_deleted_paths": [deleted],
                "actual_created_paths": [created],
                "actual_deleted_paths": [deleted],
            }
        )

        response = _parse_profile(payload, "compile_only")

        self.assertEqual(
            (
                True,
                "RUN_COMPILE_OK",
                [created],
                [deleted],
                [created],
                [deleted],
            ),
            (
                response.success,
                response.code,
                response.data["compile"]["generated_assets"][  # type: ignore[index]
                    "planned_created_paths"
                ],
                response.data["compile"]["generated_assets"][  # type: ignore[index]
                    "planned_deleted_paths"
                ],
                response.data["compile"]["generated_assets"][  # type: ignore[index]
                    "actual_created_paths"
                ],
                response.data["compile"]["generated_assets"][  # type: ignore[index]
                    "actual_deleted_paths"
                ],
            ),
        )

    def test_generated_plan_mismatch_protocol_error_retains_compile_evidence(
        self,
    ) -> None:
        canonical = "Assets/SerializedUdonPrograms/Program.asset"
        old_generated = "Assets/Acceptance/ProgramOld.asset"
        payload = _valid_clientsim_payload()
        payload.update(
            {
                "severity": "warning",
                "code": "RUN_COMPILE_OK",
            }
        )
        data = cast(dict[str, object], payload["data"])  # type: ignore[index]
        data.update(
            {
                "profile": "compile_only",
                "executed": True,
                "side_effect_report": None,
            }
        )
        clientsim_report = cast(dict[str, object], data["clientsim"])
        clientsim_report.update(
            {
                "executed": False,
                "before": None,
                "runtime": None,
                "after": None,
                "side_effect_report": None,
            }
        )
        compile_report = cast(dict[str, object], data["compile"])
        compile_report.update(
            {
                "severity": "warning",
                "code": "RUN_COMPILE_OK",
            }
        )
        delta = cast(dict[str, object], compile_report["delta"])
        delta.update(
            {
                "planned_created_paths": [canonical],
                "planned_deleted_paths": [old_generated],
                "actual_created_paths": [],
                "actual_deleted_paths": [],
            }
        )
        generated_assets = cast(dict[str, object], compile_report["generated_assets"])
        generated_assets.update(
            {
                "planned_created_paths": [canonical],
                "planned_deleted_paths": [old_generated],
            }
        )

        response = _parse_profile(payload, "compile_only")

        assert_error_envelope(
            response,
            code="RUN_PROTOCOL_ERROR",
            severity="error",
        )
        retained = response.data.get("compile", {})
        self.assertEqual(
            (
                compile_report["before"],  # type: ignore[index]
                compile_report["after"],  # type: ignore[index]
                delta,
                generated_assets,
            ),
            (
                retained.get("before"),  # type: ignore[union-attr]
                retained.get("after"),  # type: ignore[union-attr]
                retained.get("delta"),  # type: ignore[union-attr]
                retained.get("generated_assets"),  # type: ignore[union-attr]
            ),
            msg=(
                "a truthful protocol failure must retain validated compile evidence "
                "for planned/actual mismatch audit"
            ),
        )

    def test_clientsim_generated_mismatch_retains_executed_runtime_evidence(
        self,
    ) -> None:
        canonical = "Assets/SerializedUdonPrograms/Program.asset"
        payload = _valid_clientsim_payload()
        compile_report = payload["data"]["compile"]  # type: ignore[index]
        delta = cast(dict[str, object], compile_report["delta"])
        delta.update(
            {
                "planned_created_paths": [canonical],
                "actual_created_paths": [],
            }
        )
        generated_assets = compile_report["generated_assets"]  # type: ignore[index]
        generated_assets["planned_created_paths"] = [canonical]  # type: ignore[index]

        response = _parse_profile(payload, "clientsim")

        assert_error_envelope(
            response,
            code="RUN_PROTOCOL_ERROR",
            severity="error",
        )
        source_data = payload["data"]
        self.assertEqual(
            (
                True,
                source_data["compile"],  # type: ignore[index]
                source_data["clientsim"],  # type: ignore[index]
                source_data["side_effect_report"],  # type: ignore[index]
            ),
            (
                response.data.get("executed"),
                response.data.get("compile"),
                response.data.get("clientsim"),
                response.data.get("side_effect_report"),
            ),
            msg=(
                "a fully validated mismatch payload must retain truthful compile "
                "and already-executed ClientSim evidence"
            ),
        )

    def test_unknown_fields_are_rejected_at_each_protocol_section(self) -> None:
        for path in (
            (),
            ("data",),
            ("data", "compile"),
            ("data", "compile", "before"),
            ("data", "compile", "before", "related_assets", 0),
            ("data", "compile", "delta"),
            ("data", "clientsim"),
            ("data", "clientsim", "before"),
            ("data", "clientsim", "side_effect_report"),
            ("diagnostics", 0),
        ):
            with self.subTest(path=path):
                payload = _valid_clientsim_payload()
                target = payload if not path else _value_at_path(payload, path)
                target["unknown"] = "evidence"  # type: ignore[index]
                response = _parse_clientsim(payload)
                assert_error_envelope(
                    response,
                    code="RUN_PROTOCOL_ERROR",
                    severity="error",
                )


class RuntimeProtocolSuccessTests(unittest.TestCase):
    """Well-formed complete payloads retain trusted evidence."""

    def test_well_formed_payload_returns_merged_tool_response(self) -> None:
        payload = _valid_clientsim_payload()
        payload["severity"] = "warning"
        payload["message"] = "completed with warnings"

        response = _parse_clientsim(payload)

        self.assertTrue(response.success)
        self.assertEqual("RUN_CLIENTSIM_OK", response.code)
        self.assertEqual(Severity.WARNING, response.severity)
        self.assertEqual("validate_runtime", response.data["action"])
        self.assertEqual(1, response.data["udon_program_count"])
        self.assertEqual("D:/Project", response.data["project_root"])
        self.assertEqual(1, len(response.diagnostics))
        diagnostic = response.diagnostics[0]
        self.assertEqual("Assets/Program.asset", diagnostic.path)
        self.assertEqual("validate_runtime.compile", diagnostic.location)
        self.assertEqual("audited", diagnostic.detail)
        self.assertEqual("complete", diagnostic.evidence)

    def test_non_json_severity_enum_is_rejected(self) -> None:
        payload = _valid_clientsim_payload()
        payload["severity"] = Severity.INFO

        response = _parse_clientsim(payload)

        assert_error_envelope(
            response,
            code="RUN_PROTOCOL_ERROR",
            severity="error",
        )


if __name__ == "__main__":
    unittest.main()
