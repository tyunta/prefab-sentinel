"""Validate composable operations for an existing Prefab resource."""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from prefab_sentinel.contracts import Diagnostic
from prefab_sentinel.services.serialized_object.handles import validate_result_handle

GAME_OBJECT_HANDLE_KINDS = (
    "game_object_root",
    "game_object_existing",
    "game_object_generated",
)
_RELATIVE_SEGMENT = re.compile(r"[^/#\\*]+(?:#[0-9]+)?")
_OP_FIELDS = {
    "instantiate_prefab": frozenset({"op", "prefab", "parent", "result"}),
    "rename_object": frozenset({"op", "target", "name"}),
    "find_game_object": frozenset(
        {"op", "symbol_path", "file_id", "target", "relative_symbol_path", "result"}
    ),
    "find_component": frozenset({"op", "target", "type", "result"}),
    "set": frozenset({"op", "target", "path", "value"}),
}


@dataclass
class PrefabOpenContext:
    target: str
    diagnostics: list[Diagnostic] = field(default_factory=list)
    preview: list[dict[str, Any]] = field(default_factory=list)
    known_handles: dict[str, str] = field(
        default_factory=lambda: {"root": "game_object_root"}
    )


def append_schema_error(ctx: PrefabOpenContext, location: str, evidence: str) -> None:
    ctx.diagnostics.append(
        Diagnostic(
            path=ctx.target,
            location=location,
            detail="schema_error",
            evidence=evidence,
        )
    )


def _reject_unsupported_fields(
    ctx: PrefabOpenContext,
    index: int,
    op_name: str,
    op: dict[str, Any],
) -> bool:
    unsupported = sorted(set(op) - _OP_FIELDS[op_name])
    if not unsupported:
        return False
    append_schema_error(
        ctx,
        f"ops[{index}].{unsupported[0]}",
        "operation contains an unsupported field",
    )
    return True


def require_text(
    ctx: PrefabOpenContext,
    index: int,
    op: dict[str, Any],
    field_name: str,
) -> str | None:
    value = op.get(field_name)
    if isinstance(value, str) and value.strip():
        return value.strip()
    append_schema_error(
        ctx,
        f"ops[{index}].{field_name}",
        f"{field_name} must be a non-empty string",
    )
    return None


def require_result_handle(
    ctx: PrefabOpenContext,
    index: int,
    op: dict[str, Any],
    kind: str,
) -> str | None:
    if "result" not in op:
        append_schema_error(ctx, f"ops[{index}].result", "result handle is required")
        return None
    handle = validate_result_handle(
        target=ctx.target,
        index=index,
        op=op,
        known_handles=ctx.known_handles,
        diagnostics=ctx.diagnostics,
    )
    if handle is not None:
        ctx.known_handles[handle] = kind
    return handle


def is_valid_relative_symbol_path(value: object) -> bool:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        return False
    segments = value.split("/")
    return all(
        segment not in {".", ".."} and _RELATIVE_SEGMENT.fullmatch(segment)
        for segment in segments
    )


def _validators() -> dict[
    str,
    Callable[[PrefabOpenContext, int, dict[str, Any]], None],
]:
    from prefab_sentinel.services.serialized_object.prefab_open_structure import (
        validate_open_find_component,
        validate_open_find_game_object,
        validate_open_instantiate,
        validate_open_rename,
    )
    from prefab_sentinel.services.serialized_object.prefab_open_values import (
        validate_open_set,
    )

    return {
        "instantiate_prefab": validate_open_instantiate,
        "rename_object": validate_open_rename,
        "find_game_object": validate_open_find_game_object,
        "find_component": validate_open_find_component,
        "set": validate_open_set,
    }


def validate_prefab_open_ops(
    target: str, ops: list[dict[str, Any]]
) -> tuple[list[Diagnostic], list[dict[str, Any]]]:
    ctx = PrefabOpenContext(target=target)
    if not target or Path(target).suffix.lower() != ".prefab":
        append_schema_error(ctx, "resources[].path", "open prefab mode requires a .prefab target")
        return ctx.diagnostics, ctx.preview
    if not ops:
        append_schema_error(ctx, "ops", "ops must contain at least one operation")
        return ctx.diagnostics, ctx.preview

    validators = _validators()
    for index, op in enumerate(ops):
        if not isinstance(op, dict):
            append_schema_error(ctx, f"ops[{index}]", "operation must be an object")
            continue
        op_name = str(op.get("op", "")).strip()
        validator = validators.get(op_name)
        if validator is None:
            append_schema_error(
                ctx,
                f"ops[{index}].op",
                "open prefab operation is unsupported",
            )
            continue
        if _reject_unsupported_fields(ctx, index, op_name, op):
            continue
        validator(ctx, index, op)
    diagnostics = [
        Diagnostic(
            path=diagnostic.path,
            location=diagnostic.location,
            detail=diagnostic.detail,
            evidence="Open Prefab operation schema is invalid.",
            severity=diagnostic.severity,
        )
        for diagnostic in ctx.diagnostics
    ]
    return diagnostics, ctx.preview


__all__ = ["validate_prefab_open_ops"]
