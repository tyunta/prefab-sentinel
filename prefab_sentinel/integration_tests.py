"""Library for deploying and running Unity C# integration tests.

The integration test harness is a C# file
(``tools/unity/PrefabSentinel.UnityIntegrationTests.cs``) that exercises
:class:`UnityPatchBridge.ApplyFromPaths` inside a single Unity batchmode
session and reports structured JSON results.

This module provides:
* ``deploy_test_files``  – copy C# sources into a Unity project
* ``run_integration_tests`` – invoke Unity batchmode and collect results
* ``parse_integration_results`` – validate and return parsed JSON
"""

from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
from pathlib import Path
from typing import Any

VALID_SEVERITIES = frozenset({"info", "warning", "error", "critical"})

_CS_FILES = [
    "PrefabSentinel.UnityPatchBridge.cs",
    "PrefabSentinel.UnityIntegrationTests.cs",
]

_DEFAULT_EXECUTE_METHOD = "PrefabSentinel.UnityIntegrationTests.RunAll"
_DEFAULT_TIMEOUT_SEC = 300

# env var names (shared with bridge_smoke / smoke_batch)
UNITY_COMMAND_ENV = "UNITYTOOL_UNITY_COMMAND"


def _project_root() -> Path:
    """Return the prefab-sentinel repo root (parent of this package)."""
    return Path(__file__).resolve().parent.parent


# ------------------------------------------------------------------
# Deploy
# ------------------------------------------------------------------

def deploy_test_files(
    project_path: Path,
    *,
    cs_source_dir: Path | None = None,
) -> Path:
    """Copy C# bridge + test files into the Unity project Editor folder.

    Returns the destination directory.
    """
    if cs_source_dir is None:
        cs_source_dir = _project_root() / "tools" / "unity"

    dest = project_path / "Assets" / "Editor" / "PrefabSentinel"
    dest.mkdir(parents=True, exist_ok=True)

    for name in _CS_FILES:
        src = cs_source_dir / name
        if not src.is_file():
            raise FileNotFoundError(f"C# source not found: {src}")
        shutil.copy2(src, dest / name)

    return dest


# ------------------------------------------------------------------
# Run
# ------------------------------------------------------------------

def build_unity_command(
    unity_command: str,
    project_path: Path,
    output_path: Path,
    log_path: Path,
    *,
    execute_method: str = _DEFAULT_EXECUTE_METHOD,
    timeout_sec: int = _DEFAULT_TIMEOUT_SEC,
) -> list[str]:
    """Build the batchmode command list."""
    base = shlex.split(unity_command)
    return [
        *base,
        "-batchmode",
        "-quit",
        "-projectPath",
        str(project_path),
        "-executeMethod",
        execute_method,
        "-logFile",
        str(log_path),
        "-sentinelTestOutputPath",
        str(output_path),
    ]


def run_integration_tests(
    unity_command: str,
    project_path: Path,
    out_dir: Path,
    *,
    timeout_sec: int = _DEFAULT_TIMEOUT_SEC,
) -> dict[str, Any]:
    """Run the integration test harness and return parsed results.

    Raises ``RuntimeError`` on launch failure or missing results file.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    results_path = out_dir / "results.json"
    log_path = out_dir / "unity_integration.log"

    cmd = build_unity_command(
        unity_command,
        project_path,
        results_path,
        log_path,
        timeout_sec=timeout_sec,
    )

    try:
        proc = subprocess.run(
            cmd,
            timeout=timeout_sec,
            capture_output=True,
        )
    except FileNotFoundError as exc:
        raise RuntimeError(
            f"Unity command not found: {unity_command}"
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(
            f"Unity batchmode timed out after {timeout_sec}s"
        ) from exc

    if not results_path.is_file():
        stderr_tail = (proc.stderr or b"")[-2000:].decode("utf-8", errors="replace")
        raise RuntimeError(
            f"Unity exited with code {proc.returncode} but results file "
            f"was not created at {results_path}.\nstderr tail:\n{stderr_tail}"
        )

    return parse_integration_results(results_path)


# ------------------------------------------------------------------
# Parse
# ------------------------------------------------------------------

_REQUIRED_FIELDS = {"success", "severity", "code", "message", "data"}


def parse_integration_results(results_path: Path) -> dict[str, Any]:
    """Read, validate, and return integration test results."""
    text = results_path.read_text(encoding="utf-8")
    data: dict[str, Any] = json.loads(text)

    missing = _REQUIRED_FIELDS - set(data.keys())
    if missing:
        raise ValueError(f"Results JSON missing required fields: {missing}")

    sev = data.get("severity", "")
    if sev not in VALID_SEVERITIES:
        raise ValueError(f"Invalid severity '{sev}' in results.")

    return data


# ------------------------------------------------------------------
# Log extraction
# ------------------------------------------------------------------

def extract_unity_log_errors(log_path: Path, *, max_lines: int = 200) -> list[str]:
    """Return lines from the Unity log that look like errors."""
    if not log_path.is_file():
        return []
    errors: list[str] = []
    for line in log_path.read_text(encoding="utf-8", errors="replace").splitlines():
        lower = line.lower()
        if "error" in lower or "exception" in lower or "assert" in lower:
            errors.append(line)
            if len(errors) >= max_lines:
                break
    return errors
