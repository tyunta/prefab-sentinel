"""Variant material inspection helpers for Unity Prefab Variants.

Extracts material override logic that operates on PrefabInstance
m_Modifications sections, separated from the base-prefab inspection path.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path

from prefab_sentinel.material_inspector import (
    RENDERER_CLASS_IDS,
    RENDERER_CLASS_NAMES,
    MaterialInspectionResult,
    MaterialSlot,
    RendererMaterials,
    _has_renderer_blocks,
    _inspect_base_materials,
    _parse_renderer_game_object_fid,
    _parse_renderer_materials,
    _resolve_material_name,
)
from prefab_sentinel.unity_assets import (
    SOURCE_PREFAB_PATTERN,
    decode_text_file,
    normalize_guid,
)
from prefab_sentinel.unity_yaml_parser import (
    YamlBlock,
    iter_nested_prefab_children,
    parse_game_objects,
    split_yaml_blocks,
)

# Pattern for m_Materials.Array.data[N] in override propertyPath
_MATERIAL_OVERRIDE_PATH = re.compile(
    r"^m_Materials\.Array\.data\[(\d+)\]$"
)

# Patterns for PrefabInstance m_Modifications target and objectReference fields
_OVERRIDE_TARGET_PATTERN = re.compile(
    r"target:\s*\{fileID:\s*(-?\d+)(?:,\s*guid:\s*([0-9a-fA-F]{32}))?"
)
_OBJ_REF_PATTERN = re.compile(
    r"objectReference:\s*\{fileID:\s*(-?\d+)(?:,\s*guid:\s*([0-9a-fA-F]{32}))?"
)


@dataclass(slots=True)
class _ModificationEntry:
    """A single entry from a PrefabInstance m_Modifications section."""

    target_file_id: str
    property_path: str
    value: str
    object_reference_guid: str


def _iter_modifications(text: str) -> list[_ModificationEntry]:
    """Parse all entries from m_Modifications sections in YAML text."""
    entries: list[_ModificationEntry] = []
    lines = text.splitlines()
    in_modifications = False
    mod_indent = 0

    target_file_id = ""
    property_path = ""
    value = ""
    object_reference_guid = ""
    has_entry = False

    def _flush() -> None:
        nonlocal has_entry, target_file_id, property_path, value, object_reference_guid
        if has_entry and target_file_id and property_path:
            entries.append(_ModificationEntry(
                target_file_id=target_file_id,
                property_path=property_path,
                value=value,
                object_reference_guid=object_reference_guid,
            ))
        has_entry = False
        target_file_id = ""
        property_path = ""
        value = ""
        object_reference_guid = ""

    for line in lines:
        stripped = line.strip()
        indent = len(line) - len(line.lstrip(" "))

        if stripped.endswith("m_Modifications:"):
            in_modifications = True
            mod_indent = indent
            _flush()
            continue

        if in_modifications and stripped and indent <= mod_indent and not stripped.startswith("-"):
            in_modifications = False
            _flush()
            continue

        if not in_modifications:
            continue

        if stripped.startswith("- target:") or stripped.startswith("target:"):
            _flush()
            has_entry = True
            tmatch = _OVERRIDE_TARGET_PATTERN.search(stripped)
            if tmatch:
                target_file_id = tmatch.group(1)
            continue

        if not has_entry:
            continue

        if stripped.startswith("propertyPath:"):
            property_path = stripped.split(":", 1)[1].strip()
        elif stripped.startswith("value:"):
            value = stripped.split(":", 1)[1].strip()
        elif stripped.startswith("objectReference:"):
            ref_match = _OBJ_REF_PATTERN.search(stripped)
            if ref_match:
                object_reference_guid = normalize_guid(ref_match.group(2) or "")

    _flush()
    return entries


def _parse_material_overrides(
    variant_text: str,
    out: dict[tuple[str, int], str],
) -> None:
    """Parse m_Modifications to extract material slot overrides.

    Populates *out* with ``(target_file_id, slot_index) -> material_guid``.
    """
    for entry in _iter_modifications(variant_text):
        slot_match = _MATERIAL_OVERRIDE_PATH.match(entry.property_path)
        if slot_match:
            slot_index = int(slot_match.group(1))
            out[(entry.target_file_id, slot_index)] = entry.object_reference_guid


def _parse_name_overrides(text: str) -> dict[str, str]:
    """Extract m_Name property overrides from m_Modifications.

    Returns ``{target_file_id: name}`` for each modification whose
    ``propertyPath`` is ``m_Name``.
    """
    return {
        e.target_file_id: e.value
        for e in _iter_modifications(text)
        if e.property_path == "m_Name" and e.value
    }


def _build_stripped_renderer_materials(
    base_blocks: list[YamlBlock],
    base_text: str,
    variant_text: str,
    guid_index: dict[str, Path],
    project_root: Path,
    variant_overrides: dict[tuple[str, int], str],
) -> list[RendererMaterials]:
    """Build renderer material info from stripped renderer blocks.

    When the base prefab is a Model Prefab (wrapping an FBX), its renderer
    blocks are all ``stripped``.  Material assignments live in the
    PrefabInstance's ``m_Modifications`` section instead of the renderer
    blocks themselves.  This function extracts those assignments and merges
    the variant's overrides on top.
    """
    stripped_renderers = [
        b for b in base_blocks
        if b.class_id in RENDERER_CLASS_IDS and b.is_stripped
    ]
    if not stripped_renderers:
        return []

    # Base material assignments from m_Modifications
    base_mods: dict[tuple[str, int], str] = {}
    _parse_material_overrides(base_text, base_mods)

    # GO names from m_Modifications (m_Name overrides on stripped GOs)
    name_overrides = _parse_name_overrides(base_text)

    renderers: list[RendererMaterials] = []
    for block in stripped_renderers:
        # Find GO name: check if a parent GO's name was set via m_Modifications
        go_fid = _parse_renderer_game_object_fid(block)
        go_name = name_overrides.get(go_fid, f"fileID:{go_fid}") if go_fid else f"(renderer fileID:{block.file_id})"

        # Collect base material slots for this renderer from m_Modifications
        base_slots: dict[int, str] = {}
        for (fid, idx), guid in base_mods.items():
            if fid == block.file_id:
                base_slots[idx] = guid

        # Determine max slot index from both base and variant overrides
        variant_slots: dict[int, str] = {}
        for (fid, idx), guid in variant_overrides.items():
            if fid == block.file_id:
                variant_slots[idx] = guid

        all_indices = sorted(set(base_slots) | set(variant_slots))
        if not all_indices:
            continue

        slots: list[MaterialSlot] = []
        for idx in all_indices:
            is_overridden = idx in variant_slots
            effective_guid = variant_slots[idx] if is_overridden else base_slots.get(idx, "")

            name, mat_path = _resolve_material_name(
                effective_guid, guid_index, project_root,
            )
            slots.append(MaterialSlot(
                index=idx,
                material_name=name,
                material_path=mat_path,
                material_guid=effective_guid,
                is_override=is_overridden,
            ))

        renderer_type = RENDERER_CLASS_NAMES.get(block.class_id, f"Renderer({block.class_id})")
        renderers.append(RendererMaterials(
            game_object_name=go_name,
            renderer_type=renderer_type,
            file_id=block.file_id,
            slots=slots,
        ))

    return renderers


def _collect_nested_renderers(
    base_text: str,
    guid_index: dict[str, Path],
    project_root: Path,
) -> tuple[list[RendererMaterials], list[str]]:
    """Collect renderers from Nested Prefab instances in *base_text*.

    Returns (renderers, diagnostics).
    """
    renderers: list[RendererMaterials] = []

    for child in iter_nested_prefab_children(base_text, guid_index, project_root):
        child_result = _inspect_base_materials(
            str(child.path), child.text, project_root, guid_index,
        )
        for r in child_result.renderers:
            r.source_prefab = child.rel_posix
        renderers.extend(child_result.renderers)

    diagnostics: list[str] = []
    if not renderers:
        diagnostics.append(
            "No renderer blocks found in base prefab or nested prefabs"
        )
    return renderers, diagnostics


def _inspect_variant_materials(
    target_path: str,
    variant_path: Path,
    variant_text: str,
    project_root: Path,
    guid_index: dict[str, Path],
) -> MaterialInspectionResult:
    """Inspect materials on a Prefab Variant.

    Resolves the base prefab chain (multi-level), reads renderer material
    slots from the first ancestor that contains non-stripped renderer blocks,
    then applies material overrides from the variant's m_Modifications.
    """
    # Walk the Variant chain to find the first ancestor with renderer blocks.
    visited: set[str] = set()
    current_text = variant_text
    base_prefab_path_str: str | None = None
    base_text: str | None = None
    source_guid = ""
    depth_limit = 12

    for _ in range(depth_limit):
        source_match = SOURCE_PREFAB_PATTERN.search(current_text)
        if source_match is None:
            break
        parent_guid = normalize_guid(source_match.group(2))
        if parent_guid in visited:
            break
        visited.add(parent_guid)
        # Remember the first source_guid for override parsing
        if not source_guid:
            source_guid = parent_guid
        parent_path = guid_index.get(parent_guid)
        if parent_path is None or not parent_path.exists():
            break
        try:
            parent_rel = parent_path.resolve().relative_to(
                project_root.resolve()
            ).as_posix()
        except ValueError:
            parent_rel = parent_path.as_posix()
        try:
            parent_text = decode_text_file(parent_path)
        except (OSError, UnicodeDecodeError) as exc:
            logging.getLogger(__name__).debug(
                "Failed to read variant ancestor %s: %s", parent_path, exc,
            )
            break
        base_prefab_path_str = parent_rel
        base_text = parent_text
        if _has_renderer_blocks(parent_text):
            break
        # Stop if parent is a base prefab (has real GameObjects).
        # Only continue walking variant→variant chains.
        if parse_game_objects(split_yaml_blocks(parent_text)):
            break
        current_text = parent_text

    if base_text is None:
        # Cannot resolve base: return empty result
        return MaterialInspectionResult(
            target_path=target_path,
            is_variant=True,
            base_prefab_path=base_prefab_path_str,
            renderers=[],
        )

    # Parse base prefab
    base_blocks = split_yaml_blocks(base_text)
    base_game_objects = parse_game_objects(base_blocks)

    # Collect material overrides from the variant's m_Modifications
    # Key: (target_file_id, slot_index) -> objectReference guid
    material_overrides: dict[tuple[str, int], str] = {}
    _parse_material_overrides(variant_text, material_overrides)

    renderers: list[RendererMaterials] = []
    for block in base_blocks:
        if block.class_id not in RENDERER_CLASS_IDS:
            continue
        if block.is_stripped:
            continue

        go_fid = _parse_renderer_game_object_fid(block)
        go = base_game_objects.get(go_fid)
        go_name = go.name if go and go.name else f"fileID:{go_fid}"

        base_mat_refs = _parse_renderer_materials(block)
        slots: list[MaterialSlot] = []
        for idx, (_file_id, guid) in enumerate(base_mat_refs):
            override_key = (block.file_id, idx)
            is_overridden = override_key in material_overrides
            effective_guid = material_overrides[override_key] if is_overridden else guid

            name, mat_path = _resolve_material_name(
                effective_guid, guid_index, project_root,
            )
            slots.append(MaterialSlot(
                index=idx,
                material_name=name,
                material_path=mat_path,
                material_guid=effective_guid,
                is_override=is_overridden,
            ))

        renderer_type = RENDERER_CLASS_NAMES.get(block.class_id, f"Renderer({block.class_id})")
        renderers.append(RendererMaterials(
            game_object_name=go_name,
            renderer_type=renderer_type,
            file_id=block.file_id,
            slots=slots,
        ))

    # Fallback 1: base has only stripped renderers (Model Prefab wrapping FBX).
    # Extract material data from m_Modifications instead of renderer blocks.
    if not renderers:
        renderers = _build_stripped_renderer_materials(
            base_blocks, base_text, variant_text,
            guid_index, project_root, material_overrides,
        )

    # Nested Prefab expansion — renderer in a child prefab
    diagnostics: list[str] = []
    if base_text is not None:
        nested, nested_diags = _collect_nested_renderers(
            base_text, guid_index, project_root,
        )
        renderers.extend(nested)
        diagnostics.extend(nested_diags)

    return MaterialInspectionResult(
        target_path=target_path,
        is_variant=True,
        base_prefab_path=base_prefab_path_str,
        renderers=renderers,
        diagnostics=diagnostics,
    )
