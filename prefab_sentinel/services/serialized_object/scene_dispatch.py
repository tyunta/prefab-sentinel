"""Scene ``open`` / ``create`` plan dispatcher.

Seeds the state machine with ``$scene`` as the only pre-defined handle,
then forwards each op to the handler in ``scene_object_ops`` /
``scene_component_ops`` / ``scene_values``.  Enforces the
"first op creates/opens the scene, last op saves" invariants after the
per-op loop.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from prefab_sentinel.contracts import Diagnostic
from prefab_sentinel.services.serialized_object.handles import (
    SCENE_HANDLE,
    VALUE_OPS,
)


@dataclass
class _SceneContext:
    target: str
    mode: str
    diagnostics: list[Diagnostic]
    preview: list[dict[str, Any]]
    known_handles: dict[str, str]
    ops: list[dict[str, Any]]
    scene_initialized: bool = False
    saved: bool = False
    expected_first_op: str = ""


def validate_scene_ops(
    *,
    target: str,
    mode: str,
    ops: list[dict[str, Any]],
) -> tuple[list[Diagnostic], list[dict[str, Any]]]:
    """Validate a scene plan and return ``(diagnostics, preview)``.

    ``mode`` is either ``"open"`` or ``"create"``; the difference shows
    up in the first op (``open_scene`` vs ``create_scene``) and the
    required kind.
    """
    from prefab_sentinel.services.serialized_object.scene_component_ops import (
        validate_scene_add_component_op,
        validate_scene_remove_component_op,
    )
    from prefab_sentinel.services.serialized_object.scene_object_ops import (
        validate_scene_create_game_object_op,
        validate_scene_duplicate_init_op,
        validate_scene_init_op,
        validate_scene_instantiate_prefab_op,
        validate_scene_rename_object_op,
        validate_scene_reparent_op,
    )
    from prefab_sentinel.services.serialized_object.scene_values import (
        validate_scene_save_op,
        validate_scene_set_op,
    )

    diagnostics: list[Diagnostic] = []
    preview: list[dict[str, Any]] = []
    if not target:
        diagnostics.append(
            Diagnostic(
                path="",
                location="resources[].path" if mode == "create" else "target",
                detail="schema_error",
                evidence=f"target path is required for scene {mode} mode",
            )
        )
        return diagnostics, preview
    if Path(target).suffix.lower() != ".unity":
        diagnostics.append(
            Diagnostic(
                path=target,
                location="resources[].path" if mode == "create" else "target",
                detail="schema_error",
                evidence=f"scene {mode} mode requires a .unity target path",
            )
        )
        return diagnostics, preview
    if not ops:
        diagnostics.append(
            Diagnostic(
                path=target,
                location="ops",
                detail="schema_error",
                evidence="ops must contain at least one operation",
            )
        )
        return diagnostics, preview

    ctx = _SceneContext(
        target=target,
        mode=mode,
        diagnostics=diagnostics,
        preview=preview,
        known_handles={SCENE_HANDLE: "scene"},
        ops=ops,
        expected_first_op="create_scene" if mode == "create" else "open_scene",
    )

    for index, op in enumerate(ops):
        if not isinstance(op, dict):
            ctx.diagnostics.append(
                Diagnostic(
                    path=target,
                    location=f"ops[{index}]",
                    detail="schema_error",
                    evidence="operation must be an object",
                )
            )
            continue

        op_name = str(op.get("op", "")).strip()
        if index == 0:
            validate_scene_init_op(ctx, index, op, op_name)
        elif op_name in {"create_scene", "open_scene"}:
            validate_scene_duplicate_init_op(ctx, index, op, op_name)
        elif op_name == "create_game_object":
            validate_scene_create_game_object_op(ctx, index, op)
        elif op_name == "instantiate_prefab":
            validate_scene_instantiate_prefab_op(ctx, index, op)
        elif op_name == "rename_object":
            validate_scene_rename_object_op(ctx, index, op)
        elif op_name == "reparent":
            validate_scene_reparent_op(ctx, index, op)
        elif op_name in {"add_component", "find_component"}:
            validate_scene_add_component_op(ctx, index, op, op_name)
        elif op_name == "remove_component":
            validate_scene_remove_component_op(ctx, index, op)
        elif op_name in VALUE_OPS:
            validate_scene_set_op(ctx, index, op, op_name)
        elif op_name == "save_scene":
            validate_scene_save_op(ctx, index, op)
        else:
            ctx.diagnostics.append(
                Diagnostic(
                    path=target,
                    location=f"ops[{index}].op",
                    detail="schema_error",
                    evidence=f"unsupported scene op '{op_name}'",
                )
            )

    if not ctx.scene_initialized:
        ctx.diagnostics.append(
            Diagnostic(
                path=target,
                location="ops",
                detail="schema_error",
                evidence=f"scene {mode} mode requires {ctx.expected_first_op}",
            )
        )
    if not ctx.saved:
        ctx.diagnostics.append(
            Diagnostic(
                path=target,
                location="ops",
                detail="schema_error",
                evidence="scene mode requires a save_scene operation",
            )
        )
    return ctx.diagnostics, ctx.preview


__all__ = ["_SceneContext", "validate_scene_ops"]
