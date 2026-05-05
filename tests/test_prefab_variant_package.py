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
from prefab_sentinel.services.prefab_variant.overrides import OverrideEntry
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


if __name__ == "__main__":
    unittest.main()
