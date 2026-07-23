"""Validation functions extracted from Phase1Orchestrator."""

from __future__ import annotations

from pathlib import Path

from prefab_sentinel.contracts import (
    Diagnostic,
    Severity,
    ToolResponse,
    max_severity,
)
from prefab_sentinel.diagnostics_baseline import (
    DiagnosticKeyRecord,
    DiagnosticsBaseline,
    classify_current_keys,
)
from prefab_sentinel.orchestrator_variant import read_target_file
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
from prefab_sentinel.unity_assets import (
    GAMEOBJECT_BEARING_SUFFIXES,
)
from prefab_sentinel.world_canvas_inspector import inspect_world_canvas_setup

# Issue #229: ``Diagnostic.detail`` for the stale-cache hint emitted on
# the validate-refs failure path when a fresh meta-file scan would
# resolve at least one of the missing GUIDs the cached resolver
# reported. Pinned as a module-level constant so callers can match
# diagnostics by detail without a fragile string literal.
STALE_GUID_INDEX_HINT_DETAIL = "STALE_GUID_INDEX_HINT"


def inspect_structure(
    prefab_variant: PrefabVariantService,
    target_path: str,
    diagnostics_baseline: DiagnosticsBaseline | None = None,
) -> ToolResponse:
    text_or_error = read_target_file(prefab_variant, target_path, "VALIDATE_STRUCTURE")
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

    data: dict[str, object] = {
        "target_path": target_path,
        "read_only": True,
        "duplicate_file_id_count": len(result.duplicate_file_ids),
        "transform_inconsistency_count": len(result.transform_inconsistencies),
        "missing_component_count": len(result.missing_components),
        "orphaned_transform_count": len(result.orphaned_transforms),
        "checks_performed": checks_performed,
        "checks_skipped": checks_skipped,
        "skip_reason": skip_reason,
    }
    if diagnostics_baseline is not None:
        data["diagnostics_baseline"] = classify_current_keys(
            _structure_diagnostic_key_records(
                target_path,
                diagnostics,
                result.max_severity,
            ),
            diagnostics_baseline,
        ).to_dict()

    return ToolResponse(
        success=success,
        severity=result.max_severity,
        code="VALIDATE_STRUCTURE_RESULT",
        message="validate.structure completed (read-only).",
        data=data,
        diagnostics=diagnostics,
    )


def _structure_diagnostic_key_records(
    target_path: str,
    diagnostics: list[Diagnostic],
    default_severity: Severity,
) -> tuple[DiagnosticKeyRecord, ...]:
    records: list[DiagnosticKeyRecord] = []
    for diagnostic in diagnostics:
        category = _structure_diagnostic_category(diagnostic)
        records.append(
            DiagnosticKeyRecord(
                key=(
                    f"validate_structure:{category}:{target_path}:"
                    f"{diagnostic.location}:{diagnostic.evidence}"
                ),
                severity=diagnostic.severity or default_severity.value,
                message=diagnostic.detail,
                data={
                    "category": category,
                    "target_path": target_path,
                    "path": diagnostic.path,
                    "location": diagnostic.location,
                    "evidence": diagnostic.evidence,
                },
            )
        )
    return tuple(records)


def _structure_diagnostic_category(diagnostic: Diagnostic) -> str:
    detail = diagnostic.detail
    if detail.startswith("Duplicate fileID:"):
        return "duplicate_file_id"
    if "references missing component:" in detail:
        return "missing_component"
    if detail.startswith("Orphaned transform:"):
        return "orphaned_transform"
    return "transform_consistency"


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
        except SnapshotPayloadError as exc:
            return _snapshot_error(
                "VALIDATE_REFS_SNAPSHOT_BAD_NAME",
                f"snapshot file is malformed: {exc}",
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
            # Issue #201: omit the host filesystem ``project_root`` from the
            # message and from the data envelope. Snapshot identifier alone
            # is sufficient context for the caller.
            return _snapshot_error(
                "VALIDATE_REFS_SNAPSHOT_NOT_FOUND",
                f"no snapshot named {snapshot_diff!r}",
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


def _stale_cache_hint_diagnostic(stale_resolved_count: int) -> Diagnostic:
    """Build the warning diagnostic emitted on the validate-refs failure
    path when a fresh meta-file scan would resolve at least one of the
    missing GUIDs the cached resolver reported (issue #229).
    """
    return Diagnostic(
        path="",
        location="",
        detail=STALE_GUID_INDEX_HINT_DETAIL,
        evidence=(
            f"{stale_resolved_count} missing GUID(s) would resolve on a "
            f"refreshed index; retry with refresh_guid_index=True to "
            f"force a fresh GUID lookup before scanning."
        ),
    )


def _detect_stale_cache_resolutions(
    reference_resolver: ReferenceResolverService,
    unique_missing_guids: list[str],
) -> int:
    """One-shot fresh meta-file scan whose intersection with the
    resolver's unique-missing-GUID list determines whether the
    stale-cache hint should fire (issue #229).

    Issue #230 — the fresh scan is delegated to the resolver's
    ``fresh_disk_guid_index`` accessor so repeated failure-path calls
    within the freshness window do not re-walk every meta file. The
    accessor's TTL is left at its default; callers that need a forced
    refresh pass ``refresh_guid_index=True`` to ``validate_refs``,
    which invalidates both the primary and the fresh-scan caches
    before the resolver scan runs.

    Returns the count of missing GUIDs that resolve on the fresh scan.
    Returns ``0`` when the input list is empty or when no missing GUID
    appears in the fresh index (true missing assets).
    """
    if not unique_missing_guids:
        return 0
    fresh_index = reference_resolver.fresh_disk_guid_index()
    return sum(1 for guid in unique_missing_guids if guid in fresh_index)


def _diagnostic_key_records_from_scan(
    scan_data: dict[str, object],
) -> tuple[DiagnosticKeyRecord, ...]:
    raw_records = scan_data["diagnostic_keys"]
    if not isinstance(raw_records, list):
        raise TypeError("scan_data['diagnostic_keys'] must be a list")
    records: list[DiagnosticKeyRecord] = []
    for raw_record in raw_records:
        if not isinstance(raw_record, dict):
            raise TypeError("diagnostic key records must be dictionaries")
        raw_data = raw_record["data"]
        if not isinstance(raw_data, dict):
            raise TypeError("diagnostic key record data must be a dictionary")
        records.append(
            DiagnosticKeyRecord(
                key=str(raw_record["key"]),
                severity=str(raw_record["severity"]),
                message=str(raw_record["message"]),
                data=raw_data,
            )
        )
    return tuple(records)


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
    refresh_guid_index: bool = False,
    diagnostics_baseline: DiagnosticsBaseline | None = None,
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

    # Issue #229: when the caller asserts the GUID index cache is stale
    # (e.g. immediately after creating a new asset), invalidate the
    # cache so the upcoming scan reads fresh meta files. The default
    # leaves the cache untouched so existing callers keep their fast
    # path.
    if refresh_guid_index:
        reference_resolver.invalidate_guid_index()

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
    diagnostics: list[Diagnostic] = list(step.diagnostics)
    if missing_asset_unique > 0:
        top_code = "REF001"
        top_success = False
        top_severity = Severity.ERROR
        top_message = (
            f"validate.refs aborted: {missing_asset_unique} missing GUID "
            "reference(s) detected (fail-fast per #83)."
        )
        # Issue #229: the stale-cache hint is in scope only when the
        # caller did not already force a refresh. A fresh scan that
        # resolves at least one of the cached-missing GUIDs proves the
        # cache lagged behind disk; the hint tells the caller to retry
        # with the flag set.
        if not refresh_guid_index:
            unique_missing_guids = list(
                step_data.get("unique_missing_asset_guids", []) or []
            )
            stale_count = _detect_stale_cache_resolutions(
                reference_resolver, unique_missing_guids,
            )
            if stale_count > 0:
                diagnostics.append(
                    _stale_cache_hint_diagnostic(stale_count),
                )
    elif not step.success:
        top_code = step.code
        top_success = False
        top_severity = step.severity
        top_message = step.message
    else:
        top_code = "VALIDATE_REFS_RESULT"
        top_success = step.success
        top_severity = step.severity
        top_message = "validate.refs pipeline completed (read-only)."
    response_data = {
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
    }
    if not step.success:
        for field in ("error", "reason"):
            if field in step_data:
                response_data[field] = step_data[field]
    if diagnostics_baseline is not None and "diagnostic_keys" in step_data:
        response_data["diagnostics_baseline"] = classify_current_keys(
            _diagnostic_key_records_from_scan(step_data),
            diagnostics_baseline,
        ).to_dict()

    return ToolResponse(
        success=top_success,
        severity=top_severity,
        code=top_code,
        message=top_message,
        data=response_data,
        diagnostics=diagnostics,
    )


def _inspect_world_canvas_step(scene_path: str, runtime_root: Path | None = None) -> ToolResponse:
    """Issue #121: leading static WorldSpace-Canvas inspection step."""
    from prefab_sentinel.unity_assets_path import resolve_scope_path

    diagnostics: list[Diagnostic] = []
    severity = Severity.INFO
    resolved_scene = Path(scene_path)
    if runtime_root is not None:
        resolved_root = Path(runtime_root).resolve()
        try:
            resolved_scene = resolve_scope_path(scene_path, runtime_root).resolve()
        except (OSError, RuntimeError, ValueError) as exc:
            diagnostics.append(
                Diagnostic(
                    path=scene_path,
                    location="",
                    detail="WORLD_CANVAS_SCENE_INVALID",
                    evidence=f"Scene path could not be resolved for static WorldSpace-Canvas inspection: {exc}",
                )
            )
            return ToolResponse(
                success=True,
                severity=Severity.INFO,
                code="WORLD_CANVAS_INSPECT_OK",
                message="World canvas inspection skipped (scene path invalid).",
                data={"scene_path": scene_path, "read_only": True},
                diagnostics=diagnostics,
            )
        if not resolved_scene.is_relative_to(resolved_root):
            diagnostics.append(
                Diagnostic(
                    path=scene_path,
                    location="",
                    detail="WORLD_CANVAS_SCENE_OUTSIDE_ROOT",
                    evidence="Scene path resolves outside the runtime project root.",
                )
            )
            return ToolResponse(
                success=True,
                severity=Severity.INFO,
                code="WORLD_CANVAS_INSPECT_OK",
                message="World canvas inspection skipped (scene path outside runtime root).",
                data={"scene_path": scene_path, "read_only": True},
                diagnostics=diagnostics,
            )
        if resolved_scene.suffix.lower() != ".unity":
            diagnostics.append(
                Diagnostic(
                    path=scene_path,
                    location="",
                    detail="WORLD_CANVAS_SCENE_NOT_UNITY",
                    evidence="World canvas inspection only reads .unity scene files.",
                )
            )
            return ToolResponse(
                success=True,
                severity=Severity.INFO,
                code="WORLD_CANVAS_INSPECT_OK",
                message="World canvas inspection skipped (not a .unity scene).",
                data={"scene_path": scene_path, "read_only": True},
                diagnostics=diagnostics,
            )

    try:
        text = resolved_scene.read_text(encoding="utf-8")
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
    profile: str = "compile_only",
    log_file: str | None = None,
    since_timestamp: str | None = None,
    allow_warnings: bool = False,
    max_diagnostics: int = 200,
    confirm: bool = False,
    change_reason: str | None = None,
    allow_dirty_before_clientsim: bool = False,
) -> ToolResponse:
    from prefab_sentinel.services.runtime_validation.config import default_runtime_root

    supported_profiles = ("compile_only", "editor_console_only", "clientsim")
    if profile not in supported_profiles:
        return ToolResponse(
            success=False,
            severity=Severity.ERROR,
            code="VALIDATE_RUNTIME_PROFILE_UNSUPPORTED",
            message=(
                "Unsupported runtime validation profile. Supported profiles: "
                + ", ".join(supported_profiles)
                + "."
            ),
            data={"scene_path": scene_path, "profile": profile, "read_only": True},
        )

    if profile == "clientsim" and (not confirm or change_reason is None or not change_reason.strip()):
        return ToolResponse(
            success=False,
            severity=Severity.ERROR,
            code="CLIENTSIM_CONFIRM_REQUIRED",
            message="ClientSim validation requires explicit audit confirmation and a non-empty change reason.",
            data={
                "scene_path": scene_path,
                "profile": profile,
                "read_only": True,
                "executed": False,
            },
        )

    runtime_root = default_runtime_root(runtime_validation.project_root)
    canvas_step = _inspect_world_canvas_step(scene_path, runtime_root)
    steps: list[tuple[str, ToolResponse]] = [("inspect_world_canvas", canvas_step)]

    if profile in ("compile_only", "clientsim"):
        compile_step = runtime_validation.compile_udonsharp()
        steps.append(("compile_udonsharp", compile_step))

    if profile == "clientsim":
        run_step = runtime_validation.run_clientsim(
            scene_path,
            profile,
            confirm=confirm,
            change_reason=change_reason,
            allow_dirty_before=allow_dirty_before_clientsim,
        )
        steps.append(("run_clientsim", run_step))
        if run_step.severity in (Severity.ERROR, Severity.CRITICAL):
            severity = max_severity([step.severity for _, step in steps])
            return ToolResponse(
                success=False,
                severity=severity,
                code="VALIDATE_RUNTIME_RESULT",
                message="validate.runtime stopped by fail-fast policy due to scene/runtime setup errors.",
                data={
                    "scene_path": scene_path,
                    "profile": profile,
                    "read_only": all(bool(step.data.get("read_only", True)) for _, step in steps),
                    "fail_fast_triggered": True,
                    "steps": [{"step": name, "result": step.to_dict()} for name, step in steps],
                },
                diagnostics=list(canvas_step.diagnostics),
            )

    if profile == "editor_console_only":
        collect_step = runtime_validation.collect_editor_console(
            since_timestamp=since_timestamp,
            max_lines=max_diagnostics,
        )
        collect_step_name = "collect_editor_console"
    else:
        collect_step = runtime_validation.collect_unity_console(
            log_file=log_file,
            since_timestamp=since_timestamp,
        )
        collect_step_name = "collect_unity_console"
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
            (collect_step_name, collect_step),
            ("classify_errors", classify_step),
            ("assert_no_critical_errors", assert_step),
        ]
    )

    severity = max_severity([step.severity for _, step in steps])
    success = all(step.success for _, step in steps)
    diagnostics = list(canvas_step.diagnostics) + list(classify_step.diagnostics)

    return ToolResponse(
        success=success,
        severity=severity,
        code="VALIDATE_RUNTIME_RESULT",
        message="validate.runtime pipeline completed.",
        data={
            "scene_path": scene_path,
            "profile": profile,
            "read_only": all(bool(step.data.get("read_only", True)) for _, step in steps),
            "fail_fast_triggered": False,
            "steps": [{"step": name, "result": step.to_dict()} for name, step in steps],
        },
        diagnostics=diagnostics,
    )
