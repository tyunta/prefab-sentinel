from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from scripts.benchmark_samples import (
    _build_benchmark_refs_command,
    _build_history_command,
    _build_regression_command,
    _discover_baseline_inputs,
    _resolve_targets,
)


class BenchmarkSamplesTests(unittest.TestCase):
    def test_resolve_targets_expands_all(self) -> None:
        self.assertEqual(["avatar", "world"], _resolve_targets(["all"]))
        self.assertEqual(["avatar", "world"], _resolve_targets(["world", "all"]))

    def test_resolve_targets_deduplicates(self) -> None:
        self.assertEqual(["world", "avatar"], _resolve_targets(["world", "avatar", "world"]))

    def test_build_benchmark_refs_command_includes_passthrough_flags(self) -> None:
        cmd = _build_benchmark_refs_command(
            scope=Path("sample/avatar/Assets"),
            out_json=Path("sample/avatar/config/bench_x.json"),
            out_csv=Path("sample/avatar/config/benchmark_refs.csv"),
            runs=2,
            warmup_runs=1,
            csv_append=True,
            include_generated_date=True,
            excludes=["**/Generated/**"],
            ignore_guids=["aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"],
            ignore_guid_file="config/ignore_guids.txt",
        )
        self.assertIn("--scope", cmd)
        self.assertIn(Path("sample/avatar/Assets").as_posix(), cmd)
        self.assertIn("--csv-append", cmd)
        self.assertIn("--include-generated-date", cmd)
        self.assertIn("**/Generated/**", cmd)
        self.assertIn("aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", cmd)
        self.assertIn("config/ignore_guids.txt", cmd)

    def test_build_history_command_includes_date_prefix(self) -> None:
        cmd = _build_history_command(
            inputs_glob="sample/avatar/config/bench_*.json",
            out_csv=Path("sample/avatar/config/benchmark_trend.csv"),
            out_md=Path("sample/avatar/config/benchmark_trend.md"),
            generated_date_prefix="2026-02",
            min_p90=2.0,
            latest_per_scope=True,
            split_by_severity=True,
        )
        self.assertIn("--inputs", cmd)
        self.assertIn("sample/avatar/config/bench_*.json", cmd)
        self.assertIn("--generated-date-prefix", cmd)
        self.assertIn("2026-02", cmd)
        self.assertIn("--min-p90", cmd)
        self.assertIn("2.0", cmd)
        self.assertIn("--latest-per-scope", cmd)
        self.assertIn("--split-by-severity", cmd)
        self.assertIn("--out-md", cmd)
        self.assertIn(str(Path("sample/avatar/config/benchmark_trend.md")), cmd)

    def test_build_regression_command_includes_thresholds(self) -> None:
        cmd = _build_regression_command(
            baseline_inputs=["sample/avatar/config/bench_20260211*.json"],
            latest_input=Path("sample/avatar/config/bench_latest.json"),
            out_json=Path("sample/avatar/config/benchmark_regression.json"),
            out_csv=Path("sample/avatar/config/benchmark_regression.csv"),
            out_md=Path("sample/avatar/config/benchmark_regression.md"),
            baseline_pinning_file="sample/avatar/config/baseline_pinning.json",
            avg_ratio_threshold=1.1,
            p90_ratio_threshold=1.2,
            min_absolute_delta_sec=0.05,
            alerts_only=True,
            fail_on_regression=True,
            out_csv_append=True,
        )
        self.assertIn("--baseline-inputs", cmd)
        self.assertIn("sample/avatar/config/bench_20260211*.json", cmd)
        self.assertIn("--latest-inputs", cmd)
        self.assertIn(str(Path("sample/avatar/config/bench_latest.json")), cmd)
        self.assertIn("--avg-ratio-threshold", cmd)
        self.assertIn("1.1", cmd)
        self.assertIn("--p90-ratio-threshold", cmd)
        self.assertIn("1.2", cmd)
        self.assertIn("--min-absolute-delta-sec", cmd)
        self.assertIn("0.05", cmd)
        self.assertIn("--out-md", cmd)
        self.assertIn(str(Path("sample/avatar/config/benchmark_regression.md")), cmd)
        self.assertIn("--baseline-pinning-file", cmd)
        self.assertIn("sample/avatar/config/baseline_pinning.json", cmd)
        self.assertIn("--alerts-only", cmd)
        self.assertIn("--fail-on-regression", cmd)
        self.assertIn("--out-csv-append", cmd)

    def test_discover_baseline_inputs_skips_current_and_regression(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_dir = Path(temp_dir)
            old = config_dir / "bench_old.json"
            latest = config_dir / "bench_latest.json"
            current = config_dir / "bench_current.json"
            regression = config_dir / "bench_regression.json"
            for path in (old, latest, current, regression):
                path.write_text("{}", encoding="utf-8")

            old.touch()
            latest.touch()
            current.touch()
            regression.touch()

            discovered = _discover_baseline_inputs(
                config_dir=config_dir,
                current_bench_json=current,
                limit=2,
            )
            self.assertIn(str(latest), discovered)
            self.assertIn(str(old), discovered)
            self.assertNotIn(str(current), discovered)
            self.assertNotIn(str(regression), discovered)


if __name__ == "__main__":
    unittest.main()
