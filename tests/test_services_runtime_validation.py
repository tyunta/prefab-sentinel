"""Tests for ``RuntimeValidationService.classify_errors`` reshape (issue #89).

Pins the data-key contract after the rename:

- ``matched_issue_count`` -> ``count_total``
- ``categories`` -> ``count_by_category``

And the severity pin:

- ``UDON_NULLREF`` matches must surface at ``severity="critical"``.
"""

from __future__ import annotations

import unittest

from prefab_sentinel.contracts import Severity
from prefab_sentinel.services.runtime_validation import RuntimeValidationService


class RuntimeValidationClassifyTests(unittest.TestCase):
    def test_udon_nullref_returns_critical(self) -> None:
        """T58: a line matching UDON_NULLREF must surface at severity=critical."""
        svc = RuntimeValidationService()
        resp = svc.classify_errors(
            ["NullReferenceException in UdonBehaviour.MyEvent"],
        )
        self.assertEqual(Severity.CRITICAL, resp.severity)

    def test_data_has_count_total_and_count_by_category(self) -> None:
        """T59: response data must expose ``count_total`` + ``count_by_category``
        and must not carry the old ``matched_issue_count`` / ``categories`` keys."""
        svc = RuntimeValidationService()
        resp = svc.classify_errors(
            [
                "Broken PPtr in file A",
                "NullReferenceException in UdonBehaviour.B",
            ],
        )
        self.assertIn("count_total", resp.data)
        self.assertIn("count_by_category", resp.data)
        self.assertNotIn("matched_issue_count", resp.data)
        self.assertNotIn("categories", resp.data)

    def test_count_total_reflects_match_count(self) -> None:
        """T60: ``count_total`` equals the number of lines that matched a
        known category (kept distinct from the size of
        ``count_by_category``, which counts *categories* not *hits*)."""
        svc = RuntimeValidationService()
        resp = svc.classify_errors(
            [
                "Broken PPtr in file A",
                "Broken PPtr in file B",
                "NullReferenceException in UdonBehaviour.C",
                "unrelated log line",
            ],
        )
        self.assertEqual(3, resp.data["count_total"])

    def test_count_by_category_groups_per_category(self) -> None:
        """T61: ``count_by_category`` maps each category to its hit count."""
        svc = RuntimeValidationService()
        resp = svc.classify_errors(
            [
                "Broken PPtr in file A",
                "Broken PPtr in file B",
                "NullReferenceException in UdonBehaviour.C",
            ],
        )
        by_cat = resp.data["count_by_category"]
        self.assertEqual(2, by_cat.get("BROKEN_PPTR"))
        self.assertEqual(1, by_cat.get("UDON_NULLREF"))

    def test_empty_input_returns_zero_total(self) -> None:
        """T62: an empty log yields ``count_total == 0`` and empty
        ``count_by_category``."""
        svc = RuntimeValidationService()
        resp = svc.classify_errors([])
        self.assertEqual(0, resp.data["count_total"])
        self.assertEqual({}, resp.data["count_by_category"])
        self.assertTrue(resp.success)

    def test_unmatched_lines_do_not_inflate_total(self) -> None:
        """T63: lines that do not match any pattern must not contribute to
        ``count_total`` or ``count_by_category``."""
        svc = RuntimeValidationService()
        resp = svc.classify_errors(
            [
                "just a log line",
                "another unrelated message",
            ],
        )
        self.assertEqual(0, resp.data["count_total"])
        self.assertEqual({}, resp.data["count_by_category"])


if __name__ == "__main__":
    unittest.main()
