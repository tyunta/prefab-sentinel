"""Terminal response, persistence, and rollback construction for transactions."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from prefab_sentinel.contracts import Diagnostic, Severity, ToolResponse
from prefab_sentinel.patch_transaction_io import (
    discard_transaction_report,
    restore_transaction_preimage,
    write_transaction_report,
)


@dataclass(frozen=True, slots=True)
class TransactionAuditContext:
    project_root: Path
    report_path: Path
    change_reason: str


def _project_relative_path(project_root: Path, path: Path) -> str:
    return path.relative_to(project_root).as_posix()


def boundary_failure(
    boundary: str,
    exc: Exception,
    *,
    state_unknown: bool | None = None,
) -> ToolResponse:
    del exc
    message = f"Patch transaction {boundary} failed."
    data: dict[str, Any] = {"boundary": boundary}
    if state_unknown is not None:
        data["state_unknown"] = state_unknown
    return ToolResponse(
        success=False,
        severity=Severity.ERROR,
        code="PATCH_APPLY_RESULT",
        message=message,
        data=data,
        diagnostics=[
            Diagnostic(
                path="",
                location=boundary,
                detail="transaction_boundary_failure",
                evidence="Transaction boundary failed.",
                severity="error",
            )
        ],
    )


def not_started(
    audit: TransactionAuditContext,
    original: ToolResponse,
) -> ToolResponse:
    response = terminal_response(
        audit=audit,
        status="not_started",
        severity=Severity.ERROR,
        code="PATCH_APPLY_RESULT",
        message=original.message,
        original=original,
        apply_result=None,
        classification={},
        rollback_result=None,
    )
    return persist_terminal(audit.report_path, response)


def rollback(
    target_path: Path,
    preimage: bytes,
    audit: TransactionAuditContext,
    original: ToolResponse,
    apply_result: ToolResponse,
    classification: dict[str, Any],
    sync_restored: Callable[[], str],
) -> ToolResponse:
    try:
        restore_transaction_preimage(target_path, preimage)
        sync_status = sync_restored()
        if sync_status != "true":
            rollback_result = boundary_failure(
                "rollback_sync",
                RuntimeError("restored asset refresh did not succeed"),
                state_unknown=True,
            )
        else:
            rollback_result = ToolResponse(
                success=True,
                severity=Severity.INFO,
                code="PATCH_ROLLBACK_OK",
                message="Transaction preimage restored and synchronized.",
                data={
                    "target": _project_relative_path(audit.project_root, target_path),
                    "auto_refresh": sync_status,
                },
                diagnostics=[],
            )
    except OSError as exc:
        rollback_result = boundary_failure("rollback", exc)

    rollback_failed = not rollback_result.success
    status = "rollback_failed" if rollback_failed else "rolled_back"
    severity = Severity.CRITICAL if rollback_failed else Severity.ERROR
    code = "PATCH_ROLLBACK_FAILED" if rollback_failed else "PATCH_APPLY_RESULT"
    message = (
        "patch.apply validation failed and automatic rollback failed."
        if rollback_failed
        else "patch.apply validation failed; transaction rolled back."
    )
    try:
        response = terminal_response(
            audit=audit,
            status=status,
            severity=severity,
            code=code,
            message=message,
            original=original,
            apply_result=apply_result,
            classification=classification,
            rollback_result=rollback_result,
        )
    except ValueError as exc:
        response = terminal_response(
            audit=audit,
            status=status,
            severity=severity,
            code=code,
            message=message,
            original=boundary_failure("projection", exc),
            apply_result=None,
            classification=classification,
            rollback_result=rollback_result,
        )
    return persist_terminal(audit.report_path, response)


def committed(
    audit: TransactionAuditContext,
    apply_result: ToolResponse,
    classification: dict[str, Any],
) -> ToolResponse:
    try:
        return terminal_response(
            audit=audit,
            status="committed",
            severity=Severity.INFO,
            code="PATCH_APPLY_RESULT",
            message="patch.apply completed; transaction committed.",
            original=apply_result,
            apply_result=apply_result,
            classification=classification,
            rollback_result=None,
        )
    except ValueError as exc:
        return boundary_failure("projection", exc)


def _project_public_path(project_root: Path, value: object) -> object:
    if isinstance(value, str) and Path(value).is_absolute():
        return _project_relative_path(project_root, Path(value))
    return value


def _project_public_paths(
    project_root: Path,
    data: dict[str, Any],
) -> dict[str, Any]:
    projected = dict(data)
    if "target" in data:
        projected["target"] = _project_public_path(project_root, data["target"])

    raw_targets = data.get("targets")
    if isinstance(raw_targets, list):
        projected["targets"] = [
            _project_public_path(project_root, target)
            for target in raw_targets
        ]

    raw_resources = data.get("resources")
    if isinstance(raw_resources, list):
        resources: list[object] = []
        for raw_resource in raw_resources:
            if not isinstance(raw_resource, dict):
                resources.append(raw_resource)
                continue
            resource = dict(raw_resource)
            if "path" in raw_resource:
                resource["path"] = _project_public_path(
                    project_root,
                    raw_resource["path"],
                )
            resources.append(resource)
        projected["resources"] = resources
    return projected


def project_patch_data(
    project_root: Path,
    data: dict[str, Any],
    *,
    recurse_steps: bool = True,
) -> dict[str, Any]:
    projected = _project_public_paths(project_root, data)
    raw_steps = data.get("steps")
    if not recurse_steps or not isinstance(raw_steps, list):
        return projected

    steps: list[object] = []
    for raw_step in raw_steps:
        if not isinstance(raw_step, dict):
            steps.append(raw_step)
            continue
        step = dict(raw_step)
        raw_result = raw_step.get("result")
        if not isinstance(raw_result, dict):
            steps.append(step)
            continue
        result = dict(raw_result)
        raw_data = raw_result.get("data")
        if isinstance(raw_data, dict):
            result["data"] = project_patch_data(project_root, raw_data)
        step["result"] = result
        steps.append(step)

    projected["steps"] = steps
    return projected


def project_patch_response(
    project_root: Path,
    response: ToolResponse,
) -> dict[str, Any]:
    projected: dict[str, Any] = response.to_dict()
    projected["data"] = project_patch_data(project_root, response.data)
    return projected


def terminal_response(
    *,
    audit: TransactionAuditContext,
    status: str,
    severity: Severity,
    code: str,
    message: str,
    original: ToolResponse,
    apply_result: ToolResponse | None,
    classification: dict[str, Any],
    rollback_result: ToolResponse | None,
) -> ToolResponse:
    transaction = {
        "status": status,
        "report_written": True,
        "report_result": _report_result(True, None),
        "original_result": project_patch_response(
            audit.project_root,
            original,
        ),
        "rollback_result": (
            None
            if rollback_result is None
            else project_patch_response(
                audit.project_root,
                rollback_result,
            )
        ),
        "diagnostics_baseline": classification,
        "created_results": _created_results(apply_result),
        "change_reason": audit.change_reason,
        "out_report": _project_relative_path(
            audit.project_root,
            audit.report_path,
        ),
    }
    data = (
        {}
        if apply_result is None
        else project_patch_data(
            audit.project_root,
            apply_result.data,
        )
    )
    data["transaction"] = transaction
    diagnostics = list(original.diagnostics)
    if rollback_result is not None:
        diagnostics.extend(rollback_result.diagnostics)
    return ToolResponse(
        success=status == "committed",
        severity=severity,
        code=code,
        message=message,
        data=data,
        diagnostics=diagnostics,
    )


def persist_terminal(report_path: Path, response: ToolResponse) -> ToolResponse:
    try:
        write_transaction_report(report_path, response)
    except OSError:
        report_error = "Transaction report persistence failed."
        try:
            discard_transaction_report(report_path)
        except OSError:
            report_error = "Transaction report persistence and cleanup failed."
        data = dict(response.data)
        transaction = dict(data["transaction"])
        transaction["report_written"] = False
        transaction["report_result"] = _report_result(False, report_error)
        data["transaction"] = transaction
        return ToolResponse(
            success=response.success,
            severity=response.severity,
            code=response.code,
            message=response.message,
            data=data,
            diagnostics=list(response.diagnostics),
        )
    return response


def _created_results(response: ToolResponse | None) -> list[dict[str, Any]]:
    if response is None:
        return []
    direct = response.data.get("created_results")
    if isinstance(direct, list):
        return [item for item in direct if isinstance(item, dict)]
    steps = response.data.get("steps", [])
    if not isinstance(steps, list):
        return []
    for step in steps:
        if not isinstance(step, dict) or not str(step.get("step", "")).startswith(
            "apply_and_save"
        ):
            continue
        result = step.get("result")
        data = result.get("data", {}) if isinstance(result, dict) else {}
        created = data.get("created_results", []) if isinstance(data, dict) else []
        if isinstance(created, list):
            return [item for item in created if isinstance(item, dict)]
    return []


def _report_result(written: bool, error: str | None) -> dict[str, Any]:
    return {
        "success": written,
        "code": "REPORT_WRITTEN" if written else "OUT_REPORT_WRITE_FAILED",
        "error": error,
    }
