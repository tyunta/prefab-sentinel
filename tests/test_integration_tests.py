"""Unit tests for prefab_sentinel.integration_tests orchestrator."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from prefab_sentinel.integration_tests import (
    _CS_FILES,
    build_unity_command,
    deploy_test_files,
    extract_unity_log_errors,
    parse_integration_results,
    run_integration_tests,
)
from prefab_sentinel.wsl_compat import is_wsl


class DeployTests(unittest.TestCase):
    """Tests for deploy_test_files."""

    def test_deploy_copies_cs_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "TestProject"
            project.mkdir()

            # Create fake CS source dir with required files
            cs_src = Path(tmp) / "cs_sources"
            cs_src.mkdir()
            for name in _CS_FILES:
                (cs_src / name).write_text(f"// {name}", encoding="utf-8")

            dest = deploy_test_files(project, cs_source_dir=cs_src)

            self.assertTrue(dest.is_dir())
            for name in _CS_FILES:
                deployed = dest / name
                self.assertTrue(deployed.is_file(), f"Missing: {deployed}")
                self.assertEqual(deployed.read_text(encoding="utf-8"), f"// {name}")

    def test_deploy_raises_on_missing_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "TestProject"
            project.mkdir()
            empty_src = Path(tmp) / "empty"
            empty_src.mkdir()

            with self.assertRaises(FileNotFoundError):
                deploy_test_files(project, cs_source_dir=empty_src)


class BuildCommandTests(unittest.TestCase):
    """Tests for build_unity_command."""

    def setUp(self) -> None:
        is_wsl.cache_clear()

    def tearDown(self) -> None:
        is_wsl.cache_clear()

    @patch("prefab_sentinel.wsl_compat.is_wsl", return_value=False)
    def test_build_command_shape(self, _mock_wsl):
        cmd = build_unity_command(
            "Unity.exe",
            Path("/project"),
            Path("/out/results.json"),
            Path("/out/unity.log"),
        )
        self.assertIn("-batchmode", cmd)
        self.assertIn("-quit", cmd)
        self.assertIn("-executeMethod", cmd)
        self.assertIn("PrefabSentinel.UnityIntegrationTests.RunAll", cmd)
        self.assertIn("-sentinelTestOutputPath", cmd)
        idx = cmd.index("-sentinelTestOutputPath")
        self.assertEqual(cmd[idx + 1], str(Path("/out/results.json")))

    @patch("prefab_sentinel.wsl_compat.is_wsl", return_value=True)
    @patch("prefab_sentinel.wsl_compat.subprocess.run")
    def test_build_command_converts_paths_on_wsl(self, mock_run, _mock_wsl):
        """On WSL with .exe command, paths are converted to Windows format."""
        mock_run.return_value = MagicMock(
            returncode=0, stdout="D:\\Project\n"
        )
        cmd = build_unity_command(
            "Unity.exe",
            Path("/mnt/d/Project"),
            Path("/tmp/results.json"),
            Path("/tmp/unity.log"),
        )
        # All path arguments should have been passed through to_windows_path
        idx_project = cmd.index("-projectPath")
        self.assertEqual(cmd[idx_project + 1], "D:\\Project")

    @patch("prefab_sentinel.wsl_compat.is_wsl", return_value=False)
    def test_build_command_no_conversion_off_wsl(self, _mock_wsl):
        """Off WSL, paths are passed through unchanged."""
        cmd = build_unity_command(
            "Unity.exe",
            Path("/mnt/d/Project"),
            Path("/tmp/results.json"),
            Path("/tmp/unity.log"),
        )
        idx_project = cmd.index("-projectPath")
        self.assertEqual(cmd[idx_project + 1], "/mnt/d/Project")


class ParseResultsTests(unittest.TestCase):
    """Tests for parse_integration_results."""

    def _write_results(self, tmp: str, data: dict) -> Path:
        p = Path(tmp) / "results.json"
        p.write_text(json.dumps(data), encoding="utf-8")
        return p

    def test_parse_valid_results(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self._write_results(tmp, {
                "success": True,
                "severity": "info",
                "code": "INTEGRATION_TEST_OK",
                "message": "24/24 passed.",
                "data": {"total": 24, "passed": 24, "failed": 0},
                "diagnostics": [],
            })
            result = parse_integration_results(path)
            self.assertTrue(result["success"])
            self.assertEqual(result["data"]["total"], 24)

    def test_parse_rejects_missing_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self._write_results(tmp, {"success": True})
            with self.assertRaises(ValueError) as cm:
                parse_integration_results(path)
        self.assertIsInstance(cm.exception, ValueError)
        self.assertTrue(str(cm.exception))

    def test_parse_rejects_invalid_severity(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self._write_results(tmp, {
                "success": True,
                "severity": "banana",
                "code": "X",
                "message": "x",
                "data": {},
                "diagnostics": [],
            })
            with self.assertRaises(ValueError) as cm:
                parse_integration_results(path)
        self.assertIn("severity", str(cm.exception).lower())


class ExtractLogErrorsTests(unittest.TestCase):
    """Tests for extract_unity_log_errors."""

    def test_extracts_error_lines(self):
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "unity.log"
            log.write_text(
                "Normal line\nSomething Error here\nAnother line\nNullReferenceException\n",
                encoding="utf-8",
            )
            errors = extract_unity_log_errors(log)
            self.assertEqual(len(errors), 2)
            self.assertIn("Error", errors[0])
            self.assertIn("Exception", errors[1])

    def test_missing_log_returns_empty(self):
        errors = extract_unity_log_errors(Path("/nonexistent/unity.log"))
        self.assertEqual(errors, [])


class RunIntegrationTestsErrorTests(unittest.TestCase):
    """Tests for run_integration_tests error paths (no real Unity)."""

    def test_raises_on_missing_unity_command(self):
        with tempfile.TemporaryDirectory() as tmp:
            out_dir = Path(tmp) / "out"
            project = Path(tmp) / "project"
            project.mkdir()
            with self.assertRaises(RuntimeError) as ctx:
                run_integration_tests(
                    "/nonexistent/Unity.exe",
                    project,
                    out_dir,
                    timeout_sec=5,
                )
            self.assertIn("not found", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
