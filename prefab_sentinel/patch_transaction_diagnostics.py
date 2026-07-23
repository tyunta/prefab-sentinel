"""Stable diagnostic identity and introduced-only comparison for transactions."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from prefab_sentinel.contracts import Diagnostic, Severity, ToolResponse
from prefab_sentinel.diagnostics_baseline import (
    DiagnosticKeyRecord,
    DiagnosticsBaseline,
    classify_current_keys,
)


def classify_transaction_diagnostic_keys(
    baseline: Sequence[DiagnosticKeyRecord],
    current: Sequence[DiagnosticKeyRecord],
) -> dict[str, Any]:
    known_keys = tuple(record.key for record in baseline)
    return classify_current_keys(
        current,
        DiagnosticsBaseline(
            known_diagnostics=known_keys,
            path=None,
            status="transaction",
        ),
    ).to_dict()


def validation_records(
    target: str,
    structure: ToolResponse,
    refs: ToolResponse,
) -> tuple[DiagnosticKeyRecord, ...]:
    records = _diagnostic_records("structure", target, structure)
    records.extend(_reference_records(target, refs))
    return tuple(records)


def post_validation_failure(
    structure: ToolResponse,
    refs: ToolResponse,
    classification: dict[str, Any],
) -> ToolResponse | None:
    direct_failure = first_failure(structure, refs)
    if direct_failure is not None:
        return direct_failure
    if int(classification["new_count"]) == 0:
        return None
    return ToolResponse(
        success=False,
        severity=Severity.ERROR,
        code="PATCH_APPLY_RESULT",
        message="patch.apply introduced new validation diagnostics.",
        data={"diagnostics_baseline": classification},
        diagnostics=[
            Diagnostic(
                path="",
                location="post_validation",
                detail="introduced_diagnostic",
                evidence=record["key"],
                severity="error",
            )
            for record in classification["new"]
        ],
    )


def first_failure(*responses: ToolResponse) -> ToolResponse | None:
    for response in responses:
        if response.severity in (Severity.ERROR, Severity.CRITICAL):
            return response
    return None


def _diagnostic_records(
    namespace: str,
    target: str,
    response: ToolResponse,
) -> list[DiagnosticKeyRecord]:
    return [
        DiagnosticKeyRecord(
            key=(
                f"{namespace}:{diagnostic.detail}:"
                f"{diagnostic.path or target}:{diagnostic.location}:"
                f"{diagnostic.evidence}"
            ),
            severity=diagnostic.severity or response.severity.value,
            message=diagnostic.evidence,
            data={"code": diagnostic.detail},
        )
        for diagnostic in response.diagnostics
    ]


def _reference_records(
    target: str,
    response: ToolResponse,
) -> list[DiagnosticKeyRecord]:
    steps = response.data.get("steps", [])
    if isinstance(steps, list) and steps:
        result = steps[0].get("result", {}) if isinstance(steps[0], dict) else {}
        data = result.get("data", {}) if isinstance(result, dict) else {}
        raw_records = data.get("diagnostic_keys", []) if isinstance(data, dict) else []
        records = _records_from_wire(raw_records)
        if records:
            return records
    return _diagnostic_records("reference", target, response)


def _records_from_wire(raw_records: object) -> list[DiagnosticKeyRecord]:
    if not isinstance(raw_records, list):
        return []
    records: list[DiagnosticKeyRecord] = []
    for raw in raw_records:
        if not isinstance(raw, dict) or not isinstance(raw.get("key"), str):
            continue
        raw_data = raw.get("data")
        records.append(
            DiagnosticKeyRecord(
                key=raw["key"],
                severity=str(raw.get("severity", "warning")),
                message=str(raw.get("message", "")),
                data=raw_data if isinstance(raw_data, dict) else {},
            )
        )
    return records
