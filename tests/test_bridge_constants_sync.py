"""T25: drift-checker sync test.

Invokes the live drift checker against the current repository and asserts
no drift is present.  This covers the regression path: any commit that
advances one of the three invariants without the others will fail here.
"""

from __future__ import annotations

import unittest

from scripts.check_bridge_constants import main as check_main


class BridgeConstantsSyncTests(unittest.TestCase):
    def test_no_drift_in_repository(self) -> None:
        self.assertEqual(0, check_main())


if __name__ == "__main__":
    unittest.main()
