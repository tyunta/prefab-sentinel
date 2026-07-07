from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from prefab_sentinel.nested_prefab_cache import NestedPrefabCache
from prefab_sentinel.unity_assets import collect_project_guid_index

from .builder import _EffectiveHierarchyBuilder
from .models import EffectiveHierarchyNode, EffectiveHierarchyResult
from .parser import _parse_asset


def build_effective_hierarchy(
    project_root: Path,
    asset_path: str,
    text: str,
    *,
    max_depth: int | None = None,
    guid_index: Mapping[str, Path] | None = None,
    nested_prefab_cache: NestedPrefabCache | None = None,
) -> EffectiveHierarchyResult:
    resolved_guid_index = (
        collect_project_guid_index(project_root, include_package_cache=False)
        if guid_index is None
        else guid_index
    )
    model = _parse_asset(asset_path, text)
    builder = _EffectiveHierarchyBuilder(
        project_root,
        resolved_guid_index,
        max_depth,
        nested_prefab_cache=nested_prefab_cache,
    )
    roots = builder.build_roots(model)
    return EffectiveHierarchyResult(
        asset_path=asset_path,
        roots=roots,
        diagnostics=builder.diagnostics,
    )

__all__ = ["EffectiveHierarchyNode", "EffectiveHierarchyResult", "build_effective_hierarchy"]
