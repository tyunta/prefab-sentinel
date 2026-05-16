"""Schema pin for the Codex CLI plugin manifest (issue #338).

The Codex CLI install path reads ``.codex-plugin/plugin.json`` to
provision the plugin. The manifest carries identity, canonical version,
and ``mcpServers`` as a *string path* to a bundled MCP config file —
Codex's parser rejects Claude Code's inline object map there ("invalid
type: map, expected a string", issue #1). The manifest deliberately
omits the ``interface`` block (icon/screenshot/displayName) because
issue #338 excludes per-plugin asset authoring and issue #339 places
``interface.displayName`` at the marketplace root rather than inside
plugin manifests.
"""

from __future__ import annotations

import json
import tomllib
import unittest
from pathlib import Path

import pytest

pytestmark = pytest.mark.source_text_invariant


_PROJECT_ROOT = Path(__file__).resolve().parents[1]
_CODEX_PLUGIN_PATH = _PROJECT_ROOT / ".codex-plugin" / "plugin.json"
_PYPROJECT_PATH = _PROJECT_ROOT / "pyproject.toml"


def _canonical_project_version() -> str:
    data = tomllib.loads(_PYPROJECT_PATH.read_text(encoding="utf-8"))
    return data["project"]["version"]


class TestCodexPluginManifest(unittest.TestCase):
    """``.codex-plugin/plugin.json`` carries the documented Codex
    install-path fields and does not declare an ``interface`` block.
    """

    def _load_manifest(self) -> dict:
        return json.loads(_CODEX_PLUGIN_PATH.read_text(encoding="utf-8"))

    def test_identity_equals_documented_plugin_name(self) -> None:
        manifest = self._load_manifest()
        self.assertEqual("prefab-sentinel", manifest["name"])

    def test_version_equals_canonical_project_version(self) -> None:
        manifest = self._load_manifest()
        self.assertEqual(_canonical_project_version(), manifest["version"])

    def test_mcp_servers_field_is_a_relative_path_string(self) -> None:
        # Codex's manifest parser requires ``mcpServers`` to be a string
        # path to a bundled config file. An inline object map (Claude
        # Code's form) fails the Codex install with "invalid type: map,
        # expected a string" (issue #1).
        mcp_servers = self._load_manifest()["mcpServers"]
        self.assertIsInstance(mcp_servers, str)
        self.assertTrue(
            mcp_servers.startswith("./"),
            f"mcpServers path must start with './': {mcp_servers!r}",
        )

    def test_mcp_servers_file_carries_non_empty_command(self) -> None:
        rel = self._load_manifest()["mcpServers"]
        mcp_path = _PROJECT_ROOT / rel
        self.assertTrue(mcp_path.is_file(), f"missing MCP config: {mcp_path}")
        config = json.loads(mcp_path.read_text(encoding="utf-8"))
        # Codex accepts a direct server map or a ``mcp_servers`` wrapper.
        servers = config.get("mcp_servers", config)
        invocation = servers["prefab-sentinel"]
        command = invocation.get("command", "")
        self.assertNotEqual("", command, f"command empty: {invocation!r}")

    def test_manifest_does_not_declare_interface_block(self) -> None:
        # Issue #338 excludes per-plugin icon/screenshot asset authoring;
        # issue #339 owns ``interface.displayName`` at the marketplace
        # root. The Codex plugin manifest must not carry a top-level
        # ``interface`` key — premature inclusion would couple icon-
        # authoring to this PR's scope.
        manifest = self._load_manifest()
        self.assertNotIn("interface", manifest)


if __name__ == "__main__":
    unittest.main()
