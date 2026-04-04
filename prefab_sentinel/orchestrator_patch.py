"""Patch application function extracted from Phase1Orchestrator."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from prefab_sentinel.contracts import (
    Severity,
    ToolResponse,
    error_response,
    max_severity,
)
from prefab_sentinel.orchestrator_postcondition import (
    _evaluate_postcondition,
    _validate_postcondition_schema,
)
from prefab_sentinel.patch_plan import count_plan_ops, iter_resource_batches, normalize_patch_plan

if TYPE_CHECKING:
    from prefab_sentinel.orchestrator import Phase1Orchestrator


def patch_apply(
    orch: Phase1Orchestrator,
    plan: dict[str, object],
    dry_run: bool = False,
    confirm: bool = False,
    plan_sha256: str | None = None,
    plan_signature: str | None = None,
    change_reason: str | None = None,
    scope: str | None = None,
    runtime_scene: str | None = None,
    runtime_profile: str = "default",
    runtime_log_file: str | None = None,
    runtime_since_timestamp: str | None = None,
    runtime_allow_warnings: bool = False,
    runtime_max_diagnostics: int = 200,
) -> ToolResponse:
    normalized_plan = normalize_patch_plan(plan)
    resource_batches = iter_resource_batches(normalized_plan)
    resource_map = {
        str(resource.get("id", "")): resource for resource, _ in resource_batches
    }
    postconditions = list(normalized_plan.get("postconditions", []))
    resource_count = len(resource_batches)
    targets = [str(resource.get("path", "")) for resource, _ in resource_batches]
    primary_target = targets[0] if resource_count == 1 else None
    total_op_count = count_plan_ops(normalized_plan)

    steps: list[tuple[str, ToolResponse]] = []
    execution_id = uuid.uuid4().hex
    executed_at_utc = datetime.now(UTC).isoformat()
    normalized_reason = change_reason.strip() if change_reason else None

    def _step_name(base: str, resource_id: str) -> str:
        return base if resource_count == 1 else f"{base}:{resource_id}"

    def _finalize(message: str, fail_fast: bool) -> ToolResponse:
        severities = [step.severity for _, step in steps]
        severity = max_severity(severities)
        success = all(step.success for _, step in steps)
        diagnostics = [
            diagnostic
            for _, step in steps
            for diagnostic in step.diagnostics
        ]
        write_executed = any(
            step_name == "apply_and_save" or step_name.startswith("apply_and_save:")
            for step_name, _ in steps
        )
        return ToolResponse(
            success=success,
            severity=severity,
            code="PATCH_APPLY_RESULT",
            message=message,
            data={
                "plan_version": normalized_plan.get("plan_version"),
                "target": primary_target,
                "targets": targets,
                "resource_count": resource_count,
                "resources": [
                    {
                        "id": resource.get("id"),
                        "kind": resource.get("kind"),
                        "path": resource.get("path"),
                        "mode": resource.get("mode"),
                    }
                    for resource, _ in resource_batches
                ],
                "op_count": total_op_count,
                "plan_sha256": plan_sha256,
                "plan_signature": plan_signature,
                "change_reason": normalized_reason,
                "execution_id": execution_id,
                "executed_at_utc": executed_at_utc,
                "dry_run": dry_run,
                "confirm": confirm,
                "scope": scope,
                "runtime_scene": runtime_scene,
                "runtime_profile": runtime_profile,
                "runtime_log_file": runtime_log_file,
                "runtime_since_timestamp": runtime_since_timestamp,
                "runtime_allow_warnings": runtime_allow_warnings,
                "runtime_max_diagnostics": runtime_max_diagnostics,
                "postcondition_count": len(postconditions),
                "read_only": not write_executed,
                "fail_fast_triggered": fail_fast,
                "steps": [
                    {"step": step_name, "result": step.to_dict()}
                    for step_name, step in steps
                ],
            },
            diagnostics=diagnostics,
        )

    resource_ids = set(resource_map)
    for index, postcondition in enumerate(postconditions):
        schema_step = _validate_postcondition_schema(
            postcondition,
            resource_ids=resource_ids,
        )
        if not schema_step.success:
            step_type = (
                postcondition.get("type", "").strip()
                if isinstance(postcondition, dict)
                else ""
            )
            step_label = step_type or "invalid"
            steps.append((f"postcondition_schema:{step_label}[{index}]", schema_step))
            return _finalize(
                "patch.apply stopped by fail-fast policy due to invalid postcondition schema.",
                fail_fast=True,
            )

    for resource, ops in resource_batches:
        target = str(resource.get("path", ""))
        dry_step = orch.serialized_object.dry_run_resource_plan(resource=resource, ops=ops)
        steps.append((_step_name("dry_run_patch", str(resource.get("id", ""))), dry_step))
        if dry_step.severity in (Severity.ERROR, Severity.CRITICAL):
            return _finalize(
                "patch.apply stopped by fail-fast policy due to invalid patch plan.",
                fail_fast=True,
            )

    if dry_run:
        return _finalize("patch.apply dry-run completed.", fail_fast=False)

    if not confirm:
        confirm_step = error_response(
            "SER_CONFIRM_REQUIRED",
            "patch.apply requires --confirm when not using --dry-run.",
            severity=Severity.WARNING,
            data={
                "target": primary_target,
                "targets": targets,
                "resource_count": resource_count,
                "op_count": total_op_count,
                "read_only": True,
            },
        )
        steps.append(("confirm_gate", confirm_step))
        return _finalize("patch.apply blocked by confirm gate.", fail_fast=False)

    if scope:
        preflight_refs = orch.reference_resolver.scan_broken_references(
            scope=scope,
            include_diagnostics=False,
            max_diagnostics=runtime_max_diagnostics,
        )
        steps.append(("scan_broken_references_preflight", preflight_refs))
        if preflight_refs.severity in (Severity.ERROR, Severity.CRITICAL):
            return _finalize(
                "patch.apply stopped by fail-fast policy due to preflight reference errors.",
                fail_fast=True,
            )

    for resource, ops in resource_batches:
        resource_id = str(resource.get("id", ""))
        target = str(resource.get("path", ""))
        target_suffix = Path(target).suffix.lower()
        resource_mode = str(resource.get("mode", "open")).strip().lower() or "open"

        if target_suffix == ".prefab" and resource_mode == "open":
            overrides_step = orch.prefab_variant.list_overrides(target)
            steps.append(
                (_step_name("list_overrides_preflight", resource_id), overrides_step)
            )
            if overrides_step.severity in (Severity.ERROR, Severity.CRITICAL):
                return _finalize(
                    "patch.apply stopped by fail-fast policy due to preflight override inspection errors.",
                    fail_fast=True,
                )

        apply_step = orch.serialized_object.apply_resource_plan(resource=resource, ops=ops)
        steps.append((_step_name("apply_and_save", resource_id), apply_step))
        if apply_step.severity in (Severity.ERROR, Severity.CRITICAL):
            return _finalize("patch.apply completed with errors.", fail_fast=False)

    if runtime_scene:
        compile_step = orch.runtime_validation.compile_udonsharp()
        run_step = orch.runtime_validation.run_clientsim(runtime_scene, runtime_profile)
        steps.extend(
            [
                ("compile_udonsharp", compile_step),
                ("run_clientsim", run_step),
            ]
        )
        if run_step.severity in (Severity.ERROR, Severity.CRITICAL):
            return _finalize(
                "patch.apply stopped by fail-fast policy due to runtime scene validation errors.",
                fail_fast=True,
            )

        collect_step = orch.runtime_validation.collect_unity_console(
            log_file=runtime_log_file,
            since_timestamp=runtime_since_timestamp,
        )
        classify_step = orch.runtime_validation.classify_errors(
            log_lines=list(collect_step.data.get("log_lines", [])),
            max_diagnostics=runtime_max_diagnostics,
        )
        assert_step = orch.runtime_validation.assert_no_critical_errors(
            classification_result=classify_step,
            allow_warnings=runtime_allow_warnings,
        )
        steps.extend(
            [
                ("collect_unity_console", collect_step),
                ("classify_errors", classify_step),
                ("assert_no_critical_errors", assert_step),
            ]
        )
        if classify_step.severity in (Severity.ERROR, Severity.CRITICAL):
            return _finalize(
                "patch.apply stopped by fail-fast policy due to runtime error classification.",
                fail_fast=True,
            )
        if assert_step.severity in (Severity.ERROR, Severity.CRITICAL):
            return _finalize(
                "patch.apply stopped by fail-fast policy due to runtime assertion failure.",
                fail_fast=True,
            )

    for index, postcondition in enumerate(postconditions):
        evaluated = _evaluate_postcondition(
            orch.serialized_object,
            orch.reference_resolver,
            postcondition,
            resource_map=resource_map,
        )
        post_type = str(postcondition.get("type", "")).strip() or "unknown"
        steps.append((f"postcondition:{post_type}[{index}]", evaluated))
        if evaluated.severity in (Severity.ERROR, Severity.CRITICAL):
            return _finalize(
                "patch.apply stopped by fail-fast policy due to postcondition failure.",
                fail_fast=True,
            )

    success = all(step.success for _, step in steps)
    if success:
        return _finalize("patch.apply completed.", fail_fast=False)
    return _finalize("patch.apply completed with warnings.", fail_fast=False)
