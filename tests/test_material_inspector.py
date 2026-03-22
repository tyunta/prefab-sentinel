"""Tests for prefab_sentinel.material_inspector."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

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
from prefab_sentinel.unity_yaml_parser import YamlBlock, split_yaml_blocks
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


# ---------------------------------------------------------------------------
# _parse_renderer_materials tests
# ---------------------------------------------------------------------------


class TestParseRendererMaterials:
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
        assert result == []

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
        assert len(result) == 1
        assert result[0] == ("2100000", MAT_GUID_A.lower())

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
        assert len(result) == 2
        assert result[0][1] == MAT_GUID_A.lower()
        assert result[1][1] == MAT_GUID_B.lower()


# ---------------------------------------------------------------------------
# _parse_material_overrides tests
# ---------------------------------------------------------------------------


class TestParseMaterialOverrides:
    def test_no_overrides(self) -> None:
        text = (
            "--- !u!1001 &100100000\n"
            "PrefabInstance:\n"
            "  m_Modification:\n"
            "    m_Modifications: []\n"
            f"  m_SourcePrefab: {{fileID: 100100000, guid: {BASE_PREFAB_GUID}, type: 3}}\n"
        )
        out: dict[tuple[str, int], str] = {}
        _parse_material_overrides(text, BASE_PREFAB_GUID, out)
        assert out == {}

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
        _parse_material_overrides(text, BASE_PREFAB_GUID, out)
        assert ("42", 0) in out
        assert out[("42", 0)] == MAT_GUID_C.lower()

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
        _parse_material_overrides(text, BASE_PREFAB_GUID, out)
        assert len(out) == 2
        assert out[("42", 0)] == MAT_GUID_B.lower()
        assert out[("42", 1)] == MAT_GUID_C.lower()

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
        _parse_material_overrides(text, BASE_PREFAB_GUID, out)
        assert out == {}


# ---------------------------------------------------------------------------
# _inspect_base_materials tests
# ---------------------------------------------------------------------------


class TestInspectBaseMaterials:
    def test_single_renderer_with_materials(self) -> None:
        text = (
            YAML_HEADER
            + make_gameobject("1", "Hair_Base", ["2", "3"])
            + make_transform("2", "1")
            + make_skinned_mesh_renderer("3", "1", [MAT_GUID_A, MAT_GUID_B])
        )
        result = _inspect_base_materials("Assets/test.prefab", text, Path("/tmp"), {})
        assert len(result.renderers) == 1
        r = result.renderers[0]
        assert r.game_object_name == "Hair_Base"
        assert r.renderer_type == "SkinnedMeshRenderer"
        assert len(r.slots) == 2
        assert r.slots[0].index == 0
        assert r.slots[0].material_guid == MAT_GUID_A.lower()
        assert r.slots[1].index == 1
        assert not r.slots[0].is_override
        assert not result.is_variant

    def test_mesh_renderer_detected(self) -> None:
        text = (
            YAML_HEADER
            + make_gameobject("1", "Cube", ["2", "3"])
            + make_transform("2", "1")
            + make_meshrenderer_with_materials("3", "1", [MAT_GUID_A])
        )
        result = _inspect_base_materials("Assets/test.prefab", text, Path("/tmp"), {})
        assert len(result.renderers) == 1
        assert result.renderers[0].renderer_type == "MeshRenderer"

    def test_no_renderers(self) -> None:
        text = (
            YAML_HEADER
            + make_gameobject("1", "Empty", ["2"])
            + make_transform("2", "1")
        )
        result = _inspect_base_materials("Assets/test.prefab", text, Path("/tmp"), {})
        assert result.renderers == []

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
        assert len(result.renderers) == 2
        names = [r.game_object_name for r in result.renderers]
        assert "Hair" in names
        assert "Body" in names


# ---------------------------------------------------------------------------
# format_materials tests
# ---------------------------------------------------------------------------


class TestFormatMaterials:
    def test_empty_renderers(self) -> None:
        result = MaterialInspectionResult(
            target_path="Assets/test.prefab",
            is_variant=False,
            base_prefab_path=None,
            renderers=[],
        )
        assert format_materials(result) == "(no renderer components found)"

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
        assert "Hair (SkinnedMeshRenderer)" in text
        assert "[0] mat_hair (Assets/Materials/mat_hair.mat)" in text
        # No override/inherited markers for non-variants
        assert "[override]" not in text
        assert "[inherited]" not in text

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
        assert "Hair_Base (SkinnedMeshRenderer)" in text
        assert "[override]" in text
        assert "[inherited]" in text
        lines = text.split("\n")
        # First slot overridden
        assert "[override]" in lines[1]
        # Second slot inherited
        assert "[inherited]" in lines[2]

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
        assert "(no materials)" in text


# ---------------------------------------------------------------------------
# Full inspect_materials integration tests with temp files
# ---------------------------------------------------------------------------


class TestInspectMaterialsIntegration:
    def test_base_prefab_file(self, tmp_path: Path) -> None:
        """Write a base prefab to disk, inspect it, verify results."""
        text = (
            YAML_HEADER
            + make_gameobject("1", "MeshObj", ["2", "3"])
            + make_transform("2", "1")
            + make_skinned_mesh_renderer("3", "1", [MAT_GUID_A])
        )
        # Create a minimal project structure
        assets_dir = tmp_path / "Assets"
        assets_dir.mkdir()
        prefab_path = assets_dir / "test.prefab"
        prefab_path.write_text(text, encoding="utf-8")

        result = inspect_materials(str(prefab_path), project_root=tmp_path)
        assert not result.is_variant
        assert len(result.renderers) == 1
        assert result.renderers[0].game_object_name == "MeshObj"
        assert result.renderers[0].renderer_type == "SkinnedMeshRenderer"
        assert len(result.renderers[0].slots) == 1

    def test_variant_with_material_override(self, tmp_path: Path) -> None:
        """Write base + variant to disk, verify override detection."""
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
        assert result.is_variant
        assert len(result.renderers) == 1
        r = result.renderers[0]
        assert r.game_object_name == "Hair"
        assert len(r.slots) == 2
        # Slot 0 is overridden
        assert r.slots[0].is_override
        assert r.slots[0].material_guid == MAT_GUID_C.lower()
        # Slot 1 is inherited
        assert not r.slots[1].is_override
        assert r.slots[1].material_guid == MAT_GUID_B.lower()

    def test_stripped_renderer_ignored(self, tmp_path: Path) -> None:
        """Stripped renderer blocks should be skipped."""
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
        assert result.renderers == []
