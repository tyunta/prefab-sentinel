from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from prefab_sentinel.nested_prefab_cache import NestedPrefabCache


@dataclass(frozen=True, slots=True)
class ProjectInspectionContext:
    project_root: Path | None
    guid_index: Mapping[str, Path]
    script_name_map: Mapping[str, str]
    nested_prefab_cache: NestedPrefabCache
