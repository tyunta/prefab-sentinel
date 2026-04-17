"""T35: Unity batchmode harness skeleton (issue #87).

The real harness invokes the Unity Editor in batchmode against a
fixture project and asserts that integration actions (compile,
validate_refs, run_script, ...) succeed end-to-end.  That requires
Unity, a license, and significant runtime; it runs under the
``unity_live`` pytest marker on a nightly schedule only.

This skeleton always skips when ``UNITYTOOL_UNITY_COMMAND`` is unset,
which is the state of every developer machine and of CI by default.
The skip message names the env var so the cause is obvious.
"""

from __future__ import annotations

import os
import unittest

import pytest


@pytest.mark.unity_live
class UnityLiveHarness(unittest.TestCase):
    """Nightly-only Unity batchmode harness (issue #87)."""

    def test_skeleton_skipped_without_command(self) -> None:
        """Skip unless ``UNITYTOOL_UNITY_COMMAND`` names a Unity CLI."""
        command = os.environ.get("UNITYTOOL_UNITY_COMMAND", "")
        if not command:
            self.skipTest(
                "UNITYTOOL_UNITY_COMMAND is not set; "
                "unity_live harness is skipped on machines without a Unity Editor."
            )
        # The live harness is intentionally not implemented here.  Once
        # the nightly workflow provisions a Unity runner, replace this
        # skip-through with the real batchmode invocation.
        self.skipTest(
            "UNITYTOOL_UNITY_COMMAND is set but the live harness body is "
            "a skeleton (issue #87); extend this test when the nightly "
            "workflow gains a Unity runner."
        )


if __name__ == "__main__":
    unittest.main()
