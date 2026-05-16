from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from prefab_sentinel.patch_revert import (
    _collect_referenced_guids,
    _remove_lines,
    revert_overrides,
)
from prefab_sentinel.services.prefab_variant.overrides import OverrideEntry, parse_overrides
from tests._assertion_helpers import assert_error_envelope
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

        assert_error_envelope(response, code="REVERT_NO_MATCH", severity="warning")
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

        assert_error_envelope(response, code="REVERT_NOT_CONFIRMED", severity="warning")

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

        assert_error_envelope(response, code="REVERT_TARGET_NOT_FOUND")

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

            assert_error_envelope(response, code="REF001", severity="error")
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

            assert_error_envelope(response, code="REF001")


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

            assert_error_envelope(response, code="CHANGE_REASON_REQUIRED")
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

            assert_error_envelope(response, code="CHANGE_REASON_REQUIRED")


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
        assert_error_envelope(response, code="REVERT_TARGET_NOT_FOUND")
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
        assert_error_envelope(response, code="REVERT_READ_ERROR")
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
        assert_error_envelope(response, code="REF001")
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
        assert_error_envelope(response, code="REVERT_NO_MATCH", severity="warning")
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
        assert_error_envelope(response, code="REVERT_NOT_CONFIRMED", severity="warning")

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
        assert_error_envelope(response, code="CHANGE_REASON_REQUIRED")
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
        assert_error_envelope(response, code="REVERT_WRITE_ERROR")
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


class RemoveLinesAndCollectGuidsValuePinTests(unittest.TestCase):
    """Issue #209 — value-pin coverage for the YAML revert helpers.

    Targets the named survived mutants enumerated in the issue body:

    * ``_remove_lines__mutmut_3`` — ``splitlines(keepends=True)`` flipped
      to ``keepends=False``. Killed by pinning the surviving line endings
      across CRLF, LF, and multi-range inputs by exact-string equality.
    * ``_collect_referenced_guids__mutmut_6`` — ``(guid or "")``
      fallback flipped to ``(guid or "XXXX")``. The two callers of the
      inner ``_maybe_add`` already guard against falsy ``guid`` values
      (``source_match`` only matched a 32-hex string and the entry-loop
      sits behind ``if entry.target_guid:``), so the inner fallback is
      unreachable through the public surface and the mutation is
      recorded as equivalent in ``[tool.mutmut].do_not_mutate``. The
      outer truthy-guard is still pinned by a value-row here so the
      caller-side guard cannot regress without notice.
    * ``_remove_lines__mutmut_6`` — ``key=lambda r: r[0]`` collapsed to
      ``key=None``. ``_remove_lines``'s docstring requires the input
      ranges to be non-overlapping; under that invariant the natural
      tuple ordering on ``(start, end)`` and the first-element ordering
      coincide on every input the public surface accepts. The mutation
      is recorded as equivalent in ``[tool.mutmut].do_not_mutate``.
    """

    def test_remove_lines_preserves_crlf_endings(self) -> None:
        """``mutmut_3`` kill row — CRLF.

        With CRLF endings the surviving line must retain its trailing
        ``\\r\\n``. ``splitlines(keepends=False)`` would drop it and
        ``"".join(...)`` would yield a string without the CR.
        """
        text = "alpha\r\nbravo\r\ncharlie\r\n"
        result = _remove_lines(text, [(1, 2)])
        # Surviving lines: ``alpha\r\n`` (index 0) and ``charlie\r\n``
        # (index 2). The exact string equality pins the line endings.
        self.assertEqual("alpha\r\ncharlie\r\n", result)

    def test_remove_lines_preserves_lf_endings(self) -> None:
        """``mutmut_3`` kill row — LF.

        With LF endings the surviving line must retain its trailing
        ``\\n``. ``splitlines(keepends=False)`` would drop it.
        """
        text = "alpha\nbravo\ncharlie\n"
        result = _remove_lines(text, [(1, 2)])
        self.assertEqual("alpha\ncharlie\n", result)

    def test_remove_lines_multi_range_pins_post_removal_text(self) -> None:
        """``mutmut_3`` kill row — multi-range.

        Two non-overlapping ranges supplied in non-descending order must
        both be removed and the surviving text must equal the exact
        remaining string with line endings preserved.
        """
        text = "L0\nL1\nL2\nL3\nL4\nL5\n"
        # Remove L1..L1 (range [1, 2)) and L3..L4 (range [3, 5))
        result = _remove_lines(text, [(1, 2), (3, 5)])
        self.assertEqual("L0\nL2\nL5\n", result)

    def test_collect_referenced_guids_lowercases_and_dedups_upper_case(
        self,
    ) -> None:
        """``mutmut_6`` (collector) supplementary kill row — direct
        helper-level pin: an upper-case GUID input produces the
        lower-case form once, deduplicated."""
        upper = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
        lower = upper.lower()
        text = f"m_SourcePrefab: {{fileID: 100, guid: {upper}, type: 3}}\n"
        entries: list[OverrideEntry] = [
            OverrideEntry(
                target_file_id="1",
                target_guid=upper,
                target_type="3",
                target_raw="",
                property_path="a",
                value="V",
                object_reference="{fileID: 0}",
                line=1,
            ),
            OverrideEntry(
                target_file_id="2",
                target_guid=upper,
                target_type="3",
                target_raw="",
                property_path="b",
                value="V",
                object_reference="{fileID: 0}",
                line=2,
            ),
        ]
        guids = _collect_referenced_guids(text, entries)
        self.assertEqual([lower], guids)

    def test_collect_referenced_guids_outer_truthy_guard_drops_empty_entries(
        self,
    ) -> None:
        """Outer truthy-guard pin row.

        The caller-side guard ``if entry.target_guid:`` excludes empty
        ``target_guid`` values before they reach the inner
        ``_maybe_add``. With this guard in place an entry list whose
        ``target_guid`` is the empty string contributes no items to the
        accumulator, and an input text whose ``m_SourcePrefab`` pattern
        does not match contributes none either, so the collector
        returns the empty list. This is the value-pin for the outer
        truthy-guard; the inner falsy-fallback substitution is recorded
        as equivalent in ``[tool.mutmut].do_not_mutate`` because no
        public-surface input drives a falsy GUID into the inner
        accumulator.
        """
        text = "no source prefab here\n"
        entries: list[OverrideEntry] = [
            OverrideEntry(
                target_file_id="1",
                target_guid="",  # empty — the outer guard must drop this
                target_type="3",
                target_raw="",
                property_path="a",
                value="V",
                object_reference="{fileID: 0}",
                line=1,
            ),
        ]
        guids = _collect_referenced_guids(text, entries)
        self.assertEqual([], guids)


if __name__ == "__main__":
    unittest.main()
