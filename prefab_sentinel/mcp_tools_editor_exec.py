"""MCP tool: ``editor_run_script`` — compile + run a C# snippet inside the
Unity Editor in a single step (issue #74).

Contract (per spec.md §"Batch 2 — #74 editor_run_script"):

* ``confirm=True`` AND a non-empty ``change_reason`` are always required.
  Any invocation missing either returns ``CHANGE_REASON_REQUIRED`` immediately
  *before* the Editor Bridge is contacted.
* Dry-run is not supported.
* On success, the bridge's envelope is returned unmodified so the caller
  sees the fixed class/method (``PrefabSentinelTempScript.Run``) result
  codes (``EDITOR_CTRL_RUN_SCRIPT_OK/COMPILE/RUNTIME/BAD_ID``) directly.
"""

from __future__ import annotations

import re
from typing import Any

from mcp.server.fastmcp import FastMCP

from prefab_sentinel.editor_bridge import DEFAULT_TIMEOUT_SEC, send_action
from prefab_sentinel.mcp_validation import require_write_audit

__all__ = [
    "register_editor_exec_tools",
    "editor_run_script",
    "editor_run_script_submit",
    "editor_run_script_poll",
    "REQUEST_ID_HEX_LENGTH",
]

# Issue #233: shape gate for the asynchronous poll surface's
# ``request_id``.  The bridge emits 32-char lower-case hex tokens
# (``Guid.NewGuid().ToString("N")``); the wrapper rejects anything else
# pre-bridge so free-text cannot leak across the transport.
REQUEST_ID_HEX_LENGTH = 32
_REQUEST_ID_RE = re.compile(r"^[0-9a-f]{32}$")


# Issue #226: lower floor for the run-script transport poll budget.
# Pinned to the bridge transport's pre-existing default so the new
# alignment can never shrink the published wait below the value
# callers already rely on; values smaller than this would let the
# transport time out before a freshly-spawned editor process has even
# acknowledged the request.
RUN_SCRIPT_TRANSPORT_TIMEOUT_FLOOR_SEC = DEFAULT_TIMEOUT_SEC

# Issue #226: dispatch margin added on top of the bridge-side compile
# budget when deriving the transport budget. The bridge's own deadline
# is ``compile_timeout + RunScriptEntryTypeTimeoutMs (4 s) + scheduling
# slop``; a 5 s margin guarantees the transport waits at least one
# bridge poll interval beyond every documented bridge-side phase before
# surfacing as a generic transport timeout.
RUN_SCRIPT_TRANSPORT_DISPATCH_MARGIN_SEC = 5


# Default compile-pending budget for the bridge handler. Raised from the
# previous 5s value so large snippets do not bounce on every cold compile;
# documented in the run-script handler contract (issue #116).
DEFAULT_COMPILE_TIMEOUT_MS = 15000

# Inclusive bounds enforced at the public surface (issue #127). The upper
# bound caps the worst-case time a single MCP call can keep the Editor
# Bridge poll loop alive; arbitrarily large values would let a caller
# pin the bridge for minutes per request. The lower bound rejects 0 and
# negative values that would short-circuit the poll into a busy loop or
# an immediate error.
COMPILE_TIMEOUT_MIN_MS = 1
COMPILE_TIMEOUT_MAX_MS = 120000


def _compile_timeout_out_of_range_envelope(value: int) -> dict[str, Any]:
    """Return the canonical COMPILE_TIMEOUT_OUT_OF_RANGE envelope.

    The message names the supplied value and both inclusive bounds so
    the caller can fix the request without consulting external docs.
    """
    return {
        "success": False,
        "severity": "error",
        "code": "COMPILE_TIMEOUT_OUT_OF_RANGE",
        "message": (
            f"compile_timeout_ms={value} is outside the inclusive range "
            f"[{COMPILE_TIMEOUT_MIN_MS}, {COMPILE_TIMEOUT_MAX_MS}] (milliseconds)."
        ),
        "data": {
            "supplied": value,
            "min_ms": COMPILE_TIMEOUT_MIN_MS,
            "max_ms": COMPILE_TIMEOUT_MAX_MS,
        },
        "diagnostics": [],
    }


def _run_script_transport_timeout_sec(compile_timeout_ms: int) -> int:
    """Derive the transport poll budget that always outlives the bridge.

    The transport budget is the larger of the published floor and
    ``ceil(compile_timeout_ms / 1000) + RUN_SCRIPT_TRANSPORT_DISPATCH_MARGIN_SEC``
    so a tiny compile budget cannot drop transport below the floor and a
    long compile budget cannot let transport surface as a timeout before
    the bridge's own deadline elapses (issue #226).
    """
    # Round up so a sub-second compile budget still consumes its full
    # millisecond allotment before the dispatch margin kicks in.
    compile_seconds = (compile_timeout_ms + 999) // 1000
    derived = compile_seconds + RUN_SCRIPT_TRANSPORT_DISPATCH_MARGIN_SEC
    return max(RUN_SCRIPT_TRANSPORT_TIMEOUT_FLOOR_SEC, derived)


def _rewrite_transport_timeout_envelope(
    bridge_response: dict[str, Any],
    *,
    compile_timeout_ms: int,
    transport_timeout_sec: int,
) -> dict[str, Any]:
    """Rewrite the bridge's generic transport-timeout envelope as a
    run-script-specific one (issue #226). Names both budget values and
    the inclusive upper bound the caller should retry against.
    """
    return {
        "success": False,
        "severity": "error",
        "code": "EDITOR_RUN_SCRIPT_TRANSPORT_TIMEOUT",
        "message": (
            f"editor_run_script: transport timed out after "
            f"{transport_timeout_sec}s while the bridge was still working "
            f"on a {compile_timeout_ms}ms compile budget. Retry with "
            f"compile_timeout_ms up to {COMPILE_TIMEOUT_MAX_MS} so the "
            f"bridge has more time before the transport gives up."
        ),
        "data": {
            "compile_timeout_ms": compile_timeout_ms,
            "transport_timeout_sec": transport_timeout_sec,
            "compile_timeout_max_ms": COMPILE_TIMEOUT_MAX_MS,
        },
        "diagnostics": [],
    }


def _decode_run_script_value(value: Any) -> Any:
    if not isinstance(value, dict):
        return value
    kind = value.get("kind")
    if kind == "null":
        return None
    if kind == "string" and isinstance(value.get("string_value"), str):
        return value["string_value"]
    if kind == "number" and isinstance(value.get("number_value"), int | float) and not isinstance(value.get("number_value"), bool):
        return value["number_value"]
    if kind == "bool" and isinstance(value.get("bool_value"), bool):
        return value["bool_value"]
    if kind == "string_array":
        raw = value.get("string_array")
        if isinstance(raw, list) and all(isinstance(item, str) for item in raw):
            return list(raw)
    if kind == "number_array":
        raw = value.get("number_array")
        if isinstance(raw, list) and all(isinstance(item, int | float) and not isinstance(item, bool) for item in raw):
            return list(raw)
    if kind == "bool_array":
        raw = value.get("bool_array")
        if isinstance(raw, list) and all(isinstance(item, bool) for item in raw):
            return list(raw)
    return value


def _decode_run_script_outputs(outputs: Any) -> Any:
    if isinstance(outputs, dict):
        return outputs
    if not isinstance(outputs, list):
        return outputs
    decoded: dict[str, Any] = {}
    for entry in outputs:
        if not isinstance(entry, dict):
            return outputs
        key = entry.get("key")
        if not isinstance(key, str) or not key:
            return outputs
        if "value" not in entry:
            return outputs
        decoded[key] = _decode_run_script_value(entry["value"])
    return decoded


def _normalize_run_script_envelope(response: dict[str, Any]) -> dict[str, Any]:
    data = response.get("data")
    if not isinstance(data, dict):
        return response
    normalized_data = dict(data)
    if "return_value" in normalized_data:
        normalized_data["return_value"] = _decode_run_script_value(normalized_data["return_value"])
    if "outputs" in normalized_data:
        normalized_data["outputs"] = _decode_run_script_outputs(normalized_data["outputs"])
    return {**response, "data": normalized_data}


def editor_run_script(
    code: str,
    confirm: bool,
    change_reason: str | None,
    compile_timeout_ms: int = DEFAULT_COMPILE_TIMEOUT_MS,
) -> dict[str, Any]:
    audit_err = require_write_audit("editor_run_script", confirm, change_reason)
    if audit_err is not None:
        return audit_err
    normalized_reason = (change_reason or "").strip()

    if (
        compile_timeout_ms < COMPILE_TIMEOUT_MIN_MS
        or compile_timeout_ms > COMPILE_TIMEOUT_MAX_MS
    ):
        return _compile_timeout_out_of_range_envelope(compile_timeout_ms)

    transport_timeout_sec = _run_script_transport_timeout_sec(compile_timeout_ms)
    response = send_action(
        action="run_script",
        timeout_sec=transport_timeout_sec,
        code=code,
        change_reason=normalized_reason,
        compile_timeout=compile_timeout_ms,
    )
    if response.get("code") == "EDITOR_BRIDGE_TIMEOUT":
        return _rewrite_transport_timeout_envelope(
            response,
            compile_timeout_ms=compile_timeout_ms,
            transport_timeout_sec=transport_timeout_sec,
        )
    return _normalize_run_script_envelope(response)


def _request_id_invalid_envelope(value: str) -> dict[str, Any]:
    """Return the canonical ``REQUEST_ID_INVALID`` envelope.

    Names the shape requirement so the caller can fix the request
    without consulting external docs.
    """
    return {
        "success": False,
        "severity": "error",
        "code": "REQUEST_ID_INVALID",
        "message": (
            f"request_id={value!r} must be a {REQUEST_ID_HEX_LENGTH}-char "
            "lower-case hex token (bridge-emitted shape)."
        ),
        "data": {"supplied": value},
        "diagnostics": [],
    }


def editor_run_script_submit(
    code: str,
    confirm: bool,
    change_reason: str | None,
    compile_timeout_ms: int = DEFAULT_COMPILE_TIMEOUT_MS,
) -> dict[str, Any]:
    audit_err = require_write_audit(
        "editor_run_script_submit", confirm, change_reason,
    )
    if audit_err is not None:
        return audit_err
    if (
        compile_timeout_ms < COMPILE_TIMEOUT_MIN_MS
        or compile_timeout_ms > COMPILE_TIMEOUT_MAX_MS
    ):
        return _compile_timeout_out_of_range_envelope(compile_timeout_ms)
    normalized_reason = (change_reason or "").strip()
    response = send_action(
        action="run_script_submit",
        code=code,
        change_reason=normalized_reason,
        compile_timeout=compile_timeout_ms,
        confirm=True,
    )
    return _normalize_run_script_envelope(response)


def editor_run_script_poll(
    request_id: str,
    cleanup_on_timeout: bool = False,
) -> dict[str, Any]:
    if not isinstance(request_id, str) or not _REQUEST_ID_RE.match(request_id):
        return _request_id_invalid_envelope(request_id)
    response = send_action(
        action="run_script_poll",
        request_id=request_id,
        cleanup_on_timeout=cleanup_on_timeout,
    )
    return _normalize_run_script_envelope(response)


def register_editor_exec_tools(server: FastMCP) -> None:
    """Register ``editor_run_script`` on *server*."""

    @server.tool(name="editor_run_script")
    def _editor_run_script(
        code: str,
        confirm: bool = False,
        change_reason: str = "",
        compile_timeout_ms: int = DEFAULT_COMPILE_TIMEOUT_MS,
    ) -> dict[str, Any]:
        """Run an arbitrary C# snippet inside the Unity Editor.

        The snippet is written to ``Assets/Editor/_PrefabSentinelTemp/`` and
        compiled by Unity; the bridge then invokes
        ``PrefabSentinelTempScript.Run()``.  Temp files are always cleaned
        up after execution (success or failure) and on Editor startup.

        This is a write-class tool:

        * ``confirm`` must be ``True``.
        * ``change_reason`` must be a non-empty string.

        Returns the Editor Bridge envelope unchanged.  Dry-run is not
        supported per the issue spec.

        Args:
            compile_timeout_ms: Bounded compile-pending budget in
                milliseconds. Defaults to fifteen seconds; the bridge uses
                this to decide when to attach diagnostics or trigger
                stuck-detection recovery.
        """
        return editor_run_script(
            code=code,
            confirm=confirm,
            change_reason=change_reason or None,
            compile_timeout_ms=compile_timeout_ms,
        )

    @server.tool(name="editor_run_script_submit")
    def _editor_run_script_submit(
        code: str,
        confirm: bool = False,
        change_reason: str = "",
        compile_timeout_ms: int = DEFAULT_COMPILE_TIMEOUT_MS,
    ) -> dict[str, Any]:
        """Stage a C# snippet asynchronously and return an opaque
        request id (issue #233).

        Args:
            code: C# snippet that defines
                ``PrefabSentinelTempScript.Run()``.
            confirm: Required ``True`` (writer audit gate).
            change_reason: Required non-empty audit reason.
            compile_timeout_ms: Compile-budget knob shared with the
                synchronous run-script wrapper.
        """
        return editor_run_script_submit(
            code=code,
            confirm=confirm,
            change_reason=change_reason or None,
            compile_timeout_ms=compile_timeout_ms,
        )

    @server.tool(name="editor_run_script_poll")
    def _editor_run_script_poll(
        request_id: str,
        cleanup_on_timeout: bool = False,
    ) -> dict[str, Any]:
        """Poll an asynchronous run-script job (issue #233).

        Args:
            request_id: 32-char lower-case hex token returned by
                ``editor_run_script_submit``.
            cleanup_on_timeout: When ``True``, ask the bridge to tear
                the staging area down on deadline elapse and report
                ``failed`` in the same call.
        """
        return editor_run_script_poll(
            request_id=request_id,
            cleanup_on_timeout=cleanup_on_timeout,
        )
