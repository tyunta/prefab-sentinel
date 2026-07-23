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
import os
import tempfile
import unittest
from collections.abc import Callable, Coroutine
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

from prefab_sentinel import mcp_tools_session
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


def _register_session_tools(session: ProjectSession) -> ToolRecorderServer:
    return record_tools(mcp_tools_session.register_session_tools, session)


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
        with (
            patch.object(
                mcp_tools_session,
                "bridge_status",
                return_value={"connected": True, "mode": "editor", "watch_dir": "/tmp"},
            ),
            patch.object(
                mcp_tools_session,
                "send_action",
                return_value=bridge_envelope,
            ) as send,
        ):
            return (
                require_mapping(get_status(), "get_project_status response"),
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
        bridge_envelope = self._bridge_envelope(str(self.project_root))
        bridge_envelope["severity"] = "warning"
        bridge_envelope["diagnostics"] = [
            {
                "severity": "warning",
                "code": "EDITOR_STATE_ENUMERATION_LIMITED",
                "detail": "Open scene enumeration failed.",
                "location": "open_scenes",
            }
        ]

        response, send = self._run_status(bridge_envelope)
        diagnostics = [
            diagnostic for diagnostic in response["diagnostics"]
            if diagnostic.get("code") == "EDITOR_STATE_ENUMERATION_LIMITED"
        ]
        diagnostic = diagnostics[0] if diagnostics else {}

        self.assertEqual("warning", response["severity"], response)
        self.assertEqual(1, len(diagnostics), response["diagnostics"])
        self.assertEqual("warning", diagnostic.get("severity"))
        self.assertEqual("Open scene enumeration failed.", diagnostic.get("message"))
        self.assertEqual("open_scenes", diagnostic.get("data", {}).get("location"))
        send.assert_called_once_with(action="get_editor_state", expected_project_root=None)

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
            {"configured_watch_dir": "/expected/watch"},
            {"connected": True, "watch_dir": "/actual/watch"},
            {
                "state_source": "live_editor",
                "is_compiling": True,
                "is_building_player": False,
                "is_will_change_playmode": True,
                "active_stage_kind": "prefab_stage",
                "has_unsaved_changes": True,
                "dirty_scene_paths": ["Assets/Scenes/Main.unity"],
            },
        )

        by_class = {blocker["blocker_class"]: blocker for blocker in blockers}
        expected = {
            "watch_dir": {
                "blocker_class": "watch_dir",
                "state_source": "bridge_transport",
                "message": "Configured watch directory differs from the Bridge-reported watch directory.",
                "suggested_next_action": "Use the same watch directory for Codex and the Unity Editor Bridge.",
                "evidence": {
                    "configured_watch_dir": "/expected/watch",
                    "bridge_watch_dir": "/actual/watch",
                },
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

    def _run_status(self, bridge_envelope: dict[str, object]) -> dict[str, Any]:
        session = ProjectSession(project_root=self.project_root)
        registered = _register_session_tools(session)
        get_status: Callable[[], object] = registered.get("get_project_status")
        with (
            patch.object(
                mcp_tools_session,
                "bridge_status",
                return_value={"connected": True, "mode": "editor", "watch_dir": "/tmp"},
            ),
            patch.object(
                mcp_tools_session,
                "send_action",
                return_value=bridge_envelope,
            ),
        ):
            return require_mapping(get_status(), "get_project_status response")

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

    def test_public_status_reports_configured_watch_dir_mismatch(self) -> None:
        from prefab_sentinel.bridge_constants import BRIDGE_WATCH_DIR_ENV

        bridge_envelope = {
            "success": True,
            "severity": "info",
            "code": "EDITOR_CTRL_EDITOR_STATE_OK",
            "message": "ok",
            "data": {
                "watch_dir": "/bridge/watch",
                "editor_state": {
                    "state_source": "live_editor",
                    "is_playing": False,
                    "is_will_change_playmode": False,
                    "is_compiling": False,
                    "is_building_player": False,
                    "has_unsaved_changes": False,
                    "dirty_scene_paths": [],
                    "dirty_prefab_paths": [],
                    "dirty_material_paths": [],
                    "dirty_asset_paths": [],
                },
            },
            "diagnostics": [],
        }

        with patch.dict(os.environ, {BRIDGE_WATCH_DIR_ENV: "/configured/watch"}):
            response = self._run_status(bridge_envelope)

        data = response["data"]
        watch_dir_blockers = [
            blocker
            for blocker in data["blockers"]
            if blocker["blocker_class"] == "watch_dir"
        ]
        self.assertEqual(
            (
                "/configured/watch",
                [
                    {
                        "blocker_class": "watch_dir",
                        "state_source": "bridge_transport",
                        "message": "Configured watch directory differs from the Bridge-reported watch directory.",
                        "suggested_next_action": "Use the same watch directory for Codex and the Unity Editor Bridge.",
                        "evidence": {
                            "configured_watch_dir": "/configured/watch",
                            "bridge_watch_dir": "/tmp",
                        },
                    }
                ],
            ),
            (data["configured_watch_dir"], watch_dir_blockers),
        )

    def test_public_status_reports_invalid_configured_watch_dir(self) -> None:
        from prefab_sentinel.bridge_constants import BRIDGE_WATCH_DIR_ENV

        with tempfile.NamedTemporaryFile() as tmp_file:
            session = ProjectSession(project_root=self.project_root)
            registered = _register_session_tools(session)
            get_status: Callable[[], object] = registered.get("get_project_status")
            with (
                patch.dict(os.environ, {BRIDGE_WATCH_DIR_ENV: tmp_file.name}),
                patch.object(
                    mcp_tools_session,
                    "bridge_status",
                    return_value={
                        "connected": False,
                        "mode": "editor",
                        "watch_dir": tmp_file.name,
                    },
                ),
            ):
                response = require_mapping(get_status(), "get_project_status response")
                data = require_mapping(response["data"], "get_project_status data")
                expected_watch_dir = tmp_file.name

        self.assertEqual(
            [
                {
                    "blocker_class": "watch_dir",
                    "state_source": "bridge_transport",
                    "message": "Configured Editor Bridge watch directory is not an existing directory.",
                    "suggested_next_action": "Set UNITYTOOL_BRIDGE_WATCH_DIR to an existing Editor Bridge watch directory.",
                    "evidence": {"configured_watch_dir": expected_watch_dir},
                }
            ],
            data["blockers"],
        )

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


if __name__ == "__main__":
    unittest.main()
