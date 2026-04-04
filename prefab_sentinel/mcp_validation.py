"""Shared input validation for MCP tool modules."""

from __future__ import annotations

from typing import Any

__all__ = ["require_change_reason"]


def require_change_reason(
    confirm: bool, change_reason: str | None,
) -> dict[str, Any] | None:
    """Validate that change_reason is provided when confirm=True.

    Returns an error envelope dict if validation fails, None otherwise.
    """
    if not confirm:
        return None
    if not change_reason:
        return {
            "success": False,
            "severity": "error",
            "code": "CHANGE_REASON_REQUIRED",
            "message": "change_reason is required when confirm=True.",
            "data": {},
            "diagnostics": [],
        }
    return None
