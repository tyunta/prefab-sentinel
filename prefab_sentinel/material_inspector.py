"""Material slot inspector for Unity Prefab/Scene assets.

Parses Renderer components (SkinnedMeshRenderer, MeshRenderer) to extract
per-mesh material assignments, with [override]/[inherited] markers for
Prefab Variants.
"""

from __future__ import annotations

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
    CLASS_ID_GAMEOBJECT,
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

# Pattern to extract material reference entries from m_Materials array
_MATERIAL_REF_PATTERN = re.compile(
    r"\{fileID:\s*(-?\d+)(?:,\s*guid:\s*([0-9a-fA-F]{32}))?(?:,\s*type:\s*(-?\d+))?\}"
)

# Pattern for m_Materials.Array.data[N] in override propertyPath
_MATERIAL_OVERRIDE_PATH = re.compile(
    r"^m_Materials\.Array\.data\[(\d+)\]$"
)


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
        for idx, (file_id, guid) in enumerate(mat_refs):
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


def _inspect_variant_materials(
    target_path: str,
    variant_path: Path,
    variant_text: str,
    project_root: Path,
    guid_index: dict[str, Path],
) -> MaterialInspectionResult:
    """Inspect materials on a Prefab Variant.

    Resolves the base prefab chain, reads renderer material slots from the
    base, then applies material overrides from the variant's m_Modifications.
    """
    # Resolve the base prefab
    source_match = SOURCE_PREFAB_PATTERN.search(variant_text)
    base_prefab_path_str: str | None = None
    base_text: str | None = None
    source_guid = ""

    if source_match:
        source_guid = normalize_guid(source_match.group(2))
        base_path = guid_index.get(source_guid)
        if base_path and base_path.exists():
            try:
                base_prefab_path_str = base_path.resolve().relative_to(
                    project_root.resolve()
                ).as_posix()
            except ValueError:
                base_prefab_path_str = base_path.as_posix()
            try:
                base_text = decode_text_file(base_path)
            except (OSError, UnicodeDecodeError):
                base_text = None

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
    _parse_material_overrides(variant_text, source_guid, material_overrides)

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
        for idx, (file_id, guid) in enumerate(base_mat_refs):
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

    return MaterialInspectionResult(
        target_path=target_path,
        is_variant=True,
        base_prefab_path=base_prefab_path_str,
        renderers=renderers,
    )


def _parse_material_overrides(
    variant_text: str,
    source_guid: str,
    out: dict[tuple[str, int], str],
) -> None:
    """Parse m_Modifications to extract material slot overrides.

    Populates *out* with ``(target_file_id, slot_index) -> material_guid``.
    """
    lines = variant_text.splitlines()
    in_modifications = False
    mod_indent = 0

    target_file_id = ""
    property_path = ""
    object_reference_guid = ""
    has_entry = False

    def _flush() -> None:
        nonlocal has_entry, target_file_id, property_path, object_reference_guid
        if has_entry and target_file_id and property_path:
            slot_match = _MATERIAL_OVERRIDE_PATH.match(property_path)
            if slot_match:
                slot_index = int(slot_match.group(1))
                out[(target_file_id, slot_index)] = object_reference_guid
        has_entry = False
        target_file_id = ""
        property_path = ""
        object_reference_guid = ""

    target_pattern = re.compile(
        r"target:\s*\{fileID:\s*(-?\d+)(?:,\s*guid:\s*([0-9a-fA-F]{32}))?"
    )
    obj_ref_pattern = re.compile(
        r"objectReference:\s*\{fileID:\s*(-?\d+)(?:,\s*guid:\s*([0-9a-fA-F]{32}))?"
    )

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
            tmatch = target_pattern.search(stripped)
            if tmatch:
                target_file_id = tmatch.group(1)
            continue

        if not has_entry:
            continue

        if stripped.startswith("propertyPath:"):
            property_path = stripped.split(":", 1)[1].strip()
        elif stripped.startswith("objectReference:"):
            ref_match = obj_ref_pattern.search(stripped)
            if ref_match:
                object_reference_guid = normalize_guid(ref_match.group(2) or "")

    _flush()


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
