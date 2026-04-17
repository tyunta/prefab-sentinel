"""Runtime response envelope parsing shared by the batchmode and editor-bridge paths.

Both invocation paths receive a JSON object from the Unity-side runtime
validation bridge; this module turns that payload into a ``ToolResponse``
or returns the canonical ``RUN_PROTOCOL_ERROR`` envelope when the payload
violates the protocol contract.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from prefab_sentinel.contracts import (
    Diagnostic,
    Severity,
    ToolResponse,
    error_response,
)


def _coerce_severity(value: object) -> Severity | None:
    if isinstance(value, Severity):
        return value
    if isinstance(value, str):
        try:
            return Severity(value)
        except ValueError:
            return None
    return None


def protocol_error(message: str, base_data: dict[str, Any]) -> ToolResponse:
    """Wrap *message* + *base_data* in the canonical ``RUN_PROTOCOL_ERROR`` envelope."""
    return error_response(
        "RUN_PROTOCOL_ERROR",
        message,
        data={**base_data, "read_only": True, "executed": False},
    )


def parse_runtime_response(
    payload: object,
    *,
    action: str,
    project_root: Path,
    scene_path: str | None,
    profile: str | None,
    log_path: Path,
    relative_fn: Callable[[Path], str],
) -> ToolResponse:
    """Validate and convert a Unity runtime JSON payload into ``ToolResponse``.

    Any structural violation (missing or wrong-typed field) is reported as
    ``RUN_PROTOCOL_ERROR``; otherwise the payload's success / severity /
    code / message / data / diagnostics fields propagate verbatim, with the
    caller's contextual data merged into ``data``.
    """
    base_data = {
        "action": action,
        "project_root": relative_fn(project_root),
        "scene_path": scene_path,
        "profile": profile,
        "log_path": relative_fn(log_path),
    }
    if not isinstance(payload, dict):
        return protocol_error("Unity runtime response root must be an object.", base_data)

    success = payload.get("success")
    severity = _coerce_severity(payload.get("severity"))
    code = payload.get("code")
    message = payload.get("message")
    data = payload.get("data")
    diagnostics_payload = payload.get("diagnostics")
    if not isinstance(success, bool):
        return protocol_error("Unity runtime response field 'success' must be a boolean.", base_data)
    if severity is None:
        return protocol_error("Unity runtime response field 'severity' is invalid.", base_data)
    if not isinstance(code, str) or not code.strip():
        return protocol_error("Unity runtime response field 'code' must be a non-empty string.", base_data)
    if not isinstance(message, str):
        return protocol_error("Unity runtime response field 'message' must be a string.", base_data)
    if not isinstance(data, dict):
        return protocol_error("Unity runtime response field 'data' must be an object.", base_data)
    if not isinstance(diagnostics_payload, list):
        return protocol_error("Unity runtime response field 'diagnostics' must be an array.", base_data)

    diagnostics: list[Diagnostic] = []
    for entry in diagnostics_payload:
        if not isinstance(entry, dict):
            return protocol_error("Unity runtime diagnostics entries must be objects.", base_data)
        diagnostics.append(
            Diagnostic(
                path=str(entry.get("path", "")),
                location=str(entry.get("location", "")),
                detail=str(entry.get("detail", "")),
                evidence=str(entry.get("evidence", "")),
            )
        )

    return ToolResponse(
        success=success,
        severity=severity,
        code=code.strip(),
        message=message,
        data={**base_data, **data},
        diagnostics=diagnostics,
    )
