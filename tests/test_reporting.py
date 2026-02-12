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
                                "matched_issue_count": 3,
                                "categories": {"UDON_NULLREF": 2, "BROKEN_PPTR": 1},
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


if __name__ == "__main__":
    unittest.main()
