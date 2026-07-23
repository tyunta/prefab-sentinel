from __future__ import annotations

from copy import deepcopy
from typing import Any, cast

from prefab_sentinel.inspector_profiles.model import SerializedSurface, SurfaceProperty


class ProfileRenderError(ValueError):
    pass


def _selected_view(profile: dict[str, Any], view_name: str) -> dict[str, Any]:
    views = cast(list[dict[str, Any]], profile["views"])
    matches = tuple(view for view in views if view["name"] == view_name)
    if len(matches) != 1:
        raise ProfileRenderError(f"profile must define exactly one view named {view_name!r}")
    return matches[0]


def _semantic_value(
    prop: SurfaceProperty,
    include_override_origin: bool,
) -> dict[str, Any]:
    payload = {
        "path": prop.path,
        "value": deepcopy(prop.effective_value),
    }
    if include_override_origin and prop.origin is not None:
        payload["origin"] = deepcopy(prop.origin)
    return payload


def _render_fields(
    view: dict[str, Any],
    properties: dict[str, SurfaceProperty],
    include_override_origin: bool,
) -> list[dict[str, Any]]:
    rendered: list[dict[str, Any]] = []
    for field in view["fields"]:
        prop = properties[field["path"]]
        item = {
            "name": field["name"],
            "label": field.get("label", field["name"]),
            **_semantic_value(prop, include_override_origin),
        }
        group = field.get("group")
        if group is not None:
            item["group"] = group
        enum_map = field.get("enum_map")
        if enum_map is not None:
            rendered_enum_map = deepcopy(enum_map)
            value = prop.effective_value
            enum_index = value.get("index") if isinstance(value, dict) else value
            enum_key = (
                str(enum_index)
                if isinstance(enum_index, int) and not isinstance(enum_index, bool)
                else None
            )
            item["enum_map"] = rendered_enum_map
            item["enum_label"] = (
                rendered_enum_map.get(enum_key)
                if enum_key is not None
                else None
            )
        rendered.append(item)
    return rendered


def _render_zipped_arrays(
    view: dict[str, Any],
    properties: dict[str, SurfaceProperty],
    include_override_origin: bool,
) -> list[dict[str, Any]]:
    sizes = tuple(properties[array["path"]].array_size for array in view["arrays"])
    if any(size is None for size in sizes):
        raise ProfileRenderError("zipped array view requires validated array roots")
    row_count = min(size for size in sizes if size is not None)
    rows: list[dict[str, Any]] = []
    for index in range(row_count):
        fields = {
            array["name"]: _semantic_value(
                properties[f"{array['path']}.Array.data[{index}]"],
                include_override_origin,
            )
            for array in view["arrays"]
        }
        rows.append({"index": index, "fields": fields})
    return rows


def _render_reference_table(
    view: dict[str, Any],
    properties: dict[str, SurfaceProperty],
    include_override_origin: bool,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for reference in view["references"]:
        root = properties[reference["path"]]
        entries: tuple[tuple[int | None, SurfaceProperty], ...]
        if root.array_size is None:
            entries = ((None, root),)
        else:
            entries = tuple(
                (index, properties[f"{root.path}.Array.data[{index}]"])
                for index in range(root.array_size)
            )
        for index, prop in entries:
            rows.append(
                {
                    "reference": reference["name"],
                    "label": reference.get("label", reference["name"]),
                    "index": index,
                    **_semantic_value(prop, include_override_origin),
                }
            )
    return rows


def render_requested_view(
    profile: dict[str, Any],
    surface: SerializedSurface,
    view_name: str,
    include_override_origin: bool,
    writable: dict[str, Any],
) -> dict[str, Any]:
    view = _selected_view(profile, view_name)
    properties = surface.property_map()
    rendered_view = {
        "name": view["name"],
        "kind": view["kind"],
        "writable": deepcopy(writable),
    }
    if view["kind"] == "fields":
        rendered_view["fields"] = _render_fields(view, properties, include_override_origin)
    elif view["kind"] == "zipped_arrays":
        rendered_view["rows"] = _render_zipped_arrays(view, properties, include_override_origin)
    else:
        rendered_view["rows"] = _render_reference_table(view, properties, include_override_origin)
    return {"views": [rendered_view]}
