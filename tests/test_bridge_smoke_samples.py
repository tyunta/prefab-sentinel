from __future__ import annotations

import json
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
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
                            "unity_timeout_sec": 450,
                            "exit_code": 1,
                            "response_code": "SMOKE_BRIDGE_ERROR",
                            "response_path": "reports/bridge_smoke/avatar/response.json",
                            "unity_log_file": "reports/bridge_smoke/avatar/unity.log",
                        }
                    ],
                },
            }
        )
        self.assertIn("| avatar | False | 2 | 450 | 1 | SMOKE_BRIDGE_ERROR |", markdown)

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
        self.assertIsNone(summary["data"]["cases"][0]["unity_timeout_sec"])
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
        self.assertIsNone(summary["data"]["cases"][0]["unity_timeout_sec"])

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


    def test_main_applies_per_target_unity_timeout_overrides(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            avatar_plan = root / "avatar_plan.json"
            world_plan = root / "world_plan.json"
            avatar_project = root / "avatar_project"
            world_project = root / "world_project"
            smoke_script = root / "fake_smoke_timeout.py"
            out_dir = root / "reports"
            avatar_project.mkdir(parents=True, exist_ok=True)
            world_project.mkdir(parents=True, exist_ok=True)
            avatar_plan.write_text(
                json.dumps({"target": "Assets/Avatar.prefab", "ops": []}),
                encoding="utf-8",
            )
            world_plan.write_text(
                json.dumps({"target": "Assets/World.prefab", "ops": []}),
                encoding="utf-8",
            )
            smoke_script.write_text(
                """
import argparse
import json
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("--out", required=True)
parser.add_argument("--unity-timeout-sec", type=int, default=None)
args, _ = parser.parse_known_args()
payload = {
    "success": True,
    "severity": "info",
    "code": "OK",
    "message": "ok",
    "data": {"unity_timeout_sec": args.unity_timeout_sec},
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
                        "all",
                        "--avatar-plan",
                        str(avatar_plan),
                        "--world-plan",
                        str(world_plan),
                        "--avatar-project-path",
                        str(avatar_project),
                        "--world-project-path",
                        str(world_project),
                        "--smoke-script",
                        str(smoke_script),
                        "--python",
                        sys.executable,
                        "--bridge-script",
                        "tools/unity_patch_bridge.py",
                        "--unity-timeout-sec",
                        "600",
                        "--avatar-unity-timeout-sec",
                        "900",
                        "--world-unity-timeout-sec",
                        "450",
                        "--out-dir",
                        str(out_dir),
                    ]
                )

            summary = json.loads((out_dir / "summary.json").read_text(encoding="utf-8"))
            avatar_response = json.loads(
                (out_dir / "avatar" / "response.json").read_text(encoding="utf-8")
            )
            world_response = json.loads(
                (out_dir / "world" / "response.json").read_text(encoding="utf-8")
            )

        self.assertEqual(0, exit_code)
        self.assertTrue(summary["success"])
        by_name = {item["name"]: item for item in summary["data"]["cases"]}
        self.assertEqual(900, by_name["avatar"]["unity_timeout_sec"])
        self.assertEqual(450, by_name["world"]["unity_timeout_sec"])
        self.assertEqual(900, avatar_response["data"]["unity_timeout_sec"])
        self.assertEqual(450, world_response["data"]["unity_timeout_sec"])

    def test_main_rejects_non_positive_timeout_argument(self) -> None:
        with redirect_stderr(StringIO()):
            with self.assertRaises(SystemExit) as raised:
                main(["--unity-timeout-sec", "0"])

        self.assertEqual(2, raised.exception.code)


if __name__ == "__main__":
    unittest.main()
