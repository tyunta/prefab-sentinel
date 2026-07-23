"""Regression and source-invariant tests for the patch-bridge constants
module consolidation (issue #297).

Two surfaces are pinned here:

* representative wire codes continue to surface for failure paths that
  the unit harness can drive directly (protocol-version mismatch,
  empty/malformed request, missing watch dir);
* the patch bridge source file contains zero bare-string
  ``code="<UPPER_SNAKE>"`` literals — every wire-code reference must
  resolve through ``_bridge_codes``.
"""

from __future__ import annotations

import io
import json
import os
import re
import sys
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

import pytest

from tests._typing_helpers import load_json_object

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
_TOOLS_DIR = _PROJECT_ROOT / "tools"
_PATCH_BRIDGE_PATH = _TOOLS_DIR / "unity_patch_bridge.py"
if str(_TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(_TOOLS_DIR))


# Strip Python comments and docstrings before the literal scan; the
# scan is interested only in live ``code=`` argument expressions, not
# documentation references.
_PY_LINE_COMMENT_RE = re.compile(r"(?m)#[^\n]*$")
_PY_TRIPLE_DQ_RE = re.compile(r'"""[\s\S]*?"""')
_PY_TRIPLE_SQ_RE = re.compile(r"'''[\s\S]*?'''")


def _strip_python_comments_and_docstrings(text: str) -> str:
    text = _PY_TRIPLE_DQ_RE.sub("", text)
    text = _PY_TRIPLE_SQ_RE.sub("", text)
    text = _PY_LINE_COMMENT_RE.sub("", text)
    return text


def _invoke_main(stdin_text: str, env: dict[str, str]) -> dict[str, Any]:
    """Run ``unity_patch_bridge.main`` against a fabricated stdin/env."""
    import unity_patch_bridge as bridge  # noqa: PLC0415

    fake_stdin = io.StringIO(stdin_text)
    fake_stdout = io.StringIO()
    fake_stderr = io.StringIO()
    with (
        mock.patch.object(sys, "stdin", fake_stdin),
        mock.patch.object(sys, "stdout", fake_stdout),
        mock.patch.object(sys, "stderr", fake_stderr),
        mock.patch.dict(os.environ, env, clear=False),
    ):
        bridge.main()
    raw = fake_stdout.getvalue().strip().splitlines()
    # The bridge writes one JSON envelope per request; the failure
    # paths under test exit before secondary writes, so the first line
    # is the response of interest.
    return load_json_object(raw[0], "patch bridge response")


class TestPatchBridgeWireCodeRegression(unittest.TestCase):
    """Driving representative failure paths through the patch bridge
    continues to return the documented wire codes; this guards against
    a refactor typo silently renaming a wire code.
    """

    def test_request_empty_payload_returns_documented_code(self) -> None:
        response = _invoke_main(
            stdin_text="",
            env={"UNITYTOOL_BRIDGE_WATCH_DIR": "/nonexistent-bridge-dir"},
        )
        self.assertEqual("BRIDGE_REQUEST_EMPTY", response["code"])

    def test_request_malformed_json_returns_documented_code(self) -> None:
        response = _invoke_main(
            stdin_text="{not valid json",
            env={"UNITYTOOL_BRIDGE_WATCH_DIR": "/nonexistent-bridge-dir"},
        )
        self.assertEqual("BRIDGE_REQUEST_JSON", response["code"])

    def test_missing_watch_dir_returns_documented_code(self) -> None:
        # Issue #88: bridge requires the v2 ``plan_version`` +
        # ``resources`` + ``ops`` shape; the ``target`` shortcut is the
        # legacy schema and is short-circuited before the watch-dir
        # check.  Build the v2 payload directly here so the watch-dir
        # check is the first guard the bridge reaches.
        payload = json.dumps(
            {
                "protocol_version": 2,
                "plan_version": 2,
                "resources": [
                    {
                        "id": "r1",
                        "kind": "prefab",
                        "mode": "open",
                        "path": "Assets/Foo.prefab",
                    }
                ],
                "ops": [
                    {
                        "op": "set",
                        "resource": "r1",
                        "component": "Transform",
                        "path": "m_LocalPosition.x",
                        "value": 1,
                        "value_kind": "int",
                    }
                ],
            }
        )
        response = _invoke_main(
            stdin_text=payload,
            # An unset watch-dir env causes the MISSING code to
            # surface before any I/O.
            env={"UNITYTOOL_BRIDGE_WATCH_DIR": ""},
        )
        self.assertEqual("BRIDGE_WATCH_DIR_MISSING", response["code"])


@pytest.mark.source_text_invariant
class TestPatchBridgeBareLiteralCoverage(unittest.TestCase):
    """Issue #297 — after consolidation, every wire-code reference must
    resolve through ``_bridge_codes``; the patch bridge source must
    contain zero bare-string ``code="<UPPER_SNAKE>"`` literal call sites.
    """

    def test_patch_bridge_carries_no_bare_string_code_literals(self) -> None:
        text = _strip_python_comments_and_docstrings(
            _PATCH_BRIDGE_PATH.read_text(encoding="utf-8")
        )
        # ``code="<UPPER_SNAKE>"`` is the literal shape consolidated
        # behind the constants module. The matcher tolerates positional
        # whitespace but pins the argument-name and the upper-snake
        # literal form.
        hits = re.findall(
            r'code\s*=\s*"([A-Z][A-Z0-9_]*)"',
            text,
        )
        self.assertEqual(
            [],
            hits,
            f"patch bridge source carries bare-string code= literals: "
            f"{sorted(set(hits))!r}. Replace with constants from "
            f"`tools/_bridge_codes`.",
        )


if __name__ == "__main__":
    unittest.main()
