"""Field-value validators for scene plans.

Covers the value-mutating ops (``set``, ``insert_array_element``,
``remove_array_element``) and the terminator ``save_scene``.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from prefab_sentinel.contracts import Diagnostic
from prefab_sentinel.services.serialized_object.handles import require_handle_ref
from prefab_sentinel.services.serialized_object.prefab_create_dispatch import (
    _check_handle_value,
)
from prefab_sentinel.services.serialized_object.scene_dispatch import _SceneContext


def validate_scene_set_op(
    ctx: _SceneContext,
    index: int,
    op: dict[str, Any],
    op_name: str,
) -> None:
    component_handle = require_handle_ref(
        target=ctx.target,
        index=index,
        field="target",
        op=op,
        known_handles=ctx.known_handles,
        diagnostics=ctx.diagnostics,
        expected_kind="component",
    )
    property_path = str(op.get("path", "")).strip()
    if component_handle is None:
        return
    if not property_path:
        ctx.diagnostics.append(
            Diagnostic(
                path=ctx.target,
                location=f"ops[{index}].path",
                detail="schema_error",
                evidence="path is required",
            )
        )
        return
    if op_name == "set":
        if "value" not in op:
            ctx.diagnostics.append(
                Diagnostic(
                    path=ctx.target,
                    location=f"ops[{index}].value",
                    detail="schema_error",
                    evidence="value is required for set",
                )
            )
            return
        value = op.get("value")
        bad_handle = _check_handle_value(value, ctx.known_handles, ctx.target, index)
        if bad_handle is not None:
            ctx.diagnostics.append(bad_handle)
            return
        ctx.preview.append(
            {
                "op": op_name,
                "before": {"handle": component_handle, "path": property_path},
                "after": {
                    "handle": component_handle,
                    "path": property_path,
                    "value": deepcopy(value),
                },
            }
        )
        return

    op_index = op.get("index")
    if isinstance(op_index, bool) or not isinstance(op_index, int):
        ctx.diagnostics.append(
            Diagnostic(
                path=ctx.target,
                location=f"ops[{index}].index",
                detail="schema_error",
                evidence="index must be an integer",
            )
        )
        return
    entry: dict[str, Any] = {
        "op": op_name,
        "before": {
            "handle": component_handle,
            "path": property_path,
            "index": op_index,
        },
        "after": {
            "handle": component_handle,
            "path": property_path,
            "index": op_index,
        },
    }
    if op_name == "insert_array_element" and "value" in op:
        arr_value = op.get("value")
        bad_handle = _check_handle_value(arr_value, ctx.known_handles, ctx.target, index)
        if bad_handle is not None:
            ctx.diagnostics.append(bad_handle)
            return
        entry["after"]["value"] = deepcopy(arr_value)
    ctx.preview.append(entry)


def validate_scene_save_op(
    ctx: _SceneContext,
    index: int,
    op: dict[str, Any],
) -> None:
    if not ctx.scene_initialized:
        ctx.diagnostics.append(
            Diagnostic(
                path=ctx.target,
                location=f"ops[{index}].op",
                detail="schema_error",
                evidence="save_scene requires an opened scene first",
            )
        )
        return
    if ctx.saved:
        ctx.diagnostics.append(
            Diagnostic(
                path=ctx.target,
                location=f"ops[{index}].op",
                detail="schema_error",
                evidence="save_scene may appear only once",
            )
        )
        return
    if index != len(ctx.ops) - 1:
        ctx.diagnostics.append(
            Diagnostic(
                path=ctx.target,
                location=f"ops[{index}].op",
                detail="schema_error",
                evidence="save_scene must be the final operation in scene mode",
            )
        )
        return
    ctx.saved = True
    ctx.preview.append(
        {
            "op": "save_scene",
            "before": "(unsaved)",
            "after": {"path": ctx.target},
        }
    )


__all__ = [
    "validate_scene_set_op",
    "validate_scene_save_op",
]
