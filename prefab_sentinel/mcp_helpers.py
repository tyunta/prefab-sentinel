"""Shared helper functions for MCP tool modules."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from prefab_sentinel.fuzzy_match import suggest_similar
from prefab_sentinel.symbol_tree import (
    AmbiguousSymbolError,
    SymbolKind,
    SymbolNode,
    SymbolNotFoundError,
    SymbolTree,
)
from prefab_sentinel.unity_assets import decode_text_file
from prefab_sentinel.unity_assets_path import resolve_asset_path
from prefab_sentinel.unity_yaml_parser import (
    CLASS_ID_MONOBEHAVIOUR,
    split_yaml_blocks,
)

__all__ = [
    "COPY_SKIP_FIELDS",
    "KNOWLEDGE_URI_PREFIX",
    "build_component_selector",
    "collect_symbol_paths",
    "find_block_by_file_id",
    "normalize_material_value",
    "read_asset",
    "resolve_component_name",
    "resolve_component_with_type",
    "resolve_game_object_node",
]

KNOWLEDGE_URI_PREFIX = "resource://prefab-sentinel/knowledge/"

COPY_SKIP_FIELDS = frozenset({
    "m_ObjectHideFlags",
    "m_CorrespondingSourceObject",
    "m_PrefabInstance",
    "m_PrefabAsset",
    "m_GameObject",
    "m_EditorHideFlags",
    "m_Script",
    "m_EditorClassIdentifier",
})


def normalize_material_value(value: str | list | int | float) -> str:
    """Normalize a material property value to string for Bridge transmission."""
    if isinstance(value, str):
        return value
    return json.dumps(value)


def read_asset(path: str, project_root: Path | None) -> tuple[str, Path]:
    """Read a Unity asset file, returning (text, resolved_path)."""
    resolved = resolve_asset_path(path, project_root)
    if not resolved.is_file():
        msg = f"File not found: {path}"
        raise FileNotFoundError(msg)
    text = decode_text_file(resolved)
    if text is None:
        msg = f"Unable to decode file: {path}"
        raise ValueError(msg)
    return text, resolved


def resolve_component_name(node: SymbolNode) -> str:
    """Map a component SymbolNode to the type name used by patch ops."""
    if node.class_id == CLASS_ID_MONOBEHAVIOUR:
        if not node.script_name:
            msg = (
                f"MonoBehaviour at fileID={node.file_id} has no script name. "
                f"Provide --project-root for script name resolution."
            )
            raise ValueError(msg)
        return node.script_name
    return node.name


def collect_symbol_paths(tree: SymbolTree) -> list[str]:
    """Collect all symbol paths from a tree for suggestion purposes."""
    paths: list[str] = []

    def _walk(nodes: list[SymbolNode], prefix: str) -> None:
        for node in nodes:
            p = f"{prefix}/{node.name}" if prefix else node.name
            paths.append(p)
            _walk(node.children, p)

    _walk(tree.roots, "")
    return paths


def resolve_component_with_type(
    tree: SymbolTree,
    symbol_path: str,
    asset_path: str,
) -> tuple[SymbolNode, str, None] | tuple[None, None, dict[str, Any]]:
    """Resolve *symbol_path* to a component node and its type name."""
    try:
        node = tree.resolve_unique(symbol_path)
    except SymbolNotFoundError:
        suggestions = suggest_similar(
            symbol_path, collect_symbol_paths(tree),
        )
        return None, None, {
            "success": False,
            "severity": "error",
            "code": "SYMBOL_NOT_FOUND",
            "message": f"No component found at symbol path: {symbol_path!r}",
            "data": {
                "asset_path": asset_path,
                "symbol_path": symbol_path,
                "suggestions": suggestions,
            },
            "diagnostics": [],
        }
    except AmbiguousSymbolError as exc:
        return None, None, {
            "success": False,
            "severity": "error",
            "code": "SYMBOL_AMBIGUOUS",
            "message": str(exc),
            "data": {"asset_path": asset_path, "symbol_path": symbol_path},
            "diagnostics": [],
        }

    if node.kind != SymbolKind.COMPONENT:
        return None, None, {
            "success": False,
            "severity": "error",
            "code": "SYMBOL_NOT_COMPONENT",
            "message": (
                f"Symbol path {symbol_path!r} resolves to a {node.kind.value}, "
                f"not a component. Provide a path to a component."
            ),
            "data": {
                "asset_path": asset_path,
                "symbol_path": symbol_path,
                "resolved_kind": node.kind.value,
            },
            "diagnostics": [],
        }

    try:
        component_name = resolve_component_name(node)
    except ValueError as exc:
        return None, None, {
            "success": False,
            "severity": "error",
            "code": "SYMBOL_UNRESOLVABLE",
            "message": str(exc),
            "data": {"asset_path": asset_path, "symbol_path": symbol_path},
            "diagnostics": [],
        }

    return node, component_name, None


def resolve_game_object_node(
    tree: SymbolTree,
    symbol_path: str,
    asset_path: str,
) -> tuple[SymbolNode, None] | tuple[None, dict[str, Any]]:
    """Resolve *symbol_path* to a unique GameObject node."""
    try:
        node = tree.resolve_unique(symbol_path)
    except SymbolNotFoundError:
        suggestions = suggest_similar(
            symbol_path, collect_symbol_paths(tree),
        )
        return None, {
            "success": False,
            "severity": "error",
            "code": "SYMBOL_NOT_FOUND",
            "message": f"No game object found at symbol path: {symbol_path!r}",
            "data": {
                "asset_path": asset_path,
                "symbol_path": symbol_path,
                "suggestions": suggestions,
            },
            "diagnostics": [],
        }
    except AmbiguousSymbolError as exc:
        return None, {
            "success": False,
            "severity": "error",
            "code": "SYMBOL_AMBIGUOUS",
            "message": str(exc),
            "data": {"asset_path": asset_path, "symbol_path": symbol_path},
            "diagnostics": [],
        }

    if node.kind != SymbolKind.GAME_OBJECT:
        return None, {
            "success": False,
            "severity": "error",
            "code": "SYMBOL_NOT_GAME_OBJECT",
            "message": (
                f"Symbol path {symbol_path!r} resolves to a {node.kind.value}, "
                f"not a game_object. Provide a path to a GameObject."
            ),
            "data": {
                "asset_path": asset_path,
                "symbol_path": symbol_path,
                "resolved_kind": node.kind.value,
            },
            "diagnostics": [],
        }

    return node, None


def find_block_by_file_id(text: str, file_id: str) -> str:
    """Find the YAML block text for a given file ID."""
    for block in split_yaml_blocks(text):
        if block.file_id == file_id:
            return block.text
    msg = f"No YAML block found for fileID={file_id}"
    raise ValueError(msg)


def _game_object_ancestor_chain(
    tree: SymbolTree, component_node: SymbolNode,
) -> list[SymbolNode] | None:
    """Return the GameObject ancestor chain that owns *component_node*.

    The chain is root-first and ends with the GameObject that directly
    owns the component (issue #37 selector emission). Returns ``None``
    when the component node is not reachable from any tree root.

    The symbol tree stores only forward (child) links, so this walks
    from ``tree.roots`` recording the GameObject path that reaches the
    component's ``file_id``.
    """

    def _walk(
        node: SymbolNode, trail: list[SymbolNode],
    ) -> list[SymbolNode] | None:
        if node.kind == SymbolKind.GAME_OBJECT:
            here = [*trail, node]
            for child in node.children:
                if (
                    child.kind == SymbolKind.COMPONENT
                    and child.file_id == component_node.file_id
                ):
                    return here
            for child in node.children:
                found = _walk(child, here)
                if found is not None:
                    return found
            return None
        # PrefabInstance / other container: descend without recording.
        for child in node.children:
            found = _walk(child, trail)
            if found is not None:
                return found
        return None

    for root in tree.roots:
        chain = _walk(root, [])
        if chain is not None:
            return chain
    return None


def build_component_selector(
    tree: SymbolTree, component_node: SymbolNode, component_name: str,
) -> tuple[str, None] | tuple[None, dict[str, Any]]:
    """Build a ``TypeName@/hierarchy/path`` patch component selector.

    Derives *component_node*'s GameObject ancestor chain and emits a
    hierarchy-qualified selector so an asset containing several
    same-type components still resolves to the intended one (issue #37).
    Ancestors that have a same-named GameObject sibling are emitted with
    a ``#N`` (0-based, child order) occurrence token.

    Returns ``(selector, None)`` on success, or ``(None, envelope)``
    with a ``SELECTOR_NOT_EXPRESSIBLE`` error envelope when the ancestor
    chain cannot be expressed unambiguously — currently when an ancestor
    GameObject name literally contains ``#`` (the disambiguation
    metacharacter), which would make the emitted selector unparseable.
    """
    chain = _game_object_ancestor_chain(tree, component_node)
    if chain is None:
        # The component resolved through the tree but its owning
        # GameObject path could not be reconstructed. Fail fast rather
        # than emit a bare type name that drops the hierarchy qualifier.
        return None, {
            "success": False,
            "severity": "error",
            "code": "SELECTOR_NOT_EXPRESSIBLE",
            "message": (
                f"Cannot derive a GameObject ancestor chain for "
                f"component {component_name!r}; the resolved component "
                f"is not reachable from any asset root."
            ),
            "data": {
                "component": component_name,
                "file_id": component_node.file_id,
            },
            "diagnostics": [],
        }

    # Build sibling-aware segments root-first. For each GameObject in the
    # chain, count its same-named GameObject siblings to decide whether a
    # ``#N`` occurrence token is required.
    segments: list[str] = []
    parent_children: list[SymbolNode] = list(tree.roots)
    for go_node in chain:
        if "#" in go_node.name:
            return None, {
                "success": False,
                "severity": "error",
                "code": "SELECTOR_NOT_EXPRESSIBLE",
                "message": (
                    f"GameObject name {go_node.name!r} contains '#', the "
                    f"sibling-disambiguation metacharacter; the ancestor "
                    f"chain cannot be expressed as an unambiguous "
                    f"'TypeName@/path' selector."
                ),
                "data": {
                    "component": component_name,
                    "offending_name": go_node.name,
                },
                "diagnostics": [],
            }
        go_siblings = [
            n for n in parent_children if n.kind == SymbolKind.GAME_OBJECT
        ]
        same_named = [n for n in go_siblings if n.name == go_node.name]
        if len(same_named) > 1:
            occurrence = next(
                i for i, n in enumerate(same_named) if n is go_node
            )
            segments.append(f"{go_node.name}#{occurrence}")
        else:
            segments.append(go_node.name)
        parent_children = go_node.children

    selector = f"{component_name}@/" + "/".join(segments)
    return selector, None
