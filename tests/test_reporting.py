from __future__ import annotations

import unittest

from unitytool.reporting import render_markdown_report


class ReportingTests(unittest.TestCase):
    def test_render_markdown_report_includes_noise_section(self) -> None:
        payload = {
            "success": False,
            "severity": "error",
            "code": "VALIDATE_REFS_RESULT",
            "message": "validate.refs pipeline completed (read-only).",
            "data": {
                "steps": [
                    {
                        "step": "scan_broken_references",
                        "result": {
                            "data": {
                                "categories_occurrences": {
                                    "missing_asset": 123,
                                    "missing_local_id": 7,
                                },
                                "ignored_missing_asset_occurrences": 80,
                                "skipped_external_prefab_fileid_checks": 5,
                                "top_missing_asset_guids": [
                                    {"guid": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "occurrences": 90}
                                ],
                                "top_ignored_missing_asset_guids": [
                                    {"guid": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb", "occurrences": 80}
                                ],
                            }
                        },
                    }
                ]
            },
            "diagnostics": [],
        }

        rendered = render_markdown_report(payload)

        self.assertIn("## Noise Reduction", rendered)
        self.assertIn("Missing Asset Occurrences: 123", rendered)
        self.assertIn("Ignored Missing Asset Occurrences: 80", rendered)
        self.assertIn("Top Missing Asset GUID: aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa (90)", rendered)
        self.assertIn(
            "Top Ignored Missing Asset GUID: bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb (80)",
            rendered,
        )

    def test_render_markdown_report_limits_usages_list(self) -> None:
        payload = {
            "success": True,
            "severity": "info",
            "code": "INSPECT_WHERE_USED_RESULT",
            "message": "ok",
            "data": {
                "steps": [
                    {
                        "step": "where_used",
                        "result": {
                            "data": {
                                "usages": [
                                    {"path": "A", "line": 1},
                                    {"path": "B", "line": 2},
                                    {"path": "C", "line": 3},
                                ]
                            }
                        },
                    }
                ]
            },
            "diagnostics": [],
        }

        rendered = render_markdown_report(payload, md_max_usages=1)

        self.assertIn('"usages_total": 3', rendered)
        self.assertIn('"usages_truncated_for_markdown": 2', rendered)


if __name__ == "__main__":
    unittest.main()
