from __future__ import annotations

import hmac
import hashlib
import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from unitytool import cli

MISSING_GUID = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


class CliTests(unittest.TestCase):
    def run_cli(self, argv: list[str]) -> tuple[int, str]:
        buf = io.StringIO()
        with redirect_stdout(buf):
            exit_code = cli.main(argv)
        return exit_code, buf.getvalue()

    def test_inspect_variant_fail_fast_on_missing_variant(self) -> None:
        exit_code, output = self.run_cli(
            ["inspect", "variant", "--path", "Assets/Test Variant.prefab"]
        )
        payload = json.loads(output)
        self.assertEqual(0, exit_code)
        self.assertEqual("INSPECT_VARIANT_RESULT", payload["code"])
        self.assertEqual("error", payload["severity"])
        self.assertTrue(payload["data"]["read_only"])
        self.assertTrue(payload["data"]["fail_fast_triggered"])
        self.assertEqual("PVR404", payload["data"]["steps"][0]["result"]["code"])

    def test_validate_refs_returns_missing_scope_error(self) -> None:
        exit_code, output = self.run_cli(
            ["validate", "refs", "--scope", "Assets/haiirokoubou"]
        )
        payload = json.loads(output)
        self.assertEqual(0, exit_code)
        self.assertEqual("VALIDATE_REFS_RESULT", payload["code"])
        self.assertEqual("error", payload["severity"])
        self.assertTrue(payload["data"]["read_only"])
        self.assertEqual("REF404", payload["data"]["steps"][0]["result"]["code"])
        self.assertEqual([], payload["diagnostics"])

    def test_validate_runtime_returns_missing_scene_error(self) -> None:
        exit_code, output = self.run_cli(
            ["validate", "runtime", "--scene", "Assets/Scenes/Missing.unity"]
        )
        payload = json.loads(output)
        self.assertEqual(0, exit_code)
        self.assertEqual("VALIDATE_RUNTIME_RESULT", payload["code"])
        self.assertEqual("error", payload["severity"])
        self.assertTrue(payload["data"]["fail_fast_triggered"])
        self.assertEqual("RUN002", payload["data"]["steps"][1]["result"]["code"])

    def test_validate_runtime_classifies_log_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            scene = root / "Smoke.unity"
            log = root / "Editor.log"
            _write(
                scene,
                """%YAML 1.1
--- !u!1 &1
GameObject:
  m_Name: Smoke
""",
            )
            _write(log, "NullReferenceException in UdonBehaviour\n")

            exit_code, output = self.run_cli(
                [
                    "validate",
                    "runtime",
                    "--scene",
                    str(scene),
                    "--log-file",
                    str(log),
                ]
            )

        payload = json.loads(output)
        self.assertEqual(0, exit_code)
        self.assertEqual("VALIDATE_RUNTIME_RESULT", payload["code"])
        self.assertEqual("critical", payload["severity"])
        step_codes = [step["result"]["code"] for step in payload["data"]["steps"]]
        self.assertIn("RUN_LOG_COLLECTED", step_codes)
        self.assertIn("RUN001", step_codes)

    def test_validate_bridge_smoke_runs_bridge_script(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            plan = root / "plan.json"
            bridge = root / "fake_bridge.py"
            plan.write_text(
                json.dumps({"target": "Assets/Test.prefab", "ops": [{"op": "set"}]}),
                encoding="utf-8",
            )
            bridge.write_text(
                """
import json
import sys
request = json.loads(sys.stdin.read())
sys.stdout.write(
    json.dumps(
        {
            "success": True,
            "severity": "info",
            "code": "BRIDGE_OK",
            "message": "ok",
            "data": {
                "target": request.get("target"),
                "op_count": len(request.get("ops", [])),
            },
            "diagnostics": [],
        }
    )
)
""".strip(),
                encoding="utf-8",
            )
            exit_code, output = self.run_cli(
                [
                    "validate",
                    "bridge-smoke",
                    "--plan",
                    str(plan),
                    "--bridge-script",
                    str(bridge),
                    "--python",
                    sys.executable,
                ]
            )

        payload = json.loads(output)
        self.assertEqual(0, exit_code)
        self.assertTrue(payload["success"])
        self.assertEqual("BRIDGE_OK", payload["code"])
        self.assertEqual("Assets/Test.prefab", payload["data"]["target"])
        self.assertEqual(1, payload["data"]["op_count"])

    def test_validate_bridge_smoke_expect_failure_returns_nonzero_on_success(self) -> None:
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
            exit_code, output = self.run_cli(
                [
                    "validate",
                    "bridge-smoke",
                    "--plan",
                    str(plan),
                    "--bridge-script",
                    str(bridge),
                    "--python",
                    sys.executable,
                    "--expect-failure",
                ]
            )

        payload = json.loads(output)
        self.assertEqual(1, exit_code)
        self.assertTrue(payload["success"])
        self.assertEqual("BRIDGE_OK", payload["code"])

    def test_patch_apply_dry_run_returns_preview(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            plan = root / "patch.json"
            plan.write_text(
                json.dumps(
                    {
                        "target": "Assets/Variant.prefab",
                        "ops": [
                            {
                                "op": "set",
                                "component": "Example.Component",
                                "path": "items.Array.size",
                                "value": 2,
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            exit_code, output = self.run_cli(
                ["patch", "apply", "--plan", str(plan), "--dry-run"]
            )

        payload = json.loads(output)
        self.assertEqual(0, exit_code)
        self.assertEqual("PATCH_APPLY_RESULT", payload["code"])
        self.assertTrue(payload["success"])
        self.assertEqual("SER_DRY_RUN_OK", payload["data"]["steps"][0]["result"]["code"])

    def test_patch_apply_accepts_matching_plan_sha256(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            plan = root / "patch.json"
            plan.write_text(
                json.dumps(
                    {
                        "target": "Assets/Variant.prefab",
                        "ops": [
                            {
                                "op": "set",
                                "component": "Example.Component",
                                "path": "items.Array.size",
                                "value": 2,
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            digest = hashlib.sha256(plan.read_bytes()).hexdigest()
            exit_code, output = self.run_cli(
                [
                    "patch",
                    "apply",
                    "--plan",
                    str(plan),
                    "--dry-run",
                    "--plan-sha256",
                    digest,
                ]
            )

        payload = json.loads(output)
        self.assertEqual(0, exit_code)
        self.assertTrue(payload["success"])
        self.assertEqual(digest, payload["data"]["plan_sha256"])

    def test_patch_apply_accepts_matching_plan_signature(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            plan = root / "patch.json"
            plan.write_text(
                json.dumps(
                    {
                        "target": "Assets/Variant.prefab",
                        "ops": [
                            {
                                "op": "set",
                                "component": "Example.Component",
                                "path": "items.Array.size",
                                "value": 2,
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            key = "local-signing-key"
            signature = hmac.new(key.encode("utf-8"), plan.read_bytes(), hashlib.sha256).hexdigest()
            with patch.dict(os.environ, {"UNITYTOOL_PLAN_SIGNING_KEY": key}, clear=False):
                exit_code, output = self.run_cli(
                    [
                        "patch",
                        "apply",
                        "--plan",
                        str(plan),
                        "--dry-run",
                        "--plan-signature",
                        signature,
                    ]
                )

        payload = json.loads(output)
        self.assertEqual(0, exit_code)
        self.assertTrue(payload["success"])
        self.assertEqual(signature, payload["data"]["plan_signature"])

    def test_patch_apply_accepts_sha256_from_attestation_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            plan = root / "patch.json"
            attestation = root / "attestation.json"
            plan.write_text(
                json.dumps(
                    {
                        "target": "Assets/Variant.prefab",
                        "ops": [
                            {
                                "op": "set",
                                "component": "Example.Component",
                                "path": "items.Array.size",
                                "value": 2,
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            digest = hashlib.sha256(plan.read_bytes()).hexdigest()
            attestation.write_text(
                json.dumps({"data": {"sha256": digest}}),
                encoding="utf-8",
            )
            exit_code, output = self.run_cli(
                [
                    "patch",
                    "apply",
                    "--plan",
                    str(plan),
                    "--dry-run",
                    "--attestation-file",
                    str(attestation),
                ]
            )

        payload = json.loads(output)
        self.assertEqual(0, exit_code)
        self.assertTrue(payload["success"])
        self.assertEqual(digest, payload["data"]["plan_sha256"])
        self.assertEqual(str(attestation), payload["data"]["plan_attestation_file"])

    def test_patch_apply_accepts_signature_from_attestation_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            plan = root / "patch.json"
            attestation = root / "attestation.json"
            plan.write_text(
                json.dumps(
                    {
                        "target": "Assets/Variant.prefab",
                        "ops": [
                            {
                                "op": "set",
                                "component": "Example.Component",
                                "path": "items.Array.size",
                                "value": 2,
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            key = "local-signing-key"
            signature = hmac.new(key.encode("utf-8"), plan.read_bytes(), hashlib.sha256).hexdigest()
            attestation.write_text(
                json.dumps({"data": {"signature": signature}}),
                encoding="utf-8",
            )
            with patch.dict(os.environ, {"UNITYTOOL_PLAN_SIGNING_KEY": key}, clear=False):
                exit_code, output = self.run_cli(
                    [
                        "patch",
                        "apply",
                        "--plan",
                        str(plan),
                        "--dry-run",
                        "--attestation-file",
                        str(attestation),
                    ]
                )

        payload = json.loads(output)
        self.assertEqual(0, exit_code)
        self.assertTrue(payload["success"])
        self.assertEqual(signature, payload["data"]["plan_signature"])
        self.assertEqual(str(attestation), payload["data"]["plan_attestation_file"])

    def test_patch_apply_prefers_cli_expected_values_over_attestation_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            plan = root / "patch.json"
            attestation = root / "attestation.json"
            plan.write_text(
                json.dumps(
                    {
                        "target": "Assets/Variant.prefab",
                        "ops": [
                            {
                                "op": "set",
                                "component": "Example.Component",
                                "path": "items.Array.size",
                                "value": 2,
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            digest = hashlib.sha256(plan.read_bytes()).hexdigest()
            attestation.write_text(
                json.dumps({"data": {"sha256": "0" * 64}}),
                encoding="utf-8",
            )
            exit_code, output = self.run_cli(
                [
                    "patch",
                    "apply",
                    "--plan",
                    str(plan),
                    "--dry-run",
                    "--attestation-file",
                    str(attestation),
                    "--plan-sha256",
                    digest,
                ]
            )

        payload = json.loads(output)
        self.assertEqual(0, exit_code)
        self.assertTrue(payload["success"])
        self.assertEqual(digest, payload["data"]["plan_sha256"])

    def test_patch_apply_rejects_attestation_without_expected_values(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            plan = root / "patch.json"
            attestation = root / "attestation.json"
            plan.write_text(
                json.dumps(
                    {
                        "target": "Assets/Variant.prefab",
                        "ops": [],
                    }
                ),
                encoding="utf-8",
            )
            attestation.write_text(json.dumps({"data": {}}), encoding="utf-8")
            with redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit):
                    self.run_cli(
                        [
                            "patch",
                            "apply",
                            "--plan",
                            str(plan),
                            "--dry-run",
                            "--attestation-file",
                            str(attestation),
                        ]
                    )

    def test_patch_apply_rejects_plan_signature_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            plan = root / "patch.json"
            plan.write_text(
                json.dumps(
                    {
                        "target": "Assets/Variant.prefab",
                        "ops": [],
                    }
                ),
                encoding="utf-8",
            )
            with patch.dict(os.environ, {"UNITYTOOL_PLAN_SIGNING_KEY": "local-signing-key"}, clear=False):
                with redirect_stderr(io.StringIO()):
                    with self.assertRaises(SystemExit):
                        self.run_cli(
                            [
                                "patch",
                                "apply",
                                "--plan",
                                str(plan),
                                "--dry-run",
                                "--plan-signature",
                                "0" * 64,
                            ]
                        )

    def test_patch_apply_writes_out_report_json(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            plan = root / "patch.json"
            out_report = root / "reports" / "patch_result.json"
            plan.write_text(
                json.dumps(
                    {
                        "target": "Assets/Variant.prefab",
                        "ops": [
                            {
                                "op": "set",
                                "component": "Example.Component",
                                "path": "items.Array.size",
                                "value": 2,
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            exit_code, output = self.run_cli(
                [
                    "patch",
                    "apply",
                    "--plan",
                    str(plan),
                    "--dry-run",
                    "--out-report",
                    str(out_report),
                ]
            )

            report_payload = json.loads(out_report.read_text(encoding="utf-8"))

        payload = json.loads(output)
        self.assertEqual(0, exit_code)
        self.assertTrue(payload["success"])
        self.assertEqual("PATCH_APPLY_RESULT", report_payload["code"])
        self.assertEqual(payload["data"]["target"], report_payload["data"]["target"])

    def test_patch_apply_fails_when_out_report_is_unwritable(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            plan = root / "patch.json"
            out_report_dir = root / "reports"
            out_report_dir.mkdir(parents=True, exist_ok=True)
            plan.write_text(
                json.dumps(
                    {
                        "target": "Assets/Variant.prefab",
                        "ops": [],
                    }
                ),
                encoding="utf-8",
            )
            with redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit):
                    self.run_cli(
                        [
                            "patch",
                            "apply",
                            "--plan",
                            str(plan),
                            "--dry-run",
                            "--out-report",
                            str(out_report_dir),
                        ]
                    )

    def test_patch_apply_rejects_plan_sha256_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            plan = root / "patch.json"
            plan.write_text(
                json.dumps(
                    {
                        "target": "Assets/Variant.prefab",
                        "ops": [
                            {
                                "op": "set",
                                "component": "Example.Component",
                                "path": "items.Array.size",
                                "value": 2,
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            with redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit):
                    self.run_cli(
                        [
                            "patch",
                            "apply",
                            "--plan",
                            str(plan),
                            "--dry-run",
                            "--plan-sha256",
                            "0" * 64,
                        ]
                    )

    def test_patch_sign_outputs_signature_text(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            plan = root / "patch.json"
            plan.write_text(
                json.dumps(
                    {
                        "target": "Assets/Variant.prefab",
                        "ops": [],
                    }
                ),
                encoding="utf-8",
            )
            key = "local-signing-key"
            expected = hmac.new(key.encode("utf-8"), plan.read_bytes(), hashlib.sha256).hexdigest()
            with patch.dict(os.environ, {"UNITYTOOL_PLAN_SIGNING_KEY": key}, clear=False):
                exit_code, output = self.run_cli(["patch", "sign", "--plan", str(plan)])

        self.assertEqual(0, exit_code)
        self.assertEqual(expected, output.strip())

    def test_patch_sign_outputs_signature_json_from_key_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            plan = root / "patch.json"
            key_file = root / "signing_key.txt"
            plan.write_text(
                json.dumps(
                    {
                        "target": "Assets/Variant.prefab",
                        "ops": [],
                    }
                ),
                encoding="utf-8",
            )
            key_file.write_text("file-signing-key\n", encoding="utf-8")
            expected = hmac.new(
                "file-signing-key".encode("utf-8"),
                plan.read_bytes(),
                hashlib.sha256,
            ).hexdigest()
            exit_code, output = self.run_cli(
                [
                    "patch",
                    "sign",
                    "--plan",
                    str(plan),
                    "--key-file",
                    str(key_file),
                    "--format",
                    "json",
                ]
            )

        payload = json.loads(output)
        self.assertEqual(0, exit_code)
        self.assertTrue(payload["success"])
        self.assertEqual("PATCH_PLAN_SIGNATURE", payload["code"])
        self.assertEqual(expected, payload["data"]["signature"])

    def test_patch_attest_outputs_json_with_signature(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            plan = root / "patch.json"
            plan.write_text(
                json.dumps(
                    {
                        "target": "Assets/Variant.prefab",
                        "ops": [],
                    }
                ),
                encoding="utf-8",
            )
            key = "attest-signing-key"
            expected_signature = hmac.new(
                key.encode("utf-8"),
                plan.read_bytes(),
                hashlib.sha256,
            ).hexdigest()
            with patch.dict(os.environ, {"UNITYTOOL_PLAN_SIGNING_KEY": key}, clear=False):
                exit_code, output = self.run_cli(["patch", "attest", "--plan", str(plan)])

        payload = json.loads(output)
        self.assertEqual(0, exit_code)
        self.assertTrue(payload["success"])
        self.assertEqual("PATCH_PLAN_ATTESTATION", payload["code"])
        self.assertEqual(expected_signature, payload["data"]["signature"])

    def test_patch_attest_writes_output_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            plan = root / "patch.json"
            out_file = root / "reports" / "attestation.json"
            plan.write_text(
                json.dumps(
                    {
                        "target": "Assets/Variant.prefab",
                        "ops": [],
                    }
                ),
                encoding="utf-8",
            )
            exit_code, output = self.run_cli(
                ["patch", "attest", "--plan", str(plan), "--unsigned", "--out", str(out_file)]
            )

            file_payload = json.loads(out_file.read_text(encoding="utf-8"))

        payload = json.loads(output)
        self.assertEqual(0, exit_code)
        self.assertTrue(payload["success"])
        self.assertEqual(str(out_file), payload["data"]["attestation_path"])
        self.assertEqual("PATCH_PLAN_ATTESTATION", file_payload["code"])

    def test_patch_verify_succeeds_with_sha256(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            plan = root / "patch.json"
            plan.write_text(
                json.dumps(
                    {
                        "target": "Assets/Variant.prefab",
                        "ops": [],
                    }
                ),
                encoding="utf-8",
            )
            expected = hashlib.sha256(plan.read_bytes()).hexdigest()
            exit_code, output = self.run_cli(
                ["patch", "verify", "--plan", str(plan), "--sha256", expected]
            )

        payload = json.loads(output)
        self.assertEqual(0, exit_code)
        self.assertTrue(payload["success"])
        self.assertTrue(payload["data"]["sha256"]["matched"])

    def test_patch_verify_succeeds_with_signature_key_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            plan = root / "patch.json"
            key_file = root / "verify_key.txt"
            plan.write_text(
                json.dumps(
                    {
                        "target": "Assets/Variant.prefab",
                        "ops": [],
                    }
                ),
                encoding="utf-8",
            )
            key_file.write_text("verify-signing-key\n", encoding="utf-8")
            expected_signature = hmac.new(
                "verify-signing-key".encode("utf-8"),
                plan.read_bytes(),
                hashlib.sha256,
            ).hexdigest()
            exit_code, output = self.run_cli(
                [
                    "patch",
                    "verify",
                    "--plan",
                    str(plan),
                    "--signature",
                    expected_signature,
                    "--signing-key-file",
                    str(key_file),
                ]
            )

        payload = json.loads(output)
        self.assertEqual(0, exit_code)
        self.assertTrue(payload["success"])
        self.assertTrue(payload["data"]["signature"]["matched"])

    def test_patch_verify_uses_attestation_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            plan = root / "patch.json"
            attestation = root / "attestation.json"
            plan.write_text(
                json.dumps(
                    {
                        "target": "Assets/Variant.prefab",
                        "ops": [],
                    }
                ),
                encoding="utf-8",
            )
            expected = hashlib.sha256(plan.read_bytes()).hexdigest()
            attestation.write_text(
                json.dumps({"data": {"sha256": expected}}),
                encoding="utf-8",
            )
            exit_code, output = self.run_cli(
                [
                    "patch",
                    "verify",
                    "--plan",
                    str(plan),
                    "--attestation-file",
                    str(attestation),
                ]
            )

        payload = json.loads(output)
        self.assertEqual(0, exit_code)
        self.assertTrue(payload["success"])
        self.assertEqual(str(attestation), payload["data"]["attestation_file"])

    def test_patch_verify_returns_nonzero_on_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            plan = root / "patch.json"
            plan.write_text(
                json.dumps(
                    {
                        "target": "Assets/Variant.prefab",
                        "ops": [],
                    }
                ),
                encoding="utf-8",
            )
            exit_code, output = self.run_cli(
                ["patch", "verify", "--plan", str(plan), "--sha256", "0" * 64]
            )

        payload = json.loads(output)
        self.assertEqual(1, exit_code)
        self.assertFalse(payload["success"])
        self.assertEqual("PATCH_PLAN_VERIFY_MISMATCH", payload["code"])

    def test_patch_hash_outputs_sha256_text(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            plan = root / "patch.json"
            plan.write_text(
                json.dumps(
                    {
                        "target": "Assets/Variant.prefab",
                        "ops": [],
                    }
                ),
                encoding="utf-8",
            )
            expected = hashlib.sha256(plan.read_bytes()).hexdigest()
            exit_code, output = self.run_cli(["patch", "hash", "--plan", str(plan)])

        self.assertEqual(0, exit_code)
        self.assertEqual(expected, output.strip())

    def test_patch_hash_outputs_sha256_json(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            plan = root / "patch.json"
            plan.write_text(
                json.dumps(
                    {
                        "target": "Assets/Variant.prefab",
                        "ops": [],
                    }
                ),
                encoding="utf-8",
            )
            expected = hashlib.sha256(plan.read_bytes()).hexdigest()
            exit_code, output = self.run_cli(
                ["patch", "hash", "--plan", str(plan), "--format", "json"]
            )

        payload = json.loads(output)
        self.assertEqual(0, exit_code)
        self.assertTrue(payload["success"])
        self.assertEqual("PATCH_PLAN_SHA256", payload["code"])
        self.assertEqual(expected, payload["data"]["sha256"])

    def test_patch_apply_blocks_without_confirm(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            plan = root / "patch.json"
            plan.write_text(
                json.dumps(
                    {
                        "target": "Assets/Variant.prefab",
                        "ops": [
                            {
                                "op": "set",
                                "component": "Example.Component",
                                "path": "items.Array.size",
                                "value": 2,
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            exit_code, output = self.run_cli(["patch", "apply", "--plan", str(plan)])

        payload = json.loads(output)
        self.assertEqual(0, exit_code)
        self.assertEqual("PATCH_APPLY_RESULT", payload["code"])
        self.assertFalse(payload["success"])
        self.assertEqual("SER_CONFIRM_REQUIRED", payload["data"]["steps"][1]["result"]["code"])

    def test_patch_apply_confirm_updates_json_target(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            target = root / "state.json"
            target.write_text(
                json.dumps({"items": [1, 2], "nested": {"value": 10}}),
                encoding="utf-8",
            )
            plan = root / "patch.json"
            plan.write_text(
                json.dumps(
                    {
                        "target": str(target),
                        "ops": [
                            {
                                "op": "set",
                                "component": "Example.Component",
                                "path": "nested.value",
                                "value": 42,
                            },
                            {
                                "op": "insert_array_element",
                                "component": "Example.Component",
                                "path": "items.Array.data",
                                "index": 0,
                                "value": 0,
                            },
                            {
                                "op": "remove_array_element",
                                "component": "Example.Component",
                                "path": "items.Array.data",
                                "index": 1,
                            },
                        ],
                    }
                ),
                encoding="utf-8",
            )
            exit_code, output = self.run_cli(
                ["patch", "apply", "--plan", str(plan), "--confirm"]
            )

            payload = json.loads(output)
            self.assertEqual(0, exit_code)
            self.assertEqual("PATCH_APPLY_RESULT", payload["code"])
            self.assertTrue(payload["success"])
            step_codes = [step["result"]["code"] for step in payload["data"]["steps"]]
            self.assertIn("SER_APPLY_OK", step_codes)
            updated = json.loads(target.read_text(encoding="utf-8"))
            self.assertEqual({"items": [0, 2], "nested": {"value": 42}}, updated)

    def test_patch_apply_confirm_runs_preflight_and_runtime_when_requested(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            assets = root / "Assets"
            assets.mkdir(parents=True, exist_ok=True)
            scene = assets / "Smoke.unity"
            _write(scene, "%YAML 1.1\n")
            target = root / "state.json"
            target.write_text(
                json.dumps({"nested": {"value": 10}}),
                encoding="utf-8",
            )
            plan = root / "patch.json"
            plan.write_text(
                json.dumps(
                    {
                        "target": str(target),
                        "ops": [
                            {
                                "op": "set",
                                "component": "Example.Component",
                                "path": "nested.value",
                                "value": 42,
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            exit_code, output = self.run_cli(
                [
                    "patch",
                    "apply",
                    "--plan",
                    str(plan),
                    "--confirm",
                    "--scope",
                    str(assets),
                    "--runtime-scene",
                    str(scene),
                ]
            )

        payload = json.loads(output)
        self.assertEqual(0, exit_code)
        self.assertTrue(payload["success"])
        step_codes = [step["result"]["code"] for step in payload["data"]["steps"]]
        self.assertIn("REF_SCAN_OK", step_codes)
        self.assertIn("RUN_CLIENTSIM_SKIPPED", step_codes)
        self.assertIn("RUN_ASSERT_OK", step_codes)

    def test_patch_apply_confirm_uses_bridge_env_for_prefab_target(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            target = root / "state.prefab"
            target.write_text("%YAML 1.1\n", encoding="utf-8")
            bridge = root / "bridge.py"
            bridge.write_text(
                """
import json
import sys

request = json.load(sys.stdin)
print(
    json.dumps(
        {
            "success": True,
            "severity": "info",
            "code": "SER_APPLY_OK",
            "message": "Bridge apply completed.",
            "data": {
                "target": request.get("target", ""),
                "op_count": len(request.get("ops", [])),
                "applied": len(request.get("ops", [])),
                "read_only": False,
                "executed": True,
            },
            "diagnostics": [],
        }
    )
)
""".strip(),
                encoding="utf-8",
            )
            plan = root / "patch.json"
            plan.write_text(
                json.dumps(
                    {
                        "target": str(target),
                        "ops": [
                            {
                                "op": "set",
                                "component": "Example.Component",
                                "path": "nested.value",
                                "value": 42,
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            bridge_cmd = f'"{sys.executable}" "{bridge}"'
            with patch.dict(os.environ, {"UNITYTOOL_PATCH_BRIDGE": bridge_cmd}, clear=False):
                exit_code, output = self.run_cli(
                    ["patch", "apply", "--plan", str(plan), "--confirm"]
                )

            payload = json.loads(output)
            self.assertEqual(0, exit_code)
            self.assertTrue(payload["success"])
            self.assertEqual("PATCH_APPLY_RESULT", payload["code"])
            step_codes = [step["result"]["code"] for step in payload["data"]["steps"]]
            self.assertIn("SER_APPLY_OK", step_codes)

    def test_patch_apply_invalid_plan_returns_parser_error(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            plan = root / "patch.json"
            plan.write_text("[]", encoding="utf-8")
            with redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit):
                    self.run_cli(["patch", "apply", "--plan", str(plan), "--dry-run"])

    def test_inspect_where_used_returns_missing_scope_error(self) -> None:
        exit_code, output = self.run_cli(
            [
                "inspect",
                "where-used",
                "--asset-or-guid",
                "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                "--scope",
                "Assets/haiirokoubou",
            ]
        )
        payload = json.loads(output)
        self.assertEqual(0, exit_code)
        self.assertEqual("INSPECT_WHERE_USED_RESULT", payload["code"])
        self.assertEqual("error", payload["severity"])
        self.assertEqual("REF404", payload["data"]["steps"][0]["result"]["code"])

    def test_suggest_ignore_guids_returns_missing_scope_error(self) -> None:
        exit_code, output = self.run_cli(
            ["suggest", "ignore-guids", "--scope", "Assets/haiirokoubou"]
        )
        payload = json.loads(output)
        self.assertEqual(0, exit_code)
        self.assertEqual("SUGGEST_IGNORE_GUIDS_RESULT", payload["code"])
        self.assertEqual("error", payload["severity"])
        self.assertTrue(payload["data"]["read_only"])
        self.assertEqual("REF404", payload["data"]["steps"][0]["result"]["code"])

    def test_validate_refs_loads_ignore_guid_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "ignore_guids.txt"
            path.write_text(
                "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\n# comment\nbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb\n",
                encoding="utf-8",
            )
            exit_code, output = self.run_cli(
                [
                    "validate",
                    "refs",
                    "--scope",
                    "Assets/haiirokoubou",
                    "--ignore-guid-file",
                    str(path),
                ]
            )
        payload = json.loads(output)
        self.assertEqual(0, exit_code)
        self.assertEqual("VALIDATE_REFS_RESULT", payload["code"])
        self.assertEqual("REF404", payload["data"]["steps"][0]["result"]["code"])

    def test_suggest_ignore_guids_writes_ignore_guid_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            _write(
                root / "Assets" / "Ref.prefab",
                f"""%YAML 1.1
--- !u!1001 &100100000
PrefabInstance:
  m_SourcePrefab: {{fileID: 11400000, guid: {MISSING_GUID}, type: 3}}
""",
            )
            out_file = root / "config" / "ignore_guids.txt"

            exit_code, output = self.run_cli(
                [
                    "suggest",
                    "ignore-guids",
                    "--scope",
                    str(root / "Assets"),
                    "--min-occurrences",
                    "1",
                    "--max-items",
                    "10",
                    "--out-ignore-guid-file",
                    str(out_file),
                    "--out-ignore-guid-mode",
                    "replace",
                ]
            )

            payload = json.loads(output)
            self.assertEqual(0, exit_code)
            self.assertEqual("SUGGEST_IGNORE_GUIDS_RESULT", payload["code"])
            self.assertTrue(out_file.exists())
            lines = out_file.read_text(encoding="utf-8").splitlines()
            self.assertIn(MISSING_GUID, lines)
            self.assertEqual(1, payload["data"]["ignore_file_update"]["added"])

            exit_code, output = self.run_cli(
                [
                    "suggest",
                    "ignore-guids",
                    "--scope",
                    str(root / "Assets"),
                    "--min-occurrences",
                    "1",
                    "--max-items",
                    "10",
                    "--out-ignore-guid-file",
                    str(out_file),
                    "--out-ignore-guid-mode",
                    "append",
                ]
            )

            payload = json.loads(output)
            self.assertEqual(0, exit_code)
            self.assertEqual(0, payload["data"]["ignore_file_update"]["added"])

    def test_report_export_writes_markdown(self) -> None:
        payload = {
            "success": False,
            "severity": "warning",
            "code": "PHASE1_STUB",
            "message": "stub",
            "data": {"scope": "Assets/demo"},
            "diagnostics": [],
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            src = temp / "input.json"
            dst = temp / "out.md"
            src.write_text(json.dumps(payload), encoding="utf-8")

            exit_code, output = self.run_cli(
                [
                    "report",
                    "export",
                    "--input",
                    str(src),
                    "--format",
                    "md",
                    "--out",
                    str(dst),
                ]
            )

            self.assertEqual(0, exit_code)
            self.assertIn("Exported report:", output)
            self.assertTrue(dst.exists())
            content = dst.read_text(encoding="utf-8")
            self.assertIn("# UnityTool Validation Report", content)

    def test_report_export_markdown_limits_usages(self) -> None:
        payload = {
            "success": True,
            "severity": "info",
            "code": "INSPECT_WHERE_USED_RESULT",
            "message": "ok",
            "data": {
                "steps": [
                    {
                        "step": "where_used",
                        "result": {
                            "data": {
                                "usages": [
                                    {"path": "A"},
                                    {"path": "B"},
                                ]
                            }
                        },
                    }
                ]
            },
            "diagnostics": [],
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            src = temp / "input.json"
            dst = temp / "out.md"
            src.write_text(json.dumps(payload), encoding="utf-8")

            exit_code, _ = self.run_cli(
                [
                    "report",
                    "export",
                    "--input",
                    str(src),
                    "--format",
                    "md",
                    "--out",
                    str(dst),
                    "--md-max-usages",
                    "1",
                ]
            )

            self.assertEqual(0, exit_code)
            content = dst.read_text(encoding="utf-8")
            self.assertIn('"usages_total": 2', content)
            self.assertIn('"usages_truncated_for_markdown": 1', content)

    def test_report_export_markdown_omit_usages(self) -> None:
        payload = {
            "success": True,
            "severity": "info",
            "code": "INSPECT_WHERE_USED_RESULT",
            "message": "ok",
            "data": {"usages": [{"path": "A"}]},
            "diagnostics": [],
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            src = temp / "input.json"
            dst = temp / "out.md"
            src.write_text(json.dumps(payload), encoding="utf-8")

            exit_code, _ = self.run_cli(
                [
                    "report",
                    "export",
                    "--input",
                    str(src),
                    "--format",
                    "md",
                    "--out",
                    str(dst),
                    "--md-omit-usages",
                ]
            )

            self.assertEqual(0, exit_code)
            content = dst.read_text(encoding="utf-8")
            self.assertIn('"usages": []', content)

    def test_report_export_markdown_limits_steps(self) -> None:
        payload = {
            "success": True,
            "severity": "info",
            "code": "VALIDATE_RUNTIME_RESULT",
            "message": "ok",
            "data": {
                "steps": [
                    {"step": "a", "result": {"data": {"x": 1}}},
                    {"step": "b", "result": {"data": {"x": 2}}},
                ]
            },
            "diagnostics": [],
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            src = temp / "input.json"
            dst = temp / "out.md"
            src.write_text(json.dumps(payload), encoding="utf-8")

            exit_code, _ = self.run_cli(
                [
                    "report",
                    "export",
                    "--input",
                    str(src),
                    "--format",
                    "md",
                    "--out",
                    str(dst),
                    "--md-max-steps",
                    "1",
                ]
            )

            self.assertEqual(0, exit_code)
            content = dst.read_text(encoding="utf-8")
            self.assertIn('"steps_total": 2', content)
            self.assertIn('"steps_truncated_for_markdown": 1', content)

    def test_report_export_markdown_omit_steps(self) -> None:
        payload = {
            "success": True,
            "severity": "info",
            "code": "VALIDATE_RUNTIME_RESULT",
            "message": "ok",
            "data": {"steps": [{"step": "a"}]},
            "diagnostics": [],
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            src = temp / "input.json"
            dst = temp / "out.md"
            src.write_text(json.dumps(payload), encoding="utf-8")

            exit_code, _ = self.run_cli(
                [
                    "report",
                    "export",
                    "--input",
                    str(src),
                    "--format",
                    "md",
                    "--out",
                    str(dst),
                    "--md-omit-steps",
                ]
            )

            self.assertEqual(0, exit_code)
            content = dst.read_text(encoding="utf-8")
            self.assertIn('"steps": []', content)


if __name__ == "__main__":
    unittest.main()
