from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from typing import cast

from prefab_sentinel.asset_delete import (
    build_delete_plan,
    compute_broken_reference_delta,
)
from prefab_sentinel.services.reference_resolver import ReferenceResolverService
from tests.bridge_test_helpers import write_file

_SCRIPT_GUID = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
_PREFAB_GUID = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
_REFERRER_GUID = "cccccccccccccccccccccccccccccccc"


def _seed_assets_dir(root: Path) -> None:
    (root / "Assets").mkdir(parents=True, exist_ok=True)


def _write_asset_with_meta(root: Path, relative_path: str, guid: str) -> None:
    asset = root / relative_path
    write_file(
        asset,
        "%YAML 1.1\n--- !u!114 &11400000\nMonoBehaviour:\n"
        f"  m_Name: {asset.stem}\n",
    )
    write_file(asset.with_suffix(asset.suffix + ".meta"), f"guid: {guid}\n")


class DeleteAssetPlanTests(unittest.TestCase):
    def test_single_asset_dry_run_lists_meta_and_reference_impact(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            _seed_assets_dir(root)
            _write_asset_with_meta(root, "Assets/Foo.prefab", _PREFAB_GUID)
            write_file(
                root / "Assets" / "Referrer.prefab",
                "%YAML 1.1\n--- !u!114 &1\nMonoBehaviour:\n"
                f"  target: {{fileID: 11400000, guid: {_PREFAB_GUID}, type: 3}}\n",
            )
            write_file(root / "Assets" / "Referrer.prefab.meta", f"guid: {_REFERRER_GUID}\n")
            plan = build_delete_plan(
                ["Assets/Foo.prefab"],
                project_root=root,
                reference_resolver=ReferenceResolverService(project_root=root),
                scope="Assets",
            )

        self.assertEqual(
            (True, "ASSET_DELETE_DRY_RUN"),
            (plan["success"], plan["code"]),
            msg=f"delete plan envelope mismatch: {plan!r}",
        )
        self.assertEqual(
            {
                "asset_path": "Assets/Foo.prefab",
                "meta_path": "Assets/Foo.prefab.meta",
                "asset_exists": True,
                "meta_exists": True,
                "deletable": True,
            },
            {k: plan["data"]["targets"][0][k] for k in (
                "asset_path",
                "meta_path",
                "asset_exists",
                "meta_exists",
                "deletable",
            )},
        )
        self.assertEqual(
            ["Assets/Referrer.prefab"],
            [u["path"] for u in plan["data"]["targets"][0]["reference_impact"]["usages"]],
        )

    def test_default_reference_impact_scope_uses_target_parent_directory(self) -> None:
        class _RecordingResolver:
            def __init__(self) -> None:
                self.where_used_scopes: list[str | None] = []

            def where_used(self, *_args, **kwargs):
                self.where_used_scopes.append(kwargs.get("scope"))
                return type(
                    "_Response",
                    (),
                    {
                        "success": True,
                        "data": {
                            "usages": [],
                            "usage_count": 0,
                            "returned_usages": 0,
                        },
                    },
                )()

            def scan_broken_references(self, **_kwargs):
                return {"success": True, "data": {"read_only": True}}

        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            _seed_assets_dir(root)
            _write_asset_with_meta(
                root,
                "Assets/Editor/PrefabSentinel/Temp.renderTexture",
                _PREFAB_GUID,
            )
            resolver = _RecordingResolver()
            plan = build_delete_plan(
                ["Assets/Editor/PrefabSentinel/Temp.renderTexture"],
                project_root=root,
                reference_resolver=cast(ReferenceResolverService, resolver),
            )

        self.assertEqual((True, "ASSET_DELETE_DRY_RUN"), (plan["success"], plan["code"]))
        self.assertEqual(["Assets/Editor/PrefabSentinel"], resolver.where_used_scopes)

    def test_batch_dry_run_preserves_requested_asset_order(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            _seed_assets_dir(root)
            _write_asset_with_meta(root, "Assets/One.asset", "11111111111111111111111111111111")
            _write_asset_with_meta(root, "Assets/Two.asset", "22222222222222222222222222222222")
            plan = build_delete_plan(
                ["Assets/One.asset", "Assets/Two.asset"],
                project_root=root,
                reference_resolver=ReferenceResolverService(project_root=root),
                scope="Assets",
            )

        self.assertEqual(
            ["Assets/One.asset", "Assets/Two.asset"],
            [t["asset_path"] for t in plan["data"]["targets"]],
        )
        self.assertEqual(
            ["Assets/One.asset.meta", "Assets/Two.asset.meta"],
            [t["meta_path"] for t in plan["data"]["targets"]],
        )

    def test_unsupported_paths_are_rejected_without_deletable_targets(self) -> None:
        cases = [
            "Packages/com.example/file.asset",
            "Library/cache.asset",
            "Library/PackageCache/com.example/file.asset",
            "../outside.asset",
        ]
        for asset_path in cases:
            with self.subTest(asset_path=asset_path):
                with tempfile.TemporaryDirectory() as raw:
                    root = Path(raw)
                    _seed_assets_dir(root)
                    plan = build_delete_plan(
                        [asset_path],
                        project_root=root,
                        reference_resolver=ReferenceResolverService(project_root=root),
                        scope="Assets",
                    )

                self.assertEqual(
                    (False, "ASSET_DELETE_EXTERNAL_PACKAGE_UNSUPPORTED"),
                    (plan["success"], plan["code"]),
                    msg=f"unsupported path result mismatch: {plan!r}",
                )
                self.assertEqual([], plan["data"]["targets"])
                self.assertEqual(asset_path, plan["data"]["rejected_path"])

    def test_missing_asset_is_rejected_without_deletable_targets(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            _seed_assets_dir(root)
            plan = build_delete_plan(
                ["Assets/Missing.prefab"],
                project_root=root,
                reference_resolver=ReferenceResolverService(project_root=root),
                scope="Assets",
            )

        self.assertEqual(
            (False, "ASSET_DELETE_NOT_FOUND"),
            (plan["success"], plan["code"]),
            msg=f"missing target result mismatch: {plan!r}",
        )
        self.assertEqual([], plan["data"]["targets"])
        self.assertEqual("Assets/Missing.prefab", plan["data"]["missing_asset_path"])

    def test_malformed_meta_is_rejected_without_crashing_plan(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            _seed_assets_dir(root)
            asset = root / "Assets" / "Broken.prefab"
            write_file(asset, "%YAML 1.1\n--- !u!1 &1\nGameObject: {}\n")
            asset.with_suffix(asset.suffix + ".meta").write_bytes(b"\xff\xfe")
            plan = build_delete_plan(
                ["Assets/Broken.prefab"],
                project_root=root,
                reference_resolver=ReferenceResolverService(project_root=root),
                scope="Assets",
            )

        self.assertEqual(
            (False, "ASSET_DELETE_META_UNREADABLE"),
            (plan["success"], plan["code"]),
            msg=f"malformed meta result mismatch: {plan!r}",
        )
        self.assertEqual([], plan["data"]["targets"])
        self.assertEqual("Assets/Broken.prefab.meta", plan["data"]["meta_path"])

    def test_pre_delete_scan_failure_is_propagated(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            _seed_assets_dir(root)
            _write_asset_with_meta(root, "Assets/Foo.prefab", _PREFAB_GUID)
            plan = build_delete_plan(
                ["Assets/Foo.prefab"],
                project_root=root,
                reference_resolver=ReferenceResolverService(project_root=root),
                scope="Assets/MissingScope",
            )

        self.assertEqual(
            (False, "REF404"),
            (plan["success"], plan["code"]),
            msg=f"pre-delete scan failure should stop planning: {plan!r}",
        )
        self.assertEqual("Assets/MissingScope", plan["data"]["scope"])

    def test_deterministic_udonsharp_program_asset_is_related_candidate(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            _seed_assets_dir(root)
            _write_asset_with_meta(root, "Assets/MyBehaviour.cs", _SCRIPT_GUID)
            _write_asset_with_meta(root, "Assets/MyBehaviourProgram.asset", "dddddddddddddddddddddddddddddddd")
            write_file(
                root / "Assets" / "MyBehaviourProgram.asset",
                "%YAML 1.1\n--- !u!114 &11400000\nUdonSharpProgramAsset:\n"
                f"  sourceCsScript: {{fileID: 11500000, guid: {_SCRIPT_GUID}, type: 3}}\n",
            )
            plan = build_delete_plan(
                ["Assets/MyBehaviour.cs"],
                project_root=root,
                reference_resolver=ReferenceResolverService(project_root=root),
                scope="Assets",
            )

        self.assertEqual(
            [
                {
                    "asset_path": "Assets/MyBehaviourProgram.asset",
                    "reason": "udonsharp_program_asset",
                    "candidate_status": "deterministic",
                }
            ],
            plan["data"]["related_candidates"],
        )

    def test_unreadable_udonsharp_program_asset_is_skipped(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            _seed_assets_dir(root)
            _write_asset_with_meta(root, "Assets/MyBehaviour.cs", _SCRIPT_GUID)
            (root / "Assets" / "AUnreadableProgram.asset").mkdir()
            _write_asset_with_meta(root, "Assets/ZProgram.asset", "dddddddddddddddddddddddddddddddd")
            write_file(
                root / "Assets" / "ZProgram.asset",
                "%YAML 1.1\n--- !u!114 &11400000\nUdonSharpProgramAsset:\n"
                f"  sourceCsScript: {{fileID: 11500000, guid: {_SCRIPT_GUID}, type: 3}}\n",
            )
            plan = build_delete_plan(
                ["Assets/MyBehaviour.cs"],
                project_root=root,
                reference_resolver=ReferenceResolverService(project_root=root),
                scope="Assets",
            )

        self.assertEqual((True, "ASSET_DELETE_DRY_RUN"), (plan["success"], plan["code"]))
        self.assertEqual(["Assets/ZProgram.asset"], [c["asset_path"] for c in plan["data"]["related_candidates"]])

    def test_ambiguous_udonsharp_program_assets_require_decision(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            _seed_assets_dir(root)
            _write_asset_with_meta(root, "Assets/MyBehaviour.cs", _SCRIPT_GUID)
            for name in ("ProgramA.asset", "ProgramB.asset"):
                _write_asset_with_meta(root, f"Assets/{name}", f"{name[7].lower()}" * 32)
                write_file(
                    root / "Assets" / name,
                    "%YAML 1.1\n--- !u!114 &11400000\nUdonSharpProgramAsset:\n"
                    f"  sourceCsScript: {{fileID: 11500000, guid: {_SCRIPT_GUID}, type: 3}}\n",
                )
            plan = build_delete_plan(
                ["Assets/MyBehaviour.cs"],
                project_root=root,
                reference_resolver=ReferenceResolverService(project_root=root),
                scope="Assets",
            )

        self.assertEqual([], plan["data"]["related_candidates"])
        self.assertEqual(
            ["ASSET_DELETE_DECISION_REQUIRED"],
            [d["code"] for d in plan["diagnostics"]],
        )


class BrokenReferenceDeltaTests(unittest.TestCase):
    def test_equal_scans_report_zero_delta(self) -> None:
        before = {
            "broken_count": 1,
            "categories": {"missing_asset": 1, "missing_local_id": 0},
            "unique_missing_asset_guids": ["aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"],
        }
        after = {
            "broken_count": 1,
            "categories": {"missing_asset": 1, "missing_local_id": 0},
            "unique_missing_asset_guids": ["aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"],
        }

        self.assertEqual(
            {
                "before_broken_count": 1,
                "after_broken_count": 1,
                "broken_count_delta": 0,
                "categories_delta": {"missing_asset": 0, "missing_local_id": 0},
                "new_missing_asset_guids": [],
            },
            compute_broken_reference_delta(before, after),
        )

    def test_increased_missing_guid_scan_reports_positive_delta(self) -> None:
        before = {
            "broken_count": 1,
            "categories": {"missing_asset": 1, "missing_local_id": 0},
            "unique_missing_asset_guids": ["aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"],
        }
        after = {
            "broken_count": 2,
            "categories": {"missing_asset": 2, "missing_local_id": 0},
            "unique_missing_asset_guids": [
                "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
            ],
        }

        delta = compute_broken_reference_delta(before, after)

        self.assertEqual(1, delta["broken_count_delta"])
        self.assertEqual(1, delta["categories_delta"]["missing_asset"])
        self.assertEqual(["bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"], delta["new_missing_asset_guids"])


if __name__ == "__main__":
    unittest.main()
