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
        action="run_clientsim",
        project_root=Path("/project"),
        scene_path="Assets/Scene.unity",
        profile="default",
        log_path=Path("/logs/run.log"),
        relative_fn=_identity_relative,
    )


class RuntimeProtocolFailureTests(unittest.TestCase):
    """Issue #222 Phase 2 — each per-field rejection branch is expressed
    through parametrization so the input shape and the expected message
    regex are visible in the test identifier; a new branch is added by
    appending one tuple to ``_REJECTION_ROWS``.
    """

    # Each row: (subtest_label, payload, message_regex).  The payload is
    # the protocol response the parser receives; the regex pins the
    # ``RUN_PROTOCOL_ERROR`` envelope's ``message`` field for that row.
    _BASE_VALID = {
        "success": True,
        "severity": "info",
        "code": "X",
        "message": "m",
        "data": {},
        "diagnostics": [],
    }
    _REJECTION_ROWS = (
        (
            "non_object_root",
            "not an object",
            r"response root must be an object",
        ),
        (
            "missing_success",
            {**{k: v for k, v in _BASE_VALID.items() if k != "success"}},
            r"field 'success' must be a boolean",
        ),
        (
            "invalid_severity",
            {**_BASE_VALID, "severity": "magenta"},
            r"field 'severity' is invalid",
        ),
        (
            "empty_code",
            {**_BASE_VALID, "code": "   "},
            r"field 'code' must be a non-empty string",
        ),
        (
            "non_string_message",
            {**_BASE_VALID, "message": 123},
            r"field 'message' must be a string",
        ),
        (
            "non_object_data",
            {**_BASE_VALID, "data": "not-an-object"},
            r"field 'data' must be an object",
        ),
        (
            "non_array_diagnostics",
            {**_BASE_VALID, "diagnostics": "not-an-array"},
            r"field 'diagnostics' must be an array",
        ),
        (
            "non_object_diagnostic_entry",
            {**_BASE_VALID, "diagnostics": ["not-an-object"]},
            r"diagnostics entries must be objects",
        ),
    )

    def test_per_field_rejection_returns_protocol_error_envelope(self) -> None:
        for label, payload, message_regex in self._REJECTION_ROWS:
            with self.subTest(label=label):
                response = _parse(payload)
                assert_error_envelope(
                    response,
                    code="RUN_PROTOCOL_ERROR",
                    severity="error",
                    message_match=message_regex,
                )

    def test_clientsim_requires_boolean_executed_field(self) -> None:
        invalid_data_rows: tuple[tuple[str, dict[str, object]], ...] = (
            ("missing", {}),
            ("null", {"executed": None}),
            ("integer", {"executed": 1}),
            ("string", {"executed": "true"}),
        )
        for label, data in invalid_data_rows:
            with self.subTest(label=label):
                response = _parse_clientsim(
                    {
                        **self._BASE_VALID,
                        "data": data,
                    }
                )
                assert_error_envelope(
                    response,
                    code="RUN_PROTOCOL_ERROR",
                    severity="error",
                    message_match=r"field 'data.executed' must be a boolean",
                )

    def test_failure_envelope_merges_base_context_and_stamps_flags(self) -> None:
        # The base-context-merge + read-only / executed flag-stamping
        # behaviour is shared by every rejection branch; the non-object
        # root is the cheapest reproduction.
        response = _parse("not an object")
        for key in _BASE_CONTEXT_KEYS:
            self.assertIn(key, response.data)
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
