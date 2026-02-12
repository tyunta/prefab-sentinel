from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from scripts.benchmark_history_to_csv import (
    _build_split_output_path,
    _expand_inputs,
    _group_rows_by_severity,
    _is_benchmark_summary,
    _matches_filters,
    _normalize_severity,
    _pick_latest_per_scope,
    _pick_top_slowest,
    _render_markdown_summary,
    _sort_records,
    _summary_to_row,
)


class BenchmarkHistoryToCsvTests(unittest.TestCase):
    def test_summary_to_row_maps_fields(self) -> None:
        source = Path("bench.json")
        summary = {
            "scope": "sample/avatar/Assets",
            "generated_at_utc": "2026-02-12T10:00:00Z",
            "warmup_runs": 1,
            "runs": 3,
            "seconds": {"avg": 1.2, "p50": 1.2, "p90": 1.3, "min": 1.1, "max": 1.3},
            "validate_result": {
                "success": False,
                "severity": "error",
                "code": "VALIDATE_REFS_RESULT",
            },
        }

        row = _summary_to_row(source, summary)

        self.assertEqual("bench.json", row[0])
        self.assertEqual("sample/avatar/Assets", row[1])
        self.assertEqual("2026-02-12T10:00:00Z", row[2])
        self.assertEqual("1", row[3])
        self.assertEqual("3", row[4])
        self.assertEqual("1.2", row[5])
        self.assertEqual("False", row[10])

    def test_expand_inputs_supports_glob(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            a = root / "a.json"
            b = root / "b.json"
            a.write_text("{}", encoding="utf-8")
            b.write_text("{}", encoding="utf-8")

            paths = _expand_inputs([str(root / "*.json")])

            self.assertEqual(2, len(paths))
            self.assertIn(a.resolve(), paths)
            self.assertIn(b.resolve(), paths)

    def test_expand_inputs_supports_slash_normalized_glob(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            a = root / "a.json"
            b = root / "b.json"
            a.write_text("{}", encoding="utf-8")
            b.write_text("{}", encoding="utf-8")

            pattern = str(root / "*.json").replace("\\", "/")
            paths = _expand_inputs([pattern])

            self.assertEqual(2, len(paths))
            self.assertIn(a.resolve(), paths)
            self.assertIn(b.resolve(), paths)

    def test_matches_filters_by_scope_and_severity(self) -> None:
        summary = {
            "scope": "sample/avatar/Assets",
            "generated_at_utc": "2026-02-12T10:00:00Z",
            "validate_result": {"severity": "error"},
        }

        self.assertTrue(_matches_filters(summary, "avatar", {"error"}, None, None))
        self.assertFalse(_matches_filters(summary, "world", {"error"}, None, None))
        self.assertFalse(_matches_filters(summary, "avatar", {"warning"}, None, None))

    def test_matches_filters_by_generated_date_prefix(self) -> None:
        summary = {
            "generated_at_utc": "2026-02-12T10:00:00Z",
            "validate_result": {"severity": "error"},
        }

        self.assertTrue(_matches_filters(summary, None, {"error"}, "2026-02-12", None))
        self.assertTrue(_matches_filters(summary, None, {"error"}, "2026-02", None))
        self.assertFalse(_matches_filters(summary, None, {"error"}, "2026-02-11", None))
        self.assertFalse(_matches_filters({}, None, set(), "2026-02-12", None))

    def test_matches_filters_by_min_p90(self) -> None:
        summary = {"seconds": {"p90": 2.5}}
        self.assertTrue(_matches_filters(summary, None, set(), None, 2.5))
        self.assertTrue(_matches_filters(summary, None, set(), None, 2.0))
        self.assertFalse(_matches_filters(summary, None, set(), None, 3.0))
        self.assertFalse(_matches_filters({"seconds": {"p90": "bad"}}, None, set(), None, 1.0))

    def test_summary_to_row_can_include_date_column(self) -> None:
        summary = {"generated_at_utc": "2026-02-12T10:00:00Z"}
        row = _summary_to_row(Path("bench.json"), summary, include_date_column=True)
        self.assertEqual("2026-02-12", row[-1])

    def test_summary_to_row_normalizes_scope_separator(self) -> None:
        summary = {"scope": "sample\\avatar\\Assets"}
        row = _summary_to_row(Path("bench.json"), summary)
        self.assertEqual("sample/avatar/Assets", row[1])

    def test_is_benchmark_summary_rejects_non_benchmark_json(self) -> None:
        benchmark_summary = {
            "scope": "sample/avatar/Assets",
            "seconds": {"avg": 1.0},
            "validate_result": {"severity": "error"},
        }
        regression_payload = {
            "results": [],
            "thresholds": {"avg_ratio_threshold": 1.1},
        }
        self.assertTrue(_is_benchmark_summary(benchmark_summary))
        self.assertFalse(_is_benchmark_summary(regression_payload))

    def test_normalize_severity_maps_unknown(self) -> None:
        self.assertEqual("error", _normalize_severity("ERROR"))
        self.assertEqual("unknown", _normalize_severity("notice"))
        self.assertEqual("unknown", _normalize_severity(None))

    def test_sort_records_by_avg_desc(self) -> None:
        records = [
            (Path("a.json"), {"seconds": {"avg": 1.0}}),
            (Path("b.json"), {"seconds": {"avg": 3.0}}),
            (Path("c.json"), {"seconds": {"avg": 2.0}}),
        ]
        sorted_records = _sort_records(records, sort_by="avg_sec", sort_order="desc")
        self.assertEqual(Path("b.json"), sorted_records[0][0])
        self.assertEqual(Path("c.json"), sorted_records[1][0])
        self.assertEqual(Path("a.json"), sorted_records[2][0])

    def test_pick_top_slowest_uses_avg_desc(self) -> None:
        records = [
            (Path("a.json"), {"seconds": {"avg": 1.0}}),
            (Path("b.json"), {"seconds": {"avg": 3.0}}),
            (Path("c.json"), {"seconds": {"avg": 2.0}}),
        ]
        top_records = _pick_top_slowest(records, top_n=2)
        self.assertEqual(2, len(top_records))
        self.assertEqual(Path("b.json"), top_records[0][0])
        self.assertEqual(Path("c.json"), top_records[1][0])

    def test_pick_latest_per_scope_uses_generated_at(self) -> None:
        records = [
            (
                Path("a.json"),
                {"scope": "sample/avatar/Assets", "generated_at_utc": "2026-02-11T10:00:00Z"},
            ),
            (
                Path("b.json"),
                {"scope": "sample\\avatar\\Assets", "generated_at_utc": "2026-02-12T10:00:00Z"},
            ),
            (
                Path("c.json"),
                {"scope": "sample/world/Assets", "generated_at_utc": "2026-02-10T10:00:00Z"},
            ),
        ]
        latest = _pick_latest_per_scope(records)
        self.assertEqual(2, len(latest))
        latest_sources = {entry[0] for entry in latest}
        self.assertIn(Path("b.json"), latest_sources)
        self.assertIn(Path("c.json"), latest_sources)

    def test_build_split_output_path_appends_severity_suffix(self) -> None:
        out = _build_split_output_path(Path("reports/benchmark_trend.csv"), "error")
        self.assertEqual(Path("reports/benchmark_trend_error.csv"), out)

    def test_group_rows_by_severity_collects_rows(self) -> None:
        records = [
            (Path("a.json"), {"validate_result": {"severity": "error"}}),
            (Path("b.json"), {"validate_result": {"severity": "warning"}}),
            (Path("c.json"), {"validate_result": {"severity": "notice"}}),
        ]
        rows = [["a"], ["b"], ["c"]]
        grouped = _group_rows_by_severity(records, rows)
        self.assertEqual([["a"]], grouped["error"])
        self.assertEqual([["b"]], grouped["warning"])
        self.assertEqual([["c"]], grouped["unknown"])

    def test_render_markdown_summary_includes_counts_and_top_rows(self) -> None:
        records = [
            (
                Path("a.json"),
                {
                    "scope": "sample/avatar/Assets",
                    "generated_at_utc": "2026-02-12T10:00:00Z",
                    "seconds": {"avg": 2.0, "p90": 2.5},
                    "validate_result": {"severity": "error"},
                },
            ),
            (
                Path("b.json"),
                {
                    "scope": "sample/world/Assets",
                    "generated_at_utc": "2026-02-11T10:00:00Z",
                    "seconds": {"avg": 1.0, "p90": 1.2},
                    "validate_result": {"severity": "warning"},
                },
            ),
        ]
        md = _render_markdown_summary(records)
        self.assertIn("# Benchmark Trend Snapshot", md)
        self.assertIn("| error | 1 |", md)
        self.assertIn("| warning | 1 |", md)
        self.assertIn("sample/avatar/Assets", md)


if __name__ == "__main__":
    unittest.main()
