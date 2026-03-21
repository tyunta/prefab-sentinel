"""Bridge setup diagnostics.

Checks environment variables and C# file placement required for the Unity bridge
to function. Returns results in the project standard envelope format.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from prefab_sentinel.bridge_smoke import (
    UNITY_COMMAND_ENV,
    UNITY_PROJECT_PATH_ENV,
)
from prefab_sentinel.wsl_compat import is_wsl, to_wsl_path

# Environment variable names not defined in bridge_smoke.
PATCH_BRIDGE_ENV = "UNITYTOOL_PATCH_BRIDGE"
BRIDGE_MODE_ENV = "UNITYTOOL_BRIDGE_MODE"
BRIDGE_WATCH_DIR_ENV = "UNITYTOOL_BRIDGE_WATCH_DIR"

# Expected C# bridge files under Assets/Editor/.
_CS_PATCH_BRIDGE = "PrefabSentinel.UnityPatchBridge.cs"
_CS_RUNTIME_BRIDGE = "PrefabSentinel.UnityRuntimeValidationBridge.cs"
_CS_EDITOR_BRIDGE = "PrefabSentinel.EditorBridge.cs"
_CS_EDITOR_CONTROL_BRIDGE = "PrefabSentinel.UnityEditorControlBridge.cs"

_VALID_BRIDGE_MODES = {"batchmode", "editor"}


def _diag(
    severity: str, code: str, message: str
) -> dict[str, str]:
    return {"severity": severity, "code": code, "message": message}


def check_env_set(var_name: str, code: str) -> dict[str, str]:
    """Check that an environment variable is set and non-empty."""
    value = os.environ.get(var_name, "").strip()
    if value:
        return _diag("info", code, f"{var_name} is set")
    return _diag("error", code, f"{var_name} is not set")


def check_unity_command() -> dict[str, str]:
    """Check UNITYTOOL_UNITY_COMMAND: set and file exists (platform-aware)."""
    code = "BC_ENV_UNITY_COMMAND"
    value = os.environ.get(UNITY_COMMAND_ENV, "").strip()
    if not value:
        return _diag("error", code, f"{UNITY_COMMAND_ENV} is not set")
    # On WSL, convert to posix for existence check.
    check_path = to_wsl_path(value) if is_wsl() else value
    if Path(check_path).exists():
        return _diag("info", code, f"{UNITY_COMMAND_ENV} is set and file exists")
    return _diag(
        "warning",
        code,
        f"{UNITY_COMMAND_ENV} is set but file not found: {value}",
    )


def check_unity_project_path() -> dict[str, str]:
    """Check UNITYTOOL_UNITY_PROJECT_PATH: set and directory exists (WSL-aware)."""
    code = "BC_ENV_UNITY_PROJECT_PATH"
    value = os.environ.get(UNITY_PROJECT_PATH_ENV, "").strip()
    if not value:
        return _diag("error", code, f"{UNITY_PROJECT_PATH_ENV} is not set")
    check_path = to_wsl_path(value) if is_wsl() else value
    if Path(check_path).is_dir():
        return _diag(
            "info", code, f"{UNITY_PROJECT_PATH_ENV} is set and directory exists"
        )
    return _diag(
        "warning",
        code,
        f"{UNITY_PROJECT_PATH_ENV} is set but directory not found: {value}",
    )


def _resolve_project_path() -> Path | None:
    """Resolve the Unity project path from env, returning a local-filesystem Path or None."""
    value = os.environ.get(UNITY_PROJECT_PATH_ENV, "").strip()
    if not value:
        return None
    resolved = to_wsl_path(value) if is_wsl() else value
    p = Path(resolved)
    return p if p.is_dir() else None


def check_editor_dir(project_path: Path | None) -> dict[str, str]:
    """Check that Assets/Editor/ exists under the Unity project."""
    code = "BC_DIR_EDITOR"
    if project_path is None:
        return _diag(
            "warning",
            code,
            "Cannot check Assets/Editor/ — project path unavailable",
        )
    editor_dir = project_path / "Assets" / "Editor"
    if editor_dir.is_dir():
        return _diag("info", code, f"Assets/Editor/ exists in {project_path}")
    return _diag(
        "error",
        code,
        f"Assets/Editor/ not found in {project_path}",
    )


def check_cs_file(
    project_path: Path | None, filename: str, code: str, *, required: bool = True
) -> dict[str, str]:
    """Check that a C# bridge file exists under Assets/Editor/."""
    if project_path is None:
        sev = "warning" if required else "info"
        return _diag(
            sev,
            code,
            f"Cannot check {filename} — project path unavailable",
        )
    cs_path = project_path / "Assets" / "Editor" / filename
    if cs_path.is_file():
        return _diag("info", code, f"{filename} found")
    if required:
        return _diag("error", code, f"{filename} not found in Assets/Editor/")
    return _diag("info", code, f"{filename} not found (optional)")


def check_bridge_mode() -> dict[str, str]:
    """Check UNITYTOOL_BRIDGE_MODE (optional): must be batchmode or editor."""
    code = "BC_ENV_BRIDGE_MODE"
    value = os.environ.get(BRIDGE_MODE_ENV, "").strip()
    if not value:
        return _diag("info", code, f"{BRIDGE_MODE_ENV} not set (defaults to batchmode)")
    if value in _VALID_BRIDGE_MODES:
        return _diag("info", code, f"{BRIDGE_MODE_ENV}={value}")
    return _diag(
        "warning",
        code,
        f"{BRIDGE_MODE_ENV} has unexpected value '{value}' (expected: batchmode, editor)",
    )


def check_watch_dir() -> dict[str, str]:
    """Check UNITYTOOL_BRIDGE_WATCH_DIR (required when mode=editor)."""
    code = "BC_ENV_WATCH_DIR"
    mode = os.environ.get(BRIDGE_MODE_ENV, "").strip()
    value = os.environ.get(BRIDGE_WATCH_DIR_ENV, "").strip()
    if mode != "editor":
        if value:
            return _diag("info", code, f"{BRIDGE_WATCH_DIR_ENV} is set (not required in batchmode)")
        return _diag("info", code, f"{BRIDGE_WATCH_DIR_ENV} not set (not required in batchmode)")
    # editor mode: watch dir is required.
    if not value:
        return _diag(
            "error",
            code,
            f"{BRIDGE_WATCH_DIR_ENV} is required when {BRIDGE_MODE_ENV}=editor",
        )
    check_path = to_wsl_path(value) if is_wsl() else value
    if Path(check_path).is_dir():
        return _diag("info", code, f"{BRIDGE_WATCH_DIR_ENV} is set and directory exists")
    return _diag(
        "warning",
        code,
        f"{BRIDGE_WATCH_DIR_ENV} is set but directory not found: {value}",
    )


def check_platform_wsl() -> dict[str, str]:
    """Detect WSL environment (informational)."""
    code = "BC_PLATFORM_WSL"
    if is_wsl():
        return _diag("info", code, "Running under WSL — path conversion active")
    return _diag("info", code, "Not running under WSL")


def run_all_checks() -> dict[str, Any]:
    """Execute all bridge-check diagnostics and return a standard envelope."""
    project_path = _resolve_project_path()

    diagnostics: list[dict[str, str]] = [
        check_env_set(PATCH_BRIDGE_ENV, "BC_ENV_PATCH_BRIDGE"),
        check_unity_command(),
        check_unity_project_path(),
        check_editor_dir(project_path),
        check_cs_file(project_path, _CS_PATCH_BRIDGE, "BC_CS_PATCH_BRIDGE"),
        check_cs_file(project_path, _CS_RUNTIME_BRIDGE, "BC_CS_RUNTIME_BRIDGE"),
        check_cs_file(
            project_path, _CS_EDITOR_BRIDGE, "BC_CS_EDITOR_BRIDGE", required=False
        ),
        check_cs_file(
            project_path,
            _CS_EDITOR_CONTROL_BRIDGE,
            "BC_CS_EDITOR_CONTROL_BRIDGE",
            required=False,
        ),
        check_bridge_mode(),
        check_watch_dir(),
        check_platform_wsl(),
    ]

    failed = sum(1 for d in diagnostics if d["severity"] in ("error", "critical"))
    passed = sum(1 for d in diagnostics if d["severity"] == "info")
    total = len(diagnostics)

    if failed > 0:
        severity = "error"
        success = False
        message = f"{failed} of {total} checks failed"
    else:
        warned = sum(1 for d in diagnostics if d["severity"] == "warning")
        if warned > 0:
            severity = "warning"
            success = True
            message = f"All checks passed with {warned} warning(s)"
        else:
            severity = "info"
            success = True
            message = f"All {total} checks passed"

    return {
        "success": success,
        "severity": severity,
        "code": "BRIDGE_CHECK",
        "message": message,
        "data": {"passed": passed, "failed": failed, "total": total},
        "diagnostics": diagnostics,
    }


def format_text(envelope: dict[str, Any]) -> str:
    """Format bridge-check results as human-readable text."""
    lines: list[str] = []
    lines.append(f"Bridge Check: {envelope['message']}")
    lines.append("")
    for d in envelope.get("diagnostics", []):
        sev = d["severity"].upper()
        lines.append(f"  [{sev}] {d['code']}: {d['message']}")
    return "\n".join(lines)
