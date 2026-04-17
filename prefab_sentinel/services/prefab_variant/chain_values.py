"""Chain-value accumulation helpers for Prefab Variant analysis.

Implements the two chain-walk based value resolvers:

- :func:`resolve_chain_values` — plain dict of effective values across
  the full Variant chain.
- :func:`resolve_chain_values_with_origin` — same values annotated with
  the relative path and depth of the Prefab that set each value, plus
  per-level chain metadata and diagnostics.

Both functions accept the walker inputs directly so they stay decoupled
from :class:`PrefabVariantService`.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from prefab_sentinel.contracts import Diagnostic, Severity, ToolResponse, error_response, success_response
from prefab_sentinel.services.prefab_variant.chain import ChainValue, _ChainLevel, walk_chain_levels
from prefab_sentinel.services.prefab_variant.overrides import effective_value, iter_base_property_values
from prefab_sentinel.unity_assets import SOURCE_PREFAB_PATTERN, decode_text_file


def resolve_chain_values(
    variant_path: str,
    project_root: Path,
    resolve_path: Callable[[str, Path], Path],
    guid_map: dict[str, Path],
    relative_fn: Callable[[Path], str],
) -> dict[str, str]:
    """Walk the full Variant chain and return effective override values.

    Returns a dict keyed by ``"{target_file_id}:{property_path}"`` with
    the effective value (last-write-wins from the *top* of the chain —
    the variant itself wins, consistent with override semantics).  Any
    diagnostic produced by the walk is discarded.
    """
    path = resolve_path(variant_path, project_root)
    if not path.exists():
        return {}

    try:
        text = decode_text_file(path)
    except (OSError, UnicodeDecodeError):
        return {}

    if SOURCE_PREFAB_PATTERN.search(text) is None:
        return {}

    result: dict[str, str] = {}
    sink: list[Diagnostic] = []
    for level in walk_chain_levels(text, path, guid_map, relative_fn, sink):
        _accumulate_level_values(level, result)

    return result


def resolve_chain_values_with_origin(
    variant_path: str,
    project_root: Path,
    resolve_path: Callable[[str, Path], Path],
    guid_map: dict[str, Path],
    relative_fn: Callable[[Path], str],
) -> ToolResponse:
    """Walk the full Variant chain and return values with origin annotations.

    Each value carries the relative path and depth of the Prefab that
    set it.  Returns ``PVR_CHAIN_VALUES_WARN`` at ``severity=warning``
    when the walk produced any diagnostic, and
    ``PVR_CHAIN_VALUES_WITH_ORIGIN`` at ``severity=info`` otherwise.
    """
    path = resolve_path(variant_path, project_root)
    if not path.exists():
        return error_response(
            code="PVR404",
            message="Variant path does not exist.",
            data={"variant_path": variant_path, "read_only": True},
        )

    try:
        text = decode_text_file(path)
    except (OSError, UnicodeDecodeError):
        return error_response(
            "PVR_READ_ERROR",
            f"Failed to read variant file: {variant_path}",
            data={"variant_path": variant_path, "read_only": True},
        )

    if SOURCE_PREFAB_PATTERN.search(text) is None:
        return success_response(
            "PVR_NOT_VARIANT",
            "File is not a Variant; no chain to resolve.",
            data={
                "variant_path": variant_path,
                "chain": [],
                "value_count": 0,
                "values": [],
                "read_only": True,
            },
        )

    result: dict[str, ChainValue] = {}
    chain: list[dict[str, object]] = []
    diagnostics: list[Diagnostic] = []

    for level in walk_chain_levels(text, path, guid_map, relative_fn, diagnostics):
        rel = relative_fn(level.path)
        chain.append({"path": rel, "depth": level.depth})
        _accumulate_level_chain_values(level, rel, result)

    values_list = [
        {
            "target_file_id": cv.target_file_id,
            "property_path": cv.property_path,
            "value": cv.value,
            "origin_path": cv.origin_path,
            "origin_depth": cv.origin_depth,
        }
        for cv in result.values()
    ]

    if diagnostics:
        return success_response(
            "PVR_CHAIN_VALUES_WARN",
            f"Resolved {len(values_list)} values across {len(chain)} chain levels with warnings.",
            severity=Severity.WARNING,
            data={
                "variant_path": variant_path,
                "chain": chain,
                "value_count": len(values_list),
                "values": values_list,
                "read_only": True,
            },
            diagnostics=diagnostics,
        )

    return success_response(
        "PVR_CHAIN_VALUES_WITH_ORIGIN",
        f"Resolved {len(values_list)} values across {len(chain)} chain levels.",
        data={
            "variant_path": variant_path,
            "chain": chain,
            "value_count": len(values_list),
            "values": values_list,
            "read_only": True,
        },
    )


def _accumulate_level_values(level: _ChainLevel, result: dict[str, str]) -> None:
    for entry in level.entries:
        if not entry.property_path:
            continue
        key = f"{entry.target_file_id}:{entry.property_path}"
        if key not in result:
            result[key] = effective_value(entry)

    if level.is_base:
        for fid, pp, val in iter_base_property_values(level.text):
            key = f"{fid}:{pp}"
            if key not in result:
                result[key] = val


def _accumulate_level_chain_values(
    level: _ChainLevel,
    rel: str,
    result: dict[str, ChainValue],
) -> None:
    for entry in level.entries:
        if not entry.property_path:
            continue
        key = f"{entry.target_file_id}:{entry.property_path}"
        if key not in result:
            result[key] = ChainValue(
                target_file_id=entry.target_file_id,
                property_path=entry.property_path,
                value=effective_value(entry),
                origin_path=rel,
                origin_depth=level.depth,
            )

    if level.is_base:
        for fid, pp, val in iter_base_property_values(level.text):
            key = f"{fid}:{pp}"
            if key not in result:
                result[key] = ChainValue(
                    target_file_id=fid,
                    property_path=pp,
                    value=val,
                    origin_path=rel,
                    origin_depth=level.depth,
                )
