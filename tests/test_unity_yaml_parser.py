"""Tests for prefab_sentinel.unity_yaml_parser."""

from __future__ import annotations

import unittest

from prefab_sentinel.unity_yaml_parser import (
    CLASS_ID_GAMEOBJECT,
    CLASS_ID_MONOBEHAVIOUR,
    CLASS_ID_TRANSFORM,
    get_stripped_file_ids,
    parse_components,
    parse_game_objects,
    parse_transforms,
    split_yaml_blocks,
)
from prefab_sentinel.yaml_field_extraction import (
    extract_block_fields,
    parse_yaml_scalar,
)
from tests.yaml_helpers import (
    YAML_HEADER,
    make_gameobject,
    make_monobehaviour,
    make_stripped_transform,
    make_transform,
)

# ---------------------------------------------------------------------------
# split_yaml_blocks
# ---------------------------------------------------------------------------


class TestSplitYamlBlocksEmpty(unittest.TestCase):
    def test_empty_string(self) -> None:
        self.assertEqual(split_yaml_blocks(""), [])

    def test_whitespace_only(self) -> None:
        self.assertEqual(split_yaml_blocks("   \n\n  "), [])

    def test_header_only_no_document(self) -> None:
        """YAML header with no document separator yields no blocks."""
        self.assertEqual(split_yaml_blocks(YAML_HEADER), [])


class TestSplitYamlBlocksSingle(unittest.TestCase):
    def test_single_gameobject(self) -> None:
        text = "--- !u!1 &123\nGameObject:\n  m_Name: Test\n"
        blocks = split_yaml_blocks(text)
        self.assertEqual(len(blocks), 1)
        self.assertEqual(blocks[0].class_id, "1")
        self.assertEqual(blocks[0].file_id, "123")
        self.assertFalse(blocks[0].is_stripped)
        self.assertIn("m_Name: Test", blocks[0].text)

    def test_start_line_first_block(self) -> None:
        text = "--- !u!1 &100\nGameObject:\n  m_Name: A\n"
        blocks = split_yaml_blocks(text)
        self.assertEqual(blocks[0].start_line, 1)

    def test_start_line_with_header(self) -> None:
        text = YAML_HEADER + "--- !u!1 &100\nGameObject:\n  m_Name: A\n"
        blocks = split_yaml_blocks(text)
        # YAML_HEADER has 2 lines, so document starts at line 3
        self.assertEqual(blocks[0].start_line, 3)


class TestSplitYamlBlocksMultiple(unittest.TestCase):
    def test_two_blocks_different_class_ids(self) -> None:
        text = (
            "--- !u!1 &100\nGameObject:\n  m_Name: Root\n"
            "--- !u!4 &200\nTransform:\n  m_GameObject: {fileID: 100}\n"
        )
        blocks = split_yaml_blocks(text)
        self.assertEqual(len(blocks), 2)
        self.assertEqual(blocks[0].class_id, "1")
        self.assertEqual(blocks[0].file_id, "100")
        self.assertEqual(blocks[1].class_id, "4")
        self.assertEqual(blocks[1].file_id, "200")

    def test_three_blocks_with_header(self) -> None:
        text = (
            YAML_HEADER
            + make_gameobject("100", "Root", ["200"])
            + make_transform("200", "100")
            + make_monobehaviour("300", "100")
        )
        blocks = split_yaml_blocks(text)
        self.assertEqual(len(blocks), 3)
        class_ids = [b.class_id for b in blocks]
        self.assertEqual(
            class_ids,
            [CLASS_ID_GAMEOBJECT, CLASS_ID_TRANSFORM, CLASS_ID_MONOBEHAVIOUR],
        )

    def test_block_text_excludes_next_header(self) -> None:
        text = (
            "--- !u!1 &100\nGameObject:\n  m_Name: A\n"
            "--- !u!4 &200\nTransform:\n  m_GameObject: {fileID: 100}\n"
        )
        blocks = split_yaml_blocks(text)
        self.assertNotIn("--- !u!4", blocks[0].text)
        self.assertIn("--- !u!4", blocks[1].text)


class TestSplitYamlBlocksNegativeFileId(unittest.TestCase):
    def test_negative_file_id(self) -> None:
        text = "--- !u!1 &-456\nGameObject:\n  m_Name: Neg\n"
        blocks = split_yaml_blocks(text)
        self.assertEqual(len(blocks), 1)
        self.assertEqual(blocks[0].file_id, "-456")

    def test_mixed_positive_and_negative(self) -> None:
        text = (
            "--- !u!1 &100\nGameObject:\n  m_Name: Pos\n"
            "--- !u!4 &-200\nTransform:\n  m_GameObject: {fileID: 100}\n"
        )
        blocks = split_yaml_blocks(text)
        self.assertEqual(blocks[0].file_id, "100")
        self.assertEqual(blocks[1].file_id, "-200")


class TestSplitYamlBlocksStripped(unittest.TestCase):
    def test_stripped_flag(self) -> None:
        text = "--- !u!4 &500 stripped\nTransform:\n"
        blocks = split_yaml_blocks(text)
        self.assertEqual(len(blocks), 1)
        self.assertTrue(blocks[0].is_stripped)
        self.assertEqual(blocks[0].file_id, "500")

    def test_non_stripped_flag(self) -> None:
        text = "--- !u!4 &500\nTransform:\n  m_GameObject: {fileID: 100}\n"
        blocks = split_yaml_blocks(text)
        self.assertFalse(blocks[0].is_stripped)

    def test_mixed_stripped_and_normal(self) -> None:
        text = (
            "--- !u!1 &100\nGameObject:\n  m_Name: Root\n"
            "--- !u!4 &200 stripped\n"
            "--- !u!4 &300\nTransform:\n  m_GameObject: {fileID: 100}\n"
        )
        blocks = split_yaml_blocks(text)
        self.assertEqual(len(blocks), 3)
        self.assertFalse(blocks[0].is_stripped)
        self.assertTrue(blocks[1].is_stripped)
        self.assertFalse(blocks[2].is_stripped)


# ---------------------------------------------------------------------------
# parse_game_objects
# ---------------------------------------------------------------------------


class TestParseGameObjectsNormal(unittest.TestCase):
    def test_single_gameobject_name(self) -> None:
        text = make_gameobject("100", "MyObject", ["200"])
        blocks = split_yaml_blocks(text)
        gos = parse_game_objects(blocks)
        self.assertIn("100", gos)
        self.assertEqual(gos["100"].name, "MyObject")

    def test_component_file_ids(self) -> None:
        text = make_gameobject("100", "Root", ["200", "300", "400"])
        blocks = split_yaml_blocks(text)
        gos = parse_game_objects(blocks)
        self.assertEqual(gos["100"].component_file_ids, ["200", "300", "400"])


class TestParseGameObjectsMissingName(unittest.TestCase):
    def test_missing_name_returns_empty(self) -> None:
        """A GameObject block without m_Name yields an empty name string."""
        text = "--- !u!1 &100\nGameObject:\n  m_Component:\n  - component: {fileID: 200}\n"
        blocks = split_yaml_blocks(text)
        gos = parse_game_objects(blocks)
        self.assertIn("100", gos)
        self.assertEqual(gos["100"].name, "")

    def test_empty_name_value(self) -> None:
        text = "--- !u!1 &100\nGameObject:\n  m_Name: \n"
        blocks = split_yaml_blocks(text)
        gos = parse_game_objects(blocks)
        self.assertEqual(gos["100"].name, "")


class TestParseGameObjectsMultiple(unittest.TestCase):
    def test_multiple_gameobjects(self) -> None:
        text = (
            make_gameobject("100", "Alpha", ["200"])
            + make_gameobject("300", "Beta", ["400"])
        )
        blocks = split_yaml_blocks(text)
        gos = parse_game_objects(blocks)
        self.assertEqual(len(gos), 2)
        self.assertEqual(gos["100"].name, "Alpha")
        self.assertEqual(gos["300"].name, "Beta")

    def test_skips_non_gameobject_blocks(self) -> None:
        text = (
            make_gameobject("100", "Root", ["200"])
            + make_transform("200", "100")
            + make_monobehaviour("300", "100")
        )
        blocks = split_yaml_blocks(text)
        gos = parse_game_objects(blocks)
        self.assertEqual(len(gos), 1)
        self.assertIn("100", gos)


# ---------------------------------------------------------------------------
# parse_transforms
# ---------------------------------------------------------------------------


class TestParseTransformsStandard(unittest.TestCase):
    def test_basic_transform(self) -> None:
        text = make_transform("200", "100", father_file_id="0")
        blocks = split_yaml_blocks(text)
        transforms = parse_transforms(blocks)
        self.assertIn("200", transforms)
        t = transforms["200"]
        self.assertEqual(t.file_id, "200")
        self.assertEqual(t.game_object_file_id, "100")
        self.assertEqual(t.father_file_id, "0")
        self.assertFalse(t.is_rect_transform)

    def test_default_vectors(self) -> None:
        """The yaml_helpers make_transform uses identity vectors."""
        text = make_transform("200", "100")
        blocks = split_yaml_blocks(text)
        t = parse_transforms(blocks)["200"]
        self.assertEqual(t.local_position, (0.0, 0.0, 0.0))
        self.assertEqual(t.local_rotation, (0.0, 0.0, 0.0, 1.0))
        self.assertEqual(t.local_scale, (1.0, 1.0, 1.0))

    def test_custom_vectors(self) -> None:
        text = (
            "--- !u!4 &200\n"
            "Transform:\n"
            "  m_GameObject: {fileID: 100}\n"
            "  m_Father: {fileID: 0}\n"
            "  m_Children: []\n"
            "  m_LocalPosition: {x: 1.5, y: -2.3, z: 0.001}\n"
            "  m_LocalRotation: {x: 0.1, y: 0.2, z: 0.3, w: 0.9}\n"
            "  m_LocalScale: {x: 2, y: 3, z: 0.5}\n"
        )
        blocks = split_yaml_blocks(text)
        t = parse_transforms(blocks)["200"]
        self.assertAlmostEqual(t.local_position[0], 1.5)
        self.assertAlmostEqual(t.local_position[1], -2.3)
        self.assertAlmostEqual(t.local_position[2], 0.001)
        self.assertAlmostEqual(t.local_rotation[0], 0.1)
        self.assertAlmostEqual(t.local_rotation[3], 0.9)
        self.assertAlmostEqual(t.local_scale[0], 2.0)
        self.assertAlmostEqual(t.local_scale[2], 0.5)

    def test_scientific_notation_vector(self) -> None:
        text = (
            "--- !u!4 &200\n"
            "Transform:\n"
            "  m_GameObject: {fileID: 100}\n"
            "  m_Father: {fileID: 0}\n"
            "  m_Children: []\n"
            "  m_LocalPosition: {x: 1e-05, y: -2.5E+3, z: 0}\n"
            "  m_LocalRotation: {x: 0, y: 0, z: 0, w: 1}\n"
            "  m_LocalScale: {x: 1, y: 1, z: 1}\n"
        )
        blocks = split_yaml_blocks(text)
        t = parse_transforms(blocks)["200"]
        self.assertAlmostEqual(t.local_position[0], 1e-05)
        self.assertAlmostEqual(t.local_position[1], -2500.0)


class TestParseTransformsRectTransform(unittest.TestCase):
    def test_rect_transform_detected(self) -> None:
        text = make_transform("200", "100", is_rect=True)
        blocks = split_yaml_blocks(text)
        transforms = parse_transforms(blocks)
        self.assertTrue(transforms["200"].is_rect_transform)

    def test_regular_transform_not_rect(self) -> None:
        text = make_transform("200", "100", is_rect=False)
        blocks = split_yaml_blocks(text)
        transforms = parse_transforms(blocks)
        self.assertFalse(transforms["200"].is_rect_transform)


class TestParseTransformsChildren(unittest.TestCase):
    def test_empty_children_inline(self) -> None:
        text = make_transform("200", "100")  # default: m_Children: []
        blocks = split_yaml_blocks(text)
        t = parse_transforms(blocks)["200"]
        self.assertEqual(t.children_file_ids, [])

    def test_children_with_references(self) -> None:
        text = make_transform("200", "100", children_file_ids=["300", "400"])
        blocks = split_yaml_blocks(text)
        t = parse_transforms(blocks)["200"]
        self.assertEqual(t.children_file_ids, ["300", "400"])

    def test_children_multiline_format(self) -> None:
        """m_Children in block-sequence form (one child per line)."""
        text = (
            "--- !u!4 &200\n"
            "Transform:\n"
            "  m_GameObject: {fileID: 100}\n"
            "  m_Father: {fileID: 0}\n"
            "  m_Children:\n"
            "  - {fileID: 300}\n"
            "  - {fileID: 400}\n"
            "  - {fileID: 500}\n"
            "  m_LocalPosition: {x: 0, y: 0, z: 0}\n"
            "  m_LocalRotation: {x: 0, y: 0, z: 0, w: 1}\n"
            "  m_LocalScale: {x: 1, y: 1, z: 1}\n"
        )
        blocks = split_yaml_blocks(text)
        t = parse_transforms(blocks)["200"]
        self.assertEqual(t.children_file_ids, ["300", "400", "500"])


class TestParseTransformsStripped(unittest.TestCase):
    def test_stripped_transforms_skipped(self) -> None:
        text = make_stripped_transform("200")
        blocks = split_yaml_blocks(text)
        transforms = parse_transforms(blocks)
        self.assertEqual(len(transforms), 0)


# ---------------------------------------------------------------------------
# parse_components
# ---------------------------------------------------------------------------


class TestParseComponentsMonoBehaviour(unittest.TestCase):
    def test_monobehaviour_with_script_guid(self) -> None:
        guid = "abcd1234abcd1234abcd1234abcd1234"
        text = make_monobehaviour("300", "100", guid=guid)
        blocks = split_yaml_blocks(text)
        comps = parse_components(blocks)
        self.assertIn("300", comps)
        self.assertEqual(comps["300"].class_id, CLASS_ID_MONOBEHAVIOUR)
        self.assertEqual(comps["300"].script_guid, guid)
        self.assertEqual(comps["300"].game_object_file_id, "100")

    def test_script_guid_normalized_lowercase(self) -> None:
        guid_upper = "ABCD1234ABCD1234ABCD1234ABCD1234"
        text = (
            "--- !u!114 &300\n"
            "MonoBehaviour:\n"
            f"  m_Script: {{fileID: 11500000, guid: {guid_upper}, type: 3}}\n"
            "  m_GameObject: {fileID: 100}\n"
        )
        blocks = split_yaml_blocks(text)
        comps = parse_components(blocks)
        self.assertEqual(comps["300"].script_guid, guid_upper.lower())


class TestParseComponentsNonMono(unittest.TestCase):
    def test_non_monobehaviour_no_script_guid(self) -> None:
        """A MeshFilter (classID 33) has no m_Script, so script_guid is empty."""
        text = (
            "--- !u!33 &300\n"
            "MeshFilter:\n"
            "  m_GameObject: {fileID: 100}\n"
        )
        blocks = split_yaml_blocks(text)
        comps = parse_components(blocks)
        self.assertIn("300", comps)
        self.assertEqual(comps["300"].script_guid, "")
        self.assertEqual(comps["300"].class_id, "33")

    def test_component_gameobject_reference(self) -> None:
        text = (
            "--- !u!23 &400\n"
            "MeshRenderer:\n"
            "  m_GameObject: {fileID: 100}\n"
        )
        blocks = split_yaml_blocks(text)
        comps = parse_components(blocks)
        self.assertEqual(comps["400"].game_object_file_id, "100")


class TestParseComponentsSkipsGameObjectAndTransform(unittest.TestCase):
    def test_gameobject_not_in_components(self) -> None:
        text = make_gameobject("100", "Root", ["200"])
        blocks = split_yaml_blocks(text)
        comps = parse_components(blocks)
        self.assertNotIn("100", comps)

    def test_transform_not_in_components(self) -> None:
        text = make_transform("200", "100")
        blocks = split_yaml_blocks(text)
        comps = parse_components(blocks)
        self.assertNotIn("200", comps)

    def test_rect_transform_not_in_components(self) -> None:
        text = make_transform("200", "100", is_rect=True)
        blocks = split_yaml_blocks(text)
        comps = parse_components(blocks)
        self.assertNotIn("200", comps)


class TestParseComponentsWithExternalReference(unittest.TestCase):
    def test_component_with_external_field_reference(self) -> None:
        """A MonoBehaviour with an additional external reference field."""
        text = (
            "--- !u!114 &300\n"
            "MonoBehaviour:\n"
            "  m_GameObject: {fileID: 100}\n"
            "  m_Script: {fileID: 11500000, guid: abcd1234abcd1234abcd1234abcd1234, type: 3}\n"
            "  someField: {fileID: 444, guid: deadbeefdeadbeefdeadbeefdeadbeef, type: 2}\n"
        )
        blocks = split_yaml_blocks(text)
        comps = parse_components(blocks)
        # parse_components only extracts script_guid, not arbitrary field references
        self.assertEqual(
            comps["300"].script_guid, "abcd1234abcd1234abcd1234abcd1234"
        )


# ---------------------------------------------------------------------------
# get_stripped_file_ids
# ---------------------------------------------------------------------------


class TestGetStrippedFileIds(unittest.TestCase):
    def test_with_stripped_blocks(self) -> None:
        text = (
            "--- !u!1 &100\nGameObject:\n  m_Name: Root\n"
            "--- !u!4 &200 stripped\n"
            "--- !u!224 &300 stripped\n"
            "--- !u!4 &400\nTransform:\n  m_GameObject: {fileID: 100}\n"
        )
        blocks = split_yaml_blocks(text)
        stripped = get_stripped_file_ids(blocks)
        self.assertEqual(stripped, frozenset({"200", "300"}))

    def test_no_stripped_blocks(self) -> None:
        text = (
            make_gameobject("100", "Root", ["200"])
            + make_transform("200", "100")
        )
        blocks = split_yaml_blocks(text)
        stripped = get_stripped_file_ids(blocks)
        self.assertEqual(stripped, frozenset())

    def test_empty_input(self) -> None:
        blocks = split_yaml_blocks("")
        stripped = get_stripped_file_ids(blocks)
        self.assertEqual(stripped, frozenset())


# ---------------------------------------------------------------------------
# Integrated scenario: full Unity YAML document
# ---------------------------------------------------------------------------


class TestIntegratedFullDocument(unittest.TestCase):
    """End-to-end test with a realistic multi-block Unity YAML document."""

    FULL_YAML = (
        YAML_HEADER
        + "--- !u!1 &111\n"
        "GameObject:\n"
        "  m_Component:\n"
        "  - component: {fileID: 222}\n"
        "  - component: {fileID: 333}\n"
        "  m_Name: MyObject\n"
        "--- !u!4 &222\n"
        "Transform:\n"
        "  m_LocalPosition: {x: 0, y: 0, z: 0}\n"
        "  m_LocalRotation: {x: 0, y: 0, z: 0, w: 1}\n"
        "  m_LocalScale: {x: 1, y: 1, z: 1}\n"
        "  m_Children:\n"
        "  - {fileID: 555}\n"
        "  m_Father: {fileID: 0}\n"
        "  m_GameObject: {fileID: 111}\n"
        "--- !u!114 &333\n"
        "MonoBehaviour:\n"
        "  m_Script: {fileID: 11500000, guid: abcdef1234567890abcdef1234567890, type: 3}\n"
        "  m_GameObject: {fileID: 111}\n"
        "  someField: {fileID: 444, guid: deadbeefdeadbeefdeadbeefdeadbeef, type: 2}\n"
        "--- !u!4 &555 stripped\n"
    )

    def test_block_count(self) -> None:
        blocks = split_yaml_blocks(self.FULL_YAML)
        self.assertEqual(len(blocks), 4)

    def test_gameobject_parsed(self) -> None:
        blocks = split_yaml_blocks(self.FULL_YAML)
        gos = parse_game_objects(blocks)
        self.assertEqual(len(gos), 1)
        go = gos["111"]
        self.assertEqual(go.name, "MyObject")
        self.assertEqual(go.component_file_ids, ["222", "333"])

    def test_transform_parsed(self) -> None:
        blocks = split_yaml_blocks(self.FULL_YAML)
        transforms = parse_transforms(blocks)
        # stripped block (555) should be skipped
        self.assertEqual(len(transforms), 1)
        t = transforms["222"]
        self.assertEqual(t.game_object_file_id, "111")
        self.assertEqual(t.father_file_id, "0")
        self.assertEqual(t.children_file_ids, ["555"])

    def test_component_parsed(self) -> None:
        blocks = split_yaml_blocks(self.FULL_YAML)
        comps = parse_components(blocks)
        self.assertEqual(len(comps), 1)
        c = comps["333"]
        self.assertEqual(c.script_guid, "abcdef1234567890abcdef1234567890")
        self.assertEqual(c.game_object_file_id, "111")

    def test_stripped_ids(self) -> None:
        blocks = split_yaml_blocks(self.FULL_YAML)
        stripped = get_stripped_file_ids(blocks)
        self.assertEqual(stripped, frozenset({"555"}))


# ---------------------------------------------------------------------------
# extract_block_fields
# ---------------------------------------------------------------------------


class TestExtractBlockFieldsScalars(unittest.TestCase):
    """Simple scalar fields are extracted as (field_name, raw_value)."""

    def test_simple_scalars(self) -> None:
        block = (
            "--- !u!23 &300\n"
            "MeshRenderer:\n"
            "  m_Enabled: 1\n"
            "  m_CastShadows: 0\n"
        )
        result = extract_block_fields(block)
        self.assertIn(("m_Enabled", "1"), result)
        self.assertIn(("m_CastShadows", "0"), result)

    def test_flow_mapping_value(self) -> None:
        block = (
            "--- !u!23 &300\n"
            "MeshRenderer:\n"
            "  m_GameObject: {fileID: 100}\n"
        )
        result = extract_block_fields(block)
        self.assertIn(("m_GameObject", "{fileID: 100}"), result)


class TestExtractBlockFieldsArrays(unittest.TestCase):
    """Array fields produce indexed Array.data[N] paths."""

    def test_array_elements_indexed(self) -> None:
        block = (
            "--- !u!23 &300\n"
            "MeshRenderer:\n"
            "  m_Materials:\n"
            "  - {fileID: 2100000, guid: aaa, type: 2}\n"
            "  - {fileID: 2100000, guid: bbb, type: 2}\n"
        )
        result = extract_block_fields(block)
        self.assertIn(
            ("m_Materials.Array.data[0]", "{fileID: 2100000, guid: aaa, type: 2}"),
            result,
        )
        self.assertIn(
            ("m_Materials.Array.data[1]", "{fileID: 2100000, guid: bbb, type: 2}"),
            result,
        )

    def test_empty_array_skipped(self) -> None:
        block = (
            "--- !u!23 &300\n"
            "MeshRenderer:\n"
            "  m_Materials: []\n"
        )
        result = extract_block_fields(block)
        field_names = [name for name, _ in result]
        self.assertNotIn("m_Materials", field_names)

    def test_non_m_prefix_array_field(self) -> None:
        """Custom MonoBehaviour arrays without m_ prefix use next-line lookahead."""
        block = (
            "--- !u!114 &400\n"
            "MonoBehaviour:\n"
            "  targetObjects:\n"
            "  - {fileID: 500}\n"
            "  - {fileID: 600}\n"
        )
        result = extract_block_fields(block)
        self.assertIn(("targetObjects.Array.data[0]", "{fileID: 500}"), result)
        self.assertIn(("targetObjects.Array.data[1]", "{fileID: 600}"), result)


class TestExtractBlockFieldsEdgeCases(unittest.TestCase):
    """Header lines, empty lines, and nested sub-objects."""

    def test_header_and_type_line_skipped(self) -> None:
        block = (
            "--- !u!23 &300\n"
            "MeshRenderer:\n"
            "  m_Enabled: 1\n"
        )
        result = extract_block_fields(block)
        field_names = [name for name, _ in result]
        self.assertNotIn("---", field_names)
        self.assertNotIn("MeshRenderer", field_names)

    def test_nested_subobject_skipped(self) -> None:
        """A field with empty value whose next line is NOT '- ' is a sub-object header."""
        block = (
            "--- !u!4 &200\n"
            "Transform:\n"
            "  m_LocalPosition:\n"
            "    x: 1\n"
            "    y: 2\n"
            "    z: 3\n"
            "  m_Enabled: 1\n"
        )
        result = extract_block_fields(block)
        field_names = [name for name, _ in result]
        self.assertNotIn("m_LocalPosition", field_names)
        self.assertNotIn("x", field_names)
        self.assertIn("m_Enabled", field_names)

    def test_empty_lines_ignored(self) -> None:
        block = (
            "--- !u!23 &300\n"
            "MeshRenderer:\n"
            "\n"
            "  m_Enabled: 1\n"
            "\n"
        )
        result = extract_block_fields(block)
        self.assertIn(("m_Enabled", "1"), result)


# ---------------------------------------------------------------------------
# parse_yaml_scalar
# ---------------------------------------------------------------------------


class TestParseYamlScalarNumeric(unittest.TestCase):
    """Numeric strings are parsed to int or float."""

    def test_integer(self) -> None:
        self.assertEqual(parse_yaml_scalar("42"), 42)

    def test_negative_integer(self) -> None:
        self.assertEqual(parse_yaml_scalar("-7"), -7)

    def test_float(self) -> None:
        self.assertAlmostEqual(parse_yaml_scalar("3.14"), 3.14)


class TestParseYamlScalarOther(unittest.TestCase):
    """Strings, flow mappings, and edge cases."""

    def test_string(self) -> None:
        self.assertEqual(parse_yaml_scalar("hello"), "hello")

    def test_flow_mapping(self) -> None:
        result = parse_yaml_scalar("{fileID: 2100000, guid: abc, type: 2}")
        self.assertEqual(result, {"fileID": 2100000, "guid": "abc", "type": 2})

    def test_empty_string(self) -> None:
        self.assertEqual(parse_yaml_scalar(""), "")


if __name__ == "__main__":
    unittest.main()
