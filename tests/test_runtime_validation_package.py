"""Structural tests guarding the runtime_validation package split (#90).

These tests pin the two architectural invariants introduced when
``runtime_validation.py`` was split into a package:

* the public API surface (``RuntimeValidationService``) continues to
  resolve from the canonical import path (call-site compatibility);
* every ``.py`` module in the package stays within the project's
  300-line file size hard limit.
"""

from __future__ import annotations

import unittest
from pathlib import Path

import prefab_sentinel.services.runtime_validation as runtime_validation_pkg
from prefab_sentinel.services.runtime_validation import RuntimeValidationService
from prefab_sentinel.services.runtime_validation.service import (
    RuntimeValidationService as _ServiceRuntimeValidationService,
)

PACKAGE_DIR = Path(runtime_validation_pkg.__file__).parent
LINE_LIMIT = 300


class RuntimeValidationPackageImportTests(unittest.TestCase):
    def test_public_surface_preserved(self) -> None:
        """Legacy import path resolves to the post-split implementation."""
        self.assertIs(RuntimeValidationService, _ServiceRuntimeValidationService)
        self.assertIn("RuntimeValidationService", runtime_validation_pkg.__all__)

    def test_module_line_limits(self) -> None:
        """Each ``.py`` file in the package stays within the 300-line hard limit."""
        self.assertTrue(PACKAGE_DIR.is_dir(), f"Package dir missing: {PACKAGE_DIR}")
        modules = sorted(PACKAGE_DIR.glob("*.py"))
        self.assertGreater(len(modules), 0, "Package contains no .py modules")

        oversized: list[tuple[str, int]] = []
        for module_path in modules:
            line_count = sum(1 for _ in module_path.open(encoding="utf-8"))
            if line_count > LINE_LIMIT:
                oversized.append((module_path.name, line_count))

        self.assertEqual(
            oversized,
            [],
            f"Modules exceed {LINE_LIMIT}-line limit: {oversized}",
        )


if __name__ == "__main__":
    unittest.main()
