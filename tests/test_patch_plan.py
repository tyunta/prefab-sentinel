from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from typing import Any

from prefab_sentinel.patch_plan import (
    PLAN_VERSION,
    _infer_resource_kind,
    _normalize_resource,
    _normalize_v1_plan,
    build_bridge_request,
    compute_patch_plan_hmac_sha256,
    compute_patch_plan_sha256,
    count_plan_ops,
    iter_resource_batches,
    load_patch_plan,
    normalize_patch_plan,
)


def _v2_plan(
    *,
    resources: list[dict[str, Any]] | None = None,
    ops: list[dict[str, Any]] | None = None,
    postconditions: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    if resources is None:
        resources = [{"id": "res1", "path": "test.prefab", "kind": "prefab", "mode": "open"}]
    if ops is None:
        ops = [{"resource": "res1", "op": "set", "property_path": "key", "value": "val"}]
    plan: dict[str, Any] = {
        "plan_version": PLAN_VERSION,
        "resources": resources,
        "ops": ops,
    }
    if postconditions is not None:
        plan["postconditions"] = postconditions
    return plan


class InferResourceKindTests(unittest.TestCase):
    def test_prefab(self) -> None:
        self.assertEqual(_infer_resource_kind("Assets/foo.prefab"), "prefab")

    def test_unity_scene(self) -> None:
        self.assertEqual(_infer_resource_kind("Assets/scene.unity"), "scene")

    def test_json(self) -> None:
        self.assertEqual(_infer_resource_kind("config/plan.json"), "json")

    def test_material(self) -> None:
        self.assertEqual(_infer_resource_kind("Assets/mat.mat"), "material")

    def test_asset(self) -> None:
        self.assertEqual(_infer_resource_kind("Assets/data.asset"), "asset")

    def test_animation(self) -> None:
        self.assertEqual(_infer_resource_kind("Assets/clip.anim"), "animation")

    def test_controller(self) -> None:
        self.assertEqual(_infer_resource_kind("Assets/ctrl.controller"), "controller")

    def test_unknown_suffix_defaults_to_asset(self) -> None:
        self.assertEqual(_infer_resource_kind("Assets/file.xyz"), "asset")

    def test_case_insensitive(self) -> None:
        self.assertEqual(_infer_resource_kind("Assets/foo.PREFAB"), "prefab")

    def test_no_suffix_defaults_to_asset(self) -> None:
        self.assertEqual(_infer_resource_kind("Assets/noext"), "asset")


class NormalizeResourceTests(unittest.TestCase):
    def test_valid_resource(self) -> None:
        resource = {"id": "r1", "path": "Assets/a.prefab", "kind": "prefab", "mode": "open"}
        result = _normalize_resource(resource, 0)
        self.assertEqual(result["id"], "r1")
        self.assertEqual(result["kind"], "prefab")
        self.assertEqual(result["mode"], "open")

    def test_non_dict_raises(self) -> None:
        with self.assertRaises(ValueError):
            _normalize_resource("not_a_dict", 0)

    def test_missing_id_raises(self) -> None:
        with self.assertRaises(ValueError):
            _normalize_resource({"path": "a.prefab"}, 0)

    def test_empty_id_raises(self) -> None:
        with self.assertRaises(ValueError):
            _normalize_resource({"id": "  ", "path": "a.prefab"}, 0)

    def test_missing_path_raises(self) -> None:
        with self.assertRaises(ValueError):
            _normalize_resource({"id": "r1"}, 0)

    def test_empty_path_raises(self) -> None:
        with self.assertRaises(ValueError):
            _normalize_resource({"id": "r1", "path": "  "}, 0)

    def test_kind_inferred_from_path(self) -> None:
        resource = {"id": "r1", "path": "Assets/a.unity"}
        result = _normalize_resource(resource, 0)
        self.assertEqual(result["kind"], "scene")

    def test_kind_none_triggers_inference(self) -> None:
        resource = {"id": "r1", "path": "Assets/a.mat", "kind": None}
        result = _normalize_resource(resource, 0)
        self.assertEqual(result["kind"], "material")

    def test_empty_kind_triggers_inference(self) -> None:
        resource = {"id": "r1", "path": "Assets/a.prefab", "kind": "  "}
        result = _normalize_resource(resource, 0)
        self.assertEqual(result["kind"], "prefab")

    def test_mode_defaults_to_open(self) -> None:
        resource = {"id": "r1", "path": "Assets/a.prefab"}
        result = _normalize_resource(resource, 0)
        self.assertEqual(result["mode"], "open")

    def test_empty_mode_raises(self) -> None:
        with self.assertRaises(ValueError):
            _normalize_resource({"id": "r1", "path": "a.prefab", "mode": "  "}, 0)

    def test_strips_whitespace(self) -> None:
        resource = {"id": " r1 ", "path": " Assets/a.prefab "}
        result = _normalize_resource(resource, 0)
        self.assertEqual(result["id"], "r1")
        self.assertEqual(result["path"], "Assets/a.prefab")

    def test_deepcopy_isolation(self) -> None:
        resource: dict[str, Any] = {"id": "r1", "path": "a.prefab", "extra": {"nested": 1}}
        result = _normalize_resource(resource, 0)
        result["extra"]["nested"] = 99
        self.assertEqual(resource["extra"]["nested"], 1)


class NormalizeV1PlanTests(unittest.TestCase):
    def test_basic_v1_conversion(self) -> None:
        v1 = {
            "target": "Assets/a.prefab",
            "ops": [{"op": "set", "property_path": "key", "value": "val"}],
        }
        result = _normalize_v1_plan(v1)
        self.assertEqual(result["plan_version"], PLAN_VERSION)
        self.assertEqual(len(result["resources"]), 1)
        self.assertEqual(result["resources"][0]["id"], "target")
        self.assertEqual(result["resources"][0]["kind"], "prefab")
        self.assertEqual(result["resources"][0]["path"], "Assets/a.prefab")
        self.assertEqual(len(result["ops"]), 1)
        self.assertEqual(result["ops"][0]["resource"], "target")
        self.assertIn("op", result["ops"][0])
        self.assertEqual(result["postconditions"], [])

    def test_missing_target_raises(self) -> None:
        with self.assertRaises(ValueError):
            _normalize_v1_plan({"ops": []})

    def test_empty_target_raises(self) -> None:
        with self.assertRaises(ValueError):
            _normalize_v1_plan({"target": "  ", "ops": []})

    def test_missing_ops_raises(self) -> None:
        with self.assertRaises(ValueError):
            _normalize_v1_plan({"target": "Assets/a.prefab"})

    def test_non_list_ops_raises(self) -> None:
        with self.assertRaises(ValueError):
            _normalize_v1_plan({"target": "Assets/a.prefab", "ops": "notlist"})

    def test_strips_target_whitespace(self) -> None:
        result = _normalize_v1_plan({"target": "  Assets/a.prefab  ", "ops": []})
        self.assertEqual(result["resources"][0]["path"], "Assets/a.prefab")


class NormalizePatchPlanTests(unittest.TestCase):
    def test_v2_plan(self) -> None:
        plan = _v2_plan()
        result = normalize_patch_plan(plan)
        self.assertEqual(result["plan_version"], PLAN_VERSION)
        self.assertEqual(len(result["resources"]), 1)
        self.assertEqual(len(result["ops"]), 1)

    def test_v1_plan_auto_converts(self) -> None:
        v1 = {
            "target": "Assets/a.prefab",
            "ops": [{"op": "set", "property_path": "key", "value": "val"}],
        }
        result = normalize_patch_plan(v1)
        self.assertEqual(result["plan_version"], PLAN_VERSION)
        self.assertEqual(result["resources"][0]["id"], "target")

    def test_wrong_version_raises(self) -> None:
        with self.assertRaises(ValueError):
            normalize_patch_plan({"plan_version": 99})

    def test_non_dict_root_raises(self) -> None:
        with self.assertRaises(ValueError):
            normalize_patch_plan("not_a_dict")  # type: ignore[arg-type]

    def test_empty_resources_raises(self) -> None:
        with self.assertRaises(ValueError):
            normalize_patch_plan({"plan_version": PLAN_VERSION, "resources": []})

    def test_non_list_resources_raises(self) -> None:
        with self.assertRaises(ValueError):
            normalize_patch_plan({"plan_version": PLAN_VERSION, "resources": "bad"})

    def test_non_list_ops_raises(self) -> None:
        plan = _v2_plan()
        plan["ops"] = "not_a_list"
        with self.assertRaises(ValueError):
            normalize_patch_plan(plan)

    def test_non_list_postconditions_raises(self) -> None:
        plan = _v2_plan()
        plan["postconditions"] = "not_a_list"
        with self.assertRaises(ValueError):
            normalize_patch_plan(plan)

    def test_duplicate_resource_id_raises(self) -> None:
        plan = _v2_plan(
            resources=[
                {"id": "dup", "path": "a.prefab"},
                {"id": "dup", "path": "b.prefab"},
            ],
            ops=[],
        )
        with self.assertRaises(ValueError):
            normalize_patch_plan(plan)

    def test_unknown_resource_ref_raises(self) -> None:
        plan = _v2_plan(
            resources=[{"id": "res1", "path": "a.prefab"}],
            ops=[{"resource": "unknown", "op": "set"}],
        )
        with self.assertRaises(ValueError):
            normalize_patch_plan(plan)

    def test_non_dict_op_raises(self) -> None:
        plan = _v2_plan()
        plan["ops"] = ["not_a_dict"]
        with self.assertRaises(ValueError):
            normalize_patch_plan(plan)

    def test_empty_resource_ref_in_op_raises(self) -> None:
        plan = _v2_plan(ops=[{"resource": "  ", "op": "set"}])
        with self.assertRaises(ValueError):
            normalize_patch_plan(plan)

    def test_non_dict_postcondition_raises(self) -> None:
        plan = _v2_plan(
            resources=[{"id": "r1", "path": "a.prefab"}],
            ops=[],
            postconditions=["not_a_dict"],
        )
        with self.assertRaises(ValueError):
            normalize_patch_plan(plan)

    def test_postcondition_missing_type_raises(self) -> None:
        plan = _v2_plan(postconditions=[{"resource": "res1"}])
        with self.assertRaises(ValueError):
            normalize_patch_plan(plan)

    def test_postcondition_empty_type_raises(self) -> None:
        plan = _v2_plan(postconditions=[{"type": "  "}])
        with self.assertRaises(ValueError):
            normalize_patch_plan(plan)

    def test_postcondition_type_stripped(self) -> None:
        plan = _v2_plan(postconditions=[{"type": " asset_exists "}])
        result = normalize_patch_plan(plan)
        self.assertEqual(result["postconditions"][0]["type"], "asset_exists")

    def test_op_resource_stripped(self) -> None:
        plan = _v2_plan(ops=[{"resource": " res1 ", "op": "set"}])
        result = normalize_patch_plan(plan)
        self.assertEqual(result["ops"][0]["resource"], "res1")

    def test_postconditions_default_to_empty_list(self) -> None:
        plan = _v2_plan(ops=[])
        # _v2_plan with postconditions=None (default) omits the key entirely
        self.assertNotIn("postconditions", plan)
        result = normalize_patch_plan(plan)
        self.assertEqual(result["postconditions"], [])


class LoadPatchPlanTests(unittest.TestCase):
    def test_load_from_file(self) -> None:
        plan = _v2_plan()
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "plan.json"
            path.write_text(json.dumps(plan), encoding="utf-8")
            result = load_patch_plan(path)
        self.assertEqual(result["plan_version"], PLAN_VERSION)

    def test_load_invalid_json_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "plan.json"
            path.write_text("not json", encoding="utf-8")
            with self.assertRaises(json.JSONDecodeError):
                load_patch_plan(path)


class HashTests(unittest.TestCase):
    def test_sha256_deterministic(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "plan.json"
            path.write_text('{"hello": "world"}', encoding="utf-8")
            h1 = compute_patch_plan_sha256(path)
            h2 = compute_patch_plan_sha256(path)
        self.assertEqual(h1, h2)
        self.assertEqual(len(h1), 64)

    def test_sha256_changes_with_content(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "plan.json"
            path.write_text('{"a": 1}', encoding="utf-8")
            h1 = compute_patch_plan_sha256(path)
            path.write_text('{"a": 2}', encoding="utf-8")
            h2 = compute_patch_plan_sha256(path)
        self.assertNotEqual(h1, h2)

    def test_hmac_sha256_deterministic(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "plan.json"
            path.write_text('{"hello": "world"}', encoding="utf-8")
            h1 = compute_patch_plan_hmac_sha256(path, "secret")
            h2 = compute_patch_plan_hmac_sha256(path, "secret")
        self.assertEqual(h1, h2)
        self.assertEqual(len(h1), 64)

    def test_hmac_sha256_differs_by_key(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "plan.json"
            path.write_text('{"hello": "world"}', encoding="utf-8")
            h1 = compute_patch_plan_hmac_sha256(path, "key1")
            h2 = compute_patch_plan_hmac_sha256(path, "key2")
        self.assertNotEqual(h1, h2)


class CountPlanOpsTests(unittest.TestCase):
    def test_count_ops(self) -> None:
        self.assertEqual(count_plan_ops({"ops": [1, 2, 3]}), 3)

    def test_missing_ops(self) -> None:
        self.assertEqual(count_plan_ops({}), 0)

    def test_non_list_ops(self) -> None:
        self.assertEqual(count_plan_ops({"ops": "bad"}), 0)

    def test_empty_ops(self) -> None:
        self.assertEqual(count_plan_ops({"ops": []}), 0)


class IterResourceBatchesTests(unittest.TestCase):
    def test_single_resource_with_ops(self) -> None:
        plan = normalize_patch_plan(_v2_plan())
        batches = iter_resource_batches(plan)
        self.assertEqual(len(batches), 1)
        resource, ops = batches[0]
        self.assertEqual(resource["id"], "res1")
        self.assertEqual(len(ops), 1)
        self.assertNotIn("resource", ops[0])

    def test_multi_resource(self) -> None:
        plan = normalize_patch_plan(_v2_plan(
            resources=[
                {"id": "r1", "path": "a.prefab"},
                {"id": "r2", "path": "b.unity"},
            ],
            ops=[
                {"resource": "r1", "op": "set", "property_path": "k1", "value": "v1"},
                {"resource": "r2", "op": "set", "property_path": "k2", "value": "v2"},
                {"resource": "r1", "op": "set", "property_path": "k3", "value": "v3"},
            ],
        ))
        batches = iter_resource_batches(plan)
        self.assertEqual(len(batches), 2)
        r1_resource, r1_ops = batches[0]
        self.assertEqual(r1_resource["id"], "r1")
        self.assertEqual(len(r1_ops), 2)
        r2_resource, r2_ops = batches[1]
        self.assertEqual(r2_resource["id"], "r2")
        self.assertEqual(len(r2_ops), 1)

    def test_resource_with_no_ops(self) -> None:
        plan = normalize_patch_plan(_v2_plan(
            resources=[{"id": "r1", "path": "a.prefab"}],
            ops=[],
        ))
        batches = iter_resource_batches(plan)
        self.assertEqual(len(batches), 1)
        _, ops = batches[0]
        self.assertEqual(ops, [])

    def test_non_list_resources_raises(self) -> None:
        with self.assertRaises(ValueError):
            iter_resource_batches({"resources": "bad", "ops": []})

    def test_non_list_ops_raises(self) -> None:
        with self.assertRaises(ValueError):
            iter_resource_batches({"resources": [], "ops": "bad"})

    def test_deepcopy_isolation(self) -> None:
        plan = normalize_patch_plan(_v2_plan())
        batches = iter_resource_batches(plan)
        batches[0][0]["id"] = "modified"
        self.assertEqual(plan["resources"][0]["id"], "res1")


class BuildBridgeRequestTests(unittest.TestCase):
    def test_single_resource_adds_target(self) -> None:
        plan = normalize_patch_plan(_v2_plan())
        request = build_bridge_request(plan)
        self.assertEqual(request["protocol_version"], PLAN_VERSION)
        self.assertEqual(request["plan_version"], PLAN_VERSION)
        self.assertEqual(request["target"], "test.prefab")
        self.assertEqual(request["kind"], "prefab")
        self.assertEqual(request["mode"], "open")

    def test_multi_resource_no_target(self) -> None:
        plan = normalize_patch_plan(_v2_plan(
            resources=[
                {"id": "r1", "path": "a.prefab"},
                {"id": "r2", "path": "b.unity"},
            ],
            ops=[
                {"resource": "r1", "op": "set", "property_path": "k1", "value": "v1"},
                {"resource": "r2", "op": "set", "property_path": "k2", "value": "v2"},
            ],
        ))
        request = build_bridge_request(plan)
        self.assertNotIn("target", request)
        self.assertNotIn("kind", request)
        self.assertNotIn("mode", request)
        self.assertEqual(len(request["resources"]), 2)

    def test_empty_plan(self) -> None:
        request = build_bridge_request({"resources": [], "ops": []})
        self.assertNotIn("target", request)
        self.assertEqual(request["ops"], [])


if __name__ == "__main__":
    unittest.main()
