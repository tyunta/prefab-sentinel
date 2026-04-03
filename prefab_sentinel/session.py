"""Stateful session for MCP server — caches GUID index, script map, SymbolTree.

Holds a single :class:`Phase1Orchestrator` instance and per-asset
:class:`SymbolTree` cache across MCP requests so that expensive
filesystem scans are performed at most once per change.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from prefab_sentinel.bridge_constants import UNITY_PROJECT_PATH_ENV
from prefab_sentinel.editor_bridge import bridge_status
from prefab_sentinel.orchestrator import Phase1Orchestrator
from prefab_sentinel.symbol_tree import SymbolTree, build_script_name_map
from prefab_sentinel.unity_assets import (
    collect_project_guid_index,
    find_project_root,
    resolve_scope_path,
)
from prefab_sentinel.wsl_compat import to_wsl_path

__all__ = ["InvalidProjectRootError", "ProjectSession"]

logger = logging.getLogger(__name__)


class InvalidProjectRootError(FileNotFoundError):
    """Raised when a specified Unity project root lacks an Assets/ directory."""


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
        self._guid_index: dict[str, Path] | None = None
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

    def resolve_scope(self, explicit_scope: str | None) -> str | None:
        """Return *explicit_scope* if given, else session scope as str."""
        if explicit_scope is not None:
            return explicit_scope
        return str(self._scope) if self._scope is not None else None

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

    _BRIDGE_VERSION_RE = re.compile(r'BridgeVersion\s*=\s*"([^"]+)"')

    def detect_bridge_version(self) -> str | None:
        """Detect the BridgeVersion from the Unity project's Editor bridge files.

        Searches for PrefabSentinel.UnityEditorControlBridge.cs in the project's
        Assets/ directory tree and extracts the BridgeVersion constant.
        Returns None if not found.
        """
        if self._project_root is None:
            return None
        editor_dir = self._project_root / "Assets"
        if not editor_dir.is_dir():
            return None
        for cs_file in editor_dir.rglob("PrefabSentinel.UnityEditorControlBridge.cs"):
            try:
                text = cs_file.read_text(encoding="utf-8-sig", errors="replace")
                m = self._BRIDGE_VERSION_RE.search(text)
                if m:
                    return m.group(1)
            except OSError:
                continue
        return None

    def check_bridge_version(self) -> dict[str, Any] | None:
        """Check if the Unity project's Bridge version matches the plugin version.

        Returns a diagnostic dict if mismatch detected, None if OK.
        """
        detected = self.detect_bridge_version()
        if detected is None:
            return {
                "severity": "warning",
                "code": "BRIDGE_NOT_FOUND",
                "message": "Bridge C# files not found in project. "
                "Deploy with deploy_bridge tool or copy tools/unity/*.cs "
                "to Assets/Editor/PrefabSentinel/",
            }
        from importlib.metadata import version

        plugin_version = version("prefab-sentinel")

        if detected != plugin_version:
            return {
                "severity": "warning",
                "code": "BRIDGE_VERSION_MISMATCH",
                "message": f"Bridge version {detected}, plugin version {plugin_version}. "
                "Use deploy_bridge tool to update.",
                "data": {
                    "bridge_version": detected,
                    "plugin_version": plugin_version,
                    "bridge_update_available": True,
                },
            }
        return None

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

    # Case-insensitive substring matching against script_name_map values
    # and guid_index asset path strings.
    # Note: "liltoon" (lowercase) intentionally omitted — case-insensitive
    # matching makes "lilToon" sufficient.
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

        tree = SymbolTree.build(
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
    # Session management
    # ------------------------------------------------------------------

    @staticmethod
    def _resolve_and_validate_project_root(
        raw_path: str, *, source_label: str = "",
    ) -> Path:
        """Resolve *raw_path* and verify it contains an ``Assets/`` directory.

        Raises :class:`InvalidProjectRootError` when validation fails.
        """
        resolved = Path(to_wsl_path(raw_path)).resolve()
        if not (resolved / "Assets").is_dir():
            context = f" from {source_label}" if source_label else ""
            msg = (
                f"Invalid Unity project root{context}: {resolved} "
                "(Assets/ directory not found)"
            )
            raise InvalidProjectRootError(msg)
        return resolved

    async def activate(self, scope: str, *, project_root: str | None = None) -> dict[str, Any]:
        """Set project scope, warm caches, and start watcher.

        If re-activated with a different scope, stale caches are cleared
        and the watcher is restarted on the new scope path.

        Args:
            scope: Path to the Assets subdirectory to work with.
            project_root: Unity project root directory. Optional.
                Priority: this argument > UNITYTOOL_UNITY_PROJECT_PATH env var
                > auto-detect from scope path.

        Returns the session status dict.
        """
        scope_path = Path(to_wsl_path(scope))

        if project_root is not None:
            resolved_root = self._resolve_and_validate_project_root(project_root)
            if self._project_root is not None and resolved_root != self._project_root:
                self.invalidate_all()
                await self._stop_watcher()
                self._scope = None
            self._project_root = resolved_root
        elif self._project_root is None:
            env_path = os.environ.get(UNITY_PROJECT_PATH_ENV, "").strip()
            if env_path:
                self._project_root = self._resolve_and_validate_project_root(
                    env_path, source_label=UNITY_PROJECT_PATH_ENV,
                )
            else:
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
            "bridge": bridge_status(),
        }

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
