"""Smoke tests that exercise MCP tools against real YAML fixtures (no mocks)."""

from __future__ import annotations

import os
import unittest
from pathlib import Path
from typing import Any, ClassVar

from prefab_sentinel.mcp_server import create_server
from tests._mcp_test_support import call_tool_result, structured_payload

FIXTURES = Path(__file__).parent / "fixtures" / "smoke"


def _fixture_asset(name: str) -> str:
    return (FIXTURES / name).relative_to(FIXTURES.parent.parent).as_posix()


class McpSmokeTests(unittest.TestCase):
    """End-to-end smoke tests for MCP tools against static YAML fixtures."""

    server: Any

    @classmethod
    def setUpClass(cls) -> None:
        cls.server = create_server(project_root=FIXTURES.parent.parent)

    # --- inspect_wiring ---

    def test_inspect_wiring_envelope_structure(self) -> None:
        """inspect_wiring returns a well-formed envelope response."""
        result = structured_payload(call_tool_result(self.server,
            "inspect_wiring",
            {"asset_path": _fixture_asset("basic.prefab")},
        ))
        for key in ("success", "severity", "code", "data", "diagnostics"):
            self.assertIn(key, result, f"Missing envelope key: {key}")
        self.assertTrue(result["success"])

    def test_inspect_wiring_null_ratio_correct(self) -> None:
        """basic.prefab has 1 null ref out of 2 fields -> null_ratio='1/2'."""
        result = structured_payload(call_tool_result(self.server,
            "inspect_wiring",
            {"asset_path": _fixture_asset("basic.prefab")},
        ))
        comps = result["data"]["components"]
        self.assertEqual(len(comps), 1)
        self.assertEqual(comps[0]["null_ratio"], "1/2")

    def test_inspect_wiring_null_field_names_correct(self) -> None:
        """basic.prefab null_field_names should be ['nullRef']."""
        result = structured_payload(call_tool_result(self.server,
            "inspect_wiring",
            {"asset_path": _fixture_asset("basic.prefab")},
        ))
        comps = result["data"]["components"]
        self.assertEqual(comps[0]["null_field_names"], ["nullRef"])

    # --- validate_refs (parameter: scope, not asset_path) ---

    def test_validate_refs_detects_broken_ref(self) -> None:
        """broken_ref.prefab has fileID:99999 that does not exist."""
        result = structured_payload(call_tool_result(self.server,
            "validate_refs",
            {"scope": str(FIXTURES / "broken_ref.prefab"), "details": True},
        ))
        for key in ("success", "severity", "code", "data", "diagnostics"):
            self.assertIn(key, result, f"Missing envelope key: {key}")
        # Issue #304: diagnostics surface on the wire in the unified
        # ``{severity, code, message, data}`` shape; the category lives
        # under ``code`` instead of the legacy ``detail`` slot.
        broken_local = [
            d for d in result["diagnostics"]
            if d.get("code", "").startswith("missing_local_id")
        ]
        self.assertGreater(len(broken_local), 0)

    def test_validate_refs_clean_file_no_broken_local_ids(self) -> None:
        """basic.prefab has no broken internal fileID references."""
        result = structured_payload(call_tool_result(self.server,
            "validate_refs",
            {"scope": str(FIXTURES / "basic.prefab"), "details": True},
        ))
        self.assertIn("success", result)
        # Issue #304: unified wire shape — category in ``code``.
        broken_local = [
            d for d in result.get("diagnostics", [])
            if d.get("code", "").startswith("missing_local_id")
        ]
        self.assertEqual(len(broken_local), 0)

    # --- inspect_hierarchy ---

    def test_inspect_hierarchy_returns_root(self) -> None:
        """hierarchy.prefab has Root as the only root node."""
        result = structured_payload(call_tool_result(self.server,
            "inspect_hierarchy",
            {"asset_path": _fixture_asset("hierarchy.prefab")},
        ))
        for key in ("success", "severity", "code", "data", "diagnostics"):
            self.assertIn(key, result, f"Missing envelope key: {key}")
        self.assertTrue(result["success"])
        roots = result["data"]["roots"]
        self.assertEqual(1, len(roots))
        self.assertEqual("Root", roots[0]["name"])
        self.assertEqual(
            {
                "loaded_targets": 1,
                "game_objects": result["data"]["total_game_objects"],
                "components": result["data"]["total_components"],
            },
            result["data"]["partial_counts"],
        )

    # --- validate_structure ---

    def test_validate_structure_clean_file(self) -> None:
        """hierarchy.prefab should pass structure validation (no dup fileIDs)."""
        result = structured_payload(call_tool_result(self.server,
            "validate_structure",
            {"asset_path": _fixture_asset("hierarchy.prefab")},
        ))
        for key in ("success", "severity", "code", "data", "diagnostics"):
            self.assertIn(key, result, f"Missing envelope key: {key}")
        self.assertTrue(result["success"])

    def test_validate_structure_basic_file(self) -> None:
        """basic.prefab should also pass structure validation."""
        result = structured_payload(call_tool_result(self.server,
            "validate_structure",
            {"asset_path": _fixture_asset("basic.prefab")},
        ))
        self.assertIn("success", result)

    # --- get_unity_symbols ---

    def test_get_unity_symbols_returns_symbols(self) -> None:
        """hierarchy.prefab should return symbols (requires Transform for tree)."""
        result = structured_payload(call_tool_result(self.server,
            "get_unity_symbols",
            {"asset_path": _fixture_asset("hierarchy.prefab")},
        ))
        self.assertNotIn("success", result)
        self.assertIn("symbols", result)
        self.assertGreater(len(result["symbols"]), 0)

    def test_get_unity_symbols_hierarchy_root(self) -> None:
        """hierarchy.prefab root-level symbols should contain Root."""
        result = structured_payload(call_tool_result(self.server,
            "get_unity_symbols",
            {"asset_path": _fixture_asset("hierarchy.prefab")},
        ))
        root_names = [s["name"] for s in result["symbols"]]
        self.assertIn("Root", root_names)


@unittest.skipUnless(os.environ.get("SMOKE_PROJECT_ROOT"), "no external project")
class McpSmokeExternalTests(unittest.TestCase):
    """Smoke tests against a real Unity project (opt-in via SMOKE_PROJECT_ROOT env var).

    These tests validate response structure only -- no fixture-specific value assertions.
    """

    server: Any
    project_root: ClassVar[str]

    @classmethod
    def setUpClass(cls) -> None:
        cls.server = create_server()
        cls.project_root = os.environ["SMOKE_PROJECT_ROOT"]
        structured_payload(call_tool_result(
            cls.server,
            "activate_project",
            {"scope": cls.project_root},
        ))

    def test_validate_refs_structure(self) -> None:
        result = structured_payload(call_tool_result(self.server,
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
        result = structured_payload(call_tool_result(self.server,
            "inspect_wiring",
            {"asset_path": prefabs[0]},
        ))
        for key in ("success", "severity", "code", "data", "diagnostics"):
            self.assertIn(key, result)
