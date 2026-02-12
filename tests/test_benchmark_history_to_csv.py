from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from scripts.benchmark_history_to_csv import _expand_inputs, _summary_to_row


class BenchmarkHistoryToCsvTests(unittest.TestCase):
    def test_summary_to_row_maps_fields(self) -> None:
        source = Path("bench.json")
        summary = {
            "scope": "sample/avatar/Assets",
            "warmup_runs": 1,
            "runs": 3,
            "seconds": {"avg": 1.2, "min": 1.1, "max": 1.3},
            "validate_result": {
                "success": False,
                "severity": "error",
                "code": "VALIDATE_REFS_RESULT",
            },
        }

        row = _summary_to_row(source, summary)

        self.assertEqual("bench.json", row[0])
        self.assertEqual("sample/avatar/Assets", row[1])
        self.assertEqual("1", row[2])
        self.assertEqual("3", row[3])
        self.assertEqual("1.2", row[4])
        self.assertEqual("False", row[7])

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


if __name__ == "__main__":
    unittest.main()
