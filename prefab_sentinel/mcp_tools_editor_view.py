"""MCP tools for view-oriented editor bridge operations."""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP

from prefab_sentinel.bridge_constants import CONSOLE_LOG_BUFFER_MAX_ENTRIES
from prefab_sentinel.editor_bridge import send_action
from prefab_sentinel.editor_bridge_builders import build_set_camera_kwargs

__all__ = [
    "editor_console",
    "editor_recompile",
    "editor_refresh",
    "editor_screenshot",
    "editor_force_scene_view_refresh",
    "register_editor_view_tools",
    "CONSOLE_MAX_ENTRIES_MIN",
    "CONSOLE_MAX_ENTRIES_MAX",
    "RECOMPILE_AND_WAIT_TIMEOUT_MAX_SEC",
    "SCREENSHOT_CROP_ROI_PRESETS",
    "SCREENSHOT_VIEW_ALLOWLIST",
    "SCREENSHOT_ANGLE_PRESETS",
    "SCREENSHOT_ANGLE_DEFAULT",
    "SCREENSHOT_TARGET_MODE_ALLOWLIST",
    "SCREENSHOT_PROJECTION_ALLOWLIST",
]

# Issue #249: canonical screenshot region preset allowlist.  Mirrors the
# bridge-side ``SupportedScreenshotPresets`` array so an unrecognised
# preset short-circuits at the wrapper without ever reaching the bridge.
SCREENSHOT_CROP_ROI_PRESETS: tuple[str, ...] = (
    "eye_left",
    "eye_right",
    "mouth",
    "auto_face",
)

# Issue #84: canonical angle-preset allowlist for ``editor_screenshot``
# target-oriented capture mode.  Mirrors ``ObjectCaptureFramingMath
# .PresetNames`` on the bridge side; both layers enforce the same
# Renderer target presets plus the UI-only current-camera selector.
# The bridge rejects current_camera on renderer captures.
SCREENSHOT_ANGLE_PRESETS: tuple[str, ...] = (
    "front",
    "three_quarter",
    "back",
    "right",
    "left",
    "top",
    "current_camera",
)

# Default preset used when the caller supplies ``target`` without
# ``angle``.  Issue #84 body: ``three_quarter`` is the documented
# default angle for the target-oriented capture mode.
SCREENSHOT_ANGLE_DEFAULT: str = "three_quarter"

# Issue #259: canonical view-selector allowlist for ``editor_screenshot``.
# The selector is interpolated into the output filename on the bridge
# side, so an unvalidated value would let a caller compose path
# separators or traversal sequences into the screenshots-directory
# write.  Comparison is exact case-sensitive equality against this
# tuple; the bridge-side allowlist is identical so the two layers
# cannot drift.
SCREENSHOT_VIEW_ALLOWLIST: tuple[str, ...] = ("scene", "game")
SCREENSHOT_TARGET_MODE_ALLOWLIST: tuple[str, ...] = (
    "auto",
    "renderer",
    "world_space_ui",
)
SCREENSHOT_PROJECTION_ALLOWLIST: tuple[str, ...] = (
    "auto",
    "perspective",
    "orthographic",
)

# Issue #131: inclusive size bounds shared by the editor-console MCP tool
# and the C# bridge handler.  The upper bound mirrors the published
# ``ConsoleLogBuffer.DefaultCapacity`` because the bridge can never return
# more entries than the ring buffer has retained; the lower bound rejects
# 0 / negative values that would degenerate into a no-op or an error.
CONSOLE_MAX_ENTRIES_MIN = 1
CONSOLE_MAX_ENTRIES_MAX = CONSOLE_LOG_BUFFER_MAX_ENTRIES

# Default forwarded to the bridge when the caller omits ``max_entries``.
# The bridge's own default is the same (200) — see the
# ``capture_console_logs`` handler — but stating it here keeps the
# Python-side contract self-describing.
CONSOLE_MAX_ENTRIES_DEFAULT = 200

# Issue #118: default budget for the synchronous recompile-and-wait MCP
# tool, expressed in seconds.  Sized so a cold compile-and-reload of a
# typical project finishes within the budget on commodity workstations
# without blocking the caller indefinitely.
RECOMPILE_AND_WAIT_DEFAULT_TIMEOUT_SEC = 60.0

# Issue #134: published acceptance range for the synchronous
# recompile-and-wait wait budget.  The lower bound is exclusive at zero —
# 0 / negative budgets degenerate into an immediate timeout that pins the
# poll loop on the bridge.  The upper bound caps the worst-case time a
# single MCP call can keep the Editor Bridge poll loop alive; arbitrarily
# large values would let a caller block the bridge for half an hour or
# more per request.  Drift between this Python constant and the C# bridge
# constant ``RecompileAndWaitTimeoutMaxSec`` would let an oversized budget
# slip past the client check, so both sides validate identically.
RECOMPILE_AND_WAIT_TIMEOUT_MAX_SEC = 1800.0


# 0 preserves the existing "use current view size" contract; positive
# dimensions are capped before Unity allocates RenderTexture / Texture2D.
SCREENSHOT_DIMENSION_MIN = 0
SCREENSHOT_DIMENSION_MAX = 4096

def _max_entries_out_of_range_envelope(value: int) -> dict[str, Any]:
    """Return the canonical MAX_ENTRIES_OUT_OF_RANGE envelope.

    The message names the supplied value and both inclusive bounds so the
    caller can fix the request without consulting external docs.
    """
    return {
        "success": False,
        "severity": "error",
        "code": "MAX_ENTRIES_OUT_OF_RANGE",
        "message": (
            f"max_entries={value} is outside the inclusive range "
            f"[{CONSOLE_MAX_ENTRIES_MIN}, {CONSOLE_MAX_ENTRIES_MAX}] "
            "(buffered console entries)."
        ),
        "data": {
            "supplied": value,
            "min": CONSOLE_MAX_ENTRIES_MIN,
            "max": CONSOLE_MAX_ENTRIES_MAX,
        },
        "diagnostics": [],
    }


def _recompile_timeout_out_of_range_envelope(value: float) -> dict[str, Any]:
    """Return the canonical COMPILE_TIMEOUT_OUT_OF_RANGE envelope for the
    recompile-and-wait surface.

    The message names the supplied value and both bounds so the caller
    can fix the request without consulting external docs.  The lower
    bound is exclusive at zero; the upper bound is inclusive.
    """
    return {
        "success": False,
        "severity": "error",
        "code": "COMPILE_TIMEOUT_OUT_OF_RANGE",
        "message": (
            f"timeout_sec={value} is outside the accepted range "
            f"(0, {RECOMPILE_AND_WAIT_TIMEOUT_MAX_SEC}] (seconds)."
        ),
        "data": {
            "supplied": value,
            "min_exclusive": 0.0,
            "max": RECOMPILE_AND_WAIT_TIMEOUT_MAX_SEC,
        },
        "diagnostics": [],
    }


def editor_recompile(
    timeout_sec: float = RECOMPILE_AND_WAIT_DEFAULT_TIMEOUT_SEC,
) -> dict[str, Any]:
    """Recompile scripts synchronously and wait for the Editor to finish.

    Issue #54: the bare name marks this as the synchronous, blocking
    tool — it returns only when the Editor reports compilation finished,
    the compiled-assembly modification time is later than the request's
    call-time observation, and the post-reload signal has fired since the
    request was issued.  Forwards the caller-supplied wait budget to the
    bridge as the request payload's ``timeout_sec`` field.

    Issue #134: ``timeout_sec`` must satisfy
    ``0 < timeout_sec <= RECOMPILE_AND_WAIT_TIMEOUT_MAX_SEC``.  Out-of-range
    requests return the ``COMPILE_TIMEOUT_OUT_OF_RANGE`` envelope without
    contacting the bridge.
    """
    if timeout_sec <= 0.0 or timeout_sec > RECOMPILE_AND_WAIT_TIMEOUT_MAX_SEC:
        return _recompile_timeout_out_of_range_envelope(timeout_sec)

    # The transport poll budget must outlive the bridge's own wait so the
    # response file is observed before the Python side gives up; we add a
    # 5 s dispatch margin on top of the caller-supplied budget.
    transport_poll_sec = int(timeout_sec) + 5
    return send_action(
        action="editor_recompile_and_wait",
        timeout_sec=transport_poll_sec,
        request_extras={"timeout_sec": float(timeout_sec)},
    )


def editor_refresh() -> dict[str, Any]:
    """Refresh the asset database and report any triggered compile.

    Issue #70: the refresh is compile-aware. It asks the bridge to wait
    for and report a refresh-triggered compile, so the result reflects
    refresh-OK (no compile), compile-success, or compile-failure with the
    real compiler diagnostics. The transport poll budget covers a compile
    plus the domain reload, matching the recompile-and-wait budget.

    Returns the Editor Bridge response envelope unmodified.
    """
    return send_action(
        action="refresh_asset_database",
        timeout_sec=int(RECOMPILE_AND_WAIT_DEFAULT_TIMEOUT_SEC) + 5,
        wait_for_compile=True,
    )


def _screenshot_view_invalid_envelope(value: str) -> dict[str, Any]:
    """Return the canonical ``SCREENSHOT_VIEW_INVALID`` envelope (issue #259).

    The message names the supplied value (quoted via ``repr``) and the
    accepted selectors so the caller can correct the request without
    consulting external docs.  ``data`` carries the supplied value and
    the accepted set as a list for callers that prefer structured
    handling over message parsing.
    """
    return {
        "success": False,
        "severity": "error",
        "code": "SCREENSHOT_VIEW_INVALID",
        "message": (
            f"view={value!r} is not one of the accepted selectors "
            f"({', '.join(SCREENSHOT_VIEW_ALLOWLIST)}); the value is "
            "interpolated into the screenshot filename so non-allowlisted "
            "inputs are rejected at the wrapper before any bridge "
            "transport activity."
        ),
        "data": {
            "supplied": value,
            "allowed_views": list(SCREENSHOT_VIEW_ALLOWLIST),
        },
        "diagnostics": [],
    }


def _crop_roi_invalid_envelope(value: str) -> dict[str, Any]:
    """Return the canonical ``CROP_ROI_INVALID`` envelope.

    The message names the supplied value and the accepted preset set so
    the caller can fix the request without consulting external docs.
    """
    return {
        "success": False,
        "severity": "error",
        "code": "CROP_ROI_INVALID",
        "message": (
            f"crop_roi={value!r} is neither one of the four named presets "
            f"({', '.join(SCREENSHOT_CROP_ROI_PRESETS)}) nor a comma-separated "
            "quadruple of non-negative integers 'x,y,w,h'."
        ),
        "data": {
            "supplied": value,
            "allowed_presets": list(SCREENSHOT_CROP_ROI_PRESETS),
        },
        "diagnostics": [],
    }


def _screenshot_angle_invalid_envelope(value: str) -> dict[str, Any]:
    """Return the canonical ``SCREENSHOT_ANGLE_INVALID`` envelope.

    Issue #84: the message names the supplied value and the accepted
    six-preset set so the caller can correct the request without
    consulting external docs.
    """
    return {
        "success": False,
        "severity": "error",
        "code": "SCREENSHOT_ANGLE_INVALID",
        "message": (
            f"angle={value!r} is not one of the accepted preset names "
            f"({', '.join(SCREENSHOT_ANGLE_PRESETS)}); the target-oriented "
            "screenshot mode rejects non-allowlisted angles at the wrapper "
            "before any bridge transport activity."
        ),
        "data": {
            "supplied": value,
            "allowed_angles": list(SCREENSHOT_ANGLE_PRESETS),
        },
        "diagnostics": [],
    }


def _screenshot_target_invalid_view_envelope(view: str) -> dict[str, Any]:
    """Return the canonical ``SCREENSHOT_TARGET_INVALID_VIEW`` envelope.

    Issue #84: the target-oriented capture mode is Scene-view-only
    because the framing math drives ``SceneView.LookAt``; the Game view
    has no equivalent.
    """
    return {
        "success": False,
        "severity": "error",
        "code": "SCREENSHOT_TARGET_INVALID_VIEW",
        "message": (
            f"view={view!r} is incompatible with the target-oriented "
            "capture mode; target framing drives the SceneView only, so "
            "``target`` requires view='scene'."
        ),
        "data": {
            "supplied": view,
            "allowed_views": ["scene"],
        },
        "diagnostics": [],
    }


def _screenshot_target_crop_conflict_envelope(
    target: str, crop_roi: str,
) -> dict[str, Any]:
    """Return the canonical ``SCREENSHOT_TARGET_CROP_CONFLICT`` envelope.

    Issue #84: the four face-feature ``crop_roi`` presets re-frame the
    SceneView (``ResolvePresetTarget``), so combining one with the
    target-oriented framing path would request two competing re-frame
    operations on the same call.  Pixel-rectangle ``crop_roi`` is not
    rejected — it post-crops the rendered frame and is orthogonal to
    framing.
    """
    return {
        "success": False,
        "severity": "error",
        "code": "SCREENSHOT_TARGET_CROP_CONFLICT",
        "message": (
            f"crop_roi={crop_roi!r} (face-feature preset) cannot be "
            f"combined with target={target!r}: target framing and "
            "face-feature preset cropping each drive a SceneView "
            "re-frame, so requesting both in one call is rejected. "
            "Pixel-rectangle crop_roi together with target is "
            "supported."
        ),
        "data": {
            "supplied_target": target,
            "supplied_crop_roi": crop_roi,
            "allowed_crop_roi_with_target": "pixel rectangle 'x,y,w,h' only",
        },
        "diagnostics": [],
    }


def _screenshot_target_mode_invalid_envelope(value: str) -> dict[str, Any]:
    return {
        "success": False,
        "severity": "error",
        "code": "SCREENSHOT_TARGET_MODE_INVALID",
        "message": (
            f"target_mode={value!r} is not one of "
            f"({', '.join(SCREENSHOT_TARGET_MODE_ALLOWLIST)})."
        ),
        "data": {
            "supplied": value,
            "allowed_target_modes": list(SCREENSHOT_TARGET_MODE_ALLOWLIST),
        },
        "diagnostics": [],
    }


def _screenshot_projection_invalid_envelope(value: str) -> dict[str, Any]:
    return {
        "success": False,
        "severity": "error",
        "code": "SCREENSHOT_PROJECTION_INVALID",
        "message": (
            f"projection={value!r} is not one of "
            f"({', '.join(SCREENSHOT_PROJECTION_ALLOWLIST)})."
        ),
        "data": {
            "supplied": value,
            "allowed_projections": list(SCREENSHOT_PROJECTION_ALLOWLIST),
        },
        "diagnostics": [],
    }


def _screenshot_padding_ratio_invalid_envelope(value: float) -> dict[str, Any]:
    return {
        "success": False,
        "severity": "error",
        "code": "SCREENSHOT_PADDING_RATIO_INVALID",
        "message": (
            f"padding_ratio={value!r} must be between 0.0 and 1.0 inclusive."
        ),
        "data": {"supplied": value},
        "diagnostics": [],
    }


def _screenshot_dimensions_out_of_range_envelope(
    width: int, height: int,
) -> dict[str, Any]:
    return {
        "success": False,
        "severity": "error",
        "code": "SCREENSHOT_DIMENSIONS_OUT_OF_RANGE",
        "message": (
            f"width={width} and height={height} must each be 0 or within "
            f"[1, {SCREENSHOT_DIMENSION_MAX}] pixels."
        ),
        "data": {
            "width": width,
            "height": height,
            "min": SCREENSHOT_DIMENSION_MIN,
            "max": SCREENSHOT_DIMENSION_MAX,
        },
        "diagnostics": [],
    }


def _is_valid_pixel_rect(value: str) -> bool:
    """Return whether ``value`` is a comma-separated quadruple of
    non-negative integers (the pixel-rectangle escape hatch for
    ``crop_roi``).
    """
    parts = value.split(",")
    if len(parts) != 4:
        return False
    for part in parts:
        token = part.strip()
        if not token.lstrip("-").isdigit():
            return False
        if int(token) < 0:
            return False
    return True


def editor_screenshot(
    view: str = "scene",
    width: int = 0,
    height: int = 0,
    refresh: bool = True,
    crop_roi: str = "",
    target: str = "",
    angle: str = SCREENSHOT_ANGLE_DEFAULT,
    target_mode: str = "auto",
    padding_ratio: float = 0.10,
    projection: str = "auto",
) -> dict[str, Any]:
    """Capture a screenshot of the Unity Editor (issues #249, #259, #84, #95)."""
    if view not in SCREENSHOT_VIEW_ALLOWLIST:
        return _screenshot_view_invalid_envelope(view)
    if target_mode not in SCREENSHOT_TARGET_MODE_ALLOWLIST:
        return _screenshot_target_mode_invalid_envelope(target_mode)
    if projection not in SCREENSHOT_PROJECTION_ALLOWLIST:
        return _screenshot_projection_invalid_envelope(projection)
    if padding_ratio < 0.0 or padding_ratio > 1.0:
        return _screenshot_padding_ratio_invalid_envelope(padding_ratio)
    if (
        width < SCREENSHOT_DIMENSION_MIN
        or height < SCREENSHOT_DIMENSION_MIN
        or width > SCREENSHOT_DIMENSION_MAX
        or height > SCREENSHOT_DIMENSION_MAX
    ):
        return _screenshot_dimensions_out_of_range_envelope(width, height)
    if (
        crop_roi
        and crop_roi not in SCREENSHOT_CROP_ROI_PRESETS
        and not _is_valid_pixel_rect(crop_roi)
    ):
        return _crop_roi_invalid_envelope(crop_roi)
    if target:
        if angle not in SCREENSHOT_ANGLE_PRESETS:
            return _screenshot_angle_invalid_envelope(angle)
        if view != "scene":
            return _screenshot_target_invalid_view_envelope(view)
        if crop_roi in SCREENSHOT_CROP_ROI_PRESETS:
            return _screenshot_target_crop_conflict_envelope(target, crop_roi)
    if refresh:
        refresh_response = send_action(action="refresh_asset_database")
        if refresh_response.get("success") is not True:
            return refresh_response
    kwargs: dict[str, Any] = {
        "action": "capture_screenshot",
        "view": view,
        "width": width,
        "height": height,
    }
    if crop_roi:
        kwargs["crop_roi"] = crop_roi
    if target:
        kwargs["target"] = target
        kwargs["angle"] = angle
        kwargs["target_mode"] = target_mode
        kwargs["padding_ratio"] = padding_ratio
        kwargs["projection"] = projection
    return send_action(**kwargs)


def editor_force_scene_view_refresh() -> dict[str, Any]:
    """Trigger the bridge-side force-refresh primitive (issue #242).

    Sets ``forceMatrixRecalculationPerRender`` on every active
    ``SkinnedMeshRenderer`` and drives ``QueuePlayerLoopUpdate`` in one
    bridge round-trip; the success envelope reports the integer count of
    renderers touched.
    """
    return send_action(action="force_scene_view_refresh")


def editor_console(
    max_entries: int = CONSOLE_MAX_ENTRIES_DEFAULT,
    log_type_filter: str = "all",
    since_seconds: float = 60.0,
    classification_filter: str = "all",
    order: str = "newest_first",
    cursor: str = "",
    phase_filter: str = "all",
    since_sequence: int | None = None,
    since_request_id: str = "",
) -> dict[str, Any]:
    if (
        max_entries < CONSOLE_MAX_ENTRIES_MIN
        or max_entries > CONSOLE_MAX_ENTRIES_MAX
    ):
        return _max_entries_out_of_range_envelope(max_entries)

    request: dict[str, Any] = {
        "action": "capture_console_logs",
        "max_entries": max_entries,
        "log_type_filter": log_type_filter,
        "since_seconds": since_seconds,
        "classification_filter": classification_filter,
        "order": order,
        "cursor": cursor,
        "phase_filter": phase_filter,
    }
    if since_sequence is not None:
        request["since_sequence"] = since_sequence
    if since_request_id:
        request["since_request_id"] = since_request_id
    return send_action(**request)


def register_editor_view_tools(server: FastMCP) -> None:
    """Register view-oriented editor bridge tools on *server*."""

    @server.tool(name="editor_screenshot")
    def _editor_screenshot(
        view: str = "scene",
        width: int = 0,
        height: int = 0,
        refresh: bool = True,
        crop_roi: str = "",
        target: str = "",
        angle: str = SCREENSHOT_ANGLE_DEFAULT,
        target_mode: str = "auto",
        padding_ratio: float = 0.10,
        projection: str = "auto",
    ) -> dict[str, Any]:
        """Capture a screenshot of the Unity Editor."""
        return editor_screenshot(
            view=view, width=width, height=height,
            refresh=refresh, crop_roi=crop_roi,
            target=target, angle=angle,
            target_mode=target_mode,
            padding_ratio=padding_ratio,
            projection=projection,
        )

    @server.tool(name="editor_force_scene_view_refresh")
    def _editor_force_scene_view_refresh() -> dict[str, Any]:
        """Bridge-side scene-view refresh primitive (issue #242).

        Sets ``forceMatrixRecalculationPerRender`` on every active
        ``SkinnedMeshRenderer`` and drives ``QueuePlayerLoopUpdate`` in
        a single bridge round-trip. Returns the integer count of
        renderers touched.
        """
        return editor_force_scene_view_refresh()

    @server.tool()
    def editor_select(
        hierarchy_path: str,
        prefab_asset_path: str = "",
    ) -> dict[str, Any]:
        """Select a GameObject in the Unity Hierarchy.

        Args:
            hierarchy_path: Hierarchy path of the GameObject (e.g. /Canvas/Panel/Button).
            prefab_asset_path: Asset path of a Prefab to open in Prefab Stage before selecting.
        """
        kwargs: dict[str, Any] = {"hierarchy_path": hierarchy_path}
        if prefab_asset_path:
            kwargs["prefab_asset_path"] = prefab_asset_path
        return send_action(action="select_object", **kwargs)

    @server.tool()
    def editor_frame(
        zoom: float = 0.0,
    ) -> dict[str, Any]:
        """Frame the selected object in Scene view.

        Returns bounds info (bounds_center, bounds_extents) and post-frame
        camera state. Use bounds to understand where the object center is
        (e.g., SkinnedMeshRenderer bounds may center at feet).

        Args:
            zoom: Scene view distance factor (SceneView.size). 0 = keep current.
                Larger values zoom OUT, smaller values zoom IN. Typical: 0.1-5.0.
        """
        return send_action(action="frame_selected", zoom=zoom)

    @server.tool()
    def editor_get_camera() -> dict[str, Any]:
        """Get current Scene view camera state.

        Returns position, rotation (quaternion + euler), pivot, size, and
        orthographic mode. Euler uses yaw=0 as front (+Z direction).
        """
        return send_action(action="get_camera")

    @server.tool()
    def editor_set_camera(
        pivot: str = "",
        yaw: float = float("nan"),
        pitch: float = float("nan"),
        size: float = -1.0,
        orthographic: int = -1,
        position: str = "",
        look_at: str = "",
        reset_to_defaults: bool = False,
    ) -> dict[str, Any]:
        """Set Scene view camera.

        Three modes (mutually exclusive):

        * Pivot orbit — ``pivot`` + ``yaw`` / ``pitch`` / ``size``.
        * Position — ``position`` + (``look_at`` or ``yaw`` / ``pitch``).
        * Reset — ``reset_to_defaults=True`` returns the SceneView to its
          documented default pivot, rotation, size, and orthographic flag.

        Cannot mix ``position`` and ``pivot``. ``look_at`` requires
        ``position``. Euler convention: ``yaw=0`` faces +Z.

        Returns previous and current camera state.

        Args:
            pivot: JSON '{"x":0,"y":0,"z":0}' — orbit center.
            yaw: Horizontal rotation in degrees.
            pitch: Vertical rotation in degrees.
            size: SceneView.size, the Scene-view half-width (>=0 to set, -1 = keep).
            orthographic: -1=keep, 0=perspective, 1=orthographic.
            position: JSON '{"x":0,"y":1,"z":-5}' — camera world position.
            look_at: JSON '{"x":0,"y":1,"z":0}' — look-at target (requires position).
            reset_to_defaults: When ``True``, ignore the other parameters and
                restore the SceneView to its documented defaults.
        """
        kwargs = build_set_camera_kwargs(
            pivot=pivot, yaw=yaw, pitch=pitch, size=size,
            orthographic=orthographic, position=position, look_at=look_at,
            reset_to_defaults=reset_to_defaults,
        )
        return send_action(action="set_camera", **kwargs)

    @server.tool()
    def editor_list_children(
        hierarchy_path: str,
        depth: int = 1,
    ) -> dict[str, Any]:
        """List children of a GameObject in the running scene.

        Args:
            hierarchy_path: Hierarchy path to the parent GameObject.
            depth: Maximum depth to traverse (default: 1).
        """
        return send_action(action="list_children", hierarchy_path=hierarchy_path, depth=depth)

    @server.tool()
    def editor_list_materials(
        hierarchy_path: str,
    ) -> dict[str, Any]:
        """List material slots on renderers under a GameObject at runtime.

        Args:
            hierarchy_path: Hierarchy path to the root GameObject.
        """
        return send_action(action="list_materials", hierarchy_path=hierarchy_path)

    @server.tool()
    def editor_list_roots() -> dict[str, Any]:
        """List root GameObjects in the current Scene or Prefab Stage."""
        return send_action(action="list_roots")

    @server.tool()
    def editor_get_material_property(
        hierarchy_path: str,
        material_index: int,
        property_name: str = "",
    ) -> dict[str, Any]:
        """Read shader property values from a material at runtime.

        Args:
            hierarchy_path: Hierarchy path to the GameObject with a Renderer.
            material_index: Material slot index (0-based).
            property_name: Shader property to read (empty = list all properties).
        """
        return send_action(
            action="get_material_property",
            hierarchy_path=hierarchy_path, material_index=material_index,
            property_name=property_name,
        )



    @server.tool(name="editor_console")
    def _editor_console(
        max_entries: int = CONSOLE_MAX_ENTRIES_DEFAULT,
        log_type_filter: str = "all",
        since_seconds: float = 60.0,
        classification_filter: str = "all",
        order: str = "newest_first",
        cursor: str = "",
        phase_filter: str = "all",
        since_sequence: int | None = None,
        since_request_id: str = "",
    ) -> dict[str, Any]:
        """Capture Unity Console log entries as structured data."""
        return editor_console(
            max_entries=max_entries,
            log_type_filter=log_type_filter,
            since_seconds=since_seconds,
            classification_filter=classification_filter,
            order=order,
            cursor=cursor,
            phase_filter=phase_filter,
            since_sequence=since_sequence,
            since_request_id=since_request_id,
        )

    @server.tool(name="editor_refresh")
    def _editor_refresh() -> dict[str, Any]:
        """Refresh the asset database and report any triggered compile.

        Issue #70: the refresh is compile-aware. It returns refresh-OK
        when no compile is triggered, compile-success when a triggered
        compile passes, or compile-failure with the real compiler
        diagnostics when it fails. The transport poll budget covers a
        compile plus the domain reload, matching the recompile-and-wait
        budget.
        """
        return editor_refresh()

    @server.tool(name="editor_recompile")
    def _editor_recompile(
        timeout_sec: float = RECOMPILE_AND_WAIT_DEFAULT_TIMEOUT_SEC,
    ) -> dict[str, Any]:
        """Recompile scripts and synchronously wait for completion (issue #54).

        It returns only after the Editor reports compilation finished and
        the post-reload signal has fired since the request was issued.

        Args:
            timeout_sec: Maximum wait, in seconds, before the bridge
                gives up and returns the recompile-timeout envelope.
        """
        return editor_recompile(timeout_sec=timeout_sec)

    @server.tool()
    def editor_run_tests(
        timeout_sec: int = 300,
    ) -> dict[str, Any]:
        """Run Unity integration tests via Editor Bridge.

        Args:
            timeout_sec: Maximum wait time in seconds (default: 300).
        """
        return send_action(action="run_integration_tests", timeout_sec=timeout_sec)
