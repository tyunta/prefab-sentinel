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
from contextlib import AbstractContextManager
from unittest.mock import MagicMock, patch

from prefab_sentinel import mcp_tools_editor_exec, mcp_tools_editor_view


class EditorRunScriptTests(unittest.TestCase):
    """Contract tests for ``editor_run_script``."""

    _SNIPPET = (
        "public static class PrefabSentinelTempScript {"
        "  public static void Run() { }"
        "}"
    )

    def _patch_bridge(self) -> AbstractContextManager[MagicMock]:
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
        # #222 Phase 1: paired-tuple value-pin so the writer-gate envelope
        # is asserted as one fact (code, severity, success) rather than
        # three independent claims; the message names the behaviour the
        # tuple pins so a failure surfaces the intent, not the field.
        self.assertEqual(
            ("CHANGE_REASON_REQUIRED", "error", False),
            (resp["code"], resp["severity"], resp["success"]),
            msg=(
                "confirm=False must short-circuit with CHANGE_REASON_REQUIRED "
                "rejection envelope; bridge must not be contacted."
            ),
        )
        send.assert_not_called()

    def test_rejects_without_change_reason(self) -> None:
        """T8: ``change_reason=None`` must short-circuit."""
        with self._patch_bridge() as send:
            resp = mcp_tools_editor_exec.editor_run_script(
                code=self._SNIPPET,
                confirm=True,
                change_reason=None,
            )
        self.assertEqual(
            "CHANGE_REASON_REQUIRED", resp["code"],
            msg="change_reason=None must short-circuit with CHANGE_REASON_REQUIRED.",
        )
        send.assert_not_called()

    def test_rejects_with_blank_change_reason(self) -> None:
        """T9: whitespace-only ``change_reason`` must short-circuit."""
        with self._patch_bridge() as send:
            resp = mcp_tools_editor_exec.editor_run_script(
                code=self._SNIPPET,
                confirm=True,
                change_reason="   \t\n ",
            )
        self.assertEqual(
            "CHANGE_REASON_REQUIRED", resp["code"],
            msg="whitespace-only change_reason must short-circuit with CHANGE_REASON_REQUIRED.",
        )
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


class EditorRunScriptResultChannelTests(unittest.TestCase):
    _SNIPPET = (
        "public static class PrefabSentinelTempScript {"
        "  public static int Run() { return 7; }"
        "}"
    )

    def test_run_script_returns_stdout_return_value_and_outputs(self) -> None:
        bridge_envelope = {
            "success": True,
            "severity": "info",
            "code": "EDITOR_CTRL_RUN_SCRIPT_OK",
            "message": "ran",
            "data": {
                "stdout": "hello\n",
                "return_value": {"kind": "number", "number_value": 7},
                "outputs": [
                    {"key": "label", "value": {"kind": "string", "string_value": "WatchingButton"}},
                    {"key": "visible", "value": {"kind": "bool", "bool_value": True}},
                ],
                "executed": True,
            },
            "diagnostics": [],
        }
        with patch.object(mcp_tools_editor_exec, "send_action", return_value=bridge_envelope):
            response = mcp_tools_editor_exec.editor_run_script(
                code=self._SNIPPET,
                confirm=True,
                change_reason="capture result channels",
            )

        self.assertEqual(
            ("hello\n", 7, {"label": "WatchingButton", "visible": True}),
            (
                response["data"]["stdout"],
                response["data"]["return_value"],
                response["data"]["outputs"],
            ),
            msg=f"run-script result channels mismatch: {response!r}",
        )

    def test_run_script_submit_and_poll_share_terminal_channels(self) -> None:
        request_id = "0123456789abcdef0123456789abcdef"
        poll_envelope = {
            "success": True,
            "severity": "info",
            "code": "EDITOR_CTRL_RUN_SCRIPT_POLL_COMPLETED",
            "message": "completed",
            "data": {
                "request_id": request_id,
                "status": "completed",
                "stdout": "async\n",
                "return_value": {"kind": "string", "string_value": "done"},
                "outputs": [
                    {"key": "count", "value": {"kind": "number", "number_value": 2}},
                ],
            },
            "diagnostics": [],
        }
        with patch.object(mcp_tools_editor_exec, "send_action", return_value=poll_envelope):
            response = mcp_tools_editor_exec.editor_run_script_poll(request_id)

        self.assertEqual(
            ("async\n", "done", {"count": 2}),
            (
                response["data"]["stdout"],
                response["data"]["return_value"],
                response["data"]["outputs"],
            ),
            msg=f"async run-script terminal channels mismatch: {response!r}",
        )

    def test_run_script_poll_runtime_failure_keeps_terminal_channels(self) -> None:
        request_id = "0123456789abcdef0123456789abcdef"
        poll_envelope = {
            "success": False,
            "severity": "error",
            "code": "EDITOR_CTRL_RUN_SCRIPT_RUNTIME",
            "message": "runtime failed",
            "data": {
                "request_id": request_id,
                "status": "failed",
                "stdout": "before failure\n",
                "exception": {
                    "type": "InvalidOperationException",
                    "message": "boom",
                    "short_stack": "Thrower.Run",
                },
            },
            "diagnostics": [],
        }
        with patch.object(mcp_tools_editor_exec, "send_action", return_value=poll_envelope):
            response = mcp_tools_editor_exec.editor_run_script_poll(request_id)

        self.assertEqual(
            (
                False,
                "error",
                "EDITOR_CTRL_RUN_SCRIPT_RUNTIME",
                "failed",
                {
                    "type": "InvalidOperationException",
                    "message": "boom",
                    "short_stack": "Thrower.Run",
                },
            ),
            (
                response["success"],
                response["severity"],
                response["code"],
                response["data"]["status"],
                response["data"]["exception"],
            ),
            msg=f"async runtime failure channels mismatch: {response!r}",
        )

    def test_run_script_runtime_exception_is_structured_and_redacted(self) -> None:
        bridge_envelope = {
            "success": False,
            "severity": "error",
            "code": "EDITOR_CTRL_RUN_SCRIPT_RUNTIME",
            "message": "run_script: Run() threw a runtime exception.",
            "data": {
                "stdout": "before\n",
                "exception": {
                    "type": "System.InvalidOperationException",
                    "message": "failed at <wsl-path>",
                    "short_stack": "at PrefabSentinelTempScript.Run()",
                },
                "path_hints": [
                    {
                        "detected_path": "/mnt/c/project/Assets/Scene.unity",
                        "windows_path": "C:\\project\\Assets\\Scene.unity",
                        "asset_relative_path": "Assets/Scene.unity",
                        "application_data_path": "Application.dataPath + \"/Scene.unity\"",
                    }
                ],
            },
            "diagnostics": [],
        }
        with patch.object(mcp_tools_editor_exec, "send_action", return_value=bridge_envelope):
            response = mcp_tools_editor_exec.editor_run_script(
                code=self._SNIPPET,
                confirm=True,
                change_reason="capture runtime exception",
            )

        self.assertEqual(
            (
                "System.InvalidOperationException",
                "failed at <wsl-path>",
                "Assets/Scene.unity",
            ),
            (
                response["data"]["exception"]["type"],
                response["data"]["exception"]["message"],
                response["data"]["path_hints"][0]["asset_relative_path"],
            ),
            msg=f"runtime exception channel mismatch: {response!r}",
        )
        self.assertNotIn("/mnt/c/project", str(response["data"]["exception"]))


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
        # #222 Phase 1: paired-tuple value-pin for the rejection
        # envelope so the boundary outcome is asserted as one fact.
        self.assertEqual(
            ("COMPILE_TIMEOUT_OUT_OF_RANGE", "error", False),
            (resp["code"], resp["severity"], resp["success"]),
            msg=(
                "120001 ms exceeds the inclusive upper bound; the wrapper "
                "must reject pre-bridge with COMPILE_TIMEOUT_OUT_OF_RANGE."
            ),
        )
        # Message must name the supplied value and both bounds.
        self.assertIn(
            "120001", resp["message"],
            msg="rejection message must echo the supplied value 120001.",
        )
        self.assertIn(
            "1", resp["message"],
            msg="rejection message must echo the inclusive lower bound 1.",
        )
        self.assertIn(
            "120000", resp["message"],
            msg="rejection message must echo the inclusive upper bound 120000.",
        )
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

    def test_out_of_range_short_circuits_before_transport_alignment(self) -> None:
        """Issue #226 — the new transport-alignment path must not bypass the
        existing inclusive range gate. An out-of-range compile budget is
        rejected with the existing range envelope and the bridge transport
        is never contacted, even though the wrapper now also derives a
        transport budget from the same value.
        """
        with patch.object(mcp_tools_editor_exec, "send_action") as send:
            resp = mcp_tools_editor_exec.editor_run_script(
                code=self._SNIPPET,
                confirm=True,
                change_reason="range gate",
                compile_timeout_ms=mcp_tools_editor_exec.COMPILE_TIMEOUT_MAX_MS + 1,
            )
        self.assertFalse(resp["success"])
        self.assertEqual("COMPILE_TIMEOUT_OUT_OF_RANGE", resp["code"])
        send.assert_not_called()


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

    def test_default_timeout_forwards_60s(self) -> None:
        with patch.object(mcp_tools_editor_view, "send_action") as send:
            send.return_value = self._BRIDGE_OK
            mcp_tools_editor_view.editor_recompile()
        send.assert_called_once()
        kwargs = send.call_args.kwargs
        self.assertEqual("editor_recompile_and_wait", kwargs["action"])
        self.assertEqual({"timeout_sec": 60.0}, kwargs["request_extras"])

    def test_explicit_timeout_forwards(self) -> None:
        with patch.object(mcp_tools_editor_view, "send_action") as send:
            send.return_value = self._BRIDGE_OK
            mcp_tools_editor_view.editor_recompile(timeout_sec=42.0)
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

    def test_zero_rejected(self) -> None:
        with patch.object(mcp_tools_editor_view, "send_action") as send:
            resp = mcp_tools_editor_view.editor_recompile(timeout_sec=0.0)
        self.assertFalse(resp["success"])
        self.assertEqual("error", resp["severity"])
        self.assertEqual("COMPILE_TIMEOUT_OUT_OF_RANGE", resp["code"])
        send.assert_not_called()

    def test_negative_rejected(self) -> None:
        with patch.object(mcp_tools_editor_view, "send_action") as send:
            resp = mcp_tools_editor_view.editor_recompile(timeout_sec=-1.0)
        self.assertEqual("COMPILE_TIMEOUT_OUT_OF_RANGE", resp["code"])
        send.assert_not_called()

    def test_above_maximum_rejected(self) -> None:
        far_out = 1801.0
        with patch.object(mcp_tools_editor_view, "send_action") as send:
            resp = mcp_tools_editor_view.editor_recompile(timeout_sec=far_out)
        self.assertFalse(resp["success"])
        self.assertEqual("error", resp["severity"])
        self.assertEqual("COMPILE_TIMEOUT_OUT_OF_RANGE", resp["code"])
        # Message must name the supplied value and the upper bound.
        self.assertIn("1801", resp["message"])
        self.assertIn("1800", resp["message"])
        send.assert_not_called()

    def test_far_above_maximum_rejected(self) -> None:
        with patch.object(mcp_tools_editor_view, "send_action") as send:
            resp = mcp_tools_editor_view.editor_recompile(timeout_sec=99999.0)
        self.assertEqual("COMPILE_TIMEOUT_OUT_OF_RANGE", resp["code"])
        send.assert_not_called()

    def test_accepts_smallest_positive_in_range(self) -> None:
        """The lower bound is exclusive at zero; any positive float forwards."""
        with patch.object(mcp_tools_editor_view, "send_action") as send:
            send.return_value = self._BRIDGE_OK
            mcp_tools_editor_view.editor_recompile(timeout_sec=1.0)
        send.assert_called_once()
        self.assertEqual({"timeout_sec": 1.0}, send.call_args.kwargs["request_extras"])

    def test_accepts_upper_boundary(self) -> None:
        with patch.object(mcp_tools_editor_view, "send_action") as send:
            send.return_value = self._BRIDGE_OK
            mcp_tools_editor_view.editor_recompile(
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


class EditorRunScriptTransportBudgetTests(unittest.TestCase):
    """Issue #226 — the run-script wrapper computes a transport poll budget
    that always outlives the bridge's compile-plus-entry-type deadline.

    The published lower floor is the transport's pre-existing default
    (``editor_bridge.DEFAULT_TIMEOUT_SEC`` = 30 s); the dispatch margin
    that buffers transport over compile is the published constant
    ``RUN_SCRIPT_TRANSPORT_DISPATCH_MARGIN_SEC``. The transport budget is
    ``max(floor, ceil(compile_timeout_ms / 1000) + margin)`` so a tiny
    compile budget can never drag transport below the floor and a long
    compile budget can never undercut the bridge's own deadline.
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

    def test_default_compile_budget_pins_transport_at_floor(self) -> None:
        """Default compile budget (15 s + 5 s margin) sits below the 30 s floor."""
        with patch.object(mcp_tools_editor_exec, "send_action") as send:
            send.return_value = self._BRIDGE_OK
            mcp_tools_editor_exec.editor_run_script(
                code=self._SNIPPET,
                confirm=True,
                change_reason="floor pin",
            )
        kwargs = send.call_args.kwargs
        self.assertEqual(
            mcp_tools_editor_exec.RUN_SCRIPT_TRANSPORT_TIMEOUT_FLOOR_SEC,
            kwargs["timeout_sec"],
        )

    def test_upper_bound_compile_budget_outlives_bridge_deadline(self) -> None:
        """120 s compile budget produces a transport budget above the floor.

        At the upper bound, transport must equal compile + dispatch margin
        because the bridge's own compile deadline already exceeds the floor.
        """
        with patch.object(mcp_tools_editor_exec, "send_action") as send:
            send.return_value = self._BRIDGE_OK
            mcp_tools_editor_exec.editor_run_script(
                code=self._SNIPPET,
                confirm=True,
                change_reason="upper bound",
                compile_timeout_ms=120000,
            )
        kwargs = send.call_args.kwargs
        expected = (
            120
            + mcp_tools_editor_exec.RUN_SCRIPT_TRANSPORT_DISPATCH_MARGIN_SEC
        )
        self.assertEqual(expected, kwargs["timeout_sec"])
        self.assertGreater(
            kwargs["timeout_sec"],
            mcp_tools_editor_exec.RUN_SCRIPT_TRANSPORT_TIMEOUT_FLOOR_SEC,
        )

    def test_mid_range_compile_budget_tracks_compile_plus_margin(self) -> None:
        """A 60 s compile budget produces 60 + margin transport budget."""
        with patch.object(mcp_tools_editor_exec, "send_action") as send:
            send.return_value = self._BRIDGE_OK
            mcp_tools_editor_exec.editor_run_script(
                code=self._SNIPPET,
                confirm=True,
                change_reason="mid range",
                compile_timeout_ms=60000,
            )
        kwargs = send.call_args.kwargs
        expected = (
            60
            + mcp_tools_editor_exec.RUN_SCRIPT_TRANSPORT_DISPATCH_MARGIN_SEC
        )
        self.assertEqual(expected, kwargs["timeout_sec"])

    def test_sub_floor_compile_budget_pins_transport_at_floor(self) -> None:
        """The smallest in-range compile budget (1 ms) still pins to floor."""
        with patch.object(mcp_tools_editor_exec, "send_action") as send:
            send.return_value = self._BRIDGE_OK
            mcp_tools_editor_exec.editor_run_script(
                code=self._SNIPPET,
                confirm=True,
                change_reason="boundary check",
                compile_timeout_ms=1,
            )
        kwargs = send.call_args.kwargs
        self.assertEqual(
            mcp_tools_editor_exec.RUN_SCRIPT_TRANSPORT_TIMEOUT_FLOOR_SEC,
            kwargs["timeout_sec"],
        )


class EditorRunScriptTransportTimeoutRewriteTests(unittest.TestCase):
    """Issue #226 — when the bridge surfaces a generic transport timeout
    (``EDITOR_BRIDGE_TIMEOUT``) for a run-script call, the wrapper rewrites
    it as ``EDITOR_RUN_SCRIPT_TRANSPORT_TIMEOUT`` so the caller can
    distinguish a run-script-specific budget overflow from a generic
    bridge crash. Non-timeout responses pass through unchanged.
    """

    _SNIPPET = (
        "public static class PrefabSentinelTempScript {"
        "  public static void Run() { }"
        "}"
    )

    def test_transport_timeout_rewritten_to_run_script_specific(self) -> None:
        """Generic bridge timeout becomes the run-script-specific code with
        a message that names both budget values and the upper bound.
        """
        bridge_timeout = {
            "success": False,
            "severity": "error",
            "code": "EDITOR_BRIDGE_TIMEOUT",
            "message": "Editor bridge response timed out.",
            "data": {
                "action": "run_script",
                "timeout_sec": 30,
                "request_file": "/tmp/some-id.request.json",
            },
            "diagnostics": [],
        }
        with patch.object(mcp_tools_editor_exec, "send_action") as send:
            send.return_value = bridge_timeout
            resp = mcp_tools_editor_exec.editor_run_script(
                code=self._SNIPPET,
                confirm=True,
                change_reason="bridge stall",
                compile_timeout_ms=60000,
            )
        self.assertFalse(resp["success"])
        self.assertEqual("error", resp["severity"])
        self.assertEqual(
            "EDITOR_RUN_SCRIPT_TRANSPORT_TIMEOUT", resp["code"],
        )
        # Message names the supplied compile budget, the derived
        # transport budget, and the upper-bound retry recommendation.
        self.assertIn("60000", resp["message"])
        expected_transport = (
            60
            + mcp_tools_editor_exec.RUN_SCRIPT_TRANSPORT_DISPATCH_MARGIN_SEC
        )
        self.assertIn(str(expected_transport), resp["message"])
        self.assertIn(
            str(mcp_tools_editor_exec.COMPILE_TIMEOUT_MAX_MS),
            resp["message"],
        )
        # Data exposes both budgets for programmatic decisions.
        self.assertEqual(60000, resp["data"]["compile_timeout_ms"])
        self.assertEqual(expected_transport, resp["data"]["transport_timeout_sec"])

    def test_non_timeout_envelope_passes_through_unchanged(self) -> None:
        bridge_envelope = {
            "success": False,
            "severity": "error",
            "code": "EDITOR_CTRL_RUN_SCRIPT_RUNTIME",
            "message": "Run() threw NullReferenceException",
            "data": {"executed": True, "exception": "NRE"},
            "diagnostics": [],
        }
        with patch.object(mcp_tools_editor_exec, "send_action") as send:
            send.return_value = bridge_envelope
            resp = mcp_tools_editor_exec.editor_run_script(
                code=self._SNIPPET,
                confirm=True,
                change_reason="runtime check",
            )
        self.assertEqual(bridge_envelope, resp)


class EditorRunScriptCompileTimeoutPassThroughTests(unittest.TestCase):
    """Issue #234 Python half: when the bridge surfaces the new dedicated
    ``EDITOR_RUN_SCRIPT_COMPILE_TIMEOUT`` envelope on the compile-deadline
    path, the wrapper returns it unchanged. The wrapper's pre-existing
    rewrite of the generic bridge transport-timeout envelope
    (``EDITOR_BRIDGE_TIMEOUT`` → ``EDITOR_RUN_SCRIPT_TRANSPORT_TIMEOUT``)
    must continue to fire for the transport-timeout case.
    """

    _SNIPPET = (
        "public static class PrefabSentinelTempScript {"
        "  public static void Run() { }"
        "}"
    )

    def test_compile_timeout_envelope_passes_through_unchanged(self) -> None:
        """The new bridge code is forwarded verbatim — not rewritten as
        a transport timeout — so callers can distinguish "compile budget
        exhausted on the bridge" from "Python transport poll outlived
        the bridge".
        """
        bridge_envelope = {
            "success": False,
            "severity": "error",
            "code": "EDITOR_RUN_SCRIPT_COMPILE_TIMEOUT",
            "message": (
                "Script compilation did not complete within the bounded "
                "poll; a domain reload may still be pending..."
            ),
            "data": {
                "temp_id": "abc",
                "executed": False,
                "diagnostic_compiling": True,
                "diagnostic_temp_files": [],
                "diagnostic_last_domain_reload": "2026-05-12T07:00:00Z",
            },
            "diagnostics": [],
        }
        with patch.object(mcp_tools_editor_exec, "send_action") as send:
            send.return_value = bridge_envelope
            resp = mcp_tools_editor_exec.editor_run_script(
                code=self._SNIPPET,
                confirm=True,
                change_reason="compile-timeout pass-through",
                compile_timeout_ms=15000,
            )
        # The wrapper must not rewrite the envelope: the four-field
        # tuple value-pin is the direct specification match for the
        # new pass-through contract and surfaces a partial mutation
        # in the diagnostic. (A whole-dict equality would dominate
        # this tuple pin, so we keep only the spec-aligned form.)
        self.assertEqual(
            ("EDITOR_RUN_SCRIPT_COMPILE_TIMEOUT", False, "error",
             bridge_envelope["message"]),
            (resp["code"], resp["success"], resp["severity"], resp["message"]),
            msg=(
                "Wrapper must forward EDITOR_RUN_SCRIPT_COMPILE_TIMEOUT "
                "verbatim (no rewrite to transport-timeout) so callers "
                "can distinguish bridge-side compile-deadline elapse "
                "from Python transport poll giving up."
            ),
        )

    def test_transport_timeout_envelope_still_rewritten(self) -> None:
        """Regression pin: the pre-existing
        ``EDITOR_BRIDGE_TIMEOUT`` → ``EDITOR_RUN_SCRIPT_TRANSPORT_TIMEOUT``
        rewrite must still fire so the new pass-through path does not
        accidentally suppress the existing rewrite as a side effect.
        """
        bridge_timeout = {
            "success": False,
            "severity": "error",
            "code": "EDITOR_BRIDGE_TIMEOUT",
            "message": "Editor bridge response timed out.",
            "data": {
                "action": "run_script",
                "timeout_sec": 30,
                "request_file": "/tmp/some-id.request.json",
            },
            "diagnostics": [],
        }
        with patch.object(mcp_tools_editor_exec, "send_action") as send:
            send.return_value = bridge_timeout
            resp = mcp_tools_editor_exec.editor_run_script(
                code=self._SNIPPET,
                confirm=True,
                change_reason="transport-rewrite preservation",
                compile_timeout_ms=60000,
            )
        # Code is rewritten; message names both budgets and the
        # retry-recommended upper bound.
        expected_transport = (
            60 + mcp_tools_editor_exec.RUN_SCRIPT_TRANSPORT_DISPATCH_MARGIN_SEC
        )
        self.assertEqual(
            ("EDITOR_RUN_SCRIPT_TRANSPORT_TIMEOUT", False, "error"),
            (resp["code"], resp["success"], resp["severity"]),
            msg="Bridge transport timeout must still rewrite to the "
                "wrapper's run-script-specific code.",
        )
        self.assertIn("60000", resp["message"])
        self.assertIn(str(expected_transport), resp["message"])
        self.assertIn(
            str(mcp_tools_editor_exec.COMPILE_TIMEOUT_MAX_MS),
            resp["message"],
        )


class EditorRunScriptSubmitTests(unittest.TestCase):
    """Issue #233 — ``editor_run_script_submit`` contract.

    Pins:

    * Submit without confirm AND a non-empty change_reason short-circuits
      pre-bridge with ``CHANGE_REASON_REQUIRED``.
    * Valid submit forwards the snippet, the compile-budget knob, and
      the audit pair to the bridge.
    * Compile-budget knob outside the inclusive
      ``[COMPILE_TIMEOUT_MIN_MS, COMPILE_TIMEOUT_MAX_MS]`` band is
      rejected pre-bridge at both ends with
      ``COMPILE_TIMEOUT_OUT_OF_RANGE``.
    """

    _SNIPPET = (
        "public static class PrefabSentinelTempScript {"
        "  public static void Run() { }"
        "}"
    )

    _BRIDGE_OK = {
        "success": True,
        "severity": "info",
        "code": "EDITOR_CTRL_RUN_SCRIPT_SUBMIT_ACCEPTED",
        "message": "accepted",
        "data": {"request_id": "a" * 32, "accepted_at": 0, "status": "pending"},
        "diagnostics": [],
    }

    def test_submit_rejects_without_confirm(self) -> None:
        with patch.object(mcp_tools_editor_exec, "send_action") as send:
            resp = mcp_tools_editor_exec.editor_run_script_submit(
                code=self._SNIPPET,
                confirm=False,
                change_reason="repro",
            )
        send.assert_not_called()
        self.assertEqual(
            ("CHANGE_REASON_REQUIRED", "error", False),
            (resp["code"], resp["severity"], resp["success"]),
            msg=(
                "Submit must short-circuit with CHANGE_REASON_REQUIRED "
                "when confirm is False."
            ),
        )

    def test_submit_forwards_snippet_compile_budget_and_audit_pair(self) -> None:
        with patch.object(mcp_tools_editor_exec, "send_action") as send:
            send.return_value = self._BRIDGE_OK
            mcp_tools_editor_exec.editor_run_script_submit(
                code=self._SNIPPET,
                confirm=True,
                change_reason="long async work",
                compile_timeout_ms=60000,
            )
        send.assert_called_once()
        kwargs = send.call_args.kwargs
        self.assertEqual(
            (
                "run_script_submit", self._SNIPPET, 60000, True,
                "long async work",
            ),
            (
                kwargs["action"], kwargs["code"],
                kwargs["compile_timeout"], kwargs["confirm"],
                kwargs["change_reason"],
            ),
            msg=(
                "Submit must forward the run_script_submit action, "
                "the snippet, the compile budget, confirm=True, and "
                "the audit reason verbatim."
            ),
        )

    def test_submit_compile_budget_below_min_rejected_pre_bridge(self) -> None:
        with patch.object(mcp_tools_editor_exec, "send_action") as send:
            resp = mcp_tools_editor_exec.editor_run_script_submit(
                code=self._SNIPPET,
                confirm=True,
                change_reason="boundary check",
                compile_timeout_ms=(
                    mcp_tools_editor_exec.COMPILE_TIMEOUT_MIN_MS - 1
                ),
            )
        send.assert_not_called()
        self.assertEqual(
            ("COMPILE_TIMEOUT_OUT_OF_RANGE", "error", False),
            (resp["code"], resp["severity"], resp["success"]),
            msg="Below-floor compile budget must be rejected pre-bridge.",
        )

    def test_submit_compile_budget_above_max_rejected_pre_bridge(self) -> None:
        with patch.object(mcp_tools_editor_exec, "send_action") as send:
            resp = mcp_tools_editor_exec.editor_run_script_submit(
                code=self._SNIPPET,
                confirm=True,
                change_reason="boundary check",
                compile_timeout_ms=(
                    mcp_tools_editor_exec.COMPILE_TIMEOUT_MAX_MS + 1
                ),
            )
        send.assert_not_called()
        self.assertEqual(
            ("COMPILE_TIMEOUT_OUT_OF_RANGE", "error", False),
            (resp["code"], resp["severity"], resp["success"]),
            msg="Above-cap compile budget must be rejected pre-bridge.",
        )


class EditorRunScriptPollTests(unittest.TestCase):
    """Issue #233 — ``editor_run_script_poll`` contract.

    Pins:

    * Identifier whose shape is not a 32-char lower-case hex token is
      rejected pre-bridge with ``REQUEST_ID_INVALID``.
    * A valid identifier forwards the request id and the
      cleanup-on-timeout flag verbatim.
    * A bridge envelope carrying a status passes through unchanged.
    """

    _VALID_ID = "0123456789abcdef0123456789abcdef"

    def test_poll_rejects_malformed_identifier(self) -> None:
        with patch.object(mcp_tools_editor_exec, "send_action") as send:
            resp = mcp_tools_editor_exec.editor_run_script_poll(
                request_id="free-text-id",
            )
        send.assert_not_called()
        self.assertEqual(
            ("REQUEST_ID_INVALID", "error", False),
            (resp["code"], resp["severity"], resp["success"]),
            msg=(
                "Malformed request_id must be rejected pre-bridge "
                "with REQUEST_ID_INVALID so free-text cannot leak "
                "across the transport."
            ),
        )

    def test_poll_forwards_identifier_and_cleanup_flag(self) -> None:
        with patch.object(mcp_tools_editor_exec, "send_action") as send:
            send.return_value = {
                "success": True, "severity": "info",
                "code": "OK", "message": "ok",
                "data": {"status": "pending"},
                "diagnostics": [],
            }
            mcp_tools_editor_exec.editor_run_script_poll(
                request_id=self._VALID_ID,
                cleanup_on_timeout=True,
            )
        send.assert_called_once()
        kwargs = send.call_args.kwargs
        self.assertEqual(
            ("run_script_poll", self._VALID_ID, True),
            (
                kwargs["action"], kwargs["request_id"],
                kwargs["cleanup_on_timeout"],
            ),
            msg=(
                "Poll must forward the run_script_poll action, the "
                "supplied request_id, and the cleanup-on-timeout flag "
                "verbatim."
            ),
        )

    def test_poll_surfaces_bridge_status_verbatim(self) -> None:
        with patch.object(mcp_tools_editor_exec, "send_action") as send:
            send.return_value = {
                "success": True, "severity": "info",
                "code": "OK", "message": "ok",
                "data": {"status": "completed", "stdout": "ok"},
                "diagnostics": [],
            }
            resp = mcp_tools_editor_exec.editor_run_script_poll(
                request_id=self._VALID_ID,
            )
        self.assertEqual(
            "completed", resp["data"]["status"],
            msg="Poll must pass the bridge status through unchanged.",
        )


class TestEditorRefreshRequestsCompileAwareness(unittest.TestCase):
    """Issue #70: the ``editor_refresh`` wrapper asks the bridge to wait
    for and report a refresh-triggered compile, and sizes its transport
    poll budget to outlast a compile plus domain reload."""

    _BRIDGE_OK = {
        "success": True,
        "severity": "info",
        "code": "EDITOR_CTRL_REFRESH_OK",
        "message": "ok",
        "data": {"executed": True},
        "diagnostics": [],
    }

    def test_refresh_requests_compile_awareness(self) -> None:
        with patch.object(mcp_tools_editor_view, "send_action") as send:
            send.return_value = self._BRIDGE_OK
            mcp_tools_editor_view.editor_refresh()
        kwargs = send.call_args.kwargs
        self.assertEqual(
            ("refresh_asset_database", True),
            (kwargs["action"], kwargs["wait_for_compile"]),
            msg=(
                "editor_refresh must invoke the refresh_asset_database "
                "action with wait_for_compile=True so the bridge observes "
                "the triggered compile."
            ),
        )

    def test_refresh_transport_budget_covers_compile_and_reload(self) -> None:
        with patch.object(mcp_tools_editor_view, "send_action") as send:
            send.return_value = self._BRIDGE_OK
            mcp_tools_editor_view.editor_refresh()
        # The transport poll budget must outlast a compile + domain reload,
        # matching the recompile-and-wait budget plus the dispatch margin.
        expected = (
            int(mcp_tools_editor_view.RECOMPILE_AND_WAIT_DEFAULT_TIMEOUT_SEC)
            + 5
        )
        self.assertEqual(
            expected,
            send.call_args.kwargs["timeout_sec"],
            msg=(
                "editor_refresh transport budget must cover a compile plus "
                "domain reload"
            ),
        )


if __name__ == "__main__":
    unittest.main()
