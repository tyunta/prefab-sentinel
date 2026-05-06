"""On-disk snapshot helper for ``validate_refs`` build-before/after diff (issue #199).

Snapshots persist the broken-reference scan's structured ``data`` payload
under a project-root-hashed temp namespace keyed by a caller-supplied
name.  The ``diff_snapshots`` helper computes the new-broken / resolved /
unchanged-count partition between a saved snapshot and a current scan.

Name validation rejects path separators, parent-directory tokens, and
NUL bytes so a caller cannot escape the temp namespace.

The on-disk root may be overridden via the ``PREFAB_SENTINEL_SNAPSHOT_DIR``
environment variable (used by tests so developer state is not collided).
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from pathlib import Path

SNAPSHOT_DIR_ENV = "PREFAB_SENTINEL_SNAPSHOT_DIR"

# Allowed snapshot-name characters: alphanumerics, hyphen, underscore,
# dot.  Path separators / parent-directory tokens / NUL are rejected.
_NAME_PATTERN = re.compile(r"^[A-Za-z0-9._-]+$")


class SnapshotNameError(ValueError):
    """Raised when a snapshot name contains disallowed path tokens."""


class SnapshotPayloadError(ValueError):
    """Raised when an existing snapshot file is malformed."""


def _validate_name(name: str) -> None:
    if not name:
        raise SnapshotNameError("snapshot name must be non-empty")
    if "\x00" in name:
        raise SnapshotNameError("snapshot name must not contain NUL bytes")
    if "/" in name or "\\" in name:
        raise SnapshotNameError(
            f"snapshot name must not contain path separators: {name!r}"
        )
    if name in {".", ".."}:
        raise SnapshotNameError(
            f"snapshot name must not be a parent-directory token: {name!r}"
        )
    if not _NAME_PATTERN.match(name):
        raise SnapshotNameError(
            f"snapshot name has disallowed characters (allowed: A-Z a-z 0-9 . _ -): {name!r}"
        )


def _root_dir() -> Path:
    override = os.environ.get(SNAPSHOT_DIR_ENV)
    if override:
        return Path(override)
    return Path(tempfile.gettempdir()) / "prefab-sentinel-snapshots"


def _project_namespace(project_root: Path) -> str:
    # Hash the absolute project root so two distinct projects with the
    # same snapshot name do not collide in the shared temp directory.
    canonical = str(project_root.resolve())
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


def snapshot_path(name: str, project_root: Path) -> Path:
    """Return the on-disk file path for ``name`` under ``project_root``."""
    _validate_name(name)
    base = _root_dir() / _project_namespace(project_root)
    return base / f"{name}.json"


def save_snapshot(name: str, scan_data: dict, project_root: Path) -> Path:
    """Persist ``scan_data`` under the named snapshot for the project.

    The payload shape mirrors the ``data`` field of the broken-reference
    scan response (categories counts, top-missing-asset entries with
    optional ``referenced_from``, missing-local-id rows).
    """
    target = snapshot_path(name, project_root)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(scan_data, sort_keys=True), encoding="utf-8")
    return target


def load_snapshot(name: str, project_root: Path) -> dict | None:
    """Return the saved scan-data payload, or None if absent."""
    target = snapshot_path(name, project_root)
    if not target.exists():
        return None
    try:
        loaded = json.loads(target.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SnapshotPayloadError(
            f"snapshot file {target} is not valid JSON: {exc}"
        ) from exc
    if not isinstance(loaded, dict):
        raise SnapshotPayloadError(
            f"snapshot file {target} did not deserialize to a dict (got {type(loaded).__name__})"
        )
    return loaded


def _signature_set(payload: dict) -> set[tuple[str, ...]]:
    """Build the (category, signature) set for a scan-data payload.

    * ``missing_asset`` entries are keyed by the missing GUID.
    * ``missing_local_id`` entries are keyed by ``(target_path, file_id)``.

    The ``top_missing_asset_guids`` and ``missing_local_ids`` fields
    are read when present; absent fields contribute nothing.  Only the
    structural identity matters for the diff — counts are intentionally
    excluded so a re-run that resolves one of two duplicate references
    still surfaces correctly.
    """
    out: set[tuple[str, ...]] = set()
    for entry in payload.get("top_missing_asset_guids", []) or []:
        guid = entry.get("guid")
        if guid:
            out.add(("missing_asset", guid))
    for entry in payload.get("missing_local_ids", []) or []:
        target_path = entry.get("target_path", "")
        file_id = entry.get("file_id", "")
        out.add(("missing_local_id", target_path, file_id))
    return out


def diff_snapshots(prev: dict, current: dict) -> dict:
    """Return the new-broken / resolved / unchanged-count partition.

    The diff partition is keyed on (category, signature) where the
    signature is the missing GUID for missing-asset entries and the
    (target-path, file-id) tuple for missing local IDs.
    """
    prev_keys = _signature_set(prev)
    cur_keys = _signature_set(current)
    new_broken = sorted(cur_keys - prev_keys)
    resolved = sorted(prev_keys - cur_keys)
    unchanged = prev_keys & cur_keys
    return {
        "new_broken": [list(key) for key in new_broken],
        "resolved": [list(key) for key in resolved],
        "unchanged_count": len(unchanged),
    }


__all__ = [
    "SnapshotNameError",
    "SnapshotPayloadError",
    "save_snapshot",
    "load_snapshot",
    "diff_snapshots",
    "snapshot_path",
    "SNAPSHOT_DIR_ENV",
]
