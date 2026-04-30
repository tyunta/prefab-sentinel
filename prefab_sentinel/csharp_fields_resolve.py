"""Resolution and inheritance utilities for C# serialized fields.

Provides project-wide resolution of script fields, inheritance chain
traversal, and derived-class discovery.  These functions build on the
core parser in ``csharp_fields`` and require a Unity project root.
"""

from __future__ import annotations

import re
from pathlib import Path

from prefab_sentinel.contracts import Diagnostic
from prefab_sentinel.csharp_fields import (
    CSharpField,
    build_field_map,
    parse_class_info,
    parse_serialized_fields,
    record_unreadable,
)

__all__ = [
    "build_class_name_index",
    "find_derived_guids",
    "resolve_inherited_fields",
    "resolve_script_fields",
]

# Base classes where inheritance chain resolution should stop.
# These are Unity/external types whose fields are not user-defined.
_INHERITANCE_STOP_CLASSES = frozenset({
    "MonoBehaviour",
    "UdonSharpBehaviour",
    "NetworkBehaviour",
    "ScriptableObject",
    "StateMachineBehaviour",
    "Component",
    "Behaviour",
    "Object",
})


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
        # Try class name resolution via GUID index stem matching
        if project_root is not None:
            guid_index = collect_project_guid_index(
                project_root, include_package_cache=False
            )
            stem_matches: list[tuple[str, Path]] = [
                (g, p) for g, p in guid_index.items()
                if p.suffix == ".cs" and p.stem == identifier
            ]
            if len(stem_matches) == 1:
                matched_guid, matched_path = stem_matches[0]
                source = matched_path.read_text(encoding="utf-8-sig")
                fields = parse_serialized_fields(source)
                return matched_guid, matched_path, fields
            if len(stem_matches) > 1:
                paths = ", ".join(str(p) for _, p in stem_matches)
                msg = f"Multiple scripts match class name '{identifier}': {paths}"
                raise FileNotFoundError(msg)
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


def _strip_namespace(name: str) -> str:
    """Strip namespace prefix: ``UnityEngine.MonoBehaviour`` → ``MonoBehaviour``."""
    dot = name.rfind(".")
    return name[dot + 1 :] if dot >= 0 else name


def build_class_name_index(
    project_root: Path,
    _guid_index: dict[str, Path] | None = None,
    *,
    diagnostics: list[Diagnostic] | None = None,
) -> dict[str, tuple[str, Path]]:
    """Build a mapping from class name to ``(guid, cs_path)``.

    Scans ``.cs`` files in the project and extracts the main class name
    from each (using ``parse_class_info`` with file-stem hinting).

    Args:
        project_root: Unity project root directory.
        _guid_index: Pre-built GUID index to avoid redundant rebuilds.
        diagnostics: Optional sink for per-file decode-failure diagnostics.
            When supplied, each unreadable file appends one
            ``Diagnostic(detail="unreadable_file", ...)``; when omitted, the
            decode failure is silently skipped and the scan proceeds.

    Returns:
        Dict mapping class name to ``(guid, cs_path)`` tuple.
    """
    if _guid_index is None:
        from prefab_sentinel.unity_assets import collect_project_guid_index

        _guid_index = collect_project_guid_index(project_root, include_package_cache=False)
    guid_index = _guid_index
    result: dict[str, tuple[str, Path]] = {}
    for guid, asset_path in guid_index.items():
        if asset_path.suffix != ".cs":
            continue
        try:
            source = asset_path.read_text(encoding="utf-8-sig")
        except (OSError, UnicodeDecodeError):
            record_unreadable(diagnostics, asset_path, project_root)
            continue
        info = parse_class_info(source, hint_name=asset_path.stem)
        if info is not None:
            result[info.name] = (guid, asset_path)
    return result


def resolve_inherited_fields(
    script_guid: str,
    project_root: Path,
    *,
    _field_map: dict[str, list[CSharpField]] | None = None,
    _class_index: dict[str, tuple[str, Path]] | None = None,
    diagnostics: list[Diagnostic] | None = None,
) -> list[CSharpField]:
    """Resolve all serialized fields including inherited ones.

    Walks the inheritance chain from the given script up to base classes,
    collecting serialized fields from each level.  Each returned field has
    ``source_class`` set to the class that declared it.

    Stops at Unity/external base classes (``MonoBehaviour``,
    ``UdonSharpBehaviour``, etc.) or when the base class is not found in
    the project.

    Args:
        script_guid: Lowercase GUID of the script.
        project_root: Unity project root directory.
        _field_map: Pre-built field map (for caching).
        _class_index: Pre-built class name index (for caching).
        diagnostics: Optional sink for per-file decode-failure diagnostics.

    Returns:
        List of serialized fields (base class fields first, then derived).
    """
    if _field_map is None:
        _field_map = build_field_map(project_root, diagnostics=diagnostics)
    if _class_index is None:
        _class_index = build_class_name_index(project_root, diagnostics=diagnostics)

    # Build guid → (class_name, path) reverse lookup for O(1) access
    guid_to_class: dict[str, tuple[str, Path]] = {
        guid: (name, path) for name, (guid, path) in _class_index.items()
    }

    # Walk the chain: derived → base → ... → stop
    # Each entry: (class_name, guid, fields)
    chain: list[tuple[str, str, list[CSharpField]]] = []
    visited: set[str] = set()
    current_guid = script_guid.lower()

    while current_guid and current_guid not in visited:
        visited.add(current_guid)

        # Find the .cs file and parse class info
        class_entry = guid_to_class.get(current_guid)
        if class_entry is None:
            # GUID not in class index — try field map only
            direct_fields = _field_map.get(current_guid, [])
            if direct_fields:
                chain.append(("", current_guid, direct_fields))
            break

        class_name, cs_path = class_entry
        try:
            source = cs_path.read_text(encoding="utf-8-sig")
        except (OSError, UnicodeDecodeError):
            record_unreadable(diagnostics, cs_path, project_root)
            break
        info = parse_class_info(source, hint_name=cs_path.stem)
        if info is None:
            break

        direct_fields = _field_map.get(current_guid, [])
        chain.append((info.name, current_guid, direct_fields))

        # Check stop condition
        base = info.base_class
        if not base or _strip_namespace(base) in _INHERITANCE_STOP_CLASSES:
            break

        # Resolve base class
        base_stripped = _strip_namespace(base)
        base_entry = _class_index.get(base_stripped)
        if base_entry is None:
            break
        current_guid = base_entry[0]

    # Merge: base first, then derived. Set source_class on each field.
    result: list[CSharpField] = []
    for class_name, _guid, fields in reversed(chain):
        for f in fields:
            result.append(
                CSharpField(
                    name=f.name,
                    type_name=f.type_name,
                    is_serialized=f.is_serialized,
                    is_public=f.is_public,
                    line=f.line,
                    attributes=list(f.attributes),
                    source_class=class_name,
                )
            )
    return result


def find_derived_guids(
    class_name: str,
    project_root: Path,
    *,
    _class_index: dict[str, tuple[str, Path]] | None = None,
    diagnostics: list[Diagnostic] | None = None,
) -> set[str]:
    """Find GUIDs of all classes that inherit from *class_name* (transitively).

    Args:
        class_name: Name of the base class.
        project_root: Unity project root directory.
        _class_index: Pre-built class name index (for caching).
        diagnostics: Optional sink for per-file decode-failure diagnostics.

    Returns:
        Set of lowercase GUIDs for all derived classes.
    """
    if _class_index is None:
        _class_index = build_class_name_index(project_root, diagnostics=diagnostics)

    # Build reverse map: base_name → [child_class_name]
    reverse: dict[str, list[str]] = {}
    for name, (_guid, cs_path) in _class_index.items():
        try:
            source = cs_path.read_text(encoding="utf-8-sig")
        except (OSError, UnicodeDecodeError):
            record_unreadable(diagnostics, cs_path, project_root)
            continue
        info = parse_class_info(source, hint_name=cs_path.stem)
        if info and info.base_class:
            base = _strip_namespace(info.base_class)
            reverse.setdefault(base, []).append(name)

    # BFS from class_name
    result: set[str] = set()
    queue = list(reverse.get(class_name, []))
    visited: set[str] = set()
    while queue:
        current = queue.pop(0)
        if current in visited:
            continue
        visited.add(current)
        entry = _class_index.get(current)
        if entry:
            result.add(entry[0])  # guid
        queue.extend(reverse.get(current, []))

    return result
