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
import logging
import os
import time
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

from prefab_sentinel.bridge_constants import (
    BRIDGE_WATCH_DIR_ENV,
    PROTOCOL_VERSION,
    UNITY_TIMEOUT_SEC_ENV as BRIDGE_TIMEOUT_ENV,
)

# ``PROTOCOL_VERSION`` is imported above and used internally to build
# request/response envelopes (drift-checked against ``bridge_constants`` as
# the single source of truth).
# Empirical: sufficient for typical Inspector operations in loaded projects
from prefab_sentinel.editor_status_blockers import classify_tool_error_blocker
from prefab_sentinel.json_io import dump_json, load_json
from prefab_sentinel.wsl_compat import to_wsl_path

_LOGGER = logging.getLogger(__name__)
DEFAULT_TIMEOUT_SEC = 30
# Cached bridge version from last successful response
_last_bridge_version: str | None = None
_expected_project_root_provider: Callable[[], str | None] | None = None
_EXPECTED_PROJECT_ROOT_UNSET = object()
DEFAULT_POLL_INTERVAL = 1.0

SUPPORTED_ACTIONS = frozenset(
    {
        "capture_screenshot",
        "select_object",
        "frame_selected",
        "instantiate_to_scene",
        "ping_object",
        "capture_console_logs",
        "refresh_asset_database",
        "set_material",
        "delete_object",
        "delete_assets",
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
        "editor_serialized_property_read",
        "editor_serialized_property_list",
        "editor_inspect_serialized_surface",
        "editor_serialized_property_write",
        "create_generated_asset",
        "move_asset",
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
        # Issue #118: synchronous recompile-and-wait action that returns
        # only after the Editor has finished compiling and the post-reload
        # signal has fired.  Driven by the blocking ``editor_recompile``
        # MCP tool.
        "editor_recompile_and_wait",
        # Issue #119: high-level UdonSharp authoring surface — three
        # synchronous handlers (Add / SetField / WireListener) that wrap
        # the AddComponent / RunBehaviourSetup / CopyProxyToUdon chain,
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
        "get_transform",
        "get_bounds",
        "measure_distance",
        # Issue #243: AnimationClip primitives (inspect / create / apply).
        "inspect_animation_clip",
        "create_animation_clip",
        "apply_animation_clip",
    }
)


def _error_response(*, code: str, message: str, data: dict[str, Any] | None = None) -> dict[str, Any]:
    response_data = dict(data) if data is not None else {}
    blocker = classify_tool_error_blocker({"code": code, "message": message, "data": response_data})
    if blocker is not None:
        response_data["blocker_class"] = blocker["blocker_class"]
        response_data["suggested_next_action"] = blocker["suggested_next_action"]
    return {
        "protocol_version": PROTOCOL_VERSION,
        "success": False,
        "severity": "error",
        "code": code,
        "message": message,
        "data": response_data,
        "diagnostics": [],
    }


def _enrich_bridge_error_response(payload: dict[str, Any]) -> dict[str, Any]:
    if payload.get("success") is not False:
        return payload
    data = payload.get("data")
    if not isinstance(data, dict):
        return payload
    editor_state = data.get("editor_state")
    if not isinstance(editor_state, dict):
        return payload
    blocker = classify_tool_error_blocker(payload, editor_state=editor_state)
    if blocker is None:
        return payload
    enriched_data = dict(data)
    enriched_data.setdefault("blocker_class", blocker["blocker_class"])
    enriched_data.setdefault("suggested_next_action", blocker["suggested_next_action"])
    enriched = dict(payload)
    enriched["data"] = enriched_data
    return enriched


def _set_expected_project_root_provider(
    provider: Callable[[], str | None] | None,
) -> None:
    global _expected_project_root_provider
    _expected_project_root_provider = provider


def _expected_project_root(expected_project_root: str | None | object) -> str | None:
    if expected_project_root is None:
        return None
    if expected_project_root is not _EXPECTED_PROJECT_ROOT_UNSET:
        if not isinstance(expected_project_root, str):
            raise TypeError("expected_project_root must be a string, None, or unset")
        return expected_project_root
    if _expected_project_root_provider is None:
        return None
    return _expected_project_root_provider()


def _operator_context(payload: dict[str, Any]) -> dict[str, Any]:
    context = payload.get("operator_context")
    return context if isinstance(context, dict) else {}


def _operator_context_project_root(payload: dict[str, Any]) -> str | None:
    root = _operator_context(payload).get("project_root")
    if not isinstance(root, str):
        return None
    stripped = root.strip()
    return stripped or None


def _normal_project_root_identity(root: str) -> str:
    return str(Path(to_wsl_path(root)).expanduser().resolve())


def _bridge_identity_fields(payload: dict[str, Any]) -> dict[str, Any]:
    context = _operator_context(payload)
    identity: dict[str, Any] = {}
    for key in ("bridge_session_id", "bridge_instance_id", "bridge_version", "plugin_version"):
        if key in context:
            identity[key] = context[key]
        elif key in payload:
            identity[key] = payload[key]
    return identity


def _project_root_mismatch_response(
    *,
    action: str,
    request_id: str,
    expected_project_root: str,
    actual_project_root: str | None,
    payload: dict[str, Any],
) -> dict[str, Any]:
    data = {
        "action": action,
        "request_id": request_id,
        "expected_project_root": expected_project_root,
        **_bridge_identity_fields(payload),
    }
    if actual_project_root is not None:
        data["actual_project_root"] = actual_project_root
        message = (
            f"Editor bridge reached Unity project root {actual_project_root!r}, expected {expected_project_root!r}."
        )
    else:
        message = (
            "Editor bridge response did not include the actual Unity project root "
            f"required to verify expected root {expected_project_root!r}."
        )
    return _error_response(
        code="EDITOR_BRIDGE_PROJECT_ROOT_MISMATCH",
        message=message,
        data=data,
    )


def _verify_expected_project_root(
    *,
    payload: dict[str, Any],
    action: str,
    request_id: str,
    expected_project_root: str | None,
) -> dict[str, Any] | None:
    if expected_project_root is None or payload.get("success") is not True:
        return None

    actual_project_root = _operator_context_project_root(payload)
    if actual_project_root is None:
        return _project_root_mismatch_response(
            action=action,
            request_id=request_id,
            expected_project_root=expected_project_root,
            actual_project_root=None,
            payload=payload,
        )

    if _normal_project_root_identity(actual_project_root) == _normal_project_root_identity(expected_project_root):
        return None

    return _project_root_mismatch_response(
        action=action,
        request_id=request_id,
        expected_project_root=expected_project_root,
        actual_project_root=actual_project_root,
        payload=payload,
    )


def _try_delete(path: Path) -> None:
    with contextlib.suppress(OSError):
        path.unlink(missing_ok=True)


_BRIDGE_SETUP_HINT = f" Set {BRIDGE_WATCH_DIR_ENV}=<path>. See README 'Unity Bridge セットアップ' section."


def check_editor_bridge_env() -> dict[str, Any] | None:
    """Return an error response if editor bridge env is not configured, else None."""
    watch_dir = os.environ.get(BRIDGE_WATCH_DIR_ENV, "")
    if not watch_dir:
        return _error_response(
            code="EDITOR_BRIDGE_WATCH_DIR_MISSING",
            message=f"Editor Bridge not connected: {BRIDGE_WATCH_DIR_ENV} is not set.{_BRIDGE_SETUP_HINT}",
            data={"env_var": BRIDGE_WATCH_DIR_ENV},
        )
    watch_path = Path(to_wsl_path(watch_dir))
    try:
        watch_dir_exists = watch_path.is_dir()
    except OSError:
        _LOGGER.error("Editor Bridge watch directory status probe failed")
        return _error_response(
            code="EDITOR_BRIDGE_WATCH_DIR_NOT_FOUND",
            message=f"Editor Bridge watch directory status is unavailable.{_BRIDGE_SETUP_HINT}",
            data={"env_var": BRIDGE_WATCH_DIR_ENV},
        )
    if not watch_dir_exists:
        return _error_response(
            code="EDITOR_BRIDGE_WATCH_DIR_NOT_FOUND",
            message=f"Editor Bridge watch directory does not exist.{_BRIDGE_SETUP_HINT}",
            data={"env_var": BRIDGE_WATCH_DIR_ENV},
        )
    return None


def send_action(
    *,
    action: str,
    timeout_sec: int | None = None,
    request_extras: dict[str, Any] | None = None,
    expected_project_root: str | None | object = _EXPECTED_PROJECT_ROOT_UNSET,
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
    expected_project_root:
        Resolved Unity project root expected by the active MCP session.
        Successful bridge responses must carry a matching actual root in
        ``operator_context.project_root`` when this value is provided.
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
        timeout_raw = os.environ.get(BRIDGE_TIMEOUT_ENV, str(DEFAULT_TIMEOUT_SEC))
        try:
            timeout_sec = int(timeout_raw)
        except ValueError:
            return _error_response(
                code="EDITOR_BRIDGE_TIMEOUT_INVALID",
                message=f"{BRIDGE_TIMEOUT_ENV} must be a positive integer.",
                data={"received_timeout": timeout_raw},
            )
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

    resolved_expected_project_root = _expected_project_root(expected_project_root)
    request_payload: dict[str, Any] = {
        "protocol_version": PROTOCOL_VERSION,
        "action": action,
        **kwargs,
    }
    if request_extras:
        request_payload.update(request_extras)
    if resolved_expected_project_root is not None:
        request_payload["expected_project_root"] = resolved_expected_project_root

    # Atomic write uses .tmp plus rename to avoid partial reads by the watcher.
    env_err = check_editor_bridge_env()
    if env_err is not None:
        return env_err
    try:
        tmp_file.write_text(
            dump_json(request_payload, indent=None),
            encoding="utf-8",
        )
        tmp_file.rename(request_file)
    except OSError:
        _LOGGER.error("Editor Bridge request write failed")
        _try_delete(tmp_file)
        return _error_response(
            code="EDITOR_BRIDGE_WRITE",
            message="Failed to write editor bridge request file.",
        )

    deadline = time.monotonic() + timeout_sec
    try:
        while time.monotonic() < deadline:
            try:
                response_ready = response_file.exists()
            except OSError:
                _LOGGER.error("Editor Bridge response status probe failed")
                _try_delete(response_file)
                return _error_response(
                    code="EDITOR_BRIDGE_RESPONSE_READ",
                    message="Editor bridge response file status could not be read.",
                )
            if response_ready:
                try:
                    raw = response_file.read_text(encoding="utf-8")
                    payload = load_json(raw)
                except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                    _LOGGER.error("Editor Bridge response read failed")
                    return _error_response(
                        code="EDITOR_BRIDGE_RESPONSE_READ",
                        message="Editor bridge response file could not be read.",
                    )
                finally:
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
                # Issue #94: this is the file-transport request id used by the
                # bridge to tag log entries captured during the request. Expose it
                # so callers can pass it to editor_console(since_request_id=...).
                payload.setdefault("request_id", request_id)

                global _last_bridge_version
                if "bridge_version" in payload:
                    _last_bridge_version = payload["bridge_version"]

                verified_request_id = str(payload["request_id"])
                mismatch = _verify_expected_project_root(
                    payload=payload,
                    action=action,
                    request_id=verified_request_id,
                    expected_project_root=resolved_expected_project_root,
                )
                if mismatch is not None:
                    return mismatch

                return _enrich_bridge_error_response(payload)

            time.sleep(DEFAULT_POLL_INTERVAL)

        return _error_response(
            code="EDITOR_BRIDGE_TIMEOUT",
            message="Editor bridge response timed out.",
            data={"action": action, "timeout_sec": timeout_sec},
        )
    finally:
        _try_delete(request_file)


def bridge_status() -> dict[str, Any]:
    """Return current bridge connection status without making a request.

    Checks the watch directory env var and its on-disk existence only.
    Does not attempt an actual bridge request (no I/O cost).
    """
    watch_dir = os.environ.get(BRIDGE_WATCH_DIR_ENV, "")
    connected = False
    status_error: str | None = None
    if watch_dir:
        try:
            connected = Path(to_wsl_path(watch_dir)).is_dir()
        except OSError as exc:
            status_error = str(exc)
    status: dict[str, Any] = {
        "connected": connected,
        "watch_dir": watch_dir or None,
    }
    if status_error is not None:
        status["watch_dir_status_error"] = status_error
    return status


def get_last_bridge_version() -> str | None:
    """Return the bridge_version from the last successful response, or None."""
    return _last_bridge_version
