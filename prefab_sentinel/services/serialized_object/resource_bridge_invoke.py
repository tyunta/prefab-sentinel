"""Unity Editor bridge request / response / invocation.

Split out of ``resource_bridge`` so neither file crosses the 300-line
architectural limit.  This module owns:

* the request builder for the bridge protocol,
* the response parser that lifts the bridge payload into a
  ``ToolResponse`` envelope,
* ``apply_with_unity_bridge`` — the subprocess driver that ties the two
  together behind the allow-list check.
"""

from __future__ import annotations

import json
import subprocess
from copy import deepcopy
from pathlib import Path
from typing import Any

from prefab_sentinel.contracts import Diagnostic, Severity, ToolResponse, error_response
from prefab_sentinel.json_io import dump_json, load_json
from prefab_sentinel.patch_plan import PLAN_VERSION, build_bridge_request


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


def parse_bridge_response(
    payload: object,
    target_path: Path,
    ops: list[dict[str, Any]],
) -> ToolResponse:
    if not isinstance(payload, dict):
        return error_response(
            "SER_BRIDGE_PROTOCOL",
            "Unity bridge response must be a JSON object.",
            data={
                "target": str(target_path),
                "op_count": len(ops),
                "applied": 0,
                "read_only": False,
                "executed": False,
            },
        )

    protocol_raw = payload.get("protocol_version", PLAN_VERSION)
    try:
        protocol_version = int(protocol_raw)
    except (TypeError, ValueError):
        protocol_version = -1
    if protocol_version != PLAN_VERSION:
        return error_response(
            "SER_BRIDGE_PROTOCOL_VERSION",
            "Unity bridge protocol version mismatch.",
            data={
                "target": str(target_path),
                "op_count": len(ops),
                "expected_protocol_version": PLAN_VERSION,
                "received_protocol_version": protocol_raw,
                "read_only": False,
                "executed": False,
            },
        )

    severity_raw = str(payload.get("severity", Severity.ERROR.value))
    try:
        severity = Severity(severity_raw)
    except ValueError:
        severity = Severity.ERROR

    diagnostics_payload = payload.get("diagnostics", [])
    diagnostics: list[Diagnostic] = []
    if isinstance(diagnostics_payload, list):
        for item in diagnostics_payload:
            if not isinstance(item, dict):
                continue
            diagnostics.append(
                Diagnostic(
                    path=str(item.get("path", "")),
                    location=str(item.get("location", "")),
                    detail=str(item.get("detail", "")),
                    evidence=str(item.get("evidence", "")),
                )
            )

    data = payload.get("data", {})
    if not isinstance(data, dict):
        data = {}
    data.setdefault("target", str(target_path))
    data.setdefault("op_count", len(ops))
    data.setdefault("read_only", False)
    data.setdefault("executed", True)
    data.setdefault("protocol_version", protocol_version)

    return ToolResponse(
        success=bool(payload.get("success", False)),
        severity=severity,
        code=str(payload.get("code", "SER_BRIDGE_PROTOCOL")),
        message=str(payload.get("message", "Unity bridge response parsed.")),
        data=data,
        diagnostics=diagnostics,
    )


def apply_with_unity_bridge(
    bridge,
    target_path: Path,
    ops: list[dict[str, Any]],
    *,
    resource_kind: str | None = None,
    resource_mode: str = "open",
) -> ToolResponse:
    from prefab_sentinel.services.serialized_object.resource_bridge import (
        UNITY_BRIDGE_ALLOWED_COMMANDS,
        is_bridge_command_allowed,
    )

    if bridge.error:
        return error_response(
            "SER_BRIDGE_CONFIG",
            "Unity bridge command configuration is invalid.",
            data={
                "target": str(target_path),
                "op_count": len(ops),
                "applied": 0,
                "read_only": False,
                "executed": False,
                "error": bridge.error,
            },
        )
    if not bridge.command:
        return error_response(
            "SER_UNSUPPORTED_TARGET",
            "Non-JSON target requires UNITYTOOL_PATCH_BRIDGE for Unity bridge "
            "execution.",
            data={
                "target": str(target_path),
                "op_count": len(ops),
                "applied": 0,
                "read_only": False,
                "executed": False,
            },
        )
    if not is_bridge_command_allowed(bridge.command):
        return error_response(
            "SER_BRIDGE_DENIED",
            "Unity bridge command is not in the allowlist.",
            data={
                "target": str(target_path),
                "op_count": len(ops),
                "command": list(bridge.command),
                "allowed_commands": sorted(UNITY_BRIDGE_ALLOWED_COMMANDS),
                "read_only": False,
                "executed": False,
            },
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
    except subprocess.TimeoutExpired as exc:
        return error_response(
            "SER_BRIDGE_TIMEOUT",
            "Unity bridge process timed out.",
            data={
                "target": str(target_path),
                "op_count": len(ops),
                "command": list(bridge.command),
                "timeout_sec": bridge.timeout_sec,
                "error": str(exc),
                "read_only": False,
                "executed": False,
            },
        )
    except OSError as exc:
        return error_response(
            "SER_BRIDGE_EXEC",
            "Failed to start Unity bridge process.",
            data={
                "target": str(target_path),
                "op_count": len(ops),
                "command": list(bridge.command),
                "error": str(exc),
                "read_only": False,
                "executed": False,
            },
        )

    if completed.returncode != 0:
        return error_response(
            "SER_BRIDGE_FAILED",
            "Unity bridge process returned non-zero exit code.",
            data={
                "target": str(target_path),
                "op_count": len(ops),
                "command": list(bridge.command),
                "returncode": completed.returncode,
                "stdout": completed.stdout,
                "stderr": completed.stderr,
                "read_only": False,
                "executed": False,
            },
        )

    try:
        payload = load_json(completed.stdout)
    except json.JSONDecodeError as exc:
        return error_response(
            "SER_BRIDGE_PROTOCOL",
            "Unity bridge output must be valid JSON.",
            data={
                "target": str(target_path),
                "op_count": len(ops),
                "command": list(bridge.command),
                "stdout": completed.stdout,
                "stderr": completed.stderr,
                "error": str(exc),
                "read_only": False,
                "executed": False,
            },
        )

    return parse_bridge_response(payload, target_path=target_path, ops=ops)


__all__ = [
    "build_unity_bridge_request",
    "parse_bridge_response",
    "apply_with_unity_bridge",
]
