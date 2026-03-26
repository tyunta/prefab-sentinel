"""Editor Bridge client for editor-control actions.

Sends action-based requests (capture_screenshot, select_object, frame_selected,
instantiate_to_scene, ping_object) to a running Unity Editor via the watch
directory protocol.

Requires:
  UNITYTOOL_BRIDGE_MODE=editor
  UNITYTOOL_BRIDGE_WATCH_DIR=<path>
"""

from __future__ import annotations

import contextlib
import json
import os
import time
import uuid
from pathlib import Path
from typing import Any

from prefab_sentinel.bridge_constants import (
    BRIDGE_MODE_ENV,
    BRIDGE_WATCH_DIR_ENV,
    UNITY_TIMEOUT_SEC_ENV as BRIDGE_TIMEOUT_ENV,
)

PROTOCOL_VERSION = 1
# Empirical: sufficient for typical Inspector operations in loaded projects
DEFAULT_TIMEOUT_SEC = 30
# Cached bridge version from last successful response
_last_bridge_version: str | None = None
DEFAULT_POLL_INTERVAL = 1.0

SUPPORTED_ACTIONS = frozenset(
    {
        "capture_screenshot",
        "select_object",
        "frame_selected",
        "instantiate_to_scene",
        "ping_object",
        "capture_console_logs",
        "recompile_scripts",
        "refresh_asset_database",
        "set_material",
        "delete_object",
        "list_children",
        "list_materials",
        "get_camera",
        "set_camera",
        "list_roots",
        "get_material_property",
        "set_material_property",
        "run_integration_tests",
        "vrcsdk_upload",
        # Phase 2: BlendShape + Menu
        "get_blend_shapes",
        "set_blend_shape",
        "list_menu_items",
        "execute_menu_item",
    }
)


def _error_response(*, code: str, message: str, data: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "protocol_version": PROTOCOL_VERSION,
        "success": False,
        "severity": "error",
        "code": code,
        "message": message,
        "data": data or {},
        "diagnostics": [],
    }


def _try_delete(path: Path) -> None:
    with contextlib.suppress(OSError):
        path.unlink(missing_ok=True)


_BRIDGE_SETUP_HINT = (
    " Set UNITYTOOL_BRIDGE_MODE=editor and UNITYTOOL_BRIDGE_WATCH_DIR=<path>."
    " See README 'Unity Bridge セットアップ' section."
)


def check_editor_bridge_env() -> dict[str, Any] | None:
    """Return an error response if editor bridge env is not configured, else None."""
    mode = os.environ.get(BRIDGE_MODE_ENV, "")
    if mode != "editor":
        return _error_response(
            code="EDITOR_BRIDGE_MODE",
            message=f"Editor Bridge not connected: {BRIDGE_MODE_ENV} must be 'editor', got '{mode}'.{_BRIDGE_SETUP_HINT}",
            data={"env_var": BRIDGE_MODE_ENV, "value": mode},
        )
    watch_dir = os.environ.get(BRIDGE_WATCH_DIR_ENV, "")
    if not watch_dir:
        return _error_response(
            code="EDITOR_BRIDGE_WATCH_DIR_MISSING",
            message=f"Editor Bridge not connected: {BRIDGE_WATCH_DIR_ENV} is not set.{_BRIDGE_SETUP_HINT}",
            data={"env_var": BRIDGE_WATCH_DIR_ENV},
        )
    if not Path(watch_dir).is_dir():
        return _error_response(
            code="EDITOR_BRIDGE_WATCH_DIR_NOT_FOUND",
            message=f"Editor Bridge not connected: watch directory does not exist: {watch_dir}.{_BRIDGE_SETUP_HINT}",
            data={"env_var": BRIDGE_WATCH_DIR_ENV, "value": watch_dir},
        )
    return None


def send_action(
    *,
    action: str,
    timeout_sec: int | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    """Send an editor-control action and wait for the response.

    Parameters
    ----------
    action:
        One of SUPPORTED_ACTIONS.
    timeout_sec:
        Override timeout (default: env or 30s).
    **kwargs:
        Additional fields merged into the request JSON.
    """
    env_err = check_editor_bridge_env()
    if env_err is not None:
        return env_err

    if action not in SUPPORTED_ACTIONS:
        return _error_response(
            code="EDITOR_BRIDGE_UNKNOWN_ACTION",
            message=f"Unknown action: {action}. Supported: {', '.join(sorted(SUPPORTED_ACTIONS))}",
        )

    watch_dir = Path(os.environ[BRIDGE_WATCH_DIR_ENV])
    if timeout_sec is None:
        timeout_sec = int(os.environ.get(BRIDGE_TIMEOUT_ENV, DEFAULT_TIMEOUT_SEC))

    request_id = uuid.uuid4().hex
    request_file = watch_dir / f"{request_id}.request.json"
    response_file = watch_dir / f"{request_id}.response.json"
    tmp_file = Path(str(request_file) + ".tmp")

    request_payload = {
        "protocol_version": PROTOCOL_VERSION,
        "action": action,
        **kwargs,
    }

    # Atomic write: .tmp → rename to avoid partial reads by the watcher.
    try:
        watch_dir.mkdir(parents=True, exist_ok=True)
        tmp_file.write_text(
            json.dumps(request_payload, ensure_ascii=False),
            encoding="utf-8",
        )
        tmp_file.rename(request_file)
    except OSError as exc:
        return _error_response(
            code="EDITOR_BRIDGE_WRITE",
            message="Failed to write editor bridge request file.",
            data={"request_file": str(request_file), "error": str(exc)},
        )

    # Poll for response.
    deadline = time.monotonic() + timeout_sec
    while time.monotonic() < deadline:
        if response_file.exists():
            try:
                raw = response_file.read_text(encoding="utf-8")
                payload = json.loads(raw)
            except (OSError, json.JSONDecodeError) as exc:
                return _error_response(
                    code="EDITOR_BRIDGE_RESPONSE_READ",
                    message="Editor bridge response file could not be read.",
                    data={"response_file": str(response_file), "error": str(exc)},
                )
            finally:
                _try_delete(request_file)
                _try_delete(response_file)

            if not isinstance(payload, dict):
                return _error_response(
                    code="EDITOR_BRIDGE_RESPONSE_SCHEMA",
                    message="Editor bridge response root must be an object.",
                )

            payload.setdefault("bridge_mode", "editor")
            payload.setdefault("action", action)

            # Cache bridge version from response
            global _last_bridge_version
            if "bridge_version" in payload:
                _last_bridge_version = payload["bridge_version"]

            return payload

        time.sleep(DEFAULT_POLL_INTERVAL)

    # Timeout — clean up.
    _try_delete(request_file)
    return _error_response(
        code="EDITOR_BRIDGE_TIMEOUT",
        message="Editor bridge response timed out.",
        data={
            "action": action,
            "timeout_sec": timeout_sec,
            "request_file": str(request_file),
        },
    )


def bridge_status() -> dict[str, Any]:
    """Return current bridge connection status without making a request.

    Checks environment variables and watch directory existence only.
    Does not attempt an actual bridge request (no I/O cost).
    """
    mode = os.environ.get(BRIDGE_MODE_ENV, "")
    watch_dir = os.environ.get(BRIDGE_WATCH_DIR_ENV, "")
    connected = mode == "editor" and bool(watch_dir) and Path(watch_dir).is_dir()
    return {
        "connected": connected,
        "mode": mode or None,
        "watch_dir": watch_dir or None,
    }


def get_last_bridge_version() -> str | None:
    """Return the bridge_version from the last successful response, or None."""
    return _last_bridge_version


def build_set_camera_kwargs(
    *,
    pivot: str = "",
    yaw: float = float("nan"),
    pitch: float = float("nan"),
    distance: float = -1.0,
    orthographic: int = -1,
    position: str = "",
    look_at: str = "",
) -> dict[str, Any]:
    """Build send_action kwargs from set_camera parameters.

    Keeps parameter parsing separate from MCP server for testability.
    """
    import json as _json
    import math

    kwargs: dict[str, Any] = {}

    if position:
        p = _json.loads(position)
        kwargs["camera_position"] = [p["x"], p["y"], p["z"]]
    if look_at:
        la = _json.loads(look_at)
        kwargs["camera_look_at"] = [la["x"], la["y"], la["z"]]
    if pivot:
        pv = _json.loads(pivot)
        kwargs["camera_pivot"] = [pv["x"], pv["y"], pv["z"]]
    if not math.isnan(yaw):
        kwargs["yaw"] = yaw
    if not math.isnan(pitch):
        kwargs["pitch"] = pitch
    if distance >= 0:
        kwargs["distance"] = distance
    if orthographic >= 0:
        kwargs["camera_orthographic"] = orthographic

    return kwargs
