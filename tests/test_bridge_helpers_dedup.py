"""Public surface of ``tests.bridge_test_helpers``.

After issue #270 collapses the dispatch surface to a single Editor
Bridge path, the shared helper module exposes only the file-write
helper and the thread-based fake editor-bridge responder.  This module
pins that surface so a future change cannot quietly reintroduce the
batchmode-era ``drop_bridge_env`` / ``BRIDGE_ENV_VARS`` helpers without
the suite noticing.
"""

from __future__ import annotations

import unittest

import pytest

import tests.bridge_test_helpers as helpers

# The assertions inspect the helper module (a test-only file) and not
# the production tree, so they cannot observe mutations applied to
# ``prefab_sentinel/``.  The marker is the inclusion mechanism for
# mutmut's single-filter exclusion of repository-synchrony tests.
pytestmark = pytest.mark.source_text_invariant


_REQUIRED_SURVIVING_NAMES = ("write_file", "EditorBridgeResponder")
_BATCHMODE_ERA_NAMES = (
    "BRIDGE_ENV_VARS",
    "drop_bridge_env",
    "write_fake_runtime_runner",
)


class BridgeTestHelperSurfaceTests(unittest.TestCase):
    """The trimmed helper module exposes only editor-bridge helpers."""

    def test_required_surviving_names_are_exported(self) -> None:
        missing = [name for name in _REQUIRED_SURVIVING_NAMES if not hasattr(helpers, name)]
        self.assertEqual([], missing)

    def test_batchmode_era_names_are_absent(self) -> None:
        surviving = [name for name in _BATCHMODE_ERA_NAMES if hasattr(helpers, name)]
        self.assertEqual([], surviving)


if __name__ == "__main__":
    unittest.main()
