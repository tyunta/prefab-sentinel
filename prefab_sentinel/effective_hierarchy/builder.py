from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from prefab_sentinel.contracts import Diagnostic, Severity
from prefab_sentinel.nested_prefab_cache import NestedPrefabCache
from prefab_sentinel.unity_yaml_parser import TransformInfo

from .models import (
    EffectiveHierarchyNode,
    _AssetModel,
    _BuildContext,
    _PrefabInstance,
)
from .overrides import (
    _effective_file_id,
    _group_overrides,
    _origin_payload,
    _override_value,
    _target_overrides,
)
from .parser import _component_descriptors, _is_root_game_object, _parse_asset
from .paths import _join_symbol_path
from .source_loader import _load_nested_source


class _EffectiveHierarchyBuilder:
    def __init__(
        self,
        project_root: Path,
        guid_index: Mapping[str, Path],
        max_depth: int | None,
        *,
        nested_prefab_cache: NestedPrefabCache | None = None,
    ) -> None:
        self._project_root = project_root
        self._guid_index = guid_index
        self._max_depth = max_depth
        self._nested_prefab_cache = nested_prefab_cache
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
            if _is_root_game_object(model, go_file_id)
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
        source_asset_path, source_text, source_blocks, load_warning = _load_nested_source(
            instance.source_guid,
            source_path,
            self._project_root,
            self._nested_prefab_cache,
        )
        if load_warning is not None:
            self._add_warning(
                host_model.asset_path,
                instance.file_id,
                "EFFECTIVE_HIERARCHY_SOURCE_UNRESOLVED",
                load_warning,
            )
            return []
        source_model = _parse_asset(
            source_asset_path,
            source_text,
            blocks=source_blocks,
        )
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
            if _is_root_game_object(source_model, go_file_id)
        ]

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
