"""Schema pin for the cross-tool marketplace catalogs (issue #339, #1).

Two marketplace catalogs ship, one per installer toolchain, because the
plugin sits at the repo root and the two installers need different
``source`` forms there:

- ``.claude-plugin/marketplace.json`` — Claude Code. Uses the relative
  path ``"./"``; Claude Code resolves the plugin from the marketplace
  clone (verified install). It has no ``local`` source type and its
  ``github`` source clones over SSH (issue #1).
- ``.agents/plugins/marketplace.json`` — Codex CLI, on its canonical
  repo-scoped path. Uses the ``url`` source: a relative path resolving
  to the repo root is rejected by Codex CLI (openai/codex#17066), so
  the plugin is fetched as an ``https://`` repo clone whose root is the
  plugin root.

The two catalogs are otherwise identical — same identity, ``interface``,
and per-plugin fields. ``test_catalogs_differ_only_in_plugin_source``
guards against drift between the files.
"""

from __future__ import annotations

import copy
import json
import unittest
from pathlib import Path

import pytest

pytestmark = pytest.mark.source_text_invariant

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
_CLAUDE_MARKETPLACE_PATH = _PROJECT_ROOT / ".claude-plugin" / "marketplace.json"
_CODEX_MARKETPLACE_PATH = (
    _PROJECT_ROOT / ".agents" / "plugins" / "marketplace.json"
)


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


class TestClaudeMarketplaceCatalogSchema(unittest.TestCase):
    """The Claude Code catalog (``.claude-plugin/marketplace.json``)
    carries the documented fields and the marketplace-level
    ``interface.displayName``."""

    def _load_catalog(self) -> dict:
        return _load(_CLAUDE_MARKETPLACE_PATH)

    def test_root_carries_marketplace_identity_and_owner(self) -> None:
        catalog = self._load_catalog()
        self.assertEqual("tyunta-prefab-sentinel", catalog["name"])
        self.assertEqual({"name": "tyunta"}, catalog["owner"])

    def test_root_interface_displayname_is_at_marketplace_root(self) -> None:
        # Issue #339: ``interface.displayName`` belongs at the
        # marketplace root, not inside any plugin entry. Pin both the
        # placement and the documented value.
        catalog = self._load_catalog()
        self.assertEqual("Prefab Sentinel", catalog["interface"]["displayName"])

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
        source = catalog["plugins"][0]["source"]
        # Claude Code resolves the plugin from the marketplace clone via
        # the ``"./"`` relative path (verified install). The object form
        # ``{"source": "local", ...}`` is rejected — Claude Code has no
        # ``local`` source type (issue #1).
        self.assertEqual("./", source)

    def test_plugin_policy_carries_installation_and_authentication(
        self,
    ) -> None:
        policy = self._load_catalog()["plugins"][0]["policy"]
        self.assertEqual("AVAILABLE", policy["installation"])
        self.assertEqual("ON_INSTALL", policy["authentication"])

    def test_plugin_category_equals_coding(self) -> None:
        entry = self._load_catalog()["plugins"][0]
        self.assertEqual("Coding", entry["category"])

    def test_plugin_preserves_claude_code_identity_and_description(
        self,
    ) -> None:
        entry = self._load_catalog()["plugins"][0]
        # Claude Code installer requires ``name`` and ``description``
        # on each plugin entry; their absence would break the Claude
        # Code install path.
        self.assertEqual("prefab-sentinel", entry["name"])
        self.assertIsInstance(entry["description"], str)
        self.assertNotEqual("", entry["description"])


class TestCodexMarketplaceCatalogSchema(unittest.TestCase):
    """The Codex catalog (``.agents/plugins/marketplace.json``) uses the
    ``url`` plugin source so Codex CLI can install a repo-root plugin
    (openai/codex#17066)."""

    def _load_catalog(self) -> dict:
        return _load(_CODEX_MARKETPLACE_PATH)

    def test_codex_catalog_file_exists(self) -> None:
        self.assertTrue(
            _CODEX_MARKETPLACE_PATH.is_file(),
            f"Codex marketplace catalog missing at {_CODEX_MARKETPLACE_PATH}",
        )

    def test_codex_plugin_source_is_url_to_repo_clone(self) -> None:
        source = self._load_catalog()["plugins"][0]["source"]
        # A relative path resolving to the repo root is rejected by
        # Codex CLI (openai/codex#17066); the ``url`` source fetches the
        # repo as an ``https://`` clone whose root is the plugin root.
        self.assertEqual("url", source["source"])
        self.assertEqual(
            "https://github.com/tyunta/prefab-sentinel.git", source["url"]
        )


class TestMarketplaceCatalogDrift(unittest.TestCase):
    """The two host catalogs must stay identical except for the
    per-host plugin ``source`` form."""

    def test_catalogs_differ_only_in_plugin_source(self) -> None:
        claude = _load(_CLAUDE_MARKETPLACE_PATH)
        codex = _load(_CODEX_MARKETPLACE_PATH)
        # Drop the one intentionally per-host field, then require
        # structural equality everywhere else.
        claude_norm = copy.deepcopy(claude)
        codex_norm = copy.deepcopy(codex)
        claude_norm["plugins"][0].pop("source")
        codex_norm["plugins"][0].pop("source")
        self.assertEqual(
            claude_norm,
            codex_norm,
            "Claude Code and Codex marketplace catalogs have drifted "
            "in a field other than plugins[0].source.",
        )


if __name__ == "__main__":
    unittest.main()
