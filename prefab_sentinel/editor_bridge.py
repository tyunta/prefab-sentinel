"""Editor Bridge client for editor-control actions.

Sends action-based requests (capture_screenshot, select_object, frame_selected,
instantiate_to_scene, ping_object, etc.) to a running Unity Editor via the
watch directory protocol.

Requires:
  UNITYTOOL_BRIDGE_WATCH_DIR=<path>
"""

from __future__ import annotations

import contextlib
import logging
import os
import time
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

from prefab_sentinel import bridge_response_publication as response_publication
from prefab_sentinel.bridge_constants import (
    BRIDGE_WATCH_DIR_ENV,
    PROTOCOL_VERSION,
    UNITY_TIMEOUT_SEC_ENV as BRIDGE_TIMEOUT_ENV,
)

# ``PROTOCOL_VERSION`` is imported above and used internally to build
# request/response envelopes (drift-checked against ``bridge_constants`` as
# the single source of truth).
# Empirical: sufficient for typical Inspector operations in loaded projects
from prefab_sentinel.bridge_response import is_bridge_response_envelope
from prefab_sentinel.bridge_response_io import (
    BridgeResponseReadError,
    read_bridge_response_file,
)
from prefab_sentinel.editor_status_blockers import classify_tool_error_blocker
from prefab_sentinel.json_io import dump_json
from prefab_sentinel.wsl_compat import to_wsl_path

_LOGGER = logging.getLogger(__name__)
DEFAULT_TIMEOUT_SEC = 30
# Cached bridge version from last successful response
_last_bridge_version: str | None = None
_expected_project_root_provider: Callable[[], str | None] | None = None
_EXPECTED_PROJECT_ROOT_UNSET = object()
_WATCH_DIRECTORY_UNSET = object()
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


def check_editor_bridge_env(
    watch_dir: Path | None | object = _WATCH_DIRECTORY_UNSET,
) -> dict[str, Any] | None:
    """Return an error response unless the captured watch directory is usable."""
    if watch_dir is _WATCH_DIRECTORY_UNSET:
        watch_dir_raw = os.environ.get(BRIDGE_WATCH_DIR_ENV, "")
        watch_path = (
            Path(to_wsl_path(watch_dir_raw))
            if watch_dir_raw
            else None
        )
    elif isinstance(watch_dir, Path):
        watch_path = watch_dir
    else:
        watch_path = None

    if watch_path is None:
        return _error_response(
            code="EDITOR_BRIDGE_WATCH_DIR_MISSING",
            message=(
                f"Editor Bridge not connected: {BRIDGE_WATCH_DIR_ENV} is not set."
                f"{_BRIDGE_SETUP_HINT}"
            ),
            data={"env_var": BRIDGE_WATCH_DIR_ENV},
        )

    try:
        watch_dir_exists = watch_path.is_dir()
    except OSError:
        _LOGGER.exception(
            "Editor Bridge watch directory status probe failed for %s",
            watch_path,
        )
        return _error_response(
            code="EDITOR_BRIDGE_WATCH_DIR_NOT_FOUND",
            message=(
                "Editor Bridge watch directory status is unavailable."
                f"{_BRIDGE_SETUP_HINT}"
            ),
            data={"env_var": BRIDGE_WATCH_DIR_ENV},
        )
    if not watch_dir_exists:
        return _error_response(
            code="EDITOR_BRIDGE_WATCH_DIR_NOT_FOUND",
            message=(
                "Editor Bridge watch directory does not exist."
                f"{_BRIDGE_SETUP_HINT}"
            ),
            data={"env_var": BRIDGE_WATCH_DIR_ENV},
        )
    return None


_PRIVATE_DEPLOY_ACTIONS = frozenset({"promote_bridge_bundle"})

PRIVATE_ACCEPTANCE_ACTIONS = frozenset(
    {
        "run_integration_tests",
        "acceptance_status",
        "cleanup_integration_tests",
    }
)


def _send_bridge_action(
    *,
    action: str,
    allowed_actions: frozenset[str] | set[str],
    publication_state: dict[str, bool] | None = None,
    timeout_sec: int | None = None,
    request_extras: dict[str, Any] | None = None,
    expected_project_root: str | None | object = _EXPECTED_PROJECT_ROOT_UNSET,
    watch_dir: str | Path | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    watch_dir_raw = (
        os.environ.get(BRIDGE_WATCH_DIR_ENV, "")
        if watch_dir is None
        else str(watch_dir)
    )
    watch_dir = Path(to_wsl_path(watch_dir_raw)) if watch_dir_raw else None
    env_err = check_editor_bridge_env(watch_dir)
    if env_err is not None:
        return env_err
    if watch_dir is None:
        raise AssertionError("validated watch directory identity is required")

    if action not in allowed_actions:
        return _error_response(
            code="EDITOR_BRIDGE_UNKNOWN_ACTION",
            message=(
                f"Unknown action: {action}. "
                f"Supported: {', '.join(sorted(allowed_actions))}"
            ),
        )

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
    response_tmp_file, publication_failure_file = (
        response_publication.publication_artifact_paths(watch_dir, request_id)
    )
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

    env_err = check_editor_bridge_env(watch_dir)
    if env_err is not None:
        return env_err
    try:
        tmp_file.write_text(
            dump_json(request_payload, indent=None),
            encoding="utf-8",
        )
        tmp_file.rename(request_file)
        if publication_state is not None:
            publication_state["request_published"] = True
    except OSError:
        _LOGGER.exception(
            "Editor Bridge request write failed for %s",
            request_file,
        )
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
                publication_failed = publication_failure_file.exists()
            except OSError:
                _LOGGER.exception(
                    "Editor Bridge response status probe failed for %s",
                    response_file,
                )
                _try_delete(response_file)
                return _error_response(
                    code="EDITOR_BRIDGE_RESPONSE_READ",
                    message="Editor bridge response file status could not be read.",
                )
            if response_ready:
                try:
                    payload = read_bridge_response_file(response_file)
                except BridgeResponseReadError:
                    _LOGGER.exception(
                        "Editor Bridge response read failed for %s",
                        response_file,
                    )
                    return _error_response(
                        code="EDITOR_BRIDGE_RESPONSE_READ",
                        message="Editor bridge response file could not be read.",
                    )
                finally:
                    _try_delete(response_file)

                if not is_bridge_response_envelope(payload):
                    return _error_response(
                        code="EDITOR_BRIDGE_RESPONSE_SCHEMA",
                        message="Editor bridge response envelope is invalid.",
                    )
                if (
                    type(payload.get("protocol_version")) is not int
                    or payload["protocol_version"] != PROTOCOL_VERSION
                ):
                    return _error_response(
                        code="EDITOR_BRIDGE_RESPONSE_SCHEMA",
                        message="Editor bridge response protocol version is invalid.",
                    )

                payload.setdefault("bridge_mode", "editor")
                payload.setdefault("action", action)
                payload.setdefault("request_id", request_id)

                verified_request_id = str(payload["request_id"])
                mismatch = _verify_expected_project_root(
                    payload=payload,
                    action=action,
                    request_id=verified_request_id,
                    expected_project_root=resolved_expected_project_root,
                )
                if mismatch is not None:
                    return mismatch

                bridge_version = payload.get("bridge_version")
                if (
                    payload["success"] is True
                    and isinstance(bridge_version, str)
                    and bridge_version
                ):
                    global _last_bridge_version
                    _last_bridge_version = bridge_version

                return _enrich_bridge_error_response(payload)

            if publication_failed:
                return response_publication.editor_failure_response(
                    action,
                    (response_file, response_tmp_file, publication_failure_file),
                    _try_delete,
                )

            time.sleep(DEFAULT_POLL_INTERVAL)

        return _error_response(
            code="EDITOR_BRIDGE_TIMEOUT",
            message="Editor bridge response timed out.",
            data={"action": action, "timeout_sec": timeout_sec},
        )
    finally:
        _try_delete(request_file)


def send_private_acceptance_action(
    *,
    action: str,
    timeout_sec: int | None = None,
    request_extras: dict[str, Any] | None = None,
    expected_project_root: str | None | object = _EXPECTED_PROJECT_ROOT_UNSET,
    watch_dir: str | Path | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    """Send one fixed private acceptance action over the existing file IPC."""
    return _send_bridge_action(
        action=action,
        allowed_actions=PRIVATE_ACCEPTANCE_ACTIONS,
        timeout_sec=timeout_sec,
        request_extras=request_extras,
        expected_project_root=expected_project_root,
        watch_dir=watch_dir,
        **kwargs,
    )


def send_private_deploy_action(
    *,
    action: str,
    deploy_run_id: str,
    deploy_target_path: str,
    deploy_transaction_path: str,
    deploy_manifest_sha256: str,
    deploy_bridge_version: str,
) -> dict[str, Any]:
    """Send the sole private Bridge deployment action over file IPC."""
    publication_state = {"request_published": False}
    if action not in _PRIVATE_DEPLOY_ACTIONS:
        response = _error_response(
            code="EDITOR_BRIDGE_UNKNOWN_ACTION",
            message=(
                f"Unknown private deploy action: {action}. "
                f"Supported: {', '.join(sorted(_PRIVATE_DEPLOY_ACTIONS))}"
            ),
        )
    else:
        response = _send_bridge_action(
            action=action,
            allowed_actions=_PRIVATE_DEPLOY_ACTIONS,
            publication_state=publication_state,
            deploy_run_id=deploy_run_id,
            deploy_target_path=deploy_target_path,
            deploy_transaction_path=deploy_transaction_path,
            deploy_manifest_sha256=deploy_manifest_sha256,
            deploy_bridge_version=deploy_bridge_version,
        )
    return {
        **response,
        "_request_published": publication_state["request_published"],
    }

def send_action(
    *,
    action: str,
    timeout_sec: int | None = None,
    request_extras: dict[str, Any] | None = None,
    expected_project_root: str | None | object = _EXPECTED_PROJECT_ROOT_UNSET,
    **kwargs: Any,
) -> dict[str, Any]:
    """Send a public editor-control action and wait for the response."""
    return _send_bridge_action(
        action=action,
        allowed_actions=SUPPORTED_ACTIONS,
        timeout_sec=timeout_sec,
        request_extras=request_extras,
        expected_project_root=expected_project_root,
        **kwargs,
    )


def bridge_status(
    watch_dir: Path | None | object = _WATCH_DIRECTORY_UNSET,
) -> dict[str, Any]:
    """Return the public Bridge connection/configuration projection."""
    error = check_editor_bridge_env(watch_dir)
    if error is None:
        return {
            "connected": True,
            "connection_state": "connected",
            "code": None,
            "blocker_class": None,
            "suggested_next_action": None,
        }

    code_value = error.get("code")
    code = code_value if isinstance(code_value, str) else "EDITOR_BRIDGE_WATCH_DIR_NOT_FOUND"
    data_value = error.get("data")
    data = data_value if isinstance(data_value, dict) else {}
    return {
        "connected": False,
        "connection_state": (
            "not_configured"
            if code == "EDITOR_BRIDGE_WATCH_DIR_MISSING"
            else "unavailable"
        ),
        "code": code,
        "blocker_class": data.get("blocker_class"),
        "suggested_next_action": data.get("suggested_next_action"),
    }


def get_last_bridge_version() -> str | None:
    """Return the bridge_version from the last successful response, or None."""
    return _last_bridge_version
