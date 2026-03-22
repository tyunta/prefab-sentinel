from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from prefab_sentinel.contracts import Diagnostic, Severity, ToolResponse, max_severity
from prefab_sentinel.hierarchy import HierarchyNode, analyze_hierarchy, format_tree
from prefab_sentinel.mcp.prefab_variant import PrefabVariantMcp
from prefab_sentinel.mcp.reference_resolver import ReferenceResolverMcp
from prefab_sentinel.mcp.runtime_validation import RuntimeValidationMcp
from prefab_sentinel.mcp.serialized_object import SerializedObjectMcp
from prefab_sentinel.patch_plan import count_plan_ops, iter_resource_batches, normalize_patch_plan
from prefab_sentinel.structure_validator import validate_structure
from prefab_sentinel.udon_wiring import analyze_wiring
from prefab_sentinel.unity_assets import (
    GAMEOBJECT_BEARING_SUFFIXES,
    SOURCE_PREFAB_PATTERN,
    collect_project_guid_index,
    decode_text_file,
    find_project_root,
)


@dataclass(slots=True)
class Phase1Orchestrator:
    reference_resolver: ReferenceResolverMcp
    prefab_variant: PrefabVariantMcp
    runtime_validation: RuntimeValidationMcp
    serialized_object: SerializedObjectMcp

    @classmethod
    def default(cls, project_root: Path | None = None) -> Phase1Orchestrator:
        pv = PrefabVariantMcp(project_root=project_root)
        return cls(
            reference_resolver=ReferenceResolverMcp(project_root=project_root),
            prefab_variant=pv,
            runtime_validation=RuntimeValidationMcp(project_root=project_root),
            serialized_object=SerializedObjectMcp(
                project_root=project_root,
                prefab_variant=pv,
            ),
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

    @staticmethod
    def _read_target_file(target_path: str, code_prefix: str) -> ToolResponse | str:
        """Read a Unity YAML file, returning text on success or an error ToolResponse."""
        path = Path(target_path)
        if not path.exists():
            return ToolResponse(
                success=False,
                severity=Severity.ERROR,
                code=f"{code_prefix}_FILE_NOT_FOUND",
                message=f"Target file does not exist: {target_path}",
                data={"target_path": target_path, "read_only": True},
                diagnostics=[],
            )
        try:
            return decode_text_file(path)
        except (OSError, UnicodeDecodeError) as exc:
            return ToolResponse(
                success=False,
                severity=Severity.ERROR,
                code=f"{code_prefix}_READ_ERROR",
                message=f"Failed to read target file: {exc}",
                data={"target_path": target_path, "read_only": True},
                diagnostics=[],
            )

    def inspect_wiring(
        self,
        target_path: str,
        *,
        udon_only: bool = False,
    ) -> ToolResponse:
        text_or_error = self._read_target_file(target_path, "INSPECT_WIRING")
        if isinstance(text_or_error, ToolResponse):
            return text_or_error
        text = text_or_error

        suffix = Path(target_path).suffix.lower()
        if suffix not in GAMEOBJECT_BEARING_SUFFIXES:
            return ToolResponse(
                success=True,
                severity=Severity.WARNING,
                code="INSPECT_WIRING_NO_MONOBEHAVIOURS",
                message=(
                    f"inspect.wiring is not applicable to {suffix} files "
                    f"(no MonoBehaviour components). "
                    f"Use validate refs to check external reference integrity."
                ),
                data={"target_path": target_path, "file_type": suffix, "read_only": True},
                diagnostics=[],
            )

        result = analyze_wiring(text, target_path, udon_only=udon_only)
        diagnostics: list[Diagnostic] = (
            result.null_references
            + result.internal_broken_refs
            + result.duplicate_references
        )
        success = result.max_severity not in (Severity.ERROR, Severity.CRITICAL)

        def _go_name(comp_go_fid: str) -> str:
            go = result.game_objects.get(comp_go_fid)
            return go.name if go and go.name else ""

        # Best-effort GUID→script name resolution
        guid_to_name: dict[str, str] = {}
        try:
            proj_root = find_project_root(Path(target_path))
            guid_index = collect_project_guid_index(proj_root, include_package_cache=False)
            for guid, asset_path in guid_index.items():
                if asset_path.suffix == ".cs":
                    guid_to_name[guid] = asset_path.stem
        except Exception:
            pass  # best-effort: leave guid_to_name empty

        component_summaries = [
            {
                "file_id": comp.file_id,
                "game_object_file_id": comp.game_object_file_id,
                "game_object_name": _go_name(comp.game_object_file_id),
                "script_guid": comp.script_guid,
                "script_name": guid_to_name.get(comp.script_guid, ""),
                "is_udon_sharp": comp.is_udon_sharp,
                "field_count": len(comp.fields),
                "fields": [
                    {
                        "name": f.name,
                        "file_id": f.file_id,
                        "guid": f.guid,
                        "line": f.line,
                    }
                    for f in comp.fields
                ],
            }
            for comp in result.components
        ]
        return ToolResponse(
            success=success,
            severity=result.max_severity,
            code="INSPECT_WIRING_RESULT",
            message="inspect.wiring completed (read-only).",
            data={
                "target_path": target_path,
                "udon_only": udon_only,
                "read_only": True,
                "component_count": len(result.components),
                "null_reference_count": len(result.null_references),
                "internal_broken_ref_count": len(result.internal_broken_refs),
                "duplicate_reference_count": len(result.duplicate_references),
                "components": component_summaries,
            },
            diagnostics=diagnostics,
        )

    def inspect_hierarchy(
        self,
        target_path: str,
        *,
        max_depth: int | None = None,
        show_components: bool = True,
    ) -> ToolResponse:
        text_or_error = self._read_target_file(target_path, "INSPECT_HIERARCHY")
        if isinstance(text_or_error, ToolResponse):
            return text_or_error
        text = text_or_error

        suffix = Path(target_path).suffix.lower()
        if suffix not in GAMEOBJECT_BEARING_SUFFIXES:
            return ToolResponse(
                success=True,
                severity=Severity.WARNING,
                code="INSPECT_HIERARCHY_NO_GAMEOBJECTS",
                message=(
                    f"inspect.hierarchy is not applicable to {suffix} files "
                    f"(no GameObject/Transform structure). "
                    f"Use validate refs to check external reference integrity."
                ),
                data={"target_path": target_path, "file_type": suffix, "read_only": True},
                diagnostics=[],
            )

        # Detect Variant: if m_SourcePrefab exists, resolve base prefab hierarchy
        is_variant = False
        base_prefab_path: str | None = None
        override_counts: dict[str, int] | None = None
        diagnostics: list[Diagnostic] = []

        if SOURCE_PREFAB_PATTERN.search(text) is not None:
            chain_response = self.prefab_variant.resolve_prefab_chain(target_path)
            chain = chain_response.data.get("chain", [])
            diagnostics.extend(chain_response.diagnostics)

            # Find the base prefab (last entry in chain with a non-empty path)
            base_path: str | None = None
            for entry in reversed(chain):
                entry_path = entry.get("path", "")
                if entry_path and entry_path != target_path:
                    base_path = entry_path
                    break

            if base_path:
                base_text_or_error = self._read_target_file(base_path, "INSPECT_HIERARCHY")
                if not isinstance(base_text_or_error, ToolResponse):
                    text = base_text_or_error
                    is_variant = True
                    base_prefab_path = base_path

                    # Build override count map from Variant overrides
                    overrides_response = self.prefab_variant.list_overrides(target_path)
                    if overrides_response.success:
                        counts: dict[str, int] = {}
                        for ov in overrides_response.data.get("overrides", []):
                            fid = ov.get("target_file_id", "")
                            if fid:
                                counts[fid] = counts.get(fid, 0) + 1
                        override_counts = counts
                    diagnostics.extend(overrides_response.diagnostics)

        result = analyze_hierarchy(text, override_counts=override_counts)
        tree_text = format_tree(
            result,
            max_depth=max_depth,
            show_components=show_components,
        )

        def _serialize_node(node: HierarchyNode) -> dict[str, object]:
            d: dict[str, object] = {
                "file_id": node.file_id,
                "name": node.name,
                "depth": node.depth,
                "components": node.components,
                "children": [_serialize_node(c) for c in node.children],
            }
            if node.override_count > 0:
                d["override_count"] = node.override_count
            return d

        data: dict[str, object] = {
            "target_path": target_path,
            "read_only": True,
            "total_game_objects": result.total_game_objects,
            "total_components": result.total_components,
            "max_depth": result.max_depth,
            "root_count": len(result.roots),
            "tree": tree_text,
            "roots": [_serialize_node(r) for r in result.roots],
        }
        if is_variant:
            data["is_variant"] = True
            data["base_prefab_path"] = base_prefab_path

        return ToolResponse(
            success=True,
            severity=Severity.INFO,
            code="INSPECT_HIERARCHY_RESULT",
            message="inspect.hierarchy completed (read-only).",
            data=data,
            diagnostics=diagnostics,
        )

    def inspect_structure(
        self,
        target_path: str,
    ) -> ToolResponse:
        text_or_error = self._read_target_file(target_path, "VALIDATE_STRUCTURE")
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
            entry: dict[str, object] = {
                "guid": item.get("guid", ""),
                "occurrences": occurrences,
                "share_of_missing_asset_occurrences": round(share, 6),
            }
            asset_name = item.get("asset_name", "")
            if asset_name:
                entry["asset_name"] = asset_name
            candidates.append(entry)
            if len(candidates) >= effective_max_items:
                break

        decision_required = [
            {
                "action": "ignore_guid",
                "guid": item.get("guid", ""),
                "occurrences": item.get("occurrences", 0),
                **({"asset_name": item["asset_name"]} if item.get("asset_name") else {}),
            }
            for item in candidates
        ]

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
                "safe_fix": [],
                "decision_required": decision_required,
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

    def _validate_postcondition_schema(
        self,
        postcondition: object,
        *,
        resource_ids: set[str],
    ) -> ToolResponse:
        if not isinstance(postcondition, dict):
            return ToolResponse(
                success=False,
                severity=Severity.ERROR,
                code="POST_SCHEMA_ERROR",
                message="Postcondition must be an object.",
                data={"read_only": True, "executed": False},
                diagnostics=[],
            )

        postcondition_type = str(postcondition.get("type", "")).strip()
        if not postcondition_type:
            return ToolResponse(
                success=False,
                severity=Severity.ERROR,
                code="POST_SCHEMA_ERROR",
                message="Postcondition type is required.",
                data={"read_only": True, "executed": False},
                diagnostics=[],
            )

        if postcondition_type == "asset_exists":
            resource_id = str(postcondition.get("resource", "")).strip()
            explicit_path = str(postcondition.get("path", "")).strip()
            if bool(resource_id) == bool(explicit_path):
                return ToolResponse(
                    success=False,
                    severity=Severity.ERROR,
                    code="POST_SCHEMA_ERROR",
                    message="asset_exists requires exactly one of 'resource' or 'path'.",
                    data={"type": postcondition_type, "read_only": True, "executed": False},
                    diagnostics=[],
                )
            if resource_id and resource_id not in resource_ids:
                return ToolResponse(
                    success=False,
                    severity=Severity.ERROR,
                    code="POST_SCHEMA_ERROR",
                    message="asset_exists references an unknown resource id.",
                    data={
                        "type": postcondition_type,
                        "resource": resource_id,
                        "read_only": True,
                        "executed": False,
                    },
                    diagnostics=[],
                )
            return ToolResponse(
                success=True,
                severity=Severity.INFO,
                code="POST_SCHEMA_OK",
                message="Postcondition schema validated.",
                data={"type": postcondition_type, "read_only": True, "executed": False},
                diagnostics=[],
            )

        if postcondition_type == "broken_refs":
            scope = str(postcondition.get("scope", "")).strip()
            if not scope:
                return ToolResponse(
                    success=False,
                    severity=Severity.ERROR,
                    code="POST_SCHEMA_ERROR",
                    message="broken_refs requires a non-empty 'scope'.",
                    data={"type": postcondition_type, "read_only": True, "executed": False},
                    diagnostics=[],
                )
            expected_count = postcondition.get("expected_count", 0)
            if not isinstance(expected_count, int) or expected_count < 0:
                return ToolResponse(
                    success=False,
                    severity=Severity.ERROR,
                    code="POST_SCHEMA_ERROR",
                    message="broken_refs.expected_count must be a non-negative integer.",
                    data={
                        "type": postcondition_type,
                        "scope": scope,
                        "read_only": True,
                        "executed": False,
                    },
                    diagnostics=[],
                )
            for field_name in ("exclude_patterns", "ignore_asset_guids"):
                values = postcondition.get(field_name, [])
                if not isinstance(values, list) or any(
                    not isinstance(value, str) for value in values
                ):
                    return ToolResponse(
                        success=False,
                        severity=Severity.ERROR,
                        code="POST_SCHEMA_ERROR",
                        message=f"broken_refs.{field_name} must be an array of strings.",
                        data={
                            "type": postcondition_type,
                            "scope": scope,
                            "read_only": True,
                            "executed": False,
                        },
                        diagnostics=[],
                    )
            max_diagnostics = postcondition.get("max_diagnostics", 200)
            if not isinstance(max_diagnostics, int) or max_diagnostics < 0:
                return ToolResponse(
                    success=False,
                    severity=Severity.ERROR,
                    code="POST_SCHEMA_ERROR",
                    message="broken_refs.max_diagnostics must be a non-negative integer.",
                    data={
                        "type": postcondition_type,
                        "scope": scope,
                        "read_only": True,
                        "executed": False,
                    },
                    diagnostics=[],
                )
            return ToolResponse(
                success=True,
                severity=Severity.INFO,
                code="POST_SCHEMA_OK",
                message="Postcondition schema validated.",
                data={"type": postcondition_type, "scope": scope, "read_only": True, "executed": False},
                diagnostics=[],
            )

        return ToolResponse(
            success=False,
            severity=Severity.ERROR,
            code="POST_SCHEMA_ERROR",
            message="Postcondition type is not supported.",
            data={
                "type": postcondition_type,
                "read_only": True,
                "executed": False,
            },
            diagnostics=[],
        )

    def _evaluate_postcondition(
        self,
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
            target_path = self.serialized_object._resolve_target_path(target)
            exists = target_path.exists()
            if not exists:
                return ToolResponse(
                    success=False,
                    severity=Severity.ERROR,
                    code="POST_ASSET_EXISTS_FAILED",
                    message="asset_exists postcondition failed because the target path was not created.",
                    data={
                        "type": postcondition_type,
                        "resource": resource_id or None,
                        "path": str(target_path),
                        "exists": False,
                        "read_only": True,
                        "executed": True,
                    },
                    diagnostics=[],
                )
            return ToolResponse(
                success=True,
                severity=Severity.INFO,
                code="POST_ASSET_EXISTS_OK",
                message="asset_exists postcondition passed.",
                data={
                    "type": postcondition_type,
                    "resource": resource_id or None,
                    "path": str(target_path),
                    "exists": True,
                    "read_only": True,
                    "executed": True,
                },
                diagnostics=[],
            )

        scope = str(postcondition.get("scope", "")).strip()
        expected_count = int(postcondition.get("expected_count", 0))
        scan = self.reference_resolver.scan_broken_references(
            scope=scope,
            include_diagnostics=bool(postcondition.get("include_diagnostics", False)),
            max_diagnostics=int(postcondition.get("max_diagnostics", 200)),
            exclude_patterns=tuple(postcondition.get("exclude_patterns", [])),
            ignore_asset_guids=tuple(postcondition.get("ignore_asset_guids", [])),
        )
        if scan.code in {"REF404", "REF001"}:
            return ToolResponse(
                success=False,
                severity=Severity.ERROR,
                code="POST_BROKEN_REFS_ERROR",
                message="broken_refs postcondition could not be evaluated.",
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
            return ToolResponse(
                success=False,
                severity=Severity.ERROR,
                code="POST_BROKEN_REFS_FAILED",
                message="broken_refs postcondition failed.",
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
        return ToolResponse(
            success=True,
            severity=Severity.INFO,
            code="POST_BROKEN_REFS_OK",
            message="broken_refs postcondition passed.",
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

    def patch_apply(
        self,
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
            schema_step = self._validate_postcondition_schema(
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
            dry_step = self.serialized_object.dry_run_resource_plan(resource=resource, ops=ops)
            steps.append((_step_name("dry_run_patch", str(resource.get("id", ""))), dry_step))
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
                data={
                    "target": primary_target,
                    "targets": targets,
                    "resource_count": resource_count,
                    "op_count": total_op_count,
                    "read_only": True,
                },
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

        for resource, ops in resource_batches:
            resource_id = str(resource.get("id", ""))
            target = str(resource.get("path", ""))
            target_suffix = Path(target).suffix.lower()
            resource_mode = str(resource.get("mode", "open")).strip().lower() or "open"

            if target_suffix == ".prefab" and resource_mode == "open":
                overrides_step = self.prefab_variant.list_overrides(target)
                steps.append(
                    (_step_name("list_overrides_preflight", resource_id), overrides_step)
                )
                if overrides_step.severity in (Severity.ERROR, Severity.CRITICAL):
                    return _finalize(
                        "patch.apply stopped by fail-fast policy due to preflight override inspection errors.",
                        fail_fast=True,
                    )

            apply_step = self.serialized_object.apply_resource_plan(resource=resource, ops=ops)
            steps.append((_step_name("apply_and_save", resource_id), apply_step))
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

        for index, postcondition in enumerate(postconditions):
            evaluated = self._evaluate_postcondition(
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
