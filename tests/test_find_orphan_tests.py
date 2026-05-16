"""Orphan-test detector prerequisite enforcement (issue #272).

The detector script (``scripts/find_orphan_tests.py``) requires an
existing mutmut cache before it can subset the killed-mutant set per
test file.  When no cache is available the script must exit non-zero
with a message naming the prerequisite invocation so the operator
recovers without inspecting the source.
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import pytest

# The detector script lives under ``scripts/`` and the test drives it
# as an out-of-process subprocess; nothing here observes a mutation
# applied under ``prefab_sentinel/``.  The marker keeps the test out of
# the mutmut pytest selection (issue #346).
pytestmark = pytest.mark.source_text_invariant

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
_DETECTOR_PATH = _PROJECT_ROOT / "scripts" / "find_orphan_tests.py"


class TestOrphanDetectorPrerequisite(unittest.TestCase):
    """Exit status and message when the mutmut cache is absent."""

    def test_detector_exits_nonzero_when_cache_missing(self) -> None:
        # Run the script from an isolated working directory that
        # contains no ``mutants/`` artefact and no ``.mutmut-cache``;
        # the script imports ``_PROJECT_ROOT`` from its own file
        # location, so the cache lookup probes the real repository
        # but the project's quarterly cadence guarantees no cache
        # outside a mutmut session.
        with tempfile.TemporaryDirectory() as raw:
            # The detector lookup keys off
            # ``_PROJECT_ROOT / "mutants" / "mutmut-stats.json"`` (the
            # mutmut 3.5 cache sentinel); it is absent during a normal
            # repo state, so a direct invocation suffices here.
            result = subprocess.run(
                [sys.executable, str(_DETECTOR_PATH)],
                cwd=raw,
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
        combined = (result.stdout or "") + (result.stderr or "")

        # The detector skips its body and writes the prerequisite
        # message only when no cache is available; if the developer
        # happens to run this test inside a mutmut session the cache
        # might exist and the detector would write a report instead.
        # Skip the prerequisite check in that case so the test does
        # not erroneously fail.
        if (_PROJECT_ROOT / "mutants" / "mutmut-stats.json").exists():
            self.skipTest("mutmut cache is present; prerequisite path is unreachable")

        # Tuple value-pin so the diagnostic exit code AND the
        # prerequisite-naming text both surface in one assertion.
        self.assertEqual(
            (2, True, True),
            (
                result.returncode,
                "no mutmut cache available" in combined,
                "uv run mutmut run" in combined,
            ),
            f"expected exit-code 2 with prerequisite message; got "
            f"rc={result.returncode!r} message={combined!r}",
        )


if __name__ == "__main__":
    unittest.main()
