"""Stateful session for MCP server — caches GUID index, script map, SymbolTree.

Holds a single :class:`Phase1Orchestrator` instance and per-asset
:class:`SymbolTree` cache across MCP requests so that expensive
filesystem scans are performed at most once per change.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from prefab_sentinel.orchestrator import Phase1Orchestrator
from prefab_sentinel.symbol_tree import SymbolTree, build_script_name_map
from prefab_sentinel.unity_assets import find_project_root, resolve_scope_path
from prefab_sentinel.wsl_compat import to_wsl_path

__all__ = ["ProjectSession"]

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
# Session
# ---------------------------------------------------------------------------


class ProjectSession:
    """Cross-request state container for the MCP server.

    Caches:
    * :class:`Phase1Orchestrator` — singleton, re-created on GUID invalidation.
    * Script name map — lazy, derived from ``build_script_name_map``.
    * :class:`SymbolTree` per asset path — mtime-based invalidation.
    """

    def __init__(self, project_root: Path | None = None) -> None:
        self._project_root = project_root
        self._scope: Path | None = None

        # Caches
        self._orchestrator: Phase1Orchestrator | None = None
        self._script_name_map: dict[str, str] | None = None
        self._symbol_cache: dict[Path, _SymbolCacheEntry] = {}

        # Watcher lifecycle
        self._watcher_task: asyncio.Task[None] | None = None
        self._stop_event: asyncio.Event = asyncio.Event()

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def project_root(self) -> Path | None:
        return self._project_root

    @property
    def scope(self) -> Path | None:
        return self._scope

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
            self._script_name_map = build_script_name_map(self._project_root)
        return self._script_name_map

    def get_symbol_tree(
        self,
        path: Path,
        text: str,
        *,
        include_properties: bool = False,
    ) -> SymbolTree:
        """Return a SymbolTree, using mtime-based caching.

        A cached tree built with ``include_properties=True`` satisfies
        requests for both True and False.  A tree built with False is
        rebuilt when True is requested.
        """
        mtime = self._stat_mtime(path)

        if mtime is not None:
            cached = self._symbol_cache.get(path)
            if (
                cached is not None
                and cached.mtime == mtime
                and (not include_properties or cached.include_properties)
            ):
                return cached.tree

        tree = SymbolTree.build(
            text,
            str(path),
            self.script_name_map(),
            include_properties=include_properties,
        )

        if mtime is not None:
            self._symbol_cache[path] = _SymbolCacheEntry(
                mtime=mtime,
                include_properties=include_properties,
                tree=tree,
            )

        return tree

    # ------------------------------------------------------------------
    # Session management
    # ------------------------------------------------------------------

    async def activate(self, scope: str) -> dict[str, Any]:
        """Set project scope, warm caches, and start watcher.

        If re-activated with a different scope, stale caches are cleared
        and the watcher is restarted on the new scope path.

        Returns the session status dict.
        """
        scope_path = Path(to_wsl_path(scope))

        if self._project_root is None:
            self._project_root = find_project_root(scope_path)

        new_scope = resolve_scope_path(scope, self._project_root)

        # Scope change → clear caches and restart watcher
        if self._scope is not None and new_scope != self._scope:
            self.invalidate_all()
            await self._stop_watcher()

        self._scope = new_scope

        # Warm caches
        self.get_orchestrator()
        self.script_name_map()

        # Start watcher (best-effort — needs running event loop)
        self._start_watcher_if_available()

        return self.status()

    def status(self) -> dict[str, Any]:
        """Return current cache diagnostics."""
        return {
            "project_root": str(self._project_root) if self._project_root else None,
            "scope": str(self._scope) if self._scope else None,
            "orchestrator_cached": self._orchestrator is not None,
            "script_map_size": len(self._script_name_map) if self._script_name_map else 0,
            "script_map_cached": self._script_name_map is not None,
            "symbol_tree_entries": len(self._symbol_cache),
            "symbol_tree_paths": sorted(str(p) for p in self._symbol_cache),
            "watcher_running": self._watcher_task is not None
            and not self._watcher_task.done(),
        }

    # ------------------------------------------------------------------
    # Invalidation
    # ------------------------------------------------------------------

    def invalidate_guid_index(self) -> None:
        """Clear GUID index cache (trigger: .meta change).

        Cascades to script map and orchestrator since both depend on
        the GUID index.
        """
        self._orchestrator = None
        self._script_name_map = None
        logger.debug("Invalidated GUID index + script map + orchestrator")

    def invalidate_script_map(self) -> None:
        """Clear only the script name map (trigger: .cs change)."""
        self._script_name_map = None
        logger.debug("Invalidated script name map")

    def invalidate_symbol_tree(self, path: Path) -> None:
        """Evict a single SymbolTree entry (trigger: asset file change)."""
        if self._symbol_cache.pop(path, None) is not None:
            logger.debug("Evicted SymbolTree cache: %s", path)

    def invalidate_all(self) -> None:
        """Full cache reset."""
        self._orchestrator = None
        self._script_name_map = None
        self._symbol_cache.clear()
        logger.debug("Invalidated all caches")

    # ------------------------------------------------------------------
    # Watcher lifecycle
    # ------------------------------------------------------------------

    def _start_watcher_if_available(self) -> None:
        if self._watcher_task is not None and not self._watcher_task.done():
            return
        if self._scope is None:
            return
        try:
            from prefab_sentinel.watcher import _has_watchfiles, start_watcher
        except ImportError:
            # watcher module not yet available or watchfiles not installed
            return

        if not _has_watchfiles():
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            # No running event loop (e.g. CLI mode)
            return
        self._stop_event.clear()
        self._watcher_task = loop.create_task(
            start_watcher(self, self._scope, self._stop_event),
            name="prefab-sentinel-watcher",
        )
        self._watcher_task.add_done_callback(self._on_watcher_done)

    @staticmethod
    def _on_watcher_done(task: asyncio.Task[None]) -> None:
        """Log unhandled exceptions from the watcher task."""
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            logger.error("File watcher crashed: %s", exc, exc_info=exc)

    async def _stop_watcher(self) -> None:
        """Stop the watcher task if running."""
        self._stop_event.set()
        if self._watcher_task is not None:
            self._watcher_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._watcher_task
            self._watcher_task = None

    async def shutdown(self) -> None:
        """Stop the file watcher (called from MCP lifespan cleanup)."""
        await self._stop_watcher()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _stat_mtime(path: Path) -> float | None:
        try:
            return path.stat().st_mtime
        except OSError:
            return None
