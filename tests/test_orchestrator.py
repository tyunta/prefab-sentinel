from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from prefab_sentinel.contracts import Severity, ToolResponse
from prefab_sentinel.orchestrator import Phase1Orchestrator


def _ok_response(code: str = "OK", data: dict | None = None) -> ToolResponse:
    return ToolResponse(
        success=True,
        severity=Severity.INFO,
        code=code,
        message="ok",
        data=data or {},
        diagnostics=[],
    )


def _error_response(code: str = "ERR", data: dict | None = None) -> ToolResponse:
    return ToolResponse(
        success=False,
        severity=Severity.ERROR,
        code=code,
        message="error",
        data=data or {},
        diagnostics=[],
    )


def _warning_response(code: str = "WARN", data: dict | None = None) -> ToolResponse:
    return ToolResponse(
        success=False,
        severity=Severity.WARNING,
        code=code,
        message="warning",
        data=data or {},
        diagnostics=[],
    )


def _make_runtime_mock() -> MagicMock:
    """Create a RuntimeValidationMcp mock with assert_no_critical_errors pre-configured.

    MagicMock treats any attribute starting with 'assert' as an assertion method,
    so we must explicitly assign it before first access.
    """
    mock = MagicMock()
    mock.assert_no_critical_errors = MagicMock()
    return mock


def _make_orchestrator() -> Phase1Orchestrator:
    return Phase1Orchestrator(
        reference_resolver=MagicMock(),
        prefab_variant=MagicMock(),
        runtime_validation=_make_runtime_mock(),
        serialized_object=MagicMock(),
    )


class InspectVariantTests(unittest.TestCase):
    def test_all_steps_succeed(self) -> None:
        orch = _make_orchestrator()
        orch.prefab_variant.resolve_prefab_chain.return_value = _ok_response()
        orch.prefab_variant.list_overrides.return_value = _ok_response()
        orch.prefab_variant.compute_effective_values.return_value = _ok_response()
        orch.prefab_variant.detect_stale_overrides.return_value = _ok_response()

        result = orch.inspect_variant("Assets/test.prefab")
        self.assertTrue(result.success)
        self.assertEqual(Severity.INFO, result.severity)
        self.assertEqual("INSPECT_VARIANT_RESULT", result.code)
        self.assertFalse(result.data["fail_fast_triggered"])
        self.assertEqual(4, len(result.data["steps"]))

    def test_fail_fast_on_error_step(self) -> None:
        orch = _make_orchestrator()
        orch.prefab_variant.resolve_prefab_chain.return_value = _ok_response()
        orch.prefab_variant.list_overrides.return_value = _error_response()
        orch.prefab_variant.compute_effective_values.return_value = _ok_response()
        orch.prefab_variant.detect_stale_overrides.return_value = _ok_response()

        result = orch.inspect_variant("Assets/test.prefab")
        self.assertFalse(result.success)
        self.assertTrue(result.data["fail_fast_triggered"])
        # Should stop after list_overrides
        self.assertEqual(2, len(result.data["steps"]))

    def test_fail_fast_on_first_step_error(self) -> None:
        orch = _make_orchestrator()
        orch.prefab_variant.resolve_prefab_chain.return_value = _error_response()

        result = orch.inspect_variant("Assets/test.prefab")
        self.assertFalse(result.success)
        self.assertTrue(result.data["fail_fast_triggered"])
        self.assertEqual(1, len(result.data["steps"]))

    def test_warning_does_not_trigger_fail_fast(self) -> None:
        orch = _make_orchestrator()
        orch.prefab_variant.resolve_prefab_chain.return_value = _ok_response()
        orch.prefab_variant.list_overrides.return_value = _warning_response()
        orch.prefab_variant.compute_effective_values.return_value = _ok_response()
        orch.prefab_variant.detect_stale_overrides.return_value = _ok_response()

        result = orch.inspect_variant("Assets/test.prefab")
        self.assertFalse(result.data["fail_fast_triggered"])
        self.assertEqual(4, len(result.data["steps"]))
        self.assertEqual(Severity.WARNING, result.severity)

    def test_show_origin_adds_fifth_step(self) -> None:
        orch = _make_orchestrator()
        orch.prefab_variant.resolve_prefab_chain.return_value = _ok_response()
        orch.prefab_variant.list_overrides.return_value = _ok_response()
        orch.prefab_variant.compute_effective_values.return_value = _ok_response()
        orch.prefab_variant.detect_stale_overrides.return_value = _ok_response()
        orch.prefab_variant.resolve_chain_values_with_origin.return_value = _ok_response()

        result = orch.inspect_variant("Assets/test.prefab", show_origin=True)
        self.assertTrue(result.success)
        self.assertEqual(5, len(result.data["steps"]))
        self.assertEqual(
            "resolve_chain_values_with_origin", result.data["steps"][4]["step"],
        )

    def test_show_origin_false_keeps_four_steps(self) -> None:
        orch = _make_orchestrator()
        orch.prefab_variant.resolve_prefab_chain.return_value = _ok_response()
        orch.prefab_variant.list_overrides.return_value = _ok_response()
        orch.prefab_variant.compute_effective_values.return_value = _ok_response()
        orch.prefab_variant.detect_stale_overrides.return_value = _ok_response()

        result = orch.inspect_variant("Assets/test.prefab", show_origin=False)
        self.assertTrue(result.success)
        self.assertEqual(4, len(result.data["steps"]))


class FileTypeGuardTests(unittest.TestCase):
    def test_inspect_wiring_warns_on_controller_file(self) -> None:
        import tempfile

        text = "--- !u!91 &100\nAnimatorController:\n  m_Name: Test\n"
        with tempfile.NamedTemporaryFile(suffix=".controller", mode="w", delete=False) as f:
            f.write(text)
            f.flush()
            orch = _make_orchestrator()
            result = orch.inspect_wiring(f.name)
        self.assertTrue(result.success)
        self.assertEqual(Severity.WARNING, result.severity)
        self.assertEqual("INSPECT_WIRING_NO_MONOBEHAVIOURS", result.code)
        self.assertEqual(".controller", result.data["file_type"])

    def test_inspect_wiring_warns_on_anim_file(self) -> None:
        import tempfile

        text = "--- !u!74 &100\nAnimationClip:\n  m_Name: Test\n"
        with tempfile.NamedTemporaryFile(suffix=".anim", mode="w", delete=False) as f:
            f.write(text)
            f.flush()
            orch = _make_orchestrator()
            result = orch.inspect_wiring(f.name)
        self.assertTrue(result.success)
        self.assertEqual(Severity.WARNING, result.severity)
        self.assertEqual("INSPECT_WIRING_NO_MONOBEHAVIOURS", result.code)

    def test_inspect_hierarchy_warns_on_anim_file(self) -> None:
        import tempfile

        text = "--- !u!74 &100\nAnimationClip:\n  m_Name: Test\n"
        with tempfile.NamedTemporaryFile(suffix=".anim", mode="w", delete=False) as f:
            f.write(text)
            f.flush()
            orch = _make_orchestrator()
            result = orch.inspect_hierarchy(f.name)
        self.assertTrue(result.success)
        self.assertEqual(Severity.WARNING, result.severity)
        self.assertEqual("INSPECT_HIERARCHY_NO_GAMEOBJECTS", result.code)
        self.assertEqual(".anim", result.data["file_type"])

    def test_inspect_structure_annotates_checks_on_controller_file(self) -> None:
        import tempfile

        text = "--- !u!91 &100\nAnimatorController:\n  m_Name: Test\n"
        with tempfile.NamedTemporaryFile(suffix=".controller", mode="w", delete=False) as f:
            f.write(text)
            f.flush()
            orch = _make_orchestrator()
            result = orch.inspect_structure(f.name)
        self.assertTrue(result.success)
        self.assertEqual(["duplicate_file_id"], result.data["checks_performed"])
        self.assertIn("transform_consistency", result.data["checks_skipped"])
        self.assertIn(".controller", result.data["skip_reason"])

    def test_inspect_structure_all_checks_on_prefab(self) -> None:
        import tempfile
        from tests.yaml_helpers import YAML_HEADER, make_gameobject, make_transform

        text = YAML_HEADER + make_gameobject("100", "Root", ["200"]) + make_transform("200", "100")
        with tempfile.NamedTemporaryFile(suffix=".prefab", mode="w", delete=False) as f:
            f.write(text)
            f.flush()
            orch = _make_orchestrator()
            result = orch.inspect_structure(f.name)
        self.assertTrue(result.success)
        self.assertEqual(4, len(result.data["checks_performed"]))
        self.assertEqual([], result.data["checks_skipped"])
        self.assertEqual("", result.data["skip_reason"])

    def test_inspect_wiring_normal_on_prefab(self) -> None:
        """Prefab files should proceed normally, not trigger the guard."""
        import tempfile
        from tests.yaml_helpers import YAML_HEADER, make_gameobject, make_monobehaviour

        text = YAML_HEADER + make_gameobject("100", "Obj", ["200"]) + make_monobehaviour("200", "100")
        with tempfile.NamedTemporaryFile(suffix=".prefab", mode="w", delete=False) as f:
            f.write(text)
            f.flush()
            orch = _make_orchestrator()
            with patch(
                "prefab_sentinel.orchestrator.find_project_root",
                side_effect=Exception("no project"),
            ):
                result = orch.inspect_wiring(f.name)
        self.assertEqual("INSPECT_WIRING_RESULT", result.code)


class InspectWiringTests(unittest.TestCase):
    def test_script_name_and_game_object_name(self) -> None:
        """inspect_wiring should include script_name and game_object_name in component summaries."""
        import tempfile
        from tests.yaml_helpers import YAML_HEADER, make_gameobject, make_monobehaviour

        text = (
            YAML_HEADER
            + make_gameobject("100", "MyObj", ["200"])
            + make_monobehaviour("200", "100", guid="aabbccdd11223344aabbccdd11223344")
            + "  someField: {fileID: 100}\n"
        )
        with tempfile.NamedTemporaryFile(suffix=".prefab", mode="w", delete=False) as f:
            f.write(text)
            f.flush()
            orch = _make_orchestrator()
            # Mock find_project_root and collect_project_guid_index
            with patch(
                "prefab_sentinel.orchestrator.find_project_root",
                return_value=Path("/fake"),
            ), patch(
                "prefab_sentinel.orchestrator.collect_project_guid_index",
                return_value={"aabbccdd11223344aabbccdd11223344": Path("/fake/Assets/Scripts/MyScript.cs")},
            ):
                result = orch.inspect_wiring(f.name)

        self.assertTrue(result.success)
        comps = result.data["components"]
        self.assertEqual(len(comps), 1)
        self.assertEqual(comps[0]["game_object_name"], "MyObj")
        self.assertEqual(comps[0]["script_name"], "MyScript")

    def test_script_name_empty_on_project_root_failure(self) -> None:
        """script_name should be empty when project root cannot be determined."""
        import tempfile
        from tests.yaml_helpers import YAML_HEADER, make_gameobject, make_monobehaviour

        text = (
            YAML_HEADER
            + make_gameobject("100", "Obj", ["200"])
            + make_monobehaviour("200", "100")
            + "  ref: {fileID: 100}\n"
        )
        with tempfile.NamedTemporaryFile(suffix=".prefab", mode="w", delete=False) as f:
            f.write(text)
            f.flush()
            orch = _make_orchestrator()
            with patch(
                "prefab_sentinel.orchestrator.find_project_root",
                side_effect=Exception("no project root"),
            ):
                result = orch.inspect_wiring(f.name)

        self.assertTrue(result.success)
        comps = result.data["components"]
        self.assertEqual(len(comps), 1)
        self.assertEqual(comps[0]["script_name"], "")


class InspectWiringVariantTests(unittest.TestCase):
    """inspect_wiring should detect Variant prefabs and analyze wiring from the base."""

    BASE_GUID = "aabbccddaabbccddaabbccddaabbccdd"

    def _make_variant_text(self) -> str:
        return (
            "%YAML 1.1\n"
            "--- !u!1001 &100100000\n"
            "PrefabInstance:\n"
            f"  m_SourcePrefab: {{fileID: 100100000, guid: {self.BASE_GUID}, type: 3}}\n"
            "  m_Modification:\n"
            "    m_Modifications: []\n"
        )

    def _make_base_text(self) -> str:
        from tests.yaml_helpers import YAML_HEADER, make_gameobject, make_monobehaviour

        return (
            YAML_HEADER
            + make_gameobject("100", "BaseObj", ["200"])
            + make_monobehaviour("200", "100")
            + "  myRef: {fileID: 100}\n"
        )

    def test_variant_resolves_base_wiring(self) -> None:
        import tempfile

        base_text = self._make_base_text()
        variant_text = self._make_variant_text()

        with tempfile.NamedTemporaryFile(
            suffix=".prefab", mode="w", delete=False, prefix="base_"
        ) as base_f:
            base_f.write(base_text)
            base_f.flush()
            base_path = base_f.name

        with tempfile.NamedTemporaryFile(
            suffix=".prefab", mode="w", delete=False, prefix="variant_"
        ) as var_f:
            var_f.write(variant_text)
            var_f.flush()
            variant_path = var_f.name

        orch = _make_orchestrator()
        orch.prefab_variant.resolve_prefab_chain.return_value = _ok_response(
            "CHAIN_OK",
            {
                "chain": [
                    {"path": variant_path, "guid": "variant_guid"},
                    {"path": base_path, "guid": self.BASE_GUID},
                ]
            },
        )
        with patch(
            "prefab_sentinel.orchestrator.find_project_root",
            side_effect=Exception("no project"),
        ):
            result = orch.inspect_wiring(variant_path)

        self.assertEqual("INSPECT_WIRING_RESULT", result.code)
        self.assertTrue(result.data.get("is_variant"))
        self.assertEqual(base_path, result.data.get("base_prefab_path"))
        # Wiring should come from the base prefab (which has a MonoBehaviour)
        self.assertGreater(result.data["component_count"], 0)

    def test_non_variant_has_no_variant_fields(self) -> None:
        import tempfile
        from tests.yaml_helpers import YAML_HEADER, make_gameobject, make_monobehaviour

        text = YAML_HEADER + make_gameobject("100", "Obj", ["200"]) + make_monobehaviour("200", "100")
        with tempfile.NamedTemporaryFile(suffix=".prefab", mode="w", delete=False) as f:
            f.write(text)
            f.flush()
            orch = _make_orchestrator()
            with patch(
                "prefab_sentinel.orchestrator.find_project_root",
                side_effect=Exception("no project"),
            ):
                result = orch.inspect_wiring(f.name)

        self.assertEqual("INSPECT_WIRING_RESULT", result.code)
        self.assertNotIn("is_variant", result.data)
        self.assertNotIn("base_prefab_path", result.data)

    def test_variant_chain_resolution_failure_falls_through(self) -> None:
        """If chain resolution returns no usable base, analyze the variant text as-is."""
        import tempfile

        variant_text = self._make_variant_text()
        with tempfile.NamedTemporaryFile(suffix=".prefab", mode="w", delete=False) as f:
            f.write(variant_text)
            f.flush()
            variant_path = f.name

        orch = _make_orchestrator()
        # Chain returns only the variant itself (no base)
        orch.prefab_variant.resolve_prefab_chain.return_value = _ok_response(
            "CHAIN_OK",
            {"chain": [{"path": variant_path, "guid": "variant_guid"}]},
        )
        with patch(
            "prefab_sentinel.orchestrator.find_project_root",
            side_effect=Exception("no project"),
        ):
            result = orch.inspect_wiring(variant_path)

        self.assertEqual("INSPECT_WIRING_RESULT", result.code)
        # Should not be marked as variant since no base was found
        self.assertNotIn("is_variant", result.data)
        # Component count should be 0 (Variant text has no MonoBehaviours)
        self.assertEqual(0, result.data["component_count"])


class InspectWhereUsedTests(unittest.TestCase):
    def test_passthrough(self) -> None:
        orch = _make_orchestrator()
        orch.reference_resolver.where_used.return_value = _ok_response(
            "WHERE_USED_OK", {"usages": []}
        )
        result = orch.inspect_where_used("abc123", scope="Assets/")
        self.assertTrue(result.success)
        self.assertEqual("INSPECT_WHERE_USED_RESULT", result.code)
        self.assertTrue(result.data["read_only"])


class ValidateRefsTests(unittest.TestCase):
    def test_passthrough(self) -> None:
        orch = _make_orchestrator()
        orch.reference_resolver.scan_broken_references.return_value = _ok_response(
            "REF_SCAN_OK", {"broken_count": 0}
        )
        result = orch.validate_refs("Assets/")
        self.assertTrue(result.success)
        self.assertEqual("VALIDATE_REFS_RESULT", result.code)

    def test_ignore_asset_guids_forwarded(self) -> None:
        orch = _make_orchestrator()
        orch.reference_resolver.scan_broken_references.return_value = _ok_response()
        orch.validate_refs("Assets/", ignore_asset_guids=("guid1", "guid2"))
        call_kwargs = orch.reference_resolver.scan_broken_references.call_args[1]
        self.assertEqual(("guid1", "guid2"), call_kwargs["ignore_asset_guids"])


class SuggestIgnoreGuidsTests(unittest.TestCase):
    def test_candidates_found(self) -> None:
        orch = _make_orchestrator()
        orch.reference_resolver.scan_broken_references.return_value = _ok_response(
            "REF_SCAN_BROKEN",
            {
                "categories_occurrences": {"missing_asset": 100},
                "top_missing_asset_guids": [
                    {"guid": "guid1", "occurrences": 80},
                    {"guid": "guid2", "occurrences": 15},
                    {"guid": "guid3", "occurrences": 3},
                ],
                "categories": {"missing_asset": 3},
                "scanned_files": 100,
                "scanned_references": 500,
                "broken_count": 3,
                "broken_occurrences": 98,
                "unreadable_files": 0,
            },
        )
        result = orch.suggest_ignore_guids("Assets/", min_occurrences=10)
        self.assertTrue(result.success)
        self.assertEqual(Severity.INFO, result.severity)
        # guid3 has 3 occurrences < 10, so only 2 candidates
        self.assertEqual(2, result.data["candidate_count"])

    def test_no_candidates(self) -> None:
        orch = _make_orchestrator()
        orch.reference_resolver.scan_broken_references.return_value = _ok_response(
            "REF_SCAN_BROKEN",
            {
                "categories_occurrences": {"missing_asset": 5},
                "top_missing_asset_guids": [
                    {"guid": "guid1", "occurrences": 3},
                ],
                "categories": {"missing_asset": 1},
                "scanned_files": 10,
                "scanned_references": 50,
                "broken_count": 1,
                "broken_occurrences": 3,
                "unreadable_files": 0,
            },
        )
        result = orch.suggest_ignore_guids("Assets/", min_occurrences=50)
        self.assertTrue(result.success)
        self.assertEqual(Severity.WARNING, result.severity)
        self.assertEqual(0, result.data["candidate_count"])

    def test_max_items_limit(self) -> None:
        orch = _make_orchestrator()
        top_guids = [{"guid": f"guid{i}", "occurrences": 100 - i} for i in range(10)]
        orch.reference_resolver.scan_broken_references.return_value = _ok_response(
            "REF_SCAN_BROKEN",
            {
                "categories_occurrences": {"missing_asset": 500},
                "top_missing_asset_guids": top_guids,
                "categories": {"missing_asset": 10},
                "scanned_files": 100,
                "scanned_references": 1000,
                "broken_count": 10,
                "broken_occurrences": 500,
                "unreadable_files": 0,
            },
        )
        result = orch.suggest_ignore_guids("Assets/", min_occurrences=1, max_items=3)
        self.assertEqual(3, result.data["candidate_count"])

    def test_scan_unexpected_code(self) -> None:
        orch = _make_orchestrator()
        orch.reference_resolver.scan_broken_references.return_value = _error_response(
            "REF404"
        )
        result = orch.suggest_ignore_guids("Assets/")
        self.assertFalse(result.success)

    def test_division_by_zero_guard(self) -> None:
        orch = _make_orchestrator()
        orch.reference_resolver.scan_broken_references.return_value = _ok_response(
            "REF_SCAN_BROKEN",
            {
                "categories_occurrences": {"missing_asset": 0},
                "top_missing_asset_guids": [
                    {"guid": "guid1", "occurrences": 5},
                ],
                "categories": {"missing_asset": 1},
                "scanned_files": 10,
                "scanned_references": 50,
                "broken_count": 1,
                "broken_occurrences": 5,
                "unreadable_files": 0,
            },
        )
        result = orch.suggest_ignore_guids("Assets/", min_occurrences=1)
        self.assertTrue(result.success)
        candidate = result.data["candidates"][0]
        self.assertEqual(0.0, candidate["share_of_missing_asset_occurrences"])


class ValidateRuntimeTests(unittest.TestCase):
    def test_all_steps_succeed(self) -> None:
        orch = _make_orchestrator()
        orch.runtime_validation.compile_udonsharp.return_value = _ok_response()
        orch.runtime_validation.run_clientsim.return_value = _ok_response()
        orch.runtime_validation.collect_unity_console.return_value = _ok_response(
            data={"log_lines": []}
        )
        orch.runtime_validation.classify_errors.return_value = _ok_response()
        orch.runtime_validation.assert_no_critical_errors.return_value = _ok_response()

        result = orch.validate_runtime("Assets/Scenes/Test.unity")
        self.assertTrue(result.success)
        self.assertEqual("VALIDATE_RUNTIME_RESULT", result.code)
        self.assertFalse(result.data["fail_fast_triggered"])
        self.assertEqual(5, len(result.data["steps"]))

    def test_fail_fast_on_run_clientsim_error(self) -> None:
        orch = _make_orchestrator()
        orch.runtime_validation.compile_udonsharp.return_value = _ok_response()
        orch.runtime_validation.run_clientsim.return_value = _error_response()

        result = orch.validate_runtime("Assets/Scenes/Test.unity")
        self.assertFalse(result.success)
        self.assertTrue(result.data["fail_fast_triggered"])
        self.assertEqual(2, len(result.data["steps"]))
        # collect/classify/assert should not be called
        orch.runtime_validation.collect_unity_console.assert_not_called()


class PostconditionSchemaTests(unittest.TestCase):
    def test_non_dict_postcondition(self) -> None:
        orch = _make_orchestrator()
        result = orch._validate_postcondition_schema("not_a_dict", resource_ids=set())
        self.assertFalse(result.success)
        self.assertEqual("POST_SCHEMA_ERROR", result.code)

    def test_missing_type(self) -> None:
        orch = _make_orchestrator()
        result = orch._validate_postcondition_schema({}, resource_ids=set())
        self.assertFalse(result.success)

    def test_asset_exists_resource_valid(self) -> None:
        orch = _make_orchestrator()
        result = orch._validate_postcondition_schema(
            {"type": "asset_exists", "resource": "res1"},
            resource_ids={"res1"},
        )
        self.assertTrue(result.success)
        self.assertEqual("POST_SCHEMA_OK", result.code)

    def test_asset_exists_path_valid(self) -> None:
        orch = _make_orchestrator()
        result = orch._validate_postcondition_schema(
            {"type": "asset_exists", "path": "Assets/test.json"},
            resource_ids=set(),
        )
        self.assertTrue(result.success)

    def test_asset_exists_both_resource_and_path(self) -> None:
        orch = _make_orchestrator()
        result = orch._validate_postcondition_schema(
            {"type": "asset_exists", "resource": "res1", "path": "Assets/test.json"},
            resource_ids={"res1"},
        )
        self.assertFalse(result.success)
        self.assertIn("exactly one", result.message)

    def test_asset_exists_neither_resource_nor_path(self) -> None:
        orch = _make_orchestrator()
        result = orch._validate_postcondition_schema(
            {"type": "asset_exists"},
            resource_ids=set(),
        )
        self.assertFalse(result.success)

    def test_asset_exists_unknown_resource(self) -> None:
        orch = _make_orchestrator()
        result = orch._validate_postcondition_schema(
            {"type": "asset_exists", "resource": "unknown"},
            resource_ids={"res1"},
        )
        self.assertFalse(result.success)
        self.assertIn("unknown resource", result.message)

    def test_broken_refs_valid(self) -> None:
        orch = _make_orchestrator()
        result = orch._validate_postcondition_schema(
            {"type": "broken_refs", "scope": "Assets/", "expected_count": 0},
            resource_ids=set(),
        )
        self.assertTrue(result.success)

    def test_broken_refs_empty_scope(self) -> None:
        orch = _make_orchestrator()
        result = orch._validate_postcondition_schema(
            {"type": "broken_refs", "scope": "", "expected_count": 0},
            resource_ids=set(),
        )
        self.assertFalse(result.success)

    def test_broken_refs_negative_expected_count(self) -> None:
        orch = _make_orchestrator()
        result = orch._validate_postcondition_schema(
            {"type": "broken_refs", "scope": "Assets/", "expected_count": -1},
            resource_ids=set(),
        )
        self.assertFalse(result.success)

    def test_broken_refs_non_int_expected_count(self) -> None:
        orch = _make_orchestrator()
        result = orch._validate_postcondition_schema(
            {"type": "broken_refs", "scope": "Assets/", "expected_count": "five"},
            resource_ids=set(),
        )
        self.assertFalse(result.success)

    def test_broken_refs_non_list_exclude_patterns(self) -> None:
        orch = _make_orchestrator()
        result = orch._validate_postcondition_schema(
            {
                "type": "broken_refs",
                "scope": "Assets/",
                "expected_count": 0,
                "exclude_patterns": "not_a_list",
            },
            resource_ids=set(),
        )
        self.assertFalse(result.success)

    def test_broken_refs_non_string_items(self) -> None:
        orch = _make_orchestrator()
        result = orch._validate_postcondition_schema(
            {
                "type": "broken_refs",
                "scope": "Assets/",
                "expected_count": 0,
                "exclude_patterns": [123],
            },
            resource_ids=set(),
        )
        self.assertFalse(result.success)

    def test_broken_refs_negative_max_diagnostics(self) -> None:
        orch = _make_orchestrator()
        result = orch._validate_postcondition_schema(
            {
                "type": "broken_refs",
                "scope": "Assets/",
                "expected_count": 0,
                "max_diagnostics": -1,
            },
            resource_ids=set(),
        )
        self.assertFalse(result.success)

    def test_unknown_type(self) -> None:
        orch = _make_orchestrator()
        result = orch._validate_postcondition_schema(
            {"type": "unknown_type"},
            resource_ids=set(),
        )
        self.assertFalse(result.success)
        self.assertIn("not supported", result.message)


class PatchApplyTests(unittest.TestCase):
    def _minimal_plan(self, mode: str = "open", kind: str = "json", path: str = "test.json") -> dict:
        return {
            "plan_version": 2,
            "resources": [
                {"id": "res1", "path": path, "kind": kind, "mode": mode},
            ],
            "ops": [
                {"resource": "res1", "op": "set", "property_path": "key", "value": "val"},
            ],
            "postconditions": [],
        }

    def _make_orch_with_dry_run(self) -> Phase1Orchestrator:
        orch = _make_orchestrator()
        orch.serialized_object.dry_run_resource_plan.return_value = _ok_response(
            "DRY_RUN_OK"
        )
        return orch

    def test_dry_run_returns_without_apply(self) -> None:
        orch = self._make_orch_with_dry_run()
        result = orch.patch_apply(self._minimal_plan(), dry_run=True)
        self.assertTrue(result.success)
        self.assertIn("dry-run", result.message)
        self.assertTrue(result.data["dry_run"])
        self.assertTrue(result.data["read_only"])
        orch.serialized_object.apply_resource_plan.assert_not_called()

    def test_confirm_gate_blocks(self) -> None:
        orch = self._make_orch_with_dry_run()
        result = orch.patch_apply(self._minimal_plan(), dry_run=False, confirm=False)
        self.assertFalse(result.success)
        self.assertTrue(result.data["read_only"])
        step_names = [s["step"] for s in result.data["steps"]]
        self.assertIn("confirm_gate", step_names)
        orch.serialized_object.apply_resource_plan.assert_not_called()

    def test_confirm_gate_passes(self) -> None:
        orch = self._make_orch_with_dry_run()
        orch.serialized_object.apply_resource_plan.return_value = _ok_response(
            "APPLY_OK"
        )
        result = orch.patch_apply(self._minimal_plan(), dry_run=False, confirm=True)
        self.assertTrue(result.success)
        self.assertFalse(result.data["read_only"])
        orch.serialized_object.apply_resource_plan.assert_called_once()

    def test_single_resource_step_naming(self) -> None:
        orch = self._make_orch_with_dry_run()
        result = orch.patch_apply(self._minimal_plan(), dry_run=True)
        step_names = [s["step"] for s in result.data["steps"]]
        self.assertIn("dry_run_patch", step_names)
        self.assertNotIn("dry_run_patch:res1", step_names)

    def test_multi_resource_step_naming(self) -> None:
        orch = _make_orchestrator()
        orch.serialized_object.dry_run_resource_plan.return_value = _ok_response()
        plan = {
            "plan_version": 2,
            "resources": [
                {"id": "res1", "path": "a.json", "kind": "json", "mode": "open"},
                {"id": "res2", "path": "b.json", "kind": "json", "mode": "open"},
            ],
            "ops": [
                {"resource": "res1", "op": "set", "property_path": "k", "value": "v"},
                {"resource": "res2", "op": "set", "property_path": "k", "value": "v"},
            ],
            "postconditions": [],
        }
        result = orch.patch_apply(plan, dry_run=True)
        step_names = [s["step"] for s in result.data["steps"]]
        self.assertIn("dry_run_patch:res1", step_names)
        self.assertIn("dry_run_patch:res2", step_names)

    def test_dry_run_fail_fast_on_error(self) -> None:
        orch = _make_orchestrator()
        orch.serialized_object.dry_run_resource_plan.return_value = _error_response()
        result = orch.patch_apply(self._minimal_plan(), dry_run=True)
        self.assertFalse(result.success)
        self.assertTrue(result.data["fail_fast_triggered"])

    def test_postcondition_schema_fail_fast(self) -> None:
        orch = self._make_orch_with_dry_run()
        plan = self._minimal_plan()
        # asset_exists with neither resource nor path → schema error at orchestrator level
        plan["postconditions"] = [{"type": "asset_exists"}]
        result = orch.patch_apply(plan, dry_run=False, confirm=True)
        self.assertFalse(result.success)
        self.assertTrue(result.data["fail_fast_triggered"])
        orch.serialized_object.dry_run_resource_plan.assert_not_called()

    def test_preflight_ref_scan_when_scope_set(self) -> None:
        orch = self._make_orch_with_dry_run()
        orch.serialized_object.apply_resource_plan.return_value = _ok_response()
        orch.reference_resolver.scan_broken_references.return_value = _ok_response(
            "REF_SCAN_OK"
        )
        result = orch.patch_apply(
            self._minimal_plan(), dry_run=False, confirm=True, scope="Assets/"
        )
        step_names = [s["step"] for s in result.data["steps"]]
        self.assertIn("scan_broken_references_preflight", step_names)

    def test_no_preflight_ref_scan_when_no_scope(self) -> None:
        orch = self._make_orch_with_dry_run()
        orch.serialized_object.apply_resource_plan.return_value = _ok_response()
        result = orch.patch_apply(
            self._minimal_plan(), dry_run=False, confirm=True, scope=None
        )
        step_names = [s["step"] for s in result.data["steps"]]
        self.assertNotIn("scan_broken_references_preflight", step_names)

    def test_list_overrides_for_prefab_open(self) -> None:
        orch = self._make_orch_with_dry_run()
        orch.serialized_object.apply_resource_plan.return_value = _ok_response()
        orch.prefab_variant.list_overrides.return_value = _ok_response()
        plan = self._minimal_plan(kind="prefab", path="Assets/test.prefab", mode="open")
        result = orch.patch_apply(plan, dry_run=False, confirm=True)
        step_names = [s["step"] for s in result.data["steps"]]
        self.assertIn("list_overrides_preflight", step_names)

    def test_no_list_overrides_for_json(self) -> None:
        orch = self._make_orch_with_dry_run()
        orch.serialized_object.apply_resource_plan.return_value = _ok_response()
        plan = self._minimal_plan(kind="json", path="test.json", mode="open")
        result = orch.patch_apply(plan, dry_run=False, confirm=True)
        step_names = [s["step"] for s in result.data["steps"]]
        self.assertNotIn("list_overrides_preflight", step_names)

    def test_no_list_overrides_for_prefab_create(self) -> None:
        orch = self._make_orch_with_dry_run()
        orch.serialized_object.apply_resource_plan.return_value = _ok_response()
        orch.prefab_variant.list_overrides.return_value = _ok_response()
        plan = self._minimal_plan(kind="prefab", path="Assets/test.prefab", mode="create")
        result = orch.patch_apply(plan, dry_run=False, confirm=True)
        step_names = [s["step"] for s in result.data["steps"]]
        self.assertNotIn("list_overrides_preflight", step_names)

    def test_runtime_validation_when_scene_set(self) -> None:
        orch = self._make_orch_with_dry_run()
        orch.serialized_object.apply_resource_plan.return_value = _ok_response()
        orch.runtime_validation.compile_udonsharp.return_value = _ok_response()
        orch.runtime_validation.run_clientsim.return_value = _ok_response()
        orch.runtime_validation.collect_unity_console.return_value = _ok_response(
            data={"log_lines": []}
        )
        orch.runtime_validation.classify_errors.return_value = _ok_response()
        orch.runtime_validation.assert_no_critical_errors.return_value = _ok_response()

        result = orch.patch_apply(
            self._minimal_plan(),
            dry_run=False,
            confirm=True,
            runtime_scene="Assets/Scenes/Test.unity",
        )
        step_names = [s["step"] for s in result.data["steps"]]
        self.assertIn("compile_udonsharp", step_names)
        self.assertIn("run_clientsim", step_names)

    def test_no_runtime_validation_when_no_scene(self) -> None:
        orch = self._make_orch_with_dry_run()
        orch.serialized_object.apply_resource_plan.return_value = _ok_response()
        result = orch.patch_apply(
            self._minimal_plan(), dry_run=False, confirm=True, runtime_scene=None
        )
        step_names = [s["step"] for s in result.data["steps"]]
        self.assertNotIn("compile_udonsharp", step_names)

    def test_execution_id_and_timestamp(self) -> None:
        orch = self._make_orch_with_dry_run()
        result = orch.patch_apply(self._minimal_plan(), dry_run=True)
        self.assertEqual(32, len(result.data["execution_id"]))
        self.assertIn("T", result.data["executed_at_utc"])

    def test_change_reason_stripped(self) -> None:
        orch = self._make_orch_with_dry_run()
        result = orch.patch_apply(
            self._minimal_plan(), dry_run=True, change_reason="  test reason  "
        )
        self.assertEqual("test reason", result.data["change_reason"])

    def test_change_reason_none(self) -> None:
        orch = self._make_orch_with_dry_run()
        result = orch.patch_apply(self._minimal_plan(), dry_run=True, change_reason=None)
        self.assertIsNone(result.data["change_reason"])

    def test_success_with_warnings(self) -> None:
        orch = _make_orchestrator()
        orch.serialized_object.dry_run_resource_plan.return_value = _ok_response()
        orch.serialized_object.apply_resource_plan.return_value = _warning_response()
        result = orch.patch_apply(self._minimal_plan(), dry_run=False, confirm=True)
        self.assertFalse(result.success)
        self.assertIn("warnings", result.message)


class EvaluatePostconditionTests(unittest.TestCase):
    def test_asset_exists_found(self) -> None:
        orch = _make_orchestrator()
        with patch.object(Path, "exists", return_value=True):
            orch.serialized_object._resolve_target_path.return_value = Path("/tmp/test.json")
            result = orch._evaluate_postcondition(
                {"type": "asset_exists", "path": "Assets/test.json"},
                resource_map={},
            )
        self.assertTrue(result.success)
        self.assertEqual("POST_ASSET_EXISTS_OK", result.code)

    def test_asset_exists_missing(self) -> None:
        orch = _make_orchestrator()
        with patch.object(Path, "exists", return_value=False):
            orch.serialized_object._resolve_target_path.return_value = Path("/tmp/test.json")
            result = orch._evaluate_postcondition(
                {"type": "asset_exists", "path": "Assets/test.json"},
                resource_map={},
            )
        self.assertFalse(result.success)
        self.assertEqual("POST_ASSET_EXISTS_FAILED", result.code)

    def test_asset_exists_via_resource_map(self) -> None:
        orch = _make_orchestrator()
        with patch.object(Path, "exists", return_value=True):
            orch.serialized_object._resolve_target_path.return_value = Path("/tmp/out.json")
            result = orch._evaluate_postcondition(
                {"type": "asset_exists", "resource": "res1"},
                resource_map={"res1": {"path": "Assets/out.json"}},
            )
        self.assertTrue(result.success)

    def test_broken_refs_count_match(self) -> None:
        orch = _make_orchestrator()
        orch.reference_resolver.scan_broken_references.return_value = _ok_response(
            "REF_SCAN_OK", {"broken_count": 0}
        )
        result = orch._evaluate_postcondition(
            {"type": "broken_refs", "scope": "Assets/", "expected_count": 0},
            resource_map={},
        )
        self.assertTrue(result.success)
        self.assertEqual("POST_BROKEN_REFS_OK", result.code)

    def test_broken_refs_count_mismatch(self) -> None:
        orch = _make_orchestrator()
        orch.reference_resolver.scan_broken_references.return_value = _ok_response(
            "REF_SCAN_BROKEN", {"broken_count": 5}
        )
        result = orch._evaluate_postcondition(
            {"type": "broken_refs", "scope": "Assets/", "expected_count": 0},
            resource_map={},
        )
        self.assertFalse(result.success)
        self.assertEqual("POST_BROKEN_REFS_FAILED", result.code)
        self.assertEqual(5, result.data["actual_count"])
        self.assertEqual(0, result.data["expected_count"])

    def test_broken_refs_scan_error(self) -> None:
        orch = _make_orchestrator()
        orch.reference_resolver.scan_broken_references.return_value = _error_response(
            "REF404"
        )
        result = orch._evaluate_postcondition(
            {"type": "broken_refs", "scope": "Assets/", "expected_count": 0},
            resource_map={},
        )
        self.assertFalse(result.success)
        self.assertEqual("POST_BROKEN_REFS_ERROR", result.code)


if __name__ == "__main__":
    unittest.main()
