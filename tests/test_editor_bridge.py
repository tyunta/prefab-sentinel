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
            "refresh_asset_database",
            "set_material",
            "delete_object",
            "list_children",
            "list_materials",
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
        self.assertEqual("", args.prefab_stage)

    def test_editor_select_parser_with_prefab_stage(self) -> None:
        from prefab_sentinel.cli import build_parser

        parser = build_parser()
        args = parser.parse_args([
            "editor", "select",
            "--path", "Hair_Base",
            "--prefab-stage", "Assets/Prefabs/Variant.prefab",
        ])
        self.assertEqual("select", args.editor_command)
        self.assertEqual("Hair_Base", args.path)
        self.assertEqual("Assets/Prefabs/Variant.prefab", args.prefab_stage)

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


    def test_editor_refresh_parser(self) -> None:
        from prefab_sentinel.cli import build_parser

        parser = build_parser()
        args = parser.parse_args(["editor", "refresh"])
        self.assertEqual("refresh", args.editor_command)

    def test_editor_set_material_parser(self) -> None:
        from prefab_sentinel.cli import build_parser

        parser = build_parser()
        args = parser.parse_args([
            "editor", "set-material",
            "--renderer", "/Body/Mesh",
            "--index", "1",
            "--material-guid", "dbb963022c0443144810d86a576e4e50",
        ])
        self.assertEqual("set-material", args.editor_command)
        self.assertEqual("/Body/Mesh", args.renderer)
        self.assertEqual(1, args.index)
        self.assertEqual("dbb963022c0443144810d86a576e4e50", args.material_guid)


    def test_editor_delete_parser(self) -> None:
        from prefab_sentinel.cli import build_parser

        parser = build_parser()
        args = parser.parse_args([
            "editor", "delete",
            "--path", "/AvatarRoot/OldAccessory",
        ])
        self.assertEqual("delete", args.editor_command)
        self.assertEqual("/AvatarRoot/OldAccessory", args.path)

    def test_editor_list_children_parser(self) -> None:
        from prefab_sentinel.cli import build_parser

        parser = build_parser()
        args = parser.parse_args([
            "editor", "list-children",
            "--path", "/AvatarRoot",
            "--depth", "2",
        ])
        self.assertEqual("list-children", args.editor_command)
        self.assertEqual("/AvatarRoot", args.path)
        self.assertEqual(2, args.depth)

    def test_editor_list_children_default_depth(self) -> None:
        from prefab_sentinel.cli import build_parser

        parser = build_parser()
        args = parser.parse_args([
            "editor", "list-children",
            "--path", "/AvatarRoot",
        ])
        self.assertEqual(1, args.depth)

    def test_editor_list_materials_parser(self) -> None:
        from prefab_sentinel.cli import build_parser

        parser = build_parser()
        args = parser.parse_args([
            "editor", "list-materials",
            "--path", "/AvatarRoot/Body",
        ])
        self.assertEqual("list-materials", args.editor_command)
        self.assertEqual("/AvatarRoot/Body", args.path)


if __name__ == "__main__":
    unittest.main()
