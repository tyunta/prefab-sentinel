"""Stateful session for MCP server — lifecycle, watcher, bridge detection."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any

from prefab_sentinel.bridge_constants import UNITY_PROJECT_PATH_ENV
from prefab_sentinel.editor_bridge import bridge_status
from prefab_sentinel.session_cache import SessionCacheManager
from prefab_sentinel.unity_assets import find_project_root, resolve_scope_path
from prefab_sentinel.wsl_compat import to_wsl_path

if TYPE_CHECKING:
    from prefab_sentinel.orchestrator import Phase1Orchestrator
    from prefab_sentinel.symbol_tree import SymbolTree

__all__ = ["InvalidProjectRootError", "ProjectSession"]

logger = logging.getLogger(__name__)


class InvalidProjectRootError(FileNotFoundError):
    """Raised when a specified Unity project root lacks an Assets/ directory."""


class ProjectSession:
    """Cross-request state container for the MCP server.

    Owns session lifecycle (activation, watcher, bridge detection)
    and delegates caching to :class:`SessionCacheManager`.
    """

    def __init__(self, project_root: Path | None = None) -> None:
        self._cache = SessionCacheManager()
        self._cache.project_root = project_root
        self._scope: Path | None = None

        self._watcher_task: asyncio.Task[None] | None = None
        self._stop_event: asyncio.Event = asyncio.Event()

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def project_root(self) -> Path | None:
        return self._cache.project_root

    @property
    def scope(self) -> Path | None:
        return self._scope

    def resolve_scope(self, explicit_scope: str | None) -> str | None:
        """Return *explicit_scope* if given, else session scope as str."""
        if explicit_scope is not None:
            return explicit_scope
        return str(self._scope) if self._scope is not None else None

    # ------------------------------------------------------------------
    # Cache delegation
    # ------------------------------------------------------------------

    def get_orchestrator(self) -> Phase1Orchestrator:
        return self._cache.get_orchestrator()

    def script_name_map(self) -> dict[str, str]:
        return self._cache.script_name_map()

    def guid_index(self) -> dict[str, Path]:
        return self._cache.guid_index()

    def get_symbol_tree(
        self,
        path: Path,
        text: str,
        *,
        include_properties: bool = False,
        expand_nested: bool = False,
        guid_to_asset_path: dict[str, Path] | None = None,
    ) -> SymbolTree:
        return self._cache.get_symbol_tree(
            path, text,
            include_properties=include_properties,
            expand_nested=expand_nested,
            guid_to_asset_path=guid_to_asset_path,
        )

    def suggest_reads(self) -> list[str]:
        return self._cache.suggest_reads()

    def invalidate_guid_index(self) -> None:
        self._cache.invalidate_guid_index()

    def invalidate_script_map(self) -> None:
        self._cache.invalidate_script_map()

    def invalidate_symbol_tree(self, path: Path) -> None:
        self._cache.invalidate_symbol_tree(path)

    def invalidate_asset_caches(self, path: Path) -> None:
        self._cache.invalidate_asset_caches(path)

    def invalidate_all(self) -> None:
        self._cache.invalidate_all()

    # ------------------------------------------------------------------
    # Bridge version detection
    # ------------------------------------------------------------------

    _BRIDGE_VERSION_RE = re.compile(r'BridgeVersion\s*=\s*"([^"]+)"')

    def detect_bridge_version(self) -> str | None:
        """Detect the BridgeVersion from the Unity project's Editor bridge files.

        Searches for PrefabSentinel.UnityEditorControlBridge.cs in the project's
        Assets/ directory tree and extracts the BridgeVersion constant.
        Returns None if not found.
        """
        if self._cache.project_root is None:
            return None
        editor_dir = self._cache.project_root / "Assets"
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
            if self._cache.project_root is not None and resolved_root != self._cache.project_root:
                self.invalidate_all()
                await self._stop_watcher()
                self._scope = None
            self._cache.project_root = resolved_root
        elif self._cache.project_root is None:
            env_path = os.environ.get(UNITY_PROJECT_PATH_ENV, "").strip()
            if env_path:
                self._cache.project_root = self._resolve_and_validate_project_root(
                    env_path, source_label=UNITY_PROJECT_PATH_ENV,
                )
            else:
                self._cache.project_root = find_project_root(scope_path)

        new_scope = resolve_scope_path(scope, self._cache.project_root)

        if self._scope is not None and new_scope != self._scope:
            self.invalidate_all()
            await self._stop_watcher()

        self._scope = new_scope

        self.get_orchestrator()
        self.script_name_map()

        self._start_watcher_if_available()

        return self.status()

    def status(self) -> dict[str, Any]:
        """Return current cache diagnostics."""
        result = self._cache.cache_status()
        result["project_root"] = str(self._cache.project_root) if self._cache.project_root else None
        result["scope"] = str(self._scope) if self._scope else None
        result["watcher_running"] = (
            self._watcher_task is not None and not self._watcher_task.done()
        )
        result["bridge"] = bridge_status()
        return result

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
            return

        if not _has_watchfiles():
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
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
