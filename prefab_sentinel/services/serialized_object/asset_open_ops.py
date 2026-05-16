"""Open-mode validator for ``asset`` / ``material`` resources.

Open mode re-uses the single pre-seeded ``$asset`` handle; every op
must target it.  All ops are schema-checked and the before/after
preview rows are emitted.  Create-mode validation lives in
``asset_create_ops`` because the state machine is different.
"""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

from prefab_sentinel.contracts import Diagnostic
from prefab_sentinel.services.serialized_object.handles import (
    ASSET_HANDLE,
    VALUE_OPS,
    require_handle_ref,
)


def validate_asset_open_ops(
    *,
    target: str,
    kind: str,
    ops: list[dict[str, Any]],
) -> tuple[list[Diagnostic], list[dict[str, Any]]]:
    """Validate an asset / material open-mode plan.

    Returns ``(diagnostics, preview)``: schema errors accumulate in
    ``diagnostics``; the preview mirrors the dry-run diff rows.
    """
    diagnostics: list[Diagnostic] = []
    preview: list[dict[str, Any]] = []
    suffix = ".mat" if kind == "material" else ".asset"
    if not target:
        diagnostics.append(
            Diagnostic(
                path="",
                location="target",
                detail="schema_error",
                evidence=f"target path is required for {kind} open mode",
            )
        )
        return diagnostics, preview
    if Path(target).suffix.lower() != suffix:
        diagnostics.append(
            Diagnostic(
                path=target,
                location="target",
                detail="schema_error",
                evidence=f"{kind} open mode requires a {suffix} target path",
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

    known_handles = {ASSET_HANDLE: "asset"}
    for index, op in enumerate(ops):
        if not isinstance(op, dict):
            diagnostics.append(
                Diagnostic(
                    path=target,
                    location=f"ops[{index}]",
                    detail="schema_error",
                    evidence="operation must be an object",
                )
            )
            continue

        op_name = str(op.get("op", "")).strip()
        if op_name not in VALUE_OPS:
            diagnostics.append(
                Diagnostic(
                    path=target,
                    location=f"ops[{index}].op",
                    detail="schema_error",
                    evidence=f"unsupported asset open op '{op_name}'",
                )
            )
            continue

        asset_handle = require_handle_ref(
            target=target,
            index=index,
            field="target",
            op=op,
            known_handles=known_handles,
            diagnostics=diagnostics,
            expected_kind="asset",
        )
        property_path = str(op.get("path", "")).strip()
        if asset_handle is None:
            continue
        if not property_path:
            diagnostics.append(
                Diagnostic(
                    path=target,
                    location=f"ops[{index}].path",
                    detail="schema_error",
                    evidence="path is required",
                )
            )
            continue

        if op_name == "set":
            if "value" not in op:
                diagnostics.append(
                    Diagnostic(
                        path=target,
                        location=f"ops[{index}].value",
                        detail="schema_error",
                        evidence="value is required for set",
                    )
                )
                continue
            preview.append(
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
            continue

        op_index = op.get("index")
        if isinstance(op_index, bool) or not isinstance(op_index, int):
            diagnostics.append(
                Diagnostic(
                    path=target,
                    location=f"ops[{index}].index",
                    detail="schema_error",
                    evidence="index must be an integer",
                )
            )
            continue
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
        preview.append(entry)

    return diagnostics, preview


__all__ = ["validate_asset_open_ops"]
