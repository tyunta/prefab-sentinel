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
from tests._assertion_helpers import assert_error_envelope

FIXTURES = Path(__file__).parent / "fixtures" / "mat"


# ---------------------------------------------------------------------------
# Built-in shader map
# ---------------------------------------------------------------------------


class TestBuiltinShaderMap(unittest.TestCase):
    """Issue #222 Phase 2 — the built-in shader name classifier was
    one-method-per-input.  Parametrising the rows surfaces the
    (file_id, expected_name) pairs in the test identifiers so a new
    file-id mapping can be added with one new tuple and a failure
    reports the exact row that mismatched.
    """

    _SHADER_ROWS = (
        ("46", "Standard"),
        ("45", "Standard (Specular setup)"),
        ("10700", "Unlit/Color"),
        # Unknown file id falls through to the documented placeholder.
        ("99999", "Unknown (fileID=99999)"),
    )

    def test_resolve_builtin_shader_name_per_file_id(self) -> None:
        for file_id, expected in self._SHADER_ROWS:
            with self.subTest(file_id=file_id, expected=expected):
                self.assertEqual(expected, resolve_builtin_shader_name(file_id))


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

    def test_malformed_raises_non_empty_value_error(self) -> None:
        with self.assertRaises(ValueError) as cm:
            inspect_material_asset(str(FIXTURES / "malformed.mat"))
        # ``ValueError`` is not on the project's infrastructure-exception
        # allow-list, so the value-pin rule (AGENTS.md L118) requires an
        # assertion on the exception text in the same method.  Tighten
        # the previous "non-empty" pin to a substring regex on the
        # production message at ``material_asset_inspector.py:182``
        # ("Not a valid Material file: ..."): a future rewording is
        # expected, but a silent change to a placeholder bare-raise
        # message must trip this assertion.
        self.assertRegex(str(cm.exception), r"Not a valid Material file")


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
        project_root = FIXTURES.parent.parent
        orch = Phase1Orchestrator.default(project_root=project_root)
        resp = orch.inspect_material_asset(
            target_path=(FIXTURES / "standard_textured.mat").relative_to(project_root).as_posix(),
        )
        self.assertTrue(resp.success)
        self.assertEqual(resp.code, "INSPECT_MATERIAL_ASSET_RESULT")
        self.assertEqual(resp.data["material_name"], "TestMaterial")
        self.assertEqual(resp.data["shader"]["name"], "Standard")
        self.assertEqual(resp.data["texture_count"], 2)
        self.assertIn("tree", resp.data)

    def test_summary_mode_returns_selected_projection_without_full_payload(self) -> None:
        project_root = FIXTURES.parent.parent
        orch = Phase1Orchestrator.default(project_root=project_root)
        try:
            resp = orch.inspect_material_asset(
                target_path=(FIXTURES / "standard_textured.mat")
                .relative_to(project_root)
                .as_posix(),
                mode="summary",
                property_names=["_MainTex", "_Glossiness", "_Color", "_Missing"],
            )
        except TypeError as exc:
            self.fail(
                "Expected inspect_material_asset summary mode response, "
                f"observed unsupported signature: {exc}."
            )

        self.assertEqual(
            (
                True,
                "INSPECT_MATERIAL_ASSET_RESULT",
                "summary",
                "Standard",
                {
                    "name": "_MainTex",
                    "guid": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa1",
                    "path": "",
                },
                {
                    "texture_count": 2,
                    "float_count": 5,
                    "color_count": 2,
                    "int_count": 0,
                },
                False,
                False,
            ),
            (
                resp.success,
                resp.code,
                resp.data["mode"],
                resp.data["shader"]["name"],
                resp.data["main_texture"],
                resp.data["counts"],
                "properties" in resp.data,
                "tree" in resp.data,
            ),
        )
        self.assertEqual(
            {
                "_MainTex": {"kind": "texture", "guid": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa1"},
                "_Glossiness": {"kind": "float", "value": 0.8},
                "_Color": {
                    "kind": "color",
                    "value": {"r": 1.0, "g": 0.5, "b": 0.25, "a": 1.0},
                },
            },
            resp.data["selected_properties"],
        )

    def test_invalid_material_asset_mode_returns_typed_error(self) -> None:
        project_root = FIXTURES.parent.parent
        orch = Phase1Orchestrator.default(project_root=project_root)
        try:
            resp = orch.inspect_material_asset(
                target_path=(FIXTURES / "standard_textured.mat")
                .relative_to(project_root)
                .as_posix(),
                mode="compact",
            )
        except TypeError as exc:
            self.fail(
                "Expected INSPECT_MATERIAL_ASSET_INVALID_MODE envelope, "
                f"observed unsupported signature: {exc}."
            )

        assert_error_envelope(
            resp,
            code="INSPECT_MATERIAL_ASSET_INVALID_MODE",
            message_match="Unsupported material inspection mode",
        )
        self.assertEqual(
            ["full", "summary"],
            resp.data["accepted_modes"],
        )

    def test_not_mat_file(self) -> None:
        project_root = FIXTURES.parent.parent
        orch = Phase1Orchestrator.default(project_root=project_root)
        resp = orch.inspect_material_asset(
            target_path=(FIXTURES.parent / "smoke" / "basic.prefab")
            .relative_to(project_root)
            .as_posix(),
        )
        assert_error_envelope(resp, code="INSPECT_MATERIAL_ASSET_NOT_MAT")

    def test_file_not_found(self) -> None:
        orch = Phase1Orchestrator.default(project_root=FIXTURES.parent.parent)
        resp = orch.inspect_material_asset(target_path="nonexistent.mat")
        # Tighten the substring assertion (``assertIn("FILE_NOT_FOUND",
        # resp.code)``) to exact equality so renaming the code to a
        # suffix-preserving but semantically-shifted form trips this row.
        assert_error_envelope(resp, code="INSPECT_MATERIAL_ASSET_FILE_NOT_FOUND")

    def test_resolution_failure_returns_read_error_envelope(self) -> None:
        from unittest.mock import patch

        orch = Phase1Orchestrator.default(project_root=FIXTURES.parent.parent)
        with patch.object(Path, "resolve", side_effect=OSError("resolve failed")):
            try:
                resp = orch.inspect_material_asset(target_path="Assets/Test.mat")
            except OSError as exc:
                self.fail(f"inspect_material_asset leaked raw {type(exc).__name__}: {exc}")

        assert_error_envelope(
            resp,
            code="INSPECT_MATERIAL_ASSET_READ_ERROR",
            severity="error",
            message_match="resolve failed",
            data={"target_path": "Assets/Test.mat", "read_only": True},
        )
