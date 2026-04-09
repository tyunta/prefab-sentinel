"""Tests for prefab_sentinel.editor_bridge module."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from prefab_sentinel.editor_bridge import (
    BRIDGE_MODE_ENV,
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
    """Tests for environment validation."""

    @patch.dict(os.environ, {BRIDGE_MODE_ENV: "editor", BRIDGE_WATCH_DIR_ENV: ""}, clear=False)
    def test_missing_watch_dir(self) -> None:
        result = check_editor_bridge_env()
        assert result is not None
        self.assertFalse(result["success"])
        self.assertEqual("EDITOR_BRIDGE_WATCH_DIR_MISSING", result["code"])

    @patch.dict(os.environ, {BRIDGE_MODE_ENV: "batchmode"}, clear=False)
    def test_wrong_mode(self) -> None:
        result = check_editor_bridge_env()
        assert result is not None
        self.assertFalse(result["success"])
        self.assertEqual("EDITOR_BRIDGE_MODE", result["code"])

    @patch.dict(os.environ, {BRIDGE_MODE_ENV: "", BRIDGE_WATCH_DIR_ENV: ""}, clear=False)
    def test_mode_not_set(self) -> None:
        result = check_editor_bridge_env()
        assert result is not None
        self.assertFalse(result["success"])
        self.assertEqual("EDITOR_BRIDGE_MODE", result["code"])

    def test_valid_env(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.dict(
                os.environ,
                {BRIDGE_MODE_ENV: "editor", BRIDGE_WATCH_DIR_ENV: tmpdir},
                clear=False,
            ):
                result = check_editor_bridge_env()
                self.assertIsNone(result)

    @patch.dict(
        os.environ,
        {BRIDGE_MODE_ENV: "editor", BRIDGE_WATCH_DIR_ENV: "/nonexistent/path/xyz"},
        clear=False,
    )
    def test_watch_dir_not_found(self) -> None:
        result = check_editor_bridge_env()
        assert result is not None
        self.assertFalse(result["success"])
        self.assertEqual("EDITOR_BRIDGE_WATCH_DIR_NOT_FOUND", result["code"])


    def test_wsl_conversion_applied_in_check_editor_bridge_env(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.dict(
                os.environ,
                {BRIDGE_MODE_ENV: "editor", BRIDGE_WATCH_DIR_ENV: "D:\\Project\\Watch"},
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
                {BRIDGE_MODE_ENV: "editor", BRIDGE_WATCH_DIR_ENV: "D:\\Project\\Watch"},
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
                {BRIDGE_MODE_ENV: "editor", BRIDGE_WATCH_DIR_ENV: "D:\\Project\\Watch"},
                clear=False,
            ):
                with patch("prefab_sentinel.editor_bridge.check_editor_bridge_env", return_value=None):
                    with patch("prefab_sentinel.editor_bridge.to_wsl_path", return_value=tmpdir) as mock_to_wsl_path:
                        send_action(action="capture_screenshot", timeout_sec=0)
                        mock_to_wsl_path.assert_called_once_with("D:\\Project\\Watch")

    @patch.dict(
        os.environ,
        {BRIDGE_MODE_ENV: "editor", BRIDGE_WATCH_DIR_ENV: "D:\\Nonexistent"},
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
                {BRIDGE_MODE_ENV: "editor", BRIDGE_WATCH_DIR_ENV: tmpdir},
                clear=False,
            ):
                result = send_action(action="nonexistent_action")
                self.assertFalse(result["success"])
                self.assertEqual("EDITOR_BRIDGE_UNKNOWN_ACTION", result["code"])

    def test_env_not_configured(self) -> None:
        with patch.dict(os.environ, {BRIDGE_MODE_ENV: "batchmode"}, clear=False):
            result = send_action(action="capture_screenshot")
            self.assertFalse(result["success"])
            self.assertEqual("EDITOR_BRIDGE_MODE", result["code"])

    def test_request_file_written_and_timeout(self) -> None:
        """Verify request file is written correctly; timeout since no Unity responds."""
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.dict(
                os.environ,
                {BRIDGE_MODE_ENV: "editor", BRIDGE_WATCH_DIR_ENV: tmpdir},
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

            def fake_send(**kwargs):
                """Write a fake response before polling starts."""
                # Find the request file and write a matching response.
                import time

                time.sleep(0.1)
                for f in watch_dir.iterdir():
                    if f.name.endswith(".request.json"):
                        base = f.name.replace(".request.json", "")
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
                        break

            import threading

            with patch.dict(
                os.environ,
                {BRIDGE_MODE_ENV: "editor", BRIDGE_WATCH_DIR_ENV: tmpdir},
                clear=False,
            ):
                # Start a thread that writes the response after a short delay.
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

                self.assertTrue(result["success"])
                self.assertEqual("EDITOR_CTRL_SCREENSHOT_OK", result["code"])
                self.assertEqual("/tmp/test.png", result["data"]["output_path"])


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
            "recompile_scripts",
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
            "save_as_prefab",
            "editor_set_parent",
            "editor_create_empty",
            "editor_create_primitive",
            "editor_batch_create",
            "editor_batch_set_property",
            "editor_batch_set_material_property",
            "editor_open_scene",
            "editor_save_scene",
            "editor_batch_add_component",
            "editor_create_scene",
            "editor_reflect",
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
        with patch.dict(os.environ, {BRIDGE_MODE_ENV: ""}, clear=False):
            result = send_action(action="get_camera")
            self.assertFalse(result["success"])
            self.assertEqual("EDITOR_BRIDGE_MODE", result["code"])

    def test_set_camera_env_missing(self) -> None:
        """set_camera returns bridge error when env not configured."""
        with patch.dict(os.environ, {BRIDGE_MODE_ENV: ""}, clear=False):
            result = send_action(action="set_camera", yaw=0.0)
            self.assertFalse(result["success"])
            self.assertEqual("EDITOR_BRIDGE_MODE", result["code"])


class TestSetCameraParams(unittest.TestCase):
    """Validate editor_set_camera parameter conversion."""

    def test_pivot_orbit_kwargs(self) -> None:
        kwargs = build_set_camera_kwargs(
            pivot='{"x":0,"y":1.3,"z":0}',
            yaw=345.0,
            pitch=8.0,
            distance=0.28,
        )
        self.assertEqual(kwargs["camera_pivot"], [0, 1.3, 0])
        self.assertEqual(kwargs["yaw"], 345.0)
        self.assertEqual(kwargs["pitch"], 8.0)
        self.assertEqual(kwargs["distance"], 0.28)
        self.assertNotIn("camera_position", kwargs)
        self.assertNotIn("camera_look_at", kwargs)

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
            distance=0.5,
        )
        self.assertEqual(kwargs["camera_position"], [0, 1.5, -1])
        self.assertEqual(kwargs["yaw"], 0.0)
        self.assertEqual(kwargs["pitch"], 10.0)
        self.assertEqual(kwargs["distance"], 0.5)
        self.assertNotIn("camera_look_at", kwargs)

    def test_omitted_params_excluded(self) -> None:
        kwargs = build_set_camera_kwargs(yaw=180.0)
        self.assertEqual(kwargs, {"yaw": 180.0})

    def test_orthographic_passed(self) -> None:
        kwargs = build_set_camera_kwargs(orthographic=1)
        self.assertEqual(kwargs["camera_orthographic"], 1)


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
