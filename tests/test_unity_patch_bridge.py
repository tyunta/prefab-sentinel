from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


class UnityPatchBridgeTests(unittest.TestCase):
    def _bridge_path(self) -> Path:
        return Path("tools") / "unity_patch_bridge.py"

    def _run_bridge(
        self,
        payload: dict[str, object],
        *,
        env_overrides: dict[str, str] | None = None,
    ) -> dict[str, object]:
        env = os.environ.copy()
        env.pop("UNITYTOOL_UNITY_COMMAND", None)
        env.pop("UNITYTOOL_UNITY_PROJECT_PATH", None)
        env.pop("UNITYTOOL_UNITY_EXECUTE_METHOD", None)
        env.pop("UNITYTOOL_UNITY_TIMEOUT_SEC", None)
        env.pop("UNITYTOOL_UNITY_LOG_FILE", None)
        if env_overrides:
            env.update(env_overrides)
        completed = subprocess.run(
            [sys.executable, str(self._bridge_path())],
            input=json.dumps(payload),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
            check=False,
        )
        self.assertEqual(0, completed.returncode, msg=completed.stderr)
        return json.loads(completed.stdout)

    def test_reference_bridge_requires_unity_command(self) -> None:
        result = self._run_bridge(
            {
                "protocol_version": 1,
                "target": "Assets/Test.prefab",
                "ops": [],
            }
        )
        self.assertFalse(result["success"])
        self.assertEqual("BRIDGE_UNITY_COMMAND_MISSING", result["code"])

    def test_reference_bridge_rejects_protocol_mismatch(self) -> None:
        result = self._run_bridge(
            {
                "protocol_version": 999,
                "target": "Assets/Test.prefab",
                "ops": [],
            }
        )
        self.assertFalse(result["success"])
        self.assertEqual("BRIDGE_PROTOCOL_VERSION", result["code"])

    def test_reference_bridge_runs_unity_command_and_returns_payload(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            unity_runner = root / "fake_unity.py"
            unity_runner.write_text(
                """
import json
import sys
from pathlib import Path

def _arg(flag: str) -> str:
    args = sys.argv[1:]
    idx = args.index(flag)
    return args[idx + 1]

request_path = Path(_arg("-unitytoolPatchRequest"))
response_path = Path(_arg("-unitytoolPatchResponse"))
request = json.loads(request_path.read_text(encoding="utf-8"))
response_path.write_text(
    json.dumps(
        {
            "protocol_version": 1,
            "success": True,
            "severity": "info",
            "code": "SER_APPLY_OK",
            "message": "Applied by fake Unity runner.",
            "data": {"applied": len(request.get("ops", []))},
            "diagnostics": [],
        }
    ),
    encoding="utf-8",
)
""".strip(),
                encoding="utf-8",
            )

            result = self._run_bridge(
                {
                    "protocol_version": 1,
                    "target": "Assets/Test.prefab",
                    "ops": [{"op": "set"}],
                },
                env_overrides={
                    "UNITYTOOL_UNITY_COMMAND": f'"{sys.executable}" "{unity_runner}"',
                    "UNITYTOOL_UNITY_PROJECT_PATH": str(root),
                },
            )

        self.assertTrue(result["success"])
        self.assertEqual("SER_APPLY_OK", result["code"])
        self.assertEqual(1, result["protocol_version"])
        self.assertEqual(1, result["data"]["applied"])
        self.assertEqual("Assets/Test.prefab", result["data"]["target"])
        self.assertEqual(1, result["data"]["op_count"])
        self.assertTrue(result["data"]["executed"])

    def test_reference_bridge_normalizes_op_values_for_unity_request(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            unity_runner = root / "fake_unity_capture.py"
            unity_runner.write_text(
                """
import json
import sys
from pathlib import Path

def _arg(flag: str) -> str:
    args = sys.argv[1:]
    idx = args.index(flag)
    return args[idx + 1]

request_path = Path(_arg("-unitytoolPatchRequest"))
response_path = Path(_arg("-unitytoolPatchResponse"))
request = json.loads(request_path.read_text(encoding="utf-8"))
response_path.write_text(
    json.dumps(
        {
            "protocol_version": 1,
            "success": True,
            "severity": "info",
            "code": "SER_APPLY_OK",
            "message": "Captured request payload.",
            "data": {
                "applied": len(request.get("ops", [])),
                "request_ops": request.get("ops", []),
            },
            "diagnostics": [],
        }
    ),
    encoding="utf-8",
)
""".strip(),
                encoding="utf-8",
            )

            result = self._run_bridge(
                {
                    "protocol_version": 1,
                    "target": "Assets/Test.prefab",
                    "ops": [
                        {
                            "op": "set",
                            "component": "Example.Component",
                            "path": "items.Array.size",
                            "value": 2,
                        },
                        {
                            "op": "insert_array_element",
                            "component": "Example.Component",
                            "path": "items.Array.data",
                            "index": 0,
                            "value": {"name": "x"},
                        },
                        {
                            "op": "set",
                            "component": "Example.Component",
                            "path": "enabled",
                            "value": True,
                        },
                        {
                            "op": "set",
                            "component": "Example.Component",
                            "path": "weight",
                            "value": 1.5,
                        },
                        {
                            "op": "set",
                            "component": "Example.Component",
                            "path": "label",
                            "value": "hello",
                        },
                        {
                            "op": "set",
                            "component": "Example.Component",
                            "path": "optionalRef",
                            "value": None,
                        },
                        {
                            "op": "insert_array_element",
                            "component": "Example.Component",
                            "path": "items.Array.data",
                            "index": 1,
                            "value": [1, 2],
                        },
                        {
                            "op": "set",
                            "component": "Example.Component",
                            "path": "optionalRef",
                            "value": {
                                "guid": "0123456789abcdef0123456789abcdef",
                                "file_id": 11400000,
                            },
                        },
                        {
                            "op": "set",
                            "component": "Example.Component",
                            "path": "clipRect",
                            "value": {
                                "x": 1.0,
                                "y": 2.0,
                                "width": 3.0,
                                "height": 4.0,
                            },
                        },
                        {
                            "op": "set",
                            "component": "Example.Component",
                            "path": "bounds",
                            "value": {
                                "center": {"x": 1.0, "y": 2.0, "z": 3.0},
                                "size": {"x": 4.0, "y": 5.0, "z": 6.0},
                            },
                        },
                        {
                            "op": "set",
                            "component": "Example.Component",
                            "path": "clipRectInt",
                            "value": {
                                "x": 1,
                                "y": 2,
                                "width": 3,
                                "height": 4,
                            },
                        },
                        {
                            "op": "set",
                            "component": "Example.Component",
                            "path": "boundsInt",
                            "value": {
                                "position": {"x": 1, "y": 2, "z": 3},
                                "size": {"x": 4, "y": 5, "z": 6},
                            },
                        },
                        {
                            "op": "remove_array_element",
                            "component": "Example.Component",
                            "path": "items.Array.data",
                            "index": 0,
                        },
                    ],
                },
                env_overrides={
                    "UNITYTOOL_UNITY_COMMAND": f'"{sys.executable}" "{unity_runner}"',
                    "UNITYTOOL_UNITY_PROJECT_PATH": str(root),
                },
            )

        self.assertTrue(result["success"])
        request_ops = result["data"]["request_ops"]
        self.assertEqual("int", request_ops[0]["value_kind"])
        self.assertEqual(2, request_ops[0]["value_int"])

        self.assertEqual("json", request_ops[1]["value_kind"])
        self.assertEqual('{"name": "x"}', request_ops[1]["value_json"])

        self.assertEqual("bool", request_ops[2]["value_kind"])
        self.assertTrue(request_ops[2]["value_bool"])

        self.assertEqual("float", request_ops[3]["value_kind"])
        self.assertEqual(1.5, request_ops[3]["value_float"])

        self.assertEqual("string", request_ops[4]["value_kind"])
        self.assertEqual("hello", request_ops[4]["value_string"])

        self.assertEqual("null", request_ops[5]["value_kind"])
        self.assertNotIn("value_json", request_ops[5])

        self.assertEqual("json", request_ops[6]["value_kind"])
        self.assertEqual("[1, 2]", request_ops[6]["value_json"])

        self.assertEqual("json", request_ops[7]["value_kind"])
        self.assertEqual(
            '{"guid": "0123456789abcdef0123456789abcdef", "file_id": 11400000}',
            request_ops[7]["value_json"],
        )

        self.assertEqual("json", request_ops[8]["value_kind"])
        self.assertEqual(
            '{"x": 1.0, "y": 2.0, "width": 3.0, "height": 4.0}',
            request_ops[8]["value_json"],
        )

        self.assertEqual("json", request_ops[9]["value_kind"])
        self.assertEqual(
            '{"center": {"x": 1.0, "y": 2.0, "z": 3.0}, "size": {"x": 4.0, "y": 5.0, "z": 6.0}}',
            request_ops[9]["value_json"],
        )

        self.assertEqual("json", request_ops[10]["value_kind"])
        self.assertEqual(
            '{"x": 1, "y": 2, "width": 3, "height": 4}',
            request_ops[10]["value_json"],
        )

        self.assertEqual("json", request_ops[11]["value_kind"])
        self.assertEqual(
            '{"position": {"x": 1, "y": 2, "z": 3}, "size": {"x": 4, "y": 5, "z": 6}}',
            request_ops[11]["value_json"],
        )

        self.assertEqual("remove_array_element", request_ops[12]["op"])
        self.assertNotIn("value_kind", request_ops[12])

    def test_reference_bridge_surfaces_nonzero_unity_exit(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            unity_runner = root / "fake_unity_fail.py"
            unity_runner.write_text(
                """
import sys
sys.stderr.write("fake unity failed")
raise SystemExit(9)
""".strip(),
                encoding="utf-8",
            )

            result = self._run_bridge(
                {
                    "protocol_version": 1,
                    "target": "Assets/Test.prefab",
                    "ops": [],
                },
                env_overrides={
                    "UNITYTOOL_UNITY_COMMAND": f'"{sys.executable}" "{unity_runner}"',
                    "UNITYTOOL_UNITY_PROJECT_PATH": str(root),
                },
            )

        self.assertFalse(result["success"])
        self.assertEqual("BRIDGE_UNITY_FAILED", result["code"])


if __name__ == "__main__":
    unittest.main()
