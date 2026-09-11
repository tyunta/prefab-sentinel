"""Bounded canonical-document contracts for safe Bridge deployment (#193).

These tests pin machine-consumed response names, stable result codes, and the
ownership of operational contracts across the specialist documents.  They do
not assert explanatory prose outside the owning sections.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = pytest.mark.source_text_invariant

_ROOT = Path(__file__).resolve().parent.parent
_API_HEADING = "## Bridge deployment response (`deploy_bridge`)"
_EXECUTION_HEADING = "## Safe Bridge deployment transaction"
_CONFIGURATION_HEADING = "## Bridge deployment ownership and target rules"
_TESTING_HEADING = "## Safe Bridge deployment verification (#193 / #186)"

_STABLE_DEPLOY_CODES = {
    "DEPLOY_BARRIER_UNAVAILABLE",
    "DEPLOY_CROSS_FILESYSTEM",
    "DEPLOY_CLEANUP_FAILED",
    "DEPLOY_FINAL_MANIFEST_MISMATCH",
    "DEPLOY_LOCK_REPLACED",
    "DEPLOY_NO_PROJECT",
    "DEPLOY_OK",
    "DEPLOY_OUTSIDE_PROJECT",
    "DEPLOY_OWNERSHIP_INVALID",
    "DEPLOY_OWNERSHIP_WRITE_FAILED",
    "DEPLOY_PARENT_CONFLICT",
    "DEPLOY_PROMOTION_FAILED",
    "DEPLOY_REFRESH_FAILED",
    "DEPLOY_ROLLED_BACK",
    "DEPLOY_ROLLBACK_FAILED",
    "DEPLOY_SOURCE_NOT_FOUND",
    "DEPLOY_STAGING_FAILED",
    "DEPLOY_STAGING_MISMATCH",
    "DEPLOY_TARGET_UNMANAGED",
}
_CANONICAL_DATA_FIELDS = (
    "source_manifest_sha256",
    "manifest_sha256",
    "bridge_version",
    "source_file_count",
    "managed_entry_count",
    "unmanaged_entry_count",
    "preserved_meta_count",
    "stale_owned_count",
    "staging_prepared",
    "staging_verified",
    "promotion_state",
    "barrier_used",
    "rollback_attempted",
    "rollback_restored",
    "backup_retained",
    "target_complete",
    "ownership_published",
    "transaction_retained",
    "deployed_files",
)


def _read(relative_path: str) -> str:
    return (_ROOT / relative_path).read_text(encoding="utf-8")


def _section(document: str, heading: str) -> str:
    marker = f"\n{heading}\n"
    start = document.index(marker) + 1
    end = document.find("\n## ", start + len(heading))
    return document[start:] if end < 0 else document[start:end]


def test_api_section_catalogues_exact_deploy_codes_and_response_fields() -> None:
    section = _section(_read("docs/api-reference.md"), _API_HEADING)

    documented_codes = {
        line.split("`", maxsplit=2)[1]
        for line in section.splitlines()
        if line.startswith("| `DEPLOY_")
    }

    assert documented_codes == _STABLE_DEPLOY_CODES
    for field in _CANONICAL_DATA_FIELDS:
        assert f"`{field}`" in section
    assert "`recovery_required`" in section
    assert (
        "`AssetDatabase.Refresh(ImportAssetOptions.ForceSynchronousImport)`"
        in section
    )
    assert "added / removed source inventory" in section


def test_execution_section_owns_complete_transaction_lifecycle() -> None:
    section = _section(_read("docs/execution-reference.md"), _EXECUTION_HEADING)

    required_contract = (
        "Library/PrefabSentinel/deploy-transactions",
        "source-manifest-v1.json",
        "staged-target",
        "backup-target",
        "DisallowAutoRefresh",
        "AllowAutoRefresh",
        "AssetDatabase.Refresh(ImportAssetOptions.ForceSynchronousImport)",
        "synchronous added / removed source inventory import",
        "not_attempted",
        "installed_fresh",
        "promoted",
        "rolled_back",
        "rollback_failed",
        "no automatic retry",
    )
    for literal in required_contract:
        assert literal in section


def test_tools_catalog_keeps_one_public_tool_and_routes_to_owners() -> None:
    tools = _read("docs/tools.md")
    rows = [
        line
        for line in tools.splitlines()
        if line.startswith("| `deploy_bridge` |")
    ]

    assert len(rows) == 1
    assert "api-reference.md#bridge-deployment-response-deploy_bridge" in rows[0]
    assert "execution-reference.md#safe-bridge-deployment-transaction" in rows[0]
    assert "| `promote_bridge_bundle` |" not in tools


def test_configuration_section_owns_target_and_ownership_rules() -> None:
    section = _section(_read("CONFIGURATION.md"), _CONFIGURATION_HEADING)

    required_contract = (
        "Assets/Editor/PrefabSentinel",
        "Library/PrefabSentinel/deploy-ownership-v1.json",
        "fresh target",
        "recorded target",
        "legacy default target",
        "custom or broad target",
        "parent glob deletion",
        "promote_bridge_bundle",
        "DEPLOY_BARRIER_UNAVAILABLE",
        "one-time bootstrap",
    )
    for literal in required_contract:
        assert literal in section


def test_testing_section_separates_offline_gate_from_live_acceptance() -> None:
    section = _section(_read("TESTING.md"), _TESTING_HEADING)

    required_contract = (
        "UNITYTOOL_BRIDGE_WATCH_DIR",
        "UNITYTOOL_UNITY_PROJECT_PATH",
        "dotnet restore",
        "--locked-mode",
        "Task 7",
        "#186",
        "manifest_sha256",
        "bridge_version",
        "deploy_bridge は Unity コンパイル成功を証明しない",
        "Issue #213",
        "AssetDatabase.Refresh(ImportAssetOptions.ForceSynchronousImport)",
        "active pre-fix handler",
        "same-layout bootstrap",
    )
    for literal in required_contract:
        assert literal in section


def test_unreleased_changelog_declares_safe_deploy_boundary() -> None:
    section = _section(_read("CHANGELOG.md"), "## [Unreleased]")

    required_contract = (
        "#193",
        "#186",
        "promote_bridge_bundle",
        "DEPLOY_BARRIER_UNAVAILABLE",
        "one-time bootstrap",
        "manifest_sha256",
        "bridge_version",
        "Issue #213",
        "AssetDatabase.Refresh(ImportAssetOptions.ForceSynchronousImport)",
        "same-layout bootstrap",
    )
    for literal in required_contract:
        assert literal in section
