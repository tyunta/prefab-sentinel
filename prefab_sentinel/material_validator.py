"""Static material, renderer, TMP, and folder policy validation."""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from prefab_sentinel.contracts import Diagnostic, Severity, error_response, success_response
from prefab_sentinel.inspection_context import ProjectInspectionContext
from prefab_sentinel.material_asset_inspector import inspect_material_asset
from prefab_sentinel.material_inspector import RENDERER_CLASS_NAMES, parse_renderer_materials
from prefab_sentinel.material_validation_rules import MaterialValidationRules
from prefab_sentinel.parallel_scan import run_ordered
from prefab_sentinel.services.reference_resolver import ReferenceResolverService
from prefab_sentinel.unity_assets import (
    extract_local_file_ids,
    is_unity_text_asset,
    normalize_guid,
)
from prefab_sentinel.unity_assets_path import relative_to_root
from prefab_sentinel.unity_yaml_parser import (
    CLASS_ID_MONOBEHAVIOUR,
    YamlBlock,
    parse_components,
    parse_game_objects,
    parse_transforms,
    split_yaml_blocks,
)

__all__ = ["validate_materials"]

_TMP_FONT_REF = re.compile(
    r"m_(?:fontAsset|FontAsset):\s*\{fileID:\s*(-?\d+),\s*guid:\s*([0-9a-fA-F]{32})"
)
_TMP_MATERIAL_REF = re.compile(
    r"m_(?:sharedMaterial|fontMaterial|FontMaterial|materialPreset):\s*"
    r"\{fileID:\s*(-?\d+),\s*guid:\s*([0-9a-fA-F]{32})"
)
_ATLAS_REF = re.compile(
    r"-\s*\{fileID:\s*(-?\d+),\s*guid:\s*([0-9a-fA-F]{32})"
)


@dataclass(slots=True)
class _MaterialEvidence:
    path: str
    material_name: str
    shader_guid: str
    shader_file_id: str
    shader_name: str
    shader_path: str | None
    render_queue: int | None
    ztest: float | None


@dataclass(slots=True)
class _RendererSlotEvidence:
    source_path: str
    hierarchy_path: str
    slot_index: int
    material_guid: str
    material_path: str | None
    material: _MaterialEvidence | None


@dataclass(slots=True)
class _TmpEvidence:
    source_path: str
    hierarchy_path: str
    font_asset_guid: str
    font_asset_path: str | None
    material_preset_guid: str
    material_preset_path: str | None
    material: _MaterialEvidence | None
    atlas_references: tuple[str, ...]


@dataclass(slots=True)
class _FolderEvidence:
    path: str
    extension: str
    asset_kind: str | None


@dataclass(slots=True)
class _ValidationState:
    scanned_targets: int
    direct_materials: list[_MaterialEvidence] = field(default_factory=list)
    material_cache: dict[str, _MaterialEvidence] = field(default_factory=dict)
    renderer_slots: list[_RendererSlotEvidence] = field(default_factory=list)
    tmp_entries: list[_TmpEvidence] = field(default_factory=list)
    folder_entries: list[_FolderEvidence] = field(default_factory=list)
    diagnostics: list[Diagnostic] = field(default_factory=list)
    read_errors: list[Diagnostic] = field(default_factory=list)


@dataclass(slots=True)
class _FileEvidencePayload:
    state: _ValidationState


def validate_materials(
    reference_resolver: ReferenceResolverService,
    scope_path: Path,
    rules: MaterialValidationRules,
    *,
    include_details: bool = False,
    inspection_context: ProjectInspectionContext | None = None,
):
    state = _build_evidence(
        reference_resolver,
        scope_path,
        inspection_context=inspection_context,
    )
    data = _response_data(state, rules, include_details)

    if state.read_errors:
        return error_response(
            "MATERIAL_VALIDATION_READ_ERROR",
            "Material validation could not read all in-scope inputs.",
            severity=Severity.ERROR,
            data=data,
            diagnostics=state.read_errors,
        )

    diagnostics = _validation_diagnostics(state, rules)
    if diagnostics:
        return error_response(
            "MATERIAL_VALIDATION_FINDINGS",
            "Material validation found static material risks.",
            severity=Severity.WARNING,
            data=data,
            diagnostics=diagnostics,
        )

    return success_response(
        "MATERIAL_VALIDATION_OK",
        "Material validation completed without findings.",
        data=data,
    )



def _read_asset_text(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeDecodeError):
        return None

def _asset_reference_pairs(blocks_by_path: Mapping[Path, list[YamlBlock]]) -> set[tuple[str, str]]:
    pairs: set[tuple[str, str]] = set()
    for blocks in blocks_by_path.values():
        components = parse_components(blocks)
        block_by_file_id = {block.file_id: block for block in blocks}
        for component in components.values():
            if component.class_id not in RENDERER_CLASS_NAMES:
                continue
            block = block_by_file_id[component.file_id]
            for _file_id, guid in parse_renderer_materials(block):
                pairs.add((normalize_guid(guid), "2100000"))
        for block in blocks:
            if block.class_id != CLASS_ID_MONOBEHAVIOUR:
                continue
            font_match = _TMP_FONT_REF.search(block.text)
            material_match = _TMP_MATERIAL_REF.search(block.text)
            if font_match is None or material_match is None:
                continue
            pairs.add((normalize_guid(font_match.group(2)), font_match.group(1)))
            pairs.add((normalize_guid(material_match.group(2)), material_match.group(1)))
    return pairs


def _resolution_snapshot(
    reference_resolver: ReferenceResolverService,
    reference_pairs: set[tuple[str, str]],
    guid_index: Mapping[str, Path],
) -> tuple[dict[tuple[str, str], str | None], dict[Path, set[str]]]:
    resolved_paths: dict[tuple[str, str], str | None] = {}
    local_ids_by_path: dict[Path, set[str]] = {}
    for guid, file_id in sorted(reference_pairs):
        indexed_path = guid_index.get(guid)
        if indexed_path is None:
            response = reference_resolver.resolve_reference(guid, file_id)
            asset_path = response.data.get("asset_path") if response.success else None
            resolved_paths[(guid, file_id)] = asset_path if isinstance(asset_path, str) else None
            continue
        path = _project_asset_path(reference_resolver, str(indexed_path))
        if path is None or file_id == "0" or not is_unity_text_asset(path):
            continue
        if not ReferenceResolverService._should_validate_external_file_id(path):
            continue
        text = reference_resolver.read_text(path)
        local_ids_by_path[path] = extract_local_file_ids(text) if text is not None else set()
    return resolved_paths, local_ids_by_path


def _file_id_is_valid_for_path(
    reference_resolver: ReferenceResolverService,
    path: Path,
    file_id: str,
    local_ids_by_path: Mapping[Path, set[str]] | None,
) -> bool:
    if file_id == "0" or not is_unity_text_asset(path):
        return True
    if not ReferenceResolverService._should_validate_external_file_id(path):
        return True
    local_ids = (
        local_ids_by_path.get(path)
        if local_ids_by_path is not None
        else reference_resolver._local_ids(path)
    )
    return not local_ids or file_id in local_ids


def _build_evidence(
    reference_resolver: ReferenceResolverService,
    scope_path: Path,
    *,
    inspection_context: ProjectInspectionContext | None = None,
) -> _ValidationState:
    guid_index = None if inspection_context is None else inspection_context.guid_index
    files = reference_resolver.collect_scope_files(scope_path)
    file_texts = {path: reference_resolver.read_text(path) for path in files}
    file_blocks = {
        path: split_yaml_blocks(text)
        for path, text in file_texts.items()
        if text is not None
    }
    resolved_asset_paths: dict[tuple[str, str], str | None] | None = None
    local_ids_by_path: dict[Path, set[str]] | None = None
    if guid_index is not None:
        reference_pairs = _asset_reference_pairs(file_blocks)
        resolved_asset_paths, local_ids_by_path = _resolution_snapshot(
            reference_resolver,
            reference_pairs,
            guid_index,
        )
    state = _ValidationState(scanned_targets=len(files))

    def build_file_payload(path: Path) -> _FileEvidencePayload:
        rel_path = _relative(reference_resolver, path)
        file_state = _ValidationState(scanned_targets=1)
        text = file_texts[path]
        if text is None:
            file_state.read_errors.append(_diagnostic(
                "MATERIAL_VALIDATION_READ_ERROR", rel_path, "", "Unreadable Unity text asset.", "error",
            ))
            return _FileEvidencePayload(file_state)
        blocks = file_blocks[path]
        file_state.folder_entries.append(_folder_evidence(rel_path, path, blocks))
        if path.suffix.lower() == ".mat" and _has_class(blocks, "21"):
            material = _material_for_path(
                reference_resolver,
                path,
                rel_path,
                guid_index=guid_index,
            )
            if material is not None:
                file_state.direct_materials.append(material)
                file_state.material_cache[rel_path] = material
        _collect_renderer_slots(
            reference_resolver, file_state, rel_path, blocks,
            guid_index=guid_index,
            resolved_asset_paths=resolved_asset_paths,
            local_ids_by_path=local_ids_by_path,
        )
        _collect_tmp_entries(
            reference_resolver, file_state, rel_path, blocks,
            guid_index=guid_index,
            resolved_asset_paths=resolved_asset_paths,
            local_ids_by_path=local_ids_by_path,
        )
        return _FileEvidencePayload(file_state)

    for payload in run_ordered(files, build_file_payload):
        file_state = payload.state
        state.folder_entries.extend(file_state.folder_entries)
        state.direct_materials.extend(file_state.direct_materials)
        state.renderer_slots.extend(file_state.renderer_slots)
        state.tmp_entries.extend(file_state.tmp_entries)
        state.diagnostics.extend(file_state.diagnostics)
        state.read_errors.extend(file_state.read_errors)
        for material_path, material in file_state.material_cache.items():
            state.material_cache.setdefault(material_path, material)
    return state


def _collect_renderer_slots(
    reference_resolver: ReferenceResolverService,
    state: _ValidationState,
    source_path: str,
    blocks: list[YamlBlock],
    *,
    guid_index: Mapping[str, Path] | None = None,
    resolved_asset_paths: Mapping[tuple[str, str], str | None] | None = None,
    local_ids_by_path: Mapping[Path, set[str]] | None = None,
) -> None:
    hierarchy = _hierarchy_map(blocks)
    components = parse_components(blocks)
    block_by_file_id = {block.file_id: block for block in blocks}
    for component in components.values():
        if component.class_id not in RENDERER_CLASS_NAMES:
            continue
        block = block_by_file_id[component.file_id]
        for index, (_file_id, guid) in enumerate(parse_renderer_materials(block)):
            material_path = _resolve_asset_path(
                reference_resolver,
                guid,
                "2100000",
                guid_index=guid_index,
                resolved_asset_paths=resolved_asset_paths,
                local_ids_by_path=local_ids_by_path,
            )
            material = _cached_material(
                reference_resolver,
                state,
                material_path,
                guid_index=guid_index,
            )
            location = f"renderer_slot:{hierarchy.get(component.game_object_file_id, '')}[{index}]"
            if material_path is None:
                state.diagnostics.append(_diagnostic(
                    "MATERIAL_SLOT_UNRESOLVED",
                    source_path,
                    location,
                    f"Renderer material GUID {guid} could not be resolved.",
                    "warning",
                ))
            state.renderer_slots.append(_RendererSlotEvidence(
                source_path=source_path,
                hierarchy_path=hierarchy.get(component.game_object_file_id, ""),
                slot_index=index,
                material_guid=guid,
                material_path=material_path,
                material=material,
            ))


def _collect_tmp_entries(
    reference_resolver: ReferenceResolverService,
    state: _ValidationState,
    source_path: str,
    blocks: list[YamlBlock],
    *,
    guid_index: Mapping[str, Path] | None = None,
    resolved_asset_paths: Mapping[tuple[str, str], str | None] | None = None,
    local_ids_by_path: Mapping[Path, set[str]] | None = None,
) -> None:
    hierarchy = _hierarchy_map(blocks)
    components = parse_components(blocks)
    for block in blocks:
        if block.class_id != CLASS_ID_MONOBEHAVIOUR:
            continue
        font_match = _TMP_FONT_REF.search(block.text)
        material_match = _TMP_MATERIAL_REF.search(block.text)
        if font_match is None or material_match is None:
            continue
        component = components.get(block.file_id)
        go_id = "" if component is None else component.game_object_file_id
        font_guid = font_match.group(2).lower()
        material_guid = material_match.group(2).lower()
        font_path = _resolve_asset_path(
            reference_resolver,
            font_guid,
            font_match.group(1),
            guid_index=guid_index,
            resolved_asset_paths=resolved_asset_paths,
            local_ids_by_path=local_ids_by_path,
        )
        material_path = _resolve_asset_path(
            reference_resolver,
            material_guid,
            material_match.group(1),
            guid_index=guid_index,
            resolved_asset_paths=resolved_asset_paths,
            local_ids_by_path=local_ids_by_path,
        )
        state.tmp_entries.append(_TmpEvidence(
            source_path=source_path,
            hierarchy_path=hierarchy.get(go_id, ""),
            font_asset_guid=font_guid,
            font_asset_path=font_path,
            material_preset_guid=material_guid,
            material_preset_path=material_path,
            material=_cached_material(
                reference_resolver,
                state,
                material_path,
                guid_index=guid_index,
            ),
            atlas_references=_atlas_references(reference_resolver, font_path),
        ))


def _validation_diagnostics(
    state: _ValidationState,
    rules: MaterialValidationRules,
) -> list[Diagnostic]:
    diagnostics = list(state.diagnostics)
    diagnostics.extend(_shader_risk_diagnostics(state.material_cache.values()))
    diagnostics.extend(_shader_policy_diagnostics(state, rules))
    diagnostics.extend(_shared_material_diagnostics(state, rules))
    diagnostics.extend(_folder_policy_diagnostics(state, rules))
    return diagnostics


def _shader_risk_diagnostics(
    materials: Any,
) -> list[Diagnostic]:
    diagnostics: list[Diagnostic] = []
    for material in sorted(materials, key=lambda item: item.path):
        if material.shader_file_id == "0" or material.shader_guid == "":
            diagnostics.append(_diagnostic(
                "MATERIAL_SHADER_MISSING",
                material.path,
                f"material:{material.path}",
                "Material has no serialized shader reference.",
                "warning",
            ))
        elif material.shader_path is None:
            diagnostics.append(_diagnostic(
                "MATERIAL_SHADER_UNRESOLVED",
                material.path,
                f"material:{material.path}",
                f"Material shader GUID {material.shader_guid} could not be resolved.",
                "warning",
            ))
    return diagnostics


def _shader_policy_diagnostics(
    state: _ValidationState,
    rules: MaterialValidationRules,
) -> list[Diagnostic]:
    diagnostics: list[Diagnostic] = []
    for rule in rules.shader_name_policies:
        for material in state.direct_materials:
            if _path_under(material.path, rule.scope):
                diagnostics.extend(_shader_mismatch(rule.expected_shader, material, material.path, f"material:{material.path}"))
        for slot in state.renderer_slots:
            if _evidence_selected(slot.source_path, slot.hierarchy_path, rule.scope, rule.hierarchy_prefix):
                diagnostics.extend(_shader_mismatch(
                    rule.expected_shader, slot.material, slot.source_path,
                    f"renderer_slot:{slot.hierarchy_path}[{slot.slot_index}]",
                ))
        for tmp in state.tmp_entries:
            if _evidence_selected(tmp.source_path, tmp.hierarchy_path, rule.scope, rule.hierarchy_prefix):
                diagnostics.extend(_shader_mismatch(
                    rule.expected_shader, tmp.material, tmp.source_path,
                    f"tmp_material_preset:{tmp.hierarchy_path}",
                ))
    return diagnostics


def _shared_material_diagnostics(
    state: _ValidationState,
    rules: MaterialValidationRules,
) -> list[Diagnostic]:
    diagnostics: list[Diagnostic] = []
    for rule in rules.shared_material_groups:
        slots = [
            slot for slot in state.renderer_slots
            if _evidence_selected(
                slot.source_path, slot.hierarchy_path, rule.scope, rule.hierarchy_prefix,
            )
            and slot.material_path is not None
        ]
        if rule.expected_material is not None:
            diagnostics.extend(_shared_expected_mismatches(rule.expected_material, slots))
        else:
            diagnostics.extend(_shared_candidate_drift(slots))
    return diagnostics


def _folder_policy_diagnostics(
    state: _ValidationState,
    rules: MaterialValidationRules,
) -> list[Diagnostic]:
    diagnostics: list[Diagnostic] = []
    for rule in rules.folder_policies:
        for entry in state.folder_entries:
            if not _path_under(entry.path, rule.folder):
                continue
            extension_hit = entry.extension in rule.disallowed_extensions
            kind_hit = (
                entry.asset_kind is not None
                and entry.asset_kind in rule.disallowed_asset_kinds
            )
            if extension_hit or kind_hit:
                diagnostics.append(_diagnostic(
                    "MATERIAL_FOLDER_POLICY_VIOLATION",
                    entry.path,
                    f"folder_policy:{rule.id}",
                    f"Asset violates folder policy {rule.id}.",
                    "warning",
                ))
    return diagnostics


def _response_data(
    state: _ValidationState,
    rules: MaterialValidationRules,
    include_details: bool,
) -> dict[str, Any]:
    data: dict[str, Any] = {
        "summary": {
            "scanned_targets": state.scanned_targets,
            "materials": len(state.direct_materials),
            "renderer_slots": len(state.renderer_slots),
            "tmp_references": len(state.tmp_entries),
            "folder_entries": len(state.folder_entries),
        },
        "rule_config": _core_rule_config(rules),
        "read_only": True,
    }
    if include_details:
        data["details"] = _details(state)
    return data


def _details(state: _ValidationState) -> dict[str, Any]:
    return {
        "materials": [_material_detail(material) for material in state.direct_materials],
        "renderer_slots": [_renderer_detail(slot) for slot in state.renderer_slots],
        "tmp": [_tmp_detail(tmp) for tmp in state.tmp_entries],
        "folder_entries": [
            {"path": entry.path, "extension": entry.extension, "asset_kind": entry.asset_kind}
            for entry in state.folder_entries
        ],
    }


def _material_for_path(
    reference_resolver: ReferenceResolverService,
    path: Path,
    rel_path: str,
    *,
    guid_index: Mapping[str, Path] | None = None,
) -> _MaterialEvidence | None:
    try:
        result = inspect_material_asset(
            str(path),
            reference_resolver.project_root,
            guid_index=guid_index,
        )
    except (OSError, UnicodeDecodeError, ValueError):
        return None
    text = _read_asset_text(path) or ""
    ztest = next((item.value for item in result.floats if item.name == "_ZTest"), None)
    return _MaterialEvidence(
        path=rel_path,
        material_name=result.material_name,
        shader_guid=result.shader.guid,
        shader_file_id=result.shader.file_id,
        shader_name=result.shader.name,
        shader_path=result.shader.path,
        render_queue=result.render_queue if "m_CustomRenderQueue:" in text else None,
        ztest=ztest,
    )


def _project_asset_path(
    reference_resolver: ReferenceResolverService,
    asset_path: str | None,
) -> Path | None:
    if asset_path is None:
        return None
    candidate = Path(asset_path)
    if not candidate.is_absolute():
        candidate = reference_resolver.project_root / candidate
    try:
        if candidate.is_symlink():
            return None
        resolved = candidate.resolve()
        resolved.relative_to(reference_resolver.project_root.resolve())
    except (OSError, ValueError):
        return None
    return resolved

def _cached_material(
    reference_resolver: ReferenceResolverService,
    state: _ValidationState,
    material_path: str | None,
    *,
    guid_index: Mapping[str, Path] | None = None,
) -> _MaterialEvidence | None:
    if material_path is None or not material_path.lower().endswith(".mat"):
        return None
    path = _project_asset_path(reference_resolver, material_path)
    if path is None:
        return None
    rel_path = relative_to_root(path, reference_resolver.project_root)
    cached = state.material_cache.get(rel_path)
    if cached is not None:
        return cached
    material = _material_for_path(
        reference_resolver,
        path,
        rel_path,
        guid_index=guid_index,
    )
    if material is not None:
        state.material_cache[rel_path] = material
    return material


def _hierarchy_map(blocks: list[YamlBlock]) -> dict[str, str]:
    game_objects = parse_game_objects(blocks)
    transforms = parse_transforms(blocks)
    go_to_transform = {
        transform.game_object_file_id: transform for transform in transforms.values()
    }
    return {
        go_id: _hierarchy_for_go(go_id, game_objects, transforms, go_to_transform)
        for go_id in game_objects
    }


def _hierarchy_for_go(go_id, game_objects, transforms, go_to_transform) -> str:
    names: list[str] = []
    current_go_id = go_id
    while current_go_id and current_go_id in game_objects:
        names.append(game_objects[current_go_id].name)
        transform = go_to_transform.get(current_go_id)
        if transform is None or transform.father_file_id == "0":
            break
        parent = transforms.get(transform.father_file_id)
        if parent is None:
            break
        current_go_id = parent.game_object_file_id
    return "/".join(reversed([name for name in names if name]))


def _resolve_asset_path(
    reference_resolver: ReferenceResolverService,
    guid: str,
    file_id: str,
    *,
    guid_index: Mapping[str, Path] | None = None,
    resolved_asset_paths: Mapping[tuple[str, str], str | None] | None = None,
    local_ids_by_path: Mapping[Path, set[str]] | None = None,
) -> str | None:
    normalized_guid = normalize_guid(guid)
    if guid_index is not None:
        indexed_path = guid_index.get(normalized_guid)
        if indexed_path is not None:
            resolved_path = _project_asset_path(reference_resolver, str(indexed_path))
            if resolved_path is None or not _file_id_is_valid_for_path(
                reference_resolver,
                resolved_path,
                file_id,
                local_ids_by_path,
            ):
                return None
            return relative_to_root(resolved_path, reference_resolver.project_root)
        if resolved_asset_paths is not None:
            return resolved_asset_paths.get((normalized_guid, file_id))

    response = reference_resolver.resolve_reference(guid, file_id)
    if not response.success:
        return None
    asset_path = response.data.get("asset_path")
    if not isinstance(asset_path, str):
        return None
    resolved_path = _project_asset_path(reference_resolver, asset_path)
    if resolved_path is None:
        return None
    return relative_to_root(resolved_path, reference_resolver.project_root)


def _atlas_references(
    reference_resolver: ReferenceResolverService,
    font_asset_path: str | None,
) -> tuple[str, ...]:
    path = _project_asset_path(reference_resolver, font_asset_path)
    if path is None:
        return ()
    rel_path = relative_to_root(path, reference_resolver.project_root)
    text = _read_asset_text(path)
    if text is None or "m_AtlasTextures:" not in text:
        return ()
    return tuple(
        f"{rel_path}::{match.group(2).lower()}"
        for match in _ATLAS_REF.finditer(text)
    )


def _folder_evidence(
    rel_path: str,
    path: Path,
    blocks: list[YamlBlock],
) -> _FolderEvidence:
    return _FolderEvidence(
        path=rel_path,
        extension=path.suffix.lower(),
        asset_kind=_asset_kind(blocks),
    )


def _asset_kind(blocks: list[YamlBlock]) -> str | None:
    if _has_class(blocks, "21"):
        return "Material"
    return None


def _has_class(blocks: list[YamlBlock], class_id: str) -> bool:
    return any(block.class_id == class_id for block in blocks)


def _shader_mismatch(
    expected_shader: str,
    material: _MaterialEvidence | None,
    path: str,
    location: str,
) -> list[Diagnostic]:
    if material is None or material.shader_name == expected_shader:
        return []
    return [_diagnostic(
        "MATERIAL_SHADER_POLICY_MISMATCH",
        path,
        location,
        f"Expected shader {expected_shader}, observed {material.shader_name}.",
        "warning",
    )]


def _shared_expected_mismatches(
    expected_material: str,
    slots: list[_RendererSlotEvidence],
) -> list[Diagnostic]:
    return [
        _diagnostic(
            "MATERIAL_SHARED_GROUP_MISMATCH",
            slot.source_path,
            f"renderer_slot:{slot.hierarchy_path}[{slot.slot_index}]",
            f"Expected material {expected_material}, observed {slot.material_path}.",
            "warning",
        )
        for slot in slots
        if slot.material_path != expected_material
    ]


def _shared_candidate_drift(
    slots: list[_RendererSlotEvidence],
) -> list[Diagnostic]:
    counts = Counter(slot.material_path for slot in slots if slot.material_path)
    if len(counts) <= 1:
        return []
    candidates = ", ".join(f"{path}: {count}" for path, count in sorted(counts.items()))
    return [_diagnostic(
        "MATERIAL_SHARED_GROUP_DRIFT",
        slots[0].source_path,
        "shared_material_group",
        f"Multiple material candidates were found: {candidates}.",
        "warning",
    )]


def _material_detail(material: _MaterialEvidence) -> dict[str, Any]:
    detail: dict[str, Any] = {
        "path": material.path,
        "name": material.material_name,
        "shader": material.shader_name,
    }
    if material.render_queue is not None:
        detail["render_queue"] = material.render_queue
    if material.ztest is not None:
        detail["ztest"] = material.ztest
    return detail


def _renderer_detail(slot: _RendererSlotEvidence) -> dict[str, Any]:
    return {
        "source_path": slot.source_path,
        "hierarchy_path": slot.hierarchy_path,
        "slot_index": slot.slot_index,
        "material_guid": slot.material_guid,
        "material_path": slot.material_path,
    }


def _tmp_detail(tmp: _TmpEvidence) -> dict[str, Any]:
    detail: dict[str, Any] = {
        "source_path": tmp.source_path,
        "hierarchy_path": tmp.hierarchy_path,
        "font_asset_guid": tmp.font_asset_guid,
        "font_asset_path": tmp.font_asset_path,
        "material_preset_guid": tmp.material_preset_guid,
        "material_preset_path": tmp.material_preset_path,
    }
    if tmp.material is not None:
        detail["material_shader"] = tmp.material.shader_name
        if tmp.material.render_queue is not None:
            detail["render_queue"] = tmp.material.render_queue
        if tmp.material.ztest is not None:
            detail["ztest"] = tmp.material.ztest
    if tmp.atlas_references:
        detail["atlas_references"] = list(tmp.atlas_references)
    return detail


def _core_rule_config(rules: MaterialValidationRules) -> dict[str, object]:
    return {
        "status": rules.config_status,
        "path": None if rules.config_path is None else str(rules.config_path),
        "shader_name_policies": len(rules.shader_name_policies),
        "shared_material_groups": len(rules.shared_material_groups),
        "folder_policies": len(rules.folder_policies),
    }


def _diagnostic(
    code: str,
    path: str,
    location: str,
    message: str,
    severity: str,
) -> Diagnostic:
    return Diagnostic(
        path=path,
        location=location,
        detail=code,
        evidence=message,
        severity=severity,
    )


def _evidence_selected(
    source_path: str,
    hierarchy_path: str,
    scope: str,
    hierarchy_prefix: str,
) -> bool:
    return _path_under(source_path, scope) and hierarchy_path.startswith(hierarchy_prefix)


def _path_under(path: str, scope: str) -> bool:
    return path == scope or path.startswith(f"{scope.rstrip('/')}/")


def _relative(reference_resolver: ReferenceResolverService, path: Path) -> str:
    return relative_to_root(path, reference_resolver.project_root)
