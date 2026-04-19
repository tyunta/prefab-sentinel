"""Dotted-path walkers over nested dict / list payloads.

Pure helpers split out of ``service.py`` per issue #91.  No imports from
the rest of the ``serialized_object`` package so these functions are
reusable without touching the service class.
"""

from __future__ import annotations

from typing import Any

from prefab_sentinel.services.serialized_object.handles import ARRAY_DATA_SUFFIX


def split_path(property_path: str) -> list[str]:
    """Split a dotted property path into non-empty segments."""
    return [segment for segment in property_path.split(".") if segment]


def walk_dict_path(payload: object, property_path: str) -> object:
    """Walk a dotted path through nested dicts.

    Raises ``TypeError`` if a non-dict is encountered mid-walk, or
    ``KeyError`` if a segment is missing.
    """
    value = payload
    for segment in split_path(property_path):
        if not isinstance(value, dict):
            raise TypeError(f"path segment '{segment}' expects an object")
        if segment not in value:
            raise KeyError(segment)
        value = value[segment]
    return value


def get_parent_and_leaf(
    payload: object,
    property_path: str,
) -> tuple[dict[str, Any], str]:
    """Resolve the parent dict and the leaf key of ``property_path``.

    Raises ``ValueError`` for an empty path and ``TypeError`` when the
    resolved parent is not a dict.
    """
    segments = split_path(property_path)
    if not segments:
        raise ValueError("path is required")
    if len(segments) == 1:
        if not isinstance(payload, dict):
            raise TypeError("root payload must be an object for scalar set")
        return payload, segments[0]

    parent_path = ".".join(segments[:-1])
    parent = walk_dict_path(payload, parent_path)
    if not isinstance(parent, dict):
        raise TypeError("resolved parent is not an object")
    return parent, segments[-1]


def get_array_at_path(payload: object, property_path: str) -> list[Any]:
    """Resolve the array at ``property_path``.

    ``property_path`` must end with ``.Array.data``; raises ``ValueError``
    otherwise, or ``TypeError`` if the resolved value is not a list.
    """
    if not property_path.endswith(ARRAY_DATA_SUFFIX):
        raise ValueError(f"array operations require a '{ARRAY_DATA_SUFFIX}' path")
    base_path = property_path[: -len(ARRAY_DATA_SUFFIX)]
    value = walk_dict_path(payload, base_path) if base_path else payload
    if not isinstance(value, list):
        raise TypeError("target path does not resolve to an array")
    return value


__all__ = [
    "split_path",
    "walk_dict_path",
    "get_parent_and_leaf",
    "get_array_at_path",
]
