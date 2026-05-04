"""Unit tests for ``tests._assertion_helpers.assert_error_envelope``."""

from __future__ import annotations

import unittest

from prefab_sentinel.contracts import Severity, error_response
from tests._assertion_helpers import assert_error_envelope


def _failure(
    *,
    code: str = "REF001",
    severity: str = "error",
    message: str = "missing GUID",
    data: dict | None = None,
) -> dict:
    return {
        "success": False,
        "severity": severity,
        "code": code,
        "message": message,
        "data": data or {},
        "diagnostics": [],
    }


class AssertErrorEnvelopeTests(unittest.TestCase):
    def test_full_match_passes_silently(self) -> None:
        envelope = _failure(
            code="REF001",
            severity="error",
            message="missing GUID for asset",
            data={"field": "guid"},
        )
        assert_error_envelope(
            envelope,
            code="REF001",
            severity="error",
            field="guid",
            message_match=r"missing\s+GUID",
        )

    def test_rejects_success_response(self) -> None:
        envelope = {
            "success": True,
            "severity": "info",
            "code": "OK",
            "message": "",
            "data": {},
            "diagnostics": [],
        }
        with self.assertRaisesRegex(AssertionError, r"success"):
            assert_error_envelope(envelope, code="REF001")

    def test_rejects_code_mismatch(self) -> None:
        envelope = _failure(code="REF002")
        with self.assertRaisesRegex(AssertionError, r"code"):
            assert_error_envelope(envelope, code="REF001")

    def test_rejects_severity_mismatch(self) -> None:
        envelope = _failure(severity="warning")
        with self.assertRaisesRegex(AssertionError, r"severity"):
            assert_error_envelope(envelope, code="REF001", severity="error")

    def test_rejects_field_mismatch(self) -> None:
        envelope = _failure(data={"field": "fileID"})
        with self.assertRaisesRegex(AssertionError, r"field"):
            assert_error_envelope(envelope, code="REF001", field="guid")

    def test_rejects_message_pattern_mismatch(self) -> None:
        envelope = _failure(message="completely different")
        with self.assertRaisesRegex(AssertionError, r"message"):
            assert_error_envelope(
                envelope,
                code="REF001",
                message_match=r"missing GUID",
            )

    def test_accepts_tool_response_dataclass(self) -> None:
        """Helper must recognise the ``ToolResponse`` dataclass shape."""
        response = error_response(
            "REF001",
            "missing GUID for asset",
            data={"field": "guid"},
        )
        # ToolResponse stores severity as a Severity enum; helper must coerce it.
        assert response.severity is Severity.ERROR
        assert_error_envelope(
            response,
            code="REF001",
            severity="error",
            field="guid",
            message_match=r"missing GUID",
        )


if __name__ == "__main__":
    unittest.main()
