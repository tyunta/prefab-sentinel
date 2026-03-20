from __future__ import annotations

import hashlib
import hmac
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from prefab_sentinel.orchestrator import Phase1Orchestrator
from prefab_sentinel.mcp.prefab_variant import PrefabVariantMcp
from prefab_sentinel.mcp.reference_resolver import ReferenceResolverMcp
from prefab_sentinel.mcp.runtime_validation import RuntimeValidationMcp
from prefab_sentinel.mcp.serialized_object import (
    SerializedObjectMcp,
    compute_patch_plan_hmac_sha256,
    compute_patch_plan_sha256,
    load_patch_plan,
)

BASE_GUID = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
MISSING_GUID = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
VARIANT_GUID = "cccccccccccccccccccccccccccccccc"
CROSS_PROJECT_GUID = "dddddddddddddddddddddddddddddddd"


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _write_fake_runtime_runner(path: Path) -> None:
    path.write_text(
        """
import json
import sys
from pathlib import Path

args = sys.argv[1:]

def arg_value(name: str) -> str:
    for index, value in enumerate(args[:-1]):
        if value == name:
            return args[index + 1]
    raise SystemExit(f"missing argument: {name}")

request_path = Path(arg_value("-sentinelRuntimeRequest"))
response_path = Path(arg_value("-sentinelRuntimeResponse"))
request = json.loads(request_path.read_text(encoding="utf-8"))
action = request.get("action", "")

if action == "compile_udonsharp":
    payload = {
        "success": True,
        "severity": "info",
        "code": "RUN_COMPILE_OK",
        "message": "compile ok",
        "data": {
            "udon_program_count": 3,
            "executed": True,
            "read_only": False,
        },
        "diagnostics": [],
    }
elif action == "run_clientsim":
    payload = {
        "success": True,
        "severity": "info",
        "code": "RUN_CLIENTSIM_OK",
        "message": "clientsim ok",
        "data": {
            "clientsim_ready": True,
            "executed": True,
            "read_only": False,
        },
        "diagnostics": [],
    }
else:
    payload = {
        "success": False,
        "severity": "error",
        "code": "RUN_PROTOCOL_ERROR",
        "message": f"unexpected action: {action}",
        "data": {
            "executed": False,
            "read_only": True,
        },
        "diagnostics": [],
    }

response_path.write_text(json.dumps(payload), encoding="utf-8")
""".strip(),
        encoding="utf-8",
    )


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


class RuntimeValidationMcpTests(unittest.TestCase):
    def test_compile_udonsharp_returns_skip_without_runtime_env(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            _create_sample_project(root)
            mcp = RuntimeValidationMcp(project_root=root)

            with patch.dict(os.environ, {}, clear=True):
                response = mcp.compile_udonsharp()

            self.assertTrue(response.success)
            self.assertEqual("RUN_COMPILE_SKIPPED", response.code)

    def test_compile_udonsharp_runs_unity_command_when_configured(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            _create_sample_project(root)
            runner = root / "unity_runner.py"
            _write_fake_runtime_runner(runner)
            mcp = RuntimeValidationMcp(project_root=root)

            with patch.dict(
                os.environ,
                {
                    "UNITYTOOL_UNITY_COMMAND": f'"{sys.executable}" "{runner}"',
                    "UNITYTOOL_UNITY_PROJECT_PATH": str(root),
                },
                clear=False,
            ):
                response = mcp.compile_udonsharp()

            self.assertTrue(response.success)
            self.assertEqual("RUN_COMPILE_OK", response.code)
            self.assertEqual(3, response.data["udon_program_count"])
            self.assertTrue(response.data["executed"])

    def test_run_clientsim_returns_missing_scene_error(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            _create_sample_project(root)
            mcp = RuntimeValidationMcp(project_root=root)

            response = mcp.run_clientsim("Assets/MissingScene.unity", "default")

            self.assertFalse(response.success)
            self.assertEqual("RUN002", response.code)

    def test_run_clientsim_runs_unity_command_when_configured(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            _create_sample_project(root)
            _write(
                root / "Assets" / "Scenes" / "Smoke.unity",
                """%YAML 1.1
--- !u!1 &1
GameObject:
  m_Name: Smoke
""",
            )
            runner = root / "unity_runner.py"
            _write_fake_runtime_runner(runner)
            mcp = RuntimeValidationMcp(project_root=root)

            with patch.dict(
                os.environ,
                {
                    "UNITYTOOL_UNITY_COMMAND": f'"{sys.executable}" "{runner}"',
                    "UNITYTOOL_UNITY_PROJECT_PATH": str(root),
                },
                clear=False,
            ):
                response = mcp.run_clientsim("Assets/Scenes/Smoke.unity", "default")

            self.assertTrue(response.success)
            self.assertEqual("RUN_CLIENTSIM_OK", response.code)
            self.assertTrue(response.data["clientsim_ready"])
            self.assertTrue(response.data["executed"])
            self.assertEqual(".", response.data["project_root"])

    def test_classify_errors_detects_known_categories(self) -> None:
        mcp = RuntimeValidationMcp(project_root=Path.cwd())
        response = mcp.classify_errors(
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
            _write(
                root / "Assets" / "Scenes" / "Smoke.unity",
                """%YAML 1.1
--- !u!1 &1
GameObject:
  m_Name: Smoke
""",
            )
            _write(
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
            _write(
                root / "Assets" / "Scenes" / "Smoke.unity",
                """%YAML 1.1
--- !u!1 &1
GameObject:
  m_Name: Smoke
""",
            )
            runner = root / "unity_runner.py"
            _write_fake_runtime_runner(runner)

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


class SerializedObjectMcpTests(unittest.TestCase):
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
        mcp = SerializedObjectMcp()
        response = mcp.dry_run_patch(
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
        mcp = SerializedObjectMcp()
        response = mcp.dry_run_patch(
            target="Assets/Variant.prefab",
            ops=[{"op": "set", "component": "", "path": "x"}],
        )

        self.assertFalse(response.success)
        self.assertEqual("SER_PLAN_INVALID", response.code)
        self.assertTrue(response.diagnostics)

    def test_dry_run_patch_supports_material_asset_root_mutation_ops(self) -> None:
        mcp = SerializedObjectMcp()
        response = mcp.dry_run_patch(
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
        mcp = SerializedObjectMcp()
        response = mcp.dry_run_resource_plan(
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
        mcp = SerializedObjectMcp()
        response = mcp.dry_run_resource_plan(
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
        mcp = SerializedObjectMcp()
        response = mcp.dry_run_resource_plan(
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
        mcp = SerializedObjectMcp()
        response = mcp.dry_run_resource_plan(
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
        mcp = SerializedObjectMcp()
        response = mcp.dry_run_resource_plan(
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
        mcp = SerializedObjectMcp()
        response = mcp.dry_run_resource_plan(
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
        mcp = SerializedObjectMcp()
        response = mcp.dry_run_resource_plan(
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
        mcp = SerializedObjectMcp()
        response = mcp.dry_run_resource_plan(
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

    def test_dry_run_resource_plan_rejects_non_prefab_target_for_create_mode(self) -> None:
        mcp = SerializedObjectMcp()
        response = mcp.dry_run_resource_plan(
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
        mcp = SerializedObjectMcp()
        response = mcp.dry_run_resource_plan(
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
        mcp = SerializedObjectMcp()
        response = mcp.dry_run_resource_plan(
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
        mcp = SerializedObjectMcp()
        response = mcp.dry_run_resource_plan(
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
        mcp = SerializedObjectMcp()
        response = mcp.dry_run_resource_plan(
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

    def test_dry_run_resource_plan_rejects_save_before_final_op(self) -> None:
        mcp = SerializedObjectMcp()
        response = mcp.dry_run_resource_plan(
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

            mcp = SerializedObjectMcp()
            response = mcp.apply_and_save(
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

            mcp = SerializedObjectMcp()
            response = mcp.apply_resource_plan(
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

            mcp = SerializedObjectMcp()
            response = mcp.apply_and_save(
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

            mcp = SerializedObjectMcp(bridge_command=(sys.executable, str(bridge)))
            response = mcp.apply_and_save(
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

            mcp = SerializedObjectMcp(bridge_command=(sys.executable, str(bridge)))
            response = mcp.apply_and_save(
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

            mcp = SerializedObjectMcp(bridge_command=(sys.executable, str(bridge)))
            response = mcp.apply_and_save(
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

            mcp = SerializedObjectMcp(bridge_command=("forbidden-bridge", "run"))
            response = mcp.apply_and_save(
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

            mcp = SerializedObjectMcp(bridge_command=(sys.executable, str(bridge)))
            response = mcp.apply_resource_plan(
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

            mcp = SerializedObjectMcp(bridge_command=(sys.executable, str(bridge)))
            response = mcp.apply_resource_plan(
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

            mcp = SerializedObjectMcp(bridge_command=(sys.executable, str(bridge)))
            response = mcp.apply_resource_plan(
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

            mcp = SerializedObjectMcp(bridge_command=(sys.executable, str(bridge)))
            response = mcp.apply_resource_plan(
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

            mcp = SerializedObjectMcp(bridge_command=(sys.executable, str(bridge)))
            response = mcp.apply_resource_plan(
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

            mcp = SerializedObjectMcp(bridge_command=(sys.executable, str(bridge)))
            response = mcp.apply_resource_plan(
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

            mcp = SerializedObjectMcp(bridge_command=(sys.executable, str(bridge)))
            response = mcp.apply_resource_plan(
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

            mcp = SerializedObjectMcp(bridge_command=(sys.executable, str(bridge)))
            response = mcp.apply_resource_plan(
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
            self.assertEqual([], response.data["safe_fix"])
            self.assertEqual(
                response.data["candidate_count"],
                len(response.data["decision_required"]),
            )

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
