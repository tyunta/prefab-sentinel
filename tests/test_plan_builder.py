from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from prefab_sentinel.patch_plan import normalize_patch_plan
from prefab_sentinel.plan_builder import PatchPlanBuilder


class PatchPlanBuilderTests(unittest.TestCase):
    def test_empty_builder_produces_valid_plan(self) -> None:
        b = PatchPlanBuilder()
        b.add_resource(id="r", path="Assets/Test.prefab")
        plan = b.build()
        self.assertEqual(2, plan["plan_version"])
        self.assertEqual(1, len(plan["resources"]))
        self.assertEqual([], plan["ops"])

    def test_build_validates_through_normalize(self) -> None:
        b = PatchPlanBuilder()
        b.add_resource(id="r", path="Assets/Test.prefab")
        b.set(component="Comp", path="enabled", value=True)
        plan = b.build()
        # Should also pass normalize independently
        normalize_patch_plan(plan)

    def test_duplicate_resource_id_raises(self) -> None:
        b = PatchPlanBuilder()
        b.add_resource(id="r", path="Assets/A.prefab")
        with self.assertRaises(ValueError, msg="Duplicate resource id"):
            b.add_resource(id="r", path="Assets/B.prefab")

    def test_no_resource_raises_on_op(self) -> None:
        b = PatchPlanBuilder()
        with self.assertRaises(ValueError, msg="No active resource"):
            b.save()

    def test_fluent_chaining(self) -> None:
        plan = (
            PatchPlanBuilder()
            .add_resource(id="r", path="Assets/T.prefab")
            .set(component="C", path="x", value=1)
            .set(component="C", path="y", value=2)
            .save()
            .build()
        )
        self.assertEqual(3, len(plan["ops"]))

    def test_create_prefab_op(self) -> None:
        b = PatchPlanBuilder()
        b.create_prefab_resource(id="r", path="Assets/New.prefab")
        b.create_prefab("Root", result="$root")
        plan = b.build()
        op = plan["ops"][0]
        self.assertEqual("create_prefab", op["op"])
        self.assertEqual("Root", op["name"])
        self.assertEqual("$root", op["result"])

    def test_create_game_object_op(self) -> None:
        b = PatchPlanBuilder()
        b.create_prefab_resource(id="r", path="Assets/New.prefab")
        b.create_prefab(result="$root")
        b.create_game_object("Child", "$root", result="$child")
        plan = b.build()
        op = plan["ops"][1]
        self.assertEqual("create_game_object", op["op"])
        self.assertEqual("Child", op["name"])
        self.assertEqual("$root", op["parent"])
        self.assertEqual("$child", op["result"])

    def test_add_component_op(self) -> None:
        b = PatchPlanBuilder()
        b.create_prefab_resource(id="r", path="Assets/New.prefab")
        b.create_prefab(result="$root")
        b.create_game_object("Obj", "$root", result="$obj")
        b.add_component("$obj", "UnityEngine.MeshFilter", result="$mf")
        plan = b.build()
        op = plan["ops"][2]
        self.assertEqual("add_component", op["op"])
        self.assertEqual("$obj", op["target"])
        self.assertEqual("UnityEngine.MeshFilter", op["type"])
        self.assertEqual("$mf", op["result"])

    def test_find_component_op(self) -> None:
        b = PatchPlanBuilder()
        b.create_prefab_resource(id="r", path="Assets/New.prefab")
        b.create_prefab(result="$root")
        b.create_game_object("Obj", "$root", result="$obj")
        b.find_component("$obj", "UnityEngine.Transform", result="$tf")
        plan = b.build()
        op = plan["ops"][2]
        self.assertEqual("find_component", op["op"])
        self.assertEqual("UnityEngine.Transform", op["type"])

    def test_remove_component_op(self) -> None:
        b = PatchPlanBuilder()
        b.create_prefab_resource(id="r", path="Assets/New.prefab")
        b.create_prefab(result="$root")
        b.create_game_object("Obj", "$root", result="$obj")
        b.add_component("$obj", "UnityEngine.BoxCollider", result="$bc")
        b.remove_component("$bc")
        plan = b.build()
        op = plan["ops"][3]
        self.assertEqual("remove_component", op["op"])
        self.assertEqual("$bc", op["target"])

    def test_rename_object_op(self) -> None:
        b = PatchPlanBuilder()
        b.create_prefab_resource(id="r", path="Assets/New.prefab")
        b.create_prefab(result="$root")
        b.rename_object("$root", "NewName")
        plan = b.build()
        op = plan["ops"][1]
        self.assertEqual("rename_object", op["op"])
        self.assertEqual("NewName", op["name"])

    def test_reparent_op(self) -> None:
        b = PatchPlanBuilder()
        b.create_prefab_resource(id="r", path="Assets/New.prefab")
        b.create_prefab(result="$root")
        b.create_game_object("A", "$root", result="$a")
        b.create_game_object("B", "$root", result="$b")
        b.reparent("$b", "$a")
        plan = b.build()
        op = plan["ops"][3]
        self.assertEqual("reparent", op["op"])
        self.assertEqual("$b", op["target"])
        self.assertEqual("$a", op["parent"])

    def test_set_op_with_target(self) -> None:
        b = PatchPlanBuilder()
        b.create_prefab_resource(id="r", path="Assets/New.prefab")
        b.create_prefab(result="$root")
        b.create_game_object("Obj", "$root", result="$obj")
        b.find_component("$obj", "UnityEngine.Transform", result="$tf")
        b.set(target="$tf", path="m_LocalPosition.x", value=1.5)
        plan = b.build()
        op = plan["ops"][3]
        self.assertEqual("set", op["op"])
        self.assertEqual("$tf", op["target"])
        self.assertEqual("m_LocalPosition.x", op["path"])
        self.assertEqual(1.5, op["value"])

    def test_insert_array_element_op(self) -> None:
        b = PatchPlanBuilder()
        b.add_resource(id="r", path="Assets/Test.prefab")
        b.insert_array_element(
            component="Comp",
            path="items.Array.data",
            index=0,
            value={"fileID": 123, "guid": "abc"},
        )
        plan = b.build()
        op = plan["ops"][0]
        self.assertEqual("insert_array_element", op["op"])
        self.assertEqual(0, op["index"])
        self.assertEqual({"fileID": 123, "guid": "abc"}, op["value"])

    def test_remove_array_element_op(self) -> None:
        b = PatchPlanBuilder()
        b.add_resource(id="r", path="Assets/Test.prefab")
        b.remove_array_element(component="Comp", path="items.Array.data", index=2)
        plan = b.build()
        op = plan["ops"][0]
        self.assertEqual("remove_array_element", op["op"])
        self.assertEqual(2, op["index"])

    def test_handle_refs_preserve_dollar_prefix(self) -> None:
        b = PatchPlanBuilder()
        b.create_prefab_resource(id="r", path="Assets/New.prefab")
        b.create_prefab(result="$root")
        b.create_game_object("Child", "$root", result="$child")
        plan = b.build()
        self.assertEqual("$root", plan["ops"][0]["result"])
        self.assertEqual("$root", plan["ops"][1]["parent"])
        self.assertEqual("$child", plan["ops"][1]["result"])

    def test_to_json_roundtrip(self) -> None:
        b = PatchPlanBuilder()
        b.add_resource(id="r", path="Assets/Test.prefab")
        b.set(component="C", path="x", value=42)
        json_str = b.to_json()
        plan = json.loads(json_str)
        normalize_patch_plan(plan)

    def test_write_creates_file(self) -> None:
        b = PatchPlanBuilder()
        b.add_resource(id="r", path="Assets/Test.prefab")
        b.save()
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "sub" / "plan.json"
            b.write(out)
            self.assertTrue(out.exists())
            plan = json.loads(out.read_text(encoding="utf-8"))
            normalize_patch_plan(plan)

    def test_postcondition(self) -> None:
        b = PatchPlanBuilder()
        b.add_resource(id="r", path="Assets/Test.prefab")
        b.postcondition("asset_exists", resource="r")
        plan = b.build()
        self.assertEqual(1, len(plan["postconditions"]))
        self.assertEqual("asset_exists", plan["postconditions"][0]["type"])

    def test_multiple_resources(self) -> None:
        b = PatchPlanBuilder()
        b.add_resource(id="r1", path="Assets/A.prefab")
        b.set(component="C", path="x", value=1)
        b.add_resource(id="r2", path="Assets/B.prefab")
        b.set(component="C", path="y", value=2)
        plan = b.build()
        self.assertEqual(2, len(plan["resources"]))
        self.assertEqual("r1", plan["ops"][0]["resource"])
        self.assertEqual("r2", plan["ops"][1]["resource"])

    def test_create_prefab_resource_sets_create_mode(self) -> None:
        b = PatchPlanBuilder()
        b.create_prefab_resource(id="r", path="Assets/New.prefab")
        plan = b.build()
        self.assertEqual("create", plan["resources"][0]["mode"])
        self.assertEqual("prefab", plan["resources"][0]["kind"])


if __name__ == "__main__":
    unittest.main()
