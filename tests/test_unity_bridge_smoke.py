from __future__ import annotations

import argparse
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
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
    _resolve_expected_applied,
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

    def test_resolve_expected_applied(self) -> None:
        plan = {"target": "Assets/Test.prefab", "ops": [{"op": "set"}, {"op": "set"}]}
        self.assertEqual(
            (3, "cli"),
            _resolve_expected_applied(
                plan=plan,
                expected_applied=3,
                expect_applied_from_plan=True,
                expect_failure=False,
            ),
        )
        self.assertEqual(
            (2, "plan_ops"),
            _resolve_expected_applied(
                plan=plan,
                expected_applied=None,
                expect_applied_from_plan=True,
                expect_failure=False,
            ),
        )
        self.assertEqual(
            (None, "skipped_expect_failure"),
            _resolve_expected_applied(
                plan=plan,
                expected_applied=None,
                expect_applied_from_plan=True,
                expect_failure=True,
            ),
        )

    def test_build_bridge_env_applies_overrides(self) -> None:
        args = argparse.Namespace(
            unity_command="C:/Unity/Editor/Unity.exe",
            unity_project_path="D:/git/prefab-sentinel/sample/avatar",
            unity_execute_method="PrefabSentinel.UnityPatchBridge.ApplyFromJson",
            unity_timeout_sec=300,
            unity_log_file="D:/tmp/unity.log",
        )
        env = _build_bridge_env(args)
        self.assertEqual("C:/Unity/Editor/Unity.exe", env[UNITY_COMMAND_ENV])
        self.assertEqual("D:/git/prefab-sentinel/sample/avatar", env[UNITY_PROJECT_PATH_ENV])
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
        code_response = {"success": True, "code": "BRIDGE_OK", "data": {}}
        self.assertTrue(
            _validate_expectation(
                code_response,
                expect_failure=False,
                expected_code="BRIDGE_OK",
            )
        )
        self.assertEqual("BRIDGE_OK", code_response["data"]["expected_code"])
        self.assertEqual("BRIDGE_OK", code_response["data"]["actual_code"])
        self.assertTrue(code_response["data"]["code_matches"])
        code_mismatch_response = {"success": True, "code": "BRIDGE_OK", "data": {}}
        self.assertFalse(
            _validate_expectation(
                code_mismatch_response,
                expect_failure=False,
                expected_code="BRIDGE_FAIL",
            )
        )
        self.assertEqual("BRIDGE_FAIL", code_mismatch_response["data"]["expected_code"])
        self.assertEqual("BRIDGE_OK", code_mismatch_response["data"]["actual_code"])
        self.assertFalse(code_mismatch_response["data"]["code_matches"])
        response = {"success": True, "data": {"applied": 2}}
        self.assertTrue(
            _validate_expectation(
                response,
                expect_failure=False,
                expected_applied=2,
            )
        )
        self.assertEqual(2, response["data"]["expected_applied"])
        self.assertEqual("cli", response["data"]["expected_applied_source"])
        self.assertEqual(2, response["data"]["actual_applied"])
        self.assertTrue(response["data"]["applied_matches"])
        mismatch_response = {"success": True, "data": {"applied": 1}}
        self.assertFalse(
            _validate_expectation(
                mismatch_response,
                expect_failure=False,
                expected_applied=2,
            )
        )
        self.assertEqual(2, mismatch_response["data"]["expected_applied"])
        self.assertEqual("cli", mismatch_response["data"]["expected_applied_source"])
        self.assertEqual(1, mismatch_response["data"]["actual_applied"])
        self.assertFalse(mismatch_response["data"]["applied_matches"])

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

    def test_main_returns_nonzero_when_expected_applied_mismatch(self) -> None:
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
sys.stdout.write(json.dumps({"success": True, "severity": "info", "code": "OK", "message": "ok", "data": {"applied": 1}, "diagnostics": []}))
""".strip(),
                encoding="utf-8",
            )
            output = io.StringIO()
            with redirect_stdout(output):
                exit_code = main(
                    [
                        "--plan",
                        str(plan),
                        "--bridge-script",
                        str(bridge),
                        "--python",
                        sys.executable,
                        "--expected-applied",
                        "2",
                    ]
                )
        payload = json.loads(output.getvalue())
        self.assertEqual(1, exit_code)
        self.assertTrue(payload["success"])
        self.assertEqual(2, payload["data"]["expected_applied"])
        self.assertEqual("cli", payload["data"]["expected_applied_source"])
        self.assertEqual(1, payload["data"]["actual_applied"])
        self.assertFalse(payload["data"]["applied_matches"])

    def test_main_returns_nonzero_when_expected_code_mismatch(self) -> None:
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
sys.stdout.write(json.dumps({"success": True, "severity": "info", "code": "BRIDGE_OK", "message": "ok", "data": {}, "diagnostics": []}))
""".strip(),
                encoding="utf-8",
            )
            output = io.StringIO()
            with redirect_stdout(output):
                exit_code = main(
                    [
                        "--plan",
                        str(plan),
                        "--bridge-script",
                        str(bridge),
                        "--python",
                        sys.executable,
                        "--expected-code",
                        "BRIDGE_FAIL",
                    ]
                )
        payload = json.loads(output.getvalue())
        self.assertEqual(1, exit_code)
        self.assertEqual("BRIDGE_FAIL", payload["data"]["expected_code"])
        self.assertEqual("BRIDGE_OK", payload["data"]["actual_code"])
        self.assertFalse(payload["data"]["code_matches"])

    def test_main_applies_expected_applied_from_plan(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            plan = root / "plan.json"
            bridge = root / "fake_bridge.py"
            plan.write_text(
                json.dumps(
                    {
                        "target": "Assets/Test.prefab",
                        "ops": [{"op": "set"}, {"op": "set"}],
                    }
                ),
                encoding="utf-8",
            )
            bridge.write_text(
                """
import json
import sys
_ = json.loads(sys.stdin.read())
sys.stdout.write(json.dumps({"success": True, "severity": "info", "code": "OK", "message": "ok", "data": {"applied": 2}, "diagnostics": []}))
""".strip(),
                encoding="utf-8",
            )
            output = io.StringIO()
            with redirect_stdout(output):
                exit_code = main(
                    [
                        "--plan",
                        str(plan),
                        "--bridge-script",
                        str(bridge),
                        "--python",
                        sys.executable,
                        "--expect-applied-from-plan",
                    ]
                )
        payload = json.loads(output.getvalue())
        self.assertEqual(0, exit_code)
        self.assertEqual(2, payload["data"]["expected_applied"])
        self.assertEqual("plan_ops", payload["data"]["expected_applied_source"])
        self.assertEqual(2, payload["data"]["actual_applied"])
        self.assertTrue(payload["data"]["applied_matches"])

    def test_main_expect_applied_from_plan_skips_expect_failure_case(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            plan = root / "plan.json"
            bridge = root / "fake_bridge.py"
            plan.write_text(
                json.dumps(
                    {
                        "target": "Assets/Test.prefab",
                        "ops": [{"op": "set"}, {"op": "set"}],
                    }
                ),
                encoding="utf-8",
            )
            bridge.write_text(
                """
import json
import sys
_ = json.loads(sys.stdin.read())
sys.stdout.write(json.dumps({"success": False, "severity": "error", "code": "FAIL", "message": "failed", "data": {}, "diagnostics": []}))
""".strip(),
                encoding="utf-8",
            )
            output = io.StringIO()
            with redirect_stdout(output):
                exit_code = main(
                    [
                        "--plan",
                        str(plan),
                        "--bridge-script",
                        str(bridge),
                        "--python",
                        sys.executable,
                        "--expect-failure",
                        "--expect-applied-from-plan",
                    ]
                )
        payload = json.loads(output.getvalue())
        self.assertEqual(0, exit_code)
        self.assertNotIn("expected_applied", payload["data"])

    def test_main_rejects_negative_expected_applied_argument(self) -> None:
        with redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                main(["--plan", "ignored.json", "--expected-applied", "-1"])

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
