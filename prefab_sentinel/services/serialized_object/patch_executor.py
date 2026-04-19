"""Operation executor for JSON-target ``apply_and_save``.

``apply_op`` mutates ``payload`` in-place to reflect a single validated
op and returns a diff row describing the change.  It relies on
``property_walk`` for path traversal and raises ``TypeError`` /
``ValueError`` / ``KeyError`` / ``IndexError`` on schema or bounds
failures so callers can translate them into ``apply_error`` diagnostics.
"""

from __future__ import annotations

from typing import Any

from prefab_sentinel.services.serialized_object.handles import (
    ARRAY_SIZE_SUFFIX,
)
from prefab_sentinel.services.serialized_object.property_walk import (
    get_array_at_path,
    get_parent_and_leaf,
    walk_dict_path,
)


def apply_op(payload: object, op: dict[str, Any]) -> dict[str, Any]:
    """Apply ``op`` to ``payload`` in place and return a diff row."""
    op_name = str(op.get("op", ""))
    component = str(op.get("component", ""))
    property_path = str(op.get("path", ""))

    if op_name == "set":
        if property_path.endswith(ARRAY_SIZE_SUFFIX):
            base_path = property_path[: -len(ARRAY_SIZE_SUFFIX)]
            value = walk_dict_path(payload, base_path) if base_path else payload
            if not isinstance(value, list):
                raise TypeError(
                    f"'{ARRAY_SIZE_SUFFIX}' target must resolve to an array"
                )
            try:
                new_size = int(op["value"])
            except (KeyError, TypeError, ValueError) as exc:
                raise ValueError("array size must be an integer") from exc
            if new_size < 0:
                raise ValueError("array size must be >= 0")
            before = len(value)
            if new_size < before:
                del value[new_size:]
            elif new_size > before:
                value.extend([None] * (new_size - before))
            return {
                "op": op_name,
                "component": component,
                "path": property_path,
                "before": before,
                "after": len(value),
            }

        parent, leaf = get_parent_and_leaf(payload, property_path)
        if leaf not in parent:
            raise KeyError(leaf)
        before = parent[leaf]
        parent[leaf] = op.get("value")
        return {
            "op": op_name,
            "component": component,
            "path": property_path,
            "before": before,
            "after": parent[leaf],
        }

    if op_name == "insert_array_element":
        array_value = get_array_at_path(payload, property_path)
        index = int(op["index"])
        if index < 0 or index > len(array_value):
            raise IndexError("insert index is out of bounds")
        before_size = len(array_value)
        array_value.insert(index, op.get("value"))
        return {
            "op": op_name,
            "component": component,
            "path": property_path,
            "before": {"size": before_size},
            "after": {"size": len(array_value), "index": index},
        }

    if op_name == "remove_array_element":
        array_value = get_array_at_path(payload, property_path)
        index = int(op["index"])
        if index < 0 or index >= len(array_value):
            raise IndexError("remove index is out of bounds")
        before_size = len(array_value)
        removed = array_value.pop(index)
        return {
            "op": op_name,
            "component": component,
            "path": property_path,
            "before": {"size": before_size, "removed": removed},
            "after": {"size": len(array_value), "index": index},
        }

    raise ValueError(f"unsupported op '{op_name}'")


__all__ = ["apply_op"]
