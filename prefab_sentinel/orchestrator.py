from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from prefab_sentinel.contracts import (
    Diagnostic,
    Severity,
    ToolResponse,
    error_response,
    max_severity,
    success_response,
)
from prefab_sentinel.hierarchy import HierarchyNode, analyze_hierarchy, format_tree
from prefab_sentinel.material_inspector import (
    format_materials,
    inspect_materials,
)
from prefab_sentinel.patch_plan import count_plan_ops, iter_resource_batches, normalize_patch_plan
from prefab_sentinel.services.prefab_variant import PrefabVariantService
from prefab_sentinel.services.reference_resolver import ReferenceResolverService
from prefab_sentinel.services.runtime_validation import RuntimeValidationService
from prefab_sentinel.services.serialized_object import SerializedObjectService
from prefab_sentinel.structure_validator import validate_structure
from prefab_sentinel.udon_wiring import analyze_wiring
from prefab_sentinel.unity_assets import (
    GAMEOBJECT_BEARING_SUFFIXES,
    SOURCE_PREFAB_PATTERN,
    collect_project_guid_index,
    decode_text_file,
    find_project_root,
)
from prefab_sentinel.wsl_compat import to_wsl_path

__all__ = ["Phase1Orchestrator"]


@dataclass(slots=True)
class Phase1Orchestrator:
    reference_resolver: ReferenceResolverService
    prefab_variant: PrefabVariantService
    runtime_validation: RuntimeValidationService
    serialized_object: SerializedObjectService

    @classmethod
    def default(cls, project_root: Path | None = None) -> Phase1Orchestrator:
        """Create an orchestrator with default-configured service instances.

        Args:
            project_root: Unity project root. Auto-detected from cwd when ``None``.

        Returns:
            A fully wired ``Phase1Orchestrator``.
        """
        pv = PrefabVariantService(project_root=project_root)
        return cls(
            reference_resolver=ReferenceResolverService(project_root=project_root),
            prefab_variant=pv,
            runtime_validation=RuntimeValidationService(project_root=project_root),
            serialized_object=SerializedObjectService(
                project_root=project_root,
                prefab_variant=pv,
            ),
        )

    def inspect_variant(
        self,
        variant_path: str,
        component_filter: str | None = None,
        *,
        show_origin: bool = False,
    ) -> ToolResponse:
        """Run the full variant inspection pipeline (read-only).

        Args:
            variant_path: Path to a ``.prefab`` Variant asset.
            component_filter: Optional substring to filter overrides by component.
            show_origin: When ``True``, append chain-values-with-origin step.

        Returns:
            ``ToolResponse`` with ``data.steps`` containing results from
            resolve_prefab_chain, list_overrides, compute_effective_values,
            and detect_stale_overrides sub-steps.
        """
        named_steps: list[tuple[str, ToolResponse]] = [
            ("resolve_prefab_chain", self.prefab_variant.resolve_prefab_chain(variant_path)),
            ("list_overrides", self.prefab_variant.list_overrides(variant_path, component_filter)),
            (
                "compute_effective_values",
                self.prefab_variant.compute_effective_values(variant_path, component_filter),
            ),
            ("detect_stale_overrides", self.prefab_variant.detect_stale_overrides(variant_path)),
        ]
        if show_origin:
            named_steps.append((
                "resolve_chain_values_with_origin",
                self.prefab_variant.resolve_chain_values_with_origin(variant_path),
            ))
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
        """Find all files that reference a given asset or GUID (read-only).

        Args:
            asset_or_guid: Asset path or 32-char GUID to search for.
            scope: Directory or file path to restrict the search scope.
            exclude_patterns: Glob patterns for paths to skip.
            max_usages: Cap on the number of usage entries returned.

        Returns:
            ``ToolResponse`` with ``data.steps[0].result.data.usages``
            listing each referencing file, line, and column.
        """
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
        path = Path(to_wsl_path(target_path))
        if not path.exists():
            return error_response(
                f"{code_prefix}_FILE_NOT_FOUND",
                f"Target file does not exist: {target_path}",
                data={"target_path": target_path, "read_only": True},
            )
        try:
            return decode_text_file(path)
        except (OSError, UnicodeDecodeError) as exc:
            return error_response(
                f"{code_prefix}_READ_ERROR",
                f"Failed to read target file: {exc}",
                data={"target_path": target_path, "read_only": True},
            )

    def _resolve_variant_base(
        self,
        text: str,
        target_path: str,
        code_prefix: str,
    ) -> tuple[str, bool, str | None, list[Diagnostic]]:
        """If *text* is a Variant, resolve the chain and return the base text.

        Returns ``(text, is_variant, base_prefab_path, chain_diagnostics)``.
        When the file is not a Variant or the base cannot be read, the
        original *text* is returned unchanged with ``is_variant=False``.
        """
        if SOURCE_PREFAB_PATTERN.search(text) is None:
            return text, False, None, []

        chain_response = self.prefab_variant.resolve_prefab_chain(target_path)
        chain = chain_response.data.get("chain", [])
        chain_diagnostics = list(chain_response.diagnostics)

        base_path: str | None = None
        for entry in reversed(chain):
            entry_path = entry.get("path", "")
            if entry_path and entry_path != target_path:
                base_path = entry_path
                break

        if base_path:
            base_text_or_error = self._read_target_file(base_path, code_prefix)
            if not isinstance(base_text_or_error, ToolResponse):
                return base_text_or_error, True, base_path, chain_diagnostics

        return text, False, None, chain_diagnostics

    def inspect_wiring(
        self,
        target_path: str,
        *,
        udon_only: bool = False,
    ) -> ToolResponse:
        """Analyze MonoBehaviour field wiring in a Prefab or Scene (read-only).

        Args:
            target_path: Path to a ``.prefab`` or ``.unity`` file.
            udon_only: When ``True``, only report UdonSharp components.

        Returns:
            ``ToolResponse`` with ``data.components`` listing each
            MonoBehaviour, its fields, and any null/broken/duplicate
            reference diagnostics.
        """
        text_or_error = self._read_target_file(target_path, "INSPECT_WIRING")
        if isinstance(text_or_error, ToolResponse):
            return text_or_error
        text = text_or_error

        suffix = Path(target_path).suffix.lower()
        if suffix not in GAMEOBJECT_BEARING_SUFFIXES:
            return success_response(
                "INSPECT_WIRING_NO_MONOBEHAVIOURS",
                f"inspect.wiring is not applicable to {suffix} files "
                f"(no MonoBehaviour components). "
                f"Use validate refs to check external reference integrity.",
                severity=Severity.WARNING,
                data={"target_path": target_path, "file_type": suffix, "read_only": True},
            )

        text, is_variant, base_prefab_path, _ = self._resolve_variant_base(
            text, target_path, "INSPECT_WIRING",
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
        except Exception as exc:  # best-effort: project root or GUID index may fail
            logging.getLogger(__name__).debug("GUID index build failed (best-effort): %s", exc)

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
        data: dict[str, object] = {
            "target_path": target_path,
            "udon_only": udon_only,
            "read_only": True,
            "component_count": len(result.components),
            "null_reference_count": len(result.null_references),
            "internal_broken_ref_count": len(result.internal_broken_refs),
            "duplicate_reference_count": len(result.duplicate_references),
            "components": component_summaries,
        }
        if is_variant:
            data["is_variant"] = True
            data["base_prefab_path"] = base_prefab_path

        return ToolResponse(
            success=success,
            severity=result.max_severity,
            code="INSPECT_WIRING_RESULT",
            message="inspect.wiring completed (read-only).",
            data=data,
            diagnostics=diagnostics,
        )

    def inspect_hierarchy(
        self,
        target_path: str,
        *,
        max_depth: int | None = None,
        show_components: bool = True,
    ) -> ToolResponse:
        """Build the GameObject/Transform hierarchy tree (read-only).

        Args:
            target_path: Path to a ``.prefab`` or ``.unity`` file.
            max_depth: Limit the tree depth in the text representation.
            show_components: Include component names in tree nodes.

        Returns:
            ``ToolResponse`` with ``data.tree`` (formatted text) and
            ``data.roots`` (structured hierarchy nodes).
        """
        text_or_error = self._read_target_file(target_path, "INSPECT_HIERARCHY")
        if isinstance(text_or_error, ToolResponse):
            return text_or_error
        text = text_or_error

        suffix = Path(target_path).suffix.lower()
        if suffix not in GAMEOBJECT_BEARING_SUFFIXES:
            return success_response(
                "INSPECT_HIERARCHY_NO_GAMEOBJECTS",
                f"inspect.hierarchy is not applicable to {suffix} files "
                f"(no GameObject/Transform structure). "
                f"Use validate refs to check external reference integrity.",
                severity=Severity.WARNING,
                data={"target_path": target_path, "file_type": suffix, "read_only": True},
            )

        text, is_variant, base_prefab_path, chain_diags = self._resolve_variant_base(
            text, target_path, "INSPECT_HIERARCHY",
        )
        override_counts: dict[str, int] | None = None
        diagnostics: list[Diagnostic] = list(chain_diags)

        if is_variant:
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

        return success_response(
            "INSPECT_HIERARCHY_RESULT",
            "inspect.hierarchy completed (read-only).",
            data=data,
            diagnostics=diagnostics,
        )

    def inspect_materials(
        self,
        target_path: str,
    ) -> ToolResponse:
        """Inspect per-renderer material slot assignments (read-only).

        Args:
            target_path: Path to a ``.prefab`` or ``.unity`` file.

        Returns:
            ``ToolResponse`` with ``data.renderers`` listing each Renderer's
            material slots, and ``data.tree`` with a formatted text summary.
        """
        text_or_error = self._read_target_file(target_path, "INSPECT_MATERIALS")
        if isinstance(text_or_error, ToolResponse):
            return text_or_error

        suffix = Path(target_path).suffix.lower()
        if suffix not in GAMEOBJECT_BEARING_SUFFIXES:
            return success_response(
                "INSPECT_MATERIALS_NO_RENDERERS",
                f"inspect.materials is not applicable to {suffix} files "
                f"(no Renderer components expected).",
                severity=Severity.WARNING,
                data={"target_path": target_path, "file_type": suffix, "read_only": True},
            )

        try:
            result = inspect_materials(target_path)
        except (OSError, UnicodeDecodeError) as exc:
            return error_response(
                "INSPECT_MATERIALS_READ_ERROR",
                f"Failed to inspect materials: {exc}",
                data={"target_path": target_path, "read_only": True},
            )

        tree_text = format_materials(result)

        renderer_data = []
        for renderer in result.renderers:
            slot_data = [
                {
                    "index": slot.index,
                    "material_name": slot.material_name,
                    "material_path": slot.material_path,
                    "material_guid": slot.material_guid,
                    "is_override": slot.is_override,
                }
                for slot in renderer.slots
            ]
            renderer_data.append({
                "game_object_name": renderer.game_object_name,
                "renderer_type": renderer.renderer_type,
                "file_id": renderer.file_id,
                "slot_count": len(renderer.slots),
                "slots": slot_data,
            })

        data: dict[str, object] = {
            "target_path": target_path,
            "read_only": True,
            "is_variant": result.is_variant,
            "renderer_count": len(result.renderers),
            "total_material_slots": sum(len(r.slots) for r in result.renderers),
            "tree": tree_text,
            "renderers": renderer_data,
        }
        if result.is_variant:
            data["base_prefab_path"] = result.base_prefab_path
            override_count = sum(
                1 for r in result.renderers for s in r.slots if s.is_override
            )
            data["override_count"] = override_count

        return success_response(
            "INSPECT_MATERIALS_RESULT",
            "inspect.materials completed (read-only).",
            data=data,
        )

    def inspect_structure(
        self,
        target_path: str,
    ) -> ToolResponse:
        """Validate internal YAML structure of a Unity asset (read-only).

        Args:
            target_path: Path to any Unity text asset file.

        Returns:
            ``ToolResponse`` with diagnostics for duplicate fileIDs,
            Transform inconsistencies, missing components, and orphaned
            Transforms.
        """
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
        """Scan for broken GUID/fileID references in scope (read-only).

        Args:
            scope: Directory or file path to scan.
            details: When ``True``, include per-reference diagnostics.
            max_diagnostics: Cap on returned diagnostic entries.
            exclude_patterns: Glob patterns for paths to skip.
            ignore_asset_guids: GUIDs to exclude from missing-asset reports.

        Returns:
            ``ToolResponse`` whose ``data.steps[0].result.data`` contains
            ``broken_count``, ``scanned_files``, ``categories``, etc.
        """
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
        min_occurrences: int = 50,  # filter noise: below this are likely one-off refs
        max_items: int = 20,
        exclude_patterns: tuple[str, ...] = (),
        ignore_asset_guids: tuple[str, ...] = (),
    ) -> ToolResponse:
        """Suggest GUIDs that could be added to the ignore list (read-only).

        Args:
            scope: Directory or file path to scan for broken references.
            min_occurrences: Minimum occurrence count to qualify as a candidate.
            max_items: Maximum number of candidate GUIDs to return.
            exclude_patterns: Glob patterns for paths to skip during scan.
            ignore_asset_guids: GUIDs already in the ignore list.

        Returns:
            ``ToolResponse`` with ``data.candidates`` listing GUIDs sorted
            by occurrence count, and ``data.decision_required`` entries
            that need user approval before being applied.
        """
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
        """Run the full runtime validation pipeline (compile + ClientSim + log check).

        Args:
            scene_path: Path to the ``.unity`` scene to validate.
            profile: ClientSim profile name.
            log_file: Optional explicit path to Unity Editor.log.
            since_timestamp: Only classify log lines after this timestamp.
            allow_warnings: When ``True``, warnings do not fail the assertion.
            max_diagnostics: Cap on classified diagnostic entries.

        Returns:
            ``ToolResponse`` with ``data.steps`` containing compile_udonsharp,
            run_clientsim, collect_unity_console, classify_errors, and
            assert_no_critical_errors sub-step results.
        """
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
            return error_response(
                "POST_SCHEMA_ERROR",
                "Postcondition must be an object.",
                data={"read_only": True, "executed": False},
            )

        postcondition_type = str(postcondition.get("type", "")).strip()
        if not postcondition_type:
            return error_response(
                "POST_SCHEMA_ERROR",
                "Postcondition type is required.",
                data={"read_only": True, "executed": False},
            )

        if postcondition_type == "asset_exists":
            resource_id = str(postcondition.get("resource", "")).strip()
            explicit_path = str(postcondition.get("path", "")).strip()
            if bool(resource_id) == bool(explicit_path):
                return error_response(
                    "POST_SCHEMA_ERROR",
                    "asset_exists requires exactly one of 'resource' or 'path'.",
                    data={"type": postcondition_type, "read_only": True, "executed": False},
                )
            if resource_id and resource_id not in resource_ids:
                return error_response(
                    "POST_SCHEMA_ERROR",
                    "asset_exists references an unknown resource id.",
                    data={
                        "type": postcondition_type,
                        "resource": resource_id,
                        "read_only": True,
                        "executed": False,
                    },
                )
            return success_response(
                "POST_SCHEMA_OK",
                "Postcondition schema validated.",
                data={"type": postcondition_type, "read_only": True, "executed": False},
            )

        if postcondition_type == "broken_refs":
            scope = str(postcondition.get("scope", "")).strip()
            if not scope:
                return error_response(
                    "POST_SCHEMA_ERROR",
                    "broken_refs requires a non-empty 'scope'.",
                    data={"type": postcondition_type, "read_only": True, "executed": False},
                )
            expected_count = postcondition.get("expected_count", 0)
            if not isinstance(expected_count, int) or expected_count < 0:
                return error_response(
                    "POST_SCHEMA_ERROR",
                    "broken_refs.expected_count must be a non-negative integer.",
                    data={
                        "type": postcondition_type,
                        "scope": scope,
                        "read_only": True,
                        "executed": False,
                    },
                )
            for field_name in ("exclude_patterns", "ignore_asset_guids"):
                values = postcondition.get(field_name, [])
                if not isinstance(values, list) or any(
                    not isinstance(value, str) for value in values
                ):
                    return error_response(
                        "POST_SCHEMA_ERROR",
                        f"broken_refs.{field_name} must be an array of strings.",
                        data={
                            "type": postcondition_type,
                            "scope": scope,
                            "read_only": True,
                            "executed": False,
                        },
                    )
            max_diagnostics = postcondition.get("max_diagnostics", 200)
            if not isinstance(max_diagnostics, int) or max_diagnostics < 0:
                return error_response(
                    "POST_SCHEMA_ERROR",
                    "broken_refs.max_diagnostics must be a non-negative integer.",
                    data={
                        "type": postcondition_type,
                        "scope": scope,
                        "read_only": True,
                        "executed": False,
                    },
                )
            return success_response(
                "POST_SCHEMA_OK",
                "Postcondition schema validated.",
                data={"type": postcondition_type, "scope": scope, "read_only": True, "executed": False},
            )

        return error_response(
            "POST_SCHEMA_ERROR",
            "Postcondition type is not supported.",
            data={
                "type": postcondition_type,
                "read_only": True,
                "executed": False,
            },
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
                return error_response(
                    "POST_ASSET_EXISTS_FAILED",
                    "asset_exists postcondition failed: target path was not created."
                    " Check the preceding patch operations for errors;"
                    " verify the file was saved successfully.",
                    data={
                        "type": postcondition_type,
                        "resource": resource_id or None,
                        "path": str(target_path),
                        "exists": False,
                        "read_only": True,
                        "executed": True,
                    },
                )
            return success_response(
                "POST_ASSET_EXISTS_OK",
                "asset_exists postcondition passed.",
                data={
                    "type": postcondition_type,
                    "resource": resource_id or None,
                    "path": str(target_path),
                    "exists": True,
                    "read_only": True,
                    "executed": True,
                },
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
            return error_response(
                "POST_BROKEN_REFS_ERROR",
                "broken_refs postcondition could not be evaluated.",
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
            return error_response(
                "POST_BROKEN_REFS_FAILED",
                f"broken_refs postcondition failed: expected {expected_count}"
                f" broken refs but found {actual_count}."
                f" Run 'validate refs --scope {scope} --details' for full diagnostics.",
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
        return success_response(
            "POST_BROKEN_REFS_OK",
            "broken_refs postcondition passed.",
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
        """Execute a patch plan through dry-run, apply, and optional post-validation.

        Args:
            plan: Normalized patch plan dict with ``resources`` and ``ops``.
            dry_run: When ``True``, validate the plan without applying changes.
            confirm: Required to be ``True`` for actual writes (safety gate).
            plan_sha256: Optional SHA-256 digest for plan integrity verification.
            plan_signature: Optional signature for signed execution plans.
            change_reason: Human-readable reason for the change (audit trail).
            scope: Scope path for optional post-apply reference validation.
            runtime_scene: Scene path for optional post-apply runtime validation.
            runtime_profile: ClientSim profile for runtime validation.
            runtime_log_file: Explicit log file path for runtime validation.
            runtime_since_timestamp: Log timestamp filter for runtime validation.
            runtime_allow_warnings: Allow warnings in runtime assertion.
            runtime_max_diagnostics: Cap on runtime diagnostic entries.

        Returns:
            ``ToolResponse`` with ``data.steps`` containing dry_run_patch,
            apply_and_save, and optional validate_refs / validate_runtime
            sub-step results. ``data.execution_id`` provides the audit key.
        """
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
