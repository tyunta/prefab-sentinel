from __future__ import annotations

import hashlib
import hmac
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

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
from tests.bridge_test_helpers import write_fake_runtime_runner, write_file

BASE_GUID = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
MISSING_GUID = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
VARIANT_GUID = "cccccccccccccccccccccccccccccccc"
CROSS_PROJECT_GUID = "dddddddddddddddddddddddddddddddd"


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


class PrefabVariantServiceTests(unittest.TestCase):
    def test_detect_stale_and_compute_effective_values(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            _create_sample_project(root)
            svc = PrefabVariantService(project_root=root)

            chain = svc.resolve_prefab_chain("Assets/Variant.prefab")
            self.assertTrue(chain.success)
            self.assertEqual("PVR_CHAIN_OK", chain.code)
            self.assertGreaterEqual(len(chain.data["chain"]), 2)

            overrides = svc.list_overrides("Assets/Variant.prefab")
            self.assertEqual(5, overrides.data["override_count"])

            effective = svc.compute_effective_values("Assets/Variant.prefab")
            dup_values = [
                item
                for item in effective.data["effective_values"]
                if item["property_path"] == "duplicated.path"
            ]
            self.assertEqual(1, len(dup_values))
            self.assertEqual("second", dup_values[0]["value"])

            stale = svc.detect_stale_overrides("Assets/Variant.prefab")
            self.assertFalse(stale.success)
            # Mixed categories → umbrella code PVR001
            self.assertEqual("PVR001", stale.code)
            details = [diag.detail for diag in stale.diagnostics]
            self.assertIn("duplicate_override", details)
            self.assertIn("array_size_mismatch", details)
            self.assertCountEqual(
                ["array_size_mismatch", "duplicate_override"],
                stale.data["categories"],
            )

    def test_detect_stale_duplicate_only_returns_pvr002(self) -> None:
        """Single-category duplicate_override → PVR002."""
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
            self.assertFalse(stale.success)
            self.assertEqual("PVR002", stale.code)
            self.assertEqual(["duplicate_override"], stale.data["categories"])
            # Location includes both first and last occurrence
            loc = stale.diagnostics[0].location
            self.assertIn("..", loc)

    def test_detect_stale_array_mismatch_only_returns_pvr003(self) -> None:
        """Single-category array_size_mismatch → PVR003."""
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
            self.assertFalse(stale.success)
            self.assertEqual("PVR003", stale.code)
            self.assertEqual(["array_size_mismatch"], stale.data["categories"])


class RuntimeValidationServiceTests(unittest.TestCase):
    def test_compile_udonsharp_returns_skip_without_runtime_env(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            _create_sample_project(root)
            svc = RuntimeValidationService(project_root=root)

            with patch.dict(os.environ, {}, clear=True):
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
        self.assertEqual(1, response.data["categories"]["BROKEN_PPTR"])
        self.assertEqual(1, response.data["categories"]["UDON_NULLREF"])
        self.assertEqual(1, response.data["categories"]["DUPLICATE_EVENTSYSTEM"])

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
        from prefab_sentinel.contracts import Severity

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
        from prefab_sentinel.contracts import Severity

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
        from prefab_sentinel.contracts import Severity

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
        from prefab_sentinel.contracts import Severity

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
        from prefab_sentinel.contracts import Severity

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
        from prefab_sentinel.contracts import Severity

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
        from prefab_sentinel.contracts import Severity

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
                "target": "Assets/Variant.prefab",
                "ops": [
                    {
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
                    "target": str(target),
                    "ops": [
                        {
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
                    "target": str(target),
                    "ops": [
                        {
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
                    "target": str(target),
                    "ops": [
                        {
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
            result = svc._validate_op(
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
            result = svc._validate_op(
                "Assets/Test.prefab", 0,
                {"op": "set", "component": "SkinnedMeshRenderer", "path": "m_IsActive", "value": 1},
                diagnostics,
            )
            fileid_diags = [d for d in diagnostics if d.detail == "likely_fileid"]
            self.assertEqual(len(fileid_diags), 0)
            self.assertIsNotNone(result)


class TestBeforeValueResolution(unittest.TestCase):
    """P1: _validate_op resolves before values from Variant overrides."""

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
            result = svc._validate_op(
                "Assets/Variant.prefab",
                0,
                {"op": "set", "component": "42", "path": "m_Materials.Array.data[0]", "value": "new_mat"},
                diagnostics,
            )
            self.assertIsNotNone(result)
            # The before value should be the objectReference from the override
            self.assertNotEqual("(unknown)", result["before"])
            self.assertNotEqual("(unresolved)", result["before"])
            # Should contain the GUID reference
            self.assertIn("bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb", result["before"])

    def test_before_shows_base_default_for_unoverridden_property(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project_root = self._make_variant(tmp)
            pv = PrefabVariantService(project_root=project_root)
            svc = SerializedObjectService(project_root=project_root, prefab_variant=pv)

            diagnostics: list = []
            result = svc._validate_op(
                "Assets/Variant.prefab",
                0,
                {"op": "set", "component": "42", "path": "m_IsActive", "value": 1},
                diagnostics,
            )
            self.assertIsNotNone(result)
            self.assertEqual("(unresolved: not found in chain)", result["before"])

    def test_before_unresolved_without_prefab_variant(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project_root = self._make_variant(tmp)
            svc = SerializedObjectService(project_root=project_root)

            diagnostics: list = []
            result = svc._validate_op(
                "Assets/Variant.prefab",
                0,
                {"op": "set", "component": "42", "path": "m_Materials.Array.data[0]", "value": "new_mat"},
                diagnostics,
            )
            self.assertIsNotNone(result)
            self.assertEqual("(unresolved)", result["before"])

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
            result = svc._validate_op(
                "Assets/Base.prefab",
                0,
                {"op": "set", "component": "1", "path": "m_Name", "value": "NewRoot"},
                diagnostics,
            )
            self.assertIsNotNone(result)
            self.assertEqual("(unresolved: not a variant)", result["before"])

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
            result = svc._validate_op(
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
            result = svc._validate_op(
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
            result = svc._validate_op(
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
            result = svc._validate_op(
                "Assets/Orphan.prefab",
                0,
                {"op": "set", "component": "42", "path": "m_IsActive", "value": 0},
                diagnostics,
            )
            self.assertIsNotNone(result)
            # Parent is missing, m_IsActive not overridden in leaf
            self.assertIn("unresolved", result["before"])

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
