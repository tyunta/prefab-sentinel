"""YAML-level override revert for Prefab Variants.

Removes matching ``m_Modifications`` entries from a Variant YAML file,
effectively reverting those properties to their inherited parent values.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from prefab_sentinel.contracts import Diagnostic, Severity, ToolResponse, error_response, success_response
from prefab_sentinel.services.prefab_variant import OverrideEntry, PrefabVariantService
from prefab_sentinel.services.prefab_variant.overrides import (
    find_modification_line_ranges,
    parse_overrides,
)
from prefab_sentinel.unity_assets import (
    collect_project_guid_index,
    decode_text_file,
    find_project_root,
    is_unity_builtin_guid,
)
from prefab_sentinel.unity_assets_path import resolve_scope_path

# Pattern for the Variant's m_SourcePrefab GUID.
_SOURCE_PREFAB_GUID_PATTERN = re.compile(
    r"m_SourcePrefab:\s*\{[^}]*guid:\s*([0-9a-fA-F]{32})"
)


def _collect_referenced_guids(
    text: str,
    entries: list[OverrideEntry],
) -> list[str]:
    """Return the referenced GUIDs (``m_SourcePrefab`` + each override target) for revert."""
    seen: list[str] = []
    seen_set: set[str] = set()

    def _maybe_add(guid: str) -> None:
        g = (guid or "").strip().lower()
        if not g or g in seen_set:
            return
        seen_set.add(g)
        seen.append(g)

    source_match = _SOURCE_PREFAB_GUID_PATTERN.search(text)
    if source_match:
        _maybe_add(source_match.group(1))

    for entry in entries:
        if entry.target_guid:
            _maybe_add(entry.target_guid)

    return seen


@dataclass(slots=True)
class RevertMatch:
    """A single override entry matched for revert."""

    entry: OverrideEntry
    # 0-based line range [start, end) in the file lines list
    start_line_index: int
    end_line_index: int


def _find_matches(
    text: str,
    target_file_id: str,
    property_path: str,
) -> list[RevertMatch]:
    """Find all OverrideEntry instances matching the given target + propertyPath."""
    entries = parse_overrides(text)
    lines = text.splitlines()
    line_ranges = find_modification_line_ranges(lines)

    matches: list[RevertMatch] = []
    for entry in entries:
        if entry.target_file_id == target_file_id and entry.property_path == property_path:
            rng = line_ranges.get(entry.line)
            if rng is not None:
                matches.append(
                    RevertMatch(
                        entry=entry,
                        start_line_index=rng[0],
                        end_line_index=rng[1],
                    )
                )
    return matches


def _remove_lines(text: str, ranges: list[tuple[int, int]]) -> str:
    """Remove the specified 0-based [start, end) line ranges from text.

    Ranges must not overlap. Returns the text with those lines removed,
    preserving the original line endings.
    """
    lines = text.splitlines(keepends=True)
    # Sort ranges in reverse order so removal indices stay valid
    sorted_ranges = sorted(ranges, key=lambda r: r[0], reverse=True)
    for start, end in sorted_ranges:
        del lines[start:end]
    return "".join(lines)


def revert_overrides(
    variant_path: str,
    target_file_id: str,
    property_path: str,
    dry_run: bool,
    confirm: bool,
    change_reason: str | None,
    project_root: Path | None = None,
) -> ToolResponse:
    """Revert (remove) matching override entries from a Prefab Variant YAML.

    Parameters
    ----------
    variant_path:
        Path to the Variant ``.prefab`` file.
    target_file_id:
        The ``fileID`` of the target component in the parent prefab.
    property_path:
        The ``propertyPath`` of the override to remove.
    dry_run:
        If ``True``, show what would be removed without writing.
    confirm:
        If ``True``, actually remove the entries and write back.
    change_reason:
        Required when ``confirm=True``. Audit log reason.
    project_root:
        Optional project root override.
    """
    root = find_project_root(project_root or Path.cwd())
    variant_svc = PrefabVariantService(project_root=root)

    resolved_path = resolve_scope_path(variant_path, root)
    if not resolved_path.exists():
        return error_response(
            "REVERT_TARGET_NOT_FOUND",
            f"Variant file not found: {variant_path}",
            data={"variant_path": variant_path, "read_only": True},
        )

    try:
        text = decode_text_file(resolved_path)
    except (OSError, UnicodeDecodeError) as exc:
        return error_response(
            "REVERT_READ_ERROR",
            f"Failed to read variant file: {exc}",
            data={"variant_path": variant_path, "read_only": True},
        )

    # Fail-fast per #83: reject the whole operation when any GUID referenced
    # by the variant (m_SourcePrefab or any override target) is not present in
    # the project GUID map. No YAML mutation must occur in that case.
    parsed_entries = parse_overrides(text)
    referenced_guids = _collect_referenced_guids(text, parsed_entries)
    guid_map = collect_project_guid_index(root)
    missing_guids = [
        guid
        for guid in referenced_guids
        if not is_unity_builtin_guid(guid) and guid not in guid_map
    ]
    if missing_guids:
        return error_response(
            "REF001",
            (
                f"revert.overrides aborted: {len(missing_guids)} referenced GUID(s) "
                "not present in project (fail-fast per #83)."
            ),
            severity=Severity.ERROR,
            data={
                "variant_path": variant_path,
                "target": target_file_id,
                "property_path": property_path,
                "missing_guids": missing_guids,
                "read_only": True,
                "executed": False,
            },
        )

    matches = _find_matches(text, target_file_id, property_path)

    if not matches:
        return error_response(
            "REVERT_NO_MATCH",
            f"No matching override found for target={target_file_id} "
            f"propertyPath={property_path}",
            severity=Severity.WARNING,
            data={
                "variant_path": variant_path,
                "target": target_file_id,
                "property_path": property_path,
                "match_count": 0,
                "read_only": True,
            },
        )

    # Resolve chain values to show what the parent value is
    chain_values = variant_svc.resolve_chain_values(variant_path)
    parent_key = f"{target_file_id}:{property_path}"
    parent_value = chain_values.get(parent_key)

    matched_info = []
    for m in matches:
        entry = m.entry
        obj_ref = entry.object_reference
        current_val = obj_ref if obj_ref and obj_ref != "{fileID: 0}" else entry.value
        matched_info.append({
            "line": entry.line,
            "target_file_id": entry.target_file_id,
            "target_guid": entry.target_guid,
            "property_path": entry.property_path,
            "current_value": current_val,
            "value": entry.value,
            "object_reference": entry.object_reference,
            "line_range": [m.start_line_index + 1, m.end_line_index],
        })

    if dry_run:
        return success_response(
            "REVERT_DRY_RUN",
            f"Would revert {len(matches)} override(s) for "
            f"target={target_file_id} propertyPath={property_path}",
            data={
                "variant_path": variant_path,
                "target": target_file_id,
                "property_path": property_path,
                "match_count": len(matches),
                "matches": matched_info,
                "parent_value": parent_value,
                "read_only": True,
            },
        )

    if not confirm:
        return error_response(
            "REVERT_NOT_CONFIRMED",
            "Revert requires --confirm to write changes.",
            severity=Severity.WARNING,
            data={
                "variant_path": variant_path,
                "target": target_file_id,
                "property_path": property_path,
                "match_count": len(matches),
                "matches": matched_info,
                "parent_value": parent_value,
                "read_only": True,
            },
        )

    # Confirm mode: remove the matching lines and write back
    ranges_to_remove = [(m.start_line_index, m.end_line_index) for m in matches]
    new_text = _remove_lines(text, ranges_to_remove)

    diagnostics: list[Diagnostic] = []
    try:
        resolved_path.write_text(new_text, encoding="utf-8")
    except OSError as exc:
        return error_response(
            "REVERT_WRITE_ERROR",
            f"Failed to write variant file: {exc}",
            data={
                "variant_path": variant_path,
                "target": target_file_id,
                "property_path": property_path,
                "match_count": len(matches),
                "read_only": False,
                "executed": False,
            },
        )

    return success_response(
        "REVERT_APPLIED",
        f"Reverted {len(matches)} override(s) for "
        f"target={target_file_id} propertyPath={property_path}",
        data={
            "variant_path": variant_path,
            "target": target_file_id,
            "property_path": property_path,
            "match_count": len(matches),
            "matches": matched_info,
            "parent_value": parent_value,
            "change_reason": change_reason,
            "read_only": False,
            "executed": True,
        },
        diagnostics=diagnostics,
    )
