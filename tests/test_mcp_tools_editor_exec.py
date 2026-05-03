"""T7–T11c: ``editor_run_script`` MCP tool contract tests (issue #74).

The tool's boundary is: reject ``confirm=False`` / absent / blank
``change_reason`` *before* contacting the bridge, and otherwise forward
the request to the Editor Bridge verbatim and return the bridge's
envelope unchanged (dry-run is not supported per the spec).

Tests patch ``prefab_sentinel.mcp_tools_editor_exec.send_action`` so no
real Editor Bridge is required.
"""

from __future__ import annotations

import unittest
from unittest.mock import patch

from prefab_sentinel import mcp_tools_editor_exec, mcp_tools_editor_view


class EditorRunScriptTests(unittest.TestCase):
    """Contract tests for ``editor_run_script``."""

    _SNIPPET = (
        "public static class PrefabSentinelTempScript {"
        "  public static void Run() { }"
        "}"
    )

    def _patch_bridge(self) -> object:
        """Return a patch of ``send_action`` that also records calls.

        Returning the patcher lets each test control the mock's return
        value before the call and assert on arguments after.
        """
        return patch.object(mcp_tools_editor_exec, "send_action")

    # ------------------------------------------------------------------
    # Change-reason gate (T7, T8, T9)
    # ------------------------------------------------------------------

    def test_rejects_without_confirm(self) -> None:
        """T7: ``confirm=False`` must short-circuit without contacting the bridge."""
        with self._patch_bridge() as send:
            resp = mcp_tools_editor_exec.editor_run_script(
                code=self._SNIPPET,
                confirm=False,
                change_reason="test",
            )
        self.assertEqual("CHANGE_REASON_REQUIRED", resp["code"])
        self.assertFalse(resp["success"])
        self.assertEqual("error", resp["severity"])
        send.assert_not_called()

    def test_rejects_without_change_reason(self) -> None:
        """T8: ``change_reason=None`` must short-circuit."""
        with self._patch_bridge() as send:
            resp = mcp_tools_editor_exec.editor_run_script(
                code=self._SNIPPET,
                confirm=True,
                change_reason=None,
            )
        self.assertEqual("CHANGE_REASON_REQUIRED", resp["code"])
        send.assert_not_called()

    def test_rejects_with_blank_change_reason(self) -> None:
        """T9: whitespace-only ``change_reason`` must short-circuit."""
        with self._patch_bridge() as send:
            resp = mcp_tools_editor_exec.editor_run_script(
                code=self._SNIPPET,
                confirm=True,
                change_reason="   \t\n ",
            )
        self.assertEqual("CHANGE_REASON_REQUIRED", resp["code"])
        send.assert_not_called()

    # ------------------------------------------------------------------
    # Forwarding + envelope pass-through (T10, T11, T11b, T11c)
    # ------------------------------------------------------------------

    def test_success_forwards_to_bridge(self) -> None:
        """T10: valid input forwards to ``run_script`` and returns envelope unchanged."""
        bridge_envelope = {
            "success": True,
            "severity": "info",
            "code": "EDITOR_CTRL_RUN_SCRIPT_OK",
            "message": "ran",
            "data": {"stdout": "hello", "exception": None, "executed": True},
            "diagnostics": [],
        }
        with self._patch_bridge() as send:
            send.return_value = bridge_envelope
            resp = mcp_tools_editor_exec.editor_run_script(
                code=self._SNIPPET,
                confirm=True,
                change_reason="smoke",
            )
        self.assertEqual(bridge_envelope, resp)
        send.assert_called_once()
        kwargs = send.call_args.kwargs
        self.assertEqual("run_script", kwargs["action"])
        self.assertEqual(self._SNIPPET, kwargs["code"])
        self.assertEqual("smoke", kwargs["change_reason"])

    def test_compile_failure_propagates(self) -> None:
        """T11: compile-failure envelope is returned unmodified."""
        bridge_envelope = {
            "success": False,
            "severity": "error",
            "code": "EDITOR_CTRL_RUN_SCRIPT_COMPILE",
            "message": "compile failed",
            "data": {"errors": ["CS1002: ; expected"]},
            "diagnostics": [],
        }
        with self._patch_bridge() as send:
            send.return_value = bridge_envelope
            resp = mcp_tools_editor_exec.editor_run_script(
                code="intentional garbage",
                confirm=True,
                change_reason="repro compile error",
            )
        self.assertEqual(bridge_envelope, resp)

    def test_invalid_temp_id_rejected(self) -> None:
        """T11b: bridge's BAD_ID envelope is returned unmodified."""
        bridge_envelope = {
            "success": False,
            "severity": "error",
            "code": "EDITOR_CTRL_RUN_SCRIPT_BAD_ID",
            "message": "temp id contains path separator",
            "data": {},
            "diagnostics": [],
        }
        with self._patch_bridge() as send:
            send.return_value = bridge_envelope
            resp = mcp_tools_editor_exec.editor_run_script(
                code=self._SNIPPET,
                confirm=True,
                change_reason="path-traversal repro",
            )
        self.assertEqual(bridge_envelope, resp)
        self.assertFalse(resp["success"])
        self.assertEqual("error", resp["severity"])

    def test_runtime_exception_propagates(self) -> None:
        """T11c: RUNTIME envelope with exception/executed fields passes through."""
        bridge_envelope = {
            "success": False,
            "severity": "error",
            "code": "EDITOR_CTRL_RUN_SCRIPT_RUNTIME",
            "message": "Run() threw",
            "data": {
                "exception": "System.InvalidOperationException: boom",
                "executed": True,
            },
            "diagnostics": [],
        }
        with self._patch_bridge() as send:
            send.return_value = bridge_envelope
            resp = mcp_tools_editor_exec.editor_run_script(
                code=self._SNIPPET,
                confirm=True,
                change_reason="runtime exception repro",
            )
        self.assertEqual(bridge_envelope, resp)
        self.assertFalse(resp["success"])
        self.assertEqual("error", resp["severity"])


class EditorRunScriptDefaultsTests(unittest.TestCase):
    """Issue #116 — the wrapper forwards a 15s default ``compile_timeout``."""

    _SNIPPET = (
        "public static class PrefabSentinelTempScript {"
        "  public static void Run() { }"
        "}"
    )

    _BRIDGE_OK = {
        "success": True,
        "severity": "info",
        "code": "EDITOR_CTRL_RUN_SCRIPT_OK",
        "message": "ran",
        "data": {"executed": True},
        "diagnostics": [],
    }

    def test_editor_run_script_default_timeout_is_15s(self) -> None:
        with patch.object(mcp_tools_editor_exec, "send_action") as send:
            send.return_value = self._BRIDGE_OK
            mcp_tools_editor_exec.editor_run_script(
                code=self._SNIPPET,
                confirm=True,
                change_reason="default-timeout check",
            )
        kwargs = send.call_args.kwargs
        self.assertEqual(15000, kwargs["compile_timeout"])

    def test_editor_run_script_forwards_explicit_timeout(self) -> None:
        with patch.object(mcp_tools_editor_exec, "send_action") as send:
            send.return_value = self._BRIDGE_OK
            mcp_tools_editor_exec.editor_run_script(
                code=self._SNIPPET,
                confirm=True,
                change_reason="explicit-timeout check",
                compile_timeout_ms=30000,
            )
        kwargs = send.call_args.kwargs
        self.assertEqual(30000, kwargs["compile_timeout"])


class TestEditorRecompileForceReimport(unittest.TestCase):
    """Task 12: Python recompile wrapper forwards ``force_reimport`` to bridge."""

    _BRIDGE_OK = {
        "success": True,
        "severity": "info",
        "code": "EDITOR_CTRL_RECOMPILE_OK",
        "message": "ok",
        "data": {"executed": True},
        "diagnostics": [],
    }

    def test_default_forwards_false(self) -> None:
        with patch.object(mcp_tools_editor_view, "send_action") as send:
            send.return_value = self._BRIDGE_OK
            mcp_tools_editor_view.editor_recompile()
        send.assert_called_once_with(
            action="recompile_scripts", force_reimport=False
        )

    def test_explicit_true_forwards_true(self) -> None:
        with patch.object(mcp_tools_editor_view, "send_action") as send:
            send.return_value = self._BRIDGE_OK
            mcp_tools_editor_view.editor_recompile(force_reimport=True)
        send.assert_called_once_with(
            action="recompile_scripts", force_reimport=True
        )


if __name__ == "__main__":
    unittest.main()
