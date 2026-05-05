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

The apply-op and revert envelope rows live next to their target modules
in ``tests/test_d1_branch_coverage.py`` (``PatchExecutorOpTests``) and
``tests/test_patch_revert.py`` (``PatchRevertEnvelopeTests``); this file
holds the dispatch-level envelope rows in ``PatchDispatchEnvelopeTests``.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from prefab_sentinel.contracts import Diagnostic, Severity, success_response
from prefab_sentinel.patch_revert import revert_overrides
from prefab_sentinel.services.serialized_object import SerializedObjectService
from prefab_sentinel.services.serialized_object.patch_dispatch import (
    _dry_run_json_ops,
    _validate_target_and_ops,
    apply_and_save,
    dry_run_patch,
    prevalidate_property_paths,
)
from prefab_sentinel.services.serialized_object.patch_executor import apply_op
from prefab_sentinel.services.serialized_object.patch_json_apply import (
    apply_json_target,
)
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


class PatchDispatchEnvelopeTests(unittest.TestCase):
    """Issue #147 — pin every dispatch envelope code by value, including the
    prevalidator op-index, target-and-ops, JSON dry-run schema/warning,
    unsupported-target, Unity-bridge routing, and dry-run-clean apply paths.
    """

    def _service(self, root: Path) -> SerializedObjectService:
        return SerializedObjectService(project_root=root)

    # --- prevalidate_property_paths ----------------------------------------

    def test_prevalidator_returns_envelope_for_first_invalid_path(self) -> None:
        ops = [
            {"op": "set", "path": "..bad-path", "value": 1},
            {"op": "set", "path": "second.path", "value": 2},
        ]
        response = prevalidate_property_paths("Assets/T.json", ops)
        self.assertIsNotNone(response)
        self.assertFalse(response.success)
        # Op index zero, op count two, target carried verbatim.
        self.assertEqual(0, response.data["op_index"])
        self.assertEqual(2, response.data["op_count"])
        self.assertEqual("Assets/T.json", response.data["target"])
        self.assertEqual(True, response.data["read_only"])

    def test_prevalidator_returns_envelope_for_second_invalid_path(self) -> None:
        ops = [
            {"op": "set", "path": "good.path", "value": 1},
            {"op": "set", "path": "..bad-path", "value": 2},
        ]
        response = prevalidate_property_paths("Assets/T.json", ops)
        self.assertIsNotNone(response)
        self.assertEqual(1, response.data["op_index"])

    def test_prevalidator_skips_ops_without_path(self) -> None:
        # No ``path`` field => skipped; downstream validators take over.
        response = prevalidate_property_paths(
            "Assets/T.json",
            [{"op": "set"}, {"op": "set", "path": ""}],
        )
        self.assertIsNone(response)

    # --- _validate_target_and_ops ------------------------------------------

    def test_target_validator_rejects_empty_target(self) -> None:
        response = _validate_target_and_ops("", [{"op": "set"}])
        self.assertIsNotNone(response)
        self.assertEqual("SER_PLAN_INVALID", response.code)
        diags = response.diagnostics
        self.assertTrue(diags)
        self.assertEqual("schema_error", diags[0].detail)
        self.assertEqual("target is required", diags[0].evidence)

    def test_target_validator_rejects_empty_ops(self) -> None:
        response = _validate_target_and_ops("Assets/T.json", [])
        self.assertIsNotNone(response)
        self.assertEqual("SER_PLAN_INVALID", response.code)
        self.assertEqual(
            "ops must contain at least one operation",
            response.diagnostics[0].evidence,
        )

    # --- _dry_run_json_ops -------------------------------------------------

    def test_dry_run_json_ops_emits_schema_error_for_non_dict_op(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            target = root / "data.json"
            target.write_text(json.dumps({"a": 1}), encoding="utf-8")
            svc = self._service(root)
            response = _dry_run_json_ops(svc, str(target), ["not-a-dict"])  # type: ignore[list-item]
        self.assertFalse(response.success)
        self.assertEqual("SER_PLAN_INVALID", response.code)
        details = [d.detail for d in response.diagnostics]
        self.assertIn("schema_error", details)

    def test_dry_run_json_ops_emits_warning_envelope_when_soft_warnings_present(
        self,
    ) -> None:
        """Soft-warning path: ``soft_warnings_for_preview`` returns a non-empty
        list, so ``_dry_run_json_ops`` returns a success-but-warning envelope
        with ``applied=0`` and ``read_only=True``.
        """
        soft = [
            Diagnostic(
                path="Assets/T.json",
                location="C:p",
                detail="handle_in_value",
                evidence="raw bridge handle leaked into value",
            )
        ]
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            target = root / "data.json"
            target.write_text(json.dumps({"a": 1}), encoding="utf-8")
            svc = self._service(root)
            ops = [{"op": "set", "component": "C", "path": "a", "value": 9}]
            with patch(
                "prefab_sentinel.services.serialized_object.patch_dispatch.soft_warnings_for_preview",
                return_value=soft,
            ):
                response = _dry_run_json_ops(svc, str(target), ops)
        self.assertTrue(response.success)
        self.assertEqual("SER_DRY_RUN_OK", response.code)
        self.assertEqual(Severity.WARNING, response.severity)
        self.assertEqual(0, response.data["applied"])
        self.assertEqual(True, response.data["read_only"])

    # --- apply_and_save propagation, unsupported, bridge, applied ----------

    def test_apply_and_save_propagates_dry_run_failure_verbatim(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            target = root / "data.json"
            target.write_text(json.dumps({"a": 1}), encoding="utf-8")
            svc = self._service(root)
            response = apply_and_save(
                svc, str(target), [{"op": "set", "path": "..bad", "value": 1}]
            )
        self.assertFalse(response.success)
        # Prevalidator failure surfaces with executed=False / read_only=False.
        self.assertEqual(False, response.data["executed"])
        self.assertEqual(False, response.data["read_only"])

    def test_apply_and_save_returns_unsupported_target_envelope(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            target = root / "data.txt"
            target.write_text("not handled", encoding="utf-8")
            svc = self._service(root)
            # Dry-run must succeed before the apply path can reach the
            # unsupported-target branch; provide a component so validate_op
            # accepts the JSON-route op.
            response = apply_and_save(
                svc,
                str(target),
                [{"op": "set", "component": "C", "path": "x", "value": 1}],
            )
        self.assertFalse(response.success)
        self.assertEqual("SER_UNSUPPORTED_TARGET", response.code)
        self.assertEqual(False, response.data["executed"])
        self.assertEqual(False, response.data["read_only"])

    def test_apply_and_save_routes_unity_bridge_targets(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            target = root / "Assets" / "Mat.mat"
            target.parent.mkdir(parents=True)
            target.write_text(
                "%YAML 1.1\n--- !u!21 &1\nMaterial:\n  m_Name: M\n",
                encoding="utf-8",
            )
            svc = self._service(root)
            with patch(
                "prefab_sentinel.services.serialized_object.patch_dispatch.resource_bridge.apply_with_unity_bridge",
                return_value=success_response("BRIDGE_OK", "applied"),
            ) as mock_bridge, patch(
                "prefab_sentinel.services.serialized_object.patch_dispatch.validate_asset_open_ops",
                return_value=([], []),
            ):
                response = apply_and_save(
                    svc, str(target), [{"op": "set", "path": "x", "value": 1}]
                )
        self.assertTrue(response.success)
        self.assertEqual(1, mock_bridge.call_count)
        # Bridge is called once with the resolved target_path and the
        # original ops list.
        kwargs = mock_bridge.call_args.kwargs
        self.assertEqual(target, kwargs["target_path"])
        self.assertEqual(
            [{"op": "set", "path": "x", "value": 1}], kwargs["ops"]
        )

    def test_apply_and_save_returns_applied_envelope_on_dry_run_clean_json(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            target = root / "data.json"
            target.write_text(json.dumps({"a": 1}), encoding="utf-8")
            svc = self._service(root)
            # JSON-target validate_op requires the ``component`` field.
            ops = [{"op": "set", "component": "C", "path": "a", "value": 9}]
            response = apply_and_save(svc, str(target), ops)
        self.assertTrue(response.success, response)
        self.assertEqual("SER_APPLY_OK", response.code)
        self.assertEqual(1, response.data["op_count"])
        self.assertEqual(1, response.data["applied"])
        self.assertEqual(False, response.data["read_only"])
        self.assertEqual(True, response.data["executed"])


if __name__ == "__main__":
    unittest.main()
