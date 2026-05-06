"""GameObject hierarchy analyzer for Unity YAML assets.

Builds a parent-child tree from Transform blocks and produces a
printable hierarchy with optional component annotations.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field

from prefab_sentinel.unity_assets import normalize_guid
from prefab_sentinel.unity_yaml_parser import (
    CLASS_ID_MONOBEHAVIOUR,
    TransformInfo,
    YamlBlock,
    parse_game_objects,
    parse_transforms,
    split_yaml_blocks,
)

_SCRIPT_GUID_PATTERN = re.compile(
    r"\bm_Script:\s*\{[^}]*guid:\s*([0-9a-fA-F]{32})"
)

# Unity ClassID reference: https://docs.unity3d.com/Manual/ClassIDReference.html
# Subset relevant to prefab/scene hierarchy and wiring analysis.
CLASS_NAMES: dict[str, str] = {
    "4": "Transform",
    "20": "Camera",
    "23": "MeshRenderer",
    "25": "Renderer",
    "33": "MeshFilter",
    "54": "Rigidbody",
    "56": "CapsuleCollider",
    "58": "CircleCollider2D",
    "61": "BoxCollider2D",
    "64": "MeshCollider",
    "65": "BoxCollider",
    "108": "Light",
    "111": "Animation",
    "114": "MonoBehaviour",
    "120": "LineRenderer",
    "135": "SphereCollider",
    "137": "SkinnedMeshRenderer",
    "136": "TrailRenderer",
    "198": "ParticleSystem",
    "199": "ParticleSystemRenderer",
    "212": "SpriteRenderer",
    "222": "CanvasRenderer",
    "223": "Canvas",
    "224": "RectTransform",
    "225": "CanvasGroup",
    "226": "RawImage",
}


@dataclass(slots=True)
class ComponentDescriptor:
    """Per-component metadata used by the tree renderer.

    ``script_guid`` is non-empty only for MonoBehaviour entries that
    declare an ``m_Script`` reference; the renderer consults it when an
    opt-in resolver is supplied to substitute the script class name for
    the generic MonoBehaviour label.
    """

    label: str
    script_guid: str = ""


@dataclass(slots=True)
class HierarchyNode:
    file_id: str
    name: str
    components: list[str]
    children: list[HierarchyNode]
    depth: int
    transform: TransformInfo | None
    override_count: int = 0
    component_descriptors: list[ComponentDescriptor] = field(default_factory=list)


@dataclass(slots=True)
class HierarchyResult:
    roots: list[HierarchyNode]
    total_game_objects: int
    total_components: int
    max_depth: int


def _component_descriptor(
    comp_fid: str,
    transforms: dict[str, TransformInfo],
    blocks_by_fid: dict[str, YamlBlock],
) -> ComponentDescriptor | None:
    """Return a label-plus-script-guid descriptor for a component fileID."""
    if comp_fid in transforms:
        t = transforms[comp_fid]
        return ComponentDescriptor(
            label="RectTransform" if t.is_rect_transform else "Transform",
        )
    block = blocks_by_fid.get(comp_fid)
    if not block:
        return None
    name = CLASS_NAMES.get(block.class_id)
    label = name if name else f"Component({block.class_id})"
    script_guid = ""
    if block.class_id == CLASS_ID_MONOBEHAVIOUR:
        match = _SCRIPT_GUID_PATTERN.search(block.text)
        if match:
            script_guid = normalize_guid(match.group(1))
    return ComponentDescriptor(label=label, script_guid=script_guid)


def analyze_hierarchy(
    text: str,
    override_counts: dict[str, int] | None = None,
) -> HierarchyResult:
    """Build a hierarchy tree from Unity YAML text.

    Args:
        text: Raw Unity YAML content.
        override_counts: Optional mapping of fileID -> override count to
            annotate nodes (used for Variant hierarchy display).
    """
    blocks = split_yaml_blocks(text)
    game_objects = parse_game_objects(blocks)
    transforms = parse_transforms(blocks)
    blocks_by_fid = {b.file_id: b for b in blocks}

    # Map: game_object_file_id -> TransformInfo
    go_to_transform: dict[str, TransformInfo] = {}
    # Map: transform_file_id -> game_object_file_id
    transform_to_go: dict[str, str] = {}
    for t in transforms.values():
        if t.game_object_file_id:
            go_to_transform[t.game_object_file_id] = t
            transform_to_go[t.file_id] = t.game_object_file_id

    def _build_node(go_fid: str, depth: int) -> HierarchyNode:
        go = game_objects.get(go_fid)
        name = go.name if go and go.name else f"fileID:{go_fid}"
        comp_labels: list[str] = []
        descriptors: list[ComponentDescriptor] = []
        if go:
            for cfid in go.component_file_ids:
                desc = _component_descriptor(cfid, transforms, blocks_by_fid)
                if desc and desc.label not in ("Transform", "RectTransform"):
                    comp_labels.append(desc.label)
                    descriptors.append(desc)

        t = go_to_transform.get(go_fid)
        child_nodes: list[HierarchyNode] = []
        if t:
            for child_tfid in t.children_file_ids:
                child_go_fid = transform_to_go.get(child_tfid, "")
                if child_go_fid:
                    child_nodes.append(_build_node(child_go_fid, depth + 1))

        ov_count = (override_counts or {}).get(go_fid, 0)
        return HierarchyNode(
            file_id=go_fid,
            name=name,
            components=comp_labels,
            children=child_nodes,
            depth=depth,
            transform=t,
            override_count=ov_count,
            component_descriptors=descriptors,
        )

    # Identify root GameObjects: those whose Transform has m_Father == "0" or ""
    root_go_fids: list[str] = []
    for go_fid in game_objects:
        t_or_none = go_to_transform.get(go_fid)
        if t_or_none and t_or_none.father_file_id in ("0", ""):
            root_go_fids.append(go_fid)

    roots = [_build_node(fid, 0) for fid in root_go_fids]

    def _max_depth(node: HierarchyNode) -> int:
        if not node.children:
            return node.depth
        return max(_max_depth(c) for c in node.children)

    max_depth = max((_max_depth(r) for r in roots), default=0)
    total_components = sum(
        len(go.component_file_ids) for go in game_objects.values()
    )

    return HierarchyResult(
        roots=roots,
        total_game_objects=len(game_objects),
        total_components=total_components,
        max_depth=max_depth,
    )


def format_tree(
    result: HierarchyResult,
    *,
    max_depth: int | None = None,
    show_components: bool = True,
    monobehaviour_resolver: Callable[[str], str | None] | None = None,
) -> str:
    """Render hierarchy as an indented tree string.

    When ``monobehaviour_resolver`` is supplied and a component descriptor
    carries a non-empty ``script_guid``, the resolver is called with that
    GUID; a non-empty return value substitutes the rendered component
    label.  Absent a resolver (or when the resolver returns falsy), the
    descriptor's stored label is used unchanged.
    """
    def _resolved_labels(node: HierarchyNode) -> list[str]:
        if not node.component_descriptors:
            return list(node.components)
        out: list[str] = []
        for desc in node.component_descriptors:
            if monobehaviour_resolver is not None and desc.script_guid:
                resolved = monobehaviour_resolver(desc.script_guid)
                if resolved:
                    out.append(resolved)
                    continue
            out.append(desc.label)
        return out

    lines: list[str] = []

    def _render(node: HierarchyNode, prefix: str, is_last: bool) -> None:
        if max_depth is not None and node.depth > max_depth:
            return
        connector = "\u2514\u2500\u2500 " if is_last else "\u251c\u2500\u2500 "
        label = node.name
        labels = _resolved_labels(node) if show_components else []
        if show_components and labels:
            label += f" ({', '.join(labels)})"
        if node.override_count > 0:
            label += f" [overridden: {node.override_count}]"
        if node.depth == 0:
            lines.append(label)
        else:
            lines.append(f"{prefix}{connector}{label}")

        child_prefix = prefix + ("    " if is_last else "\u2502   ")
        visible_children = node.children
        if max_depth is not None:
            visible_children = [c for c in node.children if c.depth <= max_depth]
        for i, child in enumerate(visible_children):
            _render(child, child_prefix, i == len(visible_children) - 1)

    for i, root in enumerate(result.roots):
        if i > 0:
            lines.append("")
        _render(root, "", i == len(result.roots) - 1)

    return "\n".join(lines)
