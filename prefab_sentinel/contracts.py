from __future__ import annotations

from collections.abc import Iterable
from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Any

__all__ = [
    "Severity",
    "Diagnostic",
    "ToolResponse",
    "max_severity",
    "error_response",
    "success_response",
    "error_dict",
    "success_dict",
]


class Severity(StrEnum):
    """Severity levels for tool responses, ordered from least to most severe."""

    INFO = "info"          # Informational: operation succeeded as expected
    WARNING = "warning"    # Non-fatal issue: operation succeeded but with caveats
    ERROR = "error"        # Operation failed: user action required
    CRITICAL = "critical"  # System-level failure: investigation required


@dataclass(slots=True)
class Diagnostic:
    """A single diagnostic entry attached to a ToolResponse."""

    path: str       # Asset path where the issue was detected
    location: str   # Location within the asset (e.g., "114:42" for line:column)
    detail: str     # Machine-readable category (e.g., "broken_ref", "schema_error")
    evidence: str   # Human-readable description with context


@dataclass(slots=True)
class ToolResponse:
    """Standardised response envelope for all prefab-sentinel operations.

    Every public service method returns a ``ToolResponse``.  The ``success``
    flag indicates whether the operation completed without actionable errors,
    while ``severity`` captures the worst diagnostic level encountered.
    """

    success: bool  # Whether the operation completed without error/critical issues
    severity: Severity  # Worst severity level across all diagnostics
    code: str  # Machine-readable result code (e.g., "REF001")
    message: str  # Human-readable summary of the result
    data: dict[str, Any] = field(default_factory=dict)  # Operation-specific payload
    diagnostics: list[Diagnostic] = field(default_factory=list)  # Individual findings

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a plain dict, converting severity to its string value."""
        payload = asdict(self)
        payload["severity"] = self.severity.value
        return payload


_SEVERITY_ORDER: dict[Severity, int] = {
    Severity.INFO: 0,
    Severity.WARNING: 1,
    Severity.ERROR: 2,
    Severity.CRITICAL: 3,
}


def max_severity(severities: Iterable[Severity]) -> Severity:
    """Return the highest severity from an iterable of severity levels.

    Args:
        severities: Zero or more ``Severity`` values to compare.

    Returns:
        The most severe level found, or ``Severity.INFO`` when the
        iterable is empty.
    """
    levels = list(severities)
    if not levels:
        return Severity.INFO
    return max(levels, key=lambda level: _SEVERITY_ORDER[level])


def error_response(
    code: str,
    message: str,
    *,
    severity: Severity = Severity.ERROR,
    data: dict[str, Any] | None = None,
    diagnostics: list[Diagnostic] | None = None,
) -> ToolResponse:
    """Build a failed ``ToolResponse``.

    Args:
        code: Machine-readable result code (e.g., ``"REF001"``).
        message: Human-readable error description.
        severity: Override default ``ERROR`` severity when needed.
        data: Operation-specific payload dict.
        diagnostics: Pre-built diagnostic entries to attach.

    Returns:
        A ``ToolResponse`` with ``success=False``.
    """
    return ToolResponse(
        success=False,
        severity=severity,
        code=code,
        message=message,
        data=data or {},
        diagnostics=diagnostics or [],
    )


def success_response(
    code: str,
    message: str,
    *,
    severity: Severity = Severity.INFO,
    data: dict[str, Any] | None = None,
    diagnostics: list[Diagnostic] | None = None,
) -> ToolResponse:
    """Build a successful ``ToolResponse``.

    Args:
        code: Machine-readable result code (e.g., ``"REF_SCAN_OK"``).
        message: Human-readable success description.
        severity: Override default ``INFO`` severity when needed.
        data: Operation-specific payload dict.
        diagnostics: Pre-built diagnostic entries to attach.

    Returns:
        A ``ToolResponse`` with ``success=True``.
    """
    return ToolResponse(
        success=True,
        severity=severity,
        code=code,
        message=message,
        data=data or {},
        diagnostics=diagnostics or [],
    )


# ---------------------------------------------------------------------------
# Untyped dict envelope helpers
# ---------------------------------------------------------------------------


def error_dict(
    code: str,
    message: str,
    data: dict[str, Any] | None = None,
    diagnostics: list[Any] | None = None,
) -> dict[str, Any]:
    """Build a failed result dict for write-operation pipelines."""
    return {
        "success": False,
        "severity": "error",
        "code": code,
        "message": message,
        "data": data or {},
        "diagnostics": diagnostics or [],
    }


def success_dict(
    code: str,
    message: str,
    data: dict[str, Any] | None = None,
    diagnostics: list[Any] | None = None,
) -> dict[str, Any]:
    """Build a successful result dict for write-operation pipelines."""
    return {
        "success": True,
        "severity": "info",
        "code": code,
        "message": message,
        "data": data or {},
        "diagnostics": diagnostics or [],
    }

