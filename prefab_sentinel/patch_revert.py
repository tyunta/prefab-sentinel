"""YAML-level override revert for Prefab Variants.

Removes matching ``m_Modifications`` entries from a Variant YAML file,
effectively reverting those properties to their inherited parent values.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from prefab_sentinel.contracts import Diagnostic, Severity, ToolResponse
from prefab_sentinel.mcp.prefab_variant import OverrideEntry, PrefabVariantMcp
from prefab_sentinel.unity_assets import (
    decode_text_file,
    find_project_root,
    resolve_scope_path,
)

# Pattern to detect the start of a modification entry (``- target: {...}``)
_MOD_ENTRY_START = re.compile(r"^\s+-\s*target:\s*\{")


@dataclass(slots=True)
class RevertMatch:
    """A single override entry matched for revert."""

    entry: OverrideEntry
    # 0-based line range [start, end) in the file lines list
    start_line_index: int
    end_line_index: int


def _find_modification_line_ranges(
    lines: list[str],
    entries: list[OverrideEntry],
) -> dict[int, tuple[int, int]]:
    """Map each OverrideEntry (by its 1-based line number) to a 0-based [start, end) range.

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

        if _MOD_ENTRY_START.match(line):
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
                if _MOD_ENTRY_START.match(line):
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


def _find_matches(
    text: str,
    target_file_id: str,
    property_path: str,
    variant_mcp: PrefabVariantMcp,
) -> list[RevertMatch]:
    """Find all OverrideEntry instances matching the given target + propertyPath."""
    entries = variant_mcp._parse_overrides(text)
    lines = text.splitlines()
    line_ranges = _find_modification_line_ranges(lines, entries)

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
    variant_mcp = PrefabVariantMcp(project_root=root)

    resolved_path = resolve_scope_path(variant_path, root)
    if not resolved_path.exists():
        return ToolResponse(
            success=False,
            severity=Severity.ERROR,
            code="REVERT_TARGET_NOT_FOUND",
            message=f"Variant file not found: {variant_path}",
            data={
                "variant_path": variant_path,
                "read_only": True,
            },
        )

    try:
        text = decode_text_file(resolved_path)
    except (OSError, UnicodeDecodeError) as exc:
        return ToolResponse(
            success=False,
            severity=Severity.ERROR,
            code="REVERT_READ_ERROR",
            message=f"Failed to read variant file: {exc}",
            data={
                "variant_path": variant_path,
                "read_only": True,
            },
        )

    matches = _find_matches(text, target_file_id, property_path, variant_mcp)

    if not matches:
        return ToolResponse(
            success=False,
            severity=Severity.WARNING,
            code="REVERT_NO_MATCH",
            message=(
                f"No matching override found for target={target_file_id} "
                f"propertyPath={property_path}"
            ),
            data={
                "variant_path": variant_path,
                "target": target_file_id,
                "property_path": property_path,
                "match_count": 0,
                "read_only": True,
            },
        )

    # Resolve chain values to show what the parent value is
    chain_values = variant_mcp.resolve_chain_values(variant_path)
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
        return ToolResponse(
            success=True,
            severity=Severity.INFO,
            code="REVERT_DRY_RUN",
            message=(
                f"Would revert {len(matches)} override(s) for "
                f"target={target_file_id} propertyPath={property_path}"
            ),
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
        return ToolResponse(
            success=False,
            severity=Severity.WARNING,
            code="REVERT_NOT_CONFIRMED",
            message="Revert requires --confirm to write changes.",
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
        return ToolResponse(
            success=False,
            severity=Severity.ERROR,
            code="REVERT_WRITE_ERROR",
            message=f"Failed to write variant file: {exc}",
            data={
                "variant_path": variant_path,
                "target": target_file_id,
                "property_path": property_path,
                "match_count": len(matches),
                "read_only": False,
                "executed": False,
            },
        )

    return ToolResponse(
        success=True,
        severity=Severity.INFO,
        code="REVERT_APPLIED",
        message=(
            f"Reverted {len(matches)} override(s) for "
            f"target={target_file_id} propertyPath={property_path}"
        ),
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
