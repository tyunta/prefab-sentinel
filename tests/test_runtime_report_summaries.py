from __future__ import annotations

import json
import unittest
from pathlib import Path
from unittest.mock import patch

from prefab_sentinel.contracts import Severity, ToolResponse
from prefab_sentinel.orchestrator_validation import validate_runtime
from prefab_sentinel.reporting import extract_runtime_validation_data
from prefab_sentinel.reporting_markdown import render_markdown_report
from prefab_sentinel.services.runtime_validation import RuntimeReportReservation
from tests import test_orchestrator_validation as runtime_fixtures


class RuntimeReportSummaryTests(unittest.TestCase):
    def _terminal(self, profile: str, *, confirm: bool = True, compile_succeeds: bool = True):
        root = Path("/private/runtime-report-fixture")
        fixtures = runtime_fixtures.ValidateRuntimeProfileTests
        runtime = fixtures._audited_runtime(root)
        created = ["Assets/Generated/New.asset"]
        deleted = ["Assets/Generated/Old.asset", "Assets/Generated/Stale.asset"]
        compile_report = fixtures._compile_section(
            success=compile_succeeds,
            severity="info" if compile_succeeds else "error",
            code="RUN_COMPILE_OK" if compile_succeeds else "RUN_COMPILE_FAILED",
            delta_updates={
                "planned_created_paths": created,
                "planned_deleted_paths": deleted,
                "actual_created_paths": created,
                "actual_deleted_paths": deleted,
            },
        )
        for snapshot in (compile_report["before"], compile_report["after"]):
            assert isinstance(snapshot, dict)
            snapshot["generated_asset_plan"] = {
                "planned_created_paths": created,
                "planned_deleted_paths": deleted,
            }
        runtime.execute_write_profile.return_value.data["compile"] = compile_report
        if not compile_succeeds:
            runtime.execute_write_profile.return_value.success = False
            runtime.execute_write_profile.return_value.severity = Severity.ERROR
            runtime.execute_write_profile.return_value.code = "RUN_COMPILE_FAILED"
        runtime.execute_write_profile.return_value.data["clientsim"] = fixtures._clientsim_section(
            executed=profile == "clientsim" and compile_succeeds,
        )
        runtime.classify_errors.return_value = ToolResponse(
            False, Severity.CRITICAL, "RUN001", "Runtime issues classified.",
            {
                "line_count": 42,
                "count_total": 5,
                "count_by_category": {"UDON_NULLREF": 3, "BROKEN_PPTR": 2},
                "categories_by_severity": {"critical": 3, "error": 2, "warning": 0},
            },
        )
        runtime.assert_no_critical_errors.return_value = ToolResponse(
            False, Severity.CRITICAL, "RUN001", "Runtime issues detected.",
            {"critical_count": 3, "error_count": 2, "warning_count": 0, "allow_warnings": False},
        )
        canvas = ToolResponse(True, Severity.INFO, "WORLD_CANVAS_INSPECT_OK", "Inspected.", {"read_only": True})
        reservation = RuntimeReportReservation(root / "audit.json", root)
        with (
            patch("prefab_sentinel.services.runtime_validation.config.default_runtime_root", return_value=root),
            patch("prefab_sentinel.services.runtime_validation.reserve_runtime_report", return_value=reservation) as reserve,
            patch("prefab_sentinel.services.runtime_validation.publish_runtime_report") as publish,
            patch("prefab_sentinel.orchestrator_validation._inspect_world_canvas_step", return_value=canvas),
        ):
            response = validate_runtime(
                runtime, "Assets/Scenes/Runtime.unity", profile=profile,
                confirm=confirm, change_reason="reporting probe", out_report="Audit/runtime.json",
                generated_asset_policy="replace",
            )

        if profile == "editor_console_only":
            reserve.assert_not_called()
            publish.assert_not_called()
        else:
            publish.assert_called_once_with(reservation, response.data)
        return response, runtime

    def _assert_raw_data_preserved(self, response: ToolResponse, rendered: str) -> None:
        raw_json = rendered.split("```json\n", 1)[1].split("\n```", 1)[0]
        self.assertEqual(response.data, json.loads(raw_json))
        self.assertNotIn("/private/runtime-report-fixture", rendered)

    def test_current_write_profiles_extract_authoritative_sections_and_nested_steps(self) -> None:
        for profile in ("compile_only", "clientsim"):
            with self.subTest(profile=profile):
                response, _ = self._terminal(profile)
                original = json.dumps(response.data, sort_keys=True)

                summary = extract_runtime_validation_data(response.data)

                self.assertEqual(
                    {section: response.data[section] for section in ("preflight", "compile", "clientsim")},
                    {section: summary.get(section) for section in ("preflight", "compile", "clientsim")},
                )
                self.assertEqual(5, summary["classification"]["count_total"])
                self.assertEqual(42, summary["classification"]["line_count"])
                self.assertEqual({"UDON_NULLREF": 3, "BROKEN_PPTR": 2}, summary["classification"]["count_by_category"])
                self.assertEqual(3, summary["assertion"]["critical_count"])
                self.assertEqual("RUN_LOG_COLLECTED", summary["collect_unity_console"]["code"])
                self.assertEqual(original, json.dumps(response.data, sort_keys=True))

    def test_current_write_profiles_render_audit_status_and_preserve_raw_data(self) -> None:
        for profile in ("compile_only", "clientsim"):
            with self.subTest(profile=profile):
                response, _ = self._terminal(profile)

                rendered = render_markdown_report(response.to_dict())

                self.assertIn("## Runtime Validation", rendered)
                self.assertIn("Preflight Completed: True", rendered)
                self.assertIn("Preflight Diagnostics: 0", rendered)
                self.assertIn("Compile Executed: True", rendered)
                self.assertIn("Compile Result: RUN_COMPILE_OK", rendered)
                self.assertIn("Compiled Programs: 1", rendered)
                self.assertIn("Generated Assets: created=1, deleted=2", rendered)
                self.assertIn(f"ClientSim Executed: {profile == 'clientsim'}", rendered)
                if profile == "clientsim":
                    self.assertIn("ClientSim Diff Complete: True", rendered)
                else:
                    self.assertNotIn("ClientSim Diff Complete:", rendered)
                self.assertIn("Log Collect Step: RUN_LOG_COLLECTED", rendered)
                self.assertIn("Matched Issues: 5", rendered)
                self.assertIn("critical=3, error=2, warning=0", rendered)
                self.assertIn("| UDON_NULLREF | 3 |", rendered)
                self._assert_raw_data_preserved(response, rendered)

    def test_current_flat_editor_console_report_extracts_current_collector(self) -> None:
        response, runtime = self._terminal("editor_console_only")

        summary = extract_runtime_validation_data(response.data)
        rendered = render_markdown_report(response.to_dict())

        self.assertEqual("RUN_EDITOR_CONSOLE_COLLECTED", summary.get("collect_editor_console", {}).get("code"))
        self.assertEqual(5, summary["classification"]["count_total"])
        self.assertEqual(3, summary["assertion"]["critical_count"])
        self.assertIn("Log Collect Step: RUN_EDITOR_CONSOLE_COLLECTED", rendered)
        self.assertIn("Matched Issues: 5", rendered)
        self.assertNotIn("Compile Executed:", rendered)
        self.assertNotIn("ClientSim Executed:", rendered)
        runtime.execute_write_profile.assert_not_called()
        self._assert_raw_data_preserved(response, rendered)

    def test_audit_rejection_shows_unexecuted_sections_without_dispatch_or_clean_counts(self) -> None:
        response, runtime = self._terminal("compile_only", confirm=False)

        summary = extract_runtime_validation_data(response.data)
        rendered = render_markdown_report(response.to_dict())

        self.assertEqual((False, "CHANGE_REASON_REQUIRED"), (response.success, response.code))
        self.assertEqual(
            {"preflight": False, "compile": False, "clientsim": False},
            {
                "preflight": summary.get("preflight", {}).get("completed"),
                "compile": summary.get("compile", {}).get("executed"),
                "clientsim": summary.get("clientsim", {}).get("executed"),
            },
        )
        self.assertNotIn("classification", summary)
        self.assertNotIn("assertion", summary)
        self.assertIn("Preflight Completed: False", rendered)
        self.assertIn("Compile Executed: False", rendered)
        self.assertIn("ClientSim Executed: False", rendered)
        self.assertNotIn("Matched Issues:", rendered)
        self.assertNotIn("Compiled Programs:", rendered)
        runtime.execute_write_profile.assert_not_called()
        self._assert_raw_data_preserved(response, rendered)

    def test_compile_failure_preserves_partial_audit_without_claiming_log_classification(self) -> None:
        response, runtime = self._terminal("clientsim", compile_succeeds=False)

        summary = extract_runtime_validation_data(response.data)
        rendered = render_markdown_report(response.to_dict())

        self.assertFalse(response.success)
        self.assertEqual("RUN_COMPILE_FAILED", summary["compile"]["code"])
        self.assertFalse(summary["compile"]["success"])
        self.assertFalse(summary["clientsim"]["executed"])
        self.assertNotIn("classification", summary)
        self.assertIn("Compile Result: RUN_COMPILE_FAILED", rendered)
        self.assertIn("Generated Assets: created=1, deleted=2", rendered)
        self.assertIn("ClientSim Executed: False", rendered)
        self.assertNotIn("Matched Issues:", rendered)
        runtime.classify_errors.assert_not_called()
        self._assert_raw_data_preserved(response, rendered)
