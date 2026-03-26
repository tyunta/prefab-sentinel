"""Tests for the symbol_tree module."""

from __future__ import annotations

import unittest

from prefab_sentinel.symbol_tree import (
    AmbiguousSymbolError,
    SymbolKind,
    SymbolNode,
    SymbolNotFoundError,
    SymbolTree,
)
from tests.yaml_helpers import (
    YAML_HEADER,
    make_gameobject,
    make_meshrenderer,
    make_monobehaviour,
    make_transform,
)


class TestSymbolTreeBuildEmpty(unittest.TestCase):
    """SymbolTree.build with empty or header-only YAML."""

    def test_empty_string(self) -> None:
        tree = SymbolTree.build("", "test.prefab")
        self.assertEqual(tree.roots, [])
        self.assertEqual(tree.asset_path, "test.prefab")

    def test_header_only(self) -> None:
        tree = SymbolTree.build(YAML_HEADER, "test.prefab")
        self.assertEqual(tree.roots, [])


class TestSymbolTreeBuildSingleRoot(unittest.TestCase):
    """SymbolTree.build with a single root GameObject."""

    def test_single_root_go(self) -> None:
        text = (
            YAML_HEADER
            + make_gameobject("100", "Root", ["200"])
            + make_transform("200", "100")
        )
        tree = SymbolTree.build(text, "test.prefab")
        self.assertEqual(len(tree.roots), 1)
        root = tree.roots[0]
        self.assertEqual(root.kind, SymbolKind.GAME_OBJECT)
        self.assertEqual(root.name, "Root")
        self.assertEqual(root.file_id, "100")
        self.assertEqual(root.depth, 0)

    def test_root_has_transform_component(self) -> None:
        text = (
            YAML_HEADER
            + make_gameobject("100", "Root", ["200"])
            + make_transform("200", "100")
        )
        tree = SymbolTree.build(text, "test.prefab")
        root = tree.roots[0]
        # Should have Transform as a component child
        comp_names = [c.name for c in root.children if c.kind == SymbolKind.COMPONENT]
        self.assertIn("Transform", comp_names)

    def test_root_with_meshrenderer(self) -> None:
        text = (
            YAML_HEADER
            + make_gameobject("100", "Root", ["200", "300"])
            + make_transform("200", "100")
            + make_meshrenderer("300", "100")
        )
        tree = SymbolTree.build(text, "test.prefab")
        root = tree.roots[0]
        comp_names = [c.name for c in root.children if c.kind == SymbolKind.COMPONENT]
        self.assertIn("MeshRenderer", comp_names)


class TestSymbolTreeBuildNestedHierarchy(unittest.TestCase):
    """SymbolTree.build with nested GameObjects (3+ levels)."""

    def _build_nested(self) -> SymbolTree:
        text = (
            YAML_HEADER
            + make_gameobject("100", "Root", ["200"])
            + make_transform("200", "100", children_file_ids=["400"])
            + make_gameobject("300", "Child", ["400"])
            + make_transform("400", "300", father_file_id="200", children_file_ids=["600"])
            + make_gameobject("500", "GrandChild", ["600"])
            + make_transform("600", "500", father_file_id="400")
        )
        return SymbolTree.build(text, "test.prefab")

    def test_three_levels(self) -> None:
        tree = self._build_nested()
        self.assertEqual(len(tree.roots), 1)
        root = tree.roots[0]
        self.assertEqual(root.name, "Root")
        # Child GO is among root's children
        go_children = [c for c in root.children if c.kind == SymbolKind.GAME_OBJECT]
        self.assertEqual(len(go_children), 1)
        child = go_children[0]
        self.assertEqual(child.name, "Child")
        self.assertEqual(child.depth, 1)
        # GrandChild
        grand_children = [c for c in child.children if c.kind == SymbolKind.GAME_OBJECT]
        self.assertEqual(len(grand_children), 1)
        self.assertEqual(grand_children[0].name, "GrandChild")
        self.assertEqual(grand_children[0].depth, 2)

    def test_resolve_nested_path(self) -> None:
        tree = self._build_nested()
        matches = tree.resolve("Root/Child/GrandChild")
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0].name, "GrandChild")
        self.assertEqual(matches[0].file_id, "500")


class TestSymbolTreeBuildMonoBehaviour(unittest.TestCase):
    """SymbolTree.build with MonoBehaviour and script name resolution."""

    def test_monobehaviour_with_script_name(self) -> None:
        guid = "aaaa1111bbbb2222cccc3333dddd4444"
        text = (
            YAML_HEADER
            + make_gameobject("100", "Player", ["200", "300"])
            + make_transform("200", "100")
            + make_monobehaviour("300", "100", guid=guid)
        )
        script_map = {guid: "PlayerController"}
        tree = SymbolTree.build(text, "test.prefab", script_map)
        root = tree.roots[0]
        mb_nodes = [
            c for c in root.children
            if c.kind == SymbolKind.COMPONENT and c.class_id == "114"
        ]
        self.assertEqual(len(mb_nodes), 1)
        self.assertEqual(mb_nodes[0].name, "MonoBehaviour(PlayerController)")
        self.assertEqual(mb_nodes[0].script_name, "PlayerController")
        self.assertEqual(mb_nodes[0].script_guid, guid)

    def test_monobehaviour_without_script_name(self) -> None:
        guid = "aaaa1111bbbb2222cccc3333dddd4444"
        text = (
            YAML_HEADER
            + make_gameobject("100", "Player", ["200", "300"])
            + make_transform("200", "100")
            + make_monobehaviour("300", "100", guid=guid)
        )
        tree = SymbolTree.build(text, "test.prefab")
        root = tree.roots[0]
        mb_nodes = [
            c for c in root.children
            if c.kind == SymbolKind.COMPONENT and c.class_id == "114"
        ]
        self.assertEqual(len(mb_nodes), 1)
        # Should show truncated GUID
        self.assertTrue(mb_nodes[0].name.startswith("MonoBehaviour(guid:"))

    def test_resolve_monobehaviour_by_script_name(self) -> None:
        guid = "aaaa1111bbbb2222cccc3333dddd4444"
        text = (
            YAML_HEADER
            + make_gameobject("100", "Player", ["200", "300"])
            + make_transform("200", "100")
            + make_monobehaviour("300", "100", guid=guid)
        )
        script_map = {guid: "PlayerController"}
        tree = SymbolTree.build(text, "test.prefab", script_map)
        matches = tree.resolve("Player/MonoBehaviour(PlayerController)")
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0].file_id, "300")

    def test_resolve_monobehaviour_lenient(self) -> None:
        """Bare 'MonoBehaviour' matches when there's only one on the GO."""
        guid = "aaaa1111bbbb2222cccc3333dddd4444"
        text = (
            YAML_HEADER
            + make_gameobject("100", "Player", ["200", "300"])
            + make_transform("200", "100")
            + make_monobehaviour("300", "100", guid=guid)
        )
        tree = SymbolTree.build(text, "test.prefab")
        matches = tree.resolve("Player/MonoBehaviour")
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0].file_id, "300")


class TestSymbolTreeBuildDuplicateNames(unittest.TestCase):
    """SymbolTree.build with duplicate sibling names."""

    def _build_with_duplicates(self) -> SymbolTree:
        text = (
            YAML_HEADER
            + make_gameobject("100", "Root", ["200"])
            + make_transform("200", "100", children_file_ids=["400", "600", "800"])
            + make_gameobject("300", "Cube", ["400"])
            + make_transform("400", "300", father_file_id="200")
            + make_gameobject("500", "Cube", ["600"])
            + make_transform("600", "500", father_file_id="200")
            + make_gameobject("700", "Cube", ["800"])
            + make_transform("800", "700", father_file_id="200")
        )
        return SymbolTree.build(text, "test.prefab")

    def test_duplicate_names_all_present(self) -> None:
        tree = self._build_with_duplicates()
        root = tree.roots[0]
        go_children = [c for c in root.children if c.kind == SymbolKind.GAME_OBJECT]
        self.assertEqual(len(go_children), 3)
        names = [c.name for c in go_children]
        self.assertEqual(names, ["Cube", "Cube", "Cube"])

    def test_resolve_first_duplicate(self) -> None:
        tree = self._build_with_duplicates()
        # "Cube" alone matches all three
        matches = tree.resolve("Root/Cube")
        self.assertEqual(len(matches), 3)

    def test_resolve_disambiguated_duplicates(self) -> None:
        tree = self._build_with_duplicates()
        # "Cube#0" matches the first
        matches = tree.resolve("Root/Cube#0")
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0].file_id, "300")

        # "Cube#1" matches the second
        matches = tree.resolve("Root/Cube#1")
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0].file_id, "500")

        # "Cube#2" matches the third
        matches = tree.resolve("Root/Cube#2")
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0].file_id, "700")

    def test_resolve_disambiguated_out_of_range(self) -> None:
        tree = self._build_with_duplicates()
        matches = tree.resolve("Root/Cube#5")
        self.assertEqual(len(matches), 0)


class TestSymbolTreeBuildUnnamed(unittest.TestCase):
    """SymbolTree.build with unnamed GameObjects."""

    def test_unnamed_go(self) -> None:
        text = (
            YAML_HEADER
            + make_gameobject("100", "", ["200"])
            + make_transform("200", "100")
        )
        tree = SymbolTree.build(text, "test.prefab")
        root = tree.roots[0]
        self.assertEqual(root.name, "<unnamed:100>")


class TestSymbolTreeResolve(unittest.TestCase):
    """SymbolTree.resolve and resolve_unique."""

    def _build_simple(self) -> SymbolTree:
        text = (
            YAML_HEADER
            + make_gameobject("100", "Root", ["200", "300"])
            + make_transform("200", "100")
            + make_meshrenderer("300", "100")
        )
        return SymbolTree.build(text, "test.prefab")

    def test_resolve_not_found(self) -> None:
        tree = self._build_simple()
        matches = tree.resolve("NonExistent")
        self.assertEqual(matches, [])

    def test_resolve_empty_path(self) -> None:
        tree = self._build_simple()
        matches = tree.resolve("")
        self.assertEqual(matches, [])

    def test_resolve_root_go(self) -> None:
        tree = self._build_simple()
        matches = tree.resolve("Root")
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0].file_id, "100")

    def test_resolve_component(self) -> None:
        tree = self._build_simple()
        matches = tree.resolve("Root/MeshRenderer")
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0].file_id, "300")

    def test_resolve_unique_success(self) -> None:
        tree = self._build_simple()
        node = tree.resolve_unique("Root/MeshRenderer")
        self.assertEqual(node.file_id, "300")

    def test_resolve_unique_not_found(self) -> None:
        tree = self._build_simple()
        with self.assertRaises(SymbolNotFoundError):
            tree.resolve_unique("Root/Camera")

    def test_resolve_unique_ambiguous(self) -> None:
        text = (
            YAML_HEADER
            + make_gameobject("100", "Root", ["200"])
            + make_transform("200", "100", children_file_ids=["400", "600"])
            + make_gameobject("300", "Child", ["400"])
            + make_transform("400", "300", father_file_id="200")
            + make_gameobject("500", "Child", ["600"])
            + make_transform("600", "500", father_file_id="200")
        )
        tree = SymbolTree.build(text, "test.prefab")
        with self.assertRaises(AmbiguousSymbolError):
            tree.resolve_unique("Root/Child")

    def test_resolve_file_id(self) -> None:
        tree = self._build_simple()
        node = tree.resolve_file_id("300")
        self.assertIsNotNone(node)
        self.assertEqual(node.name, "MeshRenderer")  # type: ignore[union-attr]

    def test_resolve_file_id_not_found(self) -> None:
        tree = self._build_simple()
        self.assertIsNone(tree.resolve_file_id("999"))

    def test_resolve_monobehaviour_by_guid_prefix(self) -> None:
        guid = "aaaa1111bbbb2222cccc3333dddd4444"
        text = (
            YAML_HEADER
            + make_gameobject("100", "Obj", ["200", "300"])
            + make_transform("200", "100")
            + make_monobehaviour("300", "100", guid=guid)
        )
        tree = SymbolTree.build(text, "test.prefab")
        matches = tree.resolve("Obj/MonoBehaviour(guid:aaaa1111)")
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0].file_id, "300")


class TestSymbolTreeProperties(unittest.TestCase):
    """SymbolTree.build with include_properties=True."""

    def test_monobehaviour_fields_as_properties(self) -> None:
        guid = "aaaa1111bbbb2222cccc3333dddd4444"
        text = (
            YAML_HEADER
            + make_gameobject("100", "Player", ["200", "300"])
            + make_transform("200", "100")
            + make_monobehaviour(
                "300", "100", guid=guid,
                fields={"moveSpeed": "{fileID: 0}", "target": "{fileID: 400, guid: , type: 0}"},
            )
        )
        tree = SymbolTree.build(text, "test.prefab", include_properties=True)
        root = tree.roots[0]
        mb_nodes = [
            c for c in root.children
            if c.kind == SymbolKind.COMPONENT and c.class_id == "114"
        ]
        self.assertEqual(len(mb_nodes), 1)
        mb = mb_nodes[0]
        # Properties should be populated
        self.assertIn("moveSpeed", mb.properties)
        # Property children should exist
        prop_names = [c.name for c in mb.children if c.kind == SymbolKind.PROPERTY]
        self.assertIn("moveSpeed", prop_names)

    def test_no_properties_when_not_requested(self) -> None:
        guid = "aaaa1111bbbb2222cccc3333dddd4444"
        text = (
            YAML_HEADER
            + make_gameobject("100", "Player", ["200", "300"])
            + make_transform("200", "100")
            + make_monobehaviour(
                "300", "100", guid=guid,
                fields={"moveSpeed": "{fileID: 0}"},
            )
        )
        tree = SymbolTree.build(text, "test.prefab", include_properties=False)
        root = tree.roots[0]
        mb_nodes = [
            c for c in root.children
            if c.kind == SymbolKind.COMPONENT and c.class_id == "114"
        ]
        self.assertEqual(len(mb_nodes), 1)
        self.assertEqual(mb_nodes[0].properties, {})
        self.assertEqual(mb_nodes[0].children, [])


class TestSymbolTreeToOverview(unittest.TestCase):
    """SymbolTree.to_overview at various depths."""

    def _build_tree(self) -> SymbolTree:
        text = (
            YAML_HEADER
            + make_gameobject("100", "Root", ["200", "300"])
            + make_transform("200", "100", children_file_ids=["500"])
            + make_meshrenderer("300", "100")
            + make_gameobject("400", "Child", ["500"])
            + make_transform("500", "400", father_file_id="200")
        )
        return SymbolTree.build(text, "test.prefab")

    def test_depth_0(self) -> None:
        overview = self._build_tree().to_overview(depth=0)
        self.assertEqual(len(overview), 1)
        root = overview[0]
        self.assertEqual(root["name"], "Root")
        # depth=0 means no children included
        self.assertNotIn("children", root)

    def test_depth_1(self) -> None:
        overview = self._build_tree().to_overview(depth=1)
        root = overview[0]
        self.assertIn("children", root)
        child_names = [c["name"] for c in root["children"]]
        self.assertIn("Transform", child_names)
        self.assertIn("MeshRenderer", child_names)
        self.assertIn("Child", child_names)
        # Children's children should not be included
        for child in root["children"]:
            self.assertNotIn("children", child)

    def test_depth_2(self) -> None:
        overview = self._build_tree().to_overview(depth=2)
        root = overview[0]
        # Find the Child GO
        child_go = next(
            c for c in root["children"]
            if c["name"] == "Child"
        )
        # Child should have its own children (Transform)
        self.assertIn("children", child_go)


class TestSymbolTreeQuery(unittest.TestCase):
    """SymbolTree.query method."""

    def test_query_empty_path_returns_roots(self) -> None:
        text = (
            YAML_HEADER
            + make_gameobject("100", "Root", ["200"])
            + make_transform("200", "100")
        )
        tree = SymbolTree.build(text, "test.prefab")
        result = tree.query("", depth=0)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["name"], "Root")

    def test_query_with_path(self) -> None:
        text = (
            YAML_HEADER
            + make_gameobject("100", "Root", ["200", "300"])
            + make_transform("200", "100")
            + make_meshrenderer("300", "100")
        )
        tree = SymbolTree.build(text, "test.prefab")
        result = tree.query("Root/MeshRenderer", depth=0)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["name"], "MeshRenderer")


class TestSymbolTreeMultipleRoots(unittest.TestCase):
    """SymbolTree with multiple root GameObjects (scene-like)."""

    def test_multiple_roots(self) -> None:
        text = (
            YAML_HEADER
            + make_gameobject("100", "Camera", ["200"])
            + make_transform("200", "100")
            + make_gameobject("300", "Light", ["400"])
            + make_transform("400", "300")
        )
        tree = SymbolTree.build(text, "test.unity")
        self.assertEqual(len(tree.roots), 2)
        names = {r.name for r in tree.roots}
        self.assertEqual(names, {"Camera", "Light"})

    def test_resolve_specific_root(self) -> None:
        text = (
            YAML_HEADER
            + make_gameobject("100", "Camera", ["200"])
            + make_transform("200", "100")
            + make_gameobject("300", "Light", ["400"])
            + make_transform("400", "300")
        )
        tree = SymbolTree.build(text, "test.unity")
        matches = tree.resolve("Light")
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0].file_id, "300")


class TestSymbolTreeRectTransform(unittest.TestCase):
    """SymbolTree with RectTransform (UI components)."""

    def test_rect_transform_component(self) -> None:
        text = (
            YAML_HEADER
            + make_gameobject("100", "Canvas", ["200"])
            + make_transform("200", "100", is_rect=True)
        )
        tree = SymbolTree.build(text, "test.prefab")
        root = tree.roots[0]
        comp_names = [c.name for c in root.children if c.kind == SymbolKind.COMPONENT]
        self.assertIn("RectTransform", comp_names)

    def test_resolve_rect_transform(self) -> None:
        text = (
            YAML_HEADER
            + make_gameobject("100", "Canvas", ["200"])
            + make_transform("200", "100", is_rect=True)
        )
        tree = SymbolTree.build(text, "test.prefab")
        matches = tree.resolve("Canvas/RectTransform")
        self.assertEqual(len(matches), 1)


class TestSymbolNodePrefabInstance(unittest.TestCase):
    """SymbolNode with PREFAB_INSTANCE kind and source_prefab."""

    def test_prefab_instance_kind_value(self) -> None:
        self.assertEqual(SymbolKind.PREFAB_INSTANCE.value, "prefab_instance")

    def test_source_prefab_default_empty(self) -> None:
        node = SymbolNode(
            kind=SymbolKind.PREFAB_INSTANCE,
            name="[PrefabInstance: test.prefab]",
            file_id="999",
            class_id="1001",
        )
        self.assertEqual(node.source_prefab, "")

    def test_to_dict_includes_source_prefab(self) -> None:
        node = SymbolNode(
            kind=SymbolKind.PREFAB_INSTANCE,
            name="[PrefabInstance: Assets/Shirt.prefab]",
            file_id="999",
            class_id="1001",
            source_prefab="Assets/Shirt.prefab",
        )
        d = node.to_dict()
        self.assertEqual(d["kind"], "prefab_instance")
        self.assertEqual(d["source_prefab"], "Assets/Shirt.prefab")

    def test_to_dict_omits_empty_source_prefab(self) -> None:
        node = SymbolNode(
            kind=SymbolKind.GAME_OBJECT,
            name="Root",
            file_id="100",
            class_id="1",
        )
        d = node.to_dict()
        self.assertNotIn("source_prefab", d)


if __name__ == "__main__":
    unittest.main()
