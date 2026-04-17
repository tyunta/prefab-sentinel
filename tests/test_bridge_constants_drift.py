"""T26–T29: drift-checker unit tests.

Patches the three loader functions of ``scripts.check_bridge_constants``
to inject fixtures so each invariant can be exercised independently
without touching the live repository files.
"""

from __future__ import annotations

import unittest
from unittest.mock import patch

from scripts import check_bridge_constants as checker


def _fake_cs(
    bridge_version: str = "0.5.150",
    protocol_version: int = 1,
    severities: frozenset[str] | None = None,
) -> dict[str, object]:
    return {
        "bridge_version": bridge_version,
        "protocol_version": protocol_version,
        "severities": set(severities) if severities is not None else {
            "info",
            "warning",
            "error",
        },
    }


class BridgeConstantsDriftTests(unittest.TestCase):
    """Each test pins a single invariant-mismatch path.

    The Python-side constants are imported dynamically inside ``main`` so
    we do not need to patch ``VALID_SEVERITIES`` / ``PROTOCOL_VERSION``
    here — the fixtures we inject on the loader side create the drift.
    """

    def test_version_mismatch_reported(self) -> None:
        """T26: pyproject vs plugin.json version mismatch returns 1."""
        with patch.object(checker, "_load_pyproject_version", return_value="0.5.150"), \
             patch.object(checker, "_load_plugin_version", return_value="0.5.149"), \
             patch.object(checker, "_load_csharp_constants", return_value=_fake_cs()):
            exit_code = checker.main()
        self.assertEqual(1, exit_code)

    def test_protocol_mismatch_reported(self) -> None:
        """T27: C# ProtocolVersion differing from Python PROTOCOL_VERSION returns 1."""
        with patch.object(checker, "_load_pyproject_version", return_value="0.5.150"), \
             patch.object(checker, "_load_plugin_version", return_value="0.5.150"), \
             patch.object(
                 checker,
                 "_load_csharp_constants",
                 return_value=_fake_cs(protocol_version=99),
             ):
            exit_code = checker.main()
        self.assertEqual(1, exit_code)

    def test_severity_mismatch_reported(self) -> None:
        """T28: C# emitting a severity not in Python VALID_SEVERITIES returns 1."""
        with patch.object(checker, "_load_pyproject_version", return_value="0.5.150"), \
             patch.object(checker, "_load_plugin_version", return_value="0.5.150"), \
             patch.object(
                 checker,
                 "_load_csharp_constants",
                 return_value=_fake_cs(severities=frozenset({"info", "danger"})),
             ):
            exit_code = checker.main()
        self.assertEqual(1, exit_code)

    def test_missing_input_returns_2(self) -> None:
        """T29: a loader raising _LoadError (e.g. pyproject missing) returns 2."""
        def _raise_load_error() -> str:
            raise checker._LoadError("pyproject.toml missing for fixture test")

        with patch.object(checker, "_load_pyproject_version", side_effect=_raise_load_error), \
             patch.object(checker, "_load_plugin_version", return_value="0.5.150"), \
             patch.object(checker, "_load_csharp_constants", return_value=_fake_cs()):
            exit_code = checker.main()
        self.assertEqual(2, exit_code)

    def test_all_aligned_returns_zero(self) -> None:
        """Sanity: all three invariants matching yields exit 0."""
        with patch.object(checker, "_load_pyproject_version", return_value="0.5.150"), \
             patch.object(checker, "_load_plugin_version", return_value="0.5.150"), \
             patch.object(
                 checker,
                 "_load_csharp_constants",
                 return_value=_fake_cs(
                     bridge_version="0.5.150",
                     protocol_version=1,
                     severities=frozenset({"info", "warning", "error"}),
                 ),
             ):
            exit_code = checker.main()
        self.assertEqual(0, exit_code)


if __name__ == "__main__":
    unittest.main()
