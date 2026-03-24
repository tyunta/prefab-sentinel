"""Regex-based C# serialized field parser for Unity scripts.

Extracts field definitions from C# source code to enable property path
validation, rename impact analysis, and field coverage checking.

Does NOT require a full C# AST — uses regex patterns sufficient for
Unity's serialization rules:

- ``public`` fields are serialized unless marked ``[NonSerialized]``
- ``private``/``protected`` fields with ``[SerializeField]`` are serialized
- ``static``, ``const``, ``readonly`` fields are never serialized
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

__all__ = [
    "CSharpField",
    "build_field_map",
    "parse_serialized_fields",
    "resolve_script_fields",
]

# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class CSharpField:
    """A serialized field extracted from C# source."""

    name: str
    """Field identifier (e.g., ``moveSpeed``)."""

    type_name: str
    """C# type (e.g., ``float``, ``GameObject``, ``List<int>``)."""

    is_serialized: bool
    """True if Unity will serialize this field."""

    is_public: bool
    """True if access modifier is ``public``."""

    line: int
    """1-based line number in source."""

    attributes: list[str] = field(default_factory=list)
    """Collected attributes (e.g., ``["Header(\\"Movement\\")", "Range(0, 100)"]``)."""

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "name": self.name,
            "type_name": self.type_name,
            "is_serialized": self.is_serialized,
            "is_public": self.is_public,
            "line": self.line,
        }
        if self.attributes:
            result["attributes"] = list(self.attributes)
        return result


# ---------------------------------------------------------------------------
# Regex patterns
# ---------------------------------------------------------------------------

# Matches [AttributeName] or [AttributeName(...)]
_ATTRIBUTE_RE = re.compile(
    r"\[\s*(\w+(?:\s*\(.*?\))?)\s*\]"
)

# Matches a field declaration line:
#   optional_access type_name field_name ;  or  = initializer;
# Captures: (access_modifier, storage_modifiers, type_name, field_name)
_FIELD_RE = re.compile(
    r"^\s*"
    r"(?:(public|private|protected|internal)\s+)?"
    r"((?:(?:static|const|readonly|volatile|new|override|virtual|abstract|sealed|extern)\s+)*)"
    r"([\w][\w<>\[\],\s\?]*?)\s+"
    r"(\w+)\s*[;=]"
)

_NONSERIALIZED_NAMES = frozenset({"NonSerialized", "System.NonSerialized"})
_SERIALIZEFIELD_NAMES = frozenset({"SerializeField", "UnityEngine.SerializeField"})
_STORAGE_EXCLUDES = frozenset({"static", "const", "readonly"})

# C# keywords that can appear before an identifier + semicolon but are not types
_NON_TYPE_KEYWORDS = frozenset({
    "using", "namespace", "class", "struct", "interface", "enum",
    "return", "throw", "break", "continue", "goto", "yield",
    "if", "else", "for", "foreach", "while", "do", "switch", "case",
    "try", "catch", "finally", "lock", "checked", "unchecked",
})

# Detect C# property (has { get or { set) or method (has parentheses before ;)
_PROPERTY_RE = re.compile(r"\{\s*get\b|\{\s*set\b")
_METHOD_PAREN_RE = re.compile(r"\w+\s*\(")


# ---------------------------------------------------------------------------
# Core parser
# ---------------------------------------------------------------------------


def parse_serialized_fields(source: str) -> list[CSharpField]:
    """Extract serialized fields from C# source text.

    Parses field declarations using regex, applying Unity's serialization
    rules.  Methods, properties, constants, static fields, and readonly
    fields are excluded.

    Args:
        source: C# source code text.

    Returns:
        List of ``CSharpField`` instances for all serialized fields.
    """
    results: list[CSharpField] = []
    pending_attrs: list[str] = []

    for line_num, raw_line in enumerate(source.splitlines(), start=1):
        line = raw_line.strip()

        if not line or line.startswith("//") or line.startswith("/*"):
            continue

        # Collect attributes from this line
        attr_matches = _ATTRIBUTE_RE.findall(line)
        if attr_matches:
            pending_attrs.extend(attr_matches)

        # Strip attributes to get the declaration portion
        stripped = _ATTRIBUTE_RE.sub("", line).strip()
        if not stripped:
            # Line was only attributes — keep pending for next line
            continue

        # Try field declaration match on the attribute-stripped text
        m = _FIELD_RE.match(stripped)
        if not m:
            # Not a field line — reset accumulated attributes
            pending_attrs.clear()
            continue

        access = m.group(1) or ""
        storage_raw = m.group(2).strip()
        type_name = m.group(3).strip()
        field_name = m.group(4)

        # Skip C# keywords that look like type+name patterns
        if type_name in _NON_TYPE_KEYWORDS:
            pending_attrs.clear()
            continue

        # Skip if this looks like a method or property
        if _METHOD_PAREN_RE.search(type_name):
            pending_attrs.clear()
            continue
        if _PROPERTY_RE.search(stripped):
            pending_attrs.clear()
            continue

        # Check storage modifiers
        storage_mods = set(storage_raw.split()) if storage_raw else set()
        if storage_mods & _STORAGE_EXCLUDES:
            pending_attrs.clear()
            continue

        # Determine serialization
        attr_names = {a.split("(")[0].strip() for a in pending_attrs}
        has_serialize_field = bool(attr_names & _SERIALIZEFIELD_NAMES)
        has_nonserialized = bool(attr_names & _NONSERIALIZED_NAMES)
        is_public = access == "public"
        is_serialized = (
            (is_public and not has_nonserialized) or has_serialize_field
        )

        results.append(
            CSharpField(
                name=field_name,
                type_name=type_name,
                is_serialized=is_serialized,
                is_public=is_public,
                line=line_num,
                attributes=list(pending_attrs),
            )
        )
        pending_attrs.clear()

    return results


# ---------------------------------------------------------------------------
# Project-wide utilities
# ---------------------------------------------------------------------------


def build_field_map(project_root: Path) -> dict[str, list[CSharpField]]:
    """Build a mapping from script GUID to serialized fields.

    Scans the project for ``.cs`` files, parses each for serialized fields,
    and maps via the companion ``.cs.meta`` GUID.

    Args:
        project_root: Unity project root directory.

    Returns:
        Dict mapping lowercase GUID strings to lists of ``CSharpField``.
    """
    from prefab_sentinel.unity_assets import collect_project_guid_index

    guid_index = collect_project_guid_index(project_root, include_package_cache=False)
    result: dict[str, list[CSharpField]] = {}
    for guid, asset_path in guid_index.items():
        if asset_path.suffix != ".cs":
            continue
        try:
            source = asset_path.read_text(encoding="utf-8-sig")
        except (OSError, UnicodeDecodeError):
            continue
        fields = parse_serialized_fields(source)
        serialized = [f for f in fields if f.is_serialized]
        if serialized:
            result[guid] = serialized
    return result


def resolve_script_fields(
    script_path_or_guid: str,
    project_root: Path | None = None,
) -> tuple[str, Path, list[CSharpField]]:
    """Resolve a script identifier to (guid, cs_path, fields).

    Accepts either a ``.cs`` file path or a 32-char GUID string.
    GUID resolution requires ``project_root``.

    Args:
        script_path_or_guid: ``.cs`` file path or 32-char GUID.
        project_root: Unity project root (required for GUID resolution).

    Returns:
        Tuple of (guid, cs_path, list_of_all_fields).

    Raises:
        FileNotFoundError: If the script cannot be found.
        ValueError: If GUID resolution is requested without project_root.
    """
    from prefab_sentinel.unity_assets import (
        collect_project_guid_index,
        looks_like_guid,
    )
    from prefab_sentinel.wsl_compat import to_wsl_path

    identifier = script_path_or_guid.strip()

    if looks_like_guid(identifier):
        if project_root is None:
            msg = "project_root is required for GUID resolution"
            raise ValueError(msg)
        guid_index = collect_project_guid_index(
            project_root, include_package_cache=False
        )
        asset_path = guid_index.get(identifier.lower())
        if asset_path is None or asset_path.suffix != ".cs":
            msg = f"No .cs file found for GUID: {identifier}"
            raise FileNotFoundError(msg)
        source = asset_path.read_text(encoding="utf-8-sig")
        fields = parse_serialized_fields(source)
        return identifier.lower(), asset_path, fields

    # Treat as file path
    cs_path = Path(to_wsl_path(identifier))
    if not cs_path.is_file():
        msg = f"Script file not found: {identifier}"
        raise FileNotFoundError(msg)

    # Find GUID from .meta file
    meta_path = Path(str(cs_path) + ".meta")
    guid = ""
    if meta_path.is_file():
        meta_text = meta_path.read_text(encoding="utf-8")
        guid_match = re.search(r"guid:\s*([0-9a-fA-F]{32})", meta_text)
        if guid_match:
            guid = guid_match.group(1).lower()

    source = cs_path.read_text(encoding="utf-8-sig")
    fields = parse_serialized_fields(source)
    return guid, cs_path, fields
