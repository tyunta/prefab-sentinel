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


def _parse(payload: object) -> object:
    return protocol.parse_runtime_response(
        payload,
        action="test_action",
        project_root=Path("/project"),
        scene_path="Assets/Scene.unity",
        profile="default",
        log_path=Path("/logs/run.log"),
        relative_fn=_identity_relative,
    )


class RuntimeProtocolFailureTests(unittest.TestCase):
    """Each rejection branch yields a value-pinned failure envelope."""

    def test_non_object_root_returns_protocol_error(self) -> None:
        response = _parse("not an object")
        assert_error_envelope(
            response,
            code="RUN_PROTOCOL_ERROR",
            severity="error",
            message_match=r"response root must be an object",
        )
        # The base context is merged into the envelope's data payload.
        for key in _BASE_CONTEXT_KEYS:
            self.assertIn(key, response.data)
        self.assertEqual(True, response.data["read_only"])
        self.assertEqual(False, response.data["executed"])

    def test_missing_success_field_returns_protocol_error(self) -> None:
        # Missing ``success`` reaches the ``not isinstance(success, bool)``
        # rejection (``payload.get`` returns None, which is not a bool).
        response = _parse({
            "severity": "info",
            "code": "X",
            "message": "m",
            "data": {},
            "diagnostics": [],
        })
        assert_error_envelope(
            response,
            code="RUN_PROTOCOL_ERROR",
            message_match=r"field 'success' must be a boolean",
        )

    def test_invalid_severity_returns_protocol_error(self) -> None:
        response = _parse({
            "success": True,
            "severity": "magenta",
            "code": "X",
            "message": "m",
            "data": {},
            "diagnostics": [],
        })
        assert_error_envelope(
            response,
            code="RUN_PROTOCOL_ERROR",
            message_match=r"field 'severity' is invalid",
        )

    def test_empty_code_returns_protocol_error(self) -> None:
        response = _parse({
            "success": True,
            "severity": "info",
            "code": "   ",
            "message": "m",
            "data": {},
            "diagnostics": [],
        })
        assert_error_envelope(
            response,
            code="RUN_PROTOCOL_ERROR",
            message_match=r"field 'code' must be a non-empty string",
        )

    def test_non_string_message_returns_protocol_error(self) -> None:
        response = _parse({
            "success": True,
            "severity": "info",
            "code": "X",
            "message": 123,
            "data": {},
            "diagnostics": [],
        })
        assert_error_envelope(
            response,
            code="RUN_PROTOCOL_ERROR",
            message_match=r"field 'message' must be a string",
        )

    def test_non_object_data_returns_protocol_error(self) -> None:
        response = _parse({
            "success": True,
            "severity": "info",
            "code": "X",
            "message": "m",
            "data": "not-an-object",
            "diagnostics": [],
        })
        assert_error_envelope(
            response,
            code="RUN_PROTOCOL_ERROR",
            message_match=r"field 'data' must be an object",
        )

    def test_non_array_diagnostics_returns_protocol_error(self) -> None:
        response = _parse({
            "success": True,
            "severity": "info",
            "code": "X",
            "message": "m",
            "data": {},
            "diagnostics": "not-an-array",
        })
        assert_error_envelope(
            response,
            code="RUN_PROTOCOL_ERROR",
            message_match=r"field 'diagnostics' must be an array",
        )

    def test_non_object_diagnostic_entry_returns_protocol_error(self) -> None:
        response = _parse({
            "success": True,
            "severity": "info",
            "code": "X",
            "message": "m",
            "data": {},
            "diagnostics": ["not-an-object"],
        })
        assert_error_envelope(
            response,
            code="RUN_PROTOCOL_ERROR",
            message_match=r"diagnostics entries must be objects",
        )

    def test_failure_data_carries_read_only_and_executed_flags(self) -> None:
        # Any rejection path stamps both flags; the non-object-root path
        # is the simplest reproduction.
        response = _parse(["not", "an", "object"])
        self.assertEqual(True, response.data["read_only"])
        self.assertEqual(False, response.data["executed"])


class RuntimeProtocolSuccessTests(unittest.TestCase):
    """Well-formed payload merges base context with payload data and the
    severity coerces to the matching enum value.
    """

    def test_well_formed_payload_returns_merged_tool_response(self) -> None:
        response = _parse({
            "success": True,
            "severity": "warning",
            "code": "RUN_OK",
            "message": "completed with warnings",
            "data": {"action_count": 7, "scene_path": "overridden.unity"},
            "diagnostics": [
                {
                    "path": "Assets/Scene.unity",
                    "location": "1:1",
                    "detail": "outdated",
                    "evidence": "stale skin reference",
                }
            ],
        })
        self.assertTrue(response.success)
        self.assertEqual("RUN_OK", response.code)
        # Severity coerces to the enum member.
        self.assertEqual(Severity.WARNING, response.severity)
        # Base context is merged but payload's data overrides on key clash.
        self.assertEqual("test_action", response.data["action"])
        self.assertEqual(7, response.data["action_count"])
        self.assertEqual("overridden.unity", response.data["scene_path"])
        # Diagnostic projection: each dict entry becomes a Diagnostic.
        self.assertEqual(1, len(response.diagnostics))
        diag = response.diagnostics[0]
        self.assertEqual("Assets/Scene.unity", diag.path)
        self.assertEqual("1:1", diag.location)
        self.assertEqual("outdated", diag.detail)
        self.assertEqual("stale skin reference", diag.evidence)

    def test_severity_passes_through_when_already_enum(self) -> None:
        response = _parse({
            "success": True,
            "severity": Severity.INFO,
            "code": "RUN_OK",
            "message": "ok",
            "data": {},
            "diagnostics": [],
        })
        self.assertEqual(Severity.INFO, response.severity)


if __name__ == "__main__":
    unittest.main()
