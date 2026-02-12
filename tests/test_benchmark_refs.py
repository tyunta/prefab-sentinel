from __future__ import annotations

import argparse
import tempfile
import unittest
from pathlib import Path

from scripts.benchmark_refs import (
    _build_command,
    _normalize_run_counts,
    _summary_to_csv_row,
    _write_summary_csv,
)


class BenchmarkRefsTests(unittest.TestCase):
    def test_build_command_includes_optional_flags(self) -> None:
        args = argparse.Namespace(
            scope="sample/avatar/Assets",
            exclude=["**/Generated/**"],
            ignore_guid=["aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"],
            ignore_guid_file="config/ignore_guids.txt",
        )

        command = _build_command(args)

        self.assertIn("--scope", command)
        self.assertIn("sample/avatar/Assets", command)
        self.assertIn("--exclude", command)
        self.assertIn("**/Generated/**", command)
        self.assertIn("--ignore-guid", command)
        self.assertIn("aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", command)
        self.assertIn("--ignore-guid-file", command)
        self.assertIn("config/ignore_guids.txt", command)

    def test_summary_to_csv_row_maps_fields(self) -> None:
        summary = {
            "scope": "sample/avatar/Assets",
            "warmup_runs": 1,
            "runs": 3,
            "seconds": {"avg": 1.25, "min": 1.0, "max": 1.5},
            "validate_result": {
                "success": False,
                "severity": "error",
                "code": "VALIDATE_REFS_RESULT",
            },
        }

        row = _summary_to_csv_row(summary)

        self.assertEqual("sample/avatar/Assets", row[0])
        self.assertEqual("1", row[1])
        self.assertEqual("3", row[2])
        self.assertEqual("1.25", row[3])
        self.assertEqual("False", row[6])
        self.assertEqual("error", row[7])
        self.assertEqual("VALIDATE_REFS_RESULT", row[8])

    def test_write_summary_csv_overwrite_and_append(self) -> None:
        summary = {
            "scope": "sample/avatar/Assets",
            "runs": 1,
            "seconds": {"avg": 2.0, "min": 2.0, "max": 2.0},
            "validate_result": {
                "success": True,
                "severity": "warning",
                "code": "VALIDATE_REFS_RESULT",
            },
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "bench.csv"

            _write_summary_csv(path, summary, append=False)
            _write_summary_csv(path, summary, append=True)

            lines = path.read_text(encoding="utf-8").splitlines()
            self.assertEqual(3, len(lines))
            self.assertIn(
                "scope,warmup_runs,runs,avg_sec,min_sec,max_sec,success,severity,code",
                lines[0],
            )

    def test_normalize_run_counts(self) -> None:
        runs, warmup = _normalize_run_counts(runs=0, warmup_runs=-2)
        self.assertEqual(1, runs)
        self.assertEqual(0, warmup)


if __name__ == "__main__":
    unittest.main()
