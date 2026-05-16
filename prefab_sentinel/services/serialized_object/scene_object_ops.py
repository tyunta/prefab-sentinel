"""GameObject / hierarchy validators for scene plans.

Covers scene init (``open_scene`` / ``create_scene``),
``create_game_object``, ``instantiate_prefab``, ``rename_object``, and
``reparent``.  Component-shape ops live in ``scene_component_ops``;
field-value ops live in ``scene_values``.
"""

from __future__ import annotations

from typing import Any

from prefab_sentinel.contracts import Diagnostic
from prefab_sentinel.services.serialized_object.handles import (
    SCENE_HANDLE,
    require_handle_ref,
    validate_result_handle,
)
from prefab_sentinel.services.serialized_object.scene_dispatch import _SceneContext


def validate_scene_init_op(
    ctx: _SceneContext,
    index: int,
    op: dict[str, Any],
    op_name: str,
) -> None:
    if op_name != ctx.expected_first_op:
        ctx.diagnostics.append(
            Diagnostic(
                path=ctx.target,
                location="ops[0].op",
                detail="schema_error",
                evidence=f"scene {ctx.mode} mode must start with {ctx.expected_first_op}",
            )
        )
        return
    ctx.scene_initialized = True
    ctx.preview.append(
        {
            "op": op_name,
            "before": "(closed)" if ctx.mode == "open" else "(missing)",
            "after": {"path": ctx.target, "handle": SCENE_HANDLE, "kind": "scene"},
        }
    )


def validate_scene_duplicate_init_op(
    ctx: _SceneContext,
    index: int,
    op: dict[str, Any],
    op_name: str,
) -> None:
    ctx.diagnostics.append(
        Diagnostic(
            path=ctx.target,
            location=f"ops[{index}].op",
            detail="schema_error",
            evidence=f"{op_name} may appear only as the first operation",
        )
    )


def validate_scene_create_game_object_op(
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
                evidence="create_game_object requires an opened scene first",
            )
        )
        return
    name_value = op.get("name")
    if not isinstance(name_value, str) or not name_value.strip():
        ctx.diagnostics.append(
            Diagnostic(
                path=ctx.target,
                location=f"ops[{index}].name",
                detail="schema_error",
                evidence="name is required for create_game_object",
            )
        )
        return
    parent_handle = require_handle_ref(
        target=ctx.target,
        index=index,
        field="parent",
        op=op,
        known_handles=ctx.known_handles,
        diagnostics=ctx.diagnostics,
        expected_kind={"scene", "game_object"},
    )
    result_handle = validate_result_handle(
        target=ctx.target,
        index=index,
        op=op,
        known_handles=ctx.known_handles,
        diagnostics=ctx.diagnostics,
    )
    if parent_handle is None or ("result" in op and result_handle is None):
        return
    if result_handle:
        ctx.known_handles[result_handle] = "game_object"
    ctx.preview.append(
        {
            "op": "create_game_object",
            "before": "(missing)",
            "after": {
                "name": name_value.strip(),
                "parent": parent_handle,
                "handle": result_handle or "(anonymous)",
                "kind": "game_object",
            },
        }
    )


def validate_scene_instantiate_prefab_op(
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
                evidence="instantiate_prefab requires an opened scene first",
            )
        )
        return
    prefab_path = str(op.get("prefab", "")).strip()
    if not prefab_path:
        ctx.diagnostics.append(
            Diagnostic(
                path=ctx.target,
                location=f"ops[{index}].prefab",
                detail="schema_error",
                evidence="prefab is required for instantiate_prefab",
            )
        )
        return
    parent_handle = require_handle_ref(
        target=ctx.target,
        index=index,
        field="parent",
        op=op,
        known_handles=ctx.known_handles,
        diagnostics=ctx.diagnostics,
        expected_kind={"scene", "game_object"},
    )
    result_handle = validate_result_handle(
        target=ctx.target,
        index=index,
        op=op,
        known_handles=ctx.known_handles,
        diagnostics=ctx.diagnostics,
    )
    if parent_handle is None or ("result" in op and result_handle is None):
        return
    if result_handle:
        ctx.known_handles[result_handle] = "game_object"
    ctx.preview.append(
        {
            "op": "instantiate_prefab",
            "before": "(missing)",
            "after": {
                "prefab": prefab_path,
                "parent": parent_handle,
                "handle": result_handle or "(anonymous)",
                "kind": "game_object",
            },
        }
    )


def validate_scene_rename_object_op(
    ctx: _SceneContext,
    index: int,
    op: dict[str, Any],
) -> None:
    object_handle = require_handle_ref(
        target=ctx.target,
        index=index,
        field="target",
        op=op,
        known_handles=ctx.known_handles,
        diagnostics=ctx.diagnostics,
        expected_kind="game_object",
    )
    name_value = op.get("name")
    if object_handle is None:
        return
    if not isinstance(name_value, str) or not name_value.strip():
        ctx.diagnostics.append(
            Diagnostic(
                path=ctx.target,
                location=f"ops[{index}].name",
                detail="schema_error",
                evidence="name is required for rename_object",
            )
        )
        return
    ctx.preview.append(
        {
            "op": "rename_object",
            "before": {"handle": object_handle},
            "after": {"handle": object_handle, "name": name_value.strip()},
        }
    )


def validate_scene_reparent_op(
    ctx: _SceneContext,
    index: int,
    op: dict[str, Any],
) -> None:
    object_handle = require_handle_ref(
        target=ctx.target,
        index=index,
        field="target",
        op=op,
        known_handles=ctx.known_handles,
        diagnostics=ctx.diagnostics,
        expected_kind="game_object",
    )
    parent_handle = require_handle_ref(
        target=ctx.target,
        index=index,
        field="parent",
        op=op,
        known_handles=ctx.known_handles,
        diagnostics=ctx.diagnostics,
        expected_kind={"scene", "game_object"},
    )
    if object_handle is None or parent_handle is None:
        return
    if object_handle == parent_handle:
        ctx.diagnostics.append(
            Diagnostic(
                path=ctx.target,
                location=f"ops[{index}]",
                detail="schema_error",
                evidence="target and parent handles must differ",
            )
        )
        return
    ctx.preview.append(
        {
            "op": "reparent",
            "before": {"handle": object_handle},
            "after": {"handle": object_handle, "parent": parent_handle},
        }
    )


__all__ = [
    "validate_scene_init_op",
    "validate_scene_duplicate_init_op",
    "validate_scene_create_game_object_op",
    "validate_scene_instantiate_prefab_op",
    "validate_scene_rename_object_op",
    "validate_scene_reparent_op",
]
