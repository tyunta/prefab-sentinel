from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from prefab_sentinel.bridge_check import (
    check_bridge_mode,
    check_cs_file,
    check_editor_dir,
    check_env_set,
    check_platform_wsl,
    check_unity_command,
    check_unity_project_path,
    check_watch_dir,
    format_text,
    run_all_checks,
)


class CheckEnvSetTests(unittest.TestCase):
    def test_set(self) -> None:
        with patch.dict(os.environ, {"MY_VAR": "value"}):
            result = check_env_set("MY_VAR", "BC_TEST")
        self.assertEqual(result["severity"], "info")
        self.assertEqual(result["code"], "BC_TEST")

    def test_empty(self) -> None:
        with patch.dict(os.environ, {"MY_VAR": ""}, clear=False):
            result = check_env_set("MY_VAR", "BC_TEST")
        self.assertEqual(result["severity"], "error")

    def test_unset(self) -> None:
        env = os.environ.copy()
        env.pop("MY_VAR", None)
        with patch.dict(os.environ, env, clear=True):
            result = check_env_set("MY_VAR", "BC_TEST")
        self.assertEqual(result["severity"], "error")


class CheckUnityCommandTests(unittest.TestCase):
    def test_not_set(self) -> None:
        env = os.environ.copy()
        env.pop("UNITYTOOL_UNITY_COMMAND", None)
        with patch.dict(os.environ, env, clear=True):
            result = check_unity_command()
        self.assertEqual(result["severity"], "error")

    def test_set_file_exists(self) -> None:
        with tempfile.NamedTemporaryFile(suffix=".exe") as f:
            with patch.dict(os.environ, {"UNITYTOOL_UNITY_COMMAND": f.name}):
                with patch("prefab_sentinel.bridge_check.is_wsl", return_value=False):
                    result = check_unity_command()
        self.assertEqual(result["severity"], "info")

    def test_set_file_missing(self) -> None:
        with patch.dict(os.environ, {"UNITYTOOL_UNITY_COMMAND": "/nonexistent/Unity.exe"}):
            with patch("prefab_sentinel.bridge_check.is_wsl", return_value=False):
                result = check_unity_command()
        self.assertEqual(result["severity"], "warning")


class CheckUnityProjectPathTests(unittest.TestCase):
    def test_not_set(self) -> None:
        env = os.environ.copy()
        env.pop("UNITYTOOL_UNITY_PROJECT_PATH", None)
        with patch.dict(os.environ, env, clear=True):
            result = check_unity_project_path()
        self.assertEqual(result["severity"], "error")

    def test_set_dir_exists(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            with patch.dict(os.environ, {"UNITYTOOL_UNITY_PROJECT_PATH": d}):
                with patch("prefab_sentinel.bridge_check.is_wsl", return_value=False):
                    result = check_unity_project_path()
        self.assertEqual(result["severity"], "info")

    def test_set_dir_missing(self) -> None:
        with patch.dict(os.environ, {"UNITYTOOL_UNITY_PROJECT_PATH": "/nonexistent/project"}):
            with patch("prefab_sentinel.bridge_check.is_wsl", return_value=False):
                result = check_unity_project_path()
        self.assertEqual(result["severity"], "warning")


class CheckEditorDirTests(unittest.TestCase):
    def test_project_path_none(self) -> None:
        result = check_editor_dir(None)
        self.assertEqual(result["severity"], "warning")

    def test_exists(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "Assets" / "Editor").mkdir(parents=True)
            result = check_editor_dir(Path(d))
        self.assertEqual(result["severity"], "info")

    def test_missing(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            result = check_editor_dir(Path(d))
        self.assertEqual(result["severity"], "error")


class CheckCsFileTests(unittest.TestCase):
    def test_project_path_none_required(self) -> None:
        result = check_cs_file(None, "Foo.cs", "BC_TEST", required=True)
        self.assertEqual(result["severity"], "warning")

    def test_project_path_none_optional(self) -> None:
        result = check_cs_file(None, "Foo.cs", "BC_TEST", required=False)
        self.assertEqual(result["severity"], "info")

    def test_found(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            editor = Path(d) / "Assets" / "Editor"
            editor.mkdir(parents=True)
            (editor / "Foo.cs").write_text("// bridge")
            result = check_cs_file(Path(d), "Foo.cs", "BC_TEST")
        self.assertEqual(result["severity"], "info")

    def test_missing_required(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "Assets" / "Editor").mkdir(parents=True)
            result = check_cs_file(Path(d), "Foo.cs", "BC_TEST", required=True)
        self.assertEqual(result["severity"], "error")

    def test_missing_optional(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "Assets" / "Editor").mkdir(parents=True)
            result = check_cs_file(Path(d), "Foo.cs", "BC_TEST", required=False)
        self.assertEqual(result["severity"], "info")


class CheckBridgeModeTests(unittest.TestCase):
    def test_not_set(self) -> None:
        env = os.environ.copy()
        env.pop("UNITYTOOL_BRIDGE_MODE", None)
        with patch.dict(os.environ, env, clear=True):
            result = check_bridge_mode()
        self.assertEqual(result["severity"], "info")
        self.assertIn("defaults to batchmode", result["message"])

    def test_batchmode(self) -> None:
        with patch.dict(os.environ, {"UNITYTOOL_BRIDGE_MODE": "batchmode"}):
            result = check_bridge_mode()
        self.assertEqual(result["severity"], "info")

    def test_editor(self) -> None:
        with patch.dict(os.environ, {"UNITYTOOL_BRIDGE_MODE": "editor"}):
            result = check_bridge_mode()
        self.assertEqual(result["severity"], "info")

    def test_invalid(self) -> None:
        with patch.dict(os.environ, {"UNITYTOOL_BRIDGE_MODE": "unknown"}):
            result = check_bridge_mode()
        self.assertEqual(result["severity"], "warning")


class CheckWatchDirTests(unittest.TestCase):
    def test_batchmode_not_set(self) -> None:
        env = os.environ.copy()
        env.pop("UNITYTOOL_BRIDGE_MODE", None)
        env.pop("UNITYTOOL_BRIDGE_WATCH_DIR", None)
        with patch.dict(os.environ, env, clear=True):
            result = check_watch_dir()
        self.assertEqual(result["severity"], "info")

    def test_editor_mode_missing(self) -> None:
        env = {
            "UNITYTOOL_BRIDGE_MODE": "editor",
        }
        with patch.dict(os.environ, env, clear=True):
            with patch("prefab_sentinel.bridge_check.is_wsl", return_value=False):
                result = check_watch_dir()
        self.assertEqual(result["severity"], "error")

    def test_editor_mode_set_exists(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            env = {
                "UNITYTOOL_BRIDGE_MODE": "editor",
                "UNITYTOOL_BRIDGE_WATCH_DIR": d,
            }
            with patch.dict(os.environ, env, clear=True):
                with patch("prefab_sentinel.bridge_check.is_wsl", return_value=False):
                    result = check_watch_dir()
        self.assertEqual(result["severity"], "info")

    def test_editor_mode_set_missing(self) -> None:
        env = {
            "UNITYTOOL_BRIDGE_MODE": "editor",
            "UNITYTOOL_BRIDGE_WATCH_DIR": "/nonexistent/dir",
        }
        with patch.dict(os.environ, env, clear=True):
            with patch("prefab_sentinel.bridge_check.is_wsl", return_value=False):
                result = check_watch_dir()
        self.assertEqual(result["severity"], "warning")


class CheckPlatformWslTests(unittest.TestCase):
    def test_wsl(self) -> None:
        with patch("prefab_sentinel.bridge_check.is_wsl", return_value=True):
            result = check_platform_wsl()
        self.assertEqual(result["severity"], "info")
        self.assertIn("WSL", result["message"])

    def test_non_wsl(self) -> None:
        with patch("prefab_sentinel.bridge_check.is_wsl", return_value=False):
            result = check_platform_wsl()
        self.assertEqual(result["severity"], "info")
        self.assertIn("Not running", result["message"])


class RunAllChecksTests(unittest.TestCase):
    def test_all_pass(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            project = Path(d)
            editor = project / "Assets" / "Editor"
            editor.mkdir(parents=True)
            (editor / "PrefabSentinel.UnityPatchBridge.cs").write_text("// bridge")
            (editor / "PrefabSentinel.UnityRuntimeValidationBridge.cs").write_text("// bridge")

            unity_exe = project / "Unity.exe"
            unity_exe.write_text("// fake")

            env = {
                "UNITYTOOL_PATCH_BRIDGE": "python tools/unity_patch_bridge.py",
                "UNITYTOOL_UNITY_COMMAND": str(unity_exe),
                "UNITYTOOL_UNITY_PROJECT_PATH": str(project),
            }
            with patch.dict(os.environ, env, clear=True):
                with patch("prefab_sentinel.bridge_check.is_wsl", return_value=False):
                    result = run_all_checks()

        self.assertTrue(result["success"])
        self.assertEqual(result["code"], "BRIDGE_CHECK")
        self.assertEqual(result["data"]["failed"], 0)

    def test_env_unset_fails(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            with patch("prefab_sentinel.bridge_check.is_wsl", return_value=False):
                result = run_all_checks()
        self.assertFalse(result["success"])
        self.assertEqual(result["severity"], "error")
        self.assertGreater(result["data"]["failed"], 0)

    def test_partial_setup(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            project = Path(d)
            (project / "Assets" / "Editor").mkdir(parents=True)
            env = {
                "UNITYTOOL_PATCH_BRIDGE": "python tools/unity_patch_bridge.py",
                "UNITYTOOL_UNITY_COMMAND": "/nonexistent/Unity.exe",
                "UNITYTOOL_UNITY_PROJECT_PATH": str(project),
            }
            with patch.dict(os.environ, env, clear=True):
                with patch("prefab_sentinel.bridge_check.is_wsl", return_value=False):
                    result = run_all_checks()
        # C# files missing → error, Unity command missing → warning
        self.assertFalse(result["success"])
        self.assertGreater(result["data"]["failed"], 0)

    def test_envelope_structure(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            with patch("prefab_sentinel.bridge_check.is_wsl", return_value=False):
                result = run_all_checks()
        for key in ("success", "severity", "code", "message", "data", "diagnostics"):
            self.assertIn(key, result, f"Missing key: {key}")
        self.assertIsInstance(result["success"], bool)
        self.assertIn(result["severity"], {"info", "warning", "error", "critical"})
        self.assertIsInstance(result["data"], dict)
        self.assertIsInstance(result["diagnostics"], list)


class FormatTextTests(unittest.TestCase):
    def test_basic_format(self) -> None:
        envelope = {
            "success": True,
            "severity": "info",
            "code": "BRIDGE_CHECK",
            "message": "All 3 checks passed",
            "data": {"passed": 3, "failed": 0, "total": 3},
            "diagnostics": [
                {"severity": "info", "code": "BC_TEST", "message": "ok"},
                {"severity": "error", "code": "BC_FAIL", "message": "not ok"},
            ],
        }
        text = format_text(envelope)
        self.assertIn("All 3 checks passed", text)
        self.assertIn("[INFO]", text)
        self.assertIn("[ERROR]", text)
        self.assertIn("BC_TEST", text)


if __name__ == "__main__":
    unittest.main()
