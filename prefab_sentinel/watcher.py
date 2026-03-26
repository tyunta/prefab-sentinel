"""Optional file watcher for session cache invalidation.

Requires the ``watchfiles`` optional dependency::

    pip install prefab-sentinel[watch]

When ``watchfiles`` is not installed, :func:`start_watcher` returns
immediately with a log message.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from prefab_sentinel.session import ProjectSession

__all__ = ["start_watcher"]

logger = logging.getLogger(__name__)

# Extension sets for invalidation dispatch
_META_SUFFIXES = frozenset({".meta"})
_SCRIPT_SUFFIXES = frozenset({".cs"})
_ASSET_SUFFIXES = frozenset({
    ".prefab", ".unity", ".asset", ".mat",
    ".anim", ".controller",
})


def _has_watchfiles() -> bool:
    """Return True if the ``watchfiles`` package is importable."""
    try:
        import watchfiles  # noqa: F401
        return True
    except ImportError:
        return False


async def start_watcher(
    session: ProjectSession,
    watch_path: Path,
    stop_event: asyncio.Event,
) -> None:
    """Watch *watch_path* and dispatch cache invalidation to *session*.

    Runs until *stop_event* is set.  If ``watchfiles`` is not installed
    the function logs a message and returns immediately.
    """
    if not _has_watchfiles():
        logger.info("watchfiles not installed — file watcher disabled")
        return

    if not watch_path.is_dir():
        logger.warning("Watch path does not exist: %s — file watcher disabled", watch_path)
        return

    from watchfiles import awatch

    logger.info("Starting file watcher on %s", watch_path)

    async for changes in awatch(watch_path, stop_event=stop_event, recursive=True):
        dispatch_changes(session, changes)  # type: ignore[arg-type]  # watchfiles.Change is object-compatible


def dispatch_changes(
    session: ProjectSession,
    changes: set[tuple[object, str]],  # watchfiles.Change is a subtype of object
) -> None:
    """Classify *changes* and call the appropriate invalidation methods."""
    meta_changed = False
    cs_changed = False
    asset_paths: list[Path] = []

    for _change_type, path_str in changes:
        p = Path(path_str)
        suffix = p.suffix.lower()
        if suffix in _META_SUFFIXES:
            meta_changed = True
        elif suffix in _SCRIPT_SUFFIXES:
            cs_changed = True
        if suffix in _ASSET_SUFFIXES:
            asset_paths.append(p)

    if meta_changed:
        logger.debug("Meta file changed — invalidating GUID index")
        session.invalidate_guid_index()
    elif cs_changed:
        logger.debug("C# file changed — invalidating script map")
        session.invalidate_script_map()

    for ap in asset_paths:
        logger.debug("Asset changed: %s — invalidating asset caches", ap)
        session.invalidate_asset_caches(ap)
        session.invalidate_symbol_tree(ap)
