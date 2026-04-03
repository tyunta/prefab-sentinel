"""Tests for the symbol_tree module."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from prefab_sentinel.symbol_tree import (
    AmbiguousSymbolError,
    SymbolKind,
    SymbolNode,
    SymbolNotFoundError,
    SymbolTree,
    build_script_name_map,
)
from prefab_sentinel.symbol_tree_builder import build_symbol_tree
from tests.yaml_helpers import (
    YAML_HEADER,
    make_gameobject,
    make_meshrenderer,
    make_monobehaviour,
    make_prefab_instance,
    make_transform,
)


class TestSymbolTreeBuildEmpty(unittest.TestCase):
    """build_symbol_tree with empty or header-only YAML."""

    def test_empty_string(self) -> None:
        tree = build_symbol_tree("", "test.prefab")
        self.assertEqual(tree.roots, [])
        self.assertEqual(tree.asset_path, "test.prefab")

    def test_header_only(self) -> None:
        tree = build_symbol_tree(YAML_HEADER, "test.prefab")
        self.assertEqual(tree.roots, [])


class TestSymbolTreeBuildSingleRoot(unittest.TestCase):
    """build_symbol_tree with a single root GameObject."""

    def test_single_root_go(self) -> None:
        text = (
            YAML_HEADER
            + make_gameobject("100", "Root", ["200"])
            + make_transform("200", "100")
        )
        tree = build_symbol_tree(text, "test.prefab")
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
        tree = build_symbol_tree(text, "test.prefab")
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
        tree = build_symbol_tree(text, "test.prefab")
        root = tree.roots[0]
        comp_names = [c.name for c in root.children if c.kind == SymbolKind.COMPONENT]
        self.assertIn("MeshRenderer", comp_names)


class TestSymbolTreeBuildNestedHierarchy(unittest.TestCase):
    """build_symbol_tree with nested GameObjects (3+ levels)."""

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
        return build_symbol_tree(text, "test.prefab")

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
    """build_symbol_tree with MonoBehaviour and script name resolution."""

    def test_monobehaviour_with_script_name(self) -> None:
        guid = "aaaa1111bbbb2222cccc3333dddd4444"
        text = (
            YAML_HEADER
            + make_gameobject("100", "Player", ["200", "300"])
            + make_transform("200", "100")
            + make_monobehaviour("300", "100", guid=guid)
        )
        script_map = {guid: "PlayerController"}
        tree = build_symbol_tree(text, "test.prefab", script_map)
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
        tree = build_symbol_tree(text, "test.prefab")
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
        tree = build_symbol_tree(text, "test.prefab", script_map)
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
        tree = build_symbol_tree(text, "test.prefab")
        matches = tree.resolve("Player/MonoBehaviour")
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0].file_id, "300")


class TestSymbolTreeBuildDuplicateNames(unittest.TestCase):
    """build_symbol_tree with duplicate sibling names."""

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
        return build_symbol_tree(text, "test.prefab")

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
    """build_symbol_tree with unnamed GameObjects."""

    def test_unnamed_go(self) -> None:
        text = (
            YAML_HEADER
            + make_gameobject("100", "", ["200"])
            + make_transform("200", "100")
        )
        tree = build_symbol_tree(text, "test.prefab")
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
        return build_symbol_tree(text, "test.prefab")

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
        tree = build_symbol_tree(text, "test.prefab")
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
        tree = build_symbol_tree(text, "test.prefab")
        matches = tree.resolve("Obj/MonoBehaviour(guid:aaaa1111)")
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0].file_id, "300")


class TestSymbolTreeProperties(unittest.TestCase):
    """build_symbol_tree with include_properties=True."""

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
        tree = build_symbol_tree(text, "test.prefab", include_properties=True)
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
        tree = build_symbol_tree(text, "test.prefab", include_properties=False)
        root = tree.roots[0]
        mb_nodes = [
            c for c in root.children
            if c.kind == SymbolKind.COMPONENT and c.class_id == "114"
        ]
        self.assertEqual(len(mb_nodes), 1)
        self.assertEqual(mb_nodes[0].properties, {})
        self.assertEqual(mb_nodes[0].children, [])


class TestSymbolNodeDetailSerialization(unittest.TestCase):
    """SymbolNode.to_dict with detail parameter."""

    def _build_component_node_with_properties(self) -> SymbolNode:
        """Component node with properties and property children."""
        prop_a = SymbolNode(
            kind=SymbolKind.PROPERTY,
            name="speed",
            file_id="",
            class_id="",
            depth=2,
            properties={"speed": "5.0"},
        )
        prop_b = SymbolNode(
            kind=SymbolKind.PROPERTY,
            name="target",
            file_id="",
            class_id="",
            depth=2,
            properties={"target": "{fileID: 0}"},
        )
        return SymbolNode(
            kind=SymbolKind.COMPONENT,
            name="MonoBehaviour(PlayerScript)",
            file_id="300",
            class_id="114",
            script_guid="aaaa1111bbbb2222cccc3333dddd4444",
            script_name="PlayerScript",
            depth=1,
            properties={"speed": "5.0", "target": "{fileID: 0}"},
            children=[prop_a, prop_b],
        )

    def _build_go_with_component(self) -> SymbolNode:
        """GO node with a component child that has properties."""
        comp = self._build_component_node_with_properties()
        return SymbolNode(
            kind=SymbolKind.GAME_OBJECT,
            name="Player",
            file_id="100",
            class_id="1",
            depth=0,
            children=[comp],
        )

    def test_detail_summary_returns_kind_and_name_only(self) -> None:
        node = self._build_component_node_with_properties()
        d = node.to_dict(detail="summary")
        self.assertEqual(d["kind"], "component")
        self.assertEqual(d["name"], "MonoBehaviour(PlayerScript)")
        self.assertNotIn("file_id", d)
        self.assertNotIn("class_id", d)
        self.assertNotIn("script_guid", d)
        self.assertNotIn("script_name", d)
        self.assertNotIn("properties", d)
        self.assertNotIn("field_names", d)

    def test_detail_fields_returns_field_names(self) -> None:
        node = self._build_component_node_with_properties()
        d = node.to_dict(detail="fields")
        self.assertEqual(d["kind"], "component")
        self.assertEqual(d["name"], "MonoBehaviour(PlayerScript)")
        self.assertEqual(d["field_names"], ["speed", "target"])
        self.assertNotIn("file_id", d)
        self.assertNotIn("class_id", d)
        self.assertNotIn("script_guid", d)
        self.assertNotIn("script_name", d)
        self.assertNotIn("properties", d)

    def test_detail_full_returns_all_fields(self) -> None:
        node = self._build_component_node_with_properties()
        d = node.to_dict(detail="full")
        self.assertEqual(d["kind"], "component")
        self.assertEqual(d["name"], "MonoBehaviour(PlayerScript)")
        self.assertEqual(d["file_id"], "300")
        self.assertEqual(d["class_id"], "114")
        self.assertEqual(d["script_guid"], "aaaa1111bbbb2222cccc3333dddd4444")
        self.assertEqual(d["script_name"], "PlayerScript")
        self.assertIn("properties", d)
        self.assertNotIn("field_names", d)

    def test_default_detail_is_full(self) -> None:
        node = self._build_component_node_with_properties()
        d_default = node.to_dict()
        d_full = node.to_dict(detail="full")
        self.assertEqual(d_default, d_full)

    def test_detail_summary_excludes_property_children(self) -> None:
        node = self._build_component_node_with_properties()
        d = node.to_dict(detail="summary")
        children = d.get("children", [])
        prop_children = [c for c in children if c.get("kind") == "property"]
        self.assertEqual(prop_children, [])

    def test_detail_fields_excludes_property_children(self) -> None:
        node = self._build_component_node_with_properties()
        d = node.to_dict(detail="fields")
        children = d.get("children", [])
        prop_children = [c for c in children if c.get("kind") == "property"]
        self.assertEqual(prop_children, [])

    def test_detail_full_includes_property_children(self) -> None:
        node = self._build_component_node_with_properties()
        d = node.to_dict(detail="full")
        children = d.get("children", [])
        prop_children = [c for c in children if c.get("kind") == "property"]
        self.assertEqual(len(prop_children), 2)

    def test_detail_propagates_to_children(self) -> None:
        go = self._build_go_with_component()
        d = go.to_dict(detail="summary")
        comp_child = d["children"][0]
        self.assertEqual(set(comp_child.keys()), {"kind", "name"})

    def test_detail_fields_propagates_to_children(self) -> None:
        go = self._build_go_with_component()
        d = go.to_dict(detail="fields")
        comp_child = d["children"][0]
        self.assertIn("field_names", comp_child)
        self.assertNotIn("properties", comp_child)

    def test_detail_with_depth_limit(self) -> None:
        go = self._build_go_with_component()
        d = go.to_dict(depth_limit=0, detail="summary")
        self.assertNotIn("children", d)

    def test_go_node_summary_has_kind_and_name_only(self) -> None:
        go = self._build_go_with_component()
        d = go.to_dict(depth_limit=0, detail="summary")
        self.assertEqual(d["kind"], "game_object")
        self.assertEqual(d["name"], "Player")
        self.assertNotIn("file_id", d)
        self.assertNotIn("class_id", d)

    def test_field_names_are_sorted(self) -> None:
        node = self._build_component_node_with_properties()
        d = node.to_dict(detail="fields")
        self.assertEqual(d["field_names"], sorted(d["field_names"]))

    def test_node_without_properties_has_empty_field_names(self) -> None:
        node = SymbolNode(
            kind=SymbolKind.COMPONENT,
            name="Transform",
            file_id="200",
            class_id="4",
            depth=1,
        )
        d = node.to_dict(detail="fields")
        self.assertNotIn("field_names", d)


class TestSymbolTreeToOverviewDetail(unittest.TestCase):
    """SymbolTree.to_overview with detail and new depth defaults."""

    def _build_tree_with_props(self) -> SymbolTree:
        text = (
            YAML_HEADER
            + make_gameobject("100", "Root", ["200", "300"])
            + make_transform("200", "100", children_file_ids=["500"])
            + make_monobehaviour(
                "300", "100",
                fields={"speed": "{fileID: 0}", "health": "{fileID: 0}"},
            )
            + make_gameobject("400", "Child", ["500"])
            + make_transform("500", "400", father_file_id="200")
        )
        return build_symbol_tree(text, "test.prefab", include_properties=True)

    def test_to_overview_default_depth_none_returns_full_tree(self) -> None:
        tree = self._build_tree_with_props()
        overview = tree.to_overview()
        root = overview[0]
        self.assertIn("children", root)
        go_children = [c for c in root["children"] if c.get("kind") == "game_object"]
        self.assertGreater(len(go_children), 0)

    def test_to_overview_with_summary(self) -> None:
        tree = self._build_tree_with_props()
        overview = tree.to_overview(depth=1, detail="summary")
        root = overview[0]
        comp_children = [c for c in root["children"] if c.get("kind") == "component"]
        for comp in comp_children:
            self.assertNotIn("file_id", comp)
            self.assertNotIn("properties", comp)

    def test_to_overview_with_fields(self) -> None:
        tree = self._build_tree_with_props()
        overview = tree.to_overview(depth=1, detail="fields")
        root = overview[0]
        mb_children = [c for c in root["children"] if c.get("kind") == "component" and "MonoBehaviour" in c.get("name", "")]
        for mb in mb_children:
            self.assertIn("field_names", mb)
            self.assertNotIn("properties", mb)

    def test_to_overview_depth_none_detail_full_is_default(self) -> None:
        tree = self._build_tree_with_props()
        overview_default = tree.to_overview()
        overview_explicit = tree.to_overview(depth=None, detail="full")
        self.assertEqual(overview_default, overview_explicit)


class TestSymbolTreeQueryCleanup(unittest.TestCase):
    """SymbolTree.query with dead include_properties parameter removed."""

    def test_query_works_without_include_properties(self) -> None:
        text = (
            YAML_HEADER
            + make_gameobject("100", "Root", ["200"])
            + make_transform("200", "100")
        )
        tree = build_symbol_tree(text, "test.prefab")
        result = tree.query("Root", depth=0)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["name"], "Root")


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
        return build_symbol_tree(text, "test.prefab")

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
        tree = build_symbol_tree(text, "test.prefab")
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
        tree = build_symbol_tree(text, "test.prefab")
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
        tree = build_symbol_tree(text, "test.unity")
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
        tree = build_symbol_tree(text, "test.unity")
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
        tree = build_symbol_tree(text, "test.prefab")
        root = tree.roots[0]
        comp_names = [c.name for c in root.children if c.kind == SymbolKind.COMPONENT]
        self.assertIn("RectTransform", comp_names)

    def test_resolve_rect_transform(self) -> None:
        text = (
            YAML_HEADER
            + make_gameobject("100", "Canvas", ["200"])
            + make_transform("200", "100", is_rect=True)
        )
        tree = build_symbol_tree(text, "test.prefab")
        matches = tree.resolve("Canvas/RectTransform")
        self.assertEqual(len(matches), 1)


class TestSymbolTreeNestedExpansion(unittest.TestCase):
    """build_symbol_tree with expand_nested=True."""

    CHILD_GUID = "aabbccdd11223344aabbccdd11223344"

    def _write_child_prefab(self, tmpdir: Path) -> Path:
        child_path = tmpdir / "Assets" / "Child.prefab"
        child_path.parent.mkdir(parents=True, exist_ok=True)
        child_text = (
            YAML_HEADER
            + make_gameobject("500", "ChildRoot", ["600", "700"])
            + make_transform("600", "500")
            + make_meshrenderer("700", "500")
        )
        child_path.write_text(child_text, encoding="utf-8")
        return child_path

    def _parent_text_with_instance(self) -> str:
        return (
            YAML_HEADER
            + make_gameobject("100", "Avatar", ["200"])
            + make_transform("200", "100")
            + make_prefab_instance("300", self.CHILD_GUID, transform_parent="200")
        )

    def test_expand_nested_false_skips_prefab_instances(self) -> None:
        text = self._parent_text_with_instance()
        tree = build_symbol_tree(text, "test.prefab", expand_nested=False)
        self.assertEqual(len(tree.roots), 1)
        self.assertEqual(tree.roots[0].name, "Avatar")

    def test_expand_nested_true_without_guid_map_skips(self) -> None:
        text = self._parent_text_with_instance()
        tree = build_symbol_tree(text, "test.prefab", expand_nested=True, guid_to_asset_path=None)
        self.assertEqual(len(tree.roots), 1)

    def test_expand_nested_true_creates_marker_node(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            child_path = self._write_child_prefab(Path(tmpdir))
            guid_map = {self.CHILD_GUID: child_path}
            text = self._parent_text_with_instance()
            tree = build_symbol_tree(text, "test.prefab", expand_nested=True, guid_to_asset_path=guid_map)
            avatar = tree.roots[0]
            pi_nodes = [c for c in avatar.children if c.kind == SymbolKind.PREFAB_INSTANCE]
            self.assertEqual(len(pi_nodes), 1)
            pi = pi_nodes[0]
            self.assertEqual(pi.class_id, "1001")
            self.assertIn("Child.prefab", pi.source_prefab)

    def test_expanded_prefab_instance_has_child_nodes(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            child_path = self._write_child_prefab(Path(tmpdir))
            guid_map = {self.CHILD_GUID: child_path}
            text = self._parent_text_with_instance()
            tree = build_symbol_tree(text, "test.prefab", expand_nested=True, guid_to_asset_path=guid_map)
            avatar = tree.roots[0]
            pi = [c for c in avatar.children if c.kind == SymbolKind.PREFAB_INSTANCE][0]
            child_gos = [c for c in pi.children if c.kind == SymbolKind.GAME_OBJECT]
            self.assertEqual(len(child_gos), 1)
            self.assertEqual(child_gos[0].name, "ChildRoot")

    def test_unresolvable_guid_creates_unresolved_marker(self) -> None:
        text = self._parent_text_with_instance()
        guid_map: dict[str, Path] = {}
        tree = build_symbol_tree(text, "test.prefab", expand_nested=True, guid_to_asset_path=guid_map)
        avatar = tree.roots[0]
        pi_nodes = [c for c in avatar.children if c.kind == SymbolKind.PREFAB_INSTANCE]
        self.assertEqual(len(pi_nodes), 1)
        self.assertIn("Unresolved", pi_nodes[0].name)
        self.assertEqual(pi_nodes[0].children, [])

    def test_prefab_instance_marker_in_file_id_index(self) -> None:
        text = self._parent_text_with_instance()
        guid_map: dict[str, Path] = {}
        tree = build_symbol_tree(text, "test.prefab", expand_nested=True, guid_to_asset_path=guid_map)
        node = tree.resolve_file_id("300")
        self.assertIsNotNone(node)
        self.assertEqual(node.kind, SymbolKind.PREFAB_INSTANCE)

    def test_nested_child_nodes_not_in_file_id_index(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            child_path = self._write_child_prefab(Path(tmpdir))
            guid_map = {self.CHILD_GUID: child_path}
            text = self._parent_text_with_instance()
            tree = build_symbol_tree(text, "test.prefab", expand_nested=True, guid_to_asset_path=guid_map)
            self.assertIsNone(tree.resolve_file_id("500"))
            self.assertIsNone(tree.resolve_file_id("600"))

    def test_missing_file_creates_unresolved_marker(self) -> None:
        """GUID maps to a path but the file doesn't exist on disk."""
        missing_path = Path("/nonexistent/Bad.prefab")
        guid_map = {self.CHILD_GUID: missing_path}
        text = self._parent_text_with_instance()
        tree = build_symbol_tree(text, "test.prefab", expand_nested=True, guid_to_asset_path=guid_map)
        avatar = tree.roots[0]
        pi_nodes = [c for c in avatar.children if c.kind == SymbolKind.PREFAB_INSTANCE]
        self.assertEqual(len(pi_nodes), 1)
        self.assertIn("Unresolved", pi_nodes[0].name)

    def test_depth_limit_stops_expansion(self) -> None:
        text = self._parent_text_with_instance()
        guid_map: dict[str, Path] = {}
        tree = build_symbol_tree(text, "test.prefab", expand_nested=True, guid_to_asset_path=guid_map, _nested_depth=10)
        avatar = tree.roots[0]
        pi_nodes = [c for c in avatar.children if c.kind == SymbolKind.PREFAB_INSTANCE]
        self.assertEqual(len(pi_nodes), 0)

    def test_to_dict_on_expanded_tree(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            child_path = self._write_child_prefab(Path(tmpdir))
            guid_map = {self.CHILD_GUID: child_path}
            text = self._parent_text_with_instance()
            tree = build_symbol_tree(text, "test.prefab", expand_nested=True, guid_to_asset_path=guid_map)
            overview = tree.to_overview(depth=3)
            self.assertTrue(len(overview) > 0)
            avatar = overview[0]
            pi_dicts = [c for c in avatar.get("children", []) if c.get("kind") == "prefab_instance"]
            self.assertEqual(len(pi_dicts), 1)
            self.assertIn("source_prefab", pi_dicts[0])


class TestSymbolTreeNestedResolution(unittest.TestCase):
    """Path resolution through PrefabInstance boundaries."""

    CHILD_GUID = "aabbccdd11223344aabbccdd11223344"

    def _write_child_prefab(self, tmpdir: Path) -> Path:
        child_path = tmpdir / "Assets" / "Child.prefab"
        child_path.parent.mkdir(parents=True, exist_ok=True)
        child_text = (
            YAML_HEADER
            + make_gameobject("500", "ChildRoot", ["600", "700"])
            + make_transform("600", "500")
            + make_meshrenderer("700", "500")
        )
        child_path.write_text(child_text, encoding="utf-8")
        return child_path

    def _parent_text_with_instance(self) -> str:
        return (
            YAML_HEADER
            + make_gameobject("100", "Avatar", ["200"])
            + make_transform("200", "100")
            + make_prefab_instance("300", self.CHILD_GUID, transform_parent="200")
        )

    def test_resolve_through_prefab_instance(self) -> None:
        """Avatar/ChildRoot resolves — PrefabInstance boundary is transparent."""
        with tempfile.TemporaryDirectory() as tmpdir:
            child_path = self._write_child_prefab(Path(tmpdir))
            guid_map = {self.CHILD_GUID: child_path}
            text = self._parent_text_with_instance()
            tree = build_symbol_tree(text, "test.prefab", expand_nested=True, guid_to_asset_path=guid_map)
            matches = tree.resolve("Avatar")
            self.assertEqual(len(matches), 1)
            # ChildRoot is inside PrefabInstance which is a child of Avatar
            matches = tree.resolve("Avatar/ChildRoot")
            self.assertEqual(len(matches), 1)
            self.assertEqual(matches[0].name, "ChildRoot")

    def test_resolve_nested_path_through_prefab_instance(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            child_path = self._write_child_prefab(Path(tmpdir))
            guid_map = {self.CHILD_GUID: child_path}
            text = self._parent_text_with_instance()
            tree = build_symbol_tree(text, "test.prefab", expand_nested=True, guid_to_asset_path=guid_map)
            matches = tree.resolve("Avatar/ChildRoot/MeshRenderer")
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


class TestSessionCacheBypass(unittest.TestCase):
    """Session.get_symbol_tree bypasses cache when expand_nested=True."""

    def test_expand_nested_bypasses_cache(self) -> None:
        from prefab_sentinel.session import ProjectSession

        session = ProjectSession()
        text = YAML_HEADER + make_gameobject("100", "Root", ["200"]) + make_transform("200", "100")
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "test.prefab"
            path.write_text(text, encoding="utf-8")
            # First call caches
            tree1 = session.get_symbol_tree(path, text)
            # Second call with expand_nested should NOT return cached
            tree2 = session.get_symbol_tree(path, text, expand_nested=True)
            # They should be different objects (not cached)
            self.assertIsNot(tree1, tree2)


class TestBuildScriptNameMap(unittest.TestCase):
    """Direct unit tests for build_script_name_map."""

    def test_empty_index(self) -> None:
        result = build_script_name_map({})
        self.assertEqual(result, {})

    def test_single_cs_entry(self) -> None:
        result = build_script_name_map({"abc": Path("Foo.cs")})
        self.assertEqual(result, {"abc": "Foo"})

    def test_filters_non_cs_entries(self) -> None:
        result = build_script_name_map({
            "abc": Path("Foo.cs"),
            "def": Path("Bar.mat"),
        })
        self.assertEqual(result, {"abc": "Foo"})


if __name__ == "__main__":
    unittest.main()
