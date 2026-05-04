"""Tests for ``_walk_chain_levels`` diagnostics (issue #76) and model-suffix
coverage (issue #78).

These tests target the refactored package ``prefab_sentinel.services.prefab_variant``.
They exercise:

- ``WalkChainDiagnosticsTests`` (T1–T3): model-file / unreadable / missing-asset
  diagnostics emitted through the chain walker.
- ``ResolveChainValuesWithOriginTests`` (T4): ``PVR_CHAIN_VALUES_WARN`` code and
  ``severity=warning`` when diagnostics present.
- ``ModelSuffixTests`` (T6a–T6d): every member of ``MODEL_FILE_SUFFIXES``
  produces a ``model_file_base`` diagnostic, not ``unreadable_file``.

.fbx is already covered by ``test_services.py``; these tests fill in the
remaining ``.blend``, ``.gltf``, ``.glb``, ``.obj`` members.

Issue #142 (B2 value-pinning): ``OverrideValuePinningTests`` and
``NestedPrefabInstanceVariantDetectionTests`` pin override count, field
name, kind, and Variant-detection branches by value.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from prefab_sentinel.contracts import Diagnostic, Severity
from prefab_sentinel.services.prefab_variant import PrefabVariantService
from prefab_sentinel.unity_assets import is_variant_prefab
from tests.bridge_test_helpers import write_file
from tests.yaml_helpers import (
    YAML_HEADER,
    make_gameobject,
    make_prefab_instance,
    make_prefab_variant,
    make_transform,
)

BASE_GUID = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
NESTED_GUID = "ddddddddddddddddddddddddddddddddd"[:32]
VARIANT_GUID = "cccccccccccccccccccccccccccccccc"


def _write_variant_pointing_at(
    root: Path, source_guid: str, variant_name: str = "TestVariant"
) -> Path:
    """Write a minimal Variant prefab whose m_SourcePrefab points at ``source_guid``."""
    variant_path = root / "Assets" / f"{variant_name}.prefab"
    write_file(
        variant_path,
        f"""%YAML 1.1
--- !u!1001 &100100000
PrefabInstance:
  m_SourcePrefab: {{fileID: 100100000, guid: {source_guid}, type: 3}}
""",
    )
    write_file(
        root / "Assets" / f"{variant_name}.prefab.meta",
        f"fileFormatVersion: 2\nguid: {VARIANT_GUID}\n",
    )
    return variant_path


def _write_model_base(root: Path, suffix: str, guid: str) -> None:
    """Write a non-UTF-8 binary file with the given model suffix + .meta."""
    base_path = root / "Assets" / f"Model{suffix}"
    base_path.parent.mkdir(parents=True, exist_ok=True)
    base_path.write_bytes(b"\x81\x00\xff\xfe")
    write_file(
        root / "Assets" / f"Model{suffix}.meta",
        f"fileFormatVersion: 2\nguid: {guid}\n",
    )


class WalkChainDiagnosticsTests(unittest.TestCase):
    """T1–T3: ``_walk_chain_levels`` emits diagnostics matching the
    ``resolve_prefab_chain`` taxonomy (``model_file_base``,
    ``unreadable_file``, ``missing_asset``)."""

    def test_model_file_emits_model_file_base(self) -> None:
        """T1: a ``.blend`` reference produces ``detail='model_file_base'``."""
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            blend_guid = "bb00bb00bb00bb00bb00bb00bb00bb00"
            _write_model_base(root, ".blend", blend_guid)
            _write_variant_pointing_at(root, blend_guid)

            svc = PrefabVariantService(project_root=root)
            resp = svc.resolve_prefab_chain("Assets/TestVariant.prefab")

            details = [d.detail for d in resp.diagnostics]
            self.assertIn("model_file_base", details)
            self.assertNotIn("unreadable_file", details)

    def test_binary_emits_unreadable_file(self) -> None:
        """T2: a non-UTF-8 non-model binary produces ``detail='unreadable_file'``."""
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            bin_guid = "ee00ee00ee00ee00ee00ee00ee00ee00"
            bin_path = root / "Assets" / "Unknown.bin"
            bin_path.parent.mkdir(parents=True, exist_ok=True)
            bin_path.write_bytes(b"\x81\x00\xff\xfe")
            write_file(
                root / "Assets" / "Unknown.bin.meta",
                f"fileFormatVersion: 2\nguid: {bin_guid}\n",
            )
            _write_variant_pointing_at(root, bin_guid)

            svc = PrefabVariantService(project_root=root)
            resp = svc.resolve_prefab_chain("Assets/TestVariant.prefab")

            details = [d.detail for d in resp.diagnostics]
            self.assertIn("unreadable_file", details)
            self.assertNotIn("model_file_base", details)

    def test_missing_guid_emits_missing_asset(self) -> None:
        """T3: an unresolved GUID produces ``detail='missing_asset'``."""
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            missing_guid = "dead" + "00" * 14
            _write_variant_pointing_at(root, missing_guid)

            svc = PrefabVariantService(project_root=root)
            resp = svc.resolve_prefab_chain("Assets/TestVariant.prefab")

            details = [d.detail for d in resp.diagnostics]
            self.assertIn("missing_asset", details)
            missing = next(d for d in resp.diagnostics if d.detail == "missing_asset")
            self.assertEqual(missing_guid, missing.evidence)


class ResolveChainValuesWithOriginTests(unittest.TestCase):
    """T4: ``resolve_chain_values_with_origin`` returns
    ``PVR_CHAIN_VALUES_WARN`` at ``severity=warning`` when diagnostics are
    present, instead of the default ``PVR_CHAIN_VALUES_WITH_ORIGIN`` at info."""

    def test_warn_code_when_diagnostics_present(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            blend_guid = "bb11bb11bb11bb11bb11bb11bb11bb11"
            _write_model_base(root, ".blend", blend_guid)
            _write_variant_pointing_at(root, blend_guid)

            svc = PrefabVariantService(project_root=root)
            resp = svc.resolve_chain_values_with_origin("Assets/TestVariant.prefab")

            self.assertEqual("PVR_CHAIN_VALUES_WARN", resp.code)
            self.assertEqual(Severity.WARNING, resp.severity)
            self.assertTrue(resp.diagnostics)


class ModelSuffixTests(unittest.TestCase):
    """T6a–T6d: every remaining member of ``MODEL_FILE_SUFFIXES``
    (``.blend``, ``.gltf``, ``.glb``, ``.obj``) yields a
    ``model_file_base`` diagnostic and never ``unreadable_file``.
    ``.fbx`` is covered by the existing ``test_services.py`` tests."""

    _SUFFIX_GUIDS: dict[str, str] = {
        ".blend": "b1" * 16,
        ".gltf": "a1" * 16,
        ".glb": "a2" * 16,
        ".obj": "c3" * 16,
    }

    def _assert_model_suffix_diagnostic(self, suffix: str) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            guid = self._SUFFIX_GUIDS[suffix]
            _write_model_base(root, suffix, guid)
            _write_variant_pointing_at(root, guid)

            svc = PrefabVariantService(project_root=root)
            resp = svc.resolve_prefab_chain("Assets/TestVariant.prefab")

            details = [d.detail for d in resp.diagnostics]
            self.assertIn(
                "model_file_base",
                details,
                msg=f"{suffix}: expected model_file_base diagnostic, got {details}",
            )
            self.assertNotIn(
                "unreadable_file",
                details,
                msg=f"{suffix}: should not emit unreadable_file",
            )
            model_diag = next(d for d in resp.diagnostics if d.detail == "model_file_base")
            self.assertIn(
                f"Base asset is a model file ({suffix})",
                model_diag.evidence,
                msg=f"{suffix}: evidence should name the suffix explicitly",
            )

    def test_blend_treated_as_model(self) -> None:
        self._assert_model_suffix_diagnostic(".blend")

    def test_gltf_treated_as_model(self) -> None:
        self._assert_model_suffix_diagnostic(".gltf")

    def test_glb_treated_as_model(self) -> None:
        self._assert_model_suffix_diagnostic(".glb")

    def test_obj_treated_as_model(self) -> None:
        self._assert_model_suffix_diagnostic(".obj")


class ResolveChainValuesDiagnosticTests(unittest.TestCase):
    """T-94-A / T-94-B: ``resolve_chain_values`` surfaces an initial-variant
    decode failure through the optional ``diagnostics`` sink and
    preserves the silent-swallow contract when the sink is omitted."""

    @staticmethod
    def _write_unreadable_variant(root: Path, variant_name: str = "TestVariant") -> Path:
        """Write a non-UTF-8 binary file with ``.prefab`` suffix plus a meta."""
        variant_path = root / "Assets" / f"{variant_name}.prefab"
        variant_path.parent.mkdir(parents=True, exist_ok=True)
        variant_path.write_bytes(b"\x81\x00\xff\xfe not utf-8")
        write_file(
            root / "Assets" / f"{variant_name}.prefab.meta",
            f"fileFormatVersion: 2\nguid: {VARIANT_GUID}\n",
        )
        return variant_path

    def test_initial_decode_failure_appends_diagnostic(self) -> None:
        """T-94-A: with a sink, an unreadable variant yields one
        ``unreadable_file`` diagnostic and an empty dict."""
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self._write_unreadable_variant(root)

            svc = PrefabVariantService(project_root=root)
            sink: list[Diagnostic] = []
            result = svc.resolve_chain_values(
                "Assets/TestVariant.prefab", diagnostics=sink
            )

            self.assertEqual({}, result)
            self.assertEqual(1, len(sink))
            diag = sink[0]
            self.assertEqual("unreadable_file", diag.detail)
            self.assertEqual("file", diag.location)
            self.assertEqual("unable to decode variant prefab", diag.evidence)
            self.assertEqual("Assets/TestVariant.prefab", diag.path)

    def test_initial_decode_failure_without_sink_does_not_raise(self) -> None:
        """T-94-B: without a sink the contract remains ``return {}``."""
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self._write_unreadable_variant(root)

            svc = PrefabVariantService(project_root=root)
            result = svc.resolve_chain_values("Assets/TestVariant.prefab")

            self.assertEqual({}, result)


class ChainValuesVariantDecisionTests(unittest.TestCase):
    """Issue #136 — chain-value resolvers reach the canonical
    ``is_variant_prefab`` predicate before walking. A base prefab that
    nests a PrefabInstance is classified as not a Variant and therefore
    flows through the documented "not a Variant" return paths; a pure
    Variant walks the chain.
    """

    _BASE_GUID = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
    _NESTED_GUID = "dddddddddddddddddddddddddddddddd"

    @staticmethod
    def _write_meta(asset_path: Path, guid: str) -> None:
        write_file(
            asset_path.with_suffix(asset_path.suffix + ".meta"),
            f"fileFormatVersion: 2\nguid: {guid}\n",
        )

    def _write_base_with_nested_instance(self, root: Path) -> Path:
        """Base prefab carrying its own GameObject + Transform plus a
        nested PrefabInstance referencing some external source GUID.
        """
        assets = root / "Assets"
        assets.mkdir(parents=True, exist_ok=True)

        base_path = assets / "BaseWithNested.prefab"
        base_text = (
            YAML_HEADER
            + make_gameobject(file_id="100", name="Root", component_file_ids=["200"])
            + make_transform(file_id="200", go_file_id="100")
            + make_prefab_instance(file_id="999", source_guid=self._NESTED_GUID)
        )
        write_file(base_path, base_text)
        self._write_meta(base_path, self._BASE_GUID)
        return base_path

    def _write_pure_variant_chain(self, root: Path) -> tuple[Path, Path]:
        """Pure base + pure Variant pointing at it."""
        assets = root / "Assets"
        assets.mkdir(parents=True, exist_ok=True)

        base_path = assets / "PureBase.prefab"
        base_text = (
            YAML_HEADER
            + make_gameobject(file_id="100", name="Root", component_file_ids=["200"])
            + make_transform(file_id="200", go_file_id="100")
        )
        write_file(base_path, base_text)
        self._write_meta(base_path, self._BASE_GUID)

        variant_path = assets / "PureVariant.prefab"
        variant_text = (
            YAML_HEADER
            + make_prefab_variant(source_guid=self._BASE_GUID, modifications=[])
        )
        write_file(variant_path, variant_text)
        self._write_meta(variant_path, VARIANT_GUID)
        return base_path, variant_path

    def test_chain_values_on_base_with_nested_instance_returns_empty(self) -> None:
        """Chain effective-values resolver: base + nested PrefabInstance
        is not a Variant, returns ``{}`` and emits no diagnostics."""
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            base_path = self._write_base_with_nested_instance(root)

            svc = PrefabVariantService(project_root=root)
            sink: list[Diagnostic] = []
            result = svc.resolve_chain_values(
                str(base_path.relative_to(root)).replace("\\", "/"),
                diagnostics=sink,
            )

            self.assertEqual({}, result)
            self.assertEqual([], sink)

    def test_chain_values_on_pure_variant_returns_base_values(self) -> None:
        """Chain effective-values resolver on a pure Variant walks the
        chain and returns a non-empty mapping that contains the base's
        effective transform values (positive path)."""
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            _, variant_path = self._write_pure_variant_chain(root)

            svc = PrefabVariantService(project_root=root)
            result = svc.resolve_chain_values(
                str(variant_path.relative_to(root)).replace("\\", "/"),
            )

            self.assertNotEqual({}, result)
            # The base's Transform (file id 200) carries the unit-scale
            # block, so the walk must yield the base's m_LocalScale.
            self.assertIn("200:m_LocalScale", result)
            self.assertEqual("{x: 1, y: 1, z: 1}", result["200:m_LocalScale"])

    def test_chain_class_map_on_base_with_nested_instance_returns_empty(self) -> None:
        """Chain class-map resolver: base + nested PrefabInstance is not
        a Variant, returns ``{}``."""
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            base_path = self._write_base_with_nested_instance(root)

            svc = PrefabVariantService(project_root=root)
            result = svc.resolve_chain_class_map(
                str(base_path.relative_to(root)).replace("\\", "/"),
            )

            self.assertEqual({}, result)

    def test_chain_values_with_origin_on_base_with_nested_returns_pvr_not_variant(
        self,
    ) -> None:
        """``resolve_chain_values_with_origin``: base + nested
        PrefabInstance returns ``PVR_NOT_VARIANT`` with empty chain and
        empty values."""
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            base_path = self._write_base_with_nested_instance(root)

            svc = PrefabVariantService(project_root=root)
            resp = svc.resolve_chain_values_with_origin(
                str(base_path.relative_to(root)).replace("\\", "/"),
            )

            self.assertTrue(resp.success)
            self.assertEqual("PVR_NOT_VARIANT", resp.code)
            self.assertEqual([], resp.data["chain"])
            self.assertEqual([], resp.data["values"])
            self.assertEqual(0, resp.data["value_count"])


class ChainValuesSourcePrefabImportTests(unittest.TestCase):
    """Issue #136 — the chain-values module no longer imports the
    legacy regex-based ``SOURCE_PREFAB_PATTERN`` symbol; the canonical
    ``is_variant_prefab`` predicate is the sole Variant-decision entry
    point.
    """

    def test_module_does_not_import_source_prefab_pattern(self) -> None:
        from prefab_sentinel.services.prefab_variant import chain_values as cv_mod  # noqa: PLC0415

        text = Path(cv_mod.__file__).read_text(encoding="utf-8")
        self.assertNotIn("SOURCE_PREFAB_PATTERN", text)


# ---------------------------------------------------------------------------
# Issue #142 — prefab-variant value-pinning (B2)
# ---------------------------------------------------------------------------


def _write_variant_with_known_overrides(root: Path) -> None:
    write_file(
        root / "Assets" / "Base.prefab",
        """%YAML 1.1
--- !u!1 &100100000
GameObject:
  m_Name: Base
""",
    )
    write_file(
        root / "Assets" / "Base.prefab.meta",
        f"fileFormatVersion: 2\nguid: {BASE_GUID}\n",
    )
    # Three modifications: one scalar property, one array-size, one
    # array-element.  The override count and the property paths are the
    # test's pinned values.
    write_file(
        root / "Assets" / "Variant.prefab",
        f"""%YAML 1.1
--- !u!1001 &100100000
PrefabInstance:
  m_SourcePrefab: {{fileID: 100100000, guid: {BASE_GUID}, type: 3}}
  m_Modification:
    m_Modifications:
    - target: {{fileID: 100100000, guid: {BASE_GUID}, type: 3}}
      propertyPath: m_Name
      value: VariantName
      objectReference: {{fileID: 0}}
    - target: {{fileID: 100100000, guid: {BASE_GUID}, type: 3}}
      propertyPath: items.Array.size
      value: 2
      objectReference: {{fileID: 0}}
    - target: {{fileID: 100100000, guid: {BASE_GUID}, type: 3}}
      propertyPath: items.Array.data[0]
      value: first
      objectReference: {{fileID: 0}}
""",
    )
    write_file(
        root / "Assets" / "Variant.prefab.meta",
        f"fileFormatVersion: 2\nguid: {VARIANT_GUID}\n",
    )


class OverrideValuePinningTests(unittest.TestCase):
    """B2 row 1 — pin override count and per-entry property_path / kind /
    target_file_id by value.
    """

    def test_known_override_set_pins_count_and_property_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            _write_variant_with_known_overrides(root)
            svc = PrefabVariantService(project_root=root)

            response = svc.list_overrides("Assets/Variant.prefab")

        self.assertTrue(response.success)
        self.assertEqual("PVR_OVERRIDES_OK", response.code)
        # Pin override count and per-entry property_path values exactly.
        self.assertEqual(3, response.data["override_count"])
        property_paths = [entry["property_path"] for entry in response.data["overrides"]]
        self.assertEqual(
            ["m_Name", "items.Array.size", "items.Array.data[0]"],
            property_paths,
        )
        # Pin per-entry kind (scalar / array-size / array-element) by
        # detecting the property_path shape — a property-path family that
        # ends with ``.Array.size`` or ``.Array.data[N]`` is the kind.
        first, size, element = response.data["overrides"]
        self.assertNotIn(".Array.", first["property_path"])
        self.assertTrue(size["property_path"].endswith(".Array.size"))
        self.assertIn(".Array.data[", element["property_path"])
        # Pin the target_file_id and target_guid for every entry.
        for entry in response.data["overrides"]:
            self.assertEqual("100100000", entry["target_file_id"])
            self.assertEqual(BASE_GUID, entry["target_guid"])


class NestedPrefabInstanceVariantDetectionTests(unittest.TestCase):
    """B2 row 2 — a Variant whose source prefab itself contains a nested
    ``PrefabInstance`` is still detected as a Variant by ``is_variant_prefab``,
    and the chain values resolve from the variant chain.
    """

    def _write_base_with_nested_instance_chain(self, root: Path) -> Path:
        # Inner base prefab — pure GameObject.
        inner_guid = "abababababababababababababababab"
        write_file(
            root / "Assets" / "Inner.prefab",
            """%YAML 1.1
--- !u!1 &100100000
GameObject:
  m_Name: Inner
""",
        )
        write_file(
            root / "Assets" / "Inner.prefab.meta",
            f"fileFormatVersion: 2\nguid: {inner_guid}\n",
        )
        # Base prefab that nests Inner as a PrefabInstance and owns its own
        # GameObject.  Nested-PrefabInstance presence does not promote it
        # to Variant.
        write_file(
            root / "Assets" / "Base.prefab",
            f"""%YAML 1.1
--- !u!1 &200200000
GameObject:
  m_Name: Base
--- !u!1001 &300300000
PrefabInstance:
  m_SourcePrefab: {{fileID: 100100000, guid: {inner_guid}, type: 3}}
""",
        )
        write_file(
            root / "Assets" / "Base.prefab.meta",
            f"fileFormatVersion: 2\nguid: {BASE_GUID}\n",
        )
        # Variant of Base — no GameObjects of its own.
        variant_path = root / "Assets" / "Variant.prefab"
        write_file(
            variant_path,
            f"""%YAML 1.1
--- !u!1001 &100100000
PrefabInstance:
  m_SourcePrefab: {{fileID: 100100000, guid: {BASE_GUID}, type: 3}}
""",
        )
        write_file(
            root / "Assets" / "Variant.prefab.meta",
            f"fileFormatVersion: 2\nguid: {VARIANT_GUID}\n",
        )
        return variant_path

    def test_variant_with_nested_prefabinstance_in_base_resolves_chain(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            variant_path = self._write_base_with_nested_instance_chain(root)
            variant_text = variant_path.read_text(encoding="utf-8")
            base_text = (root / "Assets" / "Base.prefab").read_text(encoding="utf-8")

        # Variant (no GameObject of its own) → True.
        self.assertTrue(is_variant_prefab(variant_text))
        # Base prefab that nests a PrefabInstance but owns a GameObject → False.
        self.assertFalse(is_variant_prefab(base_text))

    def test_chain_values_resolve_from_variant_chain(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self._write_base_with_nested_instance_chain(root)
            svc = PrefabVariantService(project_root=root)

            response = svc.resolve_chain_values_with_origin("Assets/Variant.prefab")

        self.assertTrue(response.success)
        # The chain must include the Variant and its Base; nested-instance
        # presence inside Base does not derail the walk.
        chain_paths = [entry["path"] for entry in response.data["chain"]]
        self.assertIn("Assets/Variant.prefab", chain_paths)
        self.assertIn("Assets/Base.prefab", chain_paths)


if __name__ == "__main__":
    unittest.main()
