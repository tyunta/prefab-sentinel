"""Object / hierarchy validators for prefab-create plans.

Each function validates one ``op_name`` from the prefab-create vocabulary
that affects the GameObject / Component graph (``create_root``,
``create_prefab``, ``create_game_object``, ``rename_object``, ``reparent``,
``add_component`` / ``find_component``, ``remove_component``).  Value-level
ops (``set``, ``save``) live in ``prefab_create_values``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from prefab_sentinel.contracts import Diagnostic
from prefab_sentinel.services.serialized_object.prefab_create_dispatch import (
    _ROOT_HANDLE,
    _PrefabCreateContext,
)

if TYPE_CHECKING:
    from prefab_sentinel.services.serialized_object.service import (
        SerializedObjectService,
    )


def _schema_error(ctx: _PrefabCreateContext, location: str, evidence: str) -> None:
    """Append a ``schema_error`` diagnostic anchored at ``ctx.target``."""
    ctx.diagnostics.append(
        Diagnostic(
            path=ctx.target,
            location=location,
            detail="schema_error",
            evidence=evidence,
        )
    )


def validate_pcreate_root_op(
    service: SerializedObjectService,
    ctx: _PrefabCreateContext,
    index: int,
    op: dict[str, Any],
    op_name: str,
) -> None:
    if ctx.created:
        _schema_error(ctx, f"ops[{index}].op", "prefab root may be created only once")
        return
    ctx.created = True
    ctx.known_handles[_ROOT_HANDLE] = "game_object"
    name_value = op.get("name")
    if op_name == "create_root":
        if not isinstance(name_value, str) or not name_value.strip():
            _schema_error(ctx, f"ops[{index}].name", "name is required for create_root")
            return
        ctx.root_name = name_value.strip()
    elif name_value is not None:
        if not isinstance(name_value, str) or not name_value.strip():
            _schema_error(
                ctx,
                f"ops[{index}].name",
                "name must be a non-empty string when provided",
            )
            return
        ctx.root_name = name_value.strip()
    result_handle = service._validate_result_handle(
        target=ctx.target,
        index=index,
        op=op,
        known_handles=ctx.known_handles,
        diagnostics=ctx.diagnostics,
    )
    if result_handle and result_handle != _ROOT_HANDLE:
        ctx.known_handles[result_handle] = "game_object"
    ctx.preview.append(
        {
            "op": op_name,
            "before": "(missing)",
            "after": {
                "path": ctx.target,
                "root_name": ctx.root_name,
                "handle": result_handle or _ROOT_HANDLE,
                "kind": "game_object",
            },
        }
    )


def validate_pcreate_game_object_op(
    service: SerializedObjectService,
    ctx: _PrefabCreateContext,
    index: int,
    op: dict[str, Any],
) -> None:
    if not ctx.created:
        _schema_error(
            ctx, f"ops[{index}].op", "create_game_object requires a prefab root first"
        )
        return
    name_value = op.get("name")
    if not isinstance(name_value, str) or not name_value.strip():
        _schema_error(ctx, f"ops[{index}].name", "name is required for create_game_object")
        return
    parent_handle = service._require_handle_ref(
        target=ctx.target,
        index=index,
        field="parent",
        op=op,
        known_handles=ctx.known_handles,
        diagnostics=ctx.diagnostics,
        expected_kind="game_object",
    )
    result_handle = service._validate_result_handle(
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


def validate_pcreate_rename_object_op(
    service: SerializedObjectService,
    ctx: _PrefabCreateContext,
    index: int,
    op: dict[str, Any],
) -> None:
    object_handle = service._require_handle_ref(
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
        _schema_error(ctx, f"ops[{index}].name", "name is required for rename_object")
        return
    ctx.preview.append(
        {
            "op": "rename_object",
            "before": {"handle": object_handle},
            "after": {"handle": object_handle, "name": name_value.strip()},
        }
    )


def validate_pcreate_reparent_op(
    service: SerializedObjectService,
    ctx: _PrefabCreateContext,
    index: int,
    op: dict[str, Any],
) -> None:
    object_handle = service._require_handle_ref(
        target=ctx.target,
        index=index,
        field="target",
        op=op,
        known_handles=ctx.known_handles,
        diagnostics=ctx.diagnostics,
        expected_kind="game_object",
    )
    parent_handle = service._require_handle_ref(
        target=ctx.target,
        index=index,
        field="parent",
        op=op,
        known_handles=ctx.known_handles,
        diagnostics=ctx.diagnostics,
        expected_kind="game_object",
    )
    if object_handle is None or parent_handle is None:
        return
    if object_handle == _ROOT_HANDLE:
        _schema_error(ctx, f"ops[{index}].target", "root handle cannot be reparented")
        return
    if object_handle == parent_handle:
        _schema_error(ctx, f"ops[{index}]", "target and parent handles must differ")
        return
    ctx.preview.append(
        {
            "op": "reparent",
            "before": {"handle": object_handle},
            "after": {"handle": object_handle, "parent": parent_handle},
        }
    )


def validate_pcreate_add_component_op(
    service: SerializedObjectService,
    ctx: _PrefabCreateContext,
    index: int,
    op: dict[str, Any],
    op_name: str,
) -> None:
    if not ctx.created:
        _schema_error(ctx, f"ops[{index}].op", f"{op_name} requires a prefab root first")
        return
    object_handle = service._require_handle_ref(
        target=ctx.target,
        index=index,
        field="target",
        op=op,
        known_handles=ctx.known_handles,
        diagnostics=ctx.diagnostics,
        expected_kind="game_object",
    )
    type_name = op.get("type")
    if object_handle is None:
        return
    if not isinstance(type_name, str) or not type_name.strip():
        _schema_error(ctx, f"ops[{index}].type", f"type is required for {op_name}")
        return
    result_handle = service._validate_result_handle(
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


def validate_pcreate_remove_component_op(
    service: SerializedObjectService,
    ctx: _PrefabCreateContext,
    index: int,
    op: dict[str, Any],
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
    if component_handle is None:
        return
    ctx.preview.append(
        {
            "op": "remove_component",
            "before": {"handle": component_handle, "kind": "component"},
            "after": "(removed)",
        }
    )
