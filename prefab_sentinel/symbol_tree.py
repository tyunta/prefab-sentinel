"""Symbol tree model for Unity YAML assets.

Builds a human-readable symbol hierarchy from Unity YAML text,
enabling name-based addressing of GameObjects, Components, and
serialized properties.  For example::

    CharacterBody/MeshRenderer/m_Materials[0]

instead of raw ``fileID`` numbers.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

from prefab_sentinel.hierarchy import CLASS_NAMES
from prefab_sentinel.udon_wiring import analyze_wiring
from prefab_sentinel.unity_assets import SOURCE_PREFAB_PATTERN, decode_text_file, normalize_guid
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

__all__ = [
    "SymbolKind",
    "SymbolNode",
    "SymbolTree",
    "build_script_name_map",
]

# ---------------------------------------------------------------------------
# Symbol path parsing
# ---------------------------------------------------------------------------

# Matches "MonoBehaviour(ScriptName)" or "MonoBehaviour(guid:abcdef...)"
_MB_SEGMENT_RE = re.compile(r"^MonoBehaviour\((.+)\)$")
# Matches "name#N" for duplicate-sibling disambiguation
_DUP_SEGMENT_RE = re.compile(r"^(.+)#(\d+)$")

_TRANSFORM_PARENT_RE = re.compile(r"m_TransformParent:\s*\{fileID:\s*(\d+)")


class SymbolKind(StrEnum):
    GAME_OBJECT = "game_object"
    COMPONENT = "component"
    PROPERTY = "property"
    PREFAB_INSTANCE = "prefab_instance"


@dataclass(slots=True)
class SymbolNode:
    """A node in the symbol tree representing a Unity object."""

    kind: SymbolKind
    name: str
    file_id: str
    class_id: str
    children: list[SymbolNode] = field(default_factory=list)
    script_guid: str = ""
    script_name: str = ""
    depth: int = 0
    properties: dict[str, str] = field(default_factory=dict)
    source_prefab: str = ""

    def to_dict(self, depth_limit: int | None = None, *, detail: str = "full") -> dict[str, Any]:
        """Serialize to a JSON-compatible dict with optional depth truncation.

        Args:
            depth_limit: Max child levels to include. None = unlimited.
            detail: Information richness per node.
                ``"summary"`` = kind + name only.
                ``"fields"``  = kind + name + sorted field_names.
                ``"full"``    = all fields (backward-compatible default).
        """
        result: dict[str, Any] = {
            "kind": self.kind.value,
            "name": self.name,
        }
        if detail == "fields" and self.properties:
            result["field_names"] = sorted(self.properties.keys())
        if detail == "full":
            result["file_id"] = self.file_id
            if self.class_id:
                result["class_id"] = self.class_id
            if self.script_guid:
                result["script_guid"] = self.script_guid
            if self.script_name:
                result["script_name"] = self.script_name
            if self.properties:
                result["properties"] = dict(self.properties)
            if self.source_prefab:
                result["source_prefab"] = self.source_prefab
        if depth_limit is None or depth_limit > 0:
            child_limit = None if depth_limit is None else depth_limit - 1
            children_to_serialize = self.children
            if detail != "full":
                children_to_serialize = [c for c in self.children if c.kind != SymbolKind.PROPERTY]
            if children_to_serialize:
                result["children"] = [c.to_dict(child_limit, detail=detail) for c in children_to_serialize]
        return result


class AmbiguousSymbolError(Exception):
    """Raised when a symbol path matches multiple nodes."""


class SymbolNotFoundError(Exception):
    """Raised when a symbol path matches no nodes."""


@dataclass
class SymbolTree:
    """In-memory symbol model for a single Unity asset."""

    asset_path: str
    roots: list[SymbolNode] = field(default_factory=list)
    _file_id_index: dict[str, SymbolNode] = field(default_factory=dict)

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------

    @classmethod
    def build(
        cls,
        text: str,
        asset_path: str = "",
        guid_to_script_name: dict[str, str] | None = None,
        *,
        include_properties: bool = False,
        expand_nested: bool = False,
        guid_to_asset_path: dict[str, Path] | None = None,
        _depth: int = 0,
    ) -> SymbolTree:
        """Build a symbol tree from Unity YAML text.

        Args:
            text: Raw Unity YAML content.
            asset_path: Asset file path (for display/identification).
            guid_to_script_name: Optional map of script GUID -> class name
                for resolving MonoBehaviour script names.
            include_properties: When True, populate property-level nodes
                for MonoBehaviour serialized fields.
            expand_nested: When True, expand PrefabInstance nodes into
                their child Prefab's tree (recursive).
            guid_to_asset_path: GUID -> Path map for resolving nested Prefabs.
                Required when expand_nested=True.
            _depth: Internal recursion depth counter (do not set externally).
        """
        script_map = guid_to_script_name or {}

        blocks = split_yaml_blocks(text)
        if not blocks:
            return cls(asset_path=asset_path)

        game_objects = parse_game_objects(blocks)
        transforms = parse_transforms(blocks)
        components = parse_components(blocks)

        # Build wiring data for property extraction
        wiring_by_fid: dict[str, list[tuple[str, str]]] = {}
        if include_properties:
            wiring = analyze_wiring(text, asset_path or "<unknown>")
            for comp in wiring.components:
                fields: list[tuple[str, str]] = []
                for f in comp.fields:
                    fields.append((f.name, f.value))
                if fields:
                    wiring_by_fid[comp.file_id] = fields

        # Maps for hierarchy traversal
        go_to_transform: dict[str, str] = {}  # go_fid -> transform_fid
        transform_to_go: dict[str, str] = {}  # transform_fid -> go_fid
        for t in transforms.values():
            if t.game_object_file_id:
                go_to_transform[t.game_object_file_id] = t.file_id
                transform_to_go[t.file_id] = t.game_object_file_id

        file_id_index: dict[str, SymbolNode] = {}

        def _component_name(comp: ComponentInfo) -> str:
            """Derive human-readable component name."""
            # Transform/RectTransform
            if comp.class_id in TRANSFORM_CLASS_IDS:
                t = transforms.get(comp.file_id)
                if t and t.is_rect_transform:
                    return "RectTransform"
                return "Transform"
            # MonoBehaviour — use script name if available
            if comp.class_id == CLASS_ID_MONOBEHAVIOUR:
                sname = script_map.get(comp.script_guid, "")
                if sname:
                    return f"MonoBehaviour({sname})"
                if comp.script_guid:
                    return f"MonoBehaviour(guid:{comp.script_guid[:8]})"
                return "MonoBehaviour"
            # Built-in component from ClassID
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

            # Build component children
            comp_nodes: list[SymbolNode] = []
            if go:
                for cfid in go.component_file_ids:
                    comp = components.get(cfid)
                    if comp:
                        comp_nodes.append(
                            _build_component_node(comp, depth + 1)
                        )
                    elif cfid in transforms:
                        # Transform is also in the component list
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

            # Build child GO nodes via Transform hierarchy
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

        # Find root GameObjects
        root_go_fids: list[str] = []
        for go_fid in game_objects:
            t_fid = go_to_transform.get(go_fid, "")
            if t_fid and t_fid in transforms and transforms[t_fid].father_file_id in ("0", ""):
                root_go_fids.append(go_fid)

        roots = [_build_go_node(fid, 0) for fid in root_go_fids]

        # --- Nested Prefab expansion ---
        if expand_nested and guid_to_asset_path is not None and _depth < MAX_NESTED_DEPTH:
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
                    child_tree = cls.build(
                        child_text,
                        rel_path,
                        guid_to_script_name,
                        include_properties=include_properties,
                        expand_nested=True,
                        guid_to_asset_path=guid_to_asset_path,
                        _depth=_depth + 1,
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

                # Attach to parent GO via m_TransformParent
                tp_match = _TRANSFORM_PARENT_RE.search(block.text)
                parent_t_fid = tp_match.group(1) if tp_match else "0"
                parent_go_fid = transform_to_go.get(parent_t_fid, "")
                parent_node = file_id_index.get(parent_go_fid)
                if parent_node is not None:
                    parent_node.children.append(marker)
                else:
                    roots.append(marker)

        return cls(
            asset_path=asset_path,
            roots=roots,
            _file_id_index=file_id_index,
        )

    # ------------------------------------------------------------------
    # Resolution
    # ------------------------------------------------------------------

    def resolve(self, symbol_path: str) -> list[SymbolNode]:
        """Resolve a symbol path to matching nodes.

        Returns all matching nodes (may be 0, 1, or more).
        """
        if not symbol_path:
            return []
        segments = symbol_path.split("/")
        return self._resolve_segments(self.roots, segments, 0)

    def resolve_unique(self, symbol_path: str) -> SymbolNode:
        """Resolve a symbol path, raising if not exactly one match."""
        matches = self.resolve(symbol_path)
        if len(matches) == 0:
            raise SymbolNotFoundError(f"No match for symbol path: {symbol_path!r}")
        if len(matches) > 1:
            raise AmbiguousSymbolError(
                f"Ambiguous symbol path {symbol_path!r}: "
                f"{len(matches)} matches"
            )
        return matches[0]

    def resolve_file_id(self, file_id: str) -> SymbolNode | None:
        """Look up a node by its fileID."""
        return self._file_id_index.get(file_id)

    def _resolve_segments(
        self,
        candidates: list[SymbolNode],
        segments: list[str],
        seg_idx: int,
    ) -> list[SymbolNode]:
        """Recursively match segments against candidate nodes.

        ``candidates`` are the nodes to match the current segment against
        (directly, not their children).  On the first call this is ``roots``.
        """
        if seg_idx >= len(segments):
            return candidates

        # Flatten PrefabInstance nodes: replace with their children
        flat_candidates: list[SymbolNode] = []
        for node in candidates:
            if node.kind == SymbolKind.PREFAB_INSTANCE:
                flat_candidates.extend(node.children)
            else:
                flat_candidates.append(node)

        segment = segments[seg_idx]
        is_last = seg_idx == len(segments) - 1

        # Handle "#N" disambiguation at this level
        dup_match = _DUP_SEGMENT_RE.match(segment)
        if dup_match:
            base_name = dup_match.group(1)
            target_idx = int(dup_match.group(2))
            count = 0
            for node in flat_candidates:
                if node.name == base_name:
                    if count == target_idx:
                        if is_last:
                            return [node]
                        return self._resolve_segments(
                            node.children, segments, seg_idx + 1
                        )
                    count += 1
            return []

        # Match current segment against flat candidates
        matched = [n for n in flat_candidates if self._segment_matches(n, segment)]

        if is_last:
            return matched

        # Recurse into children of matched nodes for next segment
        results: list[SymbolNode] = []
        for node in matched:
            results.extend(
                self._resolve_segments(node.children, segments, seg_idx + 1)
            )
        return results

    @staticmethod
    def _segment_matches(node: SymbolNode, segment: str) -> bool:
        """Check if a single node matches a path segment."""
        # MonoBehaviour(ScriptName) match
        mb_match = _MB_SEGMENT_RE.match(segment)
        if mb_match:
            inner = mb_match.group(1)
            if node.kind != SymbolKind.COMPONENT:
                return False
            if node.class_id != CLASS_ID_MONOBEHAVIOUR:
                return False
            if inner.startswith("guid:"):
                return node.script_guid.startswith(inner[5:])
            return node.script_name == inner

        # Lenient MonoBehaviour match: bare "MonoBehaviour"
        if segment == "MonoBehaviour" and node.kind == SymbolKind.COMPONENT:
            return node.class_id == CLASS_ID_MONOBEHAVIOUR

        # Exact name match
        return node.name == segment

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def query(
        self,
        symbol_path: str,
        depth: int = 0,
    ) -> list[dict[str, Any]]:
        """Query the tree at a symbol path, returning serialized results.

        Args:
            symbol_path: Path to query (empty string for roots).
            depth: How many levels below the match to include.
        """
        nodes = self.roots if not symbol_path else self.resolve(symbol_path)
        return [n.to_dict(depth_limit=depth) for n in nodes]

    def to_overview(self, depth: int | None = None, *, detail: str = "full") -> list[dict[str, Any]]:
        """Serialize the tree to a JSON-compatible overview.

        Args:
            depth: Max child levels. None = unlimited (full tree).
            detail: Information richness per node (summary/fields/full).
        """
        return [root.to_dict(depth_limit=depth, detail=detail) for root in self.roots]


# ---------------------------------------------------------------------------
# Script name resolution
# ---------------------------------------------------------------------------


def build_script_name_map(guid_index: dict[str, Path]) -> dict[str, str]:
    """Build a guid -> script_class_name map from a pre-computed GUID index.

    Filters the index for ``.cs`` entries and maps GUIDs to class names (file stems).

    Args:
        guid_index: Pre-computed GUID-to-Path mapping (e.g. from
            ``collect_project_guid_index``).

    Returns:
        Dict mapping lowercase GUID strings to script class names (file stems).
    """
    return {
        guid: asset_path.stem
        for guid, asset_path in guid_index.items()
        if asset_path.suffix == ".cs"
    }
