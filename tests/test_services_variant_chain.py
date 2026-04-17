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
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from prefab_sentinel.contracts import Severity
from prefab_sentinel.services.prefab_variant import PrefabVariantService
from tests.bridge_test_helpers import write_file

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


if __name__ == "__main__":
    unittest.main()
