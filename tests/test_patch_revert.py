from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from prefab_sentinel.patch_revert import revert_overrides
from tests.bridge_test_helpers import write_file

BASE_GUID = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
VARIANT_GUID = "cccccccccccccccccccccccccccccccc"


def _create_variant_project(root: Path) -> None:
    """Create a minimal project with a base prefab and a variant."""
    write_file(
        root / "Assets" / "Base.prefab",
        """%YAML 1.1
--- !u!1 &100100000
GameObject:
  m_Name: Base
--- !u!137 &3430728864525902586
SkinnedMeshRenderer:
  m_GameObject: {fileID: 100100000}
  m_Materials:
  - {fileID: 2100000, guid: 11111111111111111111111111111111, type: 2}
  - {fileID: 2100000, guid: 22222222222222222222222222222222, type: 2}
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
    - target: {{fileID: 3430728864525902586, guid: {BASE_GUID}, type: 3}}
      propertyPath: m_Materials.Array.data[0]
      value:
      objectReference: {{fileID: 2100000, guid: aaaaaaaabbbbbbbbccccccccdddddddd, type: 2}}
    - target: {{fileID: 3430728864525902586, guid: {BASE_GUID}, type: 3}}
      propertyPath: m_Materials.Array.data[1]
      value:
      objectReference: {{fileID: 2100000, guid: eeeeeeeeffffffffffaaaaaabbbbbbbb, type: 2}}
    - target: {{fileID: 100100000, guid: {BASE_GUID}, type: 3}}
      propertyPath: m_Name
      value: VariantName
      objectReference: {{fileID: 0}}
""",
    )
    write_file(
        root / "Assets" / "Variant.prefab.meta",
        f"""fileFormatVersion: 2
guid: {VARIANT_GUID}
""",
    )


class PatchRevertTests(unittest.TestCase):
    """Test the patch_revert module."""

    def test_dry_run_shows_matches(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            _create_variant_project(root)

            response = revert_overrides(
                variant_path="Assets/Variant.prefab",
                target_file_id="3430728864525902586",
                property_path="m_Materials.Array.data[0]",
                dry_run=True,
                confirm=False,
                change_reason=None,
                project_root=root,
            )

        self.assertTrue(response.success)
        self.assertEqual("REVERT_DRY_RUN", response.code)
        self.assertEqual(1, response.data["match_count"])
        self.assertTrue(response.data["read_only"])
        # Check that the match info includes the current value
        match = response.data["matches"][0]
        self.assertEqual("3430728864525902586", match["target_file_id"])
        self.assertEqual("m_Materials.Array.data[0]", match["property_path"])

    def test_dry_run_no_match(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            _create_variant_project(root)

            response = revert_overrides(
                variant_path="Assets/Variant.prefab",
                target_file_id="9999999999",
                property_path="m_Materials.Array.data[0]",
                dry_run=True,
                confirm=False,
                change_reason=None,
                project_root=root,
            )

        self.assertFalse(response.success)
        self.assertEqual("REVERT_NO_MATCH", response.code)
        self.assertEqual(0, response.data["match_count"])

    def test_confirm_without_flag_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            _create_variant_project(root)

            response = revert_overrides(
                variant_path="Assets/Variant.prefab",
                target_file_id="3430728864525902586",
                property_path="m_Materials.Array.data[0]",
                dry_run=False,
                confirm=False,
                change_reason=None,
                project_root=root,
            )

        self.assertFalse(response.success)
        self.assertEqual("REVERT_NOT_CONFIRMED", response.code)

    def test_confirm_removes_override(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            _create_variant_project(root)

            variant_path = root / "Assets" / "Variant.prefab"
            original_text = variant_path.read_text(encoding="utf-8")
            self.assertIn("m_Materials.Array.data[0]", original_text)

            response = revert_overrides(
                variant_path="Assets/Variant.prefab",
                target_file_id="3430728864525902586",
                property_path="m_Materials.Array.data[0]",
                dry_run=False,
                confirm=True,
                change_reason="Revert accidental material change",
                project_root=root,
            )

            self.assertTrue(response.success)
            self.assertEqual("REVERT_APPLIED", response.code)
            self.assertEqual(1, response.data["match_count"])
            self.assertFalse(response.data["read_only"])
            self.assertTrue(response.data["executed"])
            self.assertEqual(
                "Revert accidental material change", response.data["change_reason"]
            )

            # Verify the file was actually modified
            new_text = variant_path.read_text(encoding="utf-8")
            self.assertNotIn("m_Materials.Array.data[0]", new_text)
            # The other overrides should still be present
            self.assertIn("m_Materials.Array.data[1]", new_text)
            self.assertIn("m_Name", new_text)

    def test_confirm_removes_only_matching_override(self) -> None:
        """Removing one material slot override should leave others intact."""
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            _create_variant_project(root)

            variant_path = root / "Assets" / "Variant.prefab"

            response = revert_overrides(
                variant_path="Assets/Variant.prefab",
                target_file_id="3430728864525902586",
                property_path="m_Materials.Array.data[1]",
                dry_run=False,
                confirm=True,
                change_reason="Revert slot 1",
                project_root=root,
            )

            self.assertTrue(response.success)
            self.assertEqual("REVERT_APPLIED", response.code)

            new_text = variant_path.read_text(encoding="utf-8")
            # data[0] should remain, data[1] should be gone
            self.assertIn("m_Materials.Array.data[0]", new_text)
            self.assertNotIn("m_Materials.Array.data[1]", new_text)
            self.assertIn("m_Name", new_text)

    def test_confirm_preserves_yaml_structure(self) -> None:
        """After revert, the YAML should still be valid Unity YAML."""
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            _create_variant_project(root)

            variant_path = root / "Assets" / "Variant.prefab"

            revert_overrides(
                variant_path="Assets/Variant.prefab",
                target_file_id="3430728864525902586",
                property_path="m_Materials.Array.data[0]",
                dry_run=False,
                confirm=True,
                change_reason="Test",
                project_root=root,
            )

            new_text = variant_path.read_text(encoding="utf-8")
            # File should still have the YAML header
            self.assertTrue(new_text.startswith("%YAML 1.1"))
            # m_Modifications block should still exist
            self.assertIn("m_Modifications:", new_text)
            # PrefabInstance should still be intact
            self.assertIn("PrefabInstance:", new_text)
            self.assertIn("m_SourcePrefab:", new_text)

    def test_missing_variant_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "Assets").mkdir(parents=True)

            response = revert_overrides(
                variant_path="Assets/Missing.prefab",
                target_file_id="123",
                property_path="m_Name",
                dry_run=True,
                confirm=False,
                change_reason=None,
                project_root=root,
            )

        self.assertFalse(response.success)
        self.assertEqual("REVERT_TARGET_NOT_FOUND", response.code)

    def test_revert_last_override_leaves_empty_modifications(self) -> None:
        """When all overrides for a target are reverted, m_Modifications should still be valid."""
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
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
                root / "Assets" / "Single.prefab",
                f"""%YAML 1.1
--- !u!1001 &100100000
PrefabInstance:
  m_SourcePrefab: {{fileID: 100100000, guid: {BASE_GUID}, type: 3}}
  m_Modification:
    m_Modifications:
    - target: {{fileID: 100100000, guid: {BASE_GUID}, type: 3}}
      propertyPath: m_Name
      value: OnlyOverride
      objectReference: {{fileID: 0}}
""",
            )
            write_file(
                root / "Assets" / "Single.prefab.meta",
                f"""fileFormatVersion: 2
guid: {VARIANT_GUID}
""",
            )

            variant_path = root / "Assets" / "Single.prefab"

            response = revert_overrides(
                variant_path="Assets/Single.prefab",
                target_file_id="100100000",
                property_path="m_Name",
                dry_run=False,
                confirm=True,
                change_reason="Revert only override",
                project_root=root,
            )

            self.assertTrue(response.success)
            new_text = variant_path.read_text(encoding="utf-8")
            # The file should still be valid but m_Modifications should have no entries
            self.assertIn("m_Modifications:", new_text)
            # m_Name should not appear in the modifications section
            after_mods = new_text.split("m_Modifications:")[1]
            self.assertNotIn("propertyPath: m_Name", after_mods)


class PatchRevertDuplicateOverrideTests(unittest.TestCase):
    """Test revert with duplicate overrides (same target+property appearing multiple times)."""

    def test_removes_all_duplicate_overrides(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
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
                root / "Assets" / "Dup.prefab",
                f"""%YAML 1.1
--- !u!1001 &100100000
PrefabInstance:
  m_SourcePrefab: {{fileID: 100100000, guid: {BASE_GUID}, type: 3}}
  m_Modification:
    m_Modifications:
    - target: {{fileID: 100100000, guid: {BASE_GUID}, type: 3}}
      propertyPath: duplicated.path
      value: first
      objectReference: {{fileID: 0}}
    - target: {{fileID: 100100000, guid: {BASE_GUID}, type: 3}}
      propertyPath: duplicated.path
      value: second
      objectReference: {{fileID: 0}}
    - target: {{fileID: 100100000, guid: {BASE_GUID}, type: 3}}
      propertyPath: other.path
      value: keep
      objectReference: {{fileID: 0}}
""",
            )
            write_file(
                root / "Assets" / "Dup.prefab.meta",
                f"""fileFormatVersion: 2
guid: {VARIANT_GUID}
""",
            )

            variant_path = root / "Assets" / "Dup.prefab"

            response = revert_overrides(
                variant_path="Assets/Dup.prefab",
                target_file_id="100100000",
                property_path="duplicated.path",
                dry_run=False,
                confirm=True,
                change_reason="Remove all duplicates",
                project_root=root,
            )

            self.assertTrue(response.success)
            self.assertEqual(2, response.data["match_count"])

            new_text = variant_path.read_text(encoding="utf-8")
            self.assertNotIn("duplicated.path", new_text)
            self.assertIn("other.path", new_text)


if __name__ == "__main__":
    unittest.main()
