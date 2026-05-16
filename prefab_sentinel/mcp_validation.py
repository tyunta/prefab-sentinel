"""Shared input validation for MCP tool modules."""

from __future__ import annotations

from typing import Any

__all__ = ["require_change_reason", "require_write_audit"]


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


def require_write_audit(
    tool_name: str, confirm: bool, change_reason: str | None,
) -> dict[str, Any] | None:
    """Validate the writer audit pair (confirm + non-empty change_reason).

    Unlike ``require_change_reason`` (which only fires when ``confirm=True``),
    this helper treats ``confirm=False`` as a violation too: write-class
    tools whose effects cannot be safely defaulted must never run without
    both an explicit confirmation and a non-empty, non-whitespace audit
    reason. Whitespace-only strings are treated as missing.

    Returns the canonical ``CHANGE_REASON_REQUIRED`` envelope naming the
    audited tool when the audit pair is unsatisfied, otherwise ``None``.
    """
    normalized = change_reason.strip() if isinstance(change_reason, str) else ""
    if confirm and normalized:
        return None
    return {
        "success": False,
        "severity": "error",
        "code": "CHANGE_REASON_REQUIRED",
        "message": (
            f"{tool_name} requires confirm=True AND a non-empty "
            "change_reason (audit trail)."
        ),
        "data": {},
        "diagnostics": [],
    }
