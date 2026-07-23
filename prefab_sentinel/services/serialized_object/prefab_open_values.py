"""Value operation validation for existing Prefab plans."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from prefab_sentinel.services.serialized_object.handles import (
    normalize_handle_name,
    require_handle_ref,
)
from prefab_sentinel.services.serialized_object.prefab_open_dispatch import (
    PrefabOpenContext,
    append_schema_error,
    require_text,
)


def validate_open_set(
    ctx: PrefabOpenContext, index: int, op: dict[str, Any]
) -> None:
    target = require_handle_ref(
        target=ctx.target,
        index=index,
        field="target",
        op=op,
        known_handles=ctx.known_handles,
        diagnostics=ctx.diagnostics,
        expected_kind="component",
    )
    path = require_text(ctx, index, op, "path")
    if "value" not in op:
        append_schema_error(ctx, f"ops[{index}].value", "value is required for set")
        return
    value = op.get("value")
    if isinstance(value, dict) and set(value) == {"handle"}:
        value_handle = normalize_handle_name(value.get("handle"))
        if not value_handle or value_handle not in ctx.known_handles:
            append_schema_error(
                ctx,
                f"ops[{index}].value.handle",
                f"unknown handle '{value_handle}'",
            )
            return
    if target is None or path is None:
        return
    ctx.preview.append(
        {
            "op": "set",
            "before": {"handle": target, "path": path},
            "after": {"handle": target, "path": path, "value": deepcopy(value)},
        }
    )


__all__ = ["validate_open_set"]
