from __future__ import annotations

import json
import tomllib
import unittest
from pathlib import Path

import pytest

pytestmark = pytest.mark.source_text_invariant

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
