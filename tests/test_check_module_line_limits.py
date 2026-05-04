"""Unit tests for ``scripts.check_module_line_limits``."""

from __future__ import annotations

import io
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from scripts.check_module_line_limits import check, main


class CheckFunctionTests(unittest.TestCase):
    def test_clean_tree_returns_empty_offender_list(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            package = Path(raw)
            (package / "small.py").write_text("x = 1\n", encoding="utf-8")
            (package / "medium.py").write_text("\n" * 200, encoding="utf-8")
            offenders = check([package], limit=300)
        self.assertEqual([], offenders)

    def test_one_over_limit_file_is_reported(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            package = Path(raw)
            big = package / "huge.py"
            big.write_text("\n" * 350, encoding="utf-8")
            offenders = check([package], limit=300)
        self.assertEqual(1, len(offenders))
        path, line_count = offenders[0]
        self.assertEqual(str(big), path)
        self.assertEqual(350, line_count)

    def test_missing_package_dir_raises(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            missing = Path(raw) / "no_such_dir"
            with self.assertRaises(FileNotFoundError):
                check([missing], limit=300)


class MainCliTests(unittest.TestCase):
    def test_cli_exits_zero_on_clean_tree(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            package = Path(raw)
            (package / "small.py").write_text("\n" * 10, encoding="utf-8")
            with redirect_stdout(io.StringIO()) as captured:
                exit_code = main(["--root", str(package), "--limit", "300"])
        self.assertEqual(0, exit_code)
        self.assertEqual("", captured.getvalue())

    def test_cli_prints_offenders_and_returns_nonzero(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            package = Path(raw)
            big = package / "huge.py"
            big.write_text("\n" * 350, encoding="utf-8")
            with redirect_stdout(io.StringIO()) as captured:
                exit_code = main(["--root", str(package), "--limit", "300"])
        self.assertEqual(1, exit_code)
        output = captured.getvalue()
        self.assertIn(str(big), output)
        self.assertIn("350", output)
        self.assertIn("limit 300", output)


if __name__ == "__main__":
    unittest.main()
