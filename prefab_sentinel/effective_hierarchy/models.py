from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from prefab_sentinel.contracts import Diagnostic, Severity
from prefab_sentinel.hierarchy import ComponentDescriptor
from prefab_sentinel.services.prefab_variant.overrides import OverrideEntry
from prefab_sentinel.unity_yaml_parser import (
    ComponentInfo,
    GameObjectInfo,
    TransformInfo,
    YamlBlock,
)


@dataclass(slots=True)
class EffectiveHierarchyNode:
    file_id: str
    name: str
    components: list[str]
    component_file_ids: list[str]
    children: list[EffectiveHierarchyNode]
    depth: int
    transform: TransformInfo | None
    origin: dict[str, Any]
    override_entries: list[OverrideEntry] = field(default_factory=list)
    component_descriptors: list[ComponentDescriptor] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "file_id": self.file_id,
            "name": self.name,
            "depth": self.depth,
            "components": self.components,
            "children": [child.to_dict() for child in self.children],
            "origin": self.origin,
        }

@dataclass(slots=True)
class EffectiveHierarchyResult:
    asset_path: str
    roots: list[EffectiveHierarchyNode]
    diagnostics: list[Diagnostic] = field(default_factory=list)

    @property
    def total_game_objects(self) -> int:
        return sum(1 for _ in _walk_nodes(self.roots))

    @property
    def total_components(self) -> int:
        return sum(len(node.component_file_ids) for node in _walk_nodes(self.roots))

    @property
    def max_depth(self) -> int:
        return max((node.depth for node in _walk_nodes(self.roots)), default=0)

    @property
    def severity(self) -> Severity:
        return Severity.WARNING if self.diagnostics else Severity.INFO

    def to_dict(self) -> dict[str, Any]:
        return {
            "asset_path": self.asset_path,
            "read_only": True,
            "total_game_objects": self.total_game_objects,
            "total_components": self.total_components,
            "max_depth": self.max_depth,
            "roots": [root.to_dict() for root in self.roots],
            "diagnostics": [
                _diagnostic_to_dict(diag, self.severity) for diag in self.diagnostics
            ],
        }

@dataclass(slots=True)
class _AssetModel:
    asset_path: str
    text: str
    blocks: list[YamlBlock]
    game_objects: dict[str, GameObjectInfo]
    transforms: dict[str, TransformInfo]
    components: dict[str, ComponentInfo]
    blocks_by_file_id: dict[str, YamlBlock]
    transform_by_game_object: dict[str, TransformInfo]
    game_object_by_transform: dict[str, str]
    instances_by_parent: dict[str, list[_PrefabInstance]]

@dataclass(slots=True)
class _PrefabInstance:
    file_id: str
    source_guid: str
    parent_transform_file_id: str
    modifications: list[OverrideEntry]

@dataclass(slots=True)
class _BuildContext:
    host_asset_path: str
    source_asset_path: str
    source_guid: str
    instance_file_id: str
    instance_path: tuple[str, ...]
    overrides_by_target: dict[str, list[OverrideEntry]]
    nested_depth: int

def _walk_nodes(
    nodes: list[EffectiveHierarchyNode],
) -> Any:
    for node in nodes:
        yield node
        yield from _walk_nodes(node.children)

def _diagnostic_to_dict(
    diagnostic: Diagnostic, default_severity: Severity
) -> dict[str, Any]:
    data: dict[str, Any] = {}
    if diagnostic.path:
        data["path"] = diagnostic.path
    if diagnostic.location:
        data["location"] = diagnostic.location
    return {
        "severity": diagnostic.severity
        if diagnostic.severity is not None
        else default_severity.value,
        "code": diagnostic.detail,
        "message": diagnostic.evidence or diagnostic.detail,
        "data": data,
    }
