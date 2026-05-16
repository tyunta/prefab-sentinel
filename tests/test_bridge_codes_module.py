"""Test the constants module that names every wire-level patch-bridge code.

Issue #297 — ``tools/unity_patch_bridge.py`` previously carried 23
bare-string ``code="..."`` literal call sites. The new
``tools/_bridge_codes`` constants module exposes one named constant
per documented wire-level code so a rename surfaces as a name-
resolution failure rather than as silent wire drift.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
_TOOLS_DIR = _PROJECT_ROOT / "tools"
if str(_TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(_TOOLS_DIR))


class TestBridgeCodesModule(unittest.TestCase):
    """The constants module names every wire-code currently emitted by
    the patch bridge driver; each constant's value equals the existing
    wire string.
    """

    _EXPECTED_CODE_PAIRS: tuple[tuple[str, str], ...] = (
        ("BRIDGE_PROTOCOL_VERSION", "BRIDGE_PROTOCOL_VERSION"),
        ("BRIDGE_UNITY_RESPONSE_SCHEMA", "BRIDGE_UNITY_RESPONSE_SCHEMA"),
        ("BRIDGE_EDITOR_WRITE", "BRIDGE_EDITOR_WRITE"),
        ("BRIDGE_EDITOR_RESPONSE_READ", "BRIDGE_EDITOR_RESPONSE_READ"),
        ("BRIDGE_EDITOR_TIMEOUT", "BRIDGE_EDITOR_TIMEOUT"),
        ("BRIDGE_REQUEST_SCHEMA", "BRIDGE_REQUEST_SCHEMA"),
        ("BRIDGE_REQUEST_EMPTY", "BRIDGE_REQUEST_EMPTY"),
        ("BRIDGE_REQUEST_JSON", "BRIDGE_REQUEST_JSON"),
        ("BRIDGE_LEGACY_SCHEMA_REJECTED", "BRIDGE_LEGACY_SCHEMA_REJECTED"),
        ("BRIDGE_UNSUPPORTED_TARGET", "BRIDGE_UNSUPPORTED_TARGET"),
        ("BRIDGE_TIMEOUT_INVALID", "BRIDGE_TIMEOUT_INVALID"),
        ("BRIDGE_WATCH_DIR_MISSING", "BRIDGE_WATCH_DIR_MISSING"),
    )

    def test_module_imports_cleanly(self) -> None:
        import _bridge_codes  # noqa: PLC0415

        self.assertTrue(hasattr(_bridge_codes, "__name__"))

    def test_every_expected_constant_is_present_with_documented_value(
        self,
    ) -> None:
        import _bridge_codes  # noqa: PLC0415

        for name, expected_value in self._EXPECTED_CODE_PAIRS:
            self.assertTrue(
                hasattr(_bridge_codes, name),
                f"_bridge_codes.{name} not present",
            )
            self.assertEqual(
                expected_value,
                getattr(_bridge_codes, name),
                f"_bridge_codes.{name} != {expected_value!r}",
            )


if __name__ == "__main__":
    unittest.main()
