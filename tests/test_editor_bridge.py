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
    check_editor_bridge_env,
    send_action,
)


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
                        resp_path.write_text(json.dumps(resp))
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


if __name__ == "__main__":
    unittest.main()
