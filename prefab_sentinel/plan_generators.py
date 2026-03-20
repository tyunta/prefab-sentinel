"""High-level patch plan generators.

Each generator uses :class:`PatchPlanBuilder` to produce a complete,
schema-valid patch plan dict.
"""

from __future__ import annotations

import math
from typing import Any

from prefab_sentinel.builtin_assets import (
    BUILTIN_DEFAULT_MATERIAL,
    BUILTIN_SPHERE_MESH,
)
from prefab_sentinel.plan_builder import PatchPlanBuilder

_AXIS_COMPONENTS: dict[str, tuple[str, str]] = {
    "xz": ("x", "z"),
    "xy": ("x", "y"),
    "yz": ("y", "z"),
}


def generate_circle_layout(
    *,
    output_path: str,
    root_name: str,
    count: int,
    radius: float,
    child_name_pattern: str = "{name}_{index:02d}",
    child_base_name: str = "Sphere",
    axis: str = "xz",
    scale: tuple[float, float, float] = (1.0, 1.0, 1.0),
    mesh: dict[str, Any] | None = None,
    material: dict[str, Any] | None = None,
    resource_id: str = "res1",
) -> dict[str, Any]:
    """Generate a patch plan that creates a prefab with children in a circle.

    Returns a validated patch plan dict.
    """
    if axis not in _AXIS_COMPONENTS:
        raise ValueError(f"axis must be one of {sorted(_AXIS_COMPONENTS)}, got '{axis}'.")
    if count < 0:
        raise ValueError(f"count must be >= 0, got {count}.")

    mesh_ref = mesh if mesh is not None else BUILTIN_SPHERE_MESH
    material_ref = material if material is not None else BUILTIN_DEFAULT_MATERIAL
    axis_a, axis_b = _AXIS_COMPONENTS[axis]

    b = PatchPlanBuilder()
    b.create_prefab_resource(id=resource_id, path=output_path)
    b.create_prefab(root_name)

    for i in range(count):
        child_name = child_name_pattern.format(name=child_base_name, index=i)
        obj_handle = f"$obj_{i}"
        mf_handle = f"$mf_{i}"
        mr_handle = f"$mr_{i}"
        tf_handle = f"$tf_{i}"

        angle = 2.0 * math.pi * i / count if count > 0 else 0.0
        pos_a = radius * math.cos(angle)
        pos_b = radius * math.sin(angle)

        b.create_game_object(child_name, "$root", result=obj_handle)
        b.add_component(obj_handle, "UnityEngine.MeshFilter", result=mf_handle)
        b.add_component(obj_handle, "UnityEngine.MeshRenderer", result=mr_handle)
        b.find_component(obj_handle, "UnityEngine.Transform", result=tf_handle)

        # Position
        b.set(target=tf_handle, path=f"m_LocalPosition.{axis_a}", value=pos_a)
        b.set(target=tf_handle, path=f"m_LocalPosition.{axis_b}", value=pos_b)

        # Scale
        b.set(target=tf_handle, path="m_LocalScale.x", value=scale[0])
        b.set(target=tf_handle, path="m_LocalScale.y", value=scale[1])
        b.set(target=tf_handle, path="m_LocalScale.z", value=scale[2])

        # Mesh
        b.set(target=mf_handle, path="m_Mesh", value=mesh_ref)

        # Material
        b.set(target=mr_handle, path="m_Materials.Array.data[0]", value=material_ref)

        # Renderer shadow settings
        b.set(target=mr_handle, path="m_CastShadows", value=1)

    b.save()
    return b.build()
