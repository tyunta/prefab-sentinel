"""Tests for the WorldSpace Canvas + VRC_UiShape static linter (issue #121)."""

from __future__ import annotations

import unittest

from prefab_sentinel.world_canvas_inspector import (
    VRC_UI_SHAPE_SCRIPT_GUIDS,
    inspect_world_canvas_setup,
)

_VRC_UI_SHAPE_GUID = next(iter(VRC_UI_SHAPE_SCRIPT_GUIDS))


def _yaml_canvas_only(render_mode: int = 2, local_scale: float = 0.01) -> str:
    """Synthetic YAML: GameObject + RectTransform + Canvas (no VRC_UiShape).

    ``render_mode`` follows Unity's ``RenderMode`` enum: 0 = ScreenSpaceOverlay,
    1 = ScreenSpaceCamera, 2 = WorldSpace.
    """
    return (
        "%YAML 1.1\n"
        "%TAG !u! tag:unity3d.com,2011:\n"
        "--- !u!1 &100\n"
        "GameObject:\n"
        "  m_Name: UI\n"
        "  m_Component:\n"
        "  - component: {fileID: 200}\n"
        "  - component: {fileID: 300}\n"
        "--- !u!224 &200\n"
        "RectTransform:\n"
        "  m_GameObject: {fileID: 100}\n"
        "  m_LocalRotation: {x: 0, y: 0, z: 0, w: 1}\n"
        "  m_LocalPosition: {x: 0, y: 0, z: 0}\n"
        f"  m_LocalScale: {{x: {local_scale}, y: {local_scale}, z: {local_scale}}}\n"
        "  m_Children: []\n"
        "  m_Father: {fileID: 0}\n"
        "  m_AnchorMin: {x: 0, y: 0}\n"
        "  m_AnchorMax: {x: 1, y: 1}\n"
        "  m_SizeDelta: {x: 1024, y: 768}\n"
        "--- !u!223 &300\n"
        "Canvas:\n"
        "  m_GameObject: {fileID: 100}\n"
        f"  m_RenderMode: {render_mode}\n"
    )


def _yaml_canvas_uishape(
    *, render_mode: int = 2, local_scale: float = 0.01,
    include_box_collider: bool = True,
    ui_shape_guid: str = _VRC_UI_SHAPE_GUID,
) -> str:
    """Synthetic YAML: GameObject + RectTransform + Canvas + VRC_UiShape (+ optional BoxCollider).

    ``render_mode`` follows Unity's ``RenderMode`` enum: 0 = ScreenSpaceOverlay,
    1 = ScreenSpaceCamera, 2 = WorldSpace.
    """
    blocks = [
        "%YAML 1.1\n%TAG !u! tag:unity3d.com,2011:\n",
        (
            "--- !u!1 &100\n"
            "GameObject:\n"
            "  m_Name: UI\n"
            "  m_Component:\n"
            "  - component: {fileID: 200}\n"
            "  - component: {fileID: 300}\n"
            "  - component: {fileID: 400}\n"
        ),
        (
            "--- !u!224 &200\n"
            "RectTransform:\n"
            "  m_GameObject: {fileID: 100}\n"
            "  m_LocalRotation: {x: 0, y: 0, z: 0, w: 1}\n"
            "  m_LocalPosition: {x: 0, y: 0, z: 0}\n"
            f"  m_LocalScale: {{x: {local_scale}, y: {local_scale}, z: {local_scale}}}\n"
            "  m_Children: []\n"
            "  m_Father: {fileID: 0}\n"
            "  m_SizeDelta: {x: 1024, y: 768}\n"
        ),
        (
            "--- !u!223 &300\n"
            "Canvas:\n"
            "  m_GameObject: {fileID: 100}\n"
            f"  m_RenderMode: {render_mode}\n"
        ),
        (
            "--- !u!114 &400\n"
            "MonoBehaviour:\n"
            "  m_GameObject: {fileID: 100}\n"
            "  m_Enabled: 1\n"
            f"  m_Script: {{fileID: 11500000, guid: {ui_shape_guid}, type: 3}}\n"
        ),
    ]
    if include_box_collider:
        blocks.append(
            "--- !u!65 &500\n"
            "BoxCollider:\n"
            "  m_GameObject: {fileID: 100}\n"
            "  m_IsTrigger: 1\n"
            "  m_Size: {x: 1024, y: 768, z: 1}\n"
        )
    return "".join(blocks)


class WorldCanvasInspectorTests(unittest.TestCase):
    """Issue #121 — pure-function linter for VRChat WorldSpace UI."""

    def test_warns_on_non_conforming_local_scale(self) -> None:
        text = _yaml_canvas_uishape(local_scale=1.0)
        diags = inspect_world_canvas_setup(text, "Assets/UI.prefab")
        codes = [d.detail for d in diags]
        self.assertIn("WORLD_CANVAS_LOCAL_SCALE", codes)

    def test_accepts_documented_local_scale(self) -> None:
        text = _yaml_canvas_uishape(local_scale=0.01)
        diags = inspect_world_canvas_setup(text, "Assets/UI.prefab")
        self.assertEqual([], diags)

    def test_reports_missing_box_collider(self) -> None:
        text = _yaml_canvas_uishape(local_scale=0.01, include_box_collider=False)
        diags = inspect_world_canvas_setup(text, "Assets/UI.prefab")
        codes = [d.detail for d in diags]
        self.assertIn("WORLD_CANVAS_MISSING_BOX_COLLIDER", codes)

    def test_silent_on_screen_space_overlay_canvas(self) -> None:
        # ScreenSpaceOverlay = 0; Canvas-only YAML carries no VRC_UiShape.
        text = _yaml_canvas_only(render_mode=0, local_scale=1.0)
        diags = inspect_world_canvas_setup(text, "Assets/UI.prefab")
        self.assertEqual([], diags)

    def test_silent_on_screen_space_camera_canvas(self) -> None:
        # ScreenSpaceCamera = 1; Canvas-only YAML carries no VRC_UiShape.
        text = _yaml_canvas_only(render_mode=1, local_scale=1.0)
        diags = inspect_world_canvas_setup(text, "Assets/UI.prefab")
        self.assertEqual([], diags)

    def test_silent_on_screen_space_overlay_with_vrc_ui_shape(self) -> None:
        # ScreenSpaceOverlay = 0 + VRC_UiShape with non-conforming localScale
        # must NOT produce WORLD_CANVAS_LOCAL_SCALE — that rule is gated on
        # the WorldSpace render mode (= 2).  The missing-BoxCollider rule is
        # disarmed here too because the BoxCollider is included.
        text = _yaml_canvas_uishape(render_mode=0, local_scale=1.0)
        diags = inspect_world_canvas_setup(text, "Assets/UI.prefab")
        codes = [d.detail for d in diags]
        self.assertNotIn("WORLD_CANVAS_LOCAL_SCALE", codes)

    def test_silent_without_vrc_ui_shape(self) -> None:
        # WorldSpace Canvas (= 2) without VRC_UiShape — no diagnostics.
        text = _yaml_canvas_only(render_mode=2, local_scale=1.0)
        diags = inspect_world_canvas_setup(text, "Assets/UI.prefab")
        self.assertEqual([], diags)

    def test_returns_empty_list_for_empty_text(self) -> None:
        self.assertEqual([], inspect_world_canvas_setup("", "Assets/UI.prefab"))

    def test_unknown_script_guid_is_not_classified_as_vrc_ui_shape(self) -> None:
        """Issue #121 non-goal: a missing or unknown ``m_Script`` GUID
        must not be heuristically classified as VRC_UiShape.
        """
        text = _yaml_canvas_uishape(
            local_scale=1.0,
            ui_shape_guid="0" * 32,  # arbitrary unknown GUID
        )
        diags = inspect_world_canvas_setup(text, "Assets/UI.prefab")
        self.assertEqual([], diags)


if __name__ == "__main__":
    unittest.main()
