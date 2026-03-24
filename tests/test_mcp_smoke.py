"""Smoke tests that exercise MCP tools against real YAML fixtures (no mocks)."""

from __future__ import annotations

import asyncio
import os
import unittest
from pathlib import Path
from typing import Any

from prefab_sentinel.mcp_server import create_server

FIXTURES = Path(__file__).parent / "fixtures" / "smoke"


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


class McpSmokeTests(unittest.TestCase):
    """End-to-end smoke tests for MCP tools against static YAML fixtures."""

    server: Any

    @classmethod
    def setUpClass(cls) -> None:
        cls.server = create_server()

    # --- inspect_wiring ---

    def test_inspect_wiring_envelope_structure(self) -> None:
        """inspect_wiring returns a well-formed envelope response."""
        _, result = _run(self.server.call_tool(
            "inspect_wiring",
            {"asset_path": str(FIXTURES / "basic.prefab")},
        ))
        for key in ("success", "severity", "code", "data", "diagnostics"):
            self.assertIn(key, result, f"Missing envelope key: {key}")
        self.assertTrue(result["success"])

    def test_inspect_wiring_null_ratio_correct(self) -> None:
        """basic.prefab has 1 null ref out of 2 fields -> null_ratio='1/2'."""
        _, result = _run(self.server.call_tool(
            "inspect_wiring",
            {"asset_path": str(FIXTURES / "basic.prefab")},
        ))
        comps = result["data"]["components"]
        self.assertEqual(len(comps), 1)
        self.assertEqual(comps[0]["null_ratio"], "1/2")

    def test_inspect_wiring_null_field_names_correct(self) -> None:
        """basic.prefab null_field_names should be ['nullRef']."""
        _, result = _run(self.server.call_tool(
            "inspect_wiring",
            {"asset_path": str(FIXTURES / "basic.prefab")},
        ))
        comps = result["data"]["components"]
        self.assertEqual(comps[0]["null_field_names"], ["nullRef"])

    # --- validate_refs (parameter: scope, not asset_path) ---

    def test_validate_refs_detects_broken_ref(self) -> None:
        """broken_ref.prefab has fileID:99999 that does not exist."""
        _, result = _run(self.server.call_tool(
            "validate_refs",
            {"scope": str(FIXTURES / "broken_ref.prefab"), "details": True},
        ))
        for key in ("success", "severity", "code", "data", "diagnostics"):
            self.assertIn(key, result, f"Missing envelope key: {key}")
        broken_local = [
            d for d in result["diagnostics"]
            if d.get("detail", "").startswith("missing_local_id")
        ]
        self.assertGreater(len(broken_local), 0)

    def test_validate_refs_clean_file_no_broken_local_ids(self) -> None:
        """basic.prefab has no broken internal fileID references."""
        _, result = _run(self.server.call_tool(
            "validate_refs",
            {"scope": str(FIXTURES / "basic.prefab"), "details": True},
        ))
        self.assertIn("success", result)
        broken_local = [
            d for d in result.get("diagnostics", [])
            if d.get("detail", "").startswith("missing_local_id")
        ]
        self.assertEqual(len(broken_local), 0)

    # --- inspect_hierarchy ---

    def test_inspect_hierarchy_returns_root(self) -> None:
        """hierarchy.prefab has Root as the root node."""
        _, result = _run(self.server.call_tool(
            "inspect_hierarchy",
            {"asset_path": str(FIXTURES / "hierarchy.prefab")},
        ))
        for key in ("success", "severity", "code", "data", "diagnostics"):
            self.assertIn(key, result, f"Missing envelope key: {key}")
        self.assertTrue(result["success"])
        roots = result["data"]["roots"]
        self.assertGreater(len(roots), 0)
        self.assertEqual(roots[0]["name"], "Root")

    # --- validate_structure ---

    def test_validate_structure_clean_file(self) -> None:
        """hierarchy.prefab should pass structure validation (no dup fileIDs)."""
        _, result = _run(self.server.call_tool(
            "validate_structure",
            {"asset_path": str(FIXTURES / "hierarchy.prefab")},
        ))
        for key in ("success", "severity", "code", "data", "diagnostics"):
            self.assertIn(key, result, f"Missing envelope key: {key}")
        self.assertTrue(result["success"])

    def test_validate_structure_basic_file(self) -> None:
        """basic.prefab should also pass structure validation."""
        _, result = _run(self.server.call_tool(
            "validate_structure",
            {"asset_path": str(FIXTURES / "basic.prefab")},
        ))
        self.assertIn("success", result)

    # --- get_unity_symbols ---

    def test_get_unity_symbols_returns_symbols(self) -> None:
        """hierarchy.prefab should return symbols (requires Transform for tree)."""
        _, result = _run(self.server.call_tool(
            "get_unity_symbols",
            {"asset_path": str(FIXTURES / "hierarchy.prefab")},
        ))
        self.assertNotIn("success", result)
        self.assertIn("symbols", result)
        self.assertGreater(len(result["symbols"]), 0)

    def test_get_unity_symbols_hierarchy_root(self) -> None:
        """hierarchy.prefab root-level symbols should contain Root."""
        _, result = _run(self.server.call_tool(
            "get_unity_symbols",
            {"asset_path": str(FIXTURES / "hierarchy.prefab")},
        ))
        root_names = [s["name"] for s in result["symbols"]]
        self.assertIn("Root", root_names)


@unittest.skipUnless(os.environ.get("SMOKE_PROJECT_ROOT"), "no external project")
class McpSmokeExternalTests(unittest.TestCase):
    """Smoke tests against a real Unity project (opt-in via SMOKE_PROJECT_ROOT env var).

    These tests validate response structure only -- no fixture-specific value assertions.
    """

    server: Any

    @classmethod
    def setUpClass(cls) -> None:
        cls.server = create_server()
        cls.project_root = os.environ["SMOKE_PROJECT_ROOT"]
        _run(cls.server.call_tool(
            "activate_project", {"scope": cls.project_root},
        ))

    def test_validate_refs_structure(self) -> None:
        _, result = _run(self.server.call_tool(
            "validate_refs",
            {"scope": self.project_root},
        ))
        for key in ("success", "severity", "code", "data", "diagnostics"):
            self.assertIn(key, result)

    def test_inspect_wiring_structure(self) -> None:
        import glob
        prefabs = glob.glob(
            os.path.join(self.project_root, "**", "*.prefab"),
            recursive=True,
        )
        if not prefabs:
            self.skipTest("no .prefab files in project")
        _, result = _run(self.server.call_tool(
            "inspect_wiring",
            {"asset_path": prefabs[0]},
        ))
        for key in ("success", "severity", "code", "data", "diagnostics"):
            self.assertIn(key, result)
