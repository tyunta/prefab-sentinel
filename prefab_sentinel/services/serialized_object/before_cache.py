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
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from prefab_sentinel.unity_assets import SOURCE_PREFAB_PATTERN, decode_text_file
from prefab_sentinel.unity_assets_path import resolve_scope_path

if TYPE_CHECKING:
    from prefab_sentinel.services.serialized_object.service import (
        SerializedObjectService,
    )


def resolve_before_value(
    service: SerializedObjectService,
    target: str,
    component: str,
    property_path: str,
) -> str:
    """Best-effort resolution of the current value before a patch op.

    Traverses the full Prefab Variant chain so that overrides from
    parent Variants and property values from the base prefab are
    included.  The closest (child) override wins.

    ``component`` accepts either a numeric file id (e.g. ``"42"``) or a
    Unity component type name (e.g. ``"MeshRenderer"``). Type-name input
    is disambiguated through a per-target chain-class map. Ambiguous
    type names and type names absent from the chain return labelled
    sentinels.

    Returns a labelled placeholder when the value cannot be resolved.
    """
    if service._prefab_variant is None:
        return "(unresolved)"

    if service._before_cache is None:
        try:
            target_path = resolve_scope_path(target, service.project_root)
            text = decode_text_file(target_path)
        except (OSError, UnicodeDecodeError):
            service._before_cache = {}
            service._before_class_map = {}
            return "(unresolved: file unreadable)"

        if SOURCE_PREFAB_PATTERN.search(text) is None:
            service._before_cache = {}
            service._before_class_map = {}
            return "(unresolved: not a variant)"

        service._before_cache = service._prefab_variant.resolve_chain_values(target)
        service._before_class_map = service._prefab_variant.resolve_chain_class_map(
            target
        )

    if not service._before_cache:
        return "(unresolved)"

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
            return "(unresolved: type not found in chain)"
        if len(matches) > 1:
            return "(unresolved: ambiguous component type)"
        file_id = matches[0]

    lookup_key = f"{file_id}:{property_path}"
    value = service._before_cache.get(lookup_key)
    if value is not None:
        return value
    return "(unresolved: not found in chain)"


__all__ = ["resolve_before_value"]
