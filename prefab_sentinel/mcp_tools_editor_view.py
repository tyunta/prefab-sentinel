"""MCP tools for read-only editor bridge operations."""

from __future__ import annotations

import logging
from typing import Any

from mcp.server.fastmcp import FastMCP

from prefab_sentinel.bridge_constants import CONSOLE_LOG_BUFFER_MAX_ENTRIES
from prefab_sentinel.editor_bridge import send_action
from prefab_sentinel.editor_bridge_builders import build_set_camera_kwargs
from prefab_sentinel.mcp_helpers import normalize_material_value

__all__ = [
    "editor_console",
    "editor_recompile",
    "editor_recompile_and_wait",
    "editor_screenshot",
    "editor_force_scene_view_refresh",
    "register_editor_view_tools",
    "CONSOLE_MAX_ENTRIES_MIN",
    "CONSOLE_MAX_ENTRIES_MAX",
    "RECOMPILE_AND_WAIT_TIMEOUT_MAX_SEC",
    "SCREENSHOT_CROP_ROI_PRESETS",
    "SCREENSHOT_VIEW_ALLOWLIST",
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

# Issue #259: canonical view-selector allowlist for ``editor_screenshot``.
# The selector is interpolated into the output filename on the bridge
# side, so an unvalidated value would let a caller compose path
# separators or traversal sequences into the screenshots-directory
# write.  Comparison is exact case-sensitive equality against this
# tuple; the bridge-side allowlist is identical so the two layers
# cannot drift.
SCREENSHOT_VIEW_ALLOWLIST: tuple[str, ...] = ("scene", "game")

logger = logging.getLogger(__name__)

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


def editor_recompile(force_reimport: bool = False) -> dict[str, Any]:
    """Trigger C# script recompilation in the running Unity Editor.

    When ``force_reimport`` is ``True``, the bridge synchronously re-imports
    every C# file under ``Assets/Editor/`` with ``ForceUpdate`` before
    scheduling compilation, so externally edited files are picked up
    reliably. Default is the legacy ``Refresh + RequestScriptCompilation``
    path.

    Returns the Editor Bridge response envelope unmodified.
    """
    return send_action(
        action="recompile_scripts",
        force_reimport=force_reimport,
    )


def editor_recompile_and_wait(
    timeout_sec: float = RECOMPILE_AND_WAIT_DEFAULT_TIMEOUT_SEC,
) -> dict[str, Any]:
    """Recompile scripts synchronously and wait for the Editor to finish.

    Returns only when the Editor reports compilation finished, the
    compiled-assembly modification time is later than the request's
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
) -> dict[str, Any]:
    """Capture a screenshot of the Unity Editor (issues #249, #259).

    The view selector is interpolated into the output filename on the
    bridge side, so the wrapper enforces a positive allowlist
    (``SCREENSHOT_VIEW_ALLOWLIST``) before any transport activity —
    including the optional pre-screenshot refresh round-trip — to
    prevent path-separator or traversal injection through that field
    (issue #259).  Rejected selectors return the
    ``SCREENSHOT_VIEW_INVALID`` envelope verbatim.

    When ``crop_roi`` is empty the wrapper invokes the bridge with no
    region field on the call kwargs and surfaces the bridge envelope
    unchanged.  When non-empty, the value must be either one of the four
    allowlisted preset names (``eye_left | eye_right | mouth |
    auto_face``) or a comma-separated quadruple of non-negative integers
    ``"x,y,w,h"``; everything else is rejected at the wrapper with the
    ``CROP_ROI_INVALID`` envelope so the bridge is not contacted for a
    bogus region argument.
    """
    # Issue #259: view-selector allowlist must run BEFORE the refresh
    # round-trip so a rejected request never produces side-effecting
    # bridge calls.
    if view not in SCREENSHOT_VIEW_ALLOWLIST:
        return _screenshot_view_invalid_envelope(view)
    if (
        crop_roi
        and crop_roi not in SCREENSHOT_CROP_ROI_PRESETS
        and not _is_valid_pixel_rect(crop_roi)
    ):
        return _crop_roi_invalid_envelope(crop_roi)
    if refresh:
        try:
            send_action(action="refresh_asset_database")
        except Exception:
            logger.warning("Pre-screenshot refresh failed", exc_info=True)
    kwargs: dict[str, Any] = {
        "action": "capture_screenshot",
        "view": view,
        "width": width,
        "height": height,
    }
    if crop_roi:
        kwargs["crop_roi"] = crop_roi
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
) -> dict[str, Any]:
    """Capture Unity Console log entries as structured data.

    Validates ``max_entries`` against the inclusive
    ``[CONSOLE_MAX_ENTRIES_MIN, CONSOLE_MAX_ENTRIES_MAX]`` range before
    contacting the bridge; out-of-range requests return the
    ``MAX_ENTRIES_OUT_OF_RANGE`` envelope.  In-range requests are
    forwarded to the bridge unchanged.

    Issue #239: ``phase_filter`` selects between ``all`` (default),
    ``edit``, ``play``, and ``build``.  The bridge validates the
    selector and rejects unsupported values with
    ``EDITOR_CTRL_INVALID_PHASE_FILTER``.
    """
    if (
        max_entries < CONSOLE_MAX_ENTRIES_MIN
        or max_entries > CONSOLE_MAX_ENTRIES_MAX
    ):
        return _max_entries_out_of_range_envelope(max_entries)

    return send_action(
        action="capture_console_logs",
        max_entries=max_entries,
        log_type_filter=log_type_filter,
        since_seconds=since_seconds,
        classification_filter=classification_filter,
        order=order,
        cursor=cursor,
        phase_filter=phase_filter,
    )


def register_editor_view_tools(server: FastMCP) -> None:
    """Register read-only editor bridge tools on *server*."""

    @server.tool(name="editor_screenshot")
    def _editor_screenshot(
        view: str = "scene",
        width: int = 0,
        height: int = 0,
        refresh: bool = True,
        crop_roi: str = "",
    ) -> dict[str, Any]:
        """Capture a screenshot of the Unity Editor (issue #249).

        Args:
            view: Which view to capture ("scene" or "game").
            width: Capture width in pixels (0 = current window size).
            height: Capture height in pixels (0 = current window size).
            refresh: Refresh the asset database before capturing (default True).
            crop_roi: Optional region selector. Empty (default) captures
                the full frame. Otherwise the value must be either one
                of the four named presets ``eye_left | eye_right |
                mouth | auto_face`` or a comma-separated quadruple of
                non-negative integers ``"x,y,w,h"``. Unrecognised values
                are rejected pre-bridge with the ``CROP_ROI_INVALID``
                envelope.
        """
        return editor_screenshot(
            view=view, width=width, height=height,
            refresh=refresh, crop_roi=crop_roi,
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
        distance: float = -1.0,
        orthographic: int = -1,
        position: str = "",
        look_at: str = "",
        reset_to_defaults: bool = False,
    ) -> dict[str, Any]:
        """Set Scene view camera.

        Three modes (mutually exclusive):

        * Pivot orbit — ``pivot`` + ``yaw`` / ``pitch`` / ``distance``.
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
            distance: SceneView.size (>=0 to set, -1 = keep).
            orthographic: -1=keep, 0=perspective, 1=orthographic.
            position: JSON '{"x":0,"y":1,"z":-5}' — camera world position.
            look_at: JSON '{"x":0,"y":1,"z":0}' — look-at target (requires position).
            reset_to_defaults: When ``True``, ignore the other parameters and
                restore the SceneView to its documented defaults.
        """
        kwargs = build_set_camera_kwargs(
            pivot=pivot, yaw=yaw, pitch=pitch, distance=distance,
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

    @server.tool()
    def editor_set_material_property(
        hierarchy_path: str,
        material_index: int,
        property_name: str,
        value: str | list | int | float,
    ) -> dict[str, Any]:
        """Set a shader property value on a material at runtime.

        Type is determined from shader definition (not from the value format).

        Args:
            hierarchy_path: Hierarchy path to the GameObject with a Renderer.
            material_index: Material slot index (0-based).
            property_name: Shader property name (e.g. "_Color", "_MainTex").
            value: Value as string. Format depends on shader type:
                Float/Range: "0.5"
                Int: "2"
                Color: "[1, 0.8, 0.6, 1]" (RGBA)
                Vector: "[0, 1, 0, 0]" (XYZW)
                Texture: "guid:abc123..." or "path:Assets/Tex/foo.png" or "" (null)
        """
        return send_action(
            action="set_material_property",
            hierarchy_path=hierarchy_path,
            material_index=material_index,
            property_name=property_name,
            property_value=normalize_material_value(value),
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
    ) -> dict[str, Any]:
        """Capture Unity Console log entries as structured data.

        Issue #113 (breaking change): the default ordering is
        ``newest_first`` and the default time window is 60.0 seconds so
        the typical interactive debugging request returns the most
        recent log entries first within a recent window. Pagination is
        opaque: the bridge response carries a ``next_cursor`` field
        whenever more matching entries remain, and the next call should
        forward that token verbatim through ``cursor`` to continue.

        Issue #131: ``max_entries`` must satisfy
        ``CONSOLE_MAX_ENTRIES_MIN <= max_entries <= CONSOLE_MAX_ENTRIES_MAX``.
        Out-of-range requests return the ``MAX_ENTRIES_OUT_OF_RANGE``
        envelope without contacting the bridge.

        Args:
            max_entries: Maximum number of log entries to retrieve
                (default: 200; inclusive upper bound is the buffered
                ring-buffer capacity, lower bound is 1).
            log_type_filter: Filter by log type: "all", "error", "warning", "exception".
            since_seconds: Only entries from the last N seconds (0 = no time filter).
                Default is 60.0 — recent-window capture for typical
                interactive debugging.
            classification_filter: Filter by non-fatal classification:
                ``"all"`` (default), ``"non_fatal"`` (only entries matching the
                bridge-side non-fatal pattern table), or ``"fatal"`` (only
                entries that do not match it).
            order: Ordering keyword. Accepted set: ``"newest_first"`` (default)
                or ``"oldest_first"``. Forwarded verbatim; the bridge
                rejects any other value.
            cursor: Opaque continuation token from a previous call's
                ``next_cursor`` response field. Empty (default) starts a
                fresh page from the most recent (or oldest, depending on
                ordering) matching entry.
            phase_filter: Editor phase filter (issue #239). One of
                ``"all"`` (default), ``"edit"``, ``"play"``, or
                ``"build"``. Bridge rejects any other value with
                ``EDITOR_CTRL_INVALID_PHASE_FILTER``.
        """
        return editor_console(
            max_entries=max_entries,
            log_type_filter=log_type_filter,
            since_seconds=since_seconds,
            classification_filter=classification_filter,
            order=order,
            cursor=cursor,
            phase_filter=phase_filter,
        )

    @server.tool()
    def editor_refresh() -> dict[str, Any]:
        """Trigger AssetDatabase.Refresh() in the running Unity Editor."""
        return send_action(action="refresh_asset_database")

    @server.tool(name="editor_recompile")
    def _editor_recompile(force_reimport: bool = False) -> dict[str, Any]:
        """Trigger C# script recompilation in the running Unity Editor.

        Args:
            force_reimport: When ``True``, synchronously re-import every
                ``.cs`` under ``Assets/Editor/`` with ``ForceUpdate`` before
                scheduling compilation. Use when externally edited editor
                scripts are not picked up by the default refresh.
        """
        return editor_recompile(force_reimport=force_reimport)

    @server.tool(name="editor_recompile_and_wait")
    def _editor_recompile_and_wait(
        timeout_sec: float = RECOMPILE_AND_WAIT_DEFAULT_TIMEOUT_SEC,
    ) -> dict[str, Any]:
        """Recompile scripts and synchronously wait for completion.

        Returns only after the Editor reports compilation finished, the
        compiled-assembly file's modification time has advanced past the
        request's call-time observation, and the post-reload signal has
        fired since the request was issued.

        Args:
            timeout_sec: Maximum wait, in seconds, before the bridge
                gives up and returns the recompile-timeout envelope.
        """
        return editor_recompile_and_wait(timeout_sec=timeout_sec)

    @server.tool()
    def editor_run_tests(
        timeout_sec: int = 300,
    ) -> dict[str, Any]:
        """Run Unity integration tests via Editor Bridge.

        Args:
            timeout_sec: Maximum wait time in seconds (default: 300).
        """
        return send_action(action="run_integration_tests", timeout_sec=timeout_sec)
