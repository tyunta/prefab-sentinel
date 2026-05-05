"""Tests for prefab_sentinel.material_asset_inspector."""

from __future__ import annotations

import unittest
from pathlib import Path

from prefab_sentinel.material_asset_inspector import (
    MaterialAssetResult,
    ShaderInfo,
    format_material_asset,
    inspect_material_asset,
    resolve_builtin_shader_name,
)
from prefab_sentinel.orchestrator import Phase1Orchestrator

FIXTURES = Path(__file__).parent / "fixtures" / "mat"


# ---------------------------------------------------------------------------
# Built-in shader map
# ---------------------------------------------------------------------------


class TestBuiltinShaderMap(unittest.TestCase):
    def test_standard_shader(self) -> None:
        self.assertEqual(resolve_builtin_shader_name("46"), "Standard")

    def test_standard_specular(self) -> None:
        self.assertEqual(
            resolve_builtin_shader_name("45"), "Standard (Specular setup)",
        )

    def test_unlit_color(self) -> None:
        self.assertEqual(resolve_builtin_shader_name("10700"), "Unlit/Color")

    def test_unknown_file_id(self) -> None:
        self.assertEqual(
            resolve_builtin_shader_name("99999"), "Unknown (fileID=99999)",
        )


# ---------------------------------------------------------------------------
# Dataclass construction
# ---------------------------------------------------------------------------


class TestDataclasses(unittest.TestCase):
    def test_material_asset_result_construction(self) -> None:
        result = MaterialAssetResult(
            target_path="Assets/Test.mat",
            material_name="Test",
            shader=ShaderInfo(guid="abc", file_id="46", name="Standard", path=None),
            keywords=[],
            render_queue=-1,
            lightmap_flags=4,
            gpu_instancing=False,
            double_sided_gi=False,
            textures=[],
            floats=[],
            colors=[],
            ints=[],
        )
        self.assertEqual(result.material_name, "Test")
        self.assertIsNone(result.shader.path)


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------


class TestInspectMaterialAsset(unittest.TestCase):
    def test_standard_textured(self) -> None:
        result = inspect_material_asset(str(FIXTURES / "standard_textured.mat"))
        self.assertEqual(result.material_name, "TestMaterial")
        self.assertEqual(result.shader.name, "Standard")
        self.assertEqual(result.shader.file_id, "46")
        self.assertIsNone(result.shader.path)
        self.assertEqual(result.keywords, ["_EMISSION", "_METALLICGLOSSMAP"])
        self.assertEqual(result.render_queue, -1)
        self.assertEqual(result.lightmap_flags, 2)
        self.assertFalse(result.gpu_instancing)
        self.assertFalse(result.double_sided_gi)
        # Only assigned textures (fileID != 0)
        self.assertEqual(len(result.textures), 2)
        self.assertEqual(result.textures[0].name, "_MainTex")
        self.assertEqual(result.textures[0].guid, "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa1")
        self.assertEqual(result.textures[1].name, "_EmissionMap")
        self.assertAlmostEqual(result.textures[1].scale[0], 2.0)
        self.assertAlmostEqual(result.textures[1].offset[0], 0.5)
        # Floats
        self.assertEqual(len(result.floats), 5)
        glossiness = next(f for f in result.floats if f.name == "_Glossiness")
        self.assertAlmostEqual(glossiness.value, 0.8)
        # Colors
        self.assertEqual(len(result.colors), 2)
        color = next(c for c in result.colors if c.name == "_Color")
        self.assertAlmostEqual(color.value["g"], 0.5)

    def test_no_textures(self) -> None:
        result = inspect_material_asset(str(FIXTURES / "no_textures.mat"))
        self.assertEqual(result.textures, [])
        self.assertEqual(result.material_name, "NoTextures")

    def test_with_ints(self) -> None:
        result = inspect_material_asset(str(FIXTURES / "with_ints.mat"))
        self.assertEqual(len(result.ints), 2)
        stencil = next(i for i in result.ints if i.name == "_StencilRef")
        self.assertEqual(stencil.value, 128)
        self.assertTrue(result.gpu_instancing)
        self.assertTrue(result.double_sided_gi)
        self.assertEqual(result.render_queue, 2000)

    def test_custom_shader(self) -> None:
        result = inspect_material_asset(str(FIXTURES / "custom_shader.mat"))
        self.assertEqual(result.shader.guid, "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb")
        self.assertEqual(result.shader.file_id, "4800000")
        # Without GUID index, name falls back
        self.assertIn("Unknown", result.shader.name)
        self.assertEqual(result.keywords, ["_ALPHATEST_ON"])
        self.assertEqual(result.render_queue, 2450)

    def test_malformed_raises(self) -> None:
        with self.assertRaises(ValueError) as cm:
            inspect_material_asset(str(FIXTURES / "malformed.mat"))
        self.assertIsInstance(cm.exception, ValueError)
        self.assertTrue(str(cm.exception))


# ---------------------------------------------------------------------------
# Formatter
# ---------------------------------------------------------------------------


class TestFormatMaterialAsset(unittest.TestCase):
    def test_basic_format(self) -> None:
        result = inspect_material_asset(str(FIXTURES / "standard_textured.mat"))
        tree = format_material_asset(result)
        self.assertIn("TestMaterial (Standard)", tree)
        self.assertIn("_MainTex:", tree)
        self.assertIn("_Glossiness: 0.8", tree)
        self.assertIn("_Color: (1.0, 0.5, 0.25, 1.0)", tree)

    def test_no_textures_format(self) -> None:
        result = inspect_material_asset(str(FIXTURES / "no_textures.mat"))
        tree = format_material_asset(result)
        self.assertIn("NoTextures (Standard)", tree)
        self.assertNotIn("_MainTex:", tree)


# ---------------------------------------------------------------------------
# Orchestrator integration
# ---------------------------------------------------------------------------


class TestOrchestratorInspectMaterialAsset(unittest.TestCase):
    def test_success(self) -> None:
        orch = Phase1Orchestrator.default(project_root=FIXTURES.parent.parent)
        resp = orch.inspect_material_asset(
            target_path=str(FIXTURES / "standard_textured.mat"),
        )
        self.assertTrue(resp.success)
        self.assertEqual(resp.code, "INSPECT_MATERIAL_ASSET_RESULT")
        self.assertEqual(resp.data["material_name"], "TestMaterial")
        self.assertEqual(resp.data["shader"]["name"], "Standard")
        self.assertEqual(resp.data["texture_count"], 2)
        self.assertIn("tree", resp.data)

    def test_not_mat_file(self) -> None:
        orch = Phase1Orchestrator.default(project_root=FIXTURES.parent.parent)
        resp = orch.inspect_material_asset(
            target_path=str(FIXTURES.parent / "smoke" / "basic.prefab"),
        )
        self.assertFalse(resp.success)
        self.assertEqual(resp.code, "INSPECT_MATERIAL_ASSET_NOT_MAT")

    def test_file_not_found(self) -> None:
        orch = Phase1Orchestrator.default(project_root=FIXTURES.parent.parent)
        resp = orch.inspect_material_asset(target_path="nonexistent.mat")
        self.assertFalse(resp.success)
        self.assertIn("FILE_NOT_FOUND", resp.code)
