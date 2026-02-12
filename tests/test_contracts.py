from __future__ import annotations

import unittest

from unitytool.contracts import Severity, max_severity


class ContractTests(unittest.TestCase):
    def test_max_severity_picks_highest_level(self) -> None:
        result = max_severity([Severity.INFO, Severity.WARNING, Severity.CRITICAL])
        self.assertEqual(Severity.CRITICAL, result)

    def test_max_severity_defaults_to_info_for_empty(self) -> None:
        result = max_severity([])
        self.assertEqual(Severity.INFO, result)


if __name__ == "__main__":
    unittest.main()

