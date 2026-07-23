"""Exactly-one-open-Prefab transaction coordination."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

from prefab_sentinel.contracts import ToolResponse
from prefab_sentinel.patch_transaction_diagnostics import (
    classify_transaction_diagnostic_keys as classify_diagnostics,
    first_failure,
    post_validation_failure,
    validation_records,
)
from prefab_sentinel.patch_transaction_io import (
    reserve_transaction_report,
    write_transaction_report,
)
from prefab_sentinel.patch_transaction_results import (
    TransactionAuditContext as AuditContext,
    boundary_failure,
    committed,
    not_started,
    rollback,
)

if TYPE_CHECKING:
    from prefab_sentinel.orchestrator import Phase1Orchestrator


ApplyCallback = Callable[[], ToolResponse]





def execute_single_open_prefab_transaction(
    orch: Phase1Orchestrator,
    *,
    target: str,
    out_report: str | None,
    change_reason: str,
    max_diagnostics: int,
    apply: ApplyCallback,
) -> ToolResponse:
    project_root = orch.prefab_variant.project_root
    report_path = reserve_transaction_report(project_root, out_report)
    if isinstance(report_path, ToolResponse):
        return report_path
    audit = AuditContext(
        project_root=project_root,
        report_path=report_path,
        change_reason=change_reason,
    )

    target_path = _contained_target_path(project_root, target)
    if isinstance(target_path, ToolResponse):
        return not_started(audit, target_path)
    try:
        preimage = target_path.read_bytes()
    except OSError as exc:
        return not_started(audit, boundary_failure("preimage", exc))

    try:
        structure_before = orch.inspect_structure(target)
        refs_before = orch.validate_refs(
            target,
            details=True,
            max_diagnostics=max_diagnostics,
        )
    except Exception as exc:
        return not_started(audit, boundary_failure("baseline", exc))
    preflight_failure = first_failure(structure_before, refs_before)
    if preflight_failure is not None:
        return not_started(audit, preflight_failure)

    baseline = validation_records(target, structure_before, refs_before)
    try:
        apply_result = apply()
    except Exception as exc:
        apply_result = boundary_failure("apply", exc)

    if not apply_result.success:
        return rollback(
            target_path,
            preimage,
            audit,
            apply_result,
            apply_result,
            {},
            sync_restored=orch.maybe_auto_refresh,
        )

    try:
        structure_after = orch.inspect_structure(target)
        refs_after = orch.validate_refs(
            target,
            details=True,
            max_diagnostics=max_diagnostics,
        )
        classification = classify_diagnostics(
            baseline,
            validation_records(target, structure_after, refs_after),
        )
    except Exception as exc:
        return rollback(
            target_path,
            preimage,
            audit,
            boundary_failure("post_validation", exc),
            apply_result,
            {},
            sync_restored=orch.maybe_auto_refresh,
        )

    validation_failure = post_validation_failure(
        structure_after,
        refs_after,
        classification,
    )
    if validation_failure is not None:
        return rollback(
            target_path,
            preimage,
            audit,
            validation_failure,
            apply_result,
            classification,
            sync_restored=orch.maybe_auto_refresh,
        )

    response = committed(audit, apply_result, classification)
    if not response.success:
        return rollback(
            target_path,
            preimage,
            audit,
            response,
            response,
            classification,
            sync_restored=orch.maybe_auto_refresh,
        )
    try:
        write_transaction_report(report_path, response)
    except OSError as exc:
        return rollback(
            target_path,
            preimage,
            audit,
            boundary_failure("report", exc),
            apply_result,
            classification,
            sync_restored=orch.maybe_auto_refresh,
        )
    return response





def _contained_target_path(
    project_root: Path,
    target: str,
) -> Path | ToolResponse:
    try:
        root = project_root.resolve(strict=True)
        candidate = Path(target)
        if not candidate.is_absolute():
            candidate = root / candidate
        resolved = candidate.resolve(strict=True)
    except (OSError, ValueError) as exc:
        return boundary_failure("preimage", exc)
    if not resolved.is_relative_to(root):
        return boundary_failure(
            "preimage",
            ValueError("transaction target is outside the project root"),
        )
    return resolved
