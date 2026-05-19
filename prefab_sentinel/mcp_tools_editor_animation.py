"""MCP tools for AnimationClip primitives (issue #243).

Three wrappers:

* ``editor_inspect_animation_clip`` — read a clip's curves and timing.
* ``editor_create_animation_clip`` — write a new clip from a curve
  specification under the project assets root. Requires the writer
  audit pair (``confirm=True`` AND a non-empty ``change_reason``).
* ``editor_apply_animation_clip`` — preview-apply an existing clip
  against a live hierarchy target through Unity's animation-mode
  preview API, recorded as a single Undo group. Issue #49: this is an
  Undo-reversible live preview, so the inverse-irreversibility audit
  principle does not gate it; it carries no audit pair.
"""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP

from prefab_sentinel.editor_bridge import send_action
from prefab_sentinel.json_io import dump_json
from prefab_sentinel.mcp_validation import require_write_audit

__all__ = [
    "editor_inspect_animation_clip",
    "editor_create_animation_clip",
    "editor_apply_animation_clip",
    "register_editor_animation_tools",
]


def _animation_clip_path_invalid_envelope(reason: str) -> dict[str, Any]:
    """Pre-bridge rejection envelope for unsafe AnimationClip target paths.

    Issue #243 / security: defence-in-depth gate complementing the
    canonical-path check on the bridge side. We surface
    ``EDITOR_CTRL_ANIMATION_CLIP_WRITE_FAILED`` so the public error code
    set stays stable; the message names the offending segment so the
    caller can fix the request without consulting external docs.
    """
    return {
        "success": False,
        "severity": "error",
        "code": "EDITOR_CTRL_ANIMATION_CLIP_WRITE_FAILED",
        "message": (
            "editor_create_animation_clip rejects path-traversal segments: "
            f"{reason}"
        ),
        "data": {},
        "diagnostics": [],
    }


def _has_unsafe_path_segment(value: str) -> bool:
    """Return True when ``value`` carries ``..`` or path separators.

    The bridge's canonical-path check is the authority; this function
    rejects the obvious traversal shapes pre-bridge so the audited tool
    never even contacts the bridge with a payload that would escape the
    project assets root.
    """
    if not isinstance(value, str) or not value:
        return False
    if "\0" in value or "\\" in value:
        return True
    return any(part == ".." for part in value.split("/"))


def _normalize_curves_payload(curves: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Coerce caller-facing ``value`` into the bridge DTO's ``values`` array.

    Spec accepts either a scalar (single-keyframe curve) or a list
    (multi-keyframe curve) on ``value``. The Unity-side ``JsonUtility``
    cannot polymorphically deserialise a union, so we normalise to
    ``values: list[float]`` here. Entries without a recognised value
    pass through with an empty values list so the bridge skips them.
    """
    normalized: list[dict[str, Any]] = []
    for entry in curves:
        if not isinstance(entry, dict):
            continue
        out: dict[str, Any] = {
            "relative_path": entry.get("relative_path", ""),
            "type": entry.get("type", ""),
            "property": entry.get("property", ""),
        }
        raw = entry.get("value", entry.get("values"))
        if isinstance(raw, (int, float)):
            out["values"] = [float(raw)]
        elif isinstance(raw, list):
            out["values"] = [float(v) for v in raw if isinstance(v, (int, float))]
        else:
            out["values"] = []
        normalized.append(out)
    return normalized


def editor_inspect_animation_clip(asset_path: str) -> dict[str, Any]:
    """Read an AnimationClip's curves and timing (issue #243).

    Read-only. The success envelope carries ``curves`` (a list of
    ``{relative_path, type, property, values}`` entries), ``length``,
    and ``frame_rate``.
    """
    return send_action(action="inspect_animation_clip", asset_path=asset_path)


def editor_create_animation_clip(
    asset_path: str,
    curves: list[dict[str, Any]],
    confirm: bool = False,
    change_reason: str = "",
) -> dict[str, Any]:
    """Write a new AnimationClip asset (issue #243, issue #53).

    ``asset_path`` is the full ``Assets/…/Name.anim`` path; the bridge
    derives the destination directory and clip filename from it.

    Each curve entry carries ``relative_path``, ``type``, ``property``,
    and ``value`` (a scalar for a single-keyframe curve or a list for a
    multi-keyframe curve sampled at the clip's default frame rate).
    Requires the writer audit pair.
    """
    audit_err = require_write_audit(
        "editor_create_animation_clip", confirm, change_reason,
    )
    if audit_err is not None:
        return audit_err
    if _has_unsafe_path_segment(asset_path):
        return _animation_clip_path_invalid_envelope(
            f"asset_path={asset_path!r} contains '..' or '\\\\'."
        )
    normalized_reason = change_reason.strip()
    # Curves are normalised so the bridge's ``JsonUtility`` sees a stable
    # ``values: list[float]`` shape.
    return send_action(
        action="create_animation_clip",
        asset_path=asset_path,
        curves_json=dump_json(_normalize_curves_payload(curves), indent=None),
        confirm=True,
        change_reason=normalized_reason,
    )


def editor_apply_animation_clip(
    asset_path: str,
    target_hierarchy_path: str,
) -> dict[str, Any]:
    """Preview-apply an AnimationClip against a live target (issue #243).

    The bridge resolves ``target_hierarchy_path`` through the Prefab
    Stage-aware resolver helper and samples the clip in animation-mode
    so the resulting state is recorded as a single Undo group that
    reverts the entire preview in one undo step.

    Issue #49: the preview is an Undo-reversible live scene change, so
    the inverse-irreversibility audit principle does not gate this tool;
    it carries no ``confirm`` / ``change_reason`` parameters.
    """
    return send_action(
        action="apply_animation_clip",
        asset_path=asset_path,
        target_hierarchy_path=target_hierarchy_path,
    )


def register_editor_animation_tools(server: FastMCP) -> None:
    """Register the three AnimationClip tools on *server*."""

    @server.tool(name="editor_inspect_animation_clip")
    def _editor_inspect_animation_clip(asset_path: str) -> dict[str, Any]:
        """Read an AnimationClip's curves and timing (issue #243).

        Args:
            asset_path: Asset path to the ``.anim`` file
                (e.g. ``Assets/Animations/Smile.anim``).
        """
        return editor_inspect_animation_clip(asset_path=asset_path)

    @server.tool(name="editor_create_animation_clip")
    def _editor_create_animation_clip(
        asset_path: str,
        curves: list[dict[str, Any]],
        confirm: bool = False,
        change_reason: str = "",
    ) -> dict[str, Any]:
        """Write a new AnimationClip (issue #243, issue #53).

        Args:
            asset_path: Full ``.anim`` asset path under ``Assets/``
                (e.g. ``Assets/Animations/Wink.anim``); the bridge
                derives the destination directory and clip filename.
            curves: List of ``{"relative_path", "type", "property",
                "value"}`` entries. ``value`` is a scalar (single
                keyframe) or list (multi-keyframe).
            confirm: Required ``True`` (writer audit gate).
            change_reason: Required non-empty audit reason.
        """
        return editor_create_animation_clip(
            asset_path=asset_path, curves=curves,
            confirm=confirm, change_reason=change_reason,
        )

    @server.tool(name="editor_apply_animation_clip")
    def _editor_apply_animation_clip(
        asset_path: str,
        target_hierarchy_path: str,
    ) -> dict[str, Any]:
        """Preview-apply an AnimationClip against a live target (issue #243).

        Issue #49: Undo-reversible live preview — no audit pair.

        Args:
            asset_path: Asset path to the ``.anim`` file to preview.
            target_hierarchy_path: Hierarchy path of the GameObject to
                drive (resolved through the Prefab Stage-aware helper).
        """
        return editor_apply_animation_clip(
            asset_path=asset_path,
            target_hierarchy_path=target_hierarchy_path,
        )
