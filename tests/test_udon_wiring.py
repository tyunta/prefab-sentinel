"""Tests for prefab_sentinel.udon_wiring module."""

from __future__ import annotations

import unittest

from prefab_sentinel.contracts import Severity
from prefab_sentinel.udon_wiring import (
    SKIP_FIELDS,
    UDON_BEHAVIOUR_GUID,
    _parse_monobehaviour_fields,
    analyze_wiring,
    extract_monobehaviour_field_names,
)
from prefab_sentinel.unity_yaml_parser import (
    YamlBlock,
    parse_game_objects,
    split_yaml_blocks,
)

# ---------------------------------------------------------------------------
# Synthetic YAML fragments
# ---------------------------------------------------------------------------

BASIC_MONOBEHAVIOUR = """\
%YAML 1.1
%TAG !u! tag:unity3d.com,2011:
--- !u!1 &100000
GameObject:
  m_ObjectHideFlags: 0
  m_Name: TestObject
  m_Component:
  - component: {fileID: 100001}
--- !u!114 &100001
MonoBehaviour:
  m_ObjectHideFlags: 0
  m_CorrespondingSourceObject: {fileID: 0}
  m_PrefabInstance: {fileID: 0}
  m_PrefabAsset: {fileID: 0}
  m_GameObject: {fileID: 100000}
  m_Enabled: 1
  m_EditorHideFlags: 0
  m_Script: {fileID: 11500000, guid: aabbccdd11223344aabbccdd11223344, type: 3}
  m_Name:
  m_EditorClassIdentifier:
  myRef: {fileID: 100000}
  myNullRef: {fileID: 0}
  myValue: 42
"""

UDON_SHARP_BLOCK = """\
%YAML 1.1
%TAG !u! tag:unity3d.com,2011:
--- !u!1 &200000
GameObject:
  m_ObjectHideFlags: 0
  m_Name: UdonObject
  m_Component:
  - component: {fileID: 200001}
  - component: {fileID: 200002}
--- !u!114 &200001
MonoBehaviour:
  m_ObjectHideFlags: 0
  m_CorrespondingSourceObject: {fileID: 0}
  m_PrefabInstance: {fileID: 0}
  m_PrefabAsset: {fileID: 0}
  m_GameObject: {fileID: 200000}
  m_Enabled: 1
  m_EditorHideFlags: 0
  m_Script: {fileID: 11500000, guid: deadbeef12345678deadbeef12345678, type: 3}
  m_Name:
  m_EditorClassIdentifier:
  _udonSharpBackingUdonBehaviour: {fileID: 200002}
  someField: {fileID: 200000}
--- !u!114 &200002
MonoBehaviour:
  m_ObjectHideFlags: 0
  m_CorrespondingSourceObject: {fileID: 0}
  m_PrefabInstance: {fileID: 0}
  m_PrefabAsset: {fileID: 0}
  m_GameObject: {fileID: 200000}
  m_Enabled: 1
  m_EditorHideFlags: 0
  m_Script: {fileID: 11500000, guid: 45115577ef41a5b4ca741ed302693907, type: 3}
  m_Name:
  m_EditorClassIdentifier:
  serializedProgramAsset: {fileID: 0}
"""

BROKEN_INTERNAL_REF = """\
%YAML 1.1
%TAG !u! tag:unity3d.com,2011:
--- !u!1 &300000
GameObject:
  m_ObjectHideFlags: 0
  m_Name: BrokenObj
  m_Component:
  - component: {fileID: 300001}
--- !u!114 &300001
MonoBehaviour:
  m_ObjectHideFlags: 0
  m_CorrespondingSourceObject: {fileID: 0}
  m_PrefabInstance: {fileID: 0}
  m_PrefabAsset: {fileID: 0}
  m_GameObject: {fileID: 300000}
  m_Enabled: 1
  m_EditorHideFlags: 0
  m_Script: {fileID: 11500000, guid: aabbccdd11223344aabbccdd11223344, type: 3}
  m_Name:
  m_EditorClassIdentifier:
  badRef: {fileID: 999999}
"""

DUPLICATE_REF = """\
%YAML 1.1
%TAG !u! tag:unity3d.com,2011:
--- !u!1 &400000
GameObject:
  m_ObjectHideFlags: 0
  m_Name: DupObj
  m_Component:
  - component: {fileID: 400001}
  - component: {fileID: 400002}
--- !u!114 &400001
MonoBehaviour:
  m_ObjectHideFlags: 0
  m_CorrespondingSourceObject: {fileID: 0}
  m_PrefabInstance: {fileID: 0}
  m_PrefabAsset: {fileID: 0}
  m_GameObject: {fileID: 400000}
  m_Enabled: 1
  m_EditorHideFlags: 0
  m_Script: {fileID: 11500000, guid: aabbccdd11223344aabbccdd11223344, type: 3}
  m_Name:
  m_EditorClassIdentifier:
  refA: {fileID: 400000}
--- !u!114 &400002
MonoBehaviour:
  m_ObjectHideFlags: 0
  m_CorrespondingSourceObject: {fileID: 0}
  m_PrefabInstance: {fileID: 0}
  m_PrefabAsset: {fileID: 0}
  m_GameObject: {fileID: 400000}
  m_Enabled: 1
  m_EditorHideFlags: 0
  m_Script: {fileID: 11500000, guid: bbccddee11223344bbccddee11223344, type: 3}
  m_Name:
  m_EditorClassIdentifier:
  refB: {fileID: 400000}
"""

ARRAY_FIELDS = """\
%YAML 1.1
%TAG !u! tag:unity3d.com,2011:
--- !u!1 &500000
GameObject:
  m_ObjectHideFlags: 0
  m_Name: ArrayObj
  m_Component:
  - component: {fileID: 500001}
--- !u!114 &500001
MonoBehaviour:
  m_ObjectHideFlags: 0
  m_CorrespondingSourceObject: {fileID: 0}
  m_PrefabInstance: {fileID: 0}
  m_PrefabAsset: {fileID: 0}
  m_GameObject: {fileID: 500000}
  m_Enabled: 1
  m_EditorHideFlags: 0
  m_Script: {fileID: 11500000, guid: aabbccdd11223344aabbccdd11223344, type: 3}
  m_Name:
  m_EditorClassIdentifier:
  targets:
  - {fileID: 500000}
  - {fileID: 0}
  - {fileID: 500001}
"""

SERIALIZATION_DATA_BLOCK = """\
%YAML 1.1
%TAG !u! tag:unity3d.com,2011:
--- !u!1 &600000
GameObject:
  m_ObjectHideFlags: 0
  m_Name: SerObj
  m_Component:
  - component: {fileID: 600001}
--- !u!114 &600001
MonoBehaviour:
  m_ObjectHideFlags: 0
  m_CorrespondingSourceObject: {fileID: 0}
  m_PrefabInstance: {fileID: 0}
  m_PrefabAsset: {fileID: 0}
  m_GameObject: {fileID: 600000}
  m_Enabled: 1
  m_EditorHideFlags: 0
  m_Script: {fileID: 11500000, guid: aabbccdd11223344aabbccdd11223344, type: 3}
  m_Name:
  m_EditorClassIdentifier:
  serializationData:
    SerializedBytes:
    SerializedBytesString:
    Prefab: {fileID: 0}
    PrefabModificationsReferencedUnityObjects: []
    SerializationNodes:
    - Name: shouldBeIgnored
      Entry: 7
      Data: {fileID: 999999}
  realField: {fileID: 600000}
"""

CLEAN_FILE = """\
%YAML 1.1
%TAG !u! tag:unity3d.com,2011:
--- !u!1 &700000
GameObject:
  m_ObjectHideFlags: 0
  m_Name: CleanObj
  m_Component:
  - component: {fileID: 700001}
--- !u!114 &700001
MonoBehaviour:
  m_ObjectHideFlags: 0
  m_CorrespondingSourceObject: {fileID: 0}
  m_PrefabInstance: {fileID: 0}
  m_PrefabAsset: {fileID: 0}
  m_GameObject: {fileID: 700000}
  m_Enabled: 1
  m_EditorHideFlags: 0
  m_Script: {fileID: 11500000, guid: aabbccdd11223344aabbccdd11223344, type: 3}
  m_Name:
  m_EditorClassIdentifier:
  validRef: {fileID: 700000}
"""

SAME_COMPONENT_DUPLICATE = """\
%YAML 1.1
%TAG !u! tag:unity3d.com,2011:
--- !u!1 &800000
GameObject:
  m_ObjectHideFlags: 0
  m_Name: SameDupObj
  m_Component:
  - component: {fileID: 800001}
--- !u!114 &800001
MonoBehaviour:
  m_ObjectHideFlags: 0
  m_CorrespondingSourceObject: {fileID: 0}
  m_PrefabInstance: {fileID: 0}
  m_PrefabAsset: {fileID: 0}
  m_GameObject: {fileID: 800000}
  m_Enabled: 1
  m_EditorHideFlags: 0
  m_Script: {fileID: 11500000, guid: aabbccdd11223344aabbccdd11223344, type: 3}
  m_Name:
  m_EditorClassIdentifier:
  fieldA: {fileID: 800000}
  fieldB: {fileID: 800000}
"""

NESTED_STRUCT = """\
%YAML 1.1
%TAG !u! tag:unity3d.com,2011:
--- !u!1 &1000000
GameObject:
  m_ObjectHideFlags: 0
  m_Name: NestedObj
  m_Component:
  - component: {fileID: 1000001}
--- !u!114 &1000001
MonoBehaviour:
  m_ObjectHideFlags: 0
  m_CorrespondingSourceObject: {fileID: 0}
  m_PrefabInstance: {fileID: 0}
  m_PrefabAsset: {fileID: 0}
  m_GameObject: {fileID: 1000000}
  m_Enabled: 1
  m_EditorHideFlags: 0
  m_Script: {fileID: 11500000, guid: aabbccdd11223344aabbccdd11223344, type: 3}
  m_Name:
  m_EditorClassIdentifier:
  topRef: {fileID: 1000000}
  customStruct:
    innerRef: {fileID: 0}
    deeperNested:
      veryDeep: {fileID: 999999}
  afterNested: {fileID: 1000000}
"""


# ---------------------------------------------------------------------------
# split_yaml_blocks tests
# ---------------------------------------------------------------------------


class SplitYamlBlocksTests(unittest.TestCase):
    def test_empty_text(self) -> None:
        self.assertEqual(split_yaml_blocks(""), [])
        self.assertEqual(split_yaml_blocks("   \n  "), [])

    def test_single_block(self) -> None:
        text = "--- !u!1 &100\nGameObject:\n  m_Name: A\n"
        blocks = split_yaml_blocks(text)
        self.assertEqual(len(blocks), 1)
        self.assertEqual(blocks[0].class_id, "1")
        self.assertEqual(blocks[0].file_id, "100")
        self.assertIn("m_Name: A", blocks[0].text)

    def test_multiple_blocks(self) -> None:
        blocks = split_yaml_blocks(BASIC_MONOBEHAVIOUR)
        self.assertEqual(len(blocks), 2)
        class_ids = {b.class_id for b in blocks}
        self.assertIn("1", class_ids)
        self.assertIn("114", class_ids)

    def test_negative_file_ids(self) -> None:
        """Prefab Variants use negative fileIDs."""
        text = "--- !u!114 &-1234567890\nMonoBehaviour:\n  m_Name: Neg\n"
        blocks = split_yaml_blocks(text)
        self.assertEqual(len(blocks), 1)
        self.assertEqual(blocks[0].file_id, "-1234567890")

    def test_stripped_block_sets_is_stripped(self) -> None:
        text = "--- !u!4 &1234 stripped\nTransform:\n  m_Father: {fileID: 0}\n"
        blocks = split_yaml_blocks(text)
        self.assertEqual(len(blocks), 1)
        self.assertTrue(blocks[0].is_stripped)
        self.assertEqual(blocks[0].file_id, "1234")

    def test_non_stripped_block_is_false(self) -> None:
        text = "--- !u!4 &1234\nTransform:\n  m_Father: {fileID: 0}\n"
        blocks = split_yaml_blocks(text)
        self.assertEqual(len(blocks), 1)
        self.assertFalse(blocks[0].is_stripped)

    def test_block_start_lines(self) -> None:
        blocks = split_yaml_blocks(BASIC_MONOBEHAVIOUR)
        # First block starts on the line with "--- !u!1 &100000"
        self.assertGreater(blocks[0].start_line, 0)
        self.assertGreater(blocks[1].start_line, blocks[0].start_line)


# ---------------------------------------------------------------------------
# _parse_monobehaviour_fields tests
# ---------------------------------------------------------------------------


class ParseMonoBehaviourFieldsTests(unittest.TestCase):
    def _get_mono_block(self, text: str) -> YamlBlock:
        blocks = split_yaml_blocks(text)
        for b in blocks:
            if b.class_id == "114":
                return b
        raise AssertionError("No MonoBehaviour block found")

    def test_extracts_user_fields(self) -> None:
        block = self._get_mono_block(BASIC_MONOBEHAVIOUR)
        comp = _parse_monobehaviour_fields(block)
        self.assertIsNotNone(comp)
        assert comp is not None
        field_names = {f.name for f in comp.fields}
        self.assertIn("myRef", field_names)
        self.assertIn("myNullRef", field_names)
        # myValue has no {fileID:...} pattern so it is not captured
        self.assertNotIn("myValue", field_names)

    def test_skip_fields_excluded(self) -> None:
        block = self._get_mono_block(BASIC_MONOBEHAVIOUR)
        comp = _parse_monobehaviour_fields(block)
        assert comp is not None
        field_names = {f.name for f in comp.fields}
        for skip in SKIP_FIELDS:
            self.assertNotIn(skip, field_names)

    def test_game_object_file_id(self) -> None:
        block = self._get_mono_block(BASIC_MONOBEHAVIOUR)
        comp = _parse_monobehaviour_fields(block)
        assert comp is not None
        self.assertEqual(comp.game_object_file_id, "100000")

    def test_script_guid(self) -> None:
        block = self._get_mono_block(BASIC_MONOBEHAVIOUR)
        comp = _parse_monobehaviour_fields(block)
        assert comp is not None
        self.assertEqual(comp.script_guid, "aabbccdd11223344aabbccdd11223344")

    def test_udon_sharp_detected(self) -> None:
        blocks = split_yaml_blocks(UDON_SHARP_BLOCK)
        mono_blocks = [b for b in blocks if b.class_id == "114"]
        results = [_parse_monobehaviour_fields(b) for b in mono_blocks]
        parsed = [r for r in results if r is not None]
        # UdonBehaviour block (GUID match) should be excluded
        self.assertEqual(len(parsed), 1)
        comp = parsed[0]
        self.assertTrue(comp.is_udon_sharp)
        self.assertEqual(comp.backing_udon_file_id, "200002")

    def test_udon_behaviour_excluded(self) -> None:
        blocks = split_yaml_blocks(UDON_SHARP_BLOCK)
        mono_blocks = [b for b in blocks if b.class_id == "114"]
        for b in mono_blocks:
            comp = _parse_monobehaviour_fields(b)
            if comp is not None:
                self.assertNotEqual(comp.script_guid, UDON_BEHAVIOUR_GUID)

    def test_non_monobehaviour_returns_none(self) -> None:
        blocks = split_yaml_blocks(BASIC_MONOBEHAVIOUR)
        go_block = next(b for b in blocks if b.class_id == "1")
        self.assertIsNone(_parse_monobehaviour_fields(go_block))

    def test_serialization_data_skipped(self) -> None:
        block = self._get_mono_block(SERIALIZATION_DATA_BLOCK)
        comp = _parse_monobehaviour_fields(block)
        assert comp is not None
        field_names = {f.name for f in comp.fields}
        self.assertNotIn("shouldBeIgnored", field_names)
        self.assertNotIn("SerializedBytes", field_names)
        self.assertIn("realField", field_names)

    def test_missing_game_object_line(self) -> None:
        """MonoBehaviour without m_GameObject defaults to empty string."""
        text = (
            "--- !u!114 &800001\n"
            "MonoBehaviour:\n"
            "  m_ObjectHideFlags: 0\n"
            "  m_Script: {fileID: 11500000, guid: aabbccdd11223344aabbccdd11223344, type: 3}\n"
            "  myRef: {fileID: 0}\n"
        )
        block = split_yaml_blocks(text)[0]
        comp = _parse_monobehaviour_fields(block)
        assert comp is not None
        self.assertEqual(comp.game_object_file_id, "")

    def test_nested_struct_children_excluded(self) -> None:
        """Nested YAML keys under a struct field must not appear as top-level fields."""
        block = self._get_mono_block(NESTED_STRUCT)
        comp = _parse_monobehaviour_fields(block)
        assert comp is not None
        field_names = {f.name for f in comp.fields}
        self.assertIn("topRef", field_names)
        self.assertIn("afterNested", field_names)
        # Nested keys must NOT be parsed as component fields
        self.assertNotIn("innerRef", field_names)
        self.assertNotIn("deeperNested", field_names)
        self.assertNotIn("veryDeep", field_names)

    def test_missing_script_line(self) -> None:
        """MonoBehaviour without m_Script defaults to empty guid."""
        text = (
            "--- !u!114 &800002\n"
            "MonoBehaviour:\n"
            "  m_ObjectHideFlags: 0\n"
            "  m_GameObject: {fileID: 800000}\n"
            "  myRef: {fileID: 800000}\n"
        )
        block = split_yaml_blocks(text)[0]
        comp = _parse_monobehaviour_fields(block)
        assert comp is not None
        self.assertEqual(comp.script_guid, "")

    def test_array_fields(self) -> None:
        block = self._get_mono_block(ARRAY_FIELDS)
        comp = _parse_monobehaviour_fields(block)
        assert comp is not None
        targets_fields = [f for f in comp.fields if f.name == "targets"]
        self.assertEqual(len(targets_fields), 3)
        file_ids = [f.file_id for f in targets_fields]
        self.assertIn("500000", file_ids)
        self.assertIn("0", file_ids)
        self.assertIn("500001", file_ids)


# ---------------------------------------------------------------------------
# parse_game_objects tests
# ---------------------------------------------------------------------------


class ParseGameObjectsTests(unittest.TestCase):
    def test_extracts_name_and_components(self) -> None:
        blocks = split_yaml_blocks(BASIC_MONOBEHAVIOUR)
        gos = parse_game_objects(blocks)
        self.assertIn("100000", gos)
        go = gos["100000"]
        self.assertEqual(go.name, "TestObject")
        self.assertIn("100001", go.component_file_ids)


# ---------------------------------------------------------------------------
# analyze_wiring tests
# ---------------------------------------------------------------------------


class AnalyzeWiringTests(unittest.TestCase):
    def test_null_reference_detected(self) -> None:
        result = analyze_wiring(BASIC_MONOBEHAVIOUR, "test.prefab")
        self.assertGreater(len(result.null_references), 0)
        self.assertEqual(result.max_severity, Severity.WARNING)

    def test_internal_broken_ref(self) -> None:
        result = analyze_wiring(BROKEN_INTERNAL_REF, "test.prefab")
        self.assertGreater(len(result.internal_broken_refs), 0)
        self.assertEqual(result.max_severity, Severity.ERROR)

    def test_duplicate_reference(self) -> None:
        result = analyze_wiring(DUPLICATE_REF, "test.prefab")
        self.assertGreater(len(result.duplicate_references), 0)

    def test_udon_only_filter(self) -> None:
        result = analyze_wiring(UDON_SHARP_BLOCK, "test.prefab", udon_only=True)
        self.assertGreater(len(result.components), 0)
        for comp in result.components:
            self.assertTrue(comp.is_udon_sharp)

    def test_udon_only_no_udon_sharp(self) -> None:
        result = analyze_wiring(BASIC_MONOBEHAVIOUR, "test.prefab", udon_only=True)
        self.assertEqual(len(result.components), 0)

    def test_clean_file(self) -> None:
        result = analyze_wiring(CLEAN_FILE, "test.prefab")
        self.assertEqual(len(result.null_references), 0)
        self.assertEqual(len(result.internal_broken_refs), 0)
        self.assertEqual(result.max_severity, Severity.INFO)

    def test_empty_text(self) -> None:
        result = analyze_wiring("", "test.prefab")
        self.assertEqual(len(result.components), 0)
        self.assertEqual(result.max_severity, Severity.INFO)

    def test_null_ref_detail_with_missing_game_object(self) -> None:
        """Null ref diagnostic shows <unknown> when m_GameObject is absent."""
        text = (
            "--- !u!114 &900001\n"
            "MonoBehaviour:\n"
            "  m_ObjectHideFlags: 0\n"
            "  m_Script: {fileID: 11500000, guid: aabbccdd11223344aabbccdd11223344, type: 3}\n"
            "  myRef: {fileID: 0}\n"
        )
        result = analyze_wiring(text, "test.prefab")
        self.assertEqual(len(result.null_references), 1)
        self.assertIn("<unknown>", result.null_references[0].detail)

    def test_nested_struct_does_not_produce_false_positives(self) -> None:
        """Nested {fileID: 0} and {fileID: 999999} should not generate diagnostics."""
        result = analyze_wiring(NESTED_STRUCT, "test.prefab")
        # innerRef {fileID: 0} is nested — should NOT be a null reference
        # veryDeep {fileID: 999999} is nested — should NOT be a broken ref
        self.assertEqual(len(result.null_references), 0)
        self.assertEqual(len(result.internal_broken_refs), 0)

    def test_game_objects_populated(self) -> None:
        result = analyze_wiring(BASIC_MONOBEHAVIOUR, "test.prefab")
        self.assertIn("100000", result.game_objects)
        self.assertEqual(result.game_objects["100000"].name, "TestObject")

    def test_game_objects_empty_when_no_components(self) -> None:
        result = analyze_wiring("", "test.prefab")
        self.assertEqual(result.game_objects, {})

    def test_array_null_ref_detected(self) -> None:
        result = analyze_wiring(ARRAY_FIELDS, "test.prefab")
        null_names = [d.detail for d in result.null_references]
        self.assertTrue(any("targets" in n for n in null_names))

    def test_same_component_duplicate_is_warning(self) -> None:
        result = analyze_wiring(SAME_COMPONENT_DUPLICATE, "test.prefab")
        same_diags = [d for d in result.duplicate_references if "[same-component]" in d.detail]
        self.assertGreater(len(same_diags), 0)
        # Same-component duplicates should raise severity to WARNING
        self.assertEqual(result.max_severity, Severity.WARNING)

    def test_cross_component_duplicate_is_info(self) -> None:
        result = analyze_wiring(DUPLICATE_REF, "test.prefab")
        cross_diags = [d for d in result.duplicate_references if "[cross-component]" in d.detail]
        self.assertGreater(len(cross_diags), 0)
        # Cross-component duplicates should remain INFO
        self.assertEqual(result.max_severity, Severity.INFO)

    def test_null_field_names_populated(self) -> None:
        """ComponentWiring.null_field_names lists all null ref field names."""
        result = analyze_wiring(BASIC_MONOBEHAVIOUR, "test.prefab")
        self.assertEqual(len(result.components), 1)
        self.assertEqual(result.components[0].null_field_names, ["myNullRef"])

    def test_null_field_names_empty_when_no_nulls(self) -> None:
        """ComponentWiring.null_field_names is empty when no null refs."""
        result = analyze_wiring(CLEAN_FILE, "test.prefab")
        for comp in result.components:
            self.assertEqual(comp.null_field_names, [])

    def test_null_field_names_excludes_nested_structs(self) -> None:
        """Nested struct null refs are not included in null_field_names."""
        result = analyze_wiring(NESTED_STRUCT, "test.prefab")
        for comp in result.components:
            self.assertEqual(comp.null_field_names, [])


class TestExtractMonoBehaviourFieldNames(unittest.TestCase):
    """Test extract_monobehaviour_field_names for all-field extraction."""

    def test_extracts_scalar_fields(self) -> None:
        """Captures plain scalar fields that _parse_monobehaviour_fields skips."""
        block = YamlBlock(
            file_id="100",
            class_id="114",
            text=(
                "MonoBehaviour:\n"
                "  m_ObjectHideFlags: 0\n"
                "  m_GameObject: {fileID: 50}\n"
                "  m_Script: {fileID: 11500000, guid: aabb, type: 3}\n"
                "  speed: 5.0\n"
                "  health: 100\n"
                "  label: Hello\n"
            ),
            start_line=1,
        )
        names = extract_monobehaviour_field_names(block)
        self.assertIn("speed", names)
        self.assertIn("health", names)
        self.assertIn("label", names)

    def test_skips_builtin_fields(self) -> None:
        """SKIP_FIELDS are excluded."""
        block = YamlBlock(
            file_id="100",
            class_id="114",
            text=(
                "MonoBehaviour:\n"
                "  m_ObjectHideFlags: 0\n"
                "  m_GameObject: {fileID: 50}\n"
                "  m_Enabled: 1\n"
                "  m_Script: {fileID: 11500000, guid: aabb, type: 3}\n"
                "  m_Name: Test\n"
                "  customField: 42\n"
            ),
            start_line=1,
        )
        names = extract_monobehaviour_field_names(block)
        self.assertEqual(["customField"], names)

    def test_skips_nested_lines(self) -> None:
        """Nested properties (array elements, sub-objects) are not extracted."""
        block = YamlBlock(
            file_id="100",
            class_id="114",
            text=(
                "MonoBehaviour:\n"
                "  m_GameObject: {fileID: 50}\n"
                "  m_Script: {fileID: 11500000, guid: aabb, type: 3}\n"
                "  items:\n"
                "    - {fileID: 1}\n"
                "    - {fileID: 2}\n"
                "  speed: 5.0\n"
            ),
            start_line=1,
        )
        names = extract_monobehaviour_field_names(block)
        self.assertEqual(["items", "speed"], names)

    def test_returns_empty_for_non_monobehaviour(self) -> None:
        block = YamlBlock(
            file_id="100",
            class_id="4",  # Transform
            text="Transform:\n  m_LocalPosition: {x: 0}\n",
            start_line=1,
        )
        names = extract_monobehaviour_field_names(block)
        self.assertEqual([], names)

    def test_includes_reference_fields(self) -> None:
        """Reference fields (fileID/GUID patterns) are also included."""
        block = YamlBlock(
            file_id="100",
            class_id="114",
            text=(
                "MonoBehaviour:\n"
                "  m_GameObject: {fileID: 50}\n"
                "  m_Script: {fileID: 11500000, guid: aabb, type: 3}\n"
                "  target: {fileID: 200, guid: 00000000000000000000000000000000, type: 2}\n"
                "  speed: 3.0\n"
            ),
            start_line=1,
        )
        names = extract_monobehaviour_field_names(block)
        self.assertIn("target", names)
        self.assertIn("speed", names)


class AnalyzeWiringOverrideMapTests(unittest.TestCase):
    """Tests for Variant override annotation via override_map parameter."""

    def test_no_override_map_defaults(self) -> None:
        """Without override_map, override_count=0 and is_overridden=False."""
        result = analyze_wiring(BASIC_MONOBEHAVIOUR, "test.prefab")
        for comp in result.components:
            self.assertEqual(comp.override_count, 0)
            for f in comp.fields:
                self.assertFalse(f.is_overridden)

    def test_empty_override_map_no_effect(self) -> None:
        """Empty override_map should not annotate anything."""
        result = analyze_wiring(BASIC_MONOBEHAVIOUR, "test.prefab", override_map={})
        for comp in result.components:
            self.assertEqual(comp.override_count, 0)
            for f in comp.fields:
                self.assertFalse(f.is_overridden)

    def test_override_count_propagates(self) -> None:
        """override_map with component fileID sets override_count."""
        override_map = {"100001": {"myRef", "myNullRef"}}
        result = analyze_wiring(
            BASIC_MONOBEHAVIOUR, "test.prefab", override_map=override_map,
        )
        self.assertEqual(len(result.components), 1)
        self.assertEqual(result.components[0].override_count, 2)

    def test_field_is_overridden_exact_match(self) -> None:
        """Field is_overridden=True when property_path exactly matches field name."""
        override_map = {"100001": {"myRef"}}
        result = analyze_wiring(
            BASIC_MONOBEHAVIOUR, "test.prefab", override_map=override_map,
        )
        comp = result.components[0]
        overridden = {f.name: f.is_overridden for f in comp.fields}
        self.assertTrue(overridden["myRef"])
        self.assertFalse(overridden["myNullRef"])

    def test_field_is_overridden_prefix_match(self) -> None:
        """Field is_overridden=True when property_path starts with field.name + '.'."""
        override_map = {"100001": {"myRef.fileID"}}
        result = analyze_wiring(
            BASIC_MONOBEHAVIOUR, "test.prefab", override_map=override_map,
        )
        comp = result.components[0]
        overridden = {f.name: f.is_overridden for f in comp.fields}
        self.assertTrue(overridden["myRef"])
        self.assertFalse(overridden["myNullRef"])

    def test_unmatched_component_fileid_ignored(self) -> None:
        """override_map with non-matching fileID should not annotate any component."""
        override_map = {"999999": {"someField"}}
        result = analyze_wiring(
            BASIC_MONOBEHAVIOUR, "test.prefab", override_map=override_map,
        )
        for comp in result.components:
            self.assertEqual(comp.override_count, 0)


if __name__ == "__main__":
    unittest.main()
