"""Source-text invariants for ``tools/unity/PrefabSentinel.UnityIntegrationTests.cs``.

Issue #193 — the C# integration test that exercises the non-fatal
classification path issues a bridge request whose action name is the
safe-save action and whose payload includes a non-empty
``protect_components_json`` field.  This file pins both invariants by
reading the un-mutated source tree.
"""

from __future__ import annotations

import unittest
from pathlib import Path

import pytest

# This module reads the C# integration-test sources from the un-mutated
# ``tools/unity`` tree to verify a source-text invariant; its assertions
# are insensitive to mutations applied to ``prefab_sentinel/``.  The
# marker is the inclusion mechanism for repository-synchrony tests;
# mutmut's pytest selection excludes it via a single ``-m`` filter.
pytestmark = pytest.mark.source_text_invariant

INTEGRATION_TESTS = (
    Path(__file__).resolve().parent.parent
    / "tools"
    / "unity"
    / "PrefabSentinel.UnityIntegrationTests.cs"
)


class TestNonFatalClassificationCallsSafeSave(unittest.TestCase):
    """Issue #193 — the non-fatal-classification integration test must
    issue its bridge request via the ``safe_save_prefab`` action and
    include a non-empty ``protect_components_json`` payload field.
    """

    def test_action_name_is_safe_save_prefab(self) -> None:
        text = INTEGRATION_TESTS.read_text(encoding="utf-8")
        # The non-fatal-classification scenario is the only place the
        # integration tests issue a prefab-save request, so any
        # ``RunEditorControlBridge(BuildEditorControlRequest("…", …))``
        # call with a prefab-save action must use the safe-save name.
        self.assertIn(
            'BuildEditorControlRequest("safe_save_prefab"',
            text,
            "non-fatal-classification integration test must use safe_save_prefab",
        )
        # The legacy action name must not appear as a request action.
        self.assertNotIn(
            'BuildEditorControlRequest("save_as_prefab"',
            text,
            "legacy save_as_prefab action must not remain in integration tests",
        )

    def test_payload_carries_non_empty_protect_components_json(self) -> None:
        text = INTEGRATION_TESTS.read_text(encoding="utf-8")
        # The payload must include the protect_components_json field
        # name; pinning the field name ensures the bridge handler's
        # required-argument contract is exercised.
        self.assertIn("protect_components_json", text)
        # The JSON-encoded list literal must contain at least one
        # element.  In the C# source the JSON list ``["Type"]`` is
        # written as the escaped-string literal ``[\\\"...\\\"]``; we
        # pin both the opening ``[\\\"`` and closing ``\\\"]`` brackets.
        self.assertRegex(
            text,
            r'protect_components_json\\":\\"\[\\\\\\"[A-Za-z_][A-Za-z0-9_]*\\\\\\"\]\\"',
            "safe_save_prefab integration request must carry a non-empty "
            "protect_components_json list literal",
        )


if __name__ == "__main__":
    unittest.main()
