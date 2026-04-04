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
    "CSharpClassInfo",
    "CSharpField",
    "build_field_map",
    "parse_class_info",
    "parse_serialized_fields",
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

    source_class: str = ""
    """Class that declared this field (set by ``resolve_inherited_fields()``)."""

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
        if self.source_class:
            result["source_class"] = self.source_class
        return result


@dataclass(slots=True)
class CSharpClassInfo:
    """Class declaration metadata extracted from C# source."""

    name: str
    """Class name (e.g., ``DerivedPlayer``)."""

    base_class: str
    """Base class name (e.g., ``BasePlayer``). Empty string if none."""

    fields: list[CSharpField]
    """Serialized fields declared directly in this class."""


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

# Matches a class declaration line:
#   [access] [modifiers] class Name [<T>] [: BaseClass [, IInterface ...]]
_CLASS_DECL_RE = re.compile(
    r"^\s*"
    r"(?:(?:public|private|protected|internal)\s+)?"
    r"(?:(?:abstract|sealed|static|partial|new)\s+)*"
    r"class\s+"
    r"(\w+)"  # group(1): class name
    r"(?:\s*<[^>]+>)?"  # optional generic params
    r"(?:\s*:\s*"
    r"([\w.]+(?:\s*<[^>]+>)?)"  # group(2): first base type
    r")?",
    re.MULTILINE,
)

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


def parse_class_info(
    source: str,
    hint_name: str = "",
) -> CSharpClassInfo | None:
    """Extract the main class declaration from C# source.

    Unity convention: one MonoBehaviour per file, named to match the file.
    When *hint_name* is given (typically the file stem), the class whose
    name matches is preferred.  Otherwise the first ``class`` found is
    returned.

    Args:
        source: C# source code text.
        hint_name: Preferred class name (e.g. file stem).

    Returns:
        ``CSharpClassInfo`` or ``None`` if no class declaration is found.
    """
    matches: list[tuple[str, str]] = []
    for m in _CLASS_DECL_RE.finditer(source):
        name = m.group(1)
        base = m.group(2) or ""
        # Strip generic suffix from base: "BaseClass<T>" → "BaseClass"
        angle = base.find("<")
        if angle >= 0:
            base = base[:angle].strip()
        matches.append((name, base))

    if not matches:
        return None

    # Prefer class matching hint_name
    chosen_name, chosen_base = matches[0]
    if hint_name:
        for name, base in matches:
            if name == hint_name:
                chosen_name, chosen_base = name, base
                break

    fields = parse_serialized_fields(source)
    serialized = [f for f in fields if f.is_serialized]
    return CSharpClassInfo(name=chosen_name, base_class=chosen_base, fields=serialized)


# ---------------------------------------------------------------------------
# Project-wide utilities
# ---------------------------------------------------------------------------


def build_field_map(
    project_root: Path,
    _guid_index: dict[str, Path] | None = None,
) -> dict[str, list[CSharpField]]:
    """Build a mapping from script GUID to serialized fields.

    Scans the project for ``.cs`` files, parses each for serialized fields,
    and maps via the companion ``.cs.meta`` GUID.

    Args:
        project_root: Unity project root directory.
        _guid_index: Pre-built GUID index to avoid redundant rebuilds.

    Returns:
        Dict mapping lowercase GUID strings to lists of ``CSharpField``.
    """
    if _guid_index is None:
        from prefab_sentinel.unity_assets import collect_project_guid_index

        _guid_index = collect_project_guid_index(project_root, include_package_cache=False)
    guid_index = _guid_index
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


