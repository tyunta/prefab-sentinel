from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from scripts.benchmark_regression_report import (
    _compare_scope,
    _normalize_scope,
    _pick_latest_by_scope,
    _render_alert_lines,
    _render_markdown_summary,
    _sort_results,
    _write_comparison_csv,
)


class BenchmarkRegressionReportTests(unittest.TestCase):
    def test_normalize_scope_unifies_separator(self) -> None:
        self.assertEqual("sample/avatar/Assets", _normalize_scope("sample\\avatar\\Assets"))

    def test_pick_latest_by_scope_prefers_newer_generated_at(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            older = root / "older.json"
            newer = root / "newer.json"

            older.write_text(
                json.dumps(
                    {
                        "scope": "sample\\avatar\\Assets",
                        "generated_at_utc": "2026-02-11T10:00:00Z",
                        "seconds": {"avg": 1.0},
                    }
                ),
                encoding="utf-8",
            )
            newer.write_text(
                json.dumps(
                    {
                        "scope": "sample/avatar/Assets",
                        "generated_at_utc": "2026-02-12T10:00:00Z",
                        "seconds": {"avg": 2.0},
                    }
                ),
                encoding="utf-8",
            )

            latest = _pick_latest_by_scope([older, newer])

            self.assertIn("sample/avatar/Assets", latest)
            self.assertEqual(newer, latest["sample/avatar/Assets"][0])

    def test_compare_scope_classifies_regressed(self) -> None:
        result = _compare_scope(
            scope="sample/avatar/Assets",
            baseline_entry=(Path("base.json"), {"seconds": {"avg": 1.0, "p90": 1.0}}),
            latest_entry=(
                Path("latest.json"),
                {
                    "seconds": {"avg": 1.2, "p90": 1.25},
                    "validate_result": {"severity": "error"},
                },
            ),
            avg_ratio_threshold=1.1,
            p90_ratio_threshold=1.1,
            min_absolute_delta_sec=0.1,
        )
        self.assertEqual("regressed", result["status"])
        self.assertEqual(1.2, result["avg_ratio"])
        self.assertEqual(1.25, result["p90_ratio"])

    def test_compare_scope_classifies_improved(self) -> None:
        result = _compare_scope(
            scope="sample/avatar/Assets",
            baseline_entry=(Path("base.json"), {"seconds": {"avg": 1.0, "p90": 1.0}}),
            latest_entry=(Path("latest.json"), {"seconds": {"avg": 0.8, "p90": 0.75}}),
            avg_ratio_threshold=1.1,
            p90_ratio_threshold=1.1,
            min_absolute_delta_sec=0.1,
        )
        self.assertEqual("improved", result["status"])
        self.assertEqual(-0.2, result["avg_delta_sec"])

    def test_sort_results_by_avg_ratio_desc_handles_none(self) -> None:
        results = [
            {"scope": "a", "avg_ratio": None},
            {"scope": "b", "avg_ratio": 1.3},
            {"scope": "c", "avg_ratio": 1.1},
        ]
        sorted_results = _sort_results(results, sort_by="avg_ratio", sort_order="desc")
        self.assertEqual("b", sorted_results[0]["scope"])
        self.assertEqual("a", sorted_results[-1]["scope"])

    def test_render_alert_lines_outputs_regression_only(self) -> None:
        results = [
            {
                "scope": "sample/avatar/Assets",
                "status": "regressed",
                "avg_ratio": 1.2,
                "p90_ratio": 1.1,
                "baseline_source": "base.json",
                "latest_source": "latest.json",
            },
            {"scope": "sample/world/Assets", "status": "stable"},
        ]
        lines = _render_alert_lines(results)
        self.assertEqual(1, len(lines))
        self.assertIn("REGRESSION", lines[0])
        self.assertIn("sample/avatar/Assets", lines[0])

    def test_render_alert_lines_outputs_no_regression_marker(self) -> None:
        lines = _render_alert_lines([{"scope": "sample/world/Assets", "status": "stable"}])
        self.assertEqual(["NO_REGRESSIONS"], lines)

    def test_render_markdown_summary_includes_regression_rows(self) -> None:
        markdown = _render_markdown_summary(
            {
                "baseline_file_count": 3,
                "latest_file_count": 1,
                "compared_scope_count": 2,
                "thresholds": {
                    "avg_ratio_threshold": 1.1,
                    "p90_ratio_threshold": 1.2,
                    "min_absolute_delta_sec": 0.05,
                },
                "regressed_scopes": ["sample/avatar/Assets"],
                "results": [
                    {
                        "scope": "sample/avatar/Assets",
                        "status": "regressed",
                        "avg_ratio": 1.2,
                        "p90_ratio": 1.3,
                        "avg_delta_sec": 0.2,
                        "p90_delta_sec": 0.3,
                    },
                    {
                        "scope": "sample/world/Assets",
                        "status": "stable",
                        "avg_ratio": 1.0,
                        "p90_ratio": 1.0,
                        "avg_delta_sec": 0.0,
                        "p90_delta_sec": 0.0,
                    },
                ],
            }
        )
        self.assertIn("# Benchmark Regression Summary", markdown)
        self.assertIn("- Regressed: 1", markdown)
        self.assertIn("- `sample/avatar/Assets`", markdown)
        self.assertIn("| sample/avatar/Assets | regressed | 1.2 | 1.3 | 0.2 | 0.3 |", markdown)

    def test_write_comparison_csv_supports_append(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "regression.csv"
            rows = [
                {
                    "scope": "sample/avatar/Assets",
                    "status": "stable",
                    "baseline_source": "a.json",
                    "latest_source": "b.json",
                    "baseline_avg_sec": 1.0,
                    "latest_avg_sec": 1.1,
                    "avg_delta_sec": 0.1,
                    "avg_ratio": 1.1,
                    "baseline_p90_sec": 1.0,
                    "latest_p90_sec": 1.1,
                    "p90_delta_sec": 0.1,
                    "p90_ratio": 1.1,
                    "latest_severity": "error",
                }
            ]
            _write_comparison_csv(path, rows, append=False)
            _write_comparison_csv(path, rows, append=True)

            lines = path.read_text(encoding="utf-8").splitlines()
            self.assertEqual(3, len(lines))
            self.assertIn("scope,status,baseline_source", lines[0])


if __name__ == "__main__":
    unittest.main()
