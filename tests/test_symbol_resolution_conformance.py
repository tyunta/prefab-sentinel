"""Cross-language conformance test for #N-disambiguated symbol-path resolution.

T-38-c1: drives every case in ``tests/fixtures/symbol_resolution_conformance.json``
through the offline symbol tree (``prefab_sentinel.symbol_tree.SymbolTree``) and
asserts the declared outcome. The same fixture is consumed by the C# resolver
test (``tests/csharp/SymbolPathResolverTests.cs``, T-38-c2) so the Python and C#
``#N`` resolvers cannot drift.

This test reads only the un-mutated fixture tree plus ``symbol_tree.py``; it
observes no ``prefab_sentinel/`` mutation through a synthetic node tree, so it
is marked ``source_text_invariant`` for the mutmut campaign's test selection.
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path

import pytest

from prefab_sentinel.symbol_tree import (
    AmbiguousSymbolError,
    SymbolKind,
    SymbolNode,
    SymbolNotFoundError,
    SymbolTree,
)

pytestmark = pytest.mark.source_text_invariant

_FIXTURE = (
    Path(__file__).parent / "fixtures" / "symbol_resolution_conformance.json"
)


def _build_node(entry: dict) -> SymbolNode:
    """Build a GAME_OBJECT SymbolNode from a fixture node entry.

    The fixture's ``id`` is mapped to ``file_id`` so the resolved node can
    be checked against the case's ``expected_id``.
    """
    return SymbolNode(
        kind=SymbolKind.GAME_OBJECT,
        name=entry["name"],
        file_id=entry["id"],
        class_id="1",
        children=[_build_node(c) for c in entry.get("children", [])],
    )


class TestSymbolResolutionConformance(unittest.TestCase):
    """T-38-c1: the offline symbol tree conforms to the shared fixture."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.fixture = json.loads(_FIXTURE.read_text(encoding="utf-8"))
        cls.tree = SymbolTree(
            asset_path="conformance",
            roots=[_build_node(r) for r in cls.fixture["roots"]],
        )

    def test_every_case_matches_declared_outcome(self) -> None:
        for case in self.fixture["cases"]:
            with self.subTest(case=case["name"]):
                path = case["path"]
                outcome = case["outcome"]
                matches = self.tree.resolve(path)
                if outcome == "unique":
                    self.assertEqual(
                        1, len(matches),
                        msg=f"{path!r} should resolve uniquely",
                    )
                    self.assertEqual(case["expected_id"], matches[0].file_id)
                    # resolve_unique must agree with resolve.
                    node = self.tree.resolve_unique(path)
                    self.assertEqual(case["expected_id"], node.file_id)
                elif outcome == "ambiguous":
                    self.assertGreater(
                        len(matches), 1,
                        msg=f"{path!r} should be ambiguous",
                    )
                    with self.assertRaises(AmbiguousSymbolError) as cm:
                        self.tree.resolve_unique(path)
                    self.assertIn("mbiguous", str(cm.exception))
                elif outcome == "not_found":
                    self.assertEqual(
                        [], matches,
                        msg=f"{path!r} should resolve to nothing",
                    )
                    with self.assertRaises(SymbolNotFoundError) as cm:
                        self.tree.resolve_unique(path)
                    self.assertIn(path, str(cm.exception))
                else:  # pragma: no cover - guards a malformed fixture
                    self.fail(f"unknown outcome {outcome!r}")


if __name__ == "__main__":
    unittest.main()
