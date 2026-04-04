from __future__ import annotations

import bisect
import os
import re
from dataclasses import dataclass
from pathlib import Path

from prefab_sentinel.wsl_compat import to_wsl_path

UNITY_TEXT_ASSET_SUFFIXES = {
    ".prefab",
    ".unity",
    ".asset",
    ".mat",
    ".anim",
    ".controller",
    ".overridecontroller",
    ".playable",
    ".mask",
    ".flare",
    ".physicmaterial",
}

# File types that contain GameObject/Transform hierarchy and MonoBehaviour wiring
GAMEOBJECT_BEARING_SUFFIXES = frozenset({".prefab", ".unity", ".asset"})

GUID_PATTERN = re.compile(r"\bguid:\s*([0-9a-fA-F]{32})\b")
LOCAL_FILE_ID_PATTERN = re.compile(r"^--- !u!\d+ &(-?\d+)", re.MULTILINE)
REFERENCE_PATTERN = re.compile(
    r"\{fileID:\s*(-?\d+)(?:,\s*guid:\s*([0-9a-fA-F]{32}))?(?:,\s*type:\s*(-?\d+))?\}"
)
SOURCE_PREFAB_PATTERN = re.compile(
    r"m_(?:SourcePrefab|ParentPrefab):\s*\{fileID:\s*(-?\d+),\s*guid:\s*([0-9a-fA-F]{32}),\s*type:\s*(-?\d+)\}"
)

UNITY_DEFAULT_RESOURCES_GUID = "0000000000000000e000000000000000"
UNITY_BUILTIN_EXTRA_GUID = "0000000000000000f000000000000000"

UNITY_BUILTIN_GUIDS = {
    UNITY_DEFAULT_RESOURCES_GUID,
    UNITY_BUILTIN_EXTRA_GUID,
}

DEFAULT_EXCLUDED_DIR_NAMES = {
    ".git",
    ".hg",
    ".svn",
    "library",
    "logs",
    "temp",
    "obj",
}


@dataclass(slots=True)
class ReferenceMatch:
    file_id: str
    guid: str
    ref_type: str | None
    line: int
    column: int
    raw: str


def decode_text_file(path: Path) -> str:
    raw = path.read_bytes()
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("cp932")


def looks_like_guid(value: str) -> bool:
    return bool(re.fullmatch(r"[0-9a-fA-F]{32}", value))


def normalize_guid(value: str) -> str:
    return value.strip().lower()


def is_unity_builtin_guid(value: str) -> bool:
    return normalize_guid(value) in UNITY_BUILTIN_GUIDS


def is_unity_text_asset(path: Path) -> bool:
    return path.suffix.lower() in UNITY_TEXT_ASSET_SUFFIXES


def extract_meta_guid(meta_path: Path) -> str | None:
    text = decode_text_file(meta_path)
    match = GUID_PATTERN.search(text)
    if not match:
        return None
    return normalize_guid(match.group(1))


def extract_local_file_ids(text: str) -> set[str]:
    return {match.group(1) for match in LOCAL_FILE_ID_PATTERN.finditer(text)}


def iter_references(text: str, include_location: bool = True) -> list[ReferenceMatch]:
    refs: list[ReferenceMatch] = []
    line_starts: list[int] | None = None
    if include_location:
        line_starts = [0]
        for idx, ch in enumerate(text):
            if ch == "\n":
                line_starts.append(idx + 1)

    for match in REFERENCE_PATTERN.finditer(text):
        if include_location and line_starts is not None:
            start = match.start()
            line_idx = bisect.bisect_right(line_starts, start) - 1
            line = line_idx + 1
            column = start - line_starts[line_idx] + 1
        else:
            line = 0
            column = 0

        refs.append(
            ReferenceMatch(
                file_id=match.group(1),
                guid=normalize_guid(match.group(2) or ""),
                ref_type=match.group(3),
                line=line,
                column=column,
                raw=match.group(0),
            )
        )
    return refs


PACKAGE_CACHE_REL = Path("Library") / "PackageCache"


def _extract_guid_safe(meta_path: Path) -> str | None:
    """Extract GUID from .meta file, returning None on any read failure."""
    try:
        return extract_meta_guid(meta_path)
    except (UnicodeDecodeError, OSError):
        return None


def _scan_meta_files(scan_root: Path, excluded: set[str], index: dict[str, Path]) -> None:
    from concurrent.futures import ThreadPoolExecutor, as_completed

    # Phase 1: collect .meta paths (sequential)
    meta_paths: list[Path] = []
    for root, dirnames, filenames in os.walk(scan_root):
        dirnames[:] = [dirname for dirname in dirnames if dirname.lower() not in excluded]
        for filename in filenames:
            if filename.lower().endswith(".meta"):
                meta_paths.append(Path(root) / filename)

    if not meta_paths:
        return

    # Phase 2: parallel GUID extraction
    with ThreadPoolExecutor(
        max_workers=min(10, len(meta_paths)),
    ) as pool:
        futures = {
            pool.submit(_extract_guid_safe, p): p for p in meta_paths
        }
        for future in as_completed(futures):
            path = futures[future]
            guid = future.result()
            if guid:
                index[guid] = path.with_suffix("")


def collect_project_guid_index(
    project_root: Path,
    excluded_dir_names: set[str] | None = None,
    *,
    include_package_cache: bool = True,
) -> dict[str, Path]:
    excluded = {
        name.lower() for name in (excluded_dir_names or DEFAULT_EXCLUDED_DIR_NAMES)
    }
    index: dict[str, Path] = {}
    _scan_meta_files(project_root, excluded, index)

    if include_package_cache:
        pkg_cache = project_root / PACKAGE_CACHE_REL
        if pkg_cache.is_dir():
            _scan_meta_files(pkg_cache, set(), index)

    return index


def find_project_root(start: Path) -> Path:
    current = Path(to_wsl_path(str(start))).resolve()
    if current.is_file():
        current = current.parent
    for candidate in [current, *current.parents]:
        if (candidate / "Assets").exists():
            return candidate
    return current


def resolve_guid_to_asset_name(
    guid: str,
    guid_index: dict[str, Path],
    project_root: Path | None = None,
) -> str:
    """Best-effort GUID→human-readable asset name resolution.

    Resolution order:
    1. GUID index (meta file scan) → relative asset path
    2. Empty string if unresolvable
    """
    asset_path = guid_index.get(normalize_guid(guid))
    if asset_path is None:
        return ""
    if project_root is not None:
        try:
            return asset_path.resolve().relative_to(project_root.resolve()).as_posix()
        except ValueError:
            pass
    return asset_path.as_posix()
