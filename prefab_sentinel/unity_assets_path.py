from __future__ import annotations

import json
import re
import warnings
from pathlib import Path

from prefab_sentinel.json_io import load_json_file
from prefab_sentinel.wsl_compat import to_wsl_path

__all__ = [
    "collect_package_guid_names",
    "has_path_doubling",
    "relative_to_root",
    "resolve_asset_path",
    "resolve_scope_path",
]

_PATH_DOUBLING_RE = re.compile(r"Assets/.*Assets/", re.IGNORECASE)


class ProjectPathEscapeError(ValueError):
    pass


def has_path_doubling(path: str) -> bool:
    """Return True if *path* contains a repeated ``Assets/`` segment.

    Detects CWD-dependent path doubling such as
    ``Assets/Sample/Assets/Sample/Materials/foo.mat``.
    """
    return bool(_PATH_DOUBLING_RE.search(path.replace("\\", "/")))


def resolve_scope_path(scope: str, project_root: Path) -> Path:
    scope_path = Path(to_wsl_path(scope))
    if not scope_path.is_absolute():
        scope_path = project_root / scope_path
    resolved = scope_path.resolve()
    resolved_posix = str(resolved).replace("\\", "/")
    if has_path_doubling(resolved_posix):
        warnings.warn(
            f"Path doubling detected: '{resolved_posix}' contains repeated "
            f"'Assets/' segments. This usually means the scope was already "
            f"project-root-relative but was joined with project_root again.",
            stacklevel=2,
        )
    return resolved


def resolve_asset_path(path: str, project_root: Path | None) -> Path:
    """Resolve asset path, joining project-relative paths with project root.

    Raises:
        ValueError: If the resolved path escapes the project root.
    """
    from prefab_sentinel.wsl_compat import to_wsl_path

    resolved = Path(to_wsl_path(path))
    if project_root is None:
        return resolved

    root_abs = Path(project_root).resolve()
    if not resolved.is_absolute():
        resolved = root_abs / resolved
    resolved_abs = resolved.resolve()
    if not resolved_abs.is_relative_to(root_abs):
        msg = (
            f"Path escapes project root: {path!r} "
            f"resolves to {resolved_abs} which is outside {root_abs}"
        )
        raise ProjectPathEscapeError(msg)
    return resolved_abs


def relative_to_root(path: Path, root: Path) -> str:
    """Return *path* relative to *root* as a POSIX string.

    Falls back to the resolved absolute path when *path* is outside *root*.
    """
    try:
        return path.resolve().relative_to(root).as_posix()
    except ValueError:
        return path.resolve().as_posix()


def _read_json_file(path: Path) -> dict[str, object] | None:
    """Read a JSON file, returning None on failure or non-object content."""
    try:
        data = load_json_file(path)
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    return data


def collect_package_guid_names(project_root: Path) -> dict[str, str]:
    """Map package registry names from Packages/packages-lock.json.

    Returns ``{package_name: package_name}`` for installed packages.
    This provides human-readable context when ``Library/PackageCache`` is
    unavailable (e.g. WSL environments or fresh clones).
    """
    lock_path = project_root / "Packages" / "packages-lock.json"
    lock_data = _read_json_file(lock_path)
    if not lock_data:
        return {}
    deps = lock_data.get("dependencies", {})
    if not isinstance(deps, dict):
        return {}
    return {name: name for name in deps if isinstance(name, str)}
