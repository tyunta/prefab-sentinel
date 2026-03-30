"""Tests for asset_file_ops module (copy_asset / rename_asset)."""

from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

from prefab_sentinel.asset_file_ops import (
    _generate_guid,
    _generate_meta_content,
    _rewrite_m_name,
    copy_asset,
    rename_asset,
)
from prefab_sentinel.unity_assets import decode_text_file

_FIXTURES = Path(__file__).parent / "fixtures" / "mat"

# MonoBehaviour-style .asset content
_MONOBEHAVIOUR_ASSET = """\
%YAML 1.1
%TAG !u! tag:unity3d.com,2011:
--- !u!114 &11400000
MonoBehaviour:
  m_ObjectHideFlags: 0
  m_CorrespondingSourceObject: {fileID: 0}
  m_PrefabInstance: {fileID: 0}
  m_PrefabAsset: {fileID: 0}
  m_GameObject: {fileID: 0}
  m_Enabled: 1
  m_EditorHideFlags: 0
  m_Script: {fileID: 11500000, guid: abc12345abc12345abc12345abc12345, type: 3}
  m_Name: OriginalAsset
  m_EditorClassIdentifier:
"""

# Content with no m_Name field
_NO_M_NAME_CONTENT = """\
%YAML 1.1
%TAG !u! tag:unity3d.com,2011:
--- !u!114 &11400000
MonoBehaviour:
  m_ObjectHideFlags: 0
  m_Script: {fileID: 11500000, guid: abc12345abc12345abc12345abc12345, type: 3}
  m_EditorClassIdentifier:
"""

_DUMMY_META = "fileFormatVersion: 2\nguid: abcd1234abcd1234abcd1234abcd1234\n"


class TestRewriteMName(unittest.TestCase):
    """Test _rewrite_m_name helper."""

    def test_should_replace_m_name_in_mat_file(self) -> None:
        text = decode_text_file(_FIXTURES / "standard_textured.mat")

        new_text, old_name, new_name = _rewrite_m_name(text, "CopiedMaterial")

        self.assertIn("  m_Name: CopiedMaterial", new_text)
        self.assertNotIn("  m_Name: TestMaterial", new_text)
        self.assertEqual("TestMaterial", old_name)
        self.assertEqual("CopiedMaterial", new_name)

    def test_should_replace_m_name_in_monobehaviour_asset(self) -> None:
        new_text, old_name, new_name = _rewrite_m_name(
            _MONOBEHAVIOUR_ASSET, "RenamedAsset",
        )

        self.assertIn("  m_Name: RenamedAsset", new_text)
        self.assertNotIn("  m_Name: OriginalAsset", new_text)
        self.assertEqual("OriginalAsset", old_name)
        self.assertEqual("RenamedAsset", new_name)

    def test_should_return_none_old_when_no_m_name(self) -> None:
        new_text, old_name, new_name = _rewrite_m_name(
            _NO_M_NAME_CONTENT, "SomeName",
        )

        self.assertEqual(_NO_M_NAME_CONTENT, new_text)
        self.assertIsNone(old_name)
        self.assertEqual("SomeName", new_name)

    def test_should_leave_text_unchanged_when_name_matches(self) -> None:
        new_text, old_name, new_name = _rewrite_m_name(
            _MONOBEHAVIOUR_ASSET, "OriginalAsset",
        )

        self.assertEqual(_MONOBEHAVIOUR_ASSET, new_text)
        self.assertEqual("OriginalAsset", old_name)
        self.assertEqual("OriginalAsset", new_name)


class TestMetaHelpers(unittest.TestCase):
    """Test _generate_guid and _generate_meta_content helpers."""

    def test_should_return_32_hex_chars(self) -> None:
        guid = _generate_guid()

        self.assertEqual(32, len(guid))
        self.assertRegex(guid, r"^[0-9a-f]{32}$")

    def test_should_return_unique_guids(self) -> None:
        guid1 = _generate_guid()
        guid2 = _generate_guid()

        self.assertNotEqual(guid1, guid2)

    def test_should_produce_valid_meta_format(self) -> None:
        guid = "a" * 32

        content = _generate_meta_content(guid)

        self.assertIn("fileFormatVersion: 2", content)
        self.assertIn(f"guid: {guid}", content)
        self.assertTrue(content.endswith("\n"))


class TestCopyAsset(unittest.TestCase):
    """Test copy_asset core function."""

    def test_should_preview_without_creating_file_when_dry_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            src = Path(tmpdir) / "original.mat"
            shutil.copy(_FIXTURES / "standard_textured.mat", src)
            dest = Path(tmpdir) / "copied.mat"

            result = copy_asset(str(src), str(dest), dry_run=True)

            self.assertTrue(result["success"])
            self.assertEqual("ASSET_COPY_DRY_RUN", result["code"])
            self.assertFalse(dest.exists())
            self.assertIn("m_name_before", result["data"])
            self.assertIn("m_name_after", result["data"])

    def test_should_copy_with_m_name_and_meta_when_applied(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            src = Path(tmpdir) / "original.mat"
            shutil.copy(_FIXTURES / "standard_textured.mat", src)
            src_meta = Path(str(src) + ".meta")
            src_meta.write_text(_DUMMY_META, encoding="utf-8")
            dest = Path(tmpdir) / "copied.mat"

            result = copy_asset(str(src), str(dest), dry_run=False)

            self.assertTrue(result["success"])
            self.assertEqual("ASSET_COPY_APPLIED", result["code"])
            self.assertTrue(dest.exists())
            dest_text = dest.read_text(encoding="utf-8")
            self.assertIn("  m_Name: copied", dest_text)
            self.assertNotIn("  m_Name: TestMaterial", dest_text)
            dest_meta = Path(str(dest) + ".meta")
            self.assertTrue(dest_meta.exists())
            meta_text = dest_meta.read_text(encoding="utf-8")
            self.assertIn("fileFormatVersion: 2", meta_text)
            self.assertIn("guid:", meta_text)
            self.assertIn("source_path", result["data"])
            self.assertIn("dest_path", result["data"])
            self.assertIn("new_guid", result["data"])
            self.assertTrue(result["data"]["meta_created"])
            self.assertNotIn("m_name_unchanged", result["data"])

    def test_should_reject_unsupported_type(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            src = Path(tmpdir) / "texture.png"
            src.write_bytes(b"fake png")
            dest = Path(tmpdir) / "texture_copy.png"

            result = copy_asset(str(src), str(dest), dry_run=True)

            self.assertFalse(result["success"])
            self.assertEqual("ASSET_OP_UNSUPPORTED_TYPE", result["code"])

    def test_should_error_when_source_not_found(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            result = copy_asset(
                str(Path(tmpdir) / "nonexistent.mat"),
                str(Path(tmpdir) / "dest.mat"),
                dry_run=True,
            )

            self.assertFalse(result["success"])
            self.assertEqual("ASSET_COPY_SOURCE_NOT_FOUND", result["code"])

    def test_should_error_when_dest_exists(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            src = Path(tmpdir) / "original.mat"
            shutil.copy(_FIXTURES / "standard_textured.mat", src)
            dest = Path(tmpdir) / "existing.mat"
            dest.write_text("already here", encoding="utf-8")

            result = copy_asset(str(src), str(dest), dry_run=True)

            self.assertFalse(result["success"])
            self.assertEqual("ASSET_COPY_DEST_EXISTS", result["code"])

    def test_should_error_when_dest_dir_not_found(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            src = Path(tmpdir) / "original.mat"
            shutil.copy(_FIXTURES / "standard_textured.mat", src)
            dest = Path(tmpdir) / "nonexistent_dir" / "copied.mat"

            result = copy_asset(str(src), str(dest), dry_run=True)

            self.assertFalse(result["success"])
            self.assertEqual("ASSET_COPY_DEST_DIR_NOT_FOUND", result["code"])

    def test_should_warn_when_source_meta_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            src = Path(tmpdir) / "original.mat"
            shutil.copy(_FIXTURES / "standard_textured.mat", src)
            dest = Path(tmpdir) / "copied.mat"

            result = copy_asset(str(src), str(dest), dry_run=False)

            self.assertTrue(result["success"])
            diagnostics = result.get("diagnostics", [])
            self.assertEqual(1, len(diagnostics))
            self.assertEqual("source_meta_missing", diagnostics[0]["detail"])
            self.assertIn("Source .meta not found", diagnostics[0]["evidence"])

    def test_should_set_m_name_unchanged_when_name_matches(self) -> None:
        """Regression: F-003 — m_name_unchanged must be in data when already correct."""
        with tempfile.TemporaryDirectory() as tmpdir:
            src = Path(tmpdir) / "TestMaterial.mat"
            shutil.copy(_FIXTURES / "standard_textured.mat", src)
            src_meta = Path(str(src) + ".meta")
            src_meta.write_text(_DUMMY_META, encoding="utf-8")
            dest = Path(tmpdir) / "TestMaterial_copy.mat"

            result = copy_asset(str(src), str(dest), dry_run=True)

            self.assertTrue(result["success"])
            self.assertNotIn("m_name_unchanged", result["data"])

    def test_should_warn_when_m_name_not_found(self) -> None:
        """Regression: F-001 — m_name_not_found diagnostic when no m_Name field."""
        with tempfile.TemporaryDirectory() as tmpdir:
            src = Path(tmpdir) / "no_name.asset"
            src.write_text(_NO_M_NAME_CONTENT, encoding="utf-8")
            dest = Path(tmpdir) / "copied.asset"

            result = copy_asset(str(src), str(dest), dry_run=True)

            self.assertTrue(result["success"])
            diagnostics = result.get("diagnostics", [])
            has_not_found = any(
                d.get("detail") == "m_name_not_found" for d in diagnostics
            )
            self.assertTrue(
                has_not_found,
                f"Expected m_name_not_found diagnostic, got: {diagnostics}",
            )

    def test_should_reject_positional_dry_run(self) -> None:
        """Regression: F-002 — dry_run must be keyword-only."""
        with tempfile.TemporaryDirectory() as tmpdir:
            src = Path(tmpdir) / "original.mat"
            shutil.copy(_FIXTURES / "standard_textured.mat", src)
            dest = Path(tmpdir) / "copied.mat"

            with self.assertRaises(TypeError):
                copy_asset(str(src), str(dest), True)

    def test_should_set_m_name_unchanged_when_dest_stem_matches(self) -> None:
        """Regression: F-003 — dest stem equals existing m_Name."""
        with tempfile.TemporaryDirectory() as tmpdir:
            src = Path(tmpdir) / "original.mat"
            shutil.copy(_FIXTURES / "standard_textured.mat", src)
            src_meta = Path(str(src) + ".meta")
            src_meta.write_text(_DUMMY_META, encoding="utf-8")
            dest = Path(tmpdir) / "TestMaterial.mat"

            result = copy_asset(str(src), str(dest), dry_run=False)

            self.assertTrue(result["success"])
            self.assertTrue(result["data"]["m_name_unchanged"])


class TestRenameAsset(unittest.TestCase):
    """Test rename_asset core function."""

    def test_should_preview_without_renaming_when_dry_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            src = Path(tmpdir) / "original.mat"
            shutil.copy(_FIXTURES / "standard_textured.mat", src)

            result = rename_asset(str(src), "renamed.mat", dry_run=True)

            self.assertTrue(result["success"])
            self.assertEqual("ASSET_RENAME_DRY_RUN", result["code"])
            self.assertTrue(src.exists())
            self.assertFalse((Path(tmpdir) / "renamed.mat").exists())

    def test_should_rename_with_m_name_and_meta_when_applied(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            src = Path(tmpdir) / "original.mat"
            shutil.copy(_FIXTURES / "standard_textured.mat", src)
            meta = Path(str(src) + ".meta")
            meta.write_text(_DUMMY_META, encoding="utf-8")

            result = rename_asset(str(src), "renamed.mat", dry_run=False)

            self.assertTrue(result["success"])
            self.assertEqual("ASSET_RENAME_APPLIED", result["code"])
            new_path = Path(tmpdir) / "renamed.mat"
            self.assertTrue(new_path.exists())
            self.assertFalse(src.exists())
            text = new_path.read_text(encoding="utf-8")
            self.assertIn("  m_Name: renamed", text)
            new_meta = Path(str(new_path) + ".meta")
            self.assertTrue(new_meta.exists())
            self.assertFalse(meta.exists())
            self.assertIn("asset_path", result["data"])
            self.assertIn("new_path", result["data"])
            self.assertTrue(result["data"]["meta_renamed"])
            self.assertNotIn("m_name_unchanged", result["data"])

    def test_should_reject_unsupported_type(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            src = Path(tmpdir) / "texture.png"
            src.write_bytes(b"fake png")

            result = rename_asset(str(src), "texture_new.png", dry_run=True)

            self.assertFalse(result["success"])
            self.assertEqual("ASSET_OP_UNSUPPORTED_TYPE", result["code"])

    def test_should_error_when_asset_not_found(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            result = rename_asset(
                str(Path(tmpdir) / "nonexistent.mat"),
                "renamed.mat",
                dry_run=True,
            )

            self.assertFalse(result["success"])
            self.assertEqual("ASSET_RENAME_NOT_FOUND", result["code"])

    def test_should_error_when_dest_exists(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            src = Path(tmpdir) / "original.mat"
            shutil.copy(_FIXTURES / "standard_textured.mat", src)
            existing = Path(tmpdir) / "taken.mat"
            existing.write_text("taken", encoding="utf-8")

            result = rename_asset(str(src), "taken.mat", dry_run=True)

            self.assertFalse(result["success"])
            self.assertEqual("ASSET_RENAME_DEST_EXISTS", result["code"])

    def test_should_error_when_extension_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            src = Path(tmpdir) / "original.mat"
            shutil.copy(_FIXTURES / "standard_textured.mat", src)

            result = rename_asset(str(src), "renamed.asset", dry_run=True)

            self.assertFalse(result["success"])
            self.assertEqual("ASSET_RENAME_EXT_MISMATCH", result["code"])

    def test_should_succeed_with_meta_renamed_false_when_no_meta(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            src = Path(tmpdir) / "original.mat"
            shutil.copy(_FIXTURES / "standard_textured.mat", src)

            result = rename_asset(str(src), "renamed.mat", dry_run=False)

            self.assertTrue(result["success"])
            self.assertFalse(result["data"]["meta_renamed"])

    def test_should_warn_when_m_name_not_found(self) -> None:
        """Regression: F-001 — m_name_not_found diagnostic on rename."""
        with tempfile.TemporaryDirectory() as tmpdir:
            src = Path(tmpdir) / "no_name.asset"
            src.write_text(_NO_M_NAME_CONTENT, encoding="utf-8")

            result = rename_asset(str(src), "renamed.asset", dry_run=True)

            self.assertTrue(result["success"])
            diagnostics = result.get("diagnostics", [])
            has_not_found = any(
                d.get("detail") == "m_name_not_found" for d in diagnostics
            )
            self.assertTrue(
                has_not_found,
                f"Expected m_name_not_found diagnostic, got: {diagnostics}",
            )

    def test_should_reject_positional_dry_run(self) -> None:
        """Regression: F-002 — dry_run must be keyword-only."""
        with tempfile.TemporaryDirectory() as tmpdir:
            src = Path(tmpdir) / "original.mat"
            shutil.copy(_FIXTURES / "standard_textured.mat", src)

            with self.assertRaises(TypeError):
                rename_asset(str(src), "renamed.mat", True)

    def test_should_set_m_name_unchanged_when_rename_stem_matches(self) -> None:
        """Regression: F-003 — m_name_unchanged when m_Name already matches new stem."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # m_Name in _MONOBEHAVIOUR_ASSET is "OriginalAsset"
            src = Path(tmpdir) / "wrong_filename.asset"
            src.write_text(_MONOBEHAVIOUR_ASSET, encoding="utf-8")

            result = rename_asset(str(src), "OriginalAsset.asset", dry_run=True)

            self.assertTrue(result["success"])
            self.assertTrue(result["data"].get("m_name_unchanged"))
            self.assertEqual("OriginalAsset", result["data"]["m_name_before"])
            self.assertEqual("OriginalAsset", result["data"]["m_name_after"])

    def test_should_report_diagnostic_when_meta_rename_fails(self) -> None:
        """Regression: F-005 — meta rename failure must produce a diagnostic."""
        with tempfile.TemporaryDirectory() as tmpdir:
            src = Path(tmpdir) / "original.mat"
            shutil.copy(_FIXTURES / "standard_textured.mat", src)
            src_meta = Path(str(src) + ".meta")
            src_meta.write_text(_DUMMY_META, encoding="utf-8")
            # Pre-create destination meta as a directory so rename fails
            dest_meta = Path(tmpdir) / "renamed.mat.meta"
            dest_meta.mkdir()

            result = rename_asset(str(src), "renamed.mat", dry_run=False)

            self.assertTrue(result["success"])
            self.assertFalse(result["data"]["meta_renamed"])
            diagnostics = result.get("diagnostics", [])
            has_meta_fail = any(
                d.get("detail") == "meta_rename_failed" for d in diagnostics
            )
            self.assertTrue(
                has_meta_fail,
                f"Expected meta_rename_failed diagnostic, got: {diagnostics}",
            )


if __name__ == "__main__":
    unittest.main()
