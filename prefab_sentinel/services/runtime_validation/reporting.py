"""Runtime-validation report reservation and publication boundary."""

from __future__ import annotations

import os
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from prefab_sentinel.contracts import Diagnostic, Severity, ToolResponse
from prefab_sentinel.patch_transaction_io import (
    discard_transaction_report,
    reserve_transaction_report,
    validate_transaction_report_path,
    write_report_payload,
)


@dataclass(frozen=True, slots=True)
class RuntimeReportReservation:
    """Exclusive runtime report path within one resolved Unity project."""

    path: Path
    project_root: Path


def reserve_runtime_report(
    project_root: Path,
    out_report: str | None,
) -> RuntimeReportReservation | ToolResponse:
    """Validate, probe, and exclusively reserve a runtime audit report."""
    report_path = validate_transaction_report_path(project_root, out_report)
    if isinstance(report_path, ToolResponse):
        return report_path

    resolved_root = project_root.resolve(strict=True)
    relative = report_path.relative_to(resolved_root)
    if relative.parts and relative.parts[0] == "Assets":
        return _runtime_report_error(
            "OUT_REPORT_INVALID",
            "out_report must be outside Assets/.",
        )

    probe_error = _probe_atomic_publication(report_path.parent)
    if probe_error is not None:
        return _runtime_report_error("OUT_REPORT_WRITE_FAILED", probe_error)

    reserved = reserve_transaction_report(project_root, str(report_path))
    if isinstance(reserved, ToolResponse):
        return reserved
    return RuntimeReportReservation(reserved, resolved_root)


def publish_runtime_report(
    reservation: RuntimeReportReservation,
    payload: Mapping[str, object],
) -> None:
    """Atomically publish a runtime audit payload to its reserved report path."""
    try:
        write_report_payload(reservation.path, payload)
    except OSError:
        discard_transaction_report(reservation.path)
        raise


def discard_runtime_report(reservation: RuntimeReportReservation) -> None:
    """Discard an unpublished runtime audit report reservation."""
    discard_transaction_report(reservation.path)


def runtime_report_skeleton(
    profile: str,
    audit: Mapping[str, object],
) -> dict[str, object]:
    """Create the initial auditable runtime-validation report payload."""
    return {
        "schema_version": "runtime_validation_report.v1",
        "profile": profile,
        "audit": audit,
        "preflight": {"completed": False, "diagnostics": []},
        "compile": {
            "executed": False,
            "before": {},
            "after": {},
            "delta": {},
            "generated_assets": {
                "planned_created_paths": [],
                "planned_deleted_paths": [],
                "actual_created_paths": [],
                "actual_deleted_paths": [],
            },
        },
        "clientsim": {
            "executed": False,
            "before": {},
            "runtime": {},
            "after": {},
            "side_effect_report": {},
        },
        "result": {},
    }


def _probe_atomic_publication(parent: Path) -> str | None:
    """Prove this report parent can publish and clean a flushed sibling."""
    try:
        descriptor, probe_name = tempfile.mkstemp(
            dir=parent,
            prefix=".prefab-sentinel-runtime-report-probe-",
        )
    except OSError:
        return "out_report parent does not support atomic publication."

    probe_path = Path(probe_name)
    published_probe_path = probe_path.with_name(f"{probe_path.name}.published")
    publication_failed = False
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(b"probe")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(probe_path, published_probe_path)
    except OSError:
        publication_failed = True

    probe_removed = _discard_probe_path(probe_path)
    published_probe_removed = _discard_probe_path(published_probe_path)
    if publication_failed or not probe_removed or not published_probe_removed:
        return "out_report parent does not support atomic publication."
    return None


def _discard_probe_path(path: Path) -> bool:
    try:
        path.unlink()
    except FileNotFoundError:
        return True
    except OSError:
        return False
    return True


def _runtime_report_error(code: str, message: str) -> ToolResponse:
    return ToolResponse(
        success=False,
        severity=Severity.ERROR,
        code=code,
        message=message,
        data={},
        diagnostics=[
            Diagnostic(
                path="",
                location="out_report",
                detail="invalid_field",
                evidence=message,
                severity="error",
            )
        ],
    )
