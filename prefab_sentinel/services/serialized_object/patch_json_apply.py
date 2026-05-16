"""JSON-target apply backend for ``apply_and_save``.

Reads the target file, applies each op via ``patch_executor.apply_op``,
and writes the result back with a trailing newline.  Failures surface
as ``SER_TARGET_MISSING`` / ``SER_IO_ERROR`` / ``SER_TARGET_FORMAT`` /
``SER_APPLY_FAILED`` envelopes; the dry-run propagation helper lives
here too so the "dry-run failed" path stays close to the apply path.
"""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any

from prefab_sentinel.contracts import Diagnostic, ToolResponse, error_response, success_response
from prefab_sentinel.json_io import dump_json, load_json
from prefab_sentinel.services.serialized_object.patch_executor import apply_op
from prefab_sentinel.unity_assets import decode_text_file


def propagate_dry_run_failure(
    target: str,
    ops: list[dict[str, Any]],
    dry_run_response: ToolResponse,
) -> ToolResponse:
    """Rewrite a failed dry-run envelope with apply-mode flags."""
    if dry_run_response.code in {"SER001", "SER002"}:
        data = dict(dry_run_response.data)
        data.update({"applied": 0, "read_only": False, "executed": False})
        return error_response(
            dry_run_response.code,
            dry_run_response.message,
            severity=dry_run_response.severity,
            data=data,
            diagnostics=dry_run_response.diagnostics,
        )
    return error_response(
        "SER_PLAN_INVALID",
        "Patch plan schema validation failed.",
        data={
            "target": target,
            "op_count": len(ops),
            "applied": 0,
            "read_only": False,
            "executed": False,
        },
        diagnostics=dry_run_response.diagnostics,
    )


def apply_json_target(
    target_path: Path,
    ops: list[dict[str, Any]],
) -> ToolResponse:
    """Apply *ops* to the JSON file at *target_path* and persist it."""
    if not target_path.exists():
        return error_response(
            "SER_TARGET_MISSING",
            "Patch target file was not found.",
            data={
                "target": str(target_path),
                "op_count": len(ops),
                "applied": 0,
                "read_only": False,
                "executed": False,
            },
        )

    try:
        loaded = load_json(decode_text_file(target_path))
    except (OSError, UnicodeDecodeError) as exc:
        return error_response(
            "SER_IO_ERROR",
            "Failed to read patch target file.",
            data={
                "target": str(target_path),
                "op_count": len(ops),
                "applied": 0,
                "read_only": False,
                "executed": False,
                "error": str(exc),
            },
        )
    except json.JSONDecodeError as exc:
        return error_response(
            "SER_TARGET_FORMAT",
            "Patch target file must be valid JSON for Phase 1 apply backend.",
            data={
                "target": str(target_path),
                "op_count": len(ops),
                "applied": 0,
                "read_only": False,
                "executed": False,
                "error": str(exc),
            },
        )

    working = deepcopy(loaded)
    diagnostics: list[Diagnostic] = []
    applied_ops: list[dict[str, Any]] = []
    for index, op in enumerate(ops):
        try:
            applied_ops.append(apply_op(working, op))
        except (TypeError, ValueError, KeyError, IndexError) as exc:
            diagnostics.append(
                Diagnostic(
                    path=str(target_path),
                    location=f"ops[{index}] ({op.get('op', '?')})",
                    detail="apply_error",
                    evidence=str(exc),
                )
            )

    if diagnostics:
        return error_response(
            "SER_APPLY_FAILED",
            "Patch apply failed. Target was not modified.",
            data={
                "target": str(target_path),
                "op_count": len(ops),
                "applied": 0,
                "attempted": len(applied_ops),
                "read_only": False,
                "executed": False,
            },
            diagnostics=diagnostics,
        )

    try:
        target_path.write_text(
            f"{dump_json(working)}\n",
            encoding="utf-8",
        )
    except OSError as exc:
        return error_response(
            "SER_IO_ERROR",
            "Failed to write patch target file.",
            data={
                "target": str(target_path),
                "op_count": len(ops),
                "applied": 0,
                "attempted": len(applied_ops),
                "read_only": False,
                "executed": False,
                "error": str(exc),
            },
        )

    return success_response(
        "SER_APPLY_OK",
        "Patch apply completed for JSON target.",
        data={
            "target": str(target_path),
            "op_count": len(ops),
            "applied": len(applied_ops),
            "attempted": len(applied_ops),
            "diff": applied_ops,
            "read_only": False,
            "executed": True,
        },
    )


__all__ = [
    "propagate_dry_run_failure",
    "apply_json_target",
]
