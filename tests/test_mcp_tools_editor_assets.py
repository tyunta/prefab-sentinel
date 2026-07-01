from __future__ import annotations

import importlib
import json
import tempfile
import unittest
from pathlib import Path
from typing import Any, cast
from unittest.mock import patch

from tests._assertion_helpers import assert_error_envelope

CREATE_SUCCESS = {
    "success": True,
    "severity": "info",
    "code": "OK",
    "message": "create dry-run completed",
    "data": {
        "asset_type": "render_texture",
        "unity_type": "RenderTexture",
        "asset_path": "Assets/Test/Foo.renderTexture",
        "guid": "",
        "would_create": True,
        "created": False,
        "dry_run": True,
        "saved": False,
        "refreshed": False,
        "dirty_before": False,
        "dirty_after": False,
        "name": "Foo",
        "applied_parameters": {
            "width": 256,
            "height": 128,
            "depth": 0,
            "format": "ARGB32",
            "read_write": "Default",
            "filter_mode": "Bilinear",
            "wrap_mode": "Clamp",
            "mip_map": False,
        },
        "bridge_extra": "not public",
    },
    "diagnostics": [],
    "bridge_extra": "not public",
}


MOVE_SUCCESS = {
    "success": True,
    "severity": "info",
    "code": "OK",
    "message": "move dry-run completed",
    "data": {
        "source_asset_path": "Assets/Test/Foo.renderTexture",
        "destination_asset_path": "Assets/Test/Bar.renderTexture",
        "unity_type": "RenderTexture",
        "before_guid": "11112222333344445555666677778888",
        "after_guid": "",
        "guid_preserved": False,
        "would_move": True,
        "moved": False,
        "dry_run": True,
        "saved": False,
        "refreshed": False,
        "dirty_before": False,
        "dirty_after": False,
        "old_name": "Foo",
        "new_name": "Bar",
        "name_changed": True,
    },
    "diagnostics": [],
}


def _assets():
    try:
        return importlib.import_module("prefab_sentinel.mcp_tools_editor_assets")
    except ModuleNotFoundError as exc:
        raise AssertionError(
            "prefab_sentinel.mcp_tools_editor_assets module must exist for issue #116"
        ) from exc




class TestEditorAssetModuleNamespaceInvariants(unittest.TestCase):
    def test_unused_private_constants_are_not_kept_in_module_namespace(self) -> None:
        module_values = vars(_assets())

        self.assertNotIn("_UNITY_TYPE", module_values)
        self.assertNotIn("_TRANSPORT_FAILURE_PREFIXES", module_values)

class TestEditorCreateGeneratedAsset(unittest.TestCase):
    def test_create_dry_run_ignores_audit_fields_and_contacts_bridge(self) -> None:
        assets = _assets()
        with patch.object(assets, "send_action", return_value=CREATE_SUCCESS) as send:
            result = assets.editor_create_generated_asset(
                asset_type="render_texture",
                asset_path="Assets/Test/Foo.renderTexture",
                parameters={"width": 256, "height": 128},
                confirm=False,
                project_root="relative",
                out_report="not/absolute.json",
                change_reason="",
            )

        self.assertTrue(result["success"], result)
        self.assertEqual("create_generated_asset", send.call_args.kwargs["action"])
        self.assertFalse(send.call_args.kwargs["confirm"])
        self.assertEqual(
            {
                "width": 256,
                "height": 128,
                "depth": 0,
                "format": "ARGB32",
                "read_write": "Default",
                "filter_mode": "Bilinear",
                "wrap_mode": "Clamp",
                "mip_map": False,
            },
            send.call_args.kwargs["parameters"],
        )
        self.assertEqual("Foo", result["data"]["name"])
        self.assertEqual("", result["data"]["guid"])
        self.assertTrue(result["data"]["would_create"])
        self.assertFalse(result["data"]["created"])
        self.assertNotIn("change_reason", result["data"])
        self.assertNotIn("bridge_extra", result["data"])

    def test_create_confirm_writes_report_equal_to_final_response(self) -> None:
        assets = _assets()
        bridge: dict[str, Any] = dict(CREATE_SUCCESS)
        create_data = cast(dict[str, Any], CREATE_SUCCESS["data"])
        bridge_data: dict[str, Any] = dict(create_data)
        bridge_data.update({
            "guid": "abc",
            "would_create": False,
            "created": True,
            "dry_run": False,
            "saved": True,
            "refreshed": True,
        })
        bridge["data"] = bridge_data
        with tempfile.TemporaryDirectory() as td:
            report = Path(td) / "reports" / "create.json"
            report.parent.mkdir()
            with patch.object(assets, "send_action", return_value=bridge):
                result = assets.editor_create_generated_asset(
                    asset_type="render_texture",
                    asset_path="Assets/Test/Foo.renderTexture",
                    parameters={"width": 256, "height": 128},
                    confirm=True,
                    project_root=td,
                    out_report=str(report),
                    change_reason=" create rt ",
                )

            self.assertTrue(result["success"], result)
            self.assertTrue(result["data"]["created"])
            self.assertTrue(result["data"]["saved"])
            self.assertTrue(result["data"]["refreshed"])
            self.assertEqual("create rt", result["data"]["change_reason"])
            self.assertTrue(result["data"]["report_written"])
            self.assertEqual(str(report.resolve()), result["data"]["out_report"])
            self.assertNotIn("project_root", result["data"])
            self.assertEqual(result, json.loads(report.read_text(encoding="utf-8")))

    def test_unsupported_asset_type_does_not_call_bridge_or_leak_unity_type(self) -> None:
        assets = _assets()
        with patch.object(assets, "send_action") as send:
            result = assets.editor_create_generated_asset(
                asset_type="texture2d",
                asset_path="Assets/Test/Foo.renderTexture",
                parameters={"width": 256, "height": 128},
                confirm=False,
            )

        send.assert_not_called()
        assert_error_envelope(
            result,
            code="UNSUPPORTED_GENERATED_ASSET_TYPE",
            severity="error",
            field="asset_type",
        )
        self.assertNotIn("unity_type", result["data"])

    def test_confirmed_create_validation_failure_writes_audit_report(self) -> None:
        assets = _assets()
        with tempfile.TemporaryDirectory() as td:
            report = Path(td) / "create-error.json"
            with patch.object(assets, "send_action") as send:
                result = assets.editor_create_generated_asset(
                    asset_type="render_texture",
                    asset_path="Assets/Test/Foo.rendertexture",
                    parameters={"width": 256, "height": 128},
                    confirm=True,
                    project_root=td,
                    out_report=str(report),
                    change_reason="pin invalid path",
                )

            send.assert_not_called()
            assert_error_envelope(
                result,
                code="GENERATED_ASSET_INVALID_PATH",
                severity="error",
                field="asset_path",
            )
            self.assertEqual(result, json.loads(report.read_text(encoding="utf-8")))
            self.assertTrue(result["data"]["report_written"])


class TestEditorMoveAsset(unittest.TestCase):
    def test_move_dry_run_reports_source_evidence_without_report(self) -> None:
        assets = _assets()
        with patch.object(assets, "send_action", return_value=MOVE_SUCCESS) as send:
            result = assets.editor_move_asset(
                source_asset_path="Assets/Test/Foo.renderTexture",
                destination_asset_path="Assets/Test/Bar.renderTexture",
                confirm=False,
                project_root="relative",
                out_report="not/absolute.json",
                change_reason="",
            )

        self.assertTrue(result["success"], result)
        self.assertEqual("move_asset", send.call_args.kwargs["action"])
        self.assertFalse(send.call_args.kwargs["confirm"])
        self.assertEqual("11112222333344445555666677778888", result["data"]["before_guid"])
        self.assertEqual("", result["data"]["after_guid"])
        self.assertFalse(result["data"]["guid_preserved"])
        self.assertTrue(result["data"]["would_move"])
        self.assertFalse(result["data"]["moved"])
        self.assertTrue(result["data"]["name_changed"])
        self.assertNotIn("change_reason", result["data"])

    def test_move_confirm_writes_guid_preservation_report(self) -> None:
        assets = _assets()
        bridge: dict[str, Any] = dict(MOVE_SUCCESS)
        move_data = cast(dict[str, Any], MOVE_SUCCESS["data"])
        bridge_data: dict[str, Any] = dict(move_data)
        bridge_data.update({
            "after_guid": "11112222333344445555666677778888",
            "guid_preserved": True,
            "would_move": False,
            "moved": True,
            "dry_run": False,
            "saved": True,
            "refreshed": True,
        })
        bridge["data"] = bridge_data
        with tempfile.TemporaryDirectory() as td:
            report = Path(td) / "move.json"
            with patch.object(assets, "send_action", return_value=bridge):
                result = assets.editor_move_asset(
                    source_asset_path="Assets/Test/Foo.renderTexture",
                    destination_asset_path="Assets/Test/Bar.renderTexture",
                    confirm=True,
                    project_root=td,
                    out_report=str(report),
                    change_reason="move rt",
                )

            self.assertTrue(result["success"], result)
            self.assertTrue(result["data"]["guid_preserved"])
            self.assertEqual(
                "11112222333344445555666677778888", result["data"]["after_guid"]
            )
            self.assertTrue(result["data"]["moved"])
            self.assertEqual(result, json.loads(report.read_text(encoding="utf-8")))

    def test_confirmed_move_validation_failure_writes_audit_report(self) -> None:
        assets = _assets()
        with tempfile.TemporaryDirectory() as td:
            report = Path(td) / "move-error.json"
            with patch.object(assets, "send_action") as send:
                result = assets.editor_move_asset(
                    source_asset_path="Assets/Test/Foo.renderTexture",
                    destination_asset_path="Assets/Test/Foo.renderTexture",
                    confirm=True,
                    project_root=td,
                    out_report=str(report),
                    change_reason="same path",
                )

            send.assert_not_called()
            assert_error_envelope(result, code="ASSET_MOVE_SAME_PATH", severity="error")
            self.assertEqual(result, json.loads(report.read_text(encoding="utf-8")))
            self.assertTrue(result["data"]["report_written"])


class TestConfirmReportValidation(unittest.TestCase):
    def test_confirm_validation_order_and_dry_run_exception(self) -> None:
        assets = _assets()
        with tempfile.TemporaryDirectory() as td:
            valid_report = str(Path(td) / "ok.json")
            cases = [
                ("yes", None, None, None, "INVALID_CONFIRM_VALUE"),
                (True, "relative", None, None, "PROJECT_ROOT_INVALID"),
                (True, td, None, "reason", "OUT_REPORT_REQUIRED"),
                (True, td, valid_report, " " * 3, "CHANGE_REASON_REQUIRED"),
                (True, td, valid_report, "x" * 1025, "CHANGE_REASON_TOO_LONG"),
            ]
            for confirm, root, report, reason, code in cases:
                result = assets.editor_create_generated_asset(
                    asset_type="missing on purpose",
                    asset_path="",
                    parameters={},
                    confirm=confirm,
                    project_root=root,
                    out_report=report,
                    change_reason=reason,
                )
                assert_error_envelope(result, code=code, severity="error")

            with patch.object(assets, "send_action", return_value=CREATE_SUCCESS) as send:
                result = assets.editor_create_generated_asset(
                    asset_type="render_texture",
                    asset_path="Assets/Test/Foo.renderTexture",
                    parameters={"width": 256, "height": 128},
                    confirm=False,
                    project_root="relative",
                    out_report="bad",
                    change_reason="",
                )
            self.assertTrue(result["success"], result)
            send.assert_called_once()


class TestEditorAssetValidation(unittest.TestCase):
    def test_create_path_priority_and_move_path_priority(self) -> None:
        assets = _assets()
        create_cases = [
            (None, "GENERATED_ASSET_INVALID_PATH", "asset_path_required"),
            ("", "GENERATED_ASSET_INVALID_PATH", "asset_path_required"),
            ("Assets/Foo.renderTexture\0", "GENERATED_ASSET_INVALID_PATH", "nul_byte"),
            ("/Assets/Foo.renderTexture", "GENERATED_ASSET_INVALID_PATH", "absolute_path"),
            ("Assets\\Foo.renderTexture", "GENERATED_ASSET_INVALID_PATH", "backslash"),
            ("Packages/Foo.renderTexture", "GENERATED_ASSET_INVALID_PATH", "must_start_with_assets"),
            ("Assets", "GENERATED_ASSET_INVALID_PATH", "assets_root_not_asset"),
            ("Assets/Test/", "GENERATED_ASSET_INVALID_PATH", "empty_path_segment"),
            ("Assets/./Foo.renderTexture", "GENERATED_ASSET_INVALID_PATH", "dot_segment"),
            ("Assets/Foo.renderTexture.meta", "GENERATED_ASSET_PATH_IS_META_FILE", "meta_file_path"),
            ("Assets/.renderTexture", "GENERATED_ASSET_INVALID_PATH", "asset_name_stem_required"),
            ("Assets/Foo.rendertexture", "GENERATED_ASSET_INVALID_PATH", "extension_mismatch"),
        ]
        for path, code, reason in create_cases:
            result = assets.validate_generated_asset_path(path)
            assert_error_envelope(result, code=code, severity="error", data=result["data"])
            self.assertEqual(reason, result["data"]["reason"])

        valid = assets.validate_generated_asset_path("Assets/.foo.bar.renderTexture")
        self.assertEqual("Assets/.foo.bar.renderTexture", valid.asset_path)
        self.assertEqual(".foo.bar", valid.name)

        meta_result = assets.validate_move_asset_paths(
            "Assets/Foo.renderTexture",
            "Assets/Foo.mat.meta",
        )
        assert_error_envelope(
            meta_result,
            code="ASSET_DESTINATION_IS_META_FILE",
            severity="error",
        )
        mismatch = assets.validate_move_asset_paths("Assets/Foo.mat", "Assets/Foo.asset")
        assert_error_envelope(mismatch, code="ASSET_EXTENSION_MISMATCH", severity="error")
        same = assets.validate_move_asset_paths("Assets/Foo.mat", "Assets/Foo.mat")
        assert_error_envelope(same, code="ASSET_MOVE_SAME_PATH", severity="error")
        case_only = assets.validate_move_asset_paths("Assets/Foo.mat", "assets/foo.mat")
        assert_error_envelope(
            case_only,
            code="ASSET_MOVE_CASE_ONLY_RENAME_UNSUPPORTED",
            severity="error",
        )

    def test_render_texture_parameters_defaults_and_strict_invalid_partitions(self) -> None:
        assets = _assets()
        params = assets.validate_render_texture_parameters({"width": 64, "height": 32})
        self.assertEqual(
            {
                "width": 64,
                "height": 32,
                "depth": 0,
                "format": "ARGB32",
                "read_write": "Default",
                "filter_mode": "Bilinear",
                "wrap_mode": "Clamp",
                "mip_map": False,
            },
            params.to_bridge_dict(),
        )

        unknown_missing = assets.validate_render_texture_parameters({"width": 64, "camelCase": 1})
        assert_error_envelope(
            unknown_missing,
            code="GENERATED_ASSET_INVALID_PARAMETER",
            severity="error",
        )
        self.assertEqual(["height"], unknown_missing["data"]["missing_keys"])
        self.assertEqual(["camelCase"], unknown_missing["data"]["unknown_keys"])

        invalid_cases = [
            ({"width": True, "height": 1}, "width", "integer"),
            ({"width": 1.5, "height": 1}, "width", "integer"),
            ({"width": 1, "height": 8193}, "height", "integer"),
            ({"width": 1, "height": 1, "depth": 8}, "depth", "integer"),
            ({"width": 1, "height": 1, "format": "R8"}, "format", "string"),
            ({"width": 1, "height": 1, "filter_mode": 1}, "filter_mode", "string"),
            ({"width": 1, "height": 1, "mip_map": 1}, "mip_map", "boolean"),
        ]
        for payload, field, expected_type in invalid_cases:
            result = assets.validate_render_texture_parameters(payload)
            assert_error_envelope(
                result,
                code="GENERATED_ASSET_INVALID_PARAMETER",
                severity="error",
                field=field,
            )
            self.assertEqual(expected_type, result["data"]["expected_type"])


class TestBridgeProjectionAndReports(unittest.TestCase):
    def test_malformed_bridge_envelope_has_state_unknown_partial_diagnostic(self) -> None:
        assets = _assets()
        result = assets.finalize_asset_operation_response(
            "create",
            {"severity": "info", "code": "OK", "message": "missing success", "data": {}, "diagnostics": []},
            assets.DryRunContext(),
        )
        assert_error_envelope(
            result,
            code="UNITY_BRIDGE_INVALID_RESPONSE",
            severity="error",
        )
        self.assertTrue(result["data"]["state_unknown"])
        self.assertEqual("PARTIAL_SIDE_EFFECT_REQUIRES_REVIEW", result["diagnostics"][0]["code"])
        self.assertEqual("warning", result["diagnostics"][0]["severity"])

    def test_legacy_bridge_diagnostic_preserves_code_and_detail_message(self) -> None:
        assets = _assets()
        result = assets.finalize_asset_operation_response(
            "create",
            {
                "success": False,
                "severity": "error",
                "code": "GENERATED_ASSET_CREATE_FAILED",
                "message": "create failed",
                "data": {"phase": "create"},
                "diagnostics": [
                    {
                        "severity": "warning",
                        "code": "PARTIAL_SIDE_EFFECT_REQUIRES_REVIEW",
                        "detail": "Operation state must be reviewed in Unity.",
                        "evidence": "GENERATED_ASSET_CREATE_FAILED",
                        "path": "Assets/Test/Foo.renderTexture",
                    }
                ],
            },
            assets.DryRunContext(),
        )

        diagnostic = result["diagnostics"][0]
        self.assertEqual("PARTIAL_SIDE_EFFECT_REQUIRES_REVIEW", diagnostic["code"])
        self.assertEqual("Operation state must be reviewed in Unity.", diagnostic["message"])
        self.assertEqual({"path": "Assets/Test/Foo.renderTexture"}, diagnostic["data"])
        self.assertEqual("GENERATED_ASSET_CREATE_FAILED", result["code"])

    def test_projection_accepts_extra_bridge_keys_but_preserves_public_allowlist(self) -> None:
        assets = _assets()
        result = assets.finalize_asset_operation_response(
            "create",
            CREATE_SUCCESS,
            assets.DryRunContext(),
        )
        self.assertTrue(result["success"], result)
        self.assertNotIn("bridge_extra", result["data"])
        self.assertEqual(
            {
                "asset_type",
                "unity_type",
                "asset_path",
                "guid",
                "would_create",
                "created",
                "dry_run",
                "saved",
                "refreshed",
                "dirty_before",
                "dirty_after",
                "name",
                "applied_parameters",
            },
            set(result["data"]),
        )

    def test_bridge_transport_failure_is_not_remapped(self) -> None:
        assets = _assets()
        transport = {
            "success": False,
            "severity": "error",
            "code": "EDITOR_BRIDGE_TIMEOUT",
            "message": "Editor bridge response timed out.",
            "data": {"action": "create_generated_asset"},
            "diagnostics": [],
        }
        result = assets.finalize_asset_operation_response("create", transport, assets.DryRunContext())
        self.assertEqual(transport, result)

    def test_report_write_failure_preserves_operation_result(self) -> None:
        assets = _assets()
        with tempfile.TemporaryDirectory() as td:
            audit = assets.ValidatedAuditContext(
                project_root=Path(td).resolve(),
                out_report=(Path(td) / "report.json").resolve(),
                change_reason="write report",
            )
            with patch("builtins.open", side_effect=OSError("disk full")):
                result = assets.finalize_asset_operation_response("create", CREATE_SUCCESS, audit)

        assert_error_envelope(result, code="OUT_REPORT_WRITE_FAILED", severity="error")
        self.assertEqual("disk full", result["data"]["error"])
        self.assertTrue(result["data"]["operation_result"]["success"])
        self.assertFalse(result["data"].get("rolled_back", False))


if __name__ == "__main__":
    unittest.main()
