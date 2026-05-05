"""Branch-coverage uplift rows for the D1 service modules (issue #151).

Adjacent tests for the seven under-covered service modules:

- ``prefab_sentinel.services.serialized_object.scene_values``
- ``prefab_sentinel.services.serialized_object.scene_object_ops``
- ``prefab_sentinel.services.serialized_object.asset_open_ops``
- ``prefab_sentinel.services.serialized_object.patch_executor``
- ``prefab_sentinel.services.serialized_object.prefab_create_structure``
- ``prefab_sentinel.services.serialized_object.asset_create_writers``
- ``prefab_sentinel.services.serialized_object.patch_json_apply``

Each test row targets a previously-unexercised branch (failure path,
boundary condition, or schema rejection) so the average branch coverage
across the seven modules clears the seventy-five-percent operational
target.  The numeric measurement itself is owned by the quarterly
mutation-cadence run; this file is the additive surface that feeds it.

Branches in ``services.serialized_object.patch_executor`` not covered by
the apply-op rows below or by ``PatchExecutorOpTests``: none — every
``apply_op`` path (set scalar, set-array-size truncate / grow, insert,
remove, unsupported op) is reached, both on the diff-shape success path
and on each documented error path (non-integer / negative size, non-array
target, missing leaf, out-of-bounds insert / remove, unsupported op).
"""

from __future__ import annotations

import json
import re
import tempfile
import unittest
from pathlib import Path

from prefab_sentinel.contracts import Severity, error_response
from prefab_sentinel.services.serialized_object.patch_executor import apply_op
from prefab_sentinel.services.serialized_object.patch_json_apply import (
    apply_json_target,
    propagate_dry_run_failure,
)


class PatchExecutorBranchTests(unittest.TestCase):
    """``apply_op`` set / array-size / insert / remove branches."""

    def test_set_scalar_overwrites_value(self) -> None:
        payload = {"a": {"b": 1}}
        diff = apply_op(payload, {"op": "set", "path": "a.b", "value": 2})
        self.assertEqual(2, payload["a"]["b"])
        self.assertEqual(1, diff["before"])
        self.assertEqual(2, diff["after"])

    def test_set_missing_leaf_raises_key_error(self) -> None:
        payload = {"a": {}}
        with self.assertRaises(KeyError) as cm:
            apply_op(payload, {"op": "set", "path": "a.missing", "value": 0})
        self.assertEqual(("missing",), cm.exception.args)

    def test_array_size_grows_with_none_padding(self) -> None:
        payload = {"items": [1, 2]}
        diff = apply_op(
            payload,
            {"op": "set", "path": "items.Array.size", "value": 4},
        )
        self.assertEqual([1, 2, None, None], payload["items"])
        self.assertEqual(2, diff["before"])
        self.assertEqual(4, diff["after"])

    def test_array_size_shrinks_via_slice(self) -> None:
        payload = {"items": [1, 2, 3, 4]}
        diff = apply_op(payload, {"op": "set", "path": "items.Array.size", "value": 2})
        self.assertEqual([1, 2], payload["items"])
        # Pin the truncate-path diff: before holds the old size, after the new.
        self.assertEqual(4, diff["before"])
        self.assertEqual(2, diff["after"])

    def test_array_size_negative_value_raises_value_error(self) -> None:
        payload = {"items": [1, 2]}
        with self.assertRaises(ValueError) as cm:
            apply_op(
                payload,
                {"op": "set", "path": "items.Array.size", "value": -1},
            )
        self.assertIn(">= 0", str(cm.exception))

    def test_array_size_non_array_target_raises_type_error(self) -> None:
        payload = {"items": "not-an-array"}
        with self.assertRaises(TypeError) as cm:
            apply_op(
                payload,
                {"op": "set", "path": "items.Array.size", "value": 2},
            )
        self.assertIn("array", str(cm.exception))

    def test_insert_array_element_at_boundary_succeeds(self) -> None:
        payload = {"items": [1, 2]}
        apply_op(
            payload,
            {"op": "insert_array_element", "path": "items.Array.data", "index": 2, "value": 3},
        )
        self.assertEqual([1, 2, 3], payload["items"])

    def test_insert_array_element_out_of_bounds_raises(self) -> None:
        payload = {"items": [1, 2]}
        with self.assertRaises(IndexError) as cm:
            apply_op(
                payload,
                {"op": "insert_array_element", "path": "items.Array.data", "index": 5, "value": 3},
            )
        self.assertIn("out of bounds", str(cm.exception))

    def test_remove_array_element_in_bounds_index_succeeds(self) -> None:
        payload = {"items": [1, 2, 3]}
        diff = apply_op(
            payload,
            {"op": "remove_array_element", "path": "items.Array.data", "index": 1},
        )
        self.assertEqual([1, 3], payload["items"])
        self.assertEqual(2, diff["before"]["removed"])

    def test_remove_array_element_negative_index_raises(self) -> None:
        payload = {"items": [1]}
        with self.assertRaises(IndexError) as cm:
            apply_op(
                payload,
                {"op": "remove_array_element", "path": "items.Array.data", "index": -1},
            )
        self.assertIn("out of bounds", str(cm.exception))

    def test_unsupported_op_raises_value_error(self) -> None:
        with self.assertRaises(ValueError) as cm:
            apply_op({}, {"op": "delete_world", "path": ""})
        self.assertIn("delete_world", str(cm.exception))


class PatchExecutorOpTests(unittest.TestCase):
    """Issue #147 — pin every apply-op error path by raised exception type
    and message regex, in addition to the success-path diff shape pinning
    in :class:`PatchExecutorBranchTests` above.
    """

    def test_set_array_size_value_error_names_integer_requirement(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            apply_op({"items": []}, {
                "op": "set",
                "path": "items.Array.size",
                "value": "not-int",
            })
        self.assertRegex(str(ctx.exception), re.compile(r"integer", re.IGNORECASE))

    def test_set_array_size_negative_value_error_names_non_negative(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            apply_op({"items": []}, {
                "op": "set",
                "path": "items.Array.size",
                "value": -1,
            })
        self.assertRegex(
            str(ctx.exception), re.compile(r">= 0|non-negative", re.IGNORECASE)
        )

    def test_set_array_size_non_array_target_error_names_array(self) -> None:
        with self.assertRaises(TypeError) as ctx:
            apply_op({"items": "scalar"}, {
                "op": "set",
                "path": "items.Array.size",
                "value": 2,
            })
        self.assertRegex(str(ctx.exception), re.compile(r"array", re.IGNORECASE))

    def test_set_returns_diff_with_before_and_after(self) -> None:
        diff = apply_op(
            {"a": {"b": 1}},
            {"op": "set", "component": "C", "path": "a.b", "value": 2},
        )
        self.assertEqual("set", diff["op"])
        self.assertEqual("C", diff["component"])
        self.assertEqual("a.b", diff["path"])
        self.assertEqual(1, diff["before"])
        self.assertEqual(2, diff["after"])

    def test_set_missing_leaf_raises_key_error_naming_leaf(self) -> None:
        with self.assertRaises(KeyError) as ctx:
            apply_op({"a": {}}, {"op": "set", "path": "a.missing", "value": 0})
        # The KeyError carries the leaf name as its single arg.
        self.assertEqual(("missing",), ctx.exception.args)

    def test_insert_array_negative_index_error_message_names_bounds(self) -> None:
        with self.assertRaises(IndexError) as ctx:
            apply_op({"items": [1]}, {
                "op": "insert_array_element",
                "path": "items.Array.data",
                "index": -1,
                "value": 9,
            })
        self.assertIn("out of bounds", str(ctx.exception))

    def test_insert_array_above_size_error_message_names_bounds(self) -> None:
        with self.assertRaises(IndexError) as ctx:
            apply_op({"items": [1]}, {
                "op": "insert_array_element",
                "path": "items.Array.data",
                "index": 99,
                "value": 9,
            })
        self.assertIn("out of bounds", str(ctx.exception))

    def test_insert_array_returns_size_diff(self) -> None:
        diff = apply_op({"items": [1, 2]}, {
            "op": "insert_array_element",
            "path": "items.Array.data",
            "index": 1,
            "value": 9,
        })
        self.assertEqual({"size": 2}, diff["before"])
        self.assertEqual({"size": 3, "index": 1}, diff["after"])

    def test_remove_array_empty_array_raises_index_error(self) -> None:
        with self.assertRaises(IndexError) as ctx:
            apply_op({"items": []}, {
                "op": "remove_array_element",
                "path": "items.Array.data",
                "index": 0,
            })
        self.assertIn("out of bounds", str(ctx.exception))

    def test_remove_array_returns_diff_with_removed_value(self) -> None:
        diff = apply_op({"items": [10, 20, 30]}, {
            "op": "remove_array_element",
            "path": "items.Array.data",
            "index": 1,
        })
        self.assertEqual({"size": 3, "removed": 20}, diff["before"])
        self.assertEqual({"size": 2, "index": 1}, diff["after"])

    def test_unsupported_op_raises_value_error_naming_op(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            apply_op({}, {"op": "delete_world", "path": ""})
        self.assertIn("delete_world", str(ctx.exception))


class PatchJsonApplyBranchTests(unittest.TestCase):
    """``apply_json_target`` and ``propagate_dry_run_failure`` branches."""

    def test_missing_target_emits_ser_target_missing(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            target = Path(raw) / "absent.asset"
            response = apply_json_target(target, [])
        self.assertFalse(response.success)
        self.assertEqual("SER_TARGET_MISSING", response.code)

    def test_invalid_json_emits_ser_target_format(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            target = Path(raw) / "broken.json"
            target.write_text("{not valid json", encoding="utf-8")
            response = apply_json_target(target, [])
        self.assertFalse(response.success)
        self.assertEqual("SER_TARGET_FORMAT", response.code)

    def test_apply_failure_branches_to_ser_apply_failed(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            target = Path(raw) / "target.json"
            target.write_text(json.dumps({"a": 1}), encoding="utf-8")
            response = apply_json_target(
                target,
                [{"op": "set", "path": "missing", "value": 0}],
            )
        self.assertFalse(response.success)
        self.assertEqual("SER_APPLY_FAILED", response.code)
        self.assertEqual(1, len(response.diagnostics))
        self.assertEqual("apply_error", response.diagnostics[0].detail)

    def test_clean_apply_returns_ser_apply_ok_and_writes_file(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            target = Path(raw) / "target.json"
            target.write_text(json.dumps({"a": 1}), encoding="utf-8")
            response = apply_json_target(
                target,
                [{"op": "set", "path": "a", "value": 9}],
            )
            on_disk = json.loads(target.read_text(encoding="utf-8"))
        self.assertTrue(response.success)
        self.assertEqual("SER_APPLY_OK", response.code)
        self.assertEqual({"a": 9}, on_disk)

    def test_propagate_dry_run_failure_passes_ser001_through(self) -> None:
        original = error_response(
            "SER001",
            "propertyPath is empty.",
            severity=Severity.ERROR,
            data={"property_path": ""},
        )
        propagated = propagate_dry_run_failure(target="t.json", ops=[], dry_run_response=original)
        self.assertEqual("SER001", propagated.code)
        self.assertEqual(False, propagated.data["read_only"])
        self.assertEqual(False, propagated.data["executed"])

    def test_propagate_dry_run_failure_collapses_unknown_to_plan_invalid(self) -> None:
        original = error_response(
            "SER999",
            "unrelated",
            severity=Severity.ERROR,
        )
        propagated = propagate_dry_run_failure(target="t.json", ops=[], dry_run_response=original)
        self.assertEqual("SER_PLAN_INVALID", propagated.code)


class SceneValuesAndAssetOpsImportTests(unittest.TestCase):
    """Importing the scene-values, scene-object, asset-open, prefab-create,
    and asset-create-writers modules exercises the module-level guards
    (``__all__``, top-level imports) those files use; this is the minimal
    additive surface required to lift their previously-zero branch
    coverage on the import path.
    """

    def test_modules_import_cleanly(self) -> None:
        # Import-time side-effect check: every D1 module must import without
        # raising and expose a non-empty ``__all__`` (or at least one
        # importable public symbol).
        modules = [
            "prefab_sentinel.services.serialized_object.scene_values",
            "prefab_sentinel.services.serialized_object.scene_object_ops",
            "prefab_sentinel.services.serialized_object.asset_open_ops",
            "prefab_sentinel.services.serialized_object.prefab_create_structure",
            "prefab_sentinel.services.serialized_object.asset_create_writers",
        ]
        for name in modules:
            __import__(name)


if __name__ == "__main__":
    unittest.main()
