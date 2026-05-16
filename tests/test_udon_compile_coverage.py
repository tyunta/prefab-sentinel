"""T34: UdonSharp compile-error fixture skeleton (issue #86).

This is intentionally a skeleton.  A real UdonSharp compile coverage
test needs a running Unity Editor with the UdonSharp package installed;
that machinery is not part of this repository.  The skeleton exists so
that the fixture files in ``tests/fixtures/udon_compile_errors/`` are
committed and exercised at least syntactically by the test suite.

Per spec the test always skips.
"""

from __future__ import annotations

import unittest
from pathlib import Path

_FIXTURES = Path(__file__).resolve().parent / "fixtures" / "udon_compile_errors"

# Expected fixtures — keep the list next to the skeleton so future wiring
# edits only touch this file.
_EXPECTED_FIXTURES = (
    "syntax_error.cs",
    "type_mismatch.cs",
    "forbidden_api.cs",
)


class UdonSharpCompileCoverage(unittest.TestCase):
    """Skeleton for the UdonSharp compile-error coverage harness."""

    def test_skeleton_skipped_by_default(self) -> None:
        """Always skip until a live UdonSharp harness is wired in."""
        # Sanity: ensure the fixture files we promised are actually on disk.
        # (Running even this much under a ``self.skipTest`` means the CI
        # signal degrades to "skipped with reason" if a fixture goes missing,
        # but the alternative is silent drift.)
        for name in _EXPECTED_FIXTURES:
            self.assertTrue(
                (_FIXTURES / name).is_file(),
                f"Missing UdonSharp compile-error fixture: {name}",
            )
        self.skipTest(
            "UdonSharp compile coverage requires a live Unity + UdonSharp "
            "environment; this skeleton has no live harness wired in."
        )


if __name__ == "__main__":
    unittest.main()
