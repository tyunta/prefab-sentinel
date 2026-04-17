"""Runtime configuration assembly and Unity command building.

The functions here read environment variables once per invocation and
return either a fully-populated config dict or a ``RUN_CONFIG_ERROR``
``ToolResponse`` — the caller never re-reads the environment.
"""

from __future__ import annotations

import contextlib
import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

from prefab_sentinel.bridge_constants import (
    UNITY_COMMAND_ENV,
    UNITY_LOG_FILE_ENV,
    UNITY_PROJECT_PATH_ENV,
    UNITY_TIMEOUT_SEC_ENV,
)
from prefab_sentinel.contracts import (
    Severity,
    ToolResponse,
    error_response,
    success_response,
)
from prefab_sentinel.wsl_compat import (
    needs_windows_paths,
    split_unity_command,
    to_windows_path,
    to_wsl_path,
)

UNITY_RUNTIME_EXECUTE_METHOD_ENV = "UNITYTOOL_RUNTIME_EXECUTE_METHOD"
DEFAULT_RUNTIME_EXECUTE_METHOD = "PrefabSentinel.UnityRuntimeValidationBridge.RunFromJson"
DEFAULT_TIMEOUT_SEC = 300
RUNTIME_PROTOCOL_VERSION = 1
DEFAULT_EDITOR_POLL_INTERVAL = 1.0


def default_runtime_root(service_root: Path) -> Path:
    """Return the configured Unity project root, or *service_root* when unset."""
    configured_root = os.environ.get(UNITY_PROJECT_PATH_ENV, "").strip()
    if configured_root:
        return Path(configured_root).expanduser()
    return service_root


def skip_response(*, code: str, message: str, data: dict[str, Any]) -> ToolResponse:
    """Build a uniform ``read_only=True``/``executed=False`` skip envelope."""
    return success_response(
        code,
        message,
        severity=Severity.WARNING,
        data={**data, "read_only": True, "executed": False},
    )


def load_runtime_config(
    *, default_project_root: Path
) -> tuple[dict[str, Any] | None, ToolResponse | None]:
    """Read the Unity batchmode env vars; return ``(config, None)`` or ``(None, error)``.

    A return of ``(None, None)`` means the runtime is not configured at
    all (``UNITY_COMMAND_ENV`` empty); the caller should emit the
    appropriate ``RUN_*_SKIPPED`` response.
    """
    command_raw = os.environ.get(UNITY_COMMAND_ENV, "").strip()
    if not command_raw:
        return None, None

    command, split_error = split_unity_command(command_raw)
    if split_error is not None:
        return None, error_response(
            "RUN_CONFIG_ERROR",
            "Unity runtime command cannot be parsed.",
            data={
                "command_raw": command_raw,
                "error": split_error,
                "read_only": True,
                "executed": False,
            },
        )

    timeout_raw = os.environ.get(UNITY_TIMEOUT_SEC_ENV, str(DEFAULT_TIMEOUT_SEC)).strip()
    try:
        timeout_sec = int(timeout_raw)
    except ValueError:
        timeout_sec = -1
    if timeout_sec <= 0:
        return None, error_response(
            "RUN_CONFIG_ERROR",
            f"{UNITY_TIMEOUT_SEC_ENV} must be a positive integer.",
            data={
                "received_timeout": timeout_raw,
                "read_only": True,
                "executed": False,
            },
        )

    project_path_raw = os.environ.get(UNITY_PROJECT_PATH_ENV, "").strip()
    project_path = (
        Path(to_wsl_path(project_path_raw)) if project_path_raw else default_project_root
    )
    if not project_path.exists():
        return None, error_response(
            "RUN_CONFIG_ERROR",
            "Unity project path does not exist.",
            data={
                "project_path": str(project_path),
                "read_only": True,
                "executed": False,
            },
        )

    execute_method = (
        os.environ.get(UNITY_RUNTIME_EXECUTE_METHOD_ENV, DEFAULT_RUNTIME_EXECUTE_METHOD).strip()
        or DEFAULT_RUNTIME_EXECUTE_METHOD
    )
    log_path_raw = os.environ.get(UNITY_LOG_FILE_ENV, "").strip()
    log_path = Path(log_path_raw) if log_path_raw else project_path / "Logs" / "Editor.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)

    return {
        "command": command,
        "project_path": project_path,
        "execute_method": execute_method,
        "timeout_sec": timeout_sec,
        "log_path": log_path,
    }, None


def build_runtime_command(
    *,
    config: dict[str, Any],
    request_path: Path,
    response_path: Path,
) -> list[str]:
    """Assemble the Unity batchmode command line for the runtime bridge."""
    cmd = config["command"]
    convert: Callable[[str], str] = (
        to_windows_path if needs_windows_paths(cmd) else lambda p: p
    )
    return [
        *cmd,
        "-batchmode",
        "-projectPath",
        convert(str(config["project_path"])),
        "-executeMethod",
        str(config["execute_method"]),
        "-logFile",
        convert(str(config["log_path"])),
        "-sentinelRuntimeRequest",
        convert(str(request_path)),
        "-sentinelRuntimeResponse",
        convert(str(response_path)),
    ]


def failure_code(action: str) -> str:
    """Pick the action-specific failure code (``RUN_COMPILE_FAILED`` vs ``RUN002``)."""
    return "RUN_COMPILE_FAILED" if action == "compile_udonsharp" else "RUN002"


def try_delete(path: Path) -> None:
    """Best-effort ``unlink`` that swallows ``OSError``."""
    with contextlib.suppress(OSError):
        path.unlink(missing_ok=True)
