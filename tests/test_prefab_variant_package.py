"""Structural test guarding the prefab_variant package import surface (#75).

The line-count invariant for files under this package is enforced by the
CI-side static gate ``scripts/check_module_line_limits.py``; this file
holds only the package's import-surface invariant.
"""

from __future__ import annotations

import unittest

import prefab_sentinel.services.prefab_variant as prefab_variant_pkg
from prefab_sentinel.services.prefab_variant import PrefabVariantService
from prefab_sentinel.services.prefab_variant.service import (
    PrefabVariantService as _ServicePrefabVariantService,
)


class PrefabVariantPackageImportTests(unittest.TestCase):
    def test_public_surface_preserved(self) -> None:
        """Legacy import path ``from prefab_sentinel.services.prefab_variant import PrefabVariantService`` resolves to the post-split implementation."""
        self.assertIs(PrefabVariantService, _ServicePrefabVariantService)
        self.assertIn("PrefabVariantService", prefab_variant_pkg.__all__)


if __name__ == "__main__":
    unittest.main()
