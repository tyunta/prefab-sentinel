from __future__ import annotations

import contextlib
import json
import os
import sys
import time
import uuid
from collections.abc import Sequence
from pathlib import Path
from typing import Any, TextIO

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

# Issue #297: ``_bridge_codes`` is a sibling module under ``tools/``.
# ``tools/`` is not a Python package (no ``__init__.py``), so the flat
# import below requires the directory to be on ``sys.path`` regardless
# of whether the bridge is invoked as a script or imported via the
# ``tools.unity_patch_bridge`` test path.
_TOOLS_DIR = Path(__file__).resolve().parent
if str(_TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(_TOOLS_DIR))

# Issue #297: wire-level bridge codes are owned by a sibling constants
# module so every emit site is statically discoverable by symbol lookup
# rather than by grep across bare-string literals.
from _bridge_codes import (
    BRIDGE_EDITOR_RESPONSE_READ,
    BRIDGE_EDITOR_TIMEOUT,
    BRIDGE_EDITOR_WRITE,
    BRIDGE_LEGACY_SCHEMA_REJECTED,
    BRIDGE_PROTOCOL_VERSION,
    BRIDGE_REQUEST_EMPTY,
    BRIDGE_REQUEST_JSON,
    BRIDGE_REQUEST_SCHEMA,
    BRIDGE_TIMEOUT_INVALID,
    BRIDGE_UNITY_RESPONSE_SCHEMA,
    BRIDGE_UNSUPPORTED_TARGET,
    BRIDGE_WATCH_DIR_MISSING,
)

from prefab_sentinel.patch_plan import (
    PLAN_VERSION as PROTOCOL_VERSION,
    bridge_response_state_is_valid,
    iter_resource_batches,
    normalize_patch_plan,
)
from prefab_sentinel.services.serialized_object.patch_validator import (
    bridge_diagnostics_are_valid as _bridge_diagnostics_are_valid,
)
from prefab_sentinel.services.serialized_object.resource_bridge_invoke import (
    _BRIDGE_RESPONSE_FIELDS,
    _bridge_data_is_valid,
)
from prefab_sentinel.wsl_compat import to_wsl_path

SUPPORTED_SUFFIXES = {
    ".prefab",
    ".unity",
    ".asset",
    ".mat",
    ".anim",
    ".controller",
}
BRIDGE_WATCH_DIR_ENV = "UNITYTOOL_BRIDGE_WATCH_DIR"
UNITY_TIMEOUT_SEC_ENV = "UNITYTOOL_UNITY_TIMEOUT_SEC"
DEFAULT_TIMEOUT_SEC = 120
DEFAULT_EDITOR_POLL_INTERVAL = 1.0
VALID_SEVERITIES = {"info", "warning", "error", "critical"}
SUPPORTED_OP_NAMES = {
    "set",
    "insert_array_element",
    "remove_array_element",
    "create_asset",
    "create_scene",
    "open_scene",
    "create_prefab",
    "create_root",
    "create_game_object",
    "find_game_object",
    "instantiate_prefab",
    "rename_object",
    "reparent",
    "add_component",
    "find_component",
    "remove_component",
    "save",
    "save_scene",
}
_SEVERITY_ORDER = {"info": 0, "warning": 1, "error": 2, "critical": 3}




def _emit(payload: dict[str, Any]) -> int:
    """Write the response envelope to stdout and return a process-exit code.

    Issue #157 brings this entry point into the in-process test path, where
    callers want the bridge's failure-path code values to flow through the
    return value as well as the response payload.  ``success=False`` maps
    to a non-zero exit code so direct callers can assert failure with a
    standard ``rc != 0`` check.
    """
    sys.stdout.write(json.dumps(payload, ensure_ascii=False))
    sys.stdout.write("\n")
    return 0 if bool(payload.get("success", False)) else 1


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











def _finalize_unity_response(
    *,
    payload: dict[str, Any],
    target: str,
    op_count: int,
) -> dict[str, Any]:
    protocol_raw = payload.get("protocol_version")
    if type(protocol_raw) is not int or protocol_raw != PROTOCOL_VERSION:
        return _error_response(
            code=BRIDGE_PROTOCOL_VERSION,
            message="Bridge protocol version mismatch.",
            data={
                "expected_protocol_version": PROTOCOL_VERSION,
                "received_protocol_version": protocol_raw,
            },
        )

    schema_error = _validate_unity_response_envelope(payload, op_count=op_count)
    if schema_error is not None:
        return schema_error

    data = dict(payload["data"])
    data["target"] = target
    data["op_count"] = op_count
    data["protocol_version"] = PROTOCOL_VERSION
    return {
        "protocol_version": PROTOCOL_VERSION,
        "success": payload["success"],
        "severity": payload["severity"],
        "code": payload["code"],
        "message": payload["message"],
        "data": data,
        "diagnostics": [dict(item) for item in payload["diagnostics"]],
    }


def _validate_unity_response_envelope(
    payload: dict[str, Any],
    *,
    op_count: int,
) -> dict[str, Any] | None:
    if set(payload) != _BRIDGE_RESPONSE_FIELDS:
        missing_fields = sorted(_BRIDGE_RESPONSE_FIELDS - set(payload))
        unknown_fields = sorted(set(payload) - _BRIDGE_RESPONSE_FIELDS)
        return _error_response(
            code=BRIDGE_UNITY_RESPONSE_SCHEMA,
            message="Editor Bridge response fields do not match the BridgeResponse schema.",
            data={
                "missing_fields": missing_fields,
                "unknown_fields": unknown_fields,
            },
        )
    success = payload["success"]
    if type(success) is not bool:
        return _error_response(
            code=BRIDGE_UNITY_RESPONSE_SCHEMA,
            message="Editor Bridge response field 'success' must be a boolean.",
        )
    severity = payload["severity"]
    if not isinstance(severity, str) or severity not in VALID_SEVERITIES:
        return _error_response(
            code=BRIDGE_UNITY_RESPONSE_SCHEMA,
            message=(
                "Editor Bridge response field 'severity' must be one of: "
                + ", ".join(sorted(VALID_SEVERITIES))
                + "."
            ),
        )
    code = payload["code"]
    if not isinstance(code, str) or not code.strip():
        return _error_response(
            code=BRIDGE_UNITY_RESPONSE_SCHEMA,
            message="Editor Bridge response field 'code' must be a non-empty string.",
        )
    if not isinstance(payload["message"], str):
        return _error_response(
            code=BRIDGE_UNITY_RESPONSE_SCHEMA,
            message="Editor Bridge response field 'message' must be a string.",
        )
    if not _bridge_data_is_valid(payload["data"], op_count=op_count):
        return _error_response(
            code=BRIDGE_UNITY_RESPONSE_SCHEMA,
            message="Editor Bridge response field 'data' does not match the BridgeData schema.",
        )

    data = payload["data"]
    if (
        data["read_only"]
        or (success and not data["executed"])
        or not bridge_response_state_is_valid(
            success=success,
            severity=severity,
            code=code,
        )
    ):
        return _error_response(
            code=BRIDGE_UNITY_RESPONSE_SCHEMA,
            message="Editor Bridge response execution state is contradictory.",
        )
    if not _bridge_diagnostics_are_valid(payload["diagnostics"]):
        return _error_response(
            code=BRIDGE_UNITY_RESPONSE_SCHEMA,
            message="Editor Bridge diagnostics do not match the BridgeDiagnostic schema.",
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
    if isinstance(value, dict) and "handle" in value and len(value) == 1:
        return {"value_kind": "handle", "value_string": str(value["handle"])}
    return {
        "value_kind": "json",
        "value_json": json.dumps(value, ensure_ascii=False),
    }


def _normalize_bridge_op(op: object) -> object:
    if not isinstance(op, dict):
        return op
    normalized: dict[str, object] = {}
    for key in (
        "op",
        "component",
        "file_id",
        "path",
        "index",
        "name",
        "result",
        "parent",
        "target",
        "type",
        "shader",
        "prefab",
        "symbol_path",
        "relative_symbol_path",
    ):
        if key in op:
            normalized[key] = op[key]

    op_name = str(op.get("op", "")).strip()
    if op_name in {"set", "insert_array_element"} and "value" in op:
        normalized.update(_encode_bridge_value(op["value"]))
    return normalized


def _normalize_bridge_ops(ops: Sequence[object]) -> list[object]:
    return [_normalize_bridge_op(op) for op in ops]


def _validate_find_game_object_op(
    op: dict[str, object],
    location: str,
) -> dict[str, str] | None:
    result = op.get("result")
    if not isinstance(result, str) or not result.strip():
        return {
            "location": f"{location}.result",
            "error": "find_game_object requires a non-empty 'result'",
        }

    has_symbol_path = "symbol_path" in op
    has_file_id = "file_id" in op
    has_target = "target" in op
    has_relative_path = "relative_symbol_path" in op
    has_existing_address = has_symbol_path or has_file_id
    has_generated_address = has_target or has_relative_path
    if has_existing_address == has_generated_address:
        return {
            "location": location,
            "error": "find_game_object requires exactly one existing or generated address",
        }

    if has_existing_address:
        if has_symbol_path == has_file_id:
            return {
                "location": f"{location}.symbol_path",
                "error": "find_game_object requires exactly one of symbol_path or file_id",
            }
        field = "symbol_path" if has_symbol_path else "file_id"
        value = op.get(field)
        if isinstance(value, bool) or not isinstance(value, (int, str)) or not str(value).strip():
            return {
                "location": f"{location}.{field}",
                "error": f"find_game_object '{field}' must be a non-empty address",
            }
        return None

    target = op.get("target")
    if not isinstance(target, str) or not target.strip():
        return {
            "location": f"{location}.target",
            "error": "generated find_game_object requires a non-empty 'target'",
        }
    relative_path = op.get("relative_symbol_path")
    if not isinstance(relative_path, str) or not relative_path.strip():
        return {
            "location": f"{location}.relative_symbol_path",
            "error": "generated find_game_object requires a non-empty 'relative_symbol_path'",
        }
    return None


# The transport also serves dedicated serialized-value writers. Public
# patch_apply keeps its stricter five-op grammar in prefab_open_dispatch.
_OPEN_PREFAB_OP_NAMES = frozenset(
    {
        "instantiate_prefab",
        "rename_object",
        "find_game_object",
        "find_component",
        "set",
        "insert_array_element",
        "remove_array_element",
    }
)


def _validate_open_prefab_operation_names(
    plan: dict[str, Any],
) -> dict[str, Any] | None:
    resource_modes = {
        resource["id"]: (resource["kind"], resource["mode"])
        for resource in plan["resources"]
    }
    for index, op in enumerate(plan["ops"]):
        if (
            resource_modes[op["resource"]] == ("prefab", "open")
            and str(op.get("op", "")).strip() not in _OPEN_PREFAB_OP_NAMES
        ):
            return {
                "location": f"ops[{index}].op",
                "error": "open prefab operation is unsupported",
            }
    return None


def _validate_bridge_ops(
    ops: list[object],
    *,
    require_resource: bool,
) -> dict[str, Any] | None:
    if not ops:
        return {"location": "ops", "error": "operations must be a non-empty array"}

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

        if op_name == "create_asset":
            type_name = op.get("type")
            shader_name = op.get("shader")
            has_type = isinstance(type_name, str) and bool(type_name.strip())
            has_shader = isinstance(shader_name, str) and bool(shader_name.strip())
            if "type" in op and not has_type:
                return {
                    "location": f"{location}.type",
                    "error": "create_asset 'type' must be a non-empty string when provided",
                }
            if "shader" in op and not has_shader:
                return {
                    "location": f"{location}.shader",
                    "error": "create_asset 'shader' must be a non-empty string when provided",
                }
            if not has_type and not has_shader:
                return {
                    "location": location,
                    "error": "create_asset requires a non-empty 'type' or 'shader'",
                }
            continue

        if op_name in {"create_scene", "open_scene"}:
            continue

        if op_name == "create_prefab":
            name = op.get("name")
            if name is not None and (not isinstance(name, str) or not name.strip()):
                return {
                    "location": f"{location}.name",
                    "error": "create_prefab 'name' must be a non-empty string when provided",
                }
            continue

        if op_name == "create_root":
            name = op.get("name")
            if not isinstance(name, str) or not name.strip():
                return {
                    "location": f"{location}.name",
                    "error": "create_root requires a non-empty 'name'",
                }
            continue

        if op_name == "create_game_object":
            name = op.get("name")
            parent = op.get("parent")
            if not isinstance(name, str) or not name.strip():
                return {
                    "location": f"{location}.name",
                    "error": "create_game_object requires a non-empty 'name'",
                }
            if not isinstance(parent, str) or not parent.strip():
                return {
                    "location": f"{location}.parent",
                    "error": "create_game_object requires a non-empty 'parent'",
                }
            continue

        if op_name == "find_game_object":
            error = _validate_find_game_object_op(op, location)
            if error is not None:
                return error
            continue

        if op_name == "instantiate_prefab":
            prefab = op.get("prefab")
            parent = op.get("parent")
            if not isinstance(prefab, str) or not prefab.strip():
                return {
                    "location": f"{location}.prefab",
                    "error": "instantiate_prefab requires a non-empty 'prefab'",
                }
            if not isinstance(parent, str) or not parent.strip():
                return {
                    "location": f"{location}.parent",
                    "error": "instantiate_prefab requires a non-empty 'parent'",
                }
            continue

        if op_name == "rename_object":
            target = op.get("target")
            name = op.get("name")
            if not isinstance(target, str) or not target.strip():
                return {
                    "location": f"{location}.target",
                    "error": "rename_object requires a non-empty 'target'",
                }
            if not isinstance(name, str) or not name.strip():
                return {
                    "location": f"{location}.name",
                    "error": "rename_object requires a non-empty 'name'",
                }
            continue

        if op_name == "reparent":
            target = op.get("target")
            parent = op.get("parent")
            if not isinstance(target, str) or not target.strip():
                return {
                    "location": f"{location}.target",
                    "error": "reparent requires a non-empty 'target'",
                }
            if not isinstance(parent, str) or not parent.strip():
                return {
                    "location": f"{location}.parent",
                    "error": "reparent requires a non-empty 'parent'",
                }
            continue

        if op_name in {"add_component", "find_component"}:
            target = op.get("target")
            type_name = op.get("type")
            if not isinstance(target, str) or not target.strip():
                return {
                    "location": f"{location}.target",
                    "error": f"{op_name} requires a non-empty 'target'",
                }
            if not isinstance(type_name, str) or not type_name.strip():
                return {
                    "location": f"{location}.type",
                    "error": f"{op_name} requires a non-empty 'type'",
                }
            continue

        if op_name == "remove_component":
            target = op.get("target")
            comp = op.get("component")
            has_target = isinstance(target, str) and bool(target.strip())
            has_comp = isinstance(comp, str) and bool(comp.strip())
            if not has_target and not has_comp:
                return {
                    "location": location,
                    "error": "remove_component requires a non-empty 'target' or 'component'",
                }
            continue

        if op_name in {"save", "save_scene"}:
            continue

        component = op.get("component")
        target = op.get("target")
        file_id = op.get("file_id")
        has_component = isinstance(component, str) and bool(component.strip())
        has_target = isinstance(target, str) and bool(target.strip())
        # Issue #37: value mutation ops may identify their target component
        # by an exact 'file_id' instead of a component selector.
        has_file_id = isinstance(file_id, str) and bool(file_id.strip())
        is_value_op = op_name in {
            "set",
            "insert_array_element",
            "remove_array_element",
        }
        if not has_component and not has_target and not (is_value_op and has_file_id):
            return {
                "location": location,
                "error": (
                    f"{op_name} op requires a non-empty 'component', 'target', or 'file_id'"
                    if is_value_op
                    else "mutation op requires a non-empty 'component' or 'target'"
                ),
            }

        path = op.get("path")
        if not isinstance(path, str) or not path.strip():
            return {"location": f"{location}.path", "error": "path is required"}

        if op_name in {"set", "insert_array_element"} and "value" not in op:
            return {
                "location": location,
                "error": f"{op_name} operation requires 'value'",
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
        (str(response.get("severity", "error")) for response in responses),
        key=lambda value: _SEVERITY_ORDER.get(value, _SEVERITY_ORDER["error"]),
    )


def _resource_summary(
    resource: dict[str, Any],
    ops: list[dict[str, Any]],
    response: dict[str, Any] | None,
) -> dict[str, Any]:
    summary = {
        "id": resource["id"],
        "kind": resource["kind"],
        "path": resource["path"],
        "mode": resource["mode"],
        "op_count": len(ops),
        "applied": 0,
        "executed": response is not None,
    }
    if response is None:
        return summary

    data = response.get("data", {})
    if not isinstance(data, dict):
        data = {}
    summary.update(
        {
            "applied": data.get("applied", 0),
            "success": response.get("success", False),
            "severity": response.get("severity", "error"),
            "code": response.get("code", ""),
        }
    )
    return summary


def _resource_summaries(
    *,
    resource_batches: list[tuple[dict[str, Any], list[dict[str, Any]]]],
    executable_batches: list[tuple[dict[str, Any], list[dict[str, Any]]]],
    responses: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    responses_by_id = {
        resource["id"]: response
        for (resource, _), response in zip(executable_batches, responses, strict=True)
    }
    return [
        _resource_summary(resource, ops, responses_by_id.get(resource["id"]))
        for resource, ops in resource_batches
    ]


def _finalize_bridge_plan_response(
    *,
    plan: dict[str, Any],
    responses: list[dict[str, Any]],
    resource_batches: list[tuple[dict[str, Any], list[dict[str, Any]]]],
    executable_batches: list[tuple[dict[str, Any], list[dict[str, Any]]]],
) -> dict[str, Any]:
    malformed_response = next(
        (
            response
            for response in responses
            if response.get("success") is False
            and response.get("code") == BRIDGE_UNITY_RESPONSE_SCHEMA
        ),
        None,
    )
    if malformed_response is not None:
        return dict(malformed_response)

    if len(resource_batches) == 1 and len(responses) == 1:
        return dict(responses[0])

    resource_summaries = _resource_summaries(
        resource_batches=resource_batches,
        executable_batches=executable_batches,
        responses=responses,
    )
    success = all(bool(response.get("success", False)) for response in responses)
    applied_total = sum(
        int(summary["applied"]) if type(summary.get("applied")) is int else 0
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


def _to_relative_asset_path(path: str) -> str:
    """Extract relative ``Assets/...`` path, stripping any absolute prefix.

    Editor Bridge runs inside the same Unity Editor, so relative asset paths
    are sufficient.  Sending absolute paths causes prefix-mismatch failures
    when WSL and Windows path normalisation disagree.
    """
    normalized = path.replace("\\", "/")
    idx = normalized.find("Assets/")
    if idx >= 0:
        return normalized[idx:]
    return normalized


def _run_via_editor_bridge(
    *,
    watch_dir: Path,
    timeout_sec: int,
    resource: dict[str, Any],
    ops: list[dict[str, Any]],
) -> dict[str, Any]:
    """Write a request file to watch_dir and poll for the response."""
    target = _to_relative_asset_path(str(resource.get("path", "")).strip())
    request_id = uuid.uuid4().hex
    request_file = watch_dir / f"{request_id}.request.json"
    response_file = watch_dir / f"{request_id}.response.json"
    tmp_file = Path(str(request_file) + ".tmp")

    # Issue #63: the resident EditorBridge dispatches each file-IPC
    # request by its ``action`` field. Without a discriminator the
    # request lands on EditorBridge's empty-action branch, which replies
    # with the editor-control protocol version (1); the check in
    # ``_finalize_unity_response`` below then surfaces a spurious
    # BRIDGE_PROTOCOL_VERSION mismatch that masks the routing failure.
    # ``patch_apply`` is unclaimed by the editor-control and runtime
    # action sets, so a discriminated request falls through to
    # UnityPatchBridge. ``protocol_version`` is the patch protocol
    # (PLAN_VERSION) owned by UnityPatchBridge — distinct from the
    # editor-control protocol; with routing fixed the response carries
    # the matching patch version, so the protocol check compares like
    # against like.
    request_payload = {
        "action": "patch_apply",
        "protocol_version": PROTOCOL_VERSION,
        "target": target,
        "kind": resource.get("kind", ""),
        "mode": resource.get("mode", "open"),
        "ops": _normalize_bridge_ops(ops),
    }

    # Atomic write: .tmp → rename to avoid partial reads by the watcher.
    try:
        tmp_file.write_text(
            json.dumps(request_payload, ensure_ascii=False),
            encoding="utf-8",
        )
        tmp_file.rename(request_file)
    except OSError:
        _try_delete(tmp_file)
        _try_delete(request_file)
        return _error_response(
            code=BRIDGE_EDITOR_WRITE,
            message="Failed to write editor bridge request file.",
            data={
                "resource_id": resource.get("id"),
                "target": target,
            },
        )

    # Poll for response.
    deadline = time.monotonic() + timeout_sec
    while time.monotonic() < deadline:
        try:
            response_ready = response_file.exists()
        except OSError:
            _try_delete(request_file)
            _try_delete(response_file)
            return _error_response(
                code=BRIDGE_EDITOR_RESPONSE_READ,
                message="Editor bridge response file status could not be read.",
                data={
                    "resource_id": resource.get("id"),
                    "target": target,
                },
            )
        if response_ready:
            try:
                raw = response_file.read_text(encoding="utf-8")
                unity_payload = json.loads(raw)
            except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                return _error_response(
                    code=BRIDGE_EDITOR_RESPONSE_READ,
                    message="Editor bridge response file could not be read.",
                    data={
                        "resource_id": resource.get("id"),
                        "target": target,
                    },
                )
            finally:
                _try_delete(request_file)
                _try_delete(response_file)

            if not isinstance(unity_payload, dict):
                return _error_response(
                    code=BRIDGE_UNITY_RESPONSE_SCHEMA,
                    message="Editor bridge response root must be an object.",
                    data={"resource_id": resource.get("id"), "target": target},
                )

            return _finalize_unity_response(
                payload=unity_payload,
                target=target,
                op_count=len(ops),
            )

        time.sleep(DEFAULT_EDITOR_POLL_INTERVAL)

    # Timeout — clean up request file.
    _try_delete(request_file)
    return _error_response(
        code=BRIDGE_EDITOR_TIMEOUT,
        message="Editor bridge response timed out.",
        data={
            "resource_id": resource.get("id"),
            "target": target,
            "timeout_sec": timeout_sec,
        },
    )


def _try_delete(path: Path) -> None:
    with contextlib.suppress(OSError):
        path.unlink(missing_ok=True)


def main(argv: list[str] | None = None, stdin: TextIO | None = None) -> int:
    """Drive the patch bridge end-to-end.

    Args:
        argv: Optional argument list; reserved for future flags.  The
            bridge currently consumes no positional or option arguments,
            so any non-empty list is rejected as malformed input.
        stdin: Optional text stream to read the request from.  Defaults
            to ``sys.stdin`` when omitted, matching the script-mode
            behaviour.

    Returns:
        Process exit code: ``0`` on a success response, non-zero on any
        failure response (the response payload is still written to
        stdout).
    """
    if argv:
        return _emit(
            _error_response(
                code=BRIDGE_REQUEST_SCHEMA,
                message="Bridge accepts no command-line arguments.",
                data={"received_argv": list(argv)},
            )
        )
    source = stdin if stdin is not None else sys.stdin
    raw = source.read()
    if not raw.strip():
        return _emit(
            _error_response(
                code=BRIDGE_REQUEST_EMPTY,
                message="Bridge request body is empty.",
            )
        )

    try:
        request = json.loads(raw)
    except json.JSONDecodeError:
        return _emit(
            _error_response(
                code=BRIDGE_REQUEST_JSON,
                message="Bridge request must be valid JSON.",
            )
        )

    if not isinstance(request, dict):
        return _emit(
            _error_response(
                code=BRIDGE_REQUEST_SCHEMA,
                message="Bridge request root must be an object.",
            )
        )

    # #88: the legacy ``{target, ops}`` shape is no longer accepted.  Detect
    # and reject it before any normalization so the caller sees a dedicated
    # error code instead of a generic schema message.
    if "target" in request:
        received_keys = sorted(request.keys())
        return _emit(
            _error_response(
                code=BRIDGE_LEGACY_SCHEMA_REJECTED,
                message=(
                    "Bridge request uses the legacy ``target`` shape; send the "
                    "canonical v2 payload with ``plan_version`` and "
                    "``resources`` instead."
                ),
                data={"received_keys": received_keys},
            )
        )

    protocol_version = request.get("protocol_version")
    if type(protocol_version) is not int or protocol_version != PROTOCOL_VERSION:
        return _emit(
            _error_response(
                code=BRIDGE_PROTOCOL_VERSION,
                message="Bridge protocol version mismatch.",
                data={
                    "expected_protocol_version": PROTOCOL_VERSION,
                    "received_protocol_version": protocol_version,
                },
            )
        )

    request_plan = {key: value for key, value in request.items() if key != "protocol_version"}
    try:
        plan = normalize_patch_plan(request_plan)
    except ValueError:
        return _emit(
            _error_response(
                code=BRIDGE_REQUEST_SCHEMA,
                message="Bridge request schema is invalid.",
            )
        )

    mode_schema_error = _validate_open_prefab_operation_names(plan)
    if mode_schema_error is not None:
        return _emit(
            _error_response(
                code=BRIDGE_REQUEST_SCHEMA,
                message="ops contain an operation unsupported by the resource mode.",
                data=mode_schema_error,
            )
        )

    ops = plan.get("ops", [])
    ops_schema_error = _validate_bridge_ops(ops, require_resource=True)
    if ops_schema_error is not None:
        return _emit(
            _error_response(
                code=BRIDGE_REQUEST_SCHEMA,
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
                    code=BRIDGE_UNSUPPORTED_TARGET,
                    message="Bridge target extension is not supported.",
                    data={"resource_id": resource.get("id"), "target": target},
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
                code=BRIDGE_TIMEOUT_INVALID,
                message=f"{UNITY_TIMEOUT_SEC_ENV} must be a positive integer.",
                data={"received_timeout": timeout_raw},
            )
        )

    watch_dir_raw = os.environ.get(BRIDGE_WATCH_DIR_ENV, "").strip()
    if not watch_dir_raw:
        return _emit(
            _error_response(
                code=BRIDGE_WATCH_DIR_MISSING,
                message=f"{BRIDGE_WATCH_DIR_ENV} is required to reach the Unity Editor Bridge.",
            )
        )
    watch_dir = Path(to_wsl_path(watch_dir_raw))
    try:
        watch_dir_ready = watch_dir.is_dir()
    except OSError:
        watch_dir_ready = False
    if not watch_dir_ready:
        return _emit(
            _error_response(
                code=BRIDGE_WATCH_DIR_MISSING,
                message=(f"{BRIDGE_WATCH_DIR_ENV} must name an existing Editor Bridge watch directory."),
            )
        )
    executable_batches = [batch for batch in resource_batches if batch[1]]
    responses = [
        _run_via_editor_bridge(
            watch_dir=watch_dir,
            timeout_sec=timeout_sec,
            resource=resource,
            ops=resource_ops,
        )
        for resource, resource_ops in executable_batches
    ]

    return _emit(
        _finalize_bridge_plan_response(
            plan=plan,
            responses=responses,
            resource_batches=resource_batches,
            executable_batches=executable_batches,
        )
    )


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
