"""Variant chain walking with diagnostics.

Shared traversal used by:
- ``resolve_prefab_chain`` — chain as a list of paths.
- ``resolve_chain_values`` — effective property values across the chain.
- ``resolve_chain_values_with_origin`` — same values with per-level origin
  annotations plus diagnostics propagation (issue #76).

All asset-decoding failures are emitted as ``Diagnostic`` entries appended
to a caller-supplied list; the walk never silently skips a level.  The
``detail`` taxonomy mirrors ``resolve_prefab_chain`` so downstream
consumers see one schema regardless of entry point.

The ``read_unity_text`` name is a local alias for
``prefab_sentinel.unity_assets.decode_text_file``; tests create actual
binary files on disk that trigger ``UnicodeDecodeError`` via the live
code path.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from prefab_sentinel.contracts import Diagnostic
from prefab_sentinel.unity_assets import (
    MODEL_FILE_SUFFIXES,
    SOURCE_PREFAB_PATTERN,
    decode_text_file as read_unity_text,
    normalize_guid,
)

if TYPE_CHECKING:
    from prefab_sentinel.services.prefab_variant.overrides import OverrideEntry

CHAIN_DEPTH_LIMIT = 12


@dataclass(slots=True)
class _ChainLevel:
    """One level in the Variant chain walk."""

    entries: list[OverrideEntry]
    path: Path
    depth: int
    is_base: bool
    text: str


@dataclass(slots=True)
class ChainValue:
    """A resolved property value with origin tracking."""

    target_file_id: str
    property_path: str
    value: str
    origin_path: str  # relative path of the Prefab that set this value
    origin_depth: int  # 0 = the variant itself, 1 = parent, ...


def _emit_decode_diagnostic(
    target: Path, relative_fn: Callable[[Path], str], diagnostics: list[Diagnostic]
) -> None:
    ext = target.suffix.lower()
    if ext in MODEL_FILE_SUFFIXES:
        evidence = (
            f"Base asset is a model file ({ext}). "
            "Cannot decode as YAML. "
            "Use editor_list_children for runtime hierarchy inspection."
        )
        detail = "model_file_base"
    else:
        evidence = "unable to decode source prefab"
        detail = "unreadable_file"
    diagnostics.append(
        Diagnostic(
            path=relative_fn(target),
            location="file",
            detail=detail,
            evidence=evidence,
        )
    )


def walk_chain_levels(
    initial_text: str,
    initial_path: Path,
    guid_map: dict[str, Path],
    relative_fn: Callable[[Path], str],
    diagnostics: list[Diagnostic],
) -> Iterator[_ChainLevel]:
    """Yield :class:`_ChainLevel` for each level from variant to base.

    Appends one ``Diagnostic`` to *diagnostics* for each referenced asset
    that cannot be resolved or decoded:

    - ``detail="missing_asset"`` with ``evidence=<guid>`` for unresolved GUIDs,
    - ``detail="model_file_base"`` for ``.fbx/.blend/.gltf/.glb/.obj`` bases,
    - ``detail="unreadable_file"`` for non-UTF-8 non-model binaries,
    - ``detail="loop_detected"`` when the chain references an ancestor,
    - ``detail="depth_limit"`` when the chain exceeds ``CHAIN_DEPTH_LIMIT``.

    The walk terminates on any of these conditions; the final yielded
    level is the last successfully decoded prefab.

    The caller is responsible for initial path validation and the initial
    text decode.
    """
    from prefab_sentinel.services.prefab_variant.overrides import parse_overrides

    visited: set[str] = set()
    current_text: str = initial_text
    current_path: Path = initial_path

    for depth in range(CHAIN_DEPTH_LIMIT):
        entries = parse_overrides(current_text)

        source = SOURCE_PREFAB_PATTERN.search(current_text)
        is_base = source is None

        yield _ChainLevel(
            entries=entries,
            path=current_path,
            depth=depth,
            is_base=is_base,
            text=current_text,
        )

        if is_base:
            return

        assert source is not None  # guaranteed: is_base is False
        source_guid = normalize_guid(source.group(2))
        if source_guid in visited:
            diagnostics.append(
                Diagnostic(
                    path=relative_fn(current_path),
                    location="prefab_chain",
                    detail="loop_detected",
                    evidence="prefab source chain references an already visited asset",
                )
            )
            return
        visited.add(source_guid)

        target_file = guid_map.get(source_guid)
        if target_file is None:
            diagnostics.append(
                Diagnostic(
                    path=relative_fn(current_path),
                    location="m_SourcePrefab",
                    detail="missing_asset",
                    evidence=source_guid,
                )
            )
            return

        try:
            current_text = read_unity_text(target_file)
        except (OSError, UnicodeDecodeError):
            _emit_decode_diagnostic(target_file, relative_fn, diagnostics)
            return

        current_path = target_file

    diagnostics.append(
        Diagnostic(
            path=relative_fn(initial_path),
            location="prefab_chain",
            detail="depth_limit",
            evidence=f"chain depth exceeded {CHAIN_DEPTH_LIMIT}",
        )
    )
