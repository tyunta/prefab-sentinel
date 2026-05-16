"""Common parsers for Unity YAML text assets.

Splits Unity YAML into per-document blocks and extracts GameObject,
Transform, and component-script metadata.  Used by ``udon_wiring``,
``hierarchy``, and ``structure_validator``.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import NamedTuple

from prefab_sentinel.unity_assets import (
    SOURCE_PREFAB_PATTERN,
    decode_text_file,
    normalize_guid,
)
from prefab_sentinel.unity_assets_path import relative_to_root

DOCUMENT_HEADER_PATTERN = re.compile(r"^--- !u!(\d+) &(-?\d+)( stripped)?", re.MULTILINE)

# Well-known Unity class IDs
CLASS_ID_GAMEOBJECT = "1"
CLASS_ID_TRANSFORM = "4"
CLASS_ID_RECTTRANSFORM = "224"
CLASS_ID_MONOBEHAVIOUR = "114"
CLASS_ID_PREFAB_INSTANCE = "1001"

TRANSFORM_CLASS_IDS = frozenset({CLASS_ID_TRANSFORM, CLASS_ID_RECTTRANSFORM})

MAX_NESTED_DEPTH = 10

# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class YamlBlock:
    class_id: str
    file_id: str
    text: str
    start_line: int
    is_stripped: bool = False


@dataclass(slots=True)
class GameObjectInfo:
    file_id: str
    name: str
    component_file_ids: list[str]


@dataclass(slots=True)
class RectTransformAnchor:
    """Anchor metadata carried by a RectTransform block (issue #238).

    Stretched anchors (where ``anchor_min`` and ``anchor_max`` differ on an
    axis) cannot resolve their world-space extent without consulting the
    parent's rect chain; ``size_delta`` on a stretched axis is an inset, not
    a width. The orchestrator surfaces this distinction through
    ``effective_world_size_basis``.
    """

    anchor_min: tuple[float, float]
    anchor_max: tuple[float, float]
    anchored_position: tuple[float, float]
    size_delta: tuple[float, float]
    pivot: tuple[float, float]


@dataclass(slots=True)
class TransformInfo:
    file_id: str
    game_object_file_id: str
    father_file_id: str
    children_file_ids: list[str]
    local_position: tuple[float, float, float]
    local_rotation: tuple[float, float, float, float]
    local_scale: tuple[float, float, float]
    is_rect_transform: bool
    # Issue #238: populated only on RectTransform blocks; remains ``None``
    # for plain Transforms so a serialiser can use presence as the rect
    # discriminator without inspecting ``is_rect_transform`` separately.
    rect_anchor: RectTransformAnchor | None = None


@dataclass(slots=True)
class ComponentInfo:
    """Lightweight metadata for any component block."""

    file_id: str
    class_id: str
    game_object_file_id: str
    script_guid: str


# ---------------------------------------------------------------------------
# Parsers
# ---------------------------------------------------------------------------

_FLOAT_RE = re.compile(r"-?\d+(?:\.\d+)?(?:e[+-]?\d+)?", re.IGNORECASE)


def _parse_vector(raw: str, n: int) -> tuple[float, ...]:
    """Extract *n* floats from a ``{x: ..., y: ..., z: ...}`` string."""
    values = _FLOAT_RE.findall(raw)
    if len(values) >= n:
        return tuple(float(v) for v in values[:n])
    return tuple(0.0 for _ in range(n))


def split_yaml_blocks(text: str) -> list[YamlBlock]:
    """Split Unity YAML text into per-document blocks."""
    if not text.strip():
        return []

    headers: list[tuple[int, str, str, int, bool]] = []
    for match in DOCUMENT_HEADER_PATTERN.finditer(text):
        line_number = text.count("\n", 0, match.start()) + 1
        is_stripped = match.group(3) is not None
        headers.append((match.start(), match.group(1), match.group(2), line_number, is_stripped))

    blocks: list[YamlBlock] = []
    for i, (start, class_id, file_id, start_line, is_stripped) in enumerate(headers):
        end = headers[i + 1][0] if i + 1 < len(headers) else len(text)
        blocks.append(
            YamlBlock(
                class_id=class_id,
                file_id=file_id,
                text=text[start:end],
                start_line=start_line,
                is_stripped=is_stripped,
            )
        )
    return blocks


def get_stripped_file_ids(blocks: list[YamlBlock]) -> frozenset[str]:
    """Return file IDs of all stripped blocks."""
    return frozenset(block.file_id for block in blocks if block.is_stripped)


def parse_game_objects(blocks: list[YamlBlock]) -> dict[str, GameObjectInfo]:
    """Extract GameObject name and component list from blocks."""
    result: dict[str, GameObjectInfo] = {}
    for block in blocks:
        if block.class_id != CLASS_ID_GAMEOBJECT:
            continue
        name = ""
        component_file_ids: list[str] = []
        for line in block.text.split("\n"):
            name_match = re.match(r"\s+m_Name:\s*(.*)", line)
            if name_match:
                name = name_match.group(1).strip()
                continue
            comp_match = re.match(r"\s+-\s*component:\s*\{fileID:\s*(-?\d+)\}", line)
            if comp_match:
                component_file_ids.append(comp_match.group(1))
        result[block.file_id] = GameObjectInfo(
            file_id=block.file_id,
            name=name,
            component_file_ids=component_file_ids,
        )
    return result


def parse_transforms(blocks: list[YamlBlock]) -> dict[str, TransformInfo]:
    """Extract Transform / RectTransform data from blocks.

    Issue #238: RectTransform blocks (class ID 224) additionally emit an
    anchor record carrying ``anchor_min``, ``anchor_max``,
    ``anchored_position``, ``size_delta``, and ``pivot`` for the orchestrator
    to surface; plain Transform blocks leave ``rect_anchor`` unset.
    """
    result: dict[str, TransformInfo] = {}
    for block in blocks:
        if block.class_id not in TRANSFORM_CLASS_IDS:
            continue
        if block.is_stripped:
            continue
        go_fid = ""
        father_fid = ""
        children_fids: list[str] = []
        pos_raw = ""
        rot_raw = ""
        scale_raw = ""
        # Issue #238: raw lines for the five RectTransform anchor fields.
        # Parsed unconditionally for every transform block; only attached
        # to the result when ``class_id`` is the RectTransform class id so
        # plain Transform records do not gain a spurious anchor field.
        anchor_min_raw = ""
        anchor_max_raw = ""
        anchored_position_raw = ""
        size_delta_raw = ""
        pivot_raw = ""
        in_children = False
        for line in block.text.split("\n"):
            stripped = line.rstrip()

            # m_GameObject
            go_match = re.match(r"\s+m_GameObject:\s*\{fileID:\s*(-?\d+)", line)
            if go_match:
                go_fid = go_match.group(1)
                in_children = False
                continue

            # m_Father
            father_match = re.match(r"\s+m_Father:\s*\{fileID:\s*(-?\d+)", line)
            if father_match:
                father_fid = father_match.group(1)
                in_children = False
                continue

            # m_Children start
            if re.match(r"\s+m_Children:", stripped):
                in_children = True
                # Handle inline empty: m_Children: []
                if "[]" in stripped:
                    in_children = False
                continue

            # m_Children element
            if in_children:
                child_match = re.match(r"\s+-\s*\{fileID:\s*(-?\d+)\}", line)
                if child_match:
                    children_fids.append(child_match.group(1))
                    continue
                # Any non-array line exits the children block
                if stripped and not stripped.lstrip().startswith("-"):
                    in_children = False
                    # Fall through to parse this line as a normal field

            # Vectors
            pos_match = re.match(r"\s+m_LocalPosition:\s*(.*)", line)
            if pos_match:
                pos_raw = pos_match.group(1)
                continue
            rot_match = re.match(r"\s+m_LocalRotation:\s*(.*)", line)
            if rot_match:
                rot_raw = rot_match.group(1)
                continue
            scale_match = re.match(r"\s+m_LocalScale:\s*(.*)", line)
            if scale_match:
                scale_raw = scale_match.group(1)
                continue

            # Issue #238: RectTransform anchor fields. Captured for every
            # block because the call site decides whether to attach them
            # based on the block class id; plain Transforms simply leave
            # the captured strings empty since the fields are absent.
            anchor_min_match = re.match(r"\s+m_AnchorMin:\s*(.*)", line)
            if anchor_min_match:
                anchor_min_raw = anchor_min_match.group(1)
                continue
            anchor_max_match = re.match(r"\s+m_AnchorMax:\s*(.*)", line)
            if anchor_max_match:
                anchor_max_raw = anchor_max_match.group(1)
                continue
            anchored_match = re.match(r"\s+m_AnchoredPosition:\s*(.*)", line)
            if anchored_match:
                anchored_position_raw = anchored_match.group(1)
                continue
            size_delta_match = re.match(r"\s+m_SizeDelta:\s*(.*)", line)
            if size_delta_match:
                size_delta_raw = size_delta_match.group(1)
                continue
            pivot_match = re.match(r"\s+m_Pivot:\s*(.*)", line)
            if pivot_match:
                pivot_raw = pivot_match.group(1)
                continue

        pos = _parse_vector(pos_raw, 3)
        rot = _parse_vector(rot_raw, 4)
        scale = _parse_vector(scale_raw, 3)

        is_rect = block.class_id == CLASS_ID_RECTTRANSFORM
        rect_anchor: RectTransformAnchor | None = None
        if is_rect:
            amin = _parse_vector(anchor_min_raw, 2)
            amax = _parse_vector(anchor_max_raw, 2)
            apos = _parse_vector(anchored_position_raw, 2)
            sdel = _parse_vector(size_delta_raw, 2)
            pvt = _parse_vector(pivot_raw, 2)
            rect_anchor = RectTransformAnchor(
                anchor_min=(amin[0], amin[1]),
                anchor_max=(amax[0], amax[1]),
                anchored_position=(apos[0], apos[1]),
                size_delta=(sdel[0], sdel[1]),
                pivot=(pvt[0], pvt[1]),
            )

        result[block.file_id] = TransformInfo(
            file_id=block.file_id,
            game_object_file_id=go_fid,
            father_file_id=father_fid,
            children_file_ids=children_fids,
            local_position=(pos[0], pos[1], pos[2]),
            local_rotation=(rot[0], rot[1], rot[2], rot[3]),
            local_scale=(scale[0], scale[1], scale[2]),
            is_rect_transform=is_rect,
            rect_anchor=rect_anchor,
        )
    return result


def parse_components(blocks: list[YamlBlock]) -> dict[str, ComponentInfo]:
    """Extract lightweight component metadata from all non-GameObject, non-Transform blocks."""
    result: dict[str, ComponentInfo] = {}
    skip = {CLASS_ID_GAMEOBJECT} | TRANSFORM_CLASS_IDS
    for block in blocks:
        if block.class_id in skip:
            continue
        go_fid = ""
        script_guid = ""
        for line in block.text.split("\n"):
            go_match = re.match(r"\s+m_GameObject:\s*\{fileID:\s*(-?\d+)", line)
            if go_match:
                go_fid = go_match.group(1)
                continue
            script_match = re.match(r"\s+m_Script:\s*\{.*?guid:\s*([0-9a-fA-F]{32})", line)
            if script_match:
                script_guid = normalize_guid(script_match.group(1))
                continue
        result[block.file_id] = ComponentInfo(
            file_id=block.file_id,
            class_id=block.class_id,
            game_object_file_id=go_fid,
            script_guid=script_guid,
        )
    return result


# ---------------------------------------------------------------------------
# Nested Prefab traversal
# ---------------------------------------------------------------------------


class NestedPrefabChild(NamedTuple):
    text: str
    path: Path
    rel_posix: str


def iter_nested_prefab_children(
    text: str,
    guid_index: dict[str, Path],
    project_root: Path,
    _depth: int = 0,
) -> Iterator[NestedPrefabChild]:
    """Yield ``(text, path, rel_posix)`` for each nested prefab in *text*, recursively."""
    blocks = split_yaml_blocks(text)
    for block in blocks:
        if block.class_id != CLASS_ID_PREFAB_INSTANCE:
            continue
        source_match = SOURCE_PREFAB_PATTERN.search(block.text)
        if source_match is None:
            continue
        child_guid = normalize_guid(source_match.group(2))
        child_path = guid_index.get(child_guid)
        if child_path is None or not child_path.exists():
            continue
        try:
            child_text = decode_text_file(child_path)
        except (OSError, UnicodeDecodeError):
            continue

        rel = relative_to_root(child_path, project_root)
        yield NestedPrefabChild(child_text, child_path, rel)

        if _depth + 1 < MAX_NESTED_DEPTH:
            yield from iter_nested_prefab_children(
                child_text, guid_index, project_root, _depth=_depth + 1,
            )
