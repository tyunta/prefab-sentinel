"""T7–T11c: ``editor_run_script`` MCP tool contract tests (issue #74).

The tool's boundary is: reject ``confirm=False`` / absent / blank
``change_reason`` *before* contacting the bridge, and otherwise forward
the request to the Editor Bridge verbatim and return the bridge's
envelope unchanged (dry-run is not supported per the spec).

Tests patch ``prefab_sentinel.mcp_tools_editor_exec.send_action`` so no
real Editor Bridge is required.
"""

from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from prefab_sentinel import mcp_tools_editor_exec, mcp_tools_editor_view

_BRIDGE_ENV_VARS = ("UNITYTOOL_BRIDGE_MODE", "UNITYTOOL_BRIDGE_WATCH_DIR")


def _drop_bridge_env() -> None:
    """Pop bridge dispatch env vars so unit tests do not leak host bridge state.

    The host workstation may export ``UNITYTOOL_BRIDGE_MODE=editor`` which
    would route requests to a live Editor Bridge mid-test; we always run
    these contract tests in batchmode-equivalent isolation (issue #88, #89).
    """
    for var in _BRIDGE_ENV_VARS:
        os.environ.pop(var, None)


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


class EditorRunScriptCompileTimeoutRangeTests(unittest.TestCase):
    """Issue #127 — the script-runner public surface refuses any
    ``compile_timeout_ms`` outside the inclusive 1..120000 ms range,
    returns a dedicated severity-error envelope, and never contacts
    the bridge in that case.
    """

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

    def test_inclusive_maximum_forwards_to_bridge(self) -> None:
        """120000 ms is accepted and forwarded with the value present."""
        with patch.object(mcp_tools_editor_exec, "send_action") as send:
            send.return_value = self._BRIDGE_OK
            mcp_tools_editor_exec.editor_run_script(
                code=self._SNIPPET,
                confirm=True,
                change_reason="boundary check",
                compile_timeout_ms=120000,
            )
        send.assert_called_once()
        self.assertEqual(120000, send.call_args.kwargs["compile_timeout"])

    def test_one_above_maximum_rejected(self) -> None:
        """120001 ms is rejected with the dedicated out-of-range code."""
        with patch.object(mcp_tools_editor_exec, "send_action") as send:
            resp = mcp_tools_editor_exec.editor_run_script(
                code=self._SNIPPET,
                confirm=True,
                change_reason="boundary check",
                compile_timeout_ms=120001,
            )
        self.assertFalse(resp["success"])
        self.assertEqual("error", resp["severity"])
        self.assertEqual("COMPILE_TIMEOUT_OUT_OF_RANGE", resp["code"])
        # Message must name the supplied value and both bounds.
        self.assertIn("120001", resp["message"])
        self.assertIn("1", resp["message"])
        self.assertIn("120000", resp["message"])
        send.assert_not_called()

    def test_zero_rejected(self) -> None:
        with patch.object(mcp_tools_editor_exec, "send_action") as send:
            resp = mcp_tools_editor_exec.editor_run_script(
                code=self._SNIPPET,
                confirm=True,
                change_reason="boundary check",
                compile_timeout_ms=0,
            )
        self.assertEqual("COMPILE_TIMEOUT_OUT_OF_RANGE", resp["code"])
        send.assert_not_called()

    def test_negative_rejected(self) -> None:
        with patch.object(mcp_tools_editor_exec, "send_action") as send:
            resp = mcp_tools_editor_exec.editor_run_script(
                code=self._SNIPPET,
                confirm=True,
                change_reason="boundary check",
                compile_timeout_ms=-1,
            )
        self.assertEqual("COMPILE_TIMEOUT_OUT_OF_RANGE", resp["code"])
        send.assert_not_called()

    def test_inclusive_minimum_forwards_to_bridge(self) -> None:
        """1 ms is accepted and forwarded with the value present."""
        with patch.object(mcp_tools_editor_exec, "send_action") as send:
            send.return_value = self._BRIDGE_OK
            mcp_tools_editor_exec.editor_run_script(
                code=self._SNIPPET,
                confirm=True,
                change_reason="boundary check",
                compile_timeout_ms=1,
            )
        send.assert_called_once()
        self.assertEqual(1, send.call_args.kwargs["compile_timeout"])


class EditorRecompileAndWaitTests(unittest.TestCase):
    """Issue #118 — synchronous recompile-and-wait MCP tool delegates to
    the editor-bridge transport and forwards the caller-supplied wait
    budget to the bridge as the request payload's ``timeout_sec`` field.
    """

    _BRIDGE_OK = {
        "success": True,
        "severity": "info",
        "code": "EDITOR_CTRL_RECOMPILE_AND_WAIT_OK",
        "message": "ok",
        "data": {"executed": True},
        "diagnostics": [],
    }

    def setUp(self) -> None:
        _drop_bridge_env()

    def test_default_timeout_forwards_60s(self) -> None:
        with patch.object(mcp_tools_editor_view, "send_action") as send:
            send.return_value = self._BRIDGE_OK
            mcp_tools_editor_view.editor_recompile_and_wait()
        send.assert_called_once()
        kwargs = send.call_args.kwargs
        self.assertEqual("editor_recompile_and_wait", kwargs["action"])
        self.assertEqual({"timeout_sec": 60.0}, kwargs["request_extras"])

    def test_explicit_timeout_forwards(self) -> None:
        with patch.object(mcp_tools_editor_view, "send_action") as send:
            send.return_value = self._BRIDGE_OK
            mcp_tools_editor_view.editor_recompile_and_wait(timeout_sec=42.0)
        kwargs = send.call_args.kwargs
        self.assertEqual({"timeout_sec": 42.0}, kwargs["request_extras"])


class EditorRecompileAndWaitTimeoutRangeTests(unittest.TestCase):
    """Issue #134 — the recompile-and-wait public surface refuses any
    ``timeout_sec`` outside the inclusive published acceptance range,
    returns the ``COMPILE_TIMEOUT_OUT_OF_RANGE`` envelope, and never
    contacts the bridge in that case.  In-range values forward to the
    bridge unchanged; both boundaries (smallest positive, upper bound)
    are accepted.
    """

    _BRIDGE_OK = {
        "success": True,
        "severity": "info",
        "code": "EDITOR_CTRL_RECOMPILE_AND_WAIT_OK",
        "message": "ok",
        "data": {"executed": True},
        "diagnostics": [],
    }

    def setUp(self) -> None:
        _drop_bridge_env()

    def test_zero_rejected(self) -> None:
        with patch.object(mcp_tools_editor_view, "send_action") as send:
            resp = mcp_tools_editor_view.editor_recompile_and_wait(timeout_sec=0.0)
        self.assertFalse(resp["success"])
        self.assertEqual("error", resp["severity"])
        self.assertEqual("COMPILE_TIMEOUT_OUT_OF_RANGE", resp["code"])
        send.assert_not_called()

    def test_negative_rejected(self) -> None:
        with patch.object(mcp_tools_editor_view, "send_action") as send:
            resp = mcp_tools_editor_view.editor_recompile_and_wait(timeout_sec=-1.0)
        self.assertEqual("COMPILE_TIMEOUT_OUT_OF_RANGE", resp["code"])
        send.assert_not_called()

    def test_above_maximum_rejected(self) -> None:
        far_out = 1801.0
        with patch.object(mcp_tools_editor_view, "send_action") as send:
            resp = mcp_tools_editor_view.editor_recompile_and_wait(timeout_sec=far_out)
        self.assertFalse(resp["success"])
        self.assertEqual("error", resp["severity"])
        self.assertEqual("COMPILE_TIMEOUT_OUT_OF_RANGE", resp["code"])
        # Message must name the supplied value and the upper bound.
        self.assertIn("1801", resp["message"])
        self.assertIn("1800", resp["message"])
        send.assert_not_called()

    def test_far_above_maximum_rejected(self) -> None:
        with patch.object(mcp_tools_editor_view, "send_action") as send:
            resp = mcp_tools_editor_view.editor_recompile_and_wait(timeout_sec=99999.0)
        self.assertEqual("COMPILE_TIMEOUT_OUT_OF_RANGE", resp["code"])
        send.assert_not_called()

    def test_accepts_smallest_positive_in_range(self) -> None:
        """The lower bound is exclusive at zero; any positive float forwards."""
        with patch.object(mcp_tools_editor_view, "send_action") as send:
            send.return_value = self._BRIDGE_OK
            mcp_tools_editor_view.editor_recompile_and_wait(timeout_sec=1.0)
        send.assert_called_once()
        self.assertEqual({"timeout_sec": 1.0}, send.call_args.kwargs["request_extras"])

    def test_accepts_upper_boundary(self) -> None:
        with patch.object(mcp_tools_editor_view, "send_action") as send:
            send.return_value = self._BRIDGE_OK
            mcp_tools_editor_view.editor_recompile_and_wait(
                timeout_sec=mcp_tools_editor_view.RECOMPILE_AND_WAIT_TIMEOUT_MAX_SEC,
            )
        send.assert_called_once()
        self.assertEqual(
            {"timeout_sec": mcp_tools_editor_view.RECOMPILE_AND_WAIT_TIMEOUT_MAX_SEC},
            send.call_args.kwargs["request_extras"],
        )


class EditorConsoleMaxEntriesValidationTests(unittest.TestCase):
    """Issue #131 — the editor_console MCP tool rejects ``max_entries``
    outside the inclusive ``[1, CONSOLE_MAX_ENTRIES_MAX]`` range with
    the canonical ``MAX_ENTRIES_OUT_OF_RANGE`` envelope and never
    contacts the bridge in that case.  In-range values forward to the
    bridge unchanged; the boundary values (1 and the upper bound) are
    accepted.
    """

    _BRIDGE_OK = {
        "success": True,
        "severity": "info",
        "code": "EDITOR_CTRL_CONSOLE_OK",
        "message": "ok",
        "data": {"entries": [], "executed": True},
        "diagnostics": [],
    }

    def setUp(self) -> None:
        _drop_bridge_env()

    def test_rejects_above_upper_bound(self) -> None:
        with patch.object(mcp_tools_editor_view, "send_action") as send:
            resp = mcp_tools_editor_view.editor_console(max_entries=1001)
        self.assertFalse(resp["success"])
        self.assertEqual("error", resp["severity"])
        self.assertEqual("MAX_ENTRIES_OUT_OF_RANGE", resp["code"])
        self.assertIn("1001", resp["message"])
        self.assertIn("1000", resp["message"])
        send.assert_not_called()

    def test_rejects_zero(self) -> None:
        with patch.object(mcp_tools_editor_view, "send_action") as send:
            resp = mcp_tools_editor_view.editor_console(max_entries=0)
        self.assertEqual("MAX_ENTRIES_OUT_OF_RANGE", resp["code"])
        send.assert_not_called()

    def test_rejects_negative(self) -> None:
        with patch.object(mcp_tools_editor_view, "send_action") as send:
            resp = mcp_tools_editor_view.editor_console(max_entries=-1)
        self.assertEqual("MAX_ENTRIES_OUT_OF_RANGE", resp["code"])
        send.assert_not_called()

    def test_accepts_lower_boundary(self) -> None:
        with patch.object(mcp_tools_editor_view, "send_action") as send:
            send.return_value = self._BRIDGE_OK
            mcp_tools_editor_view.editor_console(max_entries=1)
        send.assert_called_once()
        self.assertEqual(1, send.call_args.kwargs["max_entries"])

    def test_accepts_upper_boundary(self) -> None:
        with patch.object(mcp_tools_editor_view, "send_action") as send:
            send.return_value = self._BRIDGE_OK
            mcp_tools_editor_view.editor_console(
                max_entries=mcp_tools_editor_view.CONSOLE_MAX_ENTRIES_MAX,
            )
        send.assert_called_once()
        self.assertEqual(
            mcp_tools_editor_view.CONSOLE_MAX_ENTRIES_MAX,
            send.call_args.kwargs["max_entries"],
        )

    def test_accepts_normal_value(self) -> None:
        with patch.object(mcp_tools_editor_view, "send_action") as send:
            send.return_value = self._BRIDGE_OK
            mcp_tools_editor_view.editor_console(max_entries=200)
        send.assert_called_once()
        self.assertEqual(200, send.call_args.kwargs["max_entries"])


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
