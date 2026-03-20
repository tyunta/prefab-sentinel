"""Common parsers for Unity YAML text assets.

Splits Unity YAML into per-document blocks and extracts GameObject,
Transform, and component-script metadata.  Used by ``udon_wiring``,
``hierarchy``, and ``structure_validator``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

DOCUMENT_HEADER_PATTERN = re.compile(r"^--- !u!(\d+) &(-?\d+)( stripped)?", re.MULTILINE)

# Well-known Unity class IDs
CLASS_ID_GAMEOBJECT = "1"
CLASS_ID_TRANSFORM = "4"
CLASS_ID_RECTTRANSFORM = "224"
CLASS_ID_MONOBEHAVIOUR = "114"

TRANSFORM_CLASS_IDS = frozenset({CLASS_ID_TRANSFORM, CLASS_ID_RECTTRANSFORM})

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
class TransformInfo:
    file_id: str
    game_object_file_id: str
    father_file_id: str
    children_file_ids: list[str]
    local_position: tuple[float, float, float]
    local_rotation: tuple[float, float, float, float]
    local_scale: tuple[float, float, float]
    is_rect_transform: bool


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
    """Extract Transform / RectTransform data from blocks."""
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

        pos = _parse_vector(pos_raw, 3)
        rot = _parse_vector(rot_raw, 4)
        scale = _parse_vector(scale_raw, 3)

        result[block.file_id] = TransformInfo(
            file_id=block.file_id,
            game_object_file_id=go_fid,
            father_file_id=father_fid,
            children_file_ids=children_fids,
            local_position=(pos[0], pos[1], pos[2]),
            local_rotation=(rot[0], rot[1], rot[2], rot[3]),
            local_scale=(scale[0], scale[1], scale[2]),
            is_rect_transform=block.class_id == CLASS_ID_RECTTRANSFORM,
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
                script_guid = script_match.group(1).lower()
                continue
        result[block.file_id] = ComponentInfo(
            file_id=block.file_id,
            class_id=block.class_id,
            game_object_file_id=go_fid,
            script_guid=script_guid,
        )
    return result
