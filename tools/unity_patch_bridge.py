from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

PROTOCOL_VERSION = 1
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
    protocol_raw = payload.get("protocol_version", PROTOCOL_VERSION)
    try:
        protocol_version = int(protocol_raw)
    except (TypeError, ValueError):
        protocol_version = -1
    if protocol_version != PROTOCOL_VERSION:
        return _error_response(
            code="BRIDGE_PROTOCOL_VERSION",
            message="Bridge protocol version mismatch.",
            data={
                "expected_protocol_version": PROTOCOL_VERSION,
                "received_protocol_version": protocol_raw,
            },
        )

    schema_error = _validate_unity_response_envelope(payload)
    if schema_error is not None:
        return schema_error

    response = dict(payload)
    response["protocol_version"] = protocol_version
    data = dict(response.get("data", {}))
    data.setdefault("target", target)
    data.setdefault("op_count", op_count)
    data.setdefault("read_only", False)
    data.setdefault("executed", True)
    data.setdefault("protocol_version", protocol_version)
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

    target = str(request.get("target", "")).strip()
    ops = request.get("ops", [])
    if not target:
        return _emit(
            _error_response(
                code="BRIDGE_REQUEST_SCHEMA",
                message="target is required.",
            )
        )
    if not isinstance(ops, list):
        return _emit(
            _error_response(
                code="BRIDGE_REQUEST_SCHEMA",
                message="ops must be an array.",
            )
        )

    target_path = Path(target)
    if target_path.suffix.lower() not in SUPPORTED_SUFFIXES:
        return _emit(
            _error_response(
                code="BRIDGE_UNSUPPORTED_TARGET",
                message="Bridge target extension is not supported.",
                data={"target": target},
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

    with tempfile.TemporaryDirectory(prefix="unitytool-bridge-") as temp_dir:
        temp_root = Path(temp_dir)
        request_path = temp_root / "request.json"
        response_path = temp_root / "response.json"
        log_path_raw = os.environ.get(UNITY_LOG_FILE_ENV, "").strip()
        log_path = Path(log_path_raw) if log_path_raw else temp_root / "unity-bridge.log"

        request_payload = {
            "protocol_version": PROTOCOL_VERSION,
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
            return _emit(
                _error_response(
                    code="BRIDGE_UNITY_TIMEOUT",
                    message="Unity batchmode process timed out.",
                    data={
                        "timeout_sec": timeout_sec,
                        "command": command,
                        "error": str(exc),
                    },
                )
            )
        except OSError as exc:
            return _emit(
                _error_response(
                    code="BRIDGE_UNITY_EXEC",
                    message="Failed to start Unity batchmode process.",
                    data={
                        "command": command,
                        "error": str(exc),
                    },
                )
            )

        if completed.returncode != 0:
            stdout_text = _decode_process_output(completed.stdout)
            stderr_text = _decode_process_output(completed.stderr)
            return _emit(
                _error_response(
                    code="BRIDGE_UNITY_FAILED",
                    message="Unity batchmode process returned non-zero exit code.",
                    data={
                        "returncode": completed.returncode,
                        "command": command,
                        "stdout": stdout_text,
                        "stderr": stderr_text,
                        "log_path": str(log_path),
                    },
                )
            )

        if not response_path.exists():
            return _emit(
                _error_response(
                    code="BRIDGE_UNITY_RESPONSE_MISSING",
                    message="Unity batchmode response file is missing.",
                    data={
                        "response_path": str(response_path),
                        "log_path": str(log_path),
                    },
                )
            )

        try:
            unity_payload_raw = response_path.read_text(encoding="utf-8")
            unity_payload = json.loads(unity_payload_raw)
        except OSError as exc:
            return _emit(
                _error_response(
                    code="BRIDGE_UNITY_RESPONSE_READ",
                    message="Unity batchmode response file could not be read.",
                    data={
                        "response_path": str(response_path),
                        "error": str(exc),
                    },
                )
            )
        except json.JSONDecodeError as exc:
            return _emit(
                _error_response(
                    code="BRIDGE_UNITY_RESPONSE_JSON",
                    message="Unity batchmode response file is not valid JSON.",
                    data={
                        "response_path": str(response_path),
                        "error": str(exc),
                    },
                )
            )

    if not isinstance(unity_payload, dict):
        return _emit(
            _error_response(
                code="BRIDGE_UNITY_RESPONSE_SCHEMA",
                message="Unity batchmode response root must be an object.",
            )
        )

    return _emit(
        _finalize_unity_response(
            payload=unity_payload,
            target=target,
            op_count=len(ops),
        )
    )


if __name__ == "__main__":
    raise SystemExit(main())
