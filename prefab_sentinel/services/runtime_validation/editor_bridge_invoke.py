"""Editor-bridge file-watcher invocation path for runtime validation.

The resident Editor Bridge polls a watch directory for
``<id>.request.json`` files and writes back ``<id>.response.json``.
``invoke_via_editor_bridge`` performs that handshake from the Python
side; the watch directory is named by ``UNITYTOOL_BRIDGE_WATCH_DIR``
and an unset value short-circuits with a ``RUN_CONFIG_ERROR`` envelope.
"""

from __future__ import annotations

import logging
import os
import time
import uuid
from collections.abc import Callable
from pathlib import Path

from prefab_sentinel import bridge_response_publication as response_publication
from prefab_sentinel.bridge_constants import (
    BRIDGE_WATCH_DIR_ENV,
    PROTOCOL_VERSION,
    UNITY_LOG_FILE_ENV,
    UNITY_TIMEOUT_SEC_ENV,
)
from prefab_sentinel.bridge_response_io import (
    BridgeResponseReadError,
    read_bridge_response_file,
)
from prefab_sentinel.contracts import ToolResponse, error_response, success_response
from prefab_sentinel.editor_bridge import check_editor_bridge_env
from prefab_sentinel.json_io import dump_json
from prefab_sentinel.services.runtime_validation.config import (
    DEFAULT_EDITOR_POLL_INTERVAL,
    DEFAULT_TIMEOUT_SEC,
    failure_code,
    try_delete,
)
from prefab_sentinel.services.runtime_validation.editor_bridge_transport import (
    transport_failure_data,
    transport_timeout_sec,
)
from prefab_sentinel.services.runtime_validation.protocol import (
    parse_runtime_response,
)
from prefab_sentinel.wsl_compat import to_windows_path, to_wsl_path

_LOGGER = logging.getLogger(__name__)


def invoke_via_editor_bridge(
    *,
    target_root: Path,
    scene_path: str,
    profile: str,
    relative_fn: Callable[[Path], str],
    confirm: bool,
    change_reason: str,
    generated_asset_policy: str,
    allow_dirty_program_assets_before_compile: bool,
    allow_dirty_scenes_before_compile: bool,
) -> ToolResponse:
    action = "validate_runtime"

    def unavailable_watch_directory() -> ToolResponse:
        return error_response(
            "RUN_CONFIG_ERROR",
            f"{BRIDGE_WATCH_DIR_ENV} must name an existing Editor Bridge watch directory.",
            data={
                "action": action,
                "project_root": relative_fn(target_root),
                "read_only": True,
                "executed": False,
            },
        )

    watch_dir_raw = os.environ.get(BRIDGE_WATCH_DIR_ENV, "").strip()
    watch_dir = Path(to_wsl_path(watch_dir_raw)) if watch_dir_raw else None
    if check_editor_bridge_env(watch_dir) is not None:
        return unavailable_watch_directory()
    if watch_dir is None:
        raise AssertionError("validated watch directory identity is required")

    timeout_raw = os.environ.get(
        UNITY_TIMEOUT_SEC_ENV,
        str(DEFAULT_TIMEOUT_SEC),
    ).strip()
    try:
        timeout_sec = int(timeout_raw)
    except ValueError:
        timeout_sec = -1
    if timeout_sec <= 0:
        return error_response(
            "RUN_CONFIG_ERROR",
            f"{UNITY_TIMEOUT_SEC_ENV} must be a positive integer.",
            data={
                "received_timeout": timeout_raw,
                "read_only": True,
                "executed": False,
            },
        )

    request_id = uuid.uuid4().hex
    request_file = watch_dir / f"{request_id}.request.json"
    response_file = watch_dir / f"{request_id}.response.json"
    response_tmp_file, publication_failure_file = (
        response_publication.publication_artifact_paths(watch_dir, request_id)
    )
    tmp_file = Path(str(request_file) + ".tmp")

    payload = {
        "protocol_version": PROTOCOL_VERSION,
        "action": "validate_runtime",
        "project_root": to_windows_path(str(target_root)),
        "scene_path": to_windows_path(scene_path),
        "profile": profile,
        "timeout_sec": timeout_sec,
        "confirm": True,
        "change_reason": change_reason,
        "generated_asset_policy": generated_asset_policy,
        "allow_dirty_program_assets_before_compile": (
            allow_dirty_program_assets_before_compile
        ),
        "allow_dirty_scenes_before_compile": allow_dirty_scenes_before_compile,
    }

    if check_editor_bridge_env(watch_dir) is not None:
        return unavailable_watch_directory()
    try:
        tmp_file.write_text(dump_json(payload, indent=None), encoding="utf-8")
        tmp_file.rename(request_file)
    except OSError:
        _LOGGER.error("Runtime Editor Bridge request write failed")
        try_delete(tmp_file)
        return error_response(
            "RUN_EDITOR_BRIDGE_WRITE",
            "Failed to write editor bridge runtime request file.",
            data=transport_failure_data(action=action, read_only=True),
        )

    deadline = time.monotonic() + transport_timeout_sec(action, profile, timeout_sec)
    while time.monotonic() < deadline:
        try:
            response_ready = response_file.exists()
            publication_failed = publication_failure_file.exists()
        except OSError:
            _LOGGER.error("Runtime Editor Bridge response status probe failed")
            try_delete(request_file)
            try_delete(response_file)
            return error_response(
                "RUN_EDITOR_BRIDGE_RESPONSE",
                "Editor bridge runtime response file status could not be read.",
                data=transport_failure_data(action=action, read_only=False),
            )

        if response_ready:
            try:
                response_payload = read_bridge_response_file(response_file)
            except BridgeResponseReadError:
                _LOGGER.error("Runtime Editor Bridge response read failed")
                return error_response(
                    "RUN_EDITOR_BRIDGE_RESPONSE",
                    "Editor bridge runtime response file could not be read.",
                    data=transport_failure_data(action=action, read_only=False),
                )
            finally:
                try_delete(request_file)
                try_delete(response_file)

            log_path_raw = os.environ.get(UNITY_LOG_FILE_ENV, "").strip()
            log_path = (
                Path(log_path_raw)
                if log_path_raw
                else target_root / "Logs" / "Editor.log"
            )
            return parse_runtime_response(
                response_payload,
                action=action,
                project_root=target_root,
                scene_path=scene_path,
                profile=profile,
                log_path=log_path,
                relative_fn=relative_fn,
            )

        if publication_failed:
            return response_publication.runtime_failure_response(
                action,
                (response_file, response_tmp_file, publication_failure_file),
                try_delete,
            )

        time.sleep(DEFAULT_EDITOR_POLL_INTERVAL)

    try_delete(request_file)
    return error_response(
        failure_code(action),
        "Editor bridge runtime response timed out.",
        data=transport_failure_data(action=action, read_only=False),
    )


def with_clientsim_side_effect_diagnostics(response: ToolResponse) -> ToolResponse:
    from prefab_sentinel.contracts import Diagnostic, Severity, max_severity
    from prefab_sentinel.services.runtime_validation.classification import (
        clientsim_side_effect_codes,
    )

    if response.data.get("executed") is False:
        return response
    codes = clientsim_side_effect_codes(response.data.get("side_effect_report"))
    if not codes:
        return response

    messages = {
        "CLIENTSIM_SIDE_EFFECT_DIFF_UNAVAILABLE": "ClientSim side-effect diff could not be fully collected.",
        "CLIENTSIM_SIDE_EFFECT_DIFF_DETECTED": (
            "ClientSim cleanup left post-exit scene, hierarchy, component, dirty, "
            "or asset-candidate differences."
        ),
    }
    diagnostics = [
        *response.diagnostics,
        *[
            Diagnostic(
                path=str(response.data.get("scene_path", "")),
                location="",
                detail=code,
                evidence=messages[code],
                severity=Severity.WARNING.value,
            )
            for code in codes
        ],
    ]
    return ToolResponse(
        success=response.success,
        severity=max_severity([response.severity, Severity.WARNING]),
        code=response.code,
        message=response.message,
        data=response.data,
        diagnostics=diagnostics,
    )


def collect_editor_console_via_bridge(
    *,
    since_timestamp: str | None = None,
    max_lines: int = 4000,
) -> ToolResponse:
    from prefab_sentinel.editor_bridge import send_action

    max_entries = min(max(max_lines, 1), 1000)
    response = send_action(
        action="capture_console_logs",
        max_entries=max_entries,
        since_seconds=0.0,
        order="oldest_first",
    )
    data = response.get("data")
    entries = data.get("entries") if isinstance(data, dict) else None
    if (
        response.get("success") is not True
        or not isinstance(data, dict)
        or not isinstance(entries, list)
        or any(
            not isinstance(entry, dict)
            or not isinstance(entry.get("message"), str)
            or not isinstance(entry.get("log_type"), str)
            for entry in entries
        )
    ):
        return error_response(
            "RUN_EDITOR_CONSOLE_ERROR",
            "Editor console capture failed.",
            data={
                "since_timestamp": since_timestamp,
                "console_authority": "editor_bridge",
                "evidence_available": False,
                "read_only": True,
                "executed": isinstance(data, dict) and data.get("executed") is True,
            },
        )

    log_lines = [
        f"[{entry['log_type']}] {entry['message']}" if entry["log_type"] else entry["message"]
        for entry in entries
    ]
    return success_response(
        "RUN_EDITOR_CONSOLE_COLLECTED",
        "Editor Bridge console entries collected.",
        data={
            "line_count": len(log_lines),
            "log_lines": log_lines,
            "console_authority": "editor_bridge",
            "evidence_available": True,
            "since_timestamp": since_timestamp,
            "read_only": True,
            "executed": True,
        },
    )
