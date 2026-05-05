"""Prefab-variant package tests: import surface plus override response shape.

Two responsibilities live here per the spec's File Structure:

* The package's import-surface invariant (#75).
* The discriminant rows and existing-keys-preserved row for
  ``PrefabVariantService.list_overrides`` (#172).  Each override entry in
  the response payload must carry the keys ``kind``, ``target_key``,
  ``line``, ``target_file_id``, ``target_guid``, ``property_path``,
  ``value``, ``object_reference``.  ``kind`` is one of four discriminant
  strings:

  * ``array_size`` — the property path ends in ``.Array.size``.
  * ``array_data`` — the property path matches ``*.Array.data[<index>]``.
  * ``object_reference`` — the entry carries a non-empty, non-zero
    ``objectReference`` value.
  * ``value`` — every other case, including an explicit ``{fileID: 0}``
    reference and an entry without an ``objectReference`` field.

  ``target_key`` mirrors the dataclass property: the GUID-and-fileID
  composite ``"<guid>:<fileID>"``.

The line-count invariant for files under this package is enforced by the
CI-side static gate ``scripts/check_module_line_limits.py``.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import prefab_sentinel.services.prefab_variant as prefab_variant_pkg
from prefab_sentinel.services.prefab_variant import PrefabVariantService
from prefab_sentinel.services.prefab_variant.overrides import (
    OverrideEntry,
    effective_value,
    find_modification_line_ranges,
    iter_base_property_values,
    parse_overrides,
)
from prefab_sentinel.services.prefab_variant.service import (
    PrefabVariantService as _ServicePrefabVariantService,
)
from tests._assertion_helpers import assert_error_envelope
from tests.bridge_test_helpers import write_file

_BASE_GUID = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
_VARIANT_GUID = "cccccccccccccccccccccccccccccccc"
_REF_GUID = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"

_REQUIRED_KEYS = {
    "kind",
    "target_key",
    "line",
    "target_file_id",
    "target_guid",
    "property_path",
    "value",
    "object_reference",
}


def _write_base(root: Path) -> None:
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
        f"fileFormatVersion: 2\nguid: {_BASE_GUID}\n",
    )


def _write_variant(root: Path, modifications: str) -> str:
    _write_base(root)
    write_file(
        root / "Assets" / "Variant.prefab",
        f"""%YAML 1.1
--- !u!1001 &100100000
PrefabInstance:
  m_SourcePrefab: {{fileID: 100100000, guid: {_BASE_GUID}, type: 3}}
  m_Modification:
    m_Modifications:
{modifications}""",
    )
    write_file(
        root / "Assets" / "Variant.prefab.meta",
        f"fileFormatVersion: 2\nguid: {_VARIANT_GUID}\n",
    )
    return "Assets/Variant.prefab"


class PrefabVariantPackageImportTests(unittest.TestCase):
    def test_public_surface_preserved(self) -> None:
        """Legacy import path ``from prefab_sentinel.services.prefab_variant import PrefabVariantService`` resolves to the post-split implementation."""
        self.assertIs(PrefabVariantService, _ServicePrefabVariantService)
        self.assertIn("PrefabVariantService", prefab_variant_pkg.__all__)


class OverrideEntryDiscriminantTests(unittest.TestCase):
    """Pure unit tests on the dataclass property's classification rules."""

    def _entry(
        self,
        property_path: str,
        object_reference: str = "",
    ) -> OverrideEntry:
        return OverrideEntry(
            target_file_id="100100000",
            target_guid=_BASE_GUID,
            target_type="3",
            target_raw="",
            property_path=property_path,
            value="",
            object_reference=object_reference,
            line=1,
        )

    def test_array_size_path_returns_array_size(self) -> None:
        entry = self._entry("items.Array.size")
        self.assertEqual("array_size", entry.kind)

    def test_array_data_path_returns_array_data(self) -> None:
        entry = self._entry("items.Array.data[3]")
        self.assertEqual("array_data", entry.kind)

    def test_non_empty_non_zero_object_reference_returns_object_reference(self) -> None:
        entry = self._entry(
            "m_Sprite",
            object_reference=f"{{fileID: 21300000, guid: {_REF_GUID}, type: 3}}",
        )
        self.assertEqual("object_reference", entry.kind)

    def test_explicit_zero_object_reference_returns_value(self) -> None:
        entry = self._entry("m_Name", object_reference="{fileID: 0}")
        self.assertEqual("value", entry.kind)

    def test_no_object_reference_returns_value(self) -> None:
        entry = self._entry("m_Name", object_reference="")
        self.assertEqual("value", entry.kind)

    def test_target_key_is_guid_colon_fileid_composite(self) -> None:
        entry = self._entry("m_Name")
        self.assertEqual(f"{_BASE_GUID}:100100000", entry.target_key)


class ListOverridesResponseShapeTests(unittest.TestCase):
    """End-to-end pinning of the override entry payload returned by ``list_overrides``."""

    def _list_overrides_payload(self, modifications: str) -> list[dict]:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            target = _write_variant(root, modifications)
            svc = PrefabVariantService(project_root=root)
            response = svc.list_overrides(target)
        self.assertTrue(response.success, response)
        return response.data["overrides"]

    def test_array_size_entry_carries_array_size_kind(self) -> None:
        payload = self._list_overrides_payload(
            f"""    - target: {{fileID: 100100000, guid: {_BASE_GUID}, type: 3}}
      propertyPath: items.Array.size
      value: 4
      objectReference: {{fileID: 0}}
"""
        )
        self.assertEqual(1, len(payload))
        self.assertEqual("array_size", payload[0]["kind"])
        self.assertEqual(f"{_BASE_GUID}:100100000", payload[0]["target_key"])

    def test_array_data_entry_carries_array_data_kind(self) -> None:
        payload = self._list_overrides_payload(
            f"""    - target: {{fileID: 100100000, guid: {_BASE_GUID}, type: 3}}
      propertyPath: items.Array.data[2]
      value: hello
      objectReference: {{fileID: 0}}
"""
        )
        self.assertEqual("array_data", payload[0]["kind"])

    def test_object_reference_entry_carries_object_reference_kind(self) -> None:
        payload = self._list_overrides_payload(
            f"""    - target: {{fileID: 100100000, guid: {_BASE_GUID}, type: 3}}
      propertyPath: m_Sprite
      value:
      objectReference: {{fileID: 21300000, guid: {_REF_GUID}, type: 3}}
"""
        )
        self.assertEqual("object_reference", payload[0]["kind"])

    def test_value_entry_with_explicit_zero_reference_carries_value_kind(self) -> None:
        payload = self._list_overrides_payload(
            f"""    - target: {{fileID: 100100000, guid: {_BASE_GUID}, type: 3}}
      propertyPath: m_Name
      value: NewName
      objectReference: {{fileID: 0}}
"""
        )
        self.assertEqual("value", payload[0]["kind"])

    def test_value_entry_with_no_reference_field_carries_value_kind(self) -> None:
        payload = self._list_overrides_payload(
            f"""    - target: {{fileID: 100100000, guid: {_BASE_GUID}, type: 3}}
      propertyPath: m_Name
      value: NewName
"""
        )
        self.assertEqual("value", payload[0]["kind"])

    def test_existing_keys_preserved_across_entries(self) -> None:
        """Regression: every documented key remains on each entry."""
        payload = self._list_overrides_payload(
            f"""    - target: {{fileID: 100100000, guid: {_BASE_GUID}, type: 3}}
      propertyPath: items.Array.size
      value: 1
      objectReference: {{fileID: 0}}
    - target: {{fileID: 100100000, guid: {_BASE_GUID}, type: 3}}
      propertyPath: items.Array.data[0]
      value: alpha
      objectReference: {{fileID: 0}}
    - target: {{fileID: 100100000, guid: {_BASE_GUID}, type: 3}}
      propertyPath: m_Sprite
      value:
      objectReference: {{fileID: 21300000, guid: {_REF_GUID}, type: 3}}
    - target: {{fileID: 100100000, guid: {_BASE_GUID}, type: 3}}
      propertyPath: m_Name
      value: Renamed
      objectReference: {{fileID: 0}}
"""
        )
        kinds = {entry["kind"] for entry in payload}
        self.assertEqual(
            {"array_size", "array_data", "object_reference", "value"},
            kinds,
        )
        for entry in payload:
            self.assertEqual(_REQUIRED_KEYS, set(entry.keys()))
            self.assertEqual(f"{_BASE_GUID}:100100000", entry["target_key"])


class ListOverridesErrorPathTests(unittest.TestCase):
    """Error-envelope regressions: the additive payload keys are present
    only on success envelopes; the documented error codes remain.
    """

    def test_missing_variant_path_returns_pvr404(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            (root / "Assets").mkdir(parents=True, exist_ok=True)
            svc = PrefabVariantService(project_root=root)
            response = svc.list_overrides("Assets/DoesNotExist.prefab")
        assert_error_envelope(
            response,
            code="PVR404",
            severity="error",
            data={
                "variant_path": "Assets/DoesNotExist.prefab",
                "read_only": True,
            },
        )

    def test_undecodable_variant_returns_pvr400(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            (root / "Assets").mkdir(parents=True, exist_ok=True)
            variant_path = root / "Assets" / "Binary.prefab"
            variant_path.write_bytes(b"\xff\xfe\x00\x80\x90 binary garbage")
            (root / "Assets" / "Binary.prefab.meta").write_text(
                f"fileFormatVersion: 2\nguid: {_VARIANT_GUID}\n",
                encoding="utf-8",
            )
            svc = PrefabVariantService(project_root=root)
            response = svc.list_overrides("Assets/Binary.prefab")
        assert_error_envelope(
            response,
            code="PVR400",
            severity="error",
            data={
                "variant_path": "Assets/Binary.prefab",
                "read_only": True,
            },
        )


class OverridesParserTests(unittest.TestCase):
    """Issue #187 — pin parser, line-range, effective-value, and base-property
    iterator behaviour by value so mutations on the parsing primitives cannot
    survive.
    """

    def _entry(
        self,
        property_path: str = "",
        object_reference: str = "",
        value: str = "",
    ) -> OverrideEntry:
        return OverrideEntry(
            target_file_id="100100000",
            target_guid=_BASE_GUID,
            target_type="3",
            target_raw="",
            property_path=property_path,
            value=value,
            object_reference=object_reference,
            line=1,
        )

    # --- effective_value branches ------------------------------------------

    def test_effective_value_uses_reference_when_non_zero(self) -> None:
        entry = self._entry(
            object_reference=f"{{fileID: 21300000, guid: {_REF_GUID}, type: 3}}",
            value="ignored",
        )
        self.assertEqual(
            f"{{fileID: 21300000, guid: {_REF_GUID}, type: 3}}",
            effective_value(entry),
        )

    def test_effective_value_falls_back_to_value_when_reference_is_zero(
        self,
    ) -> None:
        entry = self._entry(object_reference="{fileID: 0}", value="kept")
        self.assertEqual("kept", effective_value(entry))

    def test_effective_value_falls_back_to_value_when_reference_empty(
        self,
    ) -> None:
        entry = self._entry(object_reference="", value="kept")
        self.assertEqual("kept", effective_value(entry))

    # --- parse_overrides edges ---------------------------------------------

    def test_parser_skips_lines_preceding_modifications_block(self) -> None:
        text = f"""%YAML 1.1
--- !u!1001 &100100000
PrefabInstance:
  m_SourcePrefab: {{fileID: 100100000, guid: {_BASE_GUID}, type: 3}}
  m_Modification:
    m_Modifications:
    - target: {{fileID: 100100000, guid: {_BASE_GUID}, type: 3}}
      propertyPath: m_Name
      value: First
      objectReference: {{fileID: 0}}
"""
        entries = parse_overrides(text)
        self.assertEqual(1, len(entries))
        # The line attribute records the actual ``- target:`` line, not
        # any of the leading non-modification lines.
        self.assertEqual(7, entries[0].line)

    def test_parser_handles_target_with_optional_type_field(self) -> None:
        text = f"""m_Modifications:
- target: {{fileID: 1, guid: {_BASE_GUID}, type: 3}}
  propertyPath: m_Name
  value: V
  objectReference: {{fileID: 0}}
"""
        entries = parse_overrides(text)
        self.assertEqual(1, len(entries))
        self.assertEqual("3", entries[0].target_type)
        self.assertEqual(_BASE_GUID, entries[0].target_guid)
        self.assertEqual("1", entries[0].target_file_id)

    def test_parser_handles_target_without_type_field(self) -> None:
        text = f"""m_Modifications:
- target: {{fileID: 2, guid: {_BASE_GUID}}}
  propertyPath: m_Name
  value: V
  objectReference: {{fileID: 0}}
"""
        entries = parse_overrides(text)
        self.assertEqual(1, len(entries))
        self.assertIsNone(entries[0].target_type)

    def test_parser_handles_target_without_guid_field(self) -> None:
        text = """m_Modifications:
- target: {fileID: 3}
  propertyPath: m_Name
  value: V
  objectReference: {fileID: 0}
"""
        entries = parse_overrides(text)
        self.assertEqual(1, len(entries))
        self.assertEqual("", entries[0].target_guid)
        self.assertEqual("3", entries[0].target_file_id)

    def test_parser_emits_one_entry_per_target_block(self) -> None:
        text = f"""m_Modifications:
- target: {{fileID: 1, guid: {_BASE_GUID}, type: 3}}
  propertyPath: a
  value: 1
  objectReference: {{fileID: 0}}
- target: {{fileID: 2, guid: {_BASE_GUID}, type: 3}}
  propertyPath: b
  value: 2
  objectReference: {{fileID: 0}}
- target: {{fileID: 3, guid: {_BASE_GUID}, type: 3}}
  propertyPath: c
  value: 3
  objectReference: {{fileID: 0}}
"""
        entries = parse_overrides(text)
        self.assertEqual(3, len(entries))
        self.assertEqual(
            ["a", "b", "c"], [e.property_path for e in entries]
        )

    # --- find_modification_line_ranges -------------------------------------

    def test_line_ranges_emit_one_range_per_entry(self) -> None:
        text = f"""  m_Modifications:
  - target: {{fileID: 1, guid: {_BASE_GUID}, type: 3}}
    propertyPath: a
    value: 1
    objectReference: {{fileID: 0}}
  - target: {{fileID: 2, guid: {_BASE_GUID}, type: 3}}
    propertyPath: b
    value: 2
    objectReference: {{fileID: 0}}
"""
        lines = text.splitlines()
        ranges = find_modification_line_ranges(lines)
        self.assertEqual(2, len(ranges))
        # Each range covers exactly the four lines of its entry block.
        for range_start, range_end in ranges.values():
            self.assertEqual(4, range_end - range_start)

    def test_line_ranges_include_trailing_blank_lines_for_last_entry(
        self,
    ) -> None:
        text = f"""  m_Modifications:
  - target: {{fileID: 1, guid: {_BASE_GUID}, type: 3}}
    propertyPath: a
    value: 1
    objectReference: {{fileID: 0}}

"""
        lines = text.splitlines()
        ranges = find_modification_line_ranges(lines)
        self.assertEqual(1, len(ranges))
        # Last entry's range extends across the trailing blank line.
        only_range = next(iter(ranges.values()))
        self.assertEqual(5, only_range[1] - only_range[0])

    # --- iter_base_property_values -----------------------------------------

    def test_iter_base_property_values_emits_array_data_paths_for_lists(
        self,
    ) -> None:
        text = """%YAML 1.1
--- !u!137 &100
SkinnedMeshRenderer:
  m_GameObject: {fileID: 50}
  m_Materials:
  - {fileID: 1}
  - {fileID: 2}
"""
        entries = list(iter_base_property_values(text))
        # Expect the two array-data entries on m_Materials.
        materials = [e for e in entries if "m_Materials.Array.data[" in e[1]]
        self.assertEqual(2, len(materials))
        self.assertEqual("100", materials[0][0])
        self.assertEqual("m_Materials.Array.data[0]", materials[0][1])
        self.assertEqual("m_Materials.Array.data[1]", materials[1][1])

    def test_iter_base_property_values_yields_scalar_field_values(self) -> None:
        text = """%YAML 1.1
--- !u!1 &100
GameObject:
  m_Name: Hello
"""
        entries = list(iter_base_property_values(text))
        scalars = [e for e in entries if e[1] == "m_Name"]
        self.assertEqual(1, len(scalars))
        self.assertEqual("Hello", scalars[0][2])

    def test_iter_base_property_values_skips_empty_array_marker(self) -> None:
        text = """%YAML 1.1
--- !u!137 &100
SkinnedMeshRenderer:
  m_Materials: []
"""
        entries = list(iter_base_property_values(text))
        # Empty-array marker is skipped (no array-data, no scalar).
        materials = [e for e in entries if "m_Materials" in e[1]]
        self.assertEqual([], materials)


if __name__ == "__main__":
    unittest.main()
