"""Exact compile-report schema validation for Unity runtime responses."""

from __future__ import annotations

from typing import Any

from prefab_sentinel.contracts import Severity

from . import _protocol_schema as schema


def generated_asset_plan(value: object, path: str) -> None:
    plan = schema.exact_object(
        value,
        path=path,
        fields=("planned_created_paths", "planned_deleted_paths"),
    )
    schema.string_array(plan["planned_created_paths"], f"{path}.planned_created_paths")
    schema.string_array(plan["planned_deleted_paths"], f"{path}.planned_deleted_paths")


def generated_asset_report(value: object, path: str) -> None:
    report = schema.exact_object(
        value,
        path=path,
        fields=(
            "planned_created_paths",
            "planned_deleted_paths",
            "actual_created_paths",
            "actual_deleted_paths",
        ),
    )
    for field in (
        "planned_created_paths",
        "planned_deleted_paths",
        "actual_created_paths",
        "actual_deleted_paths",
    ):
        schema.string_array(report[field], f"{path}.{field}")


def compile_snapshot(value: object, path: str) -> None:
    snapshot = schema.exact_object(
        value,
        path=path,
        fields=(
            "inventory_stable",
            "prefab_repair_paths",
            "related_assets",
            "loaded_scenes",
            "generated_asset_plan",
            "project_dirty_paths",
        ),
    )
    schema.boolean(snapshot["inventory_stable"], f"{path}.inventory_stable")
    schema.string_array(
        snapshot["prefab_repair_paths"],
        f"{path}.prefab_repair_paths",
    )
    schema.identity_array(
        snapshot["related_assets"],
        f"{path}.related_assets",
        schema.asset_identity,
    )
    schema.identity_array(
        snapshot["loaded_scenes"],
        f"{path}.loaded_scenes",
        schema.scene_identity,
    )
    generated_asset_plan(
        snapshot["generated_asset_plan"],
        f"{path}.generated_asset_plan",
    )
    schema.string_array(
        snapshot["project_dirty_paths"],
        f"{path}.project_dirty_paths",
    )


def compile_delta(value: object, path: str) -> None:
    fields = (
        "newly_dirty_paths",
        "no_longer_dirty_paths",
        "newly_dirty_scene_paths",
        "no_longer_dirty_scene_paths",
        "planned_created_paths",
        "planned_deleted_paths",
        "actual_created_paths",
        "actual_deleted_paths",
        "unrelated_dirty_paths_before",
        "unrelated_dirty_paths_after",
        "attribution_unknown",
    )
    delta = schema.exact_object(value, path=path, fields=fields)
    for field in fields:
        schema.string_array(delta[field], f"{path}.{field}")


def compile_report(value: object, path: str) -> dict[str, Any]:
    report = schema.exact_object(
        value,
        path=path,
        fields=(
            "executed",
            "success",
            "severity",
            "code",
            "program_count",
            "before",
            "after",
            "delta",
            "generated_assets",
            "diagnostics",
        ),
    )
    schema.boolean(report["executed"], f"{path}.executed")
    schema.boolean(report["success"], f"{path}.success")
    severity = schema.string(report["severity"], f"{path}.severity")
    if severity not in {member.value for member in Severity}:
        raise schema.schema_error(f"{path}.severity", "a known severity")
    schema.string(report["code"], f"{path}.code")
    schema.integer(report["program_count"], f"{path}.program_count")
    compile_snapshot(report["before"], f"{path}.before")
    compile_snapshot(report["after"], f"{path}.after")
    compile_delta(report["delta"], f"{path}.delta")
    generated_asset_report(
        report["generated_assets"],
        f"{path}.generated_assets",
    )
    diagnostics = schema.runtime_diagnostics(
        report["diagnostics"],
        f"{path}.diagnostics",
    )
    if (
        report["executed"]
        and not report["success"]
        and report["code"] == "RUN_COMPILE_FAILED"
        and not diagnostics
    ):
        raise schema.schema_error(
            f"{path}.diagnostics",
            "non-empty compiler failure evidence",
        )
    return report
