"""Tests for material_asset_inspector.write_material_property()."""

from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

from prefab_sentinel.material_asset_inspector import inspect_material_asset
from prefab_sentinel.material_asset_writer import write_material_property

_FIXTURES = Path(__file__).parent / "fixtures" / "mat"


class TestWriteMaterialPropertyFloat(unittest.TestCase):
    """Test float property writing."""

    def test_dry_run_returns_before_after(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            mat = Path(tmpdir) / "test.mat"
            shutil.copy(_FIXTURES / "standard_textured.mat", mat)
            result = write_material_property(str(mat), "_Glossiness", "0.3", dry_run=True)
            self.assertTrue(result["success"])
            self.assertEqual("MAT_PROP_DRY_RUN", result["code"])
            self.assertEqual("m_Floats", result["data"]["category"])
            self.assertAlmostEqual(0.8, float(result["data"]["before"]))
            self.assertEqual("0.3", result["data"]["after"])

    def test_confirm_writes_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            mat = Path(tmpdir) / "test.mat"
            shutil.copy(_FIXTURES / "standard_textured.mat", mat)
            result = write_material_property(str(mat), "_Glossiness", "0.3", dry_run=False)
            self.assertTrue(result["success"])
            self.assertEqual("MAT_PROP_APPLIED", result["code"])
            # Re-parse to verify
            parsed = inspect_material_asset(str(mat))
            glossiness = next(f for f in parsed.floats if f.name == "_Glossiness")
            self.assertAlmostEqual(0.3, glossiness.value)


class TestWriteMaterialPropertyInt(unittest.TestCase):
    """Test integer property writing."""

    def test_confirm_writes_int(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            mat = Path(tmpdir) / "test.mat"
            shutil.copy(_FIXTURES / "with_ints.mat", mat)
            result = write_material_property(str(mat), "_StencilRef", "64", dry_run=False)
            self.assertTrue(result["success"])
            parsed = inspect_material_asset(str(mat))
            stencil = next(i for i in parsed.ints if i.name == "_StencilRef")
            self.assertEqual(64, stencil.value)


class TestWriteMaterialPropertyColor(unittest.TestCase):
    """Test color property writing."""

    def test_confirm_writes_color(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            mat = Path(tmpdir) / "test.mat"
            shutil.copy(_FIXTURES / "standard_textured.mat", mat)
            result = write_material_property(
                str(mat), "_Color", "[0.5, 0.6, 0.7, 1]", dry_run=False,
            )
            self.assertTrue(result["success"])
            parsed = inspect_material_asset(str(mat))
            color = next(c for c in parsed.colors if c.name == "_Color")
            self.assertAlmostEqual(0.5, color.value["r"])
            self.assertAlmostEqual(0.6, color.value["g"])
            self.assertAlmostEqual(0.7, color.value["b"])
            self.assertAlmostEqual(1.0, color.value["a"])


class TestWriteMaterialPropertyTexture(unittest.TestCase):
    """Test texture property writing."""

    def test_confirm_changes_texture_guid(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            mat = Path(tmpdir) / "test.mat"
            shutil.copy(_FIXTURES / "standard_textured.mat", mat)
            new_guid = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
            result = write_material_property(
                str(mat), "_MainTex", f"guid:{new_guid}", dry_run=False,
            )
            self.assertTrue(result["success"])
            # Read raw text to verify GUID changed
            text = mat.read_text(encoding="utf-8")
            self.assertIn(new_guid, text)
            # Verify m_Scale/m_Offset preserved
            self.assertIn("m_Scale: {x: 1, y: 1}", text)

    def test_confirm_nullifies_texture(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            mat = Path(tmpdir) / "test.mat"
            shutil.copy(_FIXTURES / "standard_textured.mat", mat)
            result = write_material_property(str(mat), "_MainTex", "", dry_run=False)
            self.assertTrue(result["success"])
            text = mat.read_text(encoding="utf-8")
            self.assertIn("m_Texture: {fileID: 0}", text)


class TestWriteMaterialPropertyErrors(unittest.TestCase):
    """Test error cases."""

    def test_wrong_extension(self) -> None:
        result = write_material_property("/tmp/test.prefab", "_Foo", "1", dry_run=True)
        self.assertFalse(result["success"])
        self.assertEqual("MAT_PROP_WRONG_EXT", result["code"])

    def test_file_not_found(self) -> None:
        result = write_material_property("/nonexistent/test.mat", "_Foo", "1", dry_run=True)
        self.assertFalse(result["success"])
        self.assertEqual("MAT_PROP_FILE_NOT_FOUND", result["code"])

    def test_property_not_found(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            mat = Path(tmpdir) / "test.mat"
            shutil.copy(_FIXTURES / "standard_textured.mat", mat)
            result = write_material_property(str(mat), "_NonExistent", "1", dry_run=True)
            self.assertFalse(result["success"])
            self.assertEqual("MAT_PROP_NOT_FOUND", result["code"])
            # Should list available properties
            self.assertTrue(len(result["diagnostics"]) > 0)
            # Should have suggestions field
            self.assertIn("suggestions", result["data"])
            self.assertIsInstance(result["data"]["suggestions"], list)

    def test_property_not_found_with_suggestions(self) -> None:
        """Typo in property name returns fuzzy match suggestions."""
        with tempfile.TemporaryDirectory() as tmpdir:
            mat = Path(tmpdir) / "test.mat"
            shutil.copy(_FIXTURES / "standard_textured.mat", mat)
            result = write_material_property(str(mat), "_Colr", "1", dry_run=True)
            self.assertFalse(result["success"])
            self.assertEqual("MAT_PROP_NOT_FOUND", result["code"])
            self.assertIn("_Color", result["data"]["suggestions"])

    def test_color_parse_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            mat = Path(tmpdir) / "test.mat"
            shutil.copy(_FIXTURES / "standard_textured.mat", mat)
            result = write_material_property(str(mat), "_Color", "not_a_color", dry_run=False)
            self.assertFalse(result["success"])
            self.assertEqual("MAT_PROP_PARSE_ERROR", result["code"])


if __name__ == "__main__":
    unittest.main()
