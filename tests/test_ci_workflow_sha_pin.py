"""Source-invariant tests for ``.github/workflows/ci.yml`` (issues #292, #293).

Two structural properties are pinned here:

* every ``uses:`` line is anchored to an immutable 40-character commit
  SHA with a trailing version-tag comment (issue #293);
* the ``csharp-tests`` job's ``if:`` clause carries a
  ``github.event_name == 'workflow_dispatch'`` fallback so manual
  workflow runs do not silently skip the C# tests when no C# files
  changed (issue #292).
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

import pytest

from tests._typing_helpers import require_not_none

# Reads the un-mutated ``.github/workflows/ci.yml`` text; the
# assertions cannot observe mutations applied to ``prefab_sentinel/``.
pytestmark = pytest.mark.source_text_invariant

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
_CI_PATH = _PROJECT_ROOT / ".github" / "workflows" / "ci.yml"

# 40 hex characters is the canonical Git SHA-1 form. The trailing
# version-tag comment is a single ``# vN`` or ``# vN.N`` (or longer
# tag form) immediately after the SHA so readers can recover the
# original tag without leaving the workflow file.
_USES_LINE_RE = re.compile(
    r"^\s*uses:\s*(\S+?)@([0-9a-fA-F]{40})\s*(#\s*\S+.*)$",
)
_USES_KEY_RE = re.compile(r"^\s*uses:\s*")

# Strip ``# ...`` line comments to mask out documentation references
# (e.g. quoted earlier-version pins) before the workflow_dispatch
# fallback scan.
_YAML_COMMENT_RE = re.compile(r"(?m)#[^\n]*$")


def _strip_yaml_comments(text: str) -> str:
    return _YAML_COMMENT_RE.sub("", text)


class TestCiWorkflowShaPin(unittest.TestCase):
    """Every ``uses:`` line in ``ci.yml`` references an immutable SHA."""

    def test_every_uses_line_anchors_to_a_40char_commit_sha(self) -> None:
        text = _CI_PATH.read_text(encoding="utf-8")
        uses_lines = [
            line for line in text.splitlines()
            if _USES_KEY_RE.match(line)
        ]
        self.assertGreater(
            len(uses_lines),
            0,
            "ci.yml carries no `uses:` lines; expected at least one.",
        )
        for line in uses_lines:
            match = _USES_LINE_RE.match(line)
            self.assertIsNotNone(
                match,
                f"`uses:` line is not SHA-pinned with a trailing "
                f"version-tag comment: {line!r}. Required shape: "
                f"`uses: <action>@<40-char-sha> # <tag>`.",
            )


class TestCsharpTestsWorkflowDispatchFallback(unittest.TestCase):
    """The csharp-tests job's ``if:`` clause must carry a
    ``workflow_dispatch`` disjunct so manual runs do not silently skip
    the C# tests when no C# files changed (issue #292).
    """

    def test_csharp_tests_if_clause_carries_workflow_dispatch_disjunct(self) -> None:
        text = _strip_yaml_comments(_CI_PATH.read_text(encoding="utf-8"))
        # Locate the csharp-tests job block; pin both the change-filter
        # conjunct and the workflow_dispatch disjunct on the same
        # ``if:`` line so a future edit that drops either part trips
        # this assertion.
        match = re.search(
            r"^\s*csharp-tests:\s*\n"  # block heading
            r"(?:.*\n)*?"             # arbitrary intervening lines
            r"\s*if:\s*([^\n]+)\n",   # the if: line we care about
            text,
            re.MULTILINE,
        )
        self.assertIsNotNone(
            match,
            "csharp-tests job block with an `if:` line not found.",
        )
        match = require_not_none(match, "csharp-tests if line")
        if_expression = match.group(1)
        # The conjunct from the original change-filter must still
        # gate path-driven runs.
        self.assertIn(
            "needs.changes.outputs.csharp == 'true'",
            if_expression,
            f"path-filter conjunct dropped from `if:`: {if_expression!r}",
        )
        # The workflow_dispatch fallback must be joined with the
        # change-filter via logical-or so manual runs execute the
        # csharp-tests job regardless of the change filter's verdict.
        self.assertIn(
            "github.event_name == 'workflow_dispatch'",
            if_expression,
            f"workflow_dispatch fallback missing from `if:`: {if_expression!r}",
        )
        self.assertIn(
            "||",
            if_expression,
            f"logical-or join missing in `if:`: {if_expression!r}",
        )


class TestFullMypyWorkflowGate(unittest.TestCase):
    """Pin the full test-target mypy CI gate restored by issue #132."""

    def test_full_mypy_gate_has_required_triggers_and_command(self) -> None:
        text = _strip_yaml_comments(_CI_PATH.read_text(encoding="utf-8"))

        self.assertIn("pull_request:", text)
        self.assertIn("workflow_dispatch:", text)
        self.assertRegex(text, r"(?m)^\s*push:\s*\n\s*branches:\s*\n\s*-\s*main\s*$")
        self.assertRegex(text, r"(?m)^\s*schedule:\s*\n\s*-\s*cron:\s*['\"][^'\"]+['\"]")

        command = "uv run mypy prefab_sentinel tests --show-error-codes"
        self.assertEqual(
            1,
            text.count(command),
            "ci.yml must contain exactly one full test-target mypy gate.",
        )

        match = re.search(
            r"(?ms)^  typecheck-tests:\s*\n(?P<body>.*?)(?=^  [A-Za-z0-9_-]+:|\Z)",
            text,
        )
        self.assertIsNotNone(
            match,
            "typecheck-tests job block not found.",
        )
        match = require_not_none(match, "typecheck-tests job block")
        body = match.group("body")
        install_index = body.find("uv sync --extra lint --extra test --extra mcp")
        command_index = body.find(command)
        self.assertNotEqual(
            -1,
            install_index,
            "typecheck-tests must install lint, test, and mcp extras.",
        )
        self.assertNotEqual(
            -1,
            command_index,
            "typecheck-tests must run full mypy over prefab_sentinel and tests.",
        )
        self.assertLess(
            install_index,
            command_index,
            "typecheck-tests must install dependencies before running full mypy.",
        )


if __name__ == "__main__":
    unittest.main()
