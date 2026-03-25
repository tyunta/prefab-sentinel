"""Material slot inspector for Unity Prefab/Scene assets.

Parses Renderer components (SkinnedMeshRenderer, MeshRenderer) to extract
per-mesh material assignments, with [override]/[inherited] markers for
Prefab Variants.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path

from prefab_sentinel.unity_assets import (
    REFERENCE_PATTERN,
    SOURCE_PREFAB_PATTERN,
    collect_project_guid_index,
    decode_text_file,
    find_project_root,
    is_unity_builtin_guid,
    normalize_guid,
    resolve_scope_path,
)
from prefab_sentinel.unity_yaml_parser import (
    YamlBlock,
    parse_game_objects,
    split_yaml_blocks,
)

# Unity class IDs for renderer components that carry m_Materials
RENDERER_CLASS_IDS = frozenset({
    "23",   # MeshRenderer
    "137",  # SkinnedMeshRenderer
})

RENDERER_CLASS_NAMES: dict[str, str] = {
    "23": "MeshRenderer",
    "137": "SkinnedMeshRenderer",
}

# Reuse the canonical reference pattern from unity_assets
_MATERIAL_REF_PATTERN = REFERENCE_PATTERN

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


@dataclass(slots=True)
class MaterialSlot:
    """A single material slot on a renderer."""

    index: int
    material_name: str
    material_path: str
    material_guid: str
    is_override: bool  # True if overridden in variant, False if inherited


@dataclass(slots=True)
class RendererMaterials:
    """Material slots for a single renderer component."""

    game_object_name: str
    renderer_type: str
    file_id: str
    slots: list[MaterialSlot]


@dataclass(slots=True)
class MaterialInspectionResult:
    """Complete material inspection result."""

    target_path: str
    is_variant: bool
    base_prefab_path: str | None
    renderers: list[RendererMaterials]


def _parse_renderer_materials(block: YamlBlock) -> list[tuple[str, str]]:
    """Extract material references from a renderer block.

    Returns list of (file_id, guid) tuples for each material slot.
    """
    materials: list[tuple[str, str]] = []
    lines = block.text.split("\n")
    in_materials = False

    for line in lines:
        stripped = line.strip()

        if stripped == "m_Materials:" or stripped.startswith("m_Materials:"):
            in_materials = True
            # Handle inline empty: m_Materials: []
            if "[]" in stripped:
                in_materials = False
            continue

        if in_materials:
            if stripped.startswith("- "):
                ref_match = _MATERIAL_REF_PATTERN.search(stripped)
                if ref_match:
                    file_id = ref_match.group(1)
                    guid = normalize_guid(ref_match.group(2) or "")
                    materials.append((file_id, guid))
                continue
            # Non-array-element line exits the materials block
            if stripped and not stripped.startswith("-"):
                in_materials = False

    return materials


def _parse_renderer_game_object_fid(block: YamlBlock) -> str:
    """Extract the m_GameObject fileID from a renderer block."""
    for line in block.text.split("\n"):
        go_match = re.match(r"\s+m_GameObject:\s*\{fileID:\s*(-?\d+)", line)
        if go_match:
            return go_match.group(1)
    return ""


def _resolve_material_name(
    guid: str,
    guid_index: dict[str, Path],
    project_root: Path,
) -> tuple[str, str]:
    """Resolve a material GUID to (name, relative_path).

    Returns ("", "") if unresolvable.
    """
    if not guid or guid == "0" * 32:
        return ("", "")
    if is_unity_builtin_guid(guid):
        return ("Unity Built-in", "")
    asset_path = guid_index.get(guid)
    if asset_path is None:
        return ("", "")
    try:
        rel = asset_path.resolve().relative_to(project_root.resolve()).as_posix()
    except ValueError:
        rel = asset_path.as_posix()
    name = asset_path.stem
    return (name, rel)


def inspect_materials(
    target_path: str,
    project_root: Path | None = None,
) -> MaterialInspectionResult:
    """Inspect material assignments for all renderers in a prefab/scene.

    For Prefab Variants, resolves the base prefab chain and marks each
    material slot as [override] or [inherited].
    """
    proj_root = project_root or find_project_root(Path(target_path))
    path = resolve_scope_path(target_path, proj_root)
    text = decode_text_file(path)
    guid_index = collect_project_guid_index(proj_root, include_package_cache=False)

    is_variant = SOURCE_PREFAB_PATTERN.search(text) is not None

    # For variants, we need to:
    # 1. Load the base prefab to get renderer blocks with materials
    # 2. Parse m_Modifications to find material overrides
    if is_variant:
        return _inspect_variant_materials(
            target_path, path, text, proj_root, guid_index,
        )
    else:
        return _inspect_base_materials(
            target_path, text, proj_root, guid_index,
        )


def _inspect_base_materials(
    target_path: str,
    text: str,
    project_root: Path,
    guid_index: dict[str, Path],
) -> MaterialInspectionResult:
    """Inspect materials on a non-variant prefab (or scene)."""
    blocks = split_yaml_blocks(text)
    game_objects = parse_game_objects(blocks)

    renderers: list[RendererMaterials] = []
    for block in blocks:
        if block.class_id not in RENDERER_CLASS_IDS:
            continue
        if block.is_stripped:
            continue

        go_fid = _parse_renderer_game_object_fid(block)
        go = game_objects.get(go_fid)
        go_name = go.name if go and go.name else f"fileID:{go_fid}"

        mat_refs = _parse_renderer_materials(block)
        slots: list[MaterialSlot] = []
        for idx, (_file_id, guid) in enumerate(mat_refs):
            name, mat_path = _resolve_material_name(guid, guid_index, project_root)
            slots.append(MaterialSlot(
                index=idx,
                material_name=name,
                material_path=mat_path,
                material_guid=guid,
                is_override=False,  # Not a variant, no overrides
            ))

        renderer_type = RENDERER_CLASS_NAMES.get(block.class_id, f"Renderer({block.class_id})")
        renderers.append(RendererMaterials(
            game_object_name=go_name,
            renderer_type=renderer_type,
            file_id=block.file_id,
            slots=slots,
        ))

    return MaterialInspectionResult(
        target_path=target_path,
        is_variant=False,
        base_prefab_path=None,
        renderers=renderers,
    )


def _has_renderer_blocks(text: str) -> bool:
    """Return True if *text* contains non-stripped renderer YAML blocks."""
    blocks = split_yaml_blocks(text)
    return any(
        b.class_id in RENDERER_CLASS_IDS and not b.is_stripped
        for b in blocks
    )


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
        # Continue walking if this level also has no renderer blocks
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

    # Fallback: base has only stripped renderers (Model Prefab wrapping FBX).
    # Extract material data from m_Modifications instead of renderer blocks.
    if not renderers:
        renderers = _build_stripped_renderer_materials(
            base_blocks, base_text, variant_text,
            guid_index, project_root, material_overrides,
        )

    return MaterialInspectionResult(
        target_path=target_path,
        is_variant=True,
        base_prefab_path=base_prefab_path_str,
        renderers=renderers,
    )


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


def format_materials(result: MaterialInspectionResult) -> str:
    """Format material inspection result as human-readable text."""
    if not result.renderers:
        return "(no renderer components found)"

    lines: list[str] = []
    for renderer in result.renderers:
        lines.append(f"{renderer.game_object_name} ({renderer.renderer_type})")
        if not renderer.slots:
            lines.append("  (no materials)")
        for slot in renderer.slots:
            name = slot.material_name or "(none)"
            path_part = f" ({slot.material_path})" if slot.material_path else ""
            if result.is_variant:
                marker = "[override]" if slot.is_override else "[inherited]"
                lines.append(f"  [{slot.index}] {name}{path_part}  {marker}")
            else:
                lines.append(f"  [{slot.index}] {name}{path_part}")
    return "\n".join(lines)
