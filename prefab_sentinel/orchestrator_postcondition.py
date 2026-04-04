"""Postcondition validation functions extracted from Phase1Orchestrator."""

from __future__ import annotations

from typing import Any

from prefab_sentinel.contracts import (
    ToolResponse,
    error_response,
    success_response,
)
from prefab_sentinel.services.reference_resolver import ReferenceResolverService
from prefab_sentinel.services.serialized_object import SerializedObjectService


def _validate_postcondition_schema(
    postcondition: object,
    *,
    resource_ids: set[str],
) -> ToolResponse:
    if not isinstance(postcondition, dict):
        return error_response(
            "POST_SCHEMA_ERROR",
            "Postcondition must be an object.",
            data={"read_only": True, "executed": False},
        )

    postcondition_type = str(postcondition.get("type", "")).strip()
    if not postcondition_type:
        return error_response(
            "POST_SCHEMA_ERROR",
            "Postcondition type is required.",
            data={"read_only": True, "executed": False},
        )

    if postcondition_type == "asset_exists":
        resource_id = str(postcondition.get("resource", "")).strip()
        explicit_path = str(postcondition.get("path", "")).strip()
        if bool(resource_id) == bool(explicit_path):
            return error_response(
                "POST_SCHEMA_ERROR",
                "asset_exists requires exactly one of 'resource' or 'path'.",
                data={"type": postcondition_type, "read_only": True, "executed": False},
            )
        if resource_id and resource_id not in resource_ids:
            return error_response(
                "POST_SCHEMA_ERROR",
                "asset_exists references an unknown resource id.",
                data={
                    "type": postcondition_type,
                    "resource": resource_id,
                    "read_only": True,
                    "executed": False,
                },
            )
        return success_response(
            "POST_SCHEMA_OK",
            "Postcondition schema validated.",
            data={"type": postcondition_type, "read_only": True, "executed": False},
        )

    if postcondition_type == "broken_refs":
        scope = str(postcondition.get("scope", "")).strip()
        if not scope:
            return error_response(
                "POST_SCHEMA_ERROR",
                "broken_refs requires a non-empty 'scope'.",
                data={"type": postcondition_type, "read_only": True, "executed": False},
            )
        expected_count = postcondition.get("expected_count", 0)
        if not isinstance(expected_count, int) or expected_count < 0:
            return error_response(
                "POST_SCHEMA_ERROR",
                "broken_refs.expected_count must be a non-negative integer.",
                data={
                    "type": postcondition_type,
                    "scope": scope,
                    "read_only": True,
                    "executed": False,
                },
            )
        for field_name in ("exclude_patterns", "ignore_asset_guids"):
            values = postcondition.get(field_name, [])
            if not isinstance(values, list) or any(
                not isinstance(value, str) for value in values
            ):
                return error_response(
                    "POST_SCHEMA_ERROR",
                    f"broken_refs.{field_name} must be an array of strings.",
                    data={
                        "type": postcondition_type,
                        "scope": scope,
                        "read_only": True,
                        "executed": False,
                    },
                )
        max_diagnostics = postcondition.get("max_diagnostics", 200)
        if not isinstance(max_diagnostics, int) or max_diagnostics < 0:
            return error_response(
                "POST_SCHEMA_ERROR",
                "broken_refs.max_diagnostics must be a non-negative integer.",
                data={
                    "type": postcondition_type,
                    "scope": scope,
                    "read_only": True,
                    "executed": False,
                },
            )
        return success_response(
            "POST_SCHEMA_OK",
            "Postcondition schema validated.",
            data={"type": postcondition_type, "scope": scope, "read_only": True, "executed": False},
        )

    return error_response(
        "POST_SCHEMA_ERROR",
        "Postcondition type is not supported.",
        data={
            "type": postcondition_type,
            "read_only": True,
            "executed": False,
        },
    )


def _evaluate_postcondition(
    serialized_object: SerializedObjectService,
    reference_resolver: ReferenceResolverService,
    postcondition: dict[str, Any],
    *,
    resource_map: dict[str, dict[str, Any]],
) -> ToolResponse:
    postcondition_type = str(postcondition.get("type", "")).strip()
    if postcondition_type == "asset_exists":
        resource_id = str(postcondition.get("resource", "")).strip()
        if resource_id:
            target = str(resource_map[resource_id].get("path", "")).strip()
        else:
            target = str(postcondition.get("path", "")).strip()
        target_path = serialized_object._resolve_target_path(target)
        exists = target_path.exists()
        if not exists:
            return error_response(
                "POST_ASSET_EXISTS_FAILED",
                "asset_exists postcondition failed: target path was not created."
                " Check the preceding patch operations for errors;"
                " verify the file was saved successfully.",
                data={
                    "type": postcondition_type,
                    "resource": resource_id or None,
                    "path": str(target_path),
                    "exists": False,
                    "read_only": True,
                    "executed": True,
                },
            )
        return success_response(
            "POST_ASSET_EXISTS_OK",
            "asset_exists postcondition passed.",
            data={
                "type": postcondition_type,
                "resource": resource_id or None,
                "path": str(target_path),
                "exists": True,
                "read_only": True,
                "executed": True,
            },
        )

    scope = str(postcondition.get("scope", "")).strip()
    expected_count = int(postcondition.get("expected_count", 0))
    scan = reference_resolver.scan_broken_references(
        scope=scope,
        include_diagnostics=bool(postcondition.get("include_diagnostics", False)),
        max_diagnostics=int(postcondition.get("max_diagnostics", 200)),
        exclude_patterns=tuple(postcondition.get("exclude_patterns", [])),
        ignore_asset_guids=tuple(postcondition.get("ignore_asset_guids", [])),
    )
    if scan.code in {"REF404", "REF001"}:
        return error_response(
            "POST_BROKEN_REFS_ERROR",
            "broken_refs postcondition could not be evaluated.",
            data={
                "type": postcondition_type,
                "scope": scope,
                "expected_count": expected_count,
                "read_only": True,
                "executed": True,
                "scan_code": scan.code,
            },
            diagnostics=scan.diagnostics,
        )

    actual_count = int(scan.data.get("broken_count", 0))
    if actual_count != expected_count:
        return error_response(
            "POST_BROKEN_REFS_FAILED",
            f"broken_refs postcondition failed: expected {expected_count}"
            f" broken refs but found {actual_count}."
            f" Run 'validate refs --scope {scope} --details' for full diagnostics.",
            data={
                "type": postcondition_type,
                "scope": scope,
                "expected_count": expected_count,
                "actual_count": actual_count,
                "scan_code": scan.code,
                "read_only": True,
                "executed": True,
            },
            diagnostics=scan.diagnostics,
        )
    return success_response(
        "POST_BROKEN_REFS_OK",
        "broken_refs postcondition passed.",
        data={
            "type": postcondition_type,
            "scope": scope,
            "expected_count": expected_count,
            "actual_count": actual_count,
            "scan_code": scan.code,
            "read_only": True,
            "executed": True,
        },
        diagnostics=scan.diagnostics,
    )
