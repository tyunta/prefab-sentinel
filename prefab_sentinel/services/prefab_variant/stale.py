"""Stale-override detection for Prefab Variants.

Evaluates parsed ``OverrideEntry`` lists for three failure modes:

- ``empty_property_path``: an entry with no ``propertyPath`` — nothing
  to apply.
- ``duplicate_override``: two entries targeting the same
  ``(target_key, property_path)`` — the earlier one is shadowed.
- ``array_size_mismatch``: ``Array.size`` declares ``N`` but a later
  ``Array.data[M]`` with ``M >= N`` exists — out-of-bounds write.

The module returns a ``ToolResponse`` envelope so the service layer can
pass it through unchanged.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable
from pathlib import Path

from prefab_sentinel.contracts import Diagnostic, Severity, ToolResponse, error_response, success_response
from prefab_sentinel.services.prefab_variant.overrides import (
    ARRAY_DATA_PATH_PATTERN,
    ARRAY_SIZE_PATH_PATTERN,
    OverrideEntry,
)

# Specific code when only one category is present;
# PVR001 is also the fallback for mixed diagnostics.
_STALE_CATEGORY_CODES: dict[frozenset[str], str] = {
    frozenset({"empty_property_path"}): "PVR001",
    frozenset({"duplicate_override"}): "PVR002",
    frozenset({"array_size_mismatch"}): "PVR003",
}


def detect_stale(
    entries: list[OverrideEntry],
    variant_path: Path,
    relative_fn: Callable[[Path], str],
) -> ToolResponse:
    """Scan parsed override entries for stale/duplicate/mismatched overrides."""
    diagnostics: list[Diagnostic] = []
    key_count: dict[tuple[str, str], list[int]] = defaultdict(list)
    array_sizes: dict[tuple[str, str], int] = {}
    array_max_indexes: dict[tuple[str, str], int] = {}

    for entry in entries:
        key = (entry.target_key, entry.property_path)
        if entry.property_path:
            key_count[key].append(entry.line)
        else:
            diagnostics.append(
                Diagnostic(
                    path=relative_fn(variant_path),
                    location=f"{entry.line}:1",
                    detail="empty_property_path",
                    evidence="override entry does not specify propertyPath",
                )
            )

        size_match = ARRAY_SIZE_PATH_PATTERN.match(entry.property_path)
        if size_match:
            prefix = size_match.group("prefix")
            try:
                size = int(entry.value)
            except ValueError:
                continue
            array_sizes[(entry.target_key, prefix)] = size

        data_match = ARRAY_DATA_PATH_PATTERN.match(entry.property_path)
        if data_match:
            prefix = data_match.group("prefix")
            index = int(data_match.group("index"))
            key_array = (entry.target_key, prefix)
            current = array_max_indexes.get(key_array, -1)
            if index > current:
                array_max_indexes[key_array] = index

    for (target_key, property_path), lines in key_count.items():
        if len(lines) > 1:
            diagnostics.append(
                Diagnostic(
                    path=relative_fn(variant_path),
                    location=f"{lines[0]}:1..{lines[-1]}:1",
                    detail="duplicate_override",
                    evidence=(
                        f"{target_key} / {property_path} appears {len(lines)} times; "
                        "later entries shadow earlier entries"
                    ),
                )
            )

    for key, max_index in array_max_indexes.items():
        size = array_sizes.get(key)
        if size is None:
            continue
        if max_index >= size:
            target_key, prefix = key
            diagnostics.append(
                Diagnostic(
                    path=relative_fn(variant_path),
                    location="array_override",
                    detail="array_size_mismatch",
                    evidence=(
                        f"{target_key} / {prefix}: size={size} but data index {max_index} exists"
                    ),
                )
            )

    if diagnostics:
        categories = {d.detail for d in diagnostics}
        code = _STALE_CATEGORY_CODES.get(frozenset(categories), "PVR001")
        return error_response(
            code,
            "Potential stale overrides detected.",
            severity=Severity.WARNING,
            data={
                "variant_path": relative_fn(variant_path),
                "stale_count": len(diagnostics),
                "categories": sorted(categories),
                "read_only": True,
            },
            diagnostics=diagnostics,
        )

    return success_response(
        "PVR_STALE_NONE",
        "No stale override patterns detected.",
        data={"variant_path": relative_fn(variant_path), "stale_count": 0, "read_only": True},
    )
