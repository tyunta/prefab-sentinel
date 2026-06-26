from __future__ import annotations

import re

from prefab_sentinel.hierarchy import CLASS_NAMES, ComponentDescriptor
from prefab_sentinel.services.prefab_variant.overrides import parse_overrides
from prefab_sentinel.unity_assets import SOURCE_PREFAB_PATTERN, normalize_guid
from prefab_sentinel.unity_yaml_parser import (
    CLASS_ID_GAMEOBJECT,
    CLASS_ID_MONOBEHAVIOUR,
    CLASS_ID_PREFAB_INSTANCE,
    TRANSFORM_CLASS_IDS,
    GameObjectInfo,
    YamlBlock,
    parse_components,
    parse_game_objects,
    parse_transforms,
    split_yaml_blocks,
)

from .models import _AssetModel, _PrefabInstance

_TRANSFORM_PARENT_PATTERN = re.compile(
    r"m_TransformParent:\s*\{fileID:\s*(-?\d+)"
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
