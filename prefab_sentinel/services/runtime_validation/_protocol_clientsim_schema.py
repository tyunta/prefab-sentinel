"""Exact ClientSim-report schema validation for Unity runtime responses."""

from __future__ import annotations

from typing import Any

from . import _protocol_schema as schema


def clientsim_snapshot(value: object, path: str) -> None:
    snapshot = schema.exact_object(
        value,
        path=path,
        fields=(
            "Roots",
            "Hierarchy",
            "Components",
            "AssetChangeCandidates",
            "Dirty",
            "DirtyCount",
        ),
    )
    for field in ("Roots", "Hierarchy", "Components", "AssetChangeCandidates"):
        schema.string_array(snapshot[field], f"{path}.{field}")
    schema.boolean(snapshot["Dirty"], f"{path}.Dirty")
    schema.integer(snapshot["DirtyCount"], f"{path}.DirtyCount")


def side_effect_report(value: object, path: str) -> dict[str, Any]:
    string_array_fields = (
        "diff_warnings",
        "roots_before",
        "roots_runtime",
        "roots_after",
        "hierarchy_before",
        "hierarchy_runtime",
        "hierarchy_after",
        "components_before",
        "components_runtime",
        "components_after",
        "added_gameobjects",
        "removed_gameobjects",
        "added_components",
        "removed_components",
        "residual_added_gameobjects",
        "residual_removed_gameobjects",
        "residual_added_components",
        "residual_removed_components",
        "asset_change_candidates",
    )
    boolean_fields = (
        "diff_complete",
        "dirty_before",
        "dirty_runtime",
        "dirty_after",
    )
    integer_fields = (
        "dirty_count_before",
        "dirty_count_runtime",
        "dirty_count_after",
    )
    report = schema.exact_object(
        value,
        path=path,
        fields=(
            *boolean_fields,
            *string_array_fields,
            "scene_path",
            *integer_fields,
        ),
    )
    for field in boolean_fields:
        schema.boolean(report[field], f"{path}.{field}")
    for field in string_array_fields:
        schema.string_array(report[field], f"{path}.{field}")
    schema.string(report["scene_path"], f"{path}.scene_path")
    for field in integer_fields:
        schema.integer(report[field], f"{path}.{field}")
    return report


def is_unity_default_clientsim_snapshot(snapshot: dict[str, Any]) -> bool:
    return (
        snapshot["Roots"] == []
        and snapshot["Hierarchy"] == []
        and snapshot["Components"] == []
        and snapshot["AssetChangeCandidates"] == []
        and snapshot["Dirty"] is False
        and snapshot["DirtyCount"] == 0
    )


def is_unity_default_side_effect_report(report: dict[str, Any]) -> bool:
    string_array_fields = (
        "diff_warnings",
        "roots_before",
        "roots_runtime",
        "roots_after",
        "hierarchy_before",
        "hierarchy_runtime",
        "hierarchy_after",
        "components_before",
        "components_runtime",
        "components_after",
        "added_gameobjects",
        "removed_gameobjects",
        "added_components",
        "removed_components",
        "residual_added_gameobjects",
        "residual_removed_gameobjects",
        "residual_added_components",
        "residual_removed_components",
        "asset_change_candidates",
    )
    return (
        report["diff_complete"] is True
        and report["scene_path"] == ""
        and all(report[field] == [] for field in string_array_fields)
        and report["dirty_before"] is False
        and report["dirty_runtime"] is False
        and report["dirty_after"] is False
        and report["dirty_count_before"] == 0
        and report["dirty_count_runtime"] == 0
        and report["dirty_count_after"] == 0
    )


def clientsim_report(value: object, path: str) -> dict[str, Any]:
    report = schema.exact_object(
        value,
        path=path,
        fields=(
            "executed",
            "initial_scene_snapshot",
            "before",
            "runtime",
            "after",
            "side_effect_report",
        ),
    )
    executed = schema.boolean(report["executed"], f"{path}.executed")
    schema.identity_array(
        report["initial_scene_snapshot"],
        f"{path}.initial_scene_snapshot",
        schema.scene_identity,
    )
    for field in ("before", "runtime", "after"):
        snapshot = report[field]
        if snapshot is not None:
            clientsim_snapshot(snapshot, f"{path}.{field}")
            if not executed and is_unity_default_clientsim_snapshot(snapshot):
                report[field] = None
    side_effect = report["side_effect_report"]
    if side_effect is None:
        if executed:
            raise schema.schema_error(f"{path}.side_effect_report", "an object")
    else:
        side_effect = side_effect_report(
            side_effect,
            f"{path}.side_effect_report",
        )
        if not executed and is_unity_default_side_effect_report(side_effect):
            report["side_effect_report"] = None
    return report
