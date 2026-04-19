"""Dispatcher for the ``serialized_object`` prefab-create validation subtree.

``validate_prefab_create_ops`` walks the per-op list, invokes the
matching structural / value validator (in
``prefab_create_structure`` / ``prefab_create_values``), and finally
asserts the create-mode invariants (single root op, single trailing
``save``).

Shared types and helpers used by the validators
(``_PrefabCreateContext``, ``_check_handle_value``) live here because
both the structure and value modules import them.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from prefab_sentinel.contracts import Diagnostic
from prefab_sentinel.services.serialized_object.handles import VALUE_OPS


@dataclass
class _PrefabCreateContext:
    target: str
    diagnostics: list[Diagnostic]
    preview: list[dict[str, Any]]
    known_handles: dict[str, str]
    ops: list[dict[str, Any]]
    created: bool = False
    saved: bool = False
    root_name: str = ""


def _check_handle_value(
    value: object,
    known_handles: dict[str, str],
    target: str,
    index: int,
) -> Diagnostic | None:
    """Return a diagnostic if *value* is a ``{"handle": "..."}`` referencing an unknown handle."""
    if not (isinstance(value, dict) and "handle" in value and len(value) == 1):
        return None
    handle_name = str(value["handle"]).lstrip("$").strip()
    if handle_name in known_handles:
        return None
    return Diagnostic(
        path=target,
        location=f"ops[{index}].value.handle",
        detail="schema_error",
        evidence=f"handle '{handle_name}' is not defined by any prior op in this plan",
    )


def validate_prefab_create_ops(
    target: str,
    ops: list[dict[str, Any]],
) -> tuple[list[Diagnostic], list[dict[str, Any]]]:
    """Validate a prefab-create plan and return ``(diagnostics, preview)``.

    Schema errors are appended to ``diagnostics``; per-op preview rows
    are appended to ``preview``; the final two diagnostics enforce the
    "must create root" / "must save" invariants for create mode.
    """
    from prefab_sentinel.services.serialized_object.prefab_create_structure import (
        validate_pcreate_add_component_op,
        validate_pcreate_game_object_op,
        validate_pcreate_remove_component_op,
        validate_pcreate_rename_object_op,
        validate_pcreate_reparent_op,
        validate_pcreate_root_op,
    )
    from prefab_sentinel.services.serialized_object.prefab_create_values import (
        validate_pcreate_save_op,
        validate_pcreate_set_op,
    )

    diagnostics: list[Diagnostic] = []
    preview: list[dict[str, Any]] = []
    if not target:
        diagnostics.append(
            Diagnostic(
                path="",
                location="resources[].path",
                detail="schema_error",
                evidence="target path is required for prefab create mode",
            )
        )
        return diagnostics, preview
    if Path(target).suffix.lower() != ".prefab":
        diagnostics.append(
            Diagnostic(
                path=target,
                location="resources[].path",
                detail="schema_error",
                evidence="prefab create mode requires a .prefab target path",
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

    ctx = _PrefabCreateContext(
        target=target,
        diagnostics=diagnostics,
        preview=preview,
        known_handles={},
        ops=ops,
        root_name=Path(target).stem or "PrefabRoot",
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
        if op_name in {"create_prefab", "create_root"}:
            validate_pcreate_root_op(ctx, index, op, op_name)
        elif op_name == "create_game_object":
            validate_pcreate_game_object_op(ctx, index, op)
        elif op_name == "rename_object":
            validate_pcreate_rename_object_op(ctx, index, op)
        elif op_name == "reparent":
            validate_pcreate_reparent_op(ctx, index, op)
        elif op_name in {"add_component", "find_component"}:
            validate_pcreate_add_component_op(ctx, index, op, op_name)
        elif op_name == "remove_component":
            validate_pcreate_remove_component_op(ctx, index, op)
        elif op_name in VALUE_OPS:
            validate_pcreate_set_op(ctx, index, op, op_name)
        elif op_name == "save":
            validate_pcreate_save_op(ctx, index, op)
        else:
            ctx.diagnostics.append(
                Diagnostic(
                    path=target,
                    location=f"ops[{index}].op",
                    detail="schema_error",
                    evidence=f"unsupported prefab create op '{op_name}'",
                )
            )

    if not ctx.created:
        ctx.diagnostics.append(
            Diagnostic(
                path=target,
                location="ops",
                detail="schema_error",
                evidence="create mode requires a root creation operation",
            )
        )
    if not ctx.saved:
        ctx.diagnostics.append(
            Diagnostic(
                path=target,
                location="ops",
                detail="schema_error",
                evidence="create mode requires a save operation",
            )
        )
    return ctx.diagnostics, ctx.preview


__all__ = [
    "validate_prefab_create_ops",
]
