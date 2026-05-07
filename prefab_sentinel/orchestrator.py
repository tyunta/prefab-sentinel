from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from prefab_sentinel import (
    orchestrator_fields,
    orchestrator_inspect,
    orchestrator_patch,
    orchestrator_postcondition,
    orchestrator_validation,
    orchestrator_variant,
    orchestrator_wiring,
    orchestrator_write,
)
from prefab_sentinel.contracts import ToolResponse
from prefab_sentinel.editor_bridge import bridge_status, send_action
from prefab_sentinel.services.prefab_variant import PrefabVariantService
from prefab_sentinel.services.reference_resolver import ReferenceResolverService
from prefab_sentinel.services.runtime_validation import RuntimeValidationService
from prefab_sentinel.services.serialized_object import SerializedObjectService

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

    def maybe_auto_refresh(self) -> str:
        """Trigger AssetDatabase.Refresh if Editor Bridge is connected.

        Returns:
            "true" if refresh succeeded, "false" if refresh failed,
            "skipped" if bridge is not connected.
        """
        status = bridge_status()
        if not status["connected"]:
            return "skipped"
        try:
            send_action(action="refresh_asset_database")
            return "true"
        except Exception:
            return "false"

    # ------------------------------------------------------------------
    # Cache invalidation (delegated to services)
    # ------------------------------------------------------------------

    def invalidate_text_cache(self, path: Path | None = None) -> None:
        """Delegate text cache invalidation to reference resolver."""
        self.reference_resolver.invalidate_text_cache(path)

    def invalidate_guid_index(self) -> None:
        """Delegate GUID index invalidation to reference resolver."""
        self.reference_resolver.invalidate_guid_index()

    def invalidate_before_cache(self) -> None:
        """Delegate before-cache invalidation to serialized object service."""
        self.serialized_object.invalidate_before_cache()

    def invalidate_scope_files_cache(self) -> None:
        """Delegate scope files cache invalidation to reference resolver."""
        self.reference_resolver.invalidate_scope_files_cache()

    # ------------------------------------------------------------------
    # Internal helpers (thin wrappers for extracted functions)
    # ------------------------------------------------------------------

    def _read_target_file(self, target_path: str, code_prefix: str) -> ToolResponse | str:
        return orchestrator_variant._read_target_file(self.prefab_variant, target_path, code_prefix)

    def _resolve_variant_base(
        self, text: str, target_path: str, code_prefix: str,
    ) -> tuple[str, bool, str | None, list]:
        return orchestrator_variant._resolve_variant_base(
            self.prefab_variant, text, target_path, code_prefix,
        )

    def _validate_postcondition_schema(
        self, postcondition: object, *, resource_ids: set[str],
    ) -> ToolResponse:
        return orchestrator_postcondition._validate_postcondition_schema(
            postcondition, resource_ids=resource_ids,
        )

    def _evaluate_postcondition(
        self, postcondition: dict[str, Any], *, resource_map: dict[str, dict[str, Any]],
    ) -> ToolResponse:
        return orchestrator_postcondition._evaluate_postcondition(
            self.serialized_object, self.reference_resolver,
            postcondition, resource_map=resource_map,
        )

    # ------------------------------------------------------------------
    # Variant inspection
    # ------------------------------------------------------------------

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
        return orchestrator_variant.inspect_variant(
            self.prefab_variant, variant_path, component_filter, show_origin=show_origin,
        )

    def diff_variant(
        self,
        variant_path: str,
        component_filter: str | None = None,
    ) -> ToolResponse:
        """Compare a Variant against its Base, returning only overridden properties.

        Each diff entry pairs the variant value with the base value and includes
        origin annotations showing which Prefab in the chain set the base value.

        Args:
            variant_path: Path to a ``.prefab`` Variant asset.
            component_filter: Optional substring to filter diffs by property path.

        Returns:
            ``ToolResponse`` with ``data.diffs`` listing overridden properties.
        """
        return orchestrator_variant.diff_variant(
            self.prefab_variant, variant_path, component_filter,
        )

    # ------------------------------------------------------------------
    # C# field inspection
    # ------------------------------------------------------------------

    def list_serialized_fields(
        self,
        script_path_or_guid: str,
        include_inherited: bool = False,
    ) -> ToolResponse:
        """List all serialized C# fields for a script.

        Args:
            script_path_or_guid: ``.cs`` file path or 32-char GUID.
            include_inherited: If True, include fields inherited from
                base classes (each annotated with ``source_class``).

        Returns:
            ``ToolResponse`` with ``data.fields`` containing field dicts.
        """
        return orchestrator_fields.list_serialized_fields(
            self.reference_resolver, script_path_or_guid, include_inherited,
        )

    def validate_field_rename(
        self,
        script_path_or_guid: str,
        old_name: str,
        new_name: str,
        scope: str | None = None,
    ) -> ToolResponse:
        """Analyze the impact of renaming a serialized C# field.

        Also detects affected Prefabs/Scenes that reference derived classes
        inheriting the renamed field.

        Args:
            script_path_or_guid: ``.cs`` file path or 32-char GUID.
            old_name: Current field name.
            new_name: Proposed new field name.
            scope: Directory to restrict impact search.

        Returns:
            ``ToolResponse`` with ``data.affected_assets`` and ``data.conflict``.
        """
        return orchestrator_fields.validate_field_rename(
            self.reference_resolver, script_path_or_guid, old_name, new_name, scope,
        )

    def check_field_coverage(
        self,
        scope: str,
    ) -> ToolResponse:
        """Detect unused C# fields or orphaned YAML propertyPaths in scope.

        Resolves inheritance chains so that fields inherited from base
        classes are not falsely reported as orphaned.

        Args:
            scope: Directory or file to scan.

        Returns:
            ``ToolResponse`` with ``data.unused_fields`` and ``data.orphaned_paths``.
        """
        return orchestrator_fields.check_field_coverage(
            self.reference_resolver, scope,
        )

    # ------------------------------------------------------------------
    # Wiring inspection
    # ------------------------------------------------------------------

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
        return orchestrator_wiring.inspect_where_used(
            self.reference_resolver, asset_or_guid, scope, exclude_patterns, max_usages,
        )

    def inspect_wiring(
        self,
        target_path: str,
        *,
        udon_only: bool = False,
        cursor: str = "",
        page_size: int = orchestrator_wiring.INSPECT_WIRING_PAGE_SIZE_DEFAULT,
    ) -> ToolResponse:
        """Analyze MonoBehaviour field wiring in a Prefab or Scene (read-only).

        Args:
            target_path: Path to a ``.prefab`` or ``.unity`` file.
            udon_only: When ``True``, only report UdonSharp components.
            cursor: Opaque continuation token from a previous response;
                empty string requests the first page.
            page_size: Maximum number of merged components to return per
                page; inclusive bounds are
                ``[INSPECT_WIRING_PAGE_SIZE_MIN, INSPECT_WIRING_PAGE_SIZE_MAX]``.

        Returns:
            ``ToolResponse`` with ``data.components`` carrying a single
            page slice of the merged components list, plus
            ``data.next_cursor`` (empty when exhausted) and the
            page-independent diagnostic counts.
        """
        return orchestrator_wiring.inspect_wiring(
            self.prefab_variant, self.reference_resolver, target_path,
            udon_only=udon_only, cursor=cursor, page_size=page_size,
        )

    def validate_all_wiring(
        self,
        *,
        target_path: str = "",
    ) -> ToolResponse:
        """Run inspect_wiring on all .prefab/.unity files in scope.

        Args:
            target_path: Single file to scan. Empty = scan entire scope.

        Returns:
            Aggregated null-reference summary across all scanned files.
        """
        return orchestrator_wiring.validate_all_wiring(
            self.prefab_variant, self.reference_resolver, target_path=target_path,
        )

    # ------------------------------------------------------------------
    # Hierarchy / material inspection
    # ------------------------------------------------------------------

    def inspect_hierarchy(
        self,
        target_path: str,
        *,
        max_depth: int | None = None,
        show_components: bool = True,
        expand_monobehaviour: bool = False,
    ) -> ToolResponse:
        """Build the GameObject/Transform hierarchy tree (read-only).

        Args:
            target_path: Path to a ``.prefab`` or ``.unity`` file.
            max_depth: Limit the tree depth in the text representation.
            show_components: Include component names in tree nodes.
            expand_monobehaviour: Substitute script class names for the
                generic MonoBehaviour label when the script GUID resolves
                through the project GUID index (issue #196).

        Returns:
            ``ToolResponse`` with ``data.tree`` (formatted text) and
            ``data.roots`` (structured hierarchy nodes).
        """
        return orchestrator_inspect.inspect_hierarchy(
            self.prefab_variant,
            target_path,
            max_depth=max_depth,
            show_components=show_components,
            expand_monobehaviour=expand_monobehaviour,
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
        return orchestrator_inspect.inspect_materials(
            self.prefab_variant, target_path,
        )

    def inspect_material_asset(
        self,
        target_path: str,
    ) -> ToolResponse:
        """Inspect shader and properties of a .mat asset file (read-only).

        Unlike ``inspect_materials`` (which inspects material *slots on
        renderers* in .prefab/.unity files), this inspects the .mat asset
        file *itself*.

        Args:
            target_path: Path to a ``.mat`` file.

        Returns:
            ``ToolResponse`` with shader info, property data, and summary
            counts.
        """
        return orchestrator_inspect.inspect_material_asset(
            self.prefab_variant, target_path,
        )

    # ------------------------------------------------------------------
    # Write operations
    # ------------------------------------------------------------------

    def set_material_property(
        self,
        target_path: str,
        property_name: str,
        value: str,
        *,
        dry_run: bool = True,
        change_reason: str | None = None,
    ) -> ToolResponse:
        """Set a single property in a .mat file.

        Args:
            target_path: Path to a .mat file.
            property_name: Property name (e.g. ``_Glossiness``).
            value: New value as string.
            dry_run: If True, preview only.
            change_reason: Required when dry_run=False.

        Returns:
            ``ToolResponse`` with before/after data.
        """
        return orchestrator_write.set_material_property(
            self, target_path, property_name, value,
            dry_run=dry_run, change_reason=change_reason,
        )

    def copy_asset(
        self,
        source_path: str,
        dest_path: str,
        *,
        dry_run: bool = True,
        change_reason: str | None = None,
    ) -> ToolResponse:
        """Copy a Unity text asset with m_Name sync and .meta generation.

        Args:
            source_path: Path to the source asset file.
            dest_path: Path for the new copy.
            dry_run: If True, preview only.
            change_reason: Required when dry_run=False.

        Returns:
            ``ToolResponse`` with copy result data.
        """
        return orchestrator_write.copy_asset(
            self, source_path, dest_path,
            dry_run=dry_run, change_reason=change_reason,
        )

    def rename_asset(
        self,
        asset_path: str,
        new_name: str,
        *,
        dry_run: bool = True,
        change_reason: str | None = None,
    ) -> ToolResponse:
        """Rename a Unity text asset with m_Name sync and .meta rename.

        Args:
            asset_path: Path to the asset file to rename.
            new_name: New filename (with extension).
            dry_run: If True, preview only.
            change_reason: Required when dry_run=False.

        Returns:
            ``ToolResponse`` with rename result data.
        """
        return orchestrator_write.rename_asset(
            self, asset_path, new_name,
            dry_run=dry_run, change_reason=change_reason,
        )

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

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
        return orchestrator_validation.inspect_structure(
            self.prefab_variant, target_path,
        )

    def validate_refs(
        self,
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
        """Scan for broken GUID/fileID references in scope (read-only).

        Args:
            scope: Directory or file path to scan.
            details: When ``True``, include per-reference diagnostics.
            max_diagnostics: Cap on returned diagnostic entries.
            exclude_patterns: Glob patterns for paths to skip.
            ignore_asset_guids: GUIDs to exclude from missing-asset reports.
            top_missing_breakdown: Emit a ``referenced_from`` per-source
                file occurrence list per top missing GUID (issue #198).
            snapshot_save: When non-empty, persist this scan's data
                under the supplied snapshot name (issue #199).
            snapshot_diff: When non-empty, diff this scan against a
                previously saved snapshot of the same name (issue #199).

        Returns:
            ``ToolResponse`` whose ``data.steps[0].result.data`` contains
            ``broken_count``, ``scanned_files``, ``categories``, etc.
        """
        return orchestrator_validation.validate_refs(
            self.reference_resolver, scope, details, max_diagnostics,
            exclude_patterns, ignore_asset_guids,
            top_missing_breakdown=top_missing_breakdown,
            snapshot_save=snapshot_save,
            snapshot_diff=snapshot_diff,
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
        return orchestrator_validation.validate_runtime(
            self.runtime_validation, scene_path, profile, log_file,
            since_timestamp, allow_warnings, max_diagnostics,
        )

    # ------------------------------------------------------------------
    # Patch application
    # ------------------------------------------------------------------

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
        return orchestrator_patch.patch_apply(
            self, plan, dry_run, confirm, plan_sha256, plan_signature,
            change_reason, scope, runtime_scene, runtime_profile,
            runtime_log_file, runtime_since_timestamp, runtime_allow_warnings,
            runtime_max_diagnostics,
        )
