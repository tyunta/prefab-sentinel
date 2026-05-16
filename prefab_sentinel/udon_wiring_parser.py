"""MonoBehaviour field parser — low-level YAML block extraction.

Parses a single MonoBehaviour YAML block and returns a :class:`ComponentWiring`
describing its serialized reference fields.  This module contains no analysis
logic; all diagnostics live in :mod:`prefab_sentinel.udon_wiring`.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from prefab_sentinel.unity_assets import REFERENCE_PATTERN, normalize_guid
from prefab_sentinel.unity_yaml_parser import (
    CLASS_ID_MONOBEHAVIOUR,
    YamlBlock,
)

if TYPE_CHECKING:
    from prefab_sentinel.udon_wiring import ComponentWiring

__all__ = ["UDON_BEHAVIOUR_GUID", "_parse_monobehaviour_fields"]

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# VRChat SDK UdonBehaviour script GUID (com.vrchat.worlds, stable across SDK versions)
UDON_BEHAVIOUR_GUID = "45115577ef41a5b4ca741ed302693907"


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------


def _parse_monobehaviour_fields(block: YamlBlock) -> ComponentWiring | None:
    """Extract fields from a MonoBehaviour block.

    Returns None for non-MonoBehaviour blocks and for UdonBehaviour blocks
    (identified by m_Script GUID matching ``UDON_BEHAVIOUR_GUID``).
    """
    # Deferred import to avoid circular dependency with udon_wiring
    from prefab_sentinel.udon_wiring import SKIP_FIELDS, ComponentWiring, WiringField  # noqa: PLC0415

    if block.class_id != CLASS_ID_MONOBEHAVIOUR:
        return None

    lines = block.text.split("\n")
    game_object_file_id = ""
    script_guid = ""
    is_udon_sharp = False
    backing_udon_file_id = ""
    fields: list[WiringField] = []
    current_field_name: str | None = None
    base_indent: int | None = None

    def _try_append_ref(name: str, value: str, line_num: int) -> None:
        ref_match = REFERENCE_PATTERN.search(value)
        if ref_match:
            fields.append(
                WiringField(
                    name=name,
                    value=value,
                    line=line_num,
                    file_id=ref_match.group(1),
                    guid=normalize_guid(ref_match.group(2) or ""),
                )
            )

    for line_offset, line in enumerate(lines):
        absolute_line = block.start_line + line_offset
        stripped = line.rstrip()
        if not stripped:
            continue
        current_indent = len(line) - len(line.lstrip())

        if base_indent is not None and current_indent > base_indent:
            array_match = re.match(r"^\s+-\s*(\{.*\})", line)
            if array_match and current_field_name:
                _try_append_ref(current_field_name, array_match.group(1).strip(), absolute_line)
            continue

        go_match = re.match(r"\s+m_GameObject:\s*\{fileID:\s*(-?\d+)", line)
        if go_match:
            game_object_file_id = go_match.group(1)
            if base_indent is None:
                base_indent = current_indent
            continue

        script_match = re.match(r"\s+m_Script:\s*\{.*?guid:\s*([0-9a-fA-F]{32})", line)
        if script_match:
            script_guid = normalize_guid(script_match.group(1))
            if base_indent is None:
                base_indent = current_indent
            continue

        udon_sharp_match = re.match(
            r"\s+_udonSharpBackingUdonBehaviour:\s*\{fileID:\s*(-?\d+)",
            line,
        )
        if udon_sharp_match:
            is_udon_sharp = True
            backing_udon_file_id = udon_sharp_match.group(1)
            continue

        field_match = re.match(r"^(\s+)(\w+):\s*(.*)", line)
        if field_match:
            if base_indent is None:
                base_indent = current_indent
            elif current_indent != base_indent:
                continue

            name = field_match.group(2)
            value = field_match.group(3).strip()

            if name in SKIP_FIELDS:
                current_field_name = None
                continue

            current_field_name = name
            _try_append_ref(name, value, absolute_line)
            continue

        array_match = re.match(r"^\s+-\s*(\{.*\})", line)
        if array_match and current_field_name:
            _try_append_ref(current_field_name, array_match.group(1).strip(), absolute_line)

    if script_guid == UDON_BEHAVIOUR_GUID:
        return None

    return ComponentWiring(
        file_id=block.file_id,
        game_object_file_id=game_object_file_id,
        script_guid=script_guid,
        fields=fields,
        block_start_line=block.start_line,
        is_udon_sharp=is_udon_sharp,
        backing_udon_file_id=backing_udon_file_id,
    )
