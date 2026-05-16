"""Branch-coverage uplift for ``prefab_sentinel.orchestrator_write`` (issue #188).

Pins each write-helper envelope path and the auto-refresh marker behaviour.
The module under test is a thin orchestration layer over three core writer
helpers (``write_material_property``, ``copy_asset``, ``rename_asset``); the
tests stub the core helpers and the orchestrator's auto-refresh dispatch so
the assertions cover the orchestration surface itself.

Branches in ``orchestrator_write`` not exercised by this file: none — every
``if`` and every public helper is reached.  The helper passes ``dry_run``
straight through to its core function, and the diagnostic-projection loop is
covered by the ``test_write_helper_propagates_core_failure_diagnostics`` row.

Issue #220 — the inline ``core_result`` fixtures carry the production-code
data key ``asset_path`` (see ``prefab_sentinel.material_asset_writer`` L184)
verbatim; ``test_write_helper_attaches_auto_refresh_marker_on_confirmed_success``
value-pins that key on the response so the fixture-shape contract is locked
against future drift between production and test stubs.
"""

from __future__ import annotations

import unittest
from typing import Any
from unittest import mock

from prefab_sentinel import orchestrator_write
from prefab_sentinel.contracts import Severity
from tests._assertion_helpers import assert_error_envelope


class _StubOrchestrator:
    """Minimal stand-in for ``Phase1Orchestrator`` carrying just the
    ``maybe_auto_refresh`` hook ``orchestrator_write`` reads.
    """

    def __init__(self, refresh_marker: str = "skipped") -> None:
        self._refresh_marker = refresh_marker
        self.refresh_calls = 0

    def maybe_auto_refresh(self) -> str:
        self.refresh_calls += 1
        return self._refresh_marker


class OrchestratorWriteHelperTests(unittest.TestCase):
    """Pin each documented branch of the three public write helpers."""

    def test_set_material_property_dry_run_accepts_missing_change_reason(
        self,
    ) -> None:
        orch = _StubOrchestrator()
        core_result: dict[str, Any] = {
            "success": True,
            "severity": "info",
            "code": "MAT_PROP_OK",
            "message": "ok",
            # Issue #220 — the production data key is ``asset_path``
            # (``prefab_sentinel.material_asset_writer`` L184).
            "data": {"asset_path": "x.mat"},
            "diagnostics": [],
        }
        with mock.patch.object(
            orchestrator_write, "_write_material_property", return_value=core_result
        ) as mock_core:
            response = orchestrator_write.set_material_property(
                orch,  # type: ignore[arg-type]
                target_path="Assets/Mat.mat",
                property_name="_Color",
                value="0.5,0.5,0.5,1",
                dry_run=True,
                change_reason=None,
            )
        # Dominated-collapse: success + code + auto-refresh-call-count
        # form a single envelope-shape contract on the dry-run path.
        self.assertEqual(
            (True, "MAT_PROP_OK", 0),
            (response.success, response.code, orch.refresh_calls),
        )
        kwargs = mock_core.call_args.kwargs
        self.assertEqual(
            (True, "Assets/Mat.mat"),
            (kwargs["dry_run"], kwargs["target_path"]),
        )

    def test_set_material_property_confirm_without_change_reason_returns_envelope(
        self,
    ) -> None:
        orch = _StubOrchestrator()
        with mock.patch.object(
            orchestrator_write, "_write_material_property"
        ) as mock_core:
            response = orchestrator_write.set_material_property(
                orch,  # type: ignore[arg-type]
                target_path="Assets/Mat.mat",
                property_name="_Color",
                value="0.5,0.5,0.5,1",
                dry_run=False,
                change_reason=None,
            )
        self.assertEqual(
            0,
            mock_core.call_count,
            "core helper must not run before the change_reason guard",
        )
        assert_error_envelope(
            response,
            code="MAT_PROP_REASON_REQUIRED",
            severity="error",
            message_match=r"change_reason is required when confirm=True",
        )

    def test_copy_asset_confirm_without_change_reason_returns_envelope(
        self,
    ) -> None:
        orch = _StubOrchestrator()
        with mock.patch.object(orchestrator_write, "_copy_asset") as mock_core:
            response = orchestrator_write.copy_asset(
                orch,  # type: ignore[arg-type]
                source_path="Assets/A.prefab",
                dest_path="Assets/B.prefab",
                dry_run=False,
                change_reason=None,
            )
        self.assertEqual(
            0,
            mock_core.call_count,
            "core helper must not run before the change_reason guard",
        )
        assert_error_envelope(
            response,
            code="ASSET_OP_REASON_REQUIRED",
            severity="error",
            message_match=r"change_reason is required when confirm=True",
        )

    def test_rename_asset_confirm_without_change_reason_returns_envelope(
        self,
    ) -> None:
        orch = _StubOrchestrator()
        with mock.patch.object(orchestrator_write, "_rename_asset") as mock_core:
            response = orchestrator_write.rename_asset(
                orch,  # type: ignore[arg-type]
                asset_path="Assets/A.prefab",
                new_name="B",
                dry_run=False,
                change_reason=None,
            )
        self.assertEqual(
            0,
            mock_core.call_count,
            "core helper must not run before the change_reason guard",
        )
        assert_error_envelope(
            response,
            code="ASSET_OP_REASON_REQUIRED",
            severity="error",
            message_match=r"change_reason is required when confirm=True",
        )

    def test_write_helper_propagates_core_failure_diagnostics(self) -> None:
        """Core failure: the helper builds a failure envelope, severity is
        ``Severity.ERROR``, and every diagnostic is forwarded verbatim.
        """
        orch = _StubOrchestrator()
        core_result: dict[str, Any] = {
            "success": False,
            "severity": "error",
            "code": "MAT_PROP_NOT_FOUND",
            "message": "missing property",
            # Issue #220 — production data key is ``asset_path``.
            "data": {"asset_path": "Assets/Mat.mat"},
            "diagnostics": [
                {"detail": "missing_property", "evidence": "_Color absent"},
            ],
        }
        with mock.patch.object(
            orchestrator_write, "_write_material_property", return_value=core_result
        ):
            response = orchestrator_write.set_material_property(
                orch,  # type: ignore[arg-type]
                target_path="Assets/Mat.mat",
                property_name="_Color",
                value="0",
                dry_run=True,
                change_reason=None,
            )
        # Dominated-collapse: failure-envelope shape and diagnostic
        # forwarding are pinned as a single tuple value.  Auto-refresh
        # is not called for a failure path (or for any dry-run).
        self.assertEqual(
            (False, Severity.ERROR, "MAT_PROP_NOT_FOUND", 1, 0),
            (
                response.success,
                response.severity,
                response.code,
                len(response.diagnostics),
                orch.refresh_calls,
            ),
        )
        diag = response.diagnostics[0]
        self.assertEqual(
            ("missing_property", "_Color absent"),
            (diag.detail, diag.evidence),
        )

    def test_write_helper_attaches_auto_refresh_marker_on_confirmed_success(
        self,
    ) -> None:
        """Confirmed success path: auto-refresh fires once and its marker
        rides on ``response.data['auto_refresh']``; the production data
        key ``asset_path`` is forwarded verbatim from the core helper.
        """
        orch = _StubOrchestrator(refresh_marker="true")
        core_result: dict[str, Any] = {
            "success": True,
            "severity": "info",
            "code": "MAT_PROP_OK",
            "message": "ok",
            # Issue #220 — production data key is ``asset_path``; the
            # value-pin below locks fixture and production code together.
            "data": {"asset_path": "Assets/Mat.mat"},
            "diagnostics": [],
        }
        with mock.patch.object(
            orchestrator_write, "_write_material_property", return_value=core_result
        ):
            response = orchestrator_write.set_material_property(
                orch,  # type: ignore[arg-type]
                target_path="Assets/Mat.mat",
                property_name="_Color",
                value="0",
                dry_run=False,
                change_reason="material polish pass",
            )
        # Dominated-collapse + Issue #220 value-pin: success flag, auto-
        # refresh dispatch count, auto-refresh marker, and the production
        # ``asset_path`` data key are pinned as one tuple so a regression
        # in any one surface names exactly which slot drifted.
        self.assertEqual(
            (True, 1, "true", "Assets/Mat.mat"),
            (
                response.success,
                orch.refresh_calls,
                response.data["auto_refresh"],
                response.data["asset_path"],
            ),
        )


if __name__ == "__main__":
    unittest.main()
