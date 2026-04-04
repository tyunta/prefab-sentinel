"""Build a SymbolTree from Unity YAML text.

Extracts the construction logic from SymbolTree into a standalone
function to keep symbol_tree.py focused on the data model and queries.
"""

from __future__ import annotations

import re
from pathlib import Path

from prefab_sentinel.hierarchy import CLASS_NAMES
from prefab_sentinel.symbol_tree import SymbolKind, SymbolNode, SymbolTree
from prefab_sentinel.udon_wiring import analyze_wiring
from prefab_sentinel.unity_assets import (
    SOURCE_PREFAB_PATTERN,
    decode_text_file,
    normalize_guid,
)
from prefab_sentinel.unity_yaml_parser import (
    CLASS_ID_MONOBEHAVIOUR,
    CLASS_ID_PREFAB_INSTANCE,
    MAX_NESTED_DEPTH,
    TRANSFORM_CLASS_IDS,
    ComponentInfo,
    parse_components,
    parse_game_objects,
    parse_transforms,
    split_yaml_blocks,
)

__all__ = ["build_symbol_tree"]

_TRANSFORM_PARENT_RE = re.compile(r"m_TransformParent:\s*\{fileID:\s*(\d+)")


def build_symbol_tree(
    text: str,
    source_path: str,
    script_map: dict[str, str] | None = None,
    *,
    include_properties: bool = False,
    expand_nested: bool = False,
    guid_to_asset_path: dict[str, Path] | None = None,
    _nested_depth: int = 0,
) -> SymbolTree:
    """Build a symbol tree from Unity YAML text.

    Args:
        text: Raw Unity YAML content.
        source_path: Asset file path (for display/identification).
        script_map: Optional map of script GUID -> class name
            for resolving MonoBehaviour script names.
        include_properties: When True, populate property-level nodes
            for MonoBehaviour serialized fields.
        expand_nested: When True, expand PrefabInstance nodes into
            their child Prefab's tree (recursive).
        guid_to_asset_path: GUID -> Path map for resolving nested Prefabs.
            Required when expand_nested=True.
        _nested_depth: Internal recursion depth counter (do not set externally).
    """
    script_map = script_map or {}

    blocks = split_yaml_blocks(text)
    if not blocks:
        return SymbolTree(asset_path=source_path)

    game_objects = parse_game_objects(blocks)
    transforms = parse_transforms(blocks)
    components = parse_components(blocks)

    wiring_by_fid: dict[str, list[tuple[str, str]]] = {}
    if include_properties:
        wiring = analyze_wiring(text, source_path or "<unknown>")
        for comp in wiring.components:
            fields: list[tuple[str, str]] = []
            for f in comp.fields:
                fields.append((f.name, f.value))
            if fields:
                wiring_by_fid[comp.file_id] = fields

    go_to_transform: dict[str, str] = {}
    transform_to_go: dict[str, str] = {}
    for t in transforms.values():
        if t.game_object_file_id:
            go_to_transform[t.game_object_file_id] = t.file_id
            transform_to_go[t.file_id] = t.game_object_file_id

    file_id_index: dict[str, SymbolNode] = {}

    def _component_name(comp: ComponentInfo) -> str:
        if comp.class_id in TRANSFORM_CLASS_IDS:
            t = transforms.get(comp.file_id)
            if t and t.is_rect_transform:
                return "RectTransform"
            return "Transform"
        if comp.class_id == CLASS_ID_MONOBEHAVIOUR:
            sname = script_map.get(comp.script_guid, "")
            if sname:
                return f"MonoBehaviour({sname})"
            if comp.script_guid:
                return f"MonoBehaviour(guid:{comp.script_guid[:8]})"
            return "MonoBehaviour"
        return CLASS_NAMES.get(comp.class_id, f"Component({comp.class_id})")

    def _build_component_node(
        comp: ComponentInfo, depth: int
    ) -> SymbolNode:
        name = _component_name(comp)
        sname = ""
        if comp.class_id == CLASS_ID_MONOBEHAVIOUR:
            sname = script_map.get(comp.script_guid, "")

        props: dict[str, str] = {}
        prop_children: list[SymbolNode] = []
        if include_properties and comp.file_id in wiring_by_fid:
            for fname, fval in wiring_by_fid[comp.file_id]:
                props[fname] = fval
                prop_children.append(SymbolNode(
                    kind=SymbolKind.PROPERTY,
                    name=fname,
                    file_id="",
                    class_id="",
                    depth=depth + 1,
                    properties={fname: fval},
                ))

        node = SymbolNode(
            kind=SymbolKind.COMPONENT,
            name=name,
            file_id=comp.file_id,
            class_id=comp.class_id,
            children=prop_children,
            script_guid=comp.script_guid,
            script_name=sname,
            depth=depth,
            properties=props,
        )
        file_id_index[comp.file_id] = node
        return node

    def _build_go_node(go_fid: str, depth: int) -> SymbolNode:
        go = game_objects.get(go_fid)
        name = go.name if go and go.name else f"<unnamed:{go_fid}>"

        comp_nodes: list[SymbolNode] = []
        if go:
            for cfid in go.component_file_ids:
                comp = components.get(cfid)
                if comp:
                    comp_nodes.append(
                        _build_component_node(comp, depth + 1)
                    )
                elif cfid in transforms:
                    t = transforms[cfid]
                    t_name = "RectTransform" if t.is_rect_transform else "Transform"
                    t_node = SymbolNode(
                        kind=SymbolKind.COMPONENT,
                        name=t_name,
                        file_id=cfid,
                        class_id="224" if t.is_rect_transform else "4",
                        depth=depth + 1,
                    )
                    file_id_index[cfid] = t_node
                    comp_nodes.append(t_node)

        child_go_nodes: list[SymbolNode] = []
        t_fid = go_to_transform.get(go_fid, "")
        if t_fid and t_fid in transforms:
            for child_t_fid in transforms[t_fid].children_file_ids:
                child_go_fid = transform_to_go.get(child_t_fid, "")
                if child_go_fid:
                    child_go_nodes.append(
                        _build_go_node(child_go_fid, depth + 1)
                    )

        node = SymbolNode(
            kind=SymbolKind.GAME_OBJECT,
            name=name,
            file_id=go_fid,
            class_id="1",
            children=comp_nodes + child_go_nodes,
            depth=depth,
        )
        file_id_index[go_fid] = node
        return node

    root_go_fids: list[str] = []
    for go_fid in game_objects:
        t_fid = go_to_transform.get(go_fid, "")
        if t_fid and t_fid in transforms and transforms[t_fid].father_file_id in ("0", ""):
            root_go_fids.append(go_fid)

    roots = [_build_go_node(fid, 0) for fid in root_go_fids]

    if expand_nested and guid_to_asset_path is not None and _nested_depth < MAX_NESTED_DEPTH:
        for block in blocks:
            if block.class_id != CLASS_ID_PREFAB_INSTANCE:
                continue
            source_match = SOURCE_PREFAB_PATTERN.search(block.text)
            if source_match is None:
                continue
            guid = normalize_guid(source_match.group(2))
            child_path = guid_to_asset_path.get(guid)

            resolved = False
            child_text = ""
            if child_path is not None and child_path.exists():
                try:
                    child_text = decode_text_file(child_path)
                    resolved = True
                except (OSError, UnicodeDecodeError):
                    resolved = False

            if resolved:
                rel_path = child_path.as_posix()  # type: ignore[union-attr]
                child_tree = build_symbol_tree(
                    child_text,
                    rel_path,
                    script_map,
                    include_properties=include_properties,
                    expand_nested=True,
                    guid_to_asset_path=guid_to_asset_path,
                    _nested_depth=_nested_depth + 1,
                )
                marker = SymbolNode(
                    kind=SymbolKind.PREFAB_INSTANCE,
                    name=f"[PrefabInstance: {rel_path}]",
                    file_id=block.file_id,
                    class_id=CLASS_ID_PREFAB_INSTANCE,
                    children=child_tree.roots,
                    source_prefab=rel_path,
                )
            else:
                marker = SymbolNode(
                    kind=SymbolKind.PREFAB_INSTANCE,
                    name=f"[Unresolved: {guid}]",
                    file_id=block.file_id,
                    class_id=CLASS_ID_PREFAB_INSTANCE,
                    children=[],
                    source_prefab=guid,
                )
            file_id_index[block.file_id] = marker

            tp_match = _TRANSFORM_PARENT_RE.search(block.text)
            parent_t_fid = tp_match.group(1) if tp_match else "0"
            parent_go_fid = transform_to_go.get(parent_t_fid, "")
            parent_node = file_id_index.get(parent_go_fid)
            if parent_node is not None:
                parent_node.children.append(marker)
            else:
                roots.append(marker)

    return SymbolTree(
        asset_path=source_path,
        roots=roots,
        _file_id_index=file_id_index,
    )
