"""Unity Editor bridge configuration helpers.

Carries the ``BridgeState`` dataclass, environment parsing, the
allow-list check, and the suffix-based kind inference.  The subprocess
invocation and request / response shapes live in
``resource_bridge_invoke``.
"""

from __future__ import annotations

import os
import shlex
from dataclasses import dataclass
from pathlib import Path

from prefab_sentinel.patch_plan import PLAN_VERSION

UNITY_BRIDGE_PROTOCOL_VERSION = PLAN_VERSION

UNITY_BRIDGE_SUPPORTED_SUFFIXES = {
    ".prefab",
    ".unity",
    ".asset",
    ".mat",
    ".anim",
    ".controller",
}
UNITY_BRIDGE_ALLOWED_COMMANDS = {
    "python",
    "python3",
    "py",
    "python.exe",
    "py.exe",
    "uv",
    "uvx",
    "uv.exe",
    "uvx.exe",
    "prefab-sentinel-unity-bridge",
    "prefab-sentinel-unity-bridge.exe",
    "prefab-sentinel-unity-serialized-object-bridge",
    "prefab-sentinel-unity-serialized-object-bridge.exe",
}
UNITY_BRIDGE_KIND_BY_SUFFIX = {
    ".prefab": "prefab",
    ".unity": "scene",
    ".asset": "asset",
    ".mat": "material",
    ".anim": "animation",
    ".controller": "controller",
}


@dataclass
class BridgeState:
    """Parsed Unity bridge command state.

    ``command`` is the resolved argv tuple (or ``None`` if unset);
    ``timeout_sec`` is the clamped subprocess timeout; ``error`` carries
    the parse-failure reason when the env variable could not be parsed.
    """

    command: tuple[str, ...] | None
    timeout_sec: float
    error: str | None = None


def load_bridge_command_from_env() -> tuple[tuple[str, ...] | None, str | None]:
    """Parse ``UNITYTOOL_PATCH_BRIDGE`` into an argv tuple.

    Returns ``(command_tuple_or_None, error_string_or_None)``.
    """
    raw = os.getenv("UNITYTOOL_PATCH_BRIDGE", "").strip()
    if not raw:
        return None, None
    try:
        parts = tuple(shlex.split(raw, posix=False))
    except ValueError as exc:
        return None, f"Failed to parse UNITYTOOL_PATCH_BRIDGE: {exc}"
    normalized_parts: list[str] = []
    for part in parts:
        if len(part) >= 2 and part[0] == part[-1] and part[0] in {'"', "'"}:
            normalized_parts.append(part[1:-1])
        else:
            normalized_parts.append(part)
    parts = tuple(normalized_parts)
    if not parts:
        return None, "UNITYTOOL_PATCH_BRIDGE did not produce a command."
    return parts, None


def build_bridge_state(
    bridge_command: tuple[str, ...] | None,
    bridge_timeout_sec: float,
) -> BridgeState:
    """Build the ``BridgeState`` for a service, resolving env fallback."""
    error: str | None = None
    command = bridge_command
    if command is None:
        command, error = load_bridge_command_from_env()
    try:
        timeout = float(bridge_timeout_sec)
    except (TypeError, ValueError):
        timeout = 120.0
    return BridgeState(
        command=command,
        timeout_sec=max(1.0, timeout),
        error=error,
    )


def is_unity_bridge_target(target_path: Path) -> bool:
    return target_path.suffix.lower() in UNITY_BRIDGE_SUPPORTED_SUFFIXES


def is_bridge_command_allowed(command: tuple[str, ...]) -> bool:
    head = Path(command[0]).name.lower()
    return head in UNITY_BRIDGE_ALLOWED_COMMANDS


def infer_bridge_resource_kind(target_path: Path) -> str:
    return UNITY_BRIDGE_KIND_BY_SUFFIX.get(target_path.suffix.lower(), "asset")


def infer_resource_kind(target_path: Path) -> str:
    if target_path.suffix.lower() == ".json":
        return "json"
    return infer_bridge_resource_kind(target_path)


# Re-export the subprocess invoker from the companion module so callers
# can write ``from ... import resource_bridge`` and use
# ``resource_bridge.apply_with_unity_bridge(...)`` against either module.
from prefab_sentinel.services.serialized_object.resource_bridge_invoke import (  # noqa: E402
    apply_with_unity_bridge,
    build_unity_bridge_request,
    parse_bridge_response,
)

__all__ = [
    "UNITY_BRIDGE_PROTOCOL_VERSION",
    "UNITY_BRIDGE_SUPPORTED_SUFFIXES",
    "UNITY_BRIDGE_ALLOWED_COMMANDS",
    "UNITY_BRIDGE_KIND_BY_SUFFIX",
    "BridgeState",
    "load_bridge_command_from_env",
    "build_bridge_state",
    "is_unity_bridge_target",
    "is_bridge_command_allowed",
    "infer_bridge_resource_kind",
    "infer_resource_kind",
    "apply_with_unity_bridge",
    "build_unity_bridge_request",
    "parse_bridge_response",
]
