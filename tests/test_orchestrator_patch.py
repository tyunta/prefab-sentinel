"""Tests for orchestrator patch_apply missing-GUID fail-fast contract (#83)."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from prefab_sentinel.orchestrator import Phase1Orchestrator
from tests.bridge_test_helpers import write_file

BASE_GUID = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
VARIANT_GUID = "cccccccccccccccccccccccccccccccc"
MISSING_GUID = "ffffffffffffffffffffffffffffffff"


def _create_project_with_missing_guid(root: Path) -> None:
    """Project with a variant that references MISSING_GUID (not in project map)."""
    write_file(
        root / "Assets" / "Base.prefab",
        """%YAML 1.1
--- !u!1 &100100000
GameObject:
  m_Name: Base
""",
    )
    write_file(
        root / "Assets" / "Base.prefab.meta",
        f"""fileFormatVersion: 2
guid: {BASE_GUID}
""",
    )
    write_file(
        root / "Assets" / "OrphanVariant.prefab",
        f"""%YAML 1.1
--- !u!1001 &100100000
PrefabInstance:
  m_SourcePrefab: {{fileID: 100100000, guid: {BASE_GUID}, type: 3}}
  m_Modification:
    m_Modifications:
    - target: {{fileID: 100100000, guid: {MISSING_GUID}, type: 3}}
      propertyPath: missing.ref
      value: 0
      objectReference: {{fileID: 0}}
""",
    )
    write_file(
        root / "Assets" / "OrphanVariant.prefab.meta",
        f"""fileFormatVersion: 2
guid: {VARIANT_GUID}
""",
    )


class MissingGuidContractTests(unittest.TestCase):
    """T22: ``patch_apply`` preflight must return top-level REF001 and leave
    all write targets untouched when any referenced GUID is missing."""

    def test_patch_apply_aborts_on_missing_guid(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            _create_project_with_missing_guid(root)

            state_path = root / "state.json"
            state_initial = {"nested": {"value": 10}}
            state_path.write_text(json.dumps(state_initial), encoding="utf-8")
            original_mtime = state_path.stat().st_mtime_ns
            original_text = state_path.read_text(encoding="utf-8")

            orchestrator = Phase1Orchestrator.default(project_root=root)
            response = orchestrator.patch_apply(
                plan={
                    "plan_version": 2,
                    "resources": [
                        {
                            "id": "state",
                            "kind": "json",
                            "path": str(state_path),
                            "mode": "open",
                        }
                    ],
                    "ops": [
                        {
                            "resource": "state",
                            "op": "set",
                            "component": "Example.Component",
                            "path": "nested.value",
                            "value": 42,
                        }
                    ],
                },
                dry_run=False,
                confirm=True,
                change_reason="T22 missing-GUID contract",
                scope="Assets",
            )

            self.assertFalse(response.success)
            self.assertEqual("REF001", response.code)
            self.assertEqual("error", response.severity.value)
            self.assertTrue(response.data["fail_fast_triggered"])

            # No apply_and_save step must have run.
            step_names = [step["step"] for step in response.data["steps"]]
            self.assertNotIn("apply_and_save", step_names)
            self.assertFalse(
                any(name.startswith("apply_and_save:") for name in step_names)
            )

            # The write target must be byte-identical and the mtime unchanged.
            self.assertEqual(original_text, state_path.read_text(encoding="utf-8"))
            self.assertEqual(original_mtime, state_path.stat().st_mtime_ns)


if __name__ == "__main__":
    unittest.main()
