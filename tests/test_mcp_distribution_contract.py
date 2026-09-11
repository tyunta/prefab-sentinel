from __future__ import annotations

import json
import tomllib
import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_MCP_CONSTRAINT = "mcp>=2,<3"


class TestMCPDistributionContract(unittest.TestCase):
    def test_sdk_major_range_is_synchronized(self) -> None:
        pyproject = tomllib.loads((_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        self.assertEqual([_MCP_CONSTRAINT], pyproject["project"]["optional-dependencies"]["mcp"])

        codex = json.loads((_ROOT / ".codex-plugin/mcp.json").read_text(encoding="utf-8"))
        self.assertIn(_MCP_CONSTRAINT, codex["prefab-sentinel"]["args"])

        claude = json.loads((_ROOT / ".claude-plugin/plugin.json").read_text(encoding="utf-8"))
        self.assertIn(_MCP_CONSTRAINT, claude["mcpServers"]["prefab-sentinel"]["args"])

    def test_http_boundary_test_dependency_is_declared(self) -> None:
        pyproject = tomllib.loads((_ROOT / "pyproject.toml").read_text(encoding="utf-8"))

        self.assertIn(
            "httpx>=0.27.0,<0.29.0",
            pyproject["project"]["optional-dependencies"]["test"],
        )

    def test_codex_marks_modern_protocol_opt_in_without_marking_claude(self) -> None:
        codex = json.loads(
            (_ROOT / ".codex-plugin/mcp.json").read_text(encoding="utf-8")
        )
        self.assertEqual(
            {"CODEX_MCP_PROTOCOL_VERSION": "2026-07-28"},
            codex["prefab-sentinel"].get("env"),
        )

        claude = json.loads(
            (_ROOT / ".claude-plugin/plugin.json").read_text(encoding="utf-8")
        )
        self.assertNotIn("env", claude["mcpServers"]["prefab-sentinel"])

    def test_editor_run_tests_golden_schema_tracks_bounded_acceptance_inputs(self) -> None:
        """A stale distribution fixture would hide a public acceptance-contract change."""
        golden = json.loads(
            (_ROOT / "tests/fixtures/mcp_v1_tool_schemas.json").read_text(
                encoding="utf-8"
            )
        )
        tool = next(
            item for item in golden["tools"] if item["name"] == "editor_run_tests"
        )
        properties = tool["inputSchema"]["properties"]

        expected_defaults = {
            "profile": "default",
            "live_probes": False,
            "run_id": "",
            "timeout_sec": 300,
        }
        self.assertEqual(
            expected_defaults,
            {
                name: properties.get(name, {}).get("default")
                for name in expected_defaults
            },
        )

        from prefab_sentinel.mcp_server import create_server
        from tests._mcp_test_support import run

        public_tools = run(create_server().list_tools())
        public_by_name = {tool.name: tool for tool in public_tools}
        self.assertEqual(
            {item["name"] for item in golden["tools"]},
            set(public_by_name),
        )
        self.assertEqual(
            tool["inputSchema"],
            public_by_name["editor_run_tests"].input_schema,
        )
