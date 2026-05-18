"""Issue #3 — cross-module package-private import structural invariants.

Python convention: a leading-underscore name is module-internal. Importing
such a name from a *sibling* module under ``prefab_sentinel/`` violates the
package's responsibility boundaries. This module pins two invariants:

* no module under ``prefab_sentinel/`` imports an underscore-prefixed
  symbol from a sibling module;
* each formerly package-private cross-module symbol is now a public,
  ``__all__``-listed name in its defining module, with no underscore
  alias left behind.

Marked ``source_text_invariant`` at module scope: the assertions parse the
un-mutated ``prefab_sentinel/`` source tree and import its modules to read
``__all__``; they observe import structure, not ``prefab_sentinel`` runtime
behaviour, so they contribute no mutant-detection signal.
"""

from __future__ import annotations

import ast
import importlib
import unittest
from pathlib import Path

import pytest

pytestmark = pytest.mark.source_text_invariant

_PACKAGE_ROOT = Path(__file__).resolve().parent.parent / "prefab_sentinel"

# Issue #3 — the eight formerly package-private cross-module symbols,
# paired with their defining module, under the public names they are
# renamed to.
_PUBLICISED_SYMBOLS: tuple[tuple[str, str], ...] = (
    ("prefab_sentinel.orchestrator_variant", "read_target_file"),
    ("prefab_sentinel.orchestrator_variant", "resolve_variant_base"),
    ("prefab_sentinel.reporting", "extract_ref_scan_data"),
    ("prefab_sentinel.reporting", "extract_runtime_validation_data"),
    ("prefab_sentinel.services.prefab_variant.chain", "ChainLevel"),
    ("prefab_sentinel.services.serialized_object.scene_dispatch", "SceneContext"),
    ("prefab_sentinel.watcher", "has_watchfiles"),
    ("prefab_sentinel.udon_wiring_parser", "parse_monobehaviour_fields"),
)


def _iter_package_modules() -> list[Path]:
    return sorted(_PACKAGE_ROOT.rglob("*.py"))


def _cross_module_private_imports(path: Path) -> list[str]:
    """Return ``module:name`` strings for every import of an
    underscore-prefixed symbol from a ``prefab_sentinel`` sibling module.

    Parsing the AST (rather than grepping text) means a name that occurs
    only inside a ``#`` comment or a string literal is never matched, and
    ``from x import public as _alias`` is correctly classified by the
    imported name (``public``), not the local alias.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    violations: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom):
            continue
        # Relative imports (level > 0) and absolute ``prefab_sentinel``
        # imports both target sibling modules within the package.
        is_sibling = node.level > 0 or (node.module or "").startswith("prefab_sentinel")
        if not is_sibling:
            continue
        for alias in node.names:
            name = alias.name
            if name.startswith("_") and not name.startswith("__"):
                violations.append(f"{node.module or '.'}:{name}")
    return violations


class CrossModulePrivateImportTests(unittest.TestCase):
    def test_no_module_imports_an_underscore_prefixed_sibling_symbol(self) -> None:
        offenders: dict[str, list[str]] = {}
        for path in _iter_package_modules():
            found = _cross_module_private_imports(path)
            if found:
                offenders[str(path.relative_to(_PACKAGE_ROOT))] = found
        self.assertEqual(
            {},
            offenders,
            f"cross-module package-private imports remain: {offenders}",
        )


class PublicisedSymbolExportTests(unittest.TestCase):
    def test_each_publicised_symbol_is_defined_and_exported(self) -> None:
        for module_name, symbol in _PUBLICISED_SYMBOLS:
            with self.subTest(symbol=f"{module_name}.{symbol}"):
                module = importlib.import_module(module_name)
                self.assertTrue(
                    hasattr(module, symbol),
                    f"{module_name} does not define public symbol {symbol}",
                )
                self.assertIn(
                    symbol,
                    getattr(module, "__all__", []),
                    f"{module_name}.__all__ does not export {symbol}",
                )

    def test_no_underscore_aliased_predecessor_remains(self) -> None:
        for module_name, symbol in _PUBLICISED_SYMBOLS:
            with self.subTest(symbol=f"{module_name}._{symbol}"):
                module = importlib.import_module(module_name)
                self.assertFalse(
                    hasattr(module, f"_{symbol}"),
                    f"{module_name} still carries the private alias _{symbol}",
                )


if __name__ == "__main__":
    unittest.main()
