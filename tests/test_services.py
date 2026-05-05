from __future__ import annotations

import hashlib
import hmac
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

from prefab_sentinel.contracts import Diagnostic, Severity
from prefab_sentinel.orchestrator import Phase1Orchestrator
from prefab_sentinel.patch_plan import (
    compute_patch_plan_hmac_sha256,
    compute_patch_plan_sha256,
    load_patch_plan,
)
from prefab_sentinel.services.prefab_variant import PrefabVariantService
from prefab_sentinel.services.reference_resolver import ReferenceResolverService
from prefab_sentinel.services.runtime_validation import RuntimeValidationService
from prefab_sentinel.services.serialized_object import SerializedObjectService
from prefab_sentinel.services.serialized_object.before_cache import (
    UnresolvedReason,
)
from prefab_sentinel.services.serialized_object.patch_validator import validate_op
from tests.bridge_test_helpers import write_fake_runtime_runner, write_file

BASE_GUID = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
MISSING_GUID = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
VARIANT_GUID = "cccccccccccccccccccccccccccccccc"
CROSS_PROJECT_GUID = "dddddddddddddddddddddddddddddddd"


_BRIDGE_DISPATCH_ENV_VARS: tuple[str, ...] = (
    "UNITYTOOL_BRIDGE_MODE",
    "UNITYTOOL_BRIDGE_WATCH_DIR",
)


def _isolate_bridge_dispatch_env(test: unittest.TestCase) -> None:
    """Pop the two bridge dispatch env vars for the test; restore on cleanup.

    A host shell that exports ``UNITYTOOL_BRIDGE_MODE=editor`` (and the
    paired watch directory) would otherwise route the runtime-validation
    and serialized-object service calls through the editor file-watcher
    path, which has no responder during unit tests and times out.
    """
    for var in _BRIDGE_DISPATCH_ENV_VARS:
        original = os.environ.pop(var, None)
        if original is not None:
            test.addCleanup(os.environ.__setitem__, var, original)


def _create_sample_project(root: Path) -> None:
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
        f"""fileFormatVersion: 2
guid: {BASE_GUID}
""",
    )
    write_file(
        root / "Assets" / "Variant.prefab",
        f"""%YAML 1.1
--- !u!1001 &100100000
PrefabInstance:
  m_SourcePrefab: {{fileID: 100100000, guid: {BASE_GUID}, type: 3}}
  m_ExternalPrefabRef: {{fileID: 999999, guid: {BASE_GUID}, type: 3}}
  m_Modification:
    m_Modifications:
    - target: {{fileID: 100100000, guid: {BASE_GUID}, type: 3}}
      propertyPath: mic_obj_extra.Array.size
      value: 1
      objectReference: {{fileID: 0}}
    - target: {{fileID: 100100000, guid: {BASE_GUID}, type: 3}}
      propertyPath: mic_obj_extra.Array.data[1]
      value: 0
      objectReference: {{fileID: 0}}
    - target: {{fileID: 100100000, guid: {BASE_GUID}, type: 3}}
      propertyPath: duplicated.path
      value: first
      objectReference: {{fileID: 0}}
    - target: {{fileID: 100100000, guid: {BASE_GUID}, type: 3}}
      propertyPath: duplicated.path
      value: second
      objectReference: {{fileID: 0}}
    - target: {{fileID: 100100000, guid: {MISSING_GUID}, type: 3}}
      propertyPath: missing.asset
      value: 0
      objectReference: {{fileID: 0}}
  m_LocalRef: {{fileID: 999999}}
""",
    )
    write_file(
        root / "Assets" / "Variant.prefab.meta",
        f"""fileFormatVersion: 2
guid: {VARIANT_GUID}
""",
    )


class ReferenceResolverServiceTests(unittest.TestCase):
    def test_scan_broken_references_detects_missing_asset_and_local_id(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            _create_sample_project(root)
            svc = ReferenceResolverService(project_root=root)

            response = svc.scan_broken_references("Assets")

            self.assertFalse(response.success)
            self.assertEqual("REF_SCAN_BROKEN", response.code)
            self.assertEqual(1, response.data["categories"]["missing_asset"])
            self.assertEqual(1, response.data["categories"]["missing_local_id"])
            self.assertEqual(1, response.data["categories_occurrences"]["missing_asset"])
            self.assertEqual(1, response.data["categories_occurrences"]["missing_local_id"])
            self.assertEqual(2, response.data["broken_count"])
            self.assertEqual(2, response.data["broken_occurrences"])
            self.assertFalse(response.data["details_included"])
            self.assertEqual(0, len(response.diagnostics))
            # truncated hint is in data, not diagnostics
            self.assertIn("--details", response.data["truncated_hint"])
            self.assertGreaterEqual(
                response.data["skipped_external_prefab_fileid_checks"],
                1,
            )
            # Details include per-reference info (capped at top_guid_limit)
            details = response.data["skipped_external_prefab_fileid_details"]
            self.assertGreaterEqual(len(details), 1)
            self.assertIn("source", details[0])
            self.assertIn("target_guid", details[0])
            self.assertIn("file_id", details[0])

    def test_scan_broken_references_honors_details_limit(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            _create_sample_project(root)
            svc = ReferenceResolverService(project_root=root)

            response = svc.scan_broken_references(
                "Assets",
                include_diagnostics=True,
                max_diagnostics=1,
            )

            self.assertFalse(response.success)
            self.assertEqual(1, len(response.diagnostics))
            self.assertEqual(1, response.data["returned_diagnostics"])
            # truncated hint is in data, not diagnostics
            self.assertIn("--max-diagnostics", response.data["truncated_hint"])
            self.assertEqual(1, response.data["truncated_diagnostics"])
            self.assertGreaterEqual(
                response.data["broken_occurrences"],
                response.data["broken_count"],
            )

    def test_scan_broken_references_honors_ignore_asset_guids(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            _create_sample_project(root)
            svc = ReferenceResolverService(project_root=root)

            response = svc.scan_broken_references(
                "Assets",
                ignore_asset_guids=(MISSING_GUID,),
            )

            self.assertFalse(response.success)
            self.assertEqual(0, response.data["categories"]["missing_asset"])
            self.assertEqual(1, response.data["categories"]["missing_local_id"])
            self.assertEqual(0, response.data["categories_occurrences"]["missing_asset"])
            self.assertEqual(1, response.data["ignored_missing_asset_unique_count"])
            self.assertEqual(1, response.data["ignored_missing_asset_occurrences"])
            self.assertEqual([], response.data["top_missing_asset_guids"])
            self.assertEqual(MISSING_GUID, response.data["top_ignored_missing_asset_guids"][0]["guid"])

    def test_scan_broken_references_rejects_invalid_ignore_guid(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            _create_sample_project(root)
            svc = ReferenceResolverService(project_root=root)

            response = svc.scan_broken_references(
                "Assets",
                ignore_asset_guids=("not-a-guid",),
            )

            self.assertFalse(response.success)
            self.assertEqual("REF001", response.code)
            self.assertIn("invalid_ignore_asset_guids", response.data)

    def test_resolve_reference_and_where_used(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            _create_sample_project(root)
            svc = ReferenceResolverService(project_root=root)

            resolved = svc.resolve_reference(BASE_GUID, "100100000")
            self.assertTrue(resolved.success)
            self.assertEqual("REF_RESOLVED", resolved.code)

            usage = svc.where_used(BASE_GUID, scope="Assets", max_usages=1)
            self.assertTrue(usage.success)
            self.assertEqual("Assets", usage.data["scope"])
            self.assertEqual(1, usage.data["returned_usages"])
            self.assertGreater(usage.data["usage_count"], 1)
            self.assertGreater(usage.data["truncated_usages"], 0)

    def test_where_used_returns_missing_scope_error(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            _create_sample_project(root)
            svc = ReferenceResolverService(project_root=root)

            usage = svc.where_used(BASE_GUID, scope="Assets/NotFound")

            self.assertFalse(usage.success)
            self.assertEqual("REF404", usage.code)

    def test_scan_broken_references_scopes_guid_index_to_unity_project(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo_root = Path(temp_dir)
            avatar_root = repo_root / "sample" / "avatar"
            world_root = repo_root / "sample" / "world"
            write_file(
                avatar_root / "Assets" / "Ref.prefab",
                f"""%YAML 1.1
--- !u!1001 &100100000
PrefabInstance:
  m_SourcePrefab: {{fileID: 11400000, guid: {CROSS_PROJECT_GUID}, type: 3}}
""",
            )
            write_file(
                avatar_root / "Assets" / "Ref.prefab.meta",
                f"""fileFormatVersion: 2
guid: {VARIANT_GUID}
""",
            )
            write_file(
                world_root / "Assets" / "WorldOnly.asset",
                """%YAML 1.1
--- !u!114 &11400000
MonoBehaviour:
""",
            )
            write_file(
                world_root / "Assets" / "WorldOnly.asset.meta",
                f"""fileFormatVersion: 2
guid: {CROSS_PROJECT_GUID}
""",
            )

            svc = ReferenceResolverService(project_root=repo_root)
            response = svc.scan_broken_references("sample/avatar/Assets")

            self.assertFalse(response.success)
            self.assertEqual(1, response.data["categories"]["missing_asset"])
            self.assertEqual("sample/avatar", response.data["scan_project_root"])

    def test_where_used_skips_library_scope(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            _create_sample_project(root)
            write_file(
                root / "Library" / "Noise.prefab",
                f"""%YAML 1.1
--- !u!1001 &100100000
PrefabInstance:
  m_SourcePrefab: {{fileID: 100100000, guid: {BASE_GUID}, type: 3}}
""",
            )
            svc = ReferenceResolverService(project_root=root)

            usage = svc.where_used(BASE_GUID)
            paths = [item["path"] for item in usage.data["usages"]]

            self.assertTrue(paths)
            self.assertFalse(any(path.startswith("Library/") for path in paths))

    def test_invalidate_text_cache_single_file(self) -> None:
        svc = ReferenceResolverService(project_root=Path("/fake/project"))
        path = Path("/fake/Assets/Test.prefab")
        svc._text_cache[path] = "content"
        svc._local_id_cache[path] = {"123"}
        svc._unreadable_paths.add(path)
        other = Path("/fake/Assets/Other.prefab")
        svc._text_cache[other] = "other"

        svc.invalidate_text_cache(path)

        self.assertNotIn(path, svc._text_cache)
        self.assertNotIn(path, svc._local_id_cache)
        self.assertNotIn(path, svc._unreadable_paths)
        self.assertIn(other, svc._text_cache)

    def test_invalidate_text_cache_all(self) -> None:
        svc = ReferenceResolverService(project_root=Path("/fake/project"))
        path = Path("/fake/Assets/Test.prefab")
        svc._text_cache[path] = "content"
        svc._local_id_cache[path] = {"123"}
        svc._unreadable_paths.add(path)

        svc.invalidate_text_cache(None)

        self.assertEqual(len(svc._text_cache), 0)
        self.assertEqual(len(svc._local_id_cache), 0)
        self.assertEqual(len(svc._unreadable_paths), 0)

    def test_invalidate_guid_index(self) -> None:
        svc = ReferenceResolverService(project_root=Path("/fake/project"))
        svc._guid_index_cache[Path("/fake")] = {"guid1": Path("/fake/a.prefab")}

        svc.invalidate_guid_index()

        self.assertEqual(len(svc._guid_index_cache), 0)

    def test_preload_texts_populates_cache(self) -> None:
        """_preload_texts should populate _text_cache for multiple files."""
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            _create_sample_project(root)
            svc = ReferenceResolverService(project_root=root)

            files = [
                root / "Assets" / "Base.prefab",
                root / "Assets" / "Variant.prefab",
            ]
            svc.preload_texts(files)

            for f in files:
                self.assertIn(f, svc._text_cache)
                self.assertIsNotNone(svc._text_cache[f])

    def test_preload_texts_handles_unreadable(self) -> None:
        """_preload_texts should mark unreadable files in _unreadable_paths."""
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            _create_sample_project(root)
            svc = ReferenceResolverService(project_root=root)

            # Create a binary file that will fail decode
            bad = root / "Assets" / "bad.prefab"
            bad.write_bytes(b"\x80\x81\x82\x83" * 100)

            svc.preload_texts([bad])

            self.assertIn(bad, svc._unreadable_paths)
            self.assertIsNone(svc._text_cache[bad])

    def test_preload_texts_idempotent(self) -> None:
        """Calling _preload_texts twice should not re-read cached files."""
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            _create_sample_project(root)
            svc = ReferenceResolverService(project_root=root)

            files = [root / "Assets" / "Base.prefab"]
            svc.preload_texts(files)
            original_text = svc._text_cache[files[0]]

            # Modify file on disk — preload should NOT re-read
            files[0].write_text("modified", encoding="utf-8")
            svc.preload_texts(files)

            self.assertEqual(svc._text_cache[files[0]], original_text)

    def test_preload_texts_empty_list(self) -> None:
        """_preload_texts with empty list should not raise."""
        svc = ReferenceResolverService(project_root=Path("/fake"))
        svc.preload_texts([])  # Should not raise

    def test_collect_scope_files_cached(self) -> None:
        """Second call returns same list without re-walking."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            assets = root / "Assets"
            assets.mkdir()
            prefab = assets / "Test.prefab"
            prefab.write_text("%YAML 1.1\n", encoding="utf-8")

            service = ReferenceResolverService(project_root=root)
            result1 = service.collect_scope_files(assets)
            # Mutate filesystem — cached result should NOT reflect the change
            (assets / "New.prefab").write_text("%YAML 1.1\n", encoding="utf-8")
            result2 = service.collect_scope_files(assets)
            self.assertEqual(result1, result2)  # Same cached list

    def test_preload_and_read_populates_cache(self) -> None:
        """preload_texts + read_text uses _text_cache."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            assets = root / "Assets"
            assets.mkdir()
            prefab = assets / "Test.prefab"
            prefab.write_text(
                "%YAML 1.1\n--- !u!1 &1\nGameObject:\n  m_Name: A\n",
                encoding="utf-8",
            )

            service = ReferenceResolverService(project_root=root)
            service.preload_texts([prefab])
            self.assertIn(prefab, service._text_cache)
            text = service.read_text(prefab)
            self.assertIn("m_Name: A", text)

    def test_collect_scope_files_invalidated(self) -> None:
        """After invalidation, re-walk picks up new files."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            assets = root / "Assets"
            assets.mkdir()
            prefab = assets / "Test.prefab"
            prefab.write_text("%YAML 1.1\n", encoding="utf-8")

            service = ReferenceResolverService(project_root=root)
            result1 = service.collect_scope_files(assets)
            (assets / "New.prefab").write_text("%YAML 1.1\n", encoding="utf-8")
            service.invalidate_scope_files_cache()
            result2 = service.collect_scope_files(assets)
            self.assertEqual(len(result1) + 1, len(result2))


class ReferenceResolverEnvelopeTests(unittest.TestCase):
    """B1 — pin every reference-resolver failure path by code, severity,
    and (where reported) GUID / fileID / asset-path values via the
    structured-envelope helper plus inline data-payload equality.

    The project's REF001 / REF002 codes form the contract surface; this
    class is the value-pinning home for them.
    """

    def test_invalid_guid_emits_ref001_with_guid_value(self) -> None:
        """Issue #141 row: a non-hex GUID hits the first REF001 site."""
        from tests._assertion_helpers import assert_error_envelope  # noqa: PLC0415

        svc = ReferenceResolverService(project_root=Path("/fake/project"))
        response = svc.resolve_reference("not-a-guid", "100100000")

        assert_error_envelope(
            response,
            code="REF001",
            severity="error",
            message_match=r"32-character hexadecimal",
            data={
                "guid": "not-a-guid",
                "file_id": "100100000",
                "read_only": True,
            },
        )

    def test_unknown_guid_emits_ref001_with_normalized_guid_value(self) -> None:
        """Issue #141 row: a well-formed GUID absent from the project map
        hits the second REF001 site; the response carries the normalized
        GUID value alongside the input fileID."""
        from tests._assertion_helpers import assert_error_envelope  # noqa: PLC0415

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            _create_sample_project(root)
            svc = ReferenceResolverService(project_root=root)
            response = svc.resolve_reference(MISSING_GUID, "12345")

        assert_error_envelope(
            response,
            code="REF001",
            severity="error",
            message_match=r"not found",
            data={
                "guid": MISSING_GUID,
                "file_id": "12345",
                "read_only": True,
            },
        )
        # missing_asset diagnostic carries the GUID value verbatim by
        # full-string equality on detail and evidence.
        self.assertEqual(1, len(response.diagnostics))
        diag = response.diagnostics[0]
        self.assertEqual("", diag.path)
        self.assertEqual("guid", diag.location)
        self.assertEqual("missing_asset", diag.detail)
        self.assertEqual(f"guid {MISSING_GUID} not found", diag.evidence)

    def test_missing_local_id_emits_ref002_with_asset_path_and_file_id(self) -> None:
        """Issue #141 row: a fileID that does not exist in the resolved asset
        hits REF002; the response carries the asset path, GUID, and fileID.

        The target must be a non-prefab text asset because prefab-external
        fileID validation is intentionally skipped (the validator avoids
        false positives against imported model fileIDs)."""
        from tests._assertion_helpers import assert_error_envelope  # noqa: PLC0415

        asset_guid = "1234567890abcdef1234567890abcdef"
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            asset_path = root / "Assets" / "Settings.asset"
            write_file(
                asset_path,
                """%YAML 1.1
--- !u!114 &11400000
MonoBehaviour:
  m_Name: Settings
""",
            )
            write_file(
                root / "Assets" / "Settings.asset.meta",
                f"""fileFormatVersion: 2
guid: {asset_guid}
""",
            )
            svc = ReferenceResolverService(project_root=root)
            response = svc.resolve_reference(asset_guid, "999999999")

        assert_error_envelope(
            response,
            code="REF002",
            severity="error",
            message_match=r"fileID was not found",
            data={
                "guid": asset_guid,
                "file_id": "999999999",
                "asset_path": "Assets/Settings.asset",
                "read_only": True,
            },
        )
        self.assertEqual(1, len(response.diagnostics))
        diag = response.diagnostics[0]
        self.assertEqual("Assets/Settings.asset", diag.path)
        self.assertEqual("local fileID", diag.location)
        self.assertEqual("missing_local_id", diag.detail)
        self.assertEqual(
            "fileID 999999999 not found in referenced asset", diag.evidence
        )

    # ----- Issue #141: resolve_reference success outcomes -----

    def test_builtin_guid_emits_ref_builtin_success(self) -> None:
        """Built-in identifiers (Unity default-resources / extra) short-
        circuit to ``REF_BUILTIN`` with ``severity=info`` and carry the
        normalized GUID and input fileID under ``data``."""
        from prefab_sentinel.unity_assets import (  # noqa: PLC0415
            UNITY_BUILTIN_EXTRA_GUID,
        )

        svc = ReferenceResolverService(project_root=Path("/fake/project"))
        response = svc.resolve_reference(UNITY_BUILTIN_EXTRA_GUID.upper(), "10303")

        self.assertTrue(response.success)
        self.assertEqual("REF_BUILTIN", response.code)
        self.assertEqual(Severity.INFO, response.severity)
        # Full-payload pinning: normalised GUID, input fileID, read-only flag.
        self.assertEqual(
            {
                "guid": UNITY_BUILTIN_EXTRA_GUID,
                "file_id": "10303",
                "read_only": True,
            },
            response.data,
        )

    def test_resolved_normal_guid_emits_ref_resolved_with_asset_path(self) -> None:
        """A normal (non-builtin) GUID present in the project map and a
        fileID of ``"0"`` (asset-level) returns ``REF_RESOLVED`` with the
        relative asset path under ``data.asset_path``."""
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            _create_sample_project(root)
            svc = ReferenceResolverService(project_root=root)
            response = svc.resolve_reference(BASE_GUID, "0")

        self.assertTrue(response.success)
        self.assertEqual("REF_RESOLVED", response.code)
        self.assertEqual(Severity.INFO, response.severity)
        # Full-payload pinning for the asset-level fileID success path.
        self.assertEqual(
            {
                "guid": BASE_GUID,
                "file_id": "0",
                "asset_path": "Assets/Base.prefab",
                "file_id_validated": True,
                "validation_note": "",
                "read_only": True,
            },
            response.data,
        )

    # ----- Issue #141: scan_broken_references outcomes -----

    def test_scan_broken_references_clean_emits_ref_scan_ok(self) -> None:
        """A scope free of broken references returns ``REF_SCAN_OK``
        with ``severity=info`` and the full quality-gate counter
        payload bound by value."""
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            # Single self-contained asset with a meta file but no
            # references — ``scan_broken_references`` finds zero
            # broken refs.
            write_file(
                root / "Assets" / "Standalone.asset",
                """%YAML 1.1
--- !u!114 &11400000
MonoBehaviour:
  m_Name: Standalone
""",
            )
            write_file(
                root / "Assets" / "Standalone.asset.meta",
                """fileFormatVersion: 2
guid: 1111111111111111111111111111aaaa
""",
            )
            svc = ReferenceResolverService(project_root=root)
            response = svc.scan_broken_references("Assets")

        self.assertTrue(response.success)
        self.assertEqual("REF_SCAN_OK", response.code)
        self.assertEqual(Severity.INFO, response.severity)
        self.assertEqual(
            {
                "scope": "Assets",
                "project_root": ".",
                "scan_project_root": ".",
                "read_only": True,
                "details_included": False,
                "max_diagnostics": 200,
                "exclude_patterns": [],
                "ignore_asset_guids": [],
                "broken_count": 0,
                "broken_occurrences": 0,
                "categories": {"missing_asset": 0, "missing_local_id": 0},
                "categories_occurrences": {"missing_asset": 0, "missing_local_id": 0},
                "ignored_missing_asset_occurrences": 0,
                "ignored_missing_asset_unique_count": 0,
                "returned_diagnostics": 0,
                "scanned_files": 1,
                "scanned_references": 0,
                "skipped_external_prefab_fileid_checks": 0,
                "skipped_external_prefab_fileid_details": [],
                "skipped_unreadable_target_checks": 0,
                "top_ignored_missing_asset_guids": [],
                "top_missing_asset_guids": [],
                "truncated_diagnostics": 0,
                "truncated_hint": None,
                "unreadable_files": 0,
            },
            response.data,
        )
        self.assertEqual([], response.diagnostics)

    def test_scan_broken_references_partial_emits_ref_scan_partial(self) -> None:
        """A scope where every reference resolves but one or more files
        could not be UTF-8/CP932 decoded returns ``REF_SCAN_PARTIAL``
        with ``severity=warning`` and ``unreadable_files == 1``."""
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            # An undecodable .prefab keeps the scope non-broken (no
            # references could be parsed) but raises the partial flag.
            unreadable = root / "Assets" / "Bad.prefab"
            unreadable.parent.mkdir(parents=True, exist_ok=True)
            unreadable.write_bytes(b"\xff\xfe\xfd\xfc not valid utf-8 or cp932 \x80\x81\x82")
            write_file(
                root / "Assets" / "Bad.prefab.meta",
                """fileFormatVersion: 2
guid: 2222222222222222222222222222bbbb
""",
            )
            svc = ReferenceResolverService(project_root=root)
            response = svc.scan_broken_references("Assets", include_diagnostics=True)

        self.assertTrue(response.success)
        self.assertEqual("REF_SCAN_PARTIAL", response.code)
        self.assertEqual(Severity.WARNING, response.severity)
        # Pin the load-bearing partial-scan counters by exact value.
        self.assertEqual(0, response.data["broken_count"])
        self.assertEqual(1, response.data["unreadable_files"])
        self.assertEqual(1, response.data["returned_diagnostics"])
        self.assertEqual(True, response.data["details_included"])
        # The diagnostic detail tag for the partial-scan case binds by
        # full-string equality on detail / location / path / evidence.
        self.assertEqual(1, len(response.diagnostics))
        diag = response.diagnostics[0]
        self.assertEqual("Assets/Bad.prefab", diag.path)
        self.assertEqual("", diag.location)
        self.assertEqual("unreadable_file", diag.detail)
        self.assertEqual(
            "File could not be decoded (UTF-8/CP932). References inside this"
            " file were not validated. Check file encoding (UTF-8 or CP932"
            " expected) and permissions.",
            diag.evidence,
        )

    def test_scan_broken_references_broken_emits_ref_scan_broken_with_categories(self) -> None:
        """Issue #141 row: a scope with broken references returns
        ``REF_SCAN_BROKEN`` with ``severity=error`` and the full
        quality-gate counter payload bound by value (per-category
        map, top-missing-asset list, broken count)."""
        from tests._assertion_helpers import assert_error_envelope  # noqa: PLC0415

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            _create_sample_project(root)
            svc = ReferenceResolverService(project_root=root)
            response = svc.scan_broken_references("Assets")

        assert_error_envelope(
            response,
            code="REF_SCAN_BROKEN",
            severity="error",
            message_match=r"[Bb]roken",
            data={
                "scope": "Assets",
                "project_root": ".",
                "scan_project_root": ".",
                "read_only": True,
                "details_included": False,
                "max_diagnostics": 200,
                "exclude_patterns": [],
                "ignore_asset_guids": [],
                "broken_count": 2,
                "broken_occurrences": 2,
                "categories": {"missing_asset": 1, "missing_local_id": 1},
                "categories_occurrences": {"missing_asset": 1, "missing_local_id": 1},
                "ignored_missing_asset_occurrences": 0,
                "ignored_missing_asset_unique_count": 0,
                "returned_diagnostics": 0,
                "scanned_files": 2,
                "scanned_references": 13,
                "skipped_external_prefab_fileid_checks": 6,
                "skipped_external_prefab_fileid_details": [
                    {
                        "source": "Assets/Variant.prefab",
                        "target_guid": BASE_GUID,
                        "file_id": "100100000",
                    },
                    {
                        "source": "Assets/Variant.prefab",
                        "target_guid": BASE_GUID,
                        "file_id": "999999",
                    },
                    {
                        "source": "Assets/Variant.prefab",
                        "target_guid": BASE_GUID,
                        "file_id": "100100000",
                    },
                    {
                        "source": "Assets/Variant.prefab",
                        "target_guid": BASE_GUID,
                        "file_id": "100100000",
                    },
                    {
                        "source": "Assets/Variant.prefab",
                        "target_guid": BASE_GUID,
                        "file_id": "100100000",
                    },
                    {
                        "source": "Assets/Variant.prefab",
                        "target_guid": BASE_GUID,
                        "file_id": "100100000",
                    },
                ],
                "skipped_unreadable_target_checks": 0,
                "top_ignored_missing_asset_guids": [],
                "top_missing_asset_guids": [
                    {
                        "guid": MISSING_GUID,
                        "occurrences": 1,
                        "asset_name": "",
                    }
                ],
                "truncated_diagnostics": 2,
                "truncated_hint": (
                    "2 broken reference(s) found. Use --details to include"
                    " individual diagnostics."
                ),
                "unreadable_files": 0,
            },
        )

    def test_scan_broken_references_missing_scope_emits_ref404(self) -> None:
        """An unspecified scope (path not present on disk) returns
        ``REF404`` with the input scope echoed back under
        ``data.scope``."""
        from tests._assertion_helpers import assert_error_envelope  # noqa: PLC0415

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            svc = ReferenceResolverService(project_root=root)
            response = svc.scan_broken_references("Assets/Does/Not/Exist")

        assert_error_envelope(
            response,
            code="REF404",
            severity="error",
            message_match=r"does not exist",
            data={
                "scope": "Assets/Does/Not/Exist",
                "read_only": True,
            },
        )

    def test_scan_broken_references_invalid_ignore_entry_emits_ref001(self) -> None:
        """An ignore-asset-guid entry that is not a 32-char hex GUID
        returns ``REF001`` with the offending entry echoed under
        ``data.invalid_ignore_asset_guids``."""
        from tests._assertion_helpers import assert_error_envelope  # noqa: PLC0415

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            _create_sample_project(root)
            svc = ReferenceResolverService(project_root=root)
            response = svc.scan_broken_references(
                "Assets",
                ignore_asset_guids=("not-a-guid",),
            )

        assert_error_envelope(
            response,
            code="REF001",
            severity="error",
            message_match=r"32-character hexadecimal",
            data={
                "scope": "Assets",
                "invalid_ignore_asset_guids": ["not-a-guid"],
                "read_only": True,
            },
        )

    # ----- Issue #141: where_used outcomes -----

    def test_where_used_resolved_emits_ref_where_used_with_pinned_shape(self) -> None:
        """A resolvable GUID with a scope that contains usages returns
        ``REF_WHERE_USED`` with the deterministic fixture count
        (BASE_GUID appears 6 times in Variant.prefab) and the full
        usage entry list bound by value."""
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            _create_sample_project(root)
            svc = ReferenceResolverService(project_root=root)
            response = svc.where_used(BASE_GUID, scope="Assets")

        self.assertTrue(response.success)
        self.assertEqual("REF_WHERE_USED", response.code)
        self.assertEqual(Severity.INFO, response.severity)
        # Full-payload pinning. The 6 usages come from m_SourcePrefab
        # + m_ExternalPrefabRef + 4 modification targets (4 m_LocalRef
        # without GUID is filtered).
        self.assertEqual(
            {
                "guid": BASE_GUID,
                "asset_path": "Assets/Base.prefab",
                "scope": "Assets",
                "scan_project_root": ".",
                "usage_count": 6,
                "returned_usages": 6,
                "truncated_usages": 0,
                "max_usages": 500,
                "scanned_files": 2,
                "exclude_patterns": [],
                "read_only": True,
                "usages": [
                    {
                        "path": "Assets/Variant.prefab",
                        "line": 4,
                        "column": 19,
                        "reference": (
                            f"{{fileID: 100100000, guid: {BASE_GUID}, type: 3}}"
                        ),
                    },
                    {
                        "path": "Assets/Variant.prefab",
                        "line": 5,
                        "column": 24,
                        "reference": (
                            f"{{fileID: 999999, guid: {BASE_GUID}, type: 3}}"
                        ),
                    },
                    {
                        "path": "Assets/Variant.prefab",
                        "line": 8,
                        "column": 15,
                        "reference": (
                            f"{{fileID: 100100000, guid: {BASE_GUID}, type: 3}}"
                        ),
                    },
                    {
                        "path": "Assets/Variant.prefab",
                        "line": 12,
                        "column": 15,
                        "reference": (
                            f"{{fileID: 100100000, guid: {BASE_GUID}, type: 3}}"
                        ),
                    },
                    {
                        "path": "Assets/Variant.prefab",
                        "line": 16,
                        "column": 15,
                        "reference": (
                            f"{{fileID: 100100000, guid: {BASE_GUID}, type: 3}}"
                        ),
                    },
                    {
                        "path": "Assets/Variant.prefab",
                        "line": 20,
                        "column": 15,
                        "reference": (
                            f"{{fileID: 100100000, guid: {BASE_GUID}, type: 3}}"
                        ),
                    },
                ],
            },
            response.data,
        )

    def test_where_used_missing_scope_emits_ref404(self) -> None:
        """A scope path that does not exist returns ``REF404`` with
        the offending scope echoed under ``data.scope``."""
        from tests._assertion_helpers import assert_error_envelope  # noqa: PLC0415

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            _create_sample_project(root)
            svc = ReferenceResolverService(project_root=root)
            response = svc.where_used(BASE_GUID, scope="Assets/NotFound")

        assert_error_envelope(
            response,
            code="REF404",
            severity="error",
            message_match=r"does not exist",
            data={
                "scope": "Assets/NotFound",
                "read_only": True,
            },
        )

    def test_where_used_unknown_guid_emits_ref001(self) -> None:
        """A well-formed but unknown GUID returns ``REF001`` with the
        offending input echoed under ``data.asset_or_guid``."""
        from tests._assertion_helpers import assert_error_envelope  # noqa: PLC0415

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            _create_sample_project(root)
            svc = ReferenceResolverService(project_root=root)
            response = svc.where_used(MISSING_GUID, scope="Assets")

        assert_error_envelope(
            response,
            code="REF001",
            severity="error",
            message_match=r"not found",
            data={
                "asset_or_guid": MISSING_GUID,
                "read_only": True,
            },
        )


class PatchValidatorEnvelopeTests(unittest.TestCase):
    """B3 — pin every error-emission site of the patch-validator code
    vocabulary (SER001 / SER002 / SER003) by code, severity, field /
    property-path, and message regex.
    """

    def test_validate_property_path_empty_emits_ser001(self) -> None:
        from prefab_sentinel.services.property_path import (  # noqa: PLC0415
            validate_property_path,
        )
        from tests._assertion_helpers import assert_error_envelope  # noqa: PLC0415

        response = validate_property_path("")
        assert_error_envelope(
            response,
            code="SER001",
            severity="error",
            message_match=r"empty",
            data={"property_path": ""},
        )

    def test_validate_property_path_negative_subscript_emits_ser002(self) -> None:
        from prefab_sentinel.services.property_path import (  # noqa: PLC0415
            validate_property_path,
        )
        from tests._assertion_helpers import assert_error_envelope  # noqa: PLC0415

        bad_path = "m_List.Array.data[-1]"
        response = validate_property_path(bad_path)
        assert_error_envelope(
            response,
            code="SER002",
            severity="error",
            message_match=r"negative",
            data={"property_path": bad_path},
        )

    # Shared prefab fixture YAML used by SER003 set_component_fields rows.
    _SER003_PREFAB_YAML = (
        "%YAML 1.1\n"
        "%TAG !u! tag:unity3d.com,2011:\n"
        "--- !u!1 &100\n"
        "GameObject:\n"
        "  m_ObjectHideFlags: 0\n"
        "  serializedVersion: 6\n"
        "  m_Component:\n"
        "  - component: {fileID: 200}\n"
        "  - component: {fileID: 300}\n"
        "  m_Layer: 0\n"
        "  m_Name: Cube\n"
        "  m_TagString: Untagged\n"
        "  m_IsActive: 1\n"
        "--- !u!4 &200\n"
        "Transform:\n"
        "  m_ObjectHideFlags: 0\n"
        "  m_GameObject: {fileID: 100}\n"
        "  m_LocalRotation: {x: 0, y: 0, z: 0, w: 1}\n"
        "  m_LocalPosition: {x: 0, y: 0, z: 0}\n"
        "  m_LocalScale: {x: 1, y: 1, z: 1}\n"
        "  m_Children: []\n"
        "  m_Father: {fileID: 0}\n"
        "  m_RootOrder: 0\n"
        "--- !u!23 &300\n"
        "MeshRenderer:\n"
        "  m_ObjectHideFlags: 0\n"
        "  m_GameObject: {fileID: 100}\n"
        "  m_Enabled: 1\n"
    )

    def test_set_component_fields_unknown_property_emits_ser003_envelope(self) -> None:
        """SER003 (property_not_found classification): the supplied
        component exists on the chain but the referenced property does
        not.  Pins the full payload (target, component, property_path,
        suggestions, read_only) and the diagnostic by full-string
        equality on path / location / detail / evidence."""
        from tests._assertion_helpers import assert_error_envelope  # noqa: PLC0415
        from tests.test_mcp_server import _run  # noqa: PLC0415

        with tempfile.TemporaryDirectory() as raw:
            td = Path(raw)
            prefab_path = td / "test.prefab"
            prefab_path.write_text(self._SER003_PREFAB_YAML, encoding="utf-8")
            from prefab_sentinel.mcp_server import create_server  # noqa: PLC0415

            server = create_server()
            _, response = _run(server.call_tool(
                "set_component_fields",
                {
                    "asset_path": str(prefab_path),
                    "symbol_path": "Cube",
                    "component": "MeshRenderer",
                    "fields": {"m_NoSuchField": 0},
                },
            ))

        assert_error_envelope(
            response,
            code="SER003",
            severity="error",
            message_match=r"not found",
            data={
                "target": str(prefab_path),
                "component": "MeshRenderer",
                "property_path": "m_NoSuchField",
                "suggestions": ["m_ObjectHideFlags", "m_GameObject", "m_Enabled"],
                "read_only": True,
            },
        )
        # Diagnostic binds by full-string equality on every field.
        self.assertEqual(1, len(response["diagnostics"]))
        diag = response["diagnostics"][0]
        self.assertEqual(str(prefab_path), diag["path"])
        self.assertEqual(
            "component 'MeshRenderer' property 'm_NoSuchField'",
            diag["location"],
        )
        self.assertEqual("property_not_found", diag["detail"])
        self.assertEqual(
            f"property path 'm_NoSuchField' did not resolve on component"
            f" 'MeshRenderer' for target '{prefab_path}'",
            diag["evidence"],
        )

    def test_set_component_fields_unknown_component_emits_ser003_envelope(self) -> None:
        """SER003 (component_not_found classification): the supplied
        component type is not present on the resolved chain.  Pins the
        full payload (target, component, suggestions, read_only) and
        the diagnostic by full-string equality."""
        from tests._assertion_helpers import assert_error_envelope  # noqa: PLC0415
        from tests.test_mcp_server import _run  # noqa: PLC0415

        with tempfile.TemporaryDirectory() as raw:
            td = Path(raw)
            prefab_path = td / "test.prefab"
            prefab_path.write_text(self._SER003_PREFAB_YAML, encoding="utf-8")
            from prefab_sentinel.mcp_server import create_server  # noqa: PLC0415

            server = create_server()
            _, response = _run(server.call_tool(
                "set_component_fields",
                {
                    "asset_path": str(prefab_path),
                    "symbol_path": "Cube",
                    "component": "NoSuchComponent",
                    "fields": {"m_X": 0},
                },
            ))

        assert_error_envelope(
            response,
            code="SER003",
            severity="error",
            message_match=r"not found",
            data={
                "target": str(prefab_path),
                "component": "NoSuchComponent",
                "suggestions": ["Transform", "MeshRenderer"],
                "read_only": True,
            },
        )
        self.assertEqual(1, len(response["diagnostics"]))
        diag = response["diagnostics"][0]
        self.assertEqual(str(prefab_path), diag["path"])
        self.assertEqual("component 'NoSuchComponent'", diag["location"])
        self.assertEqual("component_not_found", diag["detail"])
        self.assertEqual(
            f"component type 'NoSuchComponent' is not present in the chain"
            f" for target '{prefab_path}'",
            diag["evidence"],
        )

    # ----- Issue #143: validate_property_path family rows -----

    def test_validate_property_path_empty_segment_emits_ser001(self) -> None:
        """Path-shape: ``a..b`` has an empty segment between consecutive
        dots — emits SER001 with the offending path under
        ``data.property_path``."""
        from prefab_sentinel.services.property_path import (  # noqa: PLC0415
            validate_property_path,
        )
        from tests._assertion_helpers import assert_error_envelope  # noqa: PLC0415

        bad_path = "a..b"
        response = validate_property_path(bad_path)
        assert_error_envelope(
            response,
            code="SER001",
            severity="error",
            message_match=r"empty segment",
            data={"property_path": bad_path},
        )

    def test_validate_property_path_unterminated_bracket_emits_ser001(self) -> None:
        """Path-shape: a segment with ``[`` and no ``]`` is rejected by
        the unterminated-bracket SER001 site."""
        from prefab_sentinel.services.property_path import (  # noqa: PLC0415
            validate_property_path,
        )
        from tests._assertion_helpers import assert_error_envelope  # noqa: PLC0415

        bad_path = "m_List.Array.data[3"
        response = validate_property_path(bad_path)
        assert_error_envelope(
            response,
            code="SER001",
            severity="error",
            message_match=r"unterminated",
            data={"property_path": bad_path},
        )

    def test_validate_property_path_empty_subscript_emits_ser001(self) -> None:
        """Path-shape: ``[]`` with no inner text is the empty-subscript
        SER001 site."""
        from prefab_sentinel.services.property_path import (  # noqa: PLC0415
            validate_property_path,
        )
        from tests._assertion_helpers import assert_error_envelope  # noqa: PLC0415

        bad_path = "m_List.Array.data[]"
        response = validate_property_path(bad_path)
        assert_error_envelope(
            response,
            code="SER001",
            severity="error",
            message_match=r"\[\]",
            data={"property_path": bad_path},
        )

    def test_validate_property_path_non_integer_subscript_emits_ser002(self) -> None:
        """Path-index: ``[abc]`` is not an integer — SER002 site."""
        from prefab_sentinel.services.property_path import (  # noqa: PLC0415
            validate_property_path,
        )
        from tests._assertion_helpers import assert_error_envelope  # noqa: PLC0415

        bad_path = "m_List.Array.data[abc]"
        response = validate_property_path(bad_path)
        assert_error_envelope(
            response,
            code="SER002",
            severity="error",
            message_match=r"integer",
            data={"property_path": bad_path},
        )

    def test_validate_property_path_size_with_subscript_emits_ser002(self) -> None:
        """Path-index: ``Array.size[0]`` — ``size`` is scalar and
        cannot be subscripted; SER002 site."""
        from prefab_sentinel.services.property_path import (  # noqa: PLC0415
            validate_property_path,
        )
        from tests._assertion_helpers import assert_error_envelope  # noqa: PLC0415

        bad_path = "m_List.Array.size[0]"
        response = validate_property_path(bad_path)
        assert_error_envelope(
            response,
            code="SER002",
            severity="error",
            message_match=r"size.*scalar|scalar.*size",
            data={"property_path": bad_path},
        )

    def test_validate_property_path_well_formed_emits_pp_ok(self) -> None:
        """Happy-path: a well-formed path returns ``PP_OK`` at
        ``severity=info`` with the path echoed under data."""
        from prefab_sentinel.services.property_path import (  # noqa: PLC0415
            validate_property_path,
        )

        path = "m_Outer.Array.data[0].m_Inner.Array.data[1]"
        response = validate_property_path(path)
        self.assertTrue(response.success)
        self.assertEqual("PP_OK", response.code)
        self.assertEqual(Severity.INFO, response.severity)
        self.assertEqual({"property_path": path}, response.data)

    # ----- Issue #143: validate_op rejection-family rows -----

    @staticmethod
    def _make_serialized_object_service() -> SerializedObjectService:
        """Construct a service instance whose project root is a
        temporary directory.  ``validate_op`` does not consult the
        project tree on the open-mode rejection paths exercised here."""
        return SerializedObjectService(project_root=Path("/tmp"))

    def _run_validate_op(self, op: dict[str, Any]) -> tuple[Any, list[Diagnostic]]:
        """Run ``validate_op`` for one op and return ``(result, diagnostics)``.

        Stable ``target`` and ``index`` keep the diagnostic ``location``
        text predictable across rejection-family rows.
        """
        diagnostics: list[Diagnostic] = []
        service = self._make_serialized_object_service()
        result = validate_op(service, target="Assets/X.prefab", index=0, op=op, diagnostics=diagnostics)
        return result, diagnostics

    def _assert_single_diagnostic(
        self,
        diagnostics: list[Diagnostic],
        *,
        location: str,
        detail: str,
        evidence: str,
    ) -> None:
        """Pin a single rejection diagnostic by full-string equality on
        every field (path / location / detail / evidence)."""
        self.assertEqual(1, len(diagnostics))
        diag = diagnostics[0]
        self.assertEqual("Assets/X.prefab", diag.path)
        self.assertEqual(location, diag.location)
        self.assertEqual(detail, diag.detail)
        self.assertEqual(evidence, diag.evidence)

    def test_validate_op_unsupported_op_emits_schema_error(self) -> None:
        """Rejection family: an op name absent from both ``VALUE_OPS``
        and ``PREFAB_CREATE_OPS`` (e.g. ``"foo"``) appends a
        ``schema_error`` diagnostic with ``unsupported op`` evidence."""
        result, diagnostics = self._run_validate_op({"op": "foo", "component": "X", "path": "m_X"})
        self.assertIsNone(result)
        self._assert_single_diagnostic(
            diagnostics,
            location="ops[0] (foo).op",
            detail="schema_error",
            evidence="unsupported op 'foo'",
        )

    def test_validate_op_create_mode_op_in_open_mode_emits_schema_error(self) -> None:
        """Rejection family: a ``PREFAB_CREATE_OPS``-only name (e.g.
        ``add_component``) in open-mode is rejected with the documented
        create-mode evidence string."""
        result, diagnostics = self._run_validate_op({"op": "add_component", "component": "X", "path": "m_X"})
        self.assertIsNone(result)
        self._assert_single_diagnostic(
            diagnostics,
            location="ops[0] (add_component).op",
            detail="schema_error",
            evidence=(
                "'add_component' is a create-mode operation and cannot be"
                " used in open-mode patch plans. To add components to"
                " existing prefabs, edit the YAML directly or use Unity's"
                " Add Component menu."
            ),
        )

    def test_validate_op_missing_component_emits_schema_error(self) -> None:
        """Rejection family: ``component`` empty or absent emits a
        ``schema_error`` diagnostic with ``component is required`` evidence."""
        result, diagnostics = self._run_validate_op(
            {"op": "set", "component": "", "path": "m_X", "value": 1}
        )
        self.assertIsNone(result)
        self._assert_single_diagnostic(
            diagnostics,
            location="ops[0] (set).component",
            detail="schema_error",
            evidence="component is required",
        )

    def test_validate_op_numeric_fileid_component_emits_likely_fileid(self) -> None:
        """Rejection family: a numeric component string (e.g. ``"123"``)
        appends a ``likely_fileid`` diagnostic *in addition to* the
        ``schema_error`` for the missing path.  Both diagnostics bind
        by full-string equality."""
        result, diagnostics = self._run_validate_op(
            {"op": "set", "component": "123", "path": "", "value": 0}
        )
        self.assertIsNone(result)
        self.assertEqual(2, len(diagnostics))
        self.assertEqual("Assets/X.prefab", diagnostics[0].path)
        self.assertEqual("ops[0] (set).component", diagnostics[0].location)
        self.assertEqual("likely_fileid", diagnostics[0].detail)
        self.assertEqual(
            "component '123' looks like a numeric fileID. The Unity bridge"
            " resolves components by type name (e.g. 'SkinnedMeshRenderer'"
            " or 'TypeName@/hierarchy/path'). Numeric fileIDs will fail"
            " at apply time.",
            diagnostics[0].evidence,
        )
        self.assertEqual("Assets/X.prefab", diagnostics[1].path)
        self.assertEqual("ops[0] (set).path", diagnostics[1].location)
        self.assertEqual("schema_error", diagnostics[1].detail)
        self.assertEqual("path is required", diagnostics[1].evidence)

    def test_validate_op_missing_path_emits_schema_error(self) -> None:
        """Rejection family: empty ``path`` emits ``schema_error`` with
        ``path is required`` evidence."""
        result, diagnostics = self._run_validate_op(
            {"op": "set", "component": "X", "path": "", "value": 0}
        )
        self.assertIsNone(result)
        self._assert_single_diagnostic(
            diagnostics,
            location="ops[0] (set).path",
            detail="schema_error",
            evidence="path is required",
        )

    def test_validate_op_missing_value_for_set_emits_schema_error(self) -> None:
        """Rejection family: ``set`` without a ``value`` key emits
        ``schema_error`` with ``value is required for set`` evidence."""
        result, diagnostics = self._run_validate_op(
            {"op": "set", "component": "X", "path": "m_X"}
        )
        self.assertIsNone(result)
        self._assert_single_diagnostic(
            diagnostics,
            location="ops[0] (set).value",
            detail="schema_error",
            evidence="value is required for set",
        )

    def test_validate_op_handle_prefix_value_emits_warning_on_set(self) -> None:
        """Rejection family: ``set`` with a value that looks like a
        create-mode handle (``$``-prefixed) attaches a ``_warning`` to
        the preview row rather than rejecting; the row is still returned
        and no diagnostic is appended."""
        result, diagnostics = self._run_validate_op(
            {"op": "set", "component": "X", "path": "m_X", "value": "$handle"}
        )
        self.assertIsNotNone(result)
        self.assertEqual(
            "Value '$handle' looks like a create-mode handle. Handle strings"
            " are only resolved in 'target'/'parent' fields. For"
            ' ObjectReference, use {"guid": "...", "fileID": ...} or null.',
            result["_warning"],
        )
        self.assertEqual([], diagnostics)

    def test_validate_op_array_op_without_array_suffix_emits_schema_error(self) -> None:
        """Rejection family: ``insert_array_element`` /
        ``remove_array_element`` whose path does not end in
        ``.Array.data`` is rejected with the array-suffix evidence."""
        result, diagnostics = self._run_validate_op(
            {
                "op": "insert_array_element",
                "component": "X",
                "path": "m_NotArray",
                "index": 0,
                "value": 1,
            }
        )
        self.assertIsNone(result)
        self._assert_single_diagnostic(
            diagnostics,
            location="ops[0] (insert_array_element).path",
            detail="schema_error",
            evidence=(
                "Array operations require path ending with '.Array.data',"
                " got 'm_NotArray'. Example:"
                " 'globalSwitches.Array.data' instead of 'globalSwitches'."
            ),
        )

    def test_validate_op_missing_index_emits_schema_error(self) -> None:
        """Rejection family: ``insert_array_element`` /
        ``remove_array_element`` without ``index`` emits schema_error
        with ``index is required for {op}`` evidence."""
        result, diagnostics = self._run_validate_op(
            {"op": "remove_array_element", "component": "X", "path": "m_X.Array.data"}
        )
        self.assertIsNone(result)
        self._assert_single_diagnostic(
            diagnostics,
            location="ops[0] (remove_array_element).index",
            detail="schema_error",
            evidence="index is required for remove_array_element",
        )

    def test_validate_op_non_integer_index_emits_schema_error(self) -> None:
        """Rejection family: a non-integer ``index`` (here ``"abc"``)
        emits ``schema_error`` with ``index must be an integer``."""
        result, diagnostics = self._run_validate_op(
            {
                "op": "remove_array_element",
                "component": "X",
                "path": "m_X.Array.data",
                "index": "abc",
            }
        )
        self.assertIsNone(result)
        self._assert_single_diagnostic(
            diagnostics,
            location="ops[0] (remove_array_element).index",
            detail="schema_error",
            evidence="index must be an integer",
        )

    def test_validate_op_negative_index_emits_schema_error(self) -> None:
        """Rejection family: a negative integer ``index`` (here ``-1``)
        emits ``schema_error`` with ``index must be >= 0``."""
        result, diagnostics = self._run_validate_op(
            {
                "op": "remove_array_element",
                "component": "X",
                "path": "m_X.Array.data",
                "index": -1,
            }
        )
        self.assertIsNone(result)
        self._assert_single_diagnostic(
            diagnostics,
            location="ops[0] (remove_array_element).index",
            detail="schema_error",
            evidence="index must be >= 0",
        )

    def test_validate_op_missing_value_for_array_insert_emits_schema_error(self) -> None:
        """Rejection family: ``insert_array_element`` without ``value``
        emits ``schema_error`` with
        ``value is required for insert_array_element``."""
        result, diagnostics = self._run_validate_op(
            {
                "op": "insert_array_element",
                "component": "X",
                "path": "m_X.Array.data",
                "index": 0,
            }
        )
        self.assertIsNone(result)
        self._assert_single_diagnostic(
            diagnostics,
            location="ops[0] (insert_array_element).value",
            detail="schema_error",
            evidence="value is required for insert_array_element",
        )


class PrefabVariantServiceTests(unittest.TestCase):
    def test_detect_stale_mixed_categories_returns_pvr001(self) -> None:
        """Mixed categories (duplicate + array-size) yield the umbrella
        code ``PVR001`` whose ``categories`` list pins both classifications."""
        from tests._assertion_helpers import assert_error_envelope  # noqa: PLC0415

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            _create_sample_project(root)
            svc = PrefabVariantService(project_root=root)
            stale = svc.detect_stale_overrides("Assets/Variant.prefab")

        assert_error_envelope(
            stale,
            code="PVR001",
            severity="warning",
            message_match=r"[Ss]tale",
            data={
                "variant_path": "Assets/Variant.prefab",
                "stale_count": 2,
                "categories": ["array_size_mismatch", "duplicate_override"],
                "read_only": True,
            },
        )
        self.assertEqual(2, len(stale.diagnostics))
        # Diagnostic per category, in deterministic order: duplicate then array.
        dup_diag = stale.diagnostics[0]
        self.assertEqual("Assets/Variant.prefab", dup_diag.path)
        self.assertEqual("16:1..20:1", dup_diag.location)
        self.assertEqual("duplicate_override", dup_diag.detail)
        self.assertEqual(
            f"{BASE_GUID}:100100000 / duplicated.path appears 2 times;"
            " later entries shadow earlier entries",
            dup_diag.evidence,
        )
        arr_diag = stale.diagnostics[1]
        self.assertEqual("Assets/Variant.prefab", arr_diag.path)
        self.assertEqual("array_override", arr_diag.location)
        self.assertEqual("array_size_mismatch", arr_diag.detail)
        self.assertEqual(
            f"{BASE_GUID}:100100000 / mic_obj_extra:"
            " size=1 but data index 1 exists",
            arr_diag.evidence,
        )

    def test_detect_stale_duplicate_only_returns_pvr002(self) -> None:
        """Single-category duplicate_override yields ``PVR002``."""
        from tests._assertion_helpers import assert_error_envelope  # noqa: PLC0415

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            write_file(
                root / "Assets" / "Base.prefab",
                "%YAML 1.1\n--- !u!1 &100100000\nGameObject:\n  m_Name: Base\n",
            )
            write_file(
                root / "Assets" / "Base.prefab.meta",
                f"fileFormatVersion: 2\nguid: {BASE_GUID}\n",
            )
            write_file(
                root / "Assets" / "Dup.prefab",
                f"""%YAML 1.1
--- !u!1001 &100100000
PrefabInstance:
  m_SourcePrefab: {{fileID: 100100000, guid: {BASE_GUID}, type: 3}}
  m_Modification:
    m_Modifications:
    - target: {{fileID: 100100000, guid: {BASE_GUID}, type: 3}}
      propertyPath: some.path
      value: a
      objectReference: {{fileID: 0}}
    - target: {{fileID: 100100000, guid: {BASE_GUID}, type: 3}}
      propertyPath: some.path
      value: b
      objectReference: {{fileID: 0}}
""",
            )
            write_file(
                root / "Assets" / "Dup.prefab.meta",
                f"fileFormatVersion: 2\nguid: {VARIANT_GUID}\n",
            )
            svc = PrefabVariantService(project_root=root)
            stale = svc.detect_stale_overrides("Assets/Dup.prefab")

        assert_error_envelope(
            stale,
            code="PVR002",
            severity="warning",
            message_match=r"[Ss]tale",
            data={
                "variant_path": "Assets/Dup.prefab",
                "stale_count": 1,
                "categories": ["duplicate_override"],
                "read_only": True,
            },
        )
        self.assertEqual(1, len(stale.diagnostics))
        diag = stale.diagnostics[0]
        self.assertEqual("Assets/Dup.prefab", diag.path)
        self.assertEqual("7:1..11:1", diag.location)
        self.assertEqual("duplicate_override", diag.detail)
        self.assertEqual(
            f"{BASE_GUID}:100100000 / some.path appears 2 times;"
            " later entries shadow earlier entries",
            diag.evidence,
        )

    def test_detect_stale_array_mismatch_only_returns_pvr003(self) -> None:
        """Single-category array_size_mismatch yields ``PVR003``."""
        from tests._assertion_helpers import assert_error_envelope  # noqa: PLC0415

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            write_file(
                root / "Assets" / "Base.prefab",
                "%YAML 1.1\n--- !u!1 &100100000\nGameObject:\n  m_Name: Base\n",
            )
            write_file(
                root / "Assets" / "Base.prefab.meta",
                f"fileFormatVersion: 2\nguid: {BASE_GUID}\n",
            )
            write_file(
                root / "Assets" / "Arr.prefab",
                f"""%YAML 1.1
--- !u!1001 &100100000
PrefabInstance:
  m_SourcePrefab: {{fileID: 100100000, guid: {BASE_GUID}, type: 3}}
  m_Modification:
    m_Modifications:
    - target: {{fileID: 100100000, guid: {BASE_GUID}, type: 3}}
      propertyPath: items.Array.size
      value: 1
      objectReference: {{fileID: 0}}
    - target: {{fileID: 100100000, guid: {BASE_GUID}, type: 3}}
      propertyPath: items.Array.data[2]
      value: 0
      objectReference: {{fileID: 0}}
""",
            )
            write_file(
                root / "Assets" / "Arr.prefab.meta",
                f"fileFormatVersion: 2\nguid: {VARIANT_GUID}\n",
            )
            svc = PrefabVariantService(project_root=root)
            stale = svc.detect_stale_overrides("Assets/Arr.prefab")

        assert_error_envelope(
            stale,
            code="PVR003",
            severity="warning",
            message_match=r"[Ss]tale",
            data={
                "variant_path": "Assets/Arr.prefab",
                "stale_count": 1,
                "categories": ["array_size_mismatch"],
                "read_only": True,
            },
        )
        self.assertEqual(1, len(stale.diagnostics))
        diag = stale.diagnostics[0]
        self.assertEqual("Assets/Arr.prefab", diag.path)
        self.assertEqual("array_override", diag.location)
        self.assertEqual("array_size_mismatch", diag.detail)
        self.assertEqual(
            f"{BASE_GUID}:100100000 / items: size=1 but data index 2 exists",
            diag.evidence,
        )

    def test_detect_stale_clean_variant_returns_pvr_stale_none(self) -> None:
        """Clean family: a variant with one unique scalar override (no
        duplicates, no array-size mismatch) returns ``PVR_STALE_NONE``
        with ``success=True``, ``severity=INFO``, full-payload pinning,
        and an empty ``diagnostics`` list."""
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            write_file(
                root / "Assets" / "Base.prefab",
                "%YAML 1.1\n--- !u!1 &100100000\nGameObject:\n  m_Name: Base\n",
            )
            write_file(
                root / "Assets" / "Base.prefab.meta",
                f"fileFormatVersion: 2\nguid: {BASE_GUID}\n",
            )
            write_file(
                root / "Assets" / "Clean.prefab",
                f"""%YAML 1.1
--- !u!1001 &100100000
PrefabInstance:
  m_SourcePrefab: {{fileID: 100100000, guid: {BASE_GUID}, type: 3}}
  m_Modification:
    m_Modifications:
    - target: {{fileID: 100100000, guid: {BASE_GUID}, type: 3}}
      propertyPath: some.scalar.path
      value: 42
      objectReference: {{fileID: 0}}
""",
            )
            write_file(
                root / "Assets" / "Clean.prefab.meta",
                f"fileFormatVersion: 2\nguid: {VARIANT_GUID}\n",
            )
            svc = PrefabVariantService(project_root=root)
            stale = svc.detect_stale_overrides("Assets/Clean.prefab")

        self.assertTrue(stale.success)
        self.assertEqual("PVR_STALE_NONE", stale.code)
        self.assertEqual(Severity.INFO, stale.severity)
        self.assertEqual(
            {
                "variant_path": "Assets/Clean.prefab",
                "stale_count": 0,
                "read_only": True,
            },
            stale.data,
        )
        self.assertEqual([], stale.diagnostics)

    # ----- Issue #142: variant load-failure envelopes -----

    def test_variant_load_missing_path_returns_pvr404(self) -> None:
        """A variant path not present on disk fails with ``PVR404`` and
        echoes the input path verbatim."""
        from tests._assertion_helpers import assert_error_envelope  # noqa: PLC0415

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            svc = PrefabVariantService(project_root=root)
            response = svc.list_overrides("Assets/DoesNotExist.prefab")

        assert_error_envelope(
            response,
            code="PVR404",
            severity="error",
            message_match=r"does not exist",
            data={
                "variant_path": "Assets/DoesNotExist.prefab",
                "read_only": True,
            },
        )

    def test_variant_load_decode_failure_returns_pvr400(self) -> None:
        """A variant path that exists but cannot be UTF-8 decoded fails
        with ``PVR400`` and echoes the input path verbatim."""
        from tests._assertion_helpers import assert_error_envelope  # noqa: PLC0415

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            bad = root / "Assets" / "BadBinary.prefab"
            bad.parent.mkdir(parents=True, exist_ok=True)
            bad.write_bytes(b"\xff\xfe\xfd\xfc not valid utf-8 \x80\x81\x82")
            svc = PrefabVariantService(project_root=root)
            response = svc.list_overrides("Assets/BadBinary.prefab")

        assert_error_envelope(
            response,
            code="PVR400",
            severity="error",
            message_match=r"could not be decoded",
            data={
                "variant_path": "Assets/BadBinary.prefab",
                "read_only": True,
            },
        )

    # ----- Issue #142: prefab-variant value-pinning rows -----

    def test_list_overrides_pins_full_payload(self) -> None:
        """Issue #142 row: ``list_overrides`` returns
        ``PVR_OVERRIDES_OK`` whose full payload (override count, the
        ordered ``overrides`` list with every per-entry field, and the
        echoed variant path / read-only flag / component filter) binds
        by value."""
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            _create_sample_project(root)
            svc = PrefabVariantService(project_root=root)
            response = svc.list_overrides("Assets/Variant.prefab")

        self.assertTrue(response.success)
        self.assertEqual("PVR_OVERRIDES_OK", response.code)
        self.assertEqual(Severity.INFO, response.severity)
        self.assertEqual(
            {
                "variant_path": "Assets/Variant.prefab",
                "override_count": 5,
                "component_filter": None,
                "read_only": True,
                "overrides": [
                    {
                        "kind": "array_size",
                        "target_key": f"{BASE_GUID}:100100000",
                        "line": 8,
                        "target_file_id": "100100000",
                        "target_guid": BASE_GUID,
                        "property_path": "mic_obj_extra.Array.size",
                        "value": "1",
                        "object_reference": "{fileID: 0}",
                    },
                    {
                        "kind": "array_data",
                        "target_key": f"{BASE_GUID}:100100000",
                        "line": 12,
                        "target_file_id": "100100000",
                        "target_guid": BASE_GUID,
                        "property_path": "mic_obj_extra.Array.data[1]",
                        "value": "0",
                        "object_reference": "{fileID: 0}",
                    },
                    {
                        "kind": "value",
                        "target_key": f"{BASE_GUID}:100100000",
                        "line": 16,
                        "target_file_id": "100100000",
                        "target_guid": BASE_GUID,
                        "property_path": "duplicated.path",
                        "value": "first",
                        "object_reference": "{fileID: 0}",
                    },
                    {
                        "kind": "value",
                        "target_key": f"{BASE_GUID}:100100000",
                        "line": 20,
                        "target_file_id": "100100000",
                        "target_guid": BASE_GUID,
                        "property_path": "duplicated.path",
                        "value": "second",
                        "object_reference": "{fileID: 0}",
                    },
                    {
                        "kind": "value",
                        "target_key": f"{MISSING_GUID}:100100000",
                        "line": 24,
                        "target_file_id": "100100000",
                        "target_guid": MISSING_GUID,
                        "property_path": "missing.asset",
                        "value": "0",
                        "object_reference": "{fileID: 0}",
                    },
                ],
            },
            response.data,
        )

    def test_parse_overrides_pins_per_entry_tuple_sequence(self) -> None:
        """Issue #142 row: ``parse_overrides`` returns the
        ``OverrideEntry`` dataclass list whose per-entry tuple
        sequence is pinned by exact value, including ``line`` and
        ``target_key``."""
        from prefab_sentinel.services.prefab_variant.overrides import (  # noqa: PLC0415
            parse_overrides,
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            _create_sample_project(root)
            text = (root / "Assets" / "Variant.prefab").read_text(encoding="utf-8")

        entries = parse_overrides(text)
        self.assertEqual(5, len(entries))
        first = entries[0]
        self.assertEqual("100100000", first.target_file_id)
        self.assertEqual(BASE_GUID, first.target_guid)
        self.assertEqual("mic_obj_extra.Array.size", first.property_path)
        self.assertEqual("1", first.value)
        self.assertEqual(f"{BASE_GUID}:100100000", first.target_key)
        # The five entries' lines are stable; pin them by tuple equality
        # so a parser regression that misaligns the line counter fails.
        line_paths = [(entry.line, entry.property_path) for entry in entries]
        self.assertEqual(
            [
                (8, "mic_obj_extra.Array.size"),
                (12, "mic_obj_extra.Array.data[1]"),
                (16, "duplicated.path"),
                (20, "duplicated.path"),
                (24, "missing.asset"),
            ],
            line_paths,
        )

    def test_resolve_prefab_chain_pins_full_payload(self) -> None:
        """Issue #142 row: ``resolve_prefab_chain`` returns
        ``PVR_CHAIN_OK`` whose full payload (variant path, leaf-to-root
        chain list, read-only flag) binds by value."""
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            _create_sample_project(root)
            svc = PrefabVariantService(project_root=root)
            response = svc.resolve_prefab_chain("Assets/Variant.prefab")

        self.assertTrue(response.success)
        self.assertEqual("PVR_CHAIN_OK", response.code)
        self.assertEqual(Severity.INFO, response.severity)
        self.assertEqual(
            {
                "variant_path": "Assets/Variant.prefab",
                "chain": [
                    {"path": "Assets/Variant.prefab", "guid": None},
                    {"path": "Assets/Base.prefab", "guid": None},
                ],
                "read_only": True,
            },
            response.data,
        )

    def test_resolve_chain_values_with_origin_pins_full_payload(self) -> None:
        """Issue #142 row: ``resolve_chain_values_with_origin`` returns
        ``PVR_CHAIN_VALUES_WITH_ORIGIN`` whose full per-entry origin
        annotations bind by value (variant-level overrides have
        ``origin_depth=0`` and the variant path; base values have
        ``origin_depth=1`` and the base path)."""
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            _create_sample_project(root)
            svc = PrefabVariantService(project_root=root)
            response = svc.resolve_chain_values_with_origin("Assets/Variant.prefab")

        self.assertTrue(response.success)
        self.assertEqual("PVR_CHAIN_VALUES_WITH_ORIGIN", response.code)
        self.assertEqual(Severity.INFO, response.severity)
        self.assertEqual(
            {
                "variant_path": "Assets/Variant.prefab",
                "value_count": 5,
                "chain": [
                    {"path": "Assets/Variant.prefab", "depth": 0},
                    {"path": "Assets/Base.prefab", "depth": 1},
                ],
                "values": [
                    {
                        "target_file_id": "100100000",
                        "property_path": "mic_obj_extra.Array.size",
                        "value": "1",
                        "origin_path": "Assets/Variant.prefab",
                        "origin_depth": 0,
                    },
                    {
                        "target_file_id": "100100000",
                        "property_path": "mic_obj_extra.Array.data[1]",
                        "value": "0",
                        "origin_path": "Assets/Variant.prefab",
                        "origin_depth": 0,
                    },
                    {
                        "target_file_id": "100100000",
                        "property_path": "duplicated.path",
                        "value": "first",
                        "origin_path": "Assets/Variant.prefab",
                        "origin_depth": 0,
                    },
                    {
                        "target_file_id": "100100000",
                        "property_path": "missing.asset",
                        "value": "0",
                        "origin_path": "Assets/Variant.prefab",
                        "origin_depth": 0,
                    },
                    {
                        "target_file_id": "100100000",
                        "property_path": "m_Name",
                        "value": "Base",
                        "origin_path": "Assets/Base.prefab",
                        "origin_depth": 1,
                    },
                ],
                "read_only": True,
            },
            response.data,
        )

    def test_compute_effective_values_pins_full_payload(self) -> None:
        """Issue #142 row: ``compute_effective_values`` returns
        ``PVR_EFFECTIVE_OK`` whose full payload (variant path,
        component filter, read-only flag, value count, and the
        last-write-wins ``effective_values`` list) binds by value."""
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            _create_sample_project(root)
            svc = PrefabVariantService(project_root=root)
            response = svc.compute_effective_values("Assets/Variant.prefab")

        self.assertTrue(response.success)
        self.assertEqual("PVR_EFFECTIVE_OK", response.code)
        self.assertEqual(Severity.INFO, response.severity)
        self.assertEqual(
            {
                "variant_path": "Assets/Variant.prefab",
                "value_count": 4,
                "component_filter": None,
                "read_only": True,
                "effective_values": [
                    {
                        "target_key": f"{BASE_GUID}:100100000",
                        "target_guid": BASE_GUID,
                        "target_file_id": "100100000",
                        "property_path": "mic_obj_extra.Array.size",
                        "value": "1",
                        "object_reference": "{fileID: 0}",
                        "line": "8",
                    },
                    {
                        "target_key": f"{BASE_GUID}:100100000",
                        "target_guid": BASE_GUID,
                        "target_file_id": "100100000",
                        "property_path": "mic_obj_extra.Array.data[1]",
                        "value": "0",
                        "object_reference": "{fileID: 0}",
                        "line": "12",
                    },
                    {
                        "target_key": f"{BASE_GUID}:100100000",
                        "target_guid": BASE_GUID,
                        "target_file_id": "100100000",
                        "property_path": "duplicated.path",
                        "value": "second",
                        "object_reference": "{fileID: 0}",
                        "line": "20",
                    },
                    {
                        "target_key": f"{MISSING_GUID}:100100000",
                        "target_guid": MISSING_GUID,
                        "target_file_id": "100100000",
                        "property_path": "missing.asset",
                        "value": "0",
                        "object_reference": "{fileID: 0}",
                        "line": "24",
                    },
                ],
            },
            response.data,
        )


class ModelFileSuffixDiagnosticTests(unittest.TestCase):
    """Regression coverage for issues #76 and #78.

    ``walk_chain_levels`` must emit a ``model_file_base`` diagnostic for every
    suffix in ``MODEL_FILE_SUFFIXES`` (issue #78) and an ``unreadable_file``
    diagnostic for non-model binaries that fail UTF-8 decode (issue #76).
    """

    # One entry per ``MODEL_FILE_SUFFIXES`` element. The list is fixed here on
    # purpose: enumerating from the constant would let a regression that drops
    # a suffix go undetected.
    MODEL_SUFFIX_CASES: tuple[tuple[str, str], ...] = (
        (".fbx", "ff00ff00ff00ff00ff00ff00ff00ff00"),
        (".blend", "bb00bb00bb00bb00bb00bb00bb00bb00"),
        (".gltf", "a100a100a100a100a100a100a100a100"),
        (".glb", "a200a200a200a200a200a200a200a200"),
        (".obj", "ab00ab00ab00ab00ab00ab00ab00ab00"),
    )

    def _build_variant_referencing(
        self, root: Path, base_filename: str, base_guid: str
    ) -> None:
        base_path = root / "Assets" / base_filename
        base_path.parent.mkdir(parents=True, exist_ok=True)
        base_path.write_bytes(b"\x81\x00\xff\xfe")
        write_file(
            root / "Assets" / f"{base_filename}.meta",
            f"fileFormatVersion: 2\nguid: {base_guid}\n",
        )
        variant_stem = f"{Path(base_filename).stem}Variant"
        write_file(
            root / "Assets" / f"{variant_stem}.prefab",
            f"""%YAML 1.1
--- !u!1001 &100100000
PrefabInstance:
  m_SourcePrefab: {{fileID: 100100000, guid: {base_guid}, type: 3}}
""",
        )
        write_file(
            root / "Assets" / f"{variant_stem}.prefab.meta",
            f"fileFormatVersion: 2\nguid: {VARIANT_GUID}\n",
        )

    def test_model_suffix_emits_model_file_base_diagnostic(self) -> None:
        """T-76/78-A: parametrized over every ``MODEL_FILE_SUFFIXES`` entry."""
        for suffix, base_guid in self.MODEL_SUFFIX_CASES:
            with self.subTest(suffix=suffix):
                with tempfile.TemporaryDirectory() as temp_dir:
                    root = Path(temp_dir)
                    base_filename = f"Model{suffix}"
                    self._build_variant_referencing(root, base_filename, base_guid)
                    variant_stem = f"{Path(base_filename).stem}Variant"
                    svc = PrefabVariantService(project_root=root)
                    chain = svc.resolve_prefab_chain(f"Assets/{variant_stem}.prefab")
                    self.assertTrue(chain.success)
                    self.assertEqual("PVR_CHAIN_WARN", chain.code)
                    model_diags = [
                        d for d in chain.diagnostics if d.detail == "model_file_base"
                    ]
                    self.assertEqual(1, len(model_diags))
                    self.assertEqual(
                        f"Base asset is a model file ({suffix}). "
                        "Cannot decode as YAML. "
                        "Use editor_list_children for runtime hierarchy inspection.",
                        model_diags[0].evidence,
                    )

    def test_non_model_binary_emits_unreadable_file_diagnostic(self) -> None:
        """T-76-B: a non-model binary produces ``unreadable_file``, not
        ``model_file_base``."""
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            bin_guid = "ee00ee00ee00ee00ee00ee00ee00ee00"
            self._build_variant_referencing(root, "Unknown.bin", bin_guid)
            svc = PrefabVariantService(project_root=root)
            chain = svc.resolve_prefab_chain("Assets/UnknownVariant.prefab")
            self.assertTrue(chain.success)
            unreadable = [
                d for d in chain.diagnostics if d.detail == "unreadable_file"
            ]
            self.assertEqual(1, len(unreadable))
            self.assertEqual("unable to decode source prefab", unreadable[0].evidence)
            self.assertFalse(
                any(d.detail == "model_file_base" for d in chain.diagnostics)
            )


class RuntimeValidationServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        _isolate_bridge_dispatch_env(self)

    def test_runtime_validation_setup_clears_bridge_env(self) -> None:
        """The class setUp must pop the two bridge dispatch env vars even
        when they are exported by the parent process; the matching cleanup
        must restore them after the test method finishes.
        """
        # The class setUp ran before this body, so the vars should already
        # be cleared in this test's process state.
        self.assertNotIn("UNITYTOOL_BRIDGE_MODE", os.environ)
        self.assertNotIn("UNITYTOOL_BRIDGE_WATCH_DIR", os.environ)

        # Exercise the setUp/cleanup lifecycle on a fresh instance with
        # the two vars deliberately exported in the parent state.
        os.environ["UNITYTOOL_BRIDGE_MODE"] = "editor"
        os.environ["UNITYTOOL_BRIDGE_WATCH_DIR"] = "/tmp/__sentinel_watch__"
        try:
            fresh = type(self)("test_compile_udonsharp_returns_skip_without_runtime_env")
            fresh.setUp()
            try:
                self.assertNotIn("UNITYTOOL_BRIDGE_MODE", os.environ)
                self.assertNotIn("UNITYTOOL_BRIDGE_WATCH_DIR", os.environ)
            finally:
                fresh.doCleanups()
            self.assertEqual("editor", os.environ["UNITYTOOL_BRIDGE_MODE"])
            self.assertEqual(
                "/tmp/__sentinel_watch__", os.environ["UNITYTOOL_BRIDGE_WATCH_DIR"]
            )
        finally:
            os.environ.pop("UNITYTOOL_BRIDGE_MODE", None)
            os.environ.pop("UNITYTOOL_BRIDGE_WATCH_DIR", None)

    def test_compile_udonsharp_returns_skip_without_runtime_env(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            _create_sample_project(root)
            svc = RuntimeValidationService(project_root=root)

            # Pop only the bridge-related runtime env vars (other env keys
            # retained); ``clear=True`` would also strip mutmut's own state
            # variable and trip the trampoline (#156).
            unitytool_keys = [
                key for key in os.environ if key.startswith("UNITYTOOL_")
            ]
            with patch.dict(os.environ, {}, clear=False):
                for key in unitytool_keys:
                    os.environ.pop(key, None)
                response = svc.compile_udonsharp()

            self.assertTrue(response.success)
            self.assertEqual("RUN_COMPILE_SKIPPED", response.code)

    def test_compile_udonsharp_runs_unity_command_when_configured(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            _create_sample_project(root)
            runner = root / "unity_runner.py"
            write_fake_runtime_runner(runner)
            svc = RuntimeValidationService(project_root=root)

            with patch.dict(
                os.environ,
                {
                    "UNITYTOOL_UNITY_COMMAND": f'"{sys.executable}" "{runner}"',
                    "UNITYTOOL_UNITY_PROJECT_PATH": str(root),
                },
                clear=False,
            ):
                response = svc.compile_udonsharp()

            self.assertTrue(response.success)
            self.assertEqual("RUN_COMPILE_OK", response.code)
            self.assertEqual(3, response.data["udon_program_count"])
            self.assertTrue(response.data["executed"])

    def test_run_clientsim_returns_missing_scene_error(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            _create_sample_project(root)
            svc = RuntimeValidationService(project_root=root)

            response = svc.run_clientsim("Assets/MissingScene.unity", "default")

            self.assertFalse(response.success)
            self.assertEqual("RUN002", response.code)

    def test_run_clientsim_runs_unity_command_when_configured(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            _create_sample_project(root)
            write_file(
                root / "Assets" / "Scenes" / "Smoke.unity",
                """%YAML 1.1
--- !u!1 &1
GameObject:
  m_Name: Smoke
""",
            )
            runner = root / "unity_runner.py"
            write_fake_runtime_runner(runner)
            svc = RuntimeValidationService(project_root=root)

            with patch.dict(
                os.environ,
                {
                    "UNITYTOOL_UNITY_COMMAND": f'"{sys.executable}" "{runner}"',
                    "UNITYTOOL_UNITY_PROJECT_PATH": str(root),
                },
                clear=False,
            ):
                response = svc.run_clientsim("Assets/Scenes/Smoke.unity", "default")

            self.assertTrue(response.success)
            self.assertEqual("RUN_CLIENTSIM_OK", response.code)
            self.assertTrue(response.data["clientsim_ready"])
            self.assertTrue(response.data["executed"])
            self.assertEqual(".", response.data["project_root"])

    def test_classify_errors_detects_known_categories(self) -> None:
        svc = RuntimeValidationService(project_root=Path.cwd())
        response = svc.classify_errors(
            [
                "Broken PPtr in file",
                "NullReferenceException in UdonBehaviour",
                "There can be only one active EventSystem",
            ]
        )

        self.assertFalse(response.success)
        self.assertEqual("RUN001", response.code)
        self.assertEqual(1, response.data["count_by_category"]["BROKEN_PPTR"])
        self.assertEqual(1, response.data["count_by_category"]["UDON_NULLREF"])
        self.assertEqual(1, response.data["count_by_category"]["DUPLICATE_EVENTSYSTEM"])

    def test_orchestrator_validate_runtime_pipeline(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            _create_sample_project(root)
            write_file(
                root / "Assets" / "Scenes" / "Smoke.unity",
                """%YAML 1.1
--- !u!1 &1
GameObject:
  m_Name: Smoke
""",
            )
            write_file(
                root / "Logs" / "Editor.log",
                "NullReferenceException in UdonBehaviour\n",
            )

            orchestrator = Phase1Orchestrator.default(project_root=root)
            response = orchestrator.validate_runtime(
                scene_path="Assets/Scenes/Smoke.unity",
                log_file="Logs/Editor.log",
            )

            self.assertFalse(response.success)
            self.assertEqual("VALIDATE_RUNTIME_RESULT", response.code)
            self.assertEqual("critical", response.severity.value)
            step_codes = [
                step["result"]["code"]
                for step in response.data["steps"]
                if isinstance(step, dict) and isinstance(step.get("result"), dict)
            ]
            self.assertIn("RUN_LOG_COLLECTED", step_codes)
            self.assertIn("RUN001", step_codes)

    def test_orchestrator_validate_runtime_pipeline_uses_runtime_runner_when_configured(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            _create_sample_project(root)
            write_file(
                root / "Assets" / "Scenes" / "Smoke.unity",
                """%YAML 1.1
--- !u!1 &1
GameObject:
  m_Name: Smoke
""",
            )
            runner = root / "unity_runner.py"
            write_fake_runtime_runner(runner)

            orchestrator = Phase1Orchestrator.default(project_root=root)
            with patch.dict(
                os.environ,
                {
                    "UNITYTOOL_UNITY_COMMAND": f'"{sys.executable}" "{runner}"',
                    "UNITYTOOL_UNITY_PROJECT_PATH": str(root),
                },
                clear=False,
            ):
                response = orchestrator.validate_runtime(
                    scene_path="Assets/Scenes/Smoke.unity",
                )

            self.assertTrue(response.success)
            self.assertEqual("VALIDATE_RUNTIME_RESULT", response.code)
            step_codes = [
                step["result"]["code"]
                for step in response.data["steps"]
                if isinstance(step, dict) and isinstance(step.get("result"), dict)
            ]
            self.assertIn("RUN_COMPILE_OK", step_codes)
            self.assertIn("RUN_CLIENTSIM_OK", step_codes)
            self.assertIn("RUN_ASSERT_OK", step_codes)


class SerializedObjectServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        _isolate_bridge_dispatch_env(self)

    def test_load_patch_plan_normalizes_v2_resources(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "patch.json"
            path.write_text(
                json.dumps(
                    {
                        "plan_version": 2,
                        "resources": [
                            {
                                "id": "first",
                                "kind": "json",
                                "path": "Assets/StateA.json",
                                "mode": "open",
                            },
                            {
                                "id": "second",
                                "kind": "prefab",
                                "path": "Assets/StateB.prefab",
                                "mode": "open",
                            },
                        ],
                        "ops": [
                            {
                                "resource": "first",
                                "op": "set",
                                "component": "Example.Component",
                                "path": "nested.value",
                                "value": 1,
                            },
                            {
                                "resource": "second",
                                "op": "set",
                                "component": "Example.Component",
                                "path": "enabled",
                                "value": True,
                            },
                        ],
                        "postconditions": [
                            {
                                "type": "asset_exists",
                                "resource": "second",
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            loaded = load_patch_plan(path)

            self.assertEqual(2, loaded["plan_version"])
            self.assertEqual(2, len(loaded["resources"]))
            self.assertEqual("first", loaded["resources"][0]["id"])
            self.assertEqual("json", loaded["resources"][0]["kind"])
            self.assertEqual("second", loaded["ops"][1]["resource"])
            self.assertEqual(
                [{"type": "asset_exists", "resource": "second"}],
                loaded["postconditions"],
            )

    def test_compute_patch_plan_sha256_returns_expected_digest(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "patch.json"
            path.write_text('{"target":"Assets/Test.prefab","ops":[]}', encoding="utf-8")

            digest = compute_patch_plan_sha256(path)
            expected = hashlib.sha256(path.read_bytes()).hexdigest()

            self.assertEqual(expected, digest)

    def test_compute_patch_plan_hmac_sha256_returns_expected_digest(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "patch.json"
            path.write_text('{"target":"Assets/Test.prefab","ops":[]}', encoding="utf-8")
            key = "local-signing-key"

            digest = compute_patch_plan_hmac_sha256(path, key)
            expected = hmac.new(key.encode("utf-8"), path.read_bytes(), hashlib.sha256).hexdigest()

            self.assertEqual(expected, digest)

    def test_dry_run_patch_validates_plan_and_returns_preview(self) -> None:
        svc = SerializedObjectService()
        response = svc.dry_run_patch(
            target="Assets/Variant.prefab",
            ops=[
                {
                    "op": "set",
                    "component": "Example.Component",
                    "path": "items.Array.size",
                    "value": 2,
                },
                {
                    "op": "remove_array_element",
                    "component": "Example.Component",
                    "path": "items.Array.data",
                    "index": 1,
                },
            ],
        )

        self.assertTrue(response.success)
        self.assertEqual("SER_DRY_RUN_OK", response.code)
        self.assertEqual(2, response.data["op_count"])
        self.assertEqual(2, len(response.data["diff"]))

    def test_set_property_rejects_ser002_path(self) -> None:
        """T21: a set op with a negative-index propertyPath must produce
        ``code=SER002`` from the service layer, with no YAML mutation.
        The pre-validator short-circuits before any op-specific logic
        runs, so we can assert directly on the dry_run envelope."""
        svc = SerializedObjectService()
        response = svc.dry_run_patch(
            target="Assets/Variant.prefab",
            ops=[
                {
                    "op": "set",
                    "component": "Example.Component",
                    "path": "m_Foo.Array.data[-1]",
                    "value": 2,
                },
            ],
        )

        self.assertFalse(response.success)
        self.assertEqual("SER002", response.code)
        self.assertEqual(Severity.ERROR, response.severity)

    def test_dry_run_patch_returns_schema_error(self) -> None:
        svc = SerializedObjectService()
        response = svc.dry_run_patch(
            target="Assets/Variant.prefab",
            ops=[{"op": "set", "component": "", "path": "x"}],
        )

        self.assertFalse(response.success)
        self.assertEqual("SER_PLAN_INVALID", response.code)
        self.assertTrue(response.diagnostics)

    def test_dry_run_patch_warns_on_unresolved_before(self) -> None:
        svc = SerializedObjectService()
        response = svc.dry_run_patch(
            target="Assets/Variant.prefab",
            ops=[
                {
                    "op": "set",
                    "component": "Example.Component",
                    "path": "someProperty",
                    "value": 42,
                },
            ],
        )

        self.assertTrue(response.success)
        self.assertEqual("SER_DRY_RUN_OK", response.code)
        self.assertEqual(Severity.WARNING, response.severity)
        self.assertTrue(response.diagnostics)
        self.assertEqual("unresolved_before_value", response.diagnostics[0].detail)

    def test_dry_run_array_op_missing_suffix(self) -> None:
        """insert_array_element with path missing .Array.data should fail."""
        svc = SerializedObjectService()
        response = svc.dry_run_patch(
            target="Assets/Variant.prefab",
            ops=[
                {
                    "op": "insert_array_element",
                    "component": "Example.Component",
                    "path": "globalSwitches",
                    "index": 0,
                    "value": {"fileID": 0},
                },
            ],
        )

        self.assertFalse(response.success)
        self.assertEqual(Severity.ERROR, response.severity)
        self.assertTrue(response.diagnostics)
        self.assertEqual("schema_error", response.diagnostics[0].detail)
        self.assertIn(".Array.data", response.diagnostics[0].evidence)

    def test_dry_run_array_op_with_suffix(self) -> None:
        """insert_array_element with correct .Array.data path should pass validation."""
        svc = SerializedObjectService()
        response = svc.dry_run_patch(
            target="Assets/Variant.prefab",
            ops=[
                {
                    "op": "insert_array_element",
                    "component": "Example.Component",
                    "path": "globalSwitches.Array.data",
                    "index": 0,
                    "value": {"fileID": 0},
                },
            ],
        )

        self.assertTrue(response.success)
        self.assertEqual("SER_DRY_RUN_OK", response.code)

    def test_dry_run_remove_array_missing_suffix(self) -> None:
        """remove_array_element with path missing .Array.data should fail."""
        svc = SerializedObjectService()
        response = svc.dry_run_patch(
            target="Assets/Variant.prefab",
            ops=[
                {
                    "op": "remove_array_element",
                    "component": "Example.Component",
                    "path": "seats",
                    "index": 0,
                },
            ],
        )

        self.assertFalse(response.success)
        self.assertEqual(Severity.ERROR, response.severity)
        self.assertTrue(response.diagnostics)
        self.assertEqual("schema_error", response.diagnostics[0].detail)
        self.assertIn(".Array.data", response.diagnostics[0].evidence)

    def test_dry_run_add_component_in_open_mode(self) -> None:
        """add_component in open-mode patch should give a helpful create-mode message."""
        svc = SerializedObjectService()
        response = svc.dry_run_patch(
            target="Assets/Variant.prefab",
            ops=[
                {
                    "op": "add_component",
                    "component": "MyScript",
                    "path": "dummy",
                },
            ],
        )

        self.assertFalse(response.success)
        self.assertEqual(Severity.ERROR, response.severity)
        self.assertTrue(response.diagnostics)
        self.assertIn("create-mode", response.diagnostics[0].evidence)

    def test_dry_run_unknown_op(self) -> None:
        """Truly unknown op should give generic error."""
        svc = SerializedObjectService()
        response = svc.dry_run_patch(
            target="Assets/Variant.prefab",
            ops=[
                {
                    "op": "foobar",
                    "component": "X",
                    "path": "y",
                },
            ],
        )

        self.assertFalse(response.success)
        self.assertEqual(Severity.ERROR, response.severity)
        self.assertIn("unsupported op", response.diagnostics[0].evidence)

    def test_dry_run_handle_in_value(self) -> None:
        """set op with handle-like value should produce WARNING."""
        svc = SerializedObjectService()
        response = svc.dry_run_patch(
            target="Assets/Variant.prefab",
            ops=[
                {
                    "op": "set",
                    "component": "Example.Component",
                    "path": "targetRef",
                    "value": "$root",
                },
            ],
        )

        self.assertTrue(response.success)
        self.assertEqual(Severity.WARNING, response.severity)
        self.assertTrue(response.diagnostics)
        diag_details = [d.detail for d in response.diagnostics]
        self.assertIn("handle_in_value", diag_details)

    def test_dry_run_handle_prefix_go(self) -> None:
        """set op with go_ prefixed value should produce WARNING."""
        svc = SerializedObjectService()
        response = svc.dry_run_patch(
            target="Assets/Variant.prefab",
            ops=[
                {
                    "op": "set",
                    "component": "Example.Component",
                    "path": "childRef",
                    "value": "go_child",
                },
            ],
        )

        self.assertTrue(response.success)
        self.assertEqual(Severity.WARNING, response.severity)
        diag_details = [d.detail for d in response.diagnostics]
        self.assertIn("handle_in_value", diag_details)

    def test_dry_run_patch_supports_material_asset_root_mutation_ops(self) -> None:
        svc = SerializedObjectService()
        response = svc.dry_run_patch(
            target="Assets/Generated/Material.mat",
            ops=[
                {
                    "op": "set",
                    "target": "$asset",
                    "path": "m_Name",
                    "value": "GeneratedMaterial",
                }
            ],
        )

        self.assertTrue(response.success)
        self.assertEqual("SER_DRY_RUN_OK", response.code)
        self.assertEqual(1, len(response.data["diff"]))

    def test_dry_run_resource_plan_supports_prefab_create_mode(self) -> None:
        svc = SerializedObjectService()
        response = svc.dry_run_resource_plan(
            resource={
                "id": "prefab",
                "kind": "prefab",
                "path": "Assets/Generated/New.prefab",
                "mode": "create",
            },
            ops=[
                {"op": "create_prefab", "name": "GeneratedRoot"},
                {"op": "save"},
            ],
        )

        self.assertTrue(response.success)
        self.assertEqual("SER_DRY_RUN_OK", response.code)
        self.assertEqual("create", response.data["mode"])
        self.assertEqual(2, len(response.data["diff"]))

    def test_dry_run_resource_plan_supports_prefab_hierarchy_ops(self) -> None:
        svc = SerializedObjectService()
        response = svc.dry_run_resource_plan(
            resource={
                "id": "prefab",
                "kind": "prefab",
                "path": "Assets/Generated/New.prefab",
                "mode": "create",
            },
            ops=[
                {"op": "create_prefab", "name": "GeneratedRoot", "result": "prefab_root"},
                {
                    "op": "create_game_object",
                    "name": "ChildA",
                    "parent": "$root",
                    "result": "child_a",
                },
                {
                    "op": "rename_object",
                    "target": "$child_a",
                    "name": "ChildRenamed",
                },
                {"op": "save"},
            ],
        )

        self.assertTrue(response.success)
        self.assertEqual("SER_DRY_RUN_OK", response.code)
        self.assertEqual(4, len(response.data["diff"]))

    def test_dry_run_resource_plan_supports_prefab_component_ops(self) -> None:
        svc = SerializedObjectService()
        response = svc.dry_run_resource_plan(
            resource={
                "id": "prefab",
                "kind": "prefab",
                "path": "Assets/Generated/New.prefab",
                "mode": "create",
            },
            ops=[
                {"op": "create_prefab", "name": "GeneratedRoot"},
                {
                    "op": "create_game_object",
                    "name": "ChildA",
                    "parent": "$root",
                    "result": "child_a",
                },
                {
                    "op": "add_component",
                    "target": "$child_a",
                    "type": "UnityEngine.BoxCollider",
                    "result": "child_collider",
                },
                {
                    "op": "find_component",
                    "target": "$child_a",
                    "type": "UnityEngine.BoxCollider",
                    "result": "resolved_collider",
                },
                {"op": "remove_component", "target": "$resolved_collider"},
                {"op": "save"},
            ],
        )

        self.assertTrue(response.success)
        self.assertEqual("SER_DRY_RUN_OK", response.code)
        self.assertEqual(6, len(response.data["diff"]))

    def test_dry_run_resource_plan_supports_prefab_component_mutation_ops(self) -> None:
        svc = SerializedObjectService()
        response = svc.dry_run_resource_plan(
            resource={
                "id": "prefab",
                "kind": "prefab",
                "path": "Assets/Generated/New.prefab",
                "mode": "create",
            },
            ops=[
                {"op": "create_prefab", "name": "GeneratedRoot"},
                {
                    "op": "add_component",
                    "target": "$root",
                    "type": "UnityEngine.BoxCollider",
                    "result": "root_collider",
                },
                {
                    "op": "set",
                    "target": "$root_collider",
                    "path": "m_IsTrigger",
                    "value": True,
                },
                {
                    "op": "insert_array_element",
                    "target": "$root_collider",
                    "path": "m_LayerOverridePriority.Array.data",
                    "index": 0,
                    "value": 1,
                },
                {"op": "save"},
            ],
        )

        self.assertTrue(response.success)
        self.assertEqual("SER_DRY_RUN_OK", response.code)
        self.assertEqual(5, len(response.data["diff"]))

    def test_dry_run_resource_plan_supports_material_create_mode(self) -> None:
        svc = SerializedObjectService()
        response = svc.dry_run_resource_plan(
            resource={
                "id": "material",
                "kind": "material",
                "path": "Assets/Generated/New.mat",
                "mode": "create",
            },
            ops=[
                {
                    "op": "create_asset",
                    "shader": "Standard",
                    "result": "generated_material",
                },
                {
                    "op": "set",
                    "target": "$generated_material",
                    "path": "m_Name",
                    "value": "GeneratedMaterial",
                },
                {"op": "save"},
            ],
        )

        self.assertTrue(response.success)
        self.assertEqual("SER_DRY_RUN_OK", response.code)
        self.assertEqual(3, len(response.data["diff"]))

    def test_dry_run_resource_plan_supports_scriptable_object_create_mode(self) -> None:
        svc = SerializedObjectService()
        response = svc.dry_run_resource_plan(
            resource={
                "id": "data",
                "kind": "asset",
                "path": "Assets/Generated/New.asset",
                "mode": "create",
            },
            ops=[
                {
                    "op": "create_asset",
                    "type": "Example.GeneratedData",
                    "result": "generated_asset",
                },
                {
                    "op": "set",
                    "target": "$generated_asset",
                    "path": "m_Name",
                    "value": "GeneratedData",
                },
                {"op": "save"},
            ],
        )

        self.assertTrue(response.success)
        self.assertEqual("SER_DRY_RUN_OK", response.code)
        self.assertEqual(3, len(response.data["diff"]))

    def test_dry_run_resource_plan_supports_scene_create_mode(self) -> None:
        svc = SerializedObjectService()
        response = svc.dry_run_resource_plan(
            resource={
                "id": "scene",
                "kind": "scene",
                "path": "Assets/Generated/New.unity",
                "mode": "create",
            },
            ops=[
                {"op": "create_scene"},
                {
                    "op": "create_game_object",
                    "name": "RootA",
                    "parent": "$scene",
                    "result": "root_a",
                },
                {
                    "op": "add_component",
                    "target": "$root_a",
                    "type": "UnityEngine.BoxCollider",
                    "result": "root_collider",
                },
                {
                    "op": "set",
                    "target": "$root_collider",
                    "path": "m_IsTrigger",
                    "value": True,
                },
                {"op": "save_scene"},
            ],
        )

        self.assertTrue(response.success)
        self.assertEqual("SER_DRY_RUN_OK", response.code)
        self.assertEqual(5, len(response.data["diff"]))

    def test_dry_run_resource_plan_supports_scene_open_mode(self) -> None:
        svc = SerializedObjectService()
        response = svc.dry_run_resource_plan(
            resource={
                "id": "scene",
                "kind": "scene",
                "path": "Assets/Generated/Existing.unity",
                "mode": "open",
            },
            ops=[
                {"op": "open_scene"},
                {
                    "op": "create_game_object",
                    "name": "RootA",
                    "parent": "$scene",
                    "result": "root_a",
                },
                {"op": "save_scene"},
            ],
        )

        self.assertTrue(response.success)
        self.assertEqual("SER_DRY_RUN_OK", response.code)
        self.assertEqual(3, len(response.data["diff"]))

    def test_dry_run_scene_find_component_accepts_scene_handle(self) -> None:
        svc = SerializedObjectService()
        response = svc.dry_run_resource_plan(
            resource={
                "id": "scene",
                "kind": "scene",
                "path": "Assets/Test.unity",
                "mode": "open",
            },
            ops=[
                {"op": "open_scene"},
                {
                    "op": "find_component",
                    "target": "$scene",
                    "type": "Camera",
                    "result": "cam",
                },
                {"op": "save_scene"},
            ],
        )
        self.assertTrue(response.success)
        self.assertEqual("SER_DRY_RUN_OK", response.code)

    def test_dry_run_scene_add_component_rejects_scene_handle(self) -> None:
        svc = SerializedObjectService()
        response = svc.dry_run_resource_plan(
            resource={
                "id": "scene",
                "kind": "scene",
                "path": "Assets/Test.unity",
                "mode": "open",
            },
            ops=[
                {"op": "open_scene"},
                {
                    "op": "add_component",
                    "target": "$scene",
                    "type": "Light",
                    "result": "light",
                },
                {"op": "save_scene"},
            ],
        )
        self.assertFalse(response.success)
        # Should report handle kind mismatch
        self.assertTrue(
            any("game object" in d.detail or "game object" in d.evidence for d in response.diagnostics)
        )

    def test_dry_run_resource_plan_rejects_non_prefab_target_for_create_mode(self) -> None:
        svc = SerializedObjectService()
        response = svc.dry_run_resource_plan(
            resource={
                "id": "prefab",
                "kind": "prefab",
                "path": "Assets/Generated/New.asset",
                "mode": "create",
            },
            ops=[
                {"op": "create_prefab", "name": "GeneratedRoot"},
                {"op": "save"},
            ],
        )

        self.assertFalse(response.success)
        self.assertEqual("SER_PLAN_INVALID", response.code)
        self.assertTrue(response.diagnostics)

    def test_dry_run_resource_plan_rejects_material_create_without_shader(self) -> None:
        svc = SerializedObjectService()
        response = svc.dry_run_resource_plan(
            resource={
                "id": "material",
                "kind": "material",
                "path": "Assets/Generated/New.mat",
                "mode": "create",
            },
            ops=[
                {"op": "create_asset", "result": "generated_material"},
                {"op": "save"},
            ],
        )

        self.assertFalse(response.success)
        self.assertEqual("SER_PLAN_INVALID", response.code)
        self.assertTrue(response.diagnostics)

    def test_dry_run_resource_plan_rejects_scene_instantiate_prefab_without_prefab(self) -> None:
        svc = SerializedObjectService()
        response = svc.dry_run_resource_plan(
            resource={
                "id": "scene",
                "kind": "scene",
                "path": "Assets/Generated/New.unity",
                "mode": "create",
            },
            ops=[
                {"op": "create_scene"},
                {
                    "op": "instantiate_prefab",
                    "parent": "$scene",
                    "result": "instance_root",
                },
                {"op": "save_scene"},
            ],
        )

        self.assertFalse(response.success)
        self.assertEqual("SER_PLAN_INVALID", response.code)
        self.assertTrue(response.diagnostics)

    def test_dry_run_resource_plan_rejects_wrong_handle_kind_for_component_op(self) -> None:
        svc = SerializedObjectService()
        response = svc.dry_run_resource_plan(
            resource={
                "id": "prefab",
                "kind": "prefab",
                "path": "Assets/Generated/New.prefab",
                "mode": "create",
            },
            ops=[
                {"op": "create_prefab", "name": "GeneratedRoot"},
                {"op": "remove_component", "target": "$root"},
                {"op": "save"},
            ],
        )

        self.assertFalse(response.success)
        self.assertEqual("SER_PLAN_INVALID", response.code)
        self.assertTrue(response.diagnostics)

    def test_dry_run_resource_plan_rejects_unknown_handle_in_prefab_hierarchy_ops(self) -> None:
        svc = SerializedObjectService()
        response = svc.dry_run_resource_plan(
            resource={
                "id": "prefab",
                "kind": "prefab",
                "path": "Assets/Generated/New.prefab",
                "mode": "create",
            },
            ops=[
                {"op": "create_prefab", "name": "GeneratedRoot"},
                {
                    "op": "create_game_object",
                    "name": "ChildA",
                    "parent": "$missing",
                    "result": "child_a",
                },
                {"op": "save"},
            ],
        )

        self.assertFalse(response.success)
        self.assertEqual("SER_PLAN_INVALID", response.code)
        self.assertTrue(response.diagnostics)

    def test_create_plan_handle_value_in_set(self) -> None:
        """set op with {"handle": "c_comp"} in create mode should pass dry-run."""
        svc = SerializedObjectService()
        response = svc.dry_run_resource_plan(
            resource={
                "id": "prefab",
                "kind": "prefab",
                "path": "Assets/Generated/New.prefab",
                "mode": "create",
            },
            ops=[
                {"op": "create_prefab", "name": "Root"},
                {
                    "op": "add_component",
                    "target": "$root",
                    "type": "UnityEngine.Camera",
                    "result": "c_cam",
                },
                {
                    "op": "set",
                    "target": "$c_cam",
                    "path": "cameraRef",
                    "value": {"handle": "c_cam"},
                },
                {"op": "save"},
            ],
        )

        self.assertTrue(response.success)
        self.assertEqual("SER_DRY_RUN_OK", response.code)

    def test_create_plan_unknown_handle_in_set(self) -> None:
        """set op with {"handle": "missing"} should produce schema_error."""
        svc = SerializedObjectService()
        response = svc.dry_run_resource_plan(
            resource={
                "id": "prefab",
                "kind": "prefab",
                "path": "Assets/Generated/New.prefab",
                "mode": "create",
            },
            ops=[
                {"op": "create_prefab", "name": "Root"},
                {
                    "op": "set",
                    "target": "$root",
                    "path": "cameraRef",
                    "value": {"handle": "missing"},
                },
                {"op": "save"},
            ],
        )

        self.assertFalse(response.success)
        self.assertEqual("SER_PLAN_INVALID", response.code)
        diag_details = [d.detail for d in response.diagnostics]
        self.assertIn("schema_error", diag_details)

    def test_create_plan_handle_value_in_insert_array(self) -> None:
        """insert_array_element with {"handle": "c_comp"} in create mode should pass."""
        svc = SerializedObjectService()
        response = svc.dry_run_resource_plan(
            resource={
                "id": "prefab",
                "kind": "prefab",
                "path": "Assets/Generated/New.prefab",
                "mode": "create",
            },
            ops=[
                {"op": "create_prefab", "name": "Root"},
                {
                    "op": "add_component",
                    "target": "$root",
                    "type": "UnityEngine.Camera",
                    "result": "c_cam",
                },
                {
                    "op": "insert_array_element",
                    "target": "$c_cam",
                    "path": "refs.Array.data",
                    "index": 0,
                    "value": {"handle": "root"},
                },
                {"op": "save"},
            ],
        )

        self.assertTrue(response.success)
        self.assertEqual("SER_DRY_RUN_OK", response.code)

    def test_dry_run_resource_plan_rejects_save_before_final_op(self) -> None:
        svc = SerializedObjectService()
        response = svc.dry_run_resource_plan(
            resource={
                "id": "prefab",
                "kind": "prefab",
                "path": "Assets/Generated/New.prefab",
                "mode": "create",
            },
            ops=[
                {"op": "create_prefab", "name": "GeneratedRoot"},
                {"op": "save"},
                {
                    "op": "create_game_object",
                    "name": "ChildA",
                    "parent": "$root",
                    "result": "child_a",
                },
            ],
        )

        self.assertFalse(response.success)
        self.assertEqual("SER_PLAN_INVALID", response.code)
        self.assertTrue(response.diagnostics)

    def test_apply_and_save_updates_json_target(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            target = root / "state.json"
            target.write_text(
                json.dumps({"items": [1, 2], "nested": {"value": 10}}),
                encoding="utf-8",
            )

            svc = SerializedObjectService()
            response = svc.apply_and_save(
                target=str(target),
                ops=[
                    {
                        "op": "set",
                        "component": "Example.Component",
                        "path": "nested.value",
                        "value": 42,
                    },
                    {
                        "op": "insert_array_element",
                        "component": "Example.Component",
                        "path": "items.Array.data",
                        "index": 0,
                        "value": 0,
                    },
                    {
                        "op": "remove_array_element",
                        "component": "Example.Component",
                        "path": "items.Array.data",
                        "index": 1,
                    },
                ],
            )

            self.assertTrue(response.success)
            self.assertEqual("SER_APPLY_OK", response.code)
            updated = json.loads(target.read_text(encoding="utf-8"))
            self.assertEqual({"items": [0, 2], "nested": {"value": 42}}, updated)

    def test_apply_resource_plan_updates_open_json_target(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            target = root / "state.json"
            target.write_text(
                json.dumps({"nested": {"value": 10}}),
                encoding="utf-8",
            )

            svc = SerializedObjectService()
            response = svc.apply_resource_plan(
                resource={
                    "path": str(target),
                    "mode": "open",
                },
                ops=[
                    {
                        "op": "set",
                        "component": "Example.Component",
                        "path": "nested.value",
                        "value": 42,
                    }
                ],
            )

            self.assertTrue(response.success)
            self.assertEqual("SER_APPLY_OK", response.code)
            self.assertEqual(
                42,
                json.loads(target.read_text(encoding="utf-8"))["nested"]["value"],
            )

    def test_apply_and_save_rejects_non_json_target(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            target = root / "state.prefab"
            target.write_text("%YAML 1.1\n", encoding="utf-8")

            svc = SerializedObjectService()
            response = svc.apply_and_save(
                target=str(target),
                ops=[
                    {
                        "op": "set",
                        "component": "Example.Component",
                        "path": "nested.value",
                        "value": 42,
                    }
                ],
            )

            self.assertFalse(response.success)
            self.assertEqual("SER_UNSUPPORTED_TARGET", response.code)

    def test_apply_and_save_uses_unity_bridge_for_prefab_target(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            target = root / "state.prefab"
            target.write_text("%YAML 1.1\n", encoding="utf-8")
            bridge = root / "bridge.py"
            bridge.write_text(
                """
import json
import sys

request = json.load(sys.stdin)
print(
    json.dumps(
        {
            "success": True,
            "severity": "info",
            "code": "SER_APPLY_OK",
            "message": "Bridge apply completed.",
            "data": {
                "target": request.get("target", ""),
                "op_count": len(request.get("ops", [])),
                "applied": len(request.get("ops", [])),
                "read_only": False,
                "executed": True,
            },
            "diagnostics": [],
        }
    )
)
""".strip(),
                encoding="utf-8",
            )

            svc = SerializedObjectService(bridge_command=(sys.executable, str(bridge)))
            response = svc.apply_and_save(
                target=str(target),
                ops=[
                    {
                        "op": "set",
                        "component": "Example.Component",
                        "path": "nested.value",
                        "value": 42,
                    }
                ],
            )

            self.assertTrue(response.success)
            self.assertEqual("SER_APPLY_OK", response.code)
            self.assertEqual(1, response.data["applied"])

    def test_apply_and_save_uses_unity_bridge_for_material_target(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            target = root / "state.mat"
            target.write_text("%YAML 1.1\n", encoding="utf-8")
            bridge = root / "bridge.py"
            bridge.write_text(
                """
import json
import sys

request = json.load(sys.stdin)
print(
    json.dumps(
        {
            "success": True,
            "severity": "info",
            "code": "SER_APPLY_OK",
            "message": "Bridge apply completed.",
            "data": {
                "target": request.get("target", ""),
                "resource_kind": request.get("resources", [{}])[0].get("kind", ""),
                "op_count": len(request.get("ops", [])),
                "applied": len(request.get("ops", [])),
                "read_only": False,
                "executed": True,
            },
            "diagnostics": [],
        }
    )
)
""".strip(),
                encoding="utf-8",
            )

            svc = SerializedObjectService(bridge_command=(sys.executable, str(bridge)))
            response = svc.apply_and_save(
                target=str(target),
                ops=[
                    {
                        "op": "set",
                        "target": "$asset",
                        "path": "m_Name",
                        "value": "GeneratedMaterial",
                    }
                ],
            )

            self.assertTrue(response.success)
            self.assertEqual("SER_APPLY_OK", response.code)
            self.assertEqual("material", response.data["resource_kind"])

    def test_apply_and_save_rejects_bridge_protocol_version_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            target = root / "state.prefab"
            target.write_text("%YAML 1.1\n", encoding="utf-8")
            bridge = root / "bridge.py"
            bridge.write_text(
                """
import json
import sys

json.load(sys.stdin)
print(
    json.dumps(
        {
            "protocol_version": 999,
            "success": True,
            "severity": "info",
            "code": "SER_APPLY_OK",
            "message": "Bridge apply completed.",
            "data": {"applied": 1},
            "diagnostics": [],
        }
    )
)
""".strip(),
                encoding="utf-8",
            )

            svc = SerializedObjectService(bridge_command=(sys.executable, str(bridge)))
            response = svc.apply_and_save(
                target=str(target),
                ops=[
                    {
                        "op": "set",
                        "component": "Example.Component",
                        "path": "nested.value",
                        "value": 42,
                    }
                ],
            )

            self.assertFalse(response.success)
            self.assertEqual("SER_BRIDGE_PROTOCOL_VERSION", response.code)

    def test_apply_and_save_rejects_bridge_command_outside_allowlist(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            target = root / "state.prefab"
            target.write_text("%YAML 1.1\n", encoding="utf-8")

            svc = SerializedObjectService(bridge_command=("forbidden-bridge", "run"))
            response = svc.apply_and_save(
                target=str(target),
                ops=[
                    {
                        "op": "set",
                        "component": "Example.Component",
                        "path": "nested.value",
                        "value": 42,
                    }
                ],
            )

            self.assertFalse(response.success)
            self.assertEqual("SER_BRIDGE_DENIED", response.code)

    def test_apply_resource_plan_uses_unity_bridge_for_prefab_create_mode(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            bridge = root / "bridge.py"
            bridge.write_text(
                """
import json
import sys

request = json.load(sys.stdin)
print(
    json.dumps(
        {
            "success": True,
            "severity": "info",
            "code": "SER_APPLY_OK",
            "message": "Bridge apply completed.",
            "data": {
                "target": request.get("target", ""),
                "mode": request.get("resources", [{}])[0].get("mode", ""),
                "op_count": len(request.get("ops", [])),
                "applied": len(request.get("ops", [])),
                "read_only": False,
                "executed": True,
            },
            "diagnostics": [],
        }
    )
)
""".strip(),
                encoding="utf-8",
            )

            svc = SerializedObjectService(bridge_command=(sys.executable, str(bridge)))
            response = svc.apply_resource_plan(
                resource={
                    "id": "prefab",
                    "kind": "prefab",
                    "path": str(root / "Assets" / "Generated" / "New.prefab"),
                    "mode": "create",
                },
                ops=[
                    {"op": "create_prefab", "name": "GeneratedRoot"},
                    {"op": "save"},
                ],
            )

            self.assertTrue(response.success)
            self.assertEqual("SER_APPLY_OK", response.code)
            self.assertEqual("create", response.data["mode"])
            self.assertEqual(2, response.data["applied"])

    def test_apply_resource_plan_forwards_prefab_hierarchy_ops_to_bridge(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            bridge = root / "bridge.py"
            bridge.write_text(
                """
import json
import sys

request = json.load(sys.stdin)
print(
    json.dumps(
        {
            "success": True,
            "severity": "info",
            "code": "SER_APPLY_OK",
            "message": "Bridge apply completed.",
            "data": {
                "target": request.get("target", ""),
                "mode": request.get("resources", [{}])[0].get("mode", ""),
                "op_count": len(request.get("ops", [])),
                "applied": len(request.get("ops", [])),
                "request_ops": request.get("ops", []),
                "read_only": False,
                "executed": True,
            },
            "diagnostics": [],
        }
    )
)
""".strip(),
                encoding="utf-8",
            )

            svc = SerializedObjectService(bridge_command=(sys.executable, str(bridge)))
            response = svc.apply_resource_plan(
                resource={
                    "id": "prefab",
                    "kind": "prefab",
                    "path": str(root / "Assets" / "Generated" / "New.prefab"),
                    "mode": "create",
                },
                ops=[
                    {"op": "create_prefab", "name": "GeneratedRoot", "result": "prefab_root"},
                    {
                        "op": "create_game_object",
                        "name": "ChildA",
                        "parent": "$root",
                        "result": "child_a",
                    },
                    {"op": "rename_object", "target": "$child_a", "name": "ChildRenamed"},
                    {"op": "save"},
                ],
            )

            self.assertTrue(response.success)
            self.assertEqual("SER_APPLY_OK", response.code)
            self.assertEqual("create", response.data["mode"])
            self.assertEqual("create_game_object", response.data["request_ops"][1]["op"])
            self.assertEqual("$root", response.data["request_ops"][1]["parent"])

    def test_apply_resource_plan_forwards_prefab_component_ops_to_bridge(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            bridge = root / "bridge.py"
            bridge.write_text(
                """
import json
import sys

request = json.load(sys.stdin)
print(
    json.dumps(
        {
            "success": True,
            "severity": "info",
            "code": "SER_APPLY_OK",
            "message": "Bridge apply completed.",
            "data": {
                "target": request.get("target", ""),
                "mode": request.get("resources", [{}])[0].get("mode", ""),
                "op_count": len(request.get("ops", [])),
                "applied": len(request.get("ops", [])),
                "request_ops": request.get("ops", []),
                "read_only": False,
                "executed": True,
            },
            "diagnostics": [],
        }
    )
)
""".strip(),
                encoding="utf-8",
            )

            svc = SerializedObjectService(bridge_command=(sys.executable, str(bridge)))
            response = svc.apply_resource_plan(
                resource={
                    "id": "prefab",
                    "kind": "prefab",
                    "path": str(root / "Assets" / "Generated" / "New.prefab"),
                    "mode": "create",
                },
                ops=[
                    {"op": "create_prefab", "name": "GeneratedRoot"},
                    {
                        "op": "create_game_object",
                        "name": "ChildA",
                        "parent": "$root",
                        "result": "child_a",
                    },
                    {
                        "op": "add_component",
                        "target": "$child_a",
                        "type": "UnityEngine.BoxCollider",
                        "result": "child_collider",
                    },
                    {"op": "remove_component", "target": "$child_collider"},
                    {"op": "save"},
                ],
            )

            self.assertTrue(response.success)
            self.assertEqual("SER_APPLY_OK", response.code)
            self.assertEqual("add_component", response.data["request_ops"][2]["op"])
            self.assertEqual(
                "UnityEngine.BoxCollider",
                response.data["request_ops"][2]["type"],
            )

    def test_apply_resource_plan_forwards_prefab_component_mutation_ops_to_bridge(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            bridge = root / "bridge.py"
            bridge.write_text(
                """
import json
import sys

request = json.load(sys.stdin)
print(
    json.dumps(
        {
            "success": True,
            "severity": "info",
            "code": "SER_APPLY_OK",
            "message": "Bridge apply completed.",
            "data": {
                "target": request.get("target", ""),
                "mode": request.get("resources", [{}])[0].get("mode", ""),
                "op_count": len(request.get("ops", [])),
                "applied": len(request.get("ops", [])),
                "request_ops": request.get("ops", []),
                "read_only": False,
                "executed": True,
            },
            "diagnostics": [],
        }
    )
)
""".strip(),
                encoding="utf-8",
            )

            svc = SerializedObjectService(bridge_command=(sys.executable, str(bridge)))
            response = svc.apply_resource_plan(
                resource={
                    "id": "prefab",
                    "kind": "prefab",
                    "path": str(root / "Assets" / "Generated" / "New.prefab"),
                    "mode": "create",
                },
                ops=[
                    {"op": "create_prefab", "name": "GeneratedRoot"},
                    {
                        "op": "add_component",
                        "target": "$root",
                        "type": "UnityEngine.BoxCollider",
                        "result": "root_collider",
                    },
                    {
                        "op": "set",
                        "target": "$root_collider",
                        "path": "m_IsTrigger",
                        "value": True,
                    },
                    {"op": "save"},
                ],
            )

            self.assertTrue(response.success)
            self.assertEqual("SER_APPLY_OK", response.code)
            self.assertEqual("set", response.data["request_ops"][2]["op"])
            self.assertEqual("$root_collider", response.data["request_ops"][2]["target"])
            self.assertEqual("m_IsTrigger", response.data["request_ops"][2]["path"])

    def test_apply_resource_plan_forwards_material_create_ops_to_bridge(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            bridge = root / "bridge.py"
            bridge.write_text(
                """
import json
import sys

request = json.load(sys.stdin)
print(
    json.dumps(
        {
            "success": True,
            "severity": "info",
            "code": "SER_APPLY_OK",
            "message": "Bridge apply completed.",
            "data": {
                "target": request.get("target", ""),
                "mode": request.get("resources", [{}])[0].get("mode", ""),
                "kind": request.get("resources", [{}])[0].get("kind", ""),
                "op_count": len(request.get("ops", [])),
                "applied": len(request.get("ops", [])),
                "request_ops": request.get("ops", []),
                "read_only": False,
                "executed": True,
            },
            "diagnostics": [],
        }
    )
)
""".strip(),
                encoding="utf-8",
            )

            svc = SerializedObjectService(bridge_command=(sys.executable, str(bridge)))
            response = svc.apply_resource_plan(
                resource={
                    "id": "material",
                    "kind": "material",
                    "path": str(root / "Assets" / "Generated" / "New.mat"),
                    "mode": "create",
                },
                ops=[
                    {
                        "op": "create_asset",
                        "shader": "Standard",
                        "result": "generated_material",
                    },
                    {
                        "op": "set",
                        "target": "$generated_material",
                        "path": "m_Name",
                        "value": "GeneratedMaterial",
                    },
                    {"op": "save"},
                ],
            )

            self.assertTrue(response.success)
            self.assertEqual("SER_APPLY_OK", response.code)
            self.assertEqual("create", response.data["mode"])
            self.assertEqual("material", response.data["kind"])
            self.assertEqual("create_asset", response.data["request_ops"][0]["op"])
            self.assertEqual("Standard", response.data["request_ops"][0]["shader"])

    def test_apply_resource_plan_forwards_material_open_ops_to_bridge(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            bridge = root / "bridge.py"
            bridge.write_text(
                """
import json
import sys

request = json.load(sys.stdin)
print(
    json.dumps(
        {
            "success": True,
            "severity": "info",
            "code": "SER_APPLY_OK",
            "message": "Bridge apply completed.",
            "data": {
                "target": request.get("target", ""),
                "mode": request.get("resources", [{}])[0].get("mode", ""),
                "kind": request.get("resources", [{}])[0].get("kind", ""),
                "op_count": len(request.get("ops", [])),
                "applied": len(request.get("ops", [])),
                "request_ops": request.get("ops", []),
                "read_only": False,
                "executed": True,
            },
            "diagnostics": [],
        }
    )
)
""".strip(),
                encoding="utf-8",
            )

            svc = SerializedObjectService(bridge_command=(sys.executable, str(bridge)))
            response = svc.apply_resource_plan(
                resource={
                    "id": "material",
                    "kind": "material",
                    "path": str(root / "Assets" / "Generated" / "Existing.mat"),
                    "mode": "open",
                },
                ops=[
                    {
                        "op": "set",
                        "target": "$asset",
                        "path": "m_Name",
                        "value": "ExistingMaterial",
                    }
                ],
            )

            self.assertTrue(response.success)
            self.assertEqual("SER_APPLY_OK", response.code)
            self.assertEqual("open", response.data["mode"])
            self.assertEqual("material", response.data["kind"])
            self.assertEqual("$asset", response.data["request_ops"][0]["target"])

    def test_apply_resource_plan_forwards_scene_create_ops_to_bridge(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            bridge = root / "bridge.py"
            bridge.write_text(
                """
import json
import sys

request = json.load(sys.stdin)
print(
    json.dumps(
        {
            "success": True,
            "severity": "info",
            "code": "SER_APPLY_OK",
            "message": "Bridge apply completed.",
            "data": {
                "target": request.get("target", ""),
                "mode": request.get("resources", [{}])[0].get("mode", ""),
                "kind": request.get("resources", [{}])[0].get("kind", ""),
                "op_count": len(request.get("ops", [])),
                "applied": len(request.get("ops", [])),
                "request_ops": request.get("ops", []),
                "read_only": False,
                "executed": True,
            },
            "diagnostics": [],
        }
    )
)
""".strip(),
                encoding="utf-8",
            )

            svc = SerializedObjectService(bridge_command=(sys.executable, str(bridge)))
            response = svc.apply_resource_plan(
                resource={
                    "id": "scene",
                    "kind": "scene",
                    "path": str(root / "Assets" / "Generated" / "New.unity"),
                    "mode": "create",
                },
                ops=[
                    {"op": "create_scene"},
                    {
                        "op": "instantiate_prefab",
                        "prefab": "Assets/Prefabs/Example.prefab",
                        "parent": "$scene",
                        "result": "instance_root",
                    },
                    {"op": "save_scene"},
                ],
            )

            self.assertTrue(response.success)
            self.assertEqual("SER_APPLY_OK", response.code)
            self.assertEqual("create", response.data["mode"])
            self.assertEqual("scene", response.data["kind"])
            self.assertEqual("instantiate_prefab", response.data["request_ops"][1]["op"])
            self.assertEqual(
                "Assets/Prefabs/Example.prefab",
                response.data["request_ops"][1]["prefab"],
            )

    def test_apply_resource_plan_forwards_scene_open_ops_to_bridge(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            bridge = root / "bridge.py"
            bridge.write_text(
                """
import json
import sys

request = json.load(sys.stdin)
print(
    json.dumps(
        {
            "success": True,
            "severity": "info",
            "code": "SER_APPLY_OK",
            "message": "Bridge apply completed.",
            "data": {
                "target": request.get("target", ""),
                "mode": request.get("resources", [{}])[0].get("mode", ""),
                "kind": request.get("resources", [{}])[0].get("kind", ""),
                "op_count": len(request.get("ops", [])),
                "applied": len(request.get("ops", [])),
                "request_ops": request.get("ops", []),
                "read_only": False,
                "executed": True,
            },
            "diagnostics": [],
        }
    )
)
""".strip(),
                encoding="utf-8",
            )

            svc = SerializedObjectService(bridge_command=(sys.executable, str(bridge)))
            response = svc.apply_resource_plan(
                resource={
                    "id": "scene",
                    "kind": "scene",
                    "path": str(root / "Assets" / "Generated" / "Existing.unity"),
                    "mode": "open",
                },
                ops=[
                    {"op": "open_scene"},
                    {
                        "op": "create_game_object",
                        "name": "GeneratedRoot",
                        "parent": "$scene",
                        "result": "generated_root",
                    },
                    {"op": "save_scene"},
                ],
            )

            self.assertTrue(response.success)
            self.assertEqual("SER_APPLY_OK", response.code)
            self.assertEqual("open", response.data["mode"])
            self.assertEqual("scene", response.data["kind"])
            self.assertEqual("open_scene", response.data["request_ops"][0]["op"])
            self.assertEqual("$scene", response.data["request_ops"][1]["parent"])

    def test_orchestrator_patch_apply_confirm_gate(self) -> None:
        orchestrator = Phase1Orchestrator.default()
        response = orchestrator.patch_apply(
            plan={
                "plan_version": 2,
                "resources": [
                    {
                        "id": "variant",
                        "kind": "prefab",
                        "path": "Assets/Variant.prefab",
                        "mode": "open",
                    }
                ],
                "ops": [
                    {
                        "resource": "variant",
                        "op": "set",
                        "component": "Example.Component",
                        "path": "items.Array.size",
                        "value": 3,
                    }
                ],
            },
            dry_run=False,
            confirm=False,
        )

        self.assertFalse(response.success)
        self.assertEqual("PATCH_APPLY_RESULT", response.code)
        step_codes = [step["result"]["code"] for step in response.data["steps"]]
        self.assertIn("SER_CONFIRM_REQUIRED", step_codes)

    def test_orchestrator_patch_apply_confirm_executes_for_json_target(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            target = root / "state.json"
            target.write_text(
                json.dumps({"items": [1, 2], "nested": {"value": 10}}),
                encoding="utf-8",
            )

            orchestrator = Phase1Orchestrator.default(project_root=root)
            response = orchestrator.patch_apply(
                plan={
                    "plan_version": 2,
                    "resources": [
                        {
                            "id": "state",
                            "kind": "json",
                            "path": str(target),
                            "mode": "open",
                        }
                    ],
                    "ops": [
                        {
                            "resource": "state",
                            "op": "set",
                            "component": "Example.Component",
                            "path": "nested.value",
                            "value": 42,
                        }
                    ],
                },
                dry_run=False,
                confirm=True,
            )

            self.assertTrue(response.success)
            self.assertEqual("PATCH_APPLY_RESULT", response.code)
            step_codes = [step["result"]["code"] for step in response.data["steps"]]
            self.assertIn("SER_APPLY_OK", step_codes)

    def test_orchestrator_patch_apply_enforces_asset_exists_postcondition(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            target = root / "state.json"
            target.write_text(
                json.dumps({"nested": {"value": 10}}),
                encoding="utf-8",
            )

            orchestrator = Phase1Orchestrator.default(project_root=root)
            response = orchestrator.patch_apply(
                plan={
                    "plan_version": 2,
                    "resources": [
                        {
                            "id": "state",
                            "kind": "json",
                            "path": str(target),
                            "mode": "open",
                        }
                    ],
                    "ops": [
                        {
                            "resource": "state",
                            "op": "set",
                            "component": "Example.Component",
                            "path": "nested.value",
                            "value": 42,
                        }
                    ],
                    "postconditions": [
                        {
                            "type": "asset_exists",
                            "resource": "state",
                        }
                    ],
                },
                dry_run=False,
                confirm=True,
            )

            self.assertTrue(response.success)
            step_codes = [step["result"]["code"] for step in response.data["steps"]]
            self.assertIn("POST_ASSET_EXISTS_OK", step_codes)

    def test_orchestrator_patch_apply_fails_broken_refs_postcondition(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            _create_sample_project(root)
            target = root / "state.json"
            target.write_text(
                json.dumps({"nested": {"value": 10}}),
                encoding="utf-8",
            )

            orchestrator = Phase1Orchestrator.default(project_root=root)
            response = orchestrator.patch_apply(
                plan={
                    "plan_version": 2,
                    "resources": [
                        {
                            "id": "state",
                            "kind": "json",
                            "path": str(target),
                            "mode": "open",
                        }
                    ],
                    "ops": [
                        {
                            "resource": "state",
                            "op": "set",
                            "component": "Example.Component",
                            "path": "nested.value",
                            "value": 42,
                        }
                    ],
                    "postconditions": [
                        {
                            "type": "broken_refs",
                            "scope": "Assets",
                            "expected_count": 0,
                        }
                    ],
                },
                dry_run=False,
                confirm=True,
            )

            self.assertFalse(response.success)
            self.assertTrue(response.data["fail_fast_triggered"])
            step_codes = [step["result"]["code"] for step in response.data["steps"]]
            self.assertIn("POST_BROKEN_REFS_FAILED", step_codes)

    def test_orchestrator_patch_apply_stops_on_preflight_reference_error(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            _create_sample_project(root)
            target = root / "state.json"
            target.write_text(json.dumps({"nested": {"value": 10}}), encoding="utf-8")

            orchestrator = Phase1Orchestrator.default(project_root=root)
            response = orchestrator.patch_apply(
                plan={
                    "plan_version": 2,
                    "resources": [
                        {
                            "id": "state",
                            "kind": "json",
                            "path": str(target),
                            "mode": "open",
                        }
                    ],
                    "ops": [
                        {
                            "resource": "state",
                            "op": "set",
                            "component": "Example.Component",
                            "path": "nested.value",
                            "value": 42,
                        }
                    ],
                },
                dry_run=False,
                confirm=True,
                scope="Assets",
            )

            self.assertFalse(response.success)
            self.assertTrue(response.data["fail_fast_triggered"])
            step_codes = [step["result"]["code"] for step in response.data["steps"]]
            self.assertIn("REF_SCAN_BROKEN", step_codes)
            self.assertNotIn("SER_APPLY_OK", step_codes)

    def test_orchestrator_patch_apply_runs_runtime_validation_when_scene_provided(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            assets = root / "Assets"
            assets.mkdir(parents=True, exist_ok=True)
            scene = assets / "Smoke.unity"
            scene.write_text("%YAML 1.1\n", encoding="utf-8")
            target = root / "state.json"
            target.write_text(
                json.dumps({"items": [1, 2], "nested": {"value": 10}}),
                encoding="utf-8",
            )

            orchestrator = Phase1Orchestrator.default(project_root=root)
            response = orchestrator.patch_apply(
                plan={
                    "plan_version": 2,
                    "resources": [
                        {
                            "id": "state",
                            "kind": "json",
                            "path": str(target),
                            "mode": "open",
                        }
                    ],
                    "ops": [
                        {
                            "resource": "state",
                            "op": "set",
                            "component": "Example.Component",
                            "path": "nested.value",
                            "value": 42,
                        }
                    ],
                },
                dry_run=False,
                confirm=True,
                scope="Assets",
                runtime_scene="Assets/Smoke.unity",
            )

            self.assertTrue(response.success)
            self.assertEqual("PATCH_APPLY_RESULT", response.code)
            step_codes = [step["result"]["code"] for step in response.data["steps"]]
            self.assertIn("REF_SCAN_OK", step_codes)
            self.assertIn("SER_APPLY_OK", step_codes)
            self.assertIn("RUN_CLIENTSIM_SKIPPED", step_codes)
            self.assertIn("RUN_ASSERT_OK", step_codes)
            self.assertFalse(response.data["read_only"])

    def test_invalidate_before_cache_from_populated(self) -> None:
        svc = SerializedObjectService(
            project_root=Path("/fake/project"), prefab_variant=MagicMock(),
        )
        svc._before_cache = {"comp:field": "value"}

        svc.invalidate_before_cache()

        self.assertIsNone(svc._before_cache)

    def test_invalidate_before_cache_from_none(self) -> None:
        svc = SerializedObjectService(
            project_root=Path("/fake/project"), prefab_variant=MagicMock(),
        )
        self.assertIsNone(svc._before_cache)

        svc.invalidate_before_cache()  # should not raise

        self.assertIsNone(svc._before_cache)


class OrchestratorSuggestionTests(unittest.TestCase):
    def test_inspect_where_used_wraps_reference_result(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            _create_sample_project(root)
            orchestrator = Phase1Orchestrator.default(project_root=root)

            response = orchestrator.inspect_where_used(
                asset_or_guid=BASE_GUID,
                scope="Assets",
                max_usages=1,
            )

            self.assertTrue(response.success)
            self.assertEqual("INSPECT_WHERE_USED_RESULT", response.code)
            self.assertEqual("where_used", response.data["steps"][0]["step"])


class TestSerializedObjectServiceProjectRoot(unittest.TestCase):
    """P2: _resolve_target_path uses project_root, not CWD."""

    def test_resolve_target_path_uses_project_root(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            assets_dir = project_root / "Assets" / "Tyunta"
            assets_dir.mkdir(parents=True)
            (assets_dir / "Test.prefab").write_text("%YAML 1.1\n")

            svc = SerializedObjectService(project_root=project_root)
            resolved = svc._resolve_target_path("Assets/Tyunta/Test.prefab")
            self.assertEqual(resolved, (project_root / "Assets" / "Tyunta" / "Test.prefab").resolve())

    def test_resolve_target_path_no_doubling(self) -> None:
        """CWD being inside Assets/ must not cause path doubling."""
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            assets_dir = project_root / "Assets" / "Tyunta"
            assets_dir.mkdir(parents=True)
            (assets_dir / "Test.prefab").write_text("%YAML 1.1\n")

            svc = SerializedObjectService(project_root=project_root)
            # Even if CWD were inside Assets/Tyunta, project_root anchors the path
            resolved = svc._resolve_target_path("Assets/Tyunta/Test.prefab")
            path_str = str(resolved)
            self.assertNotIn("Assets/Tyunta/Assets/Tyunta", path_str.replace("\\", "/"))

    def test_default_orchestrator_passes_project_root(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            (project_root / "Assets").mkdir()

            orchestrator = Phase1Orchestrator.default(project_root=project_root)
            self.assertEqual(
                orchestrator.serialized_object.project_root,
                project_root.resolve(),
            )

    def test_apply_resource_plan_sends_resolved_path_to_bridge(self) -> None:
        """Relative Assets/ path must be resolved via project_root before reaching the bridge."""
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            assets_dir = project_root / "Assets" / "Tyunta" / "Materials"
            assets_dir.mkdir(parents=True)

            # Bridge script that echoes the received target and resource path back
            bridge = project_root / "bridge.py"
            bridge.write_text(
                "import json, sys\n"
                "req = json.load(sys.stdin)\n"
                "print(json.dumps({\n"
                '  "success": True, "severity": "info",\n'
                '  "code": "SER_APPLY_OK",\n'
                '  "message": "ok",\n'
                '  "data": {\n'
                '    "target": req.get("target", ""),\n'
                '    "resource_path": req.get("resources", [{}])[0].get("path", ""),\n'
                '    "op_count": len(req.get("ops", [])),\n'
                '    "applied": len(req.get("ops", [])),\n'
                '    "read_only": False, "executed": True,\n'
                '    "mode": req.get("resources", [{}])[0].get("mode", "open"),\n'
                "  },\n"
                '  "diagnostics": [],\n'
                "}))\n",
                encoding="utf-8",
            )

            svc = SerializedObjectService(
                bridge_command=(sys.executable, str(bridge)),
                project_root=project_root,
            )
            response = svc.apply_resource_plan(
                resource={
                    "id": "mat",
                    "kind": "material",
                    "path": "Assets/Tyunta/Materials/Test.mat",
                    "mode": "create",
                },
                ops=[
                    {"op": "create_asset", "shader": "Standard"},
                    {"op": "save"},
                ],
            )

            self.assertTrue(response.success, response.message)
            # The resource path sent to the bridge must be the resolved absolute path,
            # not the raw relative input.
            resource_path = response.data.get("resource_path", "")
            self.assertTrue(
                resource_path.replace("\\", "/").endswith(
                    "Assets/Tyunta/Materials/Test.mat"
                ),
                f"Expected resolved path ending with Assets/Tyunta/Materials/Test.mat, "
                f"got: {resource_path}",
            )
            # Must NOT contain path doubling
            normalized = resource_path.replace("\\", "/")
            self.assertNotIn(
                "Assets/Tyunta/Assets/Tyunta",
                normalized,
                f"Path doubling detected in bridge request: {resource_path}",
            )


class TestNumericComponentWarning(unittest.TestCase):
    """Numeric component selectors emit a likely_fileid diagnostic."""

    def test_dry_run_warns_on_numeric_component(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project_root = Path(tmp)
            (project_root / "Assets").mkdir()
            prefab = project_root / "Assets" / "Test.prefab"
            prefab.write_text("%YAML 1.1\n--- !u!1 &1\nGameObject:\n  m_Name: Root\n")

            svc = SerializedObjectService(project_root=project_root)
            response = svc.dry_run_patch(
                "Assets/Test.prefab",
                [{"op": "set", "component": "9196683876007738779", "path": "m_IsActive", "value": 1}],
            )
            fileid_diags = [d for d in response.diagnostics if d.detail == "likely_fileid"]
            self.assertEqual(len(fileid_diags), 1)
            self.assertIn("type name", fileid_diags[0].evidence)

    def test_negative_numeric_also_warns(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project_root = Path(tmp)
            (project_root / "Assets").mkdir()
            prefab = project_root / "Assets" / "Test.prefab"
            prefab.write_text("%YAML 1.1\n--- !u!1 &1\nGameObject:\n  m_Name: Root\n")

            svc = SerializedObjectService(project_root=project_root)
            diagnostics: list = []
            result = validate_op(
                svc,
                "Assets/Test.prefab", 0,
                {"op": "set", "component": "-42", "path": "m_IsActive", "value": 1},
                diagnostics,
            )
            fileid_diags = [d for d in diagnostics if d.detail == "likely_fileid"]
            self.assertEqual(len(fileid_diags), 1)
            # Op should still proceed (warning, not error)
            self.assertIsNotNone(result)

    def test_type_name_no_warning(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project_root = Path(tmp)
            (project_root / "Assets").mkdir()
            prefab = project_root / "Assets" / "Test.prefab"
            prefab.write_text("%YAML 1.1\n--- !u!1 &1\nGameObject:\n  m_Name: Root\n")

            svc = SerializedObjectService(project_root=project_root)
            diagnostics: list = []
            result = validate_op(
                svc,
                "Assets/Test.prefab", 0,
                {"op": "set", "component": "SkinnedMeshRenderer", "path": "m_IsActive", "value": 1},
                diagnostics,
            )
            fileid_diags = [d for d in diagnostics if d.detail == "likely_fileid"]
            self.assertEqual(len(fileid_diags), 0)
            self.assertIsNotNone(result)


class TestBeforeValueResolution(unittest.TestCase):
    """P1: validate_op resolves before values from Variant overrides."""

    _VARIANT_YAML = (
        "%YAML 1.1\n%TAG !u! tag:unity3d.com,2011:\n"
        "--- !u!1001 &100100000\n"
        "PrefabInstance:\n"
        "  m_SourcePrefab: {fileID: 100100000, guid: aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa, type: 3}\n"
        "  m_Modifications:\n"
        "  - target: {fileID: 42, guid: aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa, type: 3}\n"
        "    propertyPath: m_Materials.Array.data[0]\n"
        "    value: \n"
        "    objectReference: {fileID: 2100000, guid: bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb, type: 2}\n"
    )

    def _make_variant(self, tmp: str) -> Path:
        project_root = Path(tmp)
        assets = project_root / "Assets"
        assets.mkdir(parents=True)
        variant_path = assets / "Variant.prefab"
        variant_path.write_text(self._VARIANT_YAML)
        meta = assets / "Variant.prefab.meta"
        meta.write_text("guid: cccccccccccccccccccccccccccccccc\n")
        return project_root

    def test_before_shows_existing_override_value(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project_root = self._make_variant(tmp)
            pv = PrefabVariantService(project_root=project_root)
            svc = SerializedObjectService(project_root=project_root, prefab_variant=pv)

            diagnostics: list = []
            result = validate_op(
                svc,
                "Assets/Variant.prefab",
                0,
                {"op": "set", "component": "42", "path": "m_Materials.Array.data[0]", "value": "new_mat"},
                diagnostics,
            )
            self.assertIsNotNone(result)
            # The before value should be the objectReference from the override
            self.assertNotIsInstance(result["before"], UnresolvedReason)
            # Should contain the GUID reference
            self.assertIn("bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb", result["before"])

    def test_before_shows_base_default_for_unoverridden_property(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project_root = self._make_variant(tmp)
            pv = PrefabVariantService(project_root=project_root)
            svc = SerializedObjectService(project_root=project_root, prefab_variant=pv)

            diagnostics: list = []
            result = validate_op(
                svc,
                "Assets/Variant.prefab",
                0,
                {"op": "set", "component": "42", "path": "m_IsActive", "value": 1},
                diagnostics,
            )
            self.assertIsNotNone(result)
            self.assertIs(UnresolvedReason.PATH_NOT_FOUND, result["before"])

    def test_before_unresolved_without_prefab_variant(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project_root = self._make_variant(tmp)
            svc = SerializedObjectService(project_root=project_root)

            diagnostics: list = []
            result = validate_op(
                svc,
                "Assets/Variant.prefab",
                0,
                {"op": "set", "component": "42", "path": "m_Materials.Array.data[0]", "value": "new_mat"},
                diagnostics,
            )
            self.assertIsNotNone(result)
            self.assertIs(UnresolvedReason.NO_VARIANT_RESOLVER, result["before"])

    def test_before_unresolved_for_non_variant(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project_root = Path(tmp)
            assets = project_root / "Assets"
            assets.mkdir()
            prefab = assets / "Base.prefab"
            prefab.write_text("%YAML 1.1\n--- !u!1 &1\nGameObject:\n  m_Name: Root\n")

            pv = PrefabVariantService(project_root=project_root)
            svc = SerializedObjectService(project_root=project_root, prefab_variant=pv)

            diagnostics: list = []
            result = validate_op(
                svc,
                "Assets/Base.prefab",
                0,
                {"op": "set", "component": "1", "path": "m_Name", "value": "NewRoot"},
                diagnostics,
            )
            self.assertIsNotNone(result)
            self.assertIs(UnresolvedReason.NOT_A_VARIANT, result["before"])

    def test_cache_cleared_at_dry_run_start(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project_root = self._make_variant(tmp)
            pv = PrefabVariantService(project_root=project_root)
            svc = SerializedObjectService(project_root=project_root, prefab_variant=pv)

            # Manually seed cache to simulate a previous call
            svc._before_cache = {"stale_key": "stale_value"}
            svc.dry_run_patch(
                "Assets/Variant.prefab",
                [{"op": "set", "component": "42", "path": "m_Materials.Array.data[0]", "value": "x"}],
            )
            # Stale key should be gone; cache was rebuilt from scratch
            self.assertNotIn("stale_key", svc._before_cache or {})


class TestChainBeforeValueResolution(unittest.TestCase):
    """Chain-aware before-value resolution across Variant hierarchy."""

    # Base prefab: contains a MeshRenderer (fileID 42) with m_IsActive and
    # m_Materials list
    _BASE_YAML = (
        "%YAML 1.1\n%TAG !u! tag:unity3d.com,2011:\n"
        "--- !u!23 &42\n"
        "MeshRenderer:\n"
        "  m_IsActive: 1\n"
        "  m_Materials:\n"
        "  - {fileID: 2100000, guid: 11111111111111111111111111111111, type: 2}\n"
        "  - {fileID: 2100000, guid: 22222222222222222222222222222222, type: 2}\n"
    )

    # Mid-level variant: overrides m_Materials.Array.data[0]
    _MID_VARIANT_YAML = (
        "%YAML 1.1\n%TAG !u! tag:unity3d.com,2011:\n"
        "--- !u!1001 &100100000\n"
        "PrefabInstance:\n"
        "  m_SourcePrefab: {fileID: 100100000, guid: aaaaaaaaaaaaaaaaaaaaaaaaaaaa0000, type: 3}\n"
        "  m_Modifications:\n"
        "  - target: {fileID: 42, guid: aaaaaaaaaaaaaaaaaaaaaaaaaaaa0000, type: 3}\n"
        "    propertyPath: m_Materials.Array.data[0]\n"
        "    value: \n"
        "    objectReference: {fileID: 2100000, guid: 33333333333333333333333333333333, type: 2}\n"
    )

    # Leaf variant: overrides m_Materials.Array.data[1] only
    _LEAF_VARIANT_YAML = (
        "%YAML 1.1\n%TAG !u! tag:unity3d.com,2011:\n"
        "--- !u!1001 &100100000\n"
        "PrefabInstance:\n"
        "  m_SourcePrefab: {fileID: 100100000, guid: aaaaaaaaaaaaaaaaaaaaaaaaaaaa1111, type: 3}\n"
        "  m_Modifications:\n"
        "  - target: {fileID: 42, guid: aaaaaaaaaaaaaaaaaaaaaaaaaaaa1111, type: 3}\n"
        "    propertyPath: m_Materials.Array.data[1]\n"
        "    value: \n"
        "    objectReference: {fileID: 2100000, guid: 44444444444444444444444444444444, type: 2}\n"
    )

    def _make_chain(self, tmp: str) -> Path:
        """Create a 3-level chain: Leaf -> Mid -> Base."""
        project_root = Path(tmp)
        assets = project_root / "Assets"
        assets.mkdir(parents=True)

        # Base prefab
        base = assets / "Base.prefab"
        base.write_text(self._BASE_YAML)
        base_meta = assets / "Base.prefab.meta"
        base_meta.write_text("guid: aaaaaaaaaaaaaaaaaaaaaaaaaaaa0000\n")

        # Mid-level variant
        mid = assets / "Mid.prefab"
        mid.write_text(self._MID_VARIANT_YAML)
        mid_meta = assets / "Mid.prefab.meta"
        mid_meta.write_text("guid: aaaaaaaaaaaaaaaaaaaaaaaaaaaa1111\n")

        # Leaf variant
        leaf = assets / "Leaf.prefab"
        leaf.write_text(self._LEAF_VARIANT_YAML)
        leaf_meta = assets / "Leaf.prefab.meta"
        leaf_meta.write_text("guid: aaaaaaaaaaaaaaaaaaaaaaaaaaaa2222\n")

        return project_root

    def test_chain_resolves_parent_override(self) -> None:
        """Property overridden in parent variant is found via chain walk."""
        with tempfile.TemporaryDirectory() as tmp:
            project_root = self._make_chain(tmp)
            pv = PrefabVariantService(project_root=project_root)
            svc = SerializedObjectService(project_root=project_root, prefab_variant=pv)

            diagnostics: list = []
            result = validate_op(
                svc,
                "Assets/Leaf.prefab",
                0,
                {"op": "set", "component": "42", "path": "m_Materials.Array.data[0]", "value": "x"},
                diagnostics,
            )
            self.assertIsNotNone(result)
            # data[0] is overridden in Mid variant -> should find that value
            self.assertIn("33333333333333333333333333333333", result["before"])

    def test_chain_resolves_leaf_override(self) -> None:
        """Property overridden in the leaf variant itself takes precedence."""
        with tempfile.TemporaryDirectory() as tmp:
            project_root = self._make_chain(tmp)
            pv = PrefabVariantService(project_root=project_root)
            svc = SerializedObjectService(project_root=project_root, prefab_variant=pv)

            diagnostics: list = []
            result = validate_op(
                svc,
                "Assets/Leaf.prefab",
                0,
                {"op": "set", "component": "42", "path": "m_Materials.Array.data[1]", "value": "x"},
                diagnostics,
            )
            self.assertIsNotNone(result)
            # data[1] is overridden in the Leaf itself -> should find that value
            self.assertIn("44444444444444444444444444444444", result["before"])

    def test_chain_resolves_base_prefab_value(self) -> None:
        """Property not overridden in any variant is read from the base prefab."""
        with tempfile.TemporaryDirectory() as tmp:
            project_root = self._make_chain(tmp)
            pv = PrefabVariantService(project_root=project_root)
            svc = SerializedObjectService(project_root=project_root, prefab_variant=pv)

            diagnostics: list = []
            result = validate_op(
                svc,
                "Assets/Leaf.prefab",
                0,
                {"op": "set", "component": "42", "path": "m_IsActive", "value": 0},
                diagnostics,
            )
            self.assertIsNotNone(result)
            # m_IsActive=1 is in the base prefab, not overridden anywhere
            self.assertEqual("1", result["before"])

    def test_chain_graceful_on_missing_parent(self) -> None:
        """Missing parent in chain returns unresolved for unknown properties."""
        with tempfile.TemporaryDirectory() as tmp:
            project_root = Path(tmp)
            assets = project_root / "Assets"
            assets.mkdir(parents=True)
            # Leaf variant pointing to a non-existent parent
            leaf = assets / "Orphan.prefab"
            leaf.write_text(self._LEAF_VARIANT_YAML)
            leaf_meta = assets / "Orphan.prefab.meta"
            leaf_meta.write_text("guid: aaaaaaaaaaaaaaaaaaaaaaaaaaaa9999\n")

            pv = PrefabVariantService(project_root=project_root)
            svc = SerializedObjectService(project_root=project_root, prefab_variant=pv)

            diagnostics: list = []
            result = validate_op(
                svc,
                "Assets/Orphan.prefab",
                0,
                {"op": "set", "component": "42", "path": "m_IsActive", "value": 0},
                diagnostics,
            )
            self.assertIsNotNone(result)
            # Parent is missing, m_IsActive not overridden in leaf
            self.assertIsInstance(result["before"], UnresolvedReason)

    def test_resolve_chain_values_returns_all_effective(self) -> None:
        """resolve_chain_values returns the merged effective map."""
        with tempfile.TemporaryDirectory() as tmp:
            project_root = self._make_chain(tmp)
            pv = PrefabVariantService(project_root=project_root)
            values = pv.resolve_chain_values("Assets/Leaf.prefab")

            # Leaf overrides data[1], Mid overrides data[0], Base has m_IsActive
            self.assertIn("42:m_Materials.Array.data[1]", values)
            self.assertIn("42:m_Materials.Array.data[0]", values)
            self.assertIn("42:m_IsActive", values)
            self.assertEqual("1", values["42:m_IsActive"])

    def test_resolve_chain_values_child_wins(self) -> None:
        """When both child and parent override the same property, child wins."""
        with tempfile.TemporaryDirectory() as tmp:
            project_root = self._make_chain(tmp)
            # Create a 4th level that also overrides data[0]
            assets = project_root / "Assets"
            top_yaml = (
                "%YAML 1.1\n%TAG !u! tag:unity3d.com,2011:\n"
                "--- !u!1001 &100100000\n"
                "PrefabInstance:\n"
                "  m_SourcePrefab: {fileID: 100100000, guid: aaaaaaaaaaaaaaaaaaaaaaaaaaaa2222, type: 3}\n"
                "  m_Modifications:\n"
                "  - target: {fileID: 42, guid: aaaaaaaaaaaaaaaaaaaaaaaaaaaa2222, type: 3}\n"
                "    propertyPath: m_Materials.Array.data[0]\n"
                "    value: \n"
                "    objectReference: {fileID: 2100000, guid: 55555555555555555555555555555555, type: 2}\n"
            )
            top = assets / "Top.prefab"
            top.write_text(top_yaml)
            top_meta = assets / "Top.prefab.meta"
            top_meta.write_text("guid: aaaaaaaaaaaaaaaaaaaaaaaaaaaa3333\n")

            pv = PrefabVariantService(project_root=project_root)
            values = pv.resolve_chain_values("Assets/Top.prefab")

            # Top overrides data[0] -> should shadow Mid's override
            self.assertIn("55555555555555555555555555555555", values["42:m_Materials.Array.data[0]"])

    # ----- Type-name lookup paths (component addressed by Unity type name) -----

    def test_chain_resolves_by_type_name(self) -> None:
        """`component="MeshRenderer"` resolves to the same value as `component="42"`."""
        with tempfile.TemporaryDirectory() as tmp:
            project_root = self._make_chain(tmp)
            pv = PrefabVariantService(project_root=project_root)
            svc_by_id = SerializedObjectService(
                project_root=project_root, prefab_variant=pv
            )
            svc_by_name = SerializedObjectService(
                project_root=project_root, prefab_variant=pv
            )

            by_id = validate_op(
                svc_by_id,
                "Assets/Leaf.prefab",
                0,
                {"op": "set", "component": "42",
                 "path": "m_Materials.Array.data[0]", "value": "x"},
                [],
            )
            by_name = validate_op(
                svc_by_name,
                "Assets/Leaf.prefab",
                0,
                {"op": "set", "component": "MeshRenderer",
                 "path": "m_Materials.Array.data[0]", "value": "x"},
                [],
            )

        self.assertIsNotNone(by_id)
        self.assertIsNotNone(by_name)
        self.assertEqual(by_id["before"], by_name["before"])

    def test_chain_ambiguous_type_name(self) -> None:
        """Two MeshRenderers in the same chain → ambiguous-type sentinel."""
        with tempfile.TemporaryDirectory() as tmp:
            project_root = Path(tmp)
            assets = project_root / "Assets"
            assets.mkdir(parents=True)

            # Base prefab with two MeshRenderers (file ids 42 and 43).
            base_yaml = (
                "%YAML 1.1\n%TAG !u! tag:unity3d.com,2011:\n"
                "--- !u!23 &42\nMeshRenderer:\n  m_IsActive: 1\n"
                "--- !u!23 &43\nMeshRenderer:\n  m_IsActive: 0\n"
            )
            base = assets / "Base.prefab"
            base.write_text(base_yaml)
            (assets / "Base.prefab.meta").write_text(
                "guid: aaaaaaaaaaaaaaaaaaaaaaaaaaaa0000\n"
            )

            # A Variant referencing the base so the chain has a top.
            leaf_yaml = (
                "%YAML 1.1\n%TAG !u! tag:unity3d.com,2011:\n"
                "--- !u!1001 &100100000\n"
                "PrefabInstance:\n"
                "  m_SourcePrefab: {fileID: 100100000, "
                "guid: aaaaaaaaaaaaaaaaaaaaaaaaaaaa0000, type: 3}\n"
                "  m_Modifications: []\n"
            )
            leaf = assets / "Leaf.prefab"
            leaf.write_text(leaf_yaml)
            (assets / "Leaf.prefab.meta").write_text(
                "guid: aaaaaaaaaaaaaaaaaaaaaaaaaaaa1111\n"
            )

            pv = PrefabVariantService(project_root=project_root)
            svc = SerializedObjectService(project_root=project_root, prefab_variant=pv)

            result = validate_op(
                svc,
                "Assets/Leaf.prefab",
                0,
                {"op": "set", "component": "MeshRenderer",
                 "path": "m_IsActive", "value": "0"},
                [],
            )

        self.assertIsNotNone(result)
        self.assertIs(UnresolvedReason.AMBIGUOUS_TYPE, result["before"])

    def test_chain_type_name_not_in_chain(self) -> None:
        """Type name absent from the chain → not-found sentinel."""
        with tempfile.TemporaryDirectory() as tmp:
            project_root = self._make_chain(tmp)
            pv = PrefabVariantService(project_root=project_root)
            svc = SerializedObjectService(project_root=project_root, prefab_variant=pv)

            result = validate_op(
                svc,
                "Assets/Leaf.prefab",
                0,
                {"op": "set", "component": "Camera",
                 "path": "m_IsActive", "value": "0"},
                [],
            )

        self.assertIsNotNone(result)
        self.assertIs(UnresolvedReason.TYPE_NOT_FOUND, result["before"])


class TestResolveChainValuesWithOrigin(unittest.TestCase):
    """Tests for resolve_chain_values_with_origin() origin tracking."""

    # Reuse the same YAML fixtures from TestChainBeforeValueResolution.
    _BASE_YAML = TestChainBeforeValueResolution._BASE_YAML
    _MID_VARIANT_YAML = TestChainBeforeValueResolution._MID_VARIANT_YAML
    _LEAF_VARIANT_YAML = TestChainBeforeValueResolution._LEAF_VARIANT_YAML

    def _make_chain(self, tmp: str) -> Path:
        return TestChainBeforeValueResolution._make_chain(
            TestChainBeforeValueResolution(), tmp,
        )

    def test_origin_tracks_leaf_override(self) -> None:
        """Value overridden in leaf has origin_depth=0 and leaf path."""
        with tempfile.TemporaryDirectory() as tmp:
            project_root = self._make_chain(tmp)
            pv = PrefabVariantService(project_root=project_root)
            response = pv.resolve_chain_values_with_origin("Assets/Leaf.prefab")

            self.assertTrue(response.success)
            values = response.data["values"]
            by_key = {
                f"{v['target_file_id']}:{v['property_path']}": v for v in values
            }

            data1 = by_key["42:m_Materials.Array.data[1]"]
            self.assertEqual(0, data1["origin_depth"])
            self.assertEqual("Assets/Leaf.prefab", data1["origin_path"])

    def test_origin_tracks_mid_override(self) -> None:
        """Value overridden in mid-level variant has origin_depth=1."""
        with tempfile.TemporaryDirectory() as tmp:
            project_root = self._make_chain(tmp)
            pv = PrefabVariantService(project_root=project_root)
            response = pv.resolve_chain_values_with_origin("Assets/Leaf.prefab")

            values = response.data["values"]
            by_key = {
                f"{v['target_file_id']}:{v['property_path']}": v for v in values
            }

            data0 = by_key["42:m_Materials.Array.data[0]"]
            self.assertEqual(1, data0["origin_depth"])
            self.assertEqual("Assets/Mid.prefab", data0["origin_path"])

    def test_origin_tracks_base_value(self) -> None:
        """Value from base prefab has the highest origin_depth."""
        with tempfile.TemporaryDirectory() as tmp:
            project_root = self._make_chain(tmp)
            pv = PrefabVariantService(project_root=project_root)
            response = pv.resolve_chain_values_with_origin("Assets/Leaf.prefab")

            values = response.data["values"]
            by_key = {
                f"{v['target_file_id']}:{v['property_path']}": v for v in values
            }

            is_active = by_key["42:m_IsActive"]
            self.assertEqual(2, is_active["origin_depth"])
            self.assertEqual("Assets/Base.prefab", is_active["origin_path"])
            self.assertEqual("1", is_active["value"])

    def test_chain_list_included(self) -> None:
        """Response includes chain list with depths."""
        with tempfile.TemporaryDirectory() as tmp:
            project_root = self._make_chain(tmp)
            pv = PrefabVariantService(project_root=project_root)
            response = pv.resolve_chain_values_with_origin("Assets/Leaf.prefab")

            chain = response.data["chain"]
            self.assertEqual(3, len(chain))
            self.assertEqual("Assets/Leaf.prefab", chain[0]["path"])
            self.assertEqual(0, chain[0]["depth"])
            self.assertEqual("Assets/Base.prefab", chain[2]["path"])
            self.assertEqual(2, chain[2]["depth"])

    def test_non_variant_returns_empty(self) -> None:
        """Non-variant file returns success with empty values."""
        with tempfile.TemporaryDirectory() as tmp:
            project_root = self._make_chain(tmp)
            pv = PrefabVariantService(project_root=project_root)
            response = pv.resolve_chain_values_with_origin("Assets/Base.prefab")

            self.assertTrue(response.success)
            self.assertEqual("PVR_NOT_VARIANT", response.code)
            self.assertEqual(0, response.data["value_count"])


if __name__ == "__main__":
    unittest.main()
