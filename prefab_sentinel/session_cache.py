"""Cache management layer for the MCP session.

Extracts cache state, lazy accessors, invalidation, and knowledge
suggestions from :class:`~prefab_sentinel.session.ProjectSession`
so that caching logic is independently testable.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from prefab_sentinel.orchestrator import Phase1Orchestrator
from prefab_sentinel.symbol_tree import SymbolTree, build_script_name_map
from prefab_sentinel.symbol_tree_builder import build_symbol_tree
from prefab_sentinel.unity_assets import collect_project_guid_index

__all__ = ["SessionCacheManager"]

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# SymbolTree cache entry
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class _SymbolCacheEntry:
    mtime: float
    include_properties: bool
    tree: SymbolTree


# ---------------------------------------------------------------------------
# Cache manager
# ---------------------------------------------------------------------------


class SessionCacheManager:
    """Owns all cached data for a single project session.

    Provides lazy-initialised accessors, mtime-based SymbolTree cache,
    and cascade-aware invalidation.
    """

    def __init__(self) -> None:
        self._project_root: Path | None = None
        self._orchestrator: Phase1Orchestrator | None = None
        self._guid_index: dict[str, Path] | None = None
        self._script_name_map: dict[str, str] | None = None
        self._symbol_cache: dict[Path, _SymbolCacheEntry] = {}

    # ------------------------------------------------------------------
    # Project root
    # ------------------------------------------------------------------

    @property
    def project_root(self) -> Path | None:
        return self._project_root

    @project_root.setter
    def project_root(self, value: Path | None) -> None:
        self._project_root = value

    # ------------------------------------------------------------------
    # Cache accessors
    # ------------------------------------------------------------------

    def get_orchestrator(self) -> Phase1Orchestrator:
        """Return the cached orchestrator, creating on first call."""
        if self._orchestrator is None:
            self._orchestrator = Phase1Orchestrator.default(
                project_root=self._project_root,
            )
        return self._orchestrator

    def script_name_map(self) -> dict[str, str]:
        """Return the cached script name map, building on first call."""
        if self._script_name_map is None:
            if self._project_root is None:
                return {}
            self._script_name_map = build_script_name_map(self.guid_index())
        return self._script_name_map

    def guid_index(self) -> dict[str, Path]:
        """Return the cached GUID index, building on first call."""
        if self._guid_index is None:
            if self._project_root is None:
                return {}
            self._guid_index = collect_project_guid_index(
                self._project_root, include_package_cache=False,
            )
        return self._guid_index

    def get_symbol_tree(
        self,
        path: Path,
        text: str,
        *,
        include_properties: bool = False,
        expand_nested: bool = False,
        guid_to_asset_path: dict[str, Path] | None = None,
    ) -> SymbolTree:
        """Return a SymbolTree, using mtime-based caching.

        A cached tree built with ``include_properties=True`` satisfies
        requests for both True and False.  A tree built with False is
        rebuilt when True is requested.

        When *expand_nested* is True, the cache is bypassed (expanded
        trees depend on child prefab files whose mtime is not tracked).
        """
        if not expand_nested:
            mtime = self._stat_mtime(path)
            if mtime is not None:
                cached = self._symbol_cache.get(path)
                if (
                    cached is not None
                    and cached.mtime == mtime
                    and (not include_properties or cached.include_properties)
                ):
                    return cached.tree

        tree = build_symbol_tree(
            text,
            str(path),
            self.script_name_map(),
            include_properties=include_properties,
            expand_nested=expand_nested,
            guid_to_asset_path=guid_to_asset_path,
        )

        if not expand_nested:
            mtime = self._stat_mtime(path)
            if mtime is not None:
                self._symbol_cache[path] = _SymbolCacheEntry(
                    mtime=mtime,
                    include_properties=include_properties,
                    tree=tree,
                )

        return tree

    # ------------------------------------------------------------------
    # Invalidation
    # ------------------------------------------------------------------

    def invalidate_guid_index(self) -> None:
        """Clear GUID index cache (trigger: .meta change).

        Cascades to script map, SymbolTree cache, and orchestrator since
        all depend on the GUID index.
        """
        self._orchestrator = None
        self._guid_index = None
        self._script_name_map = None
        self._symbol_cache.clear()
        logger.debug("Invalidated GUID index + script map + SymbolTree + orchestrator")

    def invalidate_script_map(self) -> None:
        """Clear only the script name map (trigger: .cs change).

        Also clears all SymbolTree entries because MonoBehaviour nodes
        reference script names from the map.
        """
        self._script_name_map = None
        self._symbol_cache.clear()
        logger.debug("Invalidated script name map + all SymbolTree entries")

    def invalidate_symbol_tree(self, path: Path) -> None:
        """Evict a single SymbolTree entry (trigger: asset file change)."""
        if self._symbol_cache.pop(path, None) is not None:
            logger.debug("Evicted SymbolTree cache: %s", path)

    def invalidate_asset_caches(self, path: Path) -> None:
        """Clear service-level caches for a single asset (trigger: asset file change).

        Unlike invalidate_guid_index, this does NOT re-create the orchestrator.
        """
        if self._orchestrator is not None:
            self._orchestrator.invalidate_text_cache(path)
            self._orchestrator.invalidate_before_cache()
            self._orchestrator.invalidate_scope_files_cache()
        logger.debug("Invalidated asset caches for %s", path)

    def invalidate_all(self) -> None:
        """Full cache reset."""
        self._orchestrator = None
        self._guid_index = None
        self._script_name_map = None
        self._symbol_cache.clear()
        logger.debug("Invalidated all caches")

    # ------------------------------------------------------------------
    # Knowledge suggestions
    # ------------------------------------------------------------------

    _SELF_KNOWLEDGE: list[str] = [
        "knowledge/prefab-sentinel-editor-camera.md",
        "knowledge/prefab-sentinel-material-operations.md",
        "knowledge/prefab-sentinel-patch-patterns.md",
        "knowledge/prefab-sentinel-variant-patterns.md",
        "knowledge/prefab-sentinel-wiring-triage.md",
        "knowledge/prefab-sentinel-workflow-patterns.md",
    ]

    _KEYWORD_TO_KNOWLEDGE: dict[str, str] = {
        "UdonSharp": "knowledge/udonsharp.md",
        "UdonBehaviour": "knowledge/udonsharp.md",
        "VRCSceneDescriptor": "knowledge/vrchat-sdk-worlds.md",
        "VRC_SceneDescriptor": "knowledge/vrchat-sdk-worlds.md",
        "VRCAvatarDescriptor": "knowledge/vrchat-sdk-avatars.md",
        "ModularAvatar": "knowledge/modular-avatar.md",
        "VRCFury": "knowledge/vrcfury.md",
        "AvatarOptimizer": "knowledge/avatar-optimizer.md",
        "lilToon": "knowledge/liltoon.md",
        "NDMF": "knowledge/ndmf.md",
        "nadena.dev.ndmf": "knowledge/ndmf.md",
    }

    def suggest_reads(self) -> list[str]:
        """Return knowledge file paths relevant to the current project.

        Combines prefab-sentinel's own knowledge (always) with ecosystem
        knowledge detected via script_name_map values and guid_index
        asset paths.
        """
        ecosystem: set[str] = set()

        script_lower = [v.lower() for v in self.script_name_map().values()]
        guid_lower = [str(p).lower() for p in self.guid_index().values()]

        for keyword, knowledge_file in self._KEYWORD_TO_KNOWLEDGE.items():
            kw_lower = keyword.lower()
            if any(kw_lower in v for v in script_lower) or any(
                kw_lower in p for p in guid_lower
            ):
                ecosystem.add(knowledge_file)

        if ecosystem:
            ecosystem.add("knowledge/vrchat-sdk-base.md")

        return sorted(self._SELF_KNOWLEDGE) + sorted(ecosystem)

    # ------------------------------------------------------------------
    # Diagnostics
    # ------------------------------------------------------------------

    def cache_status(self) -> dict[str, Any]:
        """Return cache diagnostic fields."""
        return {
            "orchestrator_cached": self._orchestrator is not None,
            "script_map_size": len(self._script_name_map) if self._script_name_map else 0,
            "script_map_cached": self._script_name_map is not None,
            "symbol_tree_entries": len(self._symbol_cache),
            "symbol_tree_paths": sorted(str(p) for p in self._symbol_cache),
        }

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _stat_mtime(path: Path) -> float | None:
        try:
            return path.stat().st_mtime
        except OSError:
            return None
