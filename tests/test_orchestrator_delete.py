from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from prefab_sentinel.contracts import Severity, ToolResponse
from prefab_sentinel.orchestrator import Phase1Orchestrator
from prefab_sentinel.services.reference_resolver import ReferenceResolverService

GUID = "1234567890abcdef1234567890abcdef"


def _load_delete_module(testcase: unittest.TestCase):
    try:
        from prefab_sentinel import orchestrator_delete
    except (ImportError, ModuleNotFoundError):
        testcase.fail("prefab_sentinel.orchestrator_delete module is required")
    return orchestrator_delete


def _make_project(root: Path) -> Path:
    assets = root / "Assets"
    assets.mkdir()
    asset = assets / "Foo.prefab"
    asset.write_text("%YAML 1.1\n--- !u!1 &1\nGameObject: {}\n", encoding="utf-8")
    asset.with_suffix(asset.suffix + ".meta").write_text(
        f"fileFormatVersion: 2\nguid: {GUID}\n",
        encoding="utf-8",
    )
    (assets / "Ref.prefab").write_text(
        f"%YAML 1.1\n--- !u!114 &1\nm_Script: {{fileID: 1, guid: {GUID}, type: 3}}\n",
        encoding="utf-8",
    )
    return asset


def _scan_response(
    broken_count: int,
    missing_guids: list[str],
    categories: dict[str, int],
) -> ToolResponse:
    return ToolResponse(
        True,
        Severity.INFO,
        "REF_SCAN_OK",
        "scan ok",
        {
            "broken_count": broken_count,
            "unique_missing_asset_guids": missing_guids,
            "categories": categories,
        },
    )


class _DeltaResolver:
    def __init__(self) -> None:
        self._scan_responses = [
            _scan_response(0, [], {"missing_asset": 0}),
            _scan_response(1, ["bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"], {"missing_asset": 1}),
        ]

    def where_used(self, *_args, **_kwargs) -> ToolResponse:
        return ToolResponse(
            True,
            Severity.INFO,
            "REF_WHERE_USED",
            "where-used ok",
            {"usages": [], "usage_count": 0},
        )

    def scan_broken_references(self, *_args, **_kwargs) -> ToolResponse:
        return self._scan_responses.pop(0)


class OrchestratorDeleteTests(unittest.TestCase):
    def _orch(self, project_root: Path, resolver=None) -> SimpleNamespace:
        reference_resolver = resolver or ReferenceResolverService(project_root)
        return SimpleNamespace(
            project_root=project_root,
            reference_resolver=reference_resolver,
            invalidate_text_cache=MagicMock(),
            invalidate_guid_index=MagicMock(),
            invalidate_scope_files_cache=MagicMock(),
        )

    def test_delete_assets_dry_run_does_not_contact_editor_bridge(self) -> None:
        module = _load_delete_module(self)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_project(root)
            orch = self._orch(root)
            with patch.object(module, "send_action") as send:
                response = module.delete_assets(
                    orch, ["Assets/Foo.prefab"], dry_run=True, confirm=False
                )

        self.assertEqual((True, "ASSET_DELETE_DRY_RUN"), (response.success, response.code))
        self.assertTrue(response.data["read_only"])
        send.assert_not_called()

    def test_confirmed_apply_without_audit_reason_is_rejected_before_bridge(self) -> None:
        module = _load_delete_module(self)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_project(root)
            orch = self._orch(root)
            with patch.object(module, "send_action") as send:
                response = module.delete_assets(
                    orch,
                    ["Assets/Foo.prefab"],
                    dry_run=False,
                    confirm=True,
                    change_reason="",
                )

        self.assertEqual((False, "CHANGE_REASON_REQUIRED"), (response.success, response.code))
        send.assert_not_called()

    def test_confirmed_apply_without_audit_reason_rejects_before_planning_errors(self) -> None:
        module = _load_delete_module(self)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "Assets").mkdir()
            orch = self._orch(root)
            with patch.object(module, "send_action") as send:
                response = module.delete_assets(
                    orch,
                    ["Assets/Missing.prefab"],
                    dry_run=False,
                    confirm=True,
                    change_reason=" ",
                )

        self.assertEqual((False, "CHANGE_REASON_REQUIRED"), (response.success, response.code))
        send.assert_not_called()

    def test_bridge_unavailable_returns_unsupported_without_filesystem_fallback(self) -> None:
        module = _load_delete_module(self)
        for bridge_code in (
            "BRIDGE_WATCH_DIR_MISSING",
            "EDITOR_BRIDGE_WATCH_DIR_NOT_FOUND",
        ):
            with self.subTest(bridge_code=bridge_code):
                bridge_response: dict[str, object] = {
                    "success": False,
                    "code": bridge_code,
                    "message": "watch dir missing",
                    "data": {"bridge_mode": "editor"},
                    "diagnostics": [],
                }
                with tempfile.TemporaryDirectory() as tmp:
                    root = Path(tmp)
                    _make_project(root)
                    orch = self._orch(root)
                    with (
                        patch.object(module, "send_action", return_value=bridge_response),
                        patch.object(Path, "unlink") as unlink,
                        patch.object(os, "remove") as remove,
                    ):
                        response = module.delete_assets(
                            orch,
                            ["Assets/Foo.prefab"],
                            dry_run=False,
                            confirm=True,
                            change_reason="remove obsolete asset",
                        )

                self.assertEqual(
                    (False, "ASSET_DELETE_UNSUPPORTED"),
                    (response.success, response.code),
                )
                self.assertEqual(bridge_code, response.data["bridge_code"])
                unlink.assert_not_called()
                remove.assert_not_called()

    def test_bridge_transport_failure_preserves_bridge_error_code(self) -> None:
        module = _load_delete_module(self)
        bridge_response = {
            "success": False,
            "code": "EDITOR_BRIDGE_TIMEOUT",
            "message": "Editor bridge response timed out.",
            "data": {"action": "delete_assets"},
            "diagnostics": [],
        }
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_project(root)
            orch = self._orch(root)
            with patch.object(module, "send_action", return_value=bridge_response):
                response = module.delete_assets(
                    orch,
                    ["Assets/Foo.prefab"],
                    dry_run=False,
                    confirm=True,
                    change_reason="remove obsolete asset",
                )

        self.assertEqual((False, "EDITOR_BRIDGE_TIMEOUT"), (response.success, response.code))
        self.assertEqual("EDITOR_BRIDGE_TIMEOUT", response.data["bridge_code"])

    def test_bridge_delete_assets_failed_code_maps_to_public_contract(self) -> None:
        module = _load_delete_module(self)
        bridge_response = {
            "success": False,
            "code": "DELETE_ASSETS_FAILED",
            "message": "AssetDatabase.DeleteAssets reported failed paths.",
            "data": {},
            "diagnostics": [],
        }
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_project(root)
            orch = self._orch(root)
            with patch.object(module, "send_action", return_value=bridge_response):
                response = module.delete_assets(
                    orch,
                    ["Assets/Foo.prefab"],
                    dry_run=False,
                    confirm=True,
                    change_reason="remove obsolete asset",
                )

        self.assertEqual((False, "ASSET_DELETE_FAILED"), (response.success, response.code))
        self.assertEqual("DELETE_ASSETS_FAILED", response.data["bridge_code"])

    def test_confirmed_apply_dispatches_bridge_action_with_audit_data(self) -> None:
        module = _load_delete_module(self)
        bridge_response = {
            "success": True,
            "code": "DELETE_ASSETS_OK",
            "message": "deleted",
            "data": {"deleted_paths": ["Assets/Foo.prefab"], "failed_paths": []},
            "diagnostics": [],
        }
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_project(root)
            orch = self._orch(root)
            with patch.object(module, "send_action", return_value=bridge_response) as send:
                response = module.delete_assets(
                    orch,
                    ["Assets/Foo.prefab"],
                    dry_run=False,
                    confirm=True,
                    change_reason="remove obsolete asset",
                )

        self.assertEqual((True, "ASSET_DELETE_APPLIED"), (response.success, response.code))
        send.assert_called_once()
        kwargs = send.call_args.kwargs
        self.assertEqual("delete_assets", kwargs["action"])
        self.assertEqual(["Assets/Foo.prefab"], json.loads(kwargs["asset_paths_json"]))
        self.assertTrue(kwargs["confirm"])
        self.assertEqual("remove obsolete asset", kwargs["change_reason"])

    def test_confirmed_apply_dispatches_normalized_plan_paths(self) -> None:
        module = _load_delete_module(self)
        bridge_response = {
            "success": True,
            "code": "DELETE_ASSETS_OK",
            "message": "deleted",
            "data": {"deleted_paths": ["Assets/Foo.prefab"], "failed_paths": []},
            "diagnostics": [],
        }
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_project(root)
            orch = self._orch(root)
            with patch.object(module, "send_action", return_value=bridge_response) as send:
                response = module.delete_assets(
                    orch,
                    ["./Assets/Foo.prefab"],
                    dry_run=False,
                    confirm=True,
                    change_reason="remove obsolete asset",
                )

        self.assertEqual((True, "ASSET_DELETE_APPLIED"), (response.success, response.code))
        kwargs = send.call_args.kwargs
        self.assertEqual(["Assets/Foo.prefab"], json.loads(kwargs["asset_paths_json"]))

    def test_broken_reference_increase_after_success_is_reported_without_failure(self) -> None:
        module = _load_delete_module(self)
        bridge_response = {
            "success": True,
            "code": "DELETE_ASSETS_OK",
            "message": "deleted",
            "data": {"deleted_paths": ["Assets/Foo.prefab"], "failed_paths": []},
            "diagnostics": [],
        }
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_project(root)
            orch = self._orch(root, resolver=_DeltaResolver())
            with patch.object(module, "send_action", return_value=bridge_response):
                response = module.delete_assets(
                    orch,
                    ["Assets/Foo.prefab"],
                    dry_run=False,
                    confirm=True,
                    change_reason="remove obsolete asset",
                )

        self.assertEqual((True, "ASSET_DELETE_APPLIED"), (response.success, response.code))
        delta = response.data["broken_reference_delta"]
        self.assertEqual(1, delta["broken_count_delta"])
        self.assertEqual(["bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"], delta["new_missing_asset_guids"])

    def test_post_delete_scan_failure_is_propagated(self) -> None:
        module = _load_delete_module(self)
        bridge_response = {
            "success": True,
            "code": "DELETE_ASSETS_OK",
            "message": "deleted",
            "data": {"deleted_paths": ["Assets/Foo.prefab"], "failed_paths": []},
            "diagnostics": [],
        }

        class ScanFailureResolver(_DeltaResolver):
            def __init__(self) -> None:
                self._scan_responses = [
                    _scan_response(0, [], {"missing_asset": 0}),
                    ToolResponse(
                        False,
                        Severity.ERROR,
                        "REF404",
                        "Scope path does not exist.",
                        {"scope": "Assets/MissingScope", "read_only": True},
                    ),
                ]

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_project(root)
            orch = self._orch(root, resolver=ScanFailureResolver())
            with patch.object(module, "send_action", return_value=bridge_response):
                response = module.delete_assets(
                    orch,
                    ["Assets/Foo.prefab"],
                    scope="Assets/MissingScope",
                    dry_run=False,
                    confirm=True,
                    change_reason="remove obsolete asset",
                )

        self.assertEqual((False, "REF404"), (response.success, response.code))
        self.assertEqual("Assets/MissingScope", response.data["scope"])

    def test_assetdatabase_failed_paths_return_delete_failed(self) -> None:
        module = _load_delete_module(self)
        bridge_response = {
            "success": True,
            "code": "DELETE_ASSETS_OK",
            "message": "partial",
            "data": {
                "deleted_paths": ["Assets/Deleted.prefab"],
                "failed_paths": ["Assets/Foo.prefab"],
            },
            "diagnostics": [],
        }
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_project(root)
            orch = self._orch(root, resolver=_DeltaResolver())
            with patch.object(module, "send_action", return_value=bridge_response):
                response = module.delete_assets(
                    orch,
                    ["Assets/Foo.prefab"],
                    dry_run=False,
                    confirm=True,
                    change_reason="remove obsolete asset",
                )

        self.assertEqual((False, "ASSET_DELETE_FAILED"), (response.success, response.code))
        self.assertEqual(["Assets/Foo.prefab"], response.data["failed_paths"])
        self.assertEqual(["Assets/Deleted.prefab"], response.data["deleted_paths"])
        self.assertEqual(1, response.data["broken_reference_delta"]["broken_count_delta"])
        self.assertEqual(
            (1, 1, 1),
            (
                orch.invalidate_text_cache.call_count,
                orch.invalidate_guid_index.call_count,
                orch.invalidate_scope_files_cache.call_count,
            ),
        )

    def test_dry_run_response_preserves_ambiguous_udon_candidate_paths(self) -> None:
        module = _load_delete_module(self)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_project(root)
            script = root / "Assets" / "MyBehaviour.cs"
            script.write_text("public class MyBehaviour {}\n", encoding="utf-8")
            script.with_suffix(script.suffix + ".meta").write_text(
                f"fileFormatVersion: 2\nguid: {GUID}\n",
                encoding="utf-8",
            )
            for name, guid in (
                ("ProgramA.asset", "a" * 32),
                ("ProgramB.asset", "b" * 32),
            ):
                program = root / "Assets" / name
                program.write_text(
                    "%YAML 1.1\n--- !u!114 &11400000\nUdonSharpProgramAsset:\n"
                    f"  sourceCsScript: {{fileID: 11500000, guid: {GUID}, type: 3}}\n",
                    encoding="utf-8",
                )
                program.with_suffix(program.suffix + ".meta").write_text(
                    f"fileFormatVersion: 2\nguid: {guid}\n",
                    encoding="utf-8",
                )

            response = module.delete_assets(
                self._orch(root),
                ["Assets/MyBehaviour.cs"],
                scope="Assets",
            )

        wire = response.to_dict()
        self.assertEqual((True, "ASSET_DELETE_DRY_RUN"), (wire["success"], wire["code"]))
        self.assertEqual(
            [
                {
                    "code": "ASSET_DELETE_DECISION_REQUIRED",
                    "detail": "ambiguous_udonsharp_program_asset",
                    "asset_paths": ["Assets/ProgramA.asset", "Assets/ProgramB.asset"],
                }
            ],
            wire["data"]["decision_required"],
        )


class Phase1OrchestratorDeleteTests(unittest.TestCase):
    def test_delete_assets_delegates_without_remapping(self) -> None:
        module = _load_delete_module(self)
        sentinel = ToolResponse(
            True,
            Severity.INFO,
            "SENTINEL_DELETE",
            "delegated",
            {"ok": True},
        )
        orch = Phase1Orchestrator.default(Path.cwd())

        with patch.object(module, "delete_assets", return_value=sentinel) as delegate:
            response = orch.delete_assets(
                ["Assets/Foo.prefab"],
                scope="Assets/Scope",
                dry_run=False,
                confirm=True,
                change_reason="remove obsolete asset",
            )

        self.assertIs(sentinel, response)
        delegate.assert_called_once_with(
            orch,
            ["Assets/Foo.prefab"],
            scope="Assets/Scope",
            dry_run=False,
            confirm=True,
            change_reason="remove obsolete asset",
        )
