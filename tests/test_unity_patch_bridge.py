from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path


class UnityPatchBridgeTests(unittest.TestCase):
    def _bridge_path(self) -> Path:
        return Path("tools") / "unity_patch_bridge.py"

    def _run_bridge(self, payload: dict[str, object]) -> dict[str, object]:
        completed = subprocess.run(
            [sys.executable, str(self._bridge_path())],
            input=json.dumps(payload),
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
        )
        self.assertEqual(0, completed.returncode, msg=completed.stderr)
        return json.loads(completed.stdout)

    def test_reference_bridge_returns_stub_for_supported_target(self) -> None:
        result = self._run_bridge(
            {
                "protocol_version": 1,
                "target": "Assets/Test.prefab",
                "ops": [],
            }
        )
        self.assertEqual(1, result["protocol_version"])
        self.assertFalse(result["success"])
        self.assertEqual("warning", result["severity"])
        self.assertEqual("PHASE1_STUB", result["code"])

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


if __name__ == "__main__":
    unittest.main()
