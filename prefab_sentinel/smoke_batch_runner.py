from __future__ import annotations

import argparse
import json
import subprocess
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from prefab_sentinel.bridge_smoke import load_patch_plan
from prefab_sentinel.json_io import dump_json, load_json
from prefab_sentinel.smoke_batch_case import _resolve_case_unity_timeout_sec, _wsl_path_exists

if TYPE_CHECKING:
    from prefab_sentinel.smoke_batch import SmokeCase


def _build_smoke_command(
    *,
    smoke_script: Path,
    python_executable: str,
    bridge_script: Path,
    unity_command: str | None,
    unity_execute_method: str,
    unity_timeout_sec: int | None,
    case: SmokeCase,
    response_out: Path,
    unity_log_file: Path,
) -> list[str]:
    command = [
        python_executable,
        str(smoke_script),
        "--plan",
        str(case.plan),
        "--bridge-script",
        str(bridge_script),
        "--python",
        python_executable,
        "--unity-project-path",
        str(case.project_path),
        "--unity-execute-method",
        unity_execute_method,
        "--unity-log-file",
        str(unity_log_file),
        "--out",
        str(response_out),
    ]
    if unity_command is not None:
        command.extend(["--unity-command", unity_command])
    if unity_timeout_sec is not None:
        command.extend(["--unity-timeout-sec", str(unity_timeout_sec)])
    if getattr(case, "expect_failure", False):
        command.append("--expect-failure")
    expected_code = getattr(case, "expected_code", None)
    if expected_code is not None:
        command.extend(["--expected-code", expected_code])
    return command


def _parse_case_payload(
    *,
    case: SmokeCase,
    exit_code: int,
    stdout_text: str,
    stderr_text: str,
) -> dict[str, Any]:
    case_name = getattr(case, "name", "")
    try:
        payload = load_json(stdout_text)
    except json.JSONDecodeError:
        payload = {
            "success": False,
            "severity": "error",
            "code": "SMOKE_BATCH_STDOUT_JSON",
            "message": "Child smoke stdout is not valid JSON.",
            "data": {
                "target": case_name,
                "exit_code": exit_code,
                "stdout": stdout_text,
                "stderr": stderr_text,
            },
            "diagnostics": [],
        }
    if not isinstance(payload, dict):
        return {
            "success": False,
            "severity": "error",
            "code": "SMOKE_BATCH_STDOUT_SCHEMA",
            "message": "Child smoke stdout root must be an object.",
            "data": {
                "target": case_name,
                "exit_code": exit_code,
                "stdout": stdout_text,
                "stderr": stderr_text,
            },
            "diagnostics": [],
        }
    return payload


def _extract_applied_count(payload: dict[str, Any]) -> int | None:
    data = payload.get("data")
    if not isinstance(data, dict):
        return None
    applied = data.get("applied")
    return applied if isinstance(applied, int) else None


def _resolve_expected_applied(
    *,
    case: SmokeCase,
    expect_applied_from_plan: bool,
) -> tuple[int | None, str]:
    expected_applied = getattr(case, "expected_applied", None)
    if expected_applied is not None:
        return expected_applied, "cli"
    if not expect_applied_from_plan:
        return None, "none"
    if getattr(case, "expect_failure", False):
        return None, "skipped_expect_failure"
    plan = load_patch_plan(case.plan)
    ops = plan.get("ops", [])
    return len(ops), "plan_ops"


def _run_smoke_with_retries(
    *,
    command: list[str],
    max_retries: int,
    retry_delay_sec: float,
    timeout_sec: float | None = None,
) -> tuple[subprocess.CompletedProcess[str], int, float]:
    attempts = 0
    started_at = time.perf_counter()
    while True:
        attempts += 1
        try:
            completed = subprocess.run(
                command,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
                timeout=timeout_sec,
            )
        except subprocess.TimeoutExpired:
            elapsed_sec = time.perf_counter() - started_at
            completed = subprocess.CompletedProcess(
                args=command,
                returncode=-1,
                stdout="",
                stderr=f"Process timed out after {timeout_sec}s",
            )
            return completed, attempts, elapsed_sec
        if completed.returncode == 0:
            elapsed_sec = time.perf_counter() - started_at
            return completed, attempts, elapsed_sec
        if attempts > max_retries:
            elapsed_sec = time.perf_counter() - started_at
            return completed, attempts, elapsed_sec
        if retry_delay_sec > 0.0:
            time.sleep(retry_delay_sec)


def _execute_batch_cases(
    args: argparse.Namespace,
    cases: list,  # list[SmokeCase]
    out_dir: Path,
    *,
    smoke_script: Path,
    bridge_script: Path,
    timeout_profile_overrides: dict[str, int],
) -> tuple[list[dict[str, Any]], Exception | None]:
    """Execute each smoke case with retries and response parsing.

    Returns ``(results, partial_error)``.  *partial_error* is non-None when the
    loop was interrupted by an exception mid-batch.
    """
    results: list[dict[str, Any]] = []
    partial_error: Exception | None = None
    for case in cases:
        try:
            if not _wsl_path_exists(case.plan):
                raise FileNotFoundError(
                    f"Plan not found for {case.name}: {case.plan}"
                )
            if not _wsl_path_exists(case.project_path):
                raise FileNotFoundError(
                    f"Project path not found for {case.name}: {case.project_path}"
                )

            case_timeout_sec, timeout_source = _resolve_case_unity_timeout_sec(
                case=case,
                default_timeout_sec=args.unity_timeout_sec,
                avatar_timeout_sec=args.avatar_unity_timeout_sec,
                world_timeout_sec=args.world_unity_timeout_sec,
                timeout_profile_overrides=timeout_profile_overrides,
            )

            case_dir = out_dir / case.name
            case_dir.mkdir(parents=True, exist_ok=True)
            response_path = case_dir / "response.json"
            unity_log_file = case_dir / "unity.log"
            command = _build_smoke_command(
                smoke_script=smoke_script,
                python_executable=args.python,
                bridge_script=bridge_script,
                unity_command=args.unity_command,
                unity_execute_method=args.unity_execute_method,
                unity_timeout_sec=case_timeout_sec,
                case=case,
                response_out=response_path,
                unity_log_file=unity_log_file,
            )
            # Add 30s buffer over Unity-side timeout so Python outlives the
            # Unity process and can capture its output on timeout.
            subprocess_timeout = (
                case_timeout_sec + 30 if case_timeout_sec is not None else None
            )
            completed, attempts, duration_sec = _run_smoke_with_retries(
                command=command,
                max_retries=args.max_retries,
                retry_delay_sec=args.retry_delay_sec,
                timeout_sec=subprocess_timeout,
            )
            case_payload = _parse_case_payload(
                case=case,
                exit_code=completed.returncode,
                stdout_text=completed.stdout,
                stderr_text=completed.stderr,
            )
            try:
                expected_applied, expected_applied_source = _resolve_expected_applied(
                    case=case,
                    expect_applied_from_plan=args.expect_applied_from_plan,
                )
            except (OSError, json.JSONDecodeError, ValueError) as exc:
                raise ValueError(
                    f"Failed to resolve expected applied count for {case.name}: {exc}"
                ) from exc
            actual_applied = _extract_applied_count(case_payload)
            applied_matches: bool | None = None
            if expected_applied is not None:
                applied_matches = actual_applied == expected_applied
            expected_code = case.expected_code
            actual_code_raw = case_payload.get("code")
            actual_code = actual_code_raw if isinstance(actual_code_raw, str) else ""
            code_matches: bool | None = None
            if expected_code is not None:
                code_matches = actual_code == expected_code
            matched_expectation = completed.returncode == 0
            if code_matches is False:
                matched_expectation = False
            if applied_matches is False:
                matched_expectation = False
            if not response_path.exists():
                response_path.write_text(
                    dump_json(case_payload),
                    encoding="utf-8",
                )
            results.append(
                {
                    "name": case.name,
                    "plan": str(case.plan),
                    "project_path": str(case.project_path),
                    "expect_failure": case.expect_failure,
                    "expected_code": expected_code,
                    "actual_code": actual_code,
                    "code_matches": code_matches,
                    "expected_applied": expected_applied,
                    "expected_applied_source": expected_applied_source,
                    "actual_applied": actual_applied,
                    "applied_matches": applied_matches,
                    "matched_expectation": matched_expectation,
                    "attempts": attempts,
                    "duration_sec": round(duration_sec, 6),
                    "unity_timeout_sec": case_timeout_sec,
                    "timeout_source": timeout_source,
                    "exit_code": completed.returncode,
                    "response_code": str(case_payload.get("code", "")),
                    "response_severity": str(case_payload.get("severity", "")),
                    "response_path": str(response_path),
                    "unity_log_file": str(unity_log_file),
                }
            )
        except (FileNotFoundError, ValueError, OSError) as exc:
            partial_error = exc
            break
    return results, partial_error
