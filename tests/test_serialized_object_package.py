"""Structural tests guarding the serialized_object package split (issue #91).

These tests pin the architectural invariants of the post-split package:

* the public API surface of ``prefab_sentinel.services.serialized_object``
  exposes exactly ``SerializedObjectService`` (no sibling module
  re-exported, no deprecation shim);
* every ``*.py`` under the package stays within the 300-line hard
  limit — including ``service.py`` itself after the Phase 2+ carve-out.
"""

from __future__ import annotations

import unittest
from pathlib import Path

import prefab_sentinel.services.serialized_object as serialized_object_pkg
from prefab_sentinel.services.serialized_object import SerializedObjectService
from prefab_sentinel.services.serialized_object.service import (
    SerializedObjectService as _ServiceSerializedObjectService,
)

PACKAGE_DIR = Path(serialized_object_pkg.__file__).parent
LINE_LIMIT = 300


class SerializedObjectPackageSurfaceTests(unittest.TestCase):
    """T-91-SURFACE: package exports exactly ``SerializedObjectService``."""

    def test_public_surface_preserved(self) -> None:
        """Legacy import path resolves to the post-split implementation."""
        self.assertIs(SerializedObjectService, _ServiceSerializedObjectService)

    def test_all_exports_service_class_only(self) -> None:
        """``__all__`` lists exactly ``SerializedObjectService``; no
        sibling module or private helper is re-exported from the
        package root."""
        self.assertEqual(
            ("SerializedObjectService",),
            tuple(serialized_object_pkg.__all__),
        )


class SerializedObjectPackageLimitTests(unittest.TestCase):
    """T-91-LIMIT: every ``*.py`` under the subtree is ≤300 lines."""

    def test_every_module_line_limit(self) -> None:
        self.assertTrue(PACKAGE_DIR.is_dir(), f"Package dir missing: {PACKAGE_DIR}")
        modules = sorted(PACKAGE_DIR.glob("*.py"))
        self.assertGreater(len(modules), 0, "Expected at least one module")

        oversized: list[tuple[str, int]] = []
        for module_path in modules:
            with module_path.open(encoding="utf-8") as handle:
                line_count = sum(1 for _ in handle)
            if line_count > LINE_LIMIT:
                oversized.append((module_path.name, line_count))

        self.assertEqual(
            oversized,
            [],
            f"Modules exceed {LINE_LIMIT}-line limit: {oversized}",
        )


if __name__ == "__main__":
    unittest.main()
