"""Validation functions extracted from Phase1Orchestrator."""

from __future__ import annotations

from pathlib import Path

from prefab_sentinel.contracts import (
    Diagnostic,
    Severity,
    ToolResponse,
    max_severity,
)
from prefab_sentinel.orchestrator_variant import _read_target_file
from prefab_sentinel.services.prefab_variant import PrefabVariantService
from prefab_sentinel.services.reference_resolver import ReferenceResolverService
from prefab_sentinel.services.runtime_validation import RuntimeValidationService
from prefab_sentinel.structure_validator import validate_structure
from prefab_sentinel.unity_assets import GAMEOBJECT_BEARING_SUFFIXES


def inspect_structure(
    prefab_variant: PrefabVariantService,
    target_path: str,
) -> ToolResponse:
    text_or_error = _read_target_file(prefab_variant, target_path, "VALIDATE_STRUCTURE")
    if isinstance(text_or_error, ToolResponse):
        return text_or_error
    text = text_or_error

    result = validate_structure(text, target_path)
    diagnostics: list[Diagnostic] = (
        result.duplicate_file_ids
        + result.transform_inconsistencies
        + result.missing_components
        + result.orphaned_transforms
    )
    success = result.max_severity not in (Severity.ERROR, Severity.CRITICAL)

    suffix = Path(target_path).suffix.lower()
    all_checks = ["duplicate_file_id", "transform_consistency", "missing_components", "orphaned_transforms"]
    if suffix in GAMEOBJECT_BEARING_SUFFIXES:
        checks_performed = all_checks
        checks_skipped: list[str] = []
        skip_reason = ""
    else:
        checks_performed = ["duplicate_file_id"]
        checks_skipped = ["transform_consistency", "missing_components", "orphaned_transforms"]
        skip_reason = f"File type {suffix} has no GameObject/Transform structure"

    return ToolResponse(
        success=success,
        severity=result.max_severity,
        code="VALIDATE_STRUCTURE_RESULT",
        message="validate.structure completed (read-only).",
        data={
            "target_path": target_path,
            "read_only": True,
            "duplicate_file_id_count": len(result.duplicate_file_ids),
            "transform_inconsistency_count": len(result.transform_inconsistencies),
            "missing_component_count": len(result.missing_components),
            "orphaned_transform_count": len(result.orphaned_transforms),
            "checks_performed": checks_performed,
            "checks_skipped": checks_skipped,
            "skip_reason": skip_reason,
        },
        diagnostics=diagnostics,
    )


def validate_refs(
    reference_resolver: ReferenceResolverService,
    scope: str,
    details: bool = False,
    max_diagnostics: int = 200,
    exclude_patterns: tuple[str, ...] = (),
    ignore_asset_guids: tuple[str, ...] = (),
) -> ToolResponse:
    step = reference_resolver.scan_broken_references(
        scope=scope,
        include_diagnostics=details,
        max_diagnostics=max_diagnostics,
        exclude_patterns=exclude_patterns,
        ignore_asset_guids=ignore_asset_guids,
    )
    return ToolResponse(
        success=step.success,
        severity=step.severity,
        code="VALIDATE_REFS_RESULT",
        message="validate.refs pipeline completed (read-only).",
        data={
            "scope": scope,
            "read_only": True,
            "ignore_asset_guids": list(ignore_asset_guids),
            "steps": [
                {
                    "step": "scan_broken_references",
                    "result": {
                        "success": step.success,
                        "severity": step.severity.value,
                        "code": step.code,
                        "message": step.message,
                        "data": step.data,
                    },
                }
            ],
        },
        diagnostics=step.diagnostics,
    )


def validate_runtime(
    runtime_validation: RuntimeValidationService,
    scene_path: str,
    profile: str = "default",
    log_file: str | None = None,
    since_timestamp: str | None = None,
    allow_warnings: bool = False,
    max_diagnostics: int = 200,
) -> ToolResponse:
    compile_step = runtime_validation.compile_udonsharp()
    run_step = runtime_validation.run_clientsim(scene_path, profile)
    runtime_read_only = all(
        bool(step.data.get("read_only", True))
        for step in (compile_step, run_step)
    )

    steps = [
        ("compile_udonsharp", compile_step),
        ("run_clientsim", run_step),
    ]
    if run_step.severity in (Severity.ERROR, Severity.CRITICAL):
        severity = max_severity([compile_step.severity, run_step.severity])
        return ToolResponse(
            success=False,
            severity=severity,
            code="VALIDATE_RUNTIME_RESULT",
            message="validate.runtime stopped by fail-fast policy due to scene/runtime setup errors.",
            data={
                "scene_path": scene_path,
                "profile": profile,
                "read_only": runtime_read_only,
                "fail_fast_triggered": True,
                "steps": [
                    {"step": name, "result": step.to_dict()} for name, step in steps
                ],
            },
        )

    collect_step = runtime_validation.collect_unity_console(
        log_file=log_file,
        since_timestamp=since_timestamp,
    )
    classify_step = runtime_validation.classify_errors(
        log_lines=list(collect_step.data.get("log_lines", [])),
        max_diagnostics=max_diagnostics,
    )
    assert_step = runtime_validation.assert_no_critical_errors(
        classification_result=classify_step,
        allow_warnings=allow_warnings,
    )
    steps.extend(
        [
            ("collect_unity_console", collect_step),
            ("classify_errors", classify_step),
            ("assert_no_critical_errors", assert_step),
        ]
    )

    severities = [step.severity for _, step in steps]
    severity = max_severity(severities)
    success = all(step.success for _, step in steps)
    diagnostics = classify_step.diagnostics

    return ToolResponse(
        success=success,
        severity=severity,
        code="VALIDATE_RUNTIME_RESULT",
        message="validate.runtime pipeline completed.",
        data={
            "scene_path": scene_path,
            "profile": profile,
            "read_only": all(
                bool(step.data.get("read_only", True))
                for _, step in steps
            ),
            "fail_fast_triggered": False,
            "steps": [{"step": name, "result": step.to_dict()} for name, step in steps],
        },
        diagnostics=diagnostics,
    )
