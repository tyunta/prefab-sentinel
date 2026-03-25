"""Tests for auto-refresh after confirmed write operations."""

from __future__ import annotations

import os
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from prefab_sentinel.editor_bridge import BRIDGE_MODE_ENV, BRIDGE_WATCH_DIR_ENV, bridge_status
from prefab_sentinel.orchestrator import Phase1Orchestrator


class TestBridgeStatus(unittest.TestCase):
    """Tests for bridge_status() helper."""

    def test_not_connected_when_mode_missing(self) -> None:
        with patch.dict(os.environ, {BRIDGE_MODE_ENV: ""}, clear=False):
            status = bridge_status()
            self.assertFalse(status["connected"])

    def test_not_connected_when_dir_missing(self) -> None:
        with patch.dict(
            os.environ,
            {BRIDGE_MODE_ENV: "editor", BRIDGE_WATCH_DIR_ENV: "/nonexistent/xyz"},
            clear=False,
        ):
            status = bridge_status()
            self.assertFalse(status["connected"])

    def test_connected_when_valid(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.dict(
                os.environ,
                {BRIDGE_MODE_ENV: "editor", BRIDGE_WATCH_DIR_ENV: tmpdir},
                clear=False,
            ):
                status = bridge_status()
                self.assertTrue(status["connected"])
                self.assertEqual("editor", status["mode"])
                self.assertEqual(tmpdir, status["watch_dir"])


class TestMaybeAutoRefresh(unittest.TestCase):
    """Tests for Phase1Orchestrator.maybe_auto_refresh()."""

    def _make_orchestrator(self) -> Phase1Orchestrator:
        return Phase1Orchestrator(
            reference_resolver=MagicMock(),
            prefab_variant=MagicMock(),
            runtime_validation=MagicMock(),
            serialized_object=MagicMock(),
        )

    def test_skipped_when_bridge_not_connected(self) -> None:
        orch = self._make_orchestrator()
        with patch.dict(os.environ, {BRIDGE_MODE_ENV: ""}, clear=False):
            result = orch.maybe_auto_refresh()
            self.assertEqual("skipped", result)

    def test_true_when_refresh_succeeds(self) -> None:
        orch = self._make_orchestrator()
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.dict(
                os.environ,
                {BRIDGE_MODE_ENV: "editor", BRIDGE_WATCH_DIR_ENV: tmpdir},
                clear=False,
            ):
                with patch("prefab_sentinel.orchestrator.send_action") as mock_send:
                    mock_send.return_value = {"success": True}
                    result = orch.maybe_auto_refresh()
                    self.assertEqual("true", result)
                    mock_send.assert_called_once_with(action="refresh_asset_database")

    def test_false_when_refresh_fails(self) -> None:
        orch = self._make_orchestrator()
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.dict(
                os.environ,
                {BRIDGE_MODE_ENV: "editor", BRIDGE_WATCH_DIR_ENV: tmpdir},
                clear=False,
            ):
                with patch("prefab_sentinel.orchestrator.send_action") as mock_send:
                    mock_send.side_effect = OSError("bridge timeout")
                    result = orch.maybe_auto_refresh()
                    self.assertEqual("false", result)


if __name__ == "__main__":
    unittest.main()
