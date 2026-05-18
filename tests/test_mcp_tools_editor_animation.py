"""Contract tests for the AnimationClip primitives (issue #243).

Pins the wrapper-side contract for the three surfaces:

* ``editor_inspect_animation_clip`` forwards the asset path.
* ``editor_create_animation_clip`` gates on the audit pair and forwards
  the target directory, the clip name, the curve specification, and
  the audit pair verbatim.
* ``editor_apply_animation_clip`` gates on the audit pair (whitespace
  is treated as missing) and forwards the asset path, the target
  hierarchy path, and the audit pair verbatim.

#222 Mode A: assertion-shape strengthening is applied throughout;
exception-asserting tests pair the envelope code with the severity /
success / message-substring value pin so a partial regression surfaces
all observable fields in one diagnostic.
"""

from __future__ import annotations

import json
import unittest
from unittest.mock import patch

from prefab_sentinel import mcp_tools_editor_animation


def _success_envelope() -> dict:
    return {
        "success": True,
        "severity": "info",
        "code": "EDITOR_CTRL_ANIMATION_CLIP_OK",
        "message": "ok",
        "data": {"executed": True},
        "diagnostics": [],
    }


class EditorInspectAnimationClipTests(unittest.TestCase):
    """Inspect surface routing."""

    def test_inspect_reaches_inspect_action_with_asset_path(self) -> None:
        with patch.object(
            mcp_tools_editor_animation, "send_action",
            return_value=_success_envelope(),
        ) as send:
            mcp_tools_editor_animation.editor_inspect_animation_clip(
                asset_path="Assets/Animations/Smile.anim",
            )
        send.assert_called_once()
        kwargs = send.call_args.kwargs
        self.assertEqual(
            ("inspect_animation_clip", "Assets/Animations/Smile.anim"),
            (kwargs["action"], kwargs["asset_path"]),
            msg=(
                "editor_inspect_animation_clip must reach the "
                "inspect_animation_clip action with the asset path."
            ),
        )


class EditorCreateAnimationClipAuditGateTests(unittest.TestCase):
    """Create surface audit gating."""

    def test_confirm_false_rejected_pre_bridge(self) -> None:
        with patch.object(
            mcp_tools_editor_animation, "send_action",
            return_value=_success_envelope(),
        ) as send:
            response = mcp_tools_editor_animation.editor_create_animation_clip(
                asset_path="Assets/Animations/Wink.anim",
                curves=[],
                confirm=False,
                change_reason="repro",
            )
        send.assert_not_called()
        self.assertEqual(
            ("CHANGE_REASON_REQUIRED", "error", False),
            (response["code"], response["severity"], response["success"]),
            msg=(
                "editor_create_animation_clip must short-circuit with "
                "CHANGE_REASON_REQUIRED when confirm is False."
            ),
        )

    def test_blank_change_reason_rejected_pre_bridge(self) -> None:
        with patch.object(
            mcp_tools_editor_animation, "send_action",
            return_value=_success_envelope(),
        ) as send:
            response = mcp_tools_editor_animation.editor_create_animation_clip(
                asset_path="Assets/Animations/Wink.anim",
                curves=[],
                confirm=True,
                change_reason="   ",
            )
        send.assert_not_called()
        self.assertEqual(
            ("CHANGE_REASON_REQUIRED", "error", False),
            (response["code"], response["severity"], response["success"]),
            msg=(
                "Whitespace-only change_reason must be treated as "
                "missing and short-circuit with CHANGE_REASON_REQUIRED."
            ),
        )


class EditorCreateAnimationClipForwardingTests(unittest.TestCase):
    """Create surface forwards every input to the bridge verbatim."""

    def test_valid_input_forwards_single_asset_path_and_audit_pair(self) -> None:
        """Issue #53: the clip tool forwards one full asset path."""
        curves = [
            {"relative_path": "Head", "type": "Transform",
             "property": "m_LocalPosition.x", "value": 0.5},
        ]
        with patch.object(
            mcp_tools_editor_animation, "send_action",
            return_value=_success_envelope(),
        ) as send:
            mcp_tools_editor_animation.editor_create_animation_clip(
                asset_path="Assets/Animations/Wink.anim",
                curves=curves,
                confirm=True,
                change_reason="author wink expression",
            )
        send.assert_called_once()
        kwargs = send.call_args.kwargs
        decoded = json.loads(kwargs["curves_json"])
        # Value-pin: action label + the single asset path + the
        # parseable curve payload length + the audit pair, in one tuple.
        self.assertEqual(
            (
                "create_animation_clip", "Assets/Animations/Wink.anim",
                1, True, "author wink expression",
            ),
            (
                kwargs["action"], kwargs["asset_path"],
                len(decoded), kwargs["confirm"], kwargs["change_reason"],
            ),
            msg=(
                "Create must forward a single asset_path, JSON curves of "
                "matching length, confirm=True, and the audit reason "
                "verbatim (issue #53)."
            ),
        )
        # The split directory/stem fields must not survive on the wire.
        self.assertNotIn("target_dir", kwargs)
        self.assertNotIn("animation_clip_name", kwargs)

    def test_removed_directory_stem_argument_names_raise_type_error(self) -> None:
        """Issue #53: the former (target_dir, name) signature is gone."""
        with self.assertRaises(TypeError) as cm:
            mcp_tools_editor_animation.editor_create_animation_clip(
                target_dir="Assets/Animations",
                name="Wink",
                curves=[],
            )
        self.assertIn("target_dir", str(cm.exception))


class EditorCreateAnimationClipPathTraversalTests(unittest.TestCase):
    """Create surface rejects path-traversal segments pre-bridge.

    Defence-in-depth gate complementing the bridge-side canonical-path
    check (issue #243 / security review). An ``asset_path`` of
    ``Assets/../../../tmp/Evil.anim`` would satisfy the trivial
    ``StartsWith("Assets/")`` test yet escape the project assets root
    after canonicalisation.
    """

    def test_asset_path_with_parent_traversal_rejected_pre_bridge(self) -> None:
        with patch.object(
            mcp_tools_editor_animation, "send_action",
            return_value=_success_envelope(),
        ) as send:
            response = mcp_tools_editor_animation.editor_create_animation_clip(
                asset_path="Assets/../../../tmp/Evil.anim",
                curves=[],
                confirm=True,
                change_reason="security regression",
            )
        send.assert_not_called()
        self.assertEqual(
            ("EDITOR_CTRL_ANIMATION_CLIP_WRITE_FAILED", "error", False),
            (response["code"], response["severity"], response["success"]),
            msg=(
                "Any '..' segment in asset_path must short-circuit "
                "pre-bridge — Assets/../tmp escapes the project assets "
                "root after canonicalisation."
            ),
        )

    def test_asset_path_with_backslash_rejected_pre_bridge(self) -> None:
        with patch.object(
            mcp_tools_editor_animation, "send_action",
            return_value=_success_envelope(),
        ) as send:
            response = mcp_tools_editor_animation.editor_create_animation_clip(
                asset_path="Assets/Animations\\Evil.anim",
                curves=[],
                confirm=True,
                change_reason="security regression",
            )
        send.assert_not_called()
        self.assertEqual(
            ("EDITOR_CTRL_ANIMATION_CLIP_WRITE_FAILED", "error", False),
            (response["code"], response["severity"], response["success"]),
            msg=(
                "Backslash separator in asset_path must be rejected pre-"
                "bridge on the path-traversal fence."
            ),
        )


class EditorApplyAnimationClipNoAuditTests(unittest.TestCase):
    """Issue #49: the preview-apply is an Undo-reversible live change, so
    it carries no audit pair — passing a ``confirm`` argument is a
    ``TypeError``."""

    def test_confirm_argument_raises_type_error(self) -> None:
        with self.assertRaises(TypeError) as cm:
            mcp_tools_editor_animation.editor_apply_animation_clip(
                asset_path="Assets/Animations/Smile.anim",
                target_hierarchy_path="/Avatar/Body",
                confirm=True,
                change_reason="\t  \n",
            )
        self.assertIn("confirm", str(cm.exception))


class EditorApplyAnimationClipForwardingTests(unittest.TestCase):
    """Apply surface forwards every input verbatim (no audit pair, #49)."""

    def test_valid_input_forwards_asset_and_target(self) -> None:
        with patch.object(
            mcp_tools_editor_animation, "send_action",
            return_value=_success_envelope(),
        ) as send:
            mcp_tools_editor_animation.editor_apply_animation_clip(
                asset_path="Assets/Animations/Smile.anim",
                target_hierarchy_path="/Avatar/Body",
            )
        send.assert_called_once()
        kwargs = send.call_args.kwargs
        self.assertEqual(
            (
                "apply_animation_clip",
                "Assets/Animations/Smile.anim",
                "/Avatar/Body",
            ),
            (
                kwargs["action"], kwargs["asset_path"],
                kwargs["target_hierarchy_path"],
            ),
            msg=(
                "Apply must forward the asset path and target hierarchy "
                "path; issue #49 removed the audit pair."
            ),
        )
        # The de-audited tool no longer forwards confirm/change_reason.
        self.assertNotIn("confirm", kwargs)
        self.assertNotIn("change_reason", kwargs)


if __name__ == "__main__":
    unittest.main()
