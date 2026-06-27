"""Coverage rows for the two gap blocks in
``prefab_sentinel/services/reference_resolver.py`` (issue #181).

Block 1 — broken-reference scan: external prefab/asset target whose
fileID is absent from the target's local-IDs (``missing_local_id``
record with the external classification key).

Block 2 — ``where_used`` path-form lookup: every documented error code
path of the path-form input (non-existent, meta missing, meta
undecodable, meta GUID malformed) and the path-form success row.

Block 3 — ``_build_top_missing_entry`` value-pin matrix (issue #207):
helper-level tests over the breakdown × per_source corners and the
``asset_name`` resolution relative to the scan project root, killing
the named survived mutants enumerated in the issue body.
"""

from __future__ import annotations

import tempfile
import unittest
from collections import Counter
from pathlib import Path

from prefab_sentinel.contracts import Severity
from prefab_sentinel.services.reference_resolver import (
    ReferenceResolverService,
    _build_top_missing_entry,
)
from tests._assertion_helpers import assert_error_envelope
from tests.bridge_test_helpers import write_file

_TARGET_GUID = "1111111111111111111111111111aaaa"
_SOURCE_GUID = "2222222222222222222222222222bbbb"
_OTHER_GUID = "3333333333333333333333333333cccc"


def _seed_minimal_project(root: Path) -> None:
    (root / "Assets").mkdir(parents=True, exist_ok=True)


class ScanBrokenReferencesExternalAssetTests(unittest.TestCase):
    """Block 1 — ``scan_broken_references`` ``missing_local_id_external``
    arm fires when the source references a non-prefab target by GUID and
    the target's local-IDs do not contain the requested fileID.
    """

    def test_external_asset_missing_fileid_reports_missing_local_id(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            _seed_minimal_project(root)
            # Target is a non-prefab text asset (``.asset`` here so the
            # ``_should_validate_external_file_id`` predicate returns
            # True; ``.prefab`` targets are skipped by design).
            target_text = (
                "%YAML 1.1\n"
                "--- !u!114 &11400000\n"
                "MonoBehaviour:\n"
                "  m_Name: Anchored\n"
                "--- !u!114 &22200000\n"
                "MonoBehaviour:\n"
                "  m_Name: Other\n"
            )
            write_file(root / "Assets" / "Target.asset", target_text)
            write_file(
                root / "Assets" / "Target.asset.meta",
                f"fileFormatVersion: 2\nguid: {_TARGET_GUID}\n",
            )
            # Source references fileID 99999999 which is NOT present in
            # the target's local IDs (11400000, 22200000).
            source_text = (
                "%YAML 1.1\n"
                "--- !u!114 &33300000\n"
                "MonoBehaviour:\n"
                "  m_Reference: {fileID: 99999999, guid: "
                f"{_TARGET_GUID}, type: 2}}\n"
            )
            write_file(root / "Assets" / "Source.asset", source_text)
            write_file(
                root / "Assets" / "Source.asset.meta",
                f"fileFormatVersion: 2\nguid: {_SOURCE_GUID}\n",
            )

            svc = ReferenceResolverService(project_root=root)
            response = svc.scan_broken_references(
                "Assets",
                include_diagnostics=True,
            )

        assert_error_envelope(response, code="REF_SCAN_BROKEN", severity="error")
        self.assertEqual(1, response.data["broken_count"])
        self.assertEqual(1, response.data["categories"]["missing_local_id"])
        self.assertEqual(0, response.data["categories"]["missing_asset"])
        # The diagnostic surfaces the missing fileID and the target's
        # repository-relative path so the operator can correlate
        # source <-> target without re-scanning.
        diag = next(
            d
            for d in response.diagnostics
            if d.detail == "missing_local_id"
        )
        self.assertIn("99999999", diag.evidence)
        self.assertIn("Assets/Target.asset", diag.evidence)


class WhereUsedPathFormErrorPathTests(unittest.TestCase):
    """Block 2 — every documented error code path of the path-form
    ``where_used`` lookup."""

    def test_non_existent_path_returns_ref404(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            _seed_minimal_project(root)
            svc = ReferenceResolverService(project_root=root)
            response = svc.where_used("Assets/Missing.asset")
        assert_error_envelope(
            response,
            code="REF404",
            severity="error",
            data={
                "asset_or_guid": "Assets/Missing.asset",
                "read_only": True,
            },
        )

    def test_meta_file_missing_returns_ref001(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            _seed_minimal_project(root)
            write_file(root / "Assets" / "Orphan.asset", "ignored body\n")
            svc = ReferenceResolverService(project_root=root)
            response = svc.where_used("Assets/Orphan.asset")
        assert_error_envelope(
            response,
            code="REF001",
            severity="error",
            data={
                "asset_or_guid": "Assets/Orphan.asset",
                "read_only": True,
            },
        )

    def test_meta_undecodable_returns_ref001(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            _seed_minimal_project(root)
            asset = root / "Assets" / "Binary.asset"
            asset.parent.mkdir(parents=True, exist_ok=True)
            asset.write_text("body\n", encoding="utf-8")
            (root / "Assets" / "Binary.asset.meta").write_bytes(
                b"\xff\xfe\xfd\xfc not utf-8 \x80\x81"
            )
            svc = ReferenceResolverService(project_root=root)
            response = svc.where_used("Assets/Binary.asset")
        assert_error_envelope(
            response,
            code="REF001",
            severity="error",
            data={
                "asset_or_guid": "Assets/Binary.asset",
                "read_only": True,
            },
        )

    def test_meta_guid_malformed_returns_ref001(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            _seed_minimal_project(root)
            asset = root / "Assets" / "Bad.asset"
            asset.parent.mkdir(parents=True, exist_ok=True)
            asset.write_text("body\n", encoding="utf-8")
            (root / "Assets" / "Bad.asset.meta").write_text(
                "fileFormatVersion: 2\nguid: NOT-A-VALID-GUID\n",
                encoding="utf-8",
            )
            svc = ReferenceResolverService(project_root=root)
            response = svc.where_used("Assets/Bad.asset")
        assert_error_envelope(
            response,
            code="REF001",
            severity="error",
            data={
                "asset_or_guid": "Assets/Bad.asset",
                "read_only": True,
            },
        )

    def test_path_form_success_lists_referencing_asset(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            _seed_minimal_project(root)
            target_path = root / "Assets" / "Target.asset"
            write_file(
                target_path,
                "%YAML 1.1\n--- !u!114 &11400000\nMonoBehaviour:\n  m_Name: T\n",
            )
            write_file(
                root / "Assets" / "Target.asset.meta",
                f"fileFormatVersion: 2\nguid: {_TARGET_GUID}\n",
            )
            referrer_text = (
                "%YAML 1.1\n"
                "--- !u!114 &22200000\n"
                "MonoBehaviour:\n"
                "  m_Reference: {fileID: 11400000, guid: "
                f"{_TARGET_GUID}, type: 2}}\n"
            )
            write_file(root / "Assets" / "Referrer.asset", referrer_text)
            write_file(
                root / "Assets" / "Referrer.asset.meta",
                f"fileFormatVersion: 2\nguid: {_OTHER_GUID}\n",
            )

            svc = ReferenceResolverService(project_root=root)
            response = svc.where_used("Assets/Target.asset")

        self.assertTrue(response.success)
        self.assertEqual("REF_WHERE_USED", response.code)
        self.assertEqual(Severity.INFO, response.severity)
        self.assertEqual(_TARGET_GUID, response.data["guid"])
        self.assertEqual("Assets/Target.asset", response.data["asset_path"])
        self.assertEqual(1, response.data["usage_count"])
        self.assertEqual(1, response.data["returned_usages"])
        usage_paths = [usage["path"] for usage in response.data["usages"]]
        self.assertIn("Assets/Referrer.asset", usage_paths)


class WhereUsedMissingGuidScanTests(unittest.TestCase):
    def test_scoped_missing_guid_returns_usages_with_missing_target_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            _seed_minimal_project(root)
            missing_guid = "9999999999999999999999999999abcd"
            raw_reference = f"{{fileID: 11400000, guid: {missing_guid}, type: 2}}"
            write_file(
                root / "Assets" / "Referrer.prefab",
                "%YAML 1.1\n--- !u!114 &11400000\nMonoBehaviour:\n"
                f"  target: {raw_reference}\n",
            )
            svc = ReferenceResolverService(project_root=root)
            response = svc.where_used(missing_guid, scope="Assets")

        self.assertEqual(
            (True, "REF_WHERE_USED"),
            (response.success, response.code),
            msg=f"scoped missing GUID scan mismatch: {response!r}",
        )
        self.assertEqual(None, response.data["asset_path"])
        self.assertEqual(True, response.data["asset_missing"])
        self.assertEqual(1, response.data["usage_count"])
        self.assertEqual(
            {
                "path": "Assets/Referrer.prefab",
                "line": 4,
                "column": 11,
                "reference": raw_reference,
            },
            response.data["usages"][0],
        )

    def test_scoped_missing_guid_obeys_max_usages_accounting(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            _seed_minimal_project(root)
            missing_guid = "9999999999999999999999999999bcde"
            refs = "\n".join(
                f"  target{i}: {{fileID: {i}, guid: {missing_guid}, type: 2}}"
                for i in range(3)
            )
            write_file(
                root / "Assets" / "Many.prefab",
                f"%YAML 1.1\n--- !u!114 &11400000\nMonoBehaviour:\n{refs}\n",
            )
            svc = ReferenceResolverService(project_root=root)
            response = svc.where_used(missing_guid, scope="Assets", max_usages=2)

        self.assertEqual(
            (True, "REF_WHERE_USED"),
            (response.success, response.code),
            msg=f"truncated missing GUID scan mismatch: {response!r}",
        )
        self.assertEqual(3, response.data["usage_count"])
        self.assertEqual(2, response.data["returned_usages"])
        self.assertEqual(1, response.data["truncated_usages"])
        self.assertEqual(2, response.data["max_usages"])

    def test_scoped_missing_guid_obeys_exclude_patterns(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            _seed_minimal_project(root)
            missing_guid = "9999999999999999999999999999cdef"
            raw_reference = f"{{fileID: 11400000, guid: {missing_guid}, type: 2}}"
            write_file(
                root / "Assets" / "Included.prefab",
                f"%YAML 1.1\n--- !u!114 &1\nMonoBehaviour:\n  target: {raw_reference}\n",
            )
            write_file(
                root / "Assets" / "Ignored.prefab",
                f"%YAML 1.1\n--- !u!114 &2\nMonoBehaviour:\n  target: {raw_reference}\n",
            )
            svc = ReferenceResolverService(project_root=root)
            response = svc.where_used(
                missing_guid,
                scope="Assets",
                exclude_patterns=("Ignored.prefab",),
            )

        self.assertEqual(
            (True, "REF_WHERE_USED"),
            (response.success, response.code),
            msg=f"excluded missing GUID scan mismatch: {response!r}",
        )
        self.assertEqual(["Assets/Included.prefab"], [u["path"] for u in response.data["usages"]])
        self.assertEqual(["Ignored.prefab"], response.data["exclude_patterns"])

    def test_scan_broken_reference_guid_can_be_passed_to_where_used(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            _seed_minimal_project(root)
            missing_guid = "9999999999999999999999999999def0"
            write_file(
                root / "Assets" / "Broken.prefab",
                "%YAML 1.1\n--- !u!114 &11400000\nMonoBehaviour:\n"
                f"  target: {{fileID: 11400000, guid: {missing_guid}, type: 2}}\n",
            )
            svc = ReferenceResolverService(project_root=root)
            scan = svc.scan_broken_references(scope="Assets")
            top_guid = scan.data["top_missing_asset_guids"][0]["guid"]
            response = svc.where_used(top_guid, scope="Assets")

        self.assertEqual(missing_guid, top_guid)
        self.assertEqual(
            (True, "REF_WHERE_USED"),
            (response.success, response.code),
            msg=f"scan-to-where_used mismatch: {response!r}",
        )
        self.assertEqual(["Assets/Broken.prefab"], [u["path"] for u in response.data["usages"]])

    def test_path_form_unreadable_meta_returns_ref001(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            _seed_minimal_project(root)
            write_file(
                root / "Assets" / "Target.asset",
                "%YAML 1.1\n--- !u!114 &11400000\nMonoBehaviour:\n  m_Name: Target\n",
            )
            (root / "Assets" / "Target.asset.meta").mkdir()
            svc = ReferenceResolverService(project_root=root)
            response = svc.where_used("Assets/Target.asset", scope="Assets")

        self.assertEqual((False, "REF001"), (response.success, response.code))
        self.assertIn("target meta metadata", response.message)

    def test_resolved_guid_marks_asset_present(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            _seed_minimal_project(root)
            write_file(
                root / "Assets" / "Target.asset",
                "%YAML 1.1\n--- !u!114 &11400000\nMonoBehaviour:\n  m_Name: Target\n",
            )
            write_file(
                root / "Assets" / "Target.asset.meta",
                f"fileFormatVersion: 2\nguid: {_TARGET_GUID}\n",
            )
            write_file(
                root / "Assets" / "Referrer.asset",
                "%YAML 1.1\n--- !u!114 &22200000\nMonoBehaviour:\n"
                f"  target: {{fileID: 11400000, guid: {_TARGET_GUID}, type: 2}}\n",
            )
            svc = ReferenceResolverService(project_root=root)
            response = svc.where_used(_TARGET_GUID, scope="Assets")

        self.assertEqual(
            (True, "REF_WHERE_USED"),
            (response.success, response.code),
            msg=f"resolved GUID scan mismatch: {response!r}",
        )
        self.assertEqual("Assets/Target.asset", response.data["asset_path"])
        self.assertEqual(False, response.data["asset_missing"])


class BuildTopMissingEntryTests(unittest.TestCase):
    """Issue #207 — value-pin matrix for
    ``services.reference_resolver._build_top_missing_entry``.

    Targets the named survived mutants enumerated in the issue body:

    * ``mutmut_10`` — ``scan_project_root`` argument replaced with
      ``None`` at the ``resolve_guid_to_asset_name`` call site.
    * ``mutmut_13`` — ``scan_project_root`` argument dropped (positional
      shift) at the same call site. Both mutants collapse onto the same
      observable behavior — ``project_root=None`` produces the absolute
      posix asset path instead of the path relative to the scan project
      root — and are killed by the same relative-asset-name pin.
    * ``mutmut_14`` — ``include_breakdown and per_source is not None``
      replaced with ``or``. Killed by exercising all four corners of the
      breakdown × per_source matrix with value-pinned ``referenced_from``
      presence/absence assertions.
    """

    def test_asset_name_is_relative_to_scan_project_root(self) -> None:
        """``mutmut_10`` / ``mutmut_13`` kill row.

        Given a GUID-to-path map keyed at a known GUID whose mapped
        path is a descendant of a temporary scan-project-root directory,
        the entry's ``asset_name`` must equal the relative posix path
        ``"Assets/Foo.prefab"`` — not the absolute posix path the
        resolver returns when ``scan_project_root`` is ``None`` or is
        omitted from the call.
        """
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw).resolve()
            (root / "Assets").mkdir(parents=True, exist_ok=True)
            asset_path = root / "Assets" / "Foo.prefab"
            asset_path.write_text("body\n", encoding="utf-8")

            guid = "1111111111111111111111111111aaaa"
            guid_map = {guid: asset_path}

            entry = _build_top_missing_entry(
                guid,
                3,
                guid_map=guid_map,
                scan_project_root=root,
                per_source=None,
                include_breakdown=False,
            )

        # Relative posix is the only value distinguishable from the
        # absolute posix path produced by the mutated call sites.
        self.assertEqual("Assets/Foo.prefab", entry["asset_name"])
        # Pin the rest of the entry by value too so a regression that
        # changes the shape of the dictionary fails loudly.
        self.assertEqual(guid, entry["guid"])
        self.assertEqual(3, entry["occurrences"])
        self.assertNotIn("referenced_from", entry)

    def test_breakdown_corners_that_omit_referenced_from(self) -> None:
        """``mutmut_14`` corners 1-3 — every (include_breakdown,
        per_source) combination except (True, Counter) must produce an
        entry without a ``referenced_from`` field.  The matrix is
        parametrised so the (include_breakdown, has_per_source) input
        is visible in the subTest identifier; corner 3 also covers the
        mutated ``or`` branch that would attempt ``None.most_common()``
        and raise ``AttributeError``.
        """
        # Subtest rows: (label, include_breakdown, per_source).
        rows = (
            ("breakdown_off__per_source_absent", False, None),
            (
                "breakdown_off__per_source_present",
                False,
                Counter({"Assets/A.prefab": 1}),
            ),
            ("breakdown_on__per_source_absent", True, None),
        )
        for label, include_breakdown, per_source in rows:
            with self.subTest(label=label):
                with tempfile.TemporaryDirectory() as raw:
                    root = Path(raw).resolve()
                    entry = _build_top_missing_entry(
                        f"guid-{label}",
                        1,
                        guid_map={},
                        scan_project_root=root,
                        per_source=per_source,
                        include_breakdown=include_breakdown,
                    )
                self.assertNotIn("referenced_from", entry)

    def test_breakdown_on_and_per_source_present_emits_ordered_breakdown(
        self,
    ) -> None:
        """``mutmut_14`` corner 4 — both inputs present.

        The ``referenced_from`` field is a list of ``{source, count}``
        rows sorted by descending count, so a counter with two distinct
        sources at counts 2 and 1 produces an ordered two-row sequence
        with the higher-count source first. The exact list is
        value-pinned to forbid a regression that returns the rows in
        insertion order or in the wrong shape."""
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw).resolve()
            counter = Counter()
            counter["Assets/Low.prefab"] = 1
            counter["Assets/High.prefab"] = 2
            entry = _build_top_missing_entry(
                "guid-4",
                3,
                guid_map={},
                scan_project_root=root,
                per_source=counter,
                include_breakdown=True,
            )
        self.assertIn("referenced_from", entry)
        self.assertEqual(
            [
                {"source": "Assets/High.prefab", "count": 2},
                {"source": "Assets/Low.prefab", "count": 1},
            ],
            entry["referenced_from"],
        )


if __name__ == "__main__":
    unittest.main()
