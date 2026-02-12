from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

# Allow direct script execution (`python scripts/unity_bridge_smoke.py`) to
# resolve project-local imports from repository root.
ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from unitytool.bridge_smoke import (
    PROTOCOL_VERSION,
    UNITY_COMMAND_ENV,
    UNITY_EXECUTE_METHOD_ENV,
    UNITY_LOG_FILE_ENV,
    UNITY_PROJECT_PATH_ENV,
    UNITY_TIMEOUT_SEC_ENV,
    build_bridge_env as _build_bridge_env_impl,
    build_bridge_request as _build_bridge_request_impl,
    load_patch_plan as _load_patch_plan_impl,
    resolve_expected_applied as _resolve_expected_applied_impl,
    run_bridge as _run_bridge_impl,
    validate_bridge_response as _validate_bridge_response_impl,
    validate_expectation as _validate_expectation_impl,
)


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
        "--expected-applied",
        type=int,
        default=None,
        help="Optional expected data.applied value for bridge response.",
    )
    parser.add_argument(
        "--expect-applied-from-plan",
        action="store_true",
        help=(
            "Infer expected applied count from patch plan ops length when "
            "--expected-applied is not specified and --expect-failure is not set."
        ),
    )
    parser.add_argument(
        "--out",
        default=None,
        help="Optional output JSON path for bridge response.",
    )
    return parser


def _load_patch_plan(path: Path) -> dict[str, Any]:
    return _load_patch_plan_impl(path)


def _build_bridge_request(plan: dict[str, Any]) -> dict[str, Any]:
    return _build_bridge_request_impl(plan)


def _build_bridge_env(args: argparse.Namespace) -> dict[str, str]:
    return _build_bridge_env_impl(
        unity_command=args.unity_command,
        unity_project_path=args.unity_project_path,
        unity_execute_method=args.unity_execute_method,
        unity_timeout_sec=args.unity_timeout_sec,
        unity_log_file=args.unity_log_file,
    )


def _run_bridge(
    *,
    bridge_script: Path,
    python_executable: str,
    request: dict[str, Any],
    env: dict[str, str],
) -> dict[str, Any]:
    return _run_bridge_impl(
        bridge_script=bridge_script,
        python_executable=python_executable,
        request=request,
        env=env,
    )


def _validate_bridge_response(payload: dict[str, Any]) -> None:
    _validate_bridge_response_impl(payload)


def _resolve_expected_applied(
    *,
    plan: dict[str, Any],
    expected_applied: int | None,
    expect_applied_from_plan: bool,
    expect_failure: bool,
) -> tuple[int | None, str]:
    return _resolve_expected_applied_impl(
        plan=plan,
        expected_applied=expected_applied,
        expect_applied_from_plan=expect_applied_from_plan,
        expect_failure=expect_failure,
    )


def _validate_expectation(
    response: dict[str, Any],
    expect_failure: bool,
    expected_applied: int | None = None,
    expected_applied_source: str | None = None,
) -> bool:
    return _validate_expectation_impl(
        response,
        expect_failure,
        expected_applied,
        expected_applied_source,
    )


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.expected_applied is not None and args.expected_applied < 0:
        parser.error("--expected-applied must be greater than or equal to 0.")

    plan_path = Path(args.plan)
    bridge_script = Path(args.bridge_script)
    try:
        plan = _load_patch_plan(plan_path)
        expected_applied, expected_applied_source = _resolve_expected_applied(
            plan=plan,
            expected_applied=args.expected_applied,
            expect_applied_from_plan=args.expect_applied_from_plan,
            expect_failure=args.expect_failure,
        )
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

    matched_expectation = _validate_expectation(
        response,
        args.expect_failure,
        expected_applied,
        expected_applied_source,
    )
    if args.out:
        output_path = Path(args.out)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            json.dumps(response, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    print(json.dumps(response, ensure_ascii=False, indent=2))
    return 0 if matched_expectation else 1


if __name__ == "__main__":
    raise SystemExit(main())
