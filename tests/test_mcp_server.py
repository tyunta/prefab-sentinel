"""Tests for MCP server tool registration and invocation."""

from __future__ import annotations

import asyncio
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

from prefab_sentinel.mcp_server import create_server
from prefab_sentinel.session import ProjectSession
from tests.yaml_helpers import (
    YAML_HEADER,
    make_gameobject,
    make_meshrenderer,
    make_monobehaviour,
    make_transform,
)


def _run(coro: Any) -> Any:
    """Run an async coroutine synchronously."""
    return asyncio.run(coro)


def _simple_prefab() -> str:
    """Build a minimal synthetic prefab with one GO + Transform + MeshRenderer."""
    return YAML_HEADER + "\n".join([
        make_gameobject("100", "Cube", ["200", "300"]),
        make_transform("200", "100"),
        make_meshrenderer("300", "100"),
    ])


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
            # New 18 tools
            "editor_screenshot", "editor_select", "editor_frame",
            "editor_get_camera", "editor_set_camera",
            "editor_refresh", "editor_recompile", "editor_instantiate",
            "editor_set_material", "editor_delete",
            "editor_list_children", "editor_list_materials", "editor_list_roots",
            "editor_get_material_property", "editor_console", "editor_run_tests",
            "inspect_materials", "inspect_material_asset",
            "validate_structure", "revert_overrides",
            # Phase 2: AI workflow tools
            "inspect_hierarchy", "validate_runtime", "patch_apply",
        }
        self.assertEqual(expected, tool_names)

    def test_tool_count(self) -> None:
        server = create_server()
        tools = _run(server.list_tools())
        self.assertEqual(38, len(tools))


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
        import tempfile

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
        import tempfile

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
        import tempfile

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
        import tempfile

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
        import tempfile

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


class TestSymbolToolsWithMonoBehaviour(unittest.TestCase):
    """Test symbol tools with MonoBehaviour components."""

    def test_find_monobehaviour_with_script_name(self) -> None:
        import tempfile

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
            "prefab_sentinel.session.Phase1Orchestrator"
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
            "prefab_sentinel.session.Phase1Orchestrator"
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
        from prefab_sentinel.contracts import Severity, ToolResponse
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
            "prefab_sentinel.session.Phase1Orchestrator"
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
            "prefab_sentinel.session.Phase1Orchestrator"
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
            "prefab_sentinel.session.Phase1Orchestrator"
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
            "prefab_sentinel.session.Phase1Orchestrator"
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
        import tempfile

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
                    "include_properties": True,
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
        import tempfile

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
                "prefab_sentinel.session.Phase1Orchestrator"
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
        import tempfile

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
                "prefab_sentinel.session.Phase1Orchestrator"
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
        import tempfile

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
                "prefab_sentinel.session.Phase1Orchestrator"
            ) as mock_cls:
                mock_orch = MagicMock()
                mock_orch.prefab_variant.resolve_chain_values_with_origin.side_effect = RuntimeError("test")
                mock_cls.default.return_value = mock_orch

                with self.assertLogs("prefab_sentinel.mcp_server", level="DEBUG") as cm:
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
        import tempfile

        text = self._prefab_with_meshrenderer()
        server = create_server()
        mock_resp = self._mock_patch_apply_response(dry_run=True)

        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "test.prefab"
            p.write_text(text, encoding="utf-8")

            with patch(
                "prefab_sentinel.session.Phase1Orchestrator"
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
        import tempfile

        text = self._prefab_with_meshrenderer()
        server = create_server()
        mock_resp = self._mock_patch_apply_response(dry_run=False)

        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "test.prefab"
            p.write_text(text, encoding="utf-8")

            with patch(
                "prefab_sentinel.session.Phase1Orchestrator"
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
                    },
                ))

        self.assertTrue(result["success"])
        call_kwargs = mock_orch.patch_apply.call_args[1]
        self.assertFalse(call_kwargs["dry_run"])
        self.assertTrue(call_kwargs["confirm"])

    def test_set_property_symbol_not_found(self) -> None:
        """Returns error when symbol path doesn't resolve."""
        import tempfile

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

    def test_set_property_not_a_component(self) -> None:
        """Returns error when symbol path points to a GameObject."""
        import tempfile

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
        import tempfile

        text = self._prefab_with_meshrenderer()
        server = create_server()
        mock_resp = self._mock_patch_apply_response()

        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "test.prefab"
            p.write_text(text, encoding="utf-8")

            with patch(
                "prefab_sentinel.session.Phase1Orchestrator"
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
        import tempfile

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
                "prefab_sentinel.session.Phase1Orchestrator"
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
        import tempfile

        text = self._prefab_with_monobehaviour()
        server = create_server()  # No project_root → no script map

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
        import tempfile

        text = self._prefab_with_meshrenderer()
        server = create_server()
        mock_resp = self._mock_patch_apply_response()

        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "test.prefab"
            p.write_text(text, encoding="utf-8")

            with patch(
                "prefab_sentinel.session.Phase1Orchestrator"
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
        import tempfile

        text = self._prefab_with_meshrenderer()
        server = create_server()
        mock_resp = self._mock_patch_apply_response()

        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "test.prefab"
            p.write_text(text, encoding="utf-8")

            with patch(
                "prefab_sentinel.session.Phase1Orchestrator"
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
        import tempfile

        text = self._prefab_with_meshrenderer()
        server = create_server()
        mock_resp = self._mock_patch_apply_response()

        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "test.prefab"
            p.write_text(text, encoding="utf-8")

            with patch(
                "prefab_sentinel.session.Phase1Orchestrator"
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


class TestListSerializedFieldsTool(unittest.TestCase):
    """Tests for the list_serialized_fields MCP tool."""

    def test_delegates_to_orchestrator(self) -> None:
        from prefab_sentinel.contracts import Severity, ToolResponse

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
        with patch("prefab_sentinel.session.Phase1Orchestrator") as mock_orch_cls:
            mock_orch_cls.default.return_value.list_serialized_fields.return_value = mock_resp
            _, result = _run(server.call_tool(
                "list_serialized_fields",
                {"script_or_guid": "aabb"},
            ))

        self.assertTrue(result["success"])
        self.assertEqual("CSF_LIST_OK", result["code"])
        self.assertEqual(2, result["data"]["field_count"])

    def test_error_propagated(self) -> None:
        from prefab_sentinel.contracts import Severity, ToolResponse

        server = create_server()
        mock_resp = ToolResponse(
            success=False,
            severity=Severity.ERROR,
            code="CSF_RESOLVE_FAILED",
            message="Script not found.",
            data={"script": "missing.cs"},
            diagnostics=[],
        )
        with patch("prefab_sentinel.session.Phase1Orchestrator") as mock_orch_cls:
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
        from prefab_sentinel.contracts import Severity, ToolResponse

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
        with patch("prefab_sentinel.session.Phase1Orchestrator") as mock_orch_cls:
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
        from prefab_sentinel.contracts import Severity, ToolResponse

        server = create_server()
        mock_resp = ToolResponse(
            success=True, severity=Severity.INFO, code="CSF_RENAME_OK",
            message="ok", data={"affected_count": 0, "read_only": True},
            diagnostics=[],
        )
        with patch("prefab_sentinel.session.Phase1Orchestrator") as mock_orch_cls:
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
        from prefab_sentinel.contracts import Severity, ToolResponse

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
        with patch("prefab_sentinel.session.Phase1Orchestrator") as mock_orch_cls:
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

    @patch("prefab_sentinel.session.build_script_name_map")
    @patch("prefab_sentinel.session.Phase1Orchestrator")
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

    @patch("prefab_sentinel.session.build_script_name_map")
    @patch("prefab_sentinel.session.Phase1Orchestrator")
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
        import tempfile

        text = self._prefab_with_child()
        server = create_server()
        mock_resp = self._mock_patch_apply_response(dry_run=True)

        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "test.prefab"
            p.write_text(text, encoding="utf-8")

            with patch(
                "prefab_sentinel.session.Phase1Orchestrator"
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
        import tempfile

        text = self._prefab_with_child()
        server = create_server()
        mock_resp = self._mock_patch_apply_response(dry_run=True)

        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "test.prefab"
            p.write_text(text, encoding="utf-8")

            with patch(
                "prefab_sentinel.session.Phase1Orchestrator"
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
        import tempfile

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

    def test_add_component_rejects_component_path(self) -> None:
        """add_component requires a game_object, not a component."""
        import tempfile

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
        import tempfile

        text = self._prefab_with_child()
        server = create_server()
        mock_resp = self._mock_patch_apply_response(dry_run=False)

        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "test.prefab"
            p.write_text(text, encoding="utf-8")

            with patch(
                "prefab_sentinel.session.Phase1Orchestrator"
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
                    },
                ))

        self.assertTrue(result["success"])
        call_kwargs = mock_orch.patch_apply.call_args[1]
        self.assertFalse(call_kwargs["dry_run"])
        self.assertTrue(call_kwargs["confirm"])

    def test_add_component_symbol_resolution_metadata(self) -> None:
        import tempfile

        text = self._prefab_with_child()
        server = create_server()
        mock_resp = self._mock_patch_apply_response(dry_run=True)

        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "test.prefab"
            p.write_text(text, encoding="utf-8")

            with patch(
                "prefab_sentinel.session.Phase1Orchestrator"
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
        import tempfile

        text = self._prefab_with_meshrenderer()
        server = create_server()
        mock_resp = self._mock_patch_apply_response(dry_run=True)

        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "test.prefab"
            p.write_text(text, encoding="utf-8")

            with patch(
                "prefab_sentinel.session.Phase1Orchestrator"
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
        import tempfile

        text = self._prefab_with_meshrenderer()
        server = create_server()
        mock_resp = self._mock_patch_apply_response(dry_run=False)

        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "test.prefab"
            p.write_text(text, encoding="utf-8")

            with patch(
                "prefab_sentinel.session.Phase1Orchestrator"
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
                    },
                ))

        self.assertTrue(result["success"])
        call_kwargs = mock_orch.patch_apply.call_args[1]
        self.assertFalse(call_kwargs["dry_run"])
        self.assertTrue(call_kwargs["confirm"])

    def test_remove_component_symbol_not_found(self) -> None:
        import tempfile

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

    def test_remove_component_rejects_gameobject_path(self) -> None:
        """remove_component requires a component, not a game_object."""
        import tempfile

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
        import tempfile

        text = self._prefab_with_meshrenderer()
        server = create_server()
        mock_resp = self._mock_patch_apply_response(dry_run=True)

        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "test.prefab"
            p.write_text(text, encoding="utf-8")

            with patch(
                "prefab_sentinel.session.Phase1Orchestrator"
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


class TestScopeFallback(unittest.TestCase):
    """MCP tools use session scope when explicit scope is omitted."""

    def test_validate_refs_passes_resolved_scope(self) -> None:
        server = create_server()
        mock_resp = MagicMock()
        mock_resp.to_dict.return_value = {"success": True, "data": {}}
        with (
            patch("prefab_sentinel.session.Phase1Orchestrator") as mock_cls,
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
        from prefab_sentinel.contracts import Severity, ToolResponse
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
            patch("prefab_sentinel.session.Phase1Orchestrator") as mock_cls,
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
            patch("prefab_sentinel.session.Phase1Orchestrator") as mock_cls,
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
            patch("prefab_sentinel.session.Phase1Orchestrator") as mock_cls,
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
        from prefab_sentinel.contracts import Severity, ToolResponse

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
        with patch("prefab_sentinel.session.Phase1Orchestrator") as mock_cls:
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
        from prefab_sentinel.contracts import Severity, ToolResponse

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
        with patch("prefab_sentinel.session.Phase1Orchestrator") as mock_cls:
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

        from prefab_sentinel.contracts import Severity, ToolResponse

        server = create_server()
        mock_step = ToolResponse(
            success=False,
            severity=Severity.ERROR,
            code="REF_ERR",
            message="Scope not found",
            data={},
            diagnostics=[],
        )
        with patch("prefab_sentinel.session.Phase1Orchestrator") as mock_cls:
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
        with patch("prefab_sentinel.mcp_server.send_action", return_value=mock_response):
            _, result = _run(server.call_tool("editor_screenshot", {"view": "game", "width": 1920}))
        self.assertEqual(mock_response, result)

    def test_editor_screenshot_defaults(self) -> None:
        server = create_server()
        with patch("prefab_sentinel.mcp_server.send_action", return_value={"success": True}) as mock_send:
            _run(server.call_tool("editor_screenshot", {}))
        mock_send.assert_called_once_with(action="capture_screenshot", view="scene", width=0, height=0)

    def test_editor_select_delegates(self) -> None:
        server = create_server()
        with patch("prefab_sentinel.mcp_server.send_action", return_value={"success": True}) as mock_send:
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
        with patch("prefab_sentinel.mcp_server.send_action", return_value={"success": True}) as mock_send:
            _run(server.call_tool("editor_select", {"hierarchy_path": "/Root/Child"}))
        _, kwargs = mock_send.call_args
        self.assertNotIn("prefab_asset_path", kwargs)

    def test_editor_frame_delegates(self) -> None:
        server = create_server()
        with patch("prefab_sentinel.mcp_server.send_action", return_value={"success": True}) as mock_send:
            _run(server.call_tool("editor_frame", {"zoom": 2.5}))
        mock_send.assert_called_once_with(action="frame_selected", zoom=2.5)

    def test_editor_frame_defaults(self) -> None:
        server = create_server()
        with patch("prefab_sentinel.mcp_server.send_action", return_value={"success": True}) as mock_send:
            _run(server.call_tool("editor_frame", {}))
        mock_send.assert_called_once_with(action="frame_selected", zoom=0.0)

    def test_editor_camera_delegates(self) -> None:
        server = create_server()
        with patch("prefab_sentinel.mcp_server.send_action", return_value={"success": True}) as mock_send:
            _run(server.call_tool("editor_camera", {"yaw": 45.0, "pitch": 15.0, "distance": 3.0}))
        mock_send.assert_called_once_with(action="camera", yaw=45.0, pitch=15.0, distance=3.0)

    def test_editor_camera_defaults(self) -> None:
        server = create_server()
        with patch("prefab_sentinel.mcp_server.send_action", return_value={"success": True}) as mock_send:
            _run(server.call_tool("editor_camera", {}))
        mock_send.assert_called_once_with(action="camera", yaw=0.0, pitch=0.0, distance=0.0)

    def test_editor_list_children_delegates(self) -> None:
        server = create_server()
        mock_response = {"success": True, "data": {"children": ["A", "B"]}}
        with patch("prefab_sentinel.mcp_server.send_action", return_value=mock_response):
            _, result = _run(server.call_tool("editor_list_children", {
                "hierarchy_path": "/Root", "depth": 2,
            }))
        self.assertEqual(mock_response, result)

    def test_editor_list_children_default_depth(self) -> None:
        server = create_server()
        with patch("prefab_sentinel.mcp_server.send_action", return_value={"success": True}) as mock_send:
            _run(server.call_tool("editor_list_children", {"hierarchy_path": "/Root"}))
        mock_send.assert_called_once_with(action="list_children", hierarchy_path="/Root", depth=1)

    def test_editor_list_materials_delegates(self) -> None:
        server = create_server()
        with patch("prefab_sentinel.mcp_server.send_action", return_value={"success": True}) as mock_send:
            _run(server.call_tool("editor_list_materials", {"hierarchy_path": "/Body"}))
        mock_send.assert_called_once_with(action="list_materials", hierarchy_path="/Body")

    def test_editor_list_roots_delegates(self) -> None:
        server = create_server()
        with patch("prefab_sentinel.mcp_server.send_action", return_value={"success": True}) as mock_send:
            _run(server.call_tool("editor_list_roots", {}))
        mock_send.assert_called_once_with(action="list_roots")

    def test_editor_get_material_property_delegates(self) -> None:
        server = create_server()
        with patch("prefab_sentinel.mcp_server.send_action", return_value={"success": True}) as mock_send:
            _run(server.call_tool("editor_get_material_property", {
                "hierarchy_path": "/Body", "material_index": 0, "property_name": "_Color",
            }))
        mock_send.assert_called_once_with(
            action="get_material_property",
            hierarchy_path="/Body", material_index=0, property_name="_Color",
        )

    def test_editor_get_material_property_default_property_name(self) -> None:
        server = create_server()
        with patch("prefab_sentinel.mcp_server.send_action", return_value={"success": True}) as mock_send:
            _run(server.call_tool("editor_get_material_property", {
                "hierarchy_path": "/Body", "material_index": 0,
            }))
        mock_send.assert_called_once_with(
            action="get_material_property",
            hierarchy_path="/Body", material_index=0, property_name="",
        )

    def test_editor_console_delegates(self) -> None:
        server = create_server()
        with patch("prefab_sentinel.mcp_server.send_action", return_value={"success": True}) as mock_send:
            _run(server.call_tool("editor_console", {
                "max_entries": 50, "log_type_filter": "error", "since_seconds": 10.0,
            }))
        mock_send.assert_called_once_with(
            action="capture_console_logs",
            max_entries=50, log_type_filter="error", since_seconds=10.0,
        )

    def test_editor_console_defaults(self) -> None:
        server = create_server()
        with patch("prefab_sentinel.mcp_server.send_action", return_value={"success": True}) as mock_send:
            _run(server.call_tool("editor_console", {}))
        mock_send.assert_called_once_with(
            action="capture_console_logs",
            max_entries=200, log_type_filter="all", since_seconds=0.0,
        )


class TestEditorSideEffectTools(unittest.TestCase):
    """Test side-effect editor bridge MCP tools."""

    def test_editor_refresh_delegates(self) -> None:
        server = create_server()
        with patch("prefab_sentinel.mcp_server.send_action", return_value={"success": True}) as mock_send:
            _, result = _run(server.call_tool("editor_refresh", {}))
        mock_send.assert_called_once_with(action="refresh_asset_database")
        self.assertTrue(result["success"])

    def test_editor_recompile_delegates(self) -> None:
        server = create_server()
        with patch("prefab_sentinel.mcp_server.send_action", return_value={"success": True}) as mock_send:
            _run(server.call_tool("editor_recompile", {}))
        mock_send.assert_called_once_with(action="recompile_scripts")

    def test_editor_run_tests_delegates(self) -> None:
        server = create_server()
        with patch("prefab_sentinel.mcp_server.send_action", return_value={"success": True}) as mock_send:
            _run(server.call_tool("editor_run_tests", {}))
        mock_send.assert_called_once_with(action="run_integration_tests", timeout_sec=300)

    def test_editor_run_tests_custom_timeout(self) -> None:
        server = create_server()
        with patch("prefab_sentinel.mcp_server.send_action", return_value={"success": True}) as mock_send:
            _run(server.call_tool("editor_run_tests", {"timeout_sec": 600}))
        mock_send.assert_called_once_with(action="run_integration_tests", timeout_sec=600)


class TestEditorWriteTools(unittest.TestCase):
    """Test write/mutation editor bridge MCP tools."""

    def test_editor_instantiate_delegates(self) -> None:
        server = create_server()
        with patch("prefab_sentinel.mcp_server.send_action", return_value={"success": True}) as mock_send:
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
        with patch("prefab_sentinel.mcp_server.send_action", return_value={"success": True}) as mock_send:
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
        with patch("prefab_sentinel.mcp_server.send_action"):
            _, result = _run(server.call_tool("editor_instantiate", {
                "asset_path": "Assets/X.prefab",
                "position": "1,2",
            }))
        self.assertFalse(result["success"])
        self.assertEqual("INVALID_POSITION", result["code"])

    def test_editor_instantiate_invalid_position_value(self) -> None:
        server = create_server()
        with patch("prefab_sentinel.mcp_server.send_action"):
            _, result = _run(server.call_tool("editor_instantiate", {
                "asset_path": "Assets/X.prefab",
                "position": "a,b,c",
            }))
        self.assertFalse(result["success"])
        self.assertEqual("INVALID_POSITION", result["code"])

    def test_editor_set_material_delegates(self) -> None:
        server = create_server()
        with patch("prefab_sentinel.mcp_server.send_action", return_value={"success": True}) as mock_send:
            _run(server.call_tool("editor_set_material", {
                "hierarchy_path": "/Body",
                "material_index": 0,
                "material_guid": "abc123def456",
            }))
        mock_send.assert_called_once_with(
            action="set_material",
            hierarchy_path="/Body", material_index=0, material_guid="abc123def456",
        )

    def test_editor_delete_delegates(self) -> None:
        server = create_server()
        with patch("prefab_sentinel.mcp_server.send_action", return_value={"success": True}) as mock_send:
            _run(server.call_tool("editor_delete", {"hierarchy_path": "/OldObject"}))
        mock_send.assert_called_once_with(action="delete_object", hierarchy_path="/OldObject")


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
        with patch("prefab_sentinel.session.Phase1Orchestrator") as mock_cls:
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
        with patch("prefab_sentinel.session.Phase1Orchestrator") as mock_cls:
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
            "prefab_sentinel.mcp_server.revert_overrides_impl",
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
            "prefab_sentinel.mcp_server.revert_overrides_impl",
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
            "prefab_sentinel.mcp_server.revert_overrides_impl",
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


if __name__ == "__main__":
    unittest.main()
