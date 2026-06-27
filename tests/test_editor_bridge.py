"""Tests for prefab_sentinel.editor_bridge module."""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from prefab_sentinel.editor_bridge import (
    BRIDGE_WATCH_DIR_ENV,
    PROTOCOL_VERSION,
    SUPPORTED_ACTIONS,
    bridge_status,
    check_editor_bridge_env,
    send_action,
)
from prefab_sentinel.editor_bridge_builders import build_create_empty_kwargs, build_set_camera_kwargs
from prefab_sentinel.unity_assets_path import resolve_asset_path


class TestCheckEditorBridgeEnv(unittest.TestCase):
    """Tests for the watch-directory-only env validator (issue #270)."""

    def test_watch_dir_present_returns_no_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.dict(
                os.environ,
                {BRIDGE_WATCH_DIR_ENV: tmpdir},
                clear=False,
            ):
                result = check_editor_bridge_env()
                self.assertIsNone(result)

    @patch.dict(os.environ, {BRIDGE_WATCH_DIR_ENV: ""}, clear=False)
    def test_watch_dir_unset_emits_watch_dir_missing(self) -> None:
        result = check_editor_bridge_env()
        assert result is not None
        self.assertEqual("EDITOR_BRIDGE_WATCH_DIR_MISSING", result["code"])
        self.assertEqual("error", result["severity"])
        self.assertIn(BRIDGE_WATCH_DIR_ENV, result["message"])
        self.assertEqual(BRIDGE_WATCH_DIR_ENV, result["data"]["env_var"])

    @patch.dict(
        os.environ,
        {BRIDGE_WATCH_DIR_ENV: "/nonexistent/xyz"},
        clear=False,
    )
    def test_watch_dir_nonexistent_emits_watch_dir_not_found(self) -> None:
        result = check_editor_bridge_env()
        assert result is not None
        self.assertEqual("EDITOR_BRIDGE_WATCH_DIR_NOT_FOUND", result["code"])
        self.assertEqual("error", result["severity"])
        self.assertEqual("/nonexistent/xyz", result["data"]["value"])

    def test_wsl_conversion_applied_in_check_editor_bridge_env(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.dict(
                os.environ,
                {BRIDGE_WATCH_DIR_ENV: "D:\\Project\\Watch"},
                clear=False,
            ):
                with patch("prefab_sentinel.editor_bridge.to_wsl_path", return_value=tmpdir) as mock_to_wsl_path:
                    result = check_editor_bridge_env()
                    self.assertIsNone(result)
                    mock_to_wsl_path.assert_called_with("D:\\Project\\Watch")

    def test_wsl_conversion_applied_in_bridge_status(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.dict(
                os.environ,
                {BRIDGE_WATCH_DIR_ENV: "D:\\Project\\Watch"},
                clear=False,
            ):
                with patch("prefab_sentinel.editor_bridge.to_wsl_path", return_value=tmpdir) as mock_to_wsl_path:
                    result = bridge_status()
                    self.assertTrue(result["connected"])
                    mock_to_wsl_path.assert_called_with("D:\\Project\\Watch")

    def test_wsl_conversion_applied_in_send_action(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.dict(
                os.environ,
                {BRIDGE_WATCH_DIR_ENV: "D:\\Project\\Watch"},
                clear=False,
            ):
                with patch("prefab_sentinel.editor_bridge.check_editor_bridge_env", return_value=None):
                    with patch("prefab_sentinel.editor_bridge.to_wsl_path", return_value=tmpdir) as mock_to_wsl_path:
                        # timeout_sec=1 keeps the call short (no responder)
                        # while staying above the > 0 boundary check.
                        send_action(action="capture_screenshot", timeout_sec=1)
                        mock_to_wsl_path.assert_called_once_with("D:\\Project\\Watch")

    @patch.dict(
        os.environ,
        {BRIDGE_WATCH_DIR_ENV: "D:\\Nonexistent"},
        clear=False,
    )
    def test_windows_path_without_wsl_conversion_fails(self) -> None:
        result = check_editor_bridge_env()
        assert result is not None
        self.assertFalse(result["success"])
        self.assertEqual("EDITOR_BRIDGE_WATCH_DIR_NOT_FOUND", result["code"])


class TestSendAction(unittest.TestCase):
    """Tests for send_action request/response protocol."""

    def test_unknown_action(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.dict(
                os.environ,
                {BRIDGE_WATCH_DIR_ENV: tmpdir},
                clear=False,
            ):
                result = send_action(action="nonexistent_action")
                self.assertFalse(result["success"])
                self.assertEqual("EDITOR_BRIDGE_UNKNOWN_ACTION", result["code"])

    def test_env_not_configured(self) -> None:
        with patch.dict(os.environ, {BRIDGE_WATCH_DIR_ENV: ""}, clear=False):
            result = send_action(action="capture_screenshot")
            self.assertFalse(result["success"])
            self.assertEqual("EDITOR_BRIDGE_WATCH_DIR_MISSING", result["code"])

    def test_zero_timeout_sec_rejected_at_boundary(self) -> None:
        """``timeout_sec <= 0`` is rejected with a dedicated envelope
        before any IPC write, mirroring the runtime-bridge invoke path.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.dict(
                os.environ,
                {BRIDGE_WATCH_DIR_ENV: tmpdir},
                clear=False,
            ):
                result = send_action(action="capture_screenshot", timeout_sec=0)
                self.assertEqual("EDITOR_BRIDGE_TIMEOUT_INVALID", result["code"])
                self.assertEqual(0, result["data"]["received_timeout"])

    def test_negative_timeout_sec_rejected_at_boundary(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.dict(
                os.environ,
                {BRIDGE_WATCH_DIR_ENV: tmpdir},
                clear=False,
            ):
                result = send_action(action="capture_screenshot", timeout_sec=-1)
                self.assertEqual("EDITOR_BRIDGE_TIMEOUT_INVALID", result["code"])

    def test_request_file_written_and_timeout(self) -> None:
        """Verify request file is written correctly; timeout since no Unity responds."""
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.dict(
                os.environ,
                {BRIDGE_WATCH_DIR_ENV: tmpdir},
                clear=False,
            ):
                result = send_action(
                    action="capture_screenshot",
                    view="scene",
                    width=800,
                    height=600,
                    timeout_sec=1,
                )
                # Should timeout since no Unity editor is responding.
                self.assertFalse(result["success"])
                self.assertEqual("EDITOR_BRIDGE_TIMEOUT", result["code"])

    def test_response_read_successfully(self) -> None:
        """Simulate Unity writing a response file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            watch_dir = Path(tmpdir)
            seen_request_id: dict[str, str] = {}
            responder_errors: list[BaseException] = []

            import threading

            request_ready = threading.Condition()
            observed_request: dict[str, Path] = {}
            original_rename = Path.rename

            def notifying_rename(self_path: Path, target: str | Path) -> Path:
                renamed_path = original_rename(self_path, target)
                target_path = Path(target)
                if (
                    target_path.parent == watch_dir
                    and target_path.name.endswith(".request.json")
                ):
                    with request_ready:
                        observed_request["path"] = target_path
                        request_ready.notify_all()
                return renamed_path

            def fake_send():
                """Write a fake response after observing the request file."""
                with request_ready:
                    request_seen = request_ready.wait_for(
                        lambda: "path" in observed_request,
                        timeout=2,
                    )
                if not request_seen:
                    responder_errors.append(
                        AssertionError("Expected request file before fake Unity response")
                    )
                    return

                request_file = observed_request["path"]
                base = request_file.name.replace(".request.json", "")
                seen_request_id["value"] = base
                resp_path = watch_dir / f"{base}.response.json"
                resp = {
                    "protocol_version": PROTOCOL_VERSION,
                    "success": True,
                    "severity": "info",
                    "code": "EDITOR_CTRL_SCREENSHOT_OK",
                    "message": "Screenshot captured",
                    "data": {
                        "output_path": "/tmp/test.png",
                        "view": "scene",
                        "width": 800,
                        "height": 600,
                        "executed": True,
                    },
                    "diagnostics": [],
                }
                resp_path.write_text(json.dumps(resp), encoding="utf-8")

            with (
                patch.dict(
                    os.environ,
                    {BRIDGE_WATCH_DIR_ENV: tmpdir},
                    clear=False,
                ),
                patch.object(Path, "rename", notifying_rename),
            ):
                t = threading.Thread(target=fake_send)
                t.start()
                result = send_action(
                    action="capture_screenshot",
                    view="scene",
                    width=800,
                    height=600,
                    timeout_sec=5,
                )
                t.join()

                self.assertEqual([], responder_errors)
                self.assertTrue(result["success"])
                self.assertEqual("EDITOR_CTRL_SCREENSHOT_OK", result["code"])
                self.assertEqual("/tmp/test.png", result["data"]["output_path"])
                self.assertEqual(seen_request_id["value"], result["request_id"])


class BridgeProjectRootMismatchTests(unittest.TestCase):
    _OMIT_EXPECTED_ROOT = object()

    def _send_with_fake_response(
        self,
        response_payload: dict[str, object],
        *,
        expected_project_root: str | None | object = _OMIT_EXPECTED_ROOT,
    ) -> tuple[dict[str, object], str]:
        with tempfile.TemporaryDirectory() as tmpdir:
            watch_dir = Path(tmpdir)
            seen_request_id: dict[str, str] = {}
            responder_errors: list[BaseException] = []

            import threading

            request_ready = threading.Condition()
            observed_request: dict[str, Path] = {}
            original_rename = Path.rename

            def notifying_rename(self_path: Path, target: str | Path) -> Path:
                renamed_path = original_rename(self_path, target)
                target_path = Path(target)
                if target_path.parent == watch_dir and target_path.name.endswith(
                    ".request.json"
                ):
                    with request_ready:
                        observed_request["path"] = target_path
                        request_ready.notify_all()
                return renamed_path

            def fake_send() -> None:
                with request_ready:
                    request_seen = request_ready.wait_for(
                        lambda: "path" in observed_request,
                        timeout=2,
                    )
                if not request_seen:
                    responder_errors.append(
                        AssertionError("Expected request file before fake Unity response")
                    )
                    return

                request_file = observed_request["path"]
                request_id = request_file.name.removesuffix(".request.json")
                seen_request_id["value"] = request_id
                response = dict(response_payload)
                response.setdefault("protocol_version", PROTOCOL_VERSION)
                response_file = watch_dir / f"{request_id}.response.json"
                tmp_response_file = Path(str(response_file) + ".tmp")
                tmp_response_file.write_text(
                    json.dumps(response),
                    encoding="utf-8",
                )
                tmp_response_file.rename(response_file)

            kwargs: dict[str, object] = {
                "action": "get_editor_state",
                "timeout_sec": 5,
            }
            if expected_project_root is not self._OMIT_EXPECTED_ROOT:
                kwargs["expected_project_root"] = expected_project_root

            with (
                patch.dict(os.environ, {BRIDGE_WATCH_DIR_ENV: tmpdir}, clear=False),
                patch.object(Path, "rename", notifying_rename),
            ):
                t = threading.Thread(target=fake_send)
                t.start()
                result = send_action(**kwargs)
                t.join()

        self.assertEqual([], responder_errors)
        return result, seen_request_id["value"]

    def test_mismatching_project_root_returns_typed_error_with_identity(self) -> None:
        expected_root = "/workspace/ExpectedProject"
        actual_root = "/workspace/OtherProject"
        result, request_id = self._send_with_fake_response(
            {
                "success": True,
                "severity": "info",
                "code": "EDITOR_CTRL_STATE_OK",
                "message": "Editor state captured",
                "data": {"is_playing": False},
                "diagnostics": [],
                "operator_context": {
                    "project_root": actual_root,
                    "bridge_session_id": "bridge-session-1",
                    "bridge_instance_id": "bridge-instance-1",
                },
            },
            expected_project_root=expected_root,
        )

        self.assertEqual(False, result["success"], result)
        self.assertEqual("EDITOR_BRIDGE_PROJECT_ROOT_MISMATCH", result["code"])
        self.assertEqual("error", result["severity"])
        self.assertIn(expected_root, result["message"])
        self.assertIn(actual_root, result["message"])
        self.assertEqual("get_editor_state", result["data"]["action"])
        self.assertEqual(request_id, result["data"]["request_id"])
        self.assertEqual(expected_root, result["data"]["expected_project_root"])
        self.assertEqual(actual_root, result["data"]["actual_project_root"])
        self.assertEqual("bridge-session-1", result["data"]["bridge_session_id"])
        self.assertEqual("bridge-instance-1", result["data"]["bridge_instance_id"])

    def test_expected_root_requires_actual_root_identity(self) -> None:
        expected_root = "/workspace/ExpectedProject"
        result, request_id = self._send_with_fake_response(
            {
                "success": True,
                "severity": "info",
                "code": "EDITOR_CTRL_STATE_OK",
                "message": "Editor state captured",
                "data": {},
                "diagnostics": [],
                "operator_context": {
                    "bridge_session_id": "bridge-session-1",
                    "bridge_instance_id": "bridge-instance-1",
                },
            },
            expected_project_root=expected_root,
        )

        self.assertEqual(False, result["success"], result)
        self.assertEqual("EDITOR_BRIDGE_PROJECT_ROOT_MISMATCH", result["code"])
        self.assertIn("actual Unity project root", result["message"])
        self.assertIn(expected_root, result["message"])
        self.assertEqual(request_id, result["data"]["request_id"])
        self.assertEqual(expected_root, result["data"]["expected_project_root"])
        self.assertNotIn("actual_project_root", result["data"])

    def test_matching_project_root_preserves_success_payload(self) -> None:
        expected_root = "/workspace/ExpectedProject"
        result, request_id = self._send_with_fake_response(
            {
                "success": True,
                "severity": "info",
                "code": "EDITOR_CTRL_STATE_OK",
                "message": "Editor state captured",
                "data": {"is_playing": False},
                "diagnostics": [],
                "operator_context": {
                    "project_root": expected_root,
                    "bridge_session_id": "bridge-session-1",
                    "bridge_instance_id": "bridge-instance-1",
                },
            },
            expected_project_root=expected_root,
        )

        self.assertEqual(True, result["success"], result)
        self.assertEqual("EDITOR_CTRL_STATE_OK", result["code"])
        self.assertEqual(request_id, result["request_id"])
        self.assertEqual(expected_root, result["operator_context"]["project_root"])
        self.assertEqual(False, result["data"]["is_playing"])






class TestEditorBridgeSupportedActions(unittest.TestCase):
    """Issue #193 — membership test: the supported-actions set contains
    exactly one prefab-save action (``safe_save_prefab``), and contains
    no other prefab-save action name (e.g. the legacy ``save_as_prefab``).
    """

    def test_safe_save_prefab_is_only_prefab_save_action(self) -> None:
        self.assertIn("safe_save_prefab", SUPPORTED_ACTIONS)
        # No other prefab-save action.
        self.assertNotIn("save_as_prefab", SUPPORTED_ACTIONS)


class TestSupportedActions(unittest.TestCase):
    """Verify the action set is complete."""

    def test_all_actions_present(self) -> None:
        expected = {
            "capture_screenshot",
            "select_object",
            "frame_selected",
            "instantiate_to_scene",
            "ping_object",
            "capture_console_logs",
            "refresh_asset_database",
            "set_material",
            "delete_object",
            "list_children",
            "list_materials",
            "get_camera",
            "set_camera",
            "list_roots",
            "get_material_property",
            "set_material_property",
            "run_integration_tests",
            "vrcsdk_upload",
            # Phase 2: BlendShape + Menu
            "get_blend_shapes",
            "set_blend_shape",
            "list_menu_items",
            "execute_menu_item",
            "find_renderers_by_material",
            "editor_rename",
            "editor_add_component",
            "editor_remove_component",
            "create_udon_program_asset",
            "editor_set_property",
            "editor_serialized_property_read",
            "editor_serialized_property_list",
            "editor_serialized_property_write",
            # Issue #193: ``safe_save_prefab`` is the sole public prefab-save action.
            "safe_save_prefab",
            "editor_set_parent",
            "editor_create_empty",
            "editor_create_primitive",
            # Issue #195: dedicated uGUI element creation surface.
            "editor_create_ui_element",
            "editor_batch_create",
            "editor_batch_set_property",
            "editor_batch_set_material_property",
            "editor_open_scene",
            "editor_save_scene",
            "editor_batch_add_component",
            "editor_create_scene",
            "editor_reflect",
            # Phase: one-step C# exec (#74)
            "run_script",
            # Issue #118: synchronous recompile-and-wait surface
            "editor_recompile_and_wait",
            # Issue #119: high-level UdonSharp authoring surface.
            "editor_add_udonsharp_component",
            "editor_set_udonsharp_field",
            "editor_wire_persistent_listener",
            # Issue #239: read-only editor-state snapshot.
            "get_editor_state",
            # Issue #242: bridge-side scene-view refresh primitive.
            "force_scene_view_refresh",
            # Issue #240: batch blend-shape write under one Undo group.
            "batch_set_blend_shape",
            # Issue #236: Prefab Stage open / close.
            "open_prefab",
            "close_prefab",
            # Issue #233: async run-script submit / poll.
            "run_script_submit",
            "run_script_poll",
            # Issue #243: AnimationClip primitives.
            "inspect_animation_clip",
            "create_animation_clip",
            "apply_animation_clip",
            # Issue #98: live geometry read primitives.
            "get_transform",
            "get_bounds",
            "measure_distance",
            # Issue #114: AssetDatabase-backed asset deletion.
            "delete_assets",
        }
        self.assertEqual(expected, SUPPORTED_ACTIONS)


class TestCameraActions(unittest.TestCase):
    """Tests for get_camera / set_camera action validation."""

    def test_get_camera_in_supported_actions(self) -> None:
        self.assertIn("get_camera", SUPPORTED_ACTIONS)

    def test_set_camera_in_supported_actions(self) -> None:
        self.assertIn("set_camera", SUPPORTED_ACTIONS)

    def test_old_camera_removed(self) -> None:
        self.assertNotIn("camera", SUPPORTED_ACTIONS)

    def test_get_camera_env_missing(self) -> None:
        """get_camera returns bridge error when env not configured."""
        with patch.dict(os.environ, {BRIDGE_WATCH_DIR_ENV: ""}, clear=False):
            result = send_action(action="get_camera")
            self.assertFalse(result["success"])
            self.assertEqual("EDITOR_BRIDGE_WATCH_DIR_MISSING", result["code"])

    def test_set_camera_env_missing(self) -> None:
        """set_camera returns bridge error when env not configured."""
        with patch.dict(os.environ, {BRIDGE_WATCH_DIR_ENV: ""}, clear=False):
            result = send_action(action="set_camera", yaw=0.0)
            self.assertFalse(result["success"])
            self.assertEqual("EDITOR_BRIDGE_WATCH_DIR_MISSING", result["code"])


class TestSetCameraParams(unittest.TestCase):
    """Validate editor_set_camera parameter conversion."""

    def test_pivot_orbit_kwargs(self) -> None:
        # Issue #81: orbit-radius argument is named ``size`` (it
        # carries ``SceneView.size`` semantics — Scene-view half-width).
        kwargs = build_set_camera_kwargs(
            pivot='{"x":0,"y":1.3,"z":0}',
            yaw=345.0,
            pitch=8.0,
            size=0.28,
        )
        self.assertEqual(kwargs["camera_pivot"], [0, 1.3, 0])
        self.assertEqual(kwargs["yaw"], 345.0)
        self.assertEqual(kwargs["pitch"], 8.0)
        self.assertEqual(kwargs["size"], 0.28)
        self.assertNotIn("camera_position", kwargs)
        self.assertNotIn("camera_look_at", kwargs)
        # The pre-rename key must not survive as a hidden alias.
        self.assertNotIn("distance", kwargs)

    def test_position_look_at_kwargs(self) -> None:
        kwargs = build_set_camera_kwargs(
            position='{"x":0,"y":1.5,"z":-1}',
            look_at='{"x":0,"y":1.3,"z":0}',
        )
        self.assertEqual(kwargs["camera_position"], [0, 1.5, -1])
        self.assertEqual(kwargs["camera_look_at"], [0, 1.3, 0])
        self.assertNotIn("camera_pivot", kwargs)

    def test_position_yaw_pitch_kwargs(self) -> None:
        kwargs = build_set_camera_kwargs(
            position='{"x":0,"y":1.5,"z":-1}',
            yaw=0.0,
            pitch=10.0,
            size=0.5,
        )
        self.assertEqual(kwargs["camera_position"], [0, 1.5, -1])
        self.assertEqual(kwargs["yaw"], 0.0)
        self.assertEqual(kwargs["pitch"], 10.0)
        self.assertEqual(kwargs["size"], 0.5)
        self.assertNotIn("camera_look_at", kwargs)
        self.assertNotIn("distance", kwargs)

    def test_omitted_params_excluded(self) -> None:
        kwargs = build_set_camera_kwargs(yaw=180.0)
        self.assertEqual(kwargs, {"yaw": 180.0})

    def test_size_sentinel_minus_one_is_omitted(self) -> None:
        # Issue #81: the orbit-radius sentinel ``-1.0`` means "keep
        # current" and must not be forwarded verbatim; the produced
        # kwargs dict must contain neither ``size`` nor the pre-rename
        # alias ``distance``.
        kwargs = build_set_camera_kwargs(size=-1.0)
        self.assertEqual(kwargs, {})

    def test_distance_is_not_a_registered_builder_parameter(self) -> None:
        # Issue #81: the pre-rename keyword name must not survive as a
        # hidden alias on the builder's keyword surface; callers that
        # still pass ``distance=...`` should get the standard Python
        # ``TypeError`` for an unknown keyword argument.
        import inspect
        params = inspect.signature(build_set_camera_kwargs).parameters
        self.assertIn("size", params)
        self.assertNotIn("distance", params)

    def test_orthographic_passed(self) -> None:
        kwargs = build_set_camera_kwargs(orthographic=1)
        self.assertEqual(kwargs["camera_orthographic"], 1)

    def test_reset_to_defaults_passed(self) -> None:
        """Issue #112: ``reset_to_defaults=True`` is forwarded to the bridge."""
        kwargs = build_set_camera_kwargs(reset_to_defaults=True)
        self.assertEqual({"reset_to_defaults": True}, kwargs)

    def test_reset_to_defaults_default_false_omitted(self) -> None:
        kwargs = build_set_camera_kwargs(yaw=0.0)
        self.assertNotIn("reset_to_defaults", kwargs)


class TestEditorSetCameraForwardsResetToDefaults(unittest.TestCase):
    """Issue #112: the MCP wrapper forwards the flag through ``send_action``."""

    def test_editor_set_camera_forwards_reset_to_defaults(self) -> None:
        from prefab_sentinel import mcp_tools_editor_view  # noqa: PLC0415
        from prefab_sentinel.mcp_server import create_server  # noqa: PLC0415

        with patch.object(mcp_tools_editor_view, "send_action") as send:
            send.return_value = {
                "success": True,
                "severity": "info",
                "code": "EDITOR_CTRL_SET_CAMERA_OK",
                "message": "ok",
                "data": {},
                "diagnostics": [],
            }
            server = create_server()
            asyncio.run(server.call_tool(
                "editor_set_camera",
                {"reset_to_defaults": True},
            ))
        kwargs = send.call_args.kwargs
        self.assertEqual("set_camera", kwargs["action"])
        self.assertTrue(kwargs.get("reset_to_defaults"))


class TestEditorConsoleForwardsClassificationFilter(unittest.TestCase):
    """Issue #117: the MCP wrapper forwards ``classification_filter``."""

    def test_editor_console_classification_filter_forwarded(self) -> None:
        from prefab_sentinel import mcp_tools_editor_view  # noqa: PLC0415
        from prefab_sentinel.mcp_server import create_server  # noqa: PLC0415

        with patch.object(mcp_tools_editor_view, "send_action") as send:
            send.return_value = {
                "success": True,
                "severity": "info",
                "code": "EDITOR_CTRL_CONSOLE_OK",
                "message": "ok",
                "data": {"entries": []},
                "diagnostics": [],
            }
            server = create_server()
            asyncio.run(server.call_tool(
                "editor_console",
                {"classification_filter": "non_fatal"},
            ))
        kwargs = send.call_args.kwargs
        self.assertEqual("capture_console_logs", kwargs["action"])
        self.assertEqual("non_fatal", kwargs["classification_filter"])


class TestCreateEmptyKwargs(unittest.TestCase):
    """I4: build_create_empty_kwargs omits empty optional fields."""

    def test_name_only(self) -> None:
        result = build_create_empty_kwargs(name="Obj")
        self.assertEqual(result, {"new_name": "Obj"})
        self.assertNotIn("hierarchy_path", result)
        self.assertNotIn("property_value", result)

    def test_with_parent(self) -> None:
        result = build_create_empty_kwargs(name="Obj", parent_path="/Root")
        self.assertEqual(result, {"new_name": "Obj", "hierarchy_path": "/Root"})

    def test_with_position(self) -> None:
        result = build_create_empty_kwargs(name="Obj", position="1,2,3")
        self.assertEqual(result, {"new_name": "Obj", "property_value": "1,2,3"})

    def test_all_specified(self) -> None:
        result = build_create_empty_kwargs(name="Obj", parent_path="/Root", position="1,2,3")
        self.assertEqual(result, {"new_name": "Obj", "hierarchy_path": "/Root", "property_value": "1,2,3"})

    def test_empty_strings_omitted(self) -> None:
        result = build_create_empty_kwargs(name="Obj", parent_path="", position="")
        self.assertEqual(result, {"new_name": "Obj"})
        self.assertNotIn("hierarchy_path", result)
        self.assertNotIn("property_value", result)


class TestResolveAssetPath(unittest.TestCase):
    """Validate resolve_asset_path joins Assets/... paths with project root."""

    def test_relative_assets_path_resolved(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            assets_dir = Path(tmpdir) / "Assets"
            assets_dir.mkdir()
            fake_asset = assets_dir / "test.prefab"
            fake_asset.write_text(
                "%YAML 1.1\n--- !u!1 &1\nGameObject:\n  m_Name: Test\n",
                encoding="utf-8",
            )

            resolved = resolve_asset_path("Assets/test.prefab", Path(tmpdir))
            self.assertEqual(resolved, fake_asset.resolve())

    def test_absolute_path_unchanged(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            fake_asset = Path(tmpdir) / "test.prefab"
            fake_asset.write_text("%YAML 1.1\n", encoding="utf-8")

            resolved = resolve_asset_path(str(fake_asset), Path(tmpdir))
            self.assertEqual(resolved, fake_asset)

    def test_no_project_root_returns_as_is(self) -> None:
        resolved = resolve_asset_path("Assets/nonexistent.prefab", None)
        self.assertEqual(resolved, Path("Assets/nonexistent.prefab"))

    def test_path_traversal_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            with self.assertRaises(ValueError) as ctx:
                resolve_asset_path("Assets/../../etc/passwd", Path(tmpdir))
            self.assertIn("escapes project root", str(ctx.exception))

    def test_absolute_outside_root_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            with self.assertRaises(ValueError) as ctx:
                resolve_asset_path("/outside/path.prefab", Path(tmpdir))
            self.assertIn("escapes project root", str(ctx.exception))

    def test_valid_relative_inside_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            assets_dir = Path(tmpdir) / "Assets"
            assets_dir.mkdir()
            asset = assets_dir / "test.prefab"
            asset.write_text("%YAML 1.1\n", encoding="utf-8")
            resolved = resolve_asset_path("Assets/test.prefab", Path(tmpdir))
            self.assertEqual(resolved, asset.resolve())

    def test_no_root_skips_guard(self) -> None:
        resolved = resolve_asset_path("../../etc/passwd", None)
        self.assertEqual(resolved, Path("../../etc/passwd"))


if __name__ == "__main__":
    unittest.main()
