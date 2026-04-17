"""Tests for MCP server tool registration and invocation."""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, call, patch

from prefab_sentinel.contracts import Severity, ToolResponse
from prefab_sentinel.mcp_helpers import KNOWLEDGE_URI_PREFIX
from prefab_sentinel.mcp_server import create_server
from prefab_sentinel.mcp_validation import require_change_reason
from prefab_sentinel.session import ProjectSession
from prefab_sentinel.symbol_tree_builder import build_symbol_tree
from tests.yaml_helpers import (
    YAML_HEADER,
    make_gameobject,
    make_meshrenderer,
    make_monobehaviour,
    make_transform,
)


def _run(coro: Any) -> Any:
    """Run an async coroutine synchronously.

    When the result is a call_tool response (list[TextContent]), normalises
    across MCP versions to always return a 2-tuple (content_list, parsed_dict)
    so tests can use ``_, result = _run(server.call_tool(...))``.

    For other coroutines (e.g. list_tools), returns the raw result unchanged.
    """
    raw = asyncio.run(coro)
    if isinstance(raw, tuple) and len(raw) == 2 and isinstance(raw[1], dict):
        # MCP 1.6+ Python 3.11 venv: (content_list, dict)
        return raw
    if isinstance(raw, list) and raw and hasattr(raw[0], "text"):
        # MCP 1.6+ Python 3.12: list[TextContent] from call_tool
        parsed = json.loads(raw[0].text)
        return raw, parsed
    # list_tools() or other coroutines: return as-is
    return raw


def _simple_prefab() -> str:
    """Build a minimal synthetic prefab with one GO + Transform + MeshRenderer."""
    return YAML_HEADER + "\n".join([
        make_gameobject("100", "Cube", ["200", "300"]),
        make_transform("200", "100"),
        make_meshrenderer("300", "100"),
    ])


def _make_simple_meshrenderer_prefab(go_name: str = "Cube") -> str:
    """Build a minimal prefab with a MeshRenderer component for set_component_fields tests."""
    return YAML_HEADER + "\n".join([
        make_gameobject("100", go_name, ["200", "300"]),
        make_transform("200", "100"),
        (
            "--- !u!23 &300\n"
            "MeshRenderer:\n"
            "  m_ObjectHideFlags: 0\n"
            "  m_GameObject: {fileID: 100}\n"
            "  m_Enabled: 1\n"
            "  m_CastShadows: 1\n"
        ),
    ])


def _make_simple_monobehaviour_prefab(guid: str = "aaaa1111bbbb2222cccc3333dddd4444") -> str:
    """Build a minimal prefab with a MonoBehaviour component for set/copy_component_fields tests."""
    return YAML_HEADER + "\n".join([
        make_gameobject("100", "Player", ["200", "300"]),
        make_transform("200", "100"),
        make_monobehaviour("300", "100", guid=guid, fields={
            "speed": "5",
            "health": "100",
        }),
    ])


class TestRequireChangeReason(unittest.TestCase):
    """Unit tests for the require_change_reason helper."""

    def test_should_reject_when_confirm_true_and_empty_string(self) -> None:
        result = require_change_reason(confirm=True, change_reason="")

        self.assertIsNotNone(result)
        assert result is not None
        self.assertFalse(result["success"])
        self.assertEqual("error", result["severity"])
        self.assertEqual("CHANGE_REASON_REQUIRED", result["code"])
        self.assertEqual("change_reason is required when confirm=True.", result["message"])
        self.assertEqual({}, result["data"])
        self.assertEqual([], result["diagnostics"])

    def test_should_reject_when_confirm_true_and_none(self) -> None:
        result = require_change_reason(confirm=True, change_reason=None)

        self.assertIsNotNone(result)
        assert result is not None
        self.assertFalse(result["success"])
        self.assertEqual("CHANGE_REASON_REQUIRED", result["code"])

    def test_should_pass_when_confirm_true_and_valid_reason(self) -> None:
        result = require_change_reason(confirm=True, change_reason="fix bug")

        self.assertIsNone(result)

    def test_should_pass_when_confirm_false_and_empty_string(self) -> None:
        result = require_change_reason(confirm=False, change_reason="")

        self.assertIsNone(result)

    def test_should_pass_when_confirm_false_and_none(self) -> None:
        result = require_change_reason(confirm=False, change_reason=None)

        self.assertIsNone(result)

    def test_should_pass_when_confirm_false_and_valid_reason(self) -> None:
        result = require_change_reason(confirm=False, change_reason="reason")

        self.assertIsNone(result)


class TestToolRegistration(unittest.TestCase):
    """Verify all expected tools are registered on the server."""

    def test_all_tools_registered(self) -> None:
        server = create_server()
        tools = _run(server.list_tools())
        tool_names = {t.name for t in tools}
        expected = {
            # Existing 15 tools
            "activate_project", "get_project_status",
            "get_unity_symbols", "find_unity_symbol", "find_referencing_assets",
            "validate_refs", "inspect_wiring", "inspect_variant",
            "diff_unity_symbols", "set_property",
            "add_component", "remove_component",
            "list_serialized_fields", "validate_field_rename", "check_field_coverage",
            # Editor bridge tools
            "editor_screenshot", "editor_select", "editor_frame",
            "editor_get_camera", "editor_set_camera",
            "editor_refresh", "editor_recompile", "editor_instantiate",
            "editor_set_material", "editor_delete",
            "editor_get_blend_shapes", "editor_set_blend_shape",
            "editor_list_menu_items", "editor_execute_menu_item",
            "editor_list_children", "editor_list_materials", "editor_list_roots",
            "editor_get_material_property", "editor_set_material_property",
            "editor_console", "editor_run_tests",
            "editor_find_renderers_by_material",
            "editor_rename", "editor_add_component",
            "editor_remove_component",
            "editor_create_udon_program_asset",
            "editor_set_property", "editor_save_as_prefab",
            "editor_set_parent",
            "editor_create_empty", "editor_create_primitive",
            "editor_batch_create", "editor_batch_set_property",
            "editor_batch_set_material_property",
            "editor_open_scene", "editor_save_scene",
            "editor_batch_add_component", "editor_create_scene",
            # Reflection tool
            "editor_reflect",
            # Infrastructure tools
            "deploy_bridge",
            # Inspection + orchestrator tools
            "inspect_materials", "inspect_material_asset", "set_material_property",
            "copy_asset", "rename_asset",
            "validate_structure", "revert_overrides", "vrcsdk_upload",
            "inspect_hierarchy", "validate_runtime", "validate_all_wiring",
            "patch_apply",
            "copy_component_fields",
            "set_component_fields",
            "editor_set_component_fields",
            # Editor exec tool (#74)
            "editor_run_script",
        }
        self.assertEqual(expected, tool_names)

    def test_tool_count(self) -> None:
        server = create_server()
        tools = _run(server.list_tools())
        self.assertEqual(71, len(tools))


class TestSymbolTools(unittest.TestCase):
    """Test get_unity_symbols and find_unity_symbol with synthetic YAML."""

    def setUp(self) -> None:
        self.server = create_server()
        self.prefab_text = _simple_prefab()
        self.tmp_path: Path | None = None

    def _write_prefab(self, tmp_dir: Path, name: str = "test.prefab") -> Path:
        p = tmp_dir / name
        p.write_text(self.prefab_text, encoding="utf-8")
        return p

    def test_get_unity_symbols_depth0(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            prefab = self._write_prefab(Path(td))
            _, result = _run(self.server.call_tool(
                "get_unity_symbols",
                {"asset_path": str(prefab), "depth": 0},
            ))
            self.assertEqual(str(prefab), result["asset_path"])
            symbols = result["symbols"]
            self.assertEqual(1, len(symbols))
            self.assertEqual("Cube", symbols[0]["name"])
            # depth=0: no children
            self.assertNotIn("children", symbols[0])
            # Serena-style: no envelope fields
            self.assertNotIn("success", result)
            self.assertNotIn("severity", result)

    def test_get_unity_symbols_depth1(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            prefab = self._write_prefab(Path(td))
            _, result = _run(self.server.call_tool(
                "get_unity_symbols",
                {"asset_path": str(prefab), "depth": 1},
            ))
            symbols = result["symbols"]
            children = symbols[0]["children"]
            child_names = {c["name"] for c in children}
            self.assertIn("Transform", child_names)
            self.assertIn("MeshRenderer", child_names)

    def test_find_unity_symbol_found(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            prefab = self._write_prefab(Path(td))
            _, result = _run(self.server.call_tool(
                "find_unity_symbol",
                {"asset_path": str(prefab), "symbol_path": "Cube"},
            ))
            self.assertEqual(1, len(result["matches"]))
            self.assertEqual("Cube", result["matches"][0]["name"])
            # Serena-style: no envelope fields
            self.assertNotIn("success", result)

    def test_find_unity_symbol_not_found(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            prefab = self._write_prefab(Path(td))
            _, result = _run(self.server.call_tool(
                "find_unity_symbol",
                {"asset_path": str(prefab), "symbol_path": "NonExistent"},
            ))
            self.assertEqual([], result["matches"])
            # Serena-style: empty matches = not found, no error envelope
            self.assertNotIn("success", result)

    def test_find_unity_symbol_component_path(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            prefab = self._write_prefab(Path(td))
            _, result = _run(self.server.call_tool(
                "find_unity_symbol",
                {"asset_path": str(prefab), "symbol_path": "Cube/MeshRenderer"},
            ))
            self.assertEqual(1, len(result["matches"]))
            self.assertEqual("MeshRenderer", result["matches"][0]["name"])

    def test_get_unity_symbols_file_not_found(self) -> None:
        from mcp.server.fastmcp.exceptions import ToolError

        with self.assertRaises(ToolError):
            _run(self.server.call_tool(
                "get_unity_symbols",
                {"asset_path": "/nonexistent/test.prefab"},
            ))


class TestGetUnitySymbolsDetail(unittest.TestCase):
    """Test get_unity_symbols with detail parameter."""

    def setUp(self) -> None:
        self.server = create_server()

    def _write_prefab_with_mb(self, tmp_dir: Path) -> Path:
        text = YAML_HEADER + "\n".join([
            make_gameobject("100", "Player", ["200", "300"]),
            make_transform("200", "100"),
            make_monobehaviour(
                "300", "100",
                fields={"speed": "{fileID: 0}", "health": "{fileID: 0}"},
            ),
        ])
        p = tmp_dir / "test.prefab"
        p.write_text(text, encoding="utf-8")
        return p

    def test_detail_summary_returns_minimal_nodes(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            prefab = self._write_prefab_with_mb(Path(td))
            _, result = _run(self.server.call_tool(
                "get_unity_symbols",
                {"asset_path": str(prefab), "depth": 1, "detail": "summary"},
            ))
            symbols = result["symbols"]
            root = symbols[0]
            for child in root.get("children", []):
                self.assertNotIn("file_id", child)
                self.assertNotIn("properties", child)
                self.assertNotIn("field_names", child)

    def test_detail_fields_returns_field_names(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            prefab = self._write_prefab_with_mb(Path(td))
            _, result = _run(self.server.call_tool(
                "get_unity_symbols",
                {"asset_path": str(prefab), "depth": 1, "detail": "fields"},
            ))
            symbols = result["symbols"]
            root = symbols[0]
            mb_children = [
                c for c in root.get("children", [])
                if "MonoBehaviour" in c.get("name", "")
            ]
            self.assertGreater(len(mb_children), 0)
            for mb in mb_children:
                self.assertIn("field_names", mb)
                self.assertNotIn("properties", mb)

    def test_default_depth_none_returns_full_tree(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            prefab = self._write_prefab_with_mb(Path(td))
            _, result = _run(self.server.call_tool(
                "get_unity_symbols",
                {"asset_path": str(prefab)},
            ))
            symbols = result["symbols"]
            root = symbols[0]
            self.assertIn("children", root)

    def test_explicit_depth_1_limits_children(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            prefab = self._write_prefab_with_mb(Path(td))
            _, result = _run(self.server.call_tool(
                "get_unity_symbols",
                {"asset_path": str(prefab), "depth": 1},
            ))
            symbols = result["symbols"]
            root = symbols[0]
            self.assertIn("children", root)
            for child in root["children"]:
                self.assertNotIn("children", child)

    def test_response_includes_detail_key(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            prefab = self._write_prefab_with_mb(Path(td))
            _, result = _run(self.server.call_tool(
                "get_unity_symbols",
                {"asset_path": str(prefab), "detail": "summary"},
            ))
            self.assertEqual(result["detail"], "summary")

    def test_response_detail_key_defaults_to_full(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            prefab = self._write_prefab_with_mb(Path(td))
            _, result = _run(self.server.call_tool(
                "get_unity_symbols",
                {"asset_path": str(prefab)},
            ))
            self.assertEqual(result["detail"], "full")


class TestFindUnitySymbolIncludeFields(unittest.TestCase):
    """Test find_unity_symbol with include_fields (renamed from include_properties)."""

    def setUp(self) -> None:
        self.server = create_server()

    def _write_prefab_with_mb(self, tmp_dir: Path) -> Path:
        text = YAML_HEADER + "\n".join([
            make_gameobject("100", "Player", ["200", "300"]),
            make_transform("200", "100"),
            make_monobehaviour(
                "300", "100",
                fields={"speed": "{fileID: 0}"},
            ),
        ])
        p = tmp_dir / "test.prefab"
        p.write_text(text, encoding="utf-8")
        return p

    def test_include_fields_false_default_no_properties(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            prefab = self._write_prefab_with_mb(Path(td))
            _, result = _run(self.server.call_tool(
                "find_unity_symbol",
                {"asset_path": str(prefab), "symbol_path": "Player/MonoBehaviour"},
            ))
            match = result["matches"][0]
            self.assertNotIn("properties", match)

    def test_include_fields_true_has_properties(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            prefab = self._write_prefab_with_mb(Path(td))
            _, result = _run(self.server.call_tool(
                "find_unity_symbol",
                {
                    "asset_path": str(prefab),
                    "symbol_path": "Player/MonoBehaviour",
                    "include_fields": True,
                },
            ))
            match = result["matches"][0]
            self.assertIn("properties", match)

    def test_show_origin_implies_include_fields(self) -> None:
        text = YAML_HEADER + "\n".join([
            make_gameobject("100", "Player", ["200", "300"]),
            make_transform("200", "100"),
            make_monobehaviour(
                "300", "100",
                fields={"ref": "{fileID: 100, guid: 00000000000000000000000000000000, type: 2}"},
            ),
        ])
        server = create_server()
        mock_resp = MagicMock()
        mock_resp.success = False

        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "test.prefab"
            p.write_text(text, encoding="utf-8")
            with patch("prefab_sentinel.session_cache.Phase1Orchestrator") as mock_cls:
                mock_orch = MagicMock()
                mock_orch.prefab_variant.resolve_chain_values_with_origin.return_value = mock_resp
                mock_cls.default.return_value = mock_orch
                _, result = _run(server.call_tool(
                    "find_unity_symbol",
                    {
                        "asset_path": str(p),
                        "symbol_path": "Player/MonoBehaviour",
                        "show_origin": True,
                    },
                ))
            match = result["matches"][0]
            self.assertIn("properties", match)


class TestSymbolToolsWithMonoBehaviour(unittest.TestCase):
    """Test symbol tools with MonoBehaviour components."""

    def test_find_monobehaviour_with_script_name(self) -> None:
        text = YAML_HEADER + "\n".join([
            make_gameobject("100", "Player", ["200", "300"]),
            make_transform("200", "100"),
            make_monobehaviour("300", "100", guid="aaaa1111bbbb2222cccc3333dddd4444"),
        ])
        server = create_server(project_root=None)

        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "player.prefab"
            p.write_text(text, encoding="utf-8")
            _, result = _run(server.call_tool(
                "find_unity_symbol",
                {
                    "asset_path": str(p),
                    "symbol_path": "Player/MonoBehaviour",
                },
            ))
            self.assertEqual(1, len(result["matches"]))


class TestGetUnitySymbolsExpandNested(unittest.TestCase):
    """Test expand_nested parameter wiring in get_unity_symbols."""

    def test_expand_nested_passed_to_build(self) -> None:
        text = YAML_HEADER + make_gameobject("100", "Root", ["200"]) + make_transform("200", "100")
        server = create_server(project_root=None)

        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "test.prefab"
            p.write_text(text, encoding="utf-8")
            with patch("prefab_sentinel.session_cache.build_symbol_tree", wraps=build_symbol_tree) as mock_build:
                _run(server.call_tool(
                    "get_unity_symbols",
                    {"asset_path": str(p), "expand_nested": True},
                ))
                mock_build.assert_called_once()
                _, kwargs = mock_build.call_args
                self.assertTrue(kwargs.get("expand_nested"))


class TestOrchestratorTools(unittest.TestCase):
    """Test orchestrator-backed tools via mocking."""

    def _make_server(self) -> Any:
        return create_server()

    def test_validate_refs_delegates_to_orchestrator(self) -> None:
        mock_resp = MagicMock()
        mock_resp.to_dict.return_value = {
            "success": True,
            "severity": "info",
            "code": "REF001",
            "message": "No broken references",
            "data": {},
            "diagnostics": [],
        }
        mock_orch = MagicMock()
        mock_orch.validate_refs.return_value = mock_resp

        server = self._make_server()

        with patch(
            "prefab_sentinel.session_cache.Phase1Orchestrator"
        ) as mock_cls:
            mock_cls.default.return_value = mock_orch
            _, result = _run(server.call_tool(
                "validate_refs",
                {"scope": "/some/path"},
            ))

        self.assertTrue(result["success"])
        mock_orch.validate_refs.assert_called_once_with(
            scope="/some/path",
            details=False,
            max_diagnostics=200,
        )

    def test_inspect_wiring_delegates_to_orchestrator(self) -> None:
        mock_resp = MagicMock()
        mock_resp.to_dict.return_value = {
            "success": True,
            "severity": "info",
            "data": {"components": []},
        }
        mock_orch = MagicMock()
        mock_orch.inspect_wiring.return_value = mock_resp

        server = self._make_server()

        with patch(
            "prefab_sentinel.session_cache.Phase1Orchestrator"
        ) as mock_cls:
            mock_cls.default.return_value = mock_orch
            _, result = _run(server.call_tool(
                "inspect_wiring",
                {"asset_path": "/some/test.prefab"},
            ))

        self.assertTrue(result["success"])
        mock_orch.inspect_wiring.assert_called_once_with(
            target_path="/some/test.prefab",
            udon_only=False,
        )

    def test_find_referencing_assets_delegates(self) -> None:

        mock_step = ToolResponse(
            success=True,
            severity=Severity.INFO,
            code="REF_WHERE_USED",
            message="ok",
            data={"usages": [], "usage_count": 0, "returned_usages": 0, "truncated_usages": 0},
            diagnostics=[],
        )
        mock_orch = MagicMock()
        mock_orch.reference_resolver.where_used.return_value = mock_step

        server = self._make_server()

        with patch(
            "prefab_sentinel.session_cache.Phase1Orchestrator"
        ) as mock_cls:
            mock_cls.default.return_value = mock_orch
            _, result = _run(server.call_tool(
                "find_referencing_assets",
                {"asset_or_guid": "abcd1234abcd1234abcd1234abcd1234"},
            ))

        # Direct payload format
        self.assertIn("matches", result)
        self.assertEqual([], result["matches"])
        mock_orch.reference_resolver.where_used.assert_called_once()

    def test_inspect_variant_delegates(self) -> None:
        mock_resp = MagicMock()
        mock_resp.to_dict.return_value = {
            "success": True,
            "data": {"overrides": []},
        }
        mock_orch = MagicMock()
        mock_orch.inspect_variant.return_value = mock_resp

        server = self._make_server()

        with patch(
            "prefab_sentinel.session_cache.Phase1Orchestrator"
        ) as mock_cls:
            mock_cls.default.return_value = mock_orch
            _, result = _run(server.call_tool(
                "inspect_variant",
                {"asset_path": "/some/variant.prefab", "show_origin": True},
            ))

        self.assertTrue(result["success"])
        mock_orch.inspect_variant.assert_called_once_with(
            variant_path="/some/variant.prefab",
            component_filter=None,
            show_origin=True,
        )


class TestDiffUnitySymbolsTool(unittest.TestCase):
    """Test the diff_unity_symbols MCP tool."""

    def test_delegates_to_orchestrator(self) -> None:
        mock_resp = MagicMock()
        mock_resp.to_dict.return_value = {
            "success": True,
            "data": {"diff_count": 1, "diffs": [{"property_path": "speed"}]},
        }
        mock_orch = MagicMock()
        mock_orch.diff_variant.return_value = mock_resp

        server = create_server()

        with patch(
            "prefab_sentinel.session_cache.Phase1Orchestrator"
        ) as mock_cls:
            mock_cls.default.return_value = mock_orch
            _, result = _run(server.call_tool(
                "diff_unity_symbols",
                {"asset_path": "/some/variant.prefab"},
            ))

        self.assertTrue(result["success"])
        mock_orch.diff_variant.assert_called_once_with(
            variant_path="/some/variant.prefab",
            component_filter=None,
        )

    def test_passes_component_filter(self) -> None:
        mock_resp = MagicMock()
        mock_resp.to_dict.return_value = {"success": True, "data": {"diffs": []}}
        mock_orch = MagicMock()
        mock_orch.diff_variant.return_value = mock_resp

        server = create_server()

        with patch(
            "prefab_sentinel.session_cache.Phase1Orchestrator"
        ) as mock_cls:
            mock_cls.default.return_value = mock_orch
            _run(server.call_tool(
                "diff_unity_symbols",
                {"asset_path": "/v.prefab", "component_filter": "speed"},
            ))

        mock_orch.diff_variant.assert_called_once_with(
            variant_path="/v.prefab",
            component_filter="speed",
        )


class TestFindUnitySymbolShowOrigin(unittest.TestCase):
    """Test find_unity_symbol with show_origin parameter."""

    def test_show_origin_false_returns_flat_properties(self) -> None:
        """Default show_origin=False keeps properties as {name: value}."""
        text = YAML_HEADER + "\n".join([
            make_gameobject("100", "Player", ["200", "300"]),
            make_transform("200", "100"),
            make_monobehaviour("300", "100", fields={"speed": "5.0"}),
        ])
        server = create_server()

        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "test.prefab"
            p.write_text(text, encoding="utf-8")
            _, result = _run(server.call_tool(
                "find_unity_symbol",
                {
                    "asset_path": str(p),
                    "symbol_path": "Player/MonoBehaviour",
                    "include_fields": True,
                },
            ))

        self.assertNotIn("show_origin", result)
        props = result["matches"][0].get("properties", {})
        # Flat format: {name: value_str}
        if props:
            first_val = next(iter(props.values()))
            self.assertIsInstance(first_val, str)

    def test_show_origin_true_annotates_properties(self) -> None:
        """show_origin=True changes properties to {name: {value, origin_path, origin_depth}}.

        Uses a MonoBehaviour with a reference field so analyze_wiring()
        populates properties (it only captures fileID/GUID references).
        """
        # Use a reference field that analyze_wiring will capture
        text = YAML_HEADER + "\n".join([
            make_gameobject("100", "Player", ["200", "300"]),
            make_transform("200", "100"),
            make_monobehaviour(
                "300", "100",
                fields={"targetRef": "{fileID: 100, guid: 00000000000000000000000000000000, type: 2}"},
            ),
        ])
        server = create_server()

        # Mock the orchestrator's prefab_variant to return origin data
        mock_resp = MagicMock()
        mock_resp.success = True
        mock_resp.data = {
            "values": [
                {
                    "target_file_id": "300",
                    "property_path": "targetRef",
                    "value": "{fileID: 100, guid: 00000000000000000000000000000000, type: 2}",
                    "origin_path": "Assets/Leaf.prefab",
                    "origin_depth": 0,
                },
            ],
        }

        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "test.prefab"
            p.write_text(text, encoding="utf-8")

            with patch(
                "prefab_sentinel.session_cache.Phase1Orchestrator"
            ) as mock_cls:
                mock_orch = MagicMock()
                mock_orch.prefab_variant.resolve_chain_values_with_origin.return_value = mock_resp
                mock_cls.default.return_value = mock_orch

                _, result = _run(server.call_tool(
                    "find_unity_symbol",
                    {
                        "asset_path": str(p),
                        "symbol_path": "Player/MonoBehaviour",
                        "show_origin": True,
                    },
                ))

        self.assertTrue(result.get("show_origin"))
        props = result["matches"][0].get("properties", {})
        self.assertIn("targetRef", props)
        self.assertIsInstance(props["targetRef"], dict)
        self.assertEqual("Assets/Leaf.prefab", props["targetRef"]["origin_path"])
        self.assertEqual(0, props["targetRef"]["origin_depth"])

    def test_show_origin_on_non_variant_degrades_gracefully(self) -> None:
        """show_origin=True on a non-variant still returns results (no origin)."""
        text = YAML_HEADER + "\n".join([
            make_gameobject("100", "Cube", ["200", "300"]),
            make_transform("200", "100"),
            make_monobehaviour(
                "300", "100",
                fields={"ref": "{fileID: 100, guid: 00000000000000000000000000000000, type: 2}"},
            ),
        ])
        server = create_server()

        # Mock returns not-variant response (success=False)
        mock_resp = MagicMock()
        mock_resp.success = False

        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "base.prefab"
            p.write_text(text, encoding="utf-8")

            with patch(
                "prefab_sentinel.session_cache.Phase1Orchestrator"
            ) as mock_cls:
                mock_orch = MagicMock()
                mock_orch.prefab_variant.resolve_chain_values_with_origin.return_value = mock_resp
                mock_cls.default.return_value = mock_orch

                _, result = _run(server.call_tool(
                    "find_unity_symbol",
                    {
                        "asset_path": str(p),
                        "symbol_path": "Cube/MonoBehaviour",
                        "show_origin": True,
                    },
                ))

        # find_unity_symbol still returns matches; origin annotation skipped
        self.assertEqual(1, len(result["matches"]))
        props = result["matches"][0].get("properties", {})
        # Properties remain in flat {name: value} format since annotation was skipped
        if props:
            first_val = next(iter(props.values()))
            self.assertIsInstance(first_val, str)


    def test_annotate_origins_logs_on_exception(self) -> None:
        """When orchestrator raises, _annotate_origins logs debug and returns."""
        text = YAML_HEADER + "\n".join([
            make_gameobject("100", "Cube", ["200", "300"]),
            make_transform("200", "100"),
            make_monobehaviour(
                "300", "100",
                fields={"ref": "{fileID: 100, guid: 00000000000000000000000000000000, type: 2}"},
            ),
        ])
        server = create_server()

        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "test.prefab"
            p.write_text(text, encoding="utf-8")

            with patch(
                "prefab_sentinel.session_cache.Phase1Orchestrator"
            ) as mock_cls:
                mock_orch = MagicMock()
                mock_orch.prefab_variant.resolve_chain_values_with_origin.side_effect = RuntimeError("test")
                mock_cls.default.return_value = mock_orch

                with self.assertLogs("prefab_sentinel.mcp_tools_symbols", level="DEBUG") as cm:
                    _, result = _run(server.call_tool(
                        "find_unity_symbol",
                        {
                            "asset_path": str(p),
                            "symbol_path": "Cube/MonoBehaviour",
                            "show_origin": True,
                        },
                    ))

        # Tool still returns matches (best-effort annotation)
        self.assertEqual(1, len(result["matches"]))
        # Verify debug log was emitted
        self.assertTrue(any("Origin annotation failed" in msg for msg in cm.output))


class TestSetPropertyTool(unittest.TestCase):
    """Test the set_property MCP tool."""

    def _prefab_with_meshrenderer(self) -> str:
        """Prefab: Cube → Transform + MeshRenderer."""
        return YAML_HEADER + "\n".join([
            make_gameobject("100", "Cube", ["200", "300"]),
            make_transform("200", "100"),
            make_meshrenderer("300", "100"),
        ])

    def _prefab_with_monobehaviour(self, guid: str = "aaaa1111bbbb2222cccc3333dddd4444") -> str:
        """Prefab: Player → Transform + MonoBehaviour(script)."""
        return YAML_HEADER + "\n".join([
            make_gameobject("100", "Player", ["200", "300"]),
            make_transform("200", "100"),
            make_monobehaviour("300", "100", guid=guid),
        ])

    def _mock_patch_apply_response(self, dry_run: bool = True) -> MagicMock:
        resp = MagicMock()
        resp.to_dict.return_value = {
            "success": True,
            "severity": "info",
            "code": "PATCH_APPLY_RESULT",
            "message": "patch.apply dry-run completed." if dry_run else "patch.apply completed.",
            "data": {"dry_run": dry_run, "confirm": not dry_run, "read_only": dry_run},
            "diagnostics": [],
        }
        return resp

    def test_set_property_dry_run(self) -> None:
        """confirm=False returns dry-run preview."""
        text = self._prefab_with_meshrenderer()
        server = create_server()
        mock_resp = self._mock_patch_apply_response(dry_run=True)

        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "test.prefab"
            p.write_text(text, encoding="utf-8")

            with patch(
                "prefab_sentinel.session_cache.Phase1Orchestrator"
            ) as mock_cls:
                mock_orch = MagicMock()
                mock_orch.patch_apply.return_value = mock_resp
                mock_cls.default.return_value = mock_orch

                _, result = _run(server.call_tool(
                    "set_property",
                    {
                        "asset_path": str(p),
                        "symbol_path": "Cube/MeshRenderer",
                        "property_path": "m_Enabled",
                        "value": 0,
                    },
                ))

        self.assertTrue(result["success"])
        mock_orch.patch_apply.assert_called_once()
        call_kwargs = mock_orch.patch_apply.call_args[1]
        self.assertTrue(call_kwargs["dry_run"])
        self.assertFalse(call_kwargs["confirm"])

    def test_set_property_confirm(self) -> None:
        """confirm=True applies the change."""
        text = self._prefab_with_meshrenderer()
        server = create_server()
        mock_resp = self._mock_patch_apply_response(dry_run=False)

        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "test.prefab"
            p.write_text(text, encoding="utf-8")

            with patch(
                "prefab_sentinel.session_cache.Phase1Orchestrator"
            ) as mock_cls:
                mock_orch = MagicMock()
                mock_orch.patch_apply.return_value = mock_resp
                mock_cls.default.return_value = mock_orch

                _, result = _run(server.call_tool(
                    "set_property",
                    {
                        "asset_path": str(p),
                        "symbol_path": "Cube/MeshRenderer",
                        "property_path": "m_Enabled",
                        "value": 1,
                        "confirm": True,
                        "change_reason": "enable renderer",
                    },
                ))

        self.assertTrue(result["success"])
        call_kwargs = mock_orch.patch_apply.call_args[1]
        self.assertFalse(call_kwargs["dry_run"])
        self.assertTrue(call_kwargs["confirm"])

    def test_set_property_symbol_not_found(self) -> None:
        """Returns error when symbol path doesn't resolve."""
        text = self._prefab_with_meshrenderer()
        server = create_server()

        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "test.prefab"
            p.write_text(text, encoding="utf-8")

            _, result = _run(server.call_tool(
                "set_property",
                {
                    "asset_path": str(p),
                    "symbol_path": "NonExistent/MeshRenderer",
                    "property_path": "m_Enabled",
                    "value": 0,
                },
            ))

        self.assertFalse(result["success"])
        self.assertEqual("SYMBOL_NOT_FOUND", result["code"])
        self.assertIn("suggestions", result["data"])
        self.assertIsInstance(result["data"]["suggestions"], list)

    def test_set_property_not_a_component(self) -> None:
        """Returns error when symbol path points to a GameObject."""
        text = self._prefab_with_meshrenderer()
        server = create_server()

        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "test.prefab"
            p.write_text(text, encoding="utf-8")

            _, result = _run(server.call_tool(
                "set_property",
                {
                    "asset_path": str(p),
                    "symbol_path": "Cube",
                    "property_path": "m_Name",
                    "value": "NewName",
                },
            ))

        self.assertFalse(result["success"])
        self.assertEqual("SYMBOL_NOT_COMPONENT", result["code"])
        self.assertIn("game_object", result["data"]["resolved_kind"])

    def test_set_property_builtin_component_name(self) -> None:
        """Built-in component resolves to its type name in the plan."""
        text = self._prefab_with_meshrenderer()
        server = create_server()
        mock_resp = self._mock_patch_apply_response()

        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "test.prefab"
            p.write_text(text, encoding="utf-8")

            with patch(
                "prefab_sentinel.session_cache.Phase1Orchestrator"
            ) as mock_cls:
                mock_orch = MagicMock()
                mock_orch.patch_apply.return_value = mock_resp
                mock_cls.default.return_value = mock_orch

                _, result = _run(server.call_tool(
                    "set_property",
                    {
                        "asset_path": str(p),
                        "symbol_path": "Cube/MeshRenderer",
                        "property_path": "m_Enabled",
                        "value": 0,
                    },
                ))

        # Verify the plan passed to patch_apply uses type name
        plan = mock_orch.patch_apply.call_args[1]["plan"]
        self.assertEqual("MeshRenderer", plan["ops"][0]["component"])
        # Verify symbol_resolution metadata
        self.assertEqual("MeshRenderer", result["symbol_resolution"]["resolved_component"])

    def test_set_property_monobehaviour_script_name(self) -> None:
        """MonoBehaviour resolves to its script name for the component field."""
        text = self._prefab_with_monobehaviour()
        mock_resp = self._mock_patch_apply_response()

        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "test.prefab"
            p.write_text(text, encoding="utf-8")

            # Create a project root with script meta
            scripts_dir = Path(td) / "Assets" / "Scripts"
            scripts_dir.mkdir(parents=True)
            (scripts_dir / "PlayerScript.cs").write_text("class PlayerScript {}", encoding="utf-8")
            (scripts_dir / "PlayerScript.cs.meta").write_text(
                "fileFormatVersion: 2\nguid: aaaa1111bbbb2222cccc3333dddd4444\n",
                encoding="utf-8",
            )

            server_with_root = create_server(project_root=td)

            with patch(
                "prefab_sentinel.session_cache.Phase1Orchestrator"
            ) as mock_cls:
                mock_orch = MagicMock()
                mock_orch.patch_apply.return_value = mock_resp
                mock_cls.default.return_value = mock_orch

                _, result = _run(server_with_root.call_tool(
                    "set_property",
                    {
                        "asset_path": str(p),
                        "symbol_path": "Player/MonoBehaviour(PlayerScript)",
                        "property_path": "speed",
                        "value": 10.0,
                    },
                ))

        plan = mock_orch.patch_apply.call_args[1]["plan"]
        self.assertEqual("PlayerScript", plan["ops"][0]["component"])
        self.assertEqual("PlayerScript", result["symbol_resolution"]["resolved_component"])

    def test_set_property_monobehaviour_no_script_name(self) -> None:
        """Returns error when MonoBehaviour has no resolved script name."""
        text = self._prefab_with_monobehaviour()
        server = create_server()  # no project_root

        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "test.prefab"
            p.write_text(text, encoding="utf-8")

            _, result = _run(server.call_tool(
                "set_property",
                {
                    "asset_path": str(p),
                    "symbol_path": "Player/MonoBehaviour",
                    "property_path": "speed",
                    "value": 5.0,
                },
            ))

        self.assertFalse(result["success"])
        self.assertEqual("SYMBOL_UNRESOLVABLE", result["code"])

    def test_set_property_passes_change_reason(self) -> None:
        """change_reason is forwarded to the orchestrator."""
        text = self._prefab_with_meshrenderer()
        server = create_server()
        mock_resp = self._mock_patch_apply_response()

        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "test.prefab"
            p.write_text(text, encoding="utf-8")

            with patch(
                "prefab_sentinel.session_cache.Phase1Orchestrator"
            ) as mock_cls:
                mock_orch = MagicMock()
                mock_orch.patch_apply.return_value = mock_resp
                mock_cls.default.return_value = mock_orch

                _run(server.call_tool(
                    "set_property",
                    {
                        "asset_path": str(p),
                        "symbol_path": "Cube/MeshRenderer",
                        "property_path": "m_Enabled",
                        "value": 1,
                        "change_reason": "Enable renderer for visibility",
                    },
                ))

        call_kwargs = mock_orch.patch_apply.call_args[1]
        self.assertEqual("Enable renderer for visibility", call_kwargs["change_reason"])

    def test_set_property_plan_structure(self) -> None:
        """Constructed plan follows V2 format."""
        text = self._prefab_with_meshrenderer()
        server = create_server()
        mock_resp = self._mock_patch_apply_response()

        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "test.prefab"
            p.write_text(text, encoding="utf-8")

            with patch(
                "prefab_sentinel.session_cache.Phase1Orchestrator"
            ) as mock_cls:
                mock_orch = MagicMock()
                mock_orch.patch_apply.return_value = mock_resp
                mock_cls.default.return_value = mock_orch

                _run(server.call_tool(
                    "set_property",
                    {
                        "asset_path": str(p),
                        "symbol_path": "Cube/MeshRenderer",
                        "property_path": "m_CastShadows",
                        "value": 0,
                    },
                ))

        plan = mock_orch.patch_apply.call_args[1]["plan"]
        self.assertEqual(2, plan["plan_version"])
        self.assertEqual(1, len(plan["resources"]))
        self.assertEqual("target", plan["resources"][0]["id"])
        self.assertEqual(str(p), plan["resources"][0]["path"])
        self.assertEqual("open", plan["resources"][0]["mode"])
        self.assertEqual(1, len(plan["ops"]))
        op = plan["ops"][0]
        self.assertEqual("target", op["resource"])
        self.assertEqual("set", op["op"])
        self.assertEqual("m_CastShadows", op["path"])
        self.assertEqual(0, op["value"])

    def test_set_property_symbol_resolution_metadata(self) -> None:
        """Response includes symbol_resolution metadata."""
        text = self._prefab_with_meshrenderer()
        server = create_server()
        mock_resp = self._mock_patch_apply_response()

        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "test.prefab"
            p.write_text(text, encoding="utf-8")

            with patch(
                "prefab_sentinel.session_cache.Phase1Orchestrator"
            ) as mock_cls:
                mock_orch = MagicMock()
                mock_orch.patch_apply.return_value = mock_resp
                mock_cls.default.return_value = mock_orch

                _, result = _run(server.call_tool(
                    "set_property",
                    {
                        "asset_path": str(p),
                        "symbol_path": "Cube/MeshRenderer",
                        "property_path": "m_Enabled",
                        "value": 1,
                    },
                ))

        sr = result["symbol_resolution"]
        self.assertEqual("Cube/MeshRenderer", sr["symbol_path"])
        self.assertEqual("MeshRenderer", sr["resolved_component"])
        self.assertEqual("300", sr["file_id"])
        self.assertEqual("m_Enabled", sr["property_path"])

    def test_confirm_requires_change_reason(self) -> None:
        server = create_server()

        _, result = _run(server.call_tool(
            "set_property",
            {
                "asset_path": "Assets/DoesNotExist.prefab",
                "symbol_path": "Cube/MeshRenderer",
                "property_path": "m_Enabled",
                "value": 0,
                "confirm": True,
                "change_reason": "",
            },
        ))

        self.assertFalse(result["success"])
        self.assertEqual("CHANGE_REASON_REQUIRED", result["code"])


class TestListSerializedFieldsTool(unittest.TestCase):
    """Tests for the list_serialized_fields MCP tool."""

    def test_delegates_to_orchestrator(self) -> None:
        server = create_server()
        mock_resp = ToolResponse(
            success=True,
            severity=Severity.INFO,
            code="CSF_LIST_OK",
            message="Found 2 serialized fields.",
            data={
                "script_guid": "aabb",
                "script_path": "/test/Foo.cs",
                "class_name": "Foo",
                "field_count": 2,
                "fields": [
                    {"name": "speed", "type_name": "float", "is_serialized": True, "is_public": True, "line": 1},
                    {"name": "health", "type_name": "int", "is_serialized": True, "is_public": False, "line": 2},
                ],
                "read_only": True,
            },
            diagnostics=[],
        )
        with patch("prefab_sentinel.session_cache.Phase1Orchestrator") as mock_orch_cls:
            mock_orch_cls.default.return_value.list_serialized_fields.return_value = mock_resp
            _, result = _run(server.call_tool(
                "list_serialized_fields",
                {"script_or_guid": "aabb"},
            ))

        self.assertTrue(result["success"])
        self.assertEqual("CSF_LIST_OK", result["code"])
        self.assertEqual(2, result["data"]["field_count"])

    def test_error_propagated(self) -> None:
        server = create_server()
        mock_resp = ToolResponse(
            success=False,
            severity=Severity.ERROR,
            code="CSF_RESOLVE_FAILED",
            message="Script not found.",
            data={"script": "missing.cs"},
            diagnostics=[],
        )
        with patch("prefab_sentinel.session_cache.Phase1Orchestrator") as mock_orch_cls:
            mock_orch_cls.default.return_value.list_serialized_fields.return_value = mock_resp
            _, result = _run(server.call_tool(
                "list_serialized_fields",
                {"script_or_guid": "missing.cs"},
            ))

        self.assertFalse(result["success"])
        self.assertEqual("CSF_RESOLVE_FAILED", result["code"])


class TestValidateFieldRenameTool(unittest.TestCase):
    """Tests for the validate_field_rename MCP tool."""

    def test_delegates_to_orchestrator(self) -> None:
        server = create_server()
        mock_resp = ToolResponse(
            success=True,
            severity=Severity.INFO,
            code="CSF_RENAME_OK",
            message="Rename 'speed' -> 'velocity': 3 affected components.",
            data={
                "script_guid": "aabb",
                "script_path": "/test/Foo.cs",
                "old_name": "speed",
                "new_name": "velocity",
                "conflict": False,
                "has_formerly_serialized_as": False,
                "affected_count": 3,
                "affected_assets": [],
                "read_only": True,
            },
            diagnostics=[],
        )
        with patch("prefab_sentinel.session_cache.Phase1Orchestrator") as mock_orch_cls:
            mock_orch_cls.default.return_value.validate_field_rename.return_value = mock_resp
            _, result = _run(server.call_tool(
                "validate_field_rename",
                {
                    "script_or_guid": "aabb",
                    "old_name": "speed",
                    "new_name": "velocity",
                },
            ))

        self.assertTrue(result["success"])
        self.assertEqual("CSF_RENAME_OK", result["code"])
        self.assertEqual(3, result["data"]["affected_count"])

    def test_with_scope_parameter(self) -> None:
        server = create_server()
        mock_resp = ToolResponse(
            success=True, severity=Severity.INFO, code="CSF_RENAME_OK",
            message="ok", data={"affected_count": 0, "read_only": True},
            diagnostics=[],
        )
        with patch("prefab_sentinel.session_cache.Phase1Orchestrator") as mock_orch_cls:
            mock_orch = mock_orch_cls.default.return_value
            mock_orch.validate_field_rename.return_value = mock_resp
            _run(server.call_tool(
                "validate_field_rename",
                {
                    "script_or_guid": "aabb",
                    "old_name": "speed",
                    "new_name": "velocity",
                    "scope": "Assets/Scripts",
                },
            ))
            mock_orch.validate_field_rename.assert_called_once_with(
                script_path_or_guid="aabb",
                old_name="speed",
                new_name="velocity",
                scope="Assets/Scripts",
            )


class TestCheckFieldCoverageTool(unittest.TestCase):
    """Tests for the check_field_coverage MCP tool."""

    def test_delegates_to_orchestrator(self) -> None:
        server = create_server()
        mock_resp = ToolResponse(
            success=True,
            severity=Severity.INFO,
            code="CSF_COVERAGE_OK",
            message="Checked 5 components (2 scripts): 1 unused, 2 orphaned.",
            data={
                "scope": "Assets/",
                "scripts_checked": 2,
                "components_checked": 5,
                "unused_count": 1,
                "unused_fields": [{"field_name": "oldField", "class_name": "Foo"}],
                "orphaned_count": 2,
                "orphaned_paths": [
                    {"field_name": "legacy1", "class_name": "Foo"},
                    {"field_name": "legacy2", "class_name": "Bar"},
                ],
                "read_only": True,
            },
            diagnostics=[],
        )
        with patch("prefab_sentinel.session_cache.Phase1Orchestrator") as mock_orch_cls:
            mock_orch_cls.default.return_value.check_field_coverage.return_value = mock_resp
            _, result = _run(server.call_tool(
                "check_field_coverage",
                {"scope": "Assets/"},
            ))

        self.assertTrue(result["success"])
        self.assertEqual("CSF_COVERAGE_OK", result["code"])
        self.assertEqual(1, result["data"]["unused_count"])
        self.assertEqual(2, result["data"]["orphaned_count"])


class TestSessionTools(unittest.TestCase):
    """Tests for activate_project and get_project_status tools."""

    def test_get_project_status_before_activation(self) -> None:
        server = create_server()
        _, result = _run(server.call_tool("get_project_status", {}))

        self.assertTrue(result["success"])
        self.assertEqual("SESSION_STATUS", result["code"])
        data = result["data"]
        self.assertIsNone(data["project_root"])
        self.assertIsNone(data["scope"])
        self.assertFalse(data["orchestrator_cached"])
        self.assertFalse(data["script_map_cached"])

    @patch("prefab_sentinel.session_cache.build_script_name_map")
    @patch("prefab_sentinel.session_cache.Phase1Orchestrator")
    @patch("prefab_sentinel.session.resolve_scope_path")
    @patch("prefab_sentinel.session.find_project_root")
    def test_activate_project_returns_status(
        self,
        mock_find: MagicMock,
        mock_resolve: MagicMock,
        mock_orch: MagicMock,
        mock_build: MagicMock,
    ) -> None:
        mock_find.return_value = Path("/unity")
        mock_resolve.return_value = Path("/unity/Assets/MyScope")
        mock_build.return_value = {"g1": "ScriptA"}

        server = create_server()
        _, result = _run(server.call_tool(
            "activate_project",
            {"scope": "Assets/MyScope"},
        ))

        self.assertTrue(result["success"])
        self.assertEqual("SESSION_ACTIVATED", result["code"])
        data = result["data"]
        self.assertEqual(str(Path("/unity")), data["project_root"])
        self.assertTrue(data["orchestrator_cached"])
        self.assertTrue(data["script_map_cached"])
        self.assertEqual(1, data["script_map_size"])

    @patch("prefab_sentinel.session_cache.build_script_name_map")
    @patch("prefab_sentinel.session_cache.Phase1Orchestrator")
    @patch("prefab_sentinel.session.resolve_scope_path")
    @patch("prefab_sentinel.session.find_project_root")
    def test_status_updates_after_activation(
        self,
        mock_find: MagicMock,
        mock_resolve: MagicMock,
        mock_orch: MagicMock,
        mock_build: MagicMock,
    ) -> None:
        mock_find.return_value = Path("/unity")
        mock_resolve.return_value = Path("/unity/Assets/Scope")
        mock_build.return_value = {}

        server = create_server()

        # Before: not activated
        _, before = _run(server.call_tool("get_project_status", {}))
        self.assertFalse(before["data"]["orchestrator_cached"])

        # Activate
        _run(server.call_tool("activate_project", {"scope": "Assets/Scope"}))

        # After: caches warm
        _, after = _run(server.call_tool("get_project_status", {}))
        self.assertTrue(after["data"]["orchestrator_cached"])
        self.assertTrue(after["data"]["script_map_cached"])

    @patch("prefab_sentinel.session_cache.build_script_name_map")
    @patch("prefab_sentinel.session_cache.Phase1Orchestrator")
    @patch("prefab_sentinel.session.resolve_scope_path")
    def test_activate_project_with_explicit_project_root(
        self,
        mock_resolve: MagicMock,
        mock_orch: MagicMock,
        mock_build: MagicMock,
    ) -> None:
        mock_build.return_value = {"g1": "ScriptA"}
        with tempfile.TemporaryDirectory() as tmpdir:
            assets = Path(tmpdir) / "Assets"
            assets.mkdir()
            mock_resolve.return_value = assets / "MyScope"

            server = create_server()
            _, result = _run(server.call_tool(
                "activate_project",
                {"scope": "Assets/MyScope", "project_root": tmpdir},
            ))

            self.assertTrue(result["success"])
            self.assertEqual(
                str(Path(tmpdir).resolve()), result["data"]["project_root"]
            )

    def test_activate_project_with_invalid_project_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            server = create_server()
            _, result = _run(server.call_tool(
                "activate_project",
                {"scope": "Assets/X", "project_root": tmpdir},
            ))

            self.assertFalse(result["success"])
            self.assertEqual("INVALID_PROJECT_ROOT", result["code"])

    @patch("prefab_sentinel.session_cache.build_script_name_map")
    @patch("prefab_sentinel.session_cache.Phase1Orchestrator")
    @patch("prefab_sentinel.session.resolve_scope_path")
    @patch("prefab_sentinel.session.find_project_root")
    def test_activate_project_without_project_root_backward_compat(
        self,
        mock_find: MagicMock,
        mock_resolve: MagicMock,
        mock_orch: MagicMock,
        mock_build: MagicMock,
    ) -> None:
        mock_find.return_value = Path("/unity")
        mock_resolve.return_value = Path("/unity/Assets/MyScope")
        mock_build.return_value = {"g1": "ScriptA"}

        server = create_server()
        _, result = _run(server.call_tool(
            "activate_project",
            {"scope": "Assets/MyScope"},
        ))

        self.assertTrue(result["success"])
        self.assertEqual("SESSION_ACTIVATED", result["code"])


class TestAddComponentTool(unittest.TestCase):
    """Test the add_component MCP tool."""

    def _prefab_with_child(self) -> str:
        """Prefab: Root → Transform(children=[ChildTransform])
                   Child → Transform(father=RootTransform) + MeshRenderer
        """
        return YAML_HEADER + "\n".join([
            make_gameobject("100", "Root", ["200"]),
            make_transform("200", "100", children_file_ids=["400"]),
            make_gameobject("300", "Child", ["400", "500"]),
            make_transform("400", "300", father_file_id="200"),
            make_meshrenderer("500", "300"),
        ])

    def _mock_patch_apply_response(self, dry_run: bool = True) -> MagicMock:
        resp = MagicMock()
        resp.success = True
        resp.to_dict.return_value = {
            "success": True,
            "severity": "info",
            "code": "PATCH_APPLY_RESULT",
            "message": "patch.apply dry-run completed." if dry_run else "patch.apply completed.",
            "data": {"dry_run": dry_run, "confirm": not dry_run, "read_only": dry_run},
            "diagnostics": [],
        }
        return resp

    def test_add_component_dry_run_on_root(self) -> None:
        text = self._prefab_with_child()
        server = create_server()
        mock_resp = self._mock_patch_apply_response(dry_run=True)

        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "test.prefab"
            p.write_text(text, encoding="utf-8")

            with patch(
                "prefab_sentinel.session_cache.Phase1Orchestrator"
            ) as mock_cls:
                mock_orch = MagicMock()
                mock_orch.patch_apply.return_value = mock_resp
                mock_cls.default.return_value = mock_orch

                _, result = _run(server.call_tool(
                    "add_component",
                    {
                        "asset_path": str(p),
                        "symbol_path": "Root",
                        "component_type": "AudioSource",
                    },
                ))

        self.assertTrue(result["success"])
        call_kwargs = mock_orch.patch_apply.call_args[1]
        plan = call_kwargs["plan"]
        op = plan["ops"][0]
        self.assertEqual("add_component", op["op"])
        self.assertEqual("/", op["target"])
        self.assertEqual("AudioSource", op["type"])
        self.assertTrue(call_kwargs["dry_run"])

    def test_add_component_on_child(self) -> None:
        text = self._prefab_with_child()
        server = create_server()
        mock_resp = self._mock_patch_apply_response(dry_run=True)

        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "test.prefab"
            p.write_text(text, encoding="utf-8")

            with patch(
                "prefab_sentinel.session_cache.Phase1Orchestrator"
            ) as mock_cls:
                mock_orch = MagicMock()
                mock_orch.patch_apply.return_value = mock_resp
                mock_cls.default.return_value = mock_orch

                _, result = _run(server.call_tool(
                    "add_component",
                    {
                        "asset_path": str(p),
                        "symbol_path": "Root/Child",
                        "component_type": "BoxCollider",
                    },
                ))

        self.assertTrue(result["success"])
        plan = mock_orch.patch_apply.call_args[1]["plan"]
        op = plan["ops"][0]
        self.assertEqual("/Child", op["target"])
        self.assertEqual("BoxCollider", op["type"])

    def test_add_component_symbol_not_found(self) -> None:
        text = self._prefab_with_child()
        server = create_server()

        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "test.prefab"
            p.write_text(text, encoding="utf-8")

            _, result = _run(server.call_tool(
                "add_component",
                {
                    "asset_path": str(p),
                    "symbol_path": "Nonexistent",
                    "component_type": "AudioSource",
                },
            ))

        self.assertFalse(result["success"])
        self.assertEqual("SYMBOL_NOT_FOUND", result["code"])
        self.assertIn("suggestions", result["data"])
        self.assertIsInstance(result["data"]["suggestions"], list)

    def test_add_component_rejects_component_path(self) -> None:
        """add_component requires a game_object, not a component."""
        text = self._prefab_with_child()
        server = create_server()

        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "test.prefab"
            p.write_text(text, encoding="utf-8")

            _, result = _run(server.call_tool(
                "add_component",
                {
                    "asset_path": str(p),
                    "symbol_path": "Root/Child/MeshRenderer",
                    "component_type": "AudioSource",
                },
            ))

        self.assertFalse(result["success"])
        self.assertEqual("SYMBOL_NOT_GAME_OBJECT", result["code"])

    def test_add_component_confirm_invalidates_cache(self) -> None:
        text = self._prefab_with_child()
        server = create_server()
        mock_resp = self._mock_patch_apply_response(dry_run=False)

        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "test.prefab"
            p.write_text(text, encoding="utf-8")

            with patch(
                "prefab_sentinel.session_cache.Phase1Orchestrator"
            ) as mock_cls:
                mock_orch = MagicMock()
                mock_orch.patch_apply.return_value = mock_resp
                mock_cls.default.return_value = mock_orch

                _, result = _run(server.call_tool(
                    "add_component",
                    {
                        "asset_path": str(p),
                        "symbol_path": "Root",
                        "component_type": "AudioSource",
                        "confirm": True,
                        "change_reason": "add audio source",
                    },
                ))

        self.assertTrue(result["success"])
        call_kwargs = mock_orch.patch_apply.call_args[1]
        self.assertFalse(call_kwargs["dry_run"])
        self.assertTrue(call_kwargs["confirm"])

    def test_add_component_symbol_resolution_metadata(self) -> None:
        text = self._prefab_with_child()
        server = create_server()
        mock_resp = self._mock_patch_apply_response(dry_run=True)

        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "test.prefab"
            p.write_text(text, encoding="utf-8")

            with patch(
                "prefab_sentinel.session_cache.Phase1Orchestrator"
            ) as mock_cls:
                mock_orch = MagicMock()
                mock_orch.patch_apply.return_value = mock_resp
                mock_cls.default.return_value = mock_orch

                _, result = _run(server.call_tool(
                    "add_component",
                    {
                        "asset_path": str(p),
                        "symbol_path": "Root/Child",
                        "component_type": "AudioSource",
                    },
                ))

        meta = result["symbol_resolution"]
        self.assertEqual("Root/Child", meta["symbol_path"])
        self.assertEqual("/Child", meta["hierarchy_target"])
        self.assertEqual("AudioSource", meta["component_type"])
        self.assertEqual("300", meta["file_id"])

    def test_confirm_requires_change_reason(self) -> None:
        server = create_server()

        _, result = _run(server.call_tool(
            "add_component",
            {
                "asset_path": "Assets/DoesNotExist.prefab",
                "symbol_path": "Root",
                "component_type": "AudioSource",
                "confirm": True,
                "change_reason": "",
            },
        ))

        self.assertFalse(result["success"])
        self.assertEqual("CHANGE_REASON_REQUIRED", result["code"])


class TestRemoveComponentTool(unittest.TestCase):
    """Test the remove_component MCP tool."""

    def _prefab_with_meshrenderer(self) -> str:
        return YAML_HEADER + "\n".join([
            make_gameobject("100", "Cube", ["200", "300"]),
            make_transform("200", "100"),
            make_meshrenderer("300", "100"),
        ])

    def _mock_patch_apply_response(self, dry_run: bool = True) -> MagicMock:
        resp = MagicMock()
        resp.success = True
        resp.to_dict.return_value = {
            "success": True,
            "severity": "info",
            "code": "PATCH_APPLY_RESULT",
            "message": "patch.apply dry-run completed." if dry_run else "patch.apply completed.",
            "data": {"dry_run": dry_run, "confirm": not dry_run, "read_only": dry_run},
            "diagnostics": [],
        }
        return resp

    def test_remove_component_dry_run(self) -> None:
        text = self._prefab_with_meshrenderer()
        server = create_server()
        mock_resp = self._mock_patch_apply_response(dry_run=True)

        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "test.prefab"
            p.write_text(text, encoding="utf-8")

            with patch(
                "prefab_sentinel.session_cache.Phase1Orchestrator"
            ) as mock_cls:
                mock_orch = MagicMock()
                mock_orch.patch_apply.return_value = mock_resp
                mock_cls.default.return_value = mock_orch

                _, result = _run(server.call_tool(
                    "remove_component",
                    {
                        "asset_path": str(p),
                        "symbol_path": "Cube/MeshRenderer",
                    },
                ))

        self.assertTrue(result["success"])
        call_kwargs = mock_orch.patch_apply.call_args[1]
        plan = call_kwargs["plan"]
        op = plan["ops"][0]
        self.assertEqual("remove_component", op["op"])
        self.assertEqual("MeshRenderer", op["component"])
        self.assertTrue(call_kwargs["dry_run"])

    def test_remove_component_confirm(self) -> None:
        text = self._prefab_with_meshrenderer()
        server = create_server()
        mock_resp = self._mock_patch_apply_response(dry_run=False)

        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "test.prefab"
            p.write_text(text, encoding="utf-8")

            with patch(
                "prefab_sentinel.session_cache.Phase1Orchestrator"
            ) as mock_cls:
                mock_orch = MagicMock()
                mock_orch.patch_apply.return_value = mock_resp
                mock_cls.default.return_value = mock_orch

                _, result = _run(server.call_tool(
                    "remove_component",
                    {
                        "asset_path": str(p),
                        "symbol_path": "Cube/MeshRenderer",
                        "confirm": True,
                        "change_reason": "remove mesh renderer",
                    },
                ))

        self.assertTrue(result["success"])
        call_kwargs = mock_orch.patch_apply.call_args[1]
        self.assertFalse(call_kwargs["dry_run"])
        self.assertTrue(call_kwargs["confirm"])

    def test_remove_component_symbol_not_found(self) -> None:
        text = self._prefab_with_meshrenderer()
        server = create_server()

        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "test.prefab"
            p.write_text(text, encoding="utf-8")

            _, result = _run(server.call_tool(
                "remove_component",
                {
                    "asset_path": str(p),
                    "symbol_path": "Cube/AudioSource",
                },
            ))

        self.assertFalse(result["success"])
        self.assertEqual("SYMBOL_NOT_FOUND", result["code"])
        self.assertIn("suggestions", result["data"])
        self.assertIsInstance(result["data"]["suggestions"], list)

    def test_remove_component_rejects_gameobject_path(self) -> None:
        """remove_component requires a component, not a game_object."""
        text = self._prefab_with_meshrenderer()
        server = create_server()

        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "test.prefab"
            p.write_text(text, encoding="utf-8")

            _, result = _run(server.call_tool(
                "remove_component",
                {
                    "asset_path": str(p),
                    "symbol_path": "Cube",
                },
            ))

        self.assertFalse(result["success"])
        self.assertEqual("SYMBOL_NOT_COMPONENT", result["code"])

    def test_remove_component_symbol_resolution_metadata(self) -> None:
        text = self._prefab_with_meshrenderer()
        server = create_server()
        mock_resp = self._mock_patch_apply_response(dry_run=True)

        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "test.prefab"
            p.write_text(text, encoding="utf-8")

            with patch(
                "prefab_sentinel.session_cache.Phase1Orchestrator"
            ) as mock_cls:
                mock_orch = MagicMock()
                mock_orch.patch_apply.return_value = mock_resp
                mock_cls.default.return_value = mock_orch

                _, result = _run(server.call_tool(
                    "remove_component",
                    {
                        "asset_path": str(p),
                        "symbol_path": "Cube/MeshRenderer",
                    },
                ))

        meta = result["symbol_resolution"]
        self.assertEqual("Cube/MeshRenderer", meta["symbol_path"])
        self.assertEqual("MeshRenderer", meta["resolved_component"])
        self.assertEqual("300", meta["file_id"])

    def test_confirm_requires_change_reason(self) -> None:
        server = create_server()

        _, result = _run(server.call_tool(
            "remove_component",
            {
                "asset_path": "Assets/DoesNotExist.prefab",
                "symbol_path": "Cube/MeshRenderer",
                "confirm": True,
                "change_reason": "",
            },
        ))

        self.assertFalse(result["success"])
        self.assertEqual("CHANGE_REASON_REQUIRED", result["code"])


class TestScopeFallback(unittest.TestCase):
    """MCP tools use session scope when explicit scope is omitted."""

    def test_validate_refs_passes_resolved_scope(self) -> None:
        server = create_server()
        mock_resp = MagicMock()
        mock_resp.to_dict.return_value = {"success": True, "data": {}}
        with (
            patch("prefab_sentinel.session_cache.Phase1Orchestrator") as mock_cls,
            patch.object(ProjectSession, "resolve_scope", return_value="Assets/Resolved"),
        ):
            mock_orch = mock_cls.default.return_value
            mock_orch.validate_refs.return_value = mock_resp
            _run(server.call_tool("validate_refs", {"scope": "Assets/Explicit"}))
            self.assertEqual(
                "Assets/Resolved",
                mock_orch.validate_refs.call_args.kwargs["scope"],
            )

    def test_find_referencing_assets_passes_resolved_scope(self) -> None:

        server = create_server()
        mock_step = ToolResponse(
            success=True,
            severity=Severity.INFO,
            code="REF_WHERE_USED",
            message="ok",
            data={"usages": [], "usage_count": 0, "returned_usages": 0, "truncated_usages": 0},
            diagnostics=[],
        )
        with (
            patch("prefab_sentinel.session_cache.Phase1Orchestrator") as mock_cls,
            patch.object(ProjectSession, "resolve_scope", return_value="Assets/Fallback"),
        ):
            mock_orch = mock_cls.default.return_value
            mock_orch.reference_resolver.where_used.return_value = mock_step
            _run(server.call_tool(
                "find_referencing_assets",
                {"asset_or_guid": "abcd1234abcd1234abcd1234abcd1234"},
            ))
            self.assertEqual(
                "Assets/Fallback",
                mock_orch.reference_resolver.where_used.call_args.kwargs["scope"],
            )

    def test_validate_field_rename_passes_resolved_scope(self) -> None:
        server = create_server()
        mock_resp = MagicMock()
        mock_resp.to_dict.return_value = {"success": True, "data": {}}
        with (
            patch("prefab_sentinel.session_cache.Phase1Orchestrator") as mock_cls,
            patch.object(ProjectSession, "resolve_scope", return_value="Assets/Resolved"),
        ):
            mock_orch = mock_cls.default.return_value
            mock_orch.validate_field_rename.return_value = mock_resp
            _run(server.call_tool("validate_field_rename", {
                "script_or_guid": "aabb",
                "old_name": "speed",
                "new_name": "velocity",
            }))
            self.assertEqual(
                "Assets/Resolved",
                mock_orch.validate_field_rename.call_args.kwargs["scope"],
            )

    def test_check_field_coverage_passes_resolved_scope(self) -> None:
        server = create_server()
        mock_resp = MagicMock()
        mock_resp.to_dict.return_value = {"success": True, "data": {}}
        with (
            patch("prefab_sentinel.session_cache.Phase1Orchestrator") as mock_cls,
            patch.object(ProjectSession, "resolve_scope", return_value="Assets/Resolved"),
        ):
            mock_orch = mock_cls.default.return_value
            mock_orch.check_field_coverage.return_value = mock_resp
            _run(server.call_tool("check_field_coverage", {"scope": "Assets/Explicit"}))
            self.assertEqual(
                "Assets/Resolved",
                mock_orch.check_field_coverage.call_args.kwargs["scope"],
            )


class TestFindReferencingAssetsDirectPayload(unittest.TestCase):
    """find_referencing_assets returns direct payload, not envelope."""

    def test_returns_matches_array(self) -> None:
        server = create_server()
        mock_step = ToolResponse(
            success=True,
            severity=Severity.INFO,
            code="REF_WHERE_USED",
            message="Found 2 usages",
            data={
                "usages": [
                    {"file": "A.prefab", "line": 10},
                    {"file": "B.prefab", "line": 20},
                ],
                "usage_count": 2,
                "returned_usages": 2,
                "truncated_usages": 0,
                "scanned_files": 5,
            },
            diagnostics=[],
        )
        with patch("prefab_sentinel.session_cache.Phase1Orchestrator") as mock_cls:
            mock_orch = mock_cls.default.return_value
            mock_orch.reference_resolver.where_used.return_value = mock_step
            _, result = _run(server.call_tool(
                "find_referencing_assets",
                {"asset_or_guid": "abcd1234abcd1234abcd1234abcd1234"},
            ))

        # Direct payload — no envelope
        self.assertIn("matches", result)
        self.assertEqual(2, len(result["matches"]))
        self.assertEqual("abcd1234abcd1234abcd1234abcd1234", result["target"])
        self.assertFalse(result["metadata"]["truncated"])
        self.assertEqual(2, result["metadata"]["total_count"])
        # No envelope keys
        self.assertNotIn("success", result)
        self.assertNotIn("severity", result)

    def test_truncated_metadata(self) -> None:
        server = create_server()
        mock_step = ToolResponse(
            success=True,
            severity=Severity.WARNING,
            code="REF_WHERE_USED",
            message="Truncated",
            data={
                "usages": [{"file": "A.prefab"}],
                "usage_count": 50,
                "returned_usages": 1,
                "truncated_usages": 49,
                "scanned_files": 100,
            },
            diagnostics=[],
        )
        with patch("prefab_sentinel.session_cache.Phase1Orchestrator") as mock_cls:
            mock_orch = mock_cls.default.return_value
            mock_orch.reference_resolver.where_used.return_value = mock_step
            _, result = _run(server.call_tool(
                "find_referencing_assets",
                {"asset_or_guid": "x" * 32, "max_results": 1},
            ))

        self.assertTrue(result["metadata"]["truncated"])
        self.assertEqual(50, result["metadata"]["total_count"])

    def test_error_raises_tool_error(self) -> None:
        from mcp.server.fastmcp.exceptions import ToolError



        server = create_server()
        mock_step = ToolResponse(
            success=False,
            severity=Severity.ERROR,
            code="REF_ERR",
            message="Scope not found",
            data={},
            diagnostics=[],
        )
        with patch("prefab_sentinel.session_cache.Phase1Orchestrator") as mock_cls:
            mock_orch = mock_cls.default.return_value
            mock_orch.reference_resolver.where_used.return_value = mock_step
            with self.assertRaises(ToolError) as ctx:
                _run(server.call_tool(
                    "find_referencing_assets",
                    {"asset_or_guid": "x" * 32},
                ))
            self.assertIn("Scope not found", str(ctx.exception))


class TestEditorReadOnlyTools(unittest.TestCase):
    """Test read-only editor bridge MCP tools."""

    def test_editor_screenshot_delegates(self) -> None:
        server = create_server()
        mock_response = {"success": True, "data": {"output_path": "/tmp/shot.png"}}
        with patch("prefab_sentinel.mcp_tools_editor_view.send_action", return_value=mock_response) as mock_send:
            _, result = _run(server.call_tool("editor_screenshot", {"view": "game", "width": 1920}))
        self.assertEqual(mock_response, result)
        # Default refresh=True: refresh + capture = 2 calls
        self.assertEqual(mock_send.call_count, 2)

    def test_editor_screenshot_defaults(self) -> None:
        server = create_server()
        with patch("prefab_sentinel.mcp_tools_editor_view.send_action", return_value={"success": True}) as mock_send:
            _run(server.call_tool("editor_screenshot", {}))
        # Default refresh=True means 2 calls: refresh + capture
        self.assertEqual(mock_send.call_count, 2)
        mock_send.assert_any_call(action="capture_screenshot", view="scene", width=0, height=0)

    def test_editor_screenshot_refresh_true_calls_refresh_then_capture(self) -> None:
        server = create_server()
        with patch("prefab_sentinel.mcp_tools_editor_view.send_action", return_value={"success": True}) as mock_send:
            _run(server.call_tool("editor_screenshot", {"refresh": True}))
        self.assertEqual(mock_send.call_count, 2)
        calls = mock_send.call_args_list
        self.assertEqual(calls[0], call(action="refresh_asset_database"))
        self.assertEqual(calls[1], call(action="capture_screenshot", view="scene", width=0, height=0))

    def test_editor_screenshot_refresh_false_skips_refresh(self) -> None:
        server = create_server()
        with patch("prefab_sentinel.mcp_tools_editor_view.send_action", return_value={"success": True}) as mock_send:
            _run(server.call_tool("editor_screenshot", {"refresh": False}))
        mock_send.assert_called_once_with(action="capture_screenshot", view="scene", width=0, height=0)

    def test_editor_screenshot_refresh_failure_still_captures(self) -> None:
        server = create_server()
        responses = [Exception("refresh failed"), {"success": True, "data": {"output_path": "/shot.png"}}]
        with patch("prefab_sentinel.mcp_tools_editor_view.send_action", side_effect=responses) as mock_send:
            _, result = _run(server.call_tool("editor_screenshot", {"refresh": True}))
        self.assertEqual(mock_send.call_count, 2)
        self.assertTrue(result["success"])

    def test_editor_select_delegates(self) -> None:
        server = create_server()
        with patch("prefab_sentinel.mcp_tools_editor_view.send_action", return_value={"success": True}) as mock_send:
            _, result = _run(server.call_tool("editor_select", {
                "hierarchy_path": "/Canvas/Panel",
                "prefab_asset_path": "Assets/UI.prefab",
            }))
        mock_send.assert_called_once_with(
            action="select_object", hierarchy_path="/Canvas/Panel", prefab_asset_path="Assets/UI.prefab",
        )
        self.assertTrue(result["success"])

    def test_editor_select_omits_empty_prefab_asset_path(self) -> None:
        server = create_server()
        with patch("prefab_sentinel.mcp_tools_editor_view.send_action", return_value={"success": True}) as mock_send:
            _run(server.call_tool("editor_select", {"hierarchy_path": "/Root/Child"}))
        _, kwargs = mock_send.call_args
        self.assertNotIn("prefab_asset_path", kwargs)

    def test_editor_frame_delegates(self) -> None:
        server = create_server()
        with patch("prefab_sentinel.mcp_tools_editor_view.send_action", return_value={"success": True}) as mock_send:
            _run(server.call_tool("editor_frame", {"zoom": 2.5}))
        mock_send.assert_called_once_with(action="frame_selected", zoom=2.5)

    def test_editor_frame_defaults(self) -> None:
        server = create_server()
        with patch("prefab_sentinel.mcp_tools_editor_view.send_action", return_value={"success": True}) as mock_send:
            _run(server.call_tool("editor_frame", {}))
        mock_send.assert_called_once_with(action="frame_selected", zoom=0.0)

    def test_editor_get_camera_delegates(self) -> None:
        server = create_server()
        with patch("prefab_sentinel.mcp_tools_editor_view.send_action", return_value={"success": True}) as mock_send:
            _run(server.call_tool("editor_get_camera", {}))
        mock_send.assert_called_once_with(action="get_camera")

    def test_editor_set_camera_mode_b(self) -> None:
        server = create_server()
        with patch("prefab_sentinel.mcp_tools_editor_view.send_action", return_value={"success": True}) as mock_send:
            _run(server.call_tool("editor_set_camera", {"yaw": 45.0, "pitch": 15.0, "distance": 3.0}))
        mock_send.assert_called_once_with(action="set_camera", yaw=45.0, pitch=15.0, distance=3.0)

    def test_editor_set_camera_defaults(self) -> None:
        server = create_server()
        with patch("prefab_sentinel.mcp_tools_editor_view.send_action", return_value={"success": True}) as mock_send:
            _run(server.call_tool("editor_set_camera", {}))
        mock_send.assert_called_once_with(action="set_camera")

    def test_editor_list_children_delegates(self) -> None:
        server = create_server()
        mock_response = {"success": True, "data": {"children": ["A", "B"]}}
        with patch("prefab_sentinel.mcp_tools_editor_view.send_action", return_value=mock_response):
            _, result = _run(server.call_tool("editor_list_children", {
                "hierarchy_path": "/Root", "depth": 2,
            }))
        self.assertEqual(mock_response, result)

    def test_editor_list_children_default_depth(self) -> None:
        server = create_server()
        with patch("prefab_sentinel.mcp_tools_editor_view.send_action", return_value={"success": True}) as mock_send:
            _run(server.call_tool("editor_list_children", {"hierarchy_path": "/Root"}))
        mock_send.assert_called_once_with(action="list_children", hierarchy_path="/Root", depth=1)

    def test_editor_list_materials_delegates(self) -> None:
        server = create_server()
        with patch("prefab_sentinel.mcp_tools_editor_view.send_action", return_value={"success": True}) as mock_send:
            _run(server.call_tool("editor_list_materials", {"hierarchy_path": "/Body"}))
        mock_send.assert_called_once_with(action="list_materials", hierarchy_path="/Body")

    def test_editor_list_roots_delegates(self) -> None:
        server = create_server()
        with patch("prefab_sentinel.mcp_tools_editor_view.send_action", return_value={"success": True}) as mock_send:
            _run(server.call_tool("editor_list_roots", {}))
        mock_send.assert_called_once_with(action="list_roots")

    def test_editor_get_material_property_delegates(self) -> None:
        server = create_server()
        with patch("prefab_sentinel.mcp_tools_editor_view.send_action", return_value={"success": True}) as mock_send:
            _run(server.call_tool("editor_get_material_property", {
                "hierarchy_path": "/Body", "material_index": 0, "property_name": "_Color",
            }))
        mock_send.assert_called_once_with(
            action="get_material_property",
            hierarchy_path="/Body", material_index=0, property_name="_Color",
        )

    def test_editor_get_material_property_default_property_name(self) -> None:
        server = create_server()
        with patch("prefab_sentinel.mcp_tools_editor_view.send_action", return_value={"success": True}) as mock_send:
            _run(server.call_tool("editor_get_material_property", {
                "hierarchy_path": "/Body", "material_index": 0,
            }))
        mock_send.assert_called_once_with(
            action="get_material_property",
            hierarchy_path="/Body", material_index=0, property_name="",
        )

    def test_editor_console_delegates(self) -> None:
        server = create_server()
        with patch("prefab_sentinel.mcp_tools_editor_view.send_action", return_value={"success": True}) as mock_send:
            _run(server.call_tool("editor_console", {
                "max_entries": 50, "log_type_filter": "error", "since_seconds": 10.0,
            }))
        mock_send.assert_called_once_with(
            action="capture_console_logs",
            max_entries=50, log_type_filter="error", since_seconds=10.0,
        )

    def test_editor_console_defaults(self) -> None:
        server = create_server()
        with patch("prefab_sentinel.mcp_tools_editor_view.send_action", return_value={"success": True}) as mock_send:
            _run(server.call_tool("editor_console", {}))
        mock_send.assert_called_once_with(
            action="capture_console_logs",
            max_entries=200, log_type_filter="all", since_seconds=0.0,
        )


class TestEditorSideEffectTools(unittest.TestCase):
    """Test side-effect editor bridge MCP tools."""

    def test_editor_refresh_delegates(self) -> None:
        server = create_server()
        with patch("prefab_sentinel.mcp_tools_editor_view.send_action", return_value={"success": True}) as mock_send:
            _, result = _run(server.call_tool("editor_refresh", {}))
        mock_send.assert_called_once_with(action="refresh_asset_database")
        self.assertTrue(result["success"])

    def test_editor_recompile_delegates(self) -> None:
        server = create_server()
        with patch("prefab_sentinel.mcp_tools_editor_view.send_action", return_value={"success": True}) as mock_send:
            _run(server.call_tool("editor_recompile", {}))
        mock_send.assert_called_once_with(action="recompile_scripts")

    def test_editor_run_tests_delegates(self) -> None:
        server = create_server()
        with patch("prefab_sentinel.mcp_tools_editor_view.send_action", return_value={"success": True}) as mock_send:
            _run(server.call_tool("editor_run_tests", {}))
        mock_send.assert_called_once_with(action="run_integration_tests", timeout_sec=300)

    def test_editor_run_tests_custom_timeout(self) -> None:
        server = create_server()
        with patch("prefab_sentinel.mcp_tools_editor_view.send_action", return_value={"success": True}) as mock_send:
            _run(server.call_tool("editor_run_tests", {"timeout_sec": 600}))
        mock_send.assert_called_once_with(action="run_integration_tests", timeout_sec=600)


class TestEditorWriteTools(unittest.TestCase):
    """Test write/mutation editor bridge MCP tools."""

    def test_editor_instantiate_delegates(self) -> None:
        server = create_server()
        with patch("prefab_sentinel.mcp_tools_editor_write.send_action", return_value={"success": True}) as mock_send:
            _run(server.call_tool("editor_instantiate", {
                "asset_path": "Assets/Prefabs/Mic.prefab",
                "hierarchy_path": "/Canvas",
                "position": "0,1.5,0",
            }))
        mock_send.assert_called_once_with(
            action="instantiate_to_scene",
            asset_path="Assets/Prefabs/Mic.prefab",
            hierarchy_path="/Canvas",
            position=[0.0, 1.5, 0.0],
        )

    def test_editor_instantiate_no_position(self) -> None:
        server = create_server()
        with patch("prefab_sentinel.mcp_tools_editor_write.send_action", return_value={"success": True}) as mock_send:
            _run(server.call_tool("editor_instantiate", {
                "asset_path": "Assets/Prefabs/Mic.prefab",
            }))
        mock_send.assert_called_once_with(
            action="instantiate_to_scene",
            asset_path="Assets/Prefabs/Mic.prefab",
            hierarchy_path="",
        )

    def test_editor_instantiate_invalid_position_count(self) -> None:
        server = create_server()
        with patch("prefab_sentinel.mcp_tools_editor_write.send_action"):
            _, result = _run(server.call_tool("editor_instantiate", {
                "asset_path": "Assets/X.prefab",
                "position": "1,2",
            }))
        self.assertFalse(result["success"])
        self.assertEqual("INVALID_POSITION", result["code"])

    def test_editor_instantiate_invalid_position_value(self) -> None:
        server = create_server()
        with patch("prefab_sentinel.mcp_tools_editor_write.send_action"):
            _, result = _run(server.call_tool("editor_instantiate", {
                "asset_path": "Assets/X.prefab",
                "position": "a,b,c",
            }))
        self.assertFalse(result["success"])
        self.assertEqual("INVALID_POSITION", result["code"])

    def test_editor_set_material_delegates(self) -> None:
        server = create_server()
        with patch("prefab_sentinel.mcp_tools_editor_write.send_action", return_value={"success": True}) as mock_send:
            _run(server.call_tool("editor_set_material", {
                "hierarchy_path": "/Body",
                "material_index": 0,
                "material_guid": "abc123def456",
            }))
        mock_send.assert_called_once_with(
            action="set_material",
            hierarchy_path="/Body", material_index=0, material_guid="abc123def456",
        )

    def test_editor_set_material_property_delegates(self) -> None:
        server = create_server()
        with patch("prefab_sentinel.mcp_tools_editor_view.send_action", return_value={"success": True}) as mock_send:
            _run(server.call_tool("editor_set_material_property", {
                "hierarchy_path": "/Foo",
                "material_index": 0,
                "property_name": "_Color",
                "value": "[1, 0, 0, 1]",
            }))
        mock_send.assert_called_once_with(
            action="set_material_property",
            hierarchy_path="/Foo",
            material_index=0,
            property_name="_Color",
            property_value="[1, 0, 0, 1]",
        )

    def test_editor_batch_set_material_property_delegates(self) -> None:
        server = create_server()
        with patch("prefab_sentinel.mcp_tools_editor_batch.send_action", return_value={"success": True}) as mock_send:
            _run(server.call_tool("editor_batch_set_material_property", {
                "hierarchy_path": "/Avatar/Hair",
                "material_index": 0,
                "properties": [
                    {"name": "_Color", "value": "[1, 0, 0, 1]"},
                    {"name": "_MainTexHSVG", "value": [0.02, 0.48, 1.18, 1]},
                ],
            }))
        args = mock_send.call_args
        self.assertEqual(args.kwargs["action"], "editor_batch_set_material_property")
        self.assertEqual(args.kwargs["hierarchy_path"], "/Avatar/Hair")
        self.assertEqual(args.kwargs["material_index"], 0)
        ops = json.loads(args.kwargs["batch_operations_json"])
        self.assertEqual(len(ops), 2)
        self.assertEqual(ops[0]["name"], "_Color")
        self.assertEqual(ops[0]["value"], "[1, 0, 0, 1]")
        # list value should be JSON-stringified
        self.assertEqual(ops[1]["value"], "[0.02, 0.48, 1.18, 1]")

    def test_editor_batch_set_material_property_by_path_delegates(self) -> None:
        server = create_server()
        with patch("prefab_sentinel.mcp_tools_editor_batch.send_action", return_value={"success": True}) as mock_send:
            _run(server.call_tool("editor_batch_set_material_property", {
                "material_path": "Assets/Materials/Hair.mat",
                "properties": [
                    {"name": "_Color", "value": "[1, 1, 1, 1]"},
                ],
            }))
        args = mock_send.call_args
        self.assertEqual(args.kwargs["action"], "editor_batch_set_material_property")
        self.assertEqual(args.kwargs["material_path"], "Assets/Materials/Hair.mat")
        self.assertNotIn("hierarchy_path", args.kwargs)
        self.assertNotIn("material_index", args.kwargs)

    def test_editor_batch_set_material_property_by_guid_delegates(self) -> None:
        server = create_server()
        with patch("prefab_sentinel.mcp_tools_editor_batch.send_action", return_value={"success": True}) as mock_send:
            _run(server.call_tool("editor_batch_set_material_property", {
                "material_guid": "abc123def456abc123def456abc123de",
                "properties": [
                    {"name": "_Float", "value": 0.5},
                ],
            }))
        args = mock_send.call_args
        self.assertEqual(args.kwargs["action"], "editor_batch_set_material_property")
        self.assertEqual(args.kwargs["material_guid"], "abc123def456abc123def456abc123de")
        self.assertNotIn("hierarchy_path", args.kwargs)
        ops = json.loads(args.kwargs["batch_operations_json"])
        self.assertEqual(ops[0]["value"], "0.5")

    def test_editor_delete_delegates(self) -> None:
        server = create_server()
        with patch("prefab_sentinel.mcp_tools_editor_write.send_action", return_value={"success": True}) as mock_send:
            _run(server.call_tool("editor_delete", {"hierarchy_path": "/OldObject"}))
        mock_send.assert_called_once_with(action="delete_object", hierarchy_path="/OldObject")

    def test_editor_remove_component_delegates(self) -> None:
        server = create_server()
        with patch("prefab_sentinel.mcp_tools_editor_write.send_action", return_value={"success": True}) as mock_send:
            _run(server.call_tool("editor_remove_component", {
                "hierarchy_path": "/Player",
                "component_type": "BoxCollider",
            }))
        mock_send.assert_called_once_with(
            action="editor_remove_component",
            hierarchy_path="/Player",
            component_type="BoxCollider",
        )

    def test_editor_remove_component_with_index(self) -> None:
        server = create_server()
        with patch("prefab_sentinel.mcp_tools_editor_write.send_action", return_value={"success": True}) as mock_send:
            _run(server.call_tool("editor_remove_component", {
                "hierarchy_path": "/Player",
                "component_type": "BoxCollider",
                "index": 1,
            }))
        mock_send.assert_called_once_with(
            action="editor_remove_component",
            hierarchy_path="/Player",
            component_type="BoxCollider",
            component_index=1,
        )

    def test_vrcsdk_upload_delegates(self) -> None:
        """Default platforms=["windows"] is serialized and passed to send_action."""
        server = create_server()
        with patch("prefab_sentinel.mcp_tools_editor_advanced.send_action", return_value={"success": True, "data": {}}) as mock_send:
            _run(server.call_tool("vrcsdk_upload", {
                "target_type": "avatar",
                "asset_path": "Assets/Avatars/Test.prefab",
                "blueprint_id": "avtr_test123",
                "confirm": False,
            }))
        mock_send.assert_called_once_with(
            action="vrcsdk_upload",
            timeout_sec=600,
            target_type="avatar",
            asset_path="Assets/Avatars/Test.prefab",
            blueprint_id="avtr_test123",
            platforms='["windows"]',
            description="",
            tags="",
            release_status="",
            confirm=False,
        )

    def test_vrcsdk_upload_requires_change_reason(self) -> None:
        """confirm=True without change_reason returns error without calling bridge."""
        server = create_server()
        with patch("prefab_sentinel.mcp_tools_editor_advanced.send_action") as mock_send:
            _, result = _run(server.call_tool("vrcsdk_upload", {
                "target_type": "avatar",
                "asset_path": "Assets/Avatars/Test.prefab",
                "blueprint_id": "avtr_test123",
                "confirm": True,
                "change_reason": "",
            }))
            mock_send.assert_not_called()
        self.assertFalse(result["success"])
        self.assertEqual("CHANGE_REASON_REQUIRED", result["code"])

    def test_vrcsdk_upload_invalid_platforms_empty(self) -> None:
        """Empty platforms list returns validation error."""
        server = create_server()
        with patch("prefab_sentinel.mcp_tools_editor_advanced.send_action") as mock_send:
            _, result = _run(server.call_tool("vrcsdk_upload", {
                "target_type": "avatar",
                "asset_path": "Assets/Avatars/Test.prefab",
                "blueprint_id": "avtr_test123",
                "platforms": [],
            }))
            mock_send.assert_not_called()
        self.assertFalse(result["success"])
        self.assertEqual("VRCSDK_INVALID_PLATFORMS", result["code"])

    def test_vrcsdk_upload_invalid_platforms_bad_value(self) -> None:
        """Invalid platform name returns validation error."""
        server = create_server()
        with patch("prefab_sentinel.mcp_tools_editor_advanced.send_action") as mock_send:
            _, result = _run(server.call_tool("vrcsdk_upload", {
                "target_type": "avatar",
                "asset_path": "Assets/Avatars/Test.prefab",
                "blueprint_id": "avtr_test123",
                "platforms": ["windows", "ps5"],
            }))
            mock_send.assert_not_called()
        self.assertFalse(result["success"])
        self.assertEqual("VRCSDK_INVALID_PLATFORMS", result["code"])

    def test_vrcsdk_upload_invalid_platforms_duplicate(self) -> None:
        """Duplicate platform returns validation error."""
        server = create_server()
        with patch("prefab_sentinel.mcp_tools_editor_advanced.send_action") as mock_send:
            _, result = _run(server.call_tool("vrcsdk_upload", {
                "target_type": "avatar",
                "asset_path": "Assets/Avatars/Test.prefab",
                "blueprint_id": "avtr_test123",
                "platforms": ["windows", "windows"],
            }))
            mock_send.assert_not_called()
        self.assertFalse(result["success"])
        self.assertEqual("VRCSDK_INVALID_PLATFORMS", result["code"])

    def test_vrcsdk_upload_converts_platform_results(self) -> None:
        """platform_results_json from C# is converted to platform_results list."""
        server = create_server()
        bridge_response = {
            "success": True,
            "data": {
                "phase": "complete",
                "platform_results_json": '[{"platform":"windows","success":true,"elapsed_sec":45.1}]',
                "original_target_restored": True,
            },
        }
        with patch("prefab_sentinel.mcp_tools_editor_advanced.send_action", return_value=bridge_response):
            _, result = _run(server.call_tool("vrcsdk_upload", {
                "target_type": "avatar",
                "asset_path": "Assets/Avatars/Test.prefab",
                "blueprint_id": "avtr_test123",
                "confirm": True,
                "change_reason": "test upload",
            }))
        self.assertIn("platform_results", result["data"])
        self.assertEqual(result["data"]["platform_results"][0]["platform"], "windows")
        self.assertNotIn("platform_results_json", result["data"])

    def test_vrcsdk_upload_converts_mixed_platform_results(self) -> None:
        """platform_results_json with success + failure + skipped is correctly parsed."""
        server = create_server()
        bridge_response = {
            "success": False,
            "data": {
                "phase": "failed",
                "platform_results_json": '[{"platform":"windows","success":true,"elapsed_sec":45.1},{"platform":"android","success":false,"elapsed_sec":9.9,"error":"Shader error"},{"platform":"ios","skipped":true}]',
                "original_target_restored": True,
            },
        }
        with patch("prefab_sentinel.mcp_tools_editor_advanced.send_action", return_value=bridge_response):
            _, result = _run(server.call_tool("vrcsdk_upload", {
                "target_type": "avatar",
                "asset_path": "Assets/Avatars/Test.prefab",
                "blueprint_id": "avtr_test123",
                "platforms": ["windows", "android", "ios"],
                "confirm": True,
                "change_reason": "test upload",
            }))
        pr = result["data"]["platform_results"]
        self.assertEqual(len(pr), 3)
        self.assertTrue(pr[0]["success"])
        self.assertFalse(pr[1]["success"])
        self.assertTrue(pr[2]["skipped"])
        self.assertNotIn("platform_results_json", result["data"])

    def test_vrcsdk_upload_dryrun_includes_platforms(self) -> None:
        """dry-run response includes platforms echo-back from Python."""
        server = create_server()
        bridge_response = {"success": True, "data": {"phase": "validated"}}
        with patch("prefab_sentinel.mcp_tools_editor_advanced.send_action", return_value=bridge_response):
            _, result = _run(server.call_tool("vrcsdk_upload", {
                "target_type": "avatar",
                "asset_path": "Assets/Avatars/Test.prefab",
                "blueprint_id": "avtr_test123",
                "platforms": ["windows", "android"],
            }))
        self.assertEqual(result["data"]["platforms"], ["windows", "android"])

    def test_editor_get_blend_shapes_delegates(self) -> None:
        server = create_server()
        with patch("prefab_sentinel.mcp_tools_editor_write.send_action", return_value={"success": True}) as mock_send:
            _run(server.call_tool("editor_get_blend_shapes", {
                "hierarchy_path": "/Avatar/Body", "filter": "vrc.v_",
            }))
        mock_send.assert_called_once_with(
            action="get_blend_shapes",
            hierarchy_path="/Avatar/Body", filter="vrc.v_",
        )

    def test_editor_get_blend_shapes_default_filter(self) -> None:
        server = create_server()
        with patch("prefab_sentinel.mcp_tools_editor_write.send_action", return_value={"success": True}) as mock_send:
            _run(server.call_tool("editor_get_blend_shapes", {
                "hierarchy_path": "/Avatar/Body",
            }))
        mock_send.assert_called_once_with(
            action="get_blend_shapes",
            hierarchy_path="/Avatar/Body", filter="",
        )

    def test_editor_set_blend_shape_delegates(self) -> None:
        server = create_server()
        with patch("prefab_sentinel.mcp_tools_editor_write.send_action", return_value={"success": True}) as mock_send:
            _run(server.call_tool("editor_set_blend_shape", {
                "hierarchy_path": "/Avatar/Body", "name": "vrc.blink", "weight": 75.0,
            }))
        mock_send.assert_called_once_with(
            action="set_blend_shape",
            hierarchy_path="/Avatar/Body",
            blend_shape_name="vrc.blink",
            blend_shape_weight=75.0,
        )

    def test_editor_list_menu_items_delegates(self) -> None:
        server = create_server()
        with patch("prefab_sentinel.mcp_tools_editor_write.send_action", return_value={"success": True}) as mock_send:
            _run(server.call_tool("editor_list_menu_items", {"prefix": "Tools/"}))
        mock_send.assert_called_once_with(action="list_menu_items", filter="Tools/")

    def test_editor_list_menu_items_default_prefix(self) -> None:
        server = create_server()
        with patch("prefab_sentinel.mcp_tools_editor_write.send_action", return_value={"success": True}) as mock_send:
            _run(server.call_tool("editor_list_menu_items", {}))
        mock_send.assert_called_once_with(action="list_menu_items", filter="")

    def test_editor_execute_menu_item_delegates(self) -> None:
        server = create_server()
        with patch("prefab_sentinel.mcp_tools_editor_write.send_action", return_value={"success": True}) as mock_send:
            _run(server.call_tool("editor_execute_menu_item", {
                "menu_path": "Tools/NDMF/Manual Bake",
            }))
        mock_send.assert_called_once_with(
            action="execute_menu_item", menu_path="Tools/NDMF/Manual Bake",
        )

    def test_editor_batch_add_component_does_not_mutate_input(self) -> None:
        """editor_batch_add_component must not mutate caller-supplied operation dicts."""
        operations = [{"hierarchy_path": "/Obj", "component_type": "C", "properties": [{"name": "speed", "value": "10"}]}]
        server = create_server()
        with patch("prefab_sentinel.mcp_tools_editor_batch.send_action", return_value={"success": True}) as mock_send:
            _run(server.call_tool("editor_batch_add_component", {"operations": operations}))
        self.assertIn("properties", operations[0])
        call_kwargs = mock_send.call_args[1]
        sent = json.loads(call_kwargs["batch_operations_json"])
        self.assertNotIn("properties", sent[0])
        self.assertIn("properties_json", sent[0])
        self.assertEqual(json.loads(sent[0]["properties_json"]), [{"name": "speed", "value": "10"}])


class TestEditorExecTools(unittest.TestCase):
    """Test editor bridge execution MCP tools (``mcp_tools_editor_exec``)."""

    def test_editor_run_script_delegates_when_confirm_and_reason(self) -> None:
        """T-92-A: ``confirm=True`` + non-empty ``change_reason`` reaches
        ``send_action`` exactly once with the ``run_script`` action."""
        server = create_server()
        with patch(
            "prefab_sentinel.mcp_tools_editor_exec.send_action",
            return_value={"success": True, "code": "EDITOR_CTRL_RUN_SCRIPT_OK"},
        ) as mock_send:
            _, parsed = _run(server.call_tool("editor_run_script", {
                "code": "public static void Run() {}",
                "confirm": True,
                "change_reason": "smoke test",
            }))
        mock_send.assert_called_once_with(
            action="run_script",
            code="public static void Run() {}",
            change_reason="smoke test",
        )
        self.assertTrue(parsed["success"])

    def test_editor_run_script_rejects_when_confirm_false(self) -> None:
        """T-92-B: ``confirm=False`` short-circuits to
        ``CHANGE_REASON_REQUIRED`` without contacting the bridge."""
        server = create_server()
        with patch(
            "prefab_sentinel.mcp_tools_editor_exec.send_action"
        ) as mock_send:
            _, parsed = _run(server.call_tool("editor_run_script", {
                "code": "public static void Run() {}",
                "confirm": False,
                "change_reason": "smoke test",
            }))
        mock_send.assert_not_called()
        self.assertFalse(parsed["success"])
        self.assertEqual("CHANGE_REASON_REQUIRED", parsed["code"])

    def test_editor_run_script_rejects_whitespace_only_reason(self) -> None:
        """T-92-C: a whitespace-only ``change_reason`` is rejected with
        ``CHANGE_REASON_REQUIRED`` and the bridge is never invoked."""
        server = create_server()
        with patch(
            "prefab_sentinel.mcp_tools_editor_exec.send_action"
        ) as mock_send:
            _, parsed = _run(server.call_tool("editor_run_script", {
                "code": "public static void Run() {}",
                "confirm": True,
                "change_reason": "   ",
            }))
        mock_send.assert_not_called()
        self.assertFalse(parsed["success"])
        self.assertEqual("CHANGE_REASON_REQUIRED", parsed["code"])


class TestInspectionTools(unittest.TestCase):
    """Test inspect_materials and validate_structure MCP tools."""

    def test_inspect_materials_delegates(self) -> None:
        mock_resp = MagicMock()
        mock_resp.to_dict.return_value = {
            "success": True, "data": {"renderers": []},
        }
        mock_orch = MagicMock()
        mock_orch.inspect_materials.return_value = mock_resp

        server = create_server()
        with patch("prefab_sentinel.session_cache.Phase1Orchestrator") as mock_cls:
            mock_cls.default.return_value = mock_orch
            _, result = _run(server.call_tool("inspect_materials", {
                "asset_path": "Assets/Avatar.prefab",
            }))

        self.assertTrue(result["success"])
        mock_orch.inspect_materials.assert_called_once_with(
            target_path="Assets/Avatar.prefab",
        )

    def test_validate_structure_delegates(self) -> None:
        mock_resp = MagicMock()
        mock_resp.to_dict.return_value = {
            "success": True, "data": {"issues": []},
        }
        mock_orch = MagicMock()
        mock_orch.inspect_structure.return_value = mock_resp

        server = create_server()
        with patch("prefab_sentinel.session_cache.Phase1Orchestrator") as mock_cls:
            mock_cls.default.return_value = mock_orch
            _, result = _run(server.call_tool("validate_structure", {
                "asset_path": "Assets/Scene.unity",
            }))

        self.assertTrue(result["success"])
        mock_orch.inspect_structure.assert_called_once_with(
            target_path="Assets/Scene.unity",
        )


class TestRevertOverridesTool(unittest.TestCase):
    """Test revert_overrides MCP tool."""

    def test_dry_run_default(self) -> None:
        mock_resp = MagicMock()
        mock_resp.to_dict.return_value = {
            "success": True, "code": "REVERT_DRY_RUN",
            "data": {"match_count": 1, "read_only": True},
        }
        server = create_server()
        with patch(
            "prefab_sentinel.mcp_tools_patch.revert_overrides_impl",
            return_value=mock_resp,
        ) as mock_revert:
            _, result = _run(server.call_tool("revert_overrides", {
                "asset_path": "Assets/V.prefab",
                "target_file_id": "12345",
                "property_path": "m_Color.r",
            }))

        mock_revert.assert_called_once_with(
            variant_path="Assets/V.prefab",
            target_file_id="12345",
            property_path="m_Color.r",
            dry_run=True,
            confirm=False,
            change_reason=None,
        )
        self.assertTrue(result["success"])

    def test_confirm_mode(self) -> None:
        mock_resp = MagicMock()
        mock_resp.to_dict.return_value = {
            "success": True, "code": "REVERT_APPLIED",
            "data": {"match_count": 1, "read_only": False},
        }
        server = create_server()
        with patch(
            "prefab_sentinel.mcp_tools_patch.revert_overrides_impl",
            return_value=mock_resp,
        ) as mock_revert:
            _, result = _run(server.call_tool("revert_overrides", {
                "asset_path": "Assets/V.prefab",
                "target_file_id": "12345",
                "property_path": "m_Color.r",
                "confirm": True,
                "change_reason": "Remove unwanted override",
            }))

        mock_revert.assert_called_once_with(
            variant_path="Assets/V.prefab",
            target_file_id="12345",
            property_path="m_Color.r",
            dry_run=False,
            confirm=True,
            change_reason="Remove unwanted override",
        )
        self.assertTrue(result["success"])

    def test_empty_change_reason_becomes_none(self) -> None:
        mock_resp = MagicMock()
        mock_resp.to_dict.return_value = {"success": True}
        server = create_server()
        with patch(
            "prefab_sentinel.mcp_tools_patch.revert_overrides_impl",
            return_value=mock_resp,
        ) as mock_revert:
            _run(server.call_tool("revert_overrides", {
                "asset_path": "Assets/V.prefab",
                "target_file_id": "12345",
                "property_path": "m_Color.r",
                "change_reason": "",
            }))

        _, kwargs = mock_revert.call_args
        self.assertIsNone(kwargs["change_reason"])

    def test_confirm_requires_change_reason(self) -> None:
        server = create_server()
        with patch(
            "prefab_sentinel.mcp_tools_patch.revert_overrides_impl",
        ) as mock_revert:
            _, result = _run(server.call_tool("revert_overrides", {
                "asset_path": "Assets/V.prefab",
                "target_file_id": "12345",
                "property_path": "m_Color.r",
                "confirm": True,
                "change_reason": "",
            }))

        self.assertFalse(result["success"])
        self.assertEqual("CHANGE_REASON_REQUIRED", result["code"])
        mock_revert.assert_not_called()


class TestInspectHierarchyTool(unittest.TestCase):
    """Tests for the inspect_hierarchy MCP tool."""

    def test_delegates_to_orchestrator(self) -> None:
        mock_resp = MagicMock()
        mock_resp.to_dict.return_value = {"success": True, "data": {"tree": "..."}}
        mock_orch = MagicMock()
        mock_orch.inspect_hierarchy.return_value = mock_resp

        server = create_server()
        with patch.object(
            ProjectSession, "get_orchestrator", return_value=mock_orch,
        ):
            _, result = _run(server.call_tool("inspect_hierarchy", {"asset_path": "Assets/A.prefab"}))

        self.assertTrue(result["success"])
        mock_orch.inspect_hierarchy.assert_called_once_with(
            target_path="Assets/A.prefab",
            max_depth=None,
            show_components=True,
        )

    def test_passes_optional_params(self) -> None:
        mock_resp = MagicMock()
        mock_resp.to_dict.return_value = {"success": True}
        mock_orch = MagicMock()
        mock_orch.inspect_hierarchy.return_value = mock_resp

        server = create_server()
        with patch.object(
            ProjectSession, "get_orchestrator", return_value=mock_orch,
        ):
            _run(server.call_tool("inspect_hierarchy", {
                "asset_path": "Assets/A.prefab", "depth": 2, "show_components": False,
            }))

        mock_orch.inspect_hierarchy.assert_called_once_with(
            target_path="Assets/A.prefab", max_depth=2, show_components=False,
        )


class TestValidateRuntimeTool(unittest.TestCase):
    """Tests for the validate_runtime MCP tool."""

    def test_delegates_to_orchestrator(self) -> None:
        mock_resp = MagicMock()
        mock_resp.to_dict.return_value = {"success": True, "data": {"steps": []}}
        mock_orch = MagicMock()
        mock_orch.validate_runtime.return_value = mock_resp

        server = create_server()
        with patch.object(
            ProjectSession, "get_orchestrator", return_value=mock_orch,
        ):
            _, result = _run(server.call_tool("validate_runtime", {
                "asset_path": "Assets/Scenes/Main.unity",
            }))

        self.assertTrue(result["success"])
        mock_orch.validate_runtime.assert_called_once_with(
            scene_path="Assets/Scenes/Main.unity",
            profile="default",
            log_file=None,
            since_timestamp=None,
            allow_warnings=False,
            max_diagnostics=200,
        )

    def test_passes_all_params(self) -> None:
        mock_resp = MagicMock()
        mock_resp.to_dict.return_value = {"success": True}
        mock_orch = MagicMock()
        mock_orch.validate_runtime.return_value = mock_resp

        server = create_server()
        with patch.object(
            ProjectSession, "get_orchestrator", return_value=mock_orch,
        ):
            _run(server.call_tool("validate_runtime", {
                "asset_path": "Assets/S.unity",
                "profile": "smoke",
                "log_file": "/tmp/Editor.log",
                "allow_warnings": True,
                "max_diagnostics": 50,
            }))

        mock_orch.validate_runtime.assert_called_once_with(
            scene_path="Assets/S.unity",
            profile="smoke",
            log_file="/tmp/Editor.log",
            since_timestamp=None,
            allow_warnings=True,
            max_diagnostics=50,
        )


class TestPatchApplyTool(unittest.TestCase):
    """Tests for the patch_apply MCP tool."""

    def test_dry_run_default(self) -> None:
        mock_resp = MagicMock()
        mock_resp.to_dict.return_value = {"success": True, "code": "PATCH_DRY_RUN"}
        mock_orch = MagicMock()
        mock_orch.patch_apply.return_value = mock_resp

        plan_json = '{"plan_version": "2", "resources": [], "ops": []}'
        server = create_server()
        with patch.object(
            ProjectSession, "get_orchestrator", return_value=mock_orch,
        ):
            _, result = _run(server.call_tool("patch_apply", {"plan": plan_json}))

        self.assertTrue(result["success"])
        mock_orch.patch_apply.assert_called_once_with(
            plan={"plan_version": "2", "resources": [], "ops": []},
            dry_run=True,
            confirm=False,
            plan_sha256=None,
            plan_signature=None,
            change_reason=None,
            scope=None,
            runtime_scene=None,
            runtime_profile="default",
            runtime_log_file=None,
            runtime_since_timestamp=None,
            runtime_allow_warnings=False,
            runtime_max_diagnostics=200,
        )

    def test_confirm_mode(self) -> None:
        mock_resp = MagicMock()
        mock_resp.to_dict.return_value = {"success": True, "code": "PATCH_APPLIED"}
        mock_orch = MagicMock()
        mock_orch.patch_apply.return_value = mock_resp

        plan_json = '{"plan_version": "2", "resources": [], "ops": []}'
        server = create_server()
        with patch.object(
            ProjectSession, "get_orchestrator", return_value=mock_orch,
        ):
            _run(server.call_tool("patch_apply", {
                "plan": plan_json, "confirm": True, "change_reason": "Fix color",
            }))

        call_kwargs = mock_orch.patch_apply.call_args[1]
        self.assertFalse(call_kwargs["dry_run"])
        self.assertTrue(call_kwargs["confirm"])
        self.assertEqual("Fix color", call_kwargs["change_reason"])

    def test_invalid_json_returns_error(self) -> None:
        server = create_server()
        _, result = _run(server.call_tool("patch_apply", {"plan": "not json"}))
        self.assertFalse(result["success"])
        self.assertEqual("INVALID_PLAN_JSON", result["code"])
        self.assertEqual("error", result["severity"])
        self.assertIn("parse", result["message"].lower())

    def test_empty_change_reason_becomes_none(self) -> None:
        mock_resp = MagicMock()
        mock_resp.to_dict.return_value = {"success": True}
        mock_orch = MagicMock()
        mock_orch.patch_apply.return_value = mock_resp

        server = create_server()
        with patch.object(
            ProjectSession, "get_orchestrator", return_value=mock_orch,
        ):
            _run(server.call_tool("patch_apply", {
                "plan": '{"plan_version": "2"}', "change_reason": "",
            }))

        call_kwargs = mock_orch.patch_apply.call_args[1]
        self.assertIsNone(call_kwargs["change_reason"])

    def test_confirm_requires_change_reason(self) -> None:
        mock_orch = MagicMock()
        server = create_server()
        with patch.object(
            ProjectSession, "get_orchestrator", return_value=mock_orch,
        ):
            _, result = _run(server.call_tool("patch_apply", {
                "plan": '{"plan_version": "2"}',
                "confirm": True,
                "change_reason": "",
            }))

        self.assertFalse(result["success"])
        self.assertEqual("CHANGE_REASON_REQUIRED", result["code"])
        mock_orch.patch_apply.assert_not_called()



# ---------------------------------------------------------------------------
# activate_project suggested_reads
# ---------------------------------------------------------------------------


class TestActivateProjectSuggestedReads(unittest.TestCase):
    """activate_project response includes suggested_reads."""

    @patch("prefab_sentinel.session_cache.collect_project_guid_index")
    @patch("prefab_sentinel.session_cache.build_script_name_map")
    @patch("prefab_sentinel.session_cache.Phase1Orchestrator")
    @patch("prefab_sentinel.session.resolve_scope_path")
    @patch("prefab_sentinel.session.find_project_root")
    def test_response_contains_suggested_reads(
        self,
        mock_find: MagicMock,
        mock_resolve: MagicMock,
        mock_orch: MagicMock,
        mock_build: MagicMock,
        mock_guid: MagicMock,
    ) -> None:
        mock_find.return_value = Path("/unity")
        mock_resolve.return_value = Path("/unity/Assets/MyScope")
        mock_build.return_value = {}
        mock_guid.return_value = {}
        server = create_server()
        _, result = _run(server.call_tool("activate_project", {"scope": "Assets/MyScope"}))
        self.assertIn("suggested_reads", result["data"])
        self.assertIsInstance(result["data"]["suggested_reads"], list)
        self.assertTrue(
            any("prefab-sentinel" in r for r in result["data"]["suggested_reads"])
        )

    @patch("prefab_sentinel.session_cache.collect_project_guid_index")
    @patch("prefab_sentinel.session_cache.build_script_name_map")
    @patch("prefab_sentinel.session_cache.Phase1Orchestrator")
    @patch("prefab_sentinel.session.resolve_scope_path")
    @patch("prefab_sentinel.session.find_project_root")
    def test_response_contains_knowledge_hint(
        self,
        mock_find: MagicMock,
        mock_resolve: MagicMock,
        mock_orch: MagicMock,
        mock_build: MagicMock,
        mock_guid: MagicMock,
    ) -> None:
        mock_find.return_value = Path("/unity")
        mock_resolve.return_value = Path("/unity/Assets/MyScope")
        mock_build.return_value = {}
        mock_guid.return_value = {}
        server = create_server()
        _, result = _run(server.call_tool("activate_project", {"scope": "Assets/MyScope"}))
        self.assertIn("knowledge_hint", result["data"])

        self.assertIn(KNOWLEDGE_URI_PREFIX, result["data"]["knowledge_hint"])


# ---------------------------------------------------------------------------
# Knowledge MCP Resources
# ---------------------------------------------------------------------------


class TestKnowledgeResources(unittest.TestCase):
    """knowledge/*.md files are registered as MCP Resources."""

    def test_resources_registered(self) -> None:
        """At least one knowledge resource is registered."""
        server = create_server()
        resources = _run(server.list_resources())
        uris = [r.uri for r in resources]
        knowledge_uris = [u for u in uris if "knowledge/" in str(u)]
        self.assertGreater(len(knowledge_uris), 0)

    def test_resource_uri_scheme(self) -> None:
        """All knowledge resources use the expected URI scheme."""

        server = create_server()
        resources = _run(server.list_resources())
        for r in resources:
            uri_str = str(r.uri)
            if "knowledge/" in uri_str:
                self.assertTrue(
                    uri_str.startswith(KNOWLEDGE_URI_PREFIX),
                    f"Unexpected URI: {uri_str}",
                )
                self.assertTrue(uri_str.endswith(".md"), f"Not .md: {uri_str}")

    def test_resource_read_returns_content(self) -> None:
        """Reading a knowledge resource returns non-empty markdown text."""
        server = create_server()
        resources = _run(server.list_resources())
        knowledge_resources = [
            r for r in resources if "knowledge/" in str(r.uri)
        ]
        self.assertGreater(len(knowledge_resources), 0)
        # Read the first one
        uri = str(knowledge_resources[0].uri)
        content = _run(server.read_resource(uri))
        # content is a list with one item (text or blob)
        text = content[0].content if hasattr(content[0], "content") else str(content[0])
        self.assertGreater(len(text), 0)

    def test_resource_has_description(self) -> None:
        """Each knowledge resource has a non-empty description."""
        server = create_server()
        resources = _run(server.list_resources())
        for r in resources:
            if "knowledge/" in str(r.uri):
                self.assertTrue(
                    r.description and len(r.description) > 0,
                    f"Missing description for {r.uri}",
                )


# ---------------------------------------------------------------------------
# deploy_bridge cleanup and unconditional deploy
# ---------------------------------------------------------------------------


class TestDeployBridgeCleanup(unittest.TestCase):
    """deploy_bridge old file cleanup and unconditional deploy."""

    def setUp(self) -> None:
        self._tmp = Path(tempfile.mkdtemp())
        self._project_root = self._tmp / "UnityProject"
        self._project_root.mkdir()
        self._target = self._project_root / "Assets" / "Editor" / "PrefabSentinel"
        self._target.mkdir(parents=True)

    def tearDown(self) -> None:
        import shutil
        shutil.rmtree(self._tmp, ignore_errors=True)

    @patch("prefab_sentinel.mcp_tools_session.send_action")
    def test_removes_old_files_from_parent(self, _mock: MagicMock) -> None:
        """Old PrefabSentinel.*.cs in parent dir are removed before deploy."""
        parent = self._target.parent
        old_cs = parent / "PrefabSentinel.EditorBridge.cs"
        old_meta = parent / "PrefabSentinel.EditorBridge.cs.meta"
        old_cs.write_text("// old", encoding="utf-8")
        old_meta.write_text("guid: abc", encoding="utf-8")

        server = create_server(project_root=str(self._project_root))
        _, result = _run(server.call_tool(
            "deploy_bridge",
            {"target_dir": str(self._target)},
        ))

        self.assertTrue(result["success"])
        self.assertIn("PrefabSentinel.EditorBridge.cs", result["data"]["removed_old_files"])
        self.assertIn("PrefabSentinel.EditorBridge.cs.meta", result["data"]["removed_old_files"])
        self.assertFalse(old_cs.exists())
        self.assertFalse(old_meta.exists())

    @patch("prefab_sentinel.mcp_tools_session.send_action")
    def test_no_old_files_no_removal(self, _mock: MagicMock) -> None:
        """When parent has no old files, removed_old_files is empty."""
        server = create_server(project_root=str(self._project_root))
        _, result = _run(server.call_tool(
            "deploy_bridge",
            {"target_dir": str(self._target)},
        ))

        self.assertTrue(result["success"])
        self.assertEqual(result["data"]["removed_old_files"], [])

    @patch("prefab_sentinel.mcp_tools_session.send_action")
    def test_first_deploy_no_old_files(self, _mock: MagicMock) -> None:
        """First deploy to a new path has no old files to clean up."""
        deep_target = self._project_root / "Assets" / "NewDir" / "SubDir" / "Bridge"
        server = create_server(project_root=str(self._project_root))
        _, result = _run(server.call_tool(
            "deploy_bridge",
            {"target_dir": str(deep_target)},
        ))

        self.assertTrue(result["success"])
        self.assertEqual(result["data"]["removed_old_files"], [])

    @patch("prefab_sentinel.mcp_tools_session.send_action")
    def test_upload_handler_always_deployed(self, _mock: MagicMock) -> None:
        """VRCSDKUploadHandler.cs is always copied unconditionally."""
        server = create_server(project_root=str(self._project_root))
        _, result = _run(server.call_tool(
            "deploy_bridge",
            {"target_dir": str(self._target)},
        ))

        self.assertTrue(result["success"])
        self.assertIn("PrefabSentinel.VRCSDKUploadHandler.cs", result["data"]["copied_files"])
        self.assertTrue((self._target / "PrefabSentinel.VRCSDKUploadHandler.cs").exists())

    @patch("prefab_sentinel.mcp_tools_session.send_action")
    def test_asmdef_deployed(self, _mock: MagicMock) -> None:
        """PrefabSentinel.Editor.asmdef is copied alongside C# files."""
        server = create_server(project_root=str(self._project_root))
        _, result = _run(server.call_tool(
            "deploy_bridge",
            {"target_dir": str(self._target)},
        ))

        self.assertTrue(result["success"])
        self.assertIn("PrefabSentinel.Editor.asmdef", result["data"]["copied_files"])
        self.assertTrue((self._target / "PrefabSentinel.Editor.asmdef").exists())

    @patch("prefab_sentinel.mcp_tools_session.send_action")
    def test_no_skipped_files_in_response(self, _mock: MagicMock) -> None:
        """Response data must not contain skipped_files key."""
        server = create_server(project_root=str(self._project_root))
        _, result = _run(server.call_tool(
            "deploy_bridge",
            {"target_dir": str(self._target)},
        ))

        self.assertTrue(result["success"])
        self.assertNotIn("skipped_files", result["data"])

    @patch("prefab_sentinel.mcp_tools_session.send_action")
    def test_diagnostics_warn_on_old_file_removal(self, _mock: MagicMock) -> None:
        """Diagnostics include warning when old files are removed."""
        parent = self._target.parent
        (parent / "PrefabSentinel.EditorBridge.cs").write_text("// old", encoding="utf-8")

        server = create_server(project_root=str(self._project_root))
        _, result = _run(server.call_tool(
            "deploy_bridge",
            {"target_dir": str(self._target)},
        ))

        warnings = [d for d in result["diagnostics"] if d["severity"] == "warning"]
        self.assertTrue(any("old Bridge" in d["message"] for d in warnings))

    @patch("prefab_sentinel.mcp_tools_session.send_action")
    def test_clean_redeploy_removes_all_target_files(self, _mock: MagicMock) -> None:
        """All pre-existing files in target_dir are removed before deploy."""
        (self._target / "Dummy.cs").write_text("// dummy", encoding="utf-8")
        (self._target / "Dummy.cs.meta").write_text("guid: dummy", encoding="utf-8")

        server = create_server(project_root=str(self._project_root))
        _, result = _run(server.call_tool(
            "deploy_bridge",
            {"target_dir": str(self._target)},
        ))

        self.assertTrue(result["success"])
        self.assertFalse((self._target / "Dummy.cs").exists())
        self.assertFalse((self._target / "Dummy.cs.meta").exists())

    @patch("prefab_sentinel.mcp_tools_session.send_action")
    def test_clean_redeploy_preserves_subdirectories(self, _mock: MagicMock) -> None:
        """Subdirectories inside target_dir survive the clean phase."""
        subdir = self._target / "subdir"
        subdir.mkdir()
        (subdir / "keep.txt").write_text("keep", encoding="utf-8")

        server = create_server(project_root=str(self._project_root))
        _, result = _run(server.call_tool(
            "deploy_bridge",
            {"target_dir": str(self._target)},
        ))

        self.assertTrue(result["success"])
        self.assertTrue(subdir.is_dir())
        self.assertTrue((subdir / "keep.txt").exists())
        self.assertIsInstance(result["data"]["removed_stale_files"], list)

    @patch("prefab_sentinel.mcp_tools_session.send_action")
    def test_removed_stale_files_in_response(self, _mock: MagicMock) -> None:
        """Stale files removed during clean phase appear in response data."""
        (self._target / "OldFile.cs").write_text("// old", encoding="utf-8")

        server = create_server(project_root=str(self._project_root))
        _, result = _run(server.call_tool(
            "deploy_bridge",
            {"target_dir": str(self._target)},
        ))

        self.assertTrue(result["success"])
        self.assertIn("OldFile.cs", result["data"]["removed_stale_files"])

    @patch("prefab_sentinel.mcp_tools_session.send_action")
    def test_clean_redeploy_diagnostic_message(self, _mock: MagicMock) -> None:
        """Clearing files produces an info diagnostic with 'Cleared' message."""
        (self._target / "Stale.cs").write_text("// stale", encoding="utf-8")

        server = create_server(project_root=str(self._project_root))
        _, result = _run(server.call_tool(
            "deploy_bridge",
            {"target_dir": str(self._target)},
        ))

        infos = [d for d in result["diagnostics"] if d["severity"] == "info"]
        self.assertTrue(any("Cleared" in d["message"] for d in infos))

    @patch("prefab_sentinel.mcp_tools_session.send_action")
    def test_first_deploy_empty_removed_stale(self, _mock: MagicMock) -> None:
        """First deploy to empty target_dir has empty removed_stale_files."""
        fresh_target = self._project_root / "Assets" / "Editor" / "FreshDeploy"
        server = create_server(project_root=str(self._project_root))
        _, result = _run(server.call_tool(
            "deploy_bridge",
            {"target_dir": str(fresh_target)},
        ))

        self.assertTrue(result["success"])
        self.assertEqual(result["data"]["removed_stale_files"], [])

    @patch("prefab_sentinel.mcp_tools_session.send_action")
    def test_uses_bridge_files_dir_when_available(self, _mock: MagicMock) -> None:
        """When _bridge_files/ exists (wheel install), uses it over tools/unity/."""
        # Create _bridge_files in a temp dir and patch __file__ to point there
        fake_pkg = self._tmp / "fake_pkg" / "prefab_sentinel"
        fake_pkg.mkdir(parents=True)
        bridge_dir = fake_pkg / "_bridge_files"
        bridge_dir.mkdir()
        test_cs = bridge_dir / "PrefabSentinel.TestBridge.cs"
        test_cs.write_text("// from _bridge_files", encoding="utf-8")

        import prefab_sentinel.mcp_tools_session as mcp_mod
        original_file = mcp_mod.__file__
        mcp_mod.__file__ = str(fake_pkg / "mcp_tools_session.py")
        try:
            server = create_server(project_root=str(self._project_root))
            _, result = _run(server.call_tool(
                "deploy_bridge",
                {"target_dir": str(self._target)},
            ))
        finally:
            mcp_mod.__file__ = original_file

        self.assertTrue(result["success"])
        # Should have copied from _bridge_files, not tools/unity/
        self.assertIn("PrefabSentinel.TestBridge.cs", result["data"]["copied_files"])
        # Should NOT contain files from tools/unity/
        self.assertNotIn("PrefabSentinel.EditorBridge.cs", result["data"]["copied_files"])


# ---------------------------------------------------------------------------
# _extract_description
# ---------------------------------------------------------------------------


class TestExtractDescription(unittest.TestCase):
    """_extract_description handles various frontmatter formats."""

    def _extract(self, content: str) -> str:
        """Write content to a temp file and extract description."""
        with tempfile.NamedTemporaryFile(
            suffix=".md", mode="w", delete=False, encoding="utf-8"
        ) as f:
            f.write(content)
            f.flush()
            path = Path(f.name)
        try:
            from prefab_sentinel.mcp_server import _extract_description
            return _extract_description(path)
        finally:
            path.unlink(missing_ok=True)

    def test_with_description_field(self) -> None:
        content = "---\ntool: foo\ndescription: A helpful guide\n---\n# Title\n"
        self.assertEqual("A helpful guide", self._extract(content))

    def test_with_tool_field_only(self) -> None:
        content = "---\ntool: liltoon\nversion_tested: 1.0\n---\n# Title\n"
        self.assertEqual("liltoon knowledge", self._extract(content))

    def test_no_frontmatter(self) -> None:
        content = "# Just a markdown file\nSome content.\n"
        result = self._extract(content)
        # Returns the file stem (temp file name)
        self.assertIsInstance(result, str)
        self.assertGreater(len(result), 0)

    def test_incomplete_frontmatter(self) -> None:
        content = "---\ntool: broken\n# No closing delimiter\n"
        result = self._extract(content)
        self.assertIsInstance(result, str)

    def test_quoted_values_stripped(self) -> None:
        content = '---\ntool: "udonsharp"\n---\n# Title\n'
        self.assertEqual("udonsharp knowledge", self._extract(content))


class TestCopyAssetTool(unittest.TestCase):
    """Tests for the copy_asset MCP tool."""

    def test_delegates_to_orchestrator(self) -> None:
        mock_resp = MagicMock()
        mock_resp.to_dict.return_value = {"success": True, "data": {"m_name_after": "copied"}}
        mock_orch = MagicMock()
        mock_orch.copy_asset.return_value = mock_resp

        server = create_server()
        with patch.object(
            ProjectSession, "get_orchestrator", return_value=mock_orch,
        ):
            _, result = _run(server.call_tool("copy_asset", {
                "source_path": "Assets/Mat/A.mat",
                "dest_path": "Assets/Mat/B.mat",
                "confirm": True,
                "change_reason": "duplicate material",
            }))

        self.assertTrue(result["success"])
        mock_orch.copy_asset.assert_called_once_with(
            source_path="Assets/Mat/A.mat",
            dest_path="Assets/Mat/B.mat",
            dry_run=False,
            change_reason="duplicate material",
        )

    def test_confirm_requires_change_reason(self) -> None:
        mock_orch = MagicMock()
        server = create_server()
        with patch.object(
            ProjectSession, "get_orchestrator", return_value=mock_orch,
        ):
            _, result = _run(server.call_tool("copy_asset", {
                "source_path": "Assets/Mat/A.mat",
                "dest_path": "Assets/Mat/B.mat",
                "confirm": True,
                "change_reason": "",
            }))

        self.assertFalse(result["success"])
        self.assertEqual("CHANGE_REASON_REQUIRED", result["code"])
        mock_orch.copy_asset.assert_not_called()


class TestRenameAssetTool(unittest.TestCase):
    """Tests for the rename_asset MCP tool."""

    def test_delegates_to_orchestrator(self) -> None:
        mock_resp = MagicMock()
        mock_resp.to_dict.return_value = {"success": True, "data": {"m_name_after": "renamed"}}
        mock_orch = MagicMock()
        mock_orch.rename_asset.return_value = mock_resp

        server = create_server()
        with patch.object(
            ProjectSession, "get_orchestrator", return_value=mock_orch,
        ):
            _, result = _run(server.call_tool("rename_asset", {
                "asset_path": "Assets/Mat/Old.mat",
                "new_name": "New.mat",
                "confirm": True,
                "change_reason": "rename for clarity",
            }))

        self.assertTrue(result["success"])
        mock_orch.rename_asset.assert_called_once_with(
            asset_path="Assets/Mat/Old.mat",
            new_name="New.mat",
            dry_run=False,
            change_reason="rename for clarity",
        )

    def test_confirm_requires_change_reason(self) -> None:
        mock_orch = MagicMock()
        server = create_server()
        with patch.object(
            ProjectSession, "get_orchestrator", return_value=mock_orch,
        ):
            _, result = _run(server.call_tool("rename_asset", {
                "asset_path": "Assets/Mat/Old.mat",
                "new_name": "New.mat",
                "confirm": True,
                "change_reason": "",
            }))

        self.assertFalse(result["success"])
        self.assertEqual("CHANGE_REASON_REQUIRED", result["code"])
        mock_orch.rename_asset.assert_not_called()


class TestSetMaterialPropertyTool(unittest.TestCase):
    """Tests for the set_material_property MCP tool."""

    def test_confirm_requires_change_reason(self) -> None:
        mock_orch = MagicMock()
        server = create_server()
        with patch.object(
            ProjectSession, "get_orchestrator", return_value=mock_orch,
        ):
            _, result = _run(server.call_tool("set_material_property", {
                "asset_path": "Assets/M.mat",
                "property_name": "_Color",
                "value": "0.5",
                "confirm": True,
                "change_reason": "",
            }))

        self.assertFalse(result["success"])
        self.assertEqual("CHANGE_REASON_REQUIRED", result["code"])
        mock_orch.set_material_property.assert_not_called()


class TestCopyComponentFieldsTool(unittest.TestCase):
    """Test the copy_component_fields MCP tool."""

    def _meshrenderer_prefab(self, go_name: str = "Cube") -> str:
        return YAML_HEADER + "\n".join([
            make_gameobject("100", go_name, ["200", "300"]),
            make_transform("200", "100"),
            (
                "--- !u!23 &300\n"
                "MeshRenderer:\n"
                "  m_ObjectHideFlags: 0\n"
                "  m_CorrespondingSourceObject: {fileID: 0}\n"
                "  m_PrefabInstance: {fileID: 0}\n"
                "  m_PrefabAsset: {fileID: 0}\n"
                "  m_GameObject: {fileID: 100}\n"
                "  m_Enabled: 1\n"
                "  m_CastShadows: 1\n"
                "  m_ReceiveShadows: 1\n"
                "  m_Materials:\n"
                "  - {fileID: 2100000, guid: aaa, type: 2}\n"
            ),
        ])

    def _two_meshrenderer_prefab(self) -> str:
        """Prefab with two GOs each having a MeshRenderer."""
        return YAML_HEADER + "\n".join([
            make_gameobject("100", "Parent", ["200", "300"]),
            make_transform("200", "100", children_file_ids=["500"]),
            (
                "--- !u!23 &300\n"
                "MeshRenderer:\n"
                "  m_ObjectHideFlags: 0\n"
                "  m_GameObject: {fileID: 100}\n"
                "  m_Enabled: 1\n"
                "  m_CastShadows: 1\n"
            ),
            make_gameobject("400", "Child", ["500", "600"]),
            make_transform("500", "400", father_file_id="200"),
            (
                "--- !u!23 &600\n"
                "MeshRenderer:\n"
                "  m_ObjectHideFlags: 0\n"
                "  m_GameObject: {fileID: 400}\n"
                "  m_Enabled: 0\n"
                "  m_CastShadows: 0\n"
            ),
        ])

    def _monobehaviour_prefab(
        self, guid: str = "aaaa1111bbbb2222cccc3333dddd4444",
    ) -> str:
        return _make_simple_monobehaviour_prefab(guid)

    def _system_fields_only_prefab(self) -> str:
        """Prefab where MeshRenderer has ONLY system fields."""
        return YAML_HEADER + "\n".join([
            make_gameobject("100", "Empty", ["200", "300"]),
            make_transform("200", "100"),
            (
                "--- !u!23 &300\n"
                "MeshRenderer:\n"
                "  m_ObjectHideFlags: 0\n"
                "  m_CorrespondingSourceObject: {fileID: 0}\n"
                "  m_PrefabInstance: {fileID: 0}\n"
                "  m_PrefabAsset: {fileID: 0}\n"
                "  m_GameObject: {fileID: 100}\n"
                "  m_EditorHideFlags: 0\n"
                "  m_Script: {fileID: 0}\n"
                "  m_EditorClassIdentifier:\n"
            ),
        ])

    def _mock_patch_apply_response(self, dry_run: bool = True) -> MagicMock:
        resp = MagicMock()
        resp.success = True
        resp.to_dict.return_value = {
            "success": True,
            "severity": "info",
            "code": "PATCH_APPLY_RESULT",
            "message": "patch.apply dry-run completed." if dry_run else "patch.apply completed.",
            "data": {"dry_run": dry_run, "confirm": not dry_run, "read_only": dry_run},
            "diagnostics": [],
        }
        return resp

    def test_copy_all_fields_dry_run(self) -> None:
        """Copy all user fields with dry_run=True produces set ops."""
        src_text = self._meshrenderer_prefab("Src")
        dst_text = self._meshrenderer_prefab("Dst")
        server = create_server()
        mock_resp = self._mock_patch_apply_response(dry_run=True)

        with tempfile.TemporaryDirectory() as td:
            src = Path(td) / "src.prefab"
            dst = Path(td) / "dst.prefab"
            src.write_text(src_text, encoding="utf-8")
            dst.write_text(dst_text, encoding="utf-8")

            with patch(
                "prefab_sentinel.session_cache.Phase1Orchestrator",
            ) as mock_cls:
                mock_orch = MagicMock()
                mock_orch.patch_apply.return_value = mock_resp
                mock_cls.default.return_value = mock_orch

                _, result = _run(server.call_tool(
                    "copy_component_fields",
                    {
                        "src_asset_path": str(src),
                        "src_symbol_path": "Src/MeshRenderer",
                        "dst_asset_path": str(dst),
                        "dst_symbol_path": "Dst/MeshRenderer",
                    },
                ))

        self.assertTrue(result["success"])
        plan = mock_orch.patch_apply.call_args[1]["plan"]
        op_paths = [op["path"] for op in plan["ops"]]
        self.assertIn("m_Enabled", op_paths)
        self.assertIn("m_CastShadows", op_paths)
        self.assertIn("m_ReceiveShadows", op_paths)
        # System fields must NOT be in ops
        for op in plan["ops"]:
            self.assertNotIn(op["path"], {
                "m_ObjectHideFlags", "m_CorrespondingSourceObject",
                "m_PrefabInstance", "m_PrefabAsset", "m_GameObject",
                "m_EditorHideFlags", "m_Script", "m_EditorClassIdentifier",
            })
        self.assertTrue(mock_orch.patch_apply.call_args[1]["dry_run"])

    def test_copy_specific_fields(self) -> None:
        """When fields parameter is provided, only those fields appear in ops."""
        src_text = self._meshrenderer_prefab("Src")
        dst_text = self._meshrenderer_prefab("Dst")
        server = create_server()
        mock_resp = self._mock_patch_apply_response(dry_run=True)

        with tempfile.TemporaryDirectory() as td:
            src = Path(td) / "src.prefab"
            dst = Path(td) / "dst.prefab"
            src.write_text(src_text, encoding="utf-8")
            dst.write_text(dst_text, encoding="utf-8")

            with patch(
                "prefab_sentinel.session_cache.Phase1Orchestrator",
            ) as mock_cls:
                mock_orch = MagicMock()
                mock_orch.patch_apply.return_value = mock_resp
                mock_cls.default.return_value = mock_orch

                _, result = _run(server.call_tool(
                    "copy_component_fields",
                    {
                        "src_asset_path": str(src),
                        "src_symbol_path": "Src/MeshRenderer",
                        "dst_asset_path": str(dst),
                        "dst_symbol_path": "Dst/MeshRenderer",
                        "fields": ["m_Enabled"],
                    },
                ))

        self.assertTrue(result["success"])
        plan = mock_orch.patch_apply.call_args[1]["plan"]
        op_paths = [op["path"] for op in plan["ops"]]
        self.assertEqual(["m_Enabled"], op_paths)

    def test_copy_cross_asset(self) -> None:
        """Source and destination in different files works."""
        src_text = self._meshrenderer_prefab("Src")
        dst_text = self._meshrenderer_prefab("Dst")
        server = create_server()
        mock_resp = self._mock_patch_apply_response(dry_run=True)

        with tempfile.TemporaryDirectory() as td:
            src = Path(td) / "a.prefab"
            dst = Path(td) / "b.prefab"
            src.write_text(src_text, encoding="utf-8")
            dst.write_text(dst_text, encoding="utf-8")

            with patch(
                "prefab_sentinel.session_cache.Phase1Orchestrator",
            ) as mock_cls:
                mock_orch = MagicMock()
                mock_orch.patch_apply.return_value = mock_resp
                mock_cls.default.return_value = mock_orch

                _, result = _run(server.call_tool(
                    "copy_component_fields",
                    {
                        "src_asset_path": str(src),
                        "src_symbol_path": "Src/MeshRenderer",
                        "dst_asset_path": str(dst),
                        "dst_symbol_path": "Dst/MeshRenderer",
                    },
                ))

        self.assertTrue(result["success"])
        plan = mock_orch.patch_apply.call_args[1]["plan"]
        self.assertEqual(str(dst), plan["resources"][0]["path"])

    def test_copy_same_asset(self) -> None:
        """Source and destination in the same file, different GOs."""
        text = self._two_meshrenderer_prefab()
        server = create_server()
        mock_resp = self._mock_patch_apply_response(dry_run=True)

        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "test.prefab"
            p.write_text(text, encoding="utf-8")

            with patch(
                "prefab_sentinel.session_cache.Phase1Orchestrator",
            ) as mock_cls:
                mock_orch = MagicMock()
                mock_orch.patch_apply.return_value = mock_resp
                mock_cls.default.return_value = mock_orch

                _, result = _run(server.call_tool(
                    "copy_component_fields",
                    {
                        "src_asset_path": str(p),
                        "src_symbol_path": "Parent/MeshRenderer",
                        "dst_asset_path": str(p),
                        "dst_symbol_path": "Parent/Child/MeshRenderer",
                    },
                ))

        self.assertTrue(result["success"])

    def test_copy_confirm(self) -> None:
        """confirm=True triggers apply, cache invalidated."""
        src_text = self._meshrenderer_prefab("Src")
        dst_text = self._meshrenderer_prefab("Dst")
        server = create_server()
        mock_resp = self._mock_patch_apply_response(dry_run=False)

        with tempfile.TemporaryDirectory() as td:
            src = Path(td) / "src.prefab"
            dst = Path(td) / "dst.prefab"
            src.write_text(src_text, encoding="utf-8")
            dst.write_text(dst_text, encoding="utf-8")

            with patch(
                "prefab_sentinel.session_cache.Phase1Orchestrator",
            ) as mock_cls:
                mock_orch = MagicMock()
                mock_orch.patch_apply.return_value = mock_resp
                mock_orch.maybe_auto_refresh.return_value = "done"
                mock_cls.default.return_value = mock_orch

                _, result = _run(server.call_tool(
                    "copy_component_fields",
                    {
                        "src_asset_path": str(src),
                        "src_symbol_path": "Src/MeshRenderer",
                        "dst_asset_path": str(dst),
                        "dst_symbol_path": "Dst/MeshRenderer",
                        "confirm": True,
                        "change_reason": "copy fields for test",
                    },
                ))

        self.assertTrue(result["success"])
        call_kwargs = mock_orch.patch_apply.call_args[1]
        self.assertFalse(call_kwargs["dry_run"])
        self.assertTrue(call_kwargs["confirm"])
        self.assertIn("auto_refresh", result)

    def test_copy_type_mismatch(self) -> None:
        """Different component types return TYPE_MISMATCH error."""
        src_text = self._meshrenderer_prefab("Src")
        dst_text = YAML_HEADER + "\n".join([
            make_gameobject("100", "Dst", ["200", "300"]),
            make_transform("200", "100"),
            (
                "--- !u!33 &300\n"
                "MeshFilter:\n"
                "  m_ObjectHideFlags: 0\n"
                "  m_GameObject: {fileID: 100}\n"
            ),
        ])
        server = create_server()

        with tempfile.TemporaryDirectory() as td:
            src = Path(td) / "src.prefab"
            dst = Path(td) / "dst.prefab"
            src.write_text(src_text, encoding="utf-8")
            dst.write_text(dst_text, encoding="utf-8")

            _, result = _run(server.call_tool(
                "copy_component_fields",
                {
                    "src_asset_path": str(src),
                    "src_symbol_path": "Src/MeshRenderer",
                    "dst_asset_path": str(dst),
                    "dst_symbol_path": "Dst/MeshFilter",
                },
            ))

        self.assertFalse(result["success"])
        self.assertEqual("TYPE_MISMATCH", result["code"])
        self.assertIn("src_type", result["data"])
        self.assertIn("dst_type", result["data"])

    def test_copy_src_symbol_not_found(self) -> None:
        """SYMBOL_NOT_FOUND when source symbol path doesn't resolve."""
        src_text = self._meshrenderer_prefab("Src")
        dst_text = self._meshrenderer_prefab("Dst")
        server = create_server()

        with tempfile.TemporaryDirectory() as td:
            src = Path(td) / "src.prefab"
            dst = Path(td) / "dst.prefab"
            src.write_text(src_text, encoding="utf-8")
            dst.write_text(dst_text, encoding="utf-8")

            _, result = _run(server.call_tool(
                "copy_component_fields",
                {
                    "src_asset_path": str(src),
                    "src_symbol_path": "NonExistent/MeshRenderer",
                    "dst_asset_path": str(dst),
                    "dst_symbol_path": "Dst/MeshRenderer",
                },
            ))

        self.assertFalse(result["success"])
        self.assertEqual("SYMBOL_NOT_FOUND", result["code"])
        self.assertIn("suggestions", result["data"])

    def test_copy_dst_symbol_not_found(self) -> None:
        """SYMBOL_NOT_FOUND when destination symbol path doesn't resolve."""
        src_text = self._meshrenderer_prefab("Src")
        dst_text = self._meshrenderer_prefab("Dst")
        server = create_server()

        with tempfile.TemporaryDirectory() as td:
            src = Path(td) / "src.prefab"
            dst = Path(td) / "dst.prefab"
            src.write_text(src_text, encoding="utf-8")
            dst.write_text(dst_text, encoding="utf-8")

            _, result = _run(server.call_tool(
                "copy_component_fields",
                {
                    "src_asset_path": str(src),
                    "src_symbol_path": "Src/MeshRenderer",
                    "dst_asset_path": str(dst),
                    "dst_symbol_path": "NonExistent/MeshRenderer",
                },
            ))

        self.assertFalse(result["success"])
        self.assertEqual("SYMBOL_NOT_FOUND", result["code"])

    def test_copy_src_not_component(self) -> None:
        """SYMBOL_NOT_COMPONENT when source is a GameObject."""
        src_text = self._meshrenderer_prefab("Src")
        dst_text = self._meshrenderer_prefab("Dst")
        server = create_server()

        with tempfile.TemporaryDirectory() as td:
            src = Path(td) / "src.prefab"
            dst = Path(td) / "dst.prefab"
            src.write_text(src_text, encoding="utf-8")
            dst.write_text(dst_text, encoding="utf-8")

            _, result = _run(server.call_tool(
                "copy_component_fields",
                {
                    "src_asset_path": str(src),
                    "src_symbol_path": "Src",
                    "dst_asset_path": str(dst),
                    "dst_symbol_path": "Dst/MeshRenderer",
                },
            ))

        self.assertFalse(result["success"])
        self.assertEqual("SYMBOL_NOT_COMPONENT", result["code"])

    def test_copy_dst_not_component(self) -> None:
        """SYMBOL_NOT_COMPONENT for destination path."""
        src_text = self._meshrenderer_prefab("Src")
        dst_text = self._meshrenderer_prefab("Dst")
        server = create_server()

        with tempfile.TemporaryDirectory() as td:
            src = Path(td) / "src.prefab"
            dst = Path(td) / "dst.prefab"
            src.write_text(src_text, encoding="utf-8")
            dst.write_text(dst_text, encoding="utf-8")

            _, result = _run(server.call_tool(
                "copy_component_fields",
                {
                    "src_asset_path": str(src),
                    "src_symbol_path": "Src/MeshRenderer",
                    "dst_asset_path": str(dst),
                    "dst_symbol_path": "Dst",
                },
            ))

        self.assertFalse(result["success"])
        self.assertEqual("SYMBOL_NOT_COMPONENT", result["code"])

    def test_copy_field_not_found(self) -> None:
        """FIELD_NOT_FOUND when requested field doesn't exist on source."""
        src_text = self._meshrenderer_prefab("Src")
        dst_text = self._meshrenderer_prefab("Dst")
        server = create_server()

        with tempfile.TemporaryDirectory() as td:
            src = Path(td) / "src.prefab"
            dst = Path(td) / "dst.prefab"
            src.write_text(src_text, encoding="utf-8")
            dst.write_text(dst_text, encoding="utf-8")

            _, result = _run(server.call_tool(
                "copy_component_fields",
                {
                    "src_asset_path": str(src),
                    "src_symbol_path": "Src/MeshRenderer",
                    "dst_asset_path": str(dst),
                    "dst_symbol_path": "Dst/MeshRenderer",
                    "fields": ["nonExistentField"],
                },
            ))

        self.assertFalse(result["success"])
        self.assertEqual("FIELD_NOT_FOUND", result["code"])
        self.assertIn("available_fields", result["data"])

    def test_copy_monobehaviour_fields(self) -> None:
        """MonoBehaviour with custom fields copies correctly."""
        src_text = self._monobehaviour_prefab()
        dst_text = self._monobehaviour_prefab()
        mock_resp = self._mock_patch_apply_response(dry_run=True)

        with tempfile.TemporaryDirectory() as td:
            src = Path(td) / "src.prefab"
            dst = Path(td) / "dst.prefab"
            src.write_text(src_text, encoding="utf-8")
            dst.write_text(dst_text, encoding="utf-8")

            script_dir = Path(td) / "Assets" / "Scripts"
            script_dir.mkdir(parents=True)
            cs_file = script_dir / "PlayerScript.cs"
            cs_file.write_text(
                "using UnityEngine;\npublic class PlayerScript : MonoBehaviour {}\n",
                encoding="utf-8",
            )
            meta_file = script_dir / "PlayerScript.cs.meta"
            meta_file.write_text(
                "fileFormatVersion: 2\nguid: aaaa1111bbbb2222cccc3333dddd4444\n",
                encoding="utf-8",
            )

            with patch(
                "prefab_sentinel.session_cache.Phase1Orchestrator",
            ) as mock_cls:
                mock_orch = MagicMock()
                mock_orch.patch_apply.return_value = mock_resp
                mock_cls.default.return_value = mock_orch

                server_inst = create_server(project_root=td)
                _, result = _run(server_inst.call_tool(
                    "copy_component_fields",
                    {
                        "src_asset_path": str(src),
                        "src_symbol_path": "Player/MonoBehaviour(PlayerScript)",
                        "dst_asset_path": str(dst),
                        "dst_symbol_path": "Player/MonoBehaviour(PlayerScript)",
                    },
                ))

        self.assertTrue(result["success"])
        plan = mock_orch.patch_apply.call_args[1]["plan"]
        op_paths = [op["path"] for op in plan["ops"]]
        self.assertIn("speed", op_paths)
        self.assertIn("health", op_paths)

    def test_copy_no_fields_to_copy(self) -> None:
        """NO_FIELDS_TO_COPY when source has only system fields."""
        src_text = self._system_fields_only_prefab()
        dst_text = self._meshrenderer_prefab("Dst")
        server = create_server()

        with tempfile.TemporaryDirectory() as td:
            src = Path(td) / "src.prefab"
            dst = Path(td) / "dst.prefab"
            src.write_text(src_text, encoding="utf-8")
            dst.write_text(dst_text, encoding="utf-8")

            _, result = _run(server.call_tool(
                "copy_component_fields",
                {
                    "src_asset_path": str(src),
                    "src_symbol_path": "Empty/MeshRenderer",
                    "dst_asset_path": str(dst),
                    "dst_symbol_path": "Dst/MeshRenderer",
                },
            ))

        self.assertFalse(result["success"])
        self.assertEqual("NO_FIELDS_TO_COPY", result["code"])
        self.assertIn("src_asset_path", result["data"])
        self.assertIn("src_symbol_path", result["data"])

    def test_copy_monobehaviour_unresolvable(self) -> None:
        """SYMBOL_UNRESOLVABLE when MonoBehaviour has no resolved script name."""
        src_text = self._monobehaviour_prefab()
        dst_text = self._monobehaviour_prefab()
        server = create_server()

        with tempfile.TemporaryDirectory() as td:
            src = Path(td) / "src.prefab"
            dst = Path(td) / "dst.prefab"
            src.write_text(src_text, encoding="utf-8")
            dst.write_text(dst_text, encoding="utf-8")

            _, result = _run(server.call_tool(
                "copy_component_fields",
                {
                    "src_asset_path": str(src),
                    "src_symbol_path": "Player/MonoBehaviour",
                    "dst_asset_path": str(dst),
                    "dst_symbol_path": "Player/MonoBehaviour",
                },
            ))

        self.assertFalse(result["success"])
        self.assertEqual("SYMBOL_UNRESOLVABLE", result["code"])
        self.assertIn("asset_path", result["data"])
        self.assertIn("symbol_path", result["data"])

    def test_confirm_requires_change_reason(self) -> None:
        server = create_server()

        _, result = _run(server.call_tool(
            "copy_component_fields",
            {
                "src_asset_path": "Assets/DoesNotExist.prefab",
                "src_symbol_path": "Cube/MeshRenderer",
                "dst_asset_path": "Assets/DoesNotExist2.prefab",
                "dst_symbol_path": "Cube/MeshRenderer",
                "confirm": True,
                "change_reason": "",
            },
        ))

        self.assertFalse(result["success"])
        self.assertEqual("CHANGE_REASON_REQUIRED", result["code"])


class TestSetComponentFieldsTool(unittest.TestCase):
    """Test the set_component_fields MCP tool."""

    def _meshrenderer_prefab(self, go_name: str = "Cube") -> str:
        return _make_simple_meshrenderer_prefab(go_name)

    def _monobehaviour_prefab(
        self, guid: str = "aaaa1111bbbb2222cccc3333dddd4444",
    ) -> str:
        return _make_simple_monobehaviour_prefab(guid)

    def _double_meshrenderer_prefab(self) -> str:
        """Prefab with two MeshRenderers on the same GameObject."""
        return YAML_HEADER + "\n".join([
            make_gameobject("100", "Cube", ["200", "300", "400"]),
            make_transform("200", "100"),
            make_meshrenderer("300", "100"),
            make_meshrenderer("400", "100"),
        ])

    def _two_same_name_go_prefab(self) -> str:
        """Prefab with two root-level GameObjects named 'Cube'."""
        return YAML_HEADER + "\n".join([
            make_gameobject("100", "Cube", ["200", "300"]),
            make_transform("200", "100"),
            make_meshrenderer("300", "100"),
            make_gameobject("400", "Cube", ["500", "600"]),
            make_transform("500", "400"),
            make_meshrenderer("600", "400"),
        ])

    def _mock_patch_apply_response(self, dry_run: bool = True) -> MagicMock:
        resp = MagicMock()
        resp.success = True
        resp.to_dict.return_value = {
            "success": True,
            "severity": "info",
            "code": "PATCH_APPLY_RESULT",
            "message": (
                "patch.apply dry-run completed." if dry_run
                else "patch.apply completed."
            ),
            "data": {"dry_run": dry_run, "confirm": not dry_run, "read_only": dry_run},
            "diagnostics": [],
        }
        return resp

    def test_dry_run_multiple_fields(self) -> None:
        """Dry-run with 2 fields builds a 2-op plan and enriches symbol_resolution."""
        text = self._meshrenderer_prefab()
        server = create_server()
        mock_resp = self._mock_patch_apply_response(dry_run=True)

        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "test.prefab"
            p.write_text(text, encoding="utf-8")

            with patch(
                "prefab_sentinel.session_cache.Phase1Orchestrator",
            ) as mock_cls:
                mock_orch = MagicMock()
                mock_orch.patch_apply.return_value = mock_resp
                mock_cls.default.return_value = mock_orch

                _, result = _run(server.call_tool(
                    "set_component_fields",
                    {
                        "asset_path": str(p),
                        "symbol_path": "Cube",
                        "component": "MeshRenderer",
                        "fields": {"m_Enabled": 0, "m_CastShadows": 0},
                    },
                ))

        self.assertTrue(result["success"])
        call_kwargs = mock_orch.patch_apply.call_args[1]
        self.assertTrue(call_kwargs["dry_run"])
        self.assertFalse(call_kwargs["confirm"])
        plan = call_kwargs["plan"]
        op_paths = [op["path"] for op in plan["ops"]]
        self.assertIn("m_Enabled", op_paths)
        self.assertIn("m_CastShadows", op_paths)
        self.assertEqual(2, len(plan["ops"]))
        sr = result["symbol_resolution"]
        self.assertEqual("MeshRenderer", sr["resolved_component"])
        self.assertIn("m_Enabled", sr["fields"])
        self.assertIn("m_CastShadows", sr["fields"])

    def test_confirm_multiple_fields(self) -> None:
        """confirm=True applies the patch and includes auto_refresh in result."""
        text = self._meshrenderer_prefab()
        mock_resp = self._mock_patch_apply_response(dry_run=False)

        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "test.prefab"
            p.write_text(text, encoding="utf-8")

            with patch(
                "prefab_sentinel.session_cache.Phase1Orchestrator",
            ) as mock_cls:
                mock_orch = MagicMock()
                mock_orch.patch_apply.return_value = mock_resp
                mock_orch.maybe_auto_refresh.return_value = "done"
                mock_cls.default.return_value = mock_orch

                report_path = Path(td) / "report.json"
                server = create_server(project_root=td)
                _, result = _run(server.call_tool(
                    "set_component_fields",
                    {
                        "asset_path": str(p),
                        "symbol_path": "Cube",
                        "component": "MeshRenderer",
                        "fields": {"m_Enabled": 0},
                        "confirm": True,
                        "change_reason": "disable mesh renderer",
                        "out_report": str(report_path),
                    },
                ))

        self.assertTrue(result["success"])
        call_kwargs = mock_orch.patch_apply.call_args[1]
        self.assertFalse(call_kwargs["dry_run"])
        self.assertTrue(call_kwargs["confirm"])
        self.assertIn("auto_refresh", result)

    def test_monobehaviour_component(self) -> None:
        """Resolves MonoBehaviour by script_name; resolved_component is the script name."""
        text = self._monobehaviour_prefab()
        mock_resp = self._mock_patch_apply_response(dry_run=True)

        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "test.prefab"
            p.write_text(text, encoding="utf-8")

            script_dir = Path(td) / "Assets" / "Scripts"
            script_dir.mkdir(parents=True)
            cs_file = script_dir / "PlayerScript.cs"
            cs_file.write_text(
                "using UnityEngine;\npublic class PlayerScript : MonoBehaviour {}\n",
                encoding="utf-8",
            )
            meta_file = script_dir / "PlayerScript.cs.meta"
            meta_file.write_text(
                "fileFormatVersion: 2\nguid: aaaa1111bbbb2222cccc3333dddd4444\n",
                encoding="utf-8",
            )

            with patch(
                "prefab_sentinel.session_cache.Phase1Orchestrator",
            ) as mock_cls:
                mock_orch = MagicMock()
                mock_orch.patch_apply.return_value = mock_resp
                mock_cls.default.return_value = mock_orch

                server = create_server(project_root=td)
                _, result = _run(server.call_tool(
                    "set_component_fields",
                    {
                        "asset_path": str(p),
                        "symbol_path": "Player",
                        "component": "PlayerScript",
                        "fields": {"speed": 10},
                    },
                ))

        self.assertTrue(result["success"])
        sr = result["symbol_resolution"]
        self.assertEqual("PlayerScript", sr["resolved_component"])

    def test_reference_value_in_fields(self) -> None:
        """Reference dict values are passed through unchanged to the patch plan."""
        text = self._meshrenderer_prefab()
        server = create_server()
        mock_resp = self._mock_patch_apply_response(dry_run=True)
        ref_value = {"fileID": 2100000, "guid": "aabbccdd11223344aabbccdd11223344", "type": 2}

        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "test.prefab"
            p.write_text(text, encoding="utf-8")

            with patch(
                "prefab_sentinel.session_cache.Phase1Orchestrator",
            ) as mock_cls:
                mock_orch = MagicMock()
                mock_orch.patch_apply.return_value = mock_resp
                mock_cls.default.return_value = mock_orch

                _, result = _run(server.call_tool(
                    "set_component_fields",
                    {
                        "asset_path": str(p),
                        "symbol_path": "Cube",
                        "component": "MeshRenderer",
                        "fields": {"m_Materials": ref_value},
                    },
                ))

        self.assertTrue(result["success"])
        plan = mock_orch.patch_apply.call_args[1]["plan"]
        self.assertEqual(ref_value, plan["ops"][0]["value"])

    def test_symbol_not_found(self) -> None:
        """SYMBOL_NOT_FOUND when symbol_path does not exist in the asset."""
        text = self._meshrenderer_prefab()
        server = create_server()

        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "test.prefab"
            p.write_text(text, encoding="utf-8")

            _, result = _run(server.call_tool(
                "set_component_fields",
                {
                    "asset_path": str(p),
                    "symbol_path": "NonExistent",
                    "component": "MeshRenderer",
                    "fields": {"m_Enabled": 0},
                },
            ))

        self.assertFalse(result["success"])
        self.assertEqual("SYMBOL_NOT_FOUND", result["code"])
        self.assertIn("suggestions", result["data"])

    def test_symbol_ambiguous(self) -> None:
        """SYMBOL_AMBIGUOUS when symbol_path matches multiple GameObjects."""
        text = self._two_same_name_go_prefab()
        server = create_server()

        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "test.prefab"
            p.write_text(text, encoding="utf-8")

            _, result = _run(server.call_tool(
                "set_component_fields",
                {
                    "asset_path": str(p),
                    "symbol_path": "Cube",
                    "component": "MeshRenderer",
                    "fields": {"m_Enabled": 0},
                },
            ))

        self.assertFalse(result["success"])
        self.assertEqual("SYMBOL_AMBIGUOUS", result["code"])

    def test_symbol_not_game_object(self) -> None:
        """SYMBOL_NOT_GAME_OBJECT when symbol_path resolves to a component."""
        text = self._meshrenderer_prefab()
        server = create_server()

        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "test.prefab"
            p.write_text(text, encoding="utf-8")

            _, result = _run(server.call_tool(
                "set_component_fields",
                {
                    "asset_path": str(p),
                    "symbol_path": "Cube/MeshRenderer",
                    "component": "MeshRenderer",
                    "fields": {"m_Enabled": 0},
                },
            ))

        self.assertFalse(result["success"])
        self.assertEqual("SYMBOL_NOT_GAME_OBJECT", result["code"])

    def test_component_not_found(self) -> None:
        """COMPONENT_NOT_FOUND when named component is absent from the GameObject."""
        text = self._meshrenderer_prefab()
        server = create_server()

        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "test.prefab"
            p.write_text(text, encoding="utf-8")

            _, result = _run(server.call_tool(
                "set_component_fields",
                {
                    "asset_path": str(p),
                    "symbol_path": "Cube",
                    "component": "BoxCollider",
                    "fields": {"m_IsTrigger": True},
                },
            ))

        self.assertFalse(result["success"])
        self.assertEqual("COMPONENT_NOT_FOUND", result["code"])
        self.assertIn("available_components", result["data"])

    def test_component_ambiguous(self) -> None:
        """COMPONENT_AMBIGUOUS when multiple components of same type exist on the GO."""
        text = self._double_meshrenderer_prefab()
        server = create_server()

        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "test.prefab"
            p.write_text(text, encoding="utf-8")

            _, result = _run(server.call_tool(
                "set_component_fields",
                {
                    "asset_path": str(p),
                    "symbol_path": "Cube",
                    "component": "MeshRenderer",
                    "fields": {"m_Enabled": 0},
                },
            ))

        self.assertFalse(result["success"])
        self.assertEqual("COMPONENT_AMBIGUOUS", result["code"])
        self.assertIn("available_components", result["data"])

    def test_empty_fields(self) -> None:
        """EMPTY_FIELDS error returned before any file I/O when fields dict is empty."""
        server = create_server()

        _, result = _run(server.call_tool(
            "set_component_fields",
            {
                "asset_path": "Assets/DoesNotExist.prefab",
                "symbol_path": "Cube",
                "component": "MeshRenderer",
                "fields": {},
            },
        ))

        self.assertFalse(result["success"])
        self.assertEqual("EMPTY_FIELDS", result["code"])

    def test_confirm_requires_change_reason(self) -> None:
        """CHANGE_REASON_REQUIRED when confirm=True without change_reason.

        Uses non-existent asset path to guarantee no file I/O occurs before validation.
        """
        server = create_server()

        _, result = _run(server.call_tool(
            "set_component_fields",
            {
                "asset_path": "Assets/DoesNotExist.prefab",
                "symbol_path": "Cube",
                "component": "MeshRenderer",
                "fields": {"m_Enabled": 0},
                "confirm": True,
            },
        ))

        self.assertFalse(result["success"])
        self.assertEqual("CHANGE_REASON_REQUIRED", result["code"])

    def test_confirm_requires_out_report(self) -> None:
        """OUT_REPORT_REQUIRED when confirm=True with change_reason but no out_report.

        Uses non-existent asset path to guarantee no file I/O occurs before validation.
        """
        server = create_server()

        _, result = _run(server.call_tool(
            "set_component_fields",
            {
                "asset_path": "Assets/DoesNotExist.prefab",
                "symbol_path": "Cube",
                "component": "MeshRenderer",
                "fields": {"m_Enabled": 0},
                "confirm": True,
                "change_reason": "test reason",
            },
        ))

        self.assertFalse(result["success"])
        self.assertEqual("OUT_REPORT_REQUIRED", result["code"])

    def test_out_report_outside_project_rejected(self) -> None:
        """OUT_REPORT_OUTSIDE_PROJECT when out_report resolves outside project_root.

        Uses non-existent asset path to guarantee no file I/O occurs before validation.
        """
        with tempfile.TemporaryDirectory() as td:
            server = create_server(project_root=td)
            _, result = _run(server.call_tool(
                "set_component_fields",
                {
                    "asset_path": "Assets/DoesNotExist.prefab",
                    "symbol_path": "Cube",
                    "component": "MeshRenderer",
                    "fields": {"m_Enabled": 0},
                    "confirm": True,
                    "change_reason": "test reason",
                    "out_report": "/tmp/outside_project.json",
                },
            ))

        self.assertFalse(result["success"])
        self.assertEqual("OUT_REPORT_OUTSIDE_PROJECT", result["code"])
        self.assertFalse(Path("/tmp/outside_project.json").exists())

    def test_out_report_rejected_when_no_project_root(self) -> None:
        """PROJECT_ROOT_REQUIRED when out_report is supplied but session has no project_root.

        Without project_root the containment boundary is unavailable; write must be
        rejected (fail-safe) rather than silently unconstrained (fail-open).
        """
        with tempfile.TemporaryDirectory() as td:
            out_path = Path(td) / "report.json"
            server = create_server()  # no project_root
            _, result = _run(server.call_tool(
                "set_component_fields",
                {
                    "asset_path": "Assets/DoesNotExist.prefab",
                    "symbol_path": "Cube",
                    "component": "MeshRenderer",
                    "fields": {"m_Enabled": 0},
                    "confirm": True,
                    "change_reason": "test reason",
                    "out_report": str(out_path),
                },
            ))
            self.assertFalse(result["success"])
            self.assertEqual("PROJECT_ROOT_REQUIRED", result["code"])
            self.assertFalse(out_path.exists())

    def test_dry_run_explicit_parameter(self) -> None:
        """dry_run=True passes dry_run=True, confirm=False to orch.patch_apply."""
        text = self._meshrenderer_prefab()
        mock_resp = self._mock_patch_apply_response(dry_run=True)

        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "test.prefab"
            p.write_text(text, encoding="utf-8")

            with patch(
                "prefab_sentinel.session_cache.Phase1Orchestrator",
            ) as mock_cls:
                mock_orch = MagicMock()
                mock_orch.patch_apply.return_value = mock_resp
                mock_cls.default.return_value = mock_orch

                server = create_server(project_root=td)
                _, result = _run(server.call_tool(
                    "set_component_fields",
                    {
                        "asset_path": str(p),
                        "symbol_path": "Cube",
                        "component": "MeshRenderer",
                        "fields": {"m_Enabled": 0},
                        "dry_run": True,
                    },
                ))

        self.assertTrue(result["success"])
        call_kwargs = mock_orch.patch_apply.call_args[1]
        self.assertTrue(call_kwargs["dry_run"])
        self.assertFalse(call_kwargs["confirm"])

    def test_dry_run_overrides_confirm(self) -> None:
        """dry_run=True wins over confirm=True: no validation error, dry_run passed to orch."""
        text = self._meshrenderer_prefab()
        mock_resp = self._mock_patch_apply_response(dry_run=True)

        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "test.prefab"
            p.write_text(text, encoding="utf-8")

            with patch(
                "prefab_sentinel.session_cache.Phase1Orchestrator",
            ) as mock_cls:
                mock_orch = MagicMock()
                mock_orch.patch_apply.return_value = mock_resp
                mock_cls.default.return_value = mock_orch

                server = create_server(project_root=td)
                _, result = _run(server.call_tool(
                    "set_component_fields",
                    {
                        "asset_path": str(p),
                        "symbol_path": "Cube",
                        "component": "MeshRenderer",
                        "fields": {"m_Enabled": 0},
                        "dry_run": True,
                        "confirm": True,
                    },
                ))

        self.assertTrue(result["success"])
        call_kwargs = mock_orch.patch_apply.call_args[1]
        self.assertTrue(call_kwargs["dry_run"])
        self.assertFalse(call_kwargs["confirm"])

    def test_empty_change_reason_normalized_to_none(self) -> None:
        """change_reason="" is normalized to None before reaching orch.patch_apply."""
        text = self._meshrenderer_prefab()
        mock_resp = self._mock_patch_apply_response(dry_run=True)

        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "test.prefab"
            p.write_text(text, encoding="utf-8")

            with patch(
                "prefab_sentinel.session_cache.Phase1Orchestrator",
            ) as mock_cls:
                mock_orch = MagicMock()
                mock_orch.patch_apply.return_value = mock_resp
                mock_cls.default.return_value = mock_orch

                server = create_server(project_root=td)
                _, result = _run(server.call_tool(
                    "set_component_fields",
                    {
                        "asset_path": str(p),
                        "symbol_path": "Cube",
                        "component": "MeshRenderer",
                        "fields": {"m_Enabled": 0},
                        "dry_run": True,
                        "change_reason": "",
                    },
                ))

        self.assertTrue(result["success"])
        call_kwargs = mock_orch.patch_apply.call_args[1]
        self.assertIsNone(call_kwargs["change_reason"])

    def test_confirm_writes_out_report(self) -> None:
        """confirm=True with change_reason + out_report writes result JSON to out_report path."""
        text = self._meshrenderer_prefab()
        mock_resp = self._mock_patch_apply_response(dry_run=False)

        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "test.prefab"
            p.write_text(text, encoding="utf-8")
            report_path = Path(td) / "report.json"

            with patch(
                "prefab_sentinel.session_cache.Phase1Orchestrator",
            ) as mock_cls:
                mock_orch = MagicMock()
                mock_orch.patch_apply.return_value = mock_resp
                mock_orch.maybe_auto_refresh.return_value = "done"
                mock_cls.default.return_value = mock_orch

                server = create_server(project_root=td)
                _, result = _run(server.call_tool(
                    "set_component_fields",
                    {
                        "asset_path": str(p),
                        "symbol_path": "Cube",
                        "component": "MeshRenderer",
                        "fields": {"m_Enabled": 0},
                        "confirm": True,
                        "change_reason": "test write report",
                        "out_report": str(report_path),
                    },
                ))

                self.assertTrue(result["success"])
                self.assertTrue(report_path.exists(), "out_report file should be written")
                written = json.loads(report_path.read_text(encoding="utf-8"))
                self.assertEqual(result, written)

    def test_value_coercion_passthrough(self) -> None:
        """Mixed field types (int, float, str, dict) are passed through unchanged to plan ops."""
        text = self._meshrenderer_prefab()
        mock_resp = self._mock_patch_apply_response(dry_run=True)
        ref_value = {"fileID": 100, "guid": "abc", "type": 2}

        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "test.prefab"
            p.write_text(text, encoding="utf-8")

            with patch(
                "prefab_sentinel.session_cache.Phase1Orchestrator",
            ) as mock_cls:
                mock_orch = MagicMock()
                mock_orch.patch_apply.return_value = mock_resp
                mock_cls.default.return_value = mock_orch

                server = create_server(project_root=td)
                _, result = _run(server.call_tool(
                    "set_component_fields",
                    {
                        "asset_path": str(p),
                        "symbol_path": "Cube",
                        "component": "MeshRenderer",
                        "fields": {
                            "int_field": 0,
                            "float_field": 3.14,
                            "str_field": "hello",
                            "ref_field": ref_value,
                        },
                    },
                ))

        self.assertTrue(result["success"])
        plan = mock_orch.patch_apply.call_args[1]["plan"]
        ops_by_path = {op["path"]: op["value"] for op in plan["ops"]}
        self.assertEqual(0, ops_by_path["int_field"])
        self.assertAlmostEqual(3.14, ops_by_path["float_field"])
        self.assertEqual("hello", ops_by_path["str_field"])
        self.assertEqual(ref_value, ops_by_path["ref_field"])

    def test_component_unresolvable_no_project_root(self) -> None:
        """SYMBOL_UNRESOLVABLE when MonoBehaviour matches by node name but has no script name.

        Without a project root the symbol tree cannot resolve the script GUID to a class
        name. The node is named "MonoBehaviour(guid:<prefix>)" and can be matched by that
        name, but _resolve_component_name raises ValueError because script_name is empty.
        """
        guid = "aaaa1111bbbb2222cccc3333dddd4444"
        text = self._monobehaviour_prefab(guid=guid)
        server = create_server()  # no project_root

        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "test.prefab"
            p.write_text(text, encoding="utf-8")

            component_name = f"MonoBehaviour(guid:{guid[:8]})"
            _, result = _run(server.call_tool(
                "set_component_fields",
                {
                    "asset_path": str(p),
                    "symbol_path": "Player",
                    "component": component_name,
                    "fields": {"speed": 10},
                },
            ))

        self.assertFalse(result["success"])
        self.assertEqual("SYMBOL_UNRESOLVABLE", result["code"])
        self.assertIn("asset_path", result["data"])
        self.assertIn("component", result["data"])


class TestEditorSetComponentFieldsTool(unittest.TestCase):
    """Test the editor_set_component_fields MCP tool."""

    def test_fields_with_values(self) -> None:
        """Primitive value fields are delegated to editor_batch_set_property."""
        server = create_server()
        with patch(
            "prefab_sentinel.mcp_tools_editor_ops.send_action",
            return_value={"success": True, "data": {}},
        ) as mock_send:
            _run(server.call_tool(
                "editor_set_component_fields",
                {
                    "hierarchy_path": "/Foo/Bar",
                    "component_type": "MyComponent",
                    "fields": [
                        {"name": "speed", "value": "60"},
                        {"name": "health", "value": "100"},
                    ],
                },
            ))

        mock_send.assert_called_once()
        args = mock_send.call_args
        self.assertEqual("editor_batch_set_property", args.kwargs["action"])
        ops = json.loads(args.kwargs["batch_operations_json"])
        self.assertEqual(2, len(ops))
        self.assertEqual("/Foo/Bar", ops[0]["hierarchy_path"])
        self.assertEqual("MyComponent", ops[0]["component_type"])
        self.assertEqual("speed", ops[0]["property_name"])
        self.assertEqual("60", ops[0]["value"])
        self.assertNotIn("object_reference", ops[0])

    def test_fields_with_object_reference(self) -> None:
        """Object reference fields are delegated with object_reference key."""
        server = create_server()
        with patch(
            "prefab_sentinel.mcp_tools_editor_ops.send_action",
            return_value={"success": True, "data": {}},
        ) as mock_send:
            _run(server.call_tool(
                "editor_set_component_fields",
                {
                    "hierarchy_path": "/Obj",
                    "component_type": "Controller",
                    "fields": [{"name": "target", "object_reference": "/SomeTarget"}],
                },
            ))

        ops = json.loads(mock_send.call_args.kwargs["batch_operations_json"])
        self.assertEqual(1, len(ops))
        self.assertEqual("target", ops[0]["property_name"])
        self.assertEqual("/SomeTarget", ops[0]["object_reference"])
        self.assertNotIn("value", ops[0])

    def test_mixed_fields(self) -> None:
        """Mix of value and object_reference fields are both mapped correctly."""
        server = create_server()
        with patch(
            "prefab_sentinel.mcp_tools_editor_ops.send_action",
            return_value={"success": True, "data": {}},
        ) as mock_send:
            _run(server.call_tool(
                "editor_set_component_fields",
                {
                    "hierarchy_path": "/Ctrl",
                    "component_type": "DualCtrl",
                    "fields": [
                        {"name": "speed", "value": "10"},
                        {"name": "target", "object_reference": "/Target"},
                    ],
                },
            ))

        ops = json.loads(mock_send.call_args.kwargs["batch_operations_json"])
        self.assertEqual(2, len(ops))
        self.assertIn("value", ops[0])
        self.assertNotIn("object_reference", ops[0])
        self.assertIn("object_reference", ops[1])
        self.assertNotIn("value", ops[1])

    def test_empty_fields(self) -> None:
        """EDITOR_SET_COMP_EMPTY_FIELDS returned for empty fields list."""
        server = create_server()
        with patch("prefab_sentinel.mcp_tools_editor_ops.send_action") as mock_send:
            _, result = _run(server.call_tool(
                "editor_set_component_fields",
                {
                    "hierarchy_path": "/Obj",
                    "component_type": "MyComp",
                    "fields": [],
                },
            ))
            mock_send.assert_not_called()

        self.assertFalse(result["success"])
        self.assertEqual("EDITOR_SET_COMP_EMPTY_FIELDS", result["code"])

    def test_field_missing_name(self) -> None:
        """EDITOR_SET_COMP_INVALID_FIELD when a field dict has no 'name' key."""
        server = create_server()
        with patch("prefab_sentinel.mcp_tools_editor_ops.send_action") as mock_send:
            _, result = _run(server.call_tool(
                "editor_set_component_fields",
                {
                    "hierarchy_path": "/Obj",
                    "component_type": "MyComp",
                    "fields": [{"value": "60"}],
                },
            ))
            mock_send.assert_not_called()

        self.assertFalse(result["success"])
        self.assertEqual("EDITOR_SET_COMP_INVALID_FIELD", result["code"])

    def test_field_missing_value_and_reference(self) -> None:
        """EDITOR_SET_COMP_INVALID_FIELD when field has name but no value/object_reference."""
        server = create_server()
        with patch("prefab_sentinel.mcp_tools_editor_ops.send_action") as mock_send:
            _, result = _run(server.call_tool(
                "editor_set_component_fields",
                {
                    "hierarchy_path": "/Obj",
                    "component_type": "MyComp",
                    "fields": [{"name": "foo"}],
                },
            ))
            mock_send.assert_not_called()

        self.assertFalse(result["success"])
        self.assertEqual("EDITOR_SET_COMP_INVALID_FIELD", result["code"])

    def test_field_has_both_value_and_object_reference(self) -> None:
        """EDITOR_SET_COMP_INVALID_FIELD when field supplies both value and object_reference."""
        server = create_server()
        with patch("prefab_sentinel.mcp_tools_editor_ops.send_action") as mock_send:
            _, result = _run(server.call_tool(
                "editor_set_component_fields",
                {
                    "hierarchy_path": "/Obj",
                    "component_type": "MyComp",
                    "fields": [
                        {"name": "target", "value": "1", "object_reference": "/Other"},
                    ],
                },
            ))
            mock_send.assert_not_called()

        self.assertFalse(result["success"])
        self.assertEqual("EDITOR_SET_COMP_INVALID_FIELD", result["code"])
        self.assertIn("not both", result["message"])


class TestSetComponentFieldsIntegration(unittest.TestCase):
    """Integration tests for set_component_fields without orchestrator mocking."""

    def _meshrenderer_prefab(self) -> str:
        return _make_simple_meshrenderer_prefab()

    def test_e2e_dry_run_with_prefab_fixture(self) -> None:
        """E2E dry-run with a real .prefab fixture (no mock): result envelope is correct."""
        text = self._meshrenderer_prefab()

        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "fixture.prefab"
            p.write_text(text, encoding="utf-8")

            server = create_server(project_root=td)
            _, result = _run(server.call_tool(
                "set_component_fields",
                {
                    "asset_path": str(p),
                    "symbol_path": "Cube",
                    "component": "MeshRenderer",
                    "fields": {"m_Enabled": 0},
                    "dry_run": True,
                },
            ))

        self.assertTrue(result["success"])
        self.assertTrue(result["data"]["dry_run"])
        self.assertGreater(len(result["data"]["steps"]), 0)
        self.assertIn("symbol_resolution", result)

    @unittest.skipUnless(
        os.environ.get("UNITYTOOL_PATCH_BRIDGE"),
        "requires patch bridge (UNITYTOOL_PATCH_BRIDGE must be set)",
    )
    def test_e2e_confirm_roundtrip(self) -> None:
        """E2E confirm with real .prefab fixture: report file written with valid JSON."""
        text = self._meshrenderer_prefab()

        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "fixture.prefab"
            p.write_text(text, encoding="utf-8")
            report_path = Path(td) / "report.json"

            server = create_server(project_root=td)
            _, result = _run(server.call_tool(
                "set_component_fields",
                {
                    "asset_path": str(p),
                    "symbol_path": "Cube",
                    "component": "MeshRenderer",
                    "fields": {"m_Enabled": 0},
                    "confirm": True,
                    "change_reason": "integration test roundtrip",
                    "out_report": str(report_path),
                },
            ))

            self.assertTrue(result["success"])
            self.assertTrue(report_path.exists(), "out_report file should be written")
            written = json.loads(report_path.read_text(encoding="utf-8"))
            self.assertEqual(result, written)


class TestEditorSetComponentFieldsIntegration(unittest.TestCase):
    """Integration tests for editor_set_component_fields with live Editor bridge."""

    @unittest.skipUnless(
        os.environ.get("UNITYTOOL_BRIDGE_MODE") == "editor",
        "requires editor bridge (UNITYTOOL_BRIDGE_MODE=editor must be set)",
    )
    def test_e2e_editor_bridge(self) -> None:
        """E2E with live Editor bridge: sets fields and returns success envelope."""
        server = create_server()
        _, result = _run(server.call_tool(
            "editor_set_component_fields",
            {
                "hierarchy_path": "/DualButtonController/Controller",
                "component_type": "DualButtonController",
                "fields": [{"name": "clearDelaySeconds", "value": "60"}],
            },
        ))

        self.assertTrue(result["success"])
        self.assertIn("data", result)


class TestEditorSaveAsPrefab(unittest.TestCase):
    """Tests for editor_save_as_prefab force_original parameter."""

    def test_force_original_true_passed_to_send_action(self) -> None:
        server = create_server()
        with patch(
            "prefab_sentinel.mcp_tools_editor_ops.send_action",
            return_value={"success": True, "data": {}},
        ) as mock_send:
            _run(server.call_tool(
                "editor_save_as_prefab",
                {"hierarchy_path": "/Obj", "asset_path": "Assets/X.prefab", "force_original": True},
            ))
        mock_send.assert_called_once()
        self.assertTrue(mock_send.call_args.kwargs.get("force_original"))

    def test_force_original_default_omits_key(self) -> None:
        server = create_server()
        with patch(
            "prefab_sentinel.mcp_tools_editor_ops.send_action",
            return_value={"success": True, "data": {}},
        ) as mock_send:
            _run(server.call_tool(
                "editor_save_as_prefab",
                {"hierarchy_path": "/Obj", "asset_path": "Assets/X.prefab"},
            ))
        mock_send.assert_called_once()
        self.assertNotIn("force_original", mock_send.call_args.kwargs)


class TestEditorBatchCreateComponents(unittest.TestCase):
    """I3: editor_batch_create serializes components list in JSON payload."""

    def test_editor_batch_create_components_serialized(self) -> None:
        server = create_server()
        with patch("prefab_sentinel.mcp_tools_editor_batch.send_action", return_value={"success": True}) as mock_send:
            _run(server.call_tool(
                "editor_batch_create",
                {"objects": [{"name": "Box", "components": ["BoxCollider"]}]},
            ))
        mock_send.assert_called_once()
        call_kwargs = mock_send.call_args.kwargs
        parsed = json.loads(call_kwargs["batch_objects_json"])
        self.assertEqual(parsed, [{"name": "Box", "components": ["BoxCollider"]}])


if __name__ == "__main__":
    unittest.main()
