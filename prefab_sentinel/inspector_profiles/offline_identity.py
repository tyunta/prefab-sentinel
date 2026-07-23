from __future__ import annotations

import re
from dataclasses import dataclass

from prefab_sentinel.inspector_profiles.model import TargetIdentity
from prefab_sentinel.mcp_helpers import read_asset
from prefab_sentinel.session import ProjectSession
from prefab_sentinel.symbol_tree import SymbolNode
from prefab_sentinel.unity_yaml_parser import split_yaml_blocks


@dataclass(frozen=True, slots=True)
class OfflineTargetMetadata:
    identity: TargetIdentity
    target: dict[str, object]


def inspect_offline_target_metadata(
    session: ProjectSession,
    asset_path: str,
    symbol_path: str | None,
) -> OfflineTargetMetadata | None:
    if session.project_root is None:
        return None
    try:
        text, resolved = read_asset(asset_path, session.project_root)
    except (FileNotFoundError, ValueError):
        return None
    tree = session.get_symbol_tree(resolved, text, include_properties=False)
    nodes = tree.resolve(symbol_path) if symbol_path is not None else _scripted_roots(tree.roots)
    if len(nodes) != 1:
        return None
    node = nodes[0]
    script_file_id = _script_file_id(text, node.file_id)
    if not node.script_guid or script_file_id is None:
        return None

    managed_type = node.script_name or _managed_selector(symbol_path) or node.name
    identity = TargetIdentity(managed_type, None, node.script_guid, script_file_id)
    script_path = _script_path(session, node.script_guid)
    target: dict[str, object] = {
        "managed_type": managed_type,
        "assembly": None,
        "script_guid": node.script_guid,
        "script_file_id": script_file_id,
        "script_path": script_path,
    }
    if script_path is None:
        target["script_path_degradation_reasons"] = [
            "Script source path could not be resolved from offline metadata."
        ]
    return OfflineTargetMetadata(identity, target)


def _scripted_roots(roots: list[SymbolNode]) -> list[SymbolNode]:
    scripted: list[SymbolNode] = []
    pending = list(roots)
    while pending:
        node = pending.pop()
        if node.script_guid:
            scripted.append(node)
        pending.extend(node.children)
    return scripted


def _script_file_id(text: str, component_file_id: str) -> int | None:
    for block in split_yaml_blocks(text):
        if block.file_id != component_file_id:
            continue
        match = re.search(r"\bm_Script:\s*\{[^}]*\bfileID:\s*(-?\d+)", block.text)
        return int(match.group(1)) if match is not None else None
    return None


def _managed_selector(symbol_path: str | None) -> str | None:
    if symbol_path is None:
        return None
    segment = symbol_path.rsplit("/", 1)[-1]
    match = re.fullmatch(r"MonoBehaviour\((?!guid:)(.+)\)(?:#\d+)?", segment)
    return match.group(1) if match is not None else None


def _script_path(session: ProjectSession, script_guid: str) -> str | None:
    path = session.guid_index().get(script_guid)
    if path is None or session.project_root is None:
        return None
    try:
        return path.relative_to(session.project_root).as_posix()
    except ValueError:
        return None
