from __future__ import annotations

import argparse
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from scripts.unity_bridge_smoke import (
    UNITY_COMMAND_ENV,
    UNITY_EXECUTE_METHOD_ENV,
    UNITY_LOG_FILE_ENV,
    UNITY_PROJECT_PATH_ENV,
    UNITY_TIMEOUT_SEC_ENV,
    _build_bridge_env,
    _build_bridge_request,
    _load_patch_plan,
    _run_bridge,
    _validate_expectation,
    main,
)


class UnityBridgeSmokeTests(unittest.TestCase):
    def test_load_patch_plan_validates_schema(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "plan.json"
            path.write_text(
                json.dumps(
                    {
                        "target": "Assets/Test.prefab",
                        "ops": [],
                    }
                ),
                encoding="utf-8",
            )
            loaded = _load_patch_plan(path)
        self.assertEqual("Assets/Test.prefab", loaded["target"])
        self.assertEqual([], loaded["ops"])

    def test_build_bridge_request_maps_fields(self) -> None:
        request = _build_bridge_request(
            {
                "target": "Assets/Test.prefab",
                "ops": [{"op": "set"}],
            }
        )
        self.assertEqual(1, request["protocol_version"])
        self.assertEqual("Assets/Test.prefab", request["target"])
        self.assertEqual([{"op": "set"}], request["ops"])

    def test_build_bridge_env_applies_overrides(self) -> None:
        args = argparse.Namespace(
            unity_command="C:/Unity/Editor/Unity.exe",
            unity_project_path="D:/git/UnityTool/sample/avatar",
            unity_execute_method="PrefabSentinel.UnityPatchBridge.ApplyFromJson",
            unity_timeout_sec=300,
            unity_log_file="D:/tmp/unity.log",
        )
        env = _build_bridge_env(args)
        self.assertEqual("C:/Unity/Editor/Unity.exe", env[UNITY_COMMAND_ENV])
        self.assertEqual("D:/git/UnityTool/sample/avatar", env[UNITY_PROJECT_PATH_ENV])
        self.assertEqual(
            "PrefabSentinel.UnityPatchBridge.ApplyFromJson",
            env[UNITY_EXECUTE_METHOD_ENV],
        )
        self.assertEqual("300", env[UNITY_TIMEOUT_SEC_ENV])
        self.assertEqual("D:/tmp/unity.log", env[UNITY_LOG_FILE_ENV])

    def test_run_bridge_parses_json_output(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            bridge = root / "fake_bridge.py"
            bridge.write_text(
                """
import json
import sys

payload = json.loads(sys.stdin.read())
sys.stdout.write(
    json.dumps(
        {
            "success": True,
            "severity": "info",
            "code": "OK",
            "message": "ok",
            "data": {"target": payload.get("target")},
            "diagnostics": [],
        }
    )
)
""".strip(),
                encoding="utf-8",
            )
            response = _run_bridge(
                bridge_script=bridge,
                python_executable=sys.executable,
                request={"protocol_version": 1, "target": "Assets/Test.prefab", "ops": []},
                env=os.environ.copy(),
            )

        self.assertTrue(response["success"])
        self.assertEqual("Assets/Test.prefab", response["data"]["target"])

    def test_run_bridge_rejects_missing_required_fields(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            bridge = root / "fake_bridge.py"
            bridge.write_text(
                """
import json
import sys
_ = json.loads(sys.stdin.read())
sys.stdout.write(json.dumps({"success": True, "severity": "info", "code": "OK", "message": "ok", "data": {}}))
""".strip(),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(RuntimeError, "missing required fields"):
                _run_bridge(
                    bridge_script=bridge,
                    python_executable=sys.executable,
                    request={"protocol_version": 1, "target": "Assets/Test.prefab", "ops": []},
                    env=os.environ.copy(),
                )

    def test_run_bridge_rejects_invalid_severity(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            bridge = root / "fake_bridge.py"
            bridge.write_text(
                """
import json
import sys
_ = json.loads(sys.stdin.read())
sys.stdout.write(json.dumps({"success": True, "severity": "notice", "code": "OK", "message": "ok", "data": {}, "diagnostics": []}))
""".strip(),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(RuntimeError, "field 'severity'"):
                _run_bridge(
                    bridge_script=bridge,
                    python_executable=sys.executable,
                    request={"protocol_version": 1, "target": "Assets/Test.prefab", "ops": []},
                    env=os.environ.copy(),
                )

    def test_validate_expectation(self) -> None:
        self.assertTrue(_validate_expectation({"success": True}, expect_failure=False))
        self.assertFalse(_validate_expectation({"success": False}, expect_failure=False))
        self.assertTrue(_validate_expectation({"success": False}, expect_failure=True))
        self.assertFalse(_validate_expectation({"success": True}, expect_failure=True))

    def test_main_returns_nonzero_when_expectation_is_not_met(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            plan = root / "plan.json"
            bridge = root / "fake_bridge.py"
            plan.write_text(
                json.dumps({"target": "Assets/Test.prefab", "ops": []}),
                encoding="utf-8",
            )
            bridge.write_text(
                """
import json
import sys
_ = json.loads(sys.stdin.read())
sys.stdout.write(json.dumps({"success": True, "severity": "info", "code": "OK", "message": "ok", "data": {}, "diagnostics": []}))
""".strip(),
                encoding="utf-8",
            )
            with redirect_stdout(io.StringIO()):
                exit_code = main(
                    [
                        "--plan",
                        str(plan),
                        "--bridge-script",
                        str(bridge),
                        "--python",
                        sys.executable,
                        "--expect-failure",
                    ]
                )
        self.assertEqual(1, exit_code)

    def test_script_entrypoint_runs_when_invoked_by_path(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            plan = root / "plan.json"
            bridge = root / "fake_bridge.py"
            plan.write_text(
                json.dumps({"target": "Assets/Test.prefab", "ops": []}),
                encoding="utf-8",
            )
            bridge.write_text(
                """
import json
import sys
_ = json.loads(sys.stdin.read())
sys.stdout.write(json.dumps({"success": False, "severity": "error", "code": "BRIDGE_FAIL", "message": "failed", "data": {}, "diagnostics": []}))
""".strip(),
                encoding="utf-8",
            )
            completed = subprocess.run(
                [
                    sys.executable,
                    str(Path("scripts") / "unity_bridge_smoke.py"),
                    "--plan",
                    str(plan),
                    "--bridge-script",
                    str(bridge),
                    "--python",
                    sys.executable,
                    "--expect-failure",
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
            )
        self.assertEqual(0, completed.returncode, msg=completed.stderr)
        payload = json.loads(completed.stdout)
        self.assertFalse(payload["success"])
        self.assertEqual("BRIDGE_FAIL", payload["code"])


if __name__ == "__main__":
    unittest.main()
