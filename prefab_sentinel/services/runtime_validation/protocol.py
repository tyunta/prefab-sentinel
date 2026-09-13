"""Runtime response envelope parsing for the editor-bridge dispatch path.

The Editor Bridge returns a JSON object from the Unity-side runtime
validation bridge; this module turns that payload into a ``ToolResponse``
or returns the canonical ``RUN_PROTOCOL_ERROR`` envelope when the payload
violates the protocol contract.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from prefab_sentinel.bridge_constants import PROTOCOL_VERSION
from prefab_sentinel.bridge_response import is_bridge_response_envelope
from prefab_sentinel.contracts import (
    Diagnostic,
    Severity,
    ToolResponse,
    error_response,
)

from . import (
    _protocol_clientsim_schema as clientsim_schema,
    _protocol_compile_schema as compile_schema,
    _protocol_schema as schema,
)


def protocol_error(
    message: str,
    base_data: dict[str, Any],
    *,
    preserve_execution_evidence: bool = False,
) -> ToolResponse:
    """Wrap a protocol failure without fabricating unvalidated execution state."""
    data = dict(base_data)
    if not preserve_execution_evidence:
        data.update({"read_only": True, "executed": False})
    return error_response(
        "RUN_PROTOCOL_ERROR",
        message,
        data=data,
    )


def _runtime_data(
    value: object,
    *,
    profile: str | None,
) -> dict[str, Any]:
    data = schema.exact_object(
        value,
        path="data",
        fields=(
            "project_root",
            "scene_path",
            "profile",
            "timeout_sec",
            "udon_program_count",
            "clientsim_ready",
            "read_only",
            "executed",
            "side_effect_report",
            "compile",
            "clientsim",
        ),
    )
    for field in ("project_root", "scene_path", "profile"):
        schema.string(data[field], f"data.{field}")
    schema.integer(data["timeout_sec"], "data.timeout_sec")
    schema.integer(data["udon_program_count"], "data.udon_program_count")
    schema.boolean(data["clientsim_ready"], "data.clientsim_ready")
    executed = schema.boolean(data["executed"], "data.executed")
    schema.boolean(data["read_only"], "data.read_only")
    compile_report = compile_schema.compile_report(data["compile"], "data.compile")
    clientsim_report = clientsim_schema.clientsim_report(
        data["clientsim"],
        "data.clientsim",
    )
    clientsim_executed = clientsim_report["executed"]

    side_effect_report = data["side_effect_report"]
    if side_effect_report is not None:
        side_effect_report = clientsim_schema.side_effect_report(
            side_effect_report,
            "data.side_effect_report",
        )
        if (
            not clientsim_executed
            and clientsim_schema.is_unity_default_side_effect_report(
                side_effect_report
            )
        ):
            side_effect_report = None
            data["side_effect_report"] = None
    if side_effect_report != clientsim_report["side_effect_report"]:
        raise schema.schema_error(
            "data.side_effect_report",
            "the ClientSim report value",
        )
    if data["udon_program_count"] != compile_report["program_count"]:
        raise schema.schema_error(
            "data.udon_program_count",
            "the compile program count",
        )
    if profile is not None and data["profile"] != profile:
        raise schema.schema_error("data.profile", "the requested profile")

    compile_executed = compile_report["executed"]
    compile_success = compile_report["success"]
    if not compile_executed and compile_success:
        raise schema.schema_error(
            "data.compile.success",
            "false when compile was not executed",
        )
    if profile == "compile_only" and clientsim_executed:
        raise schema.schema_error(
            "data.clientsim.executed",
            "false for the compile-only profile",
        )
    if clientsim_executed and not (compile_executed and compile_success):
        raise schema.schema_error(
            "data.clientsim.executed",
            "preceded by a successful executed compile",
        )
    if not clientsim_executed and any(
        clientsim_report[field] is not None
        for field in ("before", "runtime", "after", "side_effect_report")
    ):
        raise schema.schema_error(
            "data.clientsim",
            "free of runtime evidence when ClientSim was not executed",
        )

    expected_executed = (
        clientsim_executed
        if profile == "clientsim"
        else compile_executed
    )
    if executed != expected_executed:
        raise schema.schema_error(
            "data.executed",
            "the profile execution result",
        )
    return data


def _validate_runtime_payload(
    payload: object,
    *,
    profile: str | None,
) -> tuple[dict[str, Any], list[dict[str, Any]], str | None]:
    envelope = schema.exact_object(
        payload,
        path="response",
        fields=(
            "protocol_version",
            "success",
            "severity",
            "code",
            "message",
            "data",
            "diagnostics",
        ),
    )
    schema.integer(envelope["protocol_version"], "protocol_version")
    success = schema.boolean(envelope["success"], "success")
    severity = schema.string(envelope["severity"], "severity")
    severity_order = {
        Severity.INFO.value: 0,
        Severity.WARNING.value: 1,
        Severity.ERROR.value: 2,
        Severity.CRITICAL.value: 3,
    }
    if severity not in severity_order:
        raise schema.schema_error("severity", "a known severity")
    schema.string(envelope["code"], "code")
    schema.string(envelope["message"], "message")
    data = _runtime_data(envelope["data"], profile=profile)
    compile_report = data["compile"]
    if success and not compile_report["success"]:
        raise schema.schema_error(
            "success",
            "consistent with the compile result",
        )
    if severity_order[severity] < severity_order[compile_report["severity"]]:
        raise schema.schema_error(
            "severity",
            "at least the compile result severity",
        )
    diagnostics = schema.runtime_diagnostics(envelope["diagnostics"], "diagnostics")

    compile_delta = compile_report["delta"]
    generated_asset_report = compile_report["generated_assets"]
    for generated_field in (
        "planned_created_paths",
        "planned_deleted_paths",
        "actual_created_paths",
        "actual_deleted_paths",
    ):
        if generated_asset_report[generated_field] != compile_delta[generated_field]:
            raise schema.schema_error(
                f"data.compile.generated_assets.{generated_field}",
                f"equal to data.compile.delta.{generated_field}",
            )

    mismatch_error = None
    if success and (
        compile_delta["planned_created_paths"]
        != compile_delta["actual_created_paths"]
        or compile_delta["planned_deleted_paths"]
        != compile_delta["actual_deleted_paths"]
    ):
        mismatch_error = str(
            schema.schema_error(
                "data.compile.delta",
                "actual generated asset changes matching the plan",
            )
        )
    return data, diagnostics, mismatch_error


def parse_runtime_response(
    payload: object,
    *,
    action: str,
    project_root: Path,
    scene_path: str | None,
    profile: str | None,
    log_path: Path,
    relative_fn: Callable[[Path], str],
) -> ToolResponse:
    """Validate the complete Unity runtime payload before exposing evidence."""
    base_data = {
        "action": action,
        "project_root": relative_fn(project_root),
        "scene_path": scene_path,
        "profile": profile,
        "log_path": relative_fn(log_path),
    }
    if not is_bridge_response_envelope(payload):
        return protocol_error("Unity runtime response envelope is invalid.", base_data)

    try:
        data, diagnostics_payload, mismatch_error = _validate_runtime_payload(
            payload,
            profile=profile,
        )
    except schema.ProtocolSchemaError as exc:
        return protocol_error(str(exc), base_data)

    if payload["protocol_version"] != PROTOCOL_VERSION:
        return protocol_error(
            "Unity runtime response protocol version is invalid.",
            base_data,
        )
    if mismatch_error is not None:
        return protocol_error(
            mismatch_error,
            {**base_data, **data},
            preserve_execution_evidence=True,
        )

    diagnostics = [
        Diagnostic(
            path=entry["path"],
            location=entry["location"],
            detail=entry["detail"],
            evidence=entry["evidence"],
        )
        for entry in diagnostics_payload
    ]
    return ToolResponse(
        success=payload["success"],
        severity=Severity(payload["severity"]),
        code=payload["code"].strip(),
        message=payload["message"],
        data={**base_data, **data},
        diagnostics=diagnostics,
    )
