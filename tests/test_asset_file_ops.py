"""Behavioural pins for ``prefab_sentinel.asset_file_ops`` (copy / rename).

Each test below pins the documented response envelope of the public
``copy_asset`` / ``rename_asset`` helpers by exact equality on a tuple
of contract fields, plus the documented on-disk side-effect (file
exists / does not exist / contains the rewritten ``m_Name`` line) where
the contract is observable on disk.

Diagnostic-detail rows materialise the ``[d.detail for d in
diagnostics]`` list and assert exact membership, so a mutation that
either drops the diagnostic or duplicates a sibling detail is named in
the failure message.

Internal helpers ``_rewrite_m_name`` / ``_generate_guid`` /
``_generate_meta_content`` are tested directly because they are the
seams the orchestrator-side helpers compose with — testing through the
public envelope alone would make a regression in those seams surface as
a vague envelope-content failure instead of a localised one.
"""

from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

from prefab_sentinel.asset_file_ops import (
    _generate_guid,
    _generate_meta_content,
    _rewrite_m_name,
    copy_asset,
    rename_asset,
)
from prefab_sentinel.unity_assets import decode_text_file

_FIXTURES = Path(__file__).parent / "fixtures" / "mat"

# MonoBehaviour-shaped .asset content used as a fixture for rewriter tests.
_MONOBEHAVIOUR_ASSET = """\
%YAML 1.1
%TAG !u! tag:unity3d.com,2011:
--- !u!114 &11400000
MonoBehaviour:
  m_ObjectHideFlags: 0
  m_CorrespondingSourceObject: {fileID: 0}
  m_PrefabInstance: {fileID: 0}
  m_PrefabAsset: {fileID: 0}
  m_GameObject: {fileID: 0}
  m_Enabled: 1
  m_EditorHideFlags: 0
  m_Script: {fileID: 11500000, guid: abc12345abc12345abc12345abc12345, type: 3}
  m_Name: OriginalAsset
  m_EditorClassIdentifier:
"""

# Asset content with no ``m_Name:`` field — exercises the "no match" branch.
_NO_M_NAME_CONTENT = """\
%YAML 1.1
%TAG !u! tag:unity3d.com,2011:
--- !u!114 &11400000
MonoBehaviour:
  m_ObjectHideFlags: 0
  m_Script: {fileID: 11500000, guid: abc12345abc12345abc12345abc12345, type: 3}
  m_EditorClassIdentifier:
"""

# Documented dummy meta content used as a sibling for fixture .mat files.
_DUMMY_META = "fileFormatVersion: 2\nguid: abcd1234abcd1234abcd1234abcd1234\n"


def _diag_details(result: dict) -> list[str]:
    """Materialise the ordered list of diagnostic details for set membership.

    Substituting an `assertTrue(any(...))` predicate with a materialised
    list pins the missing-element identity in the failure message.
    """
    return [d.get("detail") for d in result.get("diagnostics", [])]


# ---------------------------------------------------------------------------
# _rewrite_m_name — direct unit tests on the documented seam
# ---------------------------------------------------------------------------


class RewriteMNameTests(unittest.TestCase):
    """Pin the (new_text, old_name, new_name) triple returned by the rewriter."""

    def test_first_m_name_in_mat_text_is_replaced(self) -> None:
        text = decode_text_file(_FIXTURES / "standard_textured.mat")

        new_text, old_name, new_name = _rewrite_m_name(text, "CopiedMaterial")

        self.assertEqual(("TestMaterial", "CopiedMaterial"), (old_name, new_name))
        self.assertIn("  m_Name: CopiedMaterial", new_text)
        self.assertNotIn("  m_Name: TestMaterial", new_text)

    def test_m_name_in_monobehaviour_text_is_replaced(self) -> None:
        new_text, old_name, new_name = _rewrite_m_name(
            _MONOBEHAVIOUR_ASSET, "RenamedAsset"
        )

        self.assertEqual(("OriginalAsset", "RenamedAsset"), (old_name, new_name))
        self.assertIn("  m_Name: RenamedAsset", new_text)
        self.assertNotIn("  m_Name: OriginalAsset", new_text)

    def test_text_without_m_name_returns_text_unchanged_with_none_old(self) -> None:
        new_text, old_name, new_name = _rewrite_m_name(
            _NO_M_NAME_CONTENT, "SomeName"
        )

        # Pin the documented "no-op + signal" contract: text round-trips
        # byte-for-byte AND the old name is None to flag the no-match path.
        self.assertEqual(
            (_NO_M_NAME_CONTENT, None, "SomeName"),
            (new_text, old_name, new_name),
        )

    def test_text_with_matching_name_round_trips_unchanged(self) -> None:
        new_text, old_name, new_name = _rewrite_m_name(
            _MONOBEHAVIOUR_ASSET, "OriginalAsset"
        )

        self.assertEqual(
            (_MONOBEHAVIOUR_ASSET, "OriginalAsset", "OriginalAsset"),
            (new_text, old_name, new_name),
        )


# ---------------------------------------------------------------------------
# _generate_guid / _generate_meta_content — internal seam pins
# ---------------------------------------------------------------------------


class GenerateGuidTests(unittest.TestCase):
    """Pin the GUID generator's lexical contract: 32 lowercase hex chars."""

    def test_generated_guid_is_thirty_two_lowercase_hex_chars(self) -> None:
        guid = _generate_guid()

        # Pin the (length, regex-match) tuple in one assertion so a
        # change in either dimension is named.
        self.assertEqual(32, len(guid))
        self.assertRegex(guid, r"^[0-9a-f]{32}$")

    def test_two_calls_produce_different_guids(self) -> None:
        # Necessity: a generator that returns a constant would let the
        # downstream copy/rename helpers collide on meta GUIDs.
        self.assertNotEqual(_generate_guid(), _generate_guid())


class GenerateMetaContentTests(unittest.TestCase):
    """Pin the documented meta content shape for a known GUID."""

    def test_meta_content_is_documented_two_line_text(self) -> None:
        # The shape is exactly two lines: ``fileFormatVersion`` then ``guid``.
        guid = "a" * 32

        content = _generate_meta_content(guid)

        # Materialised expected text is the most precise pin: a mutation
        # that swaps the order, drops a key, or skips the trailing
        # newline is named here.
        self.assertEqual(f"fileFormatVersion: 2\nguid: {guid}\n", content)


# ---------------------------------------------------------------------------
# copy_asset — public envelope contract
# ---------------------------------------------------------------------------


class CopyAssetDryRunTests(unittest.TestCase):
    """Pin dry-run envelope and the documented "no on-disk side-effect" invariant."""

    def test_dry_run_returns_documented_envelope_and_does_not_create_dest(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            src = Path(tmpdir) / "original.mat"
            shutil.copy(_FIXTURES / "standard_textured.mat", src)
            dest = Path(tmpdir) / "copied.mat"

            result = copy_asset(str(src), str(dest), dry_run=True)
            # Capture filesystem state inside the temp-dir context — once
            # the ``with`` block exits, ``dest.exists()`` would always
            # report False because the temp dir is deleted.
            dest_exists = dest.exists()

        # Pin (success, code, dest-exists) as a single tuple; the
        # m_name_before/m_name_after keys are part of the envelope's
        # data contract and are pinned by separate equality below.
        self.assertEqual(
            (True, "ASSET_COPY_DRY_RUN", False),
            (result["success"], result["code"], dest_exists),
        )
        # Documented data block carries before/after for the m_Name field.
        data = result["data"]
        self.assertEqual(
            ("TestMaterial", "copied"),
            (data["m_name_before"], data["m_name_after"]),
        )


class CopyAssetApplyTests(unittest.TestCase):
    """Pin the apply branch's envelope, on-disk file content, and meta creation."""

    def test_apply_writes_dest_with_rewritten_m_name_and_meta_sibling(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            src = Path(tmpdir) / "original.mat"
            shutil.copy(_FIXTURES / "standard_textured.mat", src)
            src_meta = Path(str(src) + ".meta")
            src_meta.write_text(_DUMMY_META, encoding="utf-8")
            dest = Path(tmpdir) / "copied.mat"

            result = copy_asset(str(src), str(dest), dry_run=False)
            dest_text = dest.read_text(encoding="utf-8")
            dest_meta_path = Path(str(dest) + ".meta")
            dest_meta_text = dest_meta_path.read_text(encoding="utf-8")

        # Envelope contract: success + applied code + meta_created flag.
        self.assertEqual(
            (True, "ASSET_COPY_APPLIED", True),
            (result["success"], result["code"], result["data"]["meta_created"]),
        )
        # Side-effect contract: dest file contains the rewritten m_Name
        # line and not the original.
        self.assertIn("  m_Name: copied", dest_text)
        self.assertNotIn("  m_Name: TestMaterial", dest_text)
        # Meta sibling: documented two-key shape.
        self.assertIn("fileFormatVersion: 2", dest_meta_text)
        self.assertIn("guid:", dest_meta_text)
        # Documented absence: the m_name_unchanged flag is not set when
        # the rename actually changed the name.
        self.assertNotIn("m_name_unchanged", result["data"])


class CopyAssetFailureCodeTests(unittest.TestCase):
    """Pin every documented failure envelope code."""

    def test_unsupported_extension_yields_unsupported_type_envelope(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            src = Path(tmpdir) / "texture.png"
            src.write_bytes(b"fake png")
            dest = Path(tmpdir) / "texture_copy.png"

            result = copy_asset(str(src), str(dest), dry_run=True)

        self.assertEqual(
            (False, "ASSET_OP_UNSUPPORTED_TYPE"),
            (result["success"], result["code"]),
        )

    def test_missing_source_yields_source_not_found_envelope(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            result = copy_asset(
                str(Path(tmpdir) / "nonexistent.mat"),
                str(Path(tmpdir) / "dest.mat"),
                dry_run=True,
            )

        self.assertEqual(
            (False, "ASSET_COPY_SOURCE_NOT_FOUND"),
            (result["success"], result["code"]),
        )

    def test_existing_dest_yields_dest_exists_envelope(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            src = Path(tmpdir) / "original.mat"
            shutil.copy(_FIXTURES / "standard_textured.mat", src)
            dest = Path(tmpdir) / "existing.mat"
            dest.write_text("already here", encoding="utf-8")

            result = copy_asset(str(src), str(dest), dry_run=True)

        self.assertEqual(
            (False, "ASSET_COPY_DEST_EXISTS"),
            (result["success"], result["code"]),
        )

    def test_missing_dest_directory_yields_dest_dir_not_found_envelope(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            src = Path(tmpdir) / "original.mat"
            shutil.copy(_FIXTURES / "standard_textured.mat", src)
            dest = Path(tmpdir) / "nonexistent_dir" / "copied.mat"

            result = copy_asset(str(src), str(dest), dry_run=True)

        self.assertEqual(
            (False, "ASSET_COPY_DEST_DIR_NOT_FOUND"),
            (result["success"], result["code"]),
        )


class CopyAssetDiagnosticDetailTests(unittest.TestCase):
    """Pin the documented diagnostic detail strings emitted on success."""

    def test_missing_source_meta_emits_single_source_meta_missing_diag(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            src = Path(tmpdir) / "original.mat"
            shutil.copy(_FIXTURES / "standard_textured.mat", src)
            dest = Path(tmpdir) / "copied.mat"

            result = copy_asset(str(src), str(dest), dry_run=False)

        # Pin (success, ordered diagnostic-detail list) as one tuple so
        # an extra/missing/renamed detail is named in the failure message.
        self.assertEqual(
            (True, ["source_meta_missing"]),
            (result["success"], _diag_details(result)),
        )
        # The evidence string must name the missing-meta path; pin the
        # documented prefix.
        self.assertIn(
            "Source .meta not found",
            result["diagnostics"][0]["evidence"],
        )

    def test_text_without_m_name_emits_m_name_not_found_diag(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            src = Path(tmpdir) / "no_name.asset"
            src.write_text(_NO_M_NAME_CONTENT, encoding="utf-8")
            dest = Path(tmpdir) / "copied.asset"

            result = copy_asset(str(src), str(dest), dry_run=True)

        # Pin the success flag with a value-pinned equality (kills the
        # weak-bool smell), then pin documented-detail membership in
        # the materialised list.  Membership is the right shape here
        # because the dry-run path emits ``source_meta_missing``
        # before reaching the m_Name parser, so the documented detail
        # appears alongside the prior diagnostic rather than alone.
        details = _diag_details(result)
        self.assertEqual(True, result["success"], msg=str(result))
        self.assertIn("m_name_not_found", details, msg=f"details={details!r}")


class CopyAssetSignatureTests(unittest.TestCase):
    """Pin the documented keyword-only enforcement of ``dry_run``."""

    def test_positional_dry_run_argument_raises_type_error_naming_positional(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            src = Path(tmpdir) / "original.mat"
            shutil.copy(_FIXTURES / "standard_textured.mat", src)
            dest = Path(tmpdir) / "copied.mat"

            with self.assertRaises(TypeError) as cm:
                # ``True`` here is the third positional argument; the
                # documented contract is keyword-only.
                copy_asset(str(src), str(dest), True)

        self.assertIn("positional", str(cm.exception))


class CopyAssetMNameUnchangedTests(unittest.TestCase):
    """Pin the documented ``m_name_unchanged`` flag's two carve-outs."""

    def test_dry_run_with_dest_stem_differing_omits_unchanged_flag(self) -> None:
        # When the rename actually changes m_Name, the unchanged flag
        # must NOT appear in the data block (documented absence).
        with tempfile.TemporaryDirectory() as tmpdir:
            src = Path(tmpdir) / "TestMaterial.mat"
            shutil.copy(_FIXTURES / "standard_textured.mat", src)
            src_meta = Path(str(src) + ".meta")
            src_meta.write_text(_DUMMY_META, encoding="utf-8")
            dest = Path(tmpdir) / "TestMaterial_copy.mat"

            result = copy_asset(str(src), str(dest), dry_run=True)

        # Pin (success, m_name_unchanged-presence) as one tuple so a
        # success-flag flip and a stray flag injection are both named
        # in one failure message.
        self.assertEqual(
            (True, False),
            (result["success"], "m_name_unchanged" in result["data"]),
        )

    def test_apply_with_dest_stem_matching_m_name_sets_unchanged_flag(
        self,
    ) -> None:
        # When dest stem already equals m_Name, the documented flag
        # must be present and True.
        with tempfile.TemporaryDirectory() as tmpdir:
            src = Path(tmpdir) / "original.mat"
            shutil.copy(_FIXTURES / "standard_textured.mat", src)
            src_meta = Path(str(src) + ".meta")
            src_meta.write_text(_DUMMY_META, encoding="utf-8")
            dest = Path(tmpdir) / "TestMaterial.mat"

            result = copy_asset(str(src), str(dest), dry_run=False)

        self.assertEqual(
            (True, True),
            (result["success"], result["data"]["m_name_unchanged"]),
        )


# ---------------------------------------------------------------------------
# rename_asset — public envelope contract
# ---------------------------------------------------------------------------


class RenameAssetDryRunTests(unittest.TestCase):
    """Pin the rename dry-run envelope and on-disk no-op invariant."""

    def test_dry_run_returns_envelope_and_leaves_filesystem_unchanged(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            src = Path(tmpdir) / "original.mat"
            shutil.copy(_FIXTURES / "standard_textured.mat", src)
            new_path = Path(tmpdir) / "renamed.mat"

            result = rename_asset(str(src), "renamed.mat", dry_run=True)

            # Capture filesystem state inside the temp-dir context — once
            # the ``with`` block exits, the temp dir is deleted and every
            # ``Path.exists()`` would otherwise return False.
            observed = (
                result["success"],
                result["code"],
                src.exists(),
                new_path.exists(),
            )

        self.assertEqual(
            (True, "ASSET_RENAME_DRY_RUN", True, False), observed
        )


class RenameAssetApplyTests(unittest.TestCase):
    """Pin the rename apply envelope and the on-disk move + meta-rename effects."""

    def test_apply_renames_file_with_meta_and_rewrites_m_name_line(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            src = Path(tmpdir) / "original.mat"
            shutil.copy(_FIXTURES / "standard_textured.mat", src)
            src_meta = Path(str(src) + ".meta")
            src_meta.write_text(_DUMMY_META, encoding="utf-8")
            new_path = Path(tmpdir) / "renamed.mat"
            new_meta = Path(str(new_path) + ".meta")

            result = rename_asset(str(src), "renamed.mat", dry_run=False)
            text = new_path.read_text(encoding="utf-8")
            # Capture filesystem state inside the temp-dir context.
            observed = (
                result["success"],
                result["code"],
                result["data"]["meta_renamed"],
                new_path.exists(),
                src.exists(),
                src_meta.exists(),
                new_meta.exists(),
            )

        # Pin envelope + filesystem-state tuple.
        self.assertEqual(
            (True, "ASSET_RENAME_APPLIED", True, True, False, False, True),
            observed,
        )
        self.assertIn("  m_Name: renamed", text)
        # Documented absence: m_name_unchanged is not set on a real rename.
        self.assertNotIn("m_name_unchanged", result["data"])


class RenameAssetFailureCodeTests(unittest.TestCase):
    """Pin every documented rename failure envelope code."""

    def test_unsupported_extension_yields_unsupported_type_envelope(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            src = Path(tmpdir) / "texture.png"
            src.write_bytes(b"fake png")

            result = rename_asset(str(src), "texture_new.png", dry_run=True)

        self.assertEqual(
            (False, "ASSET_OP_UNSUPPORTED_TYPE"),
            (result["success"], result["code"]),
        )

    def test_missing_asset_yields_rename_not_found_envelope(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            result = rename_asset(
                str(Path(tmpdir) / "nonexistent.mat"),
                "renamed.mat",
                dry_run=True,
            )

        self.assertEqual(
            (False, "ASSET_RENAME_NOT_FOUND"),
            (result["success"], result["code"]),
        )

    def test_existing_dest_yields_dest_exists_envelope(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            src = Path(tmpdir) / "original.mat"
            shutil.copy(_FIXTURES / "standard_textured.mat", src)
            existing = Path(tmpdir) / "taken.mat"
            existing.write_text("taken", encoding="utf-8")

            result = rename_asset(str(src), "taken.mat", dry_run=True)

        self.assertEqual(
            (False, "ASSET_RENAME_DEST_EXISTS"),
            (result["success"], result["code"]),
        )

    def test_extension_change_yields_extension_mismatch_envelope(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            src = Path(tmpdir) / "original.mat"
            shutil.copy(_FIXTURES / "standard_textured.mat", src)

            result = rename_asset(str(src), "renamed.asset", dry_run=True)

        self.assertEqual(
            (False, "ASSET_RENAME_EXT_MISMATCH"),
            (result["success"], result["code"]),
        )


class RenameAssetWithoutMetaTests(unittest.TestCase):
    """Pin the no-meta rename branch's flag value."""

    def test_apply_without_meta_sibling_sets_meta_renamed_to_false(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            src = Path(tmpdir) / "original.mat"
            shutil.copy(_FIXTURES / "standard_textured.mat", src)

            result = rename_asset(str(src), "renamed.mat", dry_run=False)

        self.assertEqual(
            (True, False),
            (result["success"], result["data"]["meta_renamed"]),
        )


class RenameAssetDiagnosticDetailTests(unittest.TestCase):
    """Pin documented rename diagnostic detail strings."""

    def test_text_without_m_name_emits_m_name_not_found_diag(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            src = Path(tmpdir) / "no_name.asset"
            src.write_text(_NO_M_NAME_CONTENT, encoding="utf-8")

            result = rename_asset(str(src), "renamed.asset", dry_run=True)

        # Pin (success, ordered diagnostic-detail list) as one tuple so
        # an extra/missing/renamed detail is named alongside the
        # success branch in one failure message.
        self.assertEqual(
            (True, ["m_name_not_found"]),
            (result["success"], _diag_details(result)),
        )

    def test_meta_rename_failure_emits_meta_rename_failed_diag_and_flag_false(
        self,
    ) -> None:
        # Pre-create the destination meta path as a directory so the
        # rename of the meta file fails; the documented contract is
        # success envelope + meta_renamed=False + the named diagnostic.
        with tempfile.TemporaryDirectory() as tmpdir:
            src = Path(tmpdir) / "original.mat"
            shutil.copy(_FIXTURES / "standard_textured.mat", src)
            src_meta = Path(str(src) + ".meta")
            src_meta.write_text(_DUMMY_META, encoding="utf-8")
            dest_meta = Path(tmpdir) / "renamed.mat.meta"
            dest_meta.mkdir()

            result = rename_asset(str(src), "renamed.mat", dry_run=False)

        self.assertEqual(
            (True, False),
            (result["success"], result["data"]["meta_renamed"]),
        )
        self.assertIn("meta_rename_failed", _diag_details(result))


class RenameAssetSignatureTests(unittest.TestCase):
    """Pin the documented keyword-only enforcement of ``dry_run``."""

    def test_positional_dry_run_argument_raises_type_error_naming_positional(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            src = Path(tmpdir) / "original.mat"
            shutil.copy(_FIXTURES / "standard_textured.mat", src)

            with self.assertRaises(TypeError) as cm:
                rename_asset(str(src), "renamed.mat", True)

        self.assertIn("positional", str(cm.exception))


class RenameAssetMNameUnchangedTests(unittest.TestCase):
    """Pin the documented ``m_name_unchanged`` flag for the rename helper."""

    def test_dry_run_with_new_stem_matching_m_name_sets_unchanged_with_before_after(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            # m_Name in _MONOBEHAVIOUR_ASSET is "OriginalAsset".
            src = Path(tmpdir) / "wrong_filename.asset"
            src.write_text(_MONOBEHAVIOUR_ASSET, encoding="utf-8")

            result = rename_asset(
                str(src), "OriginalAsset.asset", dry_run=True
            )

        self.assertEqual(
            (True, True, "OriginalAsset", "OriginalAsset"),
            (
                result["success"],
                result["data"]["m_name_unchanged"],
                result["data"]["m_name_before"],
                result["data"]["m_name_after"],
            ),
        )


if __name__ == "__main__":
    unittest.main()
