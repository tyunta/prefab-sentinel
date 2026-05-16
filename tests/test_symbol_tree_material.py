"""Symbol-tree Material support tests (issue #295).

The symbol tree builder gains a Material-asset branch: when the input
YAML carries no GameObject blocks but does carry a Material block, the
builder constructs a synthetic root naming the asset and property
leaves keyed by serialized material property name. Existing GameObject-
driven asset shapes (.prefab / .unity) are unaffected.
"""

from __future__ import annotations

import unittest
from pathlib import Path

from prefab_sentinel.symbol_tree import SymbolKind
from prefab_sentinel.symbol_tree_builder import build_symbol_tree

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
_MAT_FIXTURE = _PROJECT_ROOT / "tests" / "data" / "SampleMaterial.mat"


def _prefab_yaml_one_go() -> str:
    """Minimal prefab fixture for the back-compat row."""
    return (
        "%YAML 1.1\n"
        "%TAG !u! tag:unity3d.com,2011:\n"
        "--- !u!1 &100000\n"
        "GameObject:\n"
        "  m_ObjectHideFlags: 0\n"
        "  m_CorrespondingSourceObject: {fileID: 0}\n"
        "  m_PrefabInstance: {fileID: 0}\n"
        "  m_PrefabAsset: {fileID: 0}\n"
        "  serializedVersion: 6\n"
        "  m_Component:\n"
        "  - component: {fileID: 200000}\n"
        "  m_Name: Cube\n"
        "--- !u!4 &200000\n"
        "Transform:\n"
        "  m_ObjectHideFlags: 0\n"
        "  m_CorrespondingSourceObject: {fileID: 0}\n"
        "  m_PrefabInstance: {fileID: 0}\n"
        "  m_PrefabAsset: {fileID: 0}\n"
        "  m_GameObject: {fileID: 100000}\n"
        "  m_LocalRotation: {x: 0, y: 0, z: 0, w: 1}\n"
        "  m_LocalPosition: {x: 0, y: 0, z: 0}\n"
        "  m_LocalScale: {x: 1, y: 1, z: 1}\n"
        "  m_Children: []\n"
        "  m_Father: {fileID: 0}\n"
        "  m_RootOrder: 0\n"
        "  m_LocalEulerAnglesHint: {x: 0, y: 0, z: 0}\n"
    )


class TestMaterialSymbolTree(unittest.TestCase):
    """Material-asset input yields an addressable symbol tree."""

    def test_material_asset_yields_resolvable_property_paths(self) -> None:
        text = _MAT_FIXTURE.read_text(encoding="utf-8")
        tree = build_symbol_tree(text, str(_MAT_FIXTURE))

        # Top-level root: synthetic root naming the asset stem.
        self.assertEqual(1, len(tree.roots))
        root = tree.roots[0]
        # The synthetic root resolves under the asset stem (file name
        # without ``.mat`` suffix) so ``find_unity_symbol`` lookups use
        # the documented ``<asset-stem>/<property-name>`` addressing
        # scheme.
        self.assertEqual("SampleMaterial", root.name)

        # Resolving a single property path returns exactly one node of
        # property kind.
        matches = tree.resolve("SampleMaterial/_MainTex")
        self.assertEqual(1, len(matches))
        self.assertEqual(SymbolKind.PROPERTY, matches[0].kind)
        self.assertEqual("_MainTex", matches[0].name)

    def test_material_asset_with_no_saved_properties_yields_empty_tree(
        self,
    ) -> None:
        # Material block present but no serialized properties — the
        # degenerate-input contract returns an empty tree without
        # raising.
        text = (
            "%YAML 1.1\n"
            "%TAG !u! tag:unity3d.com,2011:\n"
            "--- !u!21 &2100000\n"
            "Material:\n"
            "  serializedVersion: 6\n"
            "  m_Name: EmptyMaterial\n"
            "  m_Shader: {fileID: 46, guid: 0000000000000000f000000000000000, type: 0}\n"
            "  m_SavedProperties:\n"
            "    serializedVersion: 3\n"
            "    m_TexEnvs: []\n"
            "    m_Floats: []\n"
            "    m_Colors: []\n"
        )
        tree = build_symbol_tree(text, "Assets/Empty.mat")

        # Synthetic root may or may not exist; if it does, it has no
        # property children. Per the degenerate-input contract the
        # function returns without raising and the tree resolves
        # nothing.
        self.assertEqual([], tree.resolve("EmptyMaterial/_MainTex"))


class TestSymbolTreeBackCompat(unittest.TestCase):
    """Existing GameObject-bearing asset shapes are unaffected by the
    Material branch.
    """

    def test_prefab_asset_resolves_root_gameobject(self) -> None:
        text = _prefab_yaml_one_go()
        tree = build_symbol_tree(text, "Assets/Cube.prefab")

        self.assertEqual(1, len(tree.roots))
        root = tree.roots[0]
        self.assertEqual(SymbolKind.GAME_OBJECT, root.kind)
        self.assertEqual("Cube", root.name)

        # Existing resolution path returns the GameObject node.
        matches = tree.resolve("Cube")
        self.assertEqual(1, len(matches))


if __name__ == "__main__":
    unittest.main()
