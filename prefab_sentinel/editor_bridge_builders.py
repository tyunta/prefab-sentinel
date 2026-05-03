"""Builder functions for editor bridge action kwargs.

Converts MCP tool parameters into the dict format expected by
:func:`~prefab_sentinel.editor_bridge.send_action`.
"""
from __future__ import annotations

import math
from typing import Any

from prefab_sentinel.json_io import load_json


def build_set_camera_kwargs(
    *,
    pivot: str = "",
    yaw: float = float("nan"),
    pitch: float = float("nan"),
    distance: float = -1.0,
    orthographic: int = -1,
    position: str = "",
    look_at: str = "",
    reset_to_defaults: bool = False,
) -> dict[str, Any]:
    """Build send_action kwargs from set_camera parameters."""
    kwargs: dict[str, Any] = {}
    if position:
        p = load_json(position)
        kwargs["camera_position"] = [p["x"], p["y"], p["z"]]
    if look_at:
        la = load_json(look_at)
        kwargs["camera_look_at"] = [la["x"], la["y"], la["z"]]
    if pivot:
        pv = load_json(pivot)
        kwargs["camera_pivot"] = [pv["x"], pv["y"], pv["z"]]
    if not math.isnan(yaw):
        kwargs["yaw"] = yaw
    if not math.isnan(pitch):
        kwargs["pitch"] = pitch
    if distance >= 0:
        kwargs["distance"] = distance
    if orthographic >= 0:
        kwargs["camera_orthographic"] = orthographic
    if reset_to_defaults:
        kwargs["reset_to_defaults"] = True
    return kwargs


def build_create_empty_kwargs(
    *,
    name: str,
    parent_path: str = "",
    position: str = "",
) -> dict[str, Any]:
    """Build send_action kwargs from editor_create_empty parameters."""
    kwargs: dict[str, Any] = {"new_name": name}
    if parent_path:
        kwargs["hierarchy_path"] = parent_path
    if position:
        kwargs["property_value"] = position
    return kwargs
