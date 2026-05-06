from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from prefab_sentinel.contracts import Severity
from prefab_sentinel.patch_revert import _collect_referenced_guids, revert_overrides
from prefab_sentinel.services.prefab_variant.overrides import parse_overrides
from tests.bridge_test_helpers import write_file

BASE_GUID = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
VARIANT_GUID = "cccccccccccccccccccccccccccccccc"


def _create_variant_project(root: Path) -> None:
    """Create a minimal project with a base prefab and a variant."""
    write_file(
        root / "Assets" / "Base.prefab",
        """%YAML 1.1
--- !u!1 &100100000
GameObject:
  m_Name: Base
--- !u!137 &3430728864525902586
SkinnedMeshRenderer:
  m_GameObject: {fileID: 100100000}
  m_Materials:
  - {fileID: 2100000, guid: 11111111111111111111111111111111, type: 2}
  - {fileID: 2100000, guid: 22222222222222222222222222222222, type: 2}
""",
    )
    write_file(
        root / "Assets" / "Base.prefab.meta",
        f"""fileFormatVersion: 2
guid: {BASE_GUID}
""",
    )
    write_file(
        root / "Assets" / "Variant.prefab",
        f"""%YAML 1.1
--- !u!1001 &100100000
PrefabInstance:
  m_SourcePrefab: {{fileID: 100100000, guid: {BASE_GUID}, type: 3}}
  m_Modification:
    m_Modifications:
    - target: {{fileID: 3430728864525902586, guid: {BASE_GUID}, type: 3}}
      propertyPath: m_Materials.Array.data[0]
      value:
      objectReference: {{fileID: 2100000, guid: aaaaaaaabbbbbbbbccccccccdddddddd, type: 2}}
    - target: {{fileID: 3430728864525902586, guid: {BASE_GUID}, type: 3}}
      propertyPath: m_Materials.Array.data[1]
      value:
      objectReference: {{fileID: 2100000, guid: eeeeeeeeffffffffffaaaaaabbbbbbbb, type: 2}}
    - target: {{fileID: 100100000, guid: {BASE_GUID}, type: 3}}
      propertyPath: m_Name
      value: VariantName
      objectReference: {{fileID: 0}}
""",
    )
    write_file(
        root / "Assets" / "Variant.prefab.meta",
        f"""fileFormatVersion: 2
guid: {VARIANT_GUID}
""",
    )


class PatchRevertTests(unittest.TestCase):
    """Test the patch_revert module."""

    def test_dry_run_shows_matches(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            _create_variant_project(root)

            response = revert_overrides(
                variant_path="Assets/Variant.prefab",
                target_file_id="3430728864525902586",
                property_path="m_Materials.Array.data[0]",
                dry_run=True,
                confirm=False,
                change_reason=None,
                project_root=root,
            )

        self.assertTrue(response.success)
        self.assertEqual("REVERT_DRY_RUN", response.code)
        self.assertEqual(1, response.data["match_count"])
        self.assertTrue(response.data["read_only"])
        # Check that the match info includes the current value
        match = response.data["matches"][0]
        self.assertEqual("3430728864525902586", match["target_file_id"])
        self.assertEqual("m_Materials.Array.data[0]", match["property_path"])

    def test_dry_run_no_match(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            _create_variant_project(root)

            response = revert_overrides(
                variant_path="Assets/Variant.prefab",
                target_file_id="9999999999",
                property_path="m_Materials.Array.data[0]",
                dry_run=True,
                confirm=False,
                change_reason=None,
                project_root=root,
            )

        self.assertFalse(response.success)
        self.assertEqual("REVERT_NO_MATCH", response.code)
        self.assertEqual(0, response.data["match_count"])

    def test_confirm_without_flag_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            _create_variant_project(root)

            response = revert_overrides(
                variant_path="Assets/Variant.prefab",
                target_file_id="3430728864525902586",
                property_path="m_Materials.Array.data[0]",
                dry_run=False,
                confirm=False,
                change_reason=None,
                project_root=root,
            )

        self.assertFalse(response.success)
        self.assertEqual("REVERT_NOT_CONFIRMED", response.code)

    def test_confirm_removes_override(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            _create_variant_project(root)

            variant_path = root / "Assets" / "Variant.prefab"
            original_text = variant_path.read_text(encoding="utf-8")
            self.assertIn("m_Materials.Array.data[0]", original_text)

            response = revert_overrides(
                variant_path="Assets/Variant.prefab",
                target_file_id="3430728864525902586",
                property_path="m_Materials.Array.data[0]",
                dry_run=False,
                confirm=True,
                change_reason="Revert accidental material change",
                project_root=root,
            )

            self.assertTrue(response.success)
            self.assertEqual("REVERT_APPLIED", response.code)
            self.assertEqual(1, response.data["match_count"])
            self.assertFalse(response.data["read_only"])
            self.assertTrue(response.data["executed"])
            self.assertEqual(
                "Revert accidental material change", response.data["change_reason"]
            )

            # Verify the file was actually modified
            new_text = variant_path.read_text(encoding="utf-8")
            self.assertNotIn("m_Materials.Array.data[0]", new_text)
            # The other overrides should still be present
            self.assertIn("m_Materials.Array.data[1]", new_text)
            self.assertIn("m_Name", new_text)

    def test_confirm_removes_only_matching_override(self) -> None:
        """Removing one material slot override should leave others intact."""
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            _create_variant_project(root)

            variant_path = root / "Assets" / "Variant.prefab"

            response = revert_overrides(
                variant_path="Assets/Variant.prefab",
                target_file_id="3430728864525902586",
                property_path="m_Materials.Array.data[1]",
                dry_run=False,
                confirm=True,
                change_reason="Revert slot 1",
                project_root=root,
            )

            self.assertTrue(response.success)
            self.assertEqual("REVERT_APPLIED", response.code)

            new_text = variant_path.read_text(encoding="utf-8")
            # data[0] should remain, data[1] should be gone
            self.assertIn("m_Materials.Array.data[0]", new_text)
            self.assertNotIn("m_Materials.Array.data[1]", new_text)
            self.assertIn("m_Name", new_text)

    def test_confirm_preserves_yaml_structure(self) -> None:
        """After revert, the YAML should still be valid Unity YAML."""
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            _create_variant_project(root)

            variant_path = root / "Assets" / "Variant.prefab"

            revert_overrides(
                variant_path="Assets/Variant.prefab",
                target_file_id="3430728864525902586",
                property_path="m_Materials.Array.data[0]",
                dry_run=False,
                confirm=True,
                change_reason="Test",
                project_root=root,
            )

            new_text = variant_path.read_text(encoding="utf-8")
            # File should still have the YAML header
            self.assertTrue(new_text.startswith("%YAML 1.1"))
            # m_Modifications block should still exist
            self.assertIn("m_Modifications:", new_text)
            # PrefabInstance should still be intact
            self.assertIn("PrefabInstance:", new_text)
            self.assertIn("m_SourcePrefab:", new_text)

    def test_missing_variant_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "Assets").mkdir(parents=True)

            response = revert_overrides(
                variant_path="Assets/Missing.prefab",
                target_file_id="123",
                property_path="m_Name",
                dry_run=True,
                confirm=False,
                change_reason=None,
                project_root=root,
            )

        self.assertFalse(response.success)
        self.assertEqual("REVERT_TARGET_NOT_FOUND", response.code)

    def test_revert_last_override_leaves_empty_modifications(self) -> None:
        """When all overrides for a target are reverted, m_Modifications should still be valid."""
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
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
                f"""fileFormatVersion: 2
guid: {BASE_GUID}
""",
            )
            write_file(
                root / "Assets" / "Single.prefab",
                f"""%YAML 1.1
--- !u!1001 &100100000
PrefabInstance:
  m_SourcePrefab: {{fileID: 100100000, guid: {BASE_GUID}, type: 3}}
  m_Modification:
    m_Modifications:
    - target: {{fileID: 100100000, guid: {BASE_GUID}, type: 3}}
      propertyPath: m_Name
      value: OnlyOverride
      objectReference: {{fileID: 0}}
""",
            )
            write_file(
                root / "Assets" / "Single.prefab.meta",
                f"""fileFormatVersion: 2
guid: {VARIANT_GUID}
""",
            )

            variant_path = root / "Assets" / "Single.prefab"

            response = revert_overrides(
                variant_path="Assets/Single.prefab",
                target_file_id="100100000",
                property_path="m_Name",
                dry_run=False,
                confirm=True,
                change_reason="Revert only override",
                project_root=root,
            )

            self.assertTrue(response.success)
            new_text = variant_path.read_text(encoding="utf-8")
            # The file should still be valid but m_Modifications should have no entries
            self.assertIn("m_Modifications:", new_text)
            # m_Name should not appear in the modifications section
            after_mods = new_text.split("m_Modifications:")[1]
            self.assertNotIn("propertyPath: m_Name", after_mods)


class PatchRevertDuplicateOverrideTests(unittest.TestCase):
    """Test revert with duplicate overrides (same target+property appearing multiple times)."""

    def test_removes_all_duplicate_overrides(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
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
                f"""fileFormatVersion: 2
guid: {BASE_GUID}
""",
            )
            write_file(
                root / "Assets" / "Dup.prefab",
                f"""%YAML 1.1
--- !u!1001 &100100000
PrefabInstance:
  m_SourcePrefab: {{fileID: 100100000, guid: {BASE_GUID}, type: 3}}
  m_Modification:
    m_Modifications:
    - target: {{fileID: 100100000, guid: {BASE_GUID}, type: 3}}
      propertyPath: duplicated.path
      value: first
      objectReference: {{fileID: 0}}
    - target: {{fileID: 100100000, guid: {BASE_GUID}, type: 3}}
      propertyPath: duplicated.path
      value: second
      objectReference: {{fileID: 0}}
    - target: {{fileID: 100100000, guid: {BASE_GUID}, type: 3}}
      propertyPath: other.path
      value: keep
      objectReference: {{fileID: 0}}
""",
            )
            write_file(
                root / "Assets" / "Dup.prefab.meta",
                f"""fileFormatVersion: 2
guid: {VARIANT_GUID}
""",
            )

            variant_path = root / "Assets" / "Dup.prefab"

            response = revert_overrides(
                variant_path="Assets/Dup.prefab",
                target_file_id="100100000",
                property_path="duplicated.path",
                dry_run=False,
                confirm=True,
                change_reason="Remove all duplicates",
                project_root=root,
            )

            self.assertTrue(response.success)
            self.assertEqual(2, response.data["match_count"])

            new_text = variant_path.read_text(encoding="utf-8")
            self.assertNotIn("duplicated.path", new_text)
            self.assertIn("other.path", new_text)


class MissingGuidContractTests(unittest.TestCase):
    """T23: ``revert_overrides`` must fail-fast with ``REF001`` when any referenced
    GUID is not present in the project (issue #83 contract)."""

    MISSING_GUID = "ffffffffffffffffffffffffffffffff"

    def _create_project_with_missing_source(self, root: Path) -> Path:
        """Create a variant whose m_SourcePrefab GUID is not in the project.

        Returns the absolute path to the variant file.
        """
        # Base prefab exists in the project but the variant refers to a
        # completely different GUID for m_SourcePrefab that has no meta.
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
            f"""fileFormatVersion: 2
guid: {BASE_GUID}
""",
        )
        write_file(
            root / "Assets" / "OrphanVariant.prefab",
            f"""%YAML 1.1
--- !u!1001 &100100000
PrefabInstance:
  m_SourcePrefab: {{fileID: 100100000, guid: {self.MISSING_GUID}, type: 3}}
  m_Modification:
    m_Modifications:
    - target: {{fileID: 100100000, guid: {self.MISSING_GUID}, type: 3}}
      propertyPath: m_Name
      value: Renamed
      objectReference: {{fileID: 0}}
""",
        )
        write_file(
            root / "Assets" / "OrphanVariant.prefab.meta",
            f"""fileFormatVersion: 2
guid: {VARIANT_GUID}
""",
        )
        return root / "Assets" / "OrphanVariant.prefab"

    def test_revert_overrides_aborts_on_missing_guid(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            variant_path = self._create_project_with_missing_source(root)
            original_text = variant_path.read_text(encoding="utf-8")
            original_mtime = variant_path.stat().st_mtime_ns

            response = revert_overrides(
                variant_path="Assets/OrphanVariant.prefab",
                target_file_id="100100000",
                property_path="m_Name",
                dry_run=False,
                confirm=True,
                change_reason="Attempt to revert on orphan variant",
                project_root=root,
            )

            self.assertFalse(response.success)
            self.assertEqual("REF001", response.code)
            self.assertEqual("error", response.severity.value)
            self.assertIn(self.MISSING_GUID, response.data["missing_guids"])
            # No YAML mutation on the variant.
            self.assertEqual(original_text, variant_path.read_text(encoding="utf-8"))
            self.assertEqual(original_mtime, variant_path.stat().st_mtime_ns)

    def test_dry_run_also_aborts_on_missing_guid(self) -> None:
        """Dry-run must also reject when any referenced GUID is missing."""
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self._create_project_with_missing_source(root)

            response = revert_overrides(
                variant_path="Assets/OrphanVariant.prefab",
                target_file_id="100100000",
                property_path="m_Name",
                dry_run=True,
                confirm=False,
                change_reason=None,
                project_root=root,
            )

            self.assertFalse(response.success)
            self.assertEqual("REF001", response.code)


class TestRevertChangeReasonRequired(unittest.TestCase):
    """confirm=True without change_reason must be rejected (audit-log contract)."""

    def test_confirm_without_change_reason_returns_change_reason_required(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            _create_variant_project(root)

            response = revert_overrides(
                variant_path="Assets/Variant.prefab",
                target_file_id="3430728864525902586",
                property_path="m_Materials.Array.data[0]",
                dry_run=False,
                confirm=True,
                change_reason=None,
                project_root=root,
            )

            self.assertFalse(response.success)
            self.assertEqual("CHANGE_REASON_REQUIRED", response.code)
            self.assertEqual(False, response.data["executed"])
            self.assertEqual(True, response.data["read_only"])

    def test_confirm_with_blank_change_reason_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            _create_variant_project(root)

            response = revert_overrides(
                variant_path="Assets/Variant.prefab",
                target_file_id="3430728864525902586",
                property_path="m_Materials.Array.data[0]",
                dry_run=False,
                confirm=True,
                change_reason="   ",
                project_root=root,
            )

            self.assertFalse(response.success)
            self.assertEqual("CHANGE_REASON_REQUIRED", response.code)


class PatchRevertEnvelopeTests(unittest.TestCase):
    """Issue #147 — pin every revert envelope code by value, including the
    write-error and reference-error paths, plus the referenced-guid
    collector deduplication and lower-casing.
    """

    def _create_minimal_variant(self, root: Path) -> Path:
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
            f"fileFormatVersion: 2\nguid: {BASE_GUID}\n",
        )
        variant = root / "Assets" / "Variant.prefab"
        write_file(
            variant,
            f"""%YAML 1.1
--- !u!1001 &100100000
PrefabInstance:
  m_SourcePrefab: {{fileID: 100100000, guid: {BASE_GUID}, type: 3}}
  m_Modification:
    m_Modifications:
    - target: {{fileID: 100100000, guid: {BASE_GUID}, type: 3}}
      propertyPath: m_Name
      value: VariantName
      objectReference: {{fileID: 0}}
""",
        )
        write_file(
            root / "Assets" / "Variant.prefab.meta",
            f"fileFormatVersion: 2\nguid: {VARIANT_GUID}\n",
        )
        return variant

    def test_target_not_found_envelope(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            (root / "Assets").mkdir(parents=True)
            response = revert_overrides(
                variant_path="Assets/Missing.prefab",
                target_file_id="1",
                property_path="m_Name",
                dry_run=True,
                confirm=False,
                change_reason=None,
                project_root=root,
            )
        self.assertFalse(response.success)
        self.assertEqual("REVERT_TARGET_NOT_FOUND", response.code)
        self.assertEqual(True, response.data["read_only"])

    def test_read_error_envelope(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            assets = root / "Assets"
            assets.mkdir(parents=True)
            (assets / "Bin.prefab").write_bytes(b"\xff\xfe garbage")
            (assets / "Bin.prefab.meta").write_text(
                f"fileFormatVersion: 2\nguid: {VARIANT_GUID}\n",
                encoding="utf-8",
            )
            response = revert_overrides(
                variant_path="Assets/Bin.prefab",
                target_file_id="1",
                property_path="m_Name",
                dry_run=True,
                confirm=False,
                change_reason=None,
                project_root=root,
            )
        self.assertFalse(response.success)
        self.assertEqual("REVERT_READ_ERROR", response.code)
        self.assertEqual(True, response.data["read_only"])

    def test_reference_error_envelope_lists_missing_guids(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            missing = "ff" * 16
            assets = root / "Assets"
            assets.mkdir(parents=True)
            write_file(
                assets / "OrphanVariant.prefab",
                f"""%YAML 1.1
--- !u!1001 &100100000
PrefabInstance:
  m_SourcePrefab: {{fileID: 100100000, guid: {missing}, type: 3}}
  m_Modification:
    m_Modifications:
    - target: {{fileID: 100100000, guid: {missing}, type: 3}}
      propertyPath: m_Name
      value: V
      objectReference: {{fileID: 0}}
""",
            )
            write_file(
                assets / "OrphanVariant.prefab.meta",
                f"fileFormatVersion: 2\nguid: {VARIANT_GUID}\n",
            )
            response = revert_overrides(
                variant_path="Assets/OrphanVariant.prefab",
                target_file_id="100100000",
                property_path="m_Name",
                dry_run=True,
                confirm=False,
                change_reason=None,
                project_root=root,
            )
        self.assertFalse(response.success)
        self.assertEqual("REF001", response.code)
        self.assertIn(missing, response.data["missing_guids"])
        self.assertEqual(True, response.data["read_only"])
        self.assertEqual(False, response.data["executed"])

    def test_no_match_envelope_warning_severity(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            self._create_minimal_variant(root)
            response = revert_overrides(
                variant_path="Assets/Variant.prefab",
                target_file_id="9999",
                property_path="m_Name",
                dry_run=True,
                confirm=False,
                change_reason=None,
                project_root=root,
            )
        self.assertFalse(response.success)
        self.assertEqual("REVERT_NO_MATCH", response.code)
        self.assertEqual(Severity.WARNING, response.severity)
        self.assertEqual(0, response.data["match_count"])

    def test_dry_run_envelope_carries_match_count(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            self._create_minimal_variant(root)
            response = revert_overrides(
                variant_path="Assets/Variant.prefab",
                target_file_id="100100000",
                property_path="m_Name",
                dry_run=True,
                confirm=False,
                change_reason=None,
                project_root=root,
            )
        self.assertTrue(response.success)
        self.assertEqual("REVERT_DRY_RUN", response.code)
        self.assertEqual(1, response.data["match_count"])
        self.assertEqual(1, len(response.data["matches"]))

    def test_not_confirmed_envelope_warning_severity(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            self._create_minimal_variant(root)
            response = revert_overrides(
                variant_path="Assets/Variant.prefab",
                target_file_id="100100000",
                property_path="m_Name",
                dry_run=False,
                confirm=False,
                change_reason=None,
                project_root=root,
            )
        self.assertFalse(response.success)
        self.assertEqual("REVERT_NOT_CONFIRMED", response.code)
        self.assertEqual(Severity.WARNING, response.severity)

    def test_change_reason_required_envelope(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            self._create_minimal_variant(root)
            response = revert_overrides(
                variant_path="Assets/Variant.prefab",
                target_file_id="100100000",
                property_path="m_Name",
                dry_run=False,
                confirm=True,
                change_reason="",
                project_root=root,
            )
        self.assertFalse(response.success)
        self.assertEqual("CHANGE_REASON_REQUIRED", response.code)
        self.assertEqual(True, response.data["read_only"])

    def test_write_error_envelope_when_write_raises_os_error(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            self._create_minimal_variant(root)
            with patch.object(Path, "write_text", side_effect=OSError("disk full")):
                response = revert_overrides(
                    variant_path="Assets/Variant.prefab",
                    target_file_id="100100000",
                    property_path="m_Name",
                    dry_run=False,
                    confirm=True,
                    change_reason="test",
                    project_root=root,
                )
        self.assertFalse(response.success)
        self.assertEqual("REVERT_WRITE_ERROR", response.code)
        self.assertEqual(False, response.data["executed"])

    def test_applied_envelope_full_success(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            self._create_minimal_variant(root)
            response = revert_overrides(
                variant_path="Assets/Variant.prefab",
                target_file_id="100100000",
                property_path="m_Name",
                dry_run=False,
                confirm=True,
                change_reason="full success path",
                project_root=root,
            )
        self.assertTrue(response.success)
        self.assertEqual("REVERT_APPLIED", response.code)
        self.assertEqual(1, response.data["match_count"])
        self.assertEqual("full success path", response.data["change_reason"])
        self.assertEqual(False, response.data["read_only"])
        self.assertEqual(True, response.data["executed"])

    def test_referenced_guid_collector_deduplicates_and_lower_cases(self) -> None:
        upper = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
        lower = upper.lower()
        text = f"""m_SourcePrefab: {{fileID: 100, guid: {upper}, type: 3}}
  m_Modification:
    m_Modifications:
    - target: {{fileID: 1, guid: {upper}, type: 3}}
      propertyPath: a
      value: V
      objectReference: {{fileID: 0}}
    - target: {{fileID: 2, guid: {upper}, type: 3}}
      propertyPath: b
      value: V
      objectReference: {{fileID: 0}}
"""
        entries = parse_overrides(text)
        guids = _collect_referenced_guids(text, entries)
        # Single deduplicated, lower-cased entry.
        self.assertEqual([lower], guids)


class PatchRevertAssertStrengthening(unittest.TestCase):
    """Issue #147 — value-pinned post-revert side-effect assertions on the
    confirm path.  Pins:
    * ``REVERT_APPLIED`` envelope code with ``executed=True``;
    * ``match_count`` equals the input matched-override count;
    * file mtime advances (the file was actually written);
    * the matched override block is absent from the post-revert text.
    """

    def test_revert_confirm_writes_file_and_pins_match_count(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            _create_variant_project(root)
            variant_path = root / "Assets" / "Variant.prefab"
            original_text = variant_path.read_text(encoding="utf-8")
            original_mtime = variant_path.stat().st_mtime_ns
            # Ensure the next write produces a strictly different mtime.
            import os
            future = original_mtime + 10_000_000_000  # +10 seconds
            os.utime(variant_path, ns=(future, future))
            baseline_mtime = variant_path.stat().st_mtime_ns

            response = revert_overrides(
                variant_path="Assets/Variant.prefab",
                target_file_id="3430728864525902586",
                property_path="m_Materials.Array.data[0]",
                dry_run=False,
                confirm=True,
                change_reason="strengthening row",
                project_root=root,
            )
            new_text = variant_path.read_text(encoding="utf-8")
            new_mtime = variant_path.stat().st_mtime_ns

        self.assertTrue(response.success)
        self.assertEqual("REVERT_APPLIED", response.code)
        self.assertEqual(1, response.data["match_count"])
        self.assertEqual(True, response.data["executed"])
        # mtime advanced — write happened.
        self.assertNotEqual(baseline_mtime, new_mtime)
        # The matched override line is absent from the post-revert content.
        self.assertIn("m_Materials.Array.data[0]", original_text)
        self.assertNotIn("m_Materials.Array.data[0]", new_text)


if __name__ == "__main__":
    unittest.main()
