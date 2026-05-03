"""Documentation invariants for the canonical-core-partial dependency
(issue #139).

The drift checker (`scripts/check_bridge_constants.py`) and the
bumpversion configuration in `pyproject.toml [tool.bumpversion]` both
hard-code the canonical core partial path
``tools/unity/PrefabSentinel.UnityEditorControlBridge.cs`` and rely on
three load-bearing constants residing in that file.  Two documentation
surfaces must publish that dependency so a future relocation of the
constants does not silently disable either tool:

1. The version-management section of the project's operational rules
   file (``CLAUDE.md``).
2. The leading documentation block of
   ``scripts/check_bridge_constants.py``.

Each surface must name the canonical core partial path, list the three
constants, and state that any relocation requires a simultaneous update
to both the drift checker and the bumpversion configuration.
"""

from __future__ import annotations

import unittest
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_CLAUDE_MD = _PROJECT_ROOT / "CLAUDE.md"
_DRIFT_CHECKER = _PROJECT_ROOT / "scripts" / "check_bridge_constants.py"

_CANONICAL_PARTIAL_PATH = "tools/unity/PrefabSentinel.UnityEditorControlBridge.cs"
_LOAD_BEARING_CONSTANTS = (
    "BridgeVersion",
    "ProtocolVersion",
    "ConsoleLogBuffer.DefaultCapacity",
)


class TestOperationalRulesDocumentsCanonicalPath(unittest.TestCase):
    """The version-management section of CLAUDE.md names the canonical
    core partial path, lists the three load-bearing constants, and
    instructs that any relocation requires a simultaneous update to
    both the drift checker and the bumpversion configuration."""

    def setUp(self) -> None:
        self.text = _CLAUDE_MD.read_text(encoding="utf-8")
        # Slice out the version-management section so the assertions
        # cannot be satisfied by an unrelated mention elsewhere in the
        # file. The section header in this repo is "## バージョン管理".
        marker = "## バージョン管理"
        start = self.text.find(marker)
        self.assertNotEqual(
            -1, start, "version-management section header not found in CLAUDE.md"
        )
        next_section = self.text.find("\n## ", start + len(marker))
        end = next_section if next_section != -1 else len(self.text)
        self.section = self.text[start:end]

    def test_section_names_canonical_partial_path(self) -> None:
        self.assertIn(_CANONICAL_PARTIAL_PATH, self.section)

    def test_section_lists_three_load_bearing_constants(self) -> None:
        for name in _LOAD_BEARING_CONSTANTS:
            with self.subTest(constant=name):
                self.assertIn(name, self.section)

    def test_section_names_both_dependent_tools(self) -> None:
        # Drift checker file name (or its basename) and bumpversion
        # marker must both appear in the section so a maintainer
        # relocating constants knows which two artifacts to keep in
        # sync.
        self.assertIn("check_bridge_constants.py", self.section)
        self.assertIn("bumpversion", self.section)

    def test_section_states_simultaneous_update_requirement(self) -> None:
        # The instruction must convey "update both at the same time".
        # The Japanese rendering used in this file is "同時に更新".
        self.assertIn("同時に更新", self.section)


class TestDriftCheckerHeaderDocumentsCanonicalPath(unittest.TestCase):
    """The leading documentation block of the drift checker module
    names the canonical core partial path, lists the three load-bearing
    constants, and instructs that any relocation requires a
    simultaneous update to both the drift checker and the bumpversion
    configuration."""

    def setUp(self) -> None:
        text = _DRIFT_CHECKER.read_text(encoding="utf-8")
        # Capture only the leading triple-quoted module docstring; the
        # contract is scoped to that block by spec.
        first = text.find('"""')
        self.assertNotEqual(-1, first, "drift checker has no leading docstring")
        second = text.find('"""', first + 3)
        self.assertNotEqual(-1, second, "drift checker docstring is unterminated")
        self.docstring = text[first + 3:second]

    def test_docstring_names_canonical_partial_path(self) -> None:
        self.assertIn(_CANONICAL_PARTIAL_PATH, self.docstring)

    def test_docstring_lists_three_load_bearing_constants(self) -> None:
        for name in _LOAD_BEARING_CONSTANTS:
            with self.subTest(constant=name):
                self.assertIn(name, self.docstring)

    def test_docstring_names_both_dependent_tools(self) -> None:
        self.assertIn("bumpversion", self.docstring)
        # The docstring lives inside the drift checker itself, so a
        # self-reference by symbolic name is sufficient.
        self.assertIn("drift", self.docstring.lower())

    def test_docstring_states_simultaneous_update_requirement(self) -> None:
        # English rendering of the simultaneous-update instruction.
        self.assertIn("simultaneously", self.docstring.lower())


if __name__ == "__main__":
    unittest.main()
