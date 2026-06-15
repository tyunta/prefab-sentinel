from __future__ import annotations

from math import sqrt
from typing import Any, TypedDict

from prefab_sentinel.contracts import (
    Diagnostic,
    Severity,
    ToolResponse,
    error_response,
    success_response,
)
from prefab_sentinel.effective_hierarchy import (
    EffectiveHierarchyNode,
    build_effective_hierarchy,
)
from prefab_sentinel.orchestrator_variant import read_target_file
from prefab_sentinel.services.prefab_variant import PrefabVariantService
from prefab_sentinel.services.prefab_variant.overrides import OverrideEntry


class _TransformNumericParseError(ValueError):
    def __init__(self, property_path: str, value: str) -> None:
        super().__init__(f"Malformed Transform numeric value at {property_path}: {value!r}")
        self.property_path = property_path
        self.value = value


class _TransformState(TypedDict):
    position: tuple[float, float, float]
    rotation: tuple[float, float, float, float]
    scale: tuple[float, float, float]


_WORLD_UNRESOLVED = "INSPECT_TRANSFORM_WORLD_UNRESOLVED"
_AXES_3 = ("x", "y", "z")
_AXES_4 = ("x", "y", "z", "w")


def inspect_transform_effective_values(
    prefab_variant: PrefabVariantService,
    asset_path: str,
    symbol_path: str,
) -> ToolResponse:
    text = read_target_file(prefab_variant, asset_path, "INSPECT_TRANSFORM")
    if isinstance(text, ToolResponse):
        return text

    hierarchy = build_effective_hierarchy(
        prefab_variant.project_root,
        asset_path,
        text,
    )
    parent_by_key = _parent_map(hierarchy.roots)
    nodes_by_symbol = _nodes_by_symbol(hierarchy.roots)
    matches = nodes_by_symbol.get(symbol_path, [])
    if not matches or matches[0].transform is None:
        return error_response(
            "INSPECT_TRANSFORM_SYMBOL_NOT_FOUND",
            f"Transform symbol path was not found: {symbol_path}",
            data={
                "asset_path": asset_path,
                "symbol_path": symbol_path,
                "read_only": True,
            },
        )
    if len(matches) > 1:
        return error_response(
            "INSPECT_TRANSFORM_SYMBOL_AMBIGUOUS",
            f"Transform symbol path matched multiple effective nodes: {symbol_path}",
            data={
                "asset_path": asset_path,
                "symbol_path": symbol_path,
                "match_count": len(matches),
                "matches": [_node_key(node) for node in matches],
                "read_only": True,
            },
        )
    node = matches[0]

    diagnostics = list(hierarchy.diagnostics)
    try:
        local_values = _local_value_table(node)
        world_values = _world_value_table(
            node,
            parent_by_key,
            diagnostics,
            asset_path,
            symbol_path,
        )
    except _TransformNumericParseError as exc:
        diagnostic = Diagnostic(
            path=asset_path,
            location=exc.property_path,
            detail="INSPECT_TRANSFORM_NUMERIC_PARSE_ERROR",
            evidence=str(exc),
            severity=Severity.ERROR.value,
        )
        return error_response(
            "INSPECT_TRANSFORM_NUMERIC_PARSE_ERROR",
            str(exc),
            data={
                "asset_path": asset_path,
                "symbol_path": symbol_path,
                "read_only": True,
            },
            diagnostics=[*diagnostics, diagnostic],
        )
    values = {**local_values, **world_values}
    severity = Severity.WARNING if diagnostics else Severity.INFO
    return success_response(
        "INSPECT_TRANSFORM_VALUES",
        "Transform default, override, and effective values inspected.",
        severity=severity,
        data={
            "asset_path": asset_path,
            "symbol_path": symbol_path,
            "values": values,
            "read_only": True,
        },
        diagnostics=diagnostics,
    )


def _local_value_table(node: EffectiveHierarchyNode) -> dict[str, dict[str, Any]]:
    if node.transform is None:
        return {}
    return {
        "local_position": _column(
            node.transform.local_position,
            _axis_overrides(node.override_entries, "m_LocalPosition", _AXES_3),
            _AXES_3,
            node.origin,
        ),
        "local_rotation": _column(
            node.transform.local_rotation,
            _axis_overrides(node.override_entries, "m_LocalRotation", _AXES_4),
            _AXES_4,
            node.origin,
        ),
        "local_scale": _column(
            node.transform.local_scale,
            _axis_overrides(node.override_entries, "m_LocalScale", _AXES_3),
            _AXES_3,
            node.origin,
        ),
    }


def _world_value_table(
    node: EffectiveHierarchyNode,
    parent_by_key: dict[str, EffectiveHierarchyNode],
    diagnostics: list[Diagnostic],
    asset_path: str,
    symbol_path: str,
) -> dict[str, dict[str, Any]]:
    default_world = _world_state(node, parent_by_key, use_effective=False)
    effective_world = _world_state(node, parent_by_key, use_effective=True)
    if default_world is None or effective_world is None:
        diagnostics.append(
            Diagnostic(
                path=asset_path,
                location=symbol_path,
                detail=_WORLD_UNRESOLVED,
                evidence=f"World Transform values could not be computed for {symbol_path}.",
                severity=Severity.WARNING.value,
            )
        )
        unresolved = {"computed": False, "diagnostic": _WORLD_UNRESOLVED}
        return {
            "world_position": unresolved,
            "world_rotation": unresolved,
            "world_scale": unresolved,
        }

    return {
        "world_position": _world_column(
            default_world["position"],
            effective_world["position"],
            _AXES_3,
            node.origin,
        ),
        "world_rotation": _world_column(
            default_world["rotation"],
            effective_world["rotation"],
            _AXES_4,
            node.origin,
        ),
        "world_scale": _world_column(
            default_world["scale"],
            effective_world["scale"],
            _AXES_3,
            node.origin,
        ),
    }


def _column(
    default_values: tuple[float, ...],
    overrides: dict[str, float],
    axes: tuple[str, ...],
    origin: dict[str, Any],
) -> dict[str, Any]:
    effective = _apply_axis_overrides(default_values, overrides, axes)
    return {
        "default": _as_list(default_values),
        "override": overrides,
        "effective": _as_list(effective),
        "overridden": bool(overrides),
        "origin": origin,
    }


def _world_column(
    default_values: tuple[float, ...],
    effective_values: tuple[float, ...],
    axes: tuple[str, ...],
    origin: dict[str, Any],
) -> dict[str, Any]:
    override = {
        axis: _clean_float(effective)
        for axis, default, effective in zip(
            axes, default_values, effective_values, strict=True
        )
        if _clean_float(default) != _clean_float(effective)
    }
    return {
        "default": _as_list(default_values),
        "override": override,
        "effective": _as_list(effective_values),
        "overridden": bool(override),
        "computed": True,
        "origin": origin,
    }


def _world_state(
    node: EffectiveHierarchyNode,
    parent_by_key: dict[str, EffectiveHierarchyNode],
    *,
    use_effective: bool,
) -> _TransformState | None:
    local = _local_state(node, use_effective=use_effective)
    if local is None:
        return None

    parent = parent_by_key.get(_node_key(node))
    if parent is None:
        father = node.transform.father_file_id if node.transform is not None else ""
        if father not in ("", "0"):
            return None
        return local

    parent_world = _world_state(parent, parent_by_key, use_effective=use_effective)
    if parent_world is None:
        return None

    scaled_position = _scale_vector(local["position"], parent_world["scale"])
    world_position = _add_vector(
        parent_world["position"],
        _rotate_vector(scaled_position, parent_world["rotation"]),
    )
    return {
        "position": world_position,
        "rotation": _normalize_quaternion(
            _multiply_quaternion(parent_world["rotation"], local["rotation"])
        ),
        "scale": _scale_vector(parent_world["scale"], local["scale"]),
    }


def _local_state(
    node: EffectiveHierarchyNode,
    *,
    use_effective: bool,
) -> _TransformState | None:
    if node.transform is None:
        return None
    if not use_effective:
        return {
            "position": node.transform.local_position,
            "rotation": node.transform.local_rotation,
            "scale": node.transform.local_scale,
        }
    position = _apply_axis_overrides(
        node.transform.local_position,
        _axis_overrides(node.override_entries, "m_LocalPosition", _AXES_3),
        _AXES_3,
    )
    rotation = _apply_axis_overrides(
        node.transform.local_rotation,
        _axis_overrides(node.override_entries, "m_LocalRotation", _AXES_4),
        _AXES_4,
    )
    scale = _apply_axis_overrides(
        node.transform.local_scale,
        _axis_overrides(node.override_entries, "m_LocalScale", _AXES_3),
        _AXES_3,
    )
    return {
        "position": (position[0], position[1], position[2]),
        "rotation": (rotation[0], rotation[1], rotation[2], rotation[3]),
        "scale": (scale[0], scale[1], scale[2]),
    }


def _axis_overrides(
    entries: list[OverrideEntry],
    property_prefix: str,
    axes: tuple[str, ...],
) -> dict[str, float]:
    values: dict[str, float] = {}
    valid_paths = {f"{property_prefix}.{axis}": axis for axis in axes}
    for entry in entries:
        axis = valid_paths.get(entry.property_path)
        if axis is None:
            continue
        try:
            values[axis] = float(entry.value)
        except ValueError as exc:
            raise _TransformNumericParseError(entry.property_path, entry.value) from exc
    return values


def _apply_axis_overrides(
    values: tuple[float, ...],
    overrides: dict[str, float],
    axes: tuple[str, ...],
) -> tuple[float, ...]:
    return tuple(
        overrides.get(axis, value) for axis, value in zip(axes, values, strict=True)
    )


def _nodes_by_symbol(
    nodes: list[EffectiveHierarchyNode],
) -> dict[str, list[EffectiveHierarchyNode]]:
    found: dict[str, list[EffectiveHierarchyNode]] = {}
    for node in nodes:
        found.setdefault(_symbol_path(node), []).append(node)
        for symbol_path, child_matches in _nodes_by_symbol(node.children).items():
            found.setdefault(symbol_path, []).extend(child_matches)
    return found


def _parent_map(
    nodes: list[EffectiveHierarchyNode],
) -> dict[str, EffectiveHierarchyNode]:
    parents: dict[str, EffectiveHierarchyNode] = {}
    for node in nodes:
        for child in node.children:
            parents[_node_key(child)] = node
        parents.update(_parent_map(node.children))
    return parents


def _symbol_path(node: EffectiveHierarchyNode) -> str:
    return str(node.origin["effective"]["symbol_path"])


def _node_key(node: EffectiveHierarchyNode) -> str:
    return str(node.origin["effective"]["file_id"])


def _add_vector(
    lhs: tuple[float, float, float],
    rhs: tuple[float, float, float],
) -> tuple[float, float, float]:
    return (
        lhs[0] + rhs[0],
        lhs[1] + rhs[1],
        lhs[2] + rhs[2],
    )


def _scale_vector(
    lhs: tuple[float, float, float],
    rhs: tuple[float, float, float],
) -> tuple[float, float, float]:
    return (
        lhs[0] * rhs[0],
        lhs[1] * rhs[1],
        lhs[2] * rhs[2],
    )


def _rotate_vector(
    vector: tuple[float, float, float],
    rotation: tuple[float, float, float, float],
) -> tuple[float, float, float]:
    qx, qy, qz, qw = _normalize_quaternion(rotation)
    vx, vy, vz = vector
    uv = (
        qy * vz - qz * vy,
        qz * vx - qx * vz,
        qx * vy - qy * vx,
    )
    uuv = (
        qy * uv[2] - qz * uv[1],
        qz * uv[0] - qx * uv[2],
        qx * uv[1] - qy * uv[0],
    )
    return (
        vx + 2.0 * (qw * uv[0] + uuv[0]),
        vy + 2.0 * (qw * uv[1] + uuv[1]),
        vz + 2.0 * (qw * uv[2] + uuv[2]),
    )


def _multiply_quaternion(
    lhs: tuple[float, float, float, float],
    rhs: tuple[float, float, float, float],
) -> tuple[float, float, float, float]:
    ax, ay, az, aw = lhs
    bx, by, bz, bw = rhs
    return (
        aw * bx + ax * bw + ay * bz - az * by,
        aw * by - ax * bz + ay * bw + az * bx,
        aw * bz + ax * by - ay * bx + az * bw,
        aw * bw - ax * bx - ay * by - az * bz,
    )


def _normalize_quaternion(
    quat: tuple[float, float, float, float],
) -> tuple[float, float, float, float]:
    length = sqrt(sum(part * part for part in quat))
    if length == 0:
        return (0.0, 0.0, 0.0, 1.0)
    return (
        quat[0] / length,
        quat[1] / length,
        quat[2] / length,
        quat[3] / length,
    )


def _as_list(values: tuple[float, ...]) -> list[float]:
    return [_clean_float(value) for value in values]


def _clean_float(value: float) -> float:
    rounded = round(float(value), 10)
    if rounded == 0:
        return 0.0
    return rounded


__all__ = ["inspect_transform_effective_values"]
