from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from unitytool.patch_plan import PLAN_VERSION as PROTOCOL_VERSION, iter_resource_batches, normalize_patch_plan

_UNITY_EXECUTE_METHOD_PROTOCOL_VERSION = 1
SUPPORTED_SUFFIXES = {
    ".prefab",
    ".unity",
    ".asset",
    ".mat",
    ".anim",
    ".controller",
}
UNITY_COMMAND_ENV = "UNITYTOOL_UNITY_COMMAND"
UNITY_PROJECT_PATH_ENV = "UNITYTOOL_UNITY_PROJECT_PATH"
UNITY_EXECUTE_METHOD_ENV = "UNITYTOOL_UNITY_EXECUTE_METHOD"
UNITY_TIMEOUT_SEC_ENV = "UNITYTOOL_UNITY_TIMEOUT_SEC"
UNITY_LOG_FILE_ENV = "UNITYTOOL_UNITY_LOG_FILE"
DEFAULT_EXECUTE_METHOD = "PrefabSentinel.UnityPatchBridge.ApplyFromJson"
DEFAULT_TIMEOUT_SEC = 120
VALID_SEVERITIES = {"info", "warning", "error", "critical"}
SUPPORTED_OP_NAMES = {"set", "insert_array_element", "remove_array_element"}
_SEVERITY_ORDER = {"info": 0, "warning": 1, "error": 2, "critical": 3}


def _emit(payload: dict[str, Any]) -> int:
    sys.stdout.write(json.dumps(payload, ensure_ascii=False))
    sys.stdout.write("\n")
    return 0


def _error_response(
    *,
    code: str,
    message: str,
    data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "protocol_version": PROTOCOL_VERSION,
        "success": False,
        "severity": "error",
        "code": code,
        "message": message,
        "data": data or {},
        "diagnostics": [],
    }


def _split_command(command_raw: str) -> tuple[list[str], str | None]:
    try:
        parts = shlex.split(command_raw)
    except ValueError as exc:
        return [], str(exc)
    command = [part.strip() for part in parts if part.strip()]
    if not command:
        return [], "command is empty after parsing"
    return command, None


def _build_unity_command(
    *,
    base_command: list[str],
    project_path: str,
    execute_method: str,
    request_path: str,
    response_path: str,
    log_path: str,
) -> list[str]:
    return [
        *base_command,
        "-batchmode",
        "-quit",
        "-projectPath",
        project_path,
        "-executeMethod",
        execute_method,
        "-logFile",
        log_path,
        "-unitytoolPatchRequest",
        request_path,
        "-unitytoolPatchResponse",
        response_path,
    ]


def _decode_process_output(raw: bytes) -> str:
    if not raw:
        return ""
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("cp932", errors="replace")


def _finalize_unity_response(
    *,
    payload: dict[str, Any],
    target: str,
    op_count: int,
) -> dict[str, Any]:
    protocol_raw = payload.get(
        "protocol_version",
        _UNITY_EXECUTE_METHOD_PROTOCOL_VERSION,
    )
    try:
        protocol_version = int(protocol_raw)
    except (TypeError, ValueError):
        protocol_version = -1
    if protocol_version != _UNITY_EXECUTE_METHOD_PROTOCOL_VERSION:
        return _error_response(
            code="BRIDGE_PROTOCOL_VERSION",
            message="Bridge protocol version mismatch.",
            data={
                "expected_protocol_version": _UNITY_EXECUTE_METHOD_PROTOCOL_VERSION,
                "received_protocol_version": protocol_raw,
            },
        )

    schema_error = _validate_unity_response_envelope(payload)
    if schema_error is not None:
        return schema_error

    response = dict(payload)
    response["protocol_version"] = PROTOCOL_VERSION
    data = dict(response.get("data", {}))
    data.setdefault("target", target)
    data.setdefault("op_count", op_count)
    data.setdefault("read_only", False)
    data.setdefault("executed", True)
    data.setdefault("protocol_version", PROTOCOL_VERSION)
    response["data"] = data
    return response


def _validate_unity_response_envelope(payload: dict[str, Any]) -> dict[str, Any] | None:
    required_fields = ("success", "severity", "code", "message", "data", "diagnostics")
    missing_fields = [field for field in required_fields if field not in payload]
    if missing_fields:
        return _error_response(
            code="BRIDGE_UNITY_RESPONSE_SCHEMA",
            message="Unity batchmode response is missing required fields.",
            data={"missing_fields": missing_fields},
        )
    if not isinstance(payload.get("success"), bool):
        return _error_response(
            code="BRIDGE_UNITY_RESPONSE_SCHEMA",
            message="Unity batchmode response field 'success' must be a boolean.",
        )
    severity = payload.get("severity")
    if not isinstance(severity, str) or severity not in VALID_SEVERITIES:
        return _error_response(
            code="BRIDGE_UNITY_RESPONSE_SCHEMA",
            message=(
                "Unity batchmode response field 'severity' must be one of: "
                + ", ".join(sorted(VALID_SEVERITIES))
                + "."
            ),
        )
    code = payload.get("code")
    if not isinstance(code, str) or not code.strip():
        return _error_response(
            code="BRIDGE_UNITY_RESPONSE_SCHEMA",
            message="Unity batchmode response field 'code' must be a non-empty string.",
        )
    if not isinstance(payload.get("message"), str):
        return _error_response(
            code="BRIDGE_UNITY_RESPONSE_SCHEMA",
            message="Unity batchmode response field 'message' must be a string.",
        )
    if not isinstance(payload.get("data"), dict):
        return _error_response(
            code="BRIDGE_UNITY_RESPONSE_SCHEMA",
            message="Unity batchmode response field 'data' must be an object.",
        )
    if not isinstance(payload.get("diagnostics"), list):
        return _error_response(
            code="BRIDGE_UNITY_RESPONSE_SCHEMA",
            message="Unity batchmode response field 'diagnostics' must be an array.",
        )
    return None


def _encode_bridge_value(value: object) -> dict[str, object]:
    if value is None:
        return {"value_kind": "null"}
    if isinstance(value, bool):
        return {"value_kind": "bool", "value_bool": value}
    if isinstance(value, int):
        return {"value_kind": "int", "value_int": value}
    if isinstance(value, float):
        return {"value_kind": "float", "value_float": value}
    if isinstance(value, str):
        return {"value_kind": "string", "value_string": value}
    return {
        "value_kind": "json",
        "value_json": json.dumps(value, ensure_ascii=False),
    }


def _normalize_bridge_op(op: object) -> object:
    if not isinstance(op, dict):
        return op
    normalized: dict[str, object] = {}
    for key in ("op", "component", "path", "index"):
        if key in op:
            normalized[key] = op[key]

    op_name = str(op.get("op", "")).strip()
    if op_name in {"set", "insert_array_element"} and "value" in op:
        normalized.update(_encode_bridge_value(op["value"]))
    return normalized


def _normalize_bridge_ops(ops: list[object]) -> list[object]:
    return [_normalize_bridge_op(op) for op in ops]


def _validate_bridge_ops(
    ops: list[object],
    *,
    require_resource: bool,
) -> dict[str, Any] | None:
    for index, op in enumerate(ops):
        location = f"ops[{index}]"
        if not isinstance(op, dict):
            return {"location": location, "error": "operation must be an object"}

        if require_resource:
            resource = op.get("resource")
            if not isinstance(resource, str) or not resource.strip():
                return {
                    "location": f"{location}.resource",
                    "error": "operation must reference a resource",
                }

        op_name = str(op.get("op", "")).strip()
        if op_name not in SUPPORTED_OP_NAMES:
            return {"location": f"{location}.op", "error": f"unsupported op '{op_name}'"}

        component = op.get("component")
        if not isinstance(component, str) or not component.strip():
            return {"location": f"{location}.component", "error": "component is required"}

        path = op.get("path")
        if not isinstance(path, str) or not path.strip():
            return {"location": f"{location}.path", "error": "path is required"}

        if op_name == "set" and "value" not in op:
            return {
                "location": location,
                "error": "set operation requires 'value'",
            }

        if op_name in {"insert_array_element", "remove_array_element"}:
            op_index = op.get("index")
            if isinstance(op_index, bool) or not isinstance(op_index, int):
                return {
                    "location": f"{location}.index",
                    "error": "array operation requires integer 'index'",
                }
    return None


def _merge_diagnostics(responses: list[dict[str, Any]]) -> list[Any]:
    diagnostics: list[Any] = []
    for response in responses:
        payload = response.get("diagnostics")
        if isinstance(payload, list):
            diagnostics.extend(payload)
    return diagnostics


def _max_response_severity(responses: list[dict[str, Any]]) -> str:
    if not responses:
        return "info"
    return max(
        (
            str(response.get("severity", "error"))
            for response in responses
        ),
        key=lambda value: _SEVERITY_ORDER.get(value, _SEVERITY_ORDER["error"]),
    )


def _resource_summary(
    resource: dict[str, Any],
    ops: list[dict[str, Any]],
    response: dict[str, Any],
) -> dict[str, Any]:
    data = response.get("data", {})
    if not isinstance(data, dict):
        data = {}
    return {
        "id": resource.get("id"),
        "kind": resource.get("kind"),
        "path": resource.get("path"),
        "mode": resource.get("mode"),
        "op_count": len(ops),
        "applied": data.get("applied", 0),
        "success": response.get("success", False),
        "severity": response.get("severity", "error"),
        "code": response.get("code", ""),
    }


def _finalize_bridge_plan_response(
    *,
    plan: dict[str, Any],
    responses: list[dict[str, Any]],
    resource_batches: list[tuple[dict[str, Any], list[dict[str, Any]]]],
) -> dict[str, Any]:
    if len(responses) == 1:
        response = dict(responses[0])
        data = dict(response.get("data", {}))
        data["plan_version"] = plan.get("plan_version", PROTOCOL_VERSION)
        data["resource_count"] = 1
        data["resources"] = [
            _resource_summary(resource_batches[0][0], resource_batches[0][1], responses[0])
        ]
        response["data"] = data
        response["protocol_version"] = PROTOCOL_VERSION
        return response

    success = all(bool(response.get("success", False)) for response in responses)
    resource_summaries = [
        _resource_summary(resource, ops, response)
        for (resource, ops), response in zip(resource_batches, responses, strict=True)
    ]
    applied_total = sum(
        int(summary["applied"]) if isinstance(summary.get("applied"), int) else 0
        for summary in resource_summaries
    )
    severity = _max_response_severity(responses)
    return {
        "protocol_version": PROTOCOL_VERSION,
        "success": success,
        "severity": severity,
        "code": "SER_APPLY_OK" if success else "BRIDGE_RESOURCE_FAILED",
        "message": (
            "Bridge apply completed for all resources."
            if success
            else "Bridge apply failed for one or more resources."
        ),
        "data": {
            "plan_version": plan.get("plan_version", PROTOCOL_VERSION),
            "resource_count": len(resource_batches),
            "op_count": len(plan.get("ops", [])),
            "applied": applied_total,
            "resources": resource_summaries,
            "read_only": False,
            "executed": True,
            "protocol_version": PROTOCOL_VERSION,
        },
        "diagnostics": _merge_diagnostics(responses),
    }


def _run_unity_for_resource(
    *,
    base_command: list[str],
    project_path: Path,
    execute_method: str,
    timeout_sec: int,
    log_path_raw: str,
    resource: dict[str, Any],
    ops: list[dict[str, Any]],
) -> dict[str, Any]:
    target = str(resource.get("path", "")).strip()
    with tempfile.TemporaryDirectory(prefix="unitytool-bridge-") as temp_dir:
        temp_root = Path(temp_dir)
        request_path = temp_root / "request.json"
        response_path = temp_root / "response.json"
        log_path = Path(log_path_raw) if log_path_raw else temp_root / "unity-bridge.log"

        request_payload = {
            "protocol_version": _UNITY_EXECUTE_METHOD_PROTOCOL_VERSION,
            "target": target,
            "ops": _normalize_bridge_ops(ops),
        }
        request_path.write_text(
            json.dumps(request_payload, ensure_ascii=False),
            encoding="utf-8",
        )

        command = _build_unity_command(
            base_command=base_command,
            project_path=str(project_path),
            execute_method=execute_method,
            request_path=str(request_path),
            response_path=str(response_path),
            log_path=str(log_path),
        )

        try:
            completed = subprocess.run(
                command,
                capture_output=True,
                timeout=timeout_sec,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            return _error_response(
                code="BRIDGE_UNITY_TIMEOUT",
                message="Unity batchmode process timed out.",
                data={
                    "resource_id": resource.get("id"),
                    "target": target,
                    "timeout_sec": timeout_sec,
                    "command": command,
                    "error": str(exc),
                },
            )
        except OSError as exc:
            return _error_response(
                code="BRIDGE_UNITY_EXEC",
                message="Failed to start Unity batchmode process.",
                data={
                    "resource_id": resource.get("id"),
                    "target": target,
                    "command": command,
                    "error": str(exc),
                },
            )

        if completed.returncode != 0:
            stdout_text = _decode_process_output(completed.stdout)
            stderr_text = _decode_process_output(completed.stderr)
            return _error_response(
                code="BRIDGE_UNITY_FAILED",
                message="Unity batchmode process returned non-zero exit code.",
                data={
                    "resource_id": resource.get("id"),
                    "target": target,
                    "returncode": completed.returncode,
                    "command": command,
                    "stdout": stdout_text,
                    "stderr": stderr_text,
                    "log_path": str(log_path),
                },
            )

        if not response_path.exists():
            return _error_response(
                code="BRIDGE_UNITY_RESPONSE_MISSING",
                message="Unity batchmode response file is missing.",
                data={
                    "resource_id": resource.get("id"),
                    "target": target,
                    "response_path": str(response_path),
                    "log_path": str(log_path),
                },
            )

        try:
            unity_payload_raw = response_path.read_text(encoding="utf-8")
            unity_payload = json.loads(unity_payload_raw)
        except OSError as exc:
            return _error_response(
                code="BRIDGE_UNITY_RESPONSE_READ",
                message="Unity batchmode response file could not be read.",
                data={
                    "resource_id": resource.get("id"),
                    "target": target,
                    "response_path": str(response_path),
                    "error": str(exc),
                },
            )
        except json.JSONDecodeError as exc:
            return _error_response(
                code="BRIDGE_UNITY_RESPONSE_JSON",
                message="Unity batchmode response file is not valid JSON.",
                data={
                    "resource_id": resource.get("id"),
                    "target": target,
                    "response_path": str(response_path),
                    "error": str(exc),
                },
            )

    if not isinstance(unity_payload, dict):
        return _error_response(
            code="BRIDGE_UNITY_RESPONSE_SCHEMA",
            message="Unity batchmode response root must be an object.",
            data={"resource_id": resource.get("id"), "target": target},
        )

    response = _finalize_unity_response(
        payload=unity_payload,
        target=target,
        op_count=len(ops),
    )
    data = response.get("data", {})
    if isinstance(data, dict):
        data.setdefault("resource_id", resource.get("id"))
        data.setdefault("resource_kind", resource.get("kind"))
        data.setdefault("resource_mode", resource.get("mode"))
    return response


def main() -> int:
    raw = sys.stdin.read()
    if not raw.strip():
        return _emit(
            _error_response(
                code="BRIDGE_REQUEST_EMPTY",
                message="Bridge request body is empty.",
            )
        )

    try:
        request = json.loads(raw)
    except json.JSONDecodeError as exc:
        return _emit(
            _error_response(
                code="BRIDGE_REQUEST_JSON",
                message="Bridge request must be valid JSON.",
                data={"error": str(exc)},
            )
        )

    if not isinstance(request, dict):
        return _emit(
            _error_response(
                code="BRIDGE_REQUEST_SCHEMA",
                message="Bridge request root must be an object.",
            )
        )

    protocol_raw = request.get("protocol_version")
    try:
        protocol_version = int(protocol_raw)
    except (TypeError, ValueError):
        protocol_version = -1
    if protocol_version != PROTOCOL_VERSION:
        return _emit(
            _error_response(
                code="BRIDGE_PROTOCOL_VERSION",
                message="Bridge protocol version mismatch.",
                data={
                    "expected_protocol_version": PROTOCOL_VERSION,
                    "received_protocol_version": protocol_raw,
                },
            )
        )

    request_plan = {key: value for key, value in request.items() if key != "protocol_version"}
    try:
        plan = normalize_patch_plan(request_plan)
    except ValueError as exc:
        return _emit(
            _error_response(
                code="BRIDGE_REQUEST_SCHEMA",
                message=str(exc),
            )
        )

    ops = plan.get("ops", [])
    ops_schema_error = _validate_bridge_ops(ops, require_resource=True)
    if ops_schema_error is not None:
        return _emit(
            _error_response(
                code="BRIDGE_REQUEST_SCHEMA",
                message="ops contain invalid operation data.",
                data=ops_schema_error,
            )
        )

    resource_batches = iter_resource_batches(plan)
    for resource, _ in resource_batches:
        target = str(resource.get("path", "")).strip()
        target_path = Path(target)
        if target_path.suffix.lower() not in SUPPORTED_SUFFIXES:
            return _emit(
                _error_response(
                    code="BRIDGE_UNSUPPORTED_TARGET",
                    message="Bridge target extension is not supported.",
                    data={"resource_id": resource.get("id"), "target": target},
                )
            )

    command_raw = os.environ.get(UNITY_COMMAND_ENV, "").strip()
    if not command_raw:
        return _emit(
            _error_response(
                code="BRIDGE_UNITY_COMMAND_MISSING",
                message=f"{UNITY_COMMAND_ENV} is not configured.",
            )
        )

    base_command, split_error = _split_command(command_raw)
    if split_error:
        return _emit(
            _error_response(
                code="BRIDGE_UNITY_COMMAND_INVALID",
                message="Unity command cannot be parsed.",
                data={"error": split_error},
            )
        )

    timeout_raw = os.environ.get(UNITY_TIMEOUT_SEC_ENV, str(DEFAULT_TIMEOUT_SEC)).strip()
    try:
        timeout_sec = int(timeout_raw)
    except ValueError:
        timeout_sec = -1
    if timeout_sec <= 0:
        return _emit(
            _error_response(
                code="BRIDGE_TIMEOUT_INVALID",
                message=f"{UNITY_TIMEOUT_SEC_ENV} must be a positive integer.",
                data={"received_timeout": timeout_raw},
            )
        )

    execute_method = os.environ.get(UNITY_EXECUTE_METHOD_ENV, DEFAULT_EXECUTE_METHOD).strip()
    if not execute_method:
        execute_method = DEFAULT_EXECUTE_METHOD
    project_path = Path(
        os.environ.get(UNITY_PROJECT_PATH_ENV, str(Path.cwd())).strip() or str(Path.cwd())
    )
    if not project_path.exists():
        return _emit(
            _error_response(
                code="BRIDGE_PROJECT_PATH_MISSING",
                message="Unity project path does not exist.",
                data={"project_path": str(project_path)},
            )
        )

    log_path_raw = os.environ.get(UNITY_LOG_FILE_ENV, "").strip()
    responses = [
        _run_unity_for_resource(
            base_command=base_command,
            project_path=project_path,
            execute_method=execute_method,
            timeout_sec=timeout_sec,
            log_path_raw=log_path_raw,
            resource=resource,
            ops=resource_ops,
        )
        for resource, resource_ops in resource_batches
    ]

    return _emit(
        _finalize_bridge_plan_response(
            plan=plan,
            responses=responses,
            resource_batches=resource_batches,
        )
    )


if __name__ == "__main__":
    raise SystemExit(main())
