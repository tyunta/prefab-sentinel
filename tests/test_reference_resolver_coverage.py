"""Coverage rows for the two gap blocks in
``prefab_sentinel/services/reference_resolver.py`` (issue #181).

Block 1 — broken-reference scan: external prefab/asset target whose
fileID is absent from the target's local-IDs (``missing_local_id``
record with the external classification key).

Block 2 — ``where_used`` path-form lookup: every documented error code
path of the path-form input (non-existent, meta missing, meta
undecodable, meta GUID malformed) and the path-form success row.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from prefab_sentinel.contracts import Severity
from prefab_sentinel.services.reference_resolver import ReferenceResolverService
from tests._assertion_helpers import assert_error_envelope
from tests.bridge_test_helpers import write_file

_TARGET_GUID = "1111111111111111111111111111aaaa"
_SOURCE_GUID = "2222222222222222222222222222bbbb"
_OTHER_GUID = "3333333333333333333333333333cccc"


def _seed_minimal_project(root: Path) -> None:
    (root / "Assets").mkdir(parents=True, exist_ok=True)


class ScanBrokenReferencesExternalAssetTests(unittest.TestCase):
    """Block 1 — ``scan_broken_references`` ``missing_local_id_external``
    arm fires when the source references a non-prefab target by GUID and
    the target's local-IDs do not contain the requested fileID.
    """

    def test_external_asset_missing_fileid_reports_missing_local_id(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            _seed_minimal_project(root)
            # Target is a non-prefab text asset (``.asset`` here so the
            # ``_should_validate_external_file_id`` predicate returns
            # True; ``.prefab`` targets are skipped by design).
            target_text = (
                "%YAML 1.1\n"
                "--- !u!114 &11400000\n"
                "MonoBehaviour:\n"
                "  m_Name: Anchored\n"
                "--- !u!114 &22200000\n"
                "MonoBehaviour:\n"
                "  m_Name: Other\n"
            )
            write_file(root / "Assets" / "Target.asset", target_text)
            write_file(
                root / "Assets" / "Target.asset.meta",
                f"fileFormatVersion: 2\nguid: {_TARGET_GUID}\n",
            )
            # Source references fileID 99999999 which is NOT present in
            # the target's local IDs (11400000, 22200000).
            source_text = (
                "%YAML 1.1\n"
                "--- !u!114 &33300000\n"
                "MonoBehaviour:\n"
                "  m_Reference: {fileID: 99999999, guid: "
                f"{_TARGET_GUID}, type: 2}}\n"
            )
            write_file(root / "Assets" / "Source.asset", source_text)
            write_file(
                root / "Assets" / "Source.asset.meta",
                f"fileFormatVersion: 2\nguid: {_SOURCE_GUID}\n",
            )

            svc = ReferenceResolverService(project_root=root)
            response = svc.scan_broken_references(
                "Assets",
                include_diagnostics=True,
            )

        assert_error_envelope(response, code="REF_SCAN_BROKEN", severity="error")
        self.assertEqual(1, response.data["broken_count"])
        self.assertEqual(1, response.data["categories"]["missing_local_id"])
        self.assertEqual(0, response.data["categories"]["missing_asset"])
        # The diagnostic surfaces the missing fileID and the target's
        # repository-relative path so the operator can correlate
        # source <-> target without re-scanning.
        diag = next(
            d
            for d in response.diagnostics
            if d.detail == "missing_local_id"
        )
        self.assertIn("99999999", diag.evidence)
        self.assertIn("Assets/Target.asset", diag.evidence)


class WhereUsedPathFormErrorPathTests(unittest.TestCase):
    """Block 2 — every documented error code path of the path-form
    ``where_used`` lookup."""

    def test_non_existent_path_returns_ref404(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            _seed_minimal_project(root)
            svc = ReferenceResolverService(project_root=root)
            response = svc.where_used("Assets/Missing.asset")
        assert_error_envelope(
            response,
            code="REF404",
            severity="error",
            data={
                "asset_or_guid": "Assets/Missing.asset",
                "read_only": True,
            },
        )

    def test_meta_file_missing_returns_ref001(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            _seed_minimal_project(root)
            write_file(root / "Assets" / "Orphan.asset", "ignored body\n")
            svc = ReferenceResolverService(project_root=root)
            response = svc.where_used("Assets/Orphan.asset")
        assert_error_envelope(
            response,
            code="REF001",
            severity="error",
            data={
                "asset_or_guid": "Assets/Orphan.asset",
                "read_only": True,
            },
        )

    def test_meta_undecodable_returns_ref001(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            _seed_minimal_project(root)
            asset = root / "Assets" / "Binary.asset"
            asset.parent.mkdir(parents=True, exist_ok=True)
            asset.write_text("body\n", encoding="utf-8")
            (root / "Assets" / "Binary.asset.meta").write_bytes(
                b"\xff\xfe\xfd\xfc not utf-8 \x80\x81"
            )
            svc = ReferenceResolverService(project_root=root)
            response = svc.where_used("Assets/Binary.asset")
        assert_error_envelope(
            response,
            code="REF001",
            severity="error",
            data={
                "asset_or_guid": "Assets/Binary.asset",
                "read_only": True,
            },
        )

    def test_meta_guid_malformed_returns_ref001(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            _seed_minimal_project(root)
            asset = root / "Assets" / "Bad.asset"
            asset.parent.mkdir(parents=True, exist_ok=True)
            asset.write_text("body\n", encoding="utf-8")
            (root / "Assets" / "Bad.asset.meta").write_text(
                "fileFormatVersion: 2\nguid: NOT-A-VALID-GUID\n",
                encoding="utf-8",
            )
            svc = ReferenceResolverService(project_root=root)
            response = svc.where_used("Assets/Bad.asset")
        assert_error_envelope(
            response,
            code="REF001",
            severity="error",
            data={
                "asset_or_guid": "Assets/Bad.asset",
                "read_only": True,
            },
        )

    def test_path_form_success_lists_referencing_asset(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            _seed_minimal_project(root)
            target_path = root / "Assets" / "Target.asset"
            write_file(
                target_path,
                "%YAML 1.1\n--- !u!114 &11400000\nMonoBehaviour:\n  m_Name: T\n",
            )
            write_file(
                root / "Assets" / "Target.asset.meta",
                f"fileFormatVersion: 2\nguid: {_TARGET_GUID}\n",
            )
            referrer_text = (
                "%YAML 1.1\n"
                "--- !u!114 &22200000\n"
                "MonoBehaviour:\n"
                "  m_Reference: {fileID: 11400000, guid: "
                f"{_TARGET_GUID}, type: 2}}\n"
            )
            write_file(root / "Assets" / "Referrer.asset", referrer_text)
            write_file(
                root / "Assets" / "Referrer.asset.meta",
                f"fileFormatVersion: 2\nguid: {_OTHER_GUID}\n",
            )

            svc = ReferenceResolverService(project_root=root)
            response = svc.where_used("Assets/Target.asset")

        self.assertTrue(response.success)
        self.assertEqual("REF_WHERE_USED", response.code)
        self.assertEqual(Severity.INFO, response.severity)
        self.assertEqual(_TARGET_GUID, response.data["guid"])
        self.assertEqual("Assets/Target.asset", response.data["asset_path"])
        self.assertEqual(1, response.data["usage_count"])
        self.assertEqual(1, response.data["returned_usages"])
        usage_paths = [usage["path"] for usage in response.data["usages"]]
        self.assertIn("Assets/Referrer.asset", usage_paths)


if __name__ == "__main__":
    unittest.main()
