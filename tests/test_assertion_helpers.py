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

    # --- payload-pinning option ----------------------------------------

    def test_payload_match_passes_silently(self) -> None:
        envelope = _failure(data={"guid": "abc", "fileID": 7})
        assert_error_envelope(
            envelope,
            code="REF001",
            data={"guid": "abc", "fileID": 7},
        )

    def test_rejects_payload_value_mismatch(self) -> None:
        envelope = _failure(data={"guid": "abc", "fileID": 7})
        with self.assertRaises(AssertionError) as ctx:
            assert_error_envelope(
                envelope,
                code="REF001",
                data={"guid": "abc", "fileID": 8},
            )
        message = str(ctx.exception)
        self.assertIn("data", message)
        self.assertIn("expected", message)
        self.assertIn("observed", message)

    def test_rejects_payload_missing_key(self) -> None:
        envelope = _failure(data={"guid": "abc"})
        with self.assertRaises(AssertionError) as ctx:
            assert_error_envelope(
                envelope,
                code="REF001",
                data={"guid": "abc", "fileID": 7},
            )
        message = str(ctx.exception)
        self.assertIn("data", message)
        self.assertIn("fileID", message)

    def test_rejects_payload_extra_key(self) -> None:
        envelope = _failure(data={"guid": "abc", "fileID": 7, "extra": True})
        with self.assertRaises(AssertionError) as ctx:
            assert_error_envelope(
                envelope,
                code="REF001",
                data={"guid": "abc", "fileID": 7},
            )
        message = str(ctx.exception)
        self.assertIn("data", message)
        self.assertIn("extra", message)

    def test_rejects_non_mapping_payload_when_pinned(self) -> None:
        envelope = _failure(data=None)
        # Force payload to a non-mapping scalar to exercise the type guard.
        envelope["data"] = "scalar"
        with self.assertRaises(AssertionError) as ctx:
            assert_error_envelope(
                envelope,
                code="REF001",
                data={"guid": "abc"},
            )
        message = str(ctx.exception)
        self.assertIn("data", message)
        self.assertIn("str", message)

    def test_field_and_data_pin_together_without_aliasing(self) -> None:
        """Regression: ``field`` and ``data`` arguments must coexist
        without name-shadow aliasing.  The data-pinning branch must
        still apply when both arguments are supplied."""
        envelope = _failure(data={"field": "guid", "guid": "abc"})
        # Match path (both checks pass).
        assert_error_envelope(
            envelope,
            code="REF001",
            field="guid",
            data={"field": "guid", "guid": "abc"},
        )
        # Mismatch path: the data check must still trigger when both
        # are supplied.  A wrong data dict raises even when the field
        # check would otherwise pass.
        with self.assertRaises(AssertionError) as ctx:
            assert_error_envelope(
                envelope,
                code="REF001",
                field="guid",
                data={"field": "guid", "guid": "DIFFERENT"},
            )
        self.assertIn("data", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
