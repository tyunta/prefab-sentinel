"""Create-mode dispatcher for ``asset`` / ``material`` resources.

The create-mode plan must start by introducing the asset root (via
``create_asset``), optionally mutate its fields (``set`` /
``insert_array_element`` / ``remove_array_element``), and finish with
``save``.  Per-op handlers that emit preview rows live in
``asset_create_writers`` to keep this file focused on the state
machine.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from prefab_sentinel.contracts import Diagnostic
from prefab_sentinel.services.serialized_object.handles import VALUE_OPS


@dataclass
class _AssetCreateContext:
    target: str
    kind: str
    diagnostics: list[Diagnostic]
    preview: list[dict[str, Any]]
    known_handles: dict[str, str]
    ops: list[dict[str, Any]]
    created: bool = False
    saved: bool = False


def validate_asset_create_ops(
    *,
    target: str,
    kind: str,
    ops: list[dict[str, Any]],
) -> tuple[list[Diagnostic], list[dict[str, Any]]]:
    """Validate an asset / material create-mode plan.

    Returns ``(diagnostics, preview)``.  Create-mode invariants — a
    single ``create_asset`` root and a trailing ``save`` — are enforced
    after the per-op loop.
    """
    from prefab_sentinel.services.serialized_object.asset_create_writers import (
        validate_acreate_create_asset_op,
        validate_acreate_save_op,
        validate_acreate_set_op,
    )

    diagnostics: list[Diagnostic] = []
    preview: list[dict[str, Any]] = []
    suffix = ".mat" if kind == "material" else ".asset"
    if not target:
        diagnostics.append(
            Diagnostic(
                path="",
                location="resources[].path",
                detail="schema_error",
                evidence=f"target path is required for {kind} create mode",
            )
        )
        return diagnostics, preview
    if Path(target).suffix.lower() != suffix:
        diagnostics.append(
            Diagnostic(
                path=target,
                location="resources[].path",
                detail="schema_error",
                evidence=f"{kind} create mode requires a {suffix} target path",
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

    ctx = _AssetCreateContext(
        target=target,
        kind=kind,
        diagnostics=diagnostics,
        preview=preview,
        known_handles={},
        ops=ops,
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
        if op_name == "create_asset":
            validate_acreate_create_asset_op(ctx, index, op)
        elif op_name in VALUE_OPS:
            validate_acreate_set_op(ctx, index, op, op_name)
        elif op_name == "save":
            validate_acreate_save_op(ctx, index, op)
        else:
            ctx.diagnostics.append(
                Diagnostic(
                    path=target,
                    location=f"ops[{index}].op",
                    detail="schema_error",
                    evidence=f"unsupported {kind} create op '{op_name}'",
                )
            )

    if not ctx.created:
        ctx.diagnostics.append(
            Diagnostic(
                path=target,
                location="ops",
                detail="schema_error",
                evidence="create mode requires a create_asset operation",
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


__all__ = ["_AssetCreateContext", "validate_asset_create_ops"]
