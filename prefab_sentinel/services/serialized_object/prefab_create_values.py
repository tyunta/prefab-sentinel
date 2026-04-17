"""Value-level validators for prefab-create plans.

Each function validates one ``op_name`` from the prefab-create vocabulary
that mutates Component fields (``set``, ``insert_array_element``,
``remove_array_element``) or terminates the plan (``save``).  Object /
hierarchy ops live in ``prefab_create_structure``.
"""

from __future__ import annotations

from copy import deepcopy
from typing import TYPE_CHECKING, Any

from prefab_sentinel.contracts import Diagnostic
from prefab_sentinel.services.serialized_object.prefab_create_dispatch import (
    _check_handle_value,
    _PrefabCreateContext,
)

if TYPE_CHECKING:
    from prefab_sentinel.services.serialized_object.service import (
        SerializedObjectService,
    )


def validate_pcreate_set_op(
    service: SerializedObjectService,
    ctx: _PrefabCreateContext,
    index: int,
    op: dict[str, Any],
    op_name: str,
) -> None:
    component_handle = service._require_handle_ref(
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


def validate_pcreate_save_op(
    ctx: _PrefabCreateContext,
    index: int,
    op: dict[str, Any],
) -> None:
    if ctx.saved:
        ctx.diagnostics.append(
            Diagnostic(
                path=ctx.target,
                location=f"ops[{index}].op",
                detail="schema_error",
                evidence="save may appear only once",
            )
        )
        return
    if not ctx.created:
        ctx.diagnostics.append(
            Diagnostic(
                path=ctx.target,
                location=f"ops[{index}].op",
                detail="schema_error",
                evidence="save requires a prefab root first",
            )
        )
        return
    if index != len(ctx.ops) - 1:
        ctx.diagnostics.append(
            Diagnostic(
                path=ctx.target,
                location=f"ops[{index}].op",
                detail="schema_error",
                evidence="save must be the final operation in create mode",
            )
        )
        return
    ctx.saved = True
    ctx.preview.append(
        {
            "op": "save",
            "before": "(unsaved)",
            "after": {"path": ctx.target},
        }
    )
