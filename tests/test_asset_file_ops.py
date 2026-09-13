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

import json
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
from prefab_sentinel.material_asset_inspector import inspect_material_asset
from prefab_sentinel.unity_assets import decode_text_file
from prefab_sentinel.unity_yaml_parser import split_yaml_blocks
from prefab_sentinel.yaml_field_extraction import extract_block_fields
from tests._assertion_helpers import assert_error_envelope

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


class CopyControllerMainObjectTests(unittest.TestCase):
    """A Controller copy must rename the main object, not a preceding State."""

    _PREFIX = "%YAML 1.1\n%TAG !u! tag:unity3d.com,2011:\n"
    _MAIN = (
        "--- !u!91 &9154321\n"
        "AnimatorController:\n"
        "  m_AnimatorLayers:\n"
        "  - m_Name: LayerName\n"
        "    m_StateMachine: {fileID: 1107001}\n"
        "  m_Name: MainController\n"
    )
    _COPIED_MAIN = (
        "--- !u!91 &9154321\n"
        "AnimatorController:\n"
        "  m_AnimatorLayers:\n"
        "  - m_Name: LayerName\n"
        "    m_StateMachine: {fileID: 1107001}\n"
        '  m_Name: "CopiedController"\n'
    )
    _STATE = (
        "--- !u!1102 &1102001\n"
        "AnimatorState:\n"
        "  m_Name: MainController\n"
        "  m_Transitions:\n"
        "  - {fileID: 1101001}\n"
        "  m_Motion: {fileID: 7400000, guid: aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa, type: 2}\n"
        "--- !u!1101 &1101001\n"
        "AnimatorStateTransition:\n"
        "  m_Name: TransitionName\n"
        "  m_DstState: {fileID: 1102001}\n"
        "--- !u!1107 &1107001\n"
        "AnimatorStateMachine:\n"
        "  m_Name: StateMachineName\n"
        "  m_ChildStates:\n"
        "  - m_State: {fileID: 1102001}\n"
    )

    def test_copy_changes_only_main_name_in_either_document_order(self) -> None:
        for main_first in (False, True):
            for dry_run in (True, False):
                with self.subTest(main_first=main_first, dry_run=dry_run):
                    source_text = self._PREFIX + (
                        self._MAIN + self._STATE if main_first else self._STATE + self._MAIN
                    )
                    expected_text = self._PREFIX + (
                        self._COPIED_MAIN + self._STATE
                        if main_first else self._STATE + self._COPIED_MAIN
                    )
                    with tempfile.TemporaryDirectory() as tmpdir:
                        root = Path(tmpdir)
                        src = root / "Original.controller"
                        src.write_text(source_text, encoding="utf-8")
                        src_meta = root / "Original.controller.meta"
                        src_meta.write_text(_DUMMY_META, encoding="utf-8")
                        unrelated = root / "Other.asset"
                        unrelated.write_text("unrelated disk state", encoding="utf-8")
                        dest = root / "CopiedController.controller"
                        result = copy_asset(str(src), str(dest), dry_run=dry_run)
                        self.assertEqual(
                            (source_text, _DUMMY_META, "unrelated disk state"),
                            (src.read_text(), src_meta.read_text(), unrelated.read_text()),
                            "Copying must preserve source, metadata, and unrelated assets",
                        )
                        self.assertEqual(
                            (not dry_run, not dry_run),
                            (dest.exists(), Path(str(dest) + ".meta").exists()),
                            "Only confirmed copies create destination files",
                        )
                        if not dry_run:
                            self.assertEqual(expected_text, dest.read_text(), "Only main m_Name changes")
                    self.assertEqual(
                        (True, "ASSET_COPY_DRY_RUN" if dry_run else "ASSET_COPY_APPLIED"),
                        (result["success"], result["code"]),
                    )
                    self.assertEqual(
                        ("MainController", "CopiedController", {
                            "class_id": "91", "file_id": "9154321",
                            "type_name": "AnimatorController", "property_path": "m_Name",
                        }),
                        (result["data"]["m_name_before"], result["data"]["m_name_after"],
                         result["data"].get("m_name_target")),
                        "Preview and apply must identify the same main object",
                    )

    def test_destination_stem_is_written_as_a_string_scalar(self) -> None:
        source_text = self._PREFIX + self._STATE + self._MAIN
        for stem, expected_scalar in (
            ("Copy # Tail", '"Copy # Tail"'),
            ("# Hidden", '"# Hidden"'),
            ("true", '"true"'),
            ("日本語 #1", '"日本語 #1"'),
        ):
            with self.subTest(stem=stem):
                with tempfile.TemporaryDirectory() as tmpdir:
                    root = Path(tmpdir)
                    assets = root / "Assets"
                    assets.mkdir()
                    src = assets / "Original.controller"
                    src.write_text(source_text, encoding="utf-8")
                    dest = assets / f"{stem}.controller"
                    preview = copy_asset(
                        "Assets/Original.controller", f"Assets/{stem}.controller",
                        project_root=root, dry_run=True,
                    )
                    self.assertEqual(
                        (True, "ASSET_COPY_DRY_RUN", False),
                        (preview["success"], preview["code"], dest.exists()),
                        "The destination filename is accepted without a write",
                    )
                    result = copy_asset(
                        "Assets/Original.controller", f"Assets/{stem}.controller",
                        project_root=root, dry_run=False,
                    )
                    observed_text = dest.read_text(encoding="utf-8")
                    self.assertEqual(source_text, src.read_text(encoding="utf-8"))
                self.assertEqual(
                    (True, "ASSET_COPY_APPLIED", stem),
                    (result["success"], result["code"], result["data"]["m_name_after"]),
                )
                main = next(block for block in split_yaml_blocks(observed_text) if block.class_id == "91")
                scalar = dict(extract_block_fields(main.text))["m_Name"]
                self.assertEqual(expected_scalar, scalar, "The name must remain a quoted string")
                # JSON double-quoted strings are YAML 1.1 scalars; decoding proves
                # that '#' and implicit YAML booleans are string content.
                self.assertEqual(stem, json.loads(scalar), "Stored scalar must equal the requested name")
                expected_main = self._MAIN.replace(
                    "  m_Name: MainController\n", f"  m_Name: {expected_scalar}\n",
                )
                self.assertEqual(
                    self._PREFIX + self._STATE + expected_main, observed_text,
                    "Only the main name scalar may change",
                )

    def test_quoted_matching_name_preserves_all_controller_text(self) -> None:
        for stem, scalar in (
            ("Copy # Tail", '"Copy # Tail"'),
        ):
            source_text = self._PREFIX + self._STATE + self._MAIN.replace(
                "  m_Name: MainController\n", f"  m_Name: {scalar}\n",
            )
            for dry_run in (True, False):
                with self.subTest(stem=stem, scalar=scalar, dry_run=dry_run):
                    with tempfile.TemporaryDirectory() as tmpdir:
                        root = Path(tmpdir)
                        src = root / "Original.controller"
                        src.write_text(source_text, encoding="utf-8")
                        dest = root / f"{stem}.controller"
                        result = copy_asset(str(src), str(dest), dry_run=dry_run)
                        self.assertEqual(
                            (scalar, stem, True),
                            (result["data"]["m_name_before"], result["data"]["m_name_after"],
                             result["data"].get("m_name_unchanged")),
                            "A canonical quoted no-op keeps both source scalar and bytes",
                        )
                        if not dry_run:
                            self.assertEqual(source_text, dest.read_text(encoding="utf-8"))

    def test_quoted_source_names_remain_accepted_without_general_yaml_decoding(self) -> None:
        for scalar, stem, expected_scalar in (
            ("'Copy''s Tail'", "Copy's Tail", '"Copy\'s Tail"'),
            ('"Copy \\x23 Tail"', "Copy # Tail", '"Copy # Tail"'),
            ('"Copy \\u0023 Tail"', "Copy # Tail", '"Copy # Tail"'),
        ):
            with self.subTest(scalar=scalar):
                source_text = self._PREFIX + self._STATE + self._MAIN.replace(
                    "  m_Name: MainController\n", f"  m_Name: {scalar}\n",
                )
                with tempfile.TemporaryDirectory() as tmpdir:
                    root = Path(tmpdir)
                    src = root / "Original.controller"
                    src.write_text(source_text, encoding="utf-8")
                    dest = root / f"{stem}.controller"
                    result = copy_asset(str(src), str(dest), dry_run=False)
                    self.assertEqual(
                        (True, "ASSET_COPY_APPLIED"), (result["success"], result["code"]),
                        "Valid YAML quoted names must not require JSON source syntax",
                    )
                    self.assertEqual(source_text, src.read_text(encoding="utf-8"))
                    expected_main = self._MAIN.replace(
                        "  m_Name: MainController\n", f"  m_Name: {expected_scalar}\n",
                    )
                    self.assertEqual(self._PREFIX + self._STATE + expected_main, dest.read_text(encoding="utf-8"))
                self.assertEqual(
                    (scalar, stem), (result["data"]["m_name_before"], result["data"]["m_name_after"]),
                    "Before remains the opaque source scalar; after is the requested name",
                )
                self.assertNotIn("m_name_unchanged", result["data"], "Normalization does not claim semantic no-op")

    def test_matching_main_name_preserves_all_controller_text(self) -> None:
        source_text = self._PREFIX + self._STATE + self._MAIN
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            src = root / "Original.controller"
            src.write_text(source_text, encoding="utf-8")
            dest = root / "MainController.controller"
            result = copy_asset(str(src), str(dest), dry_run=False)
            self.assertEqual(source_text, dest.read_text(), "A main-name no-op preserves every document")
        self.assertEqual(
            (True, "ASSET_COPY_APPLIED", True),
            (result["success"], result["code"], result["data"].get("m_name_unchanged")),
        )

    def test_unreliable_controller_target_fails_before_any_copy_write(self) -> None:
        invalid_sources = (
            (self._STATE, "main_object_not_unique", 0),
            (self._MAIN + self._MAIN.replace("9154321", "9154322"), "main_object_not_unique", 2),
            (self._MAIN.replace("&9154321", "&9154321 stripped"), "main_object_not_unique", 0),
            (self._MAIN.replace("AnimatorController:", "AnimatorState:"), "main_object_identity_invalid", 1),
            (self._MAIN + self._STATE.replace("&1102001", "&9154321"), "main_object_identity_invalid", 1),
            (self._MAIN.replace("  m_Name: MainController\n", ""), "root_name_invalid", 1),
            (self._MAIN + "  m_Name: Duplicate\n", "root_name_invalid", 1),
            (self._MAIN.replace("MainController\n", "|\n    Multiline\n"), "root_name_invalid", 1),
            (self._MAIN.replace("MainController\n", "\n"), "root_name_invalid", 1),
            (self._MAIN.replace("MainController\n", "'Unclosed\n"), "root_name_invalid", 1),
            (self._MAIN + "    continued name\n", "root_name_invalid", 1),
        )
        for body, reason, count in invalid_sources:
            for dry_run in (True, False):
                with self.subTest(reason=reason, count=count, body=body, dry_run=dry_run):
                    with tempfile.TemporaryDirectory() as tmpdir:
                        root = Path(tmpdir)
                        src = root / "Original.controller"
                        source_text = self._PREFIX + body
                        src.write_text(source_text, encoding="utf-8")
                        dest = root / "CopiedController.controller"
                        result = copy_asset(str(src), str(dest), dry_run=dry_run)
                        self.assertEqual(
                            (["Original.controller"], source_text),
                            (sorted(path.name for path in root.iterdir()), src.read_text()),
                            "Invalid main identity/name must leave no destination or staging artifacts",
                        )
                    assert_error_envelope(
                        result, code="ASSET_COPY_MAIN_OBJECT_INVALID", field="source_path",
                        message_match="Controller main object",
                        data={"field": "source_path", "reason": reason, "matching_document_count": count},
                    )


class ControllerScalarBoundaryTests(unittest.TestCase):
    """Root-looking text inside a different scalar is not a writable field."""

    _FIELDS = (
        "  m_UserData:\n  - [\"first: text\n  m_Name: Decoy\n  last\"]\n",
        "  m_UserData:\n  - {\"text: key\": \"first\n  m_Name: Decoy\n  last\"}\n",
        "  m_UserData:\n  - \"first: text\n  m_Name: Decoy\n  last\"\n",
        "  m_UserData:\n  - 'first ''quoted'': text\n  m_Name: Decoy\n  last'\n",
        "  m_UserData:\n  - \"first \\\"quoted\\\": text\n  m_Name: Decoy\n  last\"\n",
        '  m_UserData: "first\n  m_Name: Decoy\n  last"\n',
        '  m_UserData: "first \\"quoted\\"\n  m_Name: Decoy\n  last"\n',
        "  m_UserData: 'first ''quoted''\n  m_Name: Decoy\n  last'\n",
        '  m_UserData: {text: "first\n  m_Name: Decoy\n  last"}\n',
        '  m_UserData: ["first\n  m_Name: Decoy\n  last"]\n',
        '  m_UserData:\n  - "first\n  m_Name: Decoy\n  last"\n',
    )

    def test_decoy_name_inside_other_scalar_is_rejected_without_writes(self) -> None:
        for field in self._FIELDS:
            for operation in (copy_asset, rename_asset):
                for dry_run in (True, False):
                    with self.subTest(field=field, operation=operation.__name__, dry_run=dry_run):
                        source_text = (
                            CopyControllerMainObjectTests._PREFIX
                            + "--- !u!91 &9154321\nAnimatorController:\n"
                            + field + "  m_AnimatorLayers: []\n"
                        )
                        with tempfile.TemporaryDirectory() as tmpdir:
                            root = Path(tmpdir)
                            src = root / "Original.controller"
                            src.write_text(source_text, encoding="utf-8")
                            meta = root / "Original.controller.meta"
                            meta.write_text(_DUMMY_META, encoding="utf-8")
                            if operation is copy_asset:
                                result = operation(str(src), str(root / "Updated.controller"), dry_run=dry_run)
                                code, input_field = "ASSET_COPY_MAIN_OBJECT_INVALID", "source_path"
                            else:
                                result = operation(str(src), "Updated.controller", dry_run=dry_run)
                                code, input_field = "ASSET_RENAME_MAIN_OBJECT_INVALID", "asset_path"
                            assert_error_envelope(
                                result, code=code, field=input_field, message_match="Controller main object",
                                data={"field": input_field, "reason": "root_name_invalid", "matching_document_count": 1},
                            )
                            self.assertEqual(
                                (["Original.controller", "Original.controller.meta"], source_text.encode(), _DUMMY_META.encode()),
                                (sorted(path.name for path in root.iterdir()), src.read_bytes(), meta.read_bytes()),
                                "Missing real root name must reject before staging, copying, or moving",
                            )

    def test_actual_root_after_other_multiline_scalar_is_the_only_changed_span(self) -> None:
        fields = (
            *self._FIELDS,
            '  m_UserData: first "plain quote\n    and don\'t close it\n',
            '  m_UserData: first\n    - "quoted-looking plain continuation\n',
        )
        for field in fields:
            for operation in (copy_asset, rename_asset):
                for dry_run in (True, False):
                    with self.subTest(field=field, operation=operation.__name__, dry_run=dry_run):
                        prefix = (
                            CopyControllerMainObjectTests._PREFIX
                            + "--- !u!91 &9154321\nAnimatorController:\n"
                            + field + "  m_AnimatorLayers: []\n"
                        )
                        source_text = prefix + "  m_Name: ActualController\n"
                        expected_text = prefix + '  m_Name: "Updated"\n'
                        with tempfile.TemporaryDirectory() as tmpdir:
                            root = Path(tmpdir)
                            src = root / "Original.controller"
                            src.write_text(source_text, encoding="utf-8")
                            meta = root / "Original.controller.meta"
                            meta.write_text(_DUMMY_META, encoding="utf-8")
                            dest = root / "Updated.controller"
                            if operation is copy_asset:
                                result = operation(str(src), str(dest), dry_run=dry_run)
                                expected_code = "ASSET_COPY_DRY_RUN" if dry_run else "ASSET_COPY_APPLIED"
                            else:
                                result = operation(str(src), dest.name, dry_run=dry_run)
                                expected_code = "ASSET_RENAME_DRY_RUN" if dry_run else "ASSET_RENAME_APPLIED"
                            self.assertEqual((True, expected_code), (result["success"], result["code"]))
                            self.assertEqual(
                                ("ActualController", "Updated", "9154321"),
                                (result["data"]["m_name_before"], result["data"]["m_name_after"],
                                 result["data"]["m_name_target"]["file_id"]),
                            )
                            if dry_run:
                                self.assertEqual((False, source_text.encode(), _DUMMY_META.encode()),
                                                 (dest.exists(), src.read_bytes(), meta.read_bytes()))
                            else:
                                self.assertEqual(expected_text.encode(), dest.read_bytes(), "Preserve all other scalar bytes")

    def test_multiline_root_name_is_rejected_even_with_an_escaped_quote_at_line_end(self) -> None:
        for scalar in ('"first \\"\n  last"', "'first ''\n  last'"):
            for operation in (copy_asset, rename_asset):
                for dry_run in (True, False):
                    with self.subTest(scalar=scalar, operation=operation.__name__, dry_run=dry_run):
                        source_text = (
                            CopyControllerMainObjectTests._PREFIX
                            + "--- !u!91 &9154321\nAnimatorController:\n"
                            + f"  m_Name: {scalar}\n  m_AnimatorLayers: []\n"
                        )
                        with tempfile.TemporaryDirectory() as tmpdir:
                            root = Path(tmpdir)
                            src = root / "Original.controller"
                            src.write_text(source_text, encoding="utf-8")
                            meta = root / "Original.controller.meta"
                            meta.write_text(_DUMMY_META, encoding="utf-8")
                            if operation is copy_asset:
                                result = operation(str(src), str(root / "Updated.controller"), dry_run=dry_run)
                                code, field = "ASSET_COPY_MAIN_OBJECT_INVALID", "source_path"
                            else:
                                result = operation(str(src), "Updated.controller", dry_run=dry_run)
                                code, field = "ASSET_RENAME_MAIN_OBJECT_INVALID", "asset_path"
                            assert_error_envelope(
                                result, code=code, field=field, message_match="Controller main object",
                                data={"field": field, "reason": "root_name_invalid", "matching_document_count": 1},
                            )
                            self.assertEqual(
                                (["Original.controller", "Original.controller.meta"], source_text.encode(), _DUMMY_META.encode()),
                                (sorted(path.name for path in root.iterdir()), src.read_bytes(), meta.read_bytes()),
                            )


class CopyAssetProjectRootPathTests(unittest.TestCase):
    def test_project_relative_material_path_uses_project_root_for_dry_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "Assets" / "Materials").mkdir(parents=True)
            src = root / "Assets" / "Materials" / "Source.mat"
            shutil.copy(_FIXTURES / "standard_textured.mat", src)
            dest = root / "Assets" / "Materials" / "Copied.mat"

            inspected = inspect_material_asset(str(src))
            result = copy_asset(
                "Assets/Materials/Source.mat",
                "Assets/Materials/Copied.mat",
                dry_run=True,
                project_root=root,
            )
            dest_exists = dest.exists()

        self.assertEqual(
            ("TestMaterial", True, "ASSET_COPY_DRY_RUN", False),
            (inspected.material_name, result["success"], result["code"], dest_exists),
        )
        self.assertEqual(
            (str(src), str(dest)),
            (result["data"]["source_path"], result["data"]["dest_path"]),
        )

    def test_project_relative_material_path_apply_creates_asset_and_meta(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "Assets" / "Materials").mkdir(parents=True)
            src = root / "Assets" / "Materials" / "Source.mat"
            shutil.copy(_FIXTURES / "standard_textured.mat", src)
            dest = root / "Assets" / "Materials" / "Copied.mat"
            dest_meta = Path(str(dest) + ".meta")

            result = copy_asset(
                "Assets/Materials/Source.mat",
                "Assets/Materials/Copied.mat",
                dry_run=False,
                project_root=root,
            )
            filesystem_state = (dest.exists(), dest_meta.exists())

        self.assertEqual(
            (True, "ASSET_COPY_APPLIED", (True, True)),
            (result["success"], result["code"], filesystem_state),
        )


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

    def test_missing_project_relative_source_reports_resolution_details(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "Assets" / "Materials").mkdir(parents=True)
            try:
                result = copy_asset(
                    "Assets/Materials/Missing.mat",
                    "Assets/Materials/Copied.mat",
                    dry_run=True,
                    project_root=root,
                )
            except ValueError as exc:
                result = {
                    "success": "raised",
                    "code": type(exc).__name__,
                    "data": {"message": str(exc)},
                    "diagnostics": [],
                }
            expected_candidate = str(root / "Assets" / "Materials" / "Missing.mat")

        self.assertEqual(
            (
                False,
                "ASSET_COPY_SOURCE_NOT_FOUND",
                {
                    "input_path": "Assets/Materials/Missing.mat",
                    "normalized_candidate_path": expected_candidate,
                    "resolution_root": str(root),
                    "reason": "not_found",
                },
                [
                    {
                        "detail": "copy_source_resolution",
                        "evidence": "reason=not_found",
                    }
                ],
            ),
            (
                result["success"],
                result["code"],
                result["data"],
                result["diagnostics"],
            ),
        )

    def test_source_status_probe_error_returns_source_not_found_envelope(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            src = root / "Assets" / "Materials" / "Input.mat"
            src.parent.mkdir(parents=True)
            src.write_text(_NO_M_NAME_CONTENT, encoding="utf-8")
            original_is_file = Path.is_file

            def fail_source_probe(path: Path) -> bool:
                if path == src:
                    raise OSError("stat failed")
                return original_is_file(path)

            try:
                Path.is_file = fail_source_probe  # type: ignore[assignment]
                result = copy_asset(
                    "Assets/Materials/Input.mat",
                    "Assets/Materials/Copied.mat",
                    dry_run=True,
                    project_root=root,
                )
            except OSError as exc:
                result = {"success": "raised", "code": type(exc).__name__, "data": {"error": str(exc)}}
            finally:
                Path.is_file = original_is_file  # type: ignore[assignment]

        self.assertEqual(
            (False, "ASSET_COPY_SOURCE_NOT_FOUND", "status_error"),
            (result["success"], result["code"], result["data"]["reason"]),
        )

    def test_absolute_outside_source_yields_invalid_path_envelope(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / "Project"
            root.mkdir()
            outside = Path(tmpdir) / "Outside.mat"
            outside.write_text(
                "%YAML 1.1\n--- !u!21 &2100000\nMaterial:\n  m_Name: Outside\n",
                encoding="utf-8",
            )
            try:
                result = copy_asset(
                    str(outside),
                    "Assets/Materials/Copied.mat",
                    dry_run=True,
                    project_root=root,
                )
            except ValueError as exc:
                result = {
                    "success": "raised",
                    "code": type(exc).__name__,
                    "data": {"message": str(exc)},
                    "diagnostics": [],
                }
            expected_candidate = str(outside)

        self.assertEqual(
            (
                False,
                "ASSET_COPY_SOURCE_INVALID_PATH",
                {
                    "input_path": str(outside),
                    "normalized_candidate_path": expected_candidate,
                    "resolution_root": str(root),
                    "reason": "outside_project",
                },
                [
                    {
                        "detail": "copy_source_resolution",
                        "evidence": "reason=outside_project",
                    }
                ],
            ),
            (
                result["success"],
                result["code"],
                result["data"],
                result["diagnostics"],
            ),
        )

    def test_source_resolve_failure_returns_invalid_path_envelope(self) -> None:
        from unittest.mock import patch

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / "Project"
            (root / "Assets" / "Materials").mkdir(parents=True)

            with patch.object(Path, "resolve", side_effect=OSError("resolve failed")):
                try:
                    result = copy_asset(
                        "Assets/Materials/Input.mat",
                        "Assets/Materials/Copied.mat",
                        dry_run=False,
                        project_root=root,
                    )
                except OSError as exc:
                    result = {
                        "success": "raised",
                        "code": type(exc).__name__,
                        "data": {"error": str(exc)},
                    }

        self.assertEqual(
            (False, "ASSET_COPY_SOURCE_INVALID_PATH", "resolve_error"),
            (
                result["success"],
                result["code"],
                result["data"].get("reason"),
            ),
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

    def test_project_relative_dest_outside_project_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            project_root = Path(tmpdir) / "Project"
            assets = project_root / "Assets"
            assets.mkdir(parents=True)
            src = assets / "original.mat"
            shutil.copy(_FIXTURES / "standard_textured.mat", src)
            outside_dest = project_root.parent / "outside" / "copied.mat"

            result = copy_asset(
                "Assets/original.mat",
                "Assets/../../outside/copied.mat",
                dry_run=False,
                project_root=project_root,
            )

        self.assertEqual(
            (False, "ASSET_COPY_DEST_INVALID_PATH", False),
            (result["success"], result["code"], outside_dest.exists()),
        )
        self.assertEqual("outside_project_root", result["data"]["reason"])

    def test_failed_copy_cleanup_unlink_returns_write_failed_diagnostic(self) -> None:
        from unittest.mock import patch

        with tempfile.TemporaryDirectory() as tmpdir:
            src = Path(tmpdir) / "original.mat"
            dest = Path(tmpdir) / "copied.mat"
            shutil.copy(_FIXTURES / "standard_textured.mat", src)
            Path(f"{src}.meta").write_text(
                "fileFormatVersion: 2\nguid: 0123456789abcdef0123456789abcdef\n",
                encoding="utf-8",
            )
            original_write_text = Path.write_text

            def fail_meta_write(path, *args, **kwargs):
                if str(path).endswith(".meta"):
                    raise OSError("meta write failed")
                return original_write_text(path, *args, **kwargs)

            def fail_cleanup_unlink(path, *args, **kwargs):
                raise OSError("unlink failed")

            with (
                patch.object(Path, "write_text", fail_meta_write),
                patch.object(Path, "unlink", fail_cleanup_unlink),
            ):
                result = copy_asset(str(src), str(dest), dry_run=False)
            dest_exists = dest.exists()
            meta_exists = Path(f"{dest}.meta").exists()

        self.assertEqual(
            (False, "ASSET_OP_WRITE_FAILED", False, False),
            (result["success"], result["code"], dest_exists, meta_exists),
        )
        self.assertEqual("copy_cleanup_failed", result["diagnostics"][0]["detail"])

    def test_copy_cleanup_exists_probe_failure_keeps_write_failed_envelope(self) -> None:
        from unittest.mock import patch

        with tempfile.TemporaryDirectory() as tmpdir:
            src = Path(tmpdir) / "original.mat"
            dest = Path(tmpdir) / "copied.mat"
            shutil.copy(_FIXTURES / "standard_textured.mat", src)
            Path(f"{src}.meta").write_text(_DUMMY_META, encoding="utf-8")
            original_write_text = Path.write_text
            original_exists = Path.exists

            def fail_meta_write(path, *args, **kwargs):
                if str(path).endswith(".meta"):
                    raise OSError("meta write failed")
                return original_write_text(path, *args, **kwargs)

            def fail_hidden_exists(path):
                if path.name.startswith("."):
                    raise OSError("exists failed")
                return original_exists(path)

            with (
                patch.object(Path, "write_text", fail_meta_write),
                patch.object(Path, "exists", fail_hidden_exists),
            ):
                try:
                    result = copy_asset(str(src), str(dest), dry_run=False)
                except OSError as exc:
                    result = {
                        "success": "raised",
                        "code": type(exc).__name__,
                        "message": str(exc),
                    }

        self.assertEqual(
            (False, "ASSET_OP_WRITE_FAILED"),
            (result["success"], result["code"]),
        )

    def test_failed_meta_write_does_not_leave_partial_asset_copy(self) -> None:
        from unittest.mock import patch

        with tempfile.TemporaryDirectory() as tmpdir:
            src = Path(tmpdir) / "original.mat"
            dest = Path(tmpdir) / "copied.mat"
            shutil.copy(_FIXTURES / "standard_textured.mat", src)
            original_write_text = Path.write_text

            def fail_meta_write(path, *args, **kwargs):
                if str(path).endswith(".meta"):
                    raise OSError("meta write failed")
                return original_write_text(path, *args, **kwargs)

            with patch.object(Path, "write_text", fail_meta_write):
                result = copy_asset(str(src), str(dest), dry_run=False)
            dest_exists = dest.exists()
            meta_exists = Path(f"{dest}.meta").exists()

        self.assertEqual(
            (False, "ASSET_OP_WRITE_FAILED", False, False),
            (result["success"], result["code"], dest_exists, meta_exists),
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
                copy_asset(str(src), str(dest), True)  # type: ignore[misc]  # intentional keyword-only probe

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
            src_meta = Path(str(src) + ".meta")
            src_meta.write_text(_DUMMY_META, encoding="utf-8")
            new_path = Path(tmpdir) / "renamed.mat"
            new_meta = Path(str(new_path) + ".meta")

            result = rename_asset(str(src), "renamed.mat", dry_run=True)

            # Capture filesystem state inside the temp-dir context; once
            # the with block exits, the temp dir is deleted and every
            # Path.exists() would otherwise return False.
            observed = (
                result["success"],
                result["code"],
                src.exists(),
                src_meta.exists(),
                new_path.exists(),
                new_meta.exists(),
            )

        self.assertEqual(
            (True, "ASSET_RENAME_DRY_RUN", True, True, False, False),
            observed,
        )

    def test_dry_run_missing_source_meta_returns_write_failed_diagnostic(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            src = Path(tmpdir) / "original.mat"
            shutil.copy(_FIXTURES / "standard_textured.mat", src)
            new_path = Path(tmpdir) / "renamed.mat"

            result = rename_asset(str(src), "renamed.mat", dry_run=True)
            observed = (
                result["success"],
                result["code"],
                result["data"].get("meta_renamed"),
                _diag_details(result),
                src.exists(),
                new_path.exists(),
            )

        self.assertEqual(
            (
                False,
                "ASSET_OP_WRITE_FAILED",
                False,
                ["source_meta_missing"],
                True,
                False,
            ),
            observed,
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


class RenameControllerMainObjectTests(unittest.TestCase):
    """Issue #239 uses the same document fixtures with rename's metadata lifecycle."""

    def test_rename_changes_only_main_name_in_either_document_order(self) -> None:
        fixture = CopyControllerMainObjectTests
        expected_main = fixture._MAIN.replace(
            "  m_Name: MainController\n", '  m_Name: "RenamedController"\n',
        )
        for main_first in (False, True):
            for dry_run in (True, False):
                with self.subTest(main_first=main_first, dry_run=dry_run):
                    source_text = fixture._PREFIX + (
                        fixture._MAIN + fixture._STATE if main_first else fixture._STATE + fixture._MAIN
                    )
                    expected_text = fixture._PREFIX + (
                        expected_main + fixture._STATE if main_first else fixture._STATE + expected_main
                    )
                    with tempfile.TemporaryDirectory() as tmpdir:
                        root = Path(tmpdir)
                        assets = root / "Assets"
                        assets.mkdir()
                        src = assets / "Original.controller"
                        src.write_text(source_text, encoding="utf-8")
                        src_meta = assets / "Original.controller.meta"
                        src_meta.write_text(_DUMMY_META, encoding="utf-8")
                        other = assets / "Other.asset"
                        other.write_bytes(b"unrelated asset bytes")
                        dest = assets / "RenamedController.controller"
                        dest_meta = assets / "RenamedController.controller.meta"
                        result = rename_asset(
                            "Assets/Original.controller", "RenamedController.controller",
                            project_root=root, dry_run=dry_run,
                        )
                        self.assertEqual(
                            (True, "ASSET_RENAME_DRY_RUN" if dry_run else "ASSET_RENAME_APPLIED"),
                            (result["success"], result["code"]),
                        )
                        self.assertEqual(
                            (dry_run, dry_run, not dry_run, not dry_run),
                            (src.exists(), src_meta.exists(), dest.exists(), dest_meta.exists()),
                            "Preview keeps original paths; apply moves both asset and metadata",
                        )
                        self.assertEqual(
                            (source_text if dry_run else expected_text).encode(),
                            (src if dry_run else dest).read_bytes(),
                            "Rename may change only the main Controller name scalar",
                        )
                        self.assertEqual(_DUMMY_META.encode(), (src_meta if dry_run else dest_meta).read_bytes())
                        self.assertEqual(b"unrelated asset bytes", other.read_bytes())
                    self.assertEqual(
                        ("MainController", "RenamedController", {
                            "class_id": "91", "file_id": "9154321",
                            "type_name": "AnimatorController", "property_path": "m_Name",
                        }),
                        (result["data"]["m_name_before"], result["data"]["m_name_after"],
                         result["data"].get("m_name_target")),
                    )
                    if not dry_run:
                        self.assertEqual(True, result["data"]["meta_renamed"])

    def test_unreliable_controller_target_fails_before_staging_or_moving(self) -> None:
        fixture = CopyControllerMainObjectTests
        for body, reason, count in (
            (fixture._STATE, "main_object_not_unique", 0),
            (fixture._MAIN + fixture._MAIN.replace("9154321", "9154322"), "main_object_not_unique", 2),
            (fixture._MAIN.replace("&9154321", "&9154321 stripped"), "main_object_not_unique", 0),
            (fixture._MAIN.replace("AnimatorController:", "AnimatorState:"), "main_object_identity_invalid", 1),
            (fixture._MAIN + fixture._STATE.replace("&1102001", "&9154321"), "main_object_identity_invalid", 1),
            (fixture._MAIN.replace("  m_Name: MainController\n", ""), "root_name_invalid", 1),
            (fixture._MAIN + "  m_Name: Duplicate\n", "root_name_invalid", 1),
            (fixture._MAIN.replace("MainController\n", "|\n    Multiline\n"), "root_name_invalid", 1),
        ):
            for dry_run in (True, False):
                with self.subTest(reason=reason, count=count, body=body, dry_run=dry_run):
                    source_text = fixture._PREFIX + body
                    with tempfile.TemporaryDirectory() as tmpdir:
                        root = Path(tmpdir)
                        src = root / "Original.controller"
                        src.write_text(source_text, encoding="utf-8")
                        src_meta = root / "Original.controller.meta"
                        src_meta.write_text(_DUMMY_META, encoding="utf-8")
                        result = rename_asset(str(src), "RenamedController.controller", dry_run=dry_run)
                        assert_error_envelope(
                            result, code="ASSET_RENAME_MAIN_OBJECT_INVALID", field="asset_path",
                            message_match="Controller main object",
                            data={"field": "asset_path", "reason": reason, "matching_document_count": count},
                        )
                        self.assertEqual(
                            (["Original.controller", "Original.controller.meta"],
                             source_text.encode(), _DUMMY_META.encode()),
                            (sorted(path.name for path in root.iterdir()), src.read_bytes(), src_meta.read_bytes()),
                            "Invalid Controller selection leaves source, metadata, and directory entries unchanged",
                        )

    def test_controller_name_encoding_and_no_op_preserve_metadata(self) -> None:
        fixture = CopyControllerMainObjectTests
        for source_scalar, stem, expected_scalar, unchanged in (
            ("MainController", "Copy # Tail", '"Copy # Tail"', False),
            ("MainController", "# Hidden", '"# Hidden"', False),
            ("MainController", "true", '"true"', False),
            ("MainController", "日本語 #1", '"日本語 #1"', False),
            ("MainController", "MainController", "MainController", True),
            ('"Copy # Tail"', "Copy # Tail", '"Copy # Tail"', True),
            ("'Copy''s Tail'", "Copy's Tail", '"Copy\'s Tail"', False),
            ('"Copy \\x23 Tail"', "Copy # Tail", '"Copy # Tail"', False),
        ):
            with self.subTest(source_scalar=source_scalar, stem=stem):
                source_text = fixture._PREFIX + fixture._STATE + fixture._MAIN.replace(
                    "  m_Name: MainController\n", f"  m_Name: {source_scalar}\n",
                )
                expected_text = fixture._PREFIX + fixture._STATE + fixture._MAIN.replace(
                    "  m_Name: MainController\n", f"  m_Name: {expected_scalar}\n",
                )
                with tempfile.TemporaryDirectory() as tmpdir:
                    root = Path(tmpdir)
                    src = root / "Original.controller"
                    src.write_text(source_text, encoding="utf-8")
                    src_meta = root / "Original.controller.meta"
                    src_meta.write_text(_DUMMY_META, encoding="utf-8")
                    result = rename_asset(str(src), f"{stem}.controller", dry_run=False)
                    self.assertEqual((True, "ASSET_RENAME_APPLIED"), (result["success"], result["code"]))
                    self.assertEqual(expected_text.encode(), (root / f"{stem}.controller").read_bytes())
                    self.assertEqual(_DUMMY_META.encode(), (root / f"{stem}.controller.meta").read_bytes())
                    self.assertEqual((False, False), (src.exists(), src_meta.exists()))
                self.assertEqual(
                    (source_scalar, stem, True if unchanged else None),
                    (result["data"]["m_name_before"], result["data"]["m_name_after"],
                     result["data"].get("m_name_unchanged")),
                )

    def test_controller_metadata_move_failure_restores_source_bytes(self) -> None:
        from unittest.mock import patch

        fixture = CopyControllerMainObjectTests
        source_text = fixture._PREFIX + fixture._STATE + fixture._MAIN
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            src = root / "Original.controller"
            src.write_text(source_text, encoding="utf-8")
            src_meta = root / "Original.controller.meta"
            src_meta.write_text(_DUMMY_META, encoding="utf-8")
            original_rename = Path.rename

            def fail_metadata_move(path: Path, target: str | Path) -> Path:
                if path == src_meta:
                    raise OSError("injected Controller metadata move failure")
                return original_rename(path, target)

            with patch.object(Path, "rename", fail_metadata_move):
                result = rename_asset(str(src), "RenamedController.controller", dry_run=False)
            assert_error_envelope(
                result, code="ASSET_OP_WRITE_FAILED", message_match="injected Controller metadata move failure",
            )
            self.assertEqual(
                (["Original.controller", "Original.controller.meta"], source_text.encode(), _DUMMY_META.encode()),
                (sorted(path.name for path in root.iterdir()), src.read_bytes(), src_meta.read_bytes()),
                "A metadata move failure rolls back the asset move and removes staged content",
            )
        self.assertEqual(False, result["data"]["meta_renamed"])
        self.assertIn("meta_rename_failed", _diag_details(result))


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

    def test_source_status_probe_error_returns_rename_not_found_envelope(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            src = Path(tmpdir) / "existing.mat"
            src.write_text(_NO_M_NAME_CONTENT, encoding="utf-8")
            original_is_file = Path.is_file

            def fail_source_probe(path: Path) -> bool:
                if path == src:
                    raise OSError("stat failed")
                return original_is_file(path)

            try:
                Path.is_file = fail_source_probe  # type: ignore[assignment]
                result = rename_asset(str(src), "renamed.mat", dry_run=True)
            except OSError as exc:
                result = {"success": "raised", "code": type(exc).__name__, "data": {"error": str(exc)}}
            finally:
                Path.is_file = original_is_file  # type: ignore[assignment]

        self.assertEqual(
            (False, "ASSET_RENAME_NOT_FOUND", "status_error"),
            (result["success"], result["code"], result["data"].get("reason")),
        )

    def test_dest_status_probe_error_returns_write_failure_envelope(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            src = root / "existing.mat"
            dest = root / "renamed.mat"
            src.write_text(_NO_M_NAME_CONTENT, encoding="utf-8")
            original_exists = Path.exists

            def fail_dest_probe(path: Path) -> bool:
                if path == dest:
                    raise OSError("exists failed")
                return original_exists(path)

            try:
                Path.exists = fail_dest_probe  # type: ignore[assignment]
                result = rename_asset(str(src), "renamed.mat", dry_run=True)
            except OSError as exc:
                result = {"success": "raised", "code": type(exc).__name__, "diagnostics": []}
            finally:
                Path.exists = original_exists  # type: ignore[assignment]

        self.assertEqual(
            (False, "ASSET_OP_WRITE_FAILED", ["rename_dest_status_failed"]),
            (
                result["success"],
                result["code"],
                [item["detail"] for item in result["diagnostics"]],
            ),
        )

    def test_outside_project_asset_path_yields_invalid_path_envelope(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / "Project"
            root.mkdir()
            try:
                result = rename_asset(
                    "../outside.mat",
                    "renamed.mat",
                    dry_run=True,
                    project_root=root,
                )
            except ValueError as exc:
                result = {
                    "success": "raised",
                    "code": type(exc).__name__,
                    "data": {"message": str(exc)},
                    "diagnostics": [],
                }
            expected_candidate = str((root / "../outside.mat").resolve())

        self.assertEqual(
            (
                False,
                "ASSET_RENAME_INVALID_PATH",
                {
                    "input_path": "../outside.mat",
                    "normalized_candidate_path": expected_candidate,
                    "resolution_root": str(root),
                    "reason": "outside_project",
                },
                [
                    {
                        "detail": "rename_source_resolution",
                        "evidence": "reason=outside_project",
                    }
                ],
            ),
            (
                result["success"],
                result["code"],
                result["data"],
                result["diagnostics"],
            ),
        )

    def test_source_resolve_failure_returns_invalid_path_envelope(self) -> None:
        from unittest.mock import patch

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / "Project"
            (root / "Assets" / "Materials").mkdir(parents=True)

            with patch.object(Path, "resolve", side_effect=OSError("resolve failed")):
                try:
                    result = rename_asset(
                        "Assets/Materials/Input.mat",
                        "Renamed.mat",
                        dry_run=False,
                        project_root=root,
                    )
                except OSError as exc:
                    result = {
                        "success": "raised",
                        "code": type(exc).__name__,
                        "data": {"error": str(exc)},
                    }

        self.assertEqual(
            (False, "ASSET_RENAME_INVALID_PATH", "resolve_error"),
            (
                result["success"],
                result["code"],
                result["data"].get("reason"),
            ),
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

    def test_existing_dest_meta_yields_dest_exists_envelope_and_preserves_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            src = Path(tmpdir) / "original.mat"
            src_meta = Path(str(src) + ".meta")
            dest = Path(tmpdir) / "renamed.mat"
            dest_meta = Path(str(dest) + ".meta")
            shutil.copy(_FIXTURES / "standard_textured.mat", src)
            src_meta.write_text(_DUMMY_META, encoding="utf-8")
            dest_meta.write_text("guid: existingmeta\n", encoding="utf-8")

            result = rename_asset(str(src), "renamed.mat", dry_run=False)
            filesystem_state = (
                src.exists(),
                src_meta.exists(),
                dest.exists(),
                dest_meta.read_text(encoding="utf-8"),
            )

        self.assertEqual(
            (
                False,
                "ASSET_RENAME_DEST_EXISTS",
                True,
                True,
                False,
                "guid: existingmeta\n",
            ),
            (result["success"], result["code"], *filesystem_state),
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

    def test_path_like_new_name_is_rejected_without_moving_asset(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            src = Path(tmpdir) / "original.mat"
            shutil.copy(_FIXTURES / "standard_textured.mat", src)
            outside = Path(tmpdir) / "outside.mat"

            result = rename_asset(str(src), "../outside.mat", dry_run=False)
            src_exists = src.exists()
            outside_exists = outside.exists()

        self.assertEqual(
            (False, "ASSET_RENAME_INVALID_NAME", True, False),
            (result["success"], result["code"], src_exists, outside_exists),
        )
        self.assertEqual(
            {
                "input_name": "../outside.mat",
                "reason": "not_bare_filename",
            },
            result["data"],
        )

    def test_absolute_new_name_is_rejected_without_moving_asset(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            src = Path(tmpdir) / "original.mat"
            shutil.copy(_FIXTURES / "standard_textured.mat", src)
            absolute_dest = Path(tmpdir) / "absolute.mat"

            result = rename_asset(str(src), str(absolute_dest), dry_run=False)
            src_exists = src.exists()
            absolute_dest_exists = absolute_dest.exists()

        self.assertEqual(
            (False, "ASSET_RENAME_INVALID_NAME", True, False),
            (
                result["success"],
                result["code"],
                src_exists,
                absolute_dest_exists,
            ),
        )
        self.assertEqual("not_bare_filename", result["data"]["reason"])

    def test_failed_rename_cleanup_unlink_returns_write_failed_diagnostic(self) -> None:
        from unittest.mock import patch

        with tempfile.TemporaryDirectory() as tmpdir:
            src = Path(tmpdir) / "original.mat"
            dest = Path(tmpdir) / "renamed.mat"
            shutil.copy(_FIXTURES / "standard_textured.mat", src)
            Path(str(src) + ".meta").write_text(
                "guid: 1234567890abcdef1234567890abcdef\n",
                encoding="utf-8",
            )
            original_rename = Path.rename
            original_replace = Path.replace

            def fail_content_replace(path, target):
                if path.name.startswith(".renamed.mat") and target == dest:
                    raise OSError("replace failed")
                return original_replace(path, target)

            def fail_cleanup_unlink(path, *args, **kwargs):
                raise OSError("unlink failed")

            with (
                patch.object(Path, "replace", fail_content_replace),
                patch.object(Path, "rename", original_rename),
                patch.object(Path, "unlink", fail_cleanup_unlink),
            ):
                result = rename_asset(str(src), "renamed.mat", dry_run=False)
            filesystem_state = (src.exists(), dest.exists())

        self.assertEqual(
            (False, "ASSET_OP_WRITE_FAILED", True, False),
            (result["success"], result["code"], *filesystem_state),
        )
        self.assertEqual(
            ["content_replace_failed", "rename_cleanup_failed"],
            [diagnostic["detail"] for diagnostic in result["diagnostics"][:2]],
        )

    def test_rename_cleanup_exists_probe_failure_keeps_write_failed_envelope(self) -> None:
        from unittest.mock import patch

        with tempfile.TemporaryDirectory() as tmpdir:
            src = Path(tmpdir) / "original.mat"
            dest = Path(tmpdir) / "renamed.mat"
            shutil.copy(_FIXTURES / "standard_textured.mat", src)
            original_replace = Path.replace
            original_exists = Path.exists

            def fail_content_replace(path, target):
                if path.name.startswith(".renamed.mat") and target == dest:
                    raise OSError("replace failed")
                return original_replace(path, target)

            def fail_hidden_exists(path):
                if path.name.startswith("."):
                    raise OSError("exists failed")
                return original_exists(path)

            with (
                patch.object(Path, "replace", fail_content_replace),
                patch.object(Path, "exists", fail_hidden_exists),
            ):
                try:
                    result = rename_asset(str(src), "renamed.mat", dry_run=False)
                except OSError as exc:
                    result = {
                        "success": "raised",
                        "code": type(exc).__name__,
                        "message": str(exc),
                    }

        self.assertEqual(
            (False, "ASSET_OP_WRITE_FAILED"),
            (result["success"], result["code"]),
        )

    def test_failed_rename_preserves_source_text(self) -> None:
        from unittest.mock import patch

        with tempfile.TemporaryDirectory() as tmpdir:
            src = Path(tmpdir) / "original.mat"
            dest = Path(tmpdir) / "renamed.mat"
            shutil.copy(_FIXTURES / "standard_textured.mat", src)
            before = src.read_text(encoding="utf-8")
            original_rename = Path.rename

            def fail_asset_rename(path, target):
                if path == src and target == dest:
                    raise OSError("rename failed")
                return original_rename(path, target)

            with patch.object(Path, "rename", fail_asset_rename):
                result = rename_asset(str(src), "renamed.mat", dry_run=False)
            after = src.read_text(encoding="utf-8")

        self.assertEqual(
            (False, "ASSET_OP_WRITE_FAILED", before, False),
            (result["success"], result["code"], after, dest.exists()),
        )


class RenameAssetWithoutMetaTests(unittest.TestCase):
    """Pin the no-meta rename branch's flag value."""

    def test_apply_without_meta_sibling_fails_and_preserves_source(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            src = Path(tmpdir) / "original.mat"
            dest = Path(tmpdir) / "renamed.mat"
            shutil.copy(_FIXTURES / "standard_textured.mat", src)

            result = rename_asset(str(src), "renamed.mat", dry_run=False)
            filesystem_state = (src.exists(), dest.exists())

        self.assertEqual(
            (False, "ASSET_OP_WRITE_FAILED", True, False),
            (result["success"], result["code"], *filesystem_state),
        )
        self.assertIn("source_meta_missing", _diag_details(result))


class RenameAssetDiagnosticDetailTests(unittest.TestCase):
    """Pin documented rename diagnostic detail strings."""

    def test_text_without_m_name_emits_m_name_not_found_diag(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            src = Path(tmpdir) / "no_name.asset"
            src.write_text(_NO_M_NAME_CONTENT, encoding="utf-8")
            src_meta = Path(str(src) + ".meta")
            src_meta.write_text(_DUMMY_META, encoding="utf-8")

            result = rename_asset(str(src), "renamed.asset", dry_run=True)

        # Pin (success, ordered diagnostic-detail list) as one tuple so
        # an extra/missing/renamed detail is named alongside the
        # success branch in one failure message.
        self.assertEqual(
            (True, ["m_name_not_found"]),
            (result["success"], _diag_details(result)),
        )

    def test_meta_rename_failure_returns_write_failed_and_rolls_back_asset(
        self,
    ) -> None:
        from unittest.mock import patch

        with tempfile.TemporaryDirectory() as tmpdir:
            src = Path(tmpdir) / "original.mat"
            dest = Path(tmpdir) / "renamed.mat"
            shutil.copy(_FIXTURES / "standard_textured.mat", src)
            src_meta = Path(str(src) + ".meta")
            src_meta.write_text(_DUMMY_META, encoding="utf-8")
            dest_meta = Path(tmpdir) / "renamed.mat.meta"
            original_rename = Path.rename

            def fail_meta_rename(path: Path, target: Path) -> Path:
                if path == src_meta and target == dest_meta:
                    raise OSError("meta rename failed")
                return original_rename(path, target)

            with patch.object(Path, "rename", fail_meta_rename):
                result = rename_asset(str(src), "renamed.mat", dry_run=False)
            filesystem_state = (
                src.exists(),
                dest.exists(),
                src_meta.exists(),
                dest_meta.exists(),
            )

        self.assertEqual(
            (False, "ASSET_OP_WRITE_FAILED", True, False, True, False),
            (result["success"], result["code"], *filesystem_state),
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
                rename_asset(str(src), "renamed.mat", True)  # type: ignore[misc]  # intentional keyword-only probe

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
            src_meta = Path(str(src) + ".meta")
            src_meta.write_text(_DUMMY_META, encoding="utf-8")

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
