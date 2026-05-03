"""Per-target before-value resolver for JSON patch dry-run previews.

The resolver walks the Prefab Variant chain via
``PrefabVariantService.resolve_chain_values`` and caches the result on
the supplied ``service`` instance so subsequent ops on the same target
reuse the same lookup.  An empty cache is a valid "no overrides" state;
it is distinct from ``None``, which signals "not yet resolved".

A companion ``_before_class_map`` (file id → Unity component type name)
is populated alongside ``_before_cache`` from the same chain walk so
that callers can address a component by its Unity type name (e.g.
``"MeshRenderer"``) and the resolver disambiguates it through the chain
class map. Numeric file ids continue to lookup directly.

Issue #124: each unresolved branch returns a member of the typed
``UnresolvedReason`` StrEnum so the preview-warning extractor can
detect "unresolved" by isinstance check rather than by string-prefix
sniffing. ``StrEnum`` keeps each member behaving as a plain string for
JSON serialization and string formatting in diagnostics.
"""

from __future__ import annotations

from enum import StrEnum
from typing import TYPE_CHECKING

from prefab_sentinel.unity_assets import SOURCE_PREFAB_PATTERN, decode_text_file
from prefab_sentinel.unity_assets_path import resolve_scope_path

if TYPE_CHECKING:
    from prefab_sentinel.services.serialized_object.service import (
        SerializedObjectService,
    )


class UnresolvedReason(StrEnum):
    """Closed vocabulary for unresolved-before-value branches.

    Members are returned by :func:`resolve_before_value` when the
    requested ``component`` / ``property_path`` cannot be reduced to a
    single value through the Prefab Variant chain. The string value of
    each member is used as the diagnostic evidence so callers do not
    need to render reason-specific messages themselves.
    """

    NO_VARIANT_RESOLVER = "no_variant_resolver"
    FILE_UNREADABLE = "file_unreadable"
    NOT_A_VARIANT = "not_a_variant"
    EMPTY_CHAIN = "empty_chain"
    TYPE_NOT_FOUND = "type_not_found"
    AMBIGUOUS_TYPE = "ambiguous_type"
    PATH_NOT_FOUND = "path_not_found"


def resolve_before_value(
    service: SerializedObjectService,
    target: str,
    component: str,
    property_path: str,
) -> str | UnresolvedReason:
    """Best-effort resolution of the current value before a patch op.

    Traverses the full Prefab Variant chain so that overrides from
    parent Variants and property values from the base prefab are
    included.  The closest (child) override wins.

    ``component`` accepts either a numeric file id (e.g. ``"42"``) or a
    Unity component type name (e.g. ``"MeshRenderer"``). Type-name input
    is disambiguated through a per-target chain-class map. Ambiguous
    type names and type names absent from the chain return the
    corresponding :class:`UnresolvedReason` member.

    Returns the resolved value as a plain ``str`` when the chain walk
    succeeds; otherwise returns an :class:`UnresolvedReason` member
    naming the specific unresolved branch.
    """
    if service._prefab_variant is None:
        return UnresolvedReason.NO_VARIANT_RESOLVER

    if service._before_cache is None:
        try:
            target_path = resolve_scope_path(target, service.project_root)
            text = decode_text_file(target_path)
        except (OSError, UnicodeDecodeError):
            # Transient I/O errors (file deleted between calls, perm flap)
            # leave the cache untouched so subsequent calls retry the read
            # rather than poisoning the cache as ``EMPTY_CHAIN``.
            return UnresolvedReason.FILE_UNREADABLE

        if SOURCE_PREFAB_PATTERN.search(text) is None:
            service._before_cache = {}
            service._before_class_map = {}
            return UnresolvedReason.NOT_A_VARIANT

        service._before_cache = service._prefab_variant.resolve_chain_values(target)
        service._before_class_map = service._prefab_variant.resolve_chain_class_map(
            target
        )

    if not service._before_cache:
        return UnresolvedReason.EMPTY_CHAIN

    # Numeric file id → direct lookup. Component type name → resolve via
    # chain-class map first, then fall through to the same lookup table.
    if component.lstrip("-").isdigit():
        file_id = component
    else:
        class_map = service._before_class_map or {}
        matches = [
            fid for fid, type_name in class_map.items() if type_name == component
        ]
        if len(matches) == 0:
            return UnresolvedReason.TYPE_NOT_FOUND
        if len(matches) > 1:
            return UnresolvedReason.AMBIGUOUS_TYPE
        file_id = matches[0]

    lookup_key = f"{file_id}:{property_path}"
    value = service._before_cache.get(lookup_key)
    if value is not None:
        return value
    return UnresolvedReason.PATH_NOT_FOUND


__all__ = ["UnresolvedReason", "resolve_before_value"]
