"""Component-shape validators for scene plans.

Covers ``add_component``, ``find_component``, and ``remove_component``.
"""

from __future__ import annotations

from typing import Any

from prefab_sentinel.contracts import Diagnostic
from prefab_sentinel.services.serialized_object.handles import (
    require_handle_ref,
    validate_result_handle,
)
from prefab_sentinel.services.serialized_object.scene_dispatch import _SceneContext


def validate_scene_add_component_op(
    ctx: _SceneContext,
    index: int,
    op: dict[str, Any],
    op_name: str,
) -> None:
    if not ctx.scene_initialized:
        ctx.diagnostics.append(
            Diagnostic(
                path=ctx.target,
                location=f"ops[{index}].op",
                detail="schema_error",
                evidence=f"{op_name} requires an opened scene first",
            )
        )
        return
    expected_kind: str | set[str] = (
        {"scene", "game_object"} if op_name == "find_component" else "game_object"
    )
    object_handle = require_handle_ref(
        target=ctx.target,
        index=index,
        field="target",
        op=op,
        known_handles=ctx.known_handles,
        diagnostics=ctx.diagnostics,
        expected_kind=expected_kind,
    )
    type_name = op.get("type")
    if object_handle is None:
        return
    if not isinstance(type_name, str) or not type_name.strip():
        ctx.diagnostics.append(
            Diagnostic(
                path=ctx.target,
                location=f"ops[{index}].type",
                detail="schema_error",
                evidence=f"type is required for {op_name}",
            )
        )
        return
    result_handle = validate_result_handle(
        target=ctx.target,
        index=index,
        op=op,
        known_handles=ctx.known_handles,
        diagnostics=ctx.diagnostics,
    )
    if "result" in op and result_handle is None:
        return
    if result_handle:
        ctx.known_handles[result_handle] = "component"
    ctx.preview.append(
        {
            "op": op_name,
            "before": "(missing)" if op_name == "add_component" else {"target": object_handle},
            "after": {
                "target": object_handle,
                "type": type_name.strip(),
                "handle": result_handle or "(anonymous)",
                "kind": "component",
            },
        }
    )


def validate_scene_remove_component_op(
    ctx: _SceneContext,
    index: int,
    op: dict[str, Any],
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
    if component_handle is None:
        return
    ctx.preview.append(
        {
            "op": "remove_component",
            "before": {"handle": component_handle, "kind": "component"},
            "after": "(removed)",
        }
    )


__all__ = [
    "validate_scene_add_component_op",
    "validate_scene_remove_component_op",
]
