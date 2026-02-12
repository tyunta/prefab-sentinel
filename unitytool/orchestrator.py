from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from unitytool.contracts import Severity, ToolResponse, max_severity
from unitytool.mcp.prefab_variant import PrefabVariantMcp
from unitytool.mcp.reference_resolver import ReferenceResolverMcp


@dataclass(slots=True)
class Phase1Orchestrator:
    reference_resolver: ReferenceResolverMcp
    prefab_variant: PrefabVariantMcp

    @classmethod
    def default(cls, project_root: Path | None = None) -> "Phase1Orchestrator":
        return cls(
            reference_resolver=ReferenceResolverMcp(project_root=project_root),
            prefab_variant=PrefabVariantMcp(project_root=project_root),
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
