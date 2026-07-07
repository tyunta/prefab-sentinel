"""Orchestration boundary for static material validation."""

from __future__ import annotations

from pathlib import Path
from typing import cast

from prefab_sentinel.contracts import Severity, ToolResponse, error_response
from prefab_sentinel.diagnostics_baseline import (
    DiagnosticKeyRecord,
    DiagnosticsBaseline,
    classify_current_keys,
)
from prefab_sentinel.inspection_context import ProjectInspectionContext
from prefab_sentinel.material_validation_rules import (
    MaterialValidationRulesLoadResult,
    load_material_validation_rules,
)
from prefab_sentinel.material_validator import validate_materials as validate_materials_core
from prefab_sentinel.services.reference_resolver import ReferenceResolverService
from prefab_sentinel.unity_assets_path import resolve_scope_path

__all__ = ["validate_materials"]


def validate_materials(
    reference_resolver: ReferenceResolverService,
    scope: str,
    *,
    include_details: bool = False,
    diagnostics_baseline: DiagnosticsBaseline | None = None,
    inspection_context: ProjectInspectionContext | None = None,
) -> ToolResponse:
    project_root = reference_resolver.project_root.resolve()
    scope_path = resolve_scope_path(scope, project_root)
    if (
        not scope_path.exists()
        or scope_path == project_root
        or not _is_inside_project(scope_path, project_root)
    ):
        return cast(ToolResponse, error_response(
            "MATERIAL_VALIDATION_SCOPE_NOT_FOUND",
            "Material validation scope was not found inside the project.",
            data={"scope": scope, "read_only": True},
        ))

    rules_result = load_material_validation_rules(project_root)
    if rules_result.status == "invalid":
        return cast(ToolResponse, error_response(
            "MATERIAL_RULES_INVALID",
            "Material validation rules config is invalid.",
            severity=Severity.ERROR,
            data={"rule_config": _rule_config_data(rules_result), "read_only": True},
            diagnostics=list(rules_result.diagnostics),
        ))

    rules = rules_result.rules
    if rules is None:
        return cast(ToolResponse, error_response(
            "MATERIAL_RULES_INVALID",
            "Material validation rules config did not produce a rule model.",
            severity=Severity.ERROR,
            data={"rule_config": _rule_config_data(rules_result), "read_only": True},
            diagnostics=list(rules_result.diagnostics),
        ))

    response = cast(ToolResponse, validate_materials_core(
        reference_resolver,
        scope_path,
        rules,
        include_details=include_details,
        inspection_context=inspection_context,
    ))
    if diagnostics_baseline is None:
        return response

    data = dict(response.data)
    data["diagnostics_baseline"] = classify_current_keys(
        _material_diagnostic_key_records(scope, response),
        diagnostics_baseline,
    ).to_dict()
    return ToolResponse(
        success=response.success,
        severity=response.severity,
        code=response.code,
        message=response.message,
        data=data,
        diagnostics=response.diagnostics,
    )


def _material_diagnostic_key_records(
    scope: str,
    response: ToolResponse,
) -> tuple[DiagnosticKeyRecord, ...]:
    records: list[DiagnosticKeyRecord] = []
    for diagnostic in response.diagnostics:
        path = diagnostic.path or scope
        location = diagnostic.location
        records.append(
            DiagnosticKeyRecord(
                key=f"validate_materials:{diagnostic.detail}:{path}:{location}",
                severity=diagnostic.severity or response.severity.value,
                message=diagnostic.evidence or diagnostic.detail,
                data={
                    "code": diagnostic.detail,
                    "scope": scope,
                    "path": path,
                    "location": location,
                },
            )
        )
    return tuple(records)


def _is_inside_project(scope_path: Path, project_root: Path) -> bool:
    try:
        scope_path.resolve().relative_to(project_root.resolve())
    except ValueError:
        return False
    return True


def _rule_config_data(rules_result: MaterialValidationRulesLoadResult) -> dict[str, object]:
    rules = rules_result.rules
    return {
        "status": rules_result.status,
        "path": str(rules_result.config_path),
        "shader_name_policies": 0 if rules is None else len(rules.shader_name_policies),
        "shared_material_groups": 0 if rules is None else len(rules.shared_material_groups),
        "folder_policies": 0 if rules is None else len(rules.folder_policies),
    }
