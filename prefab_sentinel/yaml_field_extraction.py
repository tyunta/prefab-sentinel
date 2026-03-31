"""YAML block field extraction and scalar value parsing.

Extracts ``(property_path, raw_value)`` pairs from Unity YAML component
blocks and converts raw value strings into typed Python values for use
in patch plans.
"""

from __future__ import annotations

import re
from typing import Any

_FLOW_MAPPING_RE = re.compile(r"\{(.+)\}")
_BASE_INDENT = 2


def extract_block_fields(block_text: str) -> list[tuple[str, str]]:
    """Extract ``(property_path, raw_value)`` pairs from a single YAML block.

    Handles simple scalars, flow mappings, and array elements with
    ``Array.data[N]`` paths.  Uses next-line lookahead to distinguish
    array headers from nested sub-object headers for fields without
    the ``m_`` prefix.
    """
    lines = block_text.split("\n")
    result: list[tuple[str, str]] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        i += 1

        if not stripped or stripped.startswith("---") or stripped.startswith("- "):
            continue

        # Top-level fields have exactly _BASE_INDENT spaces of indent.
        # Type header lines (e.g. "MeshRenderer:") have zero indent — skip.
        indent = len(line) - len(line.lstrip())
        if indent != _BASE_INDENT:
            continue

        if ":" not in stripped:
            continue

        field_name, _, value_raw = stripped.partition(":")
        field_name = field_name.strip()
        value_raw = value_raw.strip()

        if value_raw == "[]":
            continue

        if value_raw:
            result.append((field_name, value_raw))
            continue

        # Empty value: lookahead to decide array vs sub-object
        next_idx = _next_non_empty_line_index(lines, i)
        if next_idx is not None and lines[next_idx].strip().startswith("- "):
            array_idx = 0
            j = next_idx
            while j < len(lines):
                item_stripped = lines[j].strip()
                if not item_stripped:
                    j += 1
                    continue
                if not item_stripped.startswith("- "):
                    break
                item_value = item_stripped[2:].strip()
                result.append((f"{field_name}.Array.data[{array_idx}]", item_value))
                array_idx += 1
                j += 1
            i = j
        # else: nested sub-object header — skip

    return result


def _next_non_empty_line_index(lines: list[str], start: int) -> int | None:
    """Return index of the next non-empty line, or None."""
    for idx in range(start, len(lines)):
        if lines[idx].strip():
            return idx
    return None


def parse_yaml_scalar(raw: str) -> int | float | str | dict[str, Any]:
    """Convert a raw YAML value string into a typed Python value.

    Parses integers, floats, flow mappings, and falls back to string.
    """
    if not raw:
        return ""

    flow_match = _FLOW_MAPPING_RE.fullmatch(raw)
    if flow_match:
        return _parse_flow_mapping(flow_match.group(1))

    try:
        return int(raw)
    except ValueError:
        pass

    try:
        return float(raw)
    except ValueError:
        pass

    return raw


def _parse_flow_mapping(inner: str) -> dict[str, Any]:
    """Parse the interior of a YAML flow mapping into a dict."""
    result: dict[str, Any] = {}
    for pair in inner.split(","):
        pair = pair.strip()
        if ":" not in pair:
            continue
        key, _, val = pair.partition(":")
        val = val.strip()
        try:
            typed_val: Any = int(val)
        except ValueError:
            try:
                typed_val = float(val)
            except ValueError:
                typed_val = val
        result[key.strip()] = typed_val
    return result
