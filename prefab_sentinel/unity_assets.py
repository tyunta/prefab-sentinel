from __future__ import annotations

import bisect
import os
import re
from dataclasses import dataclass
from pathlib import Path


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

GUID_PATTERN = re.compile(r"\bguid:\s*([0-9a-fA-F]{32})\b")
LOCAL_FILE_ID_PATTERN = re.compile(r"^--- !u!\d+ &(-?\d+)", re.MULTILINE)
REFERENCE_PATTERN = re.compile(
    r"\{fileID:\s*(-?\d+)(?:,\s*guid:\s*([0-9a-fA-F]{32}))?(?:,\s*type:\s*(-?\d+))?\}"
)
SOURCE_PREFAB_PATTERN = re.compile(
    r"m_(?:SourcePrefab|ParentPrefab):\s*\{fileID:\s*(-?\d+),\s*guid:\s*([0-9a-fA-F]{32}),\s*type:\s*(-?\d+)\}"
)

UNITY_BUILTIN_GUIDS = {
    "0000000000000000e000000000000000",
    "0000000000000000f000000000000000",
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


def collect_project_guid_index(
    project_root: Path,
    excluded_dir_names: set[str] | None = None,
) -> dict[str, Path]:
    excluded = {
        name.lower() for name in (excluded_dir_names or DEFAULT_EXCLUDED_DIR_NAMES)
    }
    index: dict[str, Path] = {}
    for root, dirnames, filenames in os.walk(project_root):
        dirnames[:] = [dirname for dirname in dirnames if dirname.lower() not in excluded]
        for filename in filenames:
            if not filename.lower().endswith(".meta"):
                continue
            meta = Path(root) / filename
            try:
                guid = extract_meta_guid(meta)
            except UnicodeDecodeError:
                continue
            if not guid:
                continue
            asset_path = meta.with_suffix("")
            index[guid] = asset_path
    return index


def find_project_root(start: Path) -> Path:
    current = start.resolve()
    if current.is_file():
        current = current.parent
    for candidate in [current, *current.parents]:
        if (candidate / "Assets").exists():
            return candidate
    return current


def resolve_scope_path(scope: str, project_root: Path) -> Path:
    scope_path = Path(scope)
    if not scope_path.is_absolute():
        scope_path = project_root / scope_path
    return scope_path.resolve()
