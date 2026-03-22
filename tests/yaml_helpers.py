"""Shared synthetic YAML builders for Unity asset tests."""

from __future__ import annotations

YAML_HEADER = "%YAML 1.1\n%TAG !u! tag:unity3d.com,2011:\n"


def make_gameobject(file_id: str, name: str, component_file_ids: list[str]) -> str:
    comps = "\n".join(f"  - component: {{fileID: {c}}}" for c in component_file_ids)
    return (
        f"--- !u!1 &{file_id}\n"
        f"GameObject:\n"
        f"  m_Component:\n"
        f"{comps}\n"
        f"  m_Name: {name}\n"
    )


def make_transform(
    file_id: str,
    go_file_id: str,
    father_file_id: str = "0",
    children_file_ids: list[str] | None = None,
    *,
    is_rect: bool = False,
) -> str:
    class_id = "224" if is_rect else "4"
    children = children_file_ids or []
    if children:
        children_lines = "\n".join(f"  - {{fileID: {c}}}" for c in children)
        children_block = f"  m_Children:\n{children_lines}"
    else:
        children_block = "  m_Children: []"
    return (
        f"--- !u!{class_id} &{file_id}\n"
        f"{'RectTransform' if is_rect else 'Transform'}:\n"
        f"  m_GameObject: {{fileID: {go_file_id}}}\n"
        f"  m_Father: {{fileID: {father_file_id}}}\n"
        f"{children_block}\n"
        f"  m_LocalPosition: {{x: 0, y: 0, z: 0}}\n"
        f"  m_LocalRotation: {{x: 0, y: 0, z: 0, w: 1}}\n"
        f"  m_LocalScale: {{x: 1, y: 1, z: 1}}\n"
    )


def make_stripped_transform(file_id: str, *, is_rect: bool = False) -> str:
    class_id = "224" if is_rect else "4"
    return f"--- !u!{class_id} &{file_id} stripped\n"


def make_meshfilter(file_id: str, go_file_id: str) -> str:
    return (
        f"--- !u!33 &{file_id}\n"
        f"MeshFilter:\n"
        f"  m_GameObject: {{fileID: {go_file_id}}}\n"
    )


def make_meshrenderer(file_id: str, go_file_id: str) -> str:
    return (
        f"--- !u!23 &{file_id}\n"
        f"MeshRenderer:\n"
        f"  m_GameObject: {{fileID: {go_file_id}}}\n"
    )


def make_skinned_mesh_renderer(
    file_id: str,
    go_file_id: str,
    material_guids: list[str] | None = None,
) -> str:
    """Build a SkinnedMeshRenderer block with optional material references."""
    mats = material_guids or []
    if mats:
        mat_lines = "\n".join(
            f"  - {{fileID: 2100000, guid: {guid}, type: 2}}" for guid in mats
        )
        mat_block = f"  m_Materials:\n{mat_lines}"
    else:
        mat_block = "  m_Materials: []"
    return (
        f"--- !u!137 &{file_id}\n"
        f"SkinnedMeshRenderer:\n"
        f"  m_GameObject: {{fileID: {go_file_id}}}\n"
        f"{mat_block}\n"
    )


def make_meshrenderer_with_materials(
    file_id: str,
    go_file_id: str,
    material_guids: list[str] | None = None,
) -> str:
    """Build a MeshRenderer block with optional material references."""
    mats = material_guids or []
    if mats:
        mat_lines = "\n".join(
            f"  - {{fileID: 2100000, guid: {guid}, type: 2}}" for guid in mats
        )
        mat_block = f"  m_Materials:\n{mat_lines}"
    else:
        mat_block = "  m_Materials: []"
    return (
        f"--- !u!23 &{file_id}\n"
        f"MeshRenderer:\n"
        f"  m_GameObject: {{fileID: {go_file_id}}}\n"
        f"{mat_block}\n"
    )


def make_prefab_variant(
    source_guid: str,
    modifications: list[dict[str, str]] | None = None,
) -> str:
    """Build a PrefabInstance (variant) block with m_Modifications."""
    mods = modifications or []
    if mods:
        mod_lines = []
        for mod in mods:
            target = mod.get("target", "{fileID: 0}")
            prop = mod.get("propertyPath", "")
            value = mod.get("value", "")
            obj_ref = mod.get("objectReference", "{fileID: 0}")
            mod_lines.append(
                f"    - target: {target}\n"
                f"      propertyPath: {prop}\n"
                f"      value: {value}\n"
                f"      objectReference: {obj_ref}"
            )
        mod_block = "    m_Modifications:\n" + "\n".join(mod_lines)
    else:
        mod_block = "    m_Modifications: []"
    return (
        f"--- !u!1001 &100100000\n"
        f"PrefabInstance:\n"
        f"  m_Modification:\n"
        f"    m_TransformParent: {{fileID: 0}}\n"
        f"{mod_block}\n"
        f"  m_SourcePrefab: {{fileID: 100100000, guid: {source_guid}, type: 3}}\n"
    )


def make_monobehaviour(
    file_id: str,
    go_file_id: str,
    guid: str = "abcd1234abcd1234abcd1234abcd1234",
) -> str:
    return (
        f"--- !u!114 &{file_id}\n"
        f"MonoBehaviour:\n"
        f"  m_GameObject: {{fileID: {go_file_id}}}\n"
        f"  m_Script: {{fileID: 11500000, guid: {guid}, type: 3}}\n"
    )
