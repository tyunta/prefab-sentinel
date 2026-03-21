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
        }
        self.assertEqual(expected, SUPPORTED_ACTIONS)


class TestCliEditorSubcommands(unittest.TestCase):
    """Test that CLI parser accepts editor subcommands."""

    def test_editor_screenshot_parser(self) -> None:
        from prefab_sentinel.cli import build_parser

        parser = build_parser()
        args = parser.parse_args(["editor", "screenshot", "--view", "game", "--width", "1920", "--height", "1080"])
        self.assertEqual("editor", args.command)
        self.assertEqual("screenshot", args.editor_command)
        self.assertEqual("game", args.view)
        self.assertEqual(1920, args.width)
        self.assertEqual(1080, args.height)

    def test_editor_select_parser(self) -> None:
        from prefab_sentinel.cli import build_parser

        parser = build_parser()
        args = parser.parse_args(["editor", "select", "--path", "/Canvas/Panel"])
        self.assertEqual("select", args.editor_command)
        self.assertEqual("/Canvas/Panel", args.path)

    def test_editor_frame_parser(self) -> None:
        from prefab_sentinel.cli import build_parser

        parser = build_parser()
        args = parser.parse_args(["editor", "frame", "--zoom", "3.5"])
        self.assertEqual("frame", args.editor_command)
        self.assertAlmostEqual(3.5, args.zoom)

    def test_editor_instantiate_parser(self) -> None:
        from prefab_sentinel.cli import build_parser

        parser = build_parser()
        args = parser.parse_args([
            "editor", "instantiate",
            "--prefab", "Assets/Prefabs/Mic.prefab",
            "--parent", "/Canvas",
            "--position", "0,1.5,0",
        ])
        self.assertEqual("instantiate", args.editor_command)
        self.assertEqual("Assets/Prefabs/Mic.prefab", args.prefab)
        self.assertEqual("/Canvas", args.parent)
        self.assertEqual("0,1.5,0", args.position)

    def test_editor_ping_parser(self) -> None:
        from prefab_sentinel.cli import build_parser

        parser = build_parser()
        args = parser.parse_args(["editor", "ping", "--asset", "Assets/Prefabs/Mic.prefab"])
        self.assertEqual("ping", args.editor_command)
        self.assertEqual("Assets/Prefabs/Mic.prefab", args.asset)

    def test_editor_console_parser_defaults(self) -> None:
        from prefab_sentinel.cli import build_parser

        parser = build_parser()
        args = parser.parse_args(["editor", "console"])
        self.assertEqual("console", args.editor_command)
        self.assertEqual(200, args.max_entries)
        self.assertEqual("all", args.filter)
        self.assertEqual(0, args.since)
        self.assertFalse(args.classify)

    def test_editor_console_parser_all_options(self) -> None:
        from prefab_sentinel.cli import build_parser

        parser = build_parser()
        args = parser.parse_args([
            "editor", "console",
            "--max-entries", "50",
            "--filter", "error",
            "--since", "120",
            "--classify",
        ])
        self.assertEqual("console", args.editor_command)
        self.assertEqual(50, args.max_entries)
        self.assertEqual("error", args.filter)
        self.assertEqual(120, args.since)
        self.assertTrue(args.classify)


if __name__ == "__main__":
    unittest.main()
