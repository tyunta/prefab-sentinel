from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

PROTOCOL_VERSION = 1
VALID_SEVERITIES = {"info", "warning", "error", "critical"}
UNITY_COMMAND_ENV = "UNITYTOOL_UNITY_COMMAND"
UNITY_PROJECT_PATH_ENV = "UNITYTOOL_UNITY_PROJECT_PATH"
UNITY_EXECUTE_METHOD_ENV = "UNITYTOOL_UNITY_EXECUTE_METHOD"
UNITY_TIMEOUT_SEC_ENV = "UNITYTOOL_UNITY_TIMEOUT_SEC"
UNITY_LOG_FILE_ENV = "UNITYTOOL_UNITY_LOG_FILE"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="unity_bridge_smoke",
        description="Run an end-to-end smoke test against tools/unity_patch_bridge.py.",
    )
    parser.add_argument("--plan", required=True, help="Patch plan JSON path.")
    parser.add_argument(
        "--bridge-script",
        default=str(Path("tools") / "unity_patch_bridge.py"),
        help="Bridge script path (default: tools/unity_patch_bridge.py).",
    )
    parser.add_argument(
        "--python",
        default=sys.executable,
        help="Python executable used to run --bridge-script.",
    )
    parser.add_argument(
        "--unity-command",
        default=None,
        help=f"Override {UNITY_COMMAND_ENV} for this run.",
    )
    parser.add_argument(
        "--unity-project-path",
        default=None,
        help=f"Override {UNITY_PROJECT_PATH_ENV} for this run.",
    )
    parser.add_argument(
        "--unity-execute-method",
        default=None,
        help=f"Override {UNITY_EXECUTE_METHOD_ENV} for this run.",
    )
    parser.add_argument(
        "--unity-timeout-sec",
        type=int,
        default=None,
        help=f"Override {UNITY_TIMEOUT_SEC_ENV} for this run.",
    )
    parser.add_argument(
        "--unity-log-file",
        default=None,
        help=f"Override {UNITY_LOG_FILE_ENV} for this run.",
    )
    parser.add_argument(
        "--expect-failure",
        action="store_true",
        help="Expect bridge result success=false (exit 0 when failure is observed).",
    )
    parser.add_argument(
        "--out",
        default=None,
        help="Optional output JSON path for bridge response.",
    )
    return parser


def _load_patch_plan(path: Path) -> dict[str, Any]:
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


def _build_bridge_request(plan: dict[str, Any]) -> dict[str, Any]:
    return {
        "protocol_version": PROTOCOL_VERSION,
        "target": str(plan.get("target", "")).strip(),
        "ops": plan.get("ops", []),
    }


def _build_bridge_env(args: argparse.Namespace) -> dict[str, str]:
    env = os.environ.copy()
    overrides: list[tuple[str, str | int | None]] = [
        (UNITY_COMMAND_ENV, args.unity_command),
        (UNITY_PROJECT_PATH_ENV, args.unity_project_path),
        (UNITY_EXECUTE_METHOD_ENV, args.unity_execute_method),
        (UNITY_TIMEOUT_SEC_ENV, args.unity_timeout_sec),
        (UNITY_LOG_FILE_ENV, args.unity_log_file),
    ]
    for key, value in overrides:
        if value is not None:
            env[key] = str(value)
    return env


def _run_bridge(
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
    _validate_bridge_response(payload)
    return payload


def _validate_bridge_response(payload: dict[str, Any]) -> None:
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


def _validate_expectation(response: dict[str, Any], expect_failure: bool) -> bool:
    success = bool(response.get("success"))
    return (not success) if expect_failure else success


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    plan_path = Path(args.plan)
    bridge_script = Path(args.bridge_script)
    try:
        plan = _load_patch_plan(plan_path)
        request = _build_bridge_request(plan)
        env = _build_bridge_env(args)
        response = _run_bridge(
            bridge_script=bridge_script,
            python_executable=args.python,
            request=request,
            env=env,
        )
    except (OSError, ValueError, RuntimeError) as exc:
        payload = {
            "success": False,
            "severity": "error",
            "code": "SMOKE_BRIDGE_ERROR",
            "message": str(exc),
            "data": {
                "plan": str(plan_path),
                "bridge_script": str(bridge_script),
            },
            "diagnostics": [],
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 1

    if args.out:
        output_path = Path(args.out)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            json.dumps(response, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    print(json.dumps(response, ensure_ascii=False, indent=2))
    return 0 if _validate_expectation(response, args.expect_failure) else 1


if __name__ == "__main__":
    raise SystemExit(main())
