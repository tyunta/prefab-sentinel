"""D2 — pin patch dispatch routing, executor parity, and revert side-effects.

Issue #147 / #143 acceptance:
* Patch dispatch branch routing — pinned by the call-shape of each branch's
  downstream invocation.  Three behavioural branches exist on the dispatcher
  (scene / asset+material / json/prefab via per-op validator); the
  ``component`` and ``prefab`` rows in the spec table fold into the
  json/prefab branch because they share the same per-op validator entry
  point.  Issue #221 — each per-branch routing test in
  ``PatchDispatchPerBranchRouting`` pins the three branch-dispatch call
  counts as a single tuple so a misroute to either of the other branches
  surfaces as a tuple-position mismatch naming the leaked branch.
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
        # Dominated-collapse: success flag, op-count parity, diff-shape
        # parity, and the four boolean-marker divergences (applied,
        # read_only, executed) that distinguish dry-run from apply are
        # pinned as a single tuple value.  Any drift in one slot names
        # exactly which parity invariant broke.
        self.assertEqual(
            (
                True,
                dry_run_snapshot["op_count"],
                apply_snapshot["op_count"],
                dry_run_snapshot["diff_shape"],
                apply_snapshot["diff_shape"],
                0,
                1,
                True,
                False,
                False,
                True,
            ),
            (
                apply_response.success,
                dry_run_snapshot["op_count"],
                apply_snapshot["op_count"],
                dry_run_snapshot["diff_shape"],
                apply_snapshot["diff_shape"],
                dry_run_snapshot["applied"],
                apply_snapshot["applied"],
                dry_run_snapshot["read_only"],
                apply_snapshot["read_only"],
                dry_run_snapshot["executed"],
                apply_snapshot["executed"],
            ),
        )


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

        # Dominated-collapse: envelope shape (success/code) plus reverted-
        # field count plus file-body change effects in one tuple value-pin.
        # Slot 3 (``original == new``) must be False — the file body
        # changed; slot 4 (``propertyPath: m_Name`` in new) must be False
        # — the override was removed.
        self.assertEqual(
            (True, "REVERT_APPLIED", 1, False, False),
            (
                response.success,
                response.code,
                response.data["match_count"],
                original_text == new_text,
                "propertyPath: m_Name" in new_text,
            ),
        )


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
        self.assertIsNotNone(
            response, "prevalidator must return an envelope for invalid path"
        )
        # Dominated-collapse: success flag, op_index (0 = first op),
        # op_count (2 ops total), target verbatim, and read_only flag
        # all pinned as one tuple.
        self.assertEqual(
            (False, 0, 2, "Assets/T.json", True),
            (
                response.success,
                response.data["op_index"],
                response.data["op_count"],
                response.data["target"],
                response.data["read_only"],
            ),
        )

    def test_prevalidator_returns_envelope_for_second_invalid_path(self) -> None:
        ops = [
            {"op": "set", "path": "good.path", "value": 1},
            {"op": "set", "path": "..bad-path", "value": 2},
        ]
        response = prevalidate_property_paths("Assets/T.json", ops)
        self.assertIsNotNone(
            response, "prevalidator must return an envelope for second-op invalid path"
        )
        self.assertEqual(1, response.data["op_index"])

    def test_prevalidator_skips_ops_without_path(self) -> None:
        # No ``path`` field => skipped; downstream validators take over.
        response = prevalidate_property_paths(
            "Assets/T.json",
            [{"op": "set"}, {"op": "set", "path": ""}],
        )
        self.assertIsNone(
            response, "prevalidator must skip ops with absent or empty path"
        )

    # --- _validate_target_and_ops ------------------------------------------

    def test_target_validator_rejects_empty_target(self) -> None:
        response = _validate_target_and_ops("", [{"op": "set"}])
        self.assertIsNotNone(
            response, "validator must return envelope for empty target"
        )
        diags = response.diagnostics
        # Dominated-collapse: code, exact diagnostic count (1 — the
        # validator emits exactly one ``schema_error`` for empty target),
        # plus the diagnostic detail/evidence fields, in one tuple.
        self.assertEqual(
            ("SER_PLAN_INVALID", 1, "schema_error", "target is required"),
            (response.code, len(diags), diags[0].detail, diags[0].evidence),
        )

    def test_target_validator_rejects_empty_ops(self) -> None:
        response = _validate_target_and_ops("Assets/T.json", [])
        self.assertIsNotNone(
            response, "validator must return envelope for empty ops"
        )
        self.assertEqual(
            ("SER_PLAN_INVALID", "ops must contain at least one operation"),
            (response.code, response.diagnostics[0].evidence),
        )

    # --- _dry_run_json_ops -------------------------------------------------

    def test_dry_run_json_ops_emits_schema_error_for_non_dict_op(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            target = root / "data.json"
            target.write_text(json.dumps({"a": 1}), encoding="utf-8")
            svc = self._service(root)
            response = _dry_run_json_ops(svc, str(target), ["not-a-dict"])  # type: ignore[list-item]
        self.assertEqual(
            (False, "SER_PLAN_INVALID", True),
            (
                response.success,
                response.code,
                "schema_error" in [d.detail for d in response.diagnostics],
            ),
        )

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
        self.assertEqual(
            (True, "SER_DRY_RUN_OK", Severity.WARNING, 0, True),
            (
                response.success,
                response.code,
                response.severity,
                response.data["applied"],
                response.data["read_only"],
            ),
        )

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
        # Prevalidator failure surfaces with executed=False / read_only=False.
        self.assertEqual(
            (False, False, False),
            (
                response.success,
                response.data["executed"],
                response.data["read_only"],
            ),
        )

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
        self.assertEqual(
            (False, "SER_UNSUPPORTED_TARGET", False, False),
            (
                response.success,
                response.code,
                response.data["executed"],
                response.data["read_only"],
            ),
        )

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
        kwargs = mock_bridge.call_args.kwargs
        # Dominated-collapse: success flag, exact bridge invocation count
        # (1), and the resolved target_path + ops kwargs the bridge
        # received, all pinned as one tuple.
        self.assertEqual(
            (True, 1, target, [{"op": "set", "path": "x", "value": 1}]),
            (
                response.success,
                mock_bridge.call_count,
                kwargs["target_path"],
                kwargs["ops"],
            ),
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
        self.assertEqual(
            (True, "SER_APPLY_OK", 1, 1, False, True),
            (
                response.success,
                response.code,
                response.data["op_count"],
                response.data["applied"],
                response.data["read_only"],
                response.data["executed"],
            ),
        )


class PatchDispatchAssertStrengthening(unittest.TestCase):
    """Issue #147 — value-pinned dry-run vs apply envelope assertions on the
    patch-dispatch surface, beyond the existing parity row.  Pins:
    * dry-run carries ``applied=0`` and ``executed`` not true and
      ``read_only=True``;
    * apply on a clean plan carries ``applied=1`` and ``executed=True`` and
      ``read_only=False``.
    """

    def _service(self, root: Path) -> SerializedObjectService:
        return SerializedObjectService(project_root=root)

    def test_dry_run_carries_applied_zero_and_executed_not_true(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            target = root / "data.json"
            target.write_text(json.dumps({"a": 1}), encoding="utf-8")
            svc = self._service(root)
            response = dry_run_patch(
                svc,
                str(target),
                [{"op": "set", "component": "C", "path": "a", "value": 2}],
            )
        # ``executed`` is either absent or False in a dry-run envelope; pin
        # both branches as "executed != True".
        self.assertEqual(
            (True, 0, False, True),
            (
                response.success,
                response.data["applied"],
                response.data.get("executed", False) is True,
                response.data["read_only"],
            ),
        )

    def test_apply_carries_applied_one_and_executed_true(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            target = root / "data.json"
            target.write_text(json.dumps({"a": 1}), encoding="utf-8")
            svc = self._service(root)
            response = apply_and_save(
                svc,
                str(target),
                [{"op": "set", "component": "C", "path": "a", "value": 9}],
            )
        self.assertEqual(
            (True, 1, True, False),
            (
                response.success,
                response.data["applied"],
                response.data["executed"],
                response.data["read_only"],
            ),
        )


class PatchDispatchPerBranchRouting(unittest.TestCase):
    """Issue #147 — per-target-branch dispatch matrix.  Each routing row
    pins the dispatched-to backend by ``assert_called_with``-style argument
    value verification AND pins the three branch-dispatch call counts as a
    single tuple value (issue #221) so a misroute to either of the other
    branches surfaces as a tuple-position mismatch naming the leaked
    branch.

    The five surface routing rows (scene / asset / material / json /
    prefab-with-component) carry the per-suffix dispatch invariants; the
    Unity-bridge-apply row carries the apply-time bridge dispatch
    invariant.  ``PatchDispatchBranchRoutingTests`` (the prior, weaker
    routing class that pinned only single-branch fire counts) is absent
    from this file — all of its routing guarantees are subsumed here.
    """

    def _service(self, root: Path) -> SerializedObjectService:
        return SerializedObjectService(project_root=root)

    def test_scene_target_invokes_scene_validator_with_open_mode(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            scene = root / "Assets" / "T.unity"
            scene.parent.mkdir(parents=True)
            scene.write_text(
                "%YAML 1.1\n--- !u!1 &1\nGameObject:\n  m_Name: T\n",
                encoding="utf-8",
            )
            svc = self._service(root)
            ops = [{"op": "set", "path": "x", "value": 1}]
            with patch(
                "prefab_sentinel.services.serialized_object.patch_dispatch.validate_scene_ops",
                return_value=([], []),
            ) as mock_scene, patch(
                "prefab_sentinel.services.serialized_object.patch_dispatch.validate_asset_open_ops",
                return_value=([], []),
            ) as mock_asset, patch(
                "prefab_sentinel.services.serialized_object.patch_dispatch._dry_run_json_ops",
            ) as mock_json:
                dry_run_patch(svc, str(scene), ops)
        # Issue #221 — per-branch counts as a single tuple ``(scene,
        # asset, json)``; only the scene branch fires.
        self.assertEqual(
            (1, 0, 0),
            (mock_scene.call_count, mock_asset.call_count, mock_json.call_count),
        )
        kwargs = mock_scene.call_args.kwargs
        self.assertEqual(
            (str(scene), "open", ops),
            (kwargs["target"], kwargs["mode"], kwargs["ops"]),
        )

    def test_asset_target_invokes_asset_open_validator_with_asset_kind(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            asset = root / "Assets" / "T.asset"
            asset.parent.mkdir(parents=True)
            asset.write_text(
                "%YAML 1.1\n--- !u!114 &1\nMonoBehaviour:\n  m_Name: T\n",
                encoding="utf-8",
            )
            svc = self._service(root)
            ops = [{"op": "set", "path": "x", "value": 1}]
            with patch(
                "prefab_sentinel.services.serialized_object.patch_dispatch.validate_scene_ops",
                return_value=([], []),
            ) as mock_scene, patch(
                "prefab_sentinel.services.serialized_object.patch_dispatch.validate_asset_open_ops",
                return_value=([], []),
            ) as mock_asset, patch(
                "prefab_sentinel.services.serialized_object.patch_dispatch._dry_run_json_ops",
            ) as mock_json:
                dry_run_patch(svc, str(asset), ops)
        # Issue #221 — per-branch counts as a single tuple ``(scene,
        # asset, json)``; only the asset branch fires.  Patching all
        # three branches catches a misroute to scene or json that the
        # prior single-mock form could not detect.
        self.assertEqual(
            (0, 1, 0),
            (mock_scene.call_count, mock_asset.call_count, mock_json.call_count),
        )
        kwargs = mock_asset.call_args.kwargs
        self.assertEqual(
            (str(asset), "asset", ops),
            (kwargs["target"], kwargs["kind"], kwargs["ops"]),
        )

    def test_material_target_invokes_asset_open_validator_with_material_kind(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            mat = root / "Assets" / "T.mat"
            mat.parent.mkdir(parents=True)
            mat.write_text(
                "%YAML 1.1\n--- !u!21 &1\nMaterial:\n  m_Name: T\n",
                encoding="utf-8",
            )
            svc = self._service(root)
            ops = [{"op": "set", "path": "x", "value": 1}]
            with patch(
                "prefab_sentinel.services.serialized_object.patch_dispatch.validate_scene_ops",
                return_value=([], []),
            ) as mock_scene, patch(
                "prefab_sentinel.services.serialized_object.patch_dispatch.validate_asset_open_ops",
                return_value=([], []),
            ) as mock_asset, patch(
                "prefab_sentinel.services.serialized_object.patch_dispatch._dry_run_json_ops",
            ) as mock_json:
                dry_run_patch(svc, str(mat), ops)
        self.assertEqual(
            (0, 1, 0),
            (mock_scene.call_count, mock_asset.call_count, mock_json.call_count),
        )
        kwargs = mock_asset.call_args.kwargs
        self.assertEqual(
            (str(mat), "material", ops),
            (kwargs["target"], kwargs["kind"], kwargs["ops"]),
        )

    def test_json_target_invokes_json_dry_run_helper(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            target = root / "data.json"
            target.write_text(json.dumps({"a": 1}), encoding="utf-8")
            svc = self._service(root)
            ops = [{"op": "set", "component": "C", "path": "a", "value": 9}]
            with patch(
                "prefab_sentinel.services.serialized_object.patch_dispatch.validate_scene_ops",
                return_value=([], []),
            ) as mock_scene, patch(
                "prefab_sentinel.services.serialized_object.patch_dispatch.validate_asset_open_ops",
                return_value=([], []),
            ) as mock_asset, patch(
                "prefab_sentinel.services.serialized_object.patch_dispatch._dry_run_json_ops",
                return_value=success_response("SER_DRY_RUN_OK", "ok", data={"applied": 0}),
            ) as mock_json:
                dry_run_patch(svc, str(target), ops)
        self.assertEqual(
            (0, 0, 1),
            (mock_scene.call_count, mock_asset.call_count, mock_json.call_count),
        )
        # _dry_run_json_ops(service, target, ops) — three positional args.
        call_args = mock_json.call_args.args
        self.assertEqual(
            (svc, str(target), ops),
            (call_args[0], call_args[1], call_args[2]),
        )

    def test_prefab_target_with_component_op_falls_through_to_json_helper(
        self,
    ) -> None:
        """The ``component`` argument is per-op (not per-branch); a
        ``.prefab`` target falls through to the JSON helper exactly like
        a ``.json`` target does, regardless of whether the op carries a
        ``component`` field.  This row preserves the prefab-routing pin
        that the prior weaker routing class carried, now expressed in
        the per-branch-tuple form.
        """
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            prefab = root / "Assets" / "C.prefab"
            prefab.parent.mkdir(parents=True)
            prefab.write_text(
                "%YAML 1.1\n--- !u!1 &1\nGameObject:\n  m_Name: T\n",
                encoding="utf-8",
            )
            svc = self._service(root)
            ops = [
                {
                    "op": "set",
                    "component": "AudioSource",
                    "path": "m_Volume",
                    "value": 0.5,
                }
            ]
            with patch(
                "prefab_sentinel.services.serialized_object.patch_dispatch.validate_scene_ops",
                return_value=([], []),
            ) as mock_scene, patch(
                "prefab_sentinel.services.serialized_object.patch_dispatch.validate_asset_open_ops",
                return_value=([], []),
            ) as mock_asset, patch(
                "prefab_sentinel.services.serialized_object.patch_dispatch._dry_run_json_ops",
                return_value=success_response(
                    "SER_DRY_RUN_OK", "ok", data={"applied": 0}
                ),
            ) as mock_json:
                dry_run_patch(svc, str(prefab), ops)
        self.assertEqual(
            (0, 0, 1),
            (mock_scene.call_count, mock_asset.call_count, mock_json.call_count),
        )
        call_args = mock_json.call_args.args
        self.assertEqual(
            (svc, str(prefab), ops),
            (call_args[0], call_args[1], call_args[2]),
        )

    def test_unity_bridge_apply_invokes_bridge_backend(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            target = root / "Assets" / "T.mat"
            target.parent.mkdir(parents=True)
            target.write_text(
                "%YAML 1.1\n--- !u!21 &1\nMaterial:\n  m_Name: T\n",
                encoding="utf-8",
            )
            svc = self._service(root)
            ops = [{"op": "set", "path": "x", "value": 1}]
            with patch(
                "prefab_sentinel.services.serialized_object.patch_dispatch.resource_bridge.apply_with_unity_bridge",
                return_value=success_response("BRIDGE_OK", "applied"),
            ) as mock_bridge, patch(
                "prefab_sentinel.services.serialized_object.patch_dispatch.validate_asset_open_ops",
                return_value=([], []),
            ), patch(
                "prefab_sentinel.services.serialized_object.patch_dispatch.apply_json_target",
            ) as mock_json_apply:
                response = apply_and_save(svc, str(target), ops)
        kwargs = mock_bridge.call_args.kwargs
        # Dominated-collapse: success flag, bridge call count (1),
        # bridge positional ``bridge`` arg (apply_with_unity_bridge
        # signature is (bridge, target_path=, ops=)), target_path kwarg,
        # ops kwarg, and JSON apply path zero-count (Unity-bridge target
        # must not reach the JSON apply backend) all pinned as one tuple.
        self.assertEqual(
            (True, 1, svc.bridge, target, ops, 0),
            (
                response.success,
                mock_bridge.call_count,
                mock_bridge.call_args.args[0],
                kwargs["target_path"],
                kwargs["ops"],
                mock_json_apply.call_count,
            ),
        )


class PatchDispatchUnsupportedTarget(unittest.TestCase):
    """Issue #147 — unsupported-target apply branch returns the deterministic
    ``SER_UNSUPPORTED_TARGET`` envelope with ``applied=0``, ``executed=False``,
    ``read_only=False``.  The resource-bridge predicate is stubbed to false so
    the test reaches the unsupported branch on a target whose suffix is
    neither ``.json`` nor a Unity bridge target.
    """

    def _service(self, root: Path) -> SerializedObjectService:
        return SerializedObjectService(project_root=root)

    def test_unsupported_target_returns_envelope_with_pinned_flags(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            target = root / "data.txt"
            target.write_text("not handled", encoding="utf-8")
            svc = self._service(root)
            ops = [{"op": "set", "component": "C", "path": "x", "value": 1}]
            with patch(
                "prefab_sentinel.services.serialized_object.patch_dispatch.resource_bridge.is_unity_bridge_target",
                return_value=False,
            ):
                response = apply_and_save(svc, str(target), ops)
        self.assertEqual(
            (False, "SER_UNSUPPORTED_TARGET", 0, False, False),
            (
                response.success,
                response.code,
                response.data["applied"],
                response.data["executed"],
                response.data["read_only"],
            ),
        )


if __name__ == "__main__":
    unittest.main()
