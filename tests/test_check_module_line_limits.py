"""Unit tests for ``scripts.check_module_line_limits``."""

from __future__ import annotations

import io
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from scripts.check_module_line_limits import DEFAULT_LIMIT, DEFAULT_PACKAGE_ROOTS, check, main


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


class DefaultLimitBoundaryTests(unittest.TestCase):
    """±1 boundary triplet (issue #179) around the
    ``check(..., limit=DEFAULT_LIMIT)`` default-parameter cap.

    Each test deliberately omits ``limit=`` so the default literal is
    the value exercised; a mutation that flips ``DEFAULT_LIMIT`` (e.g.
    300 → 299 or 300 → 301) trips one boundary test.
    """

    def _file_with_lines(self, root: Path, line_count: int) -> Path:
        path = root / f"file_{line_count}.py"
        # ``sum(1 for _ in handle)`` counts ``line_count`` newline-
        # terminated lines.  We write exactly ``line_count`` newlines so
        # ``check`` observes ``line_count`` as the per-file count.
        path.write_text("\n" * line_count, encoding="utf-8")
        return path

    def test_cap_minus_one_lines_is_not_reported(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            package = Path(raw)
            self._file_with_lines(package, DEFAULT_LIMIT - 1)
            offenders = check([package])
        self.assertEqual([], offenders)

    def test_exactly_cap_lines_is_not_reported(self) -> None:
        # The cap is inclusive: ``line_count > limit`` flips at
        # ``DEFAULT_LIMIT + 1``, so an exact-cap file is permitted.
        with tempfile.TemporaryDirectory() as raw:
            package = Path(raw)
            self._file_with_lines(package, DEFAULT_LIMIT)
            offenders = check([package])
        self.assertEqual([], offenders)

    def test_cap_plus_one_lines_is_reported_with_observed_count(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            package = Path(raw)
            path = self._file_with_lines(package, DEFAULT_LIMIT + 1)
            offenders = check([package])
        self.assertEqual(
            [(str(path), DEFAULT_LIMIT + 1)],
            offenders,
        )


class InspectorModuleBoundaryTests(unittest.TestCase):
    def test_default_roots_include_inspector_packages(self) -> None:
        package_names = {root.name for root in DEFAULT_PACKAGE_ROOTS}
        self.assertEqual(
            {
                "effective_hierarchy",
                "effective_transform_inspector",
                "unity_event_listener_inspector",
            },
            {
                name
                for name in package_names
                if name
                in {
                    "effective_hierarchy",
                    "effective_transform_inspector",
                    "unity_event_listener_inspector",
                }
            },
        )

    def test_split_inspector_modules_stay_under_default_limit(self) -> None:
        inspector_roots = [
            root
            for root in DEFAULT_PACKAGE_ROOTS
            if root.name
            in {
                "effective_hierarchy",
                "effective_transform_inspector",
                "unity_event_listener_inspector",
            }
        ]
        offenders = check(inspector_roots, limit=DEFAULT_LIMIT)
        self.assertEqual([], offenders)


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
