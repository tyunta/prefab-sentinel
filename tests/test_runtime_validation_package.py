"""Structural test guarding the runtime_validation package import surface (#90).

The line-count invariant for files under this package is enforced by the
CI-side static gate ``scripts/check_module_line_limits.py``; this file
holds only the package's import-surface invariant.
"""

from __future__ import annotations

import unittest

import prefab_sentinel.services.runtime_validation as runtime_validation_pkg
from prefab_sentinel.services.runtime_validation import RuntimeValidationService
from prefab_sentinel.services.runtime_validation.service import (
    RuntimeValidationService as _ServiceRuntimeValidationService,
)


class RuntimeValidationPackageImportTests(unittest.TestCase):
    def test_public_surface_preserved(self) -> None:
        """Legacy import path resolves to the post-split implementation."""
        self.assertIs(RuntimeValidationService, _ServiceRuntimeValidationService)
        self.assertIn("RuntimeValidationService", runtime_validation_pkg.__all__)


if __name__ == "__main__":
    unittest.main()
