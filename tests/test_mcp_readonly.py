from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from unitytool.orchestrator import Phase1Orchestrator
from unitytool.mcp.prefab_variant import PrefabVariantMcp
from unitytool.mcp.reference_resolver import ReferenceResolverMcp

BASE_GUID = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
MISSING_GUID = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
VARIANT_GUID = "cccccccccccccccccccccccccccccccc"
CROSS_PROJECT_GUID = "dddddddddddddddddddddddddddddddd"


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _create_sample_project(root: Path) -> None:
    _write(
        root / "Assets" / "Base.prefab",
        """%YAML 1.1
--- !u!1 &100100000
GameObject:
  m_Name: Base
""",
    )
    _write(
        root / "Assets" / "Base.prefab.meta",
        f"""fileFormatVersion: 2
guid: {BASE_GUID}
""",
    )
    _write(
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
    _write(
        root / "Assets" / "Variant.prefab.meta",
        f"""fileFormatVersion: 2
guid: {VARIANT_GUID}
""",
    )


class ReferenceResolverMcpTests(unittest.TestCase):
    def test_scan_broken_references_detects_missing_asset_and_local_id(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            _create_sample_project(root)
            mcp = ReferenceResolverMcp(project_root=root)

            response = mcp.scan_broken_references("Assets")

            self.assertFalse(response.success)
            self.assertEqual("REF_SCAN_BROKEN", response.code)
            self.assertEqual(1, response.data["categories"]["missing_asset"])
            self.assertEqual(1, response.data["categories"]["missing_local_id"])
            self.assertEqual(1, response.data["categories_occurrences"]["missing_asset"])
            self.assertEqual(1, response.data["categories_occurrences"]["missing_local_id"])
            self.assertEqual(2, response.data["broken_count"])
            self.assertEqual(2, response.data["broken_occurrences"])
            self.assertFalse(response.data["details_included"])
            self.assertEqual([], response.diagnostics)
            self.assertGreaterEqual(
                response.data["skipped_external_prefab_fileid_checks"],
                1,
            )

    def test_scan_broken_references_honors_details_limit(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            _create_sample_project(root)
            mcp = ReferenceResolverMcp(project_root=root)

            response = mcp.scan_broken_references(
                "Assets",
                include_diagnostics=True,
                max_diagnostics=1,
            )

            self.assertFalse(response.success)
            self.assertEqual(1, len(response.diagnostics))
            self.assertEqual(1, response.data["returned_diagnostics"])
            self.assertEqual(1, response.data["truncated_diagnostics"])
            self.assertGreaterEqual(
                response.data["broken_occurrences"],
                response.data["broken_count"],
            )

    def test_scan_broken_references_honors_ignore_asset_guids(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            _create_sample_project(root)
            mcp = ReferenceResolverMcp(project_root=root)

            response = mcp.scan_broken_references(
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
            mcp = ReferenceResolverMcp(project_root=root)

            response = mcp.scan_broken_references(
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
            mcp = ReferenceResolverMcp(project_root=root)

            resolved = mcp.resolve_reference(BASE_GUID, "100100000")
            self.assertTrue(resolved.success)
            self.assertEqual("REF_RESOLVED", resolved.code)

            usage = mcp.where_used(BASE_GUID, scope="Assets", max_usages=1)
            self.assertTrue(usage.success)
            self.assertEqual("Assets", usage.data["scope"])
            self.assertEqual(1, usage.data["returned_usages"])
            self.assertGreater(usage.data["usage_count"], 1)
            self.assertGreater(usage.data["truncated_usages"], 0)

    def test_where_used_returns_missing_scope_error(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            _create_sample_project(root)
            mcp = ReferenceResolverMcp(project_root=root)

            usage = mcp.where_used(BASE_GUID, scope="Assets/NotFound")

            self.assertFalse(usage.success)
            self.assertEqual("REF404", usage.code)

    def test_scan_broken_references_scopes_guid_index_to_unity_project(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo_root = Path(temp_dir)
            avatar_root = repo_root / "sample" / "avatar"
            world_root = repo_root / "sample" / "world"
            _write(
                avatar_root / "Assets" / "Ref.prefab",
                f"""%YAML 1.1
--- !u!1001 &100100000
PrefabInstance:
  m_SourcePrefab: {{fileID: 11400000, guid: {CROSS_PROJECT_GUID}, type: 3}}
""",
            )
            _write(
                avatar_root / "Assets" / "Ref.prefab.meta",
                f"""fileFormatVersion: 2
guid: {VARIANT_GUID}
""",
            )
            _write(
                world_root / "Assets" / "WorldOnly.asset",
                """%YAML 1.1
--- !u!114 &11400000
MonoBehaviour:
""",
            )
            _write(
                world_root / "Assets" / "WorldOnly.asset.meta",
                f"""fileFormatVersion: 2
guid: {CROSS_PROJECT_GUID}
""",
            )

            mcp = ReferenceResolverMcp(project_root=repo_root)
            response = mcp.scan_broken_references("sample/avatar/Assets")

            self.assertFalse(response.success)
            self.assertEqual(1, response.data["categories"]["missing_asset"])
            self.assertEqual("sample/avatar", response.data["scan_project_root"])

    def test_where_used_skips_library_scope(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            _create_sample_project(root)
            _write(
                root / "Library" / "Noise.prefab",
                f"""%YAML 1.1
--- !u!1001 &100100000
PrefabInstance:
  m_SourcePrefab: {{fileID: 100100000, guid: {BASE_GUID}, type: 3}}
""",
            )
            mcp = ReferenceResolverMcp(project_root=root)

            usage = mcp.where_used(BASE_GUID)
            paths = [item["path"] for item in usage.data["usages"]]

            self.assertTrue(paths)
            self.assertFalse(any(path.startswith("Library/") for path in paths))


class PrefabVariantMcpTests(unittest.TestCase):
    def test_detect_stale_and_compute_effective_values(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            _create_sample_project(root)
            mcp = PrefabVariantMcp(project_root=root)

            chain = mcp.resolve_prefab_chain("Assets/Variant.prefab")
            self.assertTrue(chain.success)
            self.assertEqual("PVR_CHAIN_OK", chain.code)
            self.assertGreaterEqual(len(chain.data["chain"]), 2)

            overrides = mcp.list_overrides("Assets/Variant.prefab")
            self.assertEqual(5, overrides.data["override_count"])

            effective = mcp.compute_effective_values("Assets/Variant.prefab")
            dup_values = [
                item
                for item in effective.data["effective_values"]
                if item["property_path"] == "duplicated.path"
            ]
            self.assertEqual(1, len(dup_values))
            self.assertEqual("second", dup_values[0]["value"])

            stale = mcp.detect_stale_overrides("Assets/Variant.prefab")
            self.assertFalse(stale.success)
            self.assertEqual("PVR001", stale.code)
            details = [diag.detail for diag in stale.diagnostics]
            self.assertIn("duplicate_override", details)
            self.assertIn("array_size_mismatch", details)


class OrchestratorSuggestionTests(unittest.TestCase):
    def test_suggest_ignore_guids_returns_candidates(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            _create_sample_project(root)
            orchestrator = Phase1Orchestrator.default(project_root=root)

            response = orchestrator.suggest_ignore_guids(
                scope="Assets",
                min_occurrences=1,
                max_items=5,
            )

            self.assertTrue(response.success)
            self.assertEqual("SUGGEST_IGNORE_GUIDS_RESULT", response.code)
            self.assertGreaterEqual(response.data["candidate_count"], 1)
            first = response.data["candidates"][0]
            self.assertEqual(MISSING_GUID, first["guid"])
            self.assertGreaterEqual(first["occurrences"], 1)

    def test_suggest_ignore_guids_respects_ignore_list(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            _create_sample_project(root)
            orchestrator = Phase1Orchestrator.default(project_root=root)

            response = orchestrator.suggest_ignore_guids(
                scope="Assets",
                min_occurrences=1,
                max_items=5,
                ignore_asset_guids=(MISSING_GUID,),
            )

            self.assertTrue(response.success)
            self.assertEqual(0, response.data["candidate_count"])

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


if __name__ == "__main__":
    unittest.main()
