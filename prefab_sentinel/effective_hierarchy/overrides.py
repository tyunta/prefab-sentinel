from __future__ import annotations

from typing import Any

from prefab_sentinel.services.prefab_variant.overrides import OverrideEntry

from .models import _BuildContext


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
