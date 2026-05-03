"""Static linter for VRChat WorldSpace Canvas + VRC_UiShape constructions.

Issue #121: detects two structural mistakes that turn a WorldSpace Canvas
into either a non-functional UI panel or a 1 m thick collider that
blocks every other laser-pointer interaction in the scene:

* ``WORLD_CANVAS_LOCAL_SCALE`` (warning): a GameObject owning a
  WorldSpace ``Canvas`` plus a ``VRC_UiShape`` whose containing
  ``Transform`` / ``RectTransform`` carries a ``localScale`` other than
  the documented ``(0.01, 0.01, 0.01)``.  At ``localScale = 1`` the
  ``BoxCollider`` that ``VRC_UiShape`` auto-attaches (sized in
  Canvas-local units, where ``1.0`` means 0.01 m at the documented
  scale) reports a 1 m thickness, which both blocks UI input on the
  Canvas itself and intercepts every laser pointer in front of it.
* ``WORLD_CANVAS_MISSING_BOX_COLLIDER`` (info): a GameObject owning a
  ``VRC_UiShape`` with no sibling ``BoxCollider``.  ``VRC_UiShape``
  auto-adds the ``BoxCollider`` at build time, so this is a soft hint
  rather than an error — but the missing collider also means the YAML
  cannot be reasoned about for laser-input behaviour until the build
  step has run.

The linter is a pure function: it accepts the asset's YAML text plus a
display path for diagnostics, and returns a list of ``Diagnostic``
entries (empty when the asset is clean).  No file I/O is performed.

Detection of ``VRC_UiShape`` MonoBehaviours is GUID-based; the YAML
does not carry the type name directly.  ``VRC_UI_SHAPE_SCRIPT_GUIDS``
lists the canonical VRChat SDK script GUIDs the linter recognises.
A missing ``m_Script`` reference is *not* heuristically classified as
``VRC_UiShape`` — that fallback would turn every broken MonoBehaviour
into a false positive (Issue #121 non-goal).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from prefab_sentinel.contracts import Diagnostic
from prefab_sentinel.unity_yaml_parser import (
    YamlBlock,
    parse_game_objects,
    split_yaml_blocks,
)

# ---------------------------------------------------------------------------
# Identification constants
# ---------------------------------------------------------------------------

# Unity built-in class IDs used by this linter (see Unity's
# documented ``YAMLClassID`` table; values are stable across Editor
# versions).
_CLASS_ID_TRANSFORM = "4"
_CLASS_ID_BOX_COLLIDER = "65"
_CLASS_ID_MONOBEHAVIOUR = "114"
_CLASS_ID_CANVAS = "223"
_CLASS_ID_RECT_TRANSFORM = "224"

# Documented Canvas render mode for a WorldSpace canvas.  Unity's
# ``RenderMode`` enum is serialised as ``int`` in YAML with
# ``ScreenSpaceOverlay = 0``, ``ScreenSpaceCamera = 1``,
# ``WorldSpace = 2``.  See
# https://docs.unity3d.com/ScriptReference/RenderMode.html.
_CANVAS_RENDER_MODE_WORLD_SPACE = "2"

# Canonical ``VRC_UiShape`` script GUIDs.  GUID is the only stable
# identifier in YAML (the class name is not serialised).  Multiple
# entries cover historical releases of the VRChat World SDK.  Add new
# GUIDs here when a fresh SDK release ships with a different script
# meta — no other change is required.
VRC_UI_SHAPE_SCRIPT_GUIDS: frozenset[str] = frozenset({
    # VRC.SDKBase.VRC_UiShape — Packages/com.vrchat.base/Runtime/SDK3/Components
    "b16d3ce69d7b9214bbe9d4fe70c1b045",
})

# Documented Canvas Transform / RectTransform local scale for VRChat
# WorldSpace UI.  The Canvas is sized in pixel units via
# ``RectTransform.sizeDelta``; ``localScale = 0.01`` converts pixels
# (px) to metres (1 px → 0.01 m).
_DOCUMENTED_LOCAL_SCALE = (0.01, 0.01, 0.01)
# Floating-point tolerance for the documented scale comparison.
_LOCAL_SCALE_EPSILON = 1e-4


@dataclass(slots=True)
class _ComponentBinding:
    """One Unity component attached to a GameObject."""

    file_id: str
    class_id: str
    block: YamlBlock


# ---------------------------------------------------------------------------
# Field extraction
# ---------------------------------------------------------------------------

_RE_GAME_OBJECT_FID = re.compile(r"^\s+m_GameObject:\s*\{fileID:\s*(-?\d+)")
_RE_CANVAS_RENDER_MODE = re.compile(r"^\s+m_RenderMode:\s*(-?\d+)")
_RE_LOCAL_SCALE = re.compile(
    r"^\s+m_LocalScale:\s*\{\s*x:\s*(-?[\d.eE+\-]+),\s*"
    r"y:\s*(-?[\d.eE+\-]+),\s*z:\s*(-?[\d.eE+\-]+)\s*\}"
)
_RE_SCRIPT_GUID = re.compile(
    r"^\s+m_Script:\s*\{[^}]*guid:\s*([0-9a-fA-F]{32})"
)


def _block_game_object_fid(block: YamlBlock) -> str:
    """Return the GameObject fileID this block's component belongs to.

    Returns ``""`` if the block does not own an ``m_GameObject`` link
    (e.g. PrefabInstance or stripped components).
    """
    for line in block.text.split("\n"):
        match = _RE_GAME_OBJECT_FID.match(line)
        if match:
            return match.group(1)
    return ""


def _canvas_render_mode(block: YamlBlock) -> str:
    """Return the Canvas's serialised ``m_RenderMode`` integer (or ``""``)."""
    for line in block.text.split("\n"):
        match = _RE_CANVAS_RENDER_MODE.match(line)
        if match:
            return match.group(1)
    return ""


def _local_scale(block: YamlBlock) -> tuple[float, float, float] | None:
    """Return the (x, y, z) localScale, or ``None`` if not present."""
    for line in block.text.split("\n"):
        match = _RE_LOCAL_SCALE.match(line)
        if match:
            return (
                float(match.group(1)),
                float(match.group(2)),
                float(match.group(3)),
            )
    return None


def _script_guid(block: YamlBlock) -> str:
    """Return the MonoBehaviour's script GUID (or ``""`` when missing)."""
    for line in block.text.split("\n"):
        match = _RE_SCRIPT_GUID.match(line)
        if match:
            return match.group(1).lower()
    return ""


def _is_world_space_canvas(block: YamlBlock) -> bool:
    """Return ``True`` when the Canvas block declares WorldSpace render mode."""
    return _canvas_render_mode(block) == _CANVAS_RENDER_MODE_WORLD_SPACE


def _is_vrc_ui_shape(block: YamlBlock) -> bool:
    """Return ``True`` when the MonoBehaviour points to a known VRC_UiShape script."""
    if block.class_id != _CLASS_ID_MONOBEHAVIOUR:
        return False
    return _script_guid(block) in VRC_UI_SHAPE_SCRIPT_GUIDS


def _scale_matches_documented(scale: tuple[float, float, float]) -> bool:
    """Return ``True`` when *scale* equals the documented (0.01, 0.01, 0.01) within tolerance."""
    return all(
        abs(actual - expected) <= _LOCAL_SCALE_EPSILON
        for actual, expected in zip(scale, _DOCUMENTED_LOCAL_SCALE, strict=True)
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def inspect_world_canvas_setup(text: str, file_path: str) -> list[Diagnostic]:
    """Return diagnostics for VRChat WorldSpace Canvas + VRC_UiShape setups.

    See module docstring for the full diagnostic vocabulary.  The
    function is pure: it reads ``text`` only and does not touch the
    filesystem.
    """
    blocks = split_yaml_blocks(text)
    if not blocks:
        return []

    game_objects = parse_game_objects(blocks)

    # Map GameObject fileID -> components attached.
    components_by_go: dict[str, list[_ComponentBinding]] = {}
    for block in blocks:
        if block.is_stripped:
            continue
        if block.class_id not in {
            _CLASS_ID_TRANSFORM,
            _CLASS_ID_RECT_TRANSFORM,
            _CLASS_ID_BOX_COLLIDER,
            _CLASS_ID_MONOBEHAVIOUR,
            _CLASS_ID_CANVAS,
        }:
            continue
        go_fid = _block_game_object_fid(block)
        if not go_fid or go_fid == "0":
            continue
        components_by_go.setdefault(go_fid, []).append(
            _ComponentBinding(
                file_id=block.file_id,
                class_id=block.class_id,
                block=block,
            )
        )

    diagnostics: list[Diagnostic] = []
    for go_fid, components in components_by_go.items():
        canvas = next(
            (
                c for c in components
                if c.class_id == _CLASS_ID_CANVAS and _is_world_space_canvas(c.block)
            ),
            None,
        )
        ui_shape = next(
            (c for c in components if _is_vrc_ui_shape(c.block)),
            None,
        )
        box_collider = next(
            (c for c in components if c.class_id == _CLASS_ID_BOX_COLLIDER),
            None,
        )
        transform = next(
            (
                c for c in components
                if c.class_id in {_CLASS_ID_TRANSFORM, _CLASS_ID_RECT_TRANSFORM}
            ),
            None,
        )

        # ── Local-scale rule (warning, requires Canvas + VRC_UiShape). ──
        if canvas is not None and ui_shape is not None and transform is not None:
            scale = _local_scale(transform.block)
            if scale is not None and not _scale_matches_documented(scale):
                go_name = (
                    game_objects[go_fid].name
                    if go_fid in game_objects
                    else f"fileID:{go_fid}"
                )
                diagnostics.append(
                    Diagnostic(
                        path=file_path,
                        location=f"fileID:{go_fid}",
                        detail="WORLD_CANVAS_LOCAL_SCALE",
                        evidence=(
                            f"GameObject '{go_name}' owns a WorldSpace Canvas + "
                            f"VRC_UiShape, but localScale={scale} differs from the "
                            "documented (0.01, 0.01, 0.01); the auto-resized "
                            "BoxCollider Z=1.0 will report a 1 m thickness and "
                            "block UI input."
                        ),
                    )
                )

        # ── Missing-BoxCollider rule (info, VRC_UiShape only). ──
        if ui_shape is not None and box_collider is None:
            go_name = (
                game_objects[go_fid].name
                if go_fid in game_objects
                else f"fileID:{go_fid}"
            )
            diagnostics.append(
                Diagnostic(
                    path=file_path,
                    location=f"fileID:{go_fid}",
                    detail="WORLD_CANVAS_MISSING_BOX_COLLIDER",
                    evidence=(
                        f"GameObject '{go_name}' owns a VRC_UiShape with no "
                        "sibling BoxCollider in the YAML; the SDK auto-adds one "
                        "at build time, but a missing collider blocks static "
                        "reasoning about laser-input behaviour."
                    ),
                )
            )

    return diagnostics


__all__ = [
    "VRC_UI_SHAPE_SCRIPT_GUIDS",
    "inspect_world_canvas_setup",
]
