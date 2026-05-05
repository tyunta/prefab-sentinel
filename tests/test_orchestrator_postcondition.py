"""Direct tests for ``prefab_sentinel.orchestrator_postcondition`` (issue #168).

The module exposes two underscored functions consumed cross-module by the
orchestrator and the patch orchestrator:

* ``_validate_postcondition_schema(postcondition, *, resource_ids)`` —
  shape-only validation; never evaluates resources.
* ``_evaluate_postcondition(serialized_object, reference_resolver,
  postcondition, *, resource_map)`` — evaluates an asset-exists or
  broken-refs postcondition against the live services.

The tests pin every documented schema-error and evaluation envelope by
code, severity, and full-payload equality.  Two snapshot anchors guard
the success-shape envelopes against drift.

Mocking model: the asset-exists rows mock ``SerializedObjectService``'s
target-path resolver via ``unittest.mock.patch.object``; the
broken-refs rows mock ``ReferenceResolverService.scan_broken_references``
the same way.  The schema rows call ``_validate_postcondition_schema``
directly with no service objects.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from prefab_sentinel.contracts import (
    Diagnostic,
    Severity,
    ToolResponse,
    error_response,
    success_response,
)
from prefab_sentinel.orchestrator_postcondition import (
    _evaluate_postcondition,
    _validate_postcondition_schema,
)
from prefab_sentinel.services.reference_resolver import ReferenceResolverService
from prefab_sentinel.services.serialized_object import SerializedObjectService
from tests._assertion_helpers import assert_error_envelope

_FIXTURE_ROOT = (
    Path(__file__).parent / "fixtures" / "orchestrator_postcondition" / "expected"
)


def _read_only_executed_false(extra: dict | None = None) -> dict:
    """Common envelope-data shape for schema-error rows."""
    payload = {"read_only": True, "executed": False}
    if extra:
        payload.update(extra)
    return payload


def _make_services(
    root: Path,
) -> tuple[SerializedObjectService, ReferenceResolverService]:
    """Construct the two services rooted at *root* for evaluation rows."""
    return (
        SerializedObjectService(project_root=root),
        ReferenceResolverService(project_root=root),
    )


class PostconditionSchemaErrorRowsTests(unittest.TestCase):
    """Schema-error envelopes pinned by code, severity, and full
    data-payload equality.  Each row corresponds to one documented
    invalid-input condition in the spec's Error Handling §Postcondition.
    """

    def test_postcondition_not_object_returns_post_schema_error(self) -> None:
        response = _validate_postcondition_schema("not-a-dict", resource_ids=set())
        assert_error_envelope(
            response,
            code="POST_SCHEMA_ERROR",
            severity="error",
            data=_read_only_executed_false(),
        )

    def test_missing_type_returns_post_schema_error(self) -> None:
        response = _validate_postcondition_schema({}, resource_ids=set())
        assert_error_envelope(
            response,
            code="POST_SCHEMA_ERROR",
            severity="error",
            data=_read_only_executed_false(),
        )

    def test_asset_exists_with_both_resource_and_path_returns_post_schema_error(
        self,
    ) -> None:
        response = _validate_postcondition_schema(
            {"type": "asset_exists", "resource": "r1", "path": "Assets/Foo"},
            resource_ids={"r1"},
        )
        assert_error_envelope(
            response,
            code="POST_SCHEMA_ERROR",
            severity="error",
            data=_read_only_executed_false({"type": "asset_exists"}),
        )

    def test_asset_exists_with_neither_resource_nor_path_returns_post_schema_error(
        self,
    ) -> None:
        response = _validate_postcondition_schema(
            {"type": "asset_exists"}, resource_ids=set()
        )
        assert_error_envelope(
            response,
            code="POST_SCHEMA_ERROR",
            severity="error",
            data=_read_only_executed_false({"type": "asset_exists"}),
        )

    def test_asset_exists_unknown_resource_returns_post_schema_error(self) -> None:
        response = _validate_postcondition_schema(
            {"type": "asset_exists", "resource": "unknown"},
            resource_ids={"r1"},
        )
        assert_error_envelope(
            response,
            code="POST_SCHEMA_ERROR",
            severity="error",
            data=_read_only_executed_false(
                {"type": "asset_exists", "resource": "unknown"}
            ),
        )

    def test_broken_refs_missing_scope_returns_post_schema_error(self) -> None:
        response = _validate_postcondition_schema(
            {"type": "broken_refs"}, resource_ids=set()
        )
        assert_error_envelope(
            response,
            code="POST_SCHEMA_ERROR",
            severity="error",
            data=_read_only_executed_false({"type": "broken_refs"}),
        )

    def test_broken_refs_negative_expected_count_returns_post_schema_error(
        self,
    ) -> None:
        response = _validate_postcondition_schema(
            {"type": "broken_refs", "scope": "Assets", "expected_count": -1},
            resource_ids=set(),
        )
        assert_error_envelope(
            response,
            code="POST_SCHEMA_ERROR",
            severity="error",
            data=_read_only_executed_false(
                {"type": "broken_refs", "scope": "Assets"}
            ),
        )

    def test_broken_refs_non_integer_expected_count_returns_post_schema_error(
        self,
    ) -> None:
        response = _validate_postcondition_schema(
            {"type": "broken_refs", "scope": "Assets", "expected_count": "many"},
            resource_ids=set(),
        )
        assert_error_envelope(
            response,
            code="POST_SCHEMA_ERROR",
            severity="error",
            data=_read_only_executed_false(
                {"type": "broken_refs", "scope": "Assets"}
            ),
        )

    def test_broken_refs_non_list_exclude_patterns_returns_post_schema_error(
        self,
    ) -> None:
        response = _validate_postcondition_schema(
            {
                "type": "broken_refs",
                "scope": "Assets",
                "exclude_patterns": "not-a-list",
            },
            resource_ids=set(),
        )
        assert_error_envelope(
            response,
            code="POST_SCHEMA_ERROR",
            severity="error",
            data=_read_only_executed_false(
                {"type": "broken_refs", "scope": "Assets"}
            ),
        )

    def test_broken_refs_non_string_exclude_pattern_returns_post_schema_error(
        self,
    ) -> None:
        response = _validate_postcondition_schema(
            {
                "type": "broken_refs",
                "scope": "Assets",
                "exclude_patterns": ["valid", 7],
            },
            resource_ids=set(),
        )
        assert_error_envelope(
            response,
            code="POST_SCHEMA_ERROR",
            severity="error",
            data=_read_only_executed_false(
                {"type": "broken_refs", "scope": "Assets"}
            ),
        )

    def test_broken_refs_non_list_ignore_asset_guids_returns_post_schema_error(
        self,
    ) -> None:
        response = _validate_postcondition_schema(
            {
                "type": "broken_refs",
                "scope": "Assets",
                "ignore_asset_guids": "not-a-list",
            },
            resource_ids=set(),
        )
        assert_error_envelope(
            response,
            code="POST_SCHEMA_ERROR",
            severity="error",
            data=_read_only_executed_false(
                {"type": "broken_refs", "scope": "Assets"}
            ),
        )

    def test_broken_refs_non_string_ignore_asset_guid_returns_post_schema_error(
        self,
    ) -> None:
        response = _validate_postcondition_schema(
            {
                "type": "broken_refs",
                "scope": "Assets",
                "ignore_asset_guids": ["good", 9],
            },
            resource_ids=set(),
        )
        assert_error_envelope(
            response,
            code="POST_SCHEMA_ERROR",
            severity="error",
            data=_read_only_executed_false(
                {"type": "broken_refs", "scope": "Assets"}
            ),
        )

    def test_broken_refs_negative_max_diagnostics_returns_post_schema_error(
        self,
    ) -> None:
        response = _validate_postcondition_schema(
            {
                "type": "broken_refs",
                "scope": "Assets",
                "max_diagnostics": -5,
            },
            resource_ids=set(),
        )
        assert_error_envelope(
            response,
            code="POST_SCHEMA_ERROR",
            severity="error",
            data=_read_only_executed_false(
                {"type": "broken_refs", "scope": "Assets"}
            ),
        )

    def test_unknown_postcondition_type_returns_post_schema_error(self) -> None:
        response = _validate_postcondition_schema(
            {"type": "WhoKnows"}, resource_ids=set()
        )
        assert_error_envelope(
            response,
            code="POST_SCHEMA_ERROR",
            severity="error",
            data=_read_only_executed_false({"type": "WhoKnows"}),
        )


class PostconditionSchemaSuccessRowsTests(unittest.TestCase):
    """Schema-OK envelopes for the documented success scenarios."""

    def test_asset_exists_by_resource_returns_post_schema_ok(self) -> None:
        response = _validate_postcondition_schema(
            {"type": "asset_exists", "resource": "r1"},
            resource_ids={"r1"},
        )
        self.assertTrue(response.success)
        self.assertEqual("POST_SCHEMA_OK", response.code)
        self.assertEqual(Severity.INFO, response.severity)
        self.assertEqual(
            {"type": "asset_exists", "read_only": True, "executed": False},
            response.data,
        )

    def test_asset_exists_by_path_returns_post_schema_ok(self) -> None:
        response = _validate_postcondition_schema(
            {"type": "asset_exists", "path": "Assets/Foo"},
            resource_ids=set(),
        )
        self.assertTrue(response.success)
        self.assertEqual("POST_SCHEMA_OK", response.code)
        self.assertEqual(Severity.INFO, response.severity)
        self.assertEqual(
            {"type": "asset_exists", "read_only": True, "executed": False},
            response.data,
        )

    def test_broken_refs_returns_post_schema_ok(self) -> None:
        response = _validate_postcondition_schema(
            {
                "type": "broken_refs",
                "scope": "Assets",
                "expected_count": 0,
            },
            resource_ids=set(),
        )
        self.assertTrue(response.success)
        self.assertEqual("POST_SCHEMA_OK", response.code)
        self.assertEqual(Severity.INFO, response.severity)
        self.assertEqual(
            {
                "type": "broken_refs",
                "scope": "Assets",
                "read_only": True,
                "executed": False,
            },
            response.data,
        )


class PostconditionEvaluateAssetExistsTests(unittest.TestCase):
    """``_evaluate_postcondition`` ``asset_exists`` evaluation rows.
    The serialized-object service's path resolver is mocked at the
    module's import site so the test does not require a live project
    tree.
    """

    def test_asset_exists_pass_returns_post_asset_exists_ok(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            (root / "Assets").mkdir()
            present = root / "Assets" / "Created.asset"
            present.write_text("body\n", encoding="utf-8")
            so_svc, ref_svc = _make_services(root)
            with mock.patch.object(
                SerializedObjectService,
                "_resolve_target_path",
                return_value=present,
            ):
                response = _evaluate_postcondition(
                    so_svc,
                    ref_svc,
                    {"type": "asset_exists", "path": "Assets/Created.asset"},
                    resource_map={},
                )
        self.assertTrue(response.success)
        self.assertEqual("POST_ASSET_EXISTS_OK", response.code)
        self.assertEqual(Severity.INFO, response.severity)
        self.assertEqual(
            {
                "type": "asset_exists",
                "resource": None,
                "path": str(present),
                "exists": True,
                "read_only": True,
                "executed": True,
            },
            response.data,
        )

    def test_asset_exists_fail_returns_post_asset_exists_failed(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            (root / "Assets").mkdir()
            absent = root / "Assets" / "NeverCreated.asset"
            so_svc, ref_svc = _make_services(root)
            with mock.patch.object(
                SerializedObjectService,
                "_resolve_target_path",
                return_value=absent,
            ):
                response = _evaluate_postcondition(
                    so_svc,
                    ref_svc,
                    {"type": "asset_exists", "path": "Assets/NeverCreated.asset"},
                    resource_map={},
                )
        assert_error_envelope(
            response,
            code="POST_ASSET_EXISTS_FAILED",
            severity="error",
            data={
                "type": "asset_exists",
                "resource": None,
                "path": str(absent),
                "exists": False,
                "read_only": True,
                "executed": True,
            },
        )


class PostconditionEvaluateBrokenRefsTests(unittest.TestCase):
    """``_evaluate_postcondition`` ``broken_refs`` evaluation rows.
    The reference resolver's scan is mocked at the module's import site.
    """

    def test_broken_refs_upstream_error_returns_post_broken_refs_error(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            (root / "Assets").mkdir()
            so_svc, ref_svc = _make_services(root)
            upstream = error_response(
                "REF404",
                "Scope path does not exist.",
                data={"scope": "Assets/Missing", "read_only": True},
                diagnostics=[
                    Diagnostic(
                        path="Assets/Missing",
                        location="",
                        detail="missing_scope",
                        evidence="scope path does not exist",
                    )
                ],
            )
            with mock.patch.object(
                ReferenceResolverService,
                "scan_broken_references",
                return_value=upstream,
            ):
                response = _evaluate_postcondition(
                    so_svc,
                    ref_svc,
                    {
                        "type": "broken_refs",
                        "scope": "Assets/Missing",
                        "expected_count": 0,
                    },
                    resource_map={},
                )
        assert_error_envelope(
            response,
            code="POST_BROKEN_REFS_ERROR",
            severity="error",
            data={
                "type": "broken_refs",
                "scope": "Assets/Missing",
                "expected_count": 0,
                "read_only": True,
                "executed": True,
                "scan_code": "REF404",
            },
        )
        self.assertEqual(1, len(response.diagnostics))

    def test_broken_refs_count_mismatch_returns_post_broken_refs_failed(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            (root / "Assets").mkdir()
            so_svc, ref_svc = _make_services(root)
            upstream = error_response(
                "REF_SCAN_BROKEN",
                "Broken references were detected in scope.",
                data={"broken_count": 3, "read_only": True},
            )
            with mock.patch.object(
                ReferenceResolverService,
                "scan_broken_references",
                return_value=upstream,
            ):
                response = _evaluate_postcondition(
                    so_svc,
                    ref_svc,
                    {
                        "type": "broken_refs",
                        "scope": "Assets",
                        "expected_count": 0,
                    },
                    resource_map={},
                )
        assert_error_envelope(
            response,
            code="POST_BROKEN_REFS_FAILED",
            severity="error",
            data={
                "type": "broken_refs",
                "scope": "Assets",
                "expected_count": 0,
                "actual_count": 3,
                "scan_code": "REF_SCAN_BROKEN",
                "read_only": True,
                "executed": True,
            },
        )

    def test_broken_refs_count_match_returns_post_broken_refs_ok(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            (root / "Assets").mkdir()
            so_svc, ref_svc = _make_services(root)
            upstream = success_response(
                "REF_SCAN_OK",
                "No broken references were detected in scope.",
                data={"broken_count": 0, "read_only": True},
            )
            with mock.patch.object(
                ReferenceResolverService,
                "scan_broken_references",
                return_value=upstream,
            ):
                response = _evaluate_postcondition(
                    so_svc,
                    ref_svc,
                    {
                        "type": "broken_refs",
                        "scope": "Assets",
                        "expected_count": 0,
                    },
                    resource_map={},
                )
        self.assertTrue(response.success)
        self.assertEqual("POST_BROKEN_REFS_OK", response.code)
        self.assertEqual(Severity.INFO, response.severity)
        self.assertEqual(
            {
                "type": "broken_refs",
                "scope": "Assets",
                "expected_count": 0,
                "actual_count": 0,
                "scan_code": "REF_SCAN_OK",
                "read_only": True,
                "executed": True,
            },
            response.data,
        )


def _to_snapshot(response: ToolResponse) -> dict:
    """Project a ToolResponse onto a stable, JSON-serializable shape."""
    return {
        "success": response.success,
        "severity": response.severity.value,
        "code": response.code,
        "message": response.message,
        "data": response.data,
    }


class PostconditionSnapshotAnchorTests(unittest.TestCase):
    """Snapshot anchors for the two documented success-shape envelopes
    pin the message text and full data payload against drift.  The
    fixtures live under ``tests/fixtures/orchestrator_postcondition/``.
    """

    def test_asset_exists_ok_snapshot_matches_fixture(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            (root / "Assets").mkdir()
            present = root / "Assets" / "Created.asset"
            present.write_text("body\n", encoding="utf-8")
            so_svc = SerializedObjectService(project_root=root)
            ref_svc = ReferenceResolverService(project_root=root)
            with mock.patch.object(
                SerializedObjectService,
                "_resolve_target_path",
                return_value=present,
            ):
                response = _evaluate_postcondition(
                    so_svc,
                    ref_svc,
                    {"type": "asset_exists", "path": "Assets/Created.asset"},
                    resource_map={},
                )
        # Replace the absolute path with a stable token so the fixture
        # is project-tree independent.
        snapshot = _to_snapshot(response)
        snapshot["data"]["path"] = "<TMP>/Assets/Created.asset"
        fixture_path = _FIXTURE_ROOT / "asset_exists_ok.json"
        expected = json.loads(fixture_path.read_text(encoding="utf-8"))
        self.assertEqual(expected, snapshot)

    def test_broken_refs_ok_snapshot_matches_fixture(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            (root / "Assets").mkdir()
            so_svc = SerializedObjectService(project_root=root)
            ref_svc = ReferenceResolverService(project_root=root)
            upstream = success_response(
                "REF_SCAN_OK",
                "No broken references were detected in scope.",
                data={"broken_count": 0, "read_only": True},
            )
            with mock.patch.object(
                ReferenceResolverService,
                "scan_broken_references",
                return_value=upstream,
            ):
                response = _evaluate_postcondition(
                    so_svc,
                    ref_svc,
                    {
                        "type": "broken_refs",
                        "scope": "Assets",
                        "expected_count": 0,
                    },
                    resource_map={},
                )
        snapshot = _to_snapshot(response)
        fixture_path = _FIXTURE_ROOT / "broken_refs_ok.json"
        expected = json.loads(fixture_path.read_text(encoding="utf-8"))
        self.assertEqual(expected, snapshot)


if __name__ == "__main__":
    unittest.main()
