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
    evaluate_postcondition,
    validate_postcondition_schema,
)
from prefab_sentinel.patch_plan import (
    count_plan_ops,
    is_single_open_prefab_plan,
    iter_resource_batches,
    normalize_patch_plan,
)
from prefab_sentinel.patch_transaction import execute_single_open_prefab_transaction
from prefab_sentinel.patch_transaction_results import (
    project_patch_data,
    project_patch_response,
)
from prefab_sentinel.unity_assets_path import (
    ProjectPathEscapeError,
    resolve_asset_path,
)

if TYPE_CHECKING:
    from prefab_sentinel.orchestrator import Phase1Orchestrator


def _reject_outside_resource_paths(
    resources: list[dict[str, object]],
    project_root: Path,
) -> None:
    for index, resource in enumerate(resources):
        try:
            resolve_asset_path(str(resource["path"]), project_root)
        except (OSError, ProjectPathEscapeError):
            raise ValueError(
                f"resources[{index}].path must resolve within the active project."
            ) from None
        except ValueError:
            # Transaction preflight owns malformed project-local paths so its
            # terminal audit report remains authoritative.
            continue


def serialized_value_patch_apply(
    orch: Phase1Orchestrator,
    plan: dict[str, object],
    dry_run: bool,
    confirm: bool,
    change_reason: str | None,
) -> ToolResponse:
    """Run offline value writers without widening the open-Prefab plan grammar."""

    normalized_plan = normalize_patch_plan(plan)
    resources = normalized_plan["resources"]
    _reject_outside_resource_paths(resources, orch.serialized_object.project_root)
    resource_batches = iter_resource_batches(normalized_plan)
    if len(resource_batches) != 1 or normalized_plan.get("postconditions"):
        return error_response(
            "SER_PLAN_INVALID",
            "Serialized value writes require exactly one resource and no postconditions.",
            severity=Severity.ERROR,
            data={"read_only": True},
        )

    resource, ops = resource_batches[0]
    target = str(resource["path"])
    steps: list[tuple[str, ToolResponse]] = []
    execution_id = uuid.uuid4().hex
    executed_at_utc = datetime.now(UTC).isoformat()
    normalized_reason = change_reason.strip() if change_reason else None

    def _finalize(message: str, fail_fast: bool) -> ToolResponse:
        apply_step = next(
            (step for step_name, step in steps if step_name == "apply_and_save"),
            None,
        )
        diagnostics = [
            diagnostic
            for _, step in steps
            for diagnostic in step.diagnostics
        ]
        return ToolResponse(
            success=all(step.success for _, step in steps),
            severity=max_severity([step.severity for _, step in steps]),
            code="PATCH_APPLY_RESULT",
            message=message,
            data=project_patch_data(
                orch.serialized_object.project_root,
                {
                "plan_version": normalized_plan.get("plan_version"),
                "target": target,
                "targets": [target],
                "resource_count": 1,
                "resources": [
                    {
                        "id": resource.get("id"),
                        "kind": resource.get("kind"),
                        "path": resource.get("path"),
                        "mode": resource.get("mode"),
                        "executed": any(
                            step_name == "dry_run_patch"
                            for step_name, _ in steps
                        ),
                        "applied": (
                            0
                            if apply_step is None
                            else apply_step.data["applied"]
                        ),
                    }
                ],
                "op_count": count_plan_ops(normalized_plan),
                "plan_sha256": None,
                "plan_signature": None,
                "change_reason": normalized_reason,
                "execution_id": execution_id,
                "executed_at_utc": executed_at_utc,
                "dry_run": dry_run,
                "confirm": confirm,
                "scope": None,
                "runtime_scene": None,
                "runtime_profile": "default",
                "runtime_log_file": None,
                "runtime_since_timestamp": None,
                "runtime_allow_warnings": False,
                "runtime_max_diagnostics": 200,
                "postcondition_count": 0,
                "read_only": apply_step is None,
                "fail_fast_triggered": fail_fast,
                "steps": [
                    {
                        "step": step_name,
                        "result": project_patch_response(
                            orch.serialized_object.project_root,
                            step,
                        ),
                    }
                    for step_name, step in steps
                ],
                },
            ),
            diagnostics=diagnostics,
        )

    dry_step = orch.serialized_object.dry_run_patch(target=target, ops=ops)
    steps.append(("dry_run_patch", dry_step))
    if dry_step.severity in (Severity.ERROR, Severity.CRITICAL):
        return _finalize(
            "patch.apply stopped by fail-fast policy due to invalid patch plan.",
            fail_fast=True,
        )
    if dry_run:
        return _finalize("patch.apply dry-run completed.", fail_fast=False)
    if not confirm:
        steps.append(
            (
                "confirm_gate",
                error_response(
                    "SER_CONFIRM_REQUIRED",
                    "patch.apply requires --confirm when not using --dry-run.",
                    severity=Severity.WARNING,
                    data={
                        "target": target,
                        "targets": [target],
                        "resource_count": 1,
                        "op_count": count_plan_ops(normalized_plan),
                        "read_only": True,
                    },
                ),
            )
        )
        return _finalize("patch.apply blocked by confirm gate.", fail_fast=False)

    if Path(target).suffix.lower() == ".prefab":
        overrides_step = orch.prefab_variant.list_overrides(target)
        steps.append(("list_overrides_preflight", overrides_step))
        if overrides_step.severity in (Severity.ERROR, Severity.CRITICAL):
            return _finalize(
                "patch.apply stopped by fail-fast policy due to preflight override inspection errors.",
                fail_fast=True,
            )

    apply_step = orch.serialized_object.apply_and_save(target=target, ops=ops)
    steps.append(("apply_and_save", apply_step))
    if not apply_step.success or apply_step.severity in (
        Severity.ERROR,
        Severity.CRITICAL,
    ):
        return _finalize(
            "patch.apply stopped by fail-fast policy due to apply failure.",
            fail_fast=True,
        )
    return _finalize("patch.apply completed.", fail_fast=False)


def patch_apply(
    orch: Phase1Orchestrator,
    plan: dict[str, object],
    dry_run: bool = False,
    confirm: bool = False,
    plan_sha256: str | None = None,
    plan_signature: str | None = None,
    change_reason: str | None = None,
    out_report: str | None = None,
    scope: str | None = None,
    runtime_scene: str | None = None,
    runtime_profile: str = "default",
    runtime_log_file: str | None = None,
    runtime_since_timestamp: str | None = None,
    runtime_allow_warnings: bool = False,
    runtime_max_diagnostics: int = 200,
    transactional: bool = False,
    _transaction_bypass: bool = False,
) -> ToolResponse:
    normalized_plan = normalize_patch_plan(plan)
    _reject_outside_resource_paths(
        normalized_plan["resources"],
        orch.serialized_object.project_root,
    )
    resource_batches = iter_resource_batches(normalized_plan)
    executable_batches = [batch for batch in resource_batches if batch[1]]
    resource_map = {str(resource.get("id", "")): resource for resource, _ in resource_batches}
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

    def _finalize(
        message: str,
        fail_fast: bool,
        code: str = "PATCH_APPLY_RESULT",
        severity_override: Severity | None = None,
        success_override: bool | None = None,
    ) -> ToolResponse:
        def _resource_summary(
            resource: dict[str, object],
        ) -> dict[str, object]:
            resource_id = str(resource.get("id", ""))
            dry_step_name = _step_name("dry_run_patch", resource_id)
            apply_step_name = _step_name("apply_and_save", resource_id)
            apply_step = next(
                (
                    step
                    for step_name, step in steps
                    if step_name == apply_step_name
                ),
                None,
            )
            return {
                "id": resource.get("id"),
                "kind": resource.get("kind"),
                "path": resource.get("path"),
                "mode": resource.get("mode"),
                "executed": any(
                    step_name == dry_step_name for step_name, _ in steps
                ),
                "applied": (
                    0 if apply_step is None else apply_step.data["applied"]
                ),
            }

        severities = [step.severity for _, step in steps]
        severity = (
            severity_override
            if severity_override is not None
            else max_severity(severities)
        )
        success = (
            success_override
            if success_override is not None
            else all(step.success for _, step in steps)
        )
        diagnostics = [
            diagnostic
            for _, step in steps
            for diagnostic in step.diagnostics
        ]
        write_executed = any(
            step_name == "apply_and_save"
            or step_name.startswith("apply_and_save:")
            for step_name, _ in steps
        )
        return ToolResponse(
            success=success,
            severity=severity,
            code=code,
            message=message,
            data=project_patch_data(
                orch.serialized_object.project_root,
                {
                "plan_version": normalized_plan.get("plan_version"),
                "target": primary_target,
                "targets": targets,
                "resource_count": resource_count,
                "resources": [
                    _resource_summary(resource)
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
                    {
                        "step": step_name,
                        "result": (
                            step.to_dict()
                            if _transaction_bypass
                            else project_patch_response(
                                orch.serialized_object.project_root,
                                step,
                            )
                        ),
                    }
                    for step_name, step in steps
                ],
                },
                recurse_steps=not _transaction_bypass,
            ),
            diagnostics=diagnostics,
        )

    resource_ids = set(resource_map)
    for index, postcondition in enumerate(postconditions):
        schema_step = validate_postcondition_schema(
            postcondition,
            resource_ids=resource_ids,
        )
        if not schema_step.success:
            step_type = postcondition.get("type", "").strip() if isinstance(postcondition, dict) else ""
            step_label = step_type or "invalid"
            steps.append((f"postcondition_schema:{step_label}[{index}]", schema_step))
            return _finalize(
                "patch.apply stopped by fail-fast policy due to invalid postcondition schema.",
                fail_fast=True,
            )

    if not executable_batches:
        steps.append(
            (
                "plan_schema",
                error_response(
                    "SER_PLAN_INVALID",
                    "ops must contain at least one operation.",
                    severity=Severity.ERROR,
                    data={"field": "ops", "read_only": True, "executed": False},
                ),
            )
        )
        return _finalize(
            "patch.apply stopped by fail-fast policy due to invalid patch plan.",
            fail_fast=True,
            code="INVALID_PLAN_SCHEMA",
        )

    for resource, ops in executable_batches:
        dry_step = orch.serialized_object.dry_run_resource_plan(resource=resource, ops=ops)
        steps.append((_step_name("dry_run_patch", str(resource.get("id", ""))), dry_step))
        if dry_step.severity in (Severity.ERROR, Severity.CRITICAL):
            public_code = (
                "INVALID_PLAN_SCHEMA"
                if resource["kind"] == "prefab" and resource["mode"] == "open"
                else "PATCH_APPLY_RESULT"
            )
            return _finalize(
                "patch.apply stopped by fail-fast policy due to invalid patch plan.",
                fail_fast=True,
                code=public_code,
            )

    if (
        not _transaction_bypass
        and transactional
        and confirm
        and not dry_run
        and is_single_open_prefab_plan(normalized_plan)
    ):
        transaction_reason = change_reason.strip() if change_reason else ""
        if not transaction_reason:
            return error_response(
                "CHANGE_REASON_REQUIRED",
                "change_reason is required when confirm=True.",
                severity=Severity.ERROR,
                data={"field": "change_reason"},
            )
        return execute_single_open_prefab_transaction(
            orch,
            target=str(normalized_plan["resources"][0]["path"]),
            out_report=out_report,
            change_reason=transaction_reason,
            max_diagnostics=runtime_max_diagnostics,
            apply=lambda: patch_apply(
                orch=orch,
                plan=normalized_plan,
                dry_run=dry_run,
                confirm=confirm,
                plan_sha256=plan_sha256,
                plan_signature=plan_signature,
                change_reason=change_reason,
                out_report=out_report,
                scope=scope,
                runtime_scene=runtime_scene,
                runtime_profile=runtime_profile,
                runtime_log_file=runtime_log_file,
                runtime_since_timestamp=runtime_since_timestamp,
                runtime_allow_warnings=runtime_allow_warnings,
                runtime_max_diagnostics=runtime_max_diagnostics,
                _transaction_bypass=True,
            ),
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
            preflight_data = preflight_refs.data if isinstance(preflight_refs.data, dict) else {}
            preflight_categories = preflight_data.get("categories", {}) or {}
            missing_asset_unique = int(preflight_categories.get("missing_asset", 0) or 0)
            if missing_asset_unique > 0:
                return _finalize(
                    (
                        f"patch.apply aborted: {missing_asset_unique} missing GUID "
                        "reference(s) detected in scope (fail-fast per #83)."
                    ),
                    fail_fast=True,
                    code="REF001",
                    severity_override=Severity.ERROR,
                    success_override=False,
                )
            return _finalize(
                "patch.apply stopped by fail-fast policy due to preflight reference errors.",
                fail_fast=True,
            )

    for resource, ops in executable_batches:
        resource_id = str(resource.get("id", ""))
        target = str(resource.get("path", ""))
        target_suffix = Path(target).suffix.lower()
        resource_mode = str(resource.get("mode", "open")).strip().lower() or "open"

        if target_suffix == ".prefab" and resource_mode == "open":
            overrides_step = orch.prefab_variant.list_overrides(target)
            steps.append((_step_name("list_overrides_preflight", resource_id), overrides_step))
            if overrides_step.severity in (Severity.ERROR, Severity.CRITICAL):
                return _finalize(
                    "patch.apply stopped by fail-fast policy due to preflight override inspection errors.",
                    fail_fast=True,
                )

        apply_step = orch.serialized_object.apply_resource_plan(resource=resource, ops=ops)
        steps.append((_step_name("apply_and_save", resource_id), apply_step))
        if not apply_step.success or apply_step.severity in (Severity.ERROR, Severity.CRITICAL):
            return _finalize(
                "patch.apply stopped by fail-fast policy due to apply failure.",
                fail_fast=True,
            )

    if runtime_scene:
        compile_step = orch.runtime_validation.compile_udonsharp()
        steps.append(("compile_udonsharp", compile_step))
        if compile_step.severity in (Severity.ERROR, Severity.CRITICAL):
            return _finalize(
                "patch.apply stopped by fail-fast policy due to UdonSharp compilation errors.",
                fail_fast=True,
            )

        run_step = orch.runtime_validation.run_clientsim(runtime_scene, runtime_profile)
        steps.append(("run_clientsim", run_step))
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
        evaluated = evaluate_postcondition(
            orch.serialized_object,
            orch.reference_resolver,
            postcondition,
            resource_map=resource_map,
        )
        post_type = str(postcondition["type"]).strip()
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
