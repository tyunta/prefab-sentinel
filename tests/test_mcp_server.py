"""Tests for MCP server tool registration and invocation."""

from __future__ import annotations

import asyncio
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

from prefab_sentinel.mcp_server import create_server
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
            "get_unity_symbols",
            "find_unity_symbol",
            "find_referencing_assets",
            "validate_refs",
            "inspect_wiring",
            "inspect_variant",
            "diff_unity_symbols",
        }
        self.assertEqual(expected, tool_names)

    def test_tool_count(self) -> None:
        server = create_server()
        tools = _run(server.list_tools())
        self.assertEqual(7, len(tools))


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
                {"path": str(prefab), "depth": 0},
            ))
            self.assertTrue(result["success"])
            self.assertEqual(str(prefab), result["asset_path"])
            symbols = result["symbols"]
            self.assertEqual(1, len(symbols))
            self.assertEqual("Cube", symbols[0]["name"])
            # depth=0: no children
            self.assertNotIn("children", symbols[0])

    def test_get_unity_symbols_depth1(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            prefab = self._write_prefab(Path(td))
            _, result = _run(self.server.call_tool(
                "get_unity_symbols",
                {"path": str(prefab), "depth": 1},
            ))
            self.assertTrue(result["success"])
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
                {"path": str(prefab), "symbol_path": "Cube"},
            ))
            self.assertTrue(result["success"])
            self.assertEqual(1, len(result["matches"]))
            self.assertEqual("Cube", result["matches"][0]["name"])

    def test_find_unity_symbol_not_found(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            prefab = self._write_prefab(Path(td))
            _, result = _run(self.server.call_tool(
                "find_unity_symbol",
                {"path": str(prefab), "symbol_path": "NonExistent"},
            ))
            self.assertFalse(result["success"])
            self.assertEqual([], result["matches"])

    def test_find_unity_symbol_component_path(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            prefab = self._write_prefab(Path(td))
            _, result = _run(self.server.call_tool(
                "find_unity_symbol",
                {"path": str(prefab), "symbol_path": "Cube/MeshRenderer"},
            ))
            self.assertTrue(result["success"])
            self.assertEqual(1, len(result["matches"]))
            self.assertEqual("MeshRenderer", result["matches"][0]["name"])

    def test_get_unity_symbols_file_not_found(self) -> None:
        from mcp.server.fastmcp.exceptions import ToolError

        with self.assertRaises(ToolError):
            _run(self.server.call_tool(
                "get_unity_symbols",
                {"path": "/nonexistent/test.prefab"},
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
                    "path": str(p),
                    "symbol_path": "Player/MonoBehaviour",
                },
            ))
            self.assertTrue(result["success"])
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
            "prefab_sentinel.mcp_server.Phase1Orchestrator"
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
            "prefab_sentinel.mcp_server.Phase1Orchestrator"
        ) as mock_cls:
            mock_cls.default.return_value = mock_orch
            _, result = _run(server.call_tool(
                "inspect_wiring",
                {"path": "/some/test.prefab"},
            ))

        self.assertTrue(result["success"])
        mock_orch.inspect_wiring.assert_called_once_with(
            target_path="/some/test.prefab",
            udon_only=False,
        )

    def test_find_referencing_assets_delegates(self) -> None:
        mock_resp = MagicMock()
        mock_resp.to_dict.return_value = {
            "success": True,
            "data": {"usages": []},
        }
        mock_orch = MagicMock()
        mock_orch.inspect_where_used.return_value = mock_resp

        server = self._make_server()

        with patch(
            "prefab_sentinel.mcp_server.Phase1Orchestrator"
        ) as mock_cls:
            mock_cls.default.return_value = mock_orch
            _, result = _run(server.call_tool(
                "find_referencing_assets",
                {"asset_or_guid": "abcd1234abcd1234abcd1234abcd1234"},
            ))

        self.assertTrue(result["success"])
        mock_orch.inspect_where_used.assert_called_once_with(
            asset_or_guid="abcd1234abcd1234abcd1234abcd1234",
            scope=None,
            max_usages=100,
        )

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
            "prefab_sentinel.mcp_server.Phase1Orchestrator"
        ) as mock_cls:
            mock_cls.default.return_value = mock_orch
            _, result = _run(server.call_tool(
                "inspect_variant",
                {"path": "/some/variant.prefab", "show_origin": True},
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
            "prefab_sentinel.mcp_server.Phase1Orchestrator"
        ) as mock_cls:
            mock_cls.default.return_value = mock_orch
            _, result = _run(server.call_tool(
                "diff_unity_symbols",
                {"path": "/some/variant.prefab"},
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
            "prefab_sentinel.mcp_server.Phase1Orchestrator"
        ) as mock_cls:
            mock_cls.default.return_value = mock_orch
            _run(server.call_tool(
                "diff_unity_symbols",
                {"path": "/v.prefab", "component_filter": "speed"},
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
                    "path": str(p),
                    "symbol_path": "Player/MonoBehaviour",
                    "include_properties": True,
                },
            ))

        self.assertTrue(result["success"])
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
                "prefab_sentinel.mcp_server.Phase1Orchestrator"
            ) as mock_cls:
                mock_orch = MagicMock()
                mock_orch.prefab_variant.resolve_chain_values_with_origin.return_value = mock_resp
                mock_cls.default.return_value = mock_orch

                _, result = _run(server.call_tool(
                    "find_unity_symbol",
                    {
                        "path": str(p),
                        "symbol_path": "Player/MonoBehaviour",
                        "show_origin": True,
                    },
                ))

        self.assertTrue(result["success"])
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
                "prefab_sentinel.mcp_server.Phase1Orchestrator"
            ) as mock_cls:
                mock_orch = MagicMock()
                mock_orch.prefab_variant.resolve_chain_values_with_origin.return_value = mock_resp
                mock_cls.default.return_value = mock_orch

                _, result = _run(server.call_tool(
                    "find_unity_symbol",
                    {
                        "path": str(p),
                        "symbol_path": "Cube/MonoBehaviour",
                        "show_origin": True,
                    },
                ))

        # find_unity_symbol still succeeds; origin annotation skipped
        self.assertTrue(result["success"])
        props = result["matches"][0].get("properties", {})
        # Properties remain in flat {name: value} format since annotation was skipped
        if props:
            first_val = next(iter(props.values()))
            self.assertIsInstance(first_val, str)


class TestCLIServeCommand(unittest.TestCase):
    """Test the CLI serve subcommand parser."""

    def test_serve_parser_registered(self) -> None:
        from prefab_sentinel.cli import build_parser

        parser = build_parser()
        args = parser.parse_args(["serve"])
        self.assertEqual("serve", args.command)
        self.assertEqual("stdio", args.transport)
        self.assertIsNone(args.project_root)

    def test_serve_parser_with_options(self) -> None:
        from prefab_sentinel.cli import build_parser

        parser = build_parser()
        args = parser.parse_args([
            "serve",
            "--transport", "streamable-http",
            "--project-root", "/unity/project",
        ])
        self.assertEqual("streamable-http", args.transport)
        self.assertEqual("/unity/project", args.project_root)


if __name__ == "__main__":
    unittest.main()
