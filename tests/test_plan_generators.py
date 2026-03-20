from __future__ import annotations

import math
import unittest

from prefab_sentinel.patch_plan import normalize_patch_plan
from prefab_sentinel.plan_generators import (
    BUILTIN_DEFAULT_MATERIAL,
    BUILTIN_SPHERE_MESH,
    generate_circle_layout,
)


class CircleLayoutGeneratorTests(unittest.TestCase):
    def test_count_12_generates_146_ops(self) -> None:
        plan = generate_circle_layout(
            output_path="Assets/Circle.prefab",
            root_name="Circle",
            count=12,
            radius=3.0,
        )
        self.assertEqual(146, len(plan["ops"]))

    def test_op_count_formula(self) -> None:
        """1 (create_prefab) + count * 12 + 1 (save) = total ops."""
        for count in (1, 4, 8, 12, 24):
            plan = generate_circle_layout(
                output_path="Assets/C.prefab",
                root_name="C",
                count=count,
                radius=1.0,
            )
            expected = 1 + count * 12 + 1
            self.assertEqual(expected, len(plan["ops"]), msg=f"count={count}")

    def test_count_0_generates_2_ops(self) -> None:
        plan = generate_circle_layout(
            output_path="Assets/Empty.prefab",
            root_name="Empty",
            count=0,
            radius=1.0,
        )
        # create_prefab + save
        self.assertEqual(2, len(plan["ops"]))

    def test_position_precision(self) -> None:
        plan = generate_circle_layout(
            output_path="Assets/C.prefab",
            root_name="C",
            count=12,
            radius=3.0,
        )
        # Extract position set ops for child 0 (index 0 in the circle)
        # Child 0 ops start at index 1 (after create_prefab)
        # Ops per child: create_game_object, add_component(MF), add_component(MR),
        #   find_component(Transform), set(pos_a), set(pos_b), set(scale_x),
        #   set(scale_y), set(scale_z), set(mesh), set(material), set(shadow)
        child_0_start = 1  # after create_prefab
        pos_x_op = plan["ops"][child_0_start + 4]  # set pos_a (x)
        pos_z_op = plan["ops"][child_0_start + 5]  # set pos_b (z)

        # Child 0: angle=0, x=3*cos(0)=3.0, z=3*sin(0)=0.0
        self.assertAlmostEqual(3.0, pos_x_op["value"], places=6)
        self.assertAlmostEqual(0.0, pos_z_op["value"], places=6)

        # Child 3: angle=pi/2, x=3*cos(pi/2)≈0, z=3*sin(pi/2)=3
        child_3_start = 1 + 3 * 12
        pos_x_op_3 = plan["ops"][child_3_start + 4]
        pos_z_op_3 = plan["ops"][child_3_start + 5]
        self.assertAlmostEqual(3.0 * math.cos(2.0 * math.pi * 3 / 12), pos_x_op_3["value"], places=6)
        self.assertAlmostEqual(3.0 * math.sin(2.0 * math.pi * 3 / 12), pos_z_op_3["value"], places=6)

    def test_create_prefab_op_has_no_result_key(self) -> None:
        plan = generate_circle_layout(
            output_path="Assets/C.prefab",
            root_name="C",
            count=1,
            radius=1.0,
        )
        create_op = plan["ops"][0]
        self.assertEqual("create_prefab", create_op["op"])
        self.assertNotIn("result", create_op)

    def test_result_passes_normalize(self) -> None:
        plan = generate_circle_layout(
            output_path="Assets/Circle.prefab",
            root_name="Circle",
            count=12,
            radius=3.0,
        )
        # Should not raise
        normalize_patch_plan(plan)

    def test_resource_is_create_mode(self) -> None:
        plan = generate_circle_layout(
            output_path="Assets/C.prefab",
            root_name="C",
            count=1,
            radius=1.0,
        )
        self.assertEqual("create", plan["resources"][0]["mode"])
        self.assertEqual("prefab", plan["resources"][0]["kind"])

    def test_custom_mesh_and_material(self) -> None:
        custom_mesh = {"fileID": 999, "guid": "aabbccdd", "type": 0}
        custom_mat = {"fileID": 888, "guid": "eeff0011", "type": 0}
        plan = generate_circle_layout(
            output_path="Assets/C.prefab",
            root_name="C",
            count=1,
            radius=1.0,
            mesh=custom_mesh,
            material=custom_mat,
        )
        # Mesh set op is at child_start + 9 = 1 + 9 = 10
        mesh_op = plan["ops"][1 + 9]
        mat_op = plan["ops"][1 + 10]
        self.assertEqual(custom_mesh, mesh_op["value"])
        self.assertEqual(custom_mat, mat_op["value"])

    def test_default_mesh_and_material(self) -> None:
        plan = generate_circle_layout(
            output_path="Assets/C.prefab",
            root_name="C",
            count=1,
            radius=1.0,
        )
        mesh_op = plan["ops"][1 + 9]
        mat_op = plan["ops"][1 + 10]
        self.assertEqual(BUILTIN_SPHERE_MESH, mesh_op["value"])
        self.assertEqual(BUILTIN_DEFAULT_MATERIAL, mat_op["value"])

    def test_custom_scale(self) -> None:
        plan = generate_circle_layout(
            output_path="Assets/C.prefab",
            root_name="C",
            count=1,
            radius=1.0,
            scale=(0.5, 0.5, 0.5),
        )
        # Scale ops at child_start + 6, +7, +8
        for offset in (6, 7, 8):
            op = plan["ops"][1 + offset]
            self.assertEqual("set", op["op"])
            self.assertEqual(0.5, op["value"])

    def test_axis_xy(self) -> None:
        plan = generate_circle_layout(
            output_path="Assets/C.prefab",
            root_name="C",
            count=1,
            radius=2.0,
            axis="xy",
        )
        # Position ops: pos_a = x, pos_b = y
        pos_a_op = plan["ops"][1 + 4]
        pos_b_op = plan["ops"][1 + 5]
        self.assertEqual("m_LocalPosition.x", pos_a_op["path"])
        self.assertEqual("m_LocalPosition.y", pos_b_op["path"])
        self.assertAlmostEqual(2.0, pos_a_op["value"], places=6)
        self.assertAlmostEqual(0.0, pos_b_op["value"], places=6)

    def test_axis_yz(self) -> None:
        plan = generate_circle_layout(
            output_path="Assets/C.prefab",
            root_name="C",
            count=1,
            radius=1.0,
            axis="yz",
        )
        pos_a_op = plan["ops"][1 + 4]
        pos_b_op = plan["ops"][1 + 5]
        self.assertEqual("m_LocalPosition.y", pos_a_op["path"])
        self.assertEqual("m_LocalPosition.z", pos_b_op["path"])

    def test_invalid_axis_raises(self) -> None:
        with self.assertRaises(ValueError, msg="axis must be"):
            generate_circle_layout(
                output_path="Assets/C.prefab",
                root_name="C",
                count=1,
                radius=1.0,
                axis="zx",
            )

    def test_negative_count_raises(self) -> None:
        with self.assertRaises(ValueError, msg="count must be"):
            generate_circle_layout(
                output_path="Assets/C.prefab",
                root_name="C",
                count=-1,
                radius=1.0,
            )

    def test_child_naming_pattern(self) -> None:
        plan = generate_circle_layout(
            output_path="Assets/C.prefab",
            root_name="C",
            count=3,
            radius=1.0,
            child_base_name="Node",
        )
        # create_game_object ops at positions 1, 13, 25
        self.assertEqual("Node_00", plan["ops"][1]["name"])
        self.assertEqual("Node_01", plan["ops"][1 + 12]["name"])
        self.assertEqual("Node_02", plan["ops"][1 + 24]["name"])

    def test_custom_resource_id(self) -> None:
        plan = generate_circle_layout(
            output_path="Assets/C.prefab",
            root_name="C",
            count=1,
            radius=1.0,
            resource_id="myres",
        )
        self.assertEqual("myres", plan["resources"][0]["id"])
        for op in plan["ops"]:
            self.assertEqual("myres", op["resource"])


if __name__ == "__main__":
    unittest.main()
