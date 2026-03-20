from __future__ import annotations

from copy import deepcopy
import hashlib
import hmac
import json
from pathlib import Path
from typing import Any

PLAN_VERSION = 2
_DEFAULT_RESOURCE_ID = "target"
_RESOURCE_KIND_BY_SUFFIX = {
    ".json": "json",
    ".prefab": "prefab",
    ".unity": "scene",
    ".asset": "asset",
    ".mat": "material",
    ".anim": "animation",
    ".controller": "controller",
}


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

    kind_value = resource.get("kind")
    kind = str(kind_value).strip() if kind_value is not None else ""
    if not kind:
        kind = _infer_resource_kind(path)

    mode_value = resource.get("mode", "open")
    mode = str(mode_value).strip()
    if not mode:
        raise _error(f"{field_prefix}.mode", "must be a non-empty string when provided.")

    normalized = deepcopy(resource)
    normalized["id"] = resource_id.strip()
    normalized["path"] = path.strip()
    normalized["kind"] = kind
    normalized["mode"] = mode
    return normalized


def _normalize_v1_plan(payload: dict[str, Any]) -> dict[str, Any]:
    target = payload.get("target")
    if not isinstance(target, str) or not target.strip():
        raise _error("target", "must be a non-empty string.")

    ops = payload.get("ops")
    if not isinstance(ops, list):
        raise _error("ops", "must be an array.")

    resource_id = _DEFAULT_RESOURCE_ID
    return {
        "plan_version": PLAN_VERSION,
        "resources": [
            {
                "id": resource_id,
                "kind": _infer_resource_kind(target),
                "path": target.strip(),
                "mode": "open",
            }
        ],
        "ops": [{**deepcopy(op), "resource": resource_id} for op in ops],
        "postconditions": [],
    }


def normalize_patch_plan(payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("Patch plan root must be an object.")

    if "plan_version" not in payload:
        normalized = _normalize_v1_plan(payload)
    else:
        plan_version = payload.get("plan_version")
        if plan_version != PLAN_VERSION:
            raise _error("plan_version", f"must equal {PLAN_VERSION}.")

        resources = payload.get("resources")
        if not isinstance(resources, list) or not resources:
            raise _error("resources", "must be a non-empty array.")

        ops = payload.get("ops")
        if not isinstance(ops, list):
            raise _error("ops", "must be an array.")

        postconditions = payload.get("postconditions", [])
        if not isinstance(postconditions, list):
            raise _error("postconditions", "must be an array when provided.")

        normalized = {
            "plan_version": PLAN_VERSION,
            "resources": [_normalize_resource(resource, index) for index, resource in enumerate(resources)],
            "ops": [deepcopy(op) for op in ops],
            "postconditions": [deepcopy(postcondition) for postcondition in postconditions],
        }

    resource_ids: set[str] = set()
    resource_map: dict[str, dict[str, Any]] = {}
    for index, resource in enumerate(normalized["resources"]):
        resource_id = resource["id"]
        if resource_id in resource_ids:
            raise _error(f"resources[{index}].id", f"duplicates resource id '{resource_id}'.")
        resource_ids.add(resource_id)
        resource_map[resource_id] = resource

    for index, op in enumerate(normalized["ops"]):
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

    for index, postcondition in enumerate(normalized.get("postconditions", [])):
        if not isinstance(postcondition, dict):
            raise _error(f"postconditions[{index}]", "must be an object.")
        postcondition_type = postcondition.get("type")
        if not isinstance(postcondition_type, str) or not postcondition_type.strip():
            raise _error(
                f"postconditions[{index}].type",
                "must be a non-empty string.",
            )
        postcondition["type"] = postcondition_type.strip()

    return normalized


def load_patch_plan(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
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

    return [
        (deepcopy(resource), grouped.get(resource["id"], []))
        for resource in resources
    ]


def build_bridge_request(plan: dict[str, Any]) -> dict[str, Any]:
    request = {
        "protocol_version": PLAN_VERSION,
        "plan_version": PLAN_VERSION,
        "resources": deepcopy(plan.get("resources", [])),
        "ops": deepcopy(plan.get("ops", [])),
    }
    resources = request["resources"]
    if len(resources) == 1 and isinstance(resources[0], dict):
        request["target"] = resources[0].get("path", "")
    return request
