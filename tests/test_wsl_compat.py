"""Tests for prefab_sentinel.wsl_compat — WSL detection, path conversion, command splitting."""

from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from prefab_sentinel.wsl_compat import (
    is_wsl,
    needs_windows_paths,
    split_unity_command,
    to_windows_path,
    to_wsl_path,
)


class TestIsWsl(unittest.TestCase):
    def setUp(self) -> None:
        is_wsl.cache_clear()

    def tearDown(self) -> None:
        is_wsl.cache_clear()

    @patch("prefab_sentinel.wsl_compat.os.name", "nt")
    def test_returns_false_on_non_posix(self) -> None:
        self.assertFalse(is_wsl())

    @patch("prefab_sentinel.wsl_compat.os.name", "posix")
    def test_returns_true_when_proc_version_contains_microsoft(self) -> None:
        content = "Linux version 5.15.0-1-Microsoft-standard-WSL2"
        with patch.object(Path, "read_text", return_value=content):
            self.assertTrue(is_wsl())

    @patch("prefab_sentinel.wsl_compat.os.name", "posix")
    def test_returns_true_when_proc_version_contains_wsl(self) -> None:
        content = "Linux version 6.6.87.2-microsoft-standard-WSL2"
        with patch.object(Path, "read_text", return_value=content):
            self.assertTrue(is_wsl())

    @patch("prefab_sentinel.wsl_compat.os.name", "posix")
    def test_returns_false_when_proc_version_has_no_marker(self) -> None:
        content = "Linux version 5.15.0-generic (buildd@lcy02-amd64-001)"
        with patch.object(Path, "read_text", return_value=content):
            self.assertFalse(is_wsl())

    @patch("prefab_sentinel.wsl_compat.os.name", "posix")
    def test_returns_false_on_oserror(self) -> None:
        with patch.object(Path, "read_text", side_effect=OSError("no such file")):
            self.assertFalse(is_wsl())

    @patch("prefab_sentinel.wsl_compat.os.name", "posix")
    def test_caches_result(self) -> None:
        content_wsl = "Linux version 5.15.0-1-Microsoft-standard-WSL2"
        content_plain = "Linux version 5.15.0-generic"
        with patch.object(Path, "read_text", return_value=content_wsl):
            first = is_wsl()
        with patch.object(Path, "read_text", return_value=content_plain):
            second = is_wsl()
        self.assertTrue(first)
        self.assertTrue(second)  # cached


class TestToWindowsPath(unittest.TestCase):
    def setUp(self) -> None:
        is_wsl.cache_clear()

    def tearDown(self) -> None:
        is_wsl.cache_clear()

    @patch("prefab_sentinel.wsl_compat.is_wsl", return_value=True)
    @patch("prefab_sentinel.wsl_compat.subprocess.run")
    def test_converts_wsl_path(self, mock_run: MagicMock, _mock_wsl: MagicMock) -> None:
        mock_run.return_value = MagicMock(returncode=0, stdout="D:\\Project\n")
        result = to_windows_path("/mnt/d/Project")
        self.assertEqual("D:\\Project", result)
        mock_run.assert_called_once_with(
            ["wslpath", "-w", "/mnt/d/Project"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )

    @patch("prefab_sentinel.wsl_compat.is_wsl", return_value=True)
    def test_idempotent_on_windows_path(self, _mock_wsl: MagicMock) -> None:
        result = to_windows_path("D:/Project")
        self.assertEqual("D:/Project", result)

    @patch("prefab_sentinel.wsl_compat.is_wsl", return_value=False)
    def test_noop_when_not_wsl(self, _mock_wsl: MagicMock) -> None:
        result = to_windows_path("/mnt/d/Project")
        self.assertEqual("/mnt/d/Project", result)

    @patch("prefab_sentinel.wsl_compat.is_wsl", return_value=True)
    @patch("prefab_sentinel.wsl_compat.subprocess.run", side_effect=OSError("no wslpath"))
    def test_graceful_on_wslpath_failure(self, _mock_run: MagicMock, _mock_wsl: MagicMock) -> None:
        result = to_windows_path("/mnt/d/Project")
        self.assertEqual("/mnt/d/Project", result)

    @patch("prefab_sentinel.wsl_compat.is_wsl", return_value=True)
    @patch("prefab_sentinel.wsl_compat.subprocess.run")
    def test_graceful_on_nonzero_exit(self, mock_run: MagicMock, _mock_wsl: MagicMock) -> None:
        mock_run.return_value = MagicMock(returncode=1, stdout="")
        result = to_windows_path("/mnt/d/Project")
        self.assertEqual("/mnt/d/Project", result)


class TestToWslPath(unittest.TestCase):
    def setUp(self) -> None:
        is_wsl.cache_clear()

    def tearDown(self) -> None:
        is_wsl.cache_clear()

    @patch("prefab_sentinel.wsl_compat.is_wsl", return_value=True)
    @patch("prefab_sentinel.wsl_compat.subprocess.run")
    def test_converts_windows_path(self, mock_run: MagicMock, _mock_wsl: MagicMock) -> None:
        mock_run.return_value = MagicMock(returncode=0, stdout="/mnt/d/Project\n")
        result = to_wsl_path("D:/Project")
        self.assertEqual("/mnt/d/Project", result)
        mock_run.assert_called_once_with(
            ["wslpath", "-u", "D:/Project"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )

    @patch("prefab_sentinel.wsl_compat.is_wsl", return_value=True)
    def test_idempotent_on_posix_path(self, _mock_wsl: MagicMock) -> None:
        result = to_wsl_path("/mnt/d/Project")
        self.assertEqual("/mnt/d/Project", result)

    @patch("prefab_sentinel.wsl_compat.is_wsl", return_value=False)
    def test_noop_when_not_wsl(self, _mock_wsl: MagicMock) -> None:
        result = to_wsl_path("D:/Project")
        self.assertEqual("D:/Project", result)

    @patch("prefab_sentinel.wsl_compat.is_wsl", return_value=True)
    @patch("prefab_sentinel.wsl_compat.subprocess.run", side_effect=OSError("no wslpath"))
    def test_graceful_on_wslpath_failure(self, _mock_run: MagicMock, _mock_wsl: MagicMock) -> None:
        result = to_wsl_path("D:/Project")
        self.assertEqual("D:/Project", result)

    @patch("prefab_sentinel.wsl_compat.is_wsl", return_value=True)
    def test_non_windows_non_posix_path_unchanged(self, _mock_wsl: MagicMock) -> None:
        result = to_wsl_path("relative/path")
        self.assertEqual("relative/path", result)


class TestNeedsWindowsPaths(unittest.TestCase):
    def setUp(self) -> None:
        is_wsl.cache_clear()

    def tearDown(self) -> None:
        is_wsl.cache_clear()

    @patch("prefab_sentinel.wsl_compat.is_wsl", return_value=True)
    def test_true_for_exe_on_wsl(self, _mock_wsl: MagicMock) -> None:
        self.assertTrue(needs_windows_paths(["/mnt/c/Program Files/Unity/Editor/Unity.exe"]))

    @patch("prefab_sentinel.wsl_compat.is_wsl", return_value=True)
    def test_false_for_non_exe_on_wsl(self, _mock_wsl: MagicMock) -> None:
        self.assertFalse(needs_windows_paths(["python", "script.py"]))

    @patch("prefab_sentinel.wsl_compat.is_wsl", return_value=False)
    def test_false_when_not_wsl(self, _mock_wsl: MagicMock) -> None:
        self.assertFalse(needs_windows_paths(["Unity.exe"]))

    @patch("prefab_sentinel.wsl_compat.is_wsl", return_value=True)
    def test_false_for_empty_command(self, _mock_wsl: MagicMock) -> None:
        self.assertFalse(needs_windows_paths([]))

    @patch("prefab_sentinel.wsl_compat.is_wsl", return_value=True)
    def test_case_insensitive(self, _mock_wsl: MagicMock) -> None:
        self.assertTrue(needs_windows_paths(["Unity.EXE"]))


class TestSplitUnityCommand(unittest.TestCase):
    def setUp(self) -> None:
        is_wsl.cache_clear()

    def tearDown(self) -> None:
        is_wsl.cache_clear()

    @patch("prefab_sentinel.wsl_compat.is_wsl", return_value=False)
    def test_standard_quoted_path(self, _mock_wsl: MagicMock) -> None:
        cmd, err = split_unity_command('"/mnt/c/Program Files/Unity/Editor/Unity.exe"')
        self.assertIsNone(err)
        self.assertEqual(["/mnt/c/Program Files/Unity/Editor/Unity.exe"], cmd)

    @patch("prefab_sentinel.wsl_compat.is_wsl", return_value=False)
    def test_simple_command(self, _mock_wsl: MagicMock) -> None:
        cmd, err = split_unity_command("python script.py")
        self.assertIsNone(err)
        self.assertEqual(["python", "script.py"], cmd)

    @patch("prefab_sentinel.wsl_compat.is_wsl", return_value=False)
    def test_empty_command(self, _mock_wsl: MagicMock) -> None:
        cmd, err = split_unity_command("   ")
        self.assertEqual([], cmd)
        self.assertIsNotNone(err)

    @patch("prefab_sentinel.wsl_compat.is_wsl", return_value=False)
    def test_invalid_quotes(self, _mock_wsl: MagicMock) -> None:
        cmd, err = split_unity_command('"unclosed')
        self.assertEqual([], cmd)
        self.assertIsNotNone(err)

    @patch("prefab_sentinel.wsl_compat.is_wsl", return_value=True)
    def test_progressive_join_on_wsl(self, _mock_wsl: MagicMock) -> None:
        """On WSL, unquoted path with spaces is recovered via progressive join."""
        # shlex.split("/mnt/c/Program Files/Unity/Editor/Unity.exe")
        #   -> ["/mnt/c/Program", "Files/Unity/Editor/Unity.exe"]  (2 tokens)
        # First call: "/mnt/c/Program" does not exist
        # Second call: "/mnt/c/Program Files/Unity/Editor/Unity.exe" exists
        with patch("prefab_sentinel.wsl_compat.to_wsl_path", side_effect=lambda p: p):
            with patch.object(Path, "exists") as mock_exists:
                mock_exists.side_effect = [False, True]
                cmd, err = split_unity_command(
                    "/mnt/c/Program Files/Unity/Editor/Unity.exe"
                )
        self.assertIsNone(err)
        self.assertEqual(["/mnt/c/Program Files/Unity/Editor/Unity.exe"], cmd)

    @patch("prefab_sentinel.wsl_compat.is_wsl", return_value=True)
    def test_progressive_join_with_trailing_args(self, _mock_wsl: MagicMock) -> None:
        """Progressive join preserves arguments after the reconstructed path."""
        with patch("prefab_sentinel.wsl_compat.to_wsl_path", side_effect=lambda p: p):
            with patch.object(Path, "exists") as mock_exists:
                # "/mnt/c/Program" -> False, "/mnt/c/Program Files/Unity.exe" -> True
                mock_exists.side_effect = [False, True]
                cmd, err = split_unity_command(
                    "/mnt/c/Program Files/Unity.exe -force-glcore"
                )
        self.assertIsNone(err)
        self.assertEqual(
            ["/mnt/c/Program Files/Unity.exe", "-force-glcore"],
            cmd,
        )

    @patch("prefab_sentinel.wsl_compat.is_wsl", return_value=True)
    def test_progressive_join_fallback_on_no_match(self, _mock_wsl: MagicMock) -> None:
        """If progressive join finds nothing, return original shlex result."""
        with patch("prefab_sentinel.wsl_compat.to_wsl_path", side_effect=lambda p: p):
            with patch.object(Path, "exists", return_value=False):
                cmd, err = split_unity_command("nonexistent path here")
        self.assertIsNone(err)
        self.assertEqual(["nonexistent", "path", "here"], cmd)

    @patch("prefab_sentinel.wsl_compat.is_wsl", return_value=True)
    def test_first_token_exists_skips_progressive_join(self, _mock_wsl: MagicMock) -> None:
        """If the first token already exists, no progressive join is needed."""
        with patch("prefab_sentinel.wsl_compat.to_wsl_path", side_effect=lambda p: p):
            with patch.object(Path, "exists", return_value=True):
                cmd, err = split_unity_command("python script.py")
        self.assertIsNone(err)
        self.assertEqual(["python", "script.py"], cmd)

    @patch("prefab_sentinel.wsl_compat.is_wsl", return_value=False)
    def test_no_progressive_join_off_wsl(self, _mock_wsl: MagicMock) -> None:
        """On non-WSL, spaces in unquoted paths are not recovered."""
        cmd, err = split_unity_command("/mnt/c/Program Files/Unity.exe")
        self.assertIsNone(err)
        # Standard shlex behavior: split on space
        self.assertEqual(["/mnt/c/Program", "Files/Unity.exe"], cmd)

    @patch("prefab_sentinel.wsl_compat.is_wsl", return_value=True)
    def test_progressive_join_with_windows_path_conversion(self, _mock_wsl: MagicMock) -> None:
        """Progressive join converts Windows paths to WSL paths for existence check."""
        def mock_to_wsl(p: str) -> str:
            if p.startswith("C:"):
                return p.replace("C:", "/mnt/c").replace("\\", "/")
            return p

        with patch("prefab_sentinel.wsl_compat.to_wsl_path", side_effect=mock_to_wsl):
            with patch.object(Path, "exists") as mock_exists:
                # "C:/Program" -> False, "C:/Program Files/Unity.exe" -> True
                mock_exists.side_effect = [False, True]
                cmd, err = split_unity_command("C:/Program Files/Unity.exe")
        self.assertIsNone(err)
        self.assertEqual(["C:/Program Files/Unity.exe"], cmd)


class TestResolveScopePathWsl(unittest.TestCase):
    """Verify that resolve_scope_path converts Windows paths on WSL."""

    def setUp(self) -> None:
        is_wsl.cache_clear()

    def tearDown(self) -> None:
        is_wsl.cache_clear()

    @patch("prefab_sentinel.unity_assets.to_wsl_path")
    def test_windows_scope_converted(self, mock_to_wsl: MagicMock) -> None:
        """When scope is a Windows path, to_wsl_path is called before Path()."""
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            mock_to_wsl.return_value = tmpdir
            from prefab_sentinel.unity_assets import resolve_scope_path

            result = resolve_scope_path("D:/VRChatProject/Assets", Path(tmpdir))
            mock_to_wsl.assert_called_once_with("D:/VRChatProject/Assets")
            self.assertEqual(Path(tmpdir).resolve(), result)

    @patch("prefab_sentinel.unity_assets.to_wsl_path", side_effect=lambda p: p)
    def test_posix_scope_unchanged(self, mock_to_wsl: MagicMock) -> None:
        """POSIX paths pass through to_wsl_path unchanged."""
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            from prefab_sentinel.unity_assets import resolve_scope_path

            result = resolve_scope_path(tmpdir, Path(tmpdir))
            mock_to_wsl.assert_called_once_with(tmpdir)


class TestFindProjectRootWsl(unittest.TestCase):
    """Verify that find_project_root converts Windows paths on WSL."""

    def setUp(self) -> None:
        is_wsl.cache_clear()

    def tearDown(self) -> None:
        is_wsl.cache_clear()

    @patch("prefab_sentinel.unity_assets.to_wsl_path")
    def test_windows_start_path_converted(self, mock_to_wsl: MagicMock) -> None:
        """Windows path passed to find_project_root gets converted via to_wsl_path."""
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            # Create Assets/ so find_project_root succeeds
            assets_dir = Path(tmpdir) / "Assets"
            assets_dir.mkdir()
            mock_to_wsl.return_value = tmpdir

            from prefab_sentinel.unity_assets import find_project_root

            result = find_project_root(Path("D:/VRChatProject"))
            mock_to_wsl.assert_called_once_with("D:/VRChatProject")
            self.assertEqual(Path(tmpdir).resolve(), result)


class TestReadTargetFileWsl(unittest.TestCase):
    """Verify that _read_target_file resolves paths via resolve_scope_path."""

    def setUp(self) -> None:
        is_wsl.cache_clear()

    def tearDown(self) -> None:
        is_wsl.cache_clear()

    @patch("prefab_sentinel.orchestrator.resolve_scope_path")
    def test_windows_target_path_converted(self, mock_resolve: MagicMock) -> None:
        """Windows path in _read_target_file gets resolved via resolve_scope_path."""
        mock_resolve.return_value = Path("/nonexistent/file.prefab")
        from prefab_sentinel.orchestrator import Phase1Orchestrator

        orch = Phase1Orchestrator(
            reference_resolver=MagicMock(),
            prefab_variant=MagicMock(),
            runtime_validation=MagicMock(),
            serialized_object=MagicMock(),
        )
        orch.prefab_variant.project_root = Path("/fake")
        result = orch._read_target_file("D:/Project/file.prefab", "TEST")
        mock_resolve.assert_called_once_with("D:/Project/file.prefab", Path("/fake"))
        # Should return error ToolResponse since file doesn't exist
        self.assertFalse(result.success)
        self.assertEqual("TEST_FILE_NOT_FOUND", result.code)


if __name__ == "__main__":
    unittest.main()
