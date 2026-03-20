"""WSL compatibility helpers for path conversion and command splitting.

On WSL, Unity.exe (Windows) expects Windows paths while Python uses POSIX paths.
All functions are no-ops on non-WSL platforms.
"""

from __future__ import annotations

import functools
import os
import re
import shlex
import subprocess
from pathlib import Path

_WINDOWS_PATH_RE = re.compile(r"^[A-Za-z]:[/\\]")


@functools.lru_cache(maxsize=1)
def is_wsl() -> bool:
    """Return True if running inside WSL. Result is cached for the process lifetime."""
    if os.name != "posix":
        return False
    try:
        version_text = Path("/proc/version").read_text(encoding="utf-8", errors="replace")
        return bool(re.search(r"microsoft|wsl", version_text, re.IGNORECASE))
    except OSError:
        return False


def _wslpath(flag: str, path: str) -> str | None:
    """Call ``wslpath`` with *flag* (``-w`` or ``-u``). Returns ``None`` on failure."""
    try:
        result = subprocess.run(
            ["wslpath", flag, path],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        pass
    return None


def to_windows_path(path: str) -> str:
    """Convert a path to Windows format for Unity.exe arguments.

    Idempotent: Windows paths are returned as-is.
    No-op on non-WSL platforms.
    """
    if not is_wsl() or _WINDOWS_PATH_RE.match(path):
        return path
    converted = _wslpath("-w", path)
    return converted if converted is not None else path


def to_wsl_path(path: str) -> str:
    """Convert a path to WSL/POSIX format for Python file I/O.

    Idempotent: POSIX paths are returned as-is.
    No-op on non-WSL platforms.
    """
    if not is_wsl() or path.startswith("/"):
        return path
    if _WINDOWS_PATH_RE.match(path):
        converted = _wslpath("-u", path)
        if converted is not None:
            return converted
    return path


def needs_windows_paths(command: list[str]) -> bool:
    """Return True if *command* targets a Windows executable on WSL.

    Only ``.exe`` commands on WSL trigger path conversion; non-``.exe`` commands
    (e.g. Python scripts in tests) expect native POSIX paths.
    """
    return is_wsl() and bool(command) and command[0].lower().endswith(".exe")


def split_unity_command(command_raw: str) -> tuple[list[str], str | None]:
    """Parse a Unity command string, handling paths with unquoted spaces on WSL.

    On WSL, if ``shlex.split`` produces tokens where the first is not an existing
    file, progressively joins subsequent tokens to reconstruct a path with spaces
    (e.g. ``/mnt/c/Program Files/Unity/.../Unity.exe``).

    Falls back to the ``shlex.split`` result when progressive join finds nothing
    or when not running on WSL.
    """
    try:
        parts = shlex.split(command_raw)
    except ValueError as exc:
        return [], str(exc)
    command = [part.strip() for part in parts if part.strip()]
    if not command:
        return [], "command is empty after parsing"

    if not is_wsl() or len(command) < 2:
        return command, None

    # Check if the first token already exists as an executable.
    exe_path = to_wsl_path(command[0])
    if Path(exe_path).exists():
        return command, None

    # Progressive join: try combining tokens to recover a space-containing path.
    for i in range(1, len(command)):
        candidate = " ".join(command[: i + 1])
        wsl_candidate = to_wsl_path(candidate)
        if Path(wsl_candidate).exists():
            return [candidate] + command[i + 1 :], None

    # Nothing matched -- return the original parse.
    return command, None
