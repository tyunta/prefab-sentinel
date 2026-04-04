"""Hierarchy and material inspection functions extracted from Phase1Orchestrator."""

from __future__ import annotations

from pathlib import Path

from prefab_sentinel.contracts import (
    Diagnostic,
    Severity,
    ToolResponse,
    error_response,
    success_response,
)
from prefab_sentinel.hierarchy import HierarchyNode, analyze_hierarchy, format_tree
from prefab_sentinel.material_asset_inspector import (
    format_material_asset,
    inspect_material_asset as _inspect_material_asset,
)
from prefab_sentinel.material_inspector import (
    format_materials,
    inspect_materials as _inspect_materials,
)
from prefab_sentinel.orchestrator_variant import _read_target_file, _resolve_variant_base
from prefab_sentinel.services.prefab_variant import PrefabVariantService
from prefab_sentinel.unity_assets import GAMEOBJECT_BEARING_SUFFIXES


def inspect_hierarchy(
    prefab_variant: PrefabVariantService,
    target_path: str,
    *,
    max_depth: int | None = None,
    show_components: bool = True,
) -> ToolResponse:
    text_or_error = _read_target_file(prefab_variant, target_path, "INSPECT_HIERARCHY")
    if isinstance(text_or_error, ToolResponse):
        return text_or_error
    text = text_or_error

    suffix = Path(target_path).suffix.lower()
    if suffix not in GAMEOBJECT_BEARING_SUFFIXES:
        return success_response(
            "INSPECT_HIERARCHY_NO_GAMEOBJECTS",
            f"inspect.hierarchy is not applicable to {suffix} files "
            f"(no GameObject/Transform structure). "
            f"Use validate refs to check external reference integrity.",
            severity=Severity.WARNING,
            data={"target_path": target_path, "file_type": suffix, "read_only": True},
        )

    text, is_variant, base_prefab_path, chain_diags = _resolve_variant_base(
        prefab_variant, text, target_path, "INSPECT_HIERARCHY",
    )
    override_counts: dict[str, int] | None = None
    diagnostics: list[Diagnostic] = list(chain_diags)

    if is_variant:
        overrides_response = prefab_variant.list_overrides(target_path)
        if overrides_response.success:
            counts: dict[str, int] = {}
            for ov in overrides_response.data.get("overrides", []):
                fid = ov.get("target_file_id", "")
                if fid:
                    counts[fid] = counts.get(fid, 0) + 1
            override_counts = counts
        diagnostics.extend(overrides_response.diagnostics)

    result = analyze_hierarchy(text, override_counts=override_counts)
    tree_text = format_tree(
        result,
        max_depth=max_depth,
        show_components=show_components,
    )

    def _serialize_node(node: HierarchyNode) -> dict[str, object]:
        d: dict[str, object] = {
            "file_id": node.file_id,
            "name": node.name,
            "depth": node.depth,
            "components": node.components,
            "children": [_serialize_node(c) for c in node.children],
        }
        if node.override_count > 0:
            d["override_count"] = node.override_count
        return d

    data: dict[str, object] = {
        "target_path": target_path,
        "read_only": True,
        "total_game_objects": result.total_game_objects,
        "total_components": result.total_components,
        "max_depth": result.max_depth,
        "root_count": len(result.roots),
        "tree": tree_text,
        "roots": [_serialize_node(r) for r in result.roots],
    }
    if is_variant:
        data["is_variant"] = True
        data["base_prefab_path"] = base_prefab_path

    return success_response(
        "INSPECT_HIERARCHY_RESULT",
        "inspect.hierarchy completed (read-only).",
        data=data,
        diagnostics=diagnostics,
    )


def inspect_materials(
    prefab_variant: PrefabVariantService,
    target_path: str,
) -> ToolResponse:
    text_or_error = _read_target_file(prefab_variant, target_path, "INSPECT_MATERIALS")
    if isinstance(text_or_error, ToolResponse):
        return text_or_error

    suffix = Path(target_path).suffix.lower()
    if suffix not in GAMEOBJECT_BEARING_SUFFIXES:
        return success_response(
            "INSPECT_MATERIALS_NO_RENDERERS",
            f"inspect.materials is not applicable to {suffix} files "
            f"(no Renderer components expected).",
            severity=Severity.WARNING,
            data={"target_path": target_path, "file_type": suffix, "read_only": True},
        )

    try:
        result = _inspect_materials(target_path, project_root=prefab_variant.project_root)
    except (OSError, UnicodeDecodeError) as exc:
        return error_response(
            "INSPECT_MATERIALS_READ_ERROR",
            f"Failed to inspect materials: {exc}",
            data={"target_path": target_path, "read_only": True},
        )

    tree_text = format_materials(result)

    renderer_data = []
    for renderer in result.renderers:
        slot_data = [
            {
                "index": slot.index,
                "material_name": slot.material_name,
                "material_path": slot.material_path,
                "material_guid": slot.material_guid,
                "is_override": slot.is_override,
            }
            for slot in renderer.slots
        ]
        entry: dict[str, object] = {
            "game_object_name": renderer.game_object_name,
            "renderer_type": renderer.renderer_type,
            "file_id": renderer.file_id,
            "slot_count": len(renderer.slots),
            "slots": slot_data,
        }
        if renderer.source_prefab:
            entry["source_prefab"] = renderer.source_prefab
        renderer_data.append(entry)

    data: dict[str, object] = {
        "target_path": target_path,
        "read_only": True,
        "is_variant": result.is_variant,
        "renderer_count": len(result.renderers),
        "total_material_slots": sum(len(r.slots) for r in result.renderers),
        "tree": tree_text,
        "renderers": renderer_data,
    }
    if result.is_variant:
        data["base_prefab_path"] = result.base_prefab_path
        override_count = sum(
            1 for r in result.renderers for s in r.slots if s.is_override
        )
        data["override_count"] = override_count
    if result.diagnostics:
        data["diagnostics"] = result.diagnostics

    return success_response(
        "INSPECT_MATERIALS_RESULT",
        "inspect.materials completed (read-only).",
        data=data,
    )


def inspect_material_asset(
    prefab_variant: PrefabVariantService,
    target_path: str,
) -> ToolResponse:
    text_or_error = _read_target_file(prefab_variant, target_path, "INSPECT_MATERIAL_ASSET")
    if isinstance(text_or_error, ToolResponse):
        return text_or_error

    suffix = Path(target_path).suffix.lower()
    if suffix != ".mat":
        return error_response(
            "INSPECT_MATERIAL_ASSET_NOT_MAT",
            f"Expected a .mat file, got {suffix}",
            data={"target_path": target_path, "read_only": True},
        )

    try:
        result = _inspect_material_asset(target_path, project_root=prefab_variant.project_root)
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        return error_response(
            "INSPECT_MATERIAL_ASSET_READ_ERROR",
            f"Failed to inspect material asset: {exc}",
            data={"target_path": target_path, "read_only": True},
        )

    tree_text = format_material_asset(result)

    tex_data = [
        {
            "name": t.name,
            "guid": t.guid,
            "path": t.path,
            "scale": t.scale,
            "offset": t.offset,
        }
        for t in result.textures
    ]
    float_data = [{"name": f.name, "value": f.value} for f in result.floats]
    color_data = [{"name": c.name, "value": c.value} for c in result.colors]
    int_data = [{"name": i.name, "value": i.value} for i in result.ints]

    data: dict[str, object] = {
        "target_path": target_path,
        "read_only": True,
        "material_name": result.material_name,
        "shader": {
            "guid": result.shader.guid,
            "file_id": result.shader.file_id,
            "name": result.shader.name,
            "path": result.shader.path,
        },
        "keywords": result.keywords,
        "render_queue": result.render_queue,
        "lightmap_flags": result.lightmap_flags,
        "gpu_instancing": result.gpu_instancing,
        "double_sided_gi": result.double_sided_gi,
        "properties": {
            "textures": tex_data,
            "floats": float_data,
            "colors": color_data,
            "ints": int_data,
        },
        "texture_count": len(result.textures),
        "float_count": len(result.floats),
        "color_count": len(result.colors),
        "int_count": len(result.ints),
        "tree": tree_text,
    }

    return success_response(
        "INSPECT_MATERIAL_ASSET_RESULT",
        "inspect.material_asset completed (read-only).",
        data=data,
    )
