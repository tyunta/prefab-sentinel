"""Per-op preview writers for asset / material create-mode plans.

Each handler validates a single ``op_name`` against the running
``_AssetCreateContext`` and, on success, appends the matching
before/after preview row.  Called from ``asset_create_ops`` which owns
the state machine.
"""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

from prefab_sentinel.contracts import Diagnostic
from prefab_sentinel.services.serialized_object.asset_create_ops import (
    _AssetCreateContext,
)
from prefab_sentinel.services.serialized_object.handles import (
    ASSET_HANDLE,
    require_handle_ref,
    validate_result_handle,
)


def validate_acreate_create_asset_op(
    ctx: _AssetCreateContext,
    index: int,
    op: dict[str, Any],
) -> None:
    if ctx.created:
        ctx.diagnostics.append(
            Diagnostic(
                path=ctx.target,
                location=f"ops[{index}].op",
                detail="schema_error",
                evidence="asset root may be created only once",
            )
        )
        return
    ctx.created = True
    ctx.known_handles[ASSET_HANDLE] = "asset"
    result_handle = validate_result_handle(
        target=ctx.target,
        index=index,
        op=op,
        known_handles=ctx.known_handles,
        diagnostics=ctx.diagnostics,
    )
    if result_handle and result_handle != ASSET_HANDLE:
        ctx.known_handles[result_handle] = "asset"
    name_value = op.get("name")
    if name_value is not None and (
        not isinstance(name_value, str) or not name_value.strip()
    ):
        ctx.diagnostics.append(
            Diagnostic(
                path=ctx.target,
                location=f"ops[{index}].name",
                detail="schema_error",
                evidence="name must be a non-empty string when provided",
            )
        )
        return
    asset_name = (
        name_value.strip()
        if isinstance(name_value, str) and name_value.strip()
        else Path(ctx.target).stem
    )

    if ctx.kind == "material":
        shader_name = op.get("shader")
        if not isinstance(shader_name, str) or not shader_name.strip():
            ctx.diagnostics.append(
                Diagnostic(
                    path=ctx.target,
                    location=f"ops[{index}].shader",
                    detail="schema_error",
                    evidence="shader is required for create_asset on material resources",
                )
            )
            return
        type_name = op.get("type")
        if type_name is not None and (
            not isinstance(type_name, str) or not type_name.strip()
        ):
            ctx.diagnostics.append(
                Diagnostic(
                    path=ctx.target,
                    location=f"ops[{index}].type",
                    detail="schema_error",
                    evidence="type must be a non-empty string when provided",
                )
            )
            return
        ctx.preview.append(
            {
                "op": "create_asset",
                "before": "(missing)",
                "after": {
                    "path": ctx.target,
                    "type": type_name.strip()
                    if isinstance(type_name, str) and type_name.strip()
                    else "UnityEngine.Material",
                    "shader": shader_name.strip(),
                    "handle": result_handle or ASSET_HANDLE,
                    "kind": "asset",
                    "name": asset_name,
                },
            }
        )
        return

    type_name = op.get("type")
    if not isinstance(type_name, str) or not type_name.strip():
        ctx.diagnostics.append(
            Diagnostic(
                path=ctx.target,
                location=f"ops[{index}].type",
                detail="schema_error",
                evidence="type is required for create_asset on asset resources",
            )
        )
        return
    ctx.preview.append(
        {
            "op": "create_asset",
            "before": "(missing)",
            "after": {
                "path": ctx.target,
                "type": type_name.strip(),
                "handle": result_handle or ASSET_HANDLE,
                "kind": "asset",
                "name": asset_name,
            },
        }
    )


def validate_acreate_set_op(
    ctx: _AssetCreateContext,
    index: int,
    op: dict[str, Any],
    op_name: str,
) -> None:
    if not ctx.created:
        ctx.diagnostics.append(
            Diagnostic(
                path=ctx.target,
                location=f"ops[{index}].op",
                detail="schema_error",
                evidence=f"{op_name} requires a create_asset operation first",
            )
        )
        return
    asset_handle = require_handle_ref(
        target=ctx.target,
        index=index,
        field="target",
        op=op,
        known_handles=ctx.known_handles,
        diagnostics=ctx.diagnostics,
        expected_kind="asset",
    )
    property_path = str(op.get("path", "")).strip()
    if asset_handle is None:
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
        ctx.preview.append(
            {
                "op": op_name,
                "before": {"handle": asset_handle, "path": property_path},
                "after": {
                    "handle": asset_handle,
                    "path": property_path,
                    "value": deepcopy(op.get("value")),
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
            "handle": asset_handle,
            "path": property_path,
            "index": op_index,
        },
        "after": {
            "handle": asset_handle,
            "path": property_path,
            "index": op_index,
        },
    }
    if op_name == "insert_array_element" and "value" in op:
        entry["after"]["value"] = deepcopy(op.get("value"))
    ctx.preview.append(entry)


def validate_acreate_save_op(
    ctx: _AssetCreateContext,
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
                evidence="save requires a create_asset operation first",
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


__all__ = [
    "validate_acreate_create_asset_op",
    "validate_acreate_set_op",
    "validate_acreate_save_op",
]
