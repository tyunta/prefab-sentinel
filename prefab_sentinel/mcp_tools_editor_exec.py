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


def editor_run_script(
    code: str,
    confirm: bool,
    change_reason: str | None,
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

    Returns
    -------
    dict
        The Editor Bridge response envelope, unmodified.
    """
    normalized_reason = change_reason.strip() if isinstance(change_reason, str) else ""
    if not confirm or not normalized_reason:
        return _change_reason_required_envelope()

    return send_action(
        action="run_script",
        code=code,
        change_reason=normalized_reason,
    )


def register_editor_exec_tools(server: FastMCP) -> None:
    """Register ``editor_run_script`` on *server*."""

    @server.tool(name="editor_run_script")
    def _editor_run_script(
        code: str,
        confirm: bool = False,
        change_reason: str = "",
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
        """
        return editor_run_script(
            code=code,
            confirm=confirm,
            change_reason=change_reason or None,
        )
