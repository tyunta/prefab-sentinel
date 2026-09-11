from __future__ import annotations

import json
import re
from pathlib import Path

from prefab_sentinel.unity_acceptance import (
    AcceptanceEvidence,
    AcceptanceEvidenceSection,
    AcceptancePhaseResult,
    AcceptanceResult,
)


def test_failure_result_keeps_failed_phase_and_stable_code() -> None:
    result = AcceptanceResult.failure(
        code="ACCEPTANCE_COMPILE_FAILED",
        message="Unity script compilation failed.",
        failed_phase="compile",
        phases=[
            AcceptancePhaseResult(
                "preflight",
                True,
                "ACCEPTANCE_PREFLIGHT_OK",
                {},
            )
        ],
    )

    assert result.to_dict()["result"] == {
        "success": False,
        "severity": "error",
        "code": "ACCEPTANCE_COMPILE_FAILED",
        "message": "Unity script compilation failed.",
        "failed_phase": "compile",
        "phases": [
            {
                "name": "preflight",
                "success": True,
                "code": "ACCEPTANCE_PREFLIGHT_OK",
                "data": {},
                "diagnostics": [],
            }
        ],
        "diagnostics": [],
    }


def test_result_snapshots_caller_owned_phase_data() -> None:
    caller_nested = {"verdict": "captured"}
    caller_items = [{"count": 1}]
    caller_data = {
        "status": "captured",
        "nested": caller_nested,
        "items": caller_items,
    }
    result = AcceptanceResult.failure(
        code="ACCEPTANCE_COMPILE_FAILED",
        message="Unity script compilation failed.",
        failed_phase="compile",
        phases=[
            AcceptancePhaseResult(
                "preflight",
                True,
                "ACCEPTANCE_PREFLIGHT_OK",
                caller_data,
            )
        ],
    )

    caller_data["status"] = "mutated after result construction"
    caller_nested["verdict"] = "mutated after result construction"
    caller_items[0]["count"] = 9
    caller_items.append({"count": 2})

    expected_data = {
        "status": "captured",
        "nested": {"verdict": "captured"},
        "items": [{"count": 1}],
    }
    assert result.phases[0].data == expected_data
    assert result.to_dict()["result"]["phases"][0]["data"] == expected_data


def test_result_snapshots_caller_owned_phase_diagnostics() -> None:
    caller_messages = ["captured"]
    caller_diagnostic = {
        "code": "COMPILE_WARN",
        "details": {"messages": caller_messages},
    }
    phase = AcceptancePhaseResult(
        "compile",
        False,
        "ACCEPTANCE_COMPILE_FAILED",
        {},
        (caller_diagnostic,),
    )
    result = AcceptanceResult.failure(
        code="ACCEPTANCE_COMPILE_FAILED",
        message="Unity script compilation failed.",
        failed_phase="compile",
        phases=[phase],
    )

    caller_diagnostic["code"] = "MUTATED"
    caller_messages.append("mutated after result construction")

    expected_diagnostic = {
        "code": "COMPILE_WARN",
        "details": {"messages": ["captured"]},
    }
    assert phase.diagnostics == (expected_diagnostic,)
    assert result.to_dict()["result"]["phases"][0]["diagnostics"] == [
        expected_diagnostic
    ]


def test_serialized_phase_payload_does_not_mutate_the_stored_result() -> None:
    phase = AcceptancePhaseResult(
        "preflight",
        True,
        "ACCEPTANCE_PREFLIGHT_OK",
        {"nested": {"verdict": "captured"}, "items": [{"count": 1}]},
        ({"code": "COMPILE_WARN", "details": {"messages": ["captured"]}},),
    )
    result = AcceptanceResult.failure(
        code="ACCEPTANCE_COMPILE_FAILED",
        message="Unity script compilation failed.",
        failed_phase="compile",
        phases=[phase],
    )

    serialized_phase = result.to_dict()["result"]["phases"][0]
    serialized_phase["data"]["nested"]["verdict"] = "mutated output"
    serialized_phase["data"]["items"][0]["count"] = 9
    serialized_phase["data"]["items"].append({"count": 2})
    serialized_phase["diagnostics"][0]["code"] = "MUTATED"
    serialized_phase["diagnostics"][0]["details"]["messages"].append("mutated output")

    expected_phase = {
        "name": "preflight",
        "success": True,
        "code": "ACCEPTANCE_PREFLIGHT_OK",
        "data": {"nested": {"verdict": "captured"}, "items": [{"count": 1}]},
        "diagnostics": [
            {"code": "COMPILE_WARN", "details": {"messages": ["captured"]}}
        ],
    }
    assert phase.data == expected_phase["data"]
    assert phase.to_dict() == expected_phase
    assert result.to_dict()["result"]["phases"][0] == expected_phase


def test_terminal_report_matches_the_documented_top_level_schema() -> None:
    result = AcceptanceResult.failure(
        code="ACCEPTANCE_CONFIG_ERROR",
        message="Acceptance configuration is invalid.",
        failed_phase="config",
        phases=[],
    )

    payload = result.to_dict()
    expected_keys = {
        "schema_version",
        "audit",
        "source",
        "environment",
        "preflight",
        "deploy",
        "compile",
        "smoke",
        "cleanup",
        "result",
    }
    assert set(payload) == expected_keys
    assert payload["result"] == {
        "success": False,
        "severity": "error",
        "code": "ACCEPTANCE_CONFIG_ERROR",
        "message": "Acceptance configuration is invalid.",
        "failed_phase": "config",
        "phases": [],
        "diagnostics": [],
    }
    for section in expected_keys - {"schema_version", "result"}:
        assert payload[section] == {
            "executed": False,
            "success": None,
            "code": None,
            "data": {},
            "diagnostics": [],
        }

    execution_reference = (
        Path(__file__).resolve().parents[1] / "docs/execution-reference.md"
    ).read_text(encoding="utf-8")
    section_start = execution_reference.index(
        "## Local Unity Bridge acceptance execution (Issue #186)"
    )
    section_end = execution_reference.index("\n## CI / テスト実行", section_start)
    documented_section = execution_reference[section_start:section_end]
    assert {key: f"`{key}`" in documented_section for key in expected_keys} == {
        key: True for key in expected_keys
    }


def test_explicit_evidence_preserves_semantic_section_ownership() -> None:
    evidence = AcceptanceEvidence(
        audit=AcceptanceEvidenceSection(
            executed=True,
            success=True,
            code="ACCEPTANCE_OPT_IN_CONFIRMED",
            data={
                "opt_in": True,
                "mode": "acceptance",
                "arguments": {
                    "project_root": "<project-root>",
                    "scope": "Assets/Acceptance",
                    "target_dir": "<target-dir>",
                    "watch_dir": "<redacted>",
                    "unity_log_file": "<redacted>",
                    "out_report": "reports/acceptance.json",
                },
                "deadlines": {
                    "recompile_timeout_sec": 120,
                    "environment_timeout_sec": 120,
                    "smoke_timeout_sec": 300,
                },
            },
        ),
        environment=AcceptanceEvidenceSection(
            executed=True,
            success=True,
            code="ACCEPTANCE_ENVIRONMENT_CAPTURED",
            data={"project_session": {"connection_state": "connected"}},
        ),
        preflight=AcceptanceEvidenceSection(
            executed=True,
            success=False,
            code="ACCEPTANCE_EDITOR_STATE_BLOCKED",
            data={"editor_state": {"dirty_scene_paths": []}},
        ),
    )
    result = AcceptanceResult.failure(
        code="ACCEPTANCE_EDITOR_STATE_BLOCKED",
        message="The running Unity Editor is not in an acceptance-safe state.",
        failed_phase="editor_state",
        phases=[],
        evidence=evidence,
    )

    payload = result.to_dict()
    assert payload["audit"] == evidence.audit.to_dict()
    assert payload["environment"] == evidence.environment.to_dict()
    assert payload["preflight"] == evidence.preflight.to_dict()
    assert payload["source"] == AcceptanceEvidenceSection().to_dict()
    assert payload["deploy"] == AcceptanceEvidenceSection().to_dict()
    assert "/private/watch" not in repr(payload)


def test_execution_reference_json_contract_matches_report_shape() -> None:
    expected_contract = {
        "schema_version": "unity_bridge_acceptance.v1",
        "top_level_keys": [
            "schema_version",
            "audit",
            "source",
            "environment",
            "preflight",
            "deploy",
            "compile",
            "smoke",
            "cleanup",
            "result",
        ],
        "result_keys": [
            "success",
            "severity",
            "code",
            "message",
            "failed_phase",
            "phases",
            "diagnostics",
        ],
        "unattempted_section": {
            "executed": False,
            "success": None,
            "code": None,
            "data": {},
            "diagnostics": [],
        },
        "audit_required_keys": ["opt_in", "mode", "arguments", "deadlines"],
        "deadline_values": {
            "recompile_timeout_sec": 120,
            "environment_timeout_sec": 120,
            "smoke_timeout_sec": 300,
        },
        "section_data_keys": {
            "source": [
                "head",
                "branch",
                "managed_source_clean",
                "managed_dirty_paths",
                "package_versions",
                "bridge_manifest_sha256",
                "bridge_files",
                "mcp_protocol_revision",
                "mcp_server",
            ],
            "environment": [
                "unity_version",
                "required_packages",
                "connection_identity",
                "project_session",
            ],
            "preflight": [
                "config_validation",
                "report_reservation",
                "editor_state",
                "blockers",
            ],
            "deploy": [
                "changed_deploy",
                "expected_manifest_sha256",
                "observed_manifest_sha256",
                "manifest_equal",
                "expected_bridge_version",
                "observed_bridge_version",
                "bridge_version_equal",
            ],
            "compile": [
                "baseline",
                "observation",
                "secondary_recompile",
                "console",
            ],
            "smoke": ["reflection", "runtime_probe", "suite"],
            "cleanup": ["status", "cleanup", "postconditions"],
        },
        "recovery_only_sections": [
            "audit",
            "environment",
            "preflight",
            "cleanup",
            "result",
        ],
        "terminal_precedence": [
            "cleanup_failure",
            "postcondition_failure",
            "smoke_failure",
            "success",
        ],
        "redaction": {
            "allowed_path_forms": ["project-relative", "redacted-marker"],
            "forbidden": [
                "absolute_paths",
                "raw_messages",
                "raw_diagnostics",
                "raw_exceptions",
            ],
        },
    }
    document = (
        Path(__file__).resolve().parents[1] / "docs/execution-reference.md"
    ).read_text(encoding="utf-8")
    section_start = document.index("### `unity_bridge_acceptance.v1` report")
    section_end = document.index("\n### Unresolved lease recovery", section_start)
    report_section = document[section_start:section_end]
    match = re.search(
        r"```json\n(.*?)\n```",
        report_section,
        flags=re.DOTALL,
    )
    assert match is not None
    assert json.loads(match.group(1)) == expected_contract

    payload = AcceptanceResult.failure(
        code="ACCEPTANCE_CONFIG_ERROR",
        message="Acceptance configuration is invalid.",
        failed_phase="config",
        phases=[],
    ).to_dict()
    assert list(payload) == expected_contract["top_level_keys"]
    assert list(payload["result"]) == expected_contract["result_keys"]
    assert payload["source"] == expected_contract["unattempted_section"]
    assert payload["audit"]["data"] == {}
