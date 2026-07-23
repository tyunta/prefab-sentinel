"""Editor-bridge file-watcher invocation path for runtime validation.

The resident Editor Bridge polls a watch directory for
``<id>.request.json`` files and writes back ``<id>.response.json``.
``invoke_via_editor_bridge`` performs that handshake from the Python
side; the watch directory is named by ``UNITYTOOL_BRIDGE_WATCH_DIR``
and an unset value short-circuits with a ``RUN_CONFIG_ERROR`` envelope.
"""

from __future__ import annotations

import json
import logging
import os
import time
import uuid
from collections.abc import Callable
from pathlib import Path

from prefab_sentinel.bridge_constants import (
    BRIDGE_WATCH_DIR_ENV,
    UNITY_LOG_FILE_ENV,
    UNITY_TIMEOUT_SEC_ENV,
)
from prefab_sentinel.contracts import ToolResponse, error_response
from prefab_sentinel.editor_bridge import check_editor_bridge_env
from prefab_sentinel.json_io import dump_json, load_json
from prefab_sentinel.services.runtime_validation.config import (
    DEFAULT_EDITOR_POLL_INTERVAL,
    DEFAULT_TIMEOUT_SEC,
    RUNTIME_PROTOCOL_VERSION,
    failure_code,
    try_delete,
)
from prefab_sentinel.services.runtime_validation.protocol import (
    parse_runtime_response,
)
from prefab_sentinel.wsl_compat import to_windows_path, to_wsl_path

_LOGGER = logging.getLogger(__name__)


# ClientSim cleanup remains bridge-owned after its operation deadline. The transport
# therefore waits through the 30-second exit bound plus a 5-second file-dispatch margin.
_CLIENTSIM_EXIT_CLEANUP_GRACE_SEC = 30
_EDITOR_DISPATCH_MARGIN_SEC = 5


def _transport_failure_data(*, action: str, read_only: bool) -> dict[str, object]:
    return {
        "action": action,
        "read_only": read_only,
        "executed": False,
    }


def _transport_timeout_sec(action: str, operation_timeout_sec: int) -> int:
    if action != "run_clientsim":
        return operation_timeout_sec
    return (
        operation_timeout_sec
        + _CLIENTSIM_EXIT_CLEANUP_GRACE_SEC
        + _EDITOR_DISPATCH_MARGIN_SEC
    )


def invoke_via_editor_bridge(
    *,
    action: str,
    target_root: Path,
    scene_path: str | None,
    profile: str | None,
    relative_fn: Callable[[Path], str],
    confirm: bool = False,
    change_reason: str | None = None,
    allow_dirty_before: bool = False,
) -> ToolResponse:
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

    if check_editor_bridge_env() is not None:
        return unavailable_watch_directory()

    watch_dir_raw = os.environ[BRIDGE_WATCH_DIR_ENV].strip()
    watch_dir = Path(to_wsl_path(watch_dir_raw))
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
    tmp_file = Path(str(request_file) + ".tmp")

    payload = {
        "protocol_version": RUNTIME_PROTOCOL_VERSION,
        "action": action,
        "project_root": to_windows_path(str(target_root)),
        "scene_path": to_windows_path(scene_path) if scene_path else "",
        "profile": profile or "",
        "timeout_sec": timeout_sec,
        "confirm": confirm,
        "change_reason": change_reason or "",
        "allow_dirty_before": allow_dirty_before,
    }

    if check_editor_bridge_env() is not None:
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
            data=_transport_failure_data(action=action, read_only=True),
        )

    deadline = time.monotonic() + _transport_timeout_sec(action, timeout_sec)
    while time.monotonic() < deadline:
        try:
            response_ready = response_file.exists()
        except OSError:
            _LOGGER.error("Runtime Editor Bridge response status probe failed")
            try_delete(request_file)
            try_delete(response_file)
            return error_response(
                "RUN_EDITOR_BRIDGE_RESPONSE",
                "Editor bridge runtime response file status could not be read.",
                data=_transport_failure_data(action=action, read_only=False),
            )

        if response_ready:
            try:
                raw = response_file.read_text(encoding="utf-8")
                response_payload = load_json(raw)
            except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                _LOGGER.error("Runtime Editor Bridge response read failed")
                return error_response(
                    "RUN_EDITOR_BRIDGE_RESPONSE",
                    "Editor bridge runtime response file could not be read.",
                    data=_transport_failure_data(action=action, read_only=False),
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

        time.sleep(DEFAULT_EDITOR_POLL_INTERVAL)

    try_delete(request_file)
    return error_response(
        failure_code(action),
        "Editor bridge runtime response timed out.",
        data=_transport_failure_data(action=action, read_only=False),
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
    from prefab_sentinel.contracts import success_response
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
            "since_timestamp": since_timestamp,
            "read_only": True,
            "executed": True,
        },
    )
