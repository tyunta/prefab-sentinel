"""Shared preview/envelope helpers for ``dry_run_patch``.

These helpers build the ``SER_PLAN_INVALID`` / ``SER_DRY_RUN_OK``
envelopes and extract soft warnings from JSON preview rows.  They are
pure data transforms and do not depend on the ``SerializedObjectService``
instance.
"""

from __future__ import annotations

from typing import Any

from prefab_sentinel.contracts import Diagnostic, ToolResponse, error_response, success_response
from prefab_sentinel.services.serialized_object.before_cache import UnresolvedReason


def plan_invalid(target: str, diagnostics: list[Diagnostic], op_count: int) -> ToolResponse:
    """Return the ``SER_PLAN_INVALID`` read-only envelope."""
    return error_response(
        "SER_PLAN_INVALID",
        "Patch plan schema validation failed.",
        data={"target": target, "op_count": op_count, "read_only": True},
        diagnostics=diagnostics,
    )


def dry_run_ok(target: str, ops: list[dict[str, Any]], preview: list[dict[str, Any]]) -> ToolResponse:
    """Return the success ``SER_DRY_RUN_OK`` envelope with a preview."""
    return success_response(
        "SER_DRY_RUN_OK",
        "dry_run_patch generated a patch preview.",
        data={
            "target": target,
            "op_count": len(ops),
            "applied": 0,
            "diff": preview,
            "read_only": True,
        },
    )


def soft_warnings_for_preview(target: str, preview: list[dict[str, Any]]) -> list[Diagnostic]:
    """Extract ``unresolved_before_value`` / ``handle_in_value`` warnings.

    The preview rows are mutated in place to pop the ``_warning`` marker
    that ``validate_op`` attaches; callers rely on this so the warning
    does not leak into the final envelope payload.

    Issue #124: ``unresolved_before_value`` detection is driven by
    isinstance membership in :class:`UnresolvedReason`, not by string
    prefix. The diagnostic evidence carries the specific reason via the
    member's own string value so callers see why the chain walk could
    not produce a value.
    """
    warnings: list[Diagnostic] = []
    for entry in preview:
        loc = f"{entry.get('component', '')}:{entry.get('path', '')}"
        before_val = entry.get("before", "")
        if isinstance(before_val, UnresolvedReason):
            component = entry.get("component", "")
            prop_path = entry.get("path", "")
            warnings.append(
                Diagnostic(
                    path=target,
                    location=loc,
                    detail="unresolved_before_value",
                    evidence=(
                        f"Before value unresolved for '{component}:{prop_path}': "
                        f"{before_val.value}. "
                        f"The component type or property path may not exist on "
                        f"the target. This operation will likely fail on apply. "
                        f"Verify with 'inspect wiring --path {target}' or "
                        f"'editor list-children' if bridge is available."
                    ),
                )
            )
        warning_msg = entry.pop("_warning", None)
        if warning_msg:
            warnings.append(
                Diagnostic(
                    path=target,
                    location=loc,
                    detail="handle_in_value",
                    evidence=warning_msg,
                )
            )
    return warnings


__all__ = [
    "plan_invalid",
    "dry_run_ok",
    "soft_warnings_for_preview",
]
