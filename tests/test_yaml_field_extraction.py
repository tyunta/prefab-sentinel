"""Tests for prefab_sentinel.yaml_field_extraction."""

from __future__ import annotations

import unittest

from prefab_sentinel.yaml_field_extraction import (
    extract_block_fields,
    parse_yaml_scalar,
)

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
