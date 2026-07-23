"""Unity Editor Bridge request, response, and subprocess invocation."""

from __future__ import annotations

import json
import subprocess
from copy import deepcopy
from pathlib import Path
from typing import Any

from prefab_sentinel.contracts import Severity, ToolResponse, error_response
from prefab_sentinel.json_io import dump_json, load_json
from prefab_sentinel.patch_plan import (
    PLAN_VERSION,
    bridge_plan_response_data_is_valid,
    bridge_response_state_is_valid,
    build_bridge_request,
)
from prefab_sentinel.services.serialized_object.handles import normalize_handle_name
from prefab_sentinel.services.serialized_object.patch_validator import (
    bridge_diagnostics_are_valid,
    created_results_are_valid,
    parse_bridge_diagnostics,
)


def build_unity_bridge_request(
    target_path: Path,
    ops: list[dict[str, Any]],
    *,
    resource_kind: str | None = None,
    resource_mode: str = "open",
) -> dict[str, Any]:
    # Local import keeps ``resource_bridge`` importable before this module.
    from prefab_sentinel.services.serialized_object.resource_bridge import (
        infer_bridge_resource_kind,
    )

    resource_id = "target"
    bridged_ops = [{**deepcopy(op), "resource": resource_id} for op in ops]
    return build_bridge_request(
        {
            "plan_version": PLAN_VERSION,
            "resources": [
                {
                    "id": resource_id,
                    "kind": resource_kind or infer_bridge_resource_kind(target_path),
                    "path": str(target_path),
                    "mode": resource_mode,
                }
            ],
            "ops": bridged_ops,
        }
    )


def _bridge_protocol_error(
    ops: list[dict[str, Any]],
    message: str = "Unity bridge response schema is invalid.",
) -> ToolResponse:
    return error_response("SER_BRIDGE_PROTOCOL", message, data=_bridge_failure_data(ops))


_BRIDGE_DATA_SCALAR_TYPES: dict[str, type] = {
    "target": str,
    "op_count": int,
    "applied": int,
    "read_only": bool,
    "executed": bool,
    "protocol_version": int,
}
_BRIDGE_RESPONSE_FIELDS = frozenset(
    {"protocol_version", "success", "severity", "code", "message", "data", "diagnostics"}
)
_BRIDGE_DATA_FIELDS = frozenset((*_BRIDGE_DATA_SCALAR_TYPES, "created_results"))


def _bridge_data_is_valid(
    data: object,
    *,
    op_count: int,
    require_complete: bool = False,
    expected_created_handles: set[str] | None = None,
) -> bool:
    if not isinstance(data, dict) or set(data) != _BRIDGE_DATA_FIELDS:
        return False
    if any(
        type(data[field]) is not expected_type
        for field, expected_type in _BRIDGE_DATA_SCALAR_TYPES.items()
    ):
        return False
    applied = data["applied"]
    if not 0 <= applied <= op_count or (require_complete and applied != op_count):
        return False
    return created_results_are_valid(
        data["created_results"],
        expected_handles=expected_created_handles,
    )


def _validate_bridge_response(
    payload: dict[str, Any],
    ops: list[dict[str, Any]],
    *,
    resource_kind: str | None,
    resource_mode: str | None,
) -> ToolResponse | None:
    if "protocol_version" not in payload:
        return _bridge_protocol_error(ops)

    protocol_version = payload["protocol_version"]
    if type(protocol_version) is not int or protocol_version != PLAN_VERSION:
        return error_response(
            "SER_BRIDGE_PROTOCOL_VERSION",
            "Unity bridge protocol version mismatch.",
            data=_bridge_failure_data(ops),
        )
    if set(payload) != _BRIDGE_RESPONSE_FIELDS:
        return _bridge_protocol_error(ops)
    if type(success := payload["success"]) is not bool:
        return _bridge_protocol_error(ops)
    severity = payload["severity"]
    if not isinstance(severity, str) or severity not in {item.value for item in Severity}:
        return _bridge_protocol_error(ops)
    if not isinstance(code := payload["code"], str) or not code.strip():
        return _bridge_protocol_error(ops)
    if not isinstance(payload["message"], str):
        return _bridge_protocol_error(ops)

    data = payload["data"]
    expected_created_handles: set[str] | None = None
    if success and resource_kind == "prefab" and resource_mode == "open":
        expected_created_handles = {
            normalize_handle_name(op.get("result"))
            for op in ops
            if str(op.get("op", "")).strip() == "instantiate_prefab"
        }
    producer_data = _bridge_data_is_valid(
        data,
        op_count=len(ops),
        require_complete=success,
        expected_created_handles=expected_created_handles,
    )
    aggregate_data = bridge_plan_response_data_is_valid(data, op_count=len(ops), success=success)
    if not producer_data and not aggregate_data:
        return _bridge_protocol_error(ops)
    if (
        data["read_only"]
        or (success and not data["executed"])
        or not bridge_response_state_is_valid(success=success, severity=severity, code=code)
    ):
        return _bridge_protocol_error(ops)
    if not bridge_diagnostics_are_valid(payload["diagnostics"]):
        return _bridge_protocol_error(ops)
    return None


def parse_bridge_response(
    payload: object,
    target_path: Path,
    ops: list[dict[str, Any]],
    *,
    resource_kind: str | None = None,
    resource_mode: str | None = None,
) -> ToolResponse:
    if not isinstance(payload, dict):
        return _bridge_protocol_error(
            ops,
            "Unity bridge response must be a JSON object.",
        )

    schema_error = _validate_bridge_response(
        payload,
        ops,
        resource_kind=resource_kind,
        resource_mode=resource_mode,
    )
    if schema_error is not None:
        return schema_error

    data: dict[str, Any] = deepcopy(payload["data"])
    if set(data) == _BRIDGE_DATA_FIELDS:
        data["target"] = str(target_path)
        data["op_count"] = len(ops)
        data["protocol_version"] = payload["protocol_version"]

    return ToolResponse(
        success=payload["success"],
        severity=Severity(payload["severity"]),
        code=payload["code"],
        message=payload["message"],
        data=data,
        diagnostics=parse_bridge_diagnostics(payload["diagnostics"]),
    )

def _bridge_failure_data(ops: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "op_count": len(ops),
        "applied": 0,
        "read_only": False,
        "executed": False,
    }


def apply_with_unity_bridge(
    bridge,
    target_path: Path,
    ops: list[dict[str, Any]],
    *,
    resource_kind: str | None = None,
    resource_mode: str = "open",
) -> ToolResponse:
    from prefab_sentinel.services.serialized_object.resource_bridge import (
        is_bridge_command_allowed,
    )

    if bridge.error:
        return error_response(
            "SER_BRIDGE_CONFIG",
            "Unity bridge command configuration is invalid.",
            data=_bridge_failure_data(ops),
        )
    if not bridge.command:
        return error_response(
            "SER_UNSUPPORTED_TARGET",
            "Non-JSON target requires UNITYTOOL_PATCH_BRIDGE for Unity bridge "
            "execution.",
            data=_bridge_failure_data(ops),
        )
    if not is_bridge_command_allowed(bridge.command):
        return error_response(
            "SER_BRIDGE_DENIED",
            "Unity bridge command is not in the allowlist.",
            data=_bridge_failure_data(ops),
        )

    request_payload = build_unity_bridge_request(
        target_path,
        ops,
        resource_kind=resource_kind,
        resource_mode=resource_mode,
    )
    try:
        completed = subprocess.run(
            list(bridge.command),
            input=dump_json(request_payload, indent=None),
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=bridge.timeout_sec,
            check=False,
        )
    except UnicodeDecodeError:
        return error_response(
            "SER_BRIDGE_PROTOCOL",
            "Unity bridge output must be valid JSON.",
            data=_bridge_failure_data(ops),
        )
    except subprocess.TimeoutExpired:
        return error_response(
            "SER_BRIDGE_TIMEOUT",
            "Unity bridge process timed out.",
            data=_bridge_failure_data(ops),
        )
    except OSError:
        return error_response(
            "SER_BRIDGE_EXEC",
            "Failed to start Unity bridge process.",
            data=_bridge_failure_data(ops),
        )

    if completed.returncode != 0:
        return error_response(
            "SER_BRIDGE_FAILED",
            "Unity bridge process returned non-zero exit code.",
            data=_bridge_failure_data(ops),
        )

    try:
        payload = load_json(completed.stdout)
    except json.JSONDecodeError:
        return error_response(
            "SER_BRIDGE_PROTOCOL",
            "Unity bridge output must be valid JSON.",
            data=_bridge_failure_data(ops),
        )

    return parse_bridge_response(
        payload,
        target_path=target_path,
        ops=ops,
        resource_kind=resource_kind,
        resource_mode=resource_mode,
    )


__all__ = ["build_unity_bridge_request", "parse_bridge_response", "apply_with_unity_bridge"]
