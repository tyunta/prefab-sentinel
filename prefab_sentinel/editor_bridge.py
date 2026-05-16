"""Editor Bridge client for editor-control actions.

Sends action-based requests (capture_screenshot, select_object, frame_selected,
instantiate_to_scene, ping_object, etc.) to a running Unity Editor via the
watch directory protocol.

Requires:
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
    BRIDGE_WATCH_DIR_ENV,
    PROTOCOL_VERSION,
    UNITY_TIMEOUT_SEC_ENV as BRIDGE_TIMEOUT_ENV,
)
from prefab_sentinel.json_io import dump_json, load_json
from prefab_sentinel.wsl_compat import to_wsl_path

# ``PROTOCOL_VERSION`` is imported above and used internally to build
# request/response envelopes (drift-checked against ``bridge_constants`` as
# the single source of truth).
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
        "find_renderers_by_material",
        # Phase 4: Rename + AddComponent + Udon
        "editor_rename",
        "editor_add_component",
        "editor_remove_component",
        "create_udon_program_asset",
        # Phase 5: SetProperty + SaveAsPrefab
        "editor_set_property",
        # Issue #193: ``safe_save_prefab`` is the sole public prefab-save
        # action.  Its handler guarantees that every caller-named protected
        # component type stays attached on the saved asset and reports both
        # the re-attached component types and the orphan parent-prefab
        # modification overrides.
        "safe_save_prefab",
        "editor_set_parent",
        # Phase 6: Batch Operations + Scene
        "editor_create_empty",
        "editor_create_primitive",
        # Issue #195: dedicated uGUI element creation surface
        # (Image / TextMeshProUGUI / Button / Slider / Toggle).
        "editor_create_ui_element",
        "editor_batch_create",
        "editor_batch_set_property",
        "editor_batch_set_material_property",
        "editor_open_scene",
        "editor_save_scene",
        # Phase 7: UX Review improvements
        "editor_batch_add_component",
        "editor_create_scene",
        # Phase 8: Reflection
        "editor_reflect",
        # Phase 9: Editor script exec (#74)
        "run_script",
        # Issue #118: synchronous recompile-and-wait surface that returns
        # only after the Editor has finished compiling, the compiled
        # assembly's mtime has advanced, and the post-reload signal has
        # fired.  ``editor_recompile`` retains its fire-and-return contract.
        "editor_recompile_and_wait",
        # Issue #119: high-level UdonSharp authoring surface — three
        # synchronous handlers (Add / SetField / WireListener) that wrap
        # the AddComponent → RunBehaviourSetup → CopyProxyToUdon chain,
        # the SerializedObject field-write surface, and the published
        # UnityEventTools persistent-listener entry point.  Mirrors the
        # bridge-side SupportedActions set so an out-of-sync action name
        # cannot silently fall through to ``EDITOR_BRIDGE_UNKNOWN_ACTION``.
        "editor_add_udonsharp_component",
        "editor_set_udonsharp_field",
        "editor_wire_persistent_listener",
        # Issue #239: read-only editor-state snapshot consumed by the
        # ``get_project_status`` MCP tool.
        "get_editor_state",
        # Issue #242: bridge-side force-refresh for SkinnedMeshRenderers +
        # editor player-loop tick in one round-trip; consumed by callers
        # running camera-render series outside the screenshot path.
        "force_scene_view_refresh",
        # Issue #240: batch blend-shape write under one Undo group.
        "batch_set_blend_shape",
        # Issue #236: Prefab Stage open and close primitives; live
        # hierarchy-bound write tools resolve against the active stage.
        "open_prefab",
        "close_prefab",
        # Issue #233: asynchronous run-script submit / poll surfaces.
        "run_script_submit",
        "run_script_poll",
        # Issue #243: AnimationClip primitives (inspect / create / apply).
        "inspect_animation_clip",
        "create_animation_clip",
        "apply_animation_clip",
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
    f" Set {BRIDGE_WATCH_DIR_ENV}=<path>."
    " See README 'Unity Bridge セットアップ' section."
)


def check_editor_bridge_env() -> dict[str, Any] | None:
    """Return an error response if editor bridge env is not configured, else None."""
    watch_dir = os.environ.get(BRIDGE_WATCH_DIR_ENV, "")
    if not watch_dir:
        return _error_response(
            code="EDITOR_BRIDGE_WATCH_DIR_MISSING",
            message=f"Editor Bridge not connected: {BRIDGE_WATCH_DIR_ENV} is not set.{_BRIDGE_SETUP_HINT}",
            data={"env_var": BRIDGE_WATCH_DIR_ENV},
        )
    if not Path(to_wsl_path(watch_dir)).is_dir():
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
    request_extras: dict[str, Any] | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    """Send an editor-control action and wait for the response.

    Parameters
    ----------
    action:
        One of SUPPORTED_ACTIONS.
    timeout_sec:
        Override transport-level poll timeout (default: env or 30s).
    request_extras:
        Optional mapping merged into the request JSON after ``kwargs``.
        Used when a request payload field collides with one of this
        function's named parameters (notably ``timeout_sec``, which the
        synchronous recompile-and-wait action requires as a payload field).
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

    watch_dir = Path(to_wsl_path(os.environ[BRIDGE_WATCH_DIR_ENV]))
    if timeout_sec is None:
        timeout_sec = int(os.environ.get(BRIDGE_TIMEOUT_ENV, DEFAULT_TIMEOUT_SEC))
    # Reject non-positive timeouts at the boundary so an operator
    # misconfiguration surfaces as a dedicated envelope rather than an
    # immediate transport timeout (parallels editor_bridge_invoke's check).
    if timeout_sec <= 0:
        return _error_response(
            code="EDITOR_BRIDGE_TIMEOUT_INVALID",
            message=f"{BRIDGE_TIMEOUT_ENV} must be a positive integer.",
            data={"received_timeout": timeout_sec},
        )

    request_id = uuid.uuid4().hex
    request_file = watch_dir / f"{request_id}.request.json"
    response_file = watch_dir / f"{request_id}.response.json"
    tmp_file = Path(str(request_file) + ".tmp")

    request_payload: dict[str, Any] = {
        "protocol_version": PROTOCOL_VERSION,
        "action": action,
        **kwargs,
    }
    if request_extras:
        request_payload.update(request_extras)

    # Atomic write: .tmp → rename to avoid partial reads by the watcher.
    try:
        watch_dir.mkdir(parents=True, exist_ok=True)
        tmp_file.write_text(
            dump_json(request_payload, indent=None),
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
                payload = load_json(raw)
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

            # ``bridge_mode`` is always "editor" since the batchmode dispatch
            # path was removed in issue #270; the field is retained as a stable
            # transport tag for response-shape callers and pinned by tests.
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

    Checks the watch directory env var and its on-disk existence only.
    Does not attempt an actual bridge request (no I/O cost).
    """
    watch_dir = os.environ.get(BRIDGE_WATCH_DIR_ENV, "")
    connected = bool(watch_dir) and Path(to_wsl_path(watch_dir)).is_dir()
    return {
        "connected": connected,
        "watch_dir": watch_dir or None,
    }


def get_last_bridge_version() -> str | None:
    """Return the bridge_version from the last successful response, or None."""
    return _last_bridge_version
