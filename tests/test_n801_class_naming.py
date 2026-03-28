"""Verify test class names in test_vrcsdk_upload_handler_source.py follow PEP 8 CapWords (N801)."""

from __future__ import annotations

import ast
import shutil
import subprocess
import unittest
from pathlib import Path

TARGET_FILE = (
    Path(__file__).resolve().parent / "test_vrcsdk_upload_handler_source.py"
)


class TestClassNamingConvention(unittest.TestCase):
    """All test classes in the target file must use CapWords naming."""

    def test_no_underscore_in_class_names(self) -> None:
        """Every class name must match CapWords (no internal underscores)."""
        source = TARGET_FILE.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(TARGET_FILE))

        violations: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) and "_" in node.name:
                violations.append(f"line {node.lineno}: {node.name}")

        self.assertEqual(
            violations,
            [],
            f"Classes with underscores violate N801:\n" + "\n".join(violations),
        )

    def test_expected_class_names_exist(self) -> None:
        """The 3 renamed classes must exist with their CapWords names."""
        source = TARGET_FILE.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(TARGET_FILE))

        class_names = {
            node.name for node in ast.walk(tree) if isinstance(node, ast.ClassDef)
        }

        expected = {
            "TestCS0117GetBuildTargetGroup",
            "TestCS1501BuildErrorVisibility",
            "TestCS0246BuildAndUploadWorldReflection",
        }
        missing = expected - class_names
        self.assertEqual(
            missing,
            set(),
            f"Expected classes not found: {missing}",
        )

    def test_old_class_names_absent(self) -> None:
        """The 3 old underscore-style names must no longer exist."""
        source = TARGET_FILE.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(TARGET_FILE))

        class_names = {
            node.name for node in ast.walk(tree) if isinstance(node, ast.ClassDef)
        }

        old_names = {
            "TestCS0117_GetBuildTargetGroup",
            "TestCS1501_BuildErrorVisibility",
            "TestCS0246_BuildAndUploadWorldReflection",
        }
        remaining = old_names & class_names
        self.assertEqual(
            remaining,
            set(),
            f"Old class names still present: {remaining}",
        )

    def test_ruff_n801_passes(self) -> None:
        """ruff check --select N801 must report zero violations."""
        ruff_bin = shutil.which("ruff")
        if ruff_bin is None:
            self.skipTest("ruff not found in PATH; install with: uv sync --extra lint")
        result = subprocess.run(
            [ruff_bin, "check", "--select", "N801", str(TARGET_FILE)],
            capture_output=True,
            text=True,
        )
        self.assertEqual(
            result.returncode,
            0,
            f"ruff N801 violations found:\n{result.stdout}{result.stderr}",
        )


if __name__ == "__main__":
    unittest.main()
