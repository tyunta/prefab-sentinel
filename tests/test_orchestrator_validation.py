"""Tests for orchestrator_validation.validate_refs missing-GUID contract (#83).

Issue #146 (Task C2): the validation post-condition responses are also
pinned via fixture files under ``tests/fixtures/orchestrator_validation/``;
those snapshot tests sit at the bottom of this file.  ``--regenerate-snapshots``
overwrites the fixture in place; without the flag the live payload must
match the on-disk fixture exactly.
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

from prefab_sentinel.contracts import Severity
from prefab_sentinel.orchestrator_validation import validate_refs
from prefab_sentinel.services.reference_resolver import ReferenceResolverService
from tests.bridge_test_helpers import write_file

FIXTURES_ROOT = (
    Path(__file__).parent / "fixtures" / "orchestrator_validation" / "expected"
)


def _stable_validate_refs_snapshot(response) -> dict:
    """Project a ``validate_refs`` response onto its pinned snapshot keys.

    The full response carries dynamic per-step data; the snapshot pins the
    quality-gate-relevant keys (success, should_proceed analogue, code,
    severity, per-category counts, per-entry severity) that the
    operating-rules contract treats as the public surface.
    """
    data = response.data
    step_result = data["steps"][0]["result"]
    step_data = step_result["data"]
    step_categories = dict(step_data.get("categories", {}) or {})
    return {
        "success": response.success,
        "severity": response.severity.value,
        "code": response.code,
        # ``should_proceed`` is the per-issue #146 alias for the
        # post-condition's ``success`` flag (the pipeline proceeds only
        # when the validation step succeeds).  Pinning both makes the
        # downgrade-mutation surface explicit.
        "should_proceed": response.success,
        "missing_asset_unique_count": data["missing_asset_unique_count"],
        "step_severity": step_result["severity"],
        "step_code": step_result["code"],
        # Per-category counts: broken_pptr / udon_runtime / variant_override
        # are the published quality-gate keys (AGENTS.md "Quality Gates").
        # The reference scan reports under the "categories" map which uses
        # different names; we project them onto the published keys here.
        "categories": {
            "broken_pptr": int(step_categories.get("missing_asset", 0)),
            "udon_runtime": 0,
            "variant_override": int(
                step_categories.get("variant_override_mismatch", 0)
            ),
        },
        "diagnostic_severities": [d.detail for d in response.diagnostics],
    }


def _assert_snapshot(
    fixture_relpath: str,
    payload: dict,
    *,
    regenerate: bool,
) -> None:
    fixture_path = FIXTURES_ROOT / fixture_relpath
    fixture_path.parent.mkdir(parents=True, exist_ok=True)
    if regenerate or not fixture_path.exists():
        fixture_path.write_text(
            json.dumps(payload, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return
    expected = json.loads(fixture_path.read_text(encoding="utf-8"))
    if expected != payload:
        raise AssertionError(
            f"snapshot mismatch at {fixture_path}:\n"
            f"expected: {json.dumps(expected, indent=2, sort_keys=True)}\n"
            f"observed: {json.dumps(payload, indent=2, sort_keys=True)}"
        )

BASE_GUID = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
VARIANT_GUID = "cccccccccccccccccccccccccccccccc"
MISSING_GUID = "ffffffffffffffffffffffffffffffff"


def _create_project_with_missing_guid(root: Path) -> None:
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
        root / "Assets" / "Variant.prefab",
        f"""%YAML 1.1
--- !u!1001 &100100000
PrefabInstance:
  m_SourcePrefab: {{fileID: 100100000, guid: {BASE_GUID}, type: 3}}
  m_Modification:
    m_Modifications:
    - target: {{fileID: 100100000, guid: {MISSING_GUID}, type: 3}}
      propertyPath: missing.ref
      value: 0
      objectReference: {{fileID: 0}}
""",
    )
    write_file(
        root / "Assets" / "Variant.prefab.meta",
        f"""fileFormatVersion: 2
guid: {VARIANT_GUID}
""",
    )


def _create_project_with_missing_guid_and_local_id(root: Path) -> None:
    _create_project_with_missing_guid(root)
    write_file(
        root / "Assets" / "LocalBroken.prefab",
        f"""%YAML 1.1
--- !u!1 &100
GameObject:
  m_Name: LocalBroken
--- !u!114 &200
MonoBehaviour:
  m_GameObject: {{fileID: 100}}
  m_Script: {{fileID: 11500000, guid: {MISSING_GUID}, type: 3}}
  localRef: {{fileID: 999999}}
""",
    )
    write_file(
        root / "Assets" / "LocalBroken.prefab.meta",
        "fileFormatVersion: 2\nguid: 33333333333333333333333333333333\n",
    )


def _create_clean_project(root: Path) -> None:
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


class MissingGuidContractTests(unittest.TestCase):
    """T24 / Issue #146: ``validate_refs`` top-level outcome and full
    nested step result envelope binds by exact value on both the
    missing-GUID fail-fast path and the clean-scope path."""

    @staticmethod
    def _expected_clean_scan_data(scope: str) -> dict:
        """Full ``scan_broken_references`` payload shape on the clean
        single-Base.prefab fixture (one scanned file, zero references)."""
        return {
            "scope": scope,
            "project_root": ".",
            "scan_project_root": ".",
            "read_only": True,
            "details_included": False,
            "max_diagnostics": 200,
            "exclude_patterns": [],
            "ignore_asset_guids": [],
            "broken_count": 0,
            "broken_occurrences": 0,
            "categories": {"missing_asset": 0, "missing_local_id": 0},
            "categories_occurrences": {"missing_asset": 0, "missing_local_id": 0},
            "diagnostic_keys": [],
            "ignored_missing_asset_occurrences": 0,
            "ignored_missing_asset_unique_count": 0,
            "returned_diagnostics": 0,
            "scanned_files": 1,
            "scanned_references": 0,
            "skipped_external_prefab_fileid_checks": 0,
            "skipped_external_prefab_fileid_details": [],
            "skipped_unreadable_target_checks": 0,
            "top_ignored_missing_asset_guids": [],
            "top_missing_asset_guids": [],
            "truncated_diagnostics": 0,
            "truncated_hint": None,
            "unique_missing_asset_guids": [],
            "unreadable_files": 0,
        }

    @staticmethod
    def _expected_missing_guid_scan_data(scope: str) -> dict:
        """Full ``scan_broken_references`` payload shape on the
        missing-GUID Base+Variant fixture."""
        return {
            "scope": scope,
            "project_root": ".",
            "scan_project_root": ".",
            "read_only": True,
            "details_included": False,
            "max_diagnostics": 200,
            "exclude_patterns": [],
            "ignore_asset_guids": [],
            "broken_count": 1,
            "broken_occurrences": 1,
            "categories": {"missing_asset": 1, "missing_local_id": 0},
            "categories_occurrences": {"missing_asset": 1, "missing_local_id": 0},
            "diagnostic_keys": [
                {
                    "key": f"missing_asset_guid:{MISSING_GUID}",
                    "severity": "error",
                    "message": "missing_asset",
                    "data": {"category": "missing_asset", "guid": MISSING_GUID},
                }
            ],
            "ignored_missing_asset_occurrences": 0,
            "ignored_missing_asset_unique_count": 0,
            "returned_diagnostics": 0,
            "scanned_files": 2,
            "scanned_references": 3,
            "skipped_external_prefab_fileid_checks": 1,
            "skipped_external_prefab_fileid_details": [
                {
                    "source": "Assets/Variant.prefab",
                    "target_guid": BASE_GUID,
                    "file_id": "100100000",
                }
            ],
            "skipped_unreadable_target_checks": 0,
            "top_ignored_missing_asset_guids": [],
            "top_missing_asset_guids": [
                {
                    "guid": MISSING_GUID,
                    "occurrences": 1,
                    "asset_name": "",
                }
            ],
            "truncated_diagnostics": 1,
            "truncated_hint": (
                "1 broken reference(s) found. Use --details to include"
                " individual diagnostics."
            ),
            "unique_missing_asset_guids": [MISSING_GUID],
            "unreadable_files": 0,
        }

    def test_validate_refs_returns_ref001(self) -> None:
        """Missing-GUID fail-fast: top-level REF001, severity error,
        and the full nested scan_broken_references step result envelope
        binds by exact value (per-step success / severity / code /
        message / data)."""
        from tests._assertion_helpers import assert_error_envelope  # noqa: PLC0415

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            _create_project_with_missing_guid(root)

            resolver = ReferenceResolverService(project_root=root)
            response = validate_refs(resolver, scope="Assets")

        assert_error_envelope(
            response,
            code="REF001",
            severity="error",
            message_match=r"missing GUID reference",
            data={
                "scope": "Assets",
                "read_only": True,
                "ignore_asset_guids": [],
                "missing_asset_unique_count": 1,
                "steps": [
                    {
                        "step": "scan_broken_references",
                        "result": {
                            "success": False,
                            "severity": "error",
                            "code": "REF_SCAN_BROKEN",
                            "message": "Broken references were detected in scope.",
                            "data": self._expected_missing_guid_scan_data("Assets"),
                        },
                    }
                ],
            },
        )

    def test_validate_refs_clean_scan_returns_validate_refs_result(self) -> None:
        """Clean scope: top-level ``VALIDATE_REFS_RESULT`` at info, and
        the full nested ``scan_broken_references`` result envelope binds
        by exact value on the clean path too."""
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            _create_clean_project(root)

            resolver = ReferenceResolverService(project_root=root)
            response = validate_refs(resolver, scope="Assets")

        from prefab_sentinel.contracts import Severity  # noqa: PLC0415

        self.assertTrue(response.success)
        self.assertEqual("VALIDATE_REFS_RESULT", response.code)
        self.assertEqual(Severity.INFO, response.severity)
        self.assertEqual(
            {
                "scope": "Assets",
                "read_only": True,
                "ignore_asset_guids": [],
                "missing_asset_unique_count": 0,
                "steps": [
                    {
                        "step": "scan_broken_references",
                        "result": {
                            "success": True,
                            "severity": "info",
                            "code": "REF_SCAN_OK",
                            "message": "No broken references were detected in scope.",
                            "data": self._expected_clean_scan_data("Assets"),
                        },
                    }
                ],
            },
            response.data,
        )


class DiagnosticsKeyContractTests(unittest.TestCase):
    def test_broken_reference_scan_reports_stable_keys_without_details(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            _create_project_with_missing_guid_and_local_id(root)
            resolver = ReferenceResolverService(project_root=root)

            without_details = resolver.scan_broken_references(
                scope="Assets",
                include_diagnostics=False,
            )
            with_details = resolver.scan_broken_references(
                scope="Assets",
                include_diagnostics=True,
            )

        expected_keys = [
            {
                "key": f"missing_asset_guid:{MISSING_GUID}",
                "severity": "error",
                "message": "missing_asset",
                "data": {"category": "missing_asset", "guid": MISSING_GUID},
            },
            {
                "key": "missing_local_id_local:Assets/LocalBroken.prefab:999999",
                "severity": "error",
                "message": "missing_local_id",
                "data": {
                    "category": "missing_local_id",
                    "source_path": "Assets/LocalBroken.prefab",
                    "file_id": "999999",
                },
            },
        ]
        for response in (without_details, with_details):
            self.assertEqual(
                (2, {"missing_asset": 1, "missing_local_id": 1}),
                (response.data["broken_count"], response.data["categories"]),
            )
            self.assertEqual(expected_keys, response.data.get("diagnostic_keys"))


class DiagnosticsBaselineValidateRefsTests(unittest.TestCase):
    SECOND_MISSING_GUID = "eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee"

    def test_validate_refs_classifies_current_keys_without_downgrading_ref001(self) -> None:
        from prefab_sentinel.diagnostics_baseline import DiagnosticsBaseline

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            _create_project_with_missing_guid(root)
            write_file(
                root / "Assets" / "SecondMissing.prefab",
                f"""%YAML 1.1
--- !u!1 &100
GameObject:
  m_Name: SecondMissing
--- !u!114 &200
MonoBehaviour:
  m_GameObject: {{fileID: 100}}
  m_Script: {{fileID: 11500000, guid: {self.SECOND_MISSING_GUID}, type: 3}}
""",
            )
            write_file(
                root / "Assets" / "SecondMissing.prefab.meta",
                "fileFormatVersion: 2\nguid: 44444444444444444444444444444444\n",
            )
            resolver = ReferenceResolverService(project_root=root)
            baseline = DiagnosticsBaseline(
                known_diagnostics=(f"missing_asset_guid:{MISSING_GUID}",),
                path=str(root / "config" / "diagnostics_baseline.json"),
                status="loaded",
            )

            response = validate_refs(
                resolver,
                scope="Assets",
                diagnostics_baseline=baseline,
            )

        scan_data = response.data["steps"][0]["result"]["data"]
        self.assertEqual((False, "REF001", Severity.ERROR), (response.success, response.code, response.severity))
        self.assertEqual((2, {"missing_asset": 2, "missing_local_id": 0}), (scan_data["broken_count"], scan_data["categories"]))
        self.assertEqual(
            {
                "new_count": 1,
                "known_count": 1,
                "resolved_count": 0,
                "new_keys": [f"missing_asset_guid:{self.SECOND_MISSING_GUID}"],
                "known_keys": [f"missing_asset_guid:{MISSING_GUID}"],
                "resolved_keys": [],
            },
            {
                "new_count": response.data.get("diagnostics_baseline", {}).get("new_count"),
                "known_count": response.data.get("diagnostics_baseline", {}).get("known_count"),
                "resolved_count": response.data.get("diagnostics_baseline", {}).get("resolved_count"),
                "new_keys": [
                    item["key"]
                    for item in response.data.get("diagnostics_baseline", {}).get("new", [])
                ],
                "known_keys": [
                    item["key"]
                    for item in response.data.get("diagnostics_baseline", {}).get("known", [])
                ],
                "resolved_keys": [
                    item["key"]
                    for item in response.data.get("diagnostics_baseline", {}).get("resolved", [])
                ],
            },
        )

    def test_validate_refs_with_baseline_preserves_invalid_ignore_guid_error(self) -> None:
        from prefab_sentinel.diagnostics_baseline import DiagnosticsBaseline

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            _create_project_with_missing_guid(root)
            resolver = ReferenceResolverService(project_root=root)
            baseline = DiagnosticsBaseline(
                known_diagnostics=(),
                path=str(root / "config" / "diagnostics_baseline.json"),
                status="loaded",
            )

            try:
                response = validate_refs(
                    resolver,
                    scope="Assets",
                    ignore_asset_guids=("not-a-guid",),
                    diagnostics_baseline=baseline,
                )
            except KeyError as exc:
                self.fail(
                    "validate_refs must preserve invalid-ignore scan errors "
                    f"instead of raising {exc!r}"
                )

        step_result = response.data["steps"][0]["result"]
        self.assertEqual((False, Severity.ERROR), (response.success, response.severity))
        self.assertEqual(
            (
                "REF001",
                {
                    "scope": "Assets",
                    "invalid_ignore_asset_guids": ["not-a-guid"],
                    "read_only": True,
                },
            ),
            (step_result["code"], step_result["data"]),
        )
        self.assertNotIn("diagnostics_baseline", response.data)

    def test_validate_refs_with_baseline_preserves_missing_scope_error(self) -> None:
        from prefab_sentinel.diagnostics_baseline import DiagnosticsBaseline

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            resolver = ReferenceResolverService(project_root=root)
            baseline = DiagnosticsBaseline(
                known_diagnostics=(),
                path=str(root / "config" / "diagnostics_baseline.json"),
                status="loaded",
            )

            try:
                response = validate_refs(
                    resolver,
                    scope="Assets/Does/Not/Exist",
                    diagnostics_baseline=baseline,
                )
            except KeyError as exc:
                self.fail(
                    "validate_refs must preserve missing-scope scan errors "
                    f"instead of raising {exc!r}"
                )

        step_result = response.data["steps"][0]["result"]
        self.assertEqual((False, Severity.ERROR), (response.success, response.severity))
        self.assertEqual(
            (
                "REF404",
                {"scope": "Assets/Does/Not/Exist", "read_only": True},
            ),
            (step_result["code"], step_result["data"]),
        )
        self.assertNotIn("diagnostics_baseline", response.data)


class InspectStructureContractTests(unittest.TestCase):
    """Issue #146 row: pin ``inspect_structure`` outcomes for prefab vs.
    non-prefab fixtures.  ``checks_performed`` / ``checks_skipped`` /
    ``skip_reason`` and per-finding counts are pinned by exact value."""

    def test_prefab_fixture_runs_full_check_set(self) -> None:
        from prefab_sentinel.orchestrator_validation import (  # noqa: PLC0415
            inspect_structure,
        )
        from prefab_sentinel.services.prefab_variant import (  # noqa: PLC0415
            PrefabVariantService,
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            _create_clean_project(root)
            svc = PrefabVariantService(project_root=root)
            response = inspect_structure(svc, "Assets/Base.prefab")

        self.assertTrue(response.success)
        self.assertEqual("VALIDATE_STRUCTURE_RESULT", response.code)
        self.assertEqual("Assets/Base.prefab", response.data["target_path"])
        self.assertEqual(
            [
                "duplicate_file_id",
                "transform_consistency",
                "missing_components",
                "orphaned_transforms",
            ],
            response.data["checks_performed"],
        )
        self.assertEqual([], response.data["checks_skipped"])
        self.assertEqual("", response.data["skip_reason"])
        # All finding counts are integers pinned at zero on the clean
        # base prefab fixture.
        self.assertEqual(0, response.data["duplicate_file_id_count"])
        self.assertEqual(0, response.data["transform_inconsistency_count"])
        self.assertEqual(0, response.data["missing_component_count"])
        self.assertEqual(0, response.data["orphaned_transform_count"])

    def test_rejects_absolute_target_path(self) -> None:
        from prefab_sentinel.orchestrator_validation import (  # noqa: PLC0415
            inspect_structure,
        )
        from prefab_sentinel.services.prefab_variant import (  # noqa: PLC0415
            PrefabVariantService,
        )
        from tests._assertion_helpers import assert_error_envelope  # noqa: PLC0415

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            _create_clean_project(root)
            svc = PrefabVariantService(project_root=root)
            target_path = str(root.parent / "outside.prefab")
            response = inspect_structure(svc, target_path)

        assert_error_envelope(
            response,
            code="VALIDATE_STRUCTURE_INVALID_TARGET_PATH",
            message_match=r"project-root-relative.*project_root",
            data={"target_path": target_path, "read_only": True},
        )

    def test_rejects_traversal_target_path(self) -> None:
        from prefab_sentinel.orchestrator_validation import (  # noqa: PLC0415
            inspect_structure,
        )
        from prefab_sentinel.services.prefab_variant import (  # noqa: PLC0415
            PrefabVariantService,
        )
        from tests._assertion_helpers import assert_error_envelope  # noqa: PLC0415

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            _create_clean_project(root)
            svc = PrefabVariantService(project_root=root)
            target_path = "Assets/../Assets/Base.prefab"
            response = inspect_structure(svc, target_path)

        assert_error_envelope(
            response,
            code="VALIDATE_STRUCTURE_INVALID_TARGET_PATH",
            message_match=r"project-root-relative.*project_root",
            data={"target_path": target_path, "read_only": True},
        )

    def test_non_prefab_fixture_skips_transform_checks(self) -> None:
        """A non-GameObject-bearing text asset (``.mat``) only runs
        the ``duplicate_file_id`` check; the other three checks land
        in ``checks_skipped`` with a non-empty ``skip_reason``.
        ``.prefab`` / ``.unity`` / ``.asset`` are GameObject-bearing
        and run the full check set; the material asset is the
        smallest non-GameObject text asset Unity ships."""
        from prefab_sentinel.orchestrator_validation import (  # noqa: PLC0415
            inspect_structure,
        )
        from prefab_sentinel.services.prefab_variant import (  # noqa: PLC0415
            PrefabVariantService,
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            asset = root / "Assets" / "Material.mat"
            asset.parent.mkdir(parents=True, exist_ok=True)
            asset.write_text(
                "%YAML 1.1\n--- !u!21 &2100000\nMaterial:\n  m_Name: M\n",
                encoding="utf-8",
            )
            (root / "Assets" / "Material.mat.meta").write_text(
                f"fileFormatVersion: 2\nguid: {BASE_GUID}\n",
                encoding="utf-8",
            )
            svc = PrefabVariantService(project_root=root)
            response = inspect_structure(svc, "Assets/Material.mat")

        self.assertEqual("VALIDATE_STRUCTURE_RESULT", response.code)
        self.assertEqual(["duplicate_file_id"], response.data["checks_performed"])
        self.assertEqual(
            [
                "transform_consistency",
                "missing_components",
                "orphaned_transforms",
            ],
            response.data["checks_skipped"],
        )
        self.assertIn(".mat", response.data["skip_reason"])


class InspectStructureDiagnosticsBaselineTests(unittest.TestCase):
    def test_baseline_keys_exclude_human_structure_messages(self) -> None:
        from prefab_sentinel.diagnostics_baseline import DiagnosticsBaseline
        from prefab_sentinel.orchestrator_validation import inspect_structure
        from prefab_sentinel.services.prefab_variant import PrefabVariantService

        duplicate_key = (
            "validate_structure:duplicate_file_id:Assets/Broken.prefab:"
            "fileID:20:{fileID: 20}"
        )
        missing_component_key = (
            "validate_structure:missing_component:Assets/Broken.prefab:"
            "fileID:10:component: {fileID: 999}"
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            write_file(
                root / "Assets" / "Broken.prefab",
                """%YAML 1.1
--- !u!1 &10
GameObject:
  m_Name: Broken
  m_Component:
  - component: {fileID: 20}
  - component: {fileID: 999}
--- !u!4 &20
Transform:
  m_GameObject: {fileID: 10}
  m_Father: {fileID: 0}
  m_Children: []
--- !u!114 &20
MonoBehaviour:
  m_GameObject: {fileID: 10}
""",
            )
            write_file(
                root / "Assets" / "Broken.prefab.meta",
                f"fileFormatVersion: 2\nguid: {BASE_GUID}\n",
            )
            svc = PrefabVariantService(project_root=root)
            baseline = DiagnosticsBaseline(
                known_diagnostics=(duplicate_key,),
                path=str(root / "config" / "diagnostics_baseline.json"),
                status="loaded",
            )

            try:
                response = inspect_structure(
                    svc,
                    "Assets/Broken.prefab",
                    diagnostics_baseline=baseline,
                )
            except TypeError as exc:
                self.fail(
                    "inspect_structure must accept diagnostics_baseline for baseline classification: "
                    f"{exc}"
                )

        classification = response.data.get("diagnostics_baseline")
        if classification is None:
            self.fail("inspect_structure must attach diagnostics_baseline classification")
        self.assertEqual(
            (1, 1, [missing_component_key], [duplicate_key]),
            (
                classification["new_count"],
                classification["known_count"],
                [r["key"] for r in classification["new"]],
                [r["key"] for r in classification["known"]],
            ),
        )
        human_messages = {
            "Duplicate fileID: 20 appears 2 times",
            "GameObject 'Broken' references missing component: fileID:999",
        }
        sampled_text = "\n".join(
            [r["key"] for r in classification["new"] + classification["known"]]
            + [str(r["data"]) for r in classification["new"] + classification["known"]]
        )
        for message in human_messages:
            self.assertNotIn(message, sampled_text)


class OpLabelCodePinTests(unittest.TestCase):
    """Issue #208 — ``inspect_structure`` op-label code prefix pin.

    The shared ``_read_target_file`` helper concatenates the caller-
    supplied operation label with the not-found / read-error suffix to
    form the failure envelope's ``code``.  ``inspect_structure_mutmut_4``
    swaps the op-label argument for ``None`` at the call site
    (``_read_target_file(prefab_variant, target_path, None)``).  The
    surviving suffix is ``_FILE_NOT_FOUND`` / ``_READ_ERROR`` regardless
    of the op-label, so a code-only assertion that pins ``"NoneType"``
    or omits the prefix would let the mutation pass.

    Of the three entry points named in issue #208
    (``validate_refs`` / ``inspect_structure`` / ``validate_runtime``),
    only ``inspect_structure`` invokes the shared file-read helper that
    propagates the operation label into the failure code; the other two
    entry points produce their op-label-prefixed envelope codes through
    unrelated paths and do not invoke the shared helper at all.  This
    test class therefore narrows correctly to the ``inspect_structure``
    path.
    """

    def test_missing_file_failure_pins_validate_structure_prefix(self) -> None:
        """Missing-file row.

        ``inspect_structure`` invoked on a non-existent target produces
        a failure envelope whose ``code`` begins with the
        ``VALIDATE_STRUCTURE`` op-label and ends with ``_FILE_NOT_FOUND``.
        With the mutated op-label argument the prefix would be
        ``None_FILE_NOT_FOUND`` (or similar), so the regex pin
        distinguishes the two shapes.
        """
        from prefab_sentinel.orchestrator_validation import (  # noqa: PLC0415
            inspect_structure,
        )
        from prefab_sentinel.services.prefab_variant import (  # noqa: PLC0415
            PrefabVariantService,
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "Assets").mkdir(parents=True, exist_ok=True)
            svc = PrefabVariantService(project_root=root)
            response = inspect_structure(svc, "Assets/DoesNotExist.prefab")

        self.assertFalse(response.success)
        self.assertEqual(Severity.ERROR, response.severity)
        self.assertEqual("VALIDATE_STRUCTURE_FILE_NOT_FOUND", response.code)
        # Prefix and suffix anchors pinned independently so a mutation
        # that drops only one of them surfaces immediately.
        self.assertRegex(response.code, r"^VALIDATE_STRUCTURE_")
        self.assertRegex(response.code, r"_FILE_NOT_FOUND$")
        self.assertEqual(
            "Assets/DoesNotExist.prefab", response.data["target_path"]
        )
        self.assertEqual(True, response.data["read_only"])

    def test_unreadable_file_failure_pins_validate_structure_prefix(self) -> None:
        """Unreadable-file row.

        ``inspect_structure`` invoked on a target whose
        ``decode_text_file`` raises a ``UnicodeDecodeError`` produces
        a failure envelope whose ``code`` begins with the
        ``VALIDATE_STRUCTURE`` op-label and ends with ``_READ_ERROR``.

        The decoder is patched at its import site inside
        ``prefab_sentinel.orchestrator_variant`` because that is the
        module where the shared ``_read_target_file`` helper lives and
        binds the ``decode_text_file`` symbol.  Patching the source
        module (``prefab_sentinel.unity_assets``) would not intercept
        the helper's already-imported reference.
        """
        from unittest.mock import patch  # noqa: PLC0415

        from prefab_sentinel.orchestrator_validation import (  # noqa: PLC0415
            inspect_structure,
        )
        from prefab_sentinel.services.prefab_variant import (  # noqa: PLC0415
            PrefabVariantService,
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            asset = root / "Assets" / "Existing.prefab"
            asset.parent.mkdir(parents=True, exist_ok=True)
            asset.write_text("body\n", encoding="utf-8")
            svc = PrefabVariantService(project_root=root)

            with patch(
                "prefab_sentinel.orchestrator_variant.decode_text_file",
                side_effect=UnicodeDecodeError(
                    "utf-8", b"\xff", 0, 1, "invalid"
                ),
            ):
                response = inspect_structure(svc, "Assets/Existing.prefab")

        self.assertFalse(response.success)
        self.assertEqual(Severity.ERROR, response.severity)
        self.assertEqual("VALIDATE_STRUCTURE_READ_ERROR", response.code)
        self.assertRegex(response.code, r"^VALIDATE_STRUCTURE_")
        self.assertRegex(response.code, r"_READ_ERROR$")
        self.assertEqual(
            "Assets/Existing.prefab", response.data["target_path"]
        )
        self.assertEqual(True, response.data["read_only"])


class InspectWorldCanvasStepTests(unittest.TestCase):
    """Issue #146 row: ``_inspect_world_canvas_step`` outcomes for an
    unreadable scene (info-level diagnostic, no abort) and a scene
    carrying the local-scale finding (warning-level severity)."""

    def test_unreadable_scene_returns_info_diagnostic(self) -> None:
        from prefab_sentinel.orchestrator_validation import (  # noqa: PLC0415
            _inspect_world_canvas_step,
        )

        response = _inspect_world_canvas_step("/nonexistent/path/to/scene.unity")
        self.assertTrue(response.success)
        self.assertEqual("WORLD_CANVAS_INSPECT_OK", response.code)
        # Severity stays at INFO and a single "scene_unreadable"
        # diagnostic carries the failure cause.
        from prefab_sentinel.contracts import Severity as _Severity  # noqa: PLC0415

        self.assertEqual(_Severity.INFO, response.severity)
        self.assertEqual(
            ["WORLD_CANVAS_SCENE_UNREADABLE"],
            [d.detail for d in response.diagnostics],
        )

    def test_scene_outside_runtime_root_is_not_read_or_inspected(self) -> None:
        from unittest.mock import MagicMock

        from prefab_sentinel.contracts import Severity as _Severity
        from prefab_sentinel.orchestrator_validation import (  # noqa: PLC0415
            _inspect_world_canvas_step,
        )

        with tempfile.TemporaryDirectory() as runtime_dir, tempfile.TemporaryDirectory() as outside_dir:
            outside_scene = Path(outside_dir) / "outside.unity"
            outside_scene.write_text("not a Unity scene fixture", encoding="utf-8")
            inspector = MagicMock()
            with patch_world_canvas_inspector(inspector):
                response = _inspect_world_canvas_step(str(outside_scene), Path(runtime_dir))

        self.assertTrue(response.success)
        self.assertEqual(_Severity.INFO, response.severity)
        self.assertEqual("WORLD_CANVAS_INSPECT_OK", response.code)
        self.assertEqual(
            ["WORLD_CANVAS_SCENE_OUTSIDE_ROOT"],
            [d.detail for d in response.diagnostics],
        )
        inspector.assert_not_called()

    def test_scene_with_local_scale_finding_returns_warning_severity(self) -> None:
        """A scene whose canvas inspector emits a
        ``WORLD_CANVAS_LOCAL_SCALE`` finding is rolled up to
        ``severity=warning`` (capped — the runtime pipeline does not
        abort)."""
        from prefab_sentinel.contracts import Diagnostic, Severity  # noqa: PLC0415
        from prefab_sentinel.orchestrator_validation import (  # noqa: PLC0415
            _inspect_world_canvas_step,
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            scene = Path(temp_dir) / "scene.unity"
            scene.write_text("%YAML 1.1\n--- !u!1 &1\nGameObject:\n", encoding="utf-8")
            with patch_world_canvas_inspector(
                lambda text, path: [
                    Diagnostic(
                        path=path,
                        location="canvas",
                        detail="WORLD_CANVAS_LOCAL_SCALE",
                        evidence="canvas localScale != (1,1,1)",
                    )
                ]
            ):
                response = _inspect_world_canvas_step(str(scene))

        self.assertTrue(response.success)
        self.assertEqual(Severity.WARNING, response.severity)
        self.assertEqual(
            "WORLD_CANVAS_LOCAL_SCALE",
            response.diagnostics[0].detail,
        )


def patch_world_canvas_inspector(stub):
    """Context manager: temporarily replace
    ``orchestrator_validation.inspect_world_canvas_setup`` with
    ``stub`` so tests can inject canvas-step diagnostics without
    constructing a full Unity scene."""
    from unittest.mock import patch  # noqa: PLC0415

    return patch(
        "prefab_sentinel.orchestrator_validation.inspect_world_canvas_setup",
        stub,
    )


class ValidateRuntimeProfileTests(unittest.TestCase):
    @staticmethod
    def _make_response(code: str, severity, success: bool, data: dict | None = None):
        from prefab_sentinel.contracts import ToolResponse

        return ToolResponse(
            success=success,
            severity=severity,
            code=code,
            message="m",
            data=data or {"read_only": True},
        )

    @staticmethod
    def _canvas_step_result(scene_path: str) -> dict:
        return {
            "success": True,
            "severity": "info",
            "code": "WORLD_CANVAS_INSPECT_OK",
            "message": "World canvas inspection completed (read-only).",
            "data": {
                "scene_path": scene_path,
                "read_only": True,
                "diagnostic_count": 0,
            },
            "diagnostics": [],
        }

    @staticmethod
    def _stub_step_result(
        *,
        success: bool,
        severity: str,
        code: str,
        data: dict | None = None,
    ) -> dict:
        return {
            "success": success,
            "severity": severity,
            "code": code,
            "message": "m",
            "data": data or {"read_only": True},
            "diagnostics": [],
        }

    @staticmethod
    def _write_scene(temp_dir: str) -> str:
        scene = Path(temp_dir) / "scene.unity"
        scene.write_text("%YAML 1.1\n--- !u!1 &1\nGameObject:\n", encoding="utf-8")
        return str(scene)

    def _runtime_with_log_steps(self):
        from unittest.mock import MagicMock

        from prefab_sentinel.contracts import Severity

        runtime = MagicMock()
        runtime.project_root = Path("/")
        runtime.assert_no_critical_errors = MagicMock()
        runtime.compile_udonsharp.return_value = self._make_response("RUN_COMPILE_OK", Severity.INFO, True)
        runtime.run_clientsim.return_value = self._make_response(
            "RUN_CLIENTSIM_OK",
            Severity.INFO,
            True,
            data={"read_only": False, "executed": True},
        )
        runtime.collect_unity_console.return_value = self._make_response(
            "RUN_LOG_COLLECTED",
            Severity.INFO,
            True,
            data={"log_lines": [], "read_only": True},
        )
        runtime.collect_editor_console.return_value = self._make_response(
            "RUN_EDITOR_CONSOLE_COLLECTED",
            Severity.INFO,
            True,
            data={"log_lines": [], "read_only": True},
        )
        runtime.classify_errors.return_value = self._make_response("RUN_CLASSIFY_OK", Severity.INFO, True)
        runtime.assert_no_critical_errors.return_value = self._make_response("RUN_ASSERT_OK", Severity.INFO, True)
        return runtime

    def test_validate_runtime_default_profile_never_runs_clientsim(self) -> None:
        from prefab_sentinel.contracts import Severity
        from prefab_sentinel.orchestrator_validation import validate_runtime

        runtime = self._runtime_with_log_steps()

        with tempfile.TemporaryDirectory() as temp_dir:
            scene_path = self._write_scene(temp_dir)
            response = validate_runtime(runtime, scene_path)

        self.assertEqual(
            (True, "VALIDATE_RUNTIME_RESULT", Severity.INFO),
            (response.success, response.code, response.severity),
            msg=f"compile-only validation envelope mismatch: {response.to_dict()!r}",
        )
        self.assertEqual(
            "compile_only",
            response.data["profile"],
            msg=f"default validation profile mismatch: {response.data!r}",
        )
        self.assertEqual(
            [
                "inspect_world_canvas",
                "compile_udonsharp",
                "collect_unity_console",
                "classify_errors",
                "assert_no_critical_errors",
            ],
            [step["step"] for step in response.data["steps"]],
            msg=f"default validation step sequence must exclude ClientSim: {response.data!r}",
        )
        runtime.run_clientsim.assert_not_called()

    def test_validate_runtime_rejects_unknown_profile(self) -> None:
        from prefab_sentinel.contracts import Severity
        from prefab_sentinel.orchestrator_validation import validate_runtime

        runtime = self._runtime_with_log_steps()

        with tempfile.TemporaryDirectory() as temp_dir:
            scene_path = self._write_scene(temp_dir)
            response = validate_runtime(runtime, scene_path, profile="smoke")

        self.assertEqual(
            (False, "VALIDATE_RUNTIME_PROFILE_UNSUPPORTED", Severity.ERROR),
            (response.success, response.code, response.severity),
            msg=f"unsupported runtime profile envelope mismatch: {response.to_dict()!r}",
        )
        self.assertIn("compile_only", response.message)
        self.assertIn("editor_console_only", response.message)
        self.assertIn("clientsim", response.message)
        runtime.compile_udonsharp.assert_not_called()
        runtime.run_clientsim.assert_not_called()

    def test_validate_runtime_editor_console_only_uses_console_without_clientsim(self) -> None:
        from prefab_sentinel.contracts import Severity
        from prefab_sentinel.orchestrator_validation import validate_runtime

        runtime = self._runtime_with_log_steps()

        with tempfile.TemporaryDirectory() as temp_dir:
            scene_path = self._write_scene(temp_dir)
            response = validate_runtime(runtime, scene_path, profile="editor_console_only")

        self.assertEqual(
            (True, "VALIDATE_RUNTIME_RESULT", Severity.INFO),
            (response.success, response.code, response.severity),
            msg=f"console-only validation envelope mismatch: {response.to_dict()!r}",
        )
        self.assertEqual(
            "editor_console_only",
            response.data["profile"],
            msg=f"console-only profile mismatch: {response.data!r}",
        )
        self.assertEqual(
            [
                "inspect_world_canvas",
                "collect_editor_console",
                "classify_errors",
                "assert_no_critical_errors",
            ],
            [step["step"] for step in response.data["steps"]],
            msg=f"console-only step sequence must exclude compile and ClientSim: {response.data!r}",
        )
        runtime.collect_unity_console.assert_not_called()
        runtime.compile_udonsharp.assert_not_called()
        runtime.run_clientsim.assert_not_called()

    def test_clientsim_profile_runs_full_side_effect_sequence(self) -> None:
        from prefab_sentinel.contracts import Severity
        from prefab_sentinel.orchestrator_validation import validate_runtime

        runtime = self._runtime_with_log_steps()
        runtime.run_clientsim.return_value = self._make_response(
            "RUN_CLIENTSIM_OK",
            Severity.INFO,
            True,
            data={"read_only": False, "executed": True},
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            scene_path = self._write_scene(temp_dir)
            response = validate_runtime(
                runtime,
                scene_path,
                profile="clientsim",
                confirm=True,
                change_reason="audit clientsim validation",
            )

        self.assertEqual(
            [
                "inspect_world_canvas",
                "compile_udonsharp",
                "run_clientsim",
                "collect_unity_console",
                "classify_errors",
                "assert_no_critical_errors",
            ],
            [step["step"] for step in response.data["steps"]],
            msg=f"clientsim profile step sequence mismatch: {response.data!r}",
        )
        runtime.run_clientsim.assert_called_once_with(
            scene_path,
            "clientsim",
            confirm=True,
            change_reason="audit clientsim validation",
            allow_dirty_before=False,
        )

    def test_clientsim_step_error_aborts_pipeline(self) -> None:
        from prefab_sentinel.contracts import Severity
        from prefab_sentinel.orchestrator_validation import validate_runtime
        from tests._assertion_helpers import assert_error_envelope

        runtime = self._runtime_with_log_steps()
        runtime.run_clientsim.return_value = self._make_response("RUN_CLIENTSIM_FAILED", Severity.ERROR, False)

        with tempfile.TemporaryDirectory() as temp_dir:
            scene_path = self._write_scene(temp_dir)
            response = validate_runtime(
                runtime,
                scene_path,
                profile="clientsim",
                confirm=True,
                change_reason="audit clientsim validation",
            )

        assert_error_envelope(
            response,
            code="VALIDATE_RUNTIME_RESULT",
            severity="error",
            message_match=r"fail-fast policy",
            data={
                "scene_path": scene_path,
                "profile": "clientsim",
                "read_only": True,
                "fail_fast_triggered": True,
                "steps": [
                    {"step": "inspect_world_canvas", "result": self._canvas_step_result(scene_path)},
                    {
                        "step": "compile_udonsharp",
                        "result": self._stub_step_result(
                            success=True,
                            severity="info",
                            code="RUN_COMPILE_OK",
                        ),
                    },
                    {
                        "step": "run_clientsim",
                        "result": self._stub_step_result(
                            success=False,
                            severity="error",
                            code="RUN_CLIENTSIM_FAILED",
                        ),
                    },
                ],
            },
        )


class TestValidationSnapshotPinning:
    """Issue #146 (C2): pin ``validate_refs`` and ``validate_runtime``
    post-condition responses via fixture files."""

    def test_validate_refs_clean_snapshot(
        self, regenerate_snapshots: bool
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            _create_clean_project(root)
            resolver = ReferenceResolverService(project_root=root)
            response = validate_refs(resolver, scope="Assets")

        snapshot = _stable_validate_refs_snapshot(response)
        _assert_snapshot(
            "validate_refs_clean.json",
            snapshot,
            regenerate=regenerate_snapshots,
        )

    def test_validate_refs_missing_guid_snapshot(
        self, regenerate_snapshots: bool
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            _create_project_with_missing_guid(root)
            resolver = ReferenceResolverService(project_root=root)
            response = validate_refs(resolver, scope="Assets")

        snapshot = _stable_validate_refs_snapshot(response)
        _assert_snapshot(
            "validate_refs_missing_guid.json",
            snapshot,
            regenerate=regenerate_snapshots,
        )

    def test_validate_runtime_skipped_compile_snapshot(
        self, regenerate_snapshots: bool
    ) -> None:
        """A representative runtime-validation snapshot anchored on the
        no-bridge-watch-dir config-error path; pins the success / severity /
        code / canvas-step shape that the orchestrator returns when the
        Editor Bridge watch directory is not configured.

        Post-issue #270 (batchmode dispatch removal): an unset
        ``UNITYTOOL_BRIDGE_WATCH_DIR`` short-circuits both
        ``compile_udonsharp`` and ``run_clientsim`` with
        ``RUN_CONFIG_ERROR``; the orchestrator then trips fail-fast and
        the post-step cascade (collect_unity_console / classify_errors /
        assert_no_critical_errors) does not run. The fixture filename
        retains the legacy ``_skip`` suffix because the test entry-point
        (env-stripped) is still the "skip path" caller; only the
        observed step codes flipped.
        """
        import os  # noqa: PLC0415
        from unittest.mock import patch  # noqa: PLC0415

        from prefab_sentinel.orchestrator_validation import validate_runtime  # noqa: PLC0415
        from prefab_sentinel.services.runtime_validation import (  # noqa: PLC0415
            RuntimeValidationService,
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            scene = root / "Assets" / "Smoke.unity"
            scene.parent.mkdir(parents=True, exist_ok=True)
            scene.write_text(
                "%YAML 1.1\n--- !u!1 &1\nGameObject:\n  m_Name: Smoke\n",
                encoding="utf-8",
            )
            svc = RuntimeValidationService(project_root=root)

            unitytool_keys = [
                key for key in os.environ if key.startswith("UNITYTOOL_")
            ]
            with patch.dict(os.environ, {}, clear=False):
                for key in unitytool_keys:
                    os.environ.pop(key, None)
                response = validate_runtime(svc, str(scene))

        # Project the response onto a stable snapshot for runtime-validation:
        # success / severity / code / per-category quality-gate keys.
        steps_summary = [
            {"step": entry["step"], "code": entry["result"]["code"]}
            for entry in response.data["steps"]
        ]
        snapshot = {
            "success": response.success,
            "severity": response.severity.value,
            "code": response.code,
            "should_proceed": response.success,
            "fail_fast_triggered": response.data["fail_fast_triggered"],
            "categories": {
                "broken_pptr": 0,
                "udon_runtime": 0,
                "variant_override": 0,
            },
            "steps_summary": steps_summary,
        }
        _assert_snapshot(
            "validate_runtime_skip.json",
            snapshot,
            regenerate=regenerate_snapshots,
        )


class TestValidateRefsSnapshot(unittest.TestCase):
    """Issue #199 — snapshot save / snapshot diff modes for validate-refs.

    Tests use ``PREFAB_SENTINEL_SNAPSHOT_DIR`` to direct the helper at
    a fresh tempdir so they do not collide with developer state.
    """

    BASE_GUID = "11111111111111111111111111111111"
    MISSING_A = "aa" * 16
    MISSING_B = "bb" * 16

    def _project_with_missing(self, root: Path, missing: list[str]) -> None:
        assets = root / "Assets"
        assets.mkdir(parents=True, exist_ok=True)
        for index, guid in enumerate(missing):
            write_file(
                assets / f"P{index}.prefab",
                f"""%YAML 1.1
--- !u!1 &100
GameObject:
  m_Name: P{index}
  m_Component:
    - component: {{fileID: 200, guid: {guid}, type: 3}}
""",
            )
            write_file(
                assets / f"P{index}.prefab.meta",
                f"fileFormatVersion: 2\nguid: {self.BASE_GUID[:31]}{index}\n",
            )

    def test_save_then_diff_returns_partition(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            snap_dir = root / "snapshots"
            self._project_with_missing(root, [self.MISSING_A, self.MISSING_B])
            svc = ReferenceResolverService(project_root=root)

            os.environ["PREFAB_SENTINEL_SNAPSHOT_DIR"] = str(snap_dir)
            try:
                # Save current state with two missing GUIDs.
                save_resp = validate_refs(
                    svc,
                    scope=str(root / "Assets"),
                    snapshot_save="baseline",
                )
                self.assertTrue(save_resp.success or save_resp.code == "REF001")

                # Resolve one missing GUID by deleting the prefab that
                # carried it.  P1 references MISSING_B; remove that file.
                (root / "Assets" / "P1.prefab").unlink()
                (root / "Assets" / "P1.prefab.meta").unlink()
                # Drop the cache so the next scan picks up the deletion.
                svc.invalidate_text_cache()
                svc.invalidate_scope_files_cache()

                # Diff against the baseline.
                diff_resp = validate_refs(
                    svc,
                    scope=str(root / "Assets"),
                    snapshot_diff="baseline",
                )
            finally:
                os.environ.pop("PREFAB_SENTINEL_SNAPSHOT_DIR", None)

        step_data = diff_resp.data["steps"][0]["result"]["data"]
        partition = step_data["snapshot_diff"]
        # MISSING_B was resolved; MISSING_A is unchanged.
        resolved_guids = [
            sig[1] for sig in partition["resolved"] if sig[0] == "missing_asset"
        ]
        self.assertIn(self.MISSING_B, resolved_guids)
        self.assertEqual([], partition["new_broken"])

    def test_diff_against_absent_snapshot_emits_not_found(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            snap_dir = root / "snapshots"
            self._project_with_missing(root, [self.MISSING_A])
            svc = ReferenceResolverService(project_root=root)

            os.environ["PREFAB_SENTINEL_SNAPSHOT_DIR"] = str(snap_dir)
            try:
                resp = validate_refs(
                    svc,
                    scope=str(root / "Assets"),
                    snapshot_diff="never-saved",
                )
            finally:
                os.environ.pop("PREFAB_SENTINEL_SNAPSHOT_DIR", None)
        self.assertFalse(resp.success)
        self.assertEqual("VALIDATE_REFS_SNAPSHOT_NOT_FOUND", resp.code)
        self.assertEqual(Severity.ERROR, resp.severity)

    def test_snapshot_not_found_message_omits_project_root(self) -> None:
        # Issue #201: VALIDATE_REFS_SNAPSHOT_NOT_FOUND.message must name only
        # the snapshot identifier and never leak the host filesystem path.
        # The data envelope must also contain no project-root path key.
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            snap_dir = root / "snapshots"
            self._project_with_missing(root, [self.MISSING_A])
            svc = ReferenceResolverService(project_root=root)

            os.environ["PREFAB_SENTINEL_SNAPSHOT_DIR"] = str(snap_dir)
            try:
                resp = validate_refs(
                    svc,
                    scope=str(root / "Assets"),
                    snapshot_diff="never-saved",
                )
            finally:
                os.environ.pop("PREFAB_SENTINEL_SNAPSHOT_DIR", None)
        self.assertEqual("VALIDATE_REFS_SNAPSHOT_NOT_FOUND", resp.code)
        self.assertEqual("no snapshot named 'never-saved'", resp.message)
        self.assertNotIn(str(root), resp.message)
        self.assertNotIn(str(snap_dir), resp.message)
        self.assertNotIn("project_root", resp.data)

    def test_save_and_diff_mutually_exclusive(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            self._project_with_missing(root, [self.MISSING_A])
            svc = ReferenceResolverService(project_root=root)

            resp = validate_refs(
                svc,
                scope=str(root / "Assets"),
                snapshot_save="x",
                snapshot_diff="y",
            )
        self.assertFalse(resp.success)
        self.assertEqual("VALIDATE_REFS_SNAPSHOT_ARG_CONFLICT", resp.code)
        self.assertEqual(Severity.ERROR, resp.severity)

    def test_snapshot_name_path_separator_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            snap_dir = root / "snapshots"
            self._project_with_missing(root, [self.MISSING_A])
            svc = ReferenceResolverService(project_root=root)

            os.environ["PREFAB_SENTINEL_SNAPSHOT_DIR"] = str(snap_dir)
            try:
                resp = validate_refs(
                    svc,
                    scope=str(root / "Assets"),
                    snapshot_save="../escape",
                )
            finally:
                os.environ.pop("PREFAB_SENTINEL_SNAPSHOT_DIR", None)
        self.assertFalse(resp.success)
        self.assertEqual("VALIDATE_REFS_SNAPSHOT_BAD_NAME", resp.code)

    def test_diff_against_malformed_snapshot_emits_bad_name(self) -> None:
        # Cover the SnapshotPayloadError branch in
        # _handle_snapshot_modes: a snapshot file that exists on disk
        # but does not deserialize as a JSON dict must be reported as
        # BAD_NAME with "malformed" in the message (issue #199 / Boy
        # Scout coverage).  The on-disk path is computed via the helper
        # so the test pins the same namespace layout the helper uses.
        from prefab_sentinel.services.reference_resolver_snapshots import (
            snapshot_path,
        )

        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            snap_dir = root / "snapshots"
            self._project_with_missing(root, [self.MISSING_A])
            svc = ReferenceResolverService(project_root=root)

            os.environ["PREFAB_SENTINEL_SNAPSHOT_DIR"] = str(snap_dir)
            try:
                target = snapshot_path("corrupt", root)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text("not-valid-json{", encoding="utf-8")

                resp = validate_refs(
                    svc,
                    scope=str(root / "Assets"),
                    snapshot_diff="corrupt",
                )
            finally:
                os.environ.pop("PREFAB_SENTINEL_SNAPSHOT_DIR", None)

        self.assertFalse(resp.success)
        self.assertEqual("VALIDATE_REFS_SNAPSHOT_BAD_NAME", resp.code)
        self.assertEqual(Severity.ERROR, resp.severity)
        self.assertIn("malformed", resp.message)


    def test_snapshot_save_rejects_symlink_target(self) -> None:
        if not hasattr(os, "symlink"):
            self.skipTest("platform does not support os.symlink")

        from prefab_sentinel.services.reference_resolver_snapshots import (
            snapshot_path,
        )

        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            snap_dir = root / "snapshots"
            _create_clean_project(root)
            svc = ReferenceResolverService(project_root=root)

            os.environ["PREFAB_SENTINEL_SNAPSHOT_DIR"] = str(snap_dir)
            try:
                target = snapshot_path("blocked", root)
                target.parent.mkdir(parents=True, exist_ok=True)
                outside = root / "outside.json"
                outside.write_text("unchanged", encoding="utf-8")
                try:
                    os.symlink(outside, target)
                except OSError as exc:
                    self.skipTest(f"symlink creation not permitted: {exc}")

                resp = validate_refs(
                    svc,
                    scope=str(root / "Assets"),
                    snapshot_save="blocked",
                )
                outside_text = outside.read_text(encoding="utf-8")
            finally:
                os.environ.pop("PREFAB_SENTINEL_SNAPSHOT_DIR", None)

        self.assertFalse(resp.success)
        self.assertEqual("VALIDATE_REFS_SNAPSHOT_BAD_NAME", resp.code)
        self.assertEqual(Severity.ERROR, resp.severity)
        self.assertIn("malformed", resp.message)
        self.assertEqual("unchanged", outside_text)

    def test_snapshot_diff_rejects_symlink_source(self) -> None:
        if not hasattr(os, "symlink"):
            self.skipTest("platform does not support os.symlink")

        from prefab_sentinel.services.reference_resolver_snapshots import (
            snapshot_path,
        )

        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            snap_dir = root / "snapshots"
            _create_clean_project(root)
            svc = ReferenceResolverService(project_root=root)

            os.environ["PREFAB_SENTINEL_SNAPSHOT_DIR"] = str(snap_dir)
            try:
                target = snapshot_path("linked", root)
                target.parent.mkdir(parents=True, exist_ok=True)
                outside = root / "outside.json"
                outside.write_text(
                    json.dumps({"top_missing_asset_guids": [{"guid": self.MISSING_A}]}),
                    encoding="utf-8",
                )
                try:
                    os.symlink(outside, target)
                except OSError as exc:
                    self.skipTest(f"symlink creation not permitted: {exc}")

                resp = validate_refs(
                    svc,
                    scope=str(root / "Assets"),
                    snapshot_diff="linked",
                )
            finally:
                os.environ.pop("PREFAB_SENTINEL_SNAPSHOT_DIR", None)

        self.assertFalse(resp.success)
        self.assertEqual("VALIDATE_REFS_SNAPSHOT_BAD_NAME", resp.code)
        self.assertEqual(Severity.ERROR, resp.severity)
        self.assertIn("malformed", resp.message)


class TestValidateRefsBreakdown(unittest.TestCase):
    """Issue #198 — opt-in per-source-file occurrence breakdown for each
    top missing GUID.  When the breakdown flag is enabled, top-missing
    entries gain a ``referenced_from`` list of ``{source, count}`` rows
    sorted by descending count; without the flag the field is absent.
    """

    MISSING_GUID = "ff" * 16

    def _project_with_two_referrers(self, root: Path) -> None:
        # Two prefabs both reference the same missing GUID; one prefab
        # carries it twice so the breakdown produces a non-trivial sort.
        assets = root / "Assets"
        assets.mkdir(parents=True)
        write_file(
            assets / "First.prefab",
            f"""%YAML 1.1
--- !u!1 &100
GameObject:
  m_Name: First
  m_Component:
    - component: {{fileID: 200, guid: {self.MISSING_GUID}, type: 3}}
    - component: {{fileID: 201, guid: {self.MISSING_GUID}, type: 3}}
""",
        )
        write_file(
            assets / "First.prefab.meta",
            "fileFormatVersion: 2\nguid: 11111111111111111111111111111111\n",
        )
        write_file(
            assets / "Second.prefab",
            f"""%YAML 1.1
--- !u!1 &100
GameObject:
  m_Name: Second
  m_Component:
    - component: {{fileID: 300, guid: {self.MISSING_GUID}, type: 3}}
""",
        )
        write_file(
            assets / "Second.prefab.meta",
            "fileFormatVersion: 2\nguid: 22222222222222222222222222222222\n",
        )

    def test_breakdown_emits_referenced_from_with_counts(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            self._project_with_two_referrers(root)
            svc = ReferenceResolverService(project_root=root)

            response = validate_refs(
                svc, scope=str(root / "Assets"), top_missing_breakdown=True
            )

        step = response.data["steps"][0]["result"]["data"]
        top = step["top_missing_asset_guids"]
        self.assertEqual(1, len(top))
        entry = top[0]
        self.assertEqual(self.MISSING_GUID, entry["guid"])
        # Per-source-file occurrence list, sorted by descending count.
        ref_from = entry["referenced_from"]
        # Two source files; First has 2 occurrences, Second has 1.
        self.assertEqual(2, len(ref_from))
        self.assertEqual(2, ref_from[0]["count"])
        self.assertEqual(1, ref_from[1]["count"])
        self.assertIn("First.prefab", ref_from[0]["source"])
        self.assertIn("Second.prefab", ref_from[1]["source"])

    def test_default_omits_referenced_from(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            self._project_with_two_referrers(root)
            svc = ReferenceResolverService(project_root=root)

            response = validate_refs(svc, scope=str(root / "Assets"))

        step = response.data["steps"][0]["result"]["data"]
        top = step["top_missing_asset_guids"]
        self.assertEqual(1, len(top))
        self.assertNotIn("referenced_from", top[0])


if __name__ == "__main__":
    unittest.main()
