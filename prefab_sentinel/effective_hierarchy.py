from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from prefab_sentinel.contracts import Diagnostic, Severity
from prefab_sentinel.hierarchy import CLASS_NAMES, ComponentDescriptor
from prefab_sentinel.services.prefab_variant.overrides import OverrideEntry, parse_overrides
from prefab_sentinel.unity_assets import (
    SOURCE_PREFAB_PATTERN,
    collect_project_guid_index,
    decode_text_file,
    normalize_guid,
)
from prefab_sentinel.unity_assets_path import relative_to_root
from prefab_sentinel.unity_yaml_parser import (
    CLASS_ID_GAMEOBJECT,
    CLASS_ID_MONOBEHAVIOUR,
    CLASS_ID_PREFAB_INSTANCE,
    TRANSFORM_CLASS_IDS,
    ComponentInfo,
    GameObjectInfo,
    TransformInfo,
    YamlBlock,
    parse_components,
    parse_game_objects,
    parse_transforms,
    split_yaml_blocks,
)

_TRANSFORM_PARENT_PATTERN = re.compile(
    r"m_TransformParent:\s*\{fileID:\s*(-?\d+)"
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


def build_effective_hierarchy(
    project_root: Path,
    asset_path: str,
    text: str,
    *,
    max_depth: int | None = None,
) -> EffectiveHierarchyResult:
    guid_index = collect_project_guid_index(project_root)
    model = _parse_asset(asset_path, text)
    builder = _EffectiveHierarchyBuilder(project_root, guid_index, max_depth)
    roots = builder.build_roots(model)
    return EffectiveHierarchyResult(
        asset_path=asset_path,
        roots=roots,
        diagnostics=builder.diagnostics,
    )


class _EffectiveHierarchyBuilder:
    def __init__(
        self,
        project_root: Path,
        guid_index: dict[str, Path],
        max_depth: int | None,
    ) -> None:
        self._project_root = project_root
        self._guid_index = guid_index
        self._max_depth = max_depth
        self.diagnostics: list[Diagnostic] = []

    def build_roots(self, model: _AssetModel) -> list[EffectiveHierarchyNode]:
        roots = [
            self._build_game_object_node(
                model,
                go_file_id,
                parent_symbol_path="",
                depth=0,
                context=None,
                source_stack=(),
                transform_stack=(),
            )
            for go_file_id in model.game_objects
            if self._is_root_game_object(model, go_file_id)
        ]
        roots.extend(
            self._expand_parent_instances(
                model,
                parent_transform_file_id="0",
                parent_symbol_path="",
                depth=0,
                context=None,
                source_stack=(),
            )
        )
        return roots

    def _build_game_object_node(
        self,
        model: _AssetModel,
        go_file_id: str,
        *,
        parent_symbol_path: str,
        depth: int,
        context: _BuildContext | None,
        source_stack: tuple[str, ...],
        transform_stack: tuple[str, ...],
    ) -> EffectiveHierarchyNode:
        game_object = model.game_objects[go_file_id]
        transform = model.transform_by_game_object.get(go_file_id)
        transform_file_id = transform.file_id if transform is not None else None
        game_object_overrides = _target_overrides(context, go_file_id)
        transform_overrides = (
            _target_overrides(context, transform_file_id)
            if transform_file_id is not None
            else []
        )
        component_overrides = [
            entry
            for component_file_id in game_object.component_file_ids
            if component_file_id != transform_file_id
            for entry in _target_overrides(context, component_file_id)
        ]
        node_overrides = [
            *game_object_overrides,
            *transform_overrides,
            *component_overrides,
        ]
        name = _override_value(game_object_overrides, "m_Name", game_object.name)
        symbol_path = _join_symbol_path(parent_symbol_path, name)
        child_transform_stack = (
            (*transform_stack, transform.file_id)
            if transform is not None
            else transform_stack
        )
        child_nodes = self._build_transform_children(
            model,
            transform,
            parent_symbol_path=symbol_path,
            depth=depth + 1,
            context=context,
            source_stack=source_stack,
            transform_stack=child_transform_stack,
        )
        child_nodes.extend(
            self._expand_parent_instances(
                model,
                parent_transform_file_id=transform.file_id if transform else "0",
                parent_symbol_path=symbol_path,
                depth=depth + 1,
                context=context,
                source_stack=source_stack,
            )
        )
        component_descriptors = _component_descriptors(model, game_object)
        return EffectiveHierarchyNode(
            file_id=_effective_file_id(go_file_id, context),
            name=name,
            components=[descriptor.label for descriptor in component_descriptors],
            component_file_ids=game_object.component_file_ids,
            children=child_nodes,
            depth=depth,
            transform=transform,
            origin=_origin_payload(
                model.asset_path, go_file_id, symbol_path, context, node_overrides
            ),
            override_entries=node_overrides,
            component_descriptors=component_descriptors,
        )

    def _build_transform_children(
        self,
        model: _AssetModel,
        transform: TransformInfo | None,
        *,
        parent_symbol_path: str,
        depth: int,
        context: _BuildContext | None,
        source_stack: tuple[str, ...],
        transform_stack: tuple[str, ...],
    ) -> list[EffectiveHierarchyNode]:
        if transform is None:
            return []
        children: list[EffectiveHierarchyNode] = []
        for child_transform_file_id in transform.children_file_ids:
            child_go_file_id = model.game_object_by_transform.get(child_transform_file_id)
            if not child_go_file_id:
                continue
            if child_transform_file_id in transform_stack:
                self._add_warning(
                    model.asset_path,
                    child_transform_file_id,
                    "EFFECTIVE_HIERARCHY_TRANSFORM_CHILD_CYCLE",
                    f"Transform child link {child_transform_file_id} creates a cycle.",
                )
                continue
            children.append(
                self._build_game_object_node(
                    model,
                    child_go_file_id,
                    parent_symbol_path=parent_symbol_path,
                    depth=depth,
                    context=context,
                    source_stack=source_stack,
                    transform_stack=transform_stack,
                )
            )
        return children

    def _expand_parent_instances(
        self,
        model: _AssetModel,
        *,
        parent_transform_file_id: str,
        parent_symbol_path: str,
        depth: int,
        context: _BuildContext | None,
        source_stack: tuple[str, ...],
    ) -> list[EffectiveHierarchyNode]:
        expanded: list[EffectiveHierarchyNode] = []
        for instance in model.instances_by_parent.get(parent_transform_file_id, []):
            expanded.extend(
                self._expand_instance(
                    model,
                    instance,
                    parent_symbol_path=parent_symbol_path,
                    depth=depth,
                    context=context,
                    source_stack=source_stack,
                )
            )
        return expanded

    def _expand_instance(
        self,
        host_model: _AssetModel,
        instance: _PrefabInstance,
        *,
        parent_symbol_path: str,
        depth: int,
        context: _BuildContext | None,
        source_stack: tuple[str, ...],
    ) -> list[EffectiveHierarchyNode]:
        if instance.source_guid in source_stack:
            self._add_warning(
                host_model.asset_path,
                instance.file_id,
                "EFFECTIVE_HIERARCHY_CYCLE",
                f"Nested PrefabInstance source GUID {instance.source_guid} is cyclic.",
            )
            return []
        nested_depth = len(source_stack) + 1
        if self._max_depth is not None and nested_depth > self._max_depth:
            self._add_warning(
                host_model.asset_path,
                instance.file_id,
                "EFFECTIVE_HIERARCHY_DEPTH_LIMIT",
                f"Nested PrefabInstance source GUID {instance.source_guid} exceeded max_depth={self._max_depth}.",
            )
            return []
        source_path = self._guid_index.get(instance.source_guid)
        if source_path is None or not source_path.exists():
            self._add_warning(
                host_model.asset_path,
                instance.file_id,
                "EFFECTIVE_HIERARCHY_SOURCE_UNRESOLVED",
                f"Nested PrefabInstance source GUID {instance.source_guid} could not be resolved.",
            )
            return []
        try:
            source_text = decode_text_file(source_path)
        except (OSError, UnicodeDecodeError) as exc:
            self._add_warning(
                host_model.asset_path,
                instance.file_id,
                "EFFECTIVE_HIERARCHY_SOURCE_UNRESOLVED",
                f"Nested PrefabInstance source GUID {instance.source_guid} could not be decoded: {exc}.",
            )
            return []
        source_asset_path = relative_to_root(source_path, self._project_root)
        source_model = _parse_asset(source_asset_path, source_text)
        instance_path = (
            (*context.instance_path, instance.file_id)
            if context is not None
            else (instance.file_id,)
        )
        child_context = _BuildContext(
            host_asset_path=host_model.asset_path,
            source_asset_path=source_asset_path,
            source_guid=instance.source_guid,
            instance_file_id=instance.file_id,
            instance_path=instance_path,
            overrides_by_target=_group_overrides(instance.modifications),
            nested_depth=nested_depth,
        )
        return [
            self._build_game_object_node(
                source_model,
                go_file_id,
                parent_symbol_path=parent_symbol_path,
                depth=depth,
                context=child_context,
                source_stack=(*source_stack, instance.source_guid),
                transform_stack=(),
            )
            for go_file_id in source_model.game_objects
            if self._is_root_game_object(source_model, go_file_id)
        ]

    @staticmethod
    def _is_root_game_object(model: _AssetModel, go_file_id: str) -> bool:
        transform = model.transform_by_game_object.get(go_file_id)
        if transform is None:
            return False
        if transform.father_file_id in ("", "0"):
            return True
        return transform.father_file_id not in model.game_object_by_transform

    def _add_warning(
        self, asset_path: str, location: str, code: str, message: str
    ) -> None:
        self.diagnostics.append(
            Diagnostic(
                path=asset_path,
                location=location,
                detail=code,
                evidence=message,
                severity=Severity.WARNING.value,
            )
        )


def _parse_asset(asset_path: str, text: str) -> _AssetModel:
    blocks = split_yaml_blocks(text)
    game_objects = parse_game_objects(blocks)
    transforms = parse_transforms(blocks)
    components = parse_components(blocks)
    transform_by_game_object = {
        transform.game_object_file_id: transform
        for transform in transforms.values()
        if transform.game_object_file_id
    }
    game_object_by_transform = {
        transform.file_id: transform.game_object_file_id
        for transform in transforms.values()
        if transform.game_object_file_id
    }
    instances = _instances_by_parent(blocks)
    return _AssetModel(
        asset_path=asset_path,
        text=text,
        blocks=blocks,
        game_objects=game_objects,
        transforms=transforms,
        components=components,
        blocks_by_file_id={block.file_id: block for block in blocks},
        transform_by_game_object=transform_by_game_object,
        game_object_by_transform=game_object_by_transform,
        instances_by_parent=instances,
    )


def _instances_by_parent(blocks: list[YamlBlock]) -> dict[str, list[_PrefabInstance]]:
    instances: dict[str, list[_PrefabInstance]] = {}
    for block in blocks:
        if block.class_id != CLASS_ID_PREFAB_INSTANCE:
            continue
        source_match = SOURCE_PREFAB_PATTERN.search(block.text)
        if source_match is None:
            continue
        parent_match = _TRANSFORM_PARENT_PATTERN.search(block.text)
        parent = parent_match.group(1) if parent_match else "0"
        source_guid = normalize_guid(source_match.group(2))
        instance = _PrefabInstance(
            file_id=block.file_id,
            source_guid=source_guid,
            parent_transform_file_id=parent,
            modifications=parse_overrides(block.text),
        )
        instances.setdefault(parent, []).append(instance)
    return instances


def _group_overrides(entries: list[OverrideEntry]) -> dict[str, list[OverrideEntry]]:
    grouped: dict[str, list[OverrideEntry]] = {}
    for entry in entries:
        grouped.setdefault(entry.target_file_id, []).append(entry)
    return grouped


def _target_overrides(
    context: _BuildContext | None, target_file_id: str
) -> list[OverrideEntry]:
    if context is None:
        return []
    return context.overrides_by_target.get(target_file_id, [])


def _override_value(
    entries: list[OverrideEntry], property_path: str, default: str
) -> str:
    for entry in entries:
        if entry.property_path == property_path:
            return entry.value
    return default





def _component_descriptors(
    model: _AssetModel,
    game_object: GameObjectInfo,
) -> list[ComponentDescriptor]:
    descriptors: list[ComponentDescriptor] = []
    for file_id in game_object.component_file_ids:
        component = model.components.get(file_id)
        if component is None:
            continue
        if component.class_id in TRANSFORM_CLASS_IDS or component.class_id == CLASS_ID_GAMEOBJECT:
            continue
        if component.class_id == CLASS_ID_MONOBEHAVIOUR:
            descriptors.append(
                ComponentDescriptor("MonoBehaviour", script_guid=component.script_guid)
            )
        else:
            descriptors.append(
                ComponentDescriptor(
                    CLASS_NAMES.get(component.class_id, f"Component({component.class_id})")
                )
            )
    return descriptors


def _origin_payload(
    asset_path: str,
    source_file_id: str,
    symbol_path: str,
    context: _BuildContext | None,
    override_entries: list[OverrideEntry],
) -> dict[str, Any]:
    if context is None:
        return {
            "source": {
                "kind": "source_default",
                "asset_path": asset_path,
                "file_id": source_file_id,
            },
            "nested_instance": None,
            "override_host": {"asset_path": asset_path, "property_paths": []},
            "effective": {
                "symbol_path": symbol_path,
                "file_id": source_file_id,
                "instance_key": "",
            },
        }
    instance_key = _effective_instance_key(context)
    return {
        "source": {
            "kind": "source_default",
            "asset_path": context.source_asset_path,
            "file_id": source_file_id,
        },
        "nested_instance": {
            "kind": "nested_prefab_instance",
            "file_id": context.instance_file_id,
            "instance_path": list(context.instance_path),
            "host_asset_path": context.host_asset_path,
        },
        "override_host": {
            "kind": "override_bearing_host",
            "asset_path": context.host_asset_path,
            "property_paths": [entry.property_path for entry in override_entries],
        },
        "effective": {
            "kind": "effective_node",
            "symbol_path": symbol_path,
            "file_id": f"{instance_key}:{source_file_id}",
            "instance_key": instance_key,
        },
    }


def _effective_file_id(source_file_id: str, context: _BuildContext | None) -> str:
    if context is None:
        return source_file_id
    return f"{_effective_instance_key(context)}:{source_file_id}"


def _effective_instance_key(context: _BuildContext) -> str:
    return "/".join(context.instance_path)


def _join_symbol_path(parent: str, name: str) -> str:
    if parent:
        return f"{parent}/{name}"
    return name


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


__all__ = ["EffectiveHierarchyNode", "EffectiveHierarchyResult", "build_effective_hierarchy"]
