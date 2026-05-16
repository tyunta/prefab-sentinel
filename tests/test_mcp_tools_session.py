"""Contract tests for ``activate_project`` severity + ``get_project_status``
editor-state surfaces.

Two blocks:

* Activation severity block — pins the response envelope's overall
  severity composition rule (issue #244).
* Status editor-state block — pins the new ``data.editor_state`` field
  population rules (issue #239).
"""

from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from prefab_sentinel import mcp_tools_session
from prefab_sentinel.session import ProjectSession


def _make_project(tmp: tempfile.TemporaryDirectory) -> tuple[Path, Path]:
    """Return ``(project_root, scope)`` with an Assets/ subdirectory."""
    project_root = Path(tmp.name)
    scope = project_root / "Assets" / "MyScope"
    scope.mkdir(parents=True)
    return project_root, scope


def _write_bridge_cs(project_root: Path, version: str) -> None:
    """Drop a minimal bridge .cs file with the requested version literal."""
    bridge_dir = project_root / "Assets" / "Editor"
    bridge_dir.mkdir(parents=True, exist_ok=True)
    cs = bridge_dir / "PrefabSentinel.UnityEditorControlBridge.cs"
    cs.write_text(
        f'public const string BridgeVersion = "{version}";',
        encoding="utf-8",
    )


def _register_session_tools(session: ProjectSession) -> dict[str, callable]:
    """Register session tools against a recording stub server."""
    registered: dict[str, callable] = {}

    class _Server:
        def tool(self_inner) -> callable:  # noqa: N805
            def deco(fn: callable) -> callable:
                registered[fn.__name__] = fn
                return fn

            return deco

    mcp_tools_session.register_session_tools(_Server(), session)
    return registered


def _patch_session_layer():
    """Common patch set for the session boundary symbols."""
    return [
        patch(
            "prefab_sentinel.session_cache.build_script_name_map",
            return_value={},
        ),
        patch("prefab_sentinel.session_cache.Phase1Orchestrator"),
    ]


class ActivationSeverityBlock(unittest.TestCase):
    """``activate_project`` envelope severity composition (issue #244)."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.project_root, self.scope = _make_project(self._tmp)
        self._patches = _patch_session_layer() + [
            patch(
                "prefab_sentinel.session.resolve_scope_path",
                return_value=self.scope,
            ),
            patch(
                "prefab_sentinel.session.find_project_root",
                return_value=self.project_root,
            ),
        ]
        for p in self._patches:
            p.start()
            self.addCleanup(p.stop)

    def _run_activate(self) -> dict:
        session = ProjectSession(project_root=self.project_root)
        registered = _register_session_tools(session)
        activate = registered["activate_project"]
        return asyncio.run(
            activate(scope=str(self.scope), project_root=str(self.project_root)),
        )

    def test_version_mismatch_escalates_envelope_severity(self) -> None:
        # Plant a bridge .cs with a version that won't match the
        # installed package version; the activation envelope must
        # escalate to warning severity and surface the diagnostic.
        _write_bridge_cs(self.project_root, "0.0.1")
        response = self._run_activate()
        codes = [d.get("code") for d in response["diagnostics"]]
        self.assertEqual(
            (True, "warning", True),
            (
                response["success"],
                response["severity"],
                "BRIDGE_VERSION_MISMATCH" in codes,
            ),
        )

    def test_missing_bridge_escalates_envelope_severity(self) -> None:
        # No bridge .cs anywhere under the project; the activation
        # envelope must escalate to warning severity and surface the
        # BRIDGE_NOT_FOUND diagnostic.
        response = self._run_activate()
        codes = [d.get("code") for d in response["diagnostics"]]
        self.assertEqual(
            (True, "warning", True),
            (
                response["success"],
                response["severity"],
                "BRIDGE_NOT_FOUND" in codes,
            ),
        )

    def test_unknown_severity_string_is_floored_to_info(self) -> None:
        # ``_compose_envelope_severity`` maps an unrecognized severity
        # string (defensive ``ValueError`` branch) to ``info`` so an
        # unexpected diagnostic entry never silently escalates the
        # envelope.  Pin the value-floor explicitly so the branch is
        # exercised.
        from prefab_sentinel.mcp_tools_session import _compose_envelope_severity

        result = _compose_envelope_severity(
            [{"severity": "totally_bogus_unknown"}],
        )
        self.assertEqual("info", result)

    def test_matching_bridge_keeps_envelope_informational(self) -> None:
        # Plant a bridge .cs whose version matches the installed
        # package; the activation envelope must stay at info severity
        # and must not include a bridge-version diagnostic.
        from importlib.metadata import version

        _write_bridge_cs(self.project_root, version("prefab-sentinel"))
        response = self._run_activate()
        codes = [d.get("code") for d in response["diagnostics"]]
        self.assertEqual(
            (
                True,
                "info",
                False,
                False,
            ),
            (
                response["success"],
                response["severity"],
                "BRIDGE_VERSION_MISMATCH" in codes,
                "BRIDGE_NOT_FOUND" in codes,
            ),
        )


class StatusEditorStateBlock(unittest.TestCase):
    """``get_project_status`` ``data.editor_state`` (issue #239)."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.project_root, self.scope = _make_project(self._tmp)

    def _run_status(self) -> dict:
        session = ProjectSession(project_root=self.project_root)
        registered = _register_session_tools(session)
        get_status = registered["get_project_status"]
        return get_status()

    def test_disconnected_bridge_yields_absent_editor_state(self) -> None:
        # When bridge_status() reports disconnected, the tool must not
        # invoke send_action and the editor_state field must be null.
        with (
            patch.object(
                mcp_tools_session,
                "bridge_status",
                return_value={"connected": False, "mode": None, "watch_dir": None},
            ),
            patch.object(mcp_tools_session, "send_action") as send,
        ):
            response = self._run_status()

        send.assert_not_called()
        self.assertIsNone(response["data"]["editor_state"])

    def test_connected_bridge_returns_four_flag_snapshot(self) -> None:
        # A connected bridge yields the four-flag snapshot exactly as
        # returned by the bridge; no extra keys, no field-name drift.
        snapshot = {
            "is_playing": True,
            "is_will_change_playmode": False,
            "is_compiling": True,
            "is_building_player": False,
        }
        bridge_envelope = {
            "success": True,
            "severity": "info",
            "code": "EDITOR_CTRL_EDITOR_STATE_OK",
            "message": "ok",
            "data": {"editor_state": snapshot},
            "diagnostics": [],
        }
        with (
            patch.object(
                mcp_tools_session,
                "bridge_status",
                return_value={
                    "connected": True,
                    "mode": "editor",
                    "watch_dir": "/tmp",
                },
            ),
            patch.object(
                mcp_tools_session,
                "send_action",
                return_value=bridge_envelope,
            ),
        ):
            response = self._run_status()

        self.assertEqual(snapshot, response["data"]["editor_state"])

    def test_bridge_action_failure_yields_absent_editor_state_and_warning(self) -> None:
        # A failure envelope from the bridge action results in:
        #   * data.editor_state == None
        #   * a warning diagnostic whose payload carries the bridge code
        # Issue #304: session-level diagnostics emit the unified
        # ``{severity, code, message, data}`` wire shape; the
        # ``BRIDGE_GET_EDITOR_STATE_FAILED`` code names the failure mode
        # and ``data.bridge_code`` carries the underlying bridge code.
        bridge_failure = {
            "success": False,
            "severity": "error",
            "code": "EDITOR_BRIDGE_TIMEOUT",
            "message": "timed out",
            "data": {},
            "diagnostics": [],
        }
        with (
            patch.object(
                mcp_tools_session,
                "bridge_status",
                return_value={
                    "connected": True,
                    "mode": "editor",
                    "watch_dir": "/tmp",
                },
            ),
            patch.object(
                mcp_tools_session,
                "send_action",
                return_value=bridge_failure,
            ),
        ):
            response = self._run_status()

        warnings = [
            d for d in response["diagnostics"]
            if d.get("severity") == "warning"
            and d.get("code") == "BRIDGE_GET_EDITOR_STATE_FAILED"
            and d.get("data", {}).get("bridge_code") == "EDITOR_BRIDGE_TIMEOUT"
        ]
        self.assertEqual(
            (None, 1),
            (response["data"]["editor_state"], len(warnings)),
        )


if __name__ == "__main__":
    unittest.main()
