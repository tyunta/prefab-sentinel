from __future__ import annotations

from prefab_sentinel.effective_hierarchy import EffectiveHierarchyNode


def _nodes_by_symbol(
    nodes: list[EffectiveHierarchyNode],
) -> dict[str, list[EffectiveHierarchyNode]]:
    found: dict[str, list[EffectiveHierarchyNode]] = {}
    for node in nodes:
        found.setdefault(_symbol_path(node), []).append(node)
        for symbol_path, child_matches in _nodes_by_symbol(node.children).items():
            found.setdefault(symbol_path, []).extend(child_matches)
    return found

def _parent_map(
    nodes: list[EffectiveHierarchyNode],
) -> dict[str, EffectiveHierarchyNode]:
    parents: dict[str, EffectiveHierarchyNode] = {}
    for node in nodes:
        for child in node.children:
            parents[_node_key(child)] = node
        parents.update(_parent_map(node.children))
    return parents

def _symbol_path(node: EffectiveHierarchyNode) -> str:
    return str(node.origin["effective"]["symbol_path"])

def _node_key(node: EffectiveHierarchyNode) -> str:
    return str(node.origin["effective"]["file_id"])
