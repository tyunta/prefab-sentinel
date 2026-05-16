"""Source-level regression test for the Editor-side assembly definition.

Asserts that the bridge's VRC SDK dependency is declared by assembly name,
not by GUID. The issue was that GUID-based references break across SDK
versions where the underlying GUID changes; name-based references survive.
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path

import pytest

# Issue #167: this module reads the original (un-mutated) source tree
# (the ``tools/unity`` checked-in asmdef), so its assertions are
# insensitive to mutations applied to ``prefab_sentinel/`` and would
# only contribute noise to mutmut runs.  The marker is the inclusion
# mechanism for repository-synchrony tests; mutmut's pytest selection
# excludes this marker via a single ``-m`` filter.
pytestmark = pytest.mark.source_text_invariant

ASMDEF: Path = (
    Path(__file__).resolve().parent.parent
    / "tools"
    / "unity"
    / "PrefabSentinel.Editor.asmdef"
)


class TestAsmdefVrcsdkReference(unittest.TestCase):
    def test_references_contain_vrc_sdkbase_editor_name(self) -> None:
        data = json.loads(ASMDEF.read_text(encoding="utf-8"))
        refs = data.get("references", [])
        self.assertIn(
            "VRC.SDKBase.Editor",
            refs,
            f"references did not contain the name 'VRC.SDKBase.Editor': {refs}",
        )

    def test_references_have_no_guid_form_entries(self) -> None:
        data = json.loads(ASMDEF.read_text(encoding="utf-8"))
        refs = data.get("references", [])
        for entry in refs:
            self.assertFalse(
                entry.startswith("GUID:"),
                f"references entry must not be GUID-form: {entry}",
            )


if __name__ == "__main__":
    unittest.main()
