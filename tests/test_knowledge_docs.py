"""Doc-grep regression tests for knowledge/prefab-sentinel-workflow-patterns.md.

Verifies that the workflow patterns document carries the operational guidance
captured in this run:

* a heading naming the editor menu helper pattern as the alternative path
  when ``editor_run_script`` gets stuck (issue #102), and
* a Unity UI property-path reference table covering at least
  ``Text.m_FontData.m_FontSize`` (and another Inspector-display-vs-property-path
  discrepancy) for trial-and-error reduction (issue #105 follow-up).
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

DOC: Path = (
    Path(__file__).resolve().parent.parent
    / "knowledge"
    / "prefab-sentinel-workflow-patterns.md"
)


def _read() -> str:
    return DOC.read_text(encoding="utf-8")


def _heading_lines(text: str) -> list[str]:
    return [line for line in text.splitlines() if line.lstrip().startswith("#")]


class TestWorkflowPatternsContent(unittest.TestCase):
    def test_editor_menu_helper_section_heading_present(self) -> None:
        text = _read()
        headings = _heading_lines(text)
        # Must have a heading that names the editor menu helper pattern.
        self.assertTrue(
            any(
                re.search(r"editor\s*menu\s*helper", line, re.IGNORECASE)
                for line in headings
            ),
            f"No heading naming the 'editor menu helper' pattern in {DOC.name}: "
            f"{headings}",
        )
        # The body must reference editor_execute_menu_item somewhere.
        self.assertIn(
            "editor_execute_menu_item",
            text,
            "Editor menu helper guidance must reference editor_execute_menu_item",
        )

    def test_unity_ui_propertypath_table_lists_textfontdata_and_one_other(
        self,
    ) -> None:
        text = _read()
        # Required canonical entry from the issue report.
        self.assertIn(
            "Text.m_FontData.m_FontSize",
            text,
            "Workflow doc must list the Text.m_FontData.m_FontSize discrepancy",
        )
        # And at least one other Inspector-display-vs-property-path discrepancy.
        # We accept any other m_FontData.* nested entry, m_AnchoredPosition,
        # m_SizeDelta, m_HorizontalAlignment, m_VerticalAlignment, or m_Sprite.
        other_candidates = [
            "Text.m_FontData.m_Alignment",
            "Text.m_FontData.m_LineSpacing",
            "Text.m_FontData.m_BestFit",
            "RectTransform.m_AnchoredPosition",
            "RectTransform.m_SizeDelta",
            "TextMeshProUGUI.m_HorizontalAlignment",
            "TextMeshProUGUI.m_VerticalAlignment",
            "Image.m_Sprite",
        ]
        hits = [c for c in other_candidates if c in text]
        self.assertGreaterEqual(
            len(hits),
            1,
            (
                "Workflow doc must list at least one additional "
                "Inspector-display-vs-property-path discrepancy beyond "
                "Text.m_FontData.m_FontSize. Candidates checked: "
                f"{other_candidates}"
            ),
        )


if __name__ == "__main__":
    unittest.main()
