"""Editor-bridge file-watcher invocation path for runtime validation.

When ``UNITYTOOL_BRIDGE_MODE=editor`` is in effect, the editor-bridge
process polls a watch directory for ``<id>.request.json`` files and
writes back ``<id>.response.json``.  ``invoke_via_editor_bridge``
performs that handshake from the Python side.
"""

from __future__ import annotations

import json
import os
import time
import uuid
from collections.abc import Callable
from pathlib import Path

from prefab_sentinel.bridge_constants import (
    BRIDGE_MODE_ENV,
    BRIDGE_WATCH_DIR_ENV,
    UNITY_LOG_FILE_ENV,
    UNITY_TIMEOUT_SEC_ENV,
)
from prefab_sentinel.contracts import ToolResponse, error_response
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


def invoke_via_editor_bridge(
    *,
    action: str,
    target_root: Path,
    scene_path: str | None,
    profile: str | None,
    relative_fn: Callable[[Path], str],
) -> ToolResponse:
    """Send a runtime validation request via the editor bridge file watcher."""
    watch_dir_raw = os.environ.get(BRIDGE_WATCH_DIR_ENV, "").strip()
    if not watch_dir_raw:
        return error_response(
            "RUN_CONFIG_ERROR",
            f"{BRIDGE_WATCH_DIR_ENV} is required when {BRIDGE_MODE_ENV}=editor.",
            data={
                "action": action,
                "project_root": relative_fn(target_root),
                "read_only": True,
                "executed": False,
            },
        )

    watch_dir = Path(to_wsl_path(watch_dir_raw))
    timeout_raw = os.environ.get(UNITY_TIMEOUT_SEC_ENV, str(DEFAULT_TIMEOUT_SEC)).strip()
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
    }

    try:
        watch_dir.mkdir(parents=True, exist_ok=True)
        tmp_file.write_text(
            dump_json(payload, indent=None),
            encoding="utf-8",
        )
        tmp_file.rename(request_file)
    except OSError as exc:
        return error_response(
            "RUN_EDITOR_BRIDGE_WRITE",
            "Failed to write editor bridge runtime request file.",
            data={
                "action": action,
                "project_root": relative_fn(target_root),
                "request_file": str(request_file),
                "error": str(exc),
                "read_only": True,
                "executed": False,
            },
        )

    deadline = time.monotonic() + timeout_sec
    while time.monotonic() < deadline:
        if response_file.exists():
            try:
                raw = response_file.read_text(encoding="utf-8")
                response_payload = load_json(raw)
            except (OSError, json.JSONDecodeError) as exc:
                return error_response(
                    "RUN_EDITOR_BRIDGE_RESPONSE",
                    "Editor bridge runtime response file could not be read.",
                    data={
                        "action": action,
                        "project_root": relative_fn(target_root),
                        "response_file": str(response_file),
                        "error": str(exc),
                        "read_only": False,
                        "executed": False,
                    },
                )
            finally:
                try_delete(request_file)
                try_delete(response_file)

            log_path_raw = os.environ.get(UNITY_LOG_FILE_ENV, "").strip()
            log_path = Path(log_path_raw) if log_path_raw else target_root / "Logs" / "Editor.log"
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
        data={
            "action": action,
            "project_root": relative_fn(target_root),
            "scene_path": scene_path,
            "profile": profile,
            "timeout_sec": timeout_sec,
            "request_file": str(request_file),
            "bridge_mode": "editor",
            "read_only": False,
            "executed": False,
        },
    )
