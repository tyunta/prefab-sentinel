from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from prefab_sentinel.effective_hierarchy import EffectiveHierarchyNode
from prefab_sentinel.services.prefab_variant.overrides import OverrideEntry
from prefab_sentinel.udon_wiring_parser import UDON_BEHAVIOUR_GUID
from prefab_sentinel.unity_assets import decode_text_file
from prefab_sentinel.unity_assets_path import resolve_scope_path
from prefab_sentinel.unity_yaml_parser import YamlBlock, split_yaml_blocks

from .models import _ARRAY_SIZE_SUFFIX


def _selector_data(
    asset_path: str,
    symbol_path: str,
    component_type: str,
    property_name: str,
) -> dict[str, Any]:
    return {
        "asset_path": asset_path,
        "symbol_path": symbol_path,
        "component_type": component_type,
        "property_name": property_name,
        "read_only": True,
    }

def _find_component_block(
    node: EffectiveHierarchyNode,
    blocks_by_file_id: dict[str, YamlBlock],
    component_type: str,
) -> YamlBlock | None:
    for component_file_id in node.component_file_ids:
        block = blocks_by_file_id.get(component_file_id)
        if block is not None and _component_type(block) == component_type:
            return block
    return None

def _component_lookup_key(node: EffectiveHierarchyNode, component_file_id: str) -> str:
    source_asset_path = str(node.origin["source"]["asset_path"])
    instance_key = str(node.origin["effective"].get("instance_key", ""))
    return f"{instance_key}|{source_asset_path}|{component_file_id}"

def _component_index(
    nodes: list[EffectiveHierarchyNode],
    project_root: Path,
    current_text: str,
    current_asset_path: str,
) -> dict[str, dict[str, str]]:
    index: dict[str, dict[str, str]] = {}
    blocks_cache: dict[str, dict[str, YamlBlock]] = {}
    for node in _walk_nodes(nodes):
        source_asset_path = str(node.origin["source"]["asset_path"])
        if source_asset_path not in blocks_cache:
            blocks_cache[source_asset_path] = _blocks_by_file_id(
                project_root,
                source_asset_path,
                current_text,
                current_asset_path,
            )
        blocks = blocks_cache[source_asset_path]
        for component_file_id in node.component_file_ids:
            block = blocks.get(component_file_id)
            if block is None:
                continue
            component_type = _component_type(block)
            backing_file_id = _backing_udon_file_id(block)
            has_backing = backing_file_id in blocks and _is_udon_behaviour(blocks[backing_file_id])
            index[_component_lookup_key(node, component_file_id)] = {
                "symbol_path": str(node.origin["effective"]["symbol_path"]),
                "component_type": component_type,
                "is_udon_proxy": str(bool(backing_file_id)).lower(),
                "has_backing_udon_behaviour": str(has_backing).lower(),
            }
    return index

def _component_overrides(
    node: EffectiveHierarchyNode,
    component_file_id: str,
    serialized_field: str,
) -> list[OverrideEntry]:
    prefix = f"{serialized_field}.m_PersistentCalls.m_Calls"
    return [
        entry
        for entry in node.override_entries
        if entry.target_file_id == component_file_id
        and (
            entry.property_path.startswith(prefix)
            or entry.property_path == f"{serialized_field}{_ARRAY_SIZE_SUFFIX}"
        )
    ]

def _blocks_by_file_id(
    project_root: Path,
    asset_path: str,
    current_text: str,
    current_asset_path: str,
) -> dict[str, YamlBlock]:
    text = current_text
    if asset_path != current_asset_path:
        text = decode_text_file(resolve_scope_path(asset_path, project_root))
    return {block.file_id: block for block in split_yaml_blocks(text)}

def _extract_field_block(block_text: str, field_name: str) -> str | None:
    lines = block_text.splitlines()
    for index, line in enumerate(lines):
        match = re.match(rf"^(?P<indent>\s+){re.escape(field_name)}:\s*$", line)
        if match is None:
            continue
        base_indent = len(match.group("indent"))
        captured = [line]
        for child in lines[index + 1:]:
            if child.strip():
                indent = len(child) - len(child.lstrip())
                if indent <= base_indent:
                    break
            captured.append(child)
        return "\n".join(captured)
    return None

def _component_type(block: YamlBlock) -> str:
    if _is_udon_behaviour(block):
        return "VRC.Udon.UdonBehaviour"
    for line in block.text.splitlines():
        match = re.match(r"\s+m_EditorClassIdentifier:\s*(.*)", line)
        if match is None:
            continue
        value = match.group(1).strip()
        if not value:
            break
        if "::" in value:
            value = value.rsplit("::", 1)[1]
        return value.rsplit(".", 1)[-1]
    return "MonoBehaviour"

def _is_udon_behaviour(block: YamlBlock) -> bool:
    return UDON_BEHAVIOUR_GUID in block.text

def _backing_udon_file_id(block: YamlBlock) -> str:
    match = re.search(r"_udonSharpBackingUdonBehaviour:\s*\{fileID:\s*(-?\d+)", block.text)
    return match.group(1) if match else ""

def _nodes_by_symbol(
    nodes: list[EffectiveHierarchyNode],
) -> dict[str, list[EffectiveHierarchyNode]]:
    found: dict[str, list[EffectiveHierarchyNode]] = {}
    for node in _walk_nodes(nodes):
        found.setdefault(str(node.origin["effective"]["symbol_path"]), []).append(node)
    return found

def _node_key(node: EffectiveHierarchyNode) -> str:
    return str(node.origin["effective"]["file_id"])

def _walk_nodes(nodes: list[EffectiveHierarchyNode]) -> Any:
    for node in nodes:
        yield node
        yield from _walk_nodes(node.children)
