from __future__ import annotations

from pathlib import Path

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

__all__ = ["EffectiveHierarchyNode", "EffectiveHierarchyResult", "build_effective_hierarchy"]
