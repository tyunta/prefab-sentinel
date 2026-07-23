"""Structural operation validators for existing Prefab plans."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

from prefab_sentinel.services.serialized_object.handles import require_handle_ref
from prefab_sentinel.services.serialized_object.prefab_open_dispatch import (
    GAME_OBJECT_HANDLE_KINDS,
    PrefabOpenContext,
    append_schema_error,
    is_valid_relative_symbol_path,
    require_result_handle,
    require_text,
)


def validate_open_instantiate(
    ctx: PrefabOpenContext, index: int, op: dict[str, Any]
) -> None:
    prefab = require_text(ctx, index, op, "prefab")
    parent = require_handle_ref(
        target=ctx.target,
        index=index,
        field="parent",
        op=op,
        known_handles=ctx.known_handles,
        diagnostics=ctx.diagnostics,
        expected_kind=GAME_OBJECT_HANDLE_KINDS,
    )
    result = require_result_handle(ctx, index, op, "game_object_generated")
    if prefab is None or parent is None or result is None:
        return
    if Path(prefab).suffix.lower() != ".prefab":
        append_schema_error(ctx, f"ops[{index}].prefab", "prefab must reference a .prefab asset")
        return
    ctx.preview.append(
        {
            "op": "instantiate_prefab",
            "before": "(missing)",
            "after": {
                "prefab": prefab,
                "parent": parent,
                "handle": result,
                "kind": "game_object",
            },
        }
    )


def validate_open_rename(
    ctx: PrefabOpenContext, index: int, op: dict[str, Any]
) -> None:
    target = require_handle_ref(
        target=ctx.target,
        index=index,
        field="target",
        op=op,
        known_handles=ctx.known_handles,
        diagnostics=ctx.diagnostics,
        expected_kind=GAME_OBJECT_HANDLE_KINDS,
    )
    name = require_text(ctx, index, op, "name")
    if target is None or name is None:
        return
    ctx.preview.append(
        {
            "op": "rename_object",
            "before": {"handle": target},
            "after": {"handle": target, "name": name},
        }
    )


def _existing_address(
    ctx: PrefabOpenContext, index: int, op: dict[str, Any]
) -> dict[str, str] | None:
    has_symbol_path = "symbol_path" in op
    has_file_id = "file_id" in op
    if has_symbol_path == has_file_id:
        location = f"ops[{index}].symbol_path" if has_symbol_path else f"ops[{index}]"
        append_schema_error(
            ctx,
            location,
            "existing address requires exactly one of symbol_path or file_id",
        )
        return None
    if has_symbol_path:
        value = require_text(ctx, index, op, "symbol_path")
        return {"symbol_path": value} if value is not None else None
    value = op.get("file_id")
    if isinstance(value, bool) or not isinstance(value, (int, str)) or not str(value).strip():
        append_schema_error(ctx, f"ops[{index}].file_id", "file_id must be a non-empty ID")
        return None
    return {"file_id": str(value).strip()}


def validate_open_find_game_object(
    ctx: PrefabOpenContext, index: int, op: dict[str, Any]
) -> None:
    has_existing = "symbol_path" in op or "file_id" in op
    has_generated = "target" in op or "relative_symbol_path" in op
    if has_existing and has_generated:
        append_schema_error(ctx, f"ops[{index}]", "find_game_object address is ambiguous")
        return

    address: dict[str, str] | None
    result_kind: str
    if has_generated:
        relative_path = op.get("relative_symbol_path")
        if not is_valid_relative_symbol_path(relative_path):
            append_schema_error(
                ctx,
                f"ops[{index}].relative_symbol_path",
                "relative_symbol_path must be a strict relative path using optional #N sibling selectors",
            )
            return
        root = require_handle_ref(
            target=ctx.target,
            index=index,
            field="target",
            op=op,
            known_handles=ctx.known_handles,
            diagnostics=ctx.diagnostics,
            expected_kind="game_object_generated",
        )
        address = (
            {"target": root, "relative_symbol_path": str(relative_path)}
            if root is not None
            else None
        )
        result_kind = "game_object_generated"
    else:
        address = _existing_address(ctx, index, op)
        result_kind = "game_object_existing"

    result = require_result_handle(ctx, index, op, result_kind)
    if address is None or result is None:
        return
    ctx.preview.append(
        {
            "op": "find_game_object",
            "before": deepcopy(address),
            "after": {**address, "handle": result, "kind": "game_object"},
        }
    )


def validate_open_find_component(
    ctx: PrefabOpenContext, index: int, op: dict[str, Any]
) -> None:
    target = require_handle_ref(
        target=ctx.target,
        index=index,
        field="target",
        op=op,
        known_handles=ctx.known_handles,
        diagnostics=ctx.diagnostics,
        expected_kind=GAME_OBJECT_HANDLE_KINDS,
    )
    type_name = require_text(ctx, index, op, "type")
    result = require_result_handle(ctx, index, op, "component")
    if target is None or type_name is None or result is None:
        return
    ctx.preview.append(
        {
            "op": "find_component",
            "before": {"target": target, "type": type_name},
            "after": {
                "target": target,
                "type": type_name,
                "handle": result,
                "kind": "component",
            },
        }
    )


__all__ = [
    "validate_open_find_component",
    "validate_open_find_game_object",
    "validate_open_instantiate",
    "validate_open_rename",
]
