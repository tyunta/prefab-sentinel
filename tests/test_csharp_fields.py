"""Tests for C# serialized field parser."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from prefab_sentinel.csharp_fields import (
    CSharpClassInfo,
    CSharpField,
    build_class_name_index,
    build_field_map,
    parse_class_info,
    parse_serialized_fields,
    resolve_inherited_fields,
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


class TestToDictSourceClass(unittest.TestCase):
    """Test CSharpField.to_dict with source_class."""

    def test_source_class_omitted_when_empty(self) -> None:
        f = CSharpField(
            name="x", type_name="int", is_serialized=True,
            is_public=True, line=1,
        )
        self.assertNotIn("source_class", f.to_dict())

    def test_source_class_included_when_set(self) -> None:
        f = CSharpField(
            name="x", type_name="int", is_serialized=True,
            is_public=True, line=1, source_class="BasePlayer",
        )
        d = f.to_dict()
        self.assertEqual("BasePlayer", d["source_class"])


class TestParseClassInfo(unittest.TestCase):
    """Test parse_class_info for class declaration extraction."""

    def test_simple_class(self) -> None:
        source = "public class PlayerScript : MonoBehaviour {\n    public float speed;\n}\n"
        info = parse_class_info(source)
        self.assertIsNotNone(info)
        self.assertEqual("PlayerScript", info.name)
        self.assertEqual("MonoBehaviour", info.base_class)
        self.assertEqual(1, len(info.fields))
        self.assertEqual("speed", info.fields[0].name)

    def test_no_base_class(self) -> None:
        source = "public class HelperData {\n    public int value;\n}\n"
        info = parse_class_info(source)
        self.assertIsNotNone(info)
        self.assertEqual("HelperData", info.name)
        self.assertEqual("", info.base_class)

    def test_abstract_class(self) -> None:
        source = "public abstract class BaseUnit : MonoBehaviour {\n    public float hp;\n}\n"
        info = parse_class_info(source)
        self.assertIsNotNone(info)
        self.assertEqual("BaseUnit", info.name)
        self.assertEqual("MonoBehaviour", info.base_class)

    def test_sealed_class(self) -> None:
        source = "sealed class Final : BaseUnit {\n    public int rank;\n}\n"
        info = parse_class_info(source)
        self.assertIsNotNone(info)
        self.assertEqual("Final", info.name)
        self.assertEqual("BaseUnit", info.base_class)

    def test_generic_base_class(self) -> None:
        source = "public class Pool : GenericBase<int> {\n    public int size;\n}\n"
        info = parse_class_info(source)
        self.assertIsNotNone(info)
        self.assertEqual("Pool", info.name)
        self.assertEqual("GenericBase", info.base_class)  # generic stripped

    def test_namespaced_base_class(self) -> None:
        source = "public class Foo : UnityEngine.MonoBehaviour {\n}\n"
        info = parse_class_info(source)
        self.assertIsNotNone(info)
        self.assertEqual("UnityEngine.MonoBehaviour", info.base_class)

    def test_class_with_interfaces(self) -> None:
        source = "public class Bar : MonoBehaviour, IDisposable {\n    public int x;\n}\n"
        info = parse_class_info(source)
        self.assertIsNotNone(info)
        self.assertEqual("Bar", info.name)
        self.assertEqual("MonoBehaviour", info.base_class)

    def test_no_class_returns_none(self) -> None:
        source = "using UnityEngine;\nnamespace Foo { }"
        info = parse_class_info(source)
        self.assertIsNone(info)

    def test_hint_name_selects_matching_class(self) -> None:
        source = (
            "class HelperUtil {\n    public int a;\n}\n"
            "class TargetClass : MonoBehaviour {\n    public float b;\n}\n"
        )
        info = parse_class_info(source, hint_name="TargetClass")
        self.assertIsNotNone(info)
        self.assertEqual("TargetClass", info.name)
        self.assertEqual("MonoBehaviour", info.base_class)

    def test_hint_name_fallback_to_first(self) -> None:
        source = "class Alpha {\n    public int a;\n}\nclass Beta {\n    public int b;\n}\n"
        info = parse_class_info(source, hint_name="Gamma")
        self.assertIsNotNone(info)
        self.assertEqual("Alpha", info.name)

    def test_partial_class(self) -> None:
        source = "public partial class SyncedBehaviour : UdonSharpBehaviour {\n    public int val;\n}\n"
        info = parse_class_info(source)
        self.assertIsNotNone(info)
        self.assertEqual("SyncedBehaviour", info.name)
        self.assertEqual("UdonSharpBehaviour", info.base_class)

    def test_fields_are_serialized_only(self) -> None:
        source = (
            "public class Foo : MonoBehaviour {\n"
            "    public float visible;\n"
            "    private int hidden;\n"
            "    public static int excluded;\n"
            "}\n"
        )
        info = parse_class_info(source)
        self.assertIsNotNone(info)
        names = {f.name for f in info.fields}
        self.assertEqual({"visible"}, names)


def _make_cs_project(
    root: Path,
    scripts: dict[str, tuple[str, str]],
) -> None:
    """Create a synthetic project with .cs + .meta files.

    Args:
        root: Project root directory.
        scripts: Mapping of ``relative_path`` → ``(guid, source_code)``.
    """
    for rel_path, (guid, source) in scripts.items():
        cs_path = root / rel_path
        cs_path.parent.mkdir(parents=True, exist_ok=True)
        cs_path.write_text(source, encoding="utf-8")
        meta_path = Path(str(cs_path) + ".meta")
        meta_path.write_text(
            f"fileFormatVersion: 2\nguid: {guid}\n",
            encoding="utf-8",
        )


class TestBuildClassNameIndex(unittest.TestCase):
    """Test build_class_name_index."""

    def test_builds_index(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _make_cs_project(root, {
                "Assets/Scripts/PlayerScript.cs": (
                    "aaaa1111bbbb2222cccc3333dddd4444",
                    "public class PlayerScript : MonoBehaviour {\n    public float speed;\n}\n",
                ),
                "Assets/Scripts/EnemyScript.cs": (
                    "bbbb2222cccc3333dddd4444eeee5555",
                    "public class EnemyScript : MonoBehaviour {\n    public int hp;\n}\n",
                ),
            })
            index = build_class_name_index(root)

        self.assertIn("PlayerScript", index)
        self.assertIn("EnemyScript", index)
        self.assertEqual("aaaa1111bbbb2222cccc3333dddd4444", index["PlayerScript"][0])

    def test_skips_non_cs_files(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            assets = root / "Assets"
            assets.mkdir(parents=True)
            shader = assets / "Foo.shader"
            shader.write_text("Shader {}", encoding="utf-8")
            meta = Path(str(shader) + ".meta")
            meta.write_text("fileFormatVersion: 2\nguid: 00001111222233334444555566667777\n", encoding="utf-8")

            index = build_class_name_index(root)

        self.assertEqual({}, index)


class TestResolveInheritedFields(unittest.TestCase):
    """Test resolve_inherited_fields for inheritance chain resolution."""

    _BASE_GUID = "aaaa1111bbbb2222cccc3333dddd4444"
    _DERIVED_GUID = "bbbb2222cccc3333dddd4444eeee5555"
    _TWO_LEVEL_SCRIPTS: dict[str, tuple[str, str]] = {
        "Assets/Scripts/BasePlayer.cs": (
            _BASE_GUID,
            (
                "public class BasePlayer : UdonSharpBehaviour {\n"
                "    public float health;\n"
                "    public int level;\n"
                "}\n"
            ),
        ),
        "Assets/Scripts/DerivedPlayer.cs": (
            _DERIVED_GUID,
            (
                "public class DerivedPlayer : BasePlayer {\n"
                "    public float moveSpeed;\n"
                "    public float jumpForce;\n"
                "}\n"
            ),
        ),
    }

    def test_two_level_chain(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _make_cs_project(root, self._TWO_LEVEL_SCRIPTS)
            fields = resolve_inherited_fields(self._DERIVED_GUID, root)
        names = [f.name for f in fields]
        # Base fields first, then derived
        self.assertEqual(["health", "level", "moveSpeed", "jumpForce"], names)

    def test_source_class_annotation(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _make_cs_project(root, self._TWO_LEVEL_SCRIPTS)
            fields = resolve_inherited_fields(self._DERIVED_GUID, root)
        by_name = {f.name: f for f in fields}
        self.assertEqual("BasePlayer", by_name["health"].source_class)
        self.assertEqual("BasePlayer", by_name["level"].source_class)
        self.assertEqual("DerivedPlayer", by_name["moveSpeed"].source_class)
        self.assertEqual("DerivedPlayer", by_name["jumpForce"].source_class)

    def test_base_only_returns_own_fields(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _make_cs_project(root, self._TWO_LEVEL_SCRIPTS)
            fields = resolve_inherited_fields(self._BASE_GUID, root)
        names = [f.name for f in fields]
        self.assertEqual(["health", "level"], names)

    def test_three_level_chain(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _make_cs_project(root, {
                "Assets/Scripts/GrandBase.cs": (
                    "aaaa0000000000000000000000000001",
                    "public class GrandBase : MonoBehaviour {\n    public int id;\n}\n",
                ),
                "Assets/Scripts/MiddleClass.cs": (
                    "aaaa0000000000000000000000000002",
                    "public class MiddleClass : GrandBase {\n    public float ratio;\n}\n",
                ),
                "Assets/Scripts/LeafClass.cs": (
                    "aaaa0000000000000000000000000003",
                    "public class LeafClass : MiddleClass {\n    public string label;\n}\n",
                ),
            })
            fields = resolve_inherited_fields("aaaa0000000000000000000000000003", root)

        names = [f.name for f in fields]
        self.assertEqual(["id", "ratio", "label"], names)
        self.assertEqual("GrandBase", fields[0].source_class)
        self.assertEqual("MiddleClass", fields[1].source_class)
        self.assertEqual("LeafClass", fields[2].source_class)

    def test_stops_at_external_base_class(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _make_cs_project(root, {
                "Assets/Scripts/Simple.cs": (
                    "aaaa0000000000000000000000000010",
                    "public class Simple : MonoBehaviour {\n    public int val;\n}\n",
                ),
            })
            fields = resolve_inherited_fields("aaaa0000000000000000000000000010", root)

        self.assertEqual(1, len(fields))
        self.assertEqual("val", fields[0].name)

    def test_stops_at_unknown_base_class(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _make_cs_project(root, {
                "Assets/Scripts/Child.cs": (
                    "aaaa0000000000000000000000000020",
                    "public class Child : ExternalBase {\n    public int x;\n}\n",
                ),
            })
            fields = resolve_inherited_fields("aaaa0000000000000000000000000020", root)

        self.assertEqual(1, len(fields))
        self.assertEqual("x", fields[0].name)

    def test_circular_inheritance_does_not_loop(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _make_cs_project(root, {
                "Assets/Scripts/CycleA.cs": (
                    "aaaa0000000000000000000000000030",
                    "public class CycleA : CycleB {\n    public int a;\n}\n",
                ),
                "Assets/Scripts/CycleB.cs": (
                    "aaaa0000000000000000000000000031",
                    "public class CycleB : CycleA {\n    public int b;\n}\n",
                ),
            })
            # Should not hang — just returns whatever it can collect
            fields = resolve_inherited_fields("aaaa0000000000000000000000000030", root)

        names = {f.name for f in fields}
        # At minimum, CycleA's own field should be present
        self.assertIn("a", names)

    def test_unknown_guid_returns_empty(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "Assets").mkdir(parents=True)
            fields = resolve_inherited_fields("0000000000000000000000000000dead", root)

        self.assertEqual([], fields)

    def test_all_fields_are_serialized(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _make_cs_project(root, self._TWO_LEVEL_SCRIPTS)
            fields = resolve_inherited_fields(self._DERIVED_GUID, root)
        for f in fields:
            self.assertTrue(f.is_serialized, f"Field {f.name} should be serialized")

    def test_caching_params_accepted(self) -> None:
        """Pre-built field_map and class_index are used when passed."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _make_cs_project(root, self._TWO_LEVEL_SCRIPTS)
            field_map = build_field_map(root)
            class_index = build_class_name_index(root)
            fields = resolve_inherited_fields(
                self._DERIVED_GUID, root,
                _field_map=field_map,
                _class_index=class_index,
            )
        self.assertEqual(4, len(fields))


if __name__ == "__main__":
    unittest.main()
