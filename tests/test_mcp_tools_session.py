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
import json
import os
import tempfile
import time
import unittest
from collections.abc import Callable, Coroutine
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch
from uuid import UUID

from prefab_sentinel import mcp_tools_session
from prefab_sentinel.bridge_watch_identity import WatchIdentityObservation
from prefab_sentinel.session import ProjectSession
from tests._mcp_tool_recorder import ToolRecorderServer, record_tools
from tests._typing_helpers import require_list, require_magic_mock, require_mapping


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


def _register_session_tools(
    session: ProjectSession,
    *,
    real_watch_tracker: bool = False,
) -> ToolRecorderServer:
    if real_watch_tracker:
        return record_tools(mcp_tools_session.register_session_tools, session)
    registered, _ = _register_session_tools_with_observer(
        session,
        WatchIdentityObservation("match", "identity_match"),
    )
    return registered

def _register_session_tools_with_observer(
    session: ProjectSession,
    observation: WatchIdentityObservation,
    *,
    side_effect: Callable[..., WatchIdentityObservation] | None = None,
) -> tuple[ToolRecorderServer, MagicMock]:
    tracker = MagicMock()
    if side_effect is None:
        tracker.observe.return_value = observation
    else:
        tracker.observe.side_effect = side_effect
    with patch.object(
        mcp_tools_session,
        "WatchIdentityTracker",
        return_value=tracker,
    ):
        registered = record_tools(mcp_tools_session.register_session_tools, session)
    return registered, require_magic_mock(tracker.observe, "watch tracker observe")


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

    def _run_activate(self) -> dict[str, object]:
        session = ProjectSession(project_root=self.project_root)
        registered = _register_session_tools(session)
        activate: Callable[..., Coroutine[object, object, object]] = registered.get(
            "activate_project"
        )
        return require_mapping(asyncio.run(
            activate(scope="Assets/Avatar"),
        ), "activate_project response")

    def _diagnostics(self, response: dict[str, object]) -> list[dict[str, object]]:
        return [
            require_mapping(diagnostic, "activation diagnostic")
            for diagnostic in require_list(
                response["diagnostics"], "activation diagnostics"
            )
        ]

    def test_version_mismatch_escalates_envelope_severity(self) -> None:
        # Plant a bridge .cs with a version that won't match the
        # installed package version; the activation envelope must
        # escalate to warning severity and surface the diagnostic.
        _write_bridge_cs(self.project_root, "0.0.1")
        response = self._run_activate()
        diagnostics = self._diagnostics(response)
        codes = [d.get("code") for d in diagnostics]
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
        diagnostics = self._diagnostics(response)
        codes = [d.get("code") for d in diagnostics]
        self.assertEqual(
            (True, "warning", True),
            (
                response["success"],
                response["severity"],
                "BRIDGE_NOT_FOUND" in codes,
            ),
        )

    def test_missing_bridge_diagnostic_surfaces_four_key_shape(self) -> None:
        # Issue #2: the activation response appends the session's
        # missing-bridge diagnostic verbatim into its diagnostics list;
        # that entry must carry the unified four-key wire shape so MCP
        # clients observe a consistent shape through the activation path.
        response = self._run_activate()
        diagnostics = self._diagnostics(response)
        missing = [
            d for d in diagnostics
            if d.get("code") == "BRIDGE_NOT_FOUND"
        ]
        self.assertEqual(
            1,
            len(missing),
            msg=(
                "activation response must surface exactly one "
                f"BRIDGE_NOT_FOUND diagnostic; got {diagnostics!r}"
            ),
        )
        self.assertEqual(
            {"severity", "code", "message", "data"},
            set(missing[0]),
            msg=(
                "missing-bridge diagnostic in the activation response "
                f"must carry exactly the four unified keys; got {missing[0]!r}"
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
        diagnostics = self._diagnostics(response)
        codes = [d.get("code") for d in diagnostics]
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

    def _run_status(self) -> dict[str, Any]:
        session = ProjectSession(project_root=self.project_root)
        registered = _register_session_tools(session)
        get_status: Callable[[], object] = registered.get("get_project_status")
        return require_mapping(get_status(), "get_project_status response")

    def test_disconnected_bridge_yields_absent_editor_state(self) -> None:
        projection = {
            "connected": False,
            "connection_state": "not_configured",
            "code": "EDITOR_BRIDGE_WATCH_DIR_MISSING",
            "blocker_class": "watch_dir",
            "suggested_next_action": (
                "Set UNITYTOOL_BRIDGE_WATCH_DIR to an existing Editor Bridge watch directory."
            ),
        }
        with (
            patch(
                "prefab_sentinel.mcp_tools_session.bridge_status",
                return_value=projection,
            ),
            patch.object(mcp_tools_session, "send_action") as send,
        ):
            response = self._run_status()

        send.assert_not_called()
        self.assertIsNone(response["data"]["editor_state"])

    def test_connected_bridge_returns_four_flag_snapshot(self) -> None:
        snapshot = {
            "unity_version": "2022.3.22f1",
            "required_packages": [
                {"name": "com.vrchat.base", "ready": True, "version": "3.10.2"},
                {"name": "com.vrchat.worlds", "ready": True, "version": "3.10.2"},
                {"name": "udonsharp", "ready": True, "version": "3.10.2"},
            ],
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
        projection = {
            "connected": True,
            "connection_state": "connected",
            "code": None,
            "blocker_class": None,
            "suggested_next_action": None,
        }
        with (
            patch(
                "prefab_sentinel.mcp_tools_session.bridge_status",
                return_value=projection,
            ),
            patch.object(
                mcp_tools_session,
                "send_action",
                return_value=bridge_envelope,
            ),
        ):
            response = self._run_status()

        self.assertEqual(snapshot, response["data"]["editor_state"])
        self.assertEqual("2022.3.22f1", response["data"]["unity_version"])
        self.assertEqual(snapshot["required_packages"], response["data"]["required_packages"])

    def test_bridge_action_failure_yields_absent_editor_state_and_warning(self) -> None:
        secret = "ISSUE162_SECRET_BRIDGE_FAILURE"
        private_path = r"D:\private\response.json"
        bridge_failure = {
            "success": False,
            "severity": "error",
            "code": f"{secret}:{private_path}",
            "message": f"{secret} {private_path}",
            "data": {
                "exception": {
                    "type": "OSError",
                    "message": secret,
                    "short_stack": private_path,
                }
            },
            "diagnostics": [],
        }
        projection = {
            "connected": True,
            "connection_state": "connected",
            "code": None,
            "blocker_class": None,
            "suggested_next_action": None,
        }
        with (
            patch(
                "prefab_sentinel.mcp_tools_session.bridge_status",
                return_value=projection,
            ),
            patch.object(
                mcp_tools_session,
                "send_action",
                return_value=bridge_failure,
            ),
            self.assertLogs(
                "prefab_sentinel.mcp_tools_session", level="ERROR"
            ) as captured,
        ):
            response = self._run_status()

        warnings = [
            d for d in response["diagnostics"]
            if d.get("severity") == "warning"
            and d.get("code") == "BRIDGE_GET_EDITOR_STATE_FAILED"
            and d.get("data", {}).get("bridge_code") == "EDITOR_BRIDGE_ERROR"
        ]
        public_wire = json.dumps(response)
        private_log = "\n".join(captured.output)
        self.assertEqual(
            (None, 1, False, False, True, True),
            (
                response["data"]["editor_state"],
                len(warnings),
                secret in public_wire,
                private_path in public_wire,
                secret in private_log,
                private_path in private_log,
            ),
        )
        self.assertEqual(
            "get_editor_state bridge action failed: EDITOR_BRIDGE_ERROR",
            warnings[0]["message"],
        )
        self.assertNotIn("bridge_message", warnings[0]["data"])


class ProjectStatusOperatorContextTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.project_root, self.scope = _make_project(self._tmp)

    def _run_status(
        self, bridge_envelope: dict[str, object],
    ) -> tuple[dict[str, Any], MagicMock]:
        session = ProjectSession(project_root=self.project_root)
        registered = _register_session_tools(session)
        get_status: Callable[[], object] = registered.get("get_project_status")
        projection = {
            "connected": True,
            "connection_state": "connected",
            "code": None,
            "blocker_class": None,
            "suggested_next_action": None,
        }
        with (
            patch(
                "prefab_sentinel.mcp_tools_session.bridge_status",
                return_value=projection,
            ) as bridge_probe,
            patch.object(
                mcp_tools_session,
                "send_action",
                return_value=bridge_envelope,
            ) as send,
        ):
            response = require_mapping(get_status(), "get_project_status response")

        bridge_probe.assert_called_once()
        return (
            response,
            require_magic_mock(send, "send_action mock"),
        )

    def _bridge_envelope(self, actual_root: str) -> dict[str, object]:
        return {
            "success": True,
            "severity": "info",
            "code": "EDITOR_CTRL_EDITOR_STATE_OK",
            "message": "ok",
            "data": {
                "editor_state": {
                    "is_playing": False,
                    "is_will_change_playmode": False,
                    "is_compiling": False,
                    "is_building_player": False,
                    "has_unsaved_changes": True,
                    "active_stage_kind": "prefab_stage",
                    "active_scene_path": "Assets/Scenes/Main.unity",
                    "active_scene_name": "Main",
                    "prefab_stage_asset_path": "Assets/Prefabs/Avatar.prefab",
                    "prefab_stage_root_name": "Avatar",
                    "prefab_stage_is_dirty": True,
                    "open_scenes": [
                        {
                            "path": "Assets/Scenes/Main.unity",
                            "name": "Main",
                            "is_dirty": False,
                        }
                    ],
                }
            },
            "operator_context": {
                "project_root": actual_root,
                "bridge_session_id": "bridge-session-1",
                "bridge_instance_id": "bridge-instance-1",
                "bridge_version": "0.7.0",
                "plugin_version": "0.7.0",
            },
            "diagnostics": [],
        }

    def test_matching_actual_root_reports_consistent_operator_context(self) -> None:
        bridge_envelope = self._bridge_envelope(str(self.project_root))
        response, send = self._run_status(bridge_envelope)
        data = response["data"]

        self.assertEqual(True, response["success"], response)
        self.assertEqual(str(self.project_root), data["expected_project_root"])
        self.assertIn("actual_project_root", data, data)
        self.assertEqual(str(self.project_root), data["actual_project_root"])
        self.assertIn("project_root_consistent", data, data)
        self.assertEqual(True, data["project_root_consistent"])
        self.assertEqual("prefab_stage", data["active_stage_kind"])
        self.assertEqual("Assets/Scenes/Main.unity", data["active_scene_path"])
        self.assertEqual("bridge-session-1", data["bridge_session_id"])
        self.assertEqual("bridge-instance-1", data["bridge_instance_id"])
        self.assertEqual("0.7.0", data["plugin_version"])
        send.assert_called_once_with(action="get_editor_state", expected_project_root=None)

    def test_bridge_warning_diagnostics_are_merged_into_status(self) -> None:
        secret = "ISSUE162_SECRET_DIAGNOSTIC"
        private_path = r"D:\private\request.response.json"
        traversal_path = f"Assets/../../{secret}/request.json"
        bridge_envelope = self._bridge_envelope(str(self.project_root))
        bridge_envelope["severity"] = "warning"
        data = require_mapping(bridge_envelope["data"], "bridge data")
        editor_state = require_mapping(data["editor_state"], "bridge editor state")
        editor_state["active_scene_path"] = traversal_path
        editor_state["dirty_scene_paths"] = [
            "Assets/Scenes/Main.unity",
            traversal_path,
            private_path,
        ]
        editor_state["open_scenes"] = [
            {
                "path": "Assets/Scenes/Main.unity",
                "name": "Main",
                "is_dirty": False,
            },
            {
                "path": traversal_path,
                "name": secret,
                "is_dirty": True,
            },
        ]
        editor_state["unexpected_private_field"] = secret
        bridge_envelope["diagnostics"] = [
            {
                "severity": "warning",
                "code": f"{secret}:{private_path}",
                "message": secret,
                "detail": secret,
                "path": private_path,
                "location": "open_scenes",
                "evidence": f"stack: {secret}",
                "blocker_class": secret,
                "state_source": secret,
                "suggested_next_action": f"{secret} {private_path}",
            }
        ]

        with self.assertLogs(
            "prefab_sentinel.mcp_tools_session", level="WARNING"
        ) as captured:
            response, send = self._run_status(bridge_envelope)

        diagnostics = [
            diagnostic for diagnostic in response["diagnostics"]
            if diagnostic.get("code") == "BRIDGE_DIAGNOSTIC"
        ]
        diagnostic = diagnostics[0] if diagnostics else {}
        public_wire = json.dumps(response)
        private_log = "\n".join(captured.output)

        self.assertEqual("warning", response["severity"], response)
        self.assertEqual(1, len(diagnostics), response["diagnostics"])
        self.assertEqual("warning", diagnostic.get("severity"))
        self.assertEqual("BRIDGE_DIAGNOSTIC", diagnostic.get("message"))
        self.assertEqual(
            {"location": "open_scenes"},
            diagnostic.get("data"),
        )
        self.assertNotIn(secret, public_wire)
        self.assertNotIn(private_path, public_wire)
        self.assertNotIn(traversal_path, public_wire)
        self.assertIn("Assets/Scenes/Main.unity", public_wire)
        self.assertIn(secret, private_log)
        self.assertIn(private_path, private_log)
        send.assert_called_once_with(action="get_editor_state", expected_project_root=None)

    def test_known_enumeration_diagnostic_keeps_stable_public_mapping(self) -> None:
        bridge_envelope = self._bridge_envelope(str(self.project_root))
        bridge_envelope["severity"] = "warning"
        bridge_envelope["diagnostics"] = [
            {
                "severity": "warning",
                "code": "EDITOR_STATE_ENUMERATION_LIMITED",
                "message": "private enumeration detail",
                "location": "open_scenes",
            }
        ]

        with self.assertLogs(
            "prefab_sentinel.mcp_tools_session", level="WARNING"
        ) as captured:
            response, send = self._run_status(bridge_envelope)

        diagnostics = [
            diagnostic for diagnostic in response["diagnostics"]
            if diagnostic.get("code") == "EDITOR_STATE_ENUMERATION_LIMITED"
        ]
        diagnostic = diagnostics[0] if diagnostics else {}

        self.assertEqual(1, len(diagnostics), response["diagnostics"])
        self.assertEqual("warning", diagnostic.get("severity"))
        self.assertEqual(
            "Unity Editor state enumeration was limited.",
            diagnostic.get("message"),
        )
        self.assertEqual({"location": "open_scenes"}, diagnostic.get("data"))
        self.assertIn("private enumeration detail", "\n".join(captured.output))
        send.assert_called_once_with(
            action="get_editor_state",
            expected_project_root=None,
        )

    def test_mismatching_actual_root_reports_warning_without_failing_status(self) -> None:
        actual_root = str(self.project_root.parent / "OtherProject")
        bridge_envelope = self._bridge_envelope(actual_root)
        response, send = self._run_status(bridge_envelope)
        data = response["data"]

        self.assertEqual(True, response["success"], response)
        self.assertEqual(str(self.project_root), data["expected_project_root"])
        self.assertIn("actual_project_root", data, data)
        self.assertEqual(actual_root, data["actual_project_root"])
        self.assertIn("project_root_consistent", data, data)
        self.assertEqual(False, data["project_root_consistent"])
        diagnostics = [
            diagnostic for diagnostic in response["diagnostics"]
            if diagnostic.get("code") == "EDITOR_BRIDGE_PROJECT_ROOT_MISMATCH"
        ]
        self.assertEqual(1, len(diagnostics), response["diagnostics"])
        self.assertEqual("warning", diagnostics[0]["severity"])
        self.assertEqual(str(self.project_root), diagnostics[0]["data"]["expected_project_root"])
        self.assertEqual(actual_root, diagnostics[0]["data"]["actual_project_root"])
        send.assert_called_once_with(action="get_editor_state", expected_project_root=None)


class EditorStatusBlockerClassifierTests(unittest.TestCase):
    def _classifiers(self) -> tuple[Callable[..., list[dict[str, Any]]], Callable[..., dict[str, Any] | None]]:
        try:
            from prefab_sentinel.editor_status_blockers import (
                classify_status_blockers,
                classify_tool_error_blocker,
            )
        except ModuleNotFoundError as exc:
            self.fail(
                "expected prefab_sentinel.editor_status_blockers classifier module, "
                f"observed missing module {exc.name!r}"
            )
        return classify_status_blockers, classify_tool_error_blocker

    def test_status_evidence_maps_to_shared_blocker_classes(self) -> None:
        classify_status_blockers, _ = self._classifiers()

        blockers = classify_status_blockers(
            {},
            {
                "connected": False,
                "code": "EDITOR_BRIDGE_WATCH_DIR_MISMATCH",
            },
            {
                "state_source": "live_editor",
                "is_compiling": True,
                "is_building_player": False,
                "is_will_change_playmode": True,
                "active_stage_kind": "prefab_stage",
                "has_unsaved_changes": True,
                "dirty_scene_paths": ["Assets/Scenes/Main.unity"],
            },
            configured_watch_dir=None,
        )

        by_class = {blocker["blocker_class"]: blocker for blocker in blockers}
        expected = {
            "watch_dir": {
                "blocker_class": "watch_dir",
                "state_source": "bridge_transport",
                "message": (
                    "Configured watch directory differs from the active Unity "
                    "Editor Bridge watch directory."
                ),
                "suggested_next_action": (
                    "Use the same watch directory for Codex and the Unity Editor Bridge."
                ),
            },
            "compile_or_build": {
                "blocker_class": "compile_or_build",
                "state_source": "live_editor",
                "message": "Unity is compiling scripts or building a player.",
                "suggested_next_action": "Wait for Unity compile or build activity to finish, then retry the tool.",
            },
            "playmode_transition": {
                "blocker_class": "playmode_transition",
                "state_source": "live_editor",
                "message": "Unity is entering or exiting Play Mode.",
                "suggested_next_action": "Wait for the Play Mode transition to complete, then retry the tool.",
            },
            "prefab_stage_for_scene_bound_operation": {
                "blocker_class": "prefab_stage_for_scene_bound_operation",
                "state_source": "live_editor",
                "message": "A Prefab Stage is active and can block scene-bound operations.",
                "suggested_next_action": "Close the active Prefab Stage before running scene-bound Editor operations.",
            },
            "dirty_or_save_blocker": {
                "blocker_class": "dirty_or_save_blocker",
                "state_source": "live_editor",
                "message": "Unity has dirty scenes, prefabs, materials, or assets.",
                "suggested_next_action": "Save or intentionally discard dirty Unity state before relying on saved YAML.",
            },
        }
        self.assertEqual(expected, by_class)

    def test_tool_error_classifier_reuses_shared_vocabulary(self) -> None:
        _, classify_tool_error_blocker = self._classifiers()

        compile_blocker = classify_tool_error_blocker(
            {"code": "EDITOR_CTRL_SCENE_WRITE_BLOCKED", "message": "blocked"},
            {"state_source": "live_editor", "is_compiling": True},
        )
        dirty_blocker = classify_tool_error_blocker(
            {"code": "EDITOR_CTRL_SAVE_BLOCKED", "message": "dirty"},
            {"state_source": "live_editor", "has_unsaved_changes": True},
        )
        write_blocker = classify_tool_error_blocker(
            {"code": "EDITOR_BRIDGE_WRITE", "message": "request write failed"},
            None,
        )
        unknown_blocker = classify_tool_error_blocker(
            {"code": "EDITOR_CTRL_UNKNOWN", "message": "generic failure"},
            None,
        )

        self.assertEqual(
            ("compile_or_build", "dirty_or_save_blocker", "watch_dir", None),
            (
                compile_blocker["blocker_class"] if compile_blocker else None,
                dirty_blocker["blocker_class"] if dirty_blocker else None,
                write_blocker["blocker_class"] if write_blocker else None,
                unknown_blocker,
            ),
        )


class ProjectStatusBlockerTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.project_root, self.scope = _make_project(self._tmp)

    def _run_status(
        self,
        bridge_envelope: dict[str, object],
    ) -> dict[str, Any]:
        session = ProjectSession(project_root=self.project_root)
        registered = _register_session_tools(session)
        get_status: Callable[[], object] = registered.get("get_project_status")
        projection = {
            "connected": True,
            "connection_state": "connected",
            "code": None,
            "blocker_class": None,
            "suggested_next_action": None,
        }
        with (
            patch(
                "prefab_sentinel.mcp_tools_session.bridge_status",
                return_value=projection,
            ) as bridge_probe,
            patch.object(
                mcp_tools_session,
                "send_action",
                return_value=bridge_envelope,
            ),
        ):
            response = require_mapping(get_status(), "get_project_status response")

        bridge_probe.assert_called_once()
        return response

    def _run_status_with_watch(
        self,
        watch_dir: str,
    ) -> tuple[dict[str, Any], MagicMock]:
        from prefab_sentinel.bridge_constants import BRIDGE_WATCH_DIR_ENV

        bridge_envelope = {
            "success": True,
            "severity": "info",
            "code": "EDITOR_CTRL_EDITOR_STATE_OK",
            "message": "ok",
            "data": {"editor_state": {}},
            "diagnostics": [],
        }
        session = ProjectSession(project_root=self.project_root)
        registered = _register_session_tools(session)
        get_status: Callable[[], object] = registered.get("get_project_status")
        with (
            patch.dict(os.environ, {BRIDGE_WATCH_DIR_ENV: watch_dir}),
            patch.object(
                mcp_tools_session,
                "send_action",
                return_value=bridge_envelope,
            ) as send,
        ):
            response = require_mapping(get_status(), "get_project_status response")
        return response, require_magic_mock(send, "send_action mock")

    def test_fresh_watch_identity_mismatch_is_path_free_and_skips_live_request(
        self,
    ) -> None:
        from prefab_sentinel.bridge_constants import BRIDGE_WATCH_DIR_ENV

        secret_marker = "a" * 32
        secret_status = "/private/ISSUE179_SECRET/status.json"
        secret_timestamp = "1700000000000"
        secret_exception = "ISSUE179_SECRET_OSERROR"
        with tempfile.TemporaryDirectory() as tmpdir:
            watch_dir = Path(tmpdir) / "ISSUE179_SECRET_WATCH"
            watch_dir.mkdir()
            secret_watch = str(watch_dir)
            observation = WatchIdentityObservation(
                "mismatch",
                (
                    f"marker={secret_marker} status={secret_status} "
                    f"updated_at={secret_timestamp} error={secret_exception}"
                ),
            )
            session = ProjectSession(project_root=self.project_root)
            registered, observe = _register_session_tools_with_observer(
                session,
                observation,
            )
            get_status: Callable[[], object] = registered.get("get_project_status")
            with (
                patch.dict(os.environ, {BRIDGE_WATCH_DIR_ENV: secret_watch}),
                patch.object(mcp_tools_session, "send_action") as send,
            ):
                response = require_mapping(
                    get_status(),
                    "get_project_status response",
                )

        data = require_mapping(response["data"], "status data")
        bridge = require_mapping(data["bridge"], "status bridge")
        blockers = require_list(data["blockers"], "status blockers")
        self.assertEqual(
            (True, "warning", "SESSION_STATUS"),
            (response["success"], response["severity"], response["code"]),
        )
        self.assertEqual(
            (
                "misconfigured",
                "EDITOR_BRIDGE_WATCH_DIR_MISMATCH",
                "watch_dir",
                1,
            ),
            (
                bridge["connection_state"],
                bridge["code"],
                require_mapping(blockers[0], "first blocker")["blocker_class"],
                len(blockers),
            ),
        )
        self.assertEqual(Path(secret_watch), observe.call_args.kwargs["watch_dir"])
        send.assert_not_called()
        public_wire = json.dumps(response)
        for private_value in (
            secret_watch,
            secret_marker,
            secret_status,
            secret_timestamp,
            secret_exception,
        ):
            self.assertNotIn(private_value, public_wire)

    def test_fresh_watch_without_marker_creates_identity_before_live_request(
        self,
    ) -> None:
        from prefab_sentinel.bridge_constants import BRIDGE_WATCH_DIR_ENV
        from prefab_sentinel.bridge_watch_identity import (
            BRIDGE_STATUS_RELATIVE_PATH,
            WATCH_IDENTITY_MARKER_FILENAME,
        )

        marker_identity = "1a2b3c4d5e6f78900123456789abcdef"
        timestamp = time.time_ns() // 1_000_000
        watch_dir = self.project_root / "ISSUE179_SECRET_WATCH"
        watch_dir.mkdir()
        status_path = self.project_root / BRIDGE_STATUS_RELATIVE_PATH
        status_path.parent.mkdir(parents=True)
        status_content = json.dumps(
            {
                "schema_version": 1,
                "watch_identity": "0" * 32,
                "bridge_session_id": "c" * 32,
                "bridge_instance_id": "d" * 32,
                "updated_at_unix_ms": timestamp,
            },
            separators=(",", ":"),
        )
        status_path.write_text(status_content, encoding="utf-8")
        session = ProjectSession(project_root=self.project_root)
        registered = _register_session_tools(
            session,
            real_watch_tracker=True,
        )
        get_status: Callable[[], object] = registered.get("get_project_status")

        with (
            patch.dict(os.environ, {BRIDGE_WATCH_DIR_ENV: str(watch_dir)}),
            patch(
                "prefab_sentinel.bridge_watch_identity.uuid.uuid4",
                return_value=UUID(marker_identity),
            ),
            patch.object(mcp_tools_session, "send_action") as send,
        ):
            response = require_mapping(get_status(), "get_project_status response")

        data = require_mapping(response["data"], "status data")
        bridge = require_mapping(data["bridge"], "status bridge")
        blockers = require_list(data["blockers"], "status blockers")
        marker_path = watch_dir / WATCH_IDENTITY_MARKER_FILENAME
        self.assertEqual(
            (True, "warning", "SESSION_STATUS"),
            (response["success"], response["severity"], response["code"]),
        )
        self.assertEqual(
            {
                "connected": False,
                "connection_state": "misconfigured",
                "code": "EDITOR_BRIDGE_WATCH_DIR_MISMATCH",
                "blocker_class": "watch_dir",
                "suggested_next_action": (
                    "Use the same watch directory for Codex and the Unity Editor Bridge."
                ),
            },
            bridge,
        )
        self.assertEqual(
            [
                {
                    "blocker_class": "watch_dir",
                    "state_source": "bridge_transport",
                    "message": (
                        "Configured watch directory differs from the active Unity "
                        "Editor Bridge watch directory."
                    ),
                    "suggested_next_action": (
                        "Use the same watch directory for Codex and the Unity Editor Bridge."
                    ),
                }
            ],
            blockers,
        )
        self.assertEqual(str(self.project_root), data["project_root"])
        self.assertEqual(str(self.project_root), data["expected_project_root"])
        self.assertIsInstance(data["session_id"], str)
        self.assertTrue(data["session_id"])
        self.assertEqual(marker_identity.encode("ascii"), marker_path.read_bytes())
        self.assertEqual(32, len(marker_path.read_bytes()))
        send.assert_not_called()
        public_wire = json.dumps(response)
        for private_value in (
            str(watch_dir),
            marker_identity,
            str(status_path),
            status_content,
            str(timestamp),
        ):
            self.assertNotIn(private_value, public_wire)

    def test_fresh_watch_identity_match_preserves_live_request(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            response, send = self._run_status_with_watch(tmpdir)

        self.assertTrue(response["success"])
        send.assert_called_once_with(
            action="get_editor_state",
            expected_project_root=None,
        )

    def test_watch_dir_classification_uses_captured_path_after_observer_mutates_env(
        self,
    ) -> None:
        from prefab_sentinel.bridge_constants import BRIDGE_WATCH_DIR_ENV

        with tempfile.TemporaryDirectory() as tmpdir:
            captured_watch_dir = Path(tmpdir) / "captured"
            captured_watch_dir.mkdir()
            changed_watch_dir = str(Path(tmpdir) / "ambient-change")

            def mutate_env_and_match(**_: object) -> WatchIdentityObservation:
                os.environ[BRIDGE_WATCH_DIR_ENV] = changed_watch_dir
                return WatchIdentityObservation("match", "identity_match")

            session = ProjectSession(project_root=self.project_root)
            registered, _ = _register_session_tools_with_observer(
                session,
                WatchIdentityObservation("match", "identity_match"),
                side_effect=mutate_env_and_match,
            )
            get_status: Callable[[], object] = registered.get("get_project_status")
            bridge_envelope = {
                "success": True,
                "severity": "info",
                "code": "EDITOR_CTRL_EDITOR_STATE_OK",
                "message": "ok",
                "data": {"editor_state": {}},
                "diagnostics": [],
            }
            with (
                patch.dict(
                    os.environ,
                    {BRIDGE_WATCH_DIR_ENV: str(captured_watch_dir)},
                ),
                patch.object(
                    mcp_tools_session,
                    "send_action",
                    return_value=bridge_envelope,
                ) as send,
            ):
                response = require_mapping(
                    get_status(),
                    "get_project_status response",
                )

        data = require_mapping(response["data"], "status data")
        self.assertEqual([], data["blockers"])
        send.assert_called_once_with(
            action="get_editor_state",
            expected_project_root=None,
        )

    def test_unavailable_watch_identity_blocks_live_request(
        self,
    ) -> None:
        from prefab_sentinel.bridge_constants import BRIDGE_WATCH_DIR_ENV

        with tempfile.TemporaryDirectory() as tmpdir:
            session = ProjectSession(project_root=self.project_root)
            registered, _ = _register_session_tools_with_observer(
                session,
                WatchIdentityObservation("unavailable", "status_unavailable"),
            )
            get_status: Callable[[], object] = registered.get("get_project_status")
            with (
                patch.dict(os.environ, {BRIDGE_WATCH_DIR_ENV: tmpdir}),
                patch.object(mcp_tools_session, "send_action") as send,
            ):
                response = require_mapping(
                    get_status(),
                    "get_project_status response",
                )

        data = require_mapping(response["data"], "status data")
        bridge = require_mapping(data["bridge"], "status bridge")
        self.assertEqual(
            ("EDITOR_BRIDGE_STATUS_UNAVAILABLE", 1),
            (
                bridge["code"],
                len(require_list(data["blockers"], "status blockers")),
            ),
        )
        send.assert_not_called()

    def test_live_editor_dirty_identities_and_blockers_stay_successful(self) -> None:
        bridge_envelope = {
            "success": True,
            "severity": "info",
            "code": "EDITOR_CTRL_EDITOR_STATE_OK",
            "message": "ok",
            "data": {
                "editor_state": {
                    "state_source": "live_editor",
                    "is_playing": False,
                    "is_will_change_playmode": False,
                    "is_compiling": True,
                    "is_building_player": False,
                    "has_unsaved_changes": True,
                    "dirty_scene_paths": ["Assets/Scenes/Main.unity"],
                    "dirty_prefab_paths": ["Assets/Prefabs/Avatar.prefab"],
                    "dirty_material_paths": ["Assets/Materials/Body.mat"],
                    "dirty_asset_paths": ["Assets/Data/Config.asset"],
                }
            },
            "diagnostics": [],
        }

        response = self._run_status(bridge_envelope)
        data = response["data"]
        by_class = {blocker["blocker_class"]: blocker for blocker in data["blockers"]}

        self.assertEqual((True, "warning"), (response["success"], response["severity"]))
        self.assertEqual(
            (
                ["Assets/Scenes/Main.unity"],
                ["Assets/Prefabs/Avatar.prefab"],
                ["Assets/Materials/Body.mat"],
                ["Assets/Data/Config.asset"],
                "live_editor",
                {
                    "compile_or_build": {
                        "blocker_class": "compile_or_build",
                        "state_source": "live_editor",
                        "message": "Unity is compiling scripts or building a player.",
                        "suggested_next_action": "Wait for Unity compile or build activity to finish, then retry the tool.",
                    },
                    "dirty_or_save_blocker": {
                        "blocker_class": "dirty_or_save_blocker",
                        "state_source": "live_editor",
                        "message": "Unity has dirty scenes, prefabs, materials, or assets.",
                        "suggested_next_action": "Save or intentionally discard dirty Unity state before relying on saved YAML.",
                    },
                },
            ),
            (
                data["dirty_scene_paths"],
                data["dirty_prefab_paths"],
                data["dirty_material_paths"],
                data["dirty_asset_paths"],
                data["state_source"],
                by_class,
            ),
        )

    def test_public_status_redacts_invalid_configured_watch_dir(self) -> None:
        from prefab_sentinel.bridge_constants import BRIDGE_WATCH_DIR_ENV

        secret = "ISSUE162_SECRET_BLOCKER_PROBE"
        with tempfile.TemporaryDirectory() as tmpdir:
            watch_dir = Path(tmpdir) / "watch"
            watch_dir.mkdir()
            original_is_dir = Path.is_dir

            def fail_watch_dir_probe(path: Path) -> bool:
                if path == watch_dir:
                    raise OSError(secret)
                return original_is_dir(path)

            session = ProjectSession(project_root=self.project_root)
            registered = _register_session_tools(session)
            get_status: Callable[[], object] = registered.get("get_project_status")
            try:
                Path.is_dir = fail_watch_dir_probe  # type: ignore[assignment]
                with patch.dict(
                    os.environ,
                    {BRIDGE_WATCH_DIR_ENV: str(watch_dir)},
                ):
                    response = require_mapping(
                        get_status(),
                        "get_project_status response",
                    )
            finally:
                Path.is_dir = original_is_dir  # type: ignore[assignment]

        data = require_mapping(response["data"], "get_project_status data")
        self.assertNotIn("configured_watch_dir", data)
        self.assertEqual(
            [
                {
                    "blocker_class": "watch_dir",
                    "state_source": "bridge_transport",
                    "message": (
                        "Editor Bridge watch directory is missing, invalid, or not writable."
                    ),
                    "suggested_next_action": (
                        "Set UNITYTOOL_BRIDGE_WATCH_DIR to an existing Editor Bridge watch directory."
                    ),
                }
            ],
            data["blockers"],
        )
        public_wire = json.dumps(response)
        self.assertNotIn(secret, public_wire)
        self.assertNotIn(str(watch_dir), public_wire)

    def test_editor_state_failure_diagnostic_includes_bridge_connection_blocker(self) -> None:
        bridge_failure = {
            "success": False,
            "severity": "error",
            "code": "EDITOR_BRIDGE_TIMEOUT",
            "message": "timed out",
            "data": {"action": "get_editor_state"},
            "diagnostics": [],
        }

        response = self._run_status(bridge_failure)
        failure_diagnostics = [
            diagnostic for diagnostic in response["diagnostics"]
            if diagnostic["code"] == "BRIDGE_GET_EDITOR_STATE_FAILED"
        ]

        self.assertEqual((True, "warning", 1), (response["success"], response["severity"], len(failure_diagnostics)))
        self.assertEqual(
            (
                "bridge_connection",
                "Confirm Unity is running and the PrefabSentinel Editor Bridge watcher is active.",
            ),
            (
                failure_diagnostics[0]["data"].get("blocker_class"),
                failure_diagnostics[0]["data"].get("suggested_next_action"),
            ),
        )


class ActivateProjectExpectedRootTests(unittest.TestCase):
    def test_activate_project_retains_expected_root_in_returned_and_subsequent_status(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            project_root = Path(tmpdir)
            scope = project_root / "Assets" / "MyScope"
            scope.mkdir(parents=True)
            session = ProjectSession()
            patches = _patch_session_layer()

            with patch.dict(os.environ, {"UNITYTOOL_UNITY_PROJECT_PATH": ""}, clear=False):
                with patches[0], patches[1]:
                    returned = asyncio.run(
                        session.activate(str(scope), project_root=str(project_root))
                    )

            self.assertIn("expected_project_root", returned, returned)
            self.assertEqual(str(project_root.resolve()), returned["expected_project_root"])
            current = session.status()
            self.assertIn("expected_project_root", current, current)
            self.assertEqual(str(project_root.resolve()), current["expected_project_root"])
            self.assertEqual(str(scope.resolve()), returned["scope"])


class ProjectSessionStatusIdentityTests(unittest.TestCase):
    def test_status_exposes_expected_root_and_stable_session_identity_without_bridge(self) -> None:
        session = ProjectSession()
        inactive = session.status()

        self.assertIn("expected_project_root", inactive, inactive)
        self.assertIsNone(inactive["expected_project_root"])
        self.assertIn("session_id", inactive, inactive)
        self.assertIsInstance(inactive["session_id"], str)
        self.assertRegex(inactive["session_id"], r"^[0-9a-f]{32}$")

        with tempfile.TemporaryDirectory() as tmpdir:
            project_root = Path(tmpdir)
            scope = project_root / "Assets" / "MyScope"
            scope.mkdir(parents=True)
            patches = _patch_session_layer()

            with patch.dict(os.environ, {"UNITYTOOL_UNITY_PROJECT_PATH": ""}, clear=False):
                with patches[0], patches[1]:
                    active = asyncio.run(
                        session.activate(str(scope), project_root=str(project_root))
                    )

        self.assertEqual(str(project_root.resolve()), active["expected_project_root"])
        self.assertEqual(inactive["session_id"], active["session_id"])
        self.assertEqual(active["session_id"], session.status()["session_id"])


class TestEditorStateFreshnessMarker(unittest.TestCase):
    """T-40: the offline symbol-reference tools attach a freshness marker
    only when the Editor Bridge is connected and reports unsaved changes
    (issue #40).
    """

    def setUp(self) -> None:
        # The watch-dir env var must not leak from the host shell so the
        # bridge-status branch under test is exercised deterministically
        # (issues #88 / #89 / #270).
        os.environ.pop("UNITYTOOL_BRIDGE_WATCH_DIR", None)
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        # A minimal one-GameObject prefab so the symbol tools have an asset.
        self.prefab = Path(self._tmp.name) / "fixture.prefab"
        self.prefab.write_text(
            "%YAML 1.1\n"
            "%TAG !u! tag:unity3d.com,2011:\n"
            "--- !u!1 &100\n"
            "GameObject:\n"
            "  m_Component:\n"
            "  - component: {fileID: 200}\n"
            "  m_Name: Cube\n"
            "--- !u!4 &200\n"
            "Transform:\n"
            "  m_GameObject: {fileID: 100}\n"
            "  m_Father: {fileID: 0}\n"
            "  m_Children: []\n",
            encoding="utf-8",
        )

    def _register(self) -> ToolRecorderServer:
        from prefab_sentinel import mcp_tools_symbols

        return record_tools(
            mcp_tools_symbols.register_symbol_tools,
            ProjectSession(project_root=None),
        )

    def _editor_state_envelope(self, *, unsaved: bool) -> dict:
        return {
            "success": True,
            "severity": "info",
            "code": "EDITOR_CTRL_EDITOR_STATE_OK",
            "message": "ok",
            "data": {
                "editor_state": {
                    "is_playing": False,
                    "is_will_change_playmode": False,
                    "is_compiling": False,
                    "is_building_player": False,
                    "has_unsaved_changes": unsaved,
                },
            },
            "diagnostics": [],
        }

    def _assert_no_freshness_markers(
        self,
        bridge_response: dict[str, object],
    ) -> None:
        from prefab_sentinel import mcp_tools_symbols

        tools = self._register()
        get_unity_symbols: Callable[..., object] = tools.get("get_unity_symbols")
        find_unity_symbol: Callable[..., object] = tools.get("find_unity_symbol")
        with patch.object(
            mcp_tools_symbols,
            "bridge_status",
            return_value={"connected": True, "watch_dir": "/tmp"},
        ), patch.object(
            mcp_tools_symbols,
            "send_action",
            return_value=bridge_response,
        ):
            payloads = {
                "get_unity_symbols": require_mapping(
                    get_unity_symbols(asset_path=str(self.prefab)),
                    "get_unity_symbols payload",
                ),
                "find_unity_symbol": require_mapping(
                    find_unity_symbol(
                        asset_path=str(self.prefab),
                        symbol_path="Cube",
                    ),
                    "find_unity_symbol payload",
                ),
            }

        for tool_name, payload in payloads.items():
            with self.subTest(tool_name=tool_name):
                self.assertNotIn("freshness", payload)

    def test_marker_present_when_connected_and_unsaved(self) -> None:
        """T-40-1: get_unity_symbols carries the marker when live edits are unsaved."""
        from prefab_sentinel import mcp_tools_symbols

        get_unity_symbols: Callable[..., object] = self._register().get(
            "get_unity_symbols"
        )
        with patch.object(
            mcp_tools_symbols, "bridge_status",
            return_value={"connected": True, "watch_dir": "/tmp"},
        ), patch.object(
            mcp_tools_symbols, "send_action",
            return_value=self._editor_state_envelope(unsaved=True),
        ):
            payload = require_mapping(
                get_unity_symbols(asset_path=str(self.prefab)),
                "get_unity_symbols payload",
            )
        self.assertIn("freshness", payload)
        freshness = require_mapping(payload["freshness"], "freshness marker")
        self.assertEqual("last_saved_disk", freshness["source"])

    def test_no_marker_without_bridge_connection(self) -> None:
        """T-40-2: find_unity_symbol carries no marker with no Bridge connection."""
        from prefab_sentinel import mcp_tools_symbols

        find_unity_symbol: Callable[..., object] = self._register().get(
            "find_unity_symbol"
        )
        with patch.object(
            mcp_tools_symbols, "bridge_status",
            return_value={"connected": False, "watch_dir": None},
        ), patch.object(mcp_tools_symbols, "send_action") as send:
            payload = require_mapping(find_unity_symbol(
                asset_path=str(self.prefab), symbol_path="Cube",
            ), "find_unity_symbol payload")
        # No Bridge round-trip and no marker — the offline no-Unity-required
        # property is preserved.
        send.assert_not_called()
        self.assertNotIn("freshness", payload)

    def test_no_marker_when_connected_and_clean(self) -> None:
        """T-40-3: no marker when the connected Bridge reports no unsaved changes."""
        from prefab_sentinel import mcp_tools_symbols

        get_unity_symbols: Callable[..., object] = self._register().get(
            "get_unity_symbols"
        )
        with patch.object(
            mcp_tools_symbols, "bridge_status",
            return_value={"connected": True, "watch_dir": "/tmp"},
        ), patch.object(
            mcp_tools_symbols, "send_action",
            return_value=self._editor_state_envelope(unsaved=False),
        ):
            payload = require_mapping(
                get_unity_symbols(asset_path=str(self.prefab)),
                "get_unity_symbols payload",
            )
        self.assertNotIn("freshness", payload)

    def test_no_marker_for_malformed_truthy_editor_state_response(self) -> None:
        bridge_response: dict[str, object] = {
            "success": "false",
            "severity": "info",
            "code": "EDITOR_CTRL_EDITOR_STATE_OK",
            "message": "malformed success",
            "data": {
                "editor_state": {
                    "has_unsaved_changes": True,
                }
            },
            "diagnostics": [],
        }

        self._assert_no_freshness_markers(bridge_response)

    def test_no_marker_for_valid_soft_negative_editor_state_response(self) -> None:
        bridge_response: dict[str, object] = {
            "success": False,
            "severity": "warning",
            "code": "EDITOR_CTRL_EDITOR_STATE_PENDING",
            "message": "state snapshot unavailable",
            "data": {
                "editor_state": {
                    "has_unsaved_changes": True,
                }
            },
            "diagnostics": [],
        }

        self._assert_no_freshness_markers(bridge_response)

class ProjectStatusHeartbeatTests(unittest.TestCase):
    MARKER = "b" * 32
    PRIVATE_TIMESTAMP = 10_000

    def setUp(self) -> None:
        from prefab_sentinel.bridge_watch_identity import (
            BRIDGE_STATUS_RELATIVE_PATH,
            WATCH_IDENTITY_MARKER_FILENAME,
        )

        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.project_root, self.scope = _make_project(self._tmp)
        self.watch_dir = self.project_root / "ISSUE194_PRIVATE_WATCH"
        self.watch_dir.mkdir()
        (self.watch_dir / WATCH_IDENTITY_MARKER_FILENAME).write_text(
            self.MARKER,
            encoding="ascii",
        )
        self.status_path = self.project_root / BRIDGE_STATUS_RELATIVE_PATH
        self.status_path.parent.mkdir(parents=True)
        self.session = ProjectSession(project_root=self.project_root)
        self.registered = _register_session_tools(
            self.session,
            real_watch_tracker=True,
        )
        self.get_status: Callable[[], object] = self.registered.get(
            "get_project_status"
        )
        self.activate: Callable[..., Coroutine[Any, Any, object]] = (
            self.registered.get("activate_project")
        )

    def _write_status(
        self,
        *,
        updated_at_unix_ms: int = PRIVATE_TIMESTAMP,
    ) -> str:
        payload = json.dumps(
            {
                "schema_version": 1,
                "watch_identity": self.MARKER,
                "bridge_session_id": "c" * 32,
                "bridge_instance_id": "d" * 32,
                "updated_at_unix_ms": updated_at_unix_ms,
            },
            separators=(",", ":"),
        )
        self.status_path.write_text(payload, encoding="utf-8")
        return payload

    def _run_status(
        self,
        now_unix_ms: int,
    ) -> tuple[dict[str, Any], MagicMock]:
        from prefab_sentinel.bridge_constants import BRIDGE_WATCH_DIR_ENV

        bridge_envelope = {
            "success": True,
            "severity": "info",
            "code": "EDITOR_CTRL_EDITOR_STATE_OK",
            "message": "ok",
            "data": {"editor_state": {}},
            "diagnostics": [],
        }
        with (
            patch.dict(
                os.environ,
                {BRIDGE_WATCH_DIR_ENV: str(self.watch_dir)},
            ),
            patch.object(
                mcp_tools_session.time,
                "time_ns",
                return_value=now_unix_ms * 1_000_000,
            ),
            patch.object(
                mcp_tools_session,
                "send_action",
                return_value=bridge_envelope,
            ) as send,
        ):
            response = require_mapping(
                self.get_status(),
                "get_project_status response",
            )
        return response, require_magic_mock(send, "send_action mock")

    def test_transient_missing_is_warning_without_blocker_or_live_request(
        self,
    ) -> None:
        private_payload = self._write_status()
        connected, connected_send = self._run_status(self.PRIVATE_TIMESTAMP)
        self.assertTrue(connected["success"])
        connected_send.assert_called_once()

        self.status_path.unlink()
        transient, transient_send = self._run_status(
            self.PRIVATE_TIMESTAMP + 5_000
        )

        data = require_mapping(transient["data"], "status data")
        bridge = require_mapping(data["bridge"], "status bridge")
        diagnostics = require_list(transient["diagnostics"], "status diagnostics")
        self.assertEqual(
            (True, "warning", "SESSION_STATUS"),
            (
                transient["success"],
                transient["severity"],
                transient["code"],
            ),
        )
        self.assertEqual(
            {
                "connected": False,
                "connection_state": "unavailable",
                "code": "EDITOR_BRIDGE_STATUS_TRANSIENT",
                "blocker_class": None,
                "suggested_next_action": None,
            },
            bridge,
        )
        self.assertEqual([], data["blockers"])
        self.assertIsNone(data["editor_state"])
        diagnostic_codes = [
            require_mapping(diagnostic, "status diagnostic")["code"]
            for diagnostic in diagnostics
        ]
        self.assertEqual(
            1,
            diagnostic_codes.count("EDITOR_BRIDGE_STATUS_TRANSIENT"),
        )
        transient_send.assert_not_called()
        public_wire = json.dumps(transient)
        for private_value in (
            str(self.watch_dir),
            str(self.status_path),
            self.MARKER,
            str(self.PRIVATE_TIMESTAMP),
            private_payload,
        ):
            self.assertNotIn(private_value, public_wire)

    def test_inclusive_transient_boundary_recovers_live_status(self) -> None:
        self._write_status()
        before, before_send = self._run_status(self.PRIVATE_TIMESTAMP)
        before_send.assert_called_once()
        self.assertTrue(
            require_mapping(
                require_mapping(before["data"], "before data")["bridge"],
                "before bridge",
            )["connected"]
        )

        self.status_path.unlink()
        transient, transient_send = self._run_status(
            self.PRIVATE_TIMESTAMP + 5_000
        )
        transient_data = require_mapping(transient["data"], "transient data")
        self.assertEqual(
            ("EDITOR_BRIDGE_STATUS_TRANSIENT", [], None),
            (
                require_mapping(
                    transient_data["bridge"],
                    "transient bridge",
                )["code"],
                require_list(transient_data["blockers"], "transient blockers"),
                transient_data["editor_state"],
            ),
        )
        transient_send.assert_not_called()

        recovered_at = self.PRIVATE_TIMESTAMP + 5_000
        self._write_status(updated_at_unix_ms=recovered_at)
        recovered, recovered_send = self._run_status(recovered_at)
        recovered_data = require_mapping(recovered["data"], "recovered data")
        recovered_bridge = require_mapping(
            recovered_data["bridge"],
            "recovered bridge",
        )
        self.assertEqual(
            (True, "connected", None, [], {}),
            (
                recovered_bridge["connected"],
                recovered_bridge["connection_state"],
                recovered_bridge["code"],
                require_list(recovered_data["blockers"], "recovered blockers"),
                recovered_data["editor_state"],
            ),
        )
        recovered_send.assert_called_once_with(
            action="get_editor_state",
            expected_project_root=None,
        )

    def test_persistent_missing_has_one_connection_blocker_and_recovers(
        self,
    ) -> None:
        self._write_status()
        _, connected_send = self._run_status(self.PRIVATE_TIMESTAMP)
        connected_send.assert_called_once()
        self.status_path.unlink()

        persistent, persistent_send = self._run_status(
            self.PRIVATE_TIMESTAMP + 5_001
        )
        data = require_mapping(persistent["data"], "persistent status data")
        bridge = require_mapping(data["bridge"], "persistent status bridge")
        blockers = require_list(data["blockers"], "persistent blockers")
        self.assertEqual(
            {
                "connected": False,
                "connection_state": "unavailable",
                "code": "EDITOR_BRIDGE_STATUS_UNAVAILABLE",
                "blocker_class": "bridge_connection",
                "suggested_next_action": (
                    "Confirm Unity is running and the PrefabSentinel Editor "
                    "Bridge watcher is active."
                ),
            },
            bridge,
        )
        self.assertEqual(
            [
                {
                    "blocker_class": "bridge_connection",
                    "state_source": "bridge_transport",
                    "message": "Editor Bridge status artifact is unavailable.",
                    "suggested_next_action": (
                        "Confirm Unity is running and the PrefabSentinel Editor "
                        "Bridge watcher is active."
                    ),
                }
            ],
            blockers,
        )
        self.assertIsNone(data["editor_state"])
        persistent_send.assert_not_called()

        self._write_status(updated_at_unix_ms=20_000)
        recovered, recovered_send = self._run_status(20_000)
        recovered_data = require_mapping(recovered["data"], "recovered status data")
        recovered_bridge = require_mapping(
            recovered_data["bridge"],
            "recovered status bridge",
        )
        self.assertEqual(
            (True, "connected", None, []),
            (
                recovered_bridge["connected"],
                recovered_bridge["connection_state"],
                recovered_bridge["code"],
                recovered_data["blockers"],
            ),
        )
        recovered_send.assert_called_once()

    def test_initial_missing_and_permission_failure_are_persistent_and_redacted(
        self,
    ) -> None:
        private_exception = "ISSUE194_PRIVATE_PERMISSION"
        initial, initial_send = self._run_status(1)
        initial_data = require_mapping(initial["data"], "initial status data")
        initial_bridge = require_mapping(
            initial_data["bridge"],
            "initial status bridge",
        )
        self.assertEqual(
            ("EDITOR_BRIDGE_STATUS_UNAVAILABLE", 1),
            (
                initial_bridge["code"],
                len(require_list(initial_data["blockers"], "initial blockers")),
            ),
        )
        initial_send.assert_not_called()

        with patch(
            "prefab_sentinel.bridge_watch_identity._read_status",
            side_effect=PermissionError(private_exception),
        ):
            permission, permission_send = self._run_status(2)
        self.assertEqual(
            "EDITOR_BRIDGE_STATUS_UNAVAILABLE",
            require_mapping(
                require_mapping(permission["data"], "permission data")["bridge"],
                "permission bridge",
            )["code"],
        )
        permission_send.assert_not_called()
        self.assertNotIn(private_exception, json.dumps(permission))

    def test_successful_activation_resets_prior_freshness(self) -> None:
        self._write_status()
        self._run_status(self.PRIVATE_TIMESTAMP)
        self.status_path.unlink()
        transient, transient_send = self._run_status(self.PRIVATE_TIMESTAMP + 1)
        self.assertEqual(
            "EDITOR_BRIDGE_STATUS_TRANSIENT",
            require_mapping(
                require_mapping(transient["data"], "transient data")["bridge"],
                "transient bridge",
            )["code"],
        )
        transient_send.assert_not_called()

        patches = _patch_session_layer()
        with patches[0], patches[1]:
            activated = require_mapping(
                asyncio.run(
                    self.activate(
                        scope=str(self.scope),
                        project_root=str(self.project_root),
                    )
                ),
                "activate_project response",
            )
        self.assertTrue(activated["success"])

        reset_status, reset_send = self._run_status(self.PRIVATE_TIMESTAMP + 2)
        reset_data = require_mapping(reset_status["data"], "reset status data")
        self.assertEqual(
            "EDITOR_BRIDGE_STATUS_UNAVAILABLE",
            require_mapping(reset_data["bridge"], "reset status bridge")["code"],
        )
        self.assertEqual(1, len(require_list(reset_data["blockers"], "reset blockers")))
        reset_send.assert_not_called()

    def test_failed_activation_preserves_prior_freshness(self) -> None:
        self._write_status()
        self._run_status(self.PRIVATE_TIMESTAMP)
        self.status_path.unlink()

        patches = _patch_session_layer()
        with patches[0], patches[1]:
            rejected = require_mapping(
                asyncio.run(
                    self.activate(
                        scope=str(self.scope),
                        project_root=str(self.project_root / "missing"),
                    )
                ),
                "activate_project response",
            )
        self.assertFalse(rejected["success"])

        status, send = self._run_status(self.PRIVATE_TIMESTAMP + 2)
        data = require_mapping(status["data"], "status data")
        bridge = require_mapping(data["bridge"], "status bridge")
        self.assertEqual(
            ("EDITOR_BRIDGE_STATUS_TRANSIENT", []),
            (
                bridge["code"],
                require_list(data["blockers"], "status blockers"),
            ),
        )
        send.assert_not_called()

    def test_unconfigured_watch_dir_keeps_existing_setup_projection(self) -> None:
        from prefab_sentinel.bridge_constants import BRIDGE_WATCH_DIR_ENV

        with (
            patch.dict(
                os.environ,
                {BRIDGE_WATCH_DIR_ENV: ""},
            ),
            patch.object(mcp_tools_session, "send_action") as send,
            self.assertNoLogs(
                "prefab_sentinel.bridge_watch_identity",
                level="ERROR",
            ),
        ):
            status = require_mapping(
                self.get_status(),
                "get_project_status response",
            )

        data = require_mapping(status["data"], "status data")
        bridge = require_mapping(data["bridge"], "status bridge")
        blockers = require_list(data["blockers"], "status blockers")
        self.assertEqual(
            (
                "not_configured",
                "EDITOR_BRIDGE_WATCH_DIR_MISSING",
                "watch_dir",
                1,
            ),
            (
                bridge["connection_state"],
                bridge["code"],
                require_mapping(blockers[0], "watch blocker")["blocker_class"],
                len(blockers),
            ),
        )
        send.assert_not_called()


if __name__ == "__main__":
    unittest.main()
