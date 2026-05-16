from __future__ import annotations

import csv
import io
import unittest

from prefab_sentinel.reporting import _extract_runtime_validation_data, render_csv_report
from prefab_sentinel.reporting_markdown import render_markdown_report


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

    def test_render_markdown_report_limits_steps_list(self) -> None:
        payload = {
            "success": True,
            "severity": "info",
            "code": "VALIDATE_RUNTIME_RESULT",
            "message": "ok",
            "data": {
                "steps": [
                    {"step": "a", "result": {"data": {"x": 1}}},
                    {"step": "b", "result": {"data": {"x": 2}}},
                    {"step": "c", "result": {"data": {"x": 3}}},
                ]
            },
            "diagnostics": [],
        }

        rendered = render_markdown_report(payload, md_max_steps=1)

        self.assertIn('"steps_total": 3', rendered)
        self.assertIn('"steps_truncated_for_markdown": 2', rendered)

    def test_render_markdown_report_includes_runtime_section(self) -> None:
        payload = {
            "success": False,
            "severity": "critical",
            "code": "VALIDATE_RUNTIME_RESULT",
            "message": "validate.runtime pipeline completed (log-based scaffold).",
            "data": {
                "steps": [
                    {
                        "step": "compile_udonsharp",
                        "result": {"code": "RUN_COMPILE_SKIPPED", "data": {}},
                    },
                    {
                        "step": "run_clientsim",
                        "result": {"code": "RUN_CLIENTSIM_SKIPPED", "data": {}},
                    },
                    {
                        "step": "collect_unity_console",
                        "result": {"code": "RUN_LOG_COLLECTED", "data": {}},
                    },
                    {
                        "step": "classify_errors",
                        "result": {
                            "code": "RUN001",
                            "data": {
                                "line_count": 12,
                                "count_total": 3,
                                "count_by_category": {"UDON_NULLREF": 2, "BROKEN_PPTR": 1},
                                "categories_by_severity": {
                                    "critical": 2,
                                    "error": 1,
                                    "warning": 0,
                                },
                            },
                        },
                    },
                    {
                        "step": "assert_no_critical_errors",
                        "result": {
                            "code": "RUN001",
                            "data": {
                                "critical_count": 2,
                                "error_count": 1,
                                "warning_count": 0,
                                "allow_warnings": False,
                            },
                        },
                    },
                ]
            },
            "diagnostics": [],
        }

        rendered = render_markdown_report(payload)

        self.assertIn("## Runtime Validation", rendered)
        self.assertIn("Compile Step: RUN_COMPILE_SKIPPED", rendered)
        self.assertIn("ClientSim Step: RUN_CLIENTSIM_SKIPPED", rendered)
        self.assertIn("Log Collect Step: RUN_LOG_COLLECTED", rendered)
        self.assertIn("Matched Issues: 3", rendered)
        self.assertIn("| UDON_NULLREF | 2 |", rendered)
        self.assertIn("| BROKEN_PPTR | 1 |", rendered)


    def test_render_markdown_report_shows_asset_name_in_noise_section(self) -> None:
        payload = {
            "success": False,
            "severity": "error",
            "code": "VALIDATE_REFS_RESULT",
            "message": "broken refs detected.",
            "data": {
                "steps": [
                    {
                        "step": "scan_broken_references",
                        "result": {
                            "data": {
                                "categories_occurrences": {
                                    "missing_asset": 50,
                                    "missing_local_id": 0,
                                },
                                "ignored_missing_asset_occurrences": 10,
                                "skipped_external_prefab_fileid_checks": 0,
                                "top_missing_asset_guids": [
                                    {
                                        "guid": "aaaa" * 8,
                                        "occurrences": 50,
                                        "asset_name": "Packages/com.unity.textmeshpro/TMP.cs",
                                    }
                                ],
                                "top_ignored_missing_asset_guids": [
                                    {
                                        "guid": "bbbb" * 8,
                                        "occurrences": 10,
                                        "asset_name": "Assets/Old/Removed.mat",
                                    }
                                ],
                            }
                        },
                    }
                ]
            },
            "diagnostics": [],
        }

        rendered = render_markdown_report(payload)

        self.assertIn(
            "Top Missing Asset GUID: "
            "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa "
            "(Packages/com.unity.textmeshpro/TMP.cs) (50)",
            rendered,
        )
        self.assertIn(
            "Top Ignored Missing Asset GUID: "
            "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb "
            "(Assets/Old/Removed.mat) (10)",
            rendered,
        )


class CsvReportTests(unittest.TestCase):
    def test_render_csv_report_diagnostics_only(self) -> None:
        payload = {
            "success": False,
            "severity": "error",
            "code": "VALIDATE_REFS_RESULT",
            "message": "broken",
            "data": {},
            "diagnostics": [
                {
                    "path": "Assets/Foo.prefab",
                    "location": "10:5",
                    "detail": "missing_asset",
                    "evidence": "guid abc not found",
                },
                {
                    "path": "Assets/Bar.unity",
                    "location": "20:3",
                    "detail": "missing_local_id",
                    "evidence": "fileID 123 not found",
                },
            ],
        }

        result = render_csv_report(payload)
        reader = csv.reader(io.StringIO(result))
        rows = list(reader)

        self.assertEqual(rows[0], ["path", "location", "detail", "evidence"])
        self.assertEqual(rows[1][0], "Assets/Foo.prefab")
        self.assertEqual(rows[1][2], "missing_asset")
        self.assertEqual(rows[2][0], "Assets/Bar.unity")
        self.assertEqual(len(rows), 3)

    def test_render_csv_report_with_summary(self) -> None:
        payload = {
            "success": True,
            "severity": "info",
            "code": "REF_SCAN_OK",
            "message": "No broken references.",
            "data": {
                "scanned_files": 42,
                "scanned_references": 1000,
                "broken_count": 0,
                "broken_occurrences": 0,
            },
            "diagnostics": [],
        }

        result = render_csv_report(payload, include_summary=True)
        lines = result.split("\n")

        # Summary section starts with key,value header
        self.assertIn("key,value", lines[0])
        # Contains metadata
        self.assertIn("success,True", result)
        self.assertIn("scanned_files,42", result)

        # Blank line separates summary from diagnostics
        reader = csv.reader(io.StringIO(result))
        rows = list(reader)
        # After summary rows + blank line, diagnostics header is present
        diag_header_found = any(row == ["path", "location", "detail", "evidence"] for row in rows)
        self.assertTrue(diag_header_found)

    def test_render_csv_report_empty_diagnostics(self) -> None:
        payload = {
            "success": True,
            "severity": "info",
            "code": "OK",
            "message": "ok",
            "data": {},
            "diagnostics": [],
        }

        result = render_csv_report(payload)
        reader = csv.reader(io.StringIO(result))
        rows = list(reader)

        # Only the header row
        self.assertEqual(rows[0], ["path", "location", "detail", "evidence"])
        self.assertEqual(len([r for r in rows if r]), 1)


class ReportingRuntimeValidationTests(unittest.TestCase):
    """T64: ``_extract_runtime_validation_data`` must surface the renamed
    ``count_total`` / ``count_by_category`` keys (issue #89).
    """

    def test_emits_new_keys(self) -> None:
        payload_data = {
            "steps": [
                {
                    "step": "classify_errors",
                    "result": {
                        "code": "RUN001",
                        "success": False,
                        "severity": "critical",
                        "data": {
                            "line_count": 42,
                            "count_total": 5,
                            "count_by_category": {
                                "UDON_NULLREF": 3,
                                "BROKEN_PPTR": 2,
                            },
                            "categories_by_severity": {
                                "critical": 3,
                                "error": 2,
                                "warning": 0,
                            },
                        },
                    },
                },
            ],
        }

        runtime = _extract_runtime_validation_data(payload_data)
        classification = runtime.get("classification", {})

        self.assertIn("count_total", classification)
        self.assertIn("count_by_category", classification)
        self.assertNotIn("matched_issue_count", classification)
        self.assertNotIn("categories", classification)
        self.assertEqual(5, classification["count_total"])
        self.assertEqual(
            {"UDON_NULLREF": 3, "BROKEN_PPTR": 2},
            classification["count_by_category"],
        )


if __name__ == "__main__":
    unittest.main()
