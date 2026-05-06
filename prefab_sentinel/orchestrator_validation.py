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
from prefab_sentinel.services.reference_resolver_snapshots import (
    SnapshotNameError,
    SnapshotPayloadError,
    diff_snapshots,
    load_snapshot,
    save_snapshot,
)
from prefab_sentinel.services.runtime_validation import RuntimeValidationService
from prefab_sentinel.structure_validator import validate_structure
from prefab_sentinel.unity_assets import GAMEOBJECT_BEARING_SUFFIXES
from prefab_sentinel.world_canvas_inspector import inspect_world_canvas_setup


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


def _handle_snapshot_modes(
    *,
    scope: str,
    project_root: Path,
    scan_data: dict,
    snapshot_save: str,
    snapshot_diff: str,
) -> ToolResponse | None:
    """Apply the snapshot-save / snapshot-diff modes to ``scan_data``.

    Mutates ``scan_data`` in place on success: ``snapshot_save`` adds
    ``scan_data['snapshot_saved_to']``; ``snapshot_diff`` adds
    ``scan_data['snapshot_diff']``.  Returns a ``ToolResponse`` error
    envelope to short-circuit the caller on invalid name, absent
    snapshot, or malformed snapshot file; returns ``None`` when no mode
    was requested or the requested mode succeeded.
    """
    if snapshot_save:
        try:
            saved_path = save_snapshot(snapshot_save, scan_data, project_root)
        except SnapshotNameError as exc:
            return _snapshot_error(
                "VALIDATE_REFS_SNAPSHOT_BAD_NAME",
                str(exc),
                scope=scope,
                snapshot_save=snapshot_save,
                snapshot_diff=snapshot_diff,
            )
        scan_data["snapshot_saved_to"] = str(saved_path)
        return None

    if snapshot_diff:
        try:
            prev = load_snapshot(snapshot_diff, project_root)
        except SnapshotNameError as exc:
            return _snapshot_error(
                "VALIDATE_REFS_SNAPSHOT_BAD_NAME",
                str(exc),
                scope=scope,
                snapshot_save=snapshot_save,
                snapshot_diff=snapshot_diff,
            )
        except SnapshotPayloadError as exc:
            return _snapshot_error(
                "VALIDATE_REFS_SNAPSHOT_BAD_NAME",
                f"snapshot file is malformed: {exc}",
                scope=scope,
                snapshot_save=snapshot_save,
                snapshot_diff=snapshot_diff,
            )
        if prev is None:
            return _snapshot_error(
                "VALIDATE_REFS_SNAPSHOT_NOT_FOUND",
                f"no snapshot named {snapshot_diff!r} for project {project_root}",
                scope=scope,
                snapshot_save=snapshot_save,
                snapshot_diff=snapshot_diff,
            )
        scan_data["snapshot_diff"] = diff_snapshots(prev, scan_data)
    return None


def _snapshot_error(
    code: str,
    message: str,
    *,
    scope: str,
    snapshot_save: str,
    snapshot_diff: str,
) -> ToolResponse:
    return ToolResponse(
        success=False,
        severity=Severity.ERROR,
        code=code,
        message=message,
        data={
            "scope": scope,
            "read_only": True,
            "snapshot_save": snapshot_save,
            "snapshot_diff": snapshot_diff,
        },
        diagnostics=[],
    )


def validate_refs(
    reference_resolver: ReferenceResolverService,
    scope: str,
    details: bool = False,
    max_diagnostics: int = 200,
    exclude_patterns: tuple[str, ...] = (),
    ignore_asset_guids: tuple[str, ...] = (),
    *,
    top_missing_breakdown: bool = False,
    snapshot_save: str = "",
    snapshot_diff: str = "",
) -> ToolResponse:
    if snapshot_save and snapshot_diff:
        return ToolResponse(
            success=False,
            severity=Severity.ERROR,
            code="VALIDATE_REFS_SNAPSHOT_ARG_CONFLICT",
            message=(
                "snapshot_save and snapshot_diff are mutually exclusive; "
                "supply at most one of the two."
            ),
            data={
                "scope": scope,
                "read_only": True,
                "snapshot_save": snapshot_save,
                "snapshot_diff": snapshot_diff,
            },
            diagnostics=[],
        )

    step = reference_resolver.scan_broken_references(
        scope=scope,
        include_diagnostics=details,
        max_diagnostics=max_diagnostics,
        exclude_patterns=exclude_patterns,
        ignore_asset_guids=ignore_asset_guids,
        top_missing_breakdown=top_missing_breakdown,
    )
    step_data = step.data if isinstance(step.data, dict) else {}

    snapshot_response = _handle_snapshot_modes(
        scope=scope,
        project_root=reference_resolver.project_root,
        scan_data=step_data,
        snapshot_save=snapshot_save,
        snapshot_diff=snapshot_diff,
    )
    if isinstance(snapshot_response, ToolResponse):
        return snapshot_response

    categories = step_data.get("categories", {}) or {}
    missing_asset_unique = int(categories.get("missing_asset", 0) or 0)
    if missing_asset_unique > 0:
        top_code = "REF001"
        top_success = False
        top_severity = Severity.ERROR
        top_message = (
            f"validate.refs aborted: {missing_asset_unique} missing GUID "
            "reference(s) detected (fail-fast per #83)."
        )
    else:
        top_code = "VALIDATE_REFS_RESULT"
        top_success = step.success
        top_severity = step.severity
        top_message = "validate.refs pipeline completed (read-only)."
    return ToolResponse(
        success=top_success,
        severity=top_severity,
        code=top_code,
        message=top_message,
        data={
            "scope": scope,
            "read_only": True,
            "ignore_asset_guids": list(ignore_asset_guids),
            "missing_asset_unique_count": missing_asset_unique,
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


def _inspect_world_canvas_step(scene_path: str) -> ToolResponse:
    """Issue #121: leading static WorldSpace-Canvas inspection step.

    Reads the scene YAML (best-effort) and runs the pure-function
    canvas linter.  The step's severity is intentionally capped at
    ``WARNING`` so a flagged Canvas does not abort the rest of the
    runtime-validation pipeline — the documented BoxCollider Z trap
    is a structural mistake worth surfacing but not a build blocker.
    A missing or unreadable scene file becomes a single ``info``
    diagnostic so the pipeline still proceeds to the runtime steps.
    """
    diagnostics: list[Diagnostic] = []
    severity = Severity.INFO
    try:
        text = Path(scene_path).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        diagnostics.append(
            Diagnostic(
                path=scene_path,
                location="",
                detail="WORLD_CANVAS_SCENE_UNREADABLE",
                evidence=(
                    f"Scene file could not be read for static WorldSpace-Canvas "
                    f"inspection: {exc}"
                ),
            )
        )
        return ToolResponse(
            success=True,
            severity=Severity.INFO,
            code="WORLD_CANVAS_INSPECT_OK",
            message="World canvas inspection skipped (scene file unreadable).",
            data={"scene_path": scene_path, "read_only": True},
            diagnostics=diagnostics,
        )

    diagnostics = inspect_world_canvas_setup(text, scene_path)
    if any(d.detail == "WORLD_CANVAS_LOCAL_SCALE" for d in diagnostics):
        severity = Severity.WARNING
    return ToolResponse(
        success=True,
        severity=severity,
        code="WORLD_CANVAS_INSPECT_OK",
        message="World canvas inspection completed (read-only).",
        data={
            "scene_path": scene_path,
            "read_only": True,
            "diagnostic_count": len(diagnostics),
        },
        diagnostics=diagnostics,
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
    # Issue #121: leading static WorldSpace-Canvas + VRC_UiShape check.
    # Severity is capped at warning so this never aborts the pipeline.
    canvas_step = _inspect_world_canvas_step(scene_path)
    compile_step = runtime_validation.compile_udonsharp()
    run_step = runtime_validation.run_clientsim(scene_path, profile)

    steps = [
        ("inspect_world_canvas", canvas_step),
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
                "read_only": all(
                    bool(step.data.get("read_only", True)) for _, step in steps
                ),
                "fail_fast_triggered": True,
                "steps": [
                    {"step": name, "result": step.to_dict()} for name, step in steps
                ],
            },
            # Issue #133: surface the canvas-step diagnostics at the top
            # level even on the fail-fast return so callers iterating
            # ``response.diagnostics`` observe canvas findings without
            # descending into nested steps. Mirrors the convention used
            # by ``inspect_structure`` and ``validate_refs``.
            diagnostics=list(canvas_step.diagnostics),
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
    # Issue #133: promote the canvas-step diagnostics to the top level so
    # callers iterating ``response.diagnostics`` observe canvas findings
    # alongside classification findings. Canvas precedes classification
    # because the canvas step runs first in the pipeline.
    diagnostics = list(canvas_step.diagnostics) + list(classify_step.diagnostics)

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
