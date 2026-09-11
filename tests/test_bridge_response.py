"""Contract tests for common Unity Bridge response envelopes."""

from __future__ import annotations

import unittest

from prefab_sentinel.bridge_response import is_bridge_response_envelope


class BridgeResponseEnvelopeTests(unittest.TestCase):
    def test_rejects_wrong_types_missing_fields_and_successful_error_states(self) -> None:
        """Rejects malformed common fields and impossible successful states."""
        base = {
            "success": True,
            "severity": "info",
            "code": "EDITOR_CTRL_OK",
            "message": "ok",
            "data": {},
            "diagnostics": [],
        }
        missing_rows = [
            (f"missing {field}", {key: value for key, value in base.items() if key != field})
            for field in base
        ]
        rows = [
            ("non-object root", "not an object"),
            *missing_rows,
            ("string success", {**base, "success": "false"}),
            ("numeric success", {**base, "success": 1}),
            ("unknown severity", {**base, "severity": "fatal"}),
            ("successful error", {**base, "severity": "error"}),
            ("successful critical", {**base, "severity": "critical"}),
            ("non-string code", {**base, "code": 7}),
            ("non-string message", {**base, "message": []}),
            ("non-object data", {**base, "data": []}),
            ("non-array diagnostics", {**base, "diagnostics": {}}),
        ]
        for label, payload in rows:
            with self.subTest(label=label):
                self.assertFalse(is_bridge_response_envelope(payload))

    def test_accepts_soft_negative_failure_severities(self) -> None:
        """Allows all known severities when the operation reports failure."""
        for severity in ("info", "warning", "error", "critical"):
            with self.subTest(severity=severity):
                self.assertTrue(is_bridge_response_envelope({
                    "success": False,
                    "severity": severity,
                    "code": "EDITOR_DEFERRED",
                    "message": "not complete",
                    "data": {},
                    "diagnostics": [],
                }))


if __name__ == "__main__":
    unittest.main()
