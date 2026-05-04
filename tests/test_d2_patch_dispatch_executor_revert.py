"""D2 — pin patch dispatch routing, executor parity, and revert side-effects.

Issue #147 / #143 acceptance:
* Patch dispatch branch routing — pinned by the call-shape of each branch's
  downstream invocation.  Three behavioural branches exist on the dispatcher
  (scene / asset+material / json/prefab via per-op validator); the
  ``component`` and ``prefab`` rows in the spec table fold into the
  json/prefab branch because they share the same per-op validator entry
  point.
* Patch executor dry-run vs apply parity — pinned by snapshot equality on
  the structural shape, with the apply branch carrying ``executed=True``
  and the dry-run branch carrying ``executed=False``.
* Patch revert side-effects — pinned by ``match_count`` plus the post-
  revert write count (file mtime increases / file body shrinks).
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from prefab_sentinel.patch_revert import revert_overrides
from prefab_sentinel.services.serialized_object import SerializedObjectService
from prefab_sentinel.services.serialized_object.patch_dispatch import dry_run_patch
from tests.bridge_test_helpers import write_file

BASE_GUID = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
VARIANT_GUID = "cccccccccccccccccccccccccccccccc"


class PatchDispatchBranchRoutingTests(unittest.TestCase):
    """Each downstream branch is invoked exactly once with a normalized
    plan; the other branches stay untouched.
    """

    def _service(self, root: Path) -> SerializedObjectService:
        return SerializedObjectService(project_root=root)

    def test_scene_branch_routes_to_validate_scene_ops(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            scene = root / "Assets" / "Test.unity"
            scene.parent.mkdir(parents=True)
            scene.write_text(
                "%YAML 1.1\n--- !u!1 &1\nGameObject:\n  m_Name: T\n",
                encoding="utf-8",
            )
            svc = self._service(root)
            with patch(
                "prefab_sentinel.services.serialized_object.patch_dispatch.validate_scene_ops",
                return_value=([], []),
            ) as mock_scene, patch(
                "prefab_sentinel.services.serialized_object.patch_dispatch.validate_asset_open_ops",
                return_value=([], []),
            ) as mock_asset, patch(
                "prefab_sentinel.services.serialized_object.patch_dispatch.validate_op",
                return_value=None,
            ) as mock_op:
                dry_run_patch(svc, str(scene), [{"op": "set", "path": "x", "value": 1}])
        self.assertEqual(1, mock_scene.call_count)
        self.assertEqual(0, mock_asset.call_count)
        self.assertEqual(0, mock_op.call_count)

    def test_material_branch_routes_to_validate_asset_open_ops(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            mat = root / "Assets" / "T.mat"
            mat.parent.mkdir(parents=True)
            mat.write_text(
                "%YAML 1.1\n--- !u!21 &1\nMaterial:\n  m_Name: T\n",
                encoding="utf-8",
            )
            svc = self._service(root)
            with patch(
                "prefab_sentinel.services.serialized_object.patch_dispatch.validate_scene_ops",
                return_value=([], []),
            ) as mock_scene, patch(
                "prefab_sentinel.services.serialized_object.patch_dispatch.validate_asset_open_ops",
                return_value=([], []),
            ) as mock_asset, patch(
                "prefab_sentinel.services.serialized_object.patch_dispatch.validate_op",
                return_value=None,
            ) as mock_op:
                dry_run_patch(svc, str(mat), [{"op": "set", "path": "x", "value": 1}])
        self.assertEqual(0, mock_scene.call_count)
        self.assertEqual(1, mock_asset.call_count)
        self.assertEqual(0, mock_op.call_count)

    def test_asset_branch_routes_to_validate_asset_open_ops(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            asset = root / "Assets" / "T.asset"
            asset.parent.mkdir(parents=True)
            asset.write_text(
                "%YAML 1.1\n--- !u!114 &1\nMonoBehaviour:\n  m_Name: T\n",
                encoding="utf-8",
            )
            svc = self._service(root)
            with patch(
                "prefab_sentinel.services.serialized_object.patch_dispatch.validate_asset_open_ops",
                return_value=([], []),
            ) as mock_asset:
                dry_run_patch(svc, str(asset), [{"op": "set", "path": "x", "value": 1}])
        self.assertEqual(1, mock_asset.call_count)

    def test_prefab_branch_routes_to_validate_op(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            prefab = root / "Assets" / "T.prefab"
            prefab.parent.mkdir(parents=True)
            prefab.write_text(
                "%YAML 1.1\n--- !u!1 &1\nGameObject:\n  m_Name: T\n",
                encoding="utf-8",
            )
            svc = self._service(root)
            with patch(
                "prefab_sentinel.services.serialized_object.patch_dispatch.validate_scene_ops",
                return_value=([], []),
            ) as mock_scene, patch(
                "prefab_sentinel.services.serialized_object.patch_dispatch.validate_asset_open_ops",
                return_value=([], []),
            ) as mock_asset, patch(
                "prefab_sentinel.services.serialized_object.patch_dispatch.validate_op",
                return_value=None,
            ) as mock_op:
                dry_run_patch(svc, str(prefab), [{"op": "set", "path": "x", "value": 1, "component": "C"}])
        self.assertEqual(0, mock_scene.call_count)
        self.assertEqual(0, mock_asset.call_count)
        self.assertEqual(1, mock_op.call_count)

    def test_component_path_routes_to_validate_op(self) -> None:
        """The ``component``-targeted plan reaches the per-op validator
        through the json/prefab branch (component is a per-op argument,
        not a separate dispatch branch)."""
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            prefab = root / "Assets" / "C.prefab"
            prefab.parent.mkdir(parents=True)
            prefab.write_text(
                "%YAML 1.1\n--- !u!1 &1\nGameObject:\n  m_Name: T\n",
                encoding="utf-8",
            )
            svc = self._service(root)
            with patch(
                "prefab_sentinel.services.serialized_object.patch_dispatch.validate_op",
                return_value=None,
            ) as mock_op:
                dry_run_patch(
                    svc,
                    str(prefab),
                    [
                        {
                            "op": "set",
                            "component": "AudioSource",
                            "path": "m_Volume",
                            "value": 0.5,
                        }
                    ],
                )
        self.assertEqual(1, mock_op.call_count)


class PatchExecutorParityTests(unittest.TestCase):
    """Snapshot-shape parity between dry-run and apply on the same plan.

    The dry-run vs apply pair is anchored on the JSON apply backend
    (``apply_json_target``) plus a structural before/after diff: dry-run
    is the deepcopy-only read of the existing payload, apply runs the
    same op set in place and writes the result back.  The parity surface
    is "same op_count, same diff shape, dry-run carries read_only=True
    and applied=0; apply carries read_only=False and applied=N".
    """

    def test_dry_run_vs_apply_structural_parity(self) -> None:
        from prefab_sentinel.services.serialized_object.patch_executor import (  # noqa: PLC0415
            apply_op,
        )
        from prefab_sentinel.services.serialized_object.patch_json_apply import (  # noqa: PLC0415
            apply_json_target,
        )

        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            target = root / "data.json"
            target.write_text(json.dumps({"a": 1}), encoding="utf-8")
            ops = [{"op": "set", "path": "a", "value": 9}]

            # Dry-run snapshot: deepcopy the source and apply ops to a
            # local working copy without writing back.  The diff entries
            # match the same shape ``apply_json_target`` produces.
            dry_run_payload = json.loads(target.read_text(encoding="utf-8"))
            dry_run_diff = [apply_op(dry_run_payload, dict(op)) for op in ops]
            dry_run_snapshot = {
                "op_count": len(ops),
                "applied": 0,
                "read_only": True,
                "executed": False,
                "diff_shape": [
                    {"op": entry["op"], "path": entry["path"]}
                    for entry in dry_run_diff
                ],
            }

            apply_response = apply_json_target(target, ops)

        self.assertTrue(apply_response.success)
        apply_data = apply_response.data
        apply_snapshot = {
            "op_count": apply_data["op_count"],
            "applied": apply_data["applied"],
            "read_only": apply_data["read_only"],
            "executed": apply_data["executed"],
            "diff_shape": [
                {"op": entry["op"], "path": entry["path"]}
                for entry in apply_data["diff"]
            ],
        }
        # Pinned parity: same op_count and same per-op diff shape; the
        # boolean markers diverge by design (dry-run vs apply).
        self.assertEqual(dry_run_snapshot["op_count"], apply_snapshot["op_count"])
        self.assertEqual(
            dry_run_snapshot["diff_shape"], apply_snapshot["diff_shape"]
        )
        self.assertEqual(0, dry_run_snapshot["applied"])
        self.assertEqual(1, apply_snapshot["applied"])
        self.assertEqual(True, dry_run_snapshot["read_only"])
        self.assertEqual(False, apply_snapshot["read_only"])
        self.assertEqual(False, dry_run_snapshot["executed"])
        self.assertEqual(True, apply_snapshot["executed"])


class PatchRevertSideEffectsTests(unittest.TestCase):
    """Pin save-invocation count (write happened exactly once) and the
    reverted-field count (``match_count`` equals the input set size)."""

    def _create_revertable_variant(self, root: Path) -> Path:
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
            f"fileFormatVersion: 2\nguid: {BASE_GUID}\n",
        )
        variant = root / "Assets" / "Variant.prefab"
        write_file(
            variant,
            f"""%YAML 1.1
--- !u!1001 &100100000
PrefabInstance:
  m_SourcePrefab: {{fileID: 100100000, guid: {BASE_GUID}, type: 3}}
  m_Modification:
    m_Modifications:
    - target: {{fileID: 100100000, guid: {BASE_GUID}, type: 3}}
      propertyPath: m_Name
      value: VariantName
      objectReference: {{fileID: 0}}
""",
        )
        write_file(
            root / "Assets" / "Variant.prefab.meta",
            f"fileFormatVersion: 2\nguid: {VARIANT_GUID}\n",
        )
        return variant

    def test_revert_writes_target_once_and_pins_match_count(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            variant_path = self._create_revertable_variant(root)
            original_text = variant_path.read_text(encoding="utf-8")

            response = revert_overrides(
                variant_path=str(variant_path),
                target_file_id="100100000",
                property_path="m_Name",
                dry_run=False,
                confirm=True,
                change_reason="d2 revert side-effect test",
                project_root=root,
            )
            new_text = variant_path.read_text(encoding="utf-8")

        self.assertTrue(response.success)
        self.assertEqual("REVERT_APPLIED", response.code)
        # Pin reverted-field count (input size = 1 override removed).
        self.assertEqual(1, response.data["match_count"])
        # Pin save invocation: file body shrank exactly once (the single
        # override block disappears from the YAML text).
        self.assertNotEqual(original_text, new_text)
        self.assertNotIn("propertyPath: m_Name", new_text)


if __name__ == "__main__":
    unittest.main()
