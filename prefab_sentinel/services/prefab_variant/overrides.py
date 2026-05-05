"""Override parsing helpers for Prefab Variant analysis.

Isolates the m_Modifications parser, override entry dataclass, regex
patterns, and base-prefab property value extraction.  Kept free of any
service-level orchestration so the parsing can be exercised by the
chain-walk and stale-override helpers independently.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass

from prefab_sentinel.unity_assets import normalize_guid
from prefab_sentinel.unity_yaml_parser import split_yaml_blocks

OVERRIDE_TARGET_PATTERN = re.compile(
    r"target:\s*\{fileID:\s*(-?\d+)(?:,\s*guid:\s*([0-9a-fA-F]{32}))?(?:,\s*type:\s*(-?\d+))?\}"
)
ARRAY_SIZE_PATH_PATTERN = re.compile(r"^(?P<prefix>.+)\.Array\.size$")
ARRAY_DATA_PATH_PATTERN = re.compile(r"^(?P<prefix>.+)\.Array\.data\[(?P<index>\d+)\]$")

# Pattern to detect the start of a modification entry (``- target: {...}``)
MOD_ENTRY_START = re.compile(r"^\s+-\s*target:\s*\{")


@dataclass(slots=True)
class OverrideEntry:
    target_file_id: str
    target_guid: str
    target_type: str | None
    target_raw: str
    property_path: str
    value: str
    object_reference: str
    line: int

    @property
    def target_key(self) -> str:
        return f"{self.target_guid}:{self.target_file_id}"

    @property
    def kind(self) -> str:
        """Discriminant string identifying the shape of this override.

        Why a derived property instead of a stored field: the four kinds
        are deterministic functions of the already-parsed
        ``property_path`` and ``object_reference`` fields, so a stored
        discriminant would either duplicate the parser logic or risk
        drift between the field and the underlying values.
        """
        if ARRAY_SIZE_PATH_PATTERN.match(self.property_path):
            return "array_size"
        if ARRAY_DATA_PATH_PATTERN.match(self.property_path):
            return "array_data"
        ref = self.object_reference
        if ref and ref != "{fileID: 0}":
            return "object_reference"
        return "value"


def effective_value(entry: OverrideEntry) -> str:
    """Return the effective value of an override entry.

    Unity stores object references in objectReference; when it is empty or
    ``{fileID: 0}`` the plain ``value`` field is the effective value instead.
    """
    ref = entry.object_reference
    return ref if ref and ref != "{fileID: 0}" else entry.value


def parse_overrides(text: str) -> list[OverrideEntry]:
    """Extract ``m_Modifications`` entries from a Variant YAML text."""
    lines = text.splitlines()
    entries: list[OverrideEntry] = []
    in_modifications = False
    mod_indent = 0
    current: OverrideEntry | None = None

    for index, line in enumerate(lines, start=1):
        stripped = line.strip()
        indent = len(line) - len(line.lstrip(" "))

        if stripped.endswith("m_Modifications:"):
            in_modifications = True
            mod_indent = indent
            if current is not None:
                entries.append(current)
                current = None
            continue

        if in_modifications and stripped and indent <= mod_indent and not stripped.startswith("-"):
            in_modifications = False
            if current is not None:
                entries.append(current)
                current = None

        if not in_modifications:
            continue

        if stripped.startswith("- target:") or stripped.startswith("target:"):
            if current is not None:
                entries.append(current)
            target_match = OVERRIDE_TARGET_PATTERN.search(stripped)
            target_file_id = ""
            target_guid = ""
            target_type: str | None = None
            if target_match:
                target_file_id = target_match.group(1)
                target_guid = normalize_guid(target_match.group(2) or "")
                target_type = target_match.group(3)
            current = OverrideEntry(
                target_file_id=target_file_id,
                target_guid=target_guid,
                target_type=target_type,
                target_raw=stripped.split("target:", 1)[-1].strip(),
                property_path="",
                value="",
                object_reference="",
                line=index,
            )
            continue

        if current is None:
            continue

        if stripped.startswith("propertyPath:"):
            current.property_path = stripped.split(":", 1)[1].strip()
        elif stripped.startswith("value:"):
            current.value = stripped.split(":", 1)[1].strip()
        elif stripped.startswith("objectReference:"):
            current.object_reference = stripped.split(":", 1)[1].strip()

    if current is not None:
        entries.append(current)

    return entries


def find_modification_line_ranges(
    lines: list[str],
) -> dict[int, tuple[int, int]]:
    """Map each override entry (by its 1-based ``- target:`` line) to a 0-based [start, end) range.

    Each modification entry in Unity YAML spans from its ``- target:`` line
    until the next ``- target:`` line or the end of the ``m_Modifications``
    block.
    """
    # Collect all ``- target:`` start positions (0-based indices)
    entry_starts: list[int] = []
    in_modifications = False
    mod_indent = 0

    for idx, line in enumerate(lines):
        stripped = line.strip()
        indent = len(line) - len(line.lstrip(" "))

        if stripped.endswith("m_Modifications:"):
            in_modifications = True
            mod_indent = indent
            continue

        if in_modifications and stripped and indent <= mod_indent and not stripped.startswith("-"):
            in_modifications = False
            continue

        if not in_modifications:
            continue

        if MOD_ENTRY_START.match(line):
            entry_starts.append(idx)

    # Build ranges: each entry runs from its start to the next entry start
    # (or end of the entries list)
    ranges: dict[int, tuple[int, int]] = {}
    for i, start_idx in enumerate(entry_starts):
        if i + 1 < len(entry_starts):
            end_idx = entry_starts[i + 1]
        else:
            # Last entry: find where the block ends
            end_idx = start_idx + 1
            for j in range(start_idx + 1, len(lines)):
                line = lines[j]
                stripped = line.strip()
                if not stripped:
                    end_idx = j + 1
                    continue
                # If we hit another ``- target:``, stop before it
                if MOD_ENTRY_START.match(line):
                    end_idx = j
                    break
                # Include property continuation lines
                if stripped.startswith(("propertyPath:", "value:", "objectReference:")):
                    end_idx = j + 1
                else:
                    # We've left the entry
                    end_idx = j
                    break

        # Map the 1-based line number to the range
        line_1based = start_idx + 1
        ranges[line_1based] = (start_idx, end_idx)

    return ranges


def iter_base_property_values(base_text: str) -> Iterator[tuple[str, str, str]]:
    """Yield ``(file_id, property_path, value)`` from a base prefab.

    Best-effort extraction of property values from YAML component blocks.
    Array elements written as ``m_Foo`` list items are mapped to
    ``m_Foo.Array.data[N]`` paths.
    """
    blocks = split_yaml_blocks(base_text)
    for block in blocks:
        file_id = block.file_id
        lines = block.text.split("\n")
        current_array_field: str | None = None
        array_index = 0

        for line in lines:
            stripped = line.strip()

            if stripped.startswith("---") or not stripped:
                current_array_field = None
                continue

            if stripped.startswith("- ") and current_array_field is not None:
                item_value = stripped[2:].strip()
                prop_path = f"{current_array_field}.Array.data[{array_index}]"
                yield (file_id, prop_path, item_value)
                array_index += 1
                continue

            if not stripped.startswith("- "):
                current_array_field = None

            if ":" in stripped:
                field_part, _, value_part = stripped.partition(":")
                field_name = field_part.strip()
                value_raw = value_part.strip()

                if not value_raw and not field_name.startswith("m_"):
                    continue

                if not value_raw and field_name.startswith("m_"):
                    current_array_field = field_name
                    array_index = 0
                    continue

                if value_raw == "[]":
                    continue

                yield (file_id, field_name, value_raw)
