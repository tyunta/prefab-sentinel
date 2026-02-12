from __future__ import annotations

import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path

from scripts.bridge_smoke_samples import (
    SmokeCase,
    _build_smoke_command,
    _render_markdown_summary,
    _resolve_targets,
    main,
)


class BridgeSmokeSamplesTests(unittest.TestCase):
    def test_resolve_targets_expands_all(self) -> None:
        self.assertEqual(["avatar", "world"], _resolve_targets(["all"]))

    def test_resolve_targets_deduplicates(self) -> None:
        self.assertEqual(["avatar", "world"], _resolve_targets(["avatar", "all", "world"]))

    def test_build_smoke_command_includes_expected_flags(self) -> None:
        command = _build_smoke_command(
            smoke_script=Path("scripts/unity_bridge_smoke.py"),
            python_executable="python",
            bridge_script=Path("tools/unity_patch_bridge.py"),
            unity_command="C:/Unity/Editor/Unity.exe",
            unity_execute_method="PrefabSentinel.UnityPatchBridge.ApplyFromJson",
            unity_timeout_sec=240,
            case=SmokeCase(
                name="avatar",
                plan=Path("sample/avatar/config/prefab_patch_plan.json"),
                project_path=Path("sample/avatar"),
                expect_failure=True,
            ),
            response_out=Path("reports/bridge_smoke/avatar/response.json"),
            unity_log_file=Path("reports/bridge_smoke/avatar/unity.log"),
        )
        self.assertIn("--plan", command)
        self.assertIn(str(Path("sample/avatar/config/prefab_patch_plan.json")), command)
        self.assertIn("--unity-project-path", command)
        self.assertIn(str(Path("sample/avatar")), command)
        self.assertIn("--unity-command", command)
        self.assertIn("C:/Unity/Editor/Unity.exe", command)
        self.assertIn("--unity-timeout-sec", command)
        self.assertIn("240", command)
        self.assertIn("--expect-failure", command)

    def test_render_markdown_summary_contains_case_rows(self) -> None:
        markdown = _render_markdown_summary(
            {
                "success": False,
                "severity": "error",
                "code": "SMOKE_BATCH_FAILED",
                "message": "failed",
                "data": {
                    "total_cases": 1,
                    "passed_cases": 0,
                    "failed_cases": 1,
                    "cases": [
                        {
                            "name": "avatar",
                            "matched_expectation": False,
                            "attempts": 2,
                            "exit_code": 1,
                            "response_code": "SMOKE_BRIDGE_ERROR",
                            "response_path": "reports/bridge_smoke/avatar/response.json",
                            "unity_log_file": "reports/bridge_smoke/avatar/unity.log",
                        }
                    ],
                },
            }
        )
        self.assertIn("| avatar | False | 2 | 1 | SMOKE_BRIDGE_ERROR |", markdown)

    def test_main_writes_summary_and_returns_zero(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            plan = root / "avatar_plan.json"
            project = root / "avatar_project"
            smoke_script = root / "fake_smoke.py"
            out_dir = root / "reports"
            project.mkdir(parents=True, exist_ok=True)
            plan.write_text(
                json.dumps({"target": "Assets/Test.prefab", "ops": []}),
                encoding="utf-8",
            )
            smoke_script.write_text(
                """
import argparse
import json
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("--plan", required=True)
parser.add_argument("--bridge-script", required=True)
parser.add_argument("--python", required=True)
parser.add_argument("--unity-project-path", required=True)
parser.add_argument("--unity-execute-method", required=True)
parser.add_argument("--unity-log-file", required=True)
parser.add_argument("--out", required=True)
parser.add_argument("--unity-command", default=None)
parser.add_argument("--unity-timeout-sec", default=None)
parser.add_argument("--expect-failure", action="store_true")
args = parser.parse_args()

payload = {
    "success": True,
    "severity": "info",
    "code": "OK",
    "message": "ok",
    "data": {"plan": args.plan},
    "diagnostics": [],
}
Path(args.out).write_text(json.dumps(payload), encoding="utf-8")
print(json.dumps(payload))
raise SystemExit(0)
""".strip(),
                encoding="utf-8",
            )

            with redirect_stdout(StringIO()):
                exit_code = main(
                    [
                        "--targets",
                        "avatar",
                        "--avatar-plan",
                        str(plan),
                        "--avatar-project-path",
                        str(project),
                        "--smoke-script",
                        str(smoke_script),
                        "--python",
                        sys.executable,
                        "--bridge-script",
                        "tools/unity_patch_bridge.py",
                        "--out-dir",
                        str(out_dir),
                    ]
                )

            summary = json.loads((out_dir / "summary.json").read_text(encoding="utf-8"))
            response = json.loads(
                (out_dir / "avatar" / "response.json").read_text(encoding="utf-8")
            )

        self.assertEqual(0, exit_code)
        self.assertTrue(summary["success"])
        self.assertEqual(1, summary["data"]["total_cases"])
        self.assertEqual(1, summary["data"]["cases"][0]["attempts"])
        self.assertEqual("OK", response["code"])

    def test_main_returns_nonzero_on_failed_case(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            plan = root / "avatar_plan.json"
            project = root / "avatar_project"
            smoke_script = root / "fake_smoke_fail.py"
            out_dir = root / "reports"
            project.mkdir(parents=True, exist_ok=True)
            plan.write_text(
                json.dumps({"target": "Assets/Test.prefab", "ops": []}),
                encoding="utf-8",
            )
            smoke_script.write_text(
                """
import argparse
import json

parser = argparse.ArgumentParser()
parser.add_argument("--out", required=True)
_ = parser.parse_known_args()
print(json.dumps({"success": False, "severity": "error", "code": "SMOKE_BRIDGE_ERROR", "message": "failed", "data": {}, "diagnostics": []}))
raise SystemExit(1)
""".strip(),
                encoding="utf-8",
            )

            with redirect_stdout(StringIO()):
                exit_code = main(
                    [
                        "--targets",
                        "avatar",
                        "--avatar-plan",
                        str(plan),
                        "--avatar-project-path",
                        str(project),
                        "--smoke-script",
                        str(smoke_script),
                        "--python",
                        sys.executable,
                        "--bridge-script",
                        "tools/unity_patch_bridge.py",
                        "--out-dir",
                        str(out_dir),
                    ]
                )

            summary = json.loads((out_dir / "summary.json").read_text(encoding="utf-8"))

        self.assertEqual(1, exit_code)
        self.assertFalse(summary["success"])
        self.assertEqual(1, summary["data"]["failed_cases"])
        self.assertEqual(1, summary["data"]["cases"][0]["attempts"])

    def test_main_retries_and_recovers_after_transient_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            plan = root / "avatar_plan.json"
            project = root / "avatar_project"
            smoke_script = root / "fake_smoke_retry.py"
            out_dir = root / "reports"
            project.mkdir(parents=True, exist_ok=True)
            plan.write_text(
                json.dumps({"target": "Assets/Test.prefab", "ops": []}),
                encoding="utf-8",
            )
            smoke_script.write_text(
                """
import argparse
import json
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("--out", required=True)
args, _ = parser.parse_known_args()
marker = Path(args.out).with_suffix(".marker")
if not marker.exists():
    marker.write_text("attempt-1", encoding="utf-8")
    print(json.dumps({"success": False, "severity": "error", "code": "TRANSIENT", "message": "retry", "data": {}, "diagnostics": []}))
    raise SystemExit(1)
print(json.dumps({"success": True, "severity": "info", "code": "OK", "message": "ok", "data": {}, "diagnostics": []}))
raise SystemExit(0)
""".strip(),
                encoding="utf-8",
            )

            with redirect_stdout(StringIO()):
                exit_code = main(
                    [
                        "--targets",
                        "avatar",
                        "--avatar-plan",
                        str(plan),
                        "--avatar-project-path",
                        str(project),
                        "--smoke-script",
                        str(smoke_script),
                        "--python",
                        sys.executable,
                        "--bridge-script",
                        "tools/unity_patch_bridge.py",
                        "--out-dir",
                        str(out_dir),
                        "--max-retries",
                        "1",
                        "--retry-delay-sec",
                        "0",
                    ]
                )

            summary = json.loads((out_dir / "summary.json").read_text(encoding="utf-8"))

        self.assertEqual(0, exit_code)
        self.assertTrue(summary["success"])
        self.assertEqual(2, summary["data"]["cases"][0]["attempts"])


if __name__ == "__main__":
    unittest.main()
