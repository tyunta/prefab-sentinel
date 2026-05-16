"""Centralized Unity builtin asset fileID⇔name mapping.

Unity ships two builtin asset bundles whose GUIDs are hard-coded:

- ``0000000000000000e000000000000000`` — *Library/unity default resources*
  (meshes, shaders, default materials exposed via Resources)
- ``0000000000000000f000000000000000`` — *Resources/unity_builtin_extra*
  (extra built-in assets such as Default-Material, UI sprites)

This module consolidates the known ``fileID → name`` mappings for these
bundles so that other modules do not need to hard-code magic numbers.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from prefab_sentinel.unity_assets import (
    UNITY_BUILTIN_EXTRA_GUID,
    UNITY_DEFAULT_RESOURCES_GUID,
    is_unity_builtin_guid,
    normalize_guid,
)


@dataclass(frozen=True, slots=True)
class BuiltinAssetInfo:
    """Describes a resolved Unity builtin asset."""

    guid: str
    file_id: int
    name: str
    bundle: str  # "unity default resources" | "unity_builtin_extra"


# --------------------------------------------------------------------------- #
# Known fileID → name tables (extracted from Unity source / Editor inspection)
# --------------------------------------------------------------------------- #

_DEFAULT_RESOURCES: dict[int, str] = {
    10202: "Cube",
    10206: "Cylinder",
    10207: "Sphere",
    10208: "Capsule",
    10209: "Plane",
    10210: "Quad",
    # Shaders
    10300: "Sprites-Default",
    10301: "Sprites-Mask",
    10302: "Default-Skybox",
    10306: "Standard",
    10308: "Default-Line",
    10309: "Default-ParticleSystem",
    # Misc
    10100: "Arial",
    10102: "Knob",
}

_BUILTIN_EXTRA: dict[int, str] = {
    10303: "Default-Material",
    10304: "Default-Diffuse",
    10754: "UI/Default Font",
    10800: "UISprite",
    10905: "Background",
    10906: "InputFieldBackground",
    10907: "Knob",
    10908: "Checkmark",
    10909: "DropdownArrow",
    10910: "UIMask",
}

# Combined lookup: (guid, file_id) → BuiltinAssetInfo
_LOOKUP: dict[tuple[str, int], BuiltinAssetInfo] = {}

for _fid, _name in _DEFAULT_RESOURCES.items():
    _LOOKUP[(UNITY_DEFAULT_RESOURCES_GUID, _fid)] = BuiltinAssetInfo(
        guid=UNITY_DEFAULT_RESOURCES_GUID,
        file_id=_fid,
        name=_name,
        bundle="unity default resources",
    )

for _fid, _name in _BUILTIN_EXTRA.items():
    _LOOKUP[(UNITY_BUILTIN_EXTRA_GUID, _fid)] = BuiltinAssetInfo(
        guid=UNITY_BUILTIN_EXTRA_GUID,
        file_id=_fid,
        name=_name,
        bundle="unity_builtin_extra",
    )


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #

def resolve_builtin_reference(guid: str, file_id: int) -> BuiltinAssetInfo | None:
    """Look up a builtin asset by GUID and fileID.

    Returns ``None`` when the GUID is not a builtin GUID or the fileID is
    not in the known mapping table.
    """
    normalized = normalize_guid(guid)
    if not is_unity_builtin_guid(normalized):
        return None
    return _LOOKUP.get((normalized, file_id))


def builtin_reference(guid: str, file_id: int) -> dict[str, Any]:
    """Build a Unity object-reference dict for a builtin asset."""
    return {"fileID": file_id, "guid": guid, "type": 0}


# Convenience constants for the most commonly used builtin assets
BUILTIN_SPHERE_MESH = builtin_reference(UNITY_DEFAULT_RESOURCES_GUID, 10207)
BUILTIN_DEFAULT_MATERIAL = builtin_reference(UNITY_BUILTIN_EXTRA_GUID, 10303)
BUILTIN_CUBE_MESH = builtin_reference(UNITY_DEFAULT_RESOURCES_GUID, 10202)
BUILTIN_CYLINDER_MESH = builtin_reference(UNITY_DEFAULT_RESOURCES_GUID, 10206)
BUILTIN_CAPSULE_MESH = builtin_reference(UNITY_DEFAULT_RESOURCES_GUID, 10208)
BUILTIN_PLANE_MESH = builtin_reference(UNITY_DEFAULT_RESOURCES_GUID, 10209)
BUILTIN_QUAD_MESH = builtin_reference(UNITY_DEFAULT_RESOURCES_GUID, 10210)
