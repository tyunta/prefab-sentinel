from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from prefab_sentinel.nested_prefab_cache import NestedPrefabCache
from prefab_sentinel.unity_assets import decode_text_file
from prefab_sentinel.unity_assets_path import relative_to_root
from prefab_sentinel.unity_yaml_parser import YamlBlock


def _load_nested_source(
    source_guid: str,
    source_path: Path,
    project_root: Path,
    nested_prefab_cache: NestedPrefabCache | None,
) -> tuple[str, str, Sequence[YamlBlock] | None, str | None]:
    if nested_prefab_cache is None:
        try:
            source_text = decode_text_file(source_path)
        except (OSError, UnicodeDecodeError):
            return (
                "",
                "",
                None,
                f"Nested PrefabInstance source GUID {source_guid} could not be decoded.",
            )
        return relative_to_root(source_path, project_root), source_text, None, None

    record = nested_prefab_cache.load_child(
        source_guid,
        source_path,
        project_root,
    )
    if record.diagnostic is not None:
        return "", "", None, record.diagnostic.evidence
    return record.rel_posix, record.text or "", record.blocks, None
