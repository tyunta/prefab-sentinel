"""Stable public failure projection for audited patch writers."""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any, TypeVar

from prefab_sentinel.bridge_response import is_bridge_response_envelope
from prefab_sentinel.contracts import Diagnostic, Severity, ToolResponse

_LOGGER = logging.getLogger(__name__)
_T = TypeVar("_T")


def _invalidate_safely(
    operation: str,
    invalidate: Callable[[], None],
) -> bool:
    try:
        invalidate()
    except Exception:
        _LOGGER.exception("Patch writer cache invalidation failed: %s", operation)
        return False
    return True


def _failure_response(
    *,
    operation: str,
    phase: str,
    mutation_state: str,
    cache_state: str,
) -> dict[str, Any]:
    diagnostics: list[Diagnostic] = []
    message = "Patch writer failed before mutation."
    if mutation_state == "unknown":
        message = "Patch writer outcome is unknown."
        diagnostics.append(
            Diagnostic(
                path="",
                location="",
                detail="PATCH_WRITER_REINSPECTION_REQUIRED",
                evidence=(
                    "Mutation state is unknown; inspect affected assets "
                    "before another write."
                ),
                severity="warning",
            )
        )
    return ToolResponse(
        success=False,
        severity=Severity.ERROR,
        code="PATCH_WRITER_BOUNDARY_FAILED",
        message=message,
        data={
            "operation": operation,
            "phase": phase,
            "mutation_state": mutation_state,
            "cache_state": cache_state,
        },
        diagnostics=diagnostics,
    ).to_dict()


def acquire_patch_writer(
    operation: str,
    acquire: Callable[[], _T],
) -> tuple[_T | None, dict[str, Any] | None]:
    """Acquire a writer dependency without exposing private exceptions."""
    try:
        return acquire(), None
    except Exception:
        _LOGGER.exception("Patch writer acquisition failed: %s", operation)
        return None, _failure_response(
            operation=operation,
            phase="acquisition",
            mutation_state="not_started",
            cache_state="unchanged",
        )


def dispatch_patch_writer(
    operation: str,
    confirmed: bool,
    dispatch: Callable[[], _T],
    invalidate: Callable[[], None],
) -> tuple[_T | None, dict[str, Any] | None]:
    """Dispatch one writer and project unexpected failures by mutation phase."""
    try:
        return dispatch(), None
    except Exception:
        _LOGGER.exception("Patch writer dispatch failed: %s", operation)
        cache_state = "unchanged"
        if confirmed:
            cache_state = (
                "invalidated"
                if _invalidate_safely(operation, invalidate)
                else "unknown"
            )
        return None, _failure_response(
            operation=operation,
            phase="dispatch",
            mutation_state="unknown" if confirmed else "not_started",
            cache_state=cache_state,
        )


def project_patch_writer_refresh(
    operation: str,
    response: ToolResponse,
    refresh: Callable[[], str],
    invalidate: Callable[[], None],
) -> ToolResponse:
    """Attach refresh evidence and preserve applied success on refresh failure."""
    try:
        refresh_result = refresh()
    except Exception:
        _LOGGER.exception("Patch writer refresh failed: %s", operation)
        refresh_result = "false"

    data = dict(response.data)
    data["auto_refresh"] = refresh_result
    diagnostics = list(response.diagnostics)
    severity = response.severity
    if refresh_result == "false":
        cache_state = (
            "invalidated"
            if _invalidate_safely(operation, invalidate)
            else "unknown"
        )
        data.update(
            {
                "mutation_state": "applied",
                "cache_state": cache_state,
            }
        )
        diagnostics.append(
            Diagnostic(
                path="",
                location="",
                detail="PATCH_WRITER_REINSPECTION_REQUIRED",
                evidence=(
                    "Mutation was applied, but cache refresh failed; "
                    "inspect affected assets before another write."
                ),
                severity="warning",
            )
        )
        severity = Severity.WARNING

    return ToolResponse(
        success=response.success,
        severity=severity,
        code=response.code,
        message=response.message,
        data=data,
        diagnostics=diagnostics,
    )


def finalize_patch_writer_response(
    operation: str,
    response: ToolResponse,
    invalidate: Callable[[], None],
) -> dict[str, Any]:
    """Validate one response and invalidate session caches when required."""
    result = response.to_dict()
    if not is_bridge_response_envelope(result):
        raise ValueError("Patch writer returned an invalid response envelope.")

    data = result["data"]
    if data.get("cache_state") == "invalidated" and not _invalidate_safely(
        operation,
        invalidate,
    ):
        data["cache_state"] = "unknown"
    return result


def dispatch_patch_writer_response(
    operation: str,
    confirmed: bool,
    dispatch: Callable[[], ToolResponse],
    invalidate: Callable[[], None],
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """Dispatch and serialize one response inside the same failure boundary."""
    return dispatch_patch_writer(
        operation,
        confirmed,
        lambda: finalize_patch_writer_response(operation, dispatch(), invalidate),
        invalidate,
    )


def execute_patch_writer(
    operation: str,
    confirmed: bool,
    acquire: Callable[[], _T],
    dispatch: Callable[[_T], ToolResponse],
    invalidate: Callable[[], None],
) -> dict[str, Any]:
    """Run acquisition, dispatch, and serialization through one boundary."""
    writer, failure = acquire_patch_writer(operation, acquire)
    if failure is not None:
        return failure
    assert writer is not None

    result, failure = dispatch_patch_writer_response(
        operation,
        confirmed,
        lambda: dispatch(writer),
        invalidate,
    )
    if failure is not None:
        return failure
    assert result is not None
    return result
