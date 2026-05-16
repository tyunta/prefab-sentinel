"""Shared helpers for the Editor-Bridge runtime invocation path.

This module hosts the project-root resolver, the project-shape skip
envelope helper, the action-to-failure-code mapping, the best-effort
file-delete helper, and the runtime protocol / timeout / poll-interval
constants consumed by ``editor_bridge_invoke``.
"""

from __future__ import annotations

import contextlib
import os
from pathlib import Path
from typing import Any

from prefab_sentinel.bridge_constants import UNITY_PROJECT_PATH_ENV
from prefab_sentinel.contracts import (
    Severity,
    ToolResponse,
    success_response,
)

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


def failure_code(action: str) -> str:
    """Pick the action-specific failure code (``RUN_COMPILE_FAILED`` vs ``RUN002``)."""
    return "RUN_COMPILE_FAILED" if action == "compile_udonsharp" else "RUN002"


def try_delete(path: Path) -> None:
    """Best-effort ``unlink`` that swallows ``OSError``."""
    with contextlib.suppress(OSError):
        path.unlink(missing_ok=True)
