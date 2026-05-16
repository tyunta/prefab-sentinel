from __future__ import annotations

import tempfile
import unittest
import warnings
from pathlib import Path

from prefab_sentinel.unity_assets import (
    DEFAULT_EXCLUDED_DIR_NAMES,
    GUID_PATTERN,
    LOCAL_FILE_ID_PATTERN,
    REFERENCE_PATTERN,
    SOURCE_PREFAB_PATTERN,
    UNITY_BUILTIN_GUIDS,
    UNITY_TEXT_ASSET_SUFFIXES,
    ReferenceMatch,
    collect_project_guid_index,
    decode_text_file,
    extract_local_file_ids,
    extract_meta_guid,
    find_project_root,
    is_unity_builtin_guid,
    is_unity_text_asset,
    iter_references,
    looks_like_guid,
    normalize_guid,
    resolve_guid_to_asset_name,
)
from prefab_sentinel.unity_assets_path import (
    collect_package_guid_names,
    has_path_doubling,
    relative_to_root,
    resolve_scope_path,
)


class GuidPatternTests(unittest.TestCase):
    def test_matches_standard_guid(self) -> None:
        m = GUID_PATTERN.search("guid: abcdef01234567890abcdef012345678")
        self.assertIsNotNone(m)
        assert m is not None
        self.assertEqual(m.group(1), "abcdef01234567890abcdef012345678")

    def test_matches_uppercase(self) -> None:
        m = GUID_PATTERN.search("guid: ABCDEF01234567890ABCDEF012345678")
        self.assertIsNotNone(m)

    def test_no_match_short_guid(self) -> None:
        self.assertIsNone(GUID_PATTERN.search("guid: abcdef0123456789"))

    def test_no_match_non_hex(self) -> None:
        self.assertIsNone(GUID_PATTERN.search("guid: zzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzz"))

    def test_colon_spacing_variations(self) -> None:
        m = GUID_PATTERN.search("guid:abcdef01234567890abcdef012345678")
        self.assertIsNotNone(m)

    def test_word_boundary(self) -> None:
        m = GUID_PATTERN.search("xguid: abcdef01234567890abcdef012345678")
        self.assertIsNone(m)


class LocalFileIdPatternTests(unittest.TestCase):
    def test_matches_standard(self) -> None:
        text = "--- !u!114 &12345678"
        m = LOCAL_FILE_ID_PATTERN.search(text)
        self.assertIsNotNone(m)
        assert m is not None
        self.assertEqual(m.group(1), "12345678")

    def test_matches_negative_id(self) -> None:
        m = LOCAL_FILE_ID_PATTERN.search("--- !u!1 &-9876543210")
        self.assertIsNotNone(m)
        assert m is not None
        self.assertEqual(m.group(1), "-9876543210")

    def test_multiline(self) -> None:
        text = "some header\n--- !u!4 &100\ndata: value\n--- !u!1 &200"
        ids = {m.group(1) for m in LOCAL_FILE_ID_PATTERN.finditer(text)}
        self.assertEqual(ids, {"100", "200"})


class ReferencePatternTests(unittest.TestCase):
    def test_full_reference(self) -> None:
        text = "{fileID: 123, guid: abcdef01234567890abcdef012345678, type: 3}"
        m = REFERENCE_PATTERN.search(text)
        self.assertIsNotNone(m)
        assert m is not None
        self.assertEqual(m.group(1), "123")
        self.assertEqual(m.group(2), "abcdef01234567890abcdef012345678")
        self.assertEqual(m.group(3), "3")

    def test_file_id_only(self) -> None:
        m = REFERENCE_PATTERN.search("{fileID: 0}")
        self.assertIsNotNone(m)
        assert m is not None
        self.assertEqual(m.group(1), "0")
        self.assertIsNone(m.group(2))
        self.assertIsNone(m.group(3))

    def test_negative_file_id(self) -> None:
        m = REFERENCE_PATTERN.search("{fileID: -100, guid: abcdef01234567890abcdef012345678, type: 2}")
        self.assertIsNotNone(m)
        assert m is not None
        self.assertEqual(m.group(1), "-100")


class SourcePrefabPatternTests(unittest.TestCase):
    def test_source_prefab(self) -> None:
        text = "m_SourcePrefab: {fileID: 100100000, guid: abcdef01234567890abcdef012345678, type: 3}"
        m = SOURCE_PREFAB_PATTERN.search(text)
        self.assertIsNotNone(m)
        assert m is not None
        self.assertEqual(m.group(1), "100100000")
        self.assertEqual(m.group(2), "abcdef01234567890abcdef012345678")
        self.assertEqual(m.group(3), "3")

    def test_parent_prefab(self) -> None:
        text = "m_ParentPrefab: {fileID: 200, guid: 11111111111111111111111111111111, type: 2}"
        m = SOURCE_PREFAB_PATTERN.search(text)
        self.assertIsNotNone(m)


class ConstantsTests(unittest.TestCase):
    def test_builtin_guids_are_lowercase_32_hex(self) -> None:
        for guid in UNITY_BUILTIN_GUIDS:
            self.assertEqual(len(guid), 32)
            self.assertTrue(
                looks_like_guid(guid),
                msg=f"UNITY_BUILTIN_GUIDS entry {guid!r} must satisfy looks_like_guid()",
            )

    def test_text_asset_suffixes_are_lowercase(self) -> None:
        for suffix in UNITY_TEXT_ASSET_SUFFIXES:
            self.assertEqual(suffix, suffix.lower())
            self.assertTrue(
                suffix.startswith("."),
                msg=f"UNITY_TEXT_ASSET_SUFFIXES entry {suffix!r} must start with '.'",
            )

    def test_excluded_dirs_are_lowercase(self) -> None:
        for name in DEFAULT_EXCLUDED_DIR_NAMES:
            self.assertEqual(name, name.lower())

    def test_prefab_in_text_assets(self) -> None:
        self.assertIn(".prefab", UNITY_TEXT_ASSET_SUFFIXES)

    def test_unity_in_text_assets(self) -> None:
        self.assertIn(".unity", UNITY_TEXT_ASSET_SUFFIXES)


class LooksLikeGuidTests(unittest.TestCase):
    def test_valid_lowercase(self) -> None:
        guid = "abcdef01234567890abcdef012345678"
        self.assertTrue(
            looks_like_guid(guid),
            msg=f"32-char lowercase hex {guid!r} must be accepted as a GUID",
        )

    def test_valid_uppercase(self) -> None:
        guid = "ABCDEF01234567890ABCDEF012345678"
        self.assertTrue(
            looks_like_guid(guid),
            msg=f"32-char uppercase hex {guid!r} must be accepted as a GUID",
        )

    def test_valid_mixed(self) -> None:
        guid = "aBcDeF01234567890AbCdEf012345678"
        self.assertTrue(
            looks_like_guid(guid),
            msg=f"32-char mixed-case hex {guid!r} must be accepted as a GUID",
        )

    def test_too_short(self) -> None:
        guid = "abcdef0123456789"
        self.assertFalse(
            looks_like_guid(guid),
            msg=f"16-char hex {guid!r} (off-length) must be rejected",
        )

    def test_too_long(self) -> None:
        guid = "abcdef01234567890abcdef0123456789"
        self.assertFalse(
            looks_like_guid(guid),
            msg=f"33-char hex {guid!r} (off-length) must be rejected",
        )

    def test_non_hex(self) -> None:
        guid = "zzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzz"
        self.assertFalse(
            looks_like_guid(guid),
            msg=f"32-char non-hex {guid!r} must be rejected",
        )

    def test_empty(self) -> None:
        self.assertFalse(
            looks_like_guid(""),
            msg="empty string must be rejected as a GUID",
        )

    def test_with_hyphens(self) -> None:
        guid = "abcdef01-2345-6789-0abc-def012345678"
        self.assertFalse(
            looks_like_guid(guid),
            msg=f"hyphenated form {guid!r} must be rejected (Unity GUIDs are unhyphenated)",
        )


class NormalizeGuidTests(unittest.TestCase):
    def test_lowercase(self) -> None:
        self.assertEqual(normalize_guid("ABCDEF01234567890ABCDEF012345678"), "abcdef01234567890abcdef012345678")

    def test_strips_whitespace(self) -> None:
        self.assertEqual(normalize_guid("  abc123  "), "abc123")

    def test_already_normalized(self) -> None:
        guid = "abcdef01234567890abcdef012345678"
        self.assertEqual(normalize_guid(guid), guid)


class IsUnityBuiltinGuidTests(unittest.TestCase):
    def test_builtin_guid(self) -> None:
        guid = "0000000000000000e000000000000000"
        self.assertTrue(
            is_unity_builtin_guid(guid),
            msg=f"documented built-in GUID {guid!r} must be recognised",
        )

    def test_builtin_guid_uppercase(self) -> None:
        guid = "0000000000000000E000000000000000"
        self.assertTrue(
            is_unity_builtin_guid(guid),
            msg=f"built-in GUID is case-insensitive: {guid!r} must be recognised",
        )

    def test_non_builtin(self) -> None:
        guid = "abcdef01234567890abcdef012345678"
        self.assertFalse(
            is_unity_builtin_guid(guid),
            msg=f"non-built-in GUID {guid!r} must not be classified as built-in",
        )


class IsUnityTextAssetTests(unittest.TestCase):
    def test_prefab(self) -> None:
        path = Path("test.prefab")
        self.assertTrue(
            is_unity_text_asset(path),
            msg=f"{path} must be classified as a Unity text asset (.prefab in allow-list)",
        )

    def test_unity_scene(self) -> None:
        path = Path("test.unity")
        self.assertTrue(
            is_unity_text_asset(path),
            msg=f"{path} must be classified as a Unity text asset (.unity in allow-list)",
        )

    def test_material(self) -> None:
        path = Path("test.mat")
        self.assertTrue(
            is_unity_text_asset(path),
            msg=f"{path} must be classified as a Unity text asset (.mat in allow-list)",
        )

    def test_case_insensitive(self) -> None:
        path = Path("test.Prefab")
        self.assertTrue(
            is_unity_text_asset(path),
            msg=f"suffix matching must be case-insensitive: {path}",
        )

    def test_non_text_asset(self) -> None:
        path = Path("test.png")
        self.assertFalse(
            is_unity_text_asset(path),
            msg=f"{path} (.png) must be rejected — binary asset, not in allow-list",
        )

    def test_cs_file(self) -> None:
        path = Path("test.cs")
        self.assertFalse(
            is_unity_text_asset(path),
            msg=f"{path} (.cs) must be rejected — source file, not a Unity text asset",
        )

    def test_meta_file(self) -> None:
        path = Path("test.meta")
        self.assertFalse(
            is_unity_text_asset(path),
            msg=f"{path} (.meta) must be rejected — companion file, not a text asset",
        )


class DecodeTextFileTests(unittest.TestCase):
    def test_utf8(self) -> None:
        with tempfile.NamedTemporaryFile(suffix=".txt", delete=False) as f:
            f.write(b"hello world")
            f.flush()
            result = decode_text_file(Path(f.name))
        self.assertEqual(result, "hello world")

    def test_cp932_bytes_raise(self) -> None:
        """T5: CP932-encoded bytes that are invalid UTF-8 must raise
        ``UnicodeDecodeError``. No fallback encoding is applied — Unity
        writes its text assets as UTF-8 and any deviation is the caller's
        responsibility to surface (fail-fast)."""
        with tempfile.NamedTemporaryFile(suffix=".txt", delete=False) as f:
            # CP932-specific character that is invalid UTF-8.
            f.write("テスト".encode("cp932"))
            f.flush()
            with self.assertRaises(UnicodeDecodeError):
                decode_text_file(Path(f.name))

    def test_arbitrary_non_utf8_bytes_raise(self) -> None:
        """T-77-A: arbitrary non-UTF-8 byte sequences must raise
        ``UnicodeDecodeError``; no encoding fallback or content sniffing is
        performed (fail-fast)."""
        with tempfile.NamedTemporaryFile(suffix=".bin", delete=False) as f:
            # 0x80–0x83 are continuation bytes with no leading byte: invalid UTF-8.
            f.write(b"\x80\x81\x82\x83")
            f.flush()
            with self.assertRaises(UnicodeDecodeError):
                decode_text_file(Path(f.name))

    def test_multiline_utf8_with_non_ascii_roundtrip(self) -> None:
        """T-77-C: a multi-line UTF-8 string containing non-ASCII characters
        round-trips through ``decode_text_file`` byte-for-byte."""
        original = "line one\nテスト\n☃ snowman\n"
        with tempfile.NamedTemporaryFile(suffix=".txt", delete=False) as f:
            f.write(original.encode("utf-8"))
            f.flush()
            result = decode_text_file(Path(f.name))
        self.assertEqual(result, original)


class ExtractMetaGuidTests(unittest.TestCase):
    def test_standard_meta(self) -> None:
        with tempfile.NamedTemporaryFile(suffix=".meta", delete=False, mode="w", encoding="utf-8") as f:
            f.write("fileFormatVersion: 2\nguid: ABCDEF01234567890abcdef012345678\n")
            f.flush()
            result = extract_meta_guid(Path(f.name))
        self.assertEqual(result, "abcdef01234567890abcdef012345678")

    def test_no_guid(self) -> None:
        with tempfile.NamedTemporaryFile(suffix=".meta", delete=False, mode="w", encoding="utf-8") as f:
            f.write("fileFormatVersion: 2\nno guid here\n")
            f.flush()
            result = extract_meta_guid(Path(f.name))
        self.assertIsNone(result)


class ExtractLocalFileIdsTests(unittest.TestCase):
    def test_single_id(self) -> None:
        result = extract_local_file_ids("--- !u!114 &12345678")
        self.assertEqual(result, {"12345678"})

    def test_multiple_ids(self) -> None:
        text = "--- !u!1 &100\ndata\n--- !u!4 &200\nmore\n--- !u!114 &300"
        result = extract_local_file_ids(text)
        self.assertEqual(result, {"100", "200", "300"})

    def test_no_ids(self) -> None:
        self.assertEqual(extract_local_file_ids("no ids here"), set())

    def test_negative_id(self) -> None:
        result = extract_local_file_ids("--- !u!1 &-500")
        self.assertEqual(result, {"-500"})


class IterReferencesTests(unittest.TestCase):
    def test_single_reference_pins_file_id_guid_type_line_and_column(self) -> None:
        text = "m_Script: {fileID: 11500000, guid: abcdef01234567890abcdef012345678, type: 3}"

        refs = iter_references(text, include_location=True)

        # Column is the 1-based offset of the opening '{' on the line.
        # Pin every observable field as a single tuple so a mutation that
        # shifts the column counter or drops the line counter is named.
        self.assertEqual(1, len(refs))
        ref = refs[0]
        self.assertEqual(
            (
                "11500000",
                "abcdef01234567890abcdef012345678",
                "3",
                1,
                text.index("{") + 1,
            ),
            (ref.file_id, ref.guid, ref.ref_type, ref.line, ref.column),
        )

    def test_multiple_references(self) -> None:
        text = (
            "line1: {fileID: 100}\n"
            "line2: {fileID: 200, guid: abcdef01234567890abcdef012345678, type: 2}\n"
        )
        refs = iter_references(text)
        self.assertEqual(len(refs), 2)
        self.assertEqual(refs[0].file_id, "100")
        self.assertEqual(refs[1].file_id, "200")

    def test_without_location(self) -> None:
        text = "ref: {fileID: 42}"
        refs = iter_references(text, include_location=False)
        self.assertEqual(len(refs), 1)
        self.assertEqual(refs[0].line, 0)
        self.assertEqual(refs[0].column, 0)

    def test_line_numbers_multiline(self) -> None:
        text = "line1\n{fileID: 1}\nline3\n{fileID: 2}"
        refs = iter_references(text, include_location=True)
        self.assertEqual(refs[0].line, 2)
        self.assertEqual(refs[1].line, 4)

    def test_no_references(self) -> None:
        self.assertEqual(iter_references("no refs here"), [])

    def test_guid_normalized(self) -> None:
        text = "{fileID: 1, guid: ABCDEF01234567890ABCDEF012345678, type: 3}"
        refs = iter_references(text)
        self.assertEqual(refs[0].guid, "abcdef01234567890abcdef012345678")

    def test_no_guid_gives_empty_string(self) -> None:
        text = "{fileID: 0}"
        refs = iter_references(text)
        self.assertEqual(refs[0].guid, "")

    def test_raw_preserved(self) -> None:
        raw = "{fileID: 123, guid: abcdef01234567890abcdef012345678, type: 3}"
        refs = iter_references(f"prefix: {raw}")
        self.assertEqual(refs[0].raw, raw)


class ReferenceMatchDataclassTests(unittest.TestCase):
    # ``test_slots_declared`` is a Tier 3 structural check retained per the
    # spec's Tier 3 Justification entry: collapsing it into the co-located
    # T1 ``test_create_pins_every_field`` would lose the slot-declaration
    # regression net at the source-shape level.  See observations.md.
    def test_slots_declared_on_dataclass(self) -> None:
        self.assertTrue(
            hasattr(ReferenceMatch, "__slots__"),
            msg="ReferenceMatch must declare __slots__ (Tier 3 source-shape regression net)",
        )

    def test_create_pins_every_field(self) -> None:
        ref = ReferenceMatch(
            file_id="1", guid="abc", ref_type="3", line=2, column=4, raw="{}"
        )

        # Materialise every slot as a single tuple so a mutation that
        # swaps two fields (e.g. line and column) is named in the same
        # failure message — the prior pair of equality checks accepted
        # such a swap silently.
        self.assertEqual(
            ("1", "abc", "3", 2, 4, "{}"),
            (ref.file_id, ref.guid, ref.ref_type, ref.line, ref.column, ref.raw),
        )


class CollectProjectGuidIndexTests(unittest.TestCase):
    def test_collects_meta_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            meta = root / "test.cs.meta"
            meta.write_text("guid: abcdef01234567890abcdef012345678\n", encoding="utf-8")
            index = collect_project_guid_index(root)
        self.assertIn("abcdef01234567890abcdef012345678", index)
        self.assertEqual(index["abcdef01234567890abcdef012345678"].name, "test.cs")

    def test_excludes_default_dirs(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            lib = root / "Library"
            lib.mkdir()
            meta = lib / "hidden.meta"
            meta.write_text("guid: abcdef01234567890abcdef012345678\n", encoding="utf-8")
            index = collect_project_guid_index(root)
        self.assertEqual(len(index), 0)

    def test_custom_exclusions(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            custom = root / "mydir"
            custom.mkdir()
            meta = custom / "file.meta"
            meta.write_text("guid: abcdef01234567890abcdef012345678\n", encoding="utf-8")
            index = collect_project_guid_index(root, excluded_dir_names={"mydir"})
        self.assertEqual(len(index), 0)

    def test_non_meta_ignored(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "script.cs").write_text("code", encoding="utf-8")
            index = collect_project_guid_index(root)
        self.assertEqual(len(index), 0)

    def test_meta_without_guid_skipped(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            meta = root / "empty.meta"
            meta.write_text("fileFormatVersion: 2\n", encoding="utf-8")
            index = collect_project_guid_index(root)
        self.assertEqual(len(index), 0)

    def test_empty_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            index = collect_project_guid_index(Path(tmpdir))
        self.assertEqual(len(index), 0)

    def test_package_cache_included_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            pkg = root / "Library" / "PackageCache" / "com.unity.ugui@1.0.0"
            pkg.mkdir(parents=True)
            meta = pkg / "Image.cs.meta"
            meta.write_text("guid: aaaaaaaabbbbbbbbccccccccdddddddd\n", encoding="utf-8")
            index = collect_project_guid_index(root)
        self.assertIn("aaaaaaaabbbbbbbbccccccccdddddddd", index)

    def test_package_cache_excluded_when_disabled(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            pkg = root / "Library" / "PackageCache" / "com.unity.ugui@1.0.0"
            pkg.mkdir(parents=True)
            meta = pkg / "Image.cs.meta"
            meta.write_text("guid: aaaaaaaabbbbbbbbccccccccdddddddd\n", encoding="utf-8")
            index = collect_project_guid_index(root, include_package_cache=False)
        self.assertEqual(len(index), 0)

    def test_library_still_excluded_outside_package_cache(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            lib = root / "Library"
            lib.mkdir()
            meta = lib / "random.meta"
            meta.write_text("guid: 11111111222222223333333344444444\n", encoding="utf-8")
            # Library/ root is excluded, only PackageCache subfolder is scanned
            index = collect_project_guid_index(root)
        self.assertNotIn("11111111222222223333333344444444", index)

    def test_package_cache_no_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            # No Library/PackageCache exists — should not error
            index = collect_project_guid_index(root)
        self.assertEqual(len(index), 0)

    def test_unreadable_meta_skipped(self) -> None:
        """Binary .meta files that fail decode should be silently skipped."""
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            good = root / "good.cs.meta"
            good.write_text("guid: abcdef01234567890abcdef012345678\n", encoding="utf-8")
            bad = root / "bad.asset.meta"
            bad.write_bytes(b"\x80\x81\x82\x83" * 100)
            index = collect_project_guid_index(root)
        self.assertIn("abcdef01234567890abcdef012345678", index)
        self.assertEqual(len(index), 1)

    def test_multiple_meta_files_collected(self) -> None:
        """Multiple .meta files should all have their GUIDs extracted."""
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            for i in range(20):
                guid = f"{i:032x}"
                meta = root / f"file_{i}.cs.meta"
                meta.write_text(f"guid: {guid}\n", encoding="utf-8")
            index = collect_project_guid_index(root)
        self.assertEqual(len(index), 20)


class FindProjectRootTests(unittest.TestCase):
    def test_directory_with_assets(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "Assets").mkdir()
            result = find_project_root(root)
        self.assertEqual(result, root.resolve())

    def test_subdirectory_finds_parent(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "Assets").mkdir()
            sub = root / "Assets" / "Scripts"
            sub.mkdir()
            result = find_project_root(sub)
        self.assertEqual(result, root.resolve())

    def test_file_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "Assets").mkdir()
            f = root / "Assets" / "test.cs"
            f.write_text("code", encoding="utf-8")
            result = find_project_root(f)
        self.assertEqual(result, root.resolve())

    def test_no_assets_returns_start(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            result = find_project_root(Path(tmpdir))
        self.assertEqual(result, Path(tmpdir).resolve())


class ResolveScopePathTests(unittest.TestCase):
    def test_relative_scope(self) -> None:
        project_root = Path("/project")
        result = resolve_scope_path("Assets/Prefabs", project_root)
        self.assertEqual(result, Path("/project/Assets/Prefabs").resolve())

    def test_absolute_scope(self) -> None:
        result = resolve_scope_path("/absolute/path", Path("/project"))
        self.assertEqual(result, Path("/absolute/path").resolve())

    def test_warns_on_path_doubling(self) -> None:
        """resolve_scope_path emits a warning when the resolved path contains doubled Assets/ segments."""
        with tempfile.TemporaryDirectory() as temp_dir:
            # Simulate a project_root that already includes "Assets/Tyunta"
            # and a scope that also starts with "Assets/Tyunta".
            # This would only happen if project_root was misconfigured.
            fake_root = Path(temp_dir) / "Assets" / "Tyunta"
            fake_root.mkdir(parents=True)
            with warnings.catch_warnings(record=True) as w:
                warnings.simplefilter("always")
                resolve_scope_path("Assets/Tyunta/Test.prefab", fake_root)
                doubled_warnings = [
                    x for x in w if "Path doubling detected" in str(x.message)
                ]
                self.assertEqual(len(doubled_warnings), 1)


class HasPathDoublingTests(unittest.TestCase):
    def test_detects_doubled_assets(self) -> None:
        path = "Assets/Tyunta/Assets/Tyunta/Materials/foo.mat"
        self.assertTrue(
            has_path_doubling(path),
            msg=f"doubled segment must be detected in {path!r}",
        )

    def test_no_doubling_for_normal_path(self) -> None:
        path = "Assets/Tyunta/Materials/foo.mat"
        self.assertFalse(
            has_path_doubling(path),
            msg=f"single-segment path must not trigger doubling: {path!r}",
        )

    def test_detects_windows_backslash_path(self) -> None:
        path = "Assets\\Tyunta\\Assets\\Tyunta\\Materials\\foo.mat"
        self.assertTrue(
            has_path_doubling(path),
            msg=f"backslash separator must still surface doubling: {path!r}",
        )

    def test_no_doubling_for_absolute_path(self) -> None:
        path = "/project/Assets/Tyunta/Materials/foo.mat"
        self.assertFalse(
            has_path_doubling(path),
            msg=f"single Assets segment with absolute prefix must not trigger doubling: {path!r}",
        )

    def test_detects_case_insensitive(self) -> None:
        path = "assets/Tyunta/Assets/Tyunta/Materials/foo.mat"
        self.assertTrue(
            has_path_doubling(path),
            msg=f"case-insensitive doubling detection must surface: {path!r}",
        )


class ResolveGuidToAssetNameTests(unittest.TestCase):
    def test_known_guid_returns_relative_path(self) -> None:
        proj = Path("/project")
        index = {"aabb" * 8: Path("/project/Assets/Scripts/Foo.cs")}
        result = resolve_guid_to_asset_name("aabb" * 8, index, proj)
        self.assertEqual(result, "Assets/Scripts/Foo.cs")

    def test_unknown_guid_returns_empty(self) -> None:
        result = resolve_guid_to_asset_name("dead" * 8, {}, Path("/project"))
        self.assertEqual(result, "")

    def test_no_project_root_returns_posix_path(self) -> None:
        index = {"aabb" * 8: Path("/some/Assets/Foo.cs")}
        result = resolve_guid_to_asset_name("aabb" * 8, index)
        self.assertIn("Foo.cs", result)

    def test_normalizes_guid_case(self) -> None:
        index = {"aabb" * 8: Path("/project/Assets/Bar.cs")}
        result = resolve_guid_to_asset_name("AABB" * 8, index, Path("/project"))
        self.assertEqual(result, "Assets/Bar.cs")


class CollectPackageGuidNamesTests(unittest.TestCase):
    def test_returns_package_names_from_lock_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            pkg_dir = Path(tmpdir) / "Packages"
            pkg_dir.mkdir()
            lock = pkg_dir / "packages-lock.json"
            lock.write_text(
                '{"dependencies": {"com.unity.textmeshpro": {"version": "3.0.0"}, '
                '"com.unity.ugui": {"version": "1.0.0"}}}',
                encoding="utf-8",
            )
            result = collect_package_guid_names(Path(tmpdir))
        self.assertIn("com.unity.textmeshpro", result)
        self.assertIn("com.unity.ugui", result)

    def test_missing_lock_file_returns_empty(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            result = collect_package_guid_names(Path(tmpdir))
        self.assertEqual(result, {})


class TestRelativeToRoot(unittest.TestCase):
    """Tests for the relative_to_root() free function."""

    def test_inside_root(self) -> None:
        root = Path("/project")
        result = relative_to_root(root / "Assets" / "Foo.prefab", root)
        self.assertEqual(result, "Assets/Foo.prefab")

    def test_outside_root(self) -> None:
        root = Path("/project")
        other = Path("/other/Bar.prefab")
        result = relative_to_root(other, root)
        # Falls back to resolved absolute path
        self.assertTrue(
            result.endswith("Bar.prefab"),
            msg=f"out-of-root path must keep its filename in fallback: {result!r}",
        )
        self.assertNotIn("project", result)


if __name__ == "__main__":
    unittest.main()
