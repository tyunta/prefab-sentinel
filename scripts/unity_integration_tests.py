#!/usr/bin/env python3
"""Run Unity C# integration tests for PrefabSentinel bridge operations.

Usage::

    python scripts/unity_integration_tests.py \
        --unity-command "C:/Program Files/Unity/Hub/Editor/2022.3.22f1/Editor/Unity.exe" \
        --unity-project-path ../UnityTool_sample/avatar \
        --out-dir reports/integration

The script:
1. Deploys C# test files into the Unity project.
2. Invokes Unity batchmode to execute the test harness.
3. Parses and prints structured results.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Allow running from repo root without install
_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

from prefab_sentinel.integration_tests import (  # noqa: E402
    deploy_test_files,
    extract_unity_log_errors,
    run_integration_tests,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run Unity integration tests.")
    parser.add_argument("--unity-command", required=True, help="Path to Unity executable.")
    parser.add_argument("--unity-project-path", required=True, help="Unity project directory.")
    parser.add_argument("--out-dir", default="reports/integration", help="Output directory.")
    parser.add_argument("--timeout-sec", type=int, default=300, help="Batchmode timeout (seconds).")
    parser.add_argument("--skip-deploy", action="store_true", help="Skip C# file deployment.")
    args = parser.parse_args(argv)

    project_path = Path(args.unity_project_path).resolve()
    out_dir = Path(args.out_dir).resolve()

    if not project_path.is_dir():
        print(f"Error: Unity project path does not exist: {project_path}", file=sys.stderr)
        return 1

    # Deploy C# files
    if not args.skip_deploy:
        try:
            dest = deploy_test_files(project_path)
            print(f"Deployed C# files to {dest}")
        except FileNotFoundError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 1

    # Run tests
    try:
        results = run_integration_tests(
            args.unity_command,
            project_path,
            out_dir,
            timeout_sec=args.timeout_sec,
        )
    except RuntimeError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        # Try to extract log errors
        log_path = out_dir / "unity_integration.log"
        errors = extract_unity_log_errors(log_path, max_lines=20)
        if errors:
            print("\nUnity log errors:", file=sys.stderr)
            for line in errors:
                print(f"  {line}", file=sys.stderr)
        return 1

    # Print summary
    data = results.get("data", {})
    total = data.get("total", 0)
    passed = data.get("passed", 0)
    failed = data.get("failed", 0)
    duration = data.get("duration_sec", 0)

    print(f"\n{'='*60}")
    print(f"Integration Tests: {passed}/{total} passed ({duration:.2f}s)")
    print(f"{'='*60}")

    cases = data.get("cases", [])
    for case in cases:
        status = "PASS" if case.get("passed") else "FAIL"
        name = case.get("name", "?")
        msg = case.get("message", "")
        line = f"  [{status}] {name}"
        if msg and not case.get("passed"):
            line += f" — {msg}"
        print(line)

    if failed > 0:
        print(f"\n{failed} test(s) failed.")
        return 1

    print("\nAll tests passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
