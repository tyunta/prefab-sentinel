"""Verify test class names in test_vrcsdk_upload_handler_source conform to PEP 8 CapWords (ruff N801)."""

from __future__ import annotations

import shutil
import subprocess
import unittest
from pathlib import Path

TARGET_FILE = Path("tests/test_vrcsdk_upload_handler_source.py")

EXPECTED_CLASSES = (
    "TestCs0117GetBuildTargetGroup",
    "TestCs1501BuildErrorVisibility",
    "TestCs0246BuildAndUploadWorldReflection",
)

REMOVED_CLASSES = (
    "TestCS0117_GetBuildTargetGroup",
    "TestCS1501_BuildErrorVisibility",
    "TestCS0246_BuildAndUploadWorldReflection",
)


def _read_source() -> str:
    return TARGET_FILE.read_text(encoding="utf-8")


def _find_ruff() -> str:
    """Locate ruff binary via PATH or uvx wrapper."""
    ruff = shutil.which("ruff")
    if ruff:
        return ruff
    uvx = shutil.which("uvx")
    if uvx is None:
        raise FileNotFoundError("Neither ruff nor uvx found on PATH")
    return uvx


class TestVrcsdkUploadHandlerSourceClassNames(unittest.TestCase):
    """Ensure the 3 renamed classes exist and old names are removed."""

    def test_expected_class_names_exist(self) -> None:
        """Each renamed class definition must be present in the source file."""
        source = _read_source()
        for name in EXPECTED_CLASSES:
            self.assertIn(
                f"class {name}(",
                source,
                f"Expected class {name!r} not found in {TARGET_FILE}",
            )

    def test_old_class_names_removed(self) -> None:
        """Old non-CapWords class names must no longer appear as class definitions."""
        source = _read_source()
        for name in REMOVED_CLASSES:
            self.assertNotIn(
                f"class {name}(",
                source,
                f"Old class {name!r} still present in {TARGET_FILE}",
            )

    def test_ruff_n801_passes(self) -> None:
        """ruff check --select N801 must report zero violations on the target file."""
        ruff = _find_ruff()
        # uvx needs "ruff" as first arg; direct ruff binary does not
        cmd = [ruff, "check", "--select", "N801", str(TARGET_FILE)]
        if ruff.endswith("uvx"):
            cmd = [ruff, "ruff", "check", "--select", "N801", str(TARGET_FILE)]
        result = subprocess.run(cmd, capture_output=True, text=True)
        self.assertEqual(
            result.returncode,
            0,
            f"ruff N801 violations found:\n{result.stdout}{result.stderr}",
        )


if __name__ == "__main__":
    unittest.main()
