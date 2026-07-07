"""Hierarchy and material inspection functions extracted from Phase1Orchestrator."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from pathlib import Path

from prefab_sentinel.contracts import (
    Diagnostic,
    Severity,
    ToolResponse,
    error_response,
    success_response,
)
from prefab_sentinel.effective_hierarchy import (
    EffectiveHierarchyNode,
    EffectiveHierarchyResult,
    build_effective_hierarchy,
)
from prefab_sentinel.hierarchy import HierarchyNode, analyze_hierarchy, format_tree
from prefab_sentinel.inspection_context import ProjectInspectionContext
from prefab_sentinel.material_asset_inspector import (
    format_material_asset,
    inspect_material_asset as _inspect_material_asset,
)
from prefab_sentinel.material_inspector import (
    format_materials,
    inspect_materials as _inspect_materials,
)
from prefab_sentinel.orchestrator_variant import read_target_file, resolve_variant_base
from prefab_sentinel.services.prefab_variant import PrefabVariantService
from prefab_sentinel.unity_assets import GAMEOBJECT_BEARING_SUFFIXES, collect_project_guid_index


def _build_script_name_resolver(
    project_root: Path,
    script_name_map: Mapping[str, str] | None = None,
) -> Callable[[str], str | None]:
    """Build a script-name resolver keyed by Unity script GUID."""
    if script_name_map is None:
        index = collect_project_guid_index(project_root, include_package_cache=False)
        script_name_map = {
            guid: path.stem
            for guid, path in index.items()
            if path.suffix.lower() == ".cs"
        }

    def _resolve(guid: str) -> str | None:
        return script_name_map.get(guid.lower())

    return _resolve




def _format_effective_tree(
    result: EffectiveHierarchyResult,
    *,
    max_depth: int | None,
    show_components: bool,
    monobehaviour_resolver: Callable[[str], str | None] | None = None,
) -> str:
    lines: list[str] = []

    def _resolved_labels(node: EffectiveHierarchyNode) -> list[str]:
        if not node.component_descriptors:
            return list(node.components)
        labels: list[str] = []
        for descriptor in node.component_descriptors:
            if monobehaviour_resolver is not None and descriptor.script_guid:
                resolved = monobehaviour_resolver(descriptor.script_guid)
                if resolved:
                    labels.append(resolved)
                    continue
            labels.append(descriptor.label)
        return labels

    def _render(node: EffectiveHierarchyNode, prefix: str) -> None:
        if max_depth is not None and node.depth > max_depth:
            return
        labels = _resolved_labels(node) if show_components else []
        suffix = f" [{', '.join(labels)}]" if show_components and labels else ""
        lines.append(f"{prefix}{node.name}{suffix}")
        for child in node.children:
            _render(child, f"{prefix}  ")

    for root in result.roots:
        _render(root, "")
    return "\n".join(lines)

def inspect_hierarchy(
    prefab_variant: PrefabVariantService,
    target_path: str,
    *,
    max_depth: int | None = None,
    show_components: bool = True,
    expand_monobehaviour: bool = False,
    expand_prefab_instances: bool = False,
    inspection_context: ProjectInspectionContext | None = None,
) -> ToolResponse:
    text_or_error = read_target_file(prefab_variant, target_path, "INSPECT_HIERARCHY")
    if isinstance(text_or_error, ToolResponse):
        return text_or_error
    text = text_or_error
    host_text = text

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

    text, is_variant, base_prefab_path, chain_diags = resolve_variant_base(
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

    monobehaviour_resolver: Callable[[str], str | None] | None = None
    script_index_unavailable = False
    if expand_monobehaviour:
        try:
            script_name_map = (
                inspection_context.script_name_map
                if inspection_context is not None
                else None
            )
            monobehaviour_resolver = _build_script_name_resolver(
                prefab_variant.project_root,
                script_name_map,
            )
        except (OSError, RuntimeError):
            script_index_unavailable = True
            diagnostics.append(
                Diagnostic(
                    path=target_path,
                    location="expand_monobehaviour",
                    detail="warning",
                    evidence="project GUID index unavailable.",
                )
            )

    if expand_prefab_instances:
        guid_index = (
            inspection_context.guid_index
            if inspection_context is not None
            else None
        )
        effective = build_effective_hierarchy(
            prefab_variant.project_root,
            target_path,
            host_text,
            max_depth=max_depth,
            guid_index=guid_index,
            nested_prefab_cache=(
                inspection_context.nested_prefab_cache
                if inspection_context is not None
                else None
            ),
        )
        diagnostics.extend(effective.diagnostics)
        effective_data = effective.to_dict()
        effective_data.pop("diagnostics", None)
        effective_data["target_path"] = target_path
        effective_data["root_count"] = len(effective.roots)
        effective_data["tree"] = _format_effective_tree(
            effective,
            max_depth=max_depth,
            show_components=show_components,
            monobehaviour_resolver=monobehaviour_resolver,
        )
        effective_data["expand_prefab_instances"] = True
        if expand_monobehaviour:
            effective_data["expand_monobehaviour"] = True
            if script_index_unavailable:
                effective_data["script_index_unavailable"] = True
        if is_variant:
            effective_data["is_variant"] = True
            effective_data["base_prefab_path"] = base_prefab_path
        return success_response(
            "INSPECT_HIERARCHY_RESULT",
            "inspect.hierarchy completed (read-only).",
            severity=Severity.WARNING if diagnostics else Severity.INFO,
            data=effective_data,
            diagnostics=diagnostics,
        )

    result = analyze_hierarchy(text, override_counts=override_counts)
    tree_text = format_tree(
        result,
        max_depth=max_depth,
        show_components=show_components,
        monobehaviour_resolver=monobehaviour_resolver,
    )

    # Issue #238: collect per-node parent rect chain so a stretched-anchor
    # child can resolve its effective world size against the rect ancestor.
    # The map is keyed by ``HierarchyNode`` identity (id()) because the
    # node dataclass is not hashable with mutable list fields.
    parent_by_node_id: dict[int, HierarchyNode] = {}
    path_by_node_id: dict[int, str] = {}

    def _walk_parents(node: HierarchyNode, parent: HierarchyNode | None, path: str) -> None:
        node_path = f"{path}/{node.name}" if path else f"/{node.name}"
        path_by_node_id[id(node)] = node_path
        if parent is not None:
            parent_by_node_id[id(node)] = parent
        for child in node.children:
            _walk_parents(child, node, node_path)

    for r in result.roots:
        _walk_parents(r, None, "")

    def _resolve_effective_world_size(
        node: HierarchyNode,
    ) -> tuple[tuple[float, float], str]:
        anchor = node.rect_anchor
        if anchor is None:
            return ((0.0, 0.0), "unresolved")
        spans_x = anchor.anchor_min[0] != anchor.anchor_max[0]
        spans_y = anchor.anchor_min[1] != anchor.anchor_max[1]
        if not spans_x and not spans_y:
            return (anchor.size_delta, "self")
        cursor = parent_by_node_id.get(id(node))
        while cursor is not None:
            parent_anchor = cursor.rect_anchor
            if parent_anchor is not None:
                parent_spans_x = parent_anchor.anchor_min[0] != parent_anchor.anchor_max[0]
                parent_spans_y = parent_anchor.anchor_min[1] != parent_anchor.anchor_max[1]
                if not parent_spans_x and not parent_spans_y:
                    parent_w, parent_h = parent_anchor.size_delta
                    if spans_x:
                        eff_w = (
                            parent_w * (anchor.anchor_max[0] - anchor.anchor_min[0])
                            + anchor.size_delta[0]
                        )
                    else:
                        eff_w = anchor.size_delta[0]
                    if spans_y:
                        eff_h = (
                            parent_h * (anchor.anchor_max[1] - anchor.anchor_min[1])
                            + anchor.size_delta[1]
                        )
                    else:
                        eff_h = anchor.size_delta[1]
                    return ((eff_w, eff_h), "parent_chain")
            cursor = parent_by_node_id.get(id(cursor))
        return ((0.0, 0.0), "unresolved")

    rect_unresolved_paths: list[str] = []

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
        if node.rect_anchor is not None:
            anchor = node.rect_anchor
            effective_size, basis = _resolve_effective_world_size(node)
            d["rect_transform"] = {
                "anchor_min": list(anchor.anchor_min),
                "anchor_max": list(anchor.anchor_max),
                "anchored_position": list(anchor.anchored_position),
                "size_delta": list(anchor.size_delta),
                "pivot": list(anchor.pivot),
                "effective_world_size": list(effective_size),
                "effective_world_size_basis": basis,
            }
            if basis == "unresolved":
                rect_unresolved_paths.append(path_by_node_id.get(id(node), node.name))
        return d

    serialized_roots = [_serialize_node(r) for r in result.roots]
    for unresolved_path in rect_unresolved_paths:
        diagnostics.append(
            Diagnostic(
                path=target_path,
                location=unresolved_path,
                detail="INSPECT_HIERARCHY_RECT_PARENT_UNRESOLVED",
                evidence=(
                    "stretched-anchor RectTransform without resolvable "
                    "parent rect chain; effective world size is unknown."
                ),
            )
        )
    data: dict[str, object] = {
        "target_path": target_path,
        "read_only": True,
        "total_game_objects": result.total_game_objects,
        "total_components": result.total_components,
        "max_depth": result.max_depth,
        "root_count": len(result.roots),
        "tree": tree_text,
        "roots": serialized_roots,
    }
    if is_variant:
        data["is_variant"] = True
        data["base_prefab_path"] = base_prefab_path
    if expand_monobehaviour:
        data["expand_monobehaviour"] = True
        if script_index_unavailable:
            data["script_index_unavailable"] = True

    severity = Severity.WARNING if diagnostics else Severity.INFO
    return success_response(
        "INSPECT_HIERARCHY_RESULT",
        "inspect.hierarchy completed (read-only).",
        severity=severity,
        data=data,
        diagnostics=diagnostics,
    )


def inspect_materials(
    prefab_variant: PrefabVariantService,
    target_path: str,
    inspection_context: ProjectInspectionContext | None = None,
) -> ToolResponse:
    text_or_error = read_target_file(prefab_variant, target_path, "INSPECT_MATERIALS")
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
        result = _inspect_materials(
            target_path,
            project_root=prefab_variant.project_root,
            inspection_context=inspection_context,
        )
    except (OSError, UnicodeDecodeError):
        return error_response(
            "INSPECT_MATERIALS_READ_ERROR",
            "Failed to inspect materials: target asset could not be read.",
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
    *,
    inspection_context: ProjectInspectionContext | None = None,
) -> ToolResponse:
    text_or_error = read_target_file(prefab_variant, target_path, "INSPECT_MATERIAL_ASSET")
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
        result = _inspect_material_asset(
            target_path,
            project_root=prefab_variant.project_root,
            guid_index=(
                inspection_context.guid_index
                if inspection_context is not None
                else None
            ),
        )
    except (OSError, UnicodeDecodeError, ValueError):
        return error_response(
            "INSPECT_MATERIAL_ASSET_READ_ERROR",
            "Failed to inspect material asset: target asset could not be read.",
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
