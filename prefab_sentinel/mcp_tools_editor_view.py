"""MCP tools for read-only editor bridge operations."""

from __future__ import annotations

import logging
from typing import Any

from mcp.server.fastmcp import FastMCP

from prefab_sentinel.editor_bridge import send_action
from prefab_sentinel.editor_bridge_builders import build_set_camera_kwargs
from prefab_sentinel.mcp_helpers import normalize_material_value

__all__ = ["editor_recompile", "register_editor_view_tools"]

logger = logging.getLogger(__name__)


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


def register_editor_view_tools(server: FastMCP) -> None:
    """Register read-only editor bridge tools on *server*."""

    @server.tool()
    def editor_screenshot(
        view: str = "scene",
        width: int = 0,
        height: int = 0,
        refresh: bool = True,
    ) -> dict[str, Any]:
        """Capture a screenshot of the Unity Editor.

        Args:
            view: Which view to capture ("scene" or "game").
            width: Capture width in pixels (0 = current window size).
            height: Capture height in pixels (0 = current window size).
            refresh: Refresh the asset database before capturing (default True).
        """
        if refresh:
            try:
                send_action(action="refresh_asset_database")
            except Exception:
                logger.warning("Pre-screenshot refresh failed", exc_info=True)
        return send_action(action="capture_screenshot", view=view, width=width, height=height)

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

    @server.tool()
    def editor_console(
        max_entries: int = 200,
        log_type_filter: str = "all",
        since_seconds: float = 60.0,
        classification_filter: str = "all",
        order: str = "newest_first",
        cursor: str = "",
    ) -> dict[str, Any]:
        """Capture Unity Console log entries as structured data.

        Issue #113 (breaking change): the default ordering is
        ``newest_first`` and the default time window is 60.0 seconds so
        the typical interactive debugging request returns the most
        recent log entries first within a recent window. Pagination is
        opaque: the bridge response carries a ``next_cursor`` field
        whenever more matching entries remain, and the next call should
        forward that token verbatim through ``cursor`` to continue.

        Args:
            max_entries: Maximum number of log entries to retrieve (default: 200).
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
        """
        return send_action(
            action="capture_console_logs",
            max_entries=max_entries, log_type_filter=log_type_filter,
            since_seconds=since_seconds,
            classification_filter=classification_filter,
            order=order,
            cursor=cursor,
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

    @server.tool()
    def editor_run_tests(
        timeout_sec: int = 300,
    ) -> dict[str, Any]:
        """Run Unity integration tests via Editor Bridge.

        Args:
            timeout_sec: Maximum wait time in seconds (default: 300).
        """
        return send_action(action="run_integration_tests", timeout_sec=timeout_sec)
