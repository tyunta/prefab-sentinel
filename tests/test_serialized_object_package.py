"""Structural tests guarding the serialized_object package split (#91 Phase 1).

These tests pin the architectural invariants introduced when the
prefab-create validation subtree was carved out of the monolithic
``serialized_object.py``:

* the public API surface (``SerializedObjectService``) continues to
  resolve from the canonical import path (call-site compatibility);
* every ``prefab_create_*.py`` helper module stays within the project's
  300-line file size hard limit.

The carve-out is staged: ``service.py`` itself is intentionally left
out of the line-limit invariant and will be split in later phases that
extract the asset / scene / patch subtrees.
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


class SerializedObjectPackageImportTests(unittest.TestCase):
    def test_public_surface_preserved(self) -> None:
        """Legacy import path resolves to the post-split implementation."""
        self.assertIs(SerializedObjectService, _ServiceSerializedObjectService)
        self.assertIn("SerializedObjectService", serialized_object_pkg.__all__)

    def test_prefab_create_module_line_limits(self) -> None:
        """Each ``prefab_create_*.py`` helper stays within the 300-line hard limit.

        ``service.py`` is not enforced here because Phase 1 only carves
        out the prefab-create subtree; subsequent phases (#91 Phase 2+)
        will extract the asset / scene / patch validators and bring
        ``service.py`` under the same limit.
        """
        self.assertTrue(PACKAGE_DIR.is_dir(), f"Package dir missing: {PACKAGE_DIR}")
        modules = sorted(PACKAGE_DIR.glob("prefab_create_*.py"))
        self.assertGreater(
            len(modules),
            0,
            "Expected at least one prefab_create_*.py helper module",
        )

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
