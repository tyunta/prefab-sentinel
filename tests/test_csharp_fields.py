"""Tests for C# serialized field parser."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from prefab_sentinel.csharp_fields import (
    CSharpField,
    build_field_map,
    parse_serialized_fields,
    resolve_script_fields,
)


class TestParseSerializedFields(unittest.TestCase):
    """Test parse_serialized_fields with various C# patterns."""

    # ----- Basic serialization rules -----

    def test_public_field_is_serialized(self) -> None:
        fields = parse_serialized_fields("public float speed;")
        self.assertEqual(1, len(fields))
        f = fields[0]
        self.assertEqual("speed", f.name)
        self.assertEqual("float", f.type_name)
        self.assertTrue(f.is_serialized)
        self.assertTrue(f.is_public)

    def test_private_field_not_serialized(self) -> None:
        fields = parse_serialized_fields("private int _internal;")
        self.assertEqual(1, len(fields))
        self.assertFalse(fields[0].is_serialized)

    def test_serialize_field_attribute(self) -> None:
        source = "[SerializeField] private int health;"
        fields = parse_serialized_fields(source)
        self.assertEqual(1, len(fields))
        self.assertTrue(fields[0].is_serialized)
        self.assertFalse(fields[0].is_public)
        self.assertEqual("health", fields[0].name)

    def test_serialize_field_separate_line(self) -> None:
        source = "[SerializeField]\nprivate int health;"
        fields = parse_serialized_fields(source)
        self.assertEqual(1, len(fields))
        self.assertTrue(fields[0].is_serialized)

    def test_nonserialized_public_field(self) -> None:
        source = "[NonSerialized] public int temp;"
        fields = parse_serialized_fields(source)
        self.assertEqual(1, len(fields))
        self.assertFalse(fields[0].is_serialized)

    # ----- Exclusion rules -----

    def test_static_field_excluded(self) -> None:
        fields = parse_serialized_fields("public static int count;")
        self.assertEqual(0, len(fields))

    def test_const_field_excluded(self) -> None:
        fields = parse_serialized_fields("const int MAX = 100;")
        self.assertEqual(0, len(fields))

    def test_readonly_field_excluded(self) -> None:
        fields = parse_serialized_fields("public readonly int id;")
        self.assertEqual(0, len(fields))

    def test_method_excluded(self) -> None:
        source = "public string GetName() { return \"\"; }"
        fields = parse_serialized_fields(source)
        self.assertEqual(0, len(fields))

    def test_property_excluded(self) -> None:
        source = "public int Hp { get; set; }"
        fields = parse_serialized_fields(source)
        self.assertEqual(0, len(fields))

    # ----- Type names -----

    def test_generic_type(self) -> None:
        fields = parse_serialized_fields("public List<int> items;")
        self.assertEqual(1, len(fields))
        self.assertEqual("List<int>", fields[0].type_name)

    def test_array_type(self) -> None:
        fields = parse_serialized_fields("[SerializeField] private string[] names;")
        self.assertEqual(1, len(fields))
        self.assertEqual("string[]", fields[0].type_name)

    def test_gameobject_reference(self) -> None:
        fields = parse_serialized_fields("public GameObject target;")
        self.assertEqual(1, len(fields))
        self.assertEqual("GameObject", fields[0].type_name)

    def test_nullable_type(self) -> None:
        fields = parse_serialized_fields("public int? optionalValue;")
        self.assertEqual(1, len(fields))
        self.assertEqual("int?", fields[0].type_name)

    # ----- Attributes -----

    def test_header_attribute_captured(self) -> None:
        source = '[Header("Movement")] public float runSpeed;'
        fields = parse_serialized_fields(source)
        self.assertEqual(1, len(fields))
        attrs = fields[0].attributes
        self.assertTrue(any("Header" in a for a in attrs))

    def test_tooltip_attribute_captured(self) -> None:
        source = '[Tooltip("Speed in m/s")] public float speed;'
        fields = parse_serialized_fields(source)
        self.assertEqual(1, len(fields))
        self.assertTrue(any("Tooltip" in a for a in fields[0].attributes))

    def test_range_attribute_captured(self) -> None:
        source = "[Range(0, 100)] public int value;"
        fields = parse_serialized_fields(source)
        self.assertEqual(1, len(fields))
        self.assertTrue(any("Range" in a for a in fields[0].attributes))

    def test_multiple_attributes(self) -> None:
        source = '[Header("X")] [Range(0, 1)] public float blend;'
        fields = parse_serialized_fields(source)
        self.assertEqual(1, len(fields))
        self.assertGreaterEqual(len(fields[0].attributes), 2)

    def test_formerly_serialized_as_captured(self) -> None:
        source = '[FormerlySerializedAs("oldSpeed")] public float newSpeed;'
        fields = parse_serialized_fields(source)
        self.assertEqual(1, len(fields))
        self.assertTrue(any("FormerlySerializedAs" in a for a in fields[0].attributes))

    # ----- Edge cases -----

    def test_initialized_field(self) -> None:
        fields = parse_serialized_fields("public float speed = 5.0f;")
        self.assertEqual(1, len(fields))
        self.assertEqual("speed", fields[0].name)

    def test_empty_source(self) -> None:
        self.assertEqual([], parse_serialized_fields(""))

    def test_no_serialized_fields(self) -> None:
        source = """
private int _a;
private float _b;
public static int Count;
"""
        fields = parse_serialized_fields(source)
        serialized = [f for f in fields if f.is_serialized]
        self.assertEqual(0, len(serialized))

    def test_line_numbers(self) -> None:
        source = """using UnityEngine;

public class Foo : MonoBehaviour {
    public float speed;
    public int health;
}
"""
        fields = parse_serialized_fields(source)
        self.assertEqual(2, len(fields))
        self.assertEqual("speed", fields[0].name)
        self.assertEqual(4, fields[0].line)
        self.assertEqual("health", fields[1].name)
        self.assertEqual(5, fields[1].line)

    def test_full_class_mixed_members(self) -> None:
        source = """
using UnityEngine;

public class PlayerScript : MonoBehaviour {
    public float moveSpeed;
    [SerializeField] private int health;
    [NonSerialized] public int tempData;
    private float _internal;
    public static int playerCount;
    const int MAX_HEALTH = 100;

    public void TakeDamage(int amount) {
        health -= amount;
    }

    public int GetHealth() { return health; }
}
"""
        fields = parse_serialized_fields(source)
        serialized = [f for f in fields if f.is_serialized]
        names = {f.name for f in serialized}
        self.assertEqual({"moveSpeed", "health"}, names)

    def test_protected_field_not_serialized_without_attribute(self) -> None:
        fields = parse_serialized_fields("protected float value;")
        self.assertEqual(1, len(fields))
        self.assertFalse(fields[0].is_serialized)

    def test_protected_field_serialized_with_attribute(self) -> None:
        source = "[SerializeField] protected float value;"
        fields = parse_serialized_fields(source)
        self.assertEqual(1, len(fields))
        self.assertTrue(fields[0].is_serialized)

    def test_to_dict(self) -> None:
        f = CSharpField(
            name="speed",
            type_name="float",
            is_serialized=True,
            is_public=True,
            line=10,
            attributes=["Range(0, 100)"],
        )
        d = f.to_dict()
        self.assertEqual("speed", d["name"])
        self.assertEqual("float", d["type_name"])
        self.assertTrue(d["is_serialized"])
        self.assertEqual(10, d["line"])
        self.assertEqual(["Range(0, 100)"], d["attributes"])

    def test_to_dict_no_attributes(self) -> None:
        f = CSharpField(
            name="hp", type_name="int", is_serialized=True,
            is_public=True, line=1,
        )
        d = f.to_dict()
        self.assertNotIn("attributes", d)


class TestBuildFieldMap(unittest.TestCase):
    """Test build_field_map with synthetic project files."""

    def test_builds_map_from_cs_files(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            assets = root / "Assets" / "Scripts"
            assets.mkdir(parents=True)

            # Write .cs file
            cs = assets / "PlayerScript.cs"
            cs.write_text(
                "public class PlayerScript : MonoBehaviour {\n"
                "    public float speed;\n"
                "    [SerializeField] private int health;\n"
                "}\n",
                encoding="utf-8",
            )
            # Write .cs.meta file
            meta = Path(str(cs) + ".meta")
            meta.write_text(
                "fileFormatVersion: 2\nguid: aaaa1111bbbb2222cccc3333dddd4444\n",
                encoding="utf-8",
            )

            field_map = build_field_map(root)

        self.assertIn("aaaa1111bbbb2222cccc3333dddd4444", field_map)
        fields = field_map["aaaa1111bbbb2222cccc3333dddd4444"]
        names = {f.name for f in fields}
        self.assertEqual({"speed", "health"}, names)

    def test_skips_non_cs_files(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            assets = root / "Assets"
            assets.mkdir(parents=True)

            # Write a .shader file
            shader = assets / "Custom.shader"
            shader.write_text("Shader {}", encoding="utf-8")
            meta = Path(str(shader) + ".meta")
            meta.write_text(
                "fileFormatVersion: 2\nguid: bbbb2222cccc3333dddd4444eeee5555\n",
                encoding="utf-8",
            )

            field_map = build_field_map(root)

        self.assertEqual({}, field_map)


class TestResolveScriptFields(unittest.TestCase):
    """Test resolve_script_fields with path and GUID inputs."""

    def test_resolve_by_path(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            cs = Path(td) / "Test.cs"
            cs.write_text("public float speed;", encoding="utf-8")
            meta = Path(str(cs) + ".meta")
            meta.write_text(
                "fileFormatVersion: 2\nguid: aaaa1111bbbb2222cccc3333dddd4444\n",
                encoding="utf-8",
            )

            guid, path, fields = resolve_script_fields(str(cs))

        self.assertEqual("aaaa1111bbbb2222cccc3333dddd4444", guid)
        self.assertEqual(cs, path)
        self.assertEqual(1, len(fields))
        self.assertEqual("speed", fields[0].name)

    def test_resolve_by_guid(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            assets = root / "Assets"
            assets.mkdir()

            cs = assets / "Foo.cs"
            cs.write_text("public int value;", encoding="utf-8")
            meta = Path(str(cs) + ".meta")
            meta.write_text(
                "fileFormatVersion: 2\nguid: cccc3333dddd4444eeee5555ffff6666\n",
                encoding="utf-8",
            )

            guid, path, fields = resolve_script_fields(
                "cccc3333dddd4444eeee5555ffff6666", project_root=root
            )

        self.assertEqual("cccc3333dddd4444eeee5555ffff6666", guid)
        self.assertEqual(1, len(fields))

    def test_resolve_nonexistent_path_raises(self) -> None:
        with self.assertRaises(FileNotFoundError):
            resolve_script_fields("/nonexistent/Test.cs")

    def test_resolve_guid_without_project_root_raises(self) -> None:
        with self.assertRaises(ValueError):
            resolve_script_fields("aaaa1111bbbb2222cccc3333dddd4444")

    def test_resolve_unknown_guid_raises(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "Assets").mkdir()

            with self.assertRaises(FileNotFoundError):
                resolve_script_fields(
                    "0000000000000000000000000000dead", project_root=root
                )


if __name__ == "__main__":
    unittest.main()
