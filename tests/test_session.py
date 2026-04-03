"""Tests for ProjectSession caching and invalidation."""

from __future__ import annotations

import asyncio
import contextlib
import os
import tempfile
import unittest
from collections.abc import Generator
from pathlib import Path
from unittest.mock import MagicMock, patch

from prefab_sentinel.bridge_constants import UNITY_PROJECT_PATH_ENV
from prefab_sentinel.session import InvalidProjectRootError, ProjectSession
from tests.yaml_helpers import (
    YAML_HEADER,
    make_gameobject,
    make_meshrenderer,
    make_transform,
)


def _simple_prefab_text() -> str:
    return YAML_HEADER + "\n".join([
        make_gameobject("100", "Cube", ["200", "300"]),
        make_transform("200", "100"),
        make_meshrenderer("300", "100"),
    ])


@contextlib.contextmanager
def _tmp_prefab() -> Generator[Path, None, None]:
    """Write a minimal prefab to a temp file and yield its Path."""
    with tempfile.NamedTemporaryFile(suffix=".prefab", mode="w", delete=False) as f:
        f.write(_simple_prefab_text())
        f.flush()
        path = Path(f.name)
    try:
        yield path
    finally:
        path.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Orchestrator caching
# ---------------------------------------------------------------------------


class TestOrchestratorCaching(unittest.TestCase):
    """Phase1Orchestrator is created once and reused."""

    @patch("prefab_sentinel.session_cache.Phase1Orchestrator")
    def test_cached_across_calls(self, mock_cls: MagicMock) -> None:
        mock_cls.default.return_value = MagicMock()
        session = ProjectSession()

        orch1 = session.get_orchestrator()
        orch2 = session.get_orchestrator()

        self.assertIs(orch1, orch2)
        mock_cls.default.assert_called_once()

    @patch("prefab_sentinel.session_cache.Phase1Orchestrator")
    def test_passes_project_root(self, mock_cls: MagicMock) -> None:
        root = Path("/fake/project")
        session = ProjectSession(project_root=root)
        session.get_orchestrator()

        mock_cls.default.assert_called_once_with(project_root=root)

    @patch("prefab_sentinel.session_cache.Phase1Orchestrator")
    def test_recreated_after_guid_invalidation(self, mock_cls: MagicMock) -> None:
        mock_cls.default.side_effect = [MagicMock(), MagicMock()]
        session = ProjectSession()
        orch1 = session.get_orchestrator()

        session.invalidate_guid_index()
        orch2 = session.get_orchestrator()

        self.assertIsNot(orch1, orch2)
        self.assertEqual(mock_cls.default.call_count, 2)


# ---------------------------------------------------------------------------
# Script name map caching
# ---------------------------------------------------------------------------


class TestScriptNameMapCaching(unittest.TestCase):
    """build_script_name_map is called once and result cached."""

    @patch("prefab_sentinel.session_cache.build_script_name_map")
    def test_cached_across_calls(self, mock_build: MagicMock) -> None:
        mock_build.return_value = {"guid1": "MyScript"}
        root = Path("/fake/project")
        session = ProjectSession(project_root=root)
        fake_index = {"guid1": Path("/fake/MyScript.cs")}
        session._cache._guid_index = fake_index

        map1 = session.script_name_map()
        map2 = session.script_name_map()

        self.assertIs(map1, map2)
        mock_build.assert_called_once_with(fake_index)

    @patch("prefab_sentinel.session_cache.build_script_name_map")
    def test_returns_empty_when_no_root(self, mock_build: MagicMock) -> None:
        session = ProjectSession()
        result = session.script_name_map()
        self.assertEqual(result, {})
        mock_build.assert_not_called()

    @patch("prefab_sentinel.session_cache.collect_project_guid_index")
    @patch("prefab_sentinel.session_cache.build_script_name_map")
    def test_cleared_by_invalidate_guid_index(self, mock_build: MagicMock, mock_collect: MagicMock) -> None:
        mock_build.return_value = {"g": "S"}
        mock_collect.return_value = {"g": Path("/fake/S.cs")}
        session = ProjectSession(project_root=Path("/fake"))

        session.script_name_map()
        session.invalidate_guid_index()
        session.script_name_map()

        self.assertEqual(mock_build.call_count, 2)

    @patch("prefab_sentinel.session_cache.build_script_name_map")
    def test_cleared_by_invalidate_script_map(self, mock_build: MagicMock) -> None:
        mock_build.return_value = {"g": "S"}
        session = ProjectSession(project_root=Path("/fake"))
        fake_index = {"g": Path("/fake/S.cs")}
        session._cache._guid_index = fake_index

        session.script_name_map()
        session.invalidate_script_map()
        session.script_name_map()

        self.assertEqual(mock_build.call_count, 2)


# ---------------------------------------------------------------------------
# GUID index caching
# ---------------------------------------------------------------------------


class TestGuidIndexCaching(unittest.TestCase):
    """collect_project_guid_index is called once and result cached."""

    @patch("prefab_sentinel.session_cache.collect_project_guid_index")
    def test_cached_across_calls(self, mock_collect: MagicMock) -> None:
        mock_collect.return_value = {"guid1": Path("/proj/Assets/foo.prefab")}
        root = Path("/fake/project")
        session = ProjectSession(project_root=root)

        idx1 = session.guid_index()
        idx2 = session.guid_index()

        self.assertIs(idx1, idx2)
        mock_collect.assert_called_once_with(root, include_package_cache=False)

    @patch("prefab_sentinel.session_cache.collect_project_guid_index")
    def test_returns_empty_when_no_root(self, mock_collect: MagicMock) -> None:
        session = ProjectSession()
        result = session.guid_index()
        self.assertEqual(result, {})
        mock_collect.assert_not_called()

    @patch("prefab_sentinel.session_cache.collect_project_guid_index")
    def test_cleared_by_invalidate_guid_index(self, mock_collect: MagicMock) -> None:
        mock_collect.return_value = {"g": Path("/p")}
        session = ProjectSession(project_root=Path("/fake"))

        session.guid_index()
        session.invalidate_guid_index()
        session.guid_index()

        self.assertEqual(mock_collect.call_count, 2)


# ---------------------------------------------------------------------------
# SymbolTree mtime caching
# ---------------------------------------------------------------------------


class TestSymbolTreeCaching(unittest.TestCase):
    """SymbolTree is cached per asset path and invalidated on mtime change."""

    def test_cache_hit_same_mtime(self) -> None:
        with _tmp_prefab() as path:
            session = ProjectSession()
            text = _simple_prefab_text()

            tree1 = session.get_symbol_tree(path, text)
            tree2 = session.get_symbol_tree(path, text)

            self.assertIs(tree1, tree2)

    def test_cache_miss_on_mtime_change(self) -> None:
        import os
        import time

        with _tmp_prefab() as path:
            session = ProjectSession()
            text = _simple_prefab_text()

            tree1 = session.get_symbol_tree(path, text)

            # Touch the file to change mtime
            time.sleep(0.05)
            os.utime(path, None)

            tree2 = session.get_symbol_tree(path, text)
            self.assertIsNot(tree1, tree2)

    def test_properties_upgrade_rebuilds(self) -> None:
        """Cached with props=False, request with props=True → rebuild."""
        with _tmp_prefab() as path:
            session = ProjectSession()
            text = _simple_prefab_text()

            tree_no_props = session.get_symbol_tree(path, text, include_properties=False)
            tree_with_props = session.get_symbol_tree(path, text, include_properties=True)

            self.assertIsNot(tree_no_props, tree_with_props)

    def test_properties_downgrade_reuses(self) -> None:
        """Cached with props=True can serve props=False request."""
        with _tmp_prefab() as path:
            session = ProjectSession()
            text = _simple_prefab_text()

            tree_with_props = session.get_symbol_tree(path, text, include_properties=True)
            tree_no_props = session.get_symbol_tree(path, text, include_properties=False)

            self.assertIs(tree_with_props, tree_no_props)

    def test_evict_single_entry(self) -> None:
        with _tmp_prefab() as path:
            session = ProjectSession()
            text = _simple_prefab_text()

            tree1 = session.get_symbol_tree(path, text)
            session.invalidate_symbol_tree(path)
            tree2 = session.get_symbol_tree(path, text)

            self.assertIsNot(tree1, tree2)

    def test_nonexistent_file_not_cached(self) -> None:
        """Files that don't exist (can't stat) are built but not cached."""
        session = ProjectSession()
        path = Path("/nonexistent/test.prefab")
        text = _simple_prefab_text()

        tree1 = session.get_symbol_tree(path, text)
        tree2 = session.get_symbol_tree(path, text)

        # Both return valid trees but different instances
        self.assertIsNot(tree1, tree2)
        self.assertEqual(len(tree1.roots), 1)


# ---------------------------------------------------------------------------
# Invalidation cascading
# ---------------------------------------------------------------------------


class TestInvalidationCascades(unittest.TestCase):
    """invalidate_guid_index clears orchestrator + script map."""

    @patch("prefab_sentinel.session_cache.Phase1Orchestrator")
    @patch("prefab_sentinel.session_cache.build_script_name_map")
    def test_guid_invalidation_cascades(
        self, mock_build: MagicMock, mock_orch: MagicMock
    ) -> None:
        session = ProjectSession(project_root=Path("/fake"))
        mock_build.return_value = {}

        session.get_orchestrator()
        session.script_name_map()

        session.invalidate_guid_index()

        self.assertIsNone(session._cache._orchestrator)
        self.assertIsNone(session._cache._guid_index)
        self.assertIsNone(session._cache._script_name_map)

    def test_invalidate_guid_index_clears_symbol_cache(self) -> None:
        with _tmp_prefab() as path:
            session = ProjectSession()
            session.get_symbol_tree(path, _simple_prefab_text())
            self.assertEqual(len(session._cache._symbol_cache), 1)

            session.invalidate_guid_index()

            self.assertIsNone(session._cache._script_name_map)
            self.assertEqual(len(session._cache._symbol_cache), 0)

    @patch("prefab_sentinel.session_cache.Phase1Orchestrator")
    @patch("prefab_sentinel.session_cache.build_script_name_map")
    def test_script_map_invalidation_does_not_cascade(
        self, mock_build: MagicMock, mock_orch: MagicMock
    ) -> None:
        session = ProjectSession(project_root=Path("/fake"))
        mock_build.return_value = {}

        orch = session.get_orchestrator()
        session.script_name_map()

        session.invalidate_script_map()

        # Orchestrator not affected
        self.assertIs(session._cache._orchestrator, orch)
        self.assertIsNone(session._cache._script_name_map)

    def test_invalidate_asset_caches_clears_text_and_before(self) -> None:
        session = ProjectSession()
        mock_orch = MagicMock()
        session._cache._orchestrator = mock_orch
        path = Path("/project/Assets/Test.prefab")

        session.invalidate_asset_caches(path)

        mock_orch.invalidate_text_cache.assert_called_once_with(path)
        mock_orch.invalidate_before_cache.assert_called_once()
        # orchestrator NOT re-created
        self.assertIs(session._cache._orchestrator, mock_orch)

    def test_invalidate_asset_caches_noop_without_orchestrator(self) -> None:
        session = ProjectSession()
        self.assertIsNone(session._cache._orchestrator)
        # Should not raise
        session.invalidate_asset_caches(Path("/fake.prefab"))

    def test_invalidate_script_map_clears_symbol_cache(self) -> None:
        with _tmp_prefab() as path:
            session = ProjectSession()
            session.get_symbol_tree(path, _simple_prefab_text())
            self.assertEqual(len(session._cache._symbol_cache), 1)

            session.invalidate_script_map()

            self.assertIsNone(session._cache._script_name_map)
            self.assertEqual(len(session._cache._symbol_cache), 0)

    def test_invalidate_all_clears_everything(self) -> None:
        with _tmp_prefab() as path:
            session = ProjectSession()
            session.get_symbol_tree(path, _simple_prefab_text())
            self.assertEqual(len(session._cache._symbol_cache), 1)

            session.invalidate_all()

            self.assertEqual(len(session._cache._symbol_cache), 0)
            self.assertIsNone(session._cache._orchestrator)
            self.assertIsNone(session._cache._guid_index)
            self.assertIsNone(session._cache._script_name_map)


# ---------------------------------------------------------------------------
# Status
# ---------------------------------------------------------------------------


class TestStatus(unittest.TestCase):
    """status() returns correct cache state."""

    def test_empty_session(self) -> None:
        session = ProjectSession()
        s = session.status()

        self.assertIsNone(s["project_root"])
        self.assertIsNone(s["scope"])
        self.assertFalse(s["orchestrator_cached"])
        self.assertEqual(s["script_map_size"], 0)
        self.assertFalse(s["script_map_cached"])
        self.assertEqual(s["symbol_tree_entries"], 0)
        self.assertEqual(s["symbol_tree_paths"], [])
        self.assertFalse(s["watcher_running"])

    @patch("prefab_sentinel.session_cache.Phase1Orchestrator")
    @patch("prefab_sentinel.session_cache.build_script_name_map")
    def test_after_warm(self, mock_build: MagicMock, mock_orch: MagicMock) -> None:
        mock_build.return_value = {"g1": "S1", "g2": "S2"}
        session = ProjectSession(project_root=Path("/fake"))

        session.get_orchestrator()
        session.script_name_map()

        s = session.status()
        self.assertTrue(s["orchestrator_cached"])
        self.assertTrue(s["script_map_cached"])
        self.assertEqual(s["script_map_size"], 2)


# ---------------------------------------------------------------------------
# Activate
# ---------------------------------------------------------------------------


class TestActivate(unittest.TestCase):
    """activate() sets scope and warms caches."""

    @patch("prefab_sentinel.session_cache.build_script_name_map")
    @patch("prefab_sentinel.session_cache.Phase1Orchestrator")
    @patch("prefab_sentinel.session.resolve_scope_path")
    @patch("prefab_sentinel.session.find_project_root")
    def test_activate_sets_scope(
        self,
        mock_find: MagicMock,
        mock_resolve: MagicMock,
        mock_orch: MagicMock,
        mock_build: MagicMock,
    ) -> None:
        mock_find.return_value = Path("/unity")
        mock_resolve.return_value = Path("/unity/Assets/MyScope")
        mock_build.return_value = {}

        session = ProjectSession()
        result = asyncio.run(session.activate("Assets/MyScope"))

        self.assertEqual(session.project_root, Path("/unity"))
        self.assertEqual(session.scope, Path("/unity/Assets/MyScope"))
        self.assertTrue(result["orchestrator_cached"])
        self.assertTrue(result["script_map_cached"])

    @patch("prefab_sentinel.session_cache.build_script_name_map")
    @patch("prefab_sentinel.session_cache.Phase1Orchestrator")
    @patch("prefab_sentinel.session.resolve_scope_path")
    def test_activate_with_existing_root(
        self,
        mock_resolve: MagicMock,
        mock_orch: MagicMock,
        mock_build: MagicMock,
    ) -> None:
        mock_resolve.return_value = Path("/unity/Assets/Scope")
        mock_build.return_value = {}
        root = Path("/unity")

        session = ProjectSession(project_root=root)
        asyncio.run(session.activate("Assets/Scope"))

        # project_root unchanged
        self.assertEqual(session.project_root, root)
        mock_resolve.assert_called_once_with("Assets/Scope", root)

    @patch("prefab_sentinel.session_cache.build_script_name_map")
    @patch("prefab_sentinel.session_cache.Phase1Orchestrator")
    @patch("prefab_sentinel.session.resolve_scope_path")
    @patch("prefab_sentinel.session.find_project_root")
    def test_reactivation_clears_caches_on_scope_change(
        self,
        mock_find: MagicMock,
        mock_resolve: MagicMock,
        mock_orch: MagicMock,
        mock_build: MagicMock,
    ) -> None:
        """Re-activating with a different scope resets stale caches."""
        mock_find.return_value = Path("/unity")
        mock_resolve.side_effect = [
            Path("/unity/Assets/ScopeA"),
            Path("/unity/Assets/ScopeB"),
        ]
        mock_build.return_value = {}
        mock_orch.default.side_effect = [MagicMock(), MagicMock()]

        session = ProjectSession()
        asyncio.run(session.activate("Assets/ScopeA"))
        orch1 = session.get_orchestrator()

        asyncio.run(session.activate("Assets/ScopeB"))
        orch2 = session.get_orchestrator()

        self.assertEqual(session.scope, Path("/unity/Assets/ScopeB"))
        self.assertIsNot(orch1, orch2)
        # build_script_name_map called twice (once per activation)
        self.assertEqual(mock_build.call_count, 2)

    @patch("prefab_sentinel.session_cache.build_script_name_map")
    @patch("prefab_sentinel.session_cache.Phase1Orchestrator")
    @patch("prefab_sentinel.session.resolve_scope_path")
    @patch("prefab_sentinel.session.find_project_root")
    def test_reactivation_same_scope_keeps_caches(
        self,
        mock_find: MagicMock,
        mock_resolve: MagicMock,
        mock_orch: MagicMock,
        mock_build: MagicMock,
    ) -> None:
        """Re-activating with the same scope keeps existing caches."""
        mock_find.return_value = Path("/unity")
        mock_resolve.return_value = Path("/unity/Assets/ScopeA")
        mock_build.return_value = {}

        session = ProjectSession()
        asyncio.run(session.activate("Assets/ScopeA"))
        orch1 = session.get_orchestrator()

        asyncio.run(session.activate("Assets/ScopeA"))
        orch2 = session.get_orchestrator()

        self.assertIs(orch1, orch2)
        # build_script_name_map called only once
        mock_build.assert_called_once()

    # -- project_root parameter tests --

    @patch("prefab_sentinel.session_cache.build_script_name_map")
    @patch("prefab_sentinel.session_cache.Phase1Orchestrator")
    @patch("prefab_sentinel.session.resolve_scope_path")
    def test_activate_explicit_project_root_overrides_existing(
        self,
        mock_resolve: MagicMock,
        mock_orch: MagicMock,
        mock_build: MagicMock,
    ) -> None:
        mock_build.return_value = {}
        with tempfile.TemporaryDirectory() as tmpdir:
            assets = Path(tmpdir) / "Assets"
            assets.mkdir()
            mock_resolve.return_value = assets / "Scope"

            session = ProjectSession(project_root=Path("/old"))
            result = asyncio.run(
                session.activate("Assets/Scope", project_root=tmpdir)
            )

            self.assertEqual(session.project_root, Path(tmpdir).resolve())
            self.assertTrue(result["orchestrator_cached"])

    @patch("prefab_sentinel.session_cache.build_script_name_map")
    @patch("prefab_sentinel.session_cache.Phase1Orchestrator")
    @patch("prefab_sentinel.session.resolve_scope_path")
    @patch("prefab_sentinel.session.find_project_root")
    def test_activate_env_var_fallback(
        self,
        mock_find: MagicMock,
        mock_resolve: MagicMock,
        mock_orch: MagicMock,
        mock_build: MagicMock,
    ) -> None:
        mock_build.return_value = {}
        with tempfile.TemporaryDirectory() as tmpdir:
            assets = Path(tmpdir) / "Assets"
            assets.mkdir()
            mock_resolve.return_value = assets / "Scope"

            with patch.dict(os.environ, {UNITY_PROJECT_PATH_ENV: tmpdir}):
                session = ProjectSession()
                asyncio.run(session.activate("Assets/Scope"))

            mock_find.assert_not_called()
            self.assertEqual(session.project_root, Path(tmpdir).resolve())

    def test_activate_env_var_invalid_path_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.dict(os.environ, {UNITY_PROJECT_PATH_ENV: tmpdir}):
                session = ProjectSession()
                with self.assertRaises(InvalidProjectRootError):
                    asyncio.run(session.activate("Assets/Scope"))

    def test_activate_explicit_root_invalid_path_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            session = ProjectSession()
            with self.assertRaises(InvalidProjectRootError):
                asyncio.run(
                    session.activate("Assets/Scope", project_root=tmpdir)
                )

    @patch("prefab_sentinel.session_cache.build_script_name_map")
    @patch("prefab_sentinel.session_cache.Phase1Orchestrator")
    @patch("prefab_sentinel.session.resolve_scope_path")
    @patch("prefab_sentinel.session.find_project_root")
    def test_activate_auto_detect_when_no_root_specified(
        self,
        mock_find: MagicMock,
        mock_resolve: MagicMock,
        mock_orch: MagicMock,
        mock_build: MagicMock,
    ) -> None:
        mock_find.return_value = Path("/unity")
        mock_resolve.return_value = Path("/unity/Assets/Scope")
        mock_build.return_value = {}

        env_clean = {
            k: v for k, v in os.environ.items()
            if k != UNITY_PROJECT_PATH_ENV
        }
        with patch.dict(os.environ, env_clean, clear=True):
            session = ProjectSession()
            asyncio.run(session.activate("Assets/Scope"))

        mock_find.assert_called_once()
        self.assertEqual(session.project_root, Path("/unity"))

    @patch("prefab_sentinel.session_cache.build_script_name_map")
    @patch("prefab_sentinel.session_cache.Phase1Orchestrator")
    @patch("prefab_sentinel.session.resolve_scope_path")
    def test_activate_project_root_change_clears_cache(
        self,
        mock_resolve: MagicMock,
        mock_orch: MagicMock,
        mock_build: MagicMock,
    ) -> None:
        mock_build.return_value = {}
        with (
            tempfile.TemporaryDirectory() as dir1,
            tempfile.TemporaryDirectory() as dir2,
        ):
            (Path(dir1) / "Assets").mkdir()
            (Path(dir2) / "Assets").mkdir()
            mock_resolve.side_effect = [
                Path(dir1) / "Assets" / "Scope",
                Path(dir2) / "Assets" / "Scope",
            ]

            session = ProjectSession()
            asyncio.run(session.activate("Assets/Scope", project_root=dir1))

            with patch.object(session, "invalidate_all") as mock_inv, \
                 patch.object(session, "_stop_watcher") as mock_stop:
                asyncio.run(
                    session.activate("Assets/Scope", project_root=dir2)
                )
                mock_inv.assert_called_once()
                mock_stop.assert_awaited_once()

            self.assertEqual(session.project_root, Path(dir2).resolve())


# ---------------------------------------------------------------------------
# Shutdown
# ---------------------------------------------------------------------------


class TestShutdown(unittest.TestCase):
    """shutdown() cleans up the watcher task."""

    def test_shutdown_without_watcher(self) -> None:
        """Shutdown on a session with no watcher is a no-op."""
        session = ProjectSession()
        asyncio.run(session.shutdown())
        self.assertIsNone(session._watcher_task)

    def test_shutdown_cancels_watcher(self) -> None:
        """Shutdown cancels a running watcher task."""
        session = ProjectSession()

        async def _run() -> None:
            loop = asyncio.get_running_loop()
            # Create a long-running dummy task
            session._watcher_task = loop.create_task(asyncio.sleep(100))
            await session.shutdown()

        asyncio.run(_run())
        self.assertIsNone(session._watcher_task)


# ---------------------------------------------------------------------------
# Bridge version detection
# ---------------------------------------------------------------------------


class TestBridgeVersionDetection(unittest.TestCase):
    def test_detects_version_from_cs_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "Assets" / "Editor" / "PrefabSentinel").mkdir(parents=True)
            cs = root / "Assets" / "Editor" / "PrefabSentinel" / "PrefabSentinel.UnityEditorControlBridge.cs"
            cs.write_text('public const string BridgeVersion = "1.2.3";', encoding="utf-8")
            session = ProjectSession(project_root=root)
            self.assertEqual("1.2.3", session.detect_bridge_version())

    def test_returns_none_when_no_bridge(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "Assets").mkdir()
            session = ProjectSession(project_root=root)
            self.assertIsNone(session.detect_bridge_version())

    def test_returns_none_when_no_project_root(self) -> None:
        session = ProjectSession()
        self.assertIsNone(session.detect_bridge_version())

    def test_check_bridge_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "Assets" / "Editor").mkdir(parents=True)
            cs = root / "Assets" / "Editor" / "PrefabSentinel.UnityEditorControlBridge.cs"
            cs.write_text('public const string BridgeVersion = "0.0.1";', encoding="utf-8")
            session = ProjectSession(project_root=root)
            diag = session.check_bridge_version()
            self.assertIsNotNone(diag)
            self.assertEqual("BRIDGE_VERSION_MISMATCH", diag["code"])

    def test_check_bridge_not_found(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "Assets").mkdir()
            session = ProjectSession(project_root=root)
            diag = session.check_bridge_version()
            self.assertIsNotNone(diag)
            self.assertEqual("BRIDGE_NOT_FOUND", diag["code"])


# Watcher exception logging
# ---------------------------------------------------------------------------


class TestWatcherDoneCallback(unittest.TestCase):
    """_on_watcher_done logs unhandled exceptions."""

    def test_logs_exception(self) -> None:
        """Crashed watcher task triggers error log."""
        session = ProjectSession()

        async def _run() -> None:
            async def _crash() -> None:
                raise RuntimeError("boom")

            loop = asyncio.get_running_loop()
            task = loop.create_task(_crash())
            task.add_done_callback(session._on_watcher_done)
            # Let the task finish
            with contextlib.suppress(RuntimeError):
                await task

        with self.assertLogs("prefab_sentinel.session", level="ERROR") as cm:
            asyncio.run(_run())

        self.assertTrue(any("boom" in msg for msg in cm.output))

    def test_no_log_on_cancel(self) -> None:
        """Cancelled task does not trigger error log."""
        session = ProjectSession()

        async def _run() -> None:
            async def _wait() -> None:
                await asyncio.sleep(100)

            loop = asyncio.get_running_loop()
            task = loop.create_task(_wait())
            task.add_done_callback(session._on_watcher_done)
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

        # No ERROR log expected — would raise if assertLogs doesn't capture
        asyncio.run(_run())


# ---------------------------------------------------------------------------
# resolve_scope
# ---------------------------------------------------------------------------


class TestResolveScope(unittest.TestCase):
    """resolve_scope returns explicit > session > None."""

    def test_explicit_scope_wins(self) -> None:
        session = ProjectSession()
        session._scope = Path("/project/Assets/A")
        self.assertEqual("Assets/B", session.resolve_scope("Assets/B"))

    def test_session_scope_fallback(self) -> None:
        session = ProjectSession()
        session._scope = Path("/project/Assets/A")
        result = session.resolve_scope(None)
        self.assertEqual(str(Path("/project/Assets/A")), result)

    def test_none_when_no_scope(self) -> None:
        session = ProjectSession()
        self.assertIsNone(session.resolve_scope(None))

    def test_explicit_empty_string_is_not_none(self) -> None:
        session = ProjectSession()
        session._scope = Path("/project/Assets/A")
        # Empty string is explicit (not None)
        self.assertEqual("", session.resolve_scope(""))


# ---------------------------------------------------------------------------
# suggest_reads
# ---------------------------------------------------------------------------


class TestSuggestReads(unittest.TestCase):
    """suggest_reads() returns knowledge file paths based on project content."""

    def test_always_includes_prefab_sentinel_knowledge(self) -> None:
        """Even with empty maps, prefab-sentinel's own knowledge is returned."""
        session = ProjectSession()
        reads = session.suggest_reads()
        expected_prefixes = [
            "knowledge/prefab-sentinel-editor-camera.md",
            "knowledge/prefab-sentinel-material-operations.md",
            "knowledge/prefab-sentinel-patch-patterns.md",
            "knowledge/prefab-sentinel-variant-patterns.md",
            "knowledge/prefab-sentinel-wiring-triage.md",
            "knowledge/prefab-sentinel-workflow-patterns.md",
        ]
        for path in expected_prefixes:
            self.assertIn(path, reads)

    @patch("prefab_sentinel.session_cache.collect_project_guid_index")
    @patch("prefab_sentinel.session_cache.build_script_name_map")
    def test_detects_udonsharp_from_script_name_map(
        self, mock_build: MagicMock, mock_guid: MagicMock
    ) -> None:
        mock_build.return_value = {
            "abc123": "UdonSharpBehaviour",  # contains "UdonSharp" → match
            "def456": "MyUdonSharpScript",   # contains "UdonSharp" → match
        }
        mock_guid.return_value = {}
        session = ProjectSession(project_root=Path("/fake"))
        reads = session.suggest_reads()
        self.assertIn("knowledge/udonsharp.md", reads)
        self.assertIn("knowledge/vrchat-sdk-base.md", reads)

    @patch("prefab_sentinel.session_cache.collect_project_guid_index")
    @patch("prefab_sentinel.session_cache.build_script_name_map")
    def test_detects_liltoon_from_guid_index(
        self, mock_build: MagicMock, mock_guid: MagicMock
    ) -> None:
        mock_build.return_value = {}
        mock_guid.return_value = {
            "aaa": Path("/proj/Assets/lilToon/Shader/lts.shader"),
        }
        session = ProjectSession(project_root=Path("/fake"))
        reads = session.suggest_reads()
        self.assertIn("knowledge/liltoon.md", reads)
        self.assertIn("knowledge/vrchat-sdk-base.md", reads)

    @patch("prefab_sentinel.session_cache.collect_project_guid_index")
    @patch("prefab_sentinel.session_cache.build_script_name_map")
    def test_no_ecosystem_match_no_base(
        self, mock_build: MagicMock, mock_guid: MagicMock
    ) -> None:
        """When no ecosystem keyword matches, vrchat-sdk-base is NOT included."""
        mock_build.return_value = {"guid1": "SomeRandomScript"}
        mock_guid.return_value = {}
        session = ProjectSession(project_root=Path("/fake"))
        reads = session.suggest_reads()
        self.assertNotIn("knowledge/vrchat-sdk-base.md", reads)

    @patch("prefab_sentinel.session_cache.collect_project_guid_index")
    @patch("prefab_sentinel.session_cache.build_script_name_map")
    def test_deduplication(
        self, mock_build: MagicMock, mock_guid: MagicMock
    ) -> None:
        """Same knowledge file matched by multiple keywords is returned once."""
        mock_build.return_value = {
            "g1": "UdonSharpBehaviourWrapper",
            "g2": "UdonBehaviourSync",
        }
        mock_guid.return_value = {}
        session = ProjectSession(project_root=Path("/fake"))
        reads = session.suggest_reads()
        count = reads.count("knowledge/udonsharp.md")
        self.assertEqual(1, count)

    @patch("prefab_sentinel.session_cache.collect_project_guid_index")
    @patch("prefab_sentinel.session_cache.build_script_name_map")
    def test_sorted_prefab_sentinel_first(
        self, mock_build: MagicMock, mock_guid: MagicMock
    ) -> None:
        """prefab-sentinel knowledge comes before ecosystem knowledge."""
        mock_build.return_value = {"g1": "VRCAvatarDescriptor"}
        mock_guid.return_value = {}
        session = ProjectSession(project_root=Path("/fake"))
        reads = session.suggest_reads()
        ps_indices = [i for i, r in enumerate(reads) if "prefab-sentinel" in r]
        eco_indices = [i for i, r in enumerate(reads) if "prefab-sentinel" not in r]
        if ps_indices and eco_indices:
            self.assertLess(max(ps_indices), min(eco_indices))

    @patch("prefab_sentinel.session_cache.collect_project_guid_index")
    @patch("prefab_sentinel.session_cache.build_script_name_map")
    def test_detects_ndmf_from_asmdef_path(
        self, mock_build: MagicMock, mock_guid: MagicMock
    ) -> None:
        mock_build.return_value = {}
        mock_guid.return_value = {
            "xyz": Path("/proj/Packages/nadena.dev.ndmf/Runtime/NDMF.asmdef"),
        }
        session = ProjectSession(project_root=Path("/fake"))
        reads = session.suggest_reads()
        self.assertIn("knowledge/ndmf.md", reads)

    def test_no_project_root_returns_only_prefab_sentinel(self) -> None:
        """Without project_root, only prefab-sentinel knowledge is returned."""
        session = ProjectSession()
        reads = session.suggest_reads()
        for r in reads:
            self.assertIn("prefab-sentinel", r)


if __name__ == "__main__":
    unittest.main()
