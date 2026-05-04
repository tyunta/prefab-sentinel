"""T25: drift-checker sync test.

Invokes the live drift checker against the current repository and asserts
no drift is present.  This covers the regression path: any commit that
advances one of the three invariants without the others will fail here.
"""

from __future__ import annotations

import unittest

import pytest

from scripts.check_bridge_constants import main as check_main

# Issue #167: this module invokes the live drift checker against the
# original (un-mutated) repository checkout, so its assertions cannot
# observe mutations applied to ``prefab_sentinel/``.  The marker is the
# inclusion mechanism for repository-synchrony tests; mutmut's pytest
# selection excludes it via a single ``-m`` filter.
pytestmark = pytest.mark.source_text_invariant


class BridgeConstantsSyncTests(unittest.TestCase):
    def test_no_drift_in_repository(self) -> None:
        self.assertEqual(0, check_main())


if __name__ == "__main__":
    unittest.main()
