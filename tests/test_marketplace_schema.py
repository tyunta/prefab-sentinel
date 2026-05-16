"""Schema pin for the cross-tool marketplace catalog (issue #339, #1).

The marketplace catalog must satisfy both installer toolchains. The
plugin ``source`` uses the relative-path string form (``"./"``):
Claude Code's marketplace schema has no ``local`` source type and
rejects the object form ``{"source": "local", ...}`` at install
("source type your Claude Code version does not support" — issue #1),
while Codex CLI accepts the string shorthand as well — so the string
satisfies both installers where the object form satisfied only Codex.
The catalog still carries ``policy`` and ``category`` for the Codex CLI
installer and preserves the Claude Code-required identity (``name`` /
``owner``) plus per-plugin ``name`` / ``description`` for the Claude
Code installer. The ``interface.displayName`` field lives at the
marketplace root, not inside any ``plugins[]`` entry — premature
relocation would couple display-name authoring to per-plugin schema.
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path

import pytest

pytestmark = pytest.mark.source_text_invariant

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
_MARKETPLACE_PATH = _PROJECT_ROOT / ".claude-plugin" / "marketplace.json"


class TestMarketplaceCatalogSchema(unittest.TestCase):
    """Marketplace catalog carries the Codex-shaped fields and the
    marketplace-level ``interface.displayName``."""

    def _load_catalog(self) -> dict:
        return json.loads(_MARKETPLACE_PATH.read_text(encoding="utf-8"))

    def test_root_carries_marketplace_identity_and_owner(self) -> None:
        catalog = self._load_catalog()
        self.assertEqual("tyunta-prefab-sentinel", catalog["name"])
        self.assertEqual({"name": "tyunta"}, catalog["owner"])

    def test_root_interface_displayname_is_at_marketplace_root(self) -> None:
        # Issue #339: ``interface.displayName`` belongs at the
        # marketplace root, not inside any plugin entry. Pin both the
        # placement and the documented value.
        catalog = self._load_catalog()
        interface = catalog["interface"]
        self.assertEqual("Prefab Sentinel", interface["displayName"])

    def test_no_plugin_entry_carries_an_interface_block(self) -> None:
        catalog = self._load_catalog()
        for entry in catalog["plugins"]:
            self.assertNotIn(
                "interface",
                entry,
                f"plugin entry {entry.get('name')!r} carries an "
                f"interface block; that surface lives only at the "
                f"marketplace root per issue #339.",
            )

    def test_plugin_source_is_relative_path_string(self) -> None:
        catalog = self._load_catalog()
        entry = catalog["plugins"][0]
        source = entry["source"]
        # Cross-tool relative-path form. Claude Code's marketplace schema
        # has no ``local`` source type and rejects the object form
        # ``{"source": "local", ...}`` at install (issue #1); it requires
        # the string shorthand starting with ``./``. Codex CLI accepts
        # the same string shorthand, so the string satisfies both
        # installers. The plugin and the marketplace share one repo, so
        # ``"./"`` resolves the plugin from the marketplace root.
        self.assertEqual("./", source)

    def test_plugin_policy_carries_installation_and_authentication(self) -> None:
        catalog = self._load_catalog()
        entry = catalog["plugins"][0]
        policy = entry["policy"]
        self.assertEqual("AVAILABLE", policy["installation"])
        self.assertEqual("ON_INSTALL", policy["authentication"])

    def test_plugin_category_equals_coding(self) -> None:
        catalog = self._load_catalog()
        entry = catalog["plugins"][0]
        self.assertEqual("Coding", entry["category"])

    def test_plugin_preserves_claude_code_identity_and_description(self) -> None:
        catalog = self._load_catalog()
        entry = catalog["plugins"][0]
        # Claude Code installer requires ``name`` and ``description``
        # on each plugin entry; their absence would break the Claude
        # Code install path even though Codex would accept the catalog.
        self.assertEqual("prefab-sentinel", entry["name"])
        self.assertIsInstance(entry["description"], str)
        self.assertNotEqual("", entry["description"])


if __name__ == "__main__":
    unittest.main()
