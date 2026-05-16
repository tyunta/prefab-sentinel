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
    "collect_symbol_paths",
    "find_block_by_file_id",
    "find_component_on_go",
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


def find_component_on_go(
    go_node: SymbolNode,
    component: str,
    asset_path: str,
) -> tuple[SymbolNode, str, None] | tuple[None, None, dict[str, Any]]:
    """Find a uniquely-named component on a GameObject node."""
    component_children = [
        child for child in go_node.children
        if child.kind == SymbolKind.COMPONENT
    ]
    available = [
        child.script_name if child.script_name else child.name
        for child in component_children
    ]

    def _matches(child: SymbolNode) -> bool:
        if child.script_name:
            return child.script_name == component
        return child.name == component

    matches = [child for child in component_children if _matches(child)]

    if not matches:
        return None, None, {
            "success": False,
            "severity": "error",
            "code": "COMPONENT_NOT_FOUND",
            "message": (
                f"No component {component!r} found on "
                f"GameObject {go_node.name!r}."
            ),
            "data": {
                "asset_path": asset_path,
                "component": component,
                "available_components": available,
            },
            "diagnostics": [],
        }

    if len(matches) > 1:
        return None, None, {
            "success": False,
            "severity": "error",
            "code": "COMPONENT_AMBIGUOUS",
            "message": (
                f"Multiple {component!r} components found on "
                f"GameObject {go_node.name!r}. Cannot resolve uniquely."
            ),
            "data": {
                "asset_path": asset_path,
                "component": component,
                "available_components": [
                    child.script_name if child.script_name else child.name
                    for child in matches
                ],
            },
            "diagnostics": [],
        }

    node = matches[0]
    try:
        component_name = resolve_component_name(node)
    except ValueError as exc:
        return None, None, {
            "success": False,
            "severity": "error",
            "code": "SYMBOL_UNRESOLVABLE",
            "message": str(exc),
            "data": {"asset_path": asset_path, "component": component},
            "diagnostics": [],
        }
    return node, component_name, None
