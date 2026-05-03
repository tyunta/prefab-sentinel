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

from typing import Any

from mcp.server.fastmcp import FastMCP

from prefab_sentinel.editor_bridge import send_action

__all__ = ["register_editor_exec_tools", "editor_run_script"]


def _change_reason_required_envelope() -> dict[str, Any]:
    """Return the canonical CHANGE_REASON_REQUIRED envelope.

    Unlike ``mcp_validation.require_change_reason`` (which only fires when
    ``confirm=True``), this tool treats ``confirm=False`` as an error too:
    a write-class tool that can evaluate arbitrary C# must never be run
    without an explicit audited reason.
    """
    return {
        "success": False,
        "severity": "error",
        "code": "CHANGE_REASON_REQUIRED",
        "message": (
            "editor_run_script requires confirm=True AND a non-empty "
            "change_reason (audit trail)."
        ),
        "data": {},
        "diagnostics": [],
    }


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


def editor_run_script(
    code: str,
    confirm: bool,
    change_reason: str | None,
    compile_timeout_ms: int = DEFAULT_COMPILE_TIMEOUT_MS,
) -> dict[str, Any]:
    """Compile and execute a C# snippet inside the Unity Editor.

    Parameters
    ----------
    code:
        C# source.  The bridge writes this to
        ``Assets/Editor/_PrefabSentinelTemp/<temp_id>.cs`` and invokes
        ``PrefabSentinelTempScript.Run()`` (``public static void``).  The
        snippet must define that fixed entry point.
    confirm:
        Must be ``True``.  ``False`` returns ``CHANGE_REASON_REQUIRED``.
    change_reason:
        Required, non-empty.  Empty / whitespace / ``None`` returns
        ``CHANGE_REASON_REQUIRED``.
    compile_timeout_ms:
        Bounded compile-pending budget in milliseconds; forwarded to the
        bridge as ``compile_timeout``. Defaults to fifteen seconds.
        Values outside the inclusive
        ``[COMPILE_TIMEOUT_MIN_MS, COMPILE_TIMEOUT_MAX_MS]`` range
        return ``COMPILE_TIMEOUT_OUT_OF_RANGE`` without contacting the
        bridge (issue #127).

    Returns
    -------
    dict
        The Editor Bridge response envelope, unmodified.
    """
    normalized_reason = change_reason.strip() if isinstance(change_reason, str) else ""
    if not confirm or not normalized_reason:
        return _change_reason_required_envelope()

    if (
        compile_timeout_ms < COMPILE_TIMEOUT_MIN_MS
        or compile_timeout_ms > COMPILE_TIMEOUT_MAX_MS
    ):
        return _compile_timeout_out_of_range_envelope(compile_timeout_ms)

    return send_action(
        action="run_script",
        code=code,
        change_reason=normalized_reason,
        compile_timeout=compile_timeout_ms,
    )


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
