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
