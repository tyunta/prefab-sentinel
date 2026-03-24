"""Tests for file watcher invalidation dispatch."""

from __future__ import annotations

import asyncio
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from prefab_sentinel.watcher import dispatch_changes


class TestHasWatchfiles(unittest.TestCase):
    """_has_watchfiles returns True only when watchfiles is importable."""

    @patch.dict("sys.modules", {"watchfiles": MagicMock()})
    def test_returns_true_when_installed(self) -> None:
        from prefab_sentinel.watcher import _has_watchfiles

        self.assertTrue(_has_watchfiles())

    @patch.dict("sys.modules", {"watchfiles": None})
    def test_returns_false_when_missing(self) -> None:
        from prefab_sentinel.watcher import _has_watchfiles

        self.assertFalse(_has_watchfiles())


class TestStartWatcherNoWatchfiles(unittest.TestCase):
    """start_watcher returns immediately when watchfiles is missing."""

    @patch("prefab_sentinel.watcher._has_watchfiles", return_value=False)
    def test_noop_without_watchfiles(self, _mock: MagicMock) -> None:
        from prefab_sentinel.watcher import start_watcher

        session = MagicMock()
        stop = asyncio.Event()
        asyncio.run(start_watcher(session, Path("/fake"), stop))

        session.invalidate_guid_index.assert_not_called()
        session.invalidate_script_map.assert_not_called()
        session.invalidate_symbol_tree.assert_not_called()


class TestDispatchChanges(unittest.TestCase):
    """Verify dispatch_changes calls the right invalidation methods."""

    def test_meta_change_invalidates_guid_index(self) -> None:
        session = MagicMock()
        dispatch_changes(session, {(1, "/project/Assets/foo.meta")})
        session.invalidate_guid_index.assert_called_once()

    def test_cs_change_invalidates_script_map(self) -> None:
        session = MagicMock()
        dispatch_changes(session, {(2, "/project/Assets/Scripts/Foo.cs")})
        session.invalidate_script_map.assert_called_once()
        session.invalidate_guid_index.assert_not_called()

    def test_prefab_change_evicts_symbol_tree(self) -> None:
        session = MagicMock()
        dispatch_changes(session, {(2, "/project/Assets/Prefabs/Player.prefab")})
        session.invalidate_symbol_tree.assert_called_once_with(
            Path("/project/Assets/Prefabs/Player.prefab")
        )

    def test_meta_takes_priority_over_cs(self) -> None:
        """When both .meta and .cs change in same batch, guid index is invalidated."""
        session = MagicMock()
        dispatch_changes(session, {
            (1, "/project/Assets/Foo.cs.meta"),
            (2, "/project/Assets/Foo.cs"),
        })
        session.invalidate_guid_index.assert_called_once()
        session.invalidate_script_map.assert_not_called()

    def test_mat_change_evicts_symbol_tree(self) -> None:
        session = MagicMock()
        dispatch_changes(session, {(2, "/project/Assets/Materials/Red.mat")})
        session.invalidate_symbol_tree.assert_called_once_with(
            Path("/project/Assets/Materials/Red.mat")
        )

    def test_unity_scene_change_evicts_symbol_tree(self) -> None:
        session = MagicMock()
        dispatch_changes(session, {(2, "/project/Assets/Scenes/Main.unity")})
        session.invalidate_symbol_tree.assert_called_once_with(
            Path("/project/Assets/Scenes/Main.unity")
        )

    def test_unrelated_file_no_invalidation(self) -> None:
        session = MagicMock()
        dispatch_changes(session, {(2, "/project/Assets/readme.txt")})
        session.invalidate_guid_index.assert_not_called()
        session.invalidate_script_map.assert_not_called()
        session.invalidate_symbol_tree.assert_not_called()


if __name__ == "__main__":
    unittest.main()
