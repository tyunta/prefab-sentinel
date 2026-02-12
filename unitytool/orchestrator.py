from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from unitytool.contracts import Severity, ToolResponse, max_severity
from unitytool.mcp.prefab_variant import PrefabVariantMcp
from unitytool.mcp.reference_resolver import ReferenceResolverMcp
from unitytool.mcp.runtime_validation import RuntimeValidationMcp
from unitytool.mcp.serialized_object import SerializedObjectMcp


@dataclass(slots=True)
class Phase1Orchestrator:
    reference_resolver: ReferenceResolverMcp
    prefab_variant: PrefabVariantMcp
    runtime_validation: RuntimeValidationMcp
    serialized_object: SerializedObjectMcp

    @classmethod
    def default(cls, project_root: Path | None = None) -> "Phase1Orchestrator":
        return cls(
            reference_resolver=ReferenceResolverMcp(project_root=project_root),
            prefab_variant=PrefabVariantMcp(project_root=project_root),
            runtime_validation=RuntimeValidationMcp(project_root=project_root),
            serialized_object=SerializedObjectMcp(),
        )

    def inspect_variant(
        self,
        variant_path: str,
        component_filter: str | None = None,
    ) -> ToolResponse:
        named_steps = [
            ("resolve_prefab_chain", self.prefab_variant.resolve_prefab_chain(variant_path)),
            ("list_overrides", self.prefab_variant.list_overrides(variant_path, component_filter)),
            (
                "compute_effective_values",
                self.prefab_variant.compute_effective_values(variant_path, component_filter),
            ),
            ("detect_stale_overrides", self.prefab_variant.detect_stale_overrides(variant_path)),
        ]
        executed_steps: list[dict[str, object]] = []
        diagnostics = []
        severities = []
        fail_fast = False
        for step_name, step in named_steps:
            executed_steps.append({"step": step_name, "result": step.to_dict()})
            diagnostics.extend(step.diagnostics)
            severities.append(step.severity)
            if step.severity in (Severity.ERROR, Severity.CRITICAL):
                fail_fast = True
                break

        severity = max_severity(severities)
        success = severity not in (Severity.ERROR, Severity.CRITICAL)
        return ToolResponse(
            success=success,
            severity=severity,
            code="INSPECT_VARIANT_RESULT",
            message=(
                "inspect.variant pipeline completed (read-only)."
                if not fail_fast
                else "inspect.variant stopped by fail-fast policy due to error severity."
            ),
            data={
                "variant_path": variant_path,
                "component_filter": component_filter,
                "read_only": True,
                "fail_fast_triggered": fail_fast,
                "steps": executed_steps,
            },
            diagnostics=diagnostics,
        )

    def inspect_where_used(
        self,
        asset_or_guid: str,
        scope: str | None = None,
        exclude_patterns: tuple[str, ...] = (),
        max_usages: int = 500,
    ) -> ToolResponse:
        step = self.reference_resolver.where_used(
            asset_or_guid=asset_or_guid,
            scope=scope,
            exclude_patterns=exclude_patterns,
            max_usages=max_usages,
        )
        return ToolResponse(
            success=step.success,
            severity=step.severity,
            code="INSPECT_WHERE_USED_RESULT",
            message="inspect.where-used pipeline completed (read-only).",
            data={
                "asset_or_guid": asset_or_guid,
                "scope": scope,
                "read_only": True,
                "steps": [
                    {
                        "step": "where_used",
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

    def validate_refs(
        self,
        scope: str,
        details: bool = False,
        max_diagnostics: int = 200,
        exclude_patterns: tuple[str, ...] = (),
        ignore_asset_guids: tuple[str, ...] = (),
    ) -> ToolResponse:
        step = self.reference_resolver.scan_broken_references(
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

    def suggest_ignore_guids(
        self,
        scope: str,
        min_occurrences: int = 50,
        max_items: int = 20,
        exclude_patterns: tuple[str, ...] = (),
        ignore_asset_guids: tuple[str, ...] = (),
    ) -> ToolResponse:
        effective_max_items = max(1, max_items)
        step = self.reference_resolver.scan_broken_references(
            scope=scope,
            include_diagnostics=False,
            max_diagnostics=0,
            exclude_patterns=exclude_patterns,
            top_guid_limit=max(100, effective_max_items * 5),
            ignore_asset_guids=ignore_asset_guids,
        )

        if step.code not in {"REF_SCAN_BROKEN", "REF_SCAN_PARTIAL", "REF_SCAN_OK"}:
            return ToolResponse(
                success=False,
                severity=step.severity,
                code="SUGGEST_IGNORE_GUIDS_RESULT",
                message="suggest.ignore-guids failed before candidate analysis.",
                data={
                    "scope": scope,
                    "read_only": True,
                    "steps": [{"step": "scan_broken_references", "result": step.to_dict()}],
                },
                diagnostics=step.diagnostics,
            )

        min_occ = max(1, min_occurrences)
        missing_asset_occurrences = step.data.get("categories_occurrences", {}).get(
            "missing_asset", 0
        )
        top_guids = step.data.get("top_missing_asset_guids", [])
        candidates: list[dict[str, object]] = []
        for item in top_guids:
            occurrences = int(item.get("occurrences", 0))
            if occurrences < min_occ:
                continue
            share = (
                occurrences / missing_asset_occurrences
                if missing_asset_occurrences > 0
                else 0.0
            )
            candidates.append(
                {
                    "guid": item.get("guid", ""),
                    "occurrences": occurrences,
                    "share_of_missing_asset_occurrences": round(share, 6),
                }
            )
            if len(candidates) >= effective_max_items:
                break

        if candidates:
            severity = Severity.INFO
            success = True
            message = "Ignore candidate GUID list was generated."
        else:
            severity = Severity.WARNING
            success = True
            message = "No ignore candidate GUIDs matched the threshold."

        return ToolResponse(
            success=success,
            severity=severity,
            code="SUGGEST_IGNORE_GUIDS_RESULT",
            message=message,
            data={
                "scope": scope,
                "read_only": True,
                "criteria": {
                    "min_occurrences": min_occ,
                    "max_items": effective_max_items,
                    "exclude_patterns": list(exclude_patterns),
                    "ignore_asset_guids": list(ignore_asset_guids),
                },
                "missing_asset_unique_count": step.data.get("categories", {}).get(
                    "missing_asset", 0
                ),
                "missing_asset_occurrences": missing_asset_occurrences,
                "candidate_count": len(candidates),
                "candidates": candidates,
                "steps": [
                    {
                        "step": "scan_broken_references",
                        "result": {
                            "success": step.success,
                            "severity": step.severity.value,
                            "code": step.code,
                            "message": step.message,
                            "data": {
                                "scanned_files": step.data.get("scanned_files", 0),
                                "scanned_references": step.data.get(
                                    "scanned_references", 0
                                ),
                                "broken_count": step.data.get("broken_count", 0),
                                "broken_occurrences": step.data.get(
                                    "broken_occurrences", 0
                                ),
                                "unreadable_files": step.data.get("unreadable_files", 0),
                                "categories": step.data.get("categories", {}),
                                "categories_occurrences": step.data.get(
                                    "categories_occurrences", {}
                                ),
                            },
                        },
                    }
                ],
                "note": (
                    "Candidates are heuristic. Review each GUID before adding to an ignore policy."
                ),
            },
            diagnostics=[],
        )

    def validate_runtime(
        self,
        scene_path: str,
        profile: str = "default",
        log_file: str | None = None,
        since_timestamp: str | None = None,
        allow_warnings: bool = False,
        max_diagnostics: int = 200,
    ) -> ToolResponse:
        compile_step = self.runtime_validation.compile_udonsharp()
        run_step = self.runtime_validation.run_clientsim(scene_path, profile)

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
                    "read_only": True,
                    "fail_fast_triggered": True,
                    "steps": [
                        {"step": name, "result": step.to_dict()} for name, step in steps
                    ],
                },
                diagnostics=[],
            )

        collect_step = self.runtime_validation.collect_unity_console(
            log_file=log_file,
            since_timestamp=since_timestamp,
        )
        classify_step = self.runtime_validation.classify_errors(
            log_lines=list(collect_step.data.get("log_lines", [])),
            max_diagnostics=max_diagnostics,
        )
        assert_step = self.runtime_validation.assert_no_critical_errors(
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
            message="validate.runtime pipeline completed (log-based scaffold).",
            data={
                "scene_path": scene_path,
                "profile": profile,
                "read_only": True,
                "fail_fast_triggered": False,
                "steps": [{"step": name, "result": step.to_dict()} for name, step in steps],
            },
            diagnostics=diagnostics,
        )

    def patch_apply(
        self,
        plan: dict[str, object],
        dry_run: bool = False,
        confirm: bool = False,
        plan_sha256: str | None = None,
        plan_signature: str | None = None,
        scope: str | None = None,
        runtime_scene: str | None = None,
        runtime_profile: str = "default",
        runtime_log_file: str | None = None,
        runtime_since_timestamp: str | None = None,
        runtime_allow_warnings: bool = False,
        runtime_max_diagnostics: int = 200,
    ) -> ToolResponse:
        target = str(plan.get("target", ""))
        raw_ops = plan.get("ops", [])
        ops = raw_ops if isinstance(raw_ops, list) else []
        target_suffix = Path(target).suffix.lower()

        steps: list[tuple[str, ToolResponse]] = []

        def _finalize(message: str, fail_fast: bool) -> ToolResponse:
            severities = [step.severity for _, step in steps]
            severity = max_severity(severities)
            success = all(step.success for _, step in steps)
            diagnostics = [
                diagnostic
                for _, step in steps
                for diagnostic in step.diagnostics
            ]
            write_executed = any(step_name == "apply_and_save" for step_name, _ in steps)
            return ToolResponse(
                success=success,
                severity=severity,
                code="PATCH_APPLY_RESULT",
                message=message,
                data={
                    "target": target,
                    "plan_sha256": plan_sha256,
                    "plan_signature": plan_signature,
                    "dry_run": dry_run,
                    "confirm": confirm,
                    "scope": scope,
                    "runtime_scene": runtime_scene,
                    "runtime_profile": runtime_profile,
                    "runtime_log_file": runtime_log_file,
                    "runtime_since_timestamp": runtime_since_timestamp,
                    "runtime_allow_warnings": runtime_allow_warnings,
                    "runtime_max_diagnostics": runtime_max_diagnostics,
                    "read_only": not write_executed,
                    "fail_fast_triggered": fail_fast,
                    "steps": [
                        {"step": step_name, "result": step.to_dict()}
                        for step_name, step in steps
                    ],
                },
                diagnostics=diagnostics,
            )

        dry_step = self.serialized_object.dry_run_patch(target=target, ops=ops)
        steps.append(("dry_run_patch", dry_step))
        if dry_step.severity in (Severity.ERROR, Severity.CRITICAL):
            return _finalize(
                "patch.apply stopped by fail-fast policy due to invalid patch plan.",
                fail_fast=True,
            )

        if dry_run:
            return _finalize("patch.apply dry-run completed.", fail_fast=False)

        if not confirm:
            confirm_step = ToolResponse(
                success=False,
                severity=Severity.WARNING,
                code="SER_CONFIRM_REQUIRED",
                message="patch.apply requires --confirm when not using --dry-run.",
                data={"target": target, "op_count": len(ops), "read_only": True},
                diagnostics=[],
            )
            steps.append(("confirm_gate", confirm_step))
            return _finalize("patch.apply blocked by confirm gate.", fail_fast=False)

        if scope:
            preflight_refs = self.reference_resolver.scan_broken_references(
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

        if target_suffix == ".prefab":
            overrides_step = self.prefab_variant.list_overrides(target)
            steps.append(("list_overrides_preflight", overrides_step))
            if overrides_step.severity in (Severity.ERROR, Severity.CRITICAL):
                return _finalize(
                    "patch.apply stopped by fail-fast policy due to preflight override inspection errors.",
                    fail_fast=True,
                )

        apply_step = self.serialized_object.apply_and_save(target=target, ops=ops)
        steps.append(("apply_and_save", apply_step))
        if apply_step.severity in (Severity.ERROR, Severity.CRITICAL):
            return _finalize("patch.apply completed with errors.", fail_fast=False)

        if runtime_scene:
            compile_step = self.runtime_validation.compile_udonsharp()
            run_step = self.runtime_validation.run_clientsim(runtime_scene, runtime_profile)
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

            collect_step = self.runtime_validation.collect_unity_console(
                log_file=runtime_log_file,
                since_timestamp=runtime_since_timestamp,
            )
            classify_step = self.runtime_validation.classify_errors(
                log_lines=list(collect_step.data.get("log_lines", [])),
                max_diagnostics=runtime_max_diagnostics,
            )
            assert_step = self.runtime_validation.assert_no_critical_errors(
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

        success = all(step.success for _, step in steps)
        if success:
            return _finalize("patch.apply completed.", fail_fast=False)
        return _finalize("patch.apply completed with warnings.", fail_fast=False)
