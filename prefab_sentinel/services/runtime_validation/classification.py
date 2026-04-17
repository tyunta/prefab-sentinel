"""Log-pattern classification and assertion logic for runtime validation.

Pure functions over ``log_lines: list[str]``; no I/O, no Unity invocation.
Severity rollup follows the contract pinned by issue #89:
``UDON_NULLREF`` matches surface at ``Severity.CRITICAL``.
"""

from __future__ import annotations

import re
from collections import Counter

from prefab_sentinel.contracts import (
    Diagnostic,
    Severity,
    ToolResponse,
    error_response,
    max_severity,
    success_response,
)

_LOG_PATTERNS: tuple[tuple[str, Severity, re.Pattern[str]], ...] = (
    (
        "BROKEN_PPTR",
        Severity.ERROR,
        re.compile(r"broken\s+pptr|broken pptr", re.IGNORECASE),
    ),
    (
        "UDON_NULLREF",
        Severity.CRITICAL,
        re.compile(
            r"(nullreferenceexception.*udon)|(udon.*nullreferenceexception)",
            re.IGNORECASE,
        ),
    ),
    (
        "VARIANT_OVERRIDE_MISMATCH",
        Severity.ERROR,
        re.compile(r"override.*mismatch|mismatch.*override", re.IGNORECASE),
    ),
    (
        "DUPLICATE_EVENTSYSTEM",
        Severity.WARNING,
        re.compile(r"there can be only one active eventsystem", re.IGNORECASE),
    ),
    (
        "MISSING_COMPONENT",
        Severity.ERROR,
        re.compile(
            r"missingcomponentexception|referenced script on this behaviour is missing",
            re.IGNORECASE,
        ),
    ),
)


def classify_errors(
    log_lines: list[str],
    max_diagnostics: int = 200,
) -> ToolResponse:
    """Classify *log_lines* against ``_LOG_PATTERNS``.

    Returns a ``ToolResponse`` whose ``data`` mirrors the shape pinned by
    issue #89 (``count_total`` and ``count_by_category`` keys; the old
    ``matched_issue_count`` / ``categories`` aliases are gone).
    """
    diagnostics: list[Diagnostic] = []
    counts: Counter[str] = Counter()
    severity_hits: list[Severity] = []
    total_hits = 0

    for index, line in enumerate(log_lines, start=1):
        for category, category_severity, pattern in _LOG_PATTERNS:
            if not pattern.search(line):
                continue
            counts[category] += 1
            severity_hits.append(category_severity)
            total_hits += 1
            if len(diagnostics) < max_diagnostics:
                diagnostics.append(
                    Diagnostic(
                        path="",
                        location=f"line {index}",
                        detail=category.lower(),
                        evidence=line.strip(),
                    )
                )
            break

    severity = max_severity(severity_hits)
    if total_hits == 0:
        code = "RUN_CLASSIFY_OK"
        message = "No runtime issues matched known error categories."
        success = True
    elif severity in (Severity.ERROR, Severity.CRITICAL):
        code = "RUN001"
        message = "Runtime issues matched error or critical categories."
        success = False
    else:
        code = "RUN_CLASSIFY_WARN"
        message = "Runtime issues matched warning categories."
        success = True

    diagnostics_limit = max(0, max_diagnostics)
    truncated = max(0, total_hits - diagnostics_limit)
    return ToolResponse(
        success=success,
        severity=severity,
        code=code,
        message=message,
        data={
            "line_count": len(log_lines),
            "count_total": total_hits,
            "returned_diagnostics": len(diagnostics),
            "truncated_diagnostics": truncated,
            "count_by_category": dict(counts),
            "categories_by_severity": {
                "critical": sum(
                    counts.get(category, 0)
                    for category, level, _ in _LOG_PATTERNS
                    if level == Severity.CRITICAL
                ),
                "error": sum(
                    counts.get(category, 0)
                    for category, level, _ in _LOG_PATTERNS
                    if level == Severity.ERROR
                ),
                "warning": sum(
                    counts.get(category, 0)
                    for category, level, _ in _LOG_PATTERNS
                    if level == Severity.WARNING
                ),
            },
            "read_only": True,
        },
        diagnostics=diagnostics,
    )


def assert_no_critical_errors(
    classification_result: ToolResponse,
    allow_warnings: bool = False,
) -> ToolResponse:
    """Assert that *classification_result* has no critical / error issues.

    Returns ``success=True`` when the assertion passes, else an error
    envelope whose code reflects the failing severity bucket.
    """
    severity_counts = classification_result.data.get("categories_by_severity", {})
    critical_count = int(severity_counts.get("critical", 0))
    error_count = int(severity_counts.get("error", 0))
    warning_count = int(severity_counts.get("warning", 0))

    if critical_count > 0 or error_count > 0:
        return error_response(
            "RUN001",
            "Runtime assertion failed due to critical/error issues.",
            severity=Severity.CRITICAL if critical_count > 0 else Severity.ERROR,
            data={
                "critical_count": critical_count,
                "error_count": error_count,
                "warning_count": warning_count,
                "allow_warnings": allow_warnings,
                "read_only": True,
            },
        )

    if warning_count > 0 and not allow_warnings:
        return error_response(
            "RUN_WARNINGS",
            "Runtime assertion failed because warnings are not allowed.",
            severity=Severity.WARNING,
            data={
                "critical_count": critical_count,
                "error_count": error_count,
                "warning_count": warning_count,
                "allow_warnings": allow_warnings,
                "read_only": True,
            },
        )

    return success_response(
        "RUN_ASSERT_OK",
        "Runtime assertion passed.",
        data={
            "critical_count": critical_count,
            "error_count": error_count,
            "warning_count": warning_count,
            "allow_warnings": allow_warnings,
            "read_only": True,
        },
    )
