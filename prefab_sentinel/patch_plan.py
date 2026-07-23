from __future__ import annotations

import hashlib
import hmac
from copy import deepcopy
from pathlib import Path
from typing import Any

from prefab_sentinel.json_io import load_json_file

PLAN_VERSION = 2
_RESOURCE_KIND_BY_SUFFIX = {
    ".json": "json",
    ".prefab": "prefab",
    ".unity": "scene",
    ".asset": "asset",
    ".mat": "material",
    ".anim": "animation",
    ".controller": "controller",
}


_RESOURCE_KINDS = frozenset(_RESOURCE_KIND_BY_SUFFIX.values())
_RESOURCE_MODES = frozenset({"open", "create"})


def _error(field: str, message: str) -> ValueError:
    return ValueError(f"Patch plan field '{field}' {message}")


def _infer_resource_kind(path: str) -> str:
    suffix = Path(path).suffix.lower()
    return _RESOURCE_KIND_BY_SUFFIX.get(suffix, "asset")


def _normalize_resource(resource: object, index: int) -> dict[str, Any]:
    field_prefix = f"resources[{index}]"
    if not isinstance(resource, dict):
        raise _error(field_prefix, "must be an object.")

    resource_id = resource.get("id")
    if not isinstance(resource_id, str) or not resource_id.strip():
        raise _error(f"{field_prefix}.id", "must be a non-empty string.")

    path = resource.get("path")
    if not isinstance(path, str) or not path.strip():
        raise _error(f"{field_prefix}.path", "must be a non-empty string.")

    if "kind" not in resource:
        kind = _infer_resource_kind(path)
    else:
        kind_value = resource["kind"]
        if not isinstance(kind_value, str) or not kind_value.strip():
            raise _error(f"{field_prefix}.kind", "must be a non-empty string when provided.")
        kind = kind_value.strip()
    if kind not in _RESOURCE_KINDS:
        raise _error(f"{field_prefix}.kind", "is not supported.")

    mode_value = resource.get("mode", "open")
    if not isinstance(mode_value, str) or not mode_value.strip():
        raise _error(f"{field_prefix}.mode", "must be a non-empty string when provided.")
    mode = mode_value.strip()
    if mode not in _RESOURCE_MODES:
        raise _error(f"{field_prefix}.mode", "is not supported.")

    normalized = deepcopy(resource)
    normalized["id"] = resource_id.strip()
    normalized["path"] = path.strip()
    normalized["kind"] = kind
    normalized["mode"] = mode
    return normalized


def normalize_patch_plan(payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("Patch plan root must be an object.")

    raw_version = payload.get("plan_version")
    if raw_version is None:
        raise _error(
            "plan_version",
            "is required; the legacy {target, ops} shape is no longer accepted.",
        )
    if type(raw_version) is int:
        plan_version = raw_version
    elif isinstance(raw_version, str):
        try:
            plan_version = int(raw_version)
        except ValueError as exc:
            raise _error(
                "plan_version",
                f"must be an integer, got {raw_version!r}.",
            ) from exc
    else:
        raise _error(
            "plan_version",
            f"must be an integer, got {raw_version!r}.",
        )
    if plan_version != PLAN_VERSION:
        raise _error("plan_version", f"must equal {PLAN_VERSION}, got {plan_version}.")

    resources = payload.get("resources")
    if not isinstance(resources, list) or not resources:
        raise _error("resources", "must be a non-empty array.")

    ops = payload.get("ops")
    if not isinstance(ops, list):
        raise _error("ops", "must be an array.")

    postconditions = payload.get("postconditions", [])
    if not isinstance(postconditions, list):
        raise _error("postconditions", "must be an array when provided.")

    normalized_resources: list[dict[str, Any]] = [
        _normalize_resource(resource, index) for index, resource in enumerate(resources)
    ]
    normalized_ops: list[dict[str, Any]] = [deepcopy(op) for op in ops]
    normalized_postconditions: list[dict[str, Any]] = [deepcopy(pc) for pc in postconditions]

    resource_ids: set[str] = set()
    resource_map: dict[str, dict[str, Any]] = {}
    for index, resource in enumerate(normalized_resources):
        resource_id = resource["id"]
        if resource_id in resource_ids:
            raise _error(f"resources[{index}].id", f"duplicates resource id '{resource_id}'.")
        resource_ids.add(resource_id)
        resource_map[resource_id] = resource

    for index, op in enumerate(normalized_ops):
        if not isinstance(op, dict):
            raise _error(f"ops[{index}]", "must be an object.")
        resource_id = op.get("resource")
        if not isinstance(resource_id, str) or not resource_id.strip():
            raise _error(f"ops[{index}].resource", "must be a non-empty string.")
        resource_id = resource_id.strip()
        if resource_id not in resource_map:
            raise _error(
                f"ops[{index}].resource",
                f"references unknown resource id '{resource_id}'.",
            )
        op["resource"] = resource_id

    for index, postcondition in enumerate(normalized_postconditions):
        if not isinstance(postcondition, dict):
            raise _error(f"postconditions[{index}]", "must be an object.")
        postcondition_type = postcondition.get("type")
        if not isinstance(postcondition_type, str) or not postcondition_type.strip():
            raise _error(
                f"postconditions[{index}].type",
                "must be a non-empty string.",
            )
        postcondition["type"] = postcondition_type.strip()

    return {
        "plan_version": PLAN_VERSION,
        "resources": normalized_resources,
        "ops": normalized_ops,
        "postconditions": normalized_postconditions,
    }


def load_patch_plan(path: Path) -> dict[str, Any]:
    payload = load_json_file(path)
    return normalize_patch_plan(payload)


def compute_patch_plan_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def compute_patch_plan_hmac_sha256(path: Path, key: str) -> str:
    digest = hmac.new(key.encode("utf-8"), path.read_bytes(), hashlib.sha256)
    return digest.hexdigest()


def count_plan_ops(plan: dict[str, Any]) -> int:
    ops = plan.get("ops")
    return len(ops) if isinstance(ops, list) else 0


def iter_resource_batches(plan: dict[str, Any]) -> list[tuple[dict[str, Any], list[dict[str, Any]]]]:
    resources = plan.get("resources")
    ops = plan.get("ops")
    if not isinstance(resources, list) or not isinstance(ops, list):
        raise ValueError("Patch plan must be normalized before iterating resources.")

    grouped: dict[str, list[dict[str, Any]]] = {
        resource["id"]: [] for resource in resources if isinstance(resource, dict) and "id" in resource
    }
    for op in ops:
        resource_id = op["resource"]
        grouped.setdefault(resource_id, []).append(
            {key: deepcopy(value) for key, value in op.items() if key != "resource"}
        )

    return [(deepcopy(resource), grouped.get(resource["id"], [])) for resource in resources]


def is_single_open_prefab_plan(plan: dict[str, Any]) -> bool:
    resources = plan.get("resources")
    if not isinstance(resources, list) or len(resources) != 1:
        return False
    resource = resources[0]
    return isinstance(resource, dict) and resource.get("kind") == "prefab" and resource.get("mode") == "open"


def build_bridge_request(plan: dict[str, Any]) -> dict[str, Any]:
    """Build the stdin request for ``unity_patch_bridge.main``.

    Emits only the canonical v2 shape (``protocol_version``, ``plan_version``,
    ``resources``, ``ops``).  The bridge rejects any request carrying a
    top-level ``target`` key with ``BRIDGE_LEGACY_SCHEMA_REJECTED`` (#88), so
    this function must not populate that key even for single-resource plans.
    """
    return {
        "protocol_version": PLAN_VERSION,
        "plan_version": PLAN_VERSION,
        "resources": deepcopy(plan.get("resources", [])),
        "ops": deepcopy(plan.get("ops", [])),
    }


_BRIDGE_PLAN_DATA_FIELDS = frozenset(
    ("plan_version", "resource_count", "op_count", "applied", "resources", "read_only", "executed", "protocol_version")
)
_BRIDGE_RESOURCE_FIELDS = frozenset(
    ("id", "kind", "path", "mode", "op_count", "applied", "executed")
)
_BRIDGE_RESOURCE_RESULT_FIELDS = frozenset(("success", "severity", "code"))
_BRIDGE_SEVERITIES = frozenset(("info", "warning", "error", "critical"))


def bridge_response_state_is_valid(
    *,
    success: bool,
    severity: str,
    code: str,
) -> bool:
    if success:
        return severity not in {"error", "critical"}
    return severity in {"error", "critical"} and code != "SER_APPLY_OK"


def _bridge_resource_summary_is_valid(value: object) -> bool:
    if not isinstance(value, dict) or type(value.get("executed")) is not bool:
        return False
    executed = value["executed"]
    expected_fields = (
        _BRIDGE_RESOURCE_FIELDS | _BRIDGE_RESOURCE_RESULT_FIELDS
        if executed
        else _BRIDGE_RESOURCE_FIELDS
    )
    if set(value) != expected_fields:
        return False
    if (
        not isinstance(value["id"], str)
        or not value["id"].strip()
        or not isinstance(value["kind"], str)
        or value["kind"] not in _RESOURCE_KINDS
        or not isinstance(value["path"], str)
        or not value["path"].strip()
        or not isinstance(value["mode"], str)
        or value["mode"] not in _RESOURCE_MODES
    ):
        return False
    op_count = value["op_count"]
    applied = value["applied"]
    if type(op_count) is not int or type(applied) is not int:
        return False
    if not executed:
        return op_count == 0 and applied == 0

    success = value["success"]
    severity = value["severity"]
    code = value["code"]
    if (
        type(success) is not bool
        or not isinstance(severity, str)
        or severity not in _BRIDGE_SEVERITIES
        or not isinstance(code, str)
        or not code.strip()
    ):
        return False
    return (
        op_count > 0
        and 0 <= applied <= op_count
        and bridge_response_state_is_valid(
            success=success,
            severity=severity,
            code=code,
        )
    )


def bridge_plan_response_data_is_valid(
    data: object,
    *,
    op_count: int,
    success: bool,
) -> bool:
    if not isinstance(data, dict) or set(data) != _BRIDGE_PLAN_DATA_FIELDS:
        return False
    int_fields = ("plan_version", "resource_count", "op_count", "applied", "protocol_version")
    if any(type(data[field]) is not int for field in int_fields):
        return False
    if type(data["read_only"]) is not bool or type(data["executed"]) is not bool:
        return False
    resources = data["resources"]
    if not isinstance(resources, list) or not all(
        _bridge_resource_summary_is_valid(resource) for resource in resources
    ):
        return False
    executed = [resource for resource in resources if resource["executed"]]
    return (
        data["plan_version"] == PLAN_VERSION
        and data["protocol_version"] == PLAN_VERSION
        and data["resource_count"] == len(resources) >= 2
        and data["op_count"] == op_count
        and sum(resource["op_count"] for resource in resources) == op_count
        and data["applied"] == sum(resource["applied"] for resource in resources)
        and data["read_only"] is False
        and data["executed"] is True
        and len({resource["id"] for resource in resources}) == len(resources)
        and bool(executed)
        and all(resource["success"] for resource in executed) is success
    )
