"""Tests for orchestrator_validation.validate_refs missing-GUID contract (#83)."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from prefab_sentinel.orchestrator_validation import validate_refs
from prefab_sentinel.services.reference_resolver import ReferenceResolverService
from tests.bridge_test_helpers import write_file

BASE_GUID = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
VARIANT_GUID = "cccccccccccccccccccccccccccccccc"
MISSING_GUID = "ffffffffffffffffffffffffffffffff"


def _create_project_with_missing_guid(root: Path) -> None:
    write_file(
        root / "Assets" / "Base.prefab",
        """%YAML 1.1
--- !u!1 &100100000
GameObject:
  m_Name: Base
""",
    )
    write_file(
        root / "Assets" / "Base.prefab.meta",
        f"""fileFormatVersion: 2
guid: {BASE_GUID}
""",
    )
    write_file(
        root / "Assets" / "Variant.prefab",
        f"""%YAML 1.1
--- !u!1001 &100100000
PrefabInstance:
  m_SourcePrefab: {{fileID: 100100000, guid: {BASE_GUID}, type: 3}}
  m_Modification:
    m_Modifications:
    - target: {{fileID: 100100000, guid: {MISSING_GUID}, type: 3}}
      propertyPath: missing.ref
      value: 0
      objectReference: {{fileID: 0}}
""",
    )
    write_file(
        root / "Assets" / "Variant.prefab.meta",
        f"""fileFormatVersion: 2
guid: {VARIANT_GUID}
""",
    )


def _create_clean_project(root: Path) -> None:
    write_file(
        root / "Assets" / "Base.prefab",
        """%YAML 1.1
--- !u!1 &100100000
GameObject:
  m_Name: Base
""",
    )
    write_file(
        root / "Assets" / "Base.prefab.meta",
        f"""fileFormatVersion: 2
guid: {BASE_GUID}
""",
    )


class MissingGuidContractTests(unittest.TestCase):
    """T24: ``validate_refs`` surfaces top-level REF001 when any referenced
    GUID is not resolvable in the project map."""

    def test_validate_refs_returns_ref001(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            _create_project_with_missing_guid(root)

            resolver = ReferenceResolverService(project_root=root)
            response = validate_refs(resolver, scope="Assets")

            self.assertFalse(response.success)
            self.assertEqual("REF001", response.code)
            self.assertEqual("error", response.severity.value)
            # The underlying scan step remains visible in data.steps.
            step_codes = [step["result"]["code"] for step in response.data["steps"]]
            self.assertIn("REF_SCAN_BROKEN", step_codes)
            self.assertGreaterEqual(response.data["missing_asset_unique_count"], 1)

    def test_validate_refs_clean_scan_returns_validate_refs_result(self) -> None:
        """Regression guard: clean scan must still return VALIDATE_REFS_RESULT."""
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            _create_clean_project(root)

            resolver = ReferenceResolverService(project_root=root)
            response = validate_refs(resolver, scope="Assets")

            self.assertTrue(response.success)
            self.assertEqual("VALIDATE_REFS_RESULT", response.code)
            self.assertEqual(0, response.data["missing_asset_unique_count"])


if __name__ == "__main__":
    unittest.main()
