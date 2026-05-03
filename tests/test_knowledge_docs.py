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


_EVENT_BINDING_DOC: Path = (
    Path(__file__).resolve().parent.parent
    / "knowledge"
    / "vrchat-event-binding.md"
)

_WORLDS_DOC: Path = (
    Path(__file__).resolve().parent.parent
    / "knowledge"
    / "vrchat-sdk-worlds.md"
)


def _read_event_binding() -> str:
    return _EVENT_BINDING_DOC.read_text(encoding="utf-8")


def _read_worlds() -> str:
    return _WORLDS_DOC.read_text(encoding="utf-8")


class EventBindingKnowledgeTests(unittest.TestCase):
    """Issue #120: the new VRChat event-binding knowledge file documents
    the persistent-listener allowlist, the strip behaviour, the
    string-listener wiring snippet, the allowlisted UI-event target
    table, and cites the official VRChat URLs.
    """

    def test_file_exists(self) -> None:
        self.assertTrue(_EVENT_BINDING_DOC.is_file())

    def test_lists_allowlisted_argument_types(self) -> None:
        text = _read_event_binding()
        for token in ("void", "int", "float", "string", "bool", "Object"):
            self.assertIn(token, text)
        # The string-event-send listener entry must be named explicitly.
        self.assertIn("SendCustomEvent", text)

    def test_documents_strip_behaviour(self) -> None:
        text = _read_event_binding()
        self.assertIn("UnityEventFilter", text)
        # The body must describe the strip behaviour ("剥奪", "strip").
        self.assertTrue(
            ("剥奪" in text) or ("strip" in text.lower()),
            "Event-binding doc must describe the persistent-listener strip behaviour",
        )

    def test_includes_string_listener_wiring_snippet(self) -> None:
        text = _read_event_binding()
        self.assertIn("UnityEventTools.AddStringPersistentListener", text)

    def test_cites_official_vrchat_urls(self) -> None:
        text = _read_event_binding()
        # Allowlisted-components / event-binding URL family.
        self.assertIn("creators.vrchat.com", text)
        # The UI-events URL must also appear (allowlisted UI targets).
        self.assertTrue(
            ("event-binding" in text) or ("ui-events" in text),
            "Event-binding doc must cite at least one event-binding-related VRChat URL",
        )


class WorldCanvasKnowledgeTests(unittest.TestCase):
    """Issue #121: the existing World SDK knowledge file gains a
    WorldSpace-Canvas construction section that documents the render
    mode, local scale, sizeDelta unit, the same-GameObject rule for
    VRC_UiShape, the BoxCollider auto-resize behaviour, the BoxCollider
    Z trap, and the resulting UI-input failure mode, citing the official
    VRC_UiShape URL.
    """

    def test_section_heading_present(self) -> None:
        text = _read_worlds()
        self.assertIn("WorldSpace Canvas", text)

    def test_documents_render_mode(self) -> None:
        text = _read_worlds()
        self.assertIn("Canvas.renderMode", text)
        self.assertIn("WorldSpace", text)

    def test_documents_local_scale(self) -> None:
        text = _read_worlds()
        self.assertIn("(0.01, 0.01, 0.01)", text)

    def test_documents_size_delta_unit(self) -> None:
        text = _read_worlds()
        self.assertIn("sizeDelta", text)
        self.assertTrue(
            ("ピクセル" in text) or ("pixel" in text.lower()),
            "WorldSpace-Canvas section must mention sizeDelta in pixel units",
        )

    def test_documents_same_gameobject_rule(self) -> None:
        text = _read_worlds()
        self.assertIn("VRC_UiShape", text)
        # The rule "VRC_UiShape goes on the same GameObject as the Canvas".
        self.assertTrue(
            ("Canvas と同じ GameObject" in text)
            or ("same GameObject" in text.lower()),
            "WorldSpace-Canvas section must state VRC_UiShape is placed on the same GameObject as the Canvas",
        )

    def test_documents_boxcollider_autoresize(self) -> None:
        text = _read_worlds()
        self.assertIn("BoxCollider", text)
        self.assertTrue(
            ("自動リサイズ" in text) or ("auto-resize" in text.lower())
            or ("自動付与" in text),
            "WorldSpace-Canvas section must mention the BoxCollider auto-resize behaviour",
        )
        # Canvas-local unit factor — the section must explain that
        # BoxCollider Z = 1.0 corresponds to 0.01 m at localScale = 0.01.
        self.assertIn("0.01", text)

    def test_documents_boxcollider_z_trap(self) -> None:
        text = _read_worlds()
        # The Z=1.0 thickness trap (1 m collider thickness without scale).
        self.assertTrue(
            ("Z = `1.0`" in text) or ("Z=1.0" in text)
            or ("1 m" in text) or ("1m" in text),
            "WorldSpace-Canvas section must describe the BoxCollider Z=1.0 trap",
        )

    def test_documents_ui_input_failure(self) -> None:
        text = _read_worlds()
        # The failure mode: "1 m thick collider blocks UI input".
        self.assertTrue(
            ("レーザー" in text) or ("UI input" in text.lower())
            or ("UI 入力" in text),
            "WorldSpace-Canvas section must describe the UI-input failure mode",
        )

    def test_cites_vrc_uishape_url(self) -> None:
        text = _read_worlds()
        self.assertIn("vrc_uishape", text.lower())
        self.assertIn("creators.vrchat.com", text)


if __name__ == "__main__":
    unittest.main()
