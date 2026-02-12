from __future__ import annotations

import argparse
import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
UNITY_BRIDGE_SMOKE_SCRIPT = SCRIPT_DIR / "unity_bridge_smoke.py"
DEFAULT_EXECUTE_METHOD = "PrefabSentinel.UnityPatchBridge.ApplyFromJson"
DEFAULT_OUT_DIR = Path("reports") / "bridge_smoke"


@dataclass(frozen=True)
class SmokeCase:
    name: str
    plan: Path
    project_path: Path
    expect_failure: bool = False


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="bridge_smoke_samples",
        description="Run unity_bridge_smoke.py for sample avatar/world cases.",
    )
    parser.add_argument(
        "--targets",
        nargs="+",
        choices=("avatar", "world", "all"),
        default=["all"],
        help="Smoke targets. 'all' expands to avatar + world.",
    )
    parser.add_argument(
        "--avatar-plan",
        default=str(Path("sample") / "avatar" / "config" / "prefab_patch_plan.json"),
        help="Patch plan for avatar target.",
    )
    parser.add_argument(
        "--world-plan",
        default=str(Path("sample") / "world" / "config" / "prefab_patch_plan.json"),
        help="Patch plan for world target.",
    )
    parser.add_argument(
        "--avatar-project-path",
        default=str(Path("sample") / "avatar"),
        help="Unity project path for avatar target.",
    )
    parser.add_argument(
        "--world-project-path",
        default=str(Path("sample") / "world"),
        help="Unity project path for world target.",
    )
    parser.add_argument(
        "--avatar-expect-failure",
        action="store_true",
        help="Pass --expect-failure for avatar target.",
    )
    parser.add_argument(
        "--world-expect-failure",
        action="store_true",
        help="Pass --expect-failure for world target.",
    )
    parser.add_argument(
        "--python",
        default=sys.executable,
        help="Python executable used for child scripts.",
    )
    parser.add_argument(
        "--smoke-script",
        default=str(UNITY_BRIDGE_SMOKE_SCRIPT),
        help="Path to scripts/unity_bridge_smoke.py.",
    )
    parser.add_argument(
        "--bridge-script",
        default=str(Path("tools") / "unity_patch_bridge.py"),
        help="Path to tools/unity_patch_bridge.py.",
    )
    parser.add_argument(
        "--unity-command",
        default=None,
        help="Optional UNITYTOOL_UNITY_COMMAND override.",
    )
    parser.add_argument(
        "--unity-execute-method",
        default=DEFAULT_EXECUTE_METHOD,
        help="UNITYTOOL_UNITY_EXECUTE_METHOD value for all targets.",
    )
    parser.add_argument(
        "--unity-timeout-sec",
        type=int,
        default=None,
        help="Optional UNITYTOOL_UNITY_TIMEOUT_SEC override.",
    )
    parser.add_argument(
        "--out-dir",
        default=str(DEFAULT_OUT_DIR),
        help="Output root directory for per-target artifacts and summary.",
    )
    parser.add_argument(
        "--summary-json",
        default=None,
        help="Optional summary JSON output path (default: <out-dir>/summary.json).",
    )
    parser.add_argument(
        "--summary-md",
        default=None,
        help="Optional summary Markdown output path.",
    )
    return parser


def _resolve_targets(raw_targets: list[str]) -> list[str]:
    expanded: list[str] = []
    for item in raw_targets:
        if item == "all":
            expanded.extend(["avatar", "world"])
        else:
            expanded.append(item)
    unique: list[str] = []
    seen: set[str] = set()
    for target in expanded:
        if target not in seen:
            seen.add(target)
            unique.append(target)
    return unique


def _build_cases(args: argparse.Namespace) -> list[SmokeCase]:
    targets = _resolve_targets(args.targets)
    cases_map: dict[str, SmokeCase] = {
        "avatar": SmokeCase(
            name="avatar",
            plan=Path(args.avatar_plan),
            project_path=Path(args.avatar_project_path),
            expect_failure=bool(args.avatar_expect_failure),
        ),
        "world": SmokeCase(
            name="world",
            plan=Path(args.world_plan),
            project_path=Path(args.world_project_path),
            expect_failure=bool(args.world_expect_failure),
        ),
    }
    return [cases_map[target] for target in targets]


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
    if case.expect_failure:
        command.append("--expect-failure")
    return command


def _parse_case_payload(
    *,
    case: SmokeCase,
    exit_code: int,
    stdout_text: str,
    stderr_text: str,
) -> dict[str, Any]:
    try:
        payload = json.loads(stdout_text)
    except json.JSONDecodeError:
        payload = {
            "success": False,
            "severity": "error",
            "code": "SMOKE_BATCH_STDOUT_JSON",
            "message": "Child smoke stdout is not valid JSON.",
            "data": {
                "target": case.name,
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
                "target": case.name,
                "exit_code": exit_code,
                "stdout": stdout_text,
                "stderr": stderr_text,
            },
            "diagnostics": [],
        }
    return payload


def _render_markdown_summary(payload: dict[str, Any]) -> str:
    data = payload.get("data", {})
    cases = data.get("cases", [])
    lines = [
        "# Unity Bridge Smoke Batch",
        "",
        f"- Success: {payload.get('success')}",
        f"- Severity: {payload.get('severity')}",
        f"- Code: {payload.get('code')}",
        f"- Message: {payload.get('message')}",
        f"- Total: {data.get('total_cases', 0)}",
        f"- Passed: {data.get('passed_cases', 0)}",
        f"- Failed: {data.get('failed_cases', 0)}",
        "",
        "| case | matched | exit_code | response_code | response_path | unity_log_file |",
        "| --- | --- | ---: | --- | --- | --- |",
    ]
    for case in cases:
        lines.append(
            "| {name} | {matched} | {exit_code} | {response_code} | {response_path} | {unity_log_file} |".format(
                name=case.get("name", ""),
                matched=case.get("matched_expectation", False),
                exit_code=case.get("exit_code", ""),
                response_code=case.get("response_code", ""),
                response_path=case.get("response_path", ""),
                unity_log_file=case.get("unity_log_file", ""),
            )
        )
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    smoke_script = Path(args.smoke_script)
    bridge_script = Path(args.bridge_script)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    cases = _build_cases(args)
    results: list[dict[str, Any]] = []
    for case in cases:
        if not case.plan.exists():
            raise FileNotFoundError(f"Plan not found for {case.name}: {case.plan}")
        if not case.project_path.exists():
            raise FileNotFoundError(
                f"Project path not found for {case.name}: {case.project_path}"
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
            unity_timeout_sec=args.unity_timeout_sec,
            case=case,
            response_out=response_path,
            unity_log_file=unity_log_file,
        )
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        case_payload = _parse_case_payload(
            case=case,
            exit_code=completed.returncode,
            stdout_text=completed.stdout,
            stderr_text=completed.stderr,
        )
        if not response_path.exists():
            response_path.write_text(
                json.dumps(case_payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        results.append(
            {
                "name": case.name,
                "plan": str(case.plan),
                "project_path": str(case.project_path),
                "expect_failure": case.expect_failure,
                "matched_expectation": completed.returncode == 0,
                "exit_code": completed.returncode,
                "response_code": str(case_payload.get("code", "")),
                "response_severity": str(case_payload.get("severity", "")),
                "response_path": str(response_path),
                "unity_log_file": str(unity_log_file),
            }
        )

    failed_cases = [item for item in results if not item["matched_expectation"]]
    summary_payload = {
        "success": len(failed_cases) == 0,
        "severity": "info" if len(failed_cases) == 0 else "error",
        "code": "SMOKE_BATCH_OK" if len(failed_cases) == 0 else "SMOKE_BATCH_FAILED",
        "message": (
            "All bridge smoke cases matched expectations."
            if len(failed_cases) == 0
            else "Some bridge smoke cases failed to match expectations."
        ),
        "data": {
            "total_cases": len(results),
            "passed_cases": len(results) - len(failed_cases),
            "failed_cases": len(failed_cases),
            "cases": results,
        },
        "diagnostics": [],
    }

    summary_json = Path(args.summary_json) if args.summary_json else out_dir / "summary.json"
    summary_json.parent.mkdir(parents=True, exist_ok=True)
    summary_json.write_text(
        json.dumps(summary_payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    if args.summary_md:
        summary_md = Path(args.summary_md)
        summary_md.parent.mkdir(parents=True, exist_ok=True)
        summary_md.write_text(
            _render_markdown_summary(summary_payload),
            encoding="utf-8",
        )

    print(summary_json)
    return 0 if summary_payload["success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
