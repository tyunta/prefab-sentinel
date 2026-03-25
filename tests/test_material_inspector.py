"""Tests for prefab_sentinel.material_inspector."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from prefab_sentinel.material_inspector import (
    MaterialInspectionResult,
    MaterialSlot,
    RendererMaterials,
    _inspect_base_materials,
    _parse_material_overrides,
    _parse_renderer_materials,
    format_materials,
    inspect_materials,
)
from prefab_sentinel.unity_yaml_parser import YamlBlock
from tests.yaml_helpers import (
    YAML_HEADER,
    make_gameobject,
    make_meshrenderer_with_materials,
    make_prefab_variant,
    make_skinned_mesh_renderer,
    make_transform,
)

# ---------------------------------------------------------------------------
# Fixture GUIDs for materials
# ---------------------------------------------------------------------------
MAT_GUID_A = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa1"
MAT_GUID_B = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa2"
MAT_GUID_C = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa3"

BASE_PREFAB_GUID = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
MID_VARIANT_GUID = "cccccccccccccccccccccccccccccccc"


# ---------------------------------------------------------------------------
# _parse_renderer_materials tests
# ---------------------------------------------------------------------------


class TestParseRendererMaterials(unittest.TestCase):
    def test_empty_materials(self) -> None:
        block = YamlBlock(
            class_id="137",
            file_id="100",
            text=(
                "--- !u!137 &100\n"
                "SkinnedMeshRenderer:\n"
                "  m_GameObject: {fileID: 1}\n"
                "  m_Materials: []\n"
            ),
            start_line=1,
        )
        result = _parse_renderer_materials(block)
        self.assertEqual(result, [])

    def test_single_material(self) -> None:
        block = YamlBlock(
            class_id="137",
            file_id="100",
            text=(
                "--- !u!137 &100\n"
                "SkinnedMeshRenderer:\n"
                "  m_GameObject: {fileID: 1}\n"
                "  m_Materials:\n"
                f"  - {{fileID: 2100000, guid: {MAT_GUID_A}, type: 2}}\n"
                "  m_StaticBatchInfo:\n"
            ),
            start_line=1,
        )
        result = _parse_renderer_materials(block)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0], ("2100000", MAT_GUID_A.lower()))

    def test_multiple_materials(self) -> None:
        block = YamlBlock(
            class_id="137",
            file_id="100",
            text=(
                "--- !u!137 &100\n"
                "SkinnedMeshRenderer:\n"
                "  m_GameObject: {fileID: 1}\n"
                "  m_Materials:\n"
                f"  - {{fileID: 2100000, guid: {MAT_GUID_A}, type: 2}}\n"
                f"  - {{fileID: 2100000, guid: {MAT_GUID_B}, type: 2}}\n"
                "  m_StaticBatchInfo:\n"
            ),
            start_line=1,
        )
        result = _parse_renderer_materials(block)
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0][1], MAT_GUID_A.lower())
        self.assertEqual(result[1][1], MAT_GUID_B.lower())


# ---------------------------------------------------------------------------
# _parse_material_overrides tests
# ---------------------------------------------------------------------------


class TestParseMaterialOverrides(unittest.TestCase):
    def test_no_overrides(self) -> None:
        text = (
            "--- !u!1001 &100100000\n"
            "PrefabInstance:\n"
            "  m_Modification:\n"
            "    m_Modifications: []\n"
            f"  m_SourcePrefab: {{fileID: 100100000, guid: {BASE_PREFAB_GUID}, type: 3}}\n"
        )
        out: dict[tuple[str, int], str] = {}
        _parse_material_overrides(text, out)
        self.assertEqual(out, {})

    def test_single_material_override(self) -> None:
        text = (
            "--- !u!1001 &100100000\n"
            "PrefabInstance:\n"
            "  m_Modification:\n"
            "    m_Modifications:\n"
            f"    - target: {{fileID: 42, guid: {BASE_PREFAB_GUID}, type: 3}}\n"
            "      propertyPath: m_Materials.Array.data[0]\n"
            "      value: \n"
            f"      objectReference: {{fileID: 2100000, guid: {MAT_GUID_C}, type: 2}}\n"
            f"  m_SourcePrefab: {{fileID: 100100000, guid: {BASE_PREFAB_GUID}, type: 3}}\n"
        )
        out: dict[tuple[str, int], str] = {}
        _parse_material_overrides(text, out)
        self.assertIn(("42", 0), out)
        self.assertEqual(out[("42", 0)], MAT_GUID_C.lower())

    def test_multiple_material_overrides(self) -> None:
        text = (
            "--- !u!1001 &100100000\n"
            "PrefabInstance:\n"
            "  m_Modification:\n"
            "    m_Modifications:\n"
            f"    - target: {{fileID: 42, guid: {BASE_PREFAB_GUID}, type: 3}}\n"
            "      propertyPath: m_Materials.Array.data[0]\n"
            "      value: \n"
            f"      objectReference: {{fileID: 2100000, guid: {MAT_GUID_B}, type: 2}}\n"
            f"    - target: {{fileID: 42, guid: {BASE_PREFAB_GUID}, type: 3}}\n"
            "      propertyPath: m_Materials.Array.data[1]\n"
            "      value: \n"
            f"      objectReference: {{fileID: 2100000, guid: {MAT_GUID_C}, type: 2}}\n"
            f"  m_SourcePrefab: {{fileID: 100100000, guid: {BASE_PREFAB_GUID}, type: 3}}\n"
        )
        out: dict[tuple[str, int], str] = {}
        _parse_material_overrides(text, out)
        self.assertEqual(len(out), 2)
        self.assertEqual(out[("42", 0)], MAT_GUID_B.lower())
        self.assertEqual(out[("42", 1)], MAT_GUID_C.lower())

    def test_non_material_overrides_ignored(self) -> None:
        text = (
            "--- !u!1001 &100100000\n"
            "PrefabInstance:\n"
            "  m_Modification:\n"
            "    m_Modifications:\n"
            f"    - target: {{fileID: 42, guid: {BASE_PREFAB_GUID}, type: 3}}\n"
            "      propertyPath: m_LocalPosition.x\n"
            "      value: 1.5\n"
            "      objectReference: {fileID: 0}\n"
            f"    - target: {{fileID: 42, guid: {BASE_PREFAB_GUID}, type: 3}}\n"
            "      propertyPath: m_Name\n"
            "      value: NewName\n"
            "      objectReference: {fileID: 0}\n"
            f"  m_SourcePrefab: {{fileID: 100100000, guid: {BASE_PREFAB_GUID}, type: 3}}\n"
        )
        out: dict[tuple[str, int], str] = {}
        _parse_material_overrides(text, out)
        self.assertEqual(out, {})


# ---------------------------------------------------------------------------
# _inspect_base_materials tests
# ---------------------------------------------------------------------------


class TestInspectBaseMaterials(unittest.TestCase):
    def test_single_renderer_with_materials(self) -> None:
        text = (
            YAML_HEADER
            + make_gameobject("1", "Hair_Base", ["2", "3"])
            + make_transform("2", "1")
            + make_skinned_mesh_renderer("3", "1", [MAT_GUID_A, MAT_GUID_B])
        )
        result = _inspect_base_materials("Assets/test.prefab", text, Path("/tmp"), {})
        self.assertEqual(len(result.renderers), 1)
        r = result.renderers[0]
        self.assertEqual(r.game_object_name, "Hair_Base")
        self.assertEqual(r.renderer_type, "SkinnedMeshRenderer")
        self.assertEqual(len(r.slots), 2)
        self.assertEqual(r.slots[0].index, 0)
        self.assertEqual(r.slots[0].material_guid, MAT_GUID_A.lower())
        self.assertEqual(r.slots[1].index, 1)
        self.assertFalse(r.slots[0].is_override)
        self.assertFalse(result.is_variant)

    def test_mesh_renderer_detected(self) -> None:
        text = (
            YAML_HEADER
            + make_gameobject("1", "Cube", ["2", "3"])
            + make_transform("2", "1")
            + make_meshrenderer_with_materials("3", "1", [MAT_GUID_A])
        )
        result = _inspect_base_materials("Assets/test.prefab", text, Path("/tmp"), {})
        self.assertEqual(len(result.renderers), 1)
        self.assertEqual(result.renderers[0].renderer_type, "MeshRenderer")

    def test_no_renderers(self) -> None:
        text = (
            YAML_HEADER
            + make_gameobject("1", "Empty", ["2"])
            + make_transform("2", "1")
        )
        result = _inspect_base_materials("Assets/test.prefab", text, Path("/tmp"), {})
        self.assertEqual(result.renderers, [])

    def test_multiple_renderers(self) -> None:
        text = (
            YAML_HEADER
            + make_gameobject("1", "Hair", ["2", "3"])
            + make_transform("2", "1", children_file_ids=["6"])
            + make_skinned_mesh_renderer("3", "1", [MAT_GUID_A])
            + make_gameobject("5", "Body", ["6", "7"])
            + make_transform("6", "5", father_file_id="2")
            + make_skinned_mesh_renderer("7", "5", [MAT_GUID_B, MAT_GUID_C])
        )
        result = _inspect_base_materials("Assets/test.prefab", text, Path("/tmp"), {})
        self.assertEqual(len(result.renderers), 2)
        names = [r.game_object_name for r in result.renderers]
        self.assertIn("Hair", names)
        self.assertIn("Body", names)


# ---------------------------------------------------------------------------
# format_materials tests
# ---------------------------------------------------------------------------


class TestFormatMaterials(unittest.TestCase):
    def test_empty_renderers(self) -> None:
        result = MaterialInspectionResult(
            target_path="Assets/test.prefab",
            is_variant=False,
            base_prefab_path=None,
            renderers=[],
        )
        self.assertEqual(format_materials(result), "(no renderer components found)")

    def test_base_prefab_format(self) -> None:
        result = MaterialInspectionResult(
            target_path="Assets/test.prefab",
            is_variant=False,
            base_prefab_path=None,
            renderers=[
                RendererMaterials(
                    game_object_name="Hair",
                    renderer_type="SkinnedMeshRenderer",
                    file_id="100",
                    slots=[
                        MaterialSlot(
                            index=0,
                            material_name="mat_hair",
                            material_path="Assets/Materials/mat_hair.mat",
                            material_guid="aaa",
                            is_override=False,
                        ),
                    ],
                ),
            ],
        )
        text = format_materials(result)
        self.assertIn("Hair (SkinnedMeshRenderer)", text)
        self.assertIn("[0] mat_hair (Assets/Materials/mat_hair.mat)", text)
        # No override/inherited markers for non-variants
        self.assertNotIn("[override]", text)
        self.assertNotIn("[inherited]", text)

    def test_variant_format_with_markers(self) -> None:
        result = MaterialInspectionResult(
            target_path="Assets/test_variant.prefab",
            is_variant=True,
            base_prefab_path="Assets/test_base.prefab",
            renderers=[
                RendererMaterials(
                    game_object_name="Hair_Base",
                    renderer_type="SkinnedMeshRenderer",
                    file_id="100",
                    slots=[
                        MaterialSlot(
                            index=0,
                            material_name="mat_blonde",
                            material_path="Assets/mat_blonde.mat",
                            material_guid="aaa",
                            is_override=True,
                        ),
                        MaterialSlot(
                            index=1,
                            material_name="mat_inner",
                            material_path="Assets/mat_inner.mat",
                            material_guid="bbb",
                            is_override=False,
                        ),
                    ],
                ),
            ],
        )
        text = format_materials(result)
        self.assertIn("Hair_Base (SkinnedMeshRenderer)", text)
        self.assertIn("[override]", text)
        self.assertIn("[inherited]", text)
        lines = text.split("\n")
        # First slot overridden
        self.assertIn("[override]", lines[1])
        # Second slot inherited
        self.assertIn("[inherited]", lines[2])

    def test_no_materials_message(self) -> None:
        result = MaterialInspectionResult(
            target_path="Assets/test.prefab",
            is_variant=False,
            base_prefab_path=None,
            renderers=[
                RendererMaterials(
                    game_object_name="EmptyRenderer",
                    renderer_type="MeshRenderer",
                    file_id="100",
                    slots=[],
                ),
            ],
        )
        text = format_materials(result)
        self.assertIn("(no materials)", text)


# ---------------------------------------------------------------------------
# Full inspect_materials integration tests with temp files
# ---------------------------------------------------------------------------


class TestInspectMaterialsIntegration(unittest.TestCase):
    def test_base_prefab_file(self) -> None:
        """Write a base prefab to disk, inspect it, verify results."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            text = (
                YAML_HEADER
                + make_gameobject("1", "MeshObj", ["2", "3"])
                + make_transform("2", "1")
                + make_skinned_mesh_renderer("3", "1", [MAT_GUID_A])
            )
            assets_dir = tmp_path / "Assets"
            assets_dir.mkdir()
            prefab_path = assets_dir / "test.prefab"
            prefab_path.write_text(text, encoding="utf-8")

            result = inspect_materials(str(prefab_path), project_root=tmp_path)
            self.assertFalse(result.is_variant)
            self.assertEqual(len(result.renderers), 1)
            self.assertEqual(result.renderers[0].game_object_name, "MeshObj")
            self.assertEqual(result.renderers[0].renderer_type, "SkinnedMeshRenderer")
            self.assertEqual(len(result.renderers[0].slots), 1)

    def test_variant_with_material_override(self) -> None:
        """Write base + variant to disk, verify override detection."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            assets_dir = tmp_path / "Assets"
            assets_dir.mkdir()

            # Write the base prefab
            base_text = (
                YAML_HEADER
                + make_gameobject("1", "Hair", ["2", "3"])
                + make_transform("2", "1")
                + make_skinned_mesh_renderer("3", "1", [MAT_GUID_A, MAT_GUID_B])
            )
            base_path = assets_dir / "Base.prefab"
            base_path.write_text(base_text, encoding="utf-8")

            # Write the .meta file for the base prefab
            meta_path = assets_dir / "Base.prefab.meta"
            meta_path.write_text(
                f"fileFormatVersion: 2\nguid: {BASE_PREFAB_GUID}\n",
                encoding="utf-8",
            )

            # Write the variant
            variant_text = (
                YAML_HEADER
                + make_prefab_variant(
                    source_guid=BASE_PREFAB_GUID,
                    modifications=[
                        {
                            "target": f"{{fileID: 3, guid: {BASE_PREFAB_GUID}, type: 3}}",
                            "propertyPath": "m_Materials.Array.data[0]",
                            "value": "",
                            "objectReference": f"{{fileID: 2100000, guid: {MAT_GUID_C}, type: 2}}",
                        },
                    ],
                )
            )
            variant_path = assets_dir / "Variant.prefab"
            variant_path.write_text(variant_text, encoding="utf-8")

            result = inspect_materials(str(variant_path), project_root=tmp_path)
            self.assertTrue(result.is_variant)
            self.assertEqual(len(result.renderers), 1)
            r = result.renderers[0]
            self.assertEqual(r.game_object_name, "Hair")
            self.assertEqual(len(r.slots), 2)
            # Slot 0 is overridden
            self.assertTrue(r.slots[0].is_override)
            self.assertEqual(r.slots[0].material_guid, MAT_GUID_C.lower())
            # Slot 1 is inherited
            self.assertFalse(r.slots[1].is_override)
            self.assertEqual(r.slots[1].material_guid, MAT_GUID_B.lower())

    def test_multi_level_variant_chain(self) -> None:
        """Walk Variant -> Variant -> Base to find renderer blocks."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            assets_dir = tmp_path / "Assets"
            assets_dir.mkdir()

            # Base prefab with actual renderer blocks
            base_text = (
                YAML_HEADER
                + make_gameobject("1", "Body", ["2", "3"])
                + make_transform("2", "1")
                + make_skinned_mesh_renderer("3", "1", [MAT_GUID_A, MAT_GUID_B])
            )
            base_path = assets_dir / "Base.prefab"
            base_path.write_text(base_text, encoding="utf-8")
            (assets_dir / "Base.prefab.meta").write_text(
                f"fileFormatVersion: 2\nguid: {BASE_PREFAB_GUID}\n",
                encoding="utf-8",
            )

            # Mid-level variant (no renderer blocks, only stripped)
            mid_text = (
                YAML_HEADER
                + "--- !u!137 &3 stripped\n"
                + "SkinnedMeshRenderer:\n"
                + "  m_CorrespondingSourceObject: {fileID: 0}\n"
                + make_prefab_variant(
                    source_guid=BASE_PREFAB_GUID,
                    modifications=[],
                )
            )
            mid_path = assets_dir / "Mid.prefab"
            mid_path.write_text(mid_text, encoding="utf-8")
            (assets_dir / "Mid.prefab.meta").write_text(
                f"fileFormatVersion: 2\nguid: {MID_VARIANT_GUID}\n",
                encoding="utf-8",
            )

            # Leaf variant pointing to mid-level
            leaf_text = (
                YAML_HEADER
                + "--- !u!137 &3 stripped\n"
                + "SkinnedMeshRenderer:\n"
                + "  m_CorrespondingSourceObject: {fileID: 0}\n"
                + make_prefab_variant(
                    source_guid=MID_VARIANT_GUID,
                    modifications=[
                        {
                            "target": f"{{fileID: 3, guid: {BASE_PREFAB_GUID}, type: 3}}",
                            "propertyPath": "m_Materials.Array.data[0]",
                            "value": "",
                            "objectReference": f"{{fileID: 2100000, guid: {MAT_GUID_C}, type: 2}}",
                        },
                    ],
                )
            )
            leaf_path = assets_dir / "Leaf.prefab"
            leaf_path.write_text(leaf_text, encoding="utf-8")

            result = inspect_materials(str(leaf_path), project_root=tmp_path)
            self.assertTrue(result.is_variant)
            # Should find the renderer from Base.prefab
            self.assertEqual(len(result.renderers), 1)
            self.assertEqual(result.renderers[0].game_object_name, "Body")
            self.assertEqual(len(result.renderers[0].slots), 2)
            # Slot 0 overridden in leaf
            self.assertTrue(result.renderers[0].slots[0].is_override)
            self.assertEqual(result.renderers[0].slots[0].material_guid, MAT_GUID_C.lower())
            # Slot 1 inherited from base
            self.assertFalse(result.renderers[0].slots[1].is_override)
            self.assertEqual(result.renderers[0].slots[1].material_guid, MAT_GUID_B.lower())

    def test_stripped_renderer_ignored(self) -> None:
        """Stripped renderer blocks should be skipped."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            text = (
                YAML_HEADER
                + make_gameobject("1", "Root", ["2"])
                + make_transform("2", "1")
                + "--- !u!137 &3 stripped\n"
                + "SkinnedMeshRenderer:\n"
                + "  m_CorrespondingSourceObject: {fileID: 0}\n"
            )
            assets_dir = tmp_path / "Assets"
            assets_dir.mkdir()
            prefab_path = assets_dir / "test.prefab"
            prefab_path.write_text(text, encoding="utf-8")

            result = inspect_materials(str(prefab_path), project_root=tmp_path)
            self.assertEqual(result.renderers, [])


    def _setup_fbx_variant_chain(
        self,
        tmp_path: Path,
        base_modifications: list[dict[str, str]],
        variant_modifications: list[dict[str, str]],
        *,
        extra_base_blocks: str = "",
    ) -> Path:
        """Create Base.prefab (Model Prefab) + Variant.prefab chain."""
        fbx_guid = "dddddddddddddddddddddddddddddddd"
        assets_dir = tmp_path / "Assets"
        assets_dir.mkdir(exist_ok=True)

        base_text = (
            YAML_HEADER
            + extra_base_blocks
            + "--- !u!137 &300 stripped\n"
            + "SkinnedMeshRenderer:\n"
            + "  m_CorrespondingSourceObject: {fileID: 0}\n"
            + "  m_GameObject: {fileID: 100}\n"
            + make_prefab_variant(source_guid=fbx_guid, modifications=base_modifications)
        )
        (assets_dir / "Base.prefab").write_text(base_text, encoding="utf-8")
        (assets_dir / "Base.prefab.meta").write_text(
            f"fileFormatVersion: 2\nguid: {BASE_PREFAB_GUID}\n", encoding="utf-8",
        )

        variant_text = (
            YAML_HEADER
            + "--- !u!137 &300 stripped\n"
            + "SkinnedMeshRenderer:\n"
            + "  m_CorrespondingSourceObject: {fileID: 0}\n"
            + "  m_GameObject: {fileID: 100}\n"
            + make_prefab_variant(source_guid=BASE_PREFAB_GUID, modifications=variant_modifications)
        )
        variant_path = assets_dir / "Variant.prefab"
        variant_path.write_text(variant_text, encoding="utf-8")
        return variant_path

    def test_variant_fbx_chain_stripped_only(self) -> None:
        """Variant -> Base (Model Prefab, all stripped) with m_Modifications materials."""
        fbx_guid = "dddddddddddddddddddddddddddddddd"
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            variant_path = self._setup_fbx_variant_chain(
                tmp_path,
                base_modifications=[
                    {
                        "target": f"{{fileID: 300, guid: {fbx_guid}, type: 3}}",
                        "propertyPath": "m_Materials.Array.data[0]",
                        "value": "",
                        "objectReference": f"{{fileID: 2100000, guid: {MAT_GUID_A}, type: 2}}",
                    },
                    {
                        "target": f"{{fileID: 300, guid: {fbx_guid}, type: 3}}",
                        "propertyPath": "m_Materials.Array.data[1]",
                        "value": "",
                        "objectReference": f"{{fileID: 2100000, guid: {MAT_GUID_B}, type: 2}}",
                    },
                    {
                        "target": f"{{fileID: 100, guid: {fbx_guid}, type: 3}}",
                        "propertyPath": "m_Name",
                        "value": "BodyMesh",
                        "objectReference": "{fileID: 0}",
                    },
                ],
                variant_modifications=[
                    {
                        "target": f"{{fileID: 300, guid: {BASE_PREFAB_GUID}, type: 3}}",
                        "propertyPath": "m_Materials.Array.data[0]",
                        "value": "",
                        "objectReference": f"{{fileID: 2100000, guid: {MAT_GUID_C}, type: 2}}",
                    },
                ],
                extra_base_blocks=(
                    "--- !u!1 &100 stripped\n"
                    "GameObject:\n"
                    "  m_CorrespondingSourceObject: {fileID: 0}\n"
                ),
            )
            result = inspect_materials(str(variant_path), project_root=tmp_path)
            self.assertTrue(result.is_variant)
            self.assertEqual(len(result.renderers), 1)
            self.assertEqual(result.renderers[0].game_object_name, "BodyMesh")
            self.assertEqual(result.renderers[0].renderer_type, "SkinnedMeshRenderer")
            self.assertEqual(len(result.renderers[0].slots), 2)
            # Slot 0 overridden by variant
            self.assertTrue(result.renderers[0].slots[0].is_override)
            self.assertEqual(result.renderers[0].slots[0].material_guid, MAT_GUID_C.lower())
            # Slot 1 inherited from base's m_Modifications
            self.assertFalse(result.renderers[0].slots[1].is_override)
            self.assertEqual(result.renderers[0].slots[1].material_guid, MAT_GUID_B.lower())

    def test_variant_fbx_chain_no_base_mods(self) -> None:
        """Variant -> Base (Model Prefab) with no material m_Modifications in base."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            variant_path = self._setup_fbx_variant_chain(
                tmp_path,
                base_modifications=[],
                variant_modifications=[
                    {
                        "target": f"{{fileID: 300, guid: {BASE_PREFAB_GUID}, type: 3}}",
                        "propertyPath": "m_Materials.Array.data[0]",
                        "value": "",
                        "objectReference": f"{{fileID: 2100000, guid: {MAT_GUID_A}, type: 2}}",
                    },
                ],
            )
            result = inspect_materials(str(variant_path), project_root=tmp_path)
            self.assertTrue(result.is_variant)
            # One renderer with one slot from variant override only
            self.assertEqual(len(result.renderers), 1)
            self.assertEqual(len(result.renderers[0].slots), 1)
            self.assertTrue(result.renderers[0].slots[0].is_override)


if __name__ == "__main__":
    unittest.main()
