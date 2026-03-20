"""Internal structure validator for Unity YAML assets.

Detects structural integrity issues within a single file:
- Duplicate fileIDs
- Transform m_Father / m_Children bidirectional inconsistencies
- GameObject m_Component references pointing to missing blocks
- Orphaned transforms (no parent, not a root)
"""

from __future__ import annotations

from dataclasses import dataclass

from prefab_sentinel.contracts import Diagnostic, Severity, max_severity
from prefab_sentinel.unity_yaml_parser import (
    parse_game_objects,
    parse_transforms,
    split_yaml_blocks,
)


@dataclass(slots=True)
class StructureResult:
    duplicate_file_ids: list[Diagnostic]
    transform_inconsistencies: list[Diagnostic]
    missing_components: list[Diagnostic]
    orphaned_transforms: list[Diagnostic]
    max_severity: Severity


def validate_structure(text: str, file_path: str) -> StructureResult:
    """Validate internal structure of a Unity YAML file."""
    blocks = split_yaml_blocks(text)
    if not blocks:
        return StructureResult(
            duplicate_file_ids=[],
            transform_inconsistencies=[],
            missing_components=[],
            orphaned_transforms=[],
            max_severity=Severity.INFO,
        )

    # --- Duplicate fileID check ---
    seen_file_ids: dict[str, int] = {}
    for block in blocks:
        seen_file_ids[block.file_id] = seen_file_ids.get(block.file_id, 0) + 1

    duplicate_diags: list[Diagnostic] = []
    for fid, count in seen_file_ids.items():
        if count > 1:
            duplicate_diags.append(
                Diagnostic(
                    path=file_path,
                    location=f"fileID:{fid}",
                    detail=f"Duplicate fileID: {fid} appears {count} times",
                    evidence=f"{{fileID: {fid}}}",
                )
            )

    # --- Parse structures ---
    all_file_ids = {block.file_id for block in blocks}
    game_objects = parse_game_objects(blocks)
    transforms = parse_transforms(blocks)

    # --- Transform bidirectional consistency ---
    transform_diags: list[Diagnostic] = []

    for t in transforms.values():
        # Check: if t has a father, the father's children should contain t
        if t.father_file_id not in ("0", ""):
            father = transforms.get(t.father_file_id)
            if father is None:
                transform_diags.append(
                    Diagnostic(
                        path=file_path,
                        location=f"fileID:{t.file_id}",
                        detail=f"m_Father references non-existent transform: fileID:{t.father_file_id}",
                        evidence=f"{{fileID: {t.father_file_id}}}",
                    )
                )
            elif t.file_id not in father.children_file_ids:
                transform_diags.append(
                    Diagnostic(
                        path=file_path,
                        location=f"fileID:{t.file_id}",
                        detail=(
                            f"Transform {t.file_id} lists father {t.father_file_id}, "
                            f"but father's m_Children does not include {t.file_id}"
                        ),
                        evidence=f"m_Father: {{fileID: {t.father_file_id}}}",
                    )
                )

        # Check: each child should reference this transform as father
        for child_fid in t.children_file_ids:
            child = transforms.get(child_fid)
            if child is None:
                transform_diags.append(
                    Diagnostic(
                        path=file_path,
                        location=f"fileID:{t.file_id}",
                        detail=f"m_Children references non-existent transform: fileID:{child_fid}",
                        evidence=f"{{fileID: {child_fid}}}",
                    )
                )
            elif child.father_file_id != t.file_id:
                transform_diags.append(
                    Diagnostic(
                        path=file_path,
                        location=f"fileID:{t.file_id}",
                        detail=(
                            f"Transform {t.file_id} lists child {child_fid}, "
                            f"but child's m_Father is {child.father_file_id}"
                        ),
                        evidence=f"m_Children: {{fileID: {child_fid}}}",
                    )
                )

    # --- Missing component references ---
    missing_comp_diags: list[Diagnostic] = []
    for go in game_objects.values():
        for comp_fid in go.component_file_ids:
            if comp_fid not in all_file_ids:
                missing_comp_diags.append(
                    Diagnostic(
                        path=file_path,
                        location=f"fileID:{go.file_id}",
                        detail=f"GameObject '{go.name}' references missing component: fileID:{comp_fid}",
                        evidence=f"component: {{fileID: {comp_fid}}}",
                    )
                )

    # --- Orphaned transforms ---
    orphaned_diags: list[Diagnostic] = []
    for t in transforms.values():
        is_root = t.father_file_id in ("0", "")
        has_valid_father = t.father_file_id in transforms
        if not is_root and not has_valid_father:
            orphaned_diags.append(
                Diagnostic(
                    path=file_path,
                    location=f"fileID:{t.file_id}",
                    detail=(
                        f"Orphaned transform: fileID:{t.file_id} "
                        f"(father fileID:{t.father_file_id} not found)"
                    ),
                    evidence=f"m_Father: {{fileID: {t.father_file_id}}}",
                )
            )

    severities: list[Severity] = []
    if duplicate_diags:
        severities.append(Severity.ERROR)
    if transform_diags:
        severities.append(Severity.ERROR)
    if missing_comp_diags:
        severities.append(Severity.ERROR)
    if orphaned_diags:
        severities.append(Severity.WARNING)

    return StructureResult(
        duplicate_file_ids=duplicate_diags,
        transform_inconsistencies=transform_diags,
        missing_components=missing_comp_diags,
        orphaned_transforms=orphaned_diags,
        max_severity=max_severity(severities) if severities else Severity.INFO,
    )
