"""Deterministic checkout and Unity Bridge source identity."""

from __future__ import annotations

import json
import re
import subprocess
import tomllib
from pathlib import Path

from prefab_sentinel.bridge_deploy import build_bridge_manifest
from prefab_sentinel.contracts import Severity, ToolResponse
from prefab_sentinel.unity_acceptance.model import (
    AcceptanceSourceIdentity,
    BridgeFileIdentity,
)

MANAGED_SOURCE_PATHS = (
    "prefab_sentinel",
    "tools/unity",
    "pyproject.toml",
    ".claude-plugin/plugin.json",
    ".codex-plugin/plugin.json",
    "uv.lock",
)

_BRIDGE_VERSION_PATTERN = re.compile(r'BridgeVersion\s*=\s*"([^"]+)"')


def collect_source_identity(repo_root: Path) -> AcceptanceSourceIdentity | ToolResponse:
    """Return a clean checkout's package and Bridge bundle identity."""
    dirty_paths = _managed_dirty_paths(repo_root)
    if isinstance(dirty_paths, ToolResponse):
        return dirty_paths
    if dirty_paths:
        return ToolResponse(
            success=False,
            severity=Severity.ERROR,
            code="ACCEPTANCE_SOURCE_DIRTY",
            message="Managed source files differ from HEAD.",
            data={"paths": list(dirty_paths)},
        )

    versions = _collect_versions(repo_root)
    if isinstance(versions, ToolResponse):
        return versions

    head = _git_output(repo_root, "rev-parse", "--verify", "HEAD")
    if head is None:
        return _config_error("Repository HEAD is unavailable.")

    branch_result = _run_git(repo_root, "symbolic-ref", "--quiet", "--short", "HEAD")
    if branch_result is None or branch_result.returncode not in (0, 1):
        return _config_error("Repository branch identity is unavailable.")
    branch = (
        branch_result.stdout.decode("utf-8", errors="surrogateescape").strip()
        if branch_result.returncode == 0
        else None
    )

    bridge_manifest = build_bridge_manifest(repo_root / "tools/unity")
    if isinstance(bridge_manifest, ToolResponse):
        return _config_error("Bridge source bundle is unavailable.")
    bridge_files = tuple(
        BridgeFileIdentity(
            path=f"tools/unity/{entry.path}",
            size=entry.size,
            sha256=entry.sha256,
        )
        for entry in bridge_manifest.files
    )
    return AcceptanceSourceIdentity(
        head=head,
        branch=branch,
        managed_dirty_paths=(),
        package_versions=versions,
        bridge_files=bridge_files,
        bridge_manifest_sha256=bridge_manifest.sha256,
    )


def _collect_versions(repo_root: Path) -> dict[str, str] | ToolResponse:
    try:
        pyproject = tomllib.loads((repo_root / "pyproject.toml").read_text("utf-8"))
        python_version = pyproject["project"]["version"]
        claude_version = json.loads(
            (repo_root / ".claude-plugin/plugin.json").read_text("utf-8")
        )["version"]
        codex_version = json.loads(
            (repo_root / ".codex-plugin/plugin.json").read_text("utf-8")
        )["version"]
        bridge_source = (
            repo_root / "tools/unity/PrefabSentinel.UnityEditorControlBridge.cs"
        ).read_text("utf-8")
    except (KeyError, OSError, TypeError, ValueError, tomllib.TOMLDecodeError):
        return _config_error("Required package version files are invalid or missing.")

    bridge_match = _BRIDGE_VERSION_PATTERN.search(bridge_source)
    if bridge_match is None:
        return _config_error("Bridge version constant is missing.")

    versions = {
        "python": python_version,
        "claude_plugin": claude_version,
        "codex_plugin": codex_version,
        "bridge": bridge_match.group(1),
    }
    if not all(isinstance(version, str) and version for version in versions.values()):
        return _config_error("Required package versions are invalid.")
    if len(set(versions.values())) != 1:
        return _config_error("Package and Bridge versions are inconsistent.")
    return versions


def _managed_dirty_paths(repo_root: Path) -> tuple[str, ...] | ToolResponse:
    completed = _run_git(
        repo_root,
        "status",
        "--porcelain=v1",
        "--untracked-files=all",
        "-z",
        "--",
        *MANAGED_SOURCE_PATHS,
    )
    if completed is None or completed.returncode != 0:
        return _config_error("Managed source status is unavailable.")

    entries = completed.stdout.decode("utf-8", errors="surrogateescape").split("\0")
    paths: list[str] = []
    index = 0
    while index < len(entries):
        entry = entries[index]
        index += 1
        if not entry:
            continue
        status, path = entry[:2], entry[3:]
        paths.append(path)
        if status[0] in {"R", "C"} or status[1] in {"R", "C"}:
            index += 1
    return tuple(sorted(paths))


def _run_git(
    repo_root: Path,
    *args: str,
) -> subprocess.CompletedProcess[bytes] | None:
    try:
        return subprocess.run(
            ["git", *args],
            cwd=repo_root,
            capture_output=True,
            check=False,
        )
    except OSError:
        return None


def _git_output(repo_root: Path, *args: str) -> str | None:
    completed = _run_git(repo_root, *args)
    if completed is None or completed.returncode != 0:
        return None
    return completed.stdout.decode("utf-8", errors="surrogateescape").strip()


def _config_error(message: str) -> ToolResponse:
    return ToolResponse(
        success=False,
        severity=Severity.ERROR,
        code="ACCEPTANCE_CONFIG_ERROR",
        message=message,
    )
