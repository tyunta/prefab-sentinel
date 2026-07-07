"""Tests for prefab_sentinel.hierarchy.

Phase-1 assertion-shape compliance (#222 Mode A):
- Multi-observation checks against a single ``analyze_hierarchy`` /
  ``format_tree`` result are collapsed into a single tuple value-pin
  so a regression surfaces all related fields in one diagnostic rather
  than failing on the first bare ``assert``.
- Every remaining bare ``assert`` carries a diagnostic message naming
  what was expected versus observed, since pytest's introspection
  prints the failing expression but not the surrounding intent.
"""

from __future__ import annotations

from prefab_sentinel.hierarchy import HierarchyNode, analyze_hierarchy, format_tree
from tests.yaml_helpers import (
    YAML_HEADER,
    make_gameobject,
    make_meshfilter,
    make_meshrenderer,
    make_monobehaviour,
    make_transform,
)

SCRIPT_GUID_ALPHA = "11112222111122221111222211112222"
SCRIPT_GUID_BETA = "33334444333344443333444433334444"

# ---------------------------------------------------------------------------
# analyze_hierarchy tests
# ---------------------------------------------------------------------------


class TestAnalyzeHierarchy:
    def test_empty_text(self) -> None:
        result = analyze_hierarchy("")
        # Empty input must yield the canonical zero-state across every
        # observable summary field; collapsing to a single tuple pin
        # so a regression in any one field surfaces all four values.
        assert (
            result.roots,
            result.total_game_objects,
            result.total_components,
            result.max_depth,
        ) == ([], 0, 0, 0), (
            "Empty YAML input must produce zero roots, zero objects, "
            "zero components, and depth 0; observed "
            f"(roots={result.roots}, total_game_objects="
            f"{result.total_game_objects}, total_components="
            f"{result.total_components}, max_depth={result.max_depth})."
        )

    def test_single_root(self) -> None:
        text = YAML_HEADER + make_gameobject("100", "Root", ["200"]) + make_transform("200", "100")
        result = analyze_hierarchy(text)
        root_name = result.roots[0].name if result.roots else None
        root_depth = result.roots[0].depth if result.roots else None
        root_children = result.roots[0].children if result.roots else None
        assert (
            len(result.roots),
            root_name,
            root_depth,
            root_children,
            result.total_game_objects,
            result.max_depth,
        ) == (1, "Root", 0, [], 1, 0), (
            "A single GameObject + Transform must produce exactly one "
            "root named 'Root' at depth 0 with no children, "
            "total_game_objects=1, max_depth=0; observed "
            f"len(roots)={len(result.roots)}, root_name={root_name}, "
            f"root_depth={root_depth}, root_children={root_children}, "
            f"total_game_objects={result.total_game_objects}, "
            f"max_depth={result.max_depth}."
        )

    def test_parent_child(self) -> None:
        text = (
            YAML_HEADER
            + make_gameobject("100", "Parent", ["200"])
            + make_transform("200", "100", father_file_id="0", children_file_ids=["400"])
            + make_gameobject("300", "Child", ["400"])
            + make_transform("400", "300", father_file_id="200")
        )
        result = analyze_hierarchy(text)
        parent = result.roots[0] if result.roots else None
        parent_name = parent.name if parent else None
        parent_children = parent.children if parent else []
        child = parent_children[0] if parent_children else None
        child_name = child.name if child else None
        child_depth = child.depth if child else None
        assert (
            len(result.roots),
            parent_name,
            len(parent_children),
            child_name,
            child_depth,
            result.max_depth,
        ) == (1, "Parent", 1, "Child", 1, 1), (
            "Parent/Child wiring must produce one root 'Parent' with "
            "exactly one child 'Child' at depth 1 and max_depth=1; "
            f"observed len(roots)={len(result.roots)}, "
            f"parent_name={parent_name}, "
            f"len(parent.children)={len(parent_children)}, "
            f"child_name={child_name}, child_depth={child_depth}, "
            f"max_depth={result.max_depth}."
        )

    def test_multiple_roots(self) -> None:
        text = (
            YAML_HEADER
            + make_gameobject("100", "RootA", ["200"])
            + make_transform("200", "100")
            + make_gameobject("300", "RootB", ["400"])
            + make_transform("400", "300")
        )
        result = analyze_hierarchy(text)
        names = sorted(r.name for r in result.roots)
        assert (len(result.roots), names) == (2, ["RootA", "RootB"]), (
            "Two parent-less GameObjects must surface as two roots "
            "named 'RootA' and 'RootB' (order-independent); observed "
            f"len(roots)={len(result.roots)}, names={names}."
        )

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
        assert (result.max_depth, result.total_game_objects) == (3, 4), (
            "Root->A->B->C must report max_depth=3 and "
            "total_game_objects=4; observed max_depth="
            f"{result.max_depth}, total_game_objects="
            f"{result.total_game_objects}."
        )

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
        # Transform is filtered out; MeshFilter and MeshRenderer are
        # included.  Pinning the membership of all three component
        # labels in one tuple so a regression in either inclusion or
        # exclusion surfaces every value.
        membership = (
            "MeshFilter" in root.components,
            "MeshRenderer" in root.components,
            "Transform" in root.components,
        )
        assert membership == (True, True, False), (
            "Component-label set must include 'MeshFilter' and "
            "'MeshRenderer' and exclude 'Transform' (Transform is "
            f"deliberately filtered out); observed components="
            f"{root.components}."
        )

    def test_rect_transform_label_excluded(self) -> None:
        text = (
            YAML_HEADER
            + make_gameobject("100", "Canvas", ["200"])
            + make_transform("200", "100", is_rect=True)
        )
        result = analyze_hierarchy(text)
        root = result.roots[0]
        assert root.components == [], (
            "A GameObject whose only component is a RectTransform must "
            "surface an empty component label list (RectTransform is "
            "filtered alongside Transform); observed "
            f"components={root.components}."
        )

    def test_total_components(self) -> None:
        text = (
            YAML_HEADER
            + make_gameobject("100", "Obj", ["200", "300", "400"])
            + make_transform("200", "100")
            + make_meshfilter("300", "100")
            + make_meshrenderer("400", "100")
        )
        result = analyze_hierarchy(text)
        # Transform + MeshFilter + MeshRenderer = 3 components total
        # (Transform is filtered from labels but counted in totals).
        assert result.total_components == 3, (
            "total_components must count every component including "
            "Transform (which is filtered only from the label list); "
            "Transform + MeshFilter + MeshRenderer = 3 expected; "
            f"observed total_components={result.total_components}."
        )

    def test_unknown_class_id_label(self) -> None:
        # class_id 999 is not in CLASS_NAMES
        text = (
            YAML_HEADER
            + make_gameobject("100", "Obj", ["200", "300"])
            + make_transform("200", "100")
            + "--- !u!999 &300\nUnknownComponent:\n  m_GameObject: {fileID: 100}\n"
        )
        result = analyze_hierarchy(text)
        root = result.roots[0]
        assert any(c.startswith("Component(") for c in root.components), (
            "An unknown Unity class id (e.g. 999) must surface a "
            "'Component(...)' fallback label so the diagnostic still "
            "names the offending entry; observed "
            f"components={root.components}."
        )

    def test_monobehaviour_label(self) -> None:
        text = (
            YAML_HEADER
            + make_gameobject("100", "Obj", ["200", "300"])
            + make_transform("200", "100")
            + make_monobehaviour("300", "100")
        )
        result = analyze_hierarchy(text)
        root = result.roots[0]
        assert "MonoBehaviour" in root.components, (
            "A MonoBehaviour component without a resolver supplied "
            "must surface the generic 'MonoBehaviour' label; observed "
            f"components={root.components}."
        )

    def test_class_id_65_renders_as_3d_box_collider(self) -> None:
        # Unity ClassIDReference: class ID 65 = BoxCollider (3D)
        text = (
            YAML_HEADER
            + make_gameobject("100", "Obj", ["200", "300"])
            + make_transform("200", "100")
            + "--- !u!65 &300\nBoxCollider:\n  m_GameObject: {fileID: 100}\n"
        )
        result = analyze_hierarchy(text)
        root = result.roots[0]
        membership = (
            "BoxCollider" in root.components,
            "BoxCollider2D" in root.components,
        )
        assert membership == (True, False), (
            "Class id 65 must resolve to the 3D 'BoxCollider' label, "
            "not 'BoxCollider2D' (class id 61); observed "
            f"components={root.components}."
        )

    def test_class_id_61_renders_as_box_collider_2d(self) -> None:
        # Unity ClassIDReference: class ID 61 = BoxCollider2D
        text = (
            YAML_HEADER
            + make_gameobject("100", "Obj", ["200", "300"])
            + make_transform("200", "100")
            + "--- !u!61 &300\nBoxCollider2D:\n  m_GameObject: {fileID: 100}\n"
        )
        result = analyze_hierarchy(text)
        root = result.roots[0]
        assert "BoxCollider2D" in root.components, (
            "Class id 61 must resolve to the 2D 'BoxCollider2D' "
            f"label; observed components={root.components}."
        )

    def test_transform_info_attached(self) -> None:
        text = YAML_HEADER + make_gameobject("100", "Obj", ["200"]) + make_transform("200", "100")
        result = analyze_hierarchy(text)
        root = result.roots[0]
        transform = root.transform
        transform_file_id = transform.file_id if transform else None
        transform_go_file_id = transform.game_object_file_id if transform else None
        assert (
            transform is not None,
            transform_file_id,
            transform_go_file_id,
        ) == (True, "200", "100"), (
            "Transform info must be attached to the root with the "
            "Transform's file_id ('200') and its GameObject's file_id "
            f"('100'); observed transform={transform}, "
            f"file_id={transform_file_id}, "
            f"game_object_file_id={transform_go_file_id}."
        )


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
        # Every named GameObject must appear in the rendered tree;
        # collapsing to a tuple pin so a regression on any one name
        # surfaces all three membership flags.
        membership = ("Root" in tree, "ChildA" in tree, "ChildB" in tree)
        assert membership == (True, True, True), (
            "format_tree must render every GameObject name ('Root', "
            "'ChildA', 'ChildB') in the output; observed "
            f"tree={tree!r}."
        )

    def test_show_components(self) -> None:
        result = analyze_hierarchy(self._simple_hierarchy())
        tree = format_tree(result, show_components=True)
        assert "MeshFilter" in tree, (
            "show_components=True must include component labels in "
            f"the rendered tree; observed tree={tree!r}."
        )

    def test_hide_components(self) -> None:
        result = analyze_hierarchy(self._simple_hierarchy())
        tree = format_tree(result, show_components=False)
        assert "MeshFilter" not in tree, (
            "show_components=False must suppress component labels in "
            f"the rendered tree; observed tree={tree!r}."
        )

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
        membership = ("Root" in tree, "A" in tree, "B" in tree)
        assert membership == (True, True, False), (
            "max_depth=1 must render the root and its direct children "
            "but drop deeper descendants ('B' is at depth 2); observed "
            f"tree={tree!r}."
        )

    def test_unicode_connectors(self) -> None:
        result = analyze_hierarchy(self._simple_hierarchy())
        tree = format_tree(result)
        # Should contain tree drawing characters (├ or └)
        assert "├" in tree or "└" in tree, (
            "Default rendering must use Unicode box-drawing "
            "connectors (U+251C '├' or U+2514 '└'); observed "
            f"tree={tree!r}."
        )

    def test_empty_roots(self) -> None:
        from prefab_sentinel.hierarchy import HierarchyResult

        result = HierarchyResult(roots=[], total_game_objects=0, total_components=0, max_depth=0)
        tree = format_tree(result)
        assert tree == "", (
            "An empty roots list must render as the empty string (no "
            f"header, no separators); observed tree={tree!r}."
        )

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
        # Multiple roots must be separated by at least one blank line
        # so a downstream reader can split-on-blank to recover roots.
        assert "" in lines, (
            "Multiple roots must be separated by a blank line in the "
            f"rendered tree; observed lines={lines}."
        )


# ---------------------------------------------------------------------------
# Override annotation tests (P4: Variant hierarchy)
# ---------------------------------------------------------------------------


class TestOverrideAnnotation:
    def test_override_count_propagates(self) -> None:
        text = (
            YAML_HEADER
            + make_gameobject("100", "Root", ["200"])
            + make_transform("200", "100", children_file_ids=["400"])
            + make_gameobject("300", "Child", ["400"])
            + make_transform("400", "300", father_file_id="200")
        )
        override_counts = {"300": 3}
        result = analyze_hierarchy(text, override_counts=override_counts)
        child = result.roots[0].children[0] if result.roots and result.roots[0].children else None
        child_name = child.name if child else None
        child_override = child.override_count if child else None
        assert (
            len(result.roots),
            child_name,
            child_override,
        ) == (1, "Child", 3), (
            "override_counts keyed by Child's GameObject file_id "
            "('300') must propagate as the Child node's "
            "override_count=3; observed len(roots)="
            f"{len(result.roots)}, child_name={child_name}, "
            f"child_override_count={child_override}."
        )

    def test_override_marker_in_tree_text(self) -> None:
        text = (
            YAML_HEADER
            + make_gameobject("100", "Root", ["200"])
            + make_transform("200", "100", children_file_ids=["400"])
            + make_gameobject("300", "Child", ["400"])
            + make_transform("400", "300", father_file_id="200")
        )
        override_counts = {"300": 5}
        result = analyze_hierarchy(text, override_counts=override_counts)
        tree = format_tree(result)
        membership = (
            "[overridden: 5]" in tree,
            "Root [overridden" in tree,
        )
        # The child carries the marker; the root does not, because
        # override_counts has no entry for its GameObject file_id.
        assert membership == (True, False), (
            "format_tree must emit '[overridden: 5]' on the Child "
            "(file_id 300) and must NOT emit '[overridden' on the "
            f"Root; observed tree={tree!r}."
        )

    def test_no_override_counts_no_markers(self) -> None:
        text = (
            YAML_HEADER
            + make_gameobject("100", "Root", ["200"])
            + make_transform("200", "100")
        )
        result = analyze_hierarchy(text)
        tree = format_tree(result)
        assert "[overridden" not in tree, (
            "Without an override_counts argument no override marker "
            "may be rendered; observed tree="
            f"{tree!r}."
        )


# ---------------------------------------------------------------------------
# MonoBehaviour resolver tests (issue #196)
# ---------------------------------------------------------------------------


class TestFormatTreeMonobehaviour:
    """Issue #196 — opt-in MonoBehaviour expansion replaces the generic
    label with the script class name when a resolver is supplied.
    """

    def _two_mono_hierarchy(self) -> str:
        return (
            YAML_HEADER
            + make_gameobject("100", "Obj", ["200", "300", "400"])
            + make_transform("200", "100")
            + make_monobehaviour("300", "100", guid=SCRIPT_GUID_ALPHA)
            + make_monobehaviour("400", "100", guid=SCRIPT_GUID_BETA)
        )

    def test_resolver_substitutes_script_names(self) -> None:
        result = analyze_hierarchy(self._two_mono_hierarchy())
        names = {SCRIPT_GUID_ALPHA: "AlphaController", SCRIPT_GUID_BETA: "BetaState"}
        tree = format_tree(
            result,
            monobehaviour_resolver=lambda guid: names.get(guid),
        )
        # Both resolved names must appear, and the generic label must
        # be wholly replaced — collapse to a tuple pin so a partial
        # substitution surfaces all three flags.
        membership = (
            "AlphaController" in tree,
            "BetaState" in tree,
            "MonoBehaviour" in tree,
        )
        assert membership == (True, True, False), (
            "Resolver substitution must render every resolved script "
            "class name and fully replace the generic 'MonoBehaviour' "
            f"label; observed tree={tree!r}."
        )

    def test_resolver_falls_back_to_monobehaviour_for_unknown_guid(self) -> None:
        result = analyze_hierarchy(self._two_mono_hierarchy())
        # Resolver returns empty / None for unknown GUIDs.
        tree = format_tree(
            result,
            monobehaviour_resolver=lambda guid: None,
        )
        assert "MonoBehaviour" in tree, (
            "A resolver returning None must fall back to the generic "
            f"'MonoBehaviour' label; observed tree={tree!r}."
        )

    def test_resolver_empty_string_falls_back_to_monobehaviour(self) -> None:
        result = analyze_hierarchy(self._two_mono_hierarchy())
        tree = format_tree(
            result,
            monobehaviour_resolver=lambda guid: "",
        )
        assert "MonoBehaviour" in tree, (
            "A resolver returning '' must be treated as 'no name "
            "resolved' and fall back to the generic 'MonoBehaviour' "
            f"label; observed tree={tree!r}."
        )

    def test_no_resolver_keeps_generic_monobehaviour_label(self) -> None:
        result = analyze_hierarchy(self._two_mono_hierarchy())
        tree = format_tree(result)
        membership = (
            "MonoBehaviour" in tree,
            "AlphaController" in tree,
        )
        assert membership == (True, False), (
            "Without a resolver the generic 'MonoBehaviour' label "
            "must be retained and no script class names may leak; "
            f"observed tree={tree!r}."
        )


# ---------------------------------------------------------------------------
# RectTransform anchor enrichment (issue #238)
# ---------------------------------------------------------------------------


class TestHierarchyTreeFormatRectTransformAnnotation:
    """The tree text annotates anchor/size only on rect nodes (#238)."""

    def test_rect_node_carries_anchor_and_size_annotation_plain_node_does_not(
        self,
    ) -> None:
        text = (
            YAML_HEADER
            + make_gameobject("100", "Canvas", ["200"])
            + make_transform(
                "200",
                "100",
                is_rect=True,
                anchor_min=(0.0, 0.0),
                anchor_max=(0.0, 0.0),
                size_delta=(320.0, 240.0),
                pivot=(0.5, 0.5),
                children_file_ids=["400"],
            )
            + make_gameobject("300", "Empty", ["400"])
            + make_transform("400", "300", father_file_id="200")
        )
        result = analyze_hierarchy(text)
        tree = format_tree(result)
        rect_annotated = "anchor=(0.0,0.0)-(0.0,0.0) size=320.0x240.0" in tree
        plain_annotated = "Empty [anchor" in tree
        assert (rect_annotated, plain_annotated) == (True, False), (
            "Tree text must annotate rect nodes only; rect node should "
            "carry the 'anchor=...-(...) size=WxH' suffix and plain "
            "Transform nodes must not. Observed tree:\n"
            f"{tree}"
        )


class TestInspectHierarchyRectTransform:
    """Rect node payload on inspect_hierarchy (#238)."""

    def _serialise(self, text: str):
        # Minimal orchestrator-free reproduction of the serialiser to keep
        # the test independent of the orchestrator entry point: walk roots,
        # collect rect data per node, mirror the basis-resolution logic.
        from prefab_sentinel.contracts import Diagnostic
        result = analyze_hierarchy(text)
        # Build parent map keyed by id()
        parent_by_node_id: dict[int, HierarchyNode] = {}

        def _walk(node: HierarchyNode, parent: HierarchyNode | None) -> None:
            if parent is not None:
                parent_by_node_id[id(node)] = parent
            for c in node.children:
                _walk(c, node)
        for r in result.roots:
            _walk(r, None)

        diagnostics: list[Diagnostic] = []

        def _effective(node: HierarchyNode) -> tuple[tuple[float, float], str]:
            a = node.rect_anchor
            if a is None:
                return ((0.0, 0.0), "unresolved")
            sx = a.anchor_min[0] != a.anchor_max[0]
            sy = a.anchor_min[1] != a.anchor_max[1]
            if not sx and not sy:
                return (a.size_delta, "self")
            cursor = parent_by_node_id.get(id(node))
            while cursor is not None:
                pa = cursor.rect_anchor
                if pa is not None:
                    psx = pa.anchor_min[0] != pa.anchor_max[0]
                    psy = pa.anchor_min[1] != pa.anchor_max[1]
                    if not psx and not psy:
                        pw, ph = pa.size_delta
                        ew = (pw * (a.anchor_max[0] - a.anchor_min[0]) + a.size_delta[0]) if sx else a.size_delta[0]
                        eh = (ph * (a.anchor_max[1] - a.anchor_min[1]) + a.size_delta[1]) if sy else a.size_delta[1]
                        return ((ew, eh), "parent_chain")
                cursor = parent_by_node_id.get(id(cursor))
            return ((0.0, 0.0), "unresolved")

        def _ser(node: HierarchyNode) -> dict[str, object]:
            d: dict[str, object] = {
                "name": node.name,
                "children": [_ser(c) for c in node.children],
            }
            if node.rect_anchor is not None:
                a = node.rect_anchor
                eff, basis = _effective(node)
                d["rect_transform"] = {
                    "anchor_min": list(a.anchor_min),
                    "anchor_max": list(a.anchor_max),
                    "anchored_position": list(a.anchored_position),
                    "size_delta": list(a.size_delta),
                    "pivot": list(a.pivot),
                    "effective_world_size": list(eff),
                    "effective_world_size_basis": basis,
                }
                if basis == "unresolved":
                    diagnostics.append(
                        Diagnostic(
                            path="",
                            location=node.name,
                            detail="INSPECT_HIERARCHY_RECT_PARENT_UNRESOLVED",
                            evidence="",
                        )
                    )
            return d

        return [_ser(r) for r in result.roots], diagnostics

    def test_rect_node_surfaces_anchor_record_with_self_basis(self) -> None:
        text = (
            YAML_HEADER
            + make_gameobject("100", "RectRoot", ["200"])
            + make_transform(
                "200",
                "100",
                is_rect=True,
                anchor_min=(0.5, 0.5),
                anchor_max=(0.5, 0.5),
                anchored_position=(12.0, 7.0),
                size_delta=(160.0, 90.0),
                pivot=(0.5, 0.5),
            )
        )
        roots, _ = self._serialise(text)
        rect = roots[0]["rect_transform"]
        observed = (
            tuple(rect["anchor_min"]),
            tuple(rect["anchor_max"]),
            tuple(rect["anchored_position"]),
            tuple(rect["size_delta"]),
            tuple(rect["pivot"]),
            tuple(rect["effective_world_size"]),
            rect["effective_world_size_basis"],
        )
        assert observed == (
            (0.5, 0.5),
            (0.5, 0.5),
            (12.0, 7.0),
            (160.0, 90.0),
            (0.5, 0.5),
            (160.0, 90.0),
            "self",
        ), (
            "Self-anchor rect must surface its five anchor fields and "
            f"basis 'self' with effective_world_size==size_delta; observed {observed}."
        )

    def test_stretched_rect_resolves_against_parent_chain(self) -> None:
        # Parent: self-basis 1000x500.  Child: stretched on both axes with
        # +/-50 insets on x and +/-25 on y.
        text = (
            YAML_HEADER
            + make_gameobject("100", "Parent", ["200"])
            + make_transform(
                "200",
                "100",
                is_rect=True,
                anchor_min=(0.0, 0.0),
                anchor_max=(0.0, 0.0),
                size_delta=(1000.0, 500.0),
                children_file_ids=["400"],
            )
            + make_gameobject("300", "Child", ["400"])
            + make_transform(
                "400",
                "300",
                father_file_id="200",
                is_rect=True,
                anchor_min=(0.0, 0.0),
                anchor_max=(1.0, 1.0),
                anchored_position=(0.0, 0.0),
                size_delta=(-100.0, -50.0),
            )
        )
        roots, diags = self._serialise(text)
        child_rect = roots[0]["children"][0]["rect_transform"]
        observed = (
            child_rect["effective_world_size_basis"],
            tuple(child_rect["effective_world_size"]),
        )
        # eff_w = 1000 * 1.0 + (-100) = 900; eff_h = 500 * 1.0 + (-50) = 450
        assert observed == ("parent_chain", (900.0, 450.0)), (
            "Stretched-anchor child with self-basis rect parent must "
            "report 'parent_chain' basis and effective size derived from "
            f"the parent extent plus the child insets; observed {observed}."
        )
        # No diagnostic since basis resolved.
        unresolved_codes = [
            d for d in diags if d.detail == "INSPECT_HIERARCHY_RECT_PARENT_UNRESOLVED"
        ]
        assert unresolved_codes == [], (
            "Resolvable stretched anchor must not emit an unresolved "
            f"diagnostic; observed {unresolved_codes}."
        )

    def test_stretched_rect_without_parent_emits_diagnostic(self) -> None:
        # Root rect that is stretched but has no parent (no chain).
        text = (
            YAML_HEADER
            + make_gameobject("100", "StretchedRoot", ["200"])
            + make_transform(
                "200",
                "100",
                is_rect=True,
                anchor_min=(0.0, 0.0),
                anchor_max=(1.0, 1.0),
                size_delta=(0.0, 0.0),
            )
        )
        roots, diags = self._serialise(text)
        observed_basis = roots[0]["rect_transform"]["effective_world_size_basis"]
        diag_codes = [d.detail for d in diags]
        assert (observed_basis, "INSPECT_HIERARCHY_RECT_PARENT_UNRESOLVED" in diag_codes) == (
            "unresolved",
            True,
        ), (
            "Stretched anchor with no resolvable parent rect chain must "
            "carry basis 'unresolved' and emit the named diagnostic; "
            f"observed basis={observed_basis!r} diagnostics={diag_codes!r}."
        )

    def test_plain_transform_node_has_no_rect_transform_key(self) -> None:
        text = (
            YAML_HEADER
            + make_gameobject("100", "Plain", ["200"])
            + make_transform("200", "100")
        )
        roots, _ = self._serialise(text)
        assert "rect_transform" not in roots[0], (
            "A plain Transform must not expose a 'rect_transform' key "
            f"on the serialised node; observed keys={list(roots[0].keys())!r}."
        )
