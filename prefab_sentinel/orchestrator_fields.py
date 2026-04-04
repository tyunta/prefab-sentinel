"""C# field inspection functions extracted from Phase1Orchestrator."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from prefab_sentinel.contracts import (
    ToolResponse,
    error_response,
    success_response,
)
from prefab_sentinel.services.reference_resolver import ReferenceResolverService
from prefab_sentinel.unity_assets import (
    GAMEOBJECT_BEARING_SUFFIXES,
    collect_project_guid_index,
)
from prefab_sentinel.unity_assets_path import resolve_scope_path


def _relative_path(path: Path, root: Path) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


def list_serialized_fields(
    reference_resolver: ReferenceResolverService,
    script_path_or_guid: str,
    include_inherited: bool = False,
) -> ToolResponse:
    from prefab_sentinel.csharp_fields_resolve import (
        resolve_inherited_fields,
        resolve_script_fields,
    )

    project_root = reference_resolver.project_root

    try:
        guid, cs_path, fields = resolve_script_fields(
            script_path_or_guid,
            project_root=project_root,
        )
    except (FileNotFoundError, ValueError) as exc:
        return error_response(
            "CSF_RESOLVE_FAILED",
            str(exc),
            data={"script": script_path_or_guid},
        )

    if include_inherited and guid:
        serialized = resolve_inherited_fields(guid, project_root)
    else:
        serialized = [f for f in fields if f.is_serialized]

    return success_response(
        "CSF_LIST_OK",
        f"Found {len(serialized)} serialized fields.",
        data={
            "script_guid": guid,
            "script_path": str(cs_path),
            "class_name": cs_path.stem,
            "field_count": len(serialized),
            "fields": [f.to_dict() for f in serialized],
            "include_inherited": include_inherited,
            "read_only": True,
        },
    )


def validate_field_rename(
    reference_resolver: ReferenceResolverService,
    script_path_or_guid: str,
    old_name: str,
    new_name: str,
    scope: str | None = None,
) -> ToolResponse:
    from prefab_sentinel.csharp_fields import parse_class_info
    from prefab_sentinel.csharp_fields_resolve import (
        find_derived_guids,
        resolve_script_fields,
    )
    from prefab_sentinel.udon_wiring import extract_monobehaviour_field_names
    from prefab_sentinel.unity_yaml_parser import CLASS_ID_MONOBEHAVIOUR, split_yaml_blocks

    project_root = reference_resolver.project_root

    try:
        guid, cs_path, fields = resolve_script_fields(
            script_path_or_guid,
            project_root=project_root,
        )
    except (FileNotFoundError, ValueError) as exc:
        return error_response(
            "CSF_RESOLVE_FAILED",
            str(exc),
            data={"script": script_path_or_guid},
        )

    serialized = [f for f in fields if f.is_serialized]
    field_names = {f.name for f in serialized}

    if old_name not in field_names:
        return error_response(
            "CSF_FIELD_NOT_FOUND",
            f"Field '{old_name}' not found in serialized fields of {cs_path.stem}.",
            data={
                "script": str(cs_path),
                "available_fields": sorted(field_names),
            },
        )

    conflict = new_name in field_names
    has_formerly = any(
        any("FormerlySerializedAs" in a for a in f.attributes)
        for f in serialized
        if f.name == old_name
    )

    scan_guids: set[str] = set()
    if guid:
        scan_guids.add(guid)
        try:
            source = cs_path.read_text(encoding="utf-8-sig")
            info = parse_class_info(source, hint_name=cs_path.stem)
            if info:
                derived = find_derived_guids(info.name, project_root)
                scan_guids.update(derived)
        except (OSError, UnicodeDecodeError):
            pass

    scope_path = (
        resolve_scope_path(scope, project_root) if scope else project_root
    )

    affected: list[dict[str, Any]] = []

    if scan_guids:
        all_files = reference_resolver.collect_scope_files(scope_path)
        yaml_files = [
            f for f in all_files
            if f.suffix.lower() in GAMEOBJECT_BEARING_SUFFIXES
        ]
        reference_resolver.preload_texts(yaml_files)
        for yaml_path in yaml_files:
            text = reference_resolver.read_text(yaml_path)
            if text is None:
                continue
            if not any(g in text for g in scan_guids):
                continue
            blocks = split_yaml_blocks(text)
            for block in blocks:
                if block.class_id != CLASS_ID_MONOBEHAVIOUR:
                    continue
                if not any(g in block.text for g in scan_guids):
                    continue
                yaml_fields = extract_monobehaviour_field_names(block)
                if old_name in yaml_fields:
                    rel = _relative_path(yaml_path, project_root)
                    block_guid = ""
                    for g in scan_guids:
                        if g in block.text:
                            block_guid = g
                            break
                    entry: dict[str, Any] = {
                        "path": rel,
                        "file_id": block.file_id,
                    }
                    if block_guid and block_guid != guid:
                        entry["via_derived_guid"] = block_guid
                    affected.append(entry)

    return success_response(
        "CSF_RENAME_OK",
        f"Rename '{old_name}' -> '{new_name}': {len(affected)} affected components.",
        data={
            "script_guid": guid,
            "script_path": str(cs_path),
            "old_name": old_name,
            "new_name": new_name,
            "conflict": conflict,
            "has_formerly_serialized_as": has_formerly,
            "affected_count": len(affected),
            "affected_assets": affected,
            "derived_guids_scanned": len(scan_guids) - (1 if guid else 0),
            "read_only": True,
        },
    )


def check_field_coverage(
    reference_resolver: ReferenceResolverService,
    scope: str,
) -> ToolResponse:
    from prefab_sentinel.csharp_fields import build_field_map
    from prefab_sentinel.csharp_fields_resolve import (
        build_class_name_index,
        resolve_inherited_fields,
    )
    from prefab_sentinel.udon_wiring import extract_monobehaviour_field_names
    from prefab_sentinel.unity_yaml_parser import CLASS_ID_MONOBEHAVIOUR, split_yaml_blocks

    project_root = reference_resolver.project_root
    scope_path = resolve_scope_path(scope, project_root)

    guid_index = collect_project_guid_index(project_root, include_package_cache=False)
    cs_by_guid: dict[str, Path] = {
        g: p for g, p in guid_index.items() if p.suffix == ".cs"
    }

    _field_map = build_field_map(project_root, _guid_index=guid_index)
    _class_index = build_class_name_index(project_root, _guid_index=guid_index)

    field_cache: dict[str, set[str]] = {}
    unused_fields: list[dict[str, Any]] = []
    orphaned_paths: list[dict[str, Any]] = []
    scripts_checked: set[str] = set()
    components_checked = 0

    all_files = reference_resolver.collect_scope_files(scope_path)
    yaml_files = [
        f for f in all_files
        if f.suffix.lower() in GAMEOBJECT_BEARING_SUFFIXES
    ]
    reference_resolver.preload_texts(yaml_files)
    for yaml_path in yaml_files:
        text = reference_resolver.read_text(yaml_path)
        if text is None:
            continue
        blocks = split_yaml_blocks(text)
        for block in blocks:
            if block.class_id != CLASS_ID_MONOBEHAVIOUR:
                continue
            guid_match = re.search(
                r"m_Script:\s*\{.*?guid:\s*([0-9a-fA-F]{32})", block.text
            )
            if not guid_match:
                continue
            script_guid = guid_match.group(1).lower()

            cs_path = cs_by_guid.get(script_guid)
            if cs_path is None:
                continue

            components_checked += 1

            if script_guid not in field_cache:
                resolved = resolve_inherited_fields(
                    script_guid,
                    project_root,
                    _field_map=_field_map,
                    _class_index=_class_index,
                )
                field_cache[script_guid] = {f.name for f in resolved}
                scripts_checked.add(script_guid)

            cs_fields = field_cache[script_guid]
            yaml_fields = set(extract_monobehaviour_field_names(block))
            rel = _relative_path(yaml_path, project_root)

            for name in sorted(yaml_fields - cs_fields):
                orphaned_paths.append({
                    "path": rel,
                    "file_id": block.file_id,
                    "field_name": name,
                    "script_guid": script_guid,
                    "class_name": cs_path.stem,
                })

            for name in sorted(cs_fields - yaml_fields):
                unused_fields.append({
                    "path": rel,
                    "file_id": block.file_id,
                    "field_name": name,
                    "script_guid": script_guid,
                    "class_name": cs_path.stem,
                })

    return success_response(
        "CSF_COVERAGE_OK",
        (
            f"Checked {components_checked} components "
            f"({len(scripts_checked)} scripts): "
            f"{len(unused_fields)} unused, {len(orphaned_paths)} orphaned."
        ),
        data={
            "scope": scope,
            "scripts_checked": len(scripts_checked),
            "components_checked": components_checked,
            "unused_count": len(unused_fields),
            "unused_fields": unused_fields,
            "orphaned_count": len(orphaned_paths),
            "orphaned_paths": orphaned_paths,
            "read_only": True,
        },
    )
