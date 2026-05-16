"""Wiring null-field classification tests (issue #296).

The wiring inspector produces a per-field classification list alongside
the legacy flat list of null field names. Categories partition null
fields by:

* ``variant_overridden_null`` — the Variant override map carries the
  field path on the component;
* ``dangling`` — the field carries a non-empty external GUID with an
  unresolved local id (``fileID: 0`` accompanies the GUID);
* ``unwired`` — neither signal is present; the field was never wired.
"""

from __future__ import annotations

import unittest

from prefab_sentinel.orchestrator_wiring import _component_to_dict
from prefab_sentinel.udon_wiring import (
    NullFieldClassification,
    analyze_wiring,
)

# ---------------------------------------------------------------------------
# Fixture YAML — one MonoBehaviour with one null reference field.
# ---------------------------------------------------------------------------


def _yaml_with_field(file_id: str, guid: str = "") -> str:
    """Build a minimal Unity YAML asset with a single MonoBehaviour
    component whose ``targetRef`` field has the supplied file_id/guid.
    """
    guid_segment = f", guid: {guid}, type: 2" if guid else ""
    return (
        "%YAML 1.1\n"
        "%TAG !u! tag:unity3d.com,2011:\n"
        "--- !u!1 &100000\n"
        "GameObject:\n"
        "  m_ObjectHideFlags: 0\n"
        "  m_CorrespondingSourceObject: {fileID: 0}\n"
        "  m_PrefabInstance: {fileID: 0}\n"
        "  m_PrefabAsset: {fileID: 0}\n"
        "  serializedVersion: 6\n"
        "  m_Component:\n"
        "  - component: {fileID: 200000}\n"
        "  m_Name: TestObj\n"
        "--- !u!114 &200000\n"
        "MonoBehaviour:\n"
        "  m_ObjectHideFlags: 0\n"
        "  m_CorrespondingSourceObject: {fileID: 0}\n"
        "  m_PrefabInstance: {fileID: 0}\n"
        "  m_PrefabAsset: {fileID: 0}\n"
        "  m_GameObject: {fileID: 100000}\n"
        "  m_Enabled: 1\n"
        "  m_EditorHideFlags: 0\n"
        "  m_Script: {fileID: 11500000, guid: aabbccddeeff00112233445566778899, type: 3}\n"
        "  m_Name: TestComponent\n"
        "  m_EditorClassIdentifier: \n"
        f"  targetRef: {{fileID: {file_id}{guid_segment}}}\n"
    )


class TestNullFieldClassification(unittest.TestCase):
    """``analyze_wiring`` classifies every null reference field by
    cause and surfaces the classification list on the component.
    """

    def test_variant_overridden_null_category_is_selected(self) -> None:
        # Field is null (fileID:0, no GUID) AND the override map names
        # the field as overridden on this component.  Variant override
        # takes precedence over the unwired default.
        text = _yaml_with_field("0", "")
        overrides = {"200000": {"targetRef"}}

        result = analyze_wiring(
            text, "Assets/Foo.prefab", override_map=overrides,
        )

        self.assertEqual(1, len(result.components))
        comp = result.components[0]
        self.assertEqual(1, len(comp.null_field_classifications))
        entry = comp.null_field_classifications[0]
        self.assertIsInstance(entry, NullFieldClassification)
        self.assertEqual("targetRef", entry.name)
        self.assertEqual("variant_overridden_null", entry.kind)
        # Evidence string names the override-map signal so a reader
        # can confirm the category without re-running the analyzer.
        self.assertIn("override", entry.evidence)

    def test_unwired_category_is_selected_when_no_signal_is_present(self) -> None:
        # Field is null (fileID:0, no GUID) AND the override map is
        # empty — the field was never wired in this Variant.
        text = _yaml_with_field("0", "")

        result = analyze_wiring(
            text, "Assets/Foo.prefab", override_map={},
        )

        comp = result.components[0]
        self.assertEqual(1, len(comp.null_field_classifications))
        entry = comp.null_field_classifications[0]
        self.assertEqual("targetRef", entry.name)
        self.assertEqual("unwired", entry.kind)

    def test_dangling_category_is_selected_for_unresolved_external_guid(self) -> None:
        # Field has fileID:0 but a non-empty external GUID — the
        # reference target was deleted or moved out of scope.
        text = _yaml_with_field("0", "1234567890abcdef1234567890abcdef")

        result = analyze_wiring(
            text, "Assets/Foo.prefab", override_map={},
        )

        comp = result.components[0]
        self.assertEqual(1, len(comp.null_field_classifications))
        entry = comp.null_field_classifications[0]
        self.assertEqual("targetRef", entry.name)
        self.assertEqual("dangling", entry.kind)
        self.assertIn("1234567890abcdef1234567890abcdef", entry.evidence)


class TestLegacyNullFieldSurface(unittest.TestCase):
    """The legacy ``null_field_names`` flat list and ``null_ratio``
    string survive the classification addition for back-compat.
    """

    def test_legacy_flat_list_and_null_ratio_still_present(self) -> None:
        text = _yaml_with_field("0", "")
        overrides = {"200000": {"targetRef"}}

        result = analyze_wiring(
            text, "Assets/Foo.prefab", override_map=overrides,
        )

        comp = result.components[0]
        # Flat list still carries the null field name.
        self.assertEqual(["targetRef"], comp.null_field_names)
        # Fixture defines exactly one non-skipped serialized field
        # (`targetRef`); pinning the count keeps the legacy ratio
        # arithmetic deterministic.
        self.assertEqual(
            1,
            len(comp.fields),
            "exactly one non-skipped field in fixture",
        )


class TestWiringResponseSerialization(unittest.TestCase):
    """``_component_to_dict`` surfaces ``null_field_classifications``
    on the response payload alongside the legacy ``null_field_names``.
    """

    def test_serializer_carries_classification_list_under_documented_key(
        self,
    ) -> None:
        text = _yaml_with_field("0", "")
        overrides = {"200000": {"targetRef"}}
        result = analyze_wiring(
            text, "Assets/Foo.prefab", override_map=overrides,
        )
        comp = result.components[0]

        wire = _component_to_dict(comp, "TestObj", guid_to_name={})

        # Legacy flat list preserved.
        self.assertEqual(["targetRef"], wire["null_field_names"])
        # Spec Expected Observable: existing keys (`null_ratio` etc.)
        # retain their values. Fixture has exactly one non-skipped
        # field (`targetRef`), one of which is null, so the ratio
        # string is `"1/1"`.
        self.assertEqual(
            "1/1",
            wire["null_ratio"],
            f"null_ratio must be '1/1' for one null field out of one "
            f"total; got {wire['null_ratio']!r}",
        )
        # New classification list under the documented key, three-key entries.
        classifications = wire["null_field_classifications"]
        self.assertEqual(1, len(classifications))
        entry = classifications[0]
        self.assertEqual(
            {"name", "kind", "evidence"},
            set(entry.keys()),
        )
        self.assertEqual("targetRef", entry["name"])
        self.assertEqual("variant_overridden_null", entry["kind"])


if __name__ == "__main__":
    unittest.main()
