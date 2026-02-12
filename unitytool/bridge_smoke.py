from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any

PROTOCOL_VERSION = 1
VALID_SEVERITIES = {"info", "warning", "error", "critical"}
UNITY_COMMAND_ENV = "UNITYTOOL_UNITY_COMMAND"
UNITY_PROJECT_PATH_ENV = "UNITYTOOL_UNITY_PROJECT_PATH"
UNITY_EXECUTE_METHOD_ENV = "UNITYTOOL_UNITY_EXECUTE_METHOD"
UNITY_TIMEOUT_SEC_ENV = "UNITYTOOL_UNITY_TIMEOUT_SEC"
UNITY_LOG_FILE_ENV = "UNITYTOOL_UNITY_LOG_FILE"


def load_patch_plan(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("Patch plan root must be an object.")
    target = payload.get("target")
    ops = payload.get("ops")
    if not isinstance(target, str) or not target.strip():
        raise ValueError("Patch plan field 'target' must be a non-empty string.")
    if not isinstance(ops, list):
        raise ValueError("Patch plan field 'ops' must be an array.")
    return payload


def build_bridge_request(plan: dict[str, Any]) -> dict[str, Any]:
    return {
        "protocol_version": PROTOCOL_VERSION,
        "target": str(plan.get("target", "")).strip(),
        "ops": plan.get("ops", []),
    }


def resolve_expected_applied(
    *,
    plan: dict[str, Any],
    expected_applied: int | None,
    expect_applied_from_plan: bool,
    expect_failure: bool,
) -> tuple[int | None, str]:
    if expected_applied is not None:
        return expected_applied, "cli"
    if not expect_applied_from_plan:
        return None, "none"
    if expect_failure:
        return None, "skipped_expect_failure"
    ops = plan.get("ops")
    if not isinstance(ops, list):
        raise ValueError("Patch plan field 'ops' must be an array.")
    return len(ops), "plan_ops"


def build_bridge_env(
    *,
    unity_command: str | None = None,
    unity_project_path: str | None = None,
    unity_execute_method: str | None = None,
    unity_timeout_sec: int | None = None,
    unity_log_file: str | None = None,
    base_env: dict[str, str] | None = None,
) -> dict[str, str]:
    env = dict(base_env) if base_env is not None else os.environ.copy()
    overrides: list[tuple[str, str | int | None]] = [
        (UNITY_COMMAND_ENV, unity_command),
        (UNITY_PROJECT_PATH_ENV, unity_project_path),
        (UNITY_EXECUTE_METHOD_ENV, unity_execute_method),
        (UNITY_TIMEOUT_SEC_ENV, unity_timeout_sec),
        (UNITY_LOG_FILE_ENV, unity_log_file),
    ]
    for key, value in overrides:
        if value is not None:
            env[key] = str(value)
    return env


def validate_bridge_response(payload: dict[str, Any]) -> None:
    required_fields = ("success", "severity", "code", "message", "data", "diagnostics")
    missing_fields = [field for field in required_fields if field not in payload]
    if missing_fields:
        raise RuntimeError(
            "Bridge response is missing required fields: "
            + ", ".join(missing_fields)
            + "."
        )
    success = payload.get("success")
    severity = payload.get("severity")
    code = payload.get("code")
    message = payload.get("message")
    data = payload.get("data")
    diagnostics = payload.get("diagnostics")
    if not isinstance(success, bool):
        raise RuntimeError("Bridge response field 'success' must be a boolean.")
    if not isinstance(severity, str) or severity not in VALID_SEVERITIES:
        raise RuntimeError(
            "Bridge response field 'severity' must be one of: "
            + ", ".join(sorted(VALID_SEVERITIES))
            + "."
        )
    if not isinstance(code, str) or not code.strip():
        raise RuntimeError("Bridge response field 'code' must be a non-empty string.")
    if not isinstance(message, str):
        raise RuntimeError("Bridge response field 'message' must be a string.")
    if not isinstance(data, dict):
        raise RuntimeError("Bridge response field 'data' must be an object.")
    if not isinstance(diagnostics, list):
        raise RuntimeError("Bridge response field 'diagnostics' must be an array.")


def run_bridge(
    *,
    bridge_script: Path,
    python_executable: str,
    request: dict[str, Any],
    env: dict[str, str],
) -> dict[str, Any]:
    completed = subprocess.run(
        [python_executable, str(bridge_script)],
        input=json.dumps(request, ensure_ascii=False),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(
            f"Bridge process exited with {completed.returncode}: {completed.stderr.strip()}"
        )
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError("Bridge stdout is not valid JSON.") from exc
    if not isinstance(payload, dict):
        raise RuntimeError("Bridge response root must be an object.")
    validate_bridge_response(payload)
    return payload


def extract_applied_count(response: dict[str, Any]) -> int | None:
    data = response.get("data")
    if not isinstance(data, dict):
        return None
    applied = data.get("applied")
    if isinstance(applied, bool):
        return None
    return applied if isinstance(applied, int) else None


def apply_applied_expectation(
    response: dict[str, Any],
    expected_applied: int | None,
    expected_applied_source: str | None = None,
) -> bool | None:
    if expected_applied is None:
        return None
    data = response.get("data")
    if not isinstance(data, dict):
        return None
    actual_applied = extract_applied_count(response)
    applied_matches = actual_applied == expected_applied
    data["expected_applied"] = expected_applied
    data["expected_applied_source"] = (
        expected_applied_source if expected_applied_source is not None else "cli"
    )
    data["actual_applied"] = actual_applied
    data["applied_matches"] = applied_matches
    return applied_matches


def apply_code_expectation(
    response: dict[str, Any],
    expected_code: str | None,
) -> bool | None:
    if expected_code is None:
        return None
    data = response.get("data")
    if not isinstance(data, dict):
        return None
    actual_code = response.get("code")
    actual_code_text = actual_code if isinstance(actual_code, str) else None
    code_matches = actual_code_text == expected_code
    data["expected_code"] = expected_code
    data["actual_code"] = actual_code_text
    data["code_matches"] = code_matches
    return code_matches


def validate_expectation(
    response: dict[str, Any],
    expect_failure: bool,
    expected_applied: int | None = None,
    expected_applied_source: str | None = None,
    expected_code: str | None = None,
) -> bool:
    success = bool(response.get("success"))
    matched_expectation = (not success) if expect_failure else success
    code_matches = apply_code_expectation(response, expected_code)
    if code_matches is False:
        return False
    applied_matches = apply_applied_expectation(
        response,
        expected_applied,
        expected_applied_source,
    )
    if applied_matches is False:
        return False
    return matched_expectation
