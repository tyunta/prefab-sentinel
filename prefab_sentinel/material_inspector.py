"""Material slot inspector for Unity Prefab/Scene assets.

Parses Renderer components (SkinnedMeshRenderer, MeshRenderer) to extract
per-mesh material assignments, with [override]/[inherited] markers for
Prefab Variants.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from prefab_sentinel.unity_assets import (
    REFERENCE_PATTERN,
    collect_project_guid_index,
    decode_text_file,
    find_project_root,
    is_unity_builtin_guid,
    is_variant_prefab,
    normalize_guid,
)
from prefab_sentinel.unity_assets_path import resolve_scope_path
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
    source_prefab: str = ""


@dataclass(slots=True)
class MaterialInspectionResult:
    """Complete material inspection result."""

    target_path: str
    is_variant: bool
    base_prefab_path: str | None
    renderers: list[RendererMaterials]
    diagnostics: list[str] = field(default_factory=list)


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
    # Import here to avoid circular dependency at module load time
    from prefab_sentinel.material_inspector_variant import (  # noqa: PLC0415
        _collect_nested_renderers,
        _inspect_variant_materials,
    )

    proj_root = project_root or find_project_root(Path(target_path))
    path = resolve_scope_path(target_path, proj_root)
    text = decode_text_file(path)
    guid_index = collect_project_guid_index(proj_root, include_package_cache=False)

    # Issue #125: ``is_variant_prefab`` is the single source of truth for
    # the variant-versus-base decision (it already encodes the
    # "m_SourcePrefab AND no real GameObject" rule).  Calling it here
    # keeps material inspection aligned with ``orchestrator_variant``
    # and the before-cache resolver instead of maintaining a parallel
    # heuristic.
    is_variant = is_variant_prefab(text)

    # For variants, we need to:
    # 1. Load the base prefab to get renderer blocks with materials
    # 2. Parse m_Modifications to find material overrides
    if is_variant:
        return _inspect_variant_materials(
            target_path, path, text, proj_root, guid_index,
        )
    else:
        result = _inspect_base_materials(
            target_path, text, proj_root, guid_index,
        )
        nested, nested_diags = _collect_nested_renderers(
            text, guid_index, proj_root,
        )
        result.renderers.extend(nested)
        result.diagnostics.extend(nested_diags)
        return result


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


def format_materials(result: MaterialInspectionResult) -> str:
    """Format material inspection result as human-readable text."""
    if not result.renderers and not result.diagnostics:
        return "(no renderer components found)"

    lines: list[str] = []
    if not result.renderers:
        lines.append("(no renderer components found)")
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
    for diag in result.diagnostics:
        lines.append(f"[diagnostic] {diag}")
    return "\n".join(lines)
