"""Behavioral tests for local Unity acceptance source identity (issue #186)."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from prefab_sentinel.bridge_deploy import build_bridge_manifest
from prefab_sentinel.contracts import ToolResponse
from prefab_sentinel.unity_acceptance.source_identity import collect_source_identity


def _git(repo: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def _write_versions(repo: Path, *, codex_version: str = "0.0.2") -> None:
    (repo / "pyproject.toml").write_text(
        "[project]\nname = \"fixture\"\nversion = \"0.0.2\"\n",
        encoding="utf-8",
    )
    for relative_path, version in (
        (".claude-plugin/plugin.json", "0.0.2"),
        (".codex-plugin/plugin.json", codex_version),
    ):
        path = repo / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"version": version}), encoding="utf-8")
    bridge_root = repo / "tools/unity"
    bridge_root.mkdir(parents=True)
    (bridge_root / "PrefabSentinel.UnityEditorControlBridge.cs").write_text(
        'public const string BridgeVersion = "0.0.2";\n',
        encoding="utf-8",
    )
    (repo / "uv.lock").write_text("version = 1\n", encoding="utf-8")


def _git_repo_with_bridge_files(
    tmp_path: Path,
    *,
    bridge_files: tuple[str, ...] = ("A.cs", "PrefabSentinel.Editor.asmdef"),
    codex_version: str = "0.0.2",
) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir(parents=True)
    _git(repo, "init")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test User")

    _write_versions(repo, codex_version=codex_version)
    (repo / "prefab_sentinel").mkdir()
    (repo / "prefab_sentinel/__init__.py").write_text("", encoding="utf-8")
    bridge_root = repo / "tools/unity"
    for name in bridge_files:
        contents = (
            'public const string BridgeVersion = "0.0.2";\n'
            if name == "A.cs"
            else f"{name}\n"
        )
        (bridge_root / name).write_text(contents, encoding="utf-8")

    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "fixture")
    return repo


def _git_head(repo: Path) -> str:
    return _git(repo, "rev-parse", "--verify", "HEAD")


def test_clean_managed_surface_has_stable_manifest_hash(tmp_path: Path) -> None:
    repo = _git_repo_with_bridge_files(tmp_path)

    identity = collect_source_identity(repo)

    assert not isinstance(identity, ToolResponse)
    assert identity.head == _git_head(repo)
    assert identity.managed_dirty_paths == ()
    assert identity.package_versions == {
        "python": "0.0.2",
        "claude_plugin": "0.0.2",
        "codex_plugin": "0.0.2",
        "bridge": "0.0.2",
    }
    assert [item.path for item in identity.bridge_files] == [
        "tools/unity/A.cs",
        "tools/unity/PrefabSentinel.Editor.asmdef",
        "tools/unity/PrefabSentinel.UnityEditorControlBridge.cs",
    ]
    assert len(identity.bridge_manifest_sha256) == 64


def test_source_identity_reuses_the_safe_deploy_manifest_digest(
    tmp_path: Path,
) -> None:
    """A second digest algorithm would reject the exact bundle that #193 deploys."""
    repo = _git_repo_with_bridge_files(tmp_path)

    identity = collect_source_identity(repo)
    canonical = build_bridge_manifest(repo / "tools/unity")

    assert not isinstance(identity, ToolResponse)
    assert not isinstance(canonical, ToolResponse)
    assert identity.bridge_manifest_sha256 == canonical.sha256


def test_dirty_bridge_source_fails_closed(tmp_path: Path) -> None:
    repo = _git_repo_with_bridge_files(tmp_path)
    (repo / "tools/unity/A.cs").write_text("changed", encoding="utf-8")

    response = collect_source_identity(repo)

    assert isinstance(response, ToolResponse)
    assert (response.success, response.code) == (
        False,
        "ACCEPTANCE_SOURCE_DIRTY",
    )


def test_version_drift_is_a_configuration_error(tmp_path: Path) -> None:
    repo = _git_repo_with_bridge_files(tmp_path, codex_version="0.0.1")

    response = collect_source_identity(repo)

    assert isinstance(response, ToolResponse)
    assert (response.success, response.code) == (
        False,
        "ACCEPTANCE_CONFIG_ERROR",
    )



def test_canonical_bridge_version_is_not_masked_by_another_source(
    tmp_path: Path,
) -> None:
    repo = _git_repo_with_bridge_files(tmp_path)
    canonical_bridge = repo / "tools/unity/PrefabSentinel.UnityEditorControlBridge.cs"
    canonical_bridge.write_text(
        'public const string BridgeVersion = "0.0.1";\n',
        encoding="utf-8",
    )
    _git(repo, "add", "tools/unity/PrefabSentinel.UnityEditorControlBridge.cs")
    _git(repo, "commit", "-m", "mismatched canonical bridge version")

    response = collect_source_identity(repo)

    assert isinstance(response, ToolResponse)
    assert (response.success, response.code) == (
        False,
        "ACCEPTANCE_CONFIG_ERROR",
    )



def test_bridge_manifest_rejects_symlinked_sources(tmp_path: Path) -> None:
    repo = _git_repo_with_bridge_files(tmp_path)
    outside_source = tmp_path / "outside.cs"
    outside_source.write_text("outside source\n", encoding="utf-8")
    linked_source = repo / "tools/unity/Linked.cs"
    linked_source.symlink_to(outside_source)
    _git(repo, "add", "tools/unity/Linked.cs")
    _git(repo, "commit", "-m", "symlinked bridge source")

    response = collect_source_identity(repo)

    assert isinstance(response, ToolResponse)
    assert (response.success, response.code, response.message) == (
        False,
        "ACCEPTANCE_CONFIG_ERROR",
        "Bridge source bundle is unavailable.",
    )
    assert str(outside_source) not in str(response.to_dict())



def test_git_launch_failure_returns_sanitized_config_error(
    tmp_path: Path,
    monkeypatch,
) -> None:
    repo = _git_repo_with_bridge_files(tmp_path)

    def raise_git_launch(*_args: object, **_kwargs: object) -> None:
        raise OSError("/private/host-specific/git-launch-failure")

    monkeypatch.setattr(
        "prefab_sentinel.unity_acceptance.source_identity.subprocess.run",
        raise_git_launch,
    )

    response = collect_source_identity(repo)

    assert isinstance(response, ToolResponse)
    assert (response.success, response.code, response.message) == (
        False,
        "ACCEPTANCE_CONFIG_ERROR",
        "Managed source status is unavailable.",
    )
    assert "/private/host-specific" not in response.message


def test_bridge_manifest_hash_uses_sorted_posix_relative_paths(tmp_path: Path) -> None:
    first = _git_repo_with_bridge_files(
        tmp_path / "first",
        bridge_files=("B.cs", "A.cs"),
    )
    second = _git_repo_with_bridge_files(
        tmp_path / "second",
        bridge_files=("A.cs", "B.cs"),
    )

    first_identity = collect_source_identity(first)
    second_identity = collect_source_identity(second)

    assert not isinstance(first_identity, ToolResponse)
    assert not isinstance(second_identity, ToolResponse)
    assert first_identity.bridge_manifest_sha256 == second_identity.bridge_manifest_sha256
