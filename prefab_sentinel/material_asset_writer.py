"""Material asset (.mat) property writer for Unity material files.

Handles in-place mutation of property values in .mat YAML text.
Inspection (parsing) lives in ``material_asset_inspector.py``;
this module focuses solely on writing.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path

from prefab_sentinel.contracts import error_dict as _error_dict, success_dict as _success_dict
from prefab_sentinel.fuzzy_match import suggest_similar
from prefab_sentinel.json_io import load_json
from prefab_sentinel.material_asset_inspector import (
    _find_property,
    _list_all_property_names,
)

__all__ = ["write_material_property"]

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Internal helpers — section-scoped regex operations
# ---------------------------------------------------------------------------


def _section_span(text: str, section_name: str) -> tuple[int, int] | None:
    """Return ``(start, end)`` byte offsets of a section in *text*."""
    pattern = re.compile(
        rf"^(\s{{4}}{re.escape(section_name)}:\s*\n)(.*?)(?=^\s{{4}}m_\w+:|\Z)",
        re.MULTILINE | re.DOTALL,
    )
    m = pattern.search(text)
    if m is None:
        return None
    # Return span covering the header plus body.
    return m.start(), m.end()


def _replace_in_section(
    text: str,
    section_name: str,
    pattern: re.Pattern[str],
    replacement: str,
) -> str:
    """Apply a regex substitution restricted to *section_name* only.

    This avoids accidental matches in other sections (e.g. an int entry
    that also matches the float pattern).
    """
    span = _section_span(text, section_name)
    if span is None:
        raise ValueError(f"Section {section_name} not found")
    start, end = span
    section_text = text[start:end]
    new_section = pattern.sub(replacement, section_text, count=1)
    return text[:start] + new_section + text[end:]


def _replace_property(
    text: str,
    property_name: str,
    value: str,
    category: str,
) -> str:
    """Replace a property value in the full .mat text."""
    if category == "float":
        try:
            float(value)
        except ValueError:
            raise ValueError(f"Invalid float value: {value}") from None
        pattern = re.compile(
            rf"(- {re.escape(property_name)}:\s*)[\d.e+-]+",
        )
        return _replace_in_section(text, "m_Floats", pattern, rf"\g<1>{value}")

    if category == "int":
        try:
            int(value)
        except ValueError:
            raise ValueError(f"Invalid int value: {value}") from None
        pattern = re.compile(
            rf"(- {re.escape(property_name)}:\s*)-?\d+",
        )
        return _replace_in_section(text, "m_Ints", pattern, rf"\g<1>{value}")

    if category == "color":
        try:
            parts = load_json(value)
            if not isinstance(parts, list) or len(parts) != 4:
                raise ValueError("Color must be [r, g, b, a]")
            r, g, b, a = (float(x) for x in parts)
        except (ValueError, TypeError, json.JSONDecodeError) as exc:
            raise ValueError(f"Invalid color value '{value}': {exc}") from None
        new_val = f"{{r: {r}, g: {g}, b: {b}, a: {a}}}"
        pattern = re.compile(
            rf"(- {re.escape(property_name)}:\s*)"
            r"\{r:\s*[\d.e+-]+,\s*g:\s*[\d.e+-]+,\s*b:\s*[\d.e+-]+,\s*a:\s*[\d.e+-]+\}",
        )
        return _replace_in_section(text, "m_Colors", pattern, rf"\g<1>{new_val}")

    if category == "texture":
        if value == "":
            new_texture = "m_Texture: {fileID: 0}"
        elif value.startswith("guid:"):
            guid = value[5:]
            new_texture = f"m_Texture: {{fileID: 2800000, guid: {guid}, type: 3}}"
        else:
            raise ValueError(
                f"Texture value must be 'guid:<hex>' or empty, got: {value}",
            )
        # Within the named tex entry, replace the m_Texture line.
        pattern = re.compile(
            rf"(- {re.escape(property_name)}:\s*\n\s+)m_Texture:\s*\{{[^}}]+\}}",
        )
        return _replace_in_section(text, "m_TexEnvs", pattern, rf"\g<1>{new_texture}")

    raise ValueError(f"Unknown category: {category}")


# ---------------------------------------------------------------------------
# Public writer
# ---------------------------------------------------------------------------


def write_material_property(
    target_path: str,
    property_name: str,
    value: str,
    *,
    dry_run: bool = True,
) -> dict:
    """Write a single property value in a ``.mat`` file.

    Args:
        target_path: Path to the .mat file.
        property_name: Property name (e.g. ``_Glossiness``).
        value: New value as string.  Format depends on the property type:

            * Float / Int: ``"0.5"`` or ``"128"``
            * Color: ``"[r, g, b, a]"``  (JSON array)
            * Texture: ``"guid:abc..."`` to set, or ``""`` to null-out.

        dry_run: If *True*, return a preview without writing.

    Returns:
        Envelope dict with ``success``, ``severity``, ``code``, ``message``,
        ``data``, ``diagnostics``.
    """
    path = Path(target_path)

    if path.suffix.lower() != ".mat":
        return _error_dict(
            "MAT_PROP_WRONG_EXT",
            f"Expected .mat file, got {path.suffix}",
        )

    if not path.exists():
        return _error_dict(
            "MAT_PROP_FILE_NOT_FOUND",
            f"File not found: {target_path}",
        )

    text = path.read_text(encoding="utf-8")

    category, before, section_name = _find_property(text, property_name)
    if category is None:
        all_names = _list_all_property_names(text)
        suggestions = suggest_similar(property_name, all_names)
        return _error_dict(
            "MAT_PROP_NOT_FOUND",
            f"Property '{property_name}' not found in {path.name}",
            data={"available_properties": all_names, "suggestions": suggestions},
            diagnostics=[{"detail": f"Available: {', '.join(all_names)}"}],
        )

    data = {
        "asset_path": target_path,
        "property_name": property_name,
        "category": section_name,
        "before": str(before),
        "after": value,
    }

    if dry_run:
        return _success_dict(
            "MAT_PROP_DRY_RUN",
            f"Would change {property_name} from {before} to {value}",
            data=data,
        )

    try:
        new_text = _replace_property(text, property_name, value, category)
    except ValueError as exc:
        return _error_dict("MAT_PROP_PARSE_ERROR", str(exc))

    path.write_text(new_text, encoding="utf-8")

    # Verify the write by re-parsing.
    verify_cat, _verify_val, _ = _find_property(new_text, property_name)
    if verify_cat is None:
        return _error_dict(
            "MAT_PROP_VERIFY_FAILED",
            "Property disappeared after write",
        )

    return _success_dict(
        "MAT_PROP_APPLIED",
        f"Changed {property_name} from {before} to {value}",
        data=data,
    )
