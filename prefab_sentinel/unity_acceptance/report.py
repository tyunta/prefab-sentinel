"""Acceptance-report reservation and publication boundary."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from prefab_sentinel.contracts import ToolResponse, error_response
from prefab_sentinel.patch_transaction_io import (
    discard_transaction_report,
    reserve_transaction_report,
    validate_transaction_report_path,
    write_report_payload,
)
from prefab_sentinel.unity_acceptance.model import AcceptanceResult


@dataclass(frozen=True, slots=True)
class AcceptanceReportReservation:
    final_path: Path
    reservation: object


def reserve_acceptance_report(
    project_root: Path,
    out_report: str | None,
) -> AcceptanceReportReservation | ToolResponse:
    """Exclusively reserve an acceptance report outside the Unity Assets tree."""
    report_path = validate_transaction_report_path(project_root, out_report)
    if isinstance(report_path, ToolResponse):
        return report_path

    resolved_root = project_root.resolve(strict=True)
    relative = report_path.relative_to(resolved_root)
    if relative.parts and relative.parts[0] == "Assets":
        return error_response(
            "OUT_REPORT_INVALID",
            "out_report must be outside Assets/.",
        )

    reserved = reserve_transaction_report(project_root, str(report_path))
    if isinstance(reserved, ToolResponse):
        return reserved
    return AcceptanceReportReservation(final_path=reserved, reservation=reserved)


def publish_acceptance_report(
    reservation: AcceptanceReportReservation,
    result: AcceptanceResult,
) -> None:
    """Atomically publish the terminal acceptance result."""
    try:
        write_report_payload(reservation.final_path, result.to_dict())
    except (OSError, TypeError, ValueError):
        discard_transaction_report(reservation.final_path)
        raise
