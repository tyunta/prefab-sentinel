from __future__ import annotations

import json
import os
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, cast
from unittest.mock import call, patch

from prefab_sentinel.contracts import Diagnostic, Severity, ToolResponse
from prefab_sentinel.diagnostics_baseline import DiagnosticKeyRecord
from prefab_sentinel.orchestrator import Phase1Orchestrator
from prefab_sentinel.patch_plan import (
    is_single_open_prefab_plan,
    iter_resource_batches,
    normalize_patch_plan,
)
from prefab_sentinel.patch_transaction import _contained_target_path
from prefab_sentinel.patch_transaction_diagnostics import (
    classify_transaction_diagnostic_keys,
)
from prefab_sentinel.patch_transaction_io import (
    reserve_transaction_report,
    write_transaction_report,
)
from prefab_sentinel.patch_transaction_results import boundary_failure, persist_terminal
from prefab_sentinel.services.serialized_object import SerializedObjectService


class TestOpenPrefabCompositionDryRun(unittest.TestCase):
    def _dry_run(self, ops: list[dict[str, Any]]):
        plan = normalize_patch_plan(
            {
                "plan_version": 2,
                "resources": [
                    {
                        "id": "target",
                        "kind": "prefab",
                        "path": "Assets/Target.prefab",
                        "mode": "open",
                    }
                ],
                "ops": [{"resource": "target", **op} for op in ops],
            }
        )
        resource, resource_ops = iter_resource_batches(plan)[0]
        return SerializedObjectService().dry_run_resource_plan(resource, resource_ops)

    def test_nested_prefab_workflow_exposes_ordered_handle_and_write_intent(self) -> None:
        response = self._dry_run(
            [
                {
                    "op": "instantiate_prefab",
                    "prefab": "Assets/Source.prefab",
                    "parent": "$root",
                    "result": "nested",
                },
                {"op": "rename_object", "target": "$nested", "name": "Nested B"},
                {
                    "op": "find_game_object",
                    "target": "$nested",
                    "relative_symbol_path": "Screens/Output#0",
                    "result": "screen",
                },
                {
                    "op": "find_component",
                    "target": "$screen",
                    "type": "Example.ScreenTarget",
                    "result": "screen_component",
                },
                {
                    "op": "find_game_object",
                    "symbol_path": "Existing/Controller",
                    "result": "controller",
                },
                {
                    "op": "find_component",
                    "target": "$controller",
                    "type": "Example.Controller",
                    "result": "controller_component",
                },
                {
                    "op": "set",
                    "target": "$controller_component",
                    "path": "m_Target",
                    "value": {"handle": "$screen_component"},
                },
            ]
        )

        self.assertEqual(
            (True, "SER_DRY_RUN_OK"),
            (response.success, response.code),
            msg=f"unexpected dry-run envelope: {response!r}",
        )
        self.assertEqual(
            (0, True),
            (response.data["applied"], response.data["read_only"]),
        )
        self.assertEqual(
            [
                "instantiate_prefab",
                "rename_object",
                "find_game_object",
                "find_component",
                "find_game_object",
                "find_component",
                "set",
            ],
            [row["op"] for row in response.data["diff"]],
        )
        self.assertEqual(
            {
                "prefab": "Assets/Source.prefab",
                "parent": "root",
                "handle": "nested",
                "kind": "game_object",
            },
            response.data["diff"][0]["after"],
        )
        self.assertEqual(
            {
                "target": "nested",
                "relative_symbol_path": "Screens/Output#0",
                "handle": "screen",
                "kind": "game_object",
            },
            response.data["diff"][2]["after"],
        )
        self.assertEqual(
            {
                "handle": "controller_component",
                "path": "m_Target",
                "value": {"handle": "$screen_component"},
            },
            response.data["diff"][6]["after"],
        )

    def test_existing_game_object_file_id_is_an_exact_address(self) -> None:
        response = self._dry_run(
            [
                {
                    "op": "find_game_object",
                    "file_id": "123456",
                    "result": "existing",
                }
            ]
        )

        self.assertEqual((True, "SER_DRY_RUN_OK"), (response.success, response.code))
        self.assertEqual(
            {
                "file_id": "123456",
                "handle": "existing",
                "kind": "game_object",
            },
            response.data["diff"][0]["after"],
        )

    def test_invalid_composition_partitions_report_the_exact_op_field(self) -> None:
        cases = (
            (
                "both existing addresses",
                {
                    "op": "find_game_object",
                    "symbol_path": "Existing",
                    "file_id": "123",
                    "result": "found",
                },
                "ops[0].symbol_path",
            ),
            (
                "neither existing address",
                {"op": "find_game_object", "result": "found"},
                "ops[0]",
            ),
            (
                "unknown generated root",
                {
                    "op": "find_game_object",
                    "target": "$missing",
                    "relative_symbol_path": "Child",
                    "result": "found",
                },
                "ops[0].target",
            ),
            (
                "invalid relative traversal",
                {
                    "op": "find_game_object",
                    "target": "$root",
                    "relative_symbol_path": "../Child",
                    "result": "found",
                },
                "ops[0].relative_symbol_path",
            ),
            (
                "invalid sibling selector",
                {
                    "op": "find_game_object",
                    "target": "$root",
                    "relative_symbol_path": "Child#x",
                    "result": "found",
                },
                "ops[0].relative_symbol_path",
            ),
            (
                "duplicate handle",
                {"op": "find_game_object", "symbol_path": "Existing", "result": "root"},
                "ops[0].result",
            ),
            (
                "recursive lookup flag",
                {
                    "op": "find_game_object",
                    "symbol_path": "Child",
                    "recursive": True,
                    "result": "found",
                },
                "ops[0].recursive",
            ),
            (
                "automatic type lookup flag",
                {
                    "op": "find_component",
                    "target": "$root",
                    "type": "Example.Controller",
                    "search_children": True,
                    "result": "component",
                },
                "ops[0].search_children",
            ),
            (
                "unknown handle value",
                {
                    "op": "set",
                    "target": "$component",
                    "path": "m_Target",
                    "value": {"handle": "$missing"},
                },
                "ops[0].target",
            ),
            (
                "explicit save",
                {"op": "save"},
                "ops[0].op",
            ),
            (
                "wiring-specific operation",
                {"op": "wire_reference"},
                "ops[0].op",
            ),
        )

        for label, op, location in cases:
            with self.subTest(label=label):
                response = self._dry_run([op])
                self.assertEqual(
                    (
                        False,
                        "SER_PLAN_INVALID",
                        location,
                        "Open Prefab operation schema is invalid.",
                    ),
                    (
                        response.success,
                        response.code,
                        response.diagnostics[0].location,
                        response.diagnostics[0].evidence,
                    ),
                    msg=f"unexpected invalid-plan envelope for {label}: {response!r}",
                )

    def test_invalid_open_prefab_plan_preserves_public_error_code_and_field(self) -> None:
        plan = {
            "plan_version": 2,
            "resources": [
                {
                    "id": "target",
                    "kind": "prefab",
                    "path": "Assets/Target.prefab",
                    "mode": "open",
                }
            ],
            "ops": [
                {
                    "resource": "target",
                    "op": "find_game_object",
                    "symbol_path": "Child",
                    "recursive": True,
                    "result": "found",
                }
            ],
        }
        with TemporaryDirectory() as project_root:
            response = Phase1Orchestrator.default(Path(project_root)).patch_apply(
                plan,
                dry_run=True,
            )

        self.assertEqual(
            (False, "error", "INVALID_PLAN_SCHEMA"),
            (response.success, response.severity.value, response.code),
            msg=f"unexpected public invalid-plan envelope: {response!r}",
        )
        self.assertEqual("ops[0].recursive", response.diagnostics[0].location)
        self.assertEqual(
            "Open Prefab operation schema is invalid.",
            response.diagnostics[0].evidence,
        )

    def test_legacy_value_ops_fail_before_dispatch_or_transaction_reservation(
        self,
    ) -> None:
        cases = (
            (
                "targetless set",
                {
                    "op": "set",
                    "component": "Example.Component",
                    "path": "m_Enabled",
                    "value": True,
                },
                "ops[0].component",
            ),
            (
                "fileID-targeted set",
                {
                    "op": "set",
                    "file_id": "300",
                    "path": "m_Enabled",
                    "value": True,
                },
                "ops[0].file_id",
            ),
            (
                "insert array element",
                {
                    "op": "insert_array_element",
                    "component": "Example.Component",
                    "path": "items.Array.data",
                    "index": 0,
                    "value": 1,
                },
                "ops[0].op",
            ),
            (
                "remove array element",
                {
                    "op": "remove_array_element",
                    "component": "Example.Component",
                    "path": "items.Array.data",
                    "index": 0,
                },
                "ops[0].op",
            ),
        )

        for case_label, operation, expected_location in cases:
            for dry_run in (True, False):
                with self.subTest(operation=case_label, dry_run=dry_run):
                    with TemporaryDirectory() as temporary:
                        project_root = Path(temporary)
                        assets = project_root / "Assets"
                        assets.mkdir()
                        (assets / "Target.prefab").write_bytes(b"before")
                        report = project_root / "transaction.json"
                        plan = {
                            "plan_version": 2,
                            "resources": [
                                {
                                    "id": "target",
                                    "kind": "prefab",
                                    "path": "Assets/Target.prefab",
                                    "mode": "open",
                                }
                            ],
                            "ops": [{"resource": "target", **operation}],
                        }
                        orchestrator = Phase1Orchestrator.default(project_root)
                        legacy_dry_run = (
                            orchestrator.serialized_object.dry_run_patch
                        )
                        with patch.object(
                            orchestrator.serialized_object,
                            "dry_run_patch",
                            wraps=legacy_dry_run,
                        ) as legacy_dry_run_mock:
                            response = orchestrator.patch_apply(
                                plan,
                                dry_run=dry_run,
                                confirm=not dry_run,
                                transactional=not dry_run,
                                change_reason=(
                                    None if dry_run else "invalid plan probe"
                                ),
                                out_report=None if dry_run else str(report),
                            )

                        self.assertEqual(
                            (
                                False,
                                "error",
                                "INVALID_PLAN_SCHEMA",
                                expected_location,
                                False,
                                0,
                            ),
                            (
                                response.success,
                                response.severity.value,
                                response.code,
                                response.diagnostics[0].location,
                                report.exists(),
                                legacy_dry_run_mock.call_count,
                            ),
                            msg=(
                                "legacy value operation bypassed the "
                                f"open-Prefab grammar: {response!r}"
                            ),
                        )
                        self.assertEqual(
                            "Open Prefab operation schema is invalid.",
                            response.diagnostics[0].evidence,
                        )

    def test_confirmed_invalid_plan_fails_before_transaction_reservation(self) -> None:
        plan = {
            "plan_version": 2,
            "resources": [
                {
                    "id": "target",
                    "kind": "prefab",
                    "path": "Assets/Target.prefab",
                    "mode": "open",
                }
            ],
            "ops": [
                {
                    "resource": "target",
                    "op": "find_game_object",
                    "symbol_path": "Child",
                    "recursive": True,
                    "result": "found",
                }
            ],
        }
        with TemporaryDirectory() as temporary:
            project_root = Path(temporary)
            (project_root / "Assets").mkdir()
            (project_root / "Assets" / "Target.prefab").write_bytes(b"before")
            report = project_root / "transaction.json"
            response = Phase1Orchestrator.default(project_root).patch_apply(
                plan,
                confirm=True,
                transactional=True,
                change_reason="invalid plan probe",
                out_report=str(report),
            )

            self.assertEqual(
                (False, "error", "INVALID_PLAN_SCHEMA", False),
                (
                    response.success,
                    response.severity.value,
                    response.code,
                    report.exists(),
                ),
                msg=f"invalid grammar must fail before transaction state exists: {response!r}",
            )
        self.assertEqual("ops[0].recursive", response.diagnostics[0].location)


class _TransactionTestCase(unittest.TestCase):
    @staticmethod
    def _response(
        code: str,
        *,
        severity: Severity = Severity.INFO,
        data: dict[str, Any] | None = None,
        diagnostics: list[Diagnostic] | None = None,
    ) -> ToolResponse:
        return ToolResponse(
            success=severity not in (Severity.ERROR, Severity.CRITICAL),
            severity=severity,
            code=code,
            message=code,
            data={} if data is None else data,
            diagnostics=[] if diagnostics is None else diagnostics,
        )

    @staticmethod
    def _plan(
        *,
        kind: str = "prefab",
        mode: str = "open",
    ) -> dict[str, Any]:
        return {
            "plan_version": 2,
            "resources": [
                {
                    "id": "target",
                    "kind": kind,
                    "path": "Assets/Target.prefab",
                    "mode": mode,
                }
            ],
            "ops": [
                {
                    "resource": "target",
                    "op": "rename_object",
                    "target": "$root",
                    "name": "Nested B",
                }
            ],
        }

    def _assert_preflight_dispatch(
        self,
        *,
        plan: dict[str, Any],
        dry_run_mock: Any,
        overrides_mock: Any,
        structure_mock: Any,
        refs_mock: Any,
        nested_apply_expected: bool,
        post_validation_expected: bool,
    ) -> None:
        resource = plan["resources"][0]
        ops = [{key: value for key, value in plan["ops"][0].items() if key != "resource"}]
        self.assertEqual(
            [call(resource=resource, ops=ops)] * (2 if nested_apply_expected else 1),
            dry_run_mock.call_args_list,
        )
        self.assertEqual(
            [call("Assets/Target.prefab")] if nested_apply_expected else [],
            overrides_mock.call_args_list,
        )
        self.assertEqual(
            [call("Assets/Target.prefab")] * (2 if post_validation_expected else 1),
            structure_mock.call_args_list,
        )
        self.assertEqual(
            [call("Assets/Target.prefab", details=True, max_diagnostics=200)] * (2 if post_validation_expected else 1),
            refs_mock.call_args_list,
        )

    def _execute(
        self,
        orch: Phase1Orchestrator,
        target: Path,
        report: Path,
        *,
        apply_result: ToolResponse,
        structure_results: list[ToolResponse],
        reference_results: list[ToolResponse],
        refresh_status: str = "true",
        plan: dict[str, Any] | None = None,
        runtime_scene: str | None = None,
        runtime_compile_result: ToolResponse | None = None,
        postcondition_result: ToolResponse | None = None,
    ) -> tuple[ToolResponse, int]:
        transaction_plan = self._plan() if plan is None else plan

        def apply_once(*, resource: dict[str, Any], ops: list[dict[str, Any]]):
            self.assertEqual("Assets/Target.prefab", resource["path"])
            self.assertEqual("rename_object", ops[0]["op"])
            target.write_bytes(b"after")
            return apply_result

        nested_apply_expected = all(
            response.severity not in (Severity.ERROR, Severity.CRITICAL)
            for response in (structure_results[0], reference_results[0])
        )
        inner_failure_expected = (
            (
                runtime_scene is not None
                and runtime_compile_result is not None
                and not runtime_compile_result.success
            )
            or (
                postcondition_result is not None
                and not postcondition_result.success
            )
        )
        with (
            patch.object(
                orch.serialized_object,
                "dry_run_resource_plan",
                return_value=self._response("DRY_RUN"),
            ) as dry_run_mock,
            patch.object(
                orch.serialized_object,
                "apply_resource_plan",
                side_effect=apply_once,
            ) as apply_mock,
            patch.object(
                orch.prefab_variant,
                "list_overrides",
                return_value=self._response("OVERRIDES"),
            ) as overrides_mock,
            patch.object(
                Phase1Orchestrator,
                "inspect_structure",
                side_effect=structure_results,
            ) as structure_mock,
            patch.object(
                Phase1Orchestrator,
                "validate_refs",
                side_effect=reference_results,
            ) as refs_mock,
            patch.object(
                Phase1Orchestrator,
                "maybe_auto_refresh",
                return_value=refresh_status,
            ),
            patch.object(
                orch.runtime_validation,
                "compile_udonsharp",
                return_value=runtime_compile_result or self._response("COMPILE_OK"),
            ) as compile_mock,
            patch(
                "prefab_sentinel.orchestrator_patch.evaluate_postcondition",
                return_value=postcondition_result or self._response("POSTCONDITION_OK"),
            ) as postcondition_mock,
        ):
            result = orch.patch_apply(
                plan=transaction_plan,
                confirm=True,
                change_reason="Compose nested prefab",
                out_report=str(report),
                runtime_scene=runtime_scene,
                transactional=True,
            )
        post_validation_expected = (
            nested_apply_expected
            and apply_result.success
            and not inner_failure_expected
        )
        self._assert_preflight_dispatch(
            plan=transaction_plan,
            dry_run_mock=dry_run_mock,
            overrides_mock=overrides_mock,
            structure_mock=structure_mock,
            refs_mock=refs_mock,
            nested_apply_expected=nested_apply_expected,
            post_validation_expected=post_validation_expected,
        )
        self.assertEqual(
            1 if runtime_scene is not None and nested_apply_expected and apply_result.success else 0,
            compile_mock.call_count,
        )
        self.assertEqual(
            1
            if transaction_plan.get("postconditions")
            and nested_apply_expected
            and apply_result.success
            and (
                runtime_scene is None
                or runtime_compile_result is None
                or runtime_compile_result.success
            )
            else 0,
            postcondition_mock.call_count,
        )
        return result, apply_mock.call_count


class TestSerializedValuePatchApply(_TransactionTestCase):
    @staticmethod
    def _value_plan() -> dict[str, Any]:
        return {
            "plan_version": 2,
            "resources": [
                {
                    "id": "target",
                    "path": "Assets/Target.prefab",
                    "mode": "open",
                }
            ],
            "ops": [
                {
                    "resource": "target",
                    "op": "set",
                    "file_id": 11400000,
                    "path": "m_Speed",
                    "value": 3,
                }
            ],
        }

    def test_confirmed_prefab_write_dispatches_dedicated_steps(self) -> None:
        with TemporaryDirectory() as tmp:
            orch = Phase1Orchestrator.default(Path(tmp))
            expected_ops = [
                {
                    "op": "set",
                    "file_id": 11400000,
                    "path": "m_Speed",
                    "value": 3,
                }
            ]
            with (
                patch.object(
                    orch.serialized_object,
                    "dry_run_patch",
                    return_value=self._response("SER_DRY_RUN_OK"),
                ) as dry_run_mock,
                patch.object(
                    orch.prefab_variant,
                    "list_overrides",
                    return_value=self._response("PREFAB_OVERRIDES_LISTED"),
                ) as overrides_mock,
                patch.object(
                    orch.serialized_object,
                    "apply_and_save",
                    return_value=self._response(
                        "SER_APPLY_OK",
                        data={"applied": 1},
                    ),
                ) as apply_mock,
            ):
                result = orch.serialized_value_patch_apply(
                    plan=self._value_plan(),
                    dry_run=False,
                    confirm=True,
                    change_reason="  Update speed  ",
                )

        self.assertEqual(
            (True, "PATCH_APPLY_RESULT"),
            (result.success, result.code),
        )
        self.assertEqual(False, result.data["read_only"])
        self.assertEqual("Update speed", result.data["change_reason"])
        self.assertEqual(
            [
                ("dry_run_patch", "SER_DRY_RUN_OK"),
                ("list_overrides_preflight", "PREFAB_OVERRIDES_LISTED"),
                ("apply_and_save", "SER_APPLY_OK"),
            ],
            [
                (step["step"], step["result"]["code"])
                for step in result.data["steps"]
            ],
        )
        self.assertEqual(1, result.data["resources"][0]["applied"])
        self.assertEqual(
            [call(target="Assets/Target.prefab", ops=expected_ops)],
            dry_run_mock.call_args_list,
        )
        self.assertEqual(
            [call("Assets/Target.prefab")],
            overrides_mock.call_args_list,
        )
        self.assertEqual(
            [call(target="Assets/Target.prefab", ops=expected_ops)],
            apply_mock.call_args_list,
        )


class TestSinglePrefabTransactionSuccess(unittest.TestCase):
    _CREATED_RESULT = {
        "handle": "nested",
        "symbol_path": "Root/Nested B",
        "game_object_file_id": "1001",
        "transform_file_id": "1002",
        "source_asset_path": "Assets/Source.prefab",
        "source_asset_guid": "0123456789abcdef0123456789abcdef",
        "overrides": [
            {
                "component": "Example.Controller",
                "property_path": "m_Target",
            }
        ],
    }

    @staticmethod
    def _response(
        code: str,
        *,
        severity: Severity = Severity.INFO,
        data: dict[str, Any] | None = None,
        diagnostics: list[Diagnostic] | None = None,
    ) -> ToolResponse:
        return ToolResponse(
            success=severity not in (Severity.ERROR, Severity.CRITICAL),
            severity=severity,
            code=code,
            message=code,
            data={} if data is None else data,
            diagnostics=[] if diagnostics is None else diagnostics,
        )

    def test_successful_transaction_persists_authoritative_created_result_report(
        self,
    ) -> None:
        with TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            assets = project_root / "Assets"
            assets.mkdir()
            target = assets / "Target.prefab"
            target.write_bytes(b"before")
            report = project_root / "transaction.json"
            orch = Phase1Orchestrator.default(project_root)

            warning = Diagnostic(
                path="Assets/Target.prefab",
                location="114:7",
                detail="existing_warning",
                evidence="unchanged",
                severity="warning",
            )
            baseline = self._response(
                "BASELINE",
                severity=Severity.WARNING,
                diagnostics=[warning],
            )
            apply_response = self._response(
                "SER_APPLY_OK",
                data={"applied": 1, "created_results": [self._CREATED_RESULT]},
            )

            def apply_once(*, resource: dict[str, Any], ops: list[dict[str, Any]]):
                self.assertEqual("Assets/Target.prefab", resource["path"])
                self.assertEqual("rename_object", ops[0]["op"])
                target.write_bytes(b"after")
                return apply_response

            plan: dict[str, Any] = {
                "plan_version": 2,
                "resources": [
                    {
                        "id": "target",
                        "kind": "prefab",
                        "path": "Assets/Target.prefab",
                        "mode": "open",
                    }
                ],
                "ops": [
                    {
                        "resource": "target",
                        "op": "rename_object",
                        "target": "$root",
                        "name": "Nested B",
                    }
                ],
            }
            with (
                patch.object(
                    orch.serialized_object,
                    "dry_run_resource_plan",
                    return_value=self._response("DRY_RUN"),
                ) as dry_run_mock,
                patch.object(
                    orch.serialized_object,
                    "apply_resource_plan",
                    side_effect=apply_once,
                ) as apply_mock,
                patch.object(
                    orch.prefab_variant,
                    "list_overrides",
                    return_value=self._response("OVERRIDES"),
                ) as overrides_mock,
                patch.object(
                    Phase1Orchestrator,
                    "inspect_structure",
                    side_effect=[baseline, baseline],
                ) as structure_mock,
                patch.object(
                    Phase1Orchestrator,
                    "validate_refs",
                    side_effect=[baseline, baseline],
                ) as refs_mock,
            ):
                result = orch.patch_apply(
                    plan=plan,
                    confirm=True,
                    change_reason="Compose nested prefab",
                    out_report=str(report),
                    transactional=True,
                )

            resource = plan["resources"][0]
            ops = [{key: value for key, value in plan["ops"][0].items() if key != "resource"}]
            self.assertEqual(
                [call(resource=resource, ops=ops), call(resource=resource, ops=ops)],
                dry_run_mock.call_args_list,
            )
            overrides_mock.assert_called_once_with("Assets/Target.prefab")
            self.assertEqual(
                [call("Assets/Target.prefab"), call("Assets/Target.prefab")],
                structure_mock.call_args_list,
            )
            self.assertEqual(
                [
                    call("Assets/Target.prefab", details=True, max_diagnostics=200),
                    call("Assets/Target.prefab", details=True, max_diagnostics=200),
                ],
                refs_mock.call_args_list,
            )
            transaction = result.data.get("transaction", {})
            self.assertEqual(
                ("Compose nested prefab", "transaction.json"),
                (
                    transaction.get("change_reason"),
                    transaction.get("out_report"),
                ),
                msg=f"missing transaction audit context: {result.to_dict()!r}",
            )
            self.assertEqual(
                (True, "committed", True, b"after", 1),
                (
                    result.success,
                    transaction.get("status"),
                    transaction.get("report_written"),
                    target.read_bytes(),
                    apply_mock.call_count,
                ),
                msg=f"unexpected committed transaction: {result.to_dict()!r}",
            )
            self.assertEqual(
                [self._CREATED_RESULT],
                transaction.get("created_results"),
            )
            self.assertEqual(result.to_dict(), json.loads(report.read_text()))


class TestTransactionDiagnosticBaseline(unittest.TestCase):
    def test_stable_keys_partition_known_new_and_resolved_diagnostics(self) -> None:
        baseline = (
            DiagnosticKeyRecord(key="warning:keep"),
            DiagnosticKeyRecord(key="warning:old"),
            DiagnosticKeyRecord(key="reference:gone"),
        )
        current = (
            DiagnosticKeyRecord(key="warning:keep"),
            DiagnosticKeyRecord(key="warning:changed"),
            DiagnosticKeyRecord(key="structure:new"),
        )

        result = classify_transaction_diagnostic_keys(baseline, current)

        self.assertEqual(
            (
                ["warning:changed", "structure:new"],
                ["warning:keep"],
                ["warning:old", "reference:gone"],
            ),
            (
                [item["key"] for item in result["new"]],
                [item["key"] for item in result["known"]],
                [item["key"] for item in result["resolved"]],
            ),
            msg=f"unexpected diagnostic partitions: {result!r}",
        )


class TestPatchTransactionAuditPreflight(unittest.TestCase):
    def test_report_reservation_rejects_every_invalid_path_partition(self) -> None:
        with TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            existing = project_root / "existing.json"
            existing.write_text("occupied")
            parent_file = project_root / "not-a-directory"
            parent_file.write_text("file")
            outside = project_root.parent / f"{project_root.name}-outside.json"
            outside_missing = (
                project_root.parent
                / f"{project_root.name}-outside-missing"
                / "report.json"
            )
            cases = (
                (None, "OUT_REPORT_REQUIRED"),
                (str(outside), "OUT_REPORT_OUTSIDE_PROJECT"),
                (str(outside_missing), "OUT_REPORT_OUTSIDE_PROJECT"),
                ("../traversal.json", "OUT_REPORT_OUTSIDE_PROJECT"),
                ("missing/report.json", "OUT_REPORT_WRITE_FAILED"),
                (
                    str(parent_file / "report.json"),
                    "OUT_REPORT_WRITE_FAILED",
                ),
                (str(existing), "OUT_REPORT_WRITE_FAILED"),
                ("\x00", "OUT_REPORT_WRITE_FAILED"),
            )

            for out_report, expected_code in cases:
                with self.subTest(out_report=out_report):
                    result = reserve_transaction_report(project_root, out_report)
                    self.assertIsInstance(result, ToolResponse)
                    response = cast(ToolResponse, result)
                    self.assertEqual(
                        (False, expected_code, "out_report"),
                        (
                            response.success,
                            response.code,
                            response.diagnostics[0].location,
                        ),
                        msg=f"unexpected reservation failure: {response.to_dict()!r}",
                    )

    def test_embedded_null_target_returns_preimage_boundary_failure(self) -> None:
        with TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            (project_root / "Assets").mkdir()
            result = _contained_target_path(
                project_root,
                "Assets/embedded\x00target.prefab",
            )

        self.assertIsInstance(result, ToolResponse)
        response = cast(ToolResponse, result)
        self.assertEqual(
            (False, "PATCH_APPLY_RESULT", "preimage", "transaction_boundary_failure"),
            (
                response.success,
                response.code,
                response.data["boundary"],
                response.diagnostics[0].detail,
            ),
        )

    def test_embedded_null_target_persists_not_started_report(self) -> None:
        with TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            (project_root / "Assets").mkdir()
            report = project_root / "audit.json"
            plan = {
                "plan_version": 2,
                "resources": [
                    {
                        "id": "target",
                        "kind": "prefab",
                        "path": "Assets/embedded\x00target.prefab",
                        "mode": "open",
                    }
                ],
                "ops": [
                    {
                        "resource": "target",
                        "op": "rename_object",
                        "target": "$root",
                        "name": "Renamed",
                    }
                ],
            }
            orchestrator = Phase1Orchestrator.default(project_root)
            dry_run = ToolResponse(
                True,
                Severity.INFO,
                "DRY_RUN",
                "Dry-run complete.",
                {},
                [],
            )
            with (
                patch.object(
                    orchestrator.serialized_object,
                    "dry_run_resource_plan",
                    return_value=dry_run,
                ),
                patch.object(
                    orchestrator.serialized_object,
                    "apply_resource_plan",
                ) as apply_mock,
            ):
                result = orchestrator.patch_apply(
                    plan=plan,
                    confirm=True,
                    change_reason="Reject invalid target",
                    out_report=str(report),
                    transactional=True,
                )

            persisted = json.loads(report.read_text(encoding="utf-8"))

        self.assertEqual(
            (False, "PATCH_APPLY_RESULT", "not_started", True, 0, result.to_dict()),
            (
                result.success,
                result.code,
                result.data["transaction"]["status"],
                result.data["transaction"]["report_written"],
                apply_mock.call_count,
                persisted,
            ),
            msg=f"invalid target left a non-terminal audit reservation: {result.to_dict()!r}",
        )







    def test_transaction_boundary_errors_do_not_expose_exception_text(self) -> None:
        secret = "/private/transaction/report.json"
        response = boundary_failure("preimage", OSError(secret))
        serialized = json.dumps(response.to_dict())

        self.assertEqual(
            (
                "Patch transaction preimage failed.",
                {"boundary": "preimage"},
                "Transaction boundary failed.",
            ),
            (
                response.message,
                response.data,
                response.diagnostics[0].evidence,
            ),
            msg=f"boundary failure must expose only stable public fields: {response.to_dict()!r}",
        )
        self.assertNotIn(secret, serialized)


    def test_explicit_boundary_uncertainty_is_projected(self) -> None:
        response = boundary_failure(
            "apply",
            RuntimeError("private writer failure"),
            state_unknown=True,
        )

        self.assertEqual(
            (
                "PATCH_APPLY_RESULT",
                {"boundary": "apply", "state_unknown": True},
            ),
            (response.code, response.data),
            msg="explicit writer uncertainty must be part of the stable boundary failure",
        )

    def test_report_persistence_errors_are_sanitized_after_cleanup_failure(self) -> None:
        secret = "/private/transaction/report.json"
        response = ToolResponse(
            success=False,
            severity=Severity.ERROR,
            code="PATCH_APPLY_RESULT",
            message="failed",
            data={"transaction": {}},
            diagnostics=[],
        )
        original = json.loads(json.dumps(response.to_dict()))
        with (
            patch(
                "prefab_sentinel.patch_transaction_results.write_transaction_report",
                side_effect=OSError(secret),
            ),
            patch(
                "prefab_sentinel.patch_transaction_results.discard_transaction_report",
                side_effect=OSError(f"cleanup {secret}"),
            ),
        ):
            result = persist_terminal(Path(secret), response)

        report_result = result.data["transaction"]["report_result"]
        self.assertEqual(
            (False, "OUT_REPORT_WRITE_FAILED", "Transaction report persistence and cleanup failed."),
            (
                report_result["success"],
                report_result["code"],
                report_result["error"],
            ),
            msg=f"terminal persistence failure must use stable public text: {result.to_dict()!r}",
        )
        self.assertIsNot(response, result)
        self.assertEqual(original, response.to_dict())
        self.assertNotIn(secret, json.dumps(result.to_dict()))


class TestReportReservationProcessFailures(unittest.TestCase):
    def _assert_stable_failure(
        self,
        result: Path | ToolResponse,
        report_path: Path,
    ) -> None:
        self.assertIsInstance(
            result,
            ToolResponse,
            msg=f"reservation process failure must return ToolResponse, got {result!r}",
        )
        response = cast(ToolResponse, result)
        self.assertEqual(
            (
                "OUT_REPORT_WRITE_FAILED",
                "out_report could not be reserved.",
                False,
            ),
            (response.code, response.message, report_path.exists()),
            msg="reservation process failures must be stable and leave no report",
        )



    def test_worker_launch_failure_is_redacted_and_leaves_no_report(self) -> None:
        with TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            report_path = project_root / "report.json"
            with patch(
                "prefab_sentinel.patch_transaction_io._run_process",
                side_effect=OSError("secret launch path"),
            ):
                result = reserve_transaction_report(project_root, str(report_path))

            self._assert_stable_failure(result, report_path)

    def test_worker_timeout_is_reaped_before_cleanup_response(self) -> None:
        with TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            report_path = project_root / "report.json"
            timeout = subprocess.TimeoutExpired(
                cmd=["secret-worker"],
                timeout=10,
            )
            with patch(
                "prefab_sentinel.patch_transaction_io._run_process",
                side_effect=timeout,
            ):
                result = reserve_transaction_report(project_root, str(report_path))

            self._assert_stable_failure(result, report_path)

    def test_abnormal_worker_exit_is_redacted_and_leaves_no_report(self) -> None:
        with TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            report_path = project_root / "report.json"
            completed = subprocess.CompletedProcess(
                args=["secret-worker"],
                returncode=9,
                stdout='{"status":"create_failed"}',
                stderr="secret worker failure",
            )
            with patch(
                "prefab_sentinel.patch_transaction_io._run_process",
                return_value=completed,
            ):
                result = reserve_transaction_report(project_root, str(report_path))

            self._assert_stable_failure(result, report_path)

    def test_malformed_worker_status_is_redacted_and_leaves_no_report(self) -> None:
        with TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            report_path = project_root / "report.json"
            completed = subprocess.CompletedProcess(
                args=["secret-worker"],
                returncode=0,
                stdout='{"status":"reserved","unexpected":true}',
                stderr="",
            )
            with patch(
                "prefab_sentinel.patch_transaction_io._run_process",
                return_value=completed,
            ):
                result = reserve_transaction_report(project_root, str(report_path))

            self._assert_stable_failure(result, report_path)

    def test_existing_report_wins_exclusive_create_race_without_replacement(self) -> None:
        with TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            report_path = project_root / "report.json"
            original = b"existing report"
            report_path.write_bytes(original)

            result = reserve_transaction_report(project_root, str(report_path))

            self.assertIsInstance(
                result,
                ToolResponse,
                msg=f"existing report must fail reservation, got {result!r}",
            )
            response = cast(ToolResponse, result)
            self.assertEqual(
                ("OUT_REPORT_WRITE_FAILED", original, ()),
                (
                    response.code,
                    report_path.read_bytes(),
                    tuple(project_root.glob(".prefab-sentinel-reservation-*")),
                ),
                msg="exclusive reservation must preserve an existing report byte-for-byte",
            )

    def test_preexisting_owner_collision_is_preserved_without_report(self) -> None:
        with TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            report_path = project_root / "report.json"
            owner_path = project_root / ".foreign-reservation-owner"
            foreign_bytes = b"foreign owner"
            owner_path.write_bytes(foreign_bytes)
            with patch(
                "prefab_sentinel.patch_transaction_io._reservation_owner_path",
                return_value=owner_path,
            ):
                result = reserve_transaction_report(project_root, str(report_path))

            owner_bytes = owner_path.read_bytes() if owner_path.exists() else None
            self.assertIsInstance(
                result,
                ToolResponse,
                msg=f"owner collision must fail reservation, got {result!r}",
            )
            response = cast(ToolResponse, result)
            self.assertEqual(
                (
                    "OUT_REPORT_WRITE_FAILED",
                    "out_report could not be reserved.",
                    foreign_bytes,
                    False,
                ),
                (
                    response.code,
                    response.message,
                    owner_bytes,
                    report_path.exists(),
                ),
                msg="reservation cleanup must not delete a marker it never owned",
            )

    def test_child_exclusive_create_failure_returns_stable_report_error(self) -> None:
        with TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            report_path = project_root / "denied.json"
            completed = subprocess.CompletedProcess(
                args=["reservation-worker"],
                returncode=1,
                stdout='{"status":"owner_create_failed"}',
                stderr="permission details",
            )
            with patch(
                "prefab_sentinel.patch_transaction_io._run_process",
                return_value=completed,
            ):
                failure = reserve_transaction_report(project_root, str(report_path))

            self.assertIsInstance(
                failure,
                ToolResponse,
                msg=f"child create failure must return ToolResponse, got {failure!r}",
            )
            response = cast(ToolResponse, failure)
            self.assertEqual(
                ("OUT_REPORT_WRITE_FAILED", "out_report", False),
                (
                    response.code,
                    response.diagnostics[0].location,
                    report_path.exists(),
                ),
                msg="exclusive-create failure must remain redacted and pre-mutation",
            )

    def test_post_exit_cleanup_failure_retains_report_and_blocks_same_path(self) -> None:
        with TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            report_path = project_root / "report.json"
            owner_path = project_root / ".reservation-owner"
            with patch(
                "prefab_sentinel.patch_transaction_io._reservation_owner_path",
                return_value=owner_path,
            ):
                with patch(
                    "prefab_sentinel.patch_transaction_io._cleanup_temp_file",
                    return_value=OSError("cleanup failed"),
                ):
                    first_failure = reserve_transaction_report(
                        project_root,
                        str(report_path),
                    )

                second_failure = reserve_transaction_report(
                    project_root,
                    str(report_path),
                )
                third_failure = reserve_transaction_report(
                    project_root,
                    str(report_path),
                )

                self.assertIsInstance(first_failure, ToolResponse)
                self.assertIsInstance(second_failure, ToolResponse)
                self.assertIsInstance(third_failure, ToolResponse)
                first_response = cast(ToolResponse, first_failure)
                second_response = cast(ToolResponse, second_failure)
                third_response = cast(ToolResponse, third_failure)
                self.assertEqual(
                    (
                        "OUT_REPORT_WRITE_FAILED",
                        "out_report reservation cleanup failed.",
                        "OUT_REPORT_WRITE_FAILED",
                        "out_report could not be reserved.",
                        "OUT_REPORT_WRITE_FAILED",
                        "out_report could not be reserved.",
                        True,
                        b"",
                        True,
                    ),
                    (
                        first_response.code,
                        first_response.message,
                        second_response.code,
                        second_response.message,
                        third_response.code,
                        third_response.message,
                        report_path.exists(),
                        report_path.read_bytes(),
                        owner_path.exists(),
                    ),
                    msg=(
                        "failed post-exit cleanup must retain its owner/report pair "
                        "and block every same-path request"
                    ),
                )

                report_path.unlink()
                owner_path.unlink()
                reserved = reserve_transaction_report(
                    project_root,
                    str(report_path),
                )

            self.assertEqual(
                report_path,
                reserved,
                msg="same-path reservation may succeed only after external removal",
            )


class TestReportReservationProcessBoundary(unittest.TestCase):
    def test_exited_child_reserves_empty_report_for_atomic_finalization(self) -> None:
        with TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            report_path = project_root / "report.json"

            reservation = reserve_transaction_report(project_root, str(report_path))

            self.assertIsInstance(
                reservation,
                Path,
                msg=f"successful child reservation must return a Path, got {reservation!r}",
            )
            reserved_path = cast(Path, reservation)
            reserved_bytes = report_path.read_bytes()
            probe_path = project_root / "parent-probe"
            probe_descriptor = os.open(
                probe_path,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                0o600,
            )
            try:
                probe_size = os.fstat(probe_descriptor).st_size
            finally:
                os.close(probe_descriptor)
            final_response = ToolResponse(
                success=True,
                severity=Severity.INFO,
                code="FINAL_REPORT",
                message="final report",
                data={"transaction_state": "committed"},
                diagnostics=[],
            )
            write_transaction_report(reserved_path, final_response)
            payload = json.loads(report_path.read_text(encoding="utf-8"))
            owner_paths = tuple(
                project_root.glob(".prefab-sentinel-reservation-*"),
            )

            self.assertEqual(
                (b"", 0, "FINAL_REPORT", "committed", ()),
                (
                    reserved_bytes,
                    probe_size,
                    payload["code"],
                    payload["data"]["transaction_state"],
                    owner_paths,
                ),
                msg=(
                    "the exited child must leave an empty exclusive reservation, "
                    "no owner marker, and a replaceable final report"
                ),
            )


class TestReportReservationDescriptorOwnership(unittest.TestCase):
    def test_child_close_anomaly_cannot_reach_parent_descriptor_table(self) -> None:
        with TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            hook_root = project_root / "child-hook"
            hook_root.mkdir()
            (hook_root / "sitecustomize.py").write_text(
                "import errno\n"
                "import os\n"
                "_real_close = os.close\n"
                "def close_then_fail(descriptor):\n"
                "    _real_close(descriptor)\n"
                "    raise OSError(errno.EIO, 'injected child close failure')\n"
                "if os.environ.get('PREFAB_SENTINEL_RESERVATION_CLOSE_FAULT') == '1':\n"
                "    os.close = close_then_fail\n",
                encoding="utf-8",
            )
            report_path = project_root / "report.json"
            with patch.dict(
                os.environ,
                {
                    "PYTHONPATH": str(hook_root),
                    "PREFAB_SENTINEL_RESERVATION_CLOSE_FAULT": "1",
                },
                clear=False,
            ):
                failure = reserve_transaction_report(project_root, str(report_path))

            probe_path = project_root / "parent-probe"
            probe_descriptor = os.open(
                probe_path,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                0o600,
            )
            try:
                probe_stat = os.fstat(probe_descriptor)
            finally:
                os.close(probe_descriptor)

            self.assertIsInstance(
                failure,
                ToolResponse,
                msg=f"child close anomaly must fail reservation, observed {failure!r}",
            )
            failure_response = cast(ToolResponse, failure)
            self.assertEqual(
                ("OUT_REPORT_WRITE_FAILED", False, 0),
                (
                    failure_response.code,
                    report_path.exists(),
                    probe_stat.st_size,
                ),
                msg=(
                    "the reaped child must leave no reservation and cannot "
                    "invalidate an unrelated parent descriptor"
                ),
            )


class TestPatchTransactionValidationPreflight(_TransactionTestCase):
    def test_existing_error_stops_before_apply_with_not_started_report(self) -> None:
        with TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            assets = project_root / "Assets"
            assets.mkdir()
            target = assets / "Target.prefab"
            target.write_bytes(b"before")
            report = project_root / "preflight.json"
            orch = Phase1Orchestrator.default(project_root)
            preflight_error = self._response(
                "VALIDATE_STRUCTURE_RESULT",
                severity=Severity.ERROR,
                diagnostics=[
                    Diagnostic(
                        path="Assets/Target.prefab",
                        location="114:7",
                        detail="duplicate_file_id",
                        evidence="duplicate 1001",
                        severity="error",
                    )
                ],
            )

            result, apply_count = self._execute(
                orch,
                target,
                report,
                apply_result=self._response("SER_APPLY_OK"),
                structure_results=[preflight_error],
                reference_results=[self._response("VALIDATE_REFS_RESULT")],
            )

            transaction = result.data["transaction"]
            self.assertEqual(
                (False, "PATCH_APPLY_RESULT", "not_started", 0, b"before"),
                (
                    result.success,
                    result.code,
                    transaction["status"],
                    apply_count,
                    target.read_bytes(),
                ),
                msg=f"unexpected preflight result: {result.to_dict()!r}",
            )
            self.assertEqual(
                "VALIDATE_STRUCTURE_RESULT",
                transaction["original_result"]["code"],
            )
            self.assertEqual(result.to_dict(), json.loads(report.read_text()))


class TestSinglePrefabTransactionRollback(_TransactionTestCase):
    def test_projection_failure_restores_exact_preimage_and_finalizes_report(
        self,
    ) -> None:
        with TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            assets = project_root / "Assets"
            assets.mkdir()
            target = assets / "Target.prefab"
            target.write_bytes(b"before")
            report = project_root / "projection-failure.json"
            outside_target = (
                project_root.parent / f"{project_root.name}-outside" / "Target.prefab"
            ).resolve()
            orch = Phase1Orchestrator.default(project_root)

            result, apply_count = self._execute(
                orch,
                target,
                report,
                apply_result=self._response(
                    "SER_APPLY_OK",
                    data={"applied": 1, "target": str(outside_target)},
                ),
                structure_results=[
                    self._response("STRUCTURE_BEFORE"),
                    self._response("STRUCTURE_AFTER"),
                ],
                reference_results=[
                    self._response("REFS_BEFORE"),
                    self._response("REFS_AFTER"),
                ],
            )

            transaction = result.data["transaction"]
            self.assertEqual(
                (
                    False,
                    "error",
                    "PATCH_APPLY_RESULT",
                    "rolled_back",
                    "projection",
                    True,
                    True,
                    "Assets/Target.prefab",
                    b"before",
                    1,
                ),
                (
                    result.success,
                    result.severity.value,
                    result.code,
                    transaction["status"],
                    transaction["original_result"]["data"]["boundary"],
                    transaction["report_written"],
                    transaction["rollback_result"]["success"],
                    transaction["rollback_result"]["data"]["target"],
                    target.read_bytes(),
                    apply_count,
                ),
                msg=f"projection failure did not produce an exact rollback: {result.to_dict()!r}",
            )
            self.assertEqual(result.to_dict(), json.loads(report.read_text()))


    def test_nested_projection_failure_during_rollback_finalizes_report(self) -> None:
        with TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            assets = project_root / "Assets"
            assets.mkdir()
            target = assets / "Target.prefab"
            target.write_bytes(b"before")
            report = project_root / "nested-projection-failure.json"
            outside_target = (
                project_root.parent / f"{project_root.name}-outside" / "Target.prefab"
            ).resolve()
            orch = Phase1Orchestrator.default(project_root)
            introduced = Diagnostic(
                path="Assets/Target.prefab",
                location="4:2",
                detail="orphaned_transform",
                evidence="Transform 1002 has no GameObject",
                severity="warning",
            )

            result, apply_count = self._execute(
                orch,
                target,
                report,
                apply_result=self._response(
                    "SER_APPLY_OK",
                    data={
                        "applied": 1,
                        "steps": [
                            {
                                "result": {
                                    "data": {"target": str(outside_target)}
                                }
                            }
                        ],
                    },
                ),
                structure_results=[
                    self._response("STRUCTURE_BEFORE"),
                    self._response(
                        "STRUCTURE_AFTER",
                        severity=Severity.WARNING,
                        diagnostics=[introduced],
                    ),
                ],
                reference_results=[
                    self._response("REFS_BEFORE"),
                    self._response("REFS_AFTER"),
                ],
            )

            transaction = result.data["transaction"]
            self.assertEqual(
                (
                    False,
                    "error",
                    "PATCH_APPLY_RESULT",
                    "rolled_back",
                    "projection",
                    True,
                    True,
                    "Assets/Target.prefab",
                    b"before",
                    1,
                ),
                (
                    result.success,
                    result.severity.value,
                    result.code,
                    transaction["status"],
                    transaction["original_result"]["data"]["boundary"],
                    transaction["report_written"],
                    transaction["rollback_result"]["success"],
                    transaction["rollback_result"]["data"]["target"],
                    target.read_bytes(),
                    apply_count,
                ),
                msg=f"nested projection failure lost its rollback result: {result.to_dict()!r}",
            )
            self.assertEqual(result.to_dict(), json.loads(report.read_text()))

    def test_introduced_diagnostic_restores_exact_preimage_and_report(self) -> None:
        with TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            assets = project_root / "Assets"
            assets.mkdir()
            target = assets / "Target.prefab"
            target.write_bytes(b"before")
            report = project_root / "introduced.json"
            orch = Phase1Orchestrator.default(project_root)
            introduced = Diagnostic(
                path="Assets/Target.prefab",
                location="4:2",
                detail="orphaned_transform",
                evidence="Transform 1002 has no GameObject",
                severity="warning",
            )

            result, apply_count = self._execute(
                orch,
                target,
                report,
                apply_result=self._response("SER_APPLY_OK", data={"applied": 1}),
                structure_results=[
                    self._response("STRUCTURE_BEFORE"),
                    self._response(
                        "STRUCTURE_AFTER",
                        severity=Severity.WARNING,
                        diagnostics=[introduced],
                    ),
                ],
                reference_results=[
                    self._response("REFS_BEFORE"),
                    self._response("REFS_AFTER"),
                ],
            )

            transaction = result.data["transaction"]
            self.assertEqual(
                (
                    False,
                    "error",
                    "PATCH_APPLY_RESULT",
                    "patch.apply validation failed; transaction rolled back.",
                    "rolled_back",
                    True,
                    "introduced.json",
                    "Assets/Target.prefab",
                    b"before",
                    1,
                ),
                (
                    result.success,
                    result.severity.value,
                    result.code,
                    result.message,
                    transaction["status"],
                    transaction["rollback_result"]["success"],
                    transaction["out_report"],
                    transaction["rollback_result"]["data"]["target"],
                    target.read_bytes(),
                    apply_count,
                ),
                msg=f"unexpected introduced-diagnostic rollback: {result.to_dict()!r}",
            )
            self.assertEqual(1, transaction["diagnostics_baseline"]["new_count"])
            self.assertEqual(result.to_dict(), json.loads(report.read_text()))


    def test_introduced_duplicate_file_id_restores_exact_preimage_and_partitions(self) -> None:
        with TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            assets = project_root / "Assets"
            assets.mkdir()
            target = assets / "Target.prefab"
            target.write_bytes(b"before")
            report = project_root / "introduced-duplicate.json"
            orch = Phase1Orchestrator.default(project_root)
            known = Diagnostic(
                path="Assets/Target.prefab",
                location="8:1",
                detail="known_warning",
                evidence="Known structure warning.",
                severity="warning",
            )
            resolved = Diagnostic(
                path="Assets/Target.prefab",
                location="9:1",
                detail="resolved_warning",
                evidence="Resolved structure warning.",
                severity="warning",
            )
            introduced = Diagnostic(
                path="Assets/Target.prefab",
                location="12:1",
                detail="duplicate_file_id",
                evidence="fileID 1001 is duplicated",
                severity="warning",
            )

            result, apply_count = self._execute(
                orch,
                target,
                report,
                apply_result=self._response("SER_APPLY_OK", data={"applied": 1}),
                structure_results=[
                    self._response(
                        "STRUCTURE_BEFORE",
                        severity=Severity.WARNING,
                        diagnostics=[known, resolved],
                    ),
                    self._response(
                        "STRUCTURE_AFTER",
                        severity=Severity.WARNING,
                        diagnostics=[known, introduced],
                    ),
                ],
                reference_results=[
                    self._response("REFS_BEFORE"),
                    self._response("REFS_AFTER"),
                ],
            )

            transaction = result.data["transaction"]
            self.assertEqual(
                ("rolled_back", True, b"before", 1),
                (
                    transaction["status"],
                    transaction["rollback_result"]["success"],
                    target.read_bytes(),
                    apply_count,
                ),
                msg=f"unexpected duplicate-fileID rollback: {result.to_dict()!r}",
            )
            self.assertEqual(
                {
                    "status": "transaction",
                    "path": None,
                    "new_count": 1,
                    "known_count": 1,
                    "resolved_count": 1,
                    "new": [
                        {
                            "key": (
                                "structure:duplicate_file_id:Assets/Target.prefab:"
                                "12:1:fileID 1001 is duplicated"
                            ),
                            "severity": "warning",
                            "message": "fileID 1001 is duplicated",
                            "data": {"code": "duplicate_file_id"},
                        }
                    ],
                    "known": [
                        {
                            "key": (
                                "structure:known_warning:Assets/Target.prefab:"
                                "8:1:Known structure warning."
                            ),
                            "severity": "warning",
                            "message": "Known structure warning.",
                            "data": {"code": "known_warning"},
                        }
                    ],
                    "resolved": [
                        {
                            "key": (
                                "structure:resolved_warning:Assets/Target.prefab:"
                                "9:1:Resolved structure warning."
                            ),
                            "severity": "warning",
                            "message": "",
                            "data": {},
                        }
                    ],
                },
                transaction["diagnostics_baseline"],
                msg=f"duplicate-fileID diagnostic partitions drifted: {result.to_dict()!r}",
            )
            self.assertEqual(result.to_dict(), json.loads(report.read_text()))

    def test_introduced_broken_reference_restores_exact_preimage_and_partitions(self) -> None:
        with TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            assets = project_root / "Assets"
            assets.mkdir()
            target = assets / "Target.prefab"
            target.write_bytes(b"before")
            report = project_root / "introduced-broken-reference.json"
            orch = Phase1Orchestrator.default(project_root)
            known = Diagnostic(
                path="Assets/Target.prefab",
                location="MonoBehaviour.m_Known",
                detail="known_reference_warning",
                evidence="Known reference warning.",
                severity="warning",
            )
            resolved = Diagnostic(
                path="Assets/Target.prefab",
                location="MonoBehaviour.m_Resolved",
                detail="resolved_reference_warning",
                evidence="Resolved reference warning.",
                severity="warning",
            )
            introduced = Diagnostic(
                path="Assets/Target.prefab",
                location="MonoBehaviour.m_Target",
                detail="broken_reference",
                evidence="GUID 0123456789abcdef0123456789abcdef does not resolve.",
                severity="warning",
            )

            result, apply_count = self._execute(
                orch,
                target,
                report,
                apply_result=self._response("SER_APPLY_OK", data={"applied": 1}),
                structure_results=[
                    self._response("STRUCTURE_BEFORE"),
                    self._response("STRUCTURE_AFTER"),
                ],
                reference_results=[
                    self._response(
                        "REFS_BEFORE",
                        severity=Severity.WARNING,
                        diagnostics=[known, resolved],
                    ),
                    self._response(
                        "REFS_AFTER",
                        severity=Severity.WARNING,
                        diagnostics=[known, introduced],
                    ),
                ],
            )

            transaction = result.data["transaction"]
            self.assertEqual(
                ("rolled_back", True, b"before", 1),
                (
                    transaction["status"],
                    transaction["rollback_result"]["success"],
                    target.read_bytes(),
                    apply_count,
                ),
                msg=f"unexpected broken-reference rollback: {result.to_dict()!r}",
            )
            self.assertEqual(
                {
                    "status": "transaction",
                    "path": None,
                    "new_count": 1,
                    "known_count": 1,
                    "resolved_count": 1,
                    "new": [
                        {
                            "key": (
                                "reference:broken_reference:Assets/Target.prefab:"
                                "MonoBehaviour.m_Target:"
                                "GUID 0123456789abcdef0123456789abcdef does not resolve."
                            ),
                            "severity": "warning",
                            "message": "GUID 0123456789abcdef0123456789abcdef does not resolve.",
                            "data": {"code": "broken_reference"},
                        }
                    ],
                    "known": [
                        {
                            "key": (
                                "reference:known_reference_warning:Assets/Target.prefab:"
                                "MonoBehaviour.m_Known:Known reference warning."
                            ),
                            "severity": "warning",
                            "message": "Known reference warning.",
                            "data": {"code": "known_reference_warning"},
                        }
                    ],
                    "resolved": [
                        {
                            "key": (
                                "reference:resolved_reference_warning:Assets/Target.prefab:"
                                "MonoBehaviour.m_Resolved:Resolved reference warning."
                            ),
                            "severity": "warning",
                            "message": "",
                            "data": {},
                        }
                    ],
                },
                transaction["diagnostics_baseline"],
                msg=f"broken-reference diagnostic partitions drifted: {result.to_dict()!r}",
            )
            self.assertEqual(result.to_dict(), json.loads(report.read_text()))

    def test_apply_failure_restores_exact_preimage_and_preserves_failure(self) -> None:
        with TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            assets = project_root / "Assets"
            assets.mkdir()
            target = assets / "Target.prefab"
            target.write_bytes(b"before")
            report = project_root / "apply-failure.json"
            orch = Phase1Orchestrator.default(project_root)

            result, apply_count = self._execute(
                orch,
                target,
                report,
                apply_result=self._response(
                    "SER_APPLY_REJECTED",
                    severity=Severity.ERROR,
                    data={"applied": 0, "read_only": False, "executed": False},
                ),
                structure_results=[self._response("STRUCTURE_BEFORE")],
                reference_results=[self._response("REFS_BEFORE")],
            )

            transaction = result.data["transaction"]
            self.assertEqual(
                ("rolled_back", "PATCH_APPLY_RESULT", True, b"before", 1),
                (
                    transaction["status"],
                    transaction["original_result"]["code"],
                    transaction["rollback_result"]["success"],
                    target.read_bytes(),
                    apply_count,
                ),
                msg=f"unexpected apply rollback: {result.to_dict()!r}",
            )
            self.assertEqual(result.to_dict(), json.loads(report.read_text()))


    def test_udonsharp_proxy_sync_failure_restores_exact_preimage(self) -> None:
        with TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            assets = project_root / "Assets"
            assets.mkdir()
            target = assets / "Target.prefab"
            target.write_bytes(b"before")
            report = project_root / "udonsharp-sync-failure.json"
            orch = Phase1Orchestrator.default(project_root)
            sync_diagnostic = Diagnostic(
                path="Assets/Target.prefab",
                location="ops[3].path",
                detail="udonsharp_sync_error",
                evidence="CopyProxyToUdon failed.",
                severity="error",
            )

            result, apply_count = self._execute(
                orch,
                target,
                report,
                apply_result=self._response(
                    "SER_APPLY_REJECTED",
                    severity=Severity.ERROR,
                    data={"applied": 3, "read_only": False, "executed": True},
                    diagnostics=[sync_diagnostic],
                ),
                structure_results=[self._response("STRUCTURE_BEFORE")],
                reference_results=[self._response("REFS_BEFORE")],
            )

            transaction = result.data["transaction"]
            bridge_result = transaction["original_result"]["data"]["steps"][-1]["result"]
            sync_wire = bridge_result["diagnostics"][0]
            self.assertEqual(
                (
                    "rolled_back",
                    True,
                    b"before",
                    1,
                    "SER_APPLY_REJECTED",
                    "udonsharp_sync_error",
                    "ops[3].path",
                ),
                (
                    transaction["status"],
                    transaction["rollback_result"]["success"],
                    target.read_bytes(),
                    apply_count,
                    bridge_result["code"],
                    sync_wire["code"],
                    sync_wire["data"]["location"],
                ),
                msg=f"unexpected UdonSharp sync rollback: {result.to_dict()!r}",
            )
            self.assertEqual(result.to_dict(), json.loads(report.read_text()))


    def test_prefab_save_failure_restores_exact_preimage_and_preserves_bridge_code(self) -> None:
        with TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            assets = project_root / "Assets"
            assets.mkdir()
            target = assets / "Target.prefab"
            target.write_bytes(b"before")
            report = project_root / "prefab-save-failure.json"
            orch = Phase1Orchestrator.default(project_root)

            result, apply_count = self._execute(
                orch,
                target,
                report,
                apply_result=self._response(
                    "UNITY_BRIDGE_PREFAB_SAVE",
                    severity=Severity.ERROR,
                    data={
                        "applied": 0,
                        "read_only": False,
                        "executed": False,
                        "boundary": "save",
                    },
                ),
                structure_results=[self._response("STRUCTURE_BEFORE")],
                reference_results=[self._response("REFS_BEFORE")],
            )

            transaction = result.data["transaction"]
            apply_step = transaction["original_result"]["data"]["steps"][2]["result"]
            self.assertEqual(
                (
                    False,
                    "error",
                    "PATCH_APPLY_RESULT",
                    "rolled_back",
                    True,
                    "UNITY_BRIDGE_PREFAB_SAVE",
                    "save",
                    b"before",
                    1,
                ),
                (
                    result.success,
                    result.severity.value,
                    result.code,
                    transaction["status"],
                    transaction["rollback_result"]["success"],
                    apply_step["code"],
                    apply_step["data"]["boundary"],
                    target.read_bytes(),
                    apply_count,
                ),
                msg=f"unexpected Prefab save-failure rollback: {result.to_dict()!r}",
            )
            self.assertEqual(result.to_dict(), json.loads(report.read_text()))

    def test_runtime_validation_failure_restores_exact_preimage(self) -> None:
        with TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            assets = project_root / "Assets"
            assets.mkdir()
            target = assets / "Target.prefab"
            target.write_bytes(b"before")
            report = project_root / "runtime-failure.json"
            orch = Phase1Orchestrator.default(project_root)

            result, apply_count = self._execute(
                orch,
                target,
                report,
                apply_result=self._response("SER_APPLY_OK", data={"applied": 1}),
                structure_results=[self._response("STRUCTURE_BEFORE")],
                reference_results=[self._response("REFS_BEFORE")],
                runtime_scene="Assets/Runtime.unity",
                runtime_compile_result=self._response(
                    "RUN_COMPILE_FAILED",
                    severity=Severity.ERROR,
                ),
            )

            transaction = result.data["transaction"]
            self.assertEqual(
                ("rolled_back", True, b"before", 1, "RUN_COMPILE_FAILED"),
                (
                    transaction["status"],
                    transaction["rollback_result"]["success"],
                    target.read_bytes(),
                    apply_count,
                    transaction["original_result"]["data"]["steps"][-1]["result"]["code"],
                ),
                msg=f"runtime failure did not roll back the prefab: {result.to_dict()!r}",
            )
            self.assertEqual(result.to_dict(), json.loads(report.read_text()))

    def test_postcondition_failure_restores_exact_preimage(self) -> None:
        with TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            assets = project_root / "Assets"
            assets.mkdir()
            target = assets / "Target.prefab"
            target.write_bytes(b"before")
            report = project_root / "postcondition-failure.json"
            orch = Phase1Orchestrator.default(project_root)
            plan = self._plan()
            plan["postconditions"] = [
                {"type": "asset_exists", "resource": "target"}
            ]

            result, apply_count = self._execute(
                orch,
                target,
                report,
                apply_result=self._response("SER_APPLY_OK", data={"applied": 1}),
                structure_results=[self._response("STRUCTURE_BEFORE")],
                reference_results=[self._response("REFS_BEFORE")],
                plan=plan,
                postcondition_result=self._response(
                    "POSTCONDITION_FAILED",
                    severity=Severity.ERROR,
                ),
            )

            transaction = result.data["transaction"]
            self.assertEqual(
                ("rolled_back", True, b"before", 1, "POSTCONDITION_FAILED"),
                (
                    transaction["status"],
                    transaction["rollback_result"]["success"],
                    target.read_bytes(),
                    apply_count,
                    transaction["original_result"]["data"]["steps"][-1]["result"]["code"],
                ),
                msg=f"postcondition failure did not roll back the prefab: {result.to_dict()!r}",
            )
            self.assertEqual(result.to_dict(), json.loads(report.read_text()))

    def test_failed_direct_apply_with_read_only_flag_rolls_back(self) -> None:
        from unittest.mock import MagicMock

        from prefab_sentinel.patch_transaction import (
            execute_single_open_prefab_transaction,
        )

        with TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            assets = project_root / "Assets"
            assets.mkdir()
            target = assets / "Target.prefab"
            target.write_bytes(b"before")
            report = project_root / "read-only-apply-failure.json"
            orch = MagicMock()
            orch.prefab_variant.project_root = project_root
            orch.inspect_structure.return_value = self._response("STRUCTURE_BEFORE")
            orch.validate_refs.return_value = self._response("REFS_BEFORE")
            orch.maybe_auto_refresh.return_value = "true"

            def apply_once() -> ToolResponse:
                target.write_bytes(b"after")
                return self._response(
                    "SER_APPLY_REJECTED",
                    severity=Severity.ERROR,
                    data={"applied": 0, "read_only": True, "executed": False},
                )

            apply_mock = MagicMock(side_effect=apply_once)
            result = execute_single_open_prefab_transaction(
                orch,
                target="Assets/Target.prefab",
                out_report=str(report),
                change_reason="Exercise direct failed apply rollback",
                max_diagnostics=200,
                apply=apply_mock,
            )

            transaction = result.data["transaction"]
            self.assertEqual(
                ("rolled_back", True, "true", b"before"),
                (
                    transaction["status"],
                    transaction["rollback_result"]["success"],
                    transaction["rollback_result"]["data"]["auto_refresh"],
                    target.read_bytes(),
                ),
                msg=f"unexpected direct apply rollback: {result.to_dict()!r}",
            )
            self.assertEqual(result.to_dict(), json.loads(report.read_text()))
            orch.inspect_structure.assert_called_once_with("Assets/Target.prefab")
            orch.validate_refs.assert_called_once_with(
                "Assets/Target.prefab",
                details=True,
                max_diagnostics=200,
            )
            orch.maybe_auto_refresh.assert_called_once_with()
            apply_mock.assert_called_once_with()

    def test_commit_report_failure_rolls_back_then_persists_terminal_report(
        self,
    ) -> None:
        with TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            assets = project_root / "Assets"
            assets.mkdir()
            target = assets / "Target.prefab"
            target.write_bytes(b"before")
            report = project_root / "report-failure.json"
            orch = Phase1Orchestrator.default(project_root)
            attempts = 0

            def fail_then_write(path: Path, response: ToolResponse) -> None:
                nonlocal attempts
                attempts += 1
                if attempts == 1:
                    raise OSError("commit report unavailable")
                write_transaction_report(path, response)

            with (
                patch(
                    "prefab_sentinel.patch_transaction.write_transaction_report",
                    side_effect=fail_then_write,
                ),
                patch(
                    "prefab_sentinel.patch_transaction_results.write_transaction_report",
                    side_effect=fail_then_write,
                ),
            ):
                result, apply_count = self._execute(
                    orch,
                    target,
                    report,
                    apply_result=self._response("SER_APPLY_OK", data={"applied": 1}),
                    structure_results=[
                        self._response("STRUCTURE_BEFORE"),
                        self._response("STRUCTURE_AFTER"),
                    ],
                    reference_results=[
                        self._response("REFS_BEFORE"),
                        self._response("REFS_AFTER"),
                    ],
                )

            transaction = result.data["transaction"]
            self.assertEqual(
                (
                    "rolled_back",
                    "report",
                    True,
                    True,
                    b"before",
                    1,
                    2,
                ),
                (
                    transaction["status"],
                    transaction["original_result"]["data"]["boundary"],
                    transaction["rollback_result"]["success"],
                    transaction["report_written"],
                    target.read_bytes(),
                    apply_count,
                    attempts,
                ),
                msg=f"unexpected report-gate rollback: {result.to_dict()!r}",
            )
            self.assertEqual(result.to_dict(), json.loads(report.read_text()))

    def test_persistent_report_failure_returns_authoritative_response_only(
        self,
    ) -> None:
        with TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            assets = project_root / "Assets"
            assets.mkdir()
            target = assets / "Target.prefab"
            target.write_bytes(b"before")
            report = project_root / "unavailable.json"
            orch = Phase1Orchestrator.default(project_root)

            with (
                patch(
                    "prefab_sentinel.patch_transaction.write_transaction_report",
                    side_effect=OSError("persistent report failure"),
                ) as commit_writer,
                patch(
                    "prefab_sentinel.patch_transaction_results.write_transaction_report",
                    side_effect=OSError("persistent report failure"),
                ) as terminal_writer,
            ):
                result, apply_count = self._execute(
                    orch,
                    target,
                    report,
                    apply_result=self._response("SER_APPLY_OK", data={"applied": 1}),
                    structure_results=[
                        self._response("STRUCTURE_BEFORE"),
                        self._response("STRUCTURE_AFTER"),
                    ],
                    reference_results=[
                        self._response("REFS_BEFORE"),
                        self._response("REFS_AFTER"),
                    ],
                )

            transaction = result.data["transaction"]
            self.assertEqual(
                (
                    "rolled_back",
                    False,
                    "OUT_REPORT_WRITE_FAILED",
                    False,
                    b"before",
                    1,
                    2,
                ),
                (
                    transaction["status"],
                    transaction["report_written"],
                    transaction["report_result"]["code"],
                    report.exists(),
                    target.read_bytes(),
                    apply_count,
                    commit_writer.call_count + terminal_writer.call_count,
                ),
                msg=f"unexpected persistent report failure: {result.to_dict()!r}",
            )


class TestSinglePrefabRollbackFailure(_TransactionTestCase):
    def test_restoration_failure_is_critical_and_preserves_both_failures(
        self,
    ) -> None:
        with TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            assets = project_root / "Assets"
            assets.mkdir()
            target = assets / "Target.prefab"
            target.write_bytes(b"before")
            report = project_root / "rollback-failure.json"
            orch = Phase1Orchestrator.default(project_root)

            with patch(
                "prefab_sentinel.patch_transaction_results.restore_transaction_preimage",
                side_effect=OSError("restore denied"),
            ):
                result, apply_count = self._execute(
                    orch,
                    target,
                    report,
                    apply_result=self._response(
                        "SER_APPLY_REJECTED",
                        severity=Severity.ERROR,
                        data={"applied": 0, "read_only": False, "executed": False},
                    ),
                    structure_results=[self._response("STRUCTURE_BEFORE")],
                    reference_results=[self._response("REFS_BEFORE")],
                )

            transaction = result.data["transaction"]
            self.assertEqual(
                (
                    False,
                    "critical",
                    "PATCH_ROLLBACK_FAILED",
                    "patch.apply validation failed and automatic rollback failed.",
                    "rollback_failed",
                    "PATCH_APPLY_RESULT",
                    False,
                    b"after",
                    1,
                ),
                (
                    result.success,
                    result.severity.value,
                    result.code,
                    result.message,
                    transaction["status"],
                    transaction["original_result"]["code"],
                    transaction["rollback_result"]["success"],
                    target.read_bytes(),
                    apply_count,
                ),
                msg=f"unexpected rollback failure: {result.to_dict()!r}",
            )
            self.assertEqual(
                "SER_APPLY_REJECTED",
                transaction["original_result"]["data"]["steps"][-1]["result"]["code"],
            )
            self.assertEqual(
                {
                    "success": False,
                    "severity": "error",
                    "code": "PATCH_APPLY_RESULT",
                    "message": "Patch transaction rollback failed.",
                    "data": {"boundary": "rollback"},
                    "diagnostics": [
                        {
                            "severity": "error",
                            "code": "transaction_boundary_failure",
                            "message": "Transaction boundary failed.",
                            "data": {"location": "rollback"},
                        }
                    ],
                },
                transaction["rollback_result"],
                msg=f"rollback restoration cause drifted: {result.to_dict()!r}",
            )
            self.assertEqual(result.to_dict(), json.loads(report.read_text()))

    def test_refresh_failure_is_critical_after_exact_disk_restore(self) -> None:
        with TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            assets = project_root / "Assets"
            assets.mkdir()
            target = assets / "Target.prefab"
            target.write_bytes(b"before")
            report = project_root / "rollback-refresh-failure.json"
            orch = Phase1Orchestrator.default(project_root)

            result, apply_count = self._execute(
                orch,
                target,
                report,
                apply_result=self._response(
                    "SER_APPLY_REJECTED",
                    severity=Severity.ERROR,
                    data={"applied": 0, "read_only": False, "executed": False},
                ),
                structure_results=[self._response("STRUCTURE_BEFORE")],
                reference_results=[self._response("REFS_BEFORE")],
                refresh_status="false",
            )

            transaction = result.data["transaction"]
            self.assertEqual(
                (
                    False,
                    "critical",
                    "PATCH_ROLLBACK_FAILED",
                    "rollback_failed",
                    False,
                    "rollback_sync",
                    b"before",
                    1,
                ),
                (
                    result.success,
                    result.severity.value,
                    result.code,
                    transaction["status"],
                    transaction["rollback_result"]["success"],
                    transaction["rollback_result"]["data"].get("boundary"),
                    target.read_bytes(),
                    apply_count,
                ),
                msg=f"rollback refresh failure lost disk restoration evidence: {result.to_dict()!r}",
            )
            self.assertEqual(
                (
                    "patch.apply validation failed and automatic rollback failed.",
                    "PATCH_APPLY_RESULT",
                    "Patch transaction rollback_sync failed.",
                    {"boundary": "rollback_sync", "state_unknown": True},
                    "SER_APPLY_REJECTED",
                ),
                (
                    result.message,
                    transaction["rollback_result"]["code"],
                    transaction["rollback_result"]["message"],
                    transaction["rollback_result"]["data"],
                    transaction["original_result"]["data"]["steps"][-1]["result"]["code"],
                ),
                msg=f"rollback refresh failure did not preserve both causes: {result.to_dict()!r}",
            )
            self.assertEqual(result.to_dict(), json.loads(report.read_text()))


class TestSinglePrefabTransactionActivation(_TransactionTestCase):
    def test_only_exactly_one_open_prefab_is_transaction_eligible(self) -> None:
        eligible = self._plan()
        two_open = self._plan()
        two_open["resources"].append(
            {
                "id": "second",
                "kind": "prefab",
                "path": "Assets/Second.prefab",
                "mode": "open",
            }
        )
        mixed = self._plan()
        mixed["resources"].append(
            {
                "id": "material",
                "kind": "material",
                "path": "Assets/Material.mat",
                "mode": "open",
            }
        )
        create_mode = self._plan(mode="create")
        non_prefab = self._plan(kind="material")

        self.assertEqual(
            [True, False, False, False, False],
            [is_single_open_prefab_plan(plan) for plan in (eligible, two_open, mixed, create_mode, non_prefab)],
        )


if __name__ == "__main__":
    unittest.main()
