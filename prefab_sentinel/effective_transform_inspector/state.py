from __future__ import annotations

from typing import Any, TypedDict

from prefab_sentinel.contracts import Diagnostic, Severity
from prefab_sentinel.effective_hierarchy import EffectiveHierarchyNode
from prefab_sentinel.services.prefab_variant.overrides import OverrideEntry

from .math import (
    _add_vector,
    _as_list,
    _clean_float,
    _multiply_quaternion,
    _normalize_quaternion,
    _rotate_vector,
    _scale_vector,
)
from .node_lookup import _node_key


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
