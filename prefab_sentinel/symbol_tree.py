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

from prefab_sentinel.unity_yaml_parser import CLASS_ID_MONOBEHAVIOUR

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
