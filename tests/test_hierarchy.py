"""Tests for prefab_sentinel.hierarchy."""

from __future__ import annotations

from prefab_sentinel.hierarchy import analyze_hierarchy, format_tree
from tests.yaml_helpers import (
    YAML_HEADER,
    make_gameobject,
    make_meshfilter,
    make_meshrenderer,
    make_monobehaviour,
    make_transform,
)

# ---------------------------------------------------------------------------
# analyze_hierarchy tests
# ---------------------------------------------------------------------------


class TestAnalyzeHierarchy:
    def test_empty_text(self) -> None:
        result = analyze_hierarchy("")
        assert result.roots == []
        assert result.total_game_objects == 0
        assert result.total_components == 0
        assert result.max_depth == 0

    def test_single_root(self) -> None:
        text = YAML_HEADER + make_gameobject("100", "Root", ["200"]) + make_transform("200", "100")
        result = analyze_hierarchy(text)
        assert len(result.roots) == 1
        assert result.roots[0].name == "Root"
        assert result.roots[0].depth == 0
        assert result.roots[0].children == []
        assert result.total_game_objects == 1
        assert result.max_depth == 0

    def test_parent_child(self) -> None:
        text = (
            YAML_HEADER
            + make_gameobject("100", "Parent", ["200"])
            + make_transform("200", "100", father_file_id="0", children_file_ids=["400"])
            + make_gameobject("300", "Child", ["400"])
            + make_transform("400", "300", father_file_id="200")
        )
        result = analyze_hierarchy(text)
        assert len(result.roots) == 1
        parent = result.roots[0]
        assert parent.name == "Parent"
        assert len(parent.children) == 1
        child = parent.children[0]
        assert child.name == "Child"
        assert child.depth == 1
        assert result.max_depth == 1

    def test_multiple_roots(self) -> None:
        text = (
            YAML_HEADER
            + make_gameobject("100", "RootA", ["200"])
            + make_transform("200", "100")
            + make_gameobject("300", "RootB", ["400"])
            + make_transform("400", "300")
        )
        result = analyze_hierarchy(text)
        assert len(result.roots) == 2
        names = [r.name for r in result.roots]
        assert "RootA" in names
        assert "RootB" in names

    def test_deep_hierarchy(self) -> None:
        # Root -> A -> B -> C (depth 3)
        text = (
            YAML_HEADER
            + make_gameobject("10", "Root", ["11"])
            + make_transform("11", "10", children_file_ids=["21"])
            + make_gameobject("20", "A", ["21"])
            + make_transform("21", "20", father_file_id="11", children_file_ids=["31"])
            + make_gameobject("30", "B", ["31"])
            + make_transform("31", "30", father_file_id="21", children_file_ids=["41"])
            + make_gameobject("40", "C", ["41"])
            + make_transform("41", "40", father_file_id="31")
        )
        result = analyze_hierarchy(text)
        assert result.max_depth == 3
        assert result.total_game_objects == 4

    def test_component_labels(self) -> None:
        text = (
            YAML_HEADER
            + make_gameobject("100", "Obj", ["200", "300", "400"])
            + make_transform("200", "100")
            + make_meshfilter("300", "100")
            + make_meshrenderer("400", "100")
        )
        result = analyze_hierarchy(text)
        root = result.roots[0]
        # Transform is filtered out; MeshFilter and MeshRenderer are included
        assert "MeshFilter" in root.components
        assert "MeshRenderer" in root.components
        assert "Transform" not in root.components

    def test_rect_transform_label_excluded(self) -> None:
        text = (
            YAML_HEADER
            + make_gameobject("100", "Canvas", ["200"])
            + make_transform("200", "100", is_rect=True)
        )
        result = analyze_hierarchy(text)
        root = result.roots[0]
        assert root.components == []

    def test_total_components(self) -> None:
        text = (
            YAML_HEADER
            + make_gameobject("100", "Obj", ["200", "300", "400"])
            + make_transform("200", "100")
            + make_meshfilter("300", "100")
            + make_meshrenderer("400", "100")
        )
        result = analyze_hierarchy(text)
        assert result.total_components == 3  # Transform + MeshFilter + MeshRenderer

    def test_unknown_class_id_label(self) -> None:
        # class_id 999 is not in _CLASS_NAMES
        text = (
            YAML_HEADER
            + make_gameobject("100", "Obj", ["200", "300"])
            + make_transform("200", "100")
            + "--- !u!999 &300\nUnknownComponent:\n  m_GameObject: {fileID: 100}\n"
        )
        result = analyze_hierarchy(text)
        root = result.roots[0]
        assert any(c.startswith("Component(") for c in root.components)

    def test_monobehaviour_label(self) -> None:
        text = (
            YAML_HEADER
            + make_gameobject("100", "Obj", ["200", "300"])
            + make_transform("200", "100")
            + make_monobehaviour("300", "100")
        )
        result = analyze_hierarchy(text)
        root = result.roots[0]
        assert "MonoBehaviour" in root.components

    def test_transform_info_attached(self) -> None:
        text = YAML_HEADER + make_gameobject("100", "Obj", ["200"]) + make_transform("200", "100")
        result = analyze_hierarchy(text)
        root = result.roots[0]
        assert root.transform is not None
        assert root.transform.file_id == "200"
        assert root.transform.game_object_file_id == "100"


# ---------------------------------------------------------------------------
# format_tree tests
# ---------------------------------------------------------------------------


class TestFormatTree:
    def _simple_hierarchy(self) -> str:
        return (
            YAML_HEADER
            + make_gameobject("100", "Root", ["200"])
            + make_transform("200", "100", children_file_ids=["400", "600"])
            + make_gameobject("300", "ChildA", ["400", "500"])
            + make_transform("400", "300", father_file_id="200")
            + make_meshfilter("500", "300")
            + make_gameobject("700", "ChildB", ["600"])
            + make_transform("600", "700", father_file_id="200")
        )

    def test_basic_tree(self) -> None:
        result = analyze_hierarchy(self._simple_hierarchy())
        tree = format_tree(result)
        assert "Root" in tree
        assert "ChildA" in tree
        assert "ChildB" in tree

    def test_show_components(self) -> None:
        result = analyze_hierarchy(self._simple_hierarchy())
        tree = format_tree(result, show_components=True)
        assert "MeshFilter" in tree

    def test_hide_components(self) -> None:
        result = analyze_hierarchy(self._simple_hierarchy())
        tree = format_tree(result, show_components=False)
        assert "MeshFilter" not in tree

    def test_max_depth_limits_output(self) -> None:
        # Root -> A -> B (depth 2)
        text = (
            YAML_HEADER
            + make_gameobject("10", "Root", ["11"])
            + make_transform("11", "10", children_file_ids=["21"])
            + make_gameobject("20", "A", ["21"])
            + make_transform("21", "20", father_file_id="11", children_file_ids=["31"])
            + make_gameobject("30", "B", ["31"])
            + make_transform("31", "30", father_file_id="21")
        )
        result = analyze_hierarchy(text)
        tree = format_tree(result, max_depth=1)
        assert "Root" in tree
        assert "A" in tree
        assert "B" not in tree

    def test_unicode_connectors(self) -> None:
        result = analyze_hierarchy(self._simple_hierarchy())
        tree = format_tree(result)
        # Should contain tree drawing characters
        assert "\u251c" in tree or "\u2514" in tree

    def test_empty_roots(self) -> None:
        from prefab_sentinel.hierarchy import HierarchyResult

        result = HierarchyResult(roots=[], total_game_objects=0, total_components=0, max_depth=0)
        tree = format_tree(result)
        assert tree == ""

    def test_multiple_roots_separated(self) -> None:
        text = (
            YAML_HEADER
            + make_gameobject("100", "RootA", ["200"])
            + make_transform("200", "100")
            + make_gameobject("300", "RootB", ["400"])
            + make_transform("400", "300")
        )
        result = analyze_hierarchy(text)
        tree = format_tree(result)
        lines = tree.split("\n")
        # Multiple roots should have a blank line between them
        assert "" in lines
