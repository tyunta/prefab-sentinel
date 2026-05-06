"""MCP tools for asset inspection and validation."""
from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP

from prefab_sentinel.session import ProjectSession

__all__ = ["register_validation_tools"]


def register_validation_tools(server: FastMCP, session: ProjectSession) -> None:
    """Register inspection and validation tools on *server*."""

    @server.tool()
    def find_referencing_assets(
        asset_or_guid: str,
        scope: str | None = None,
        max_results: int = 100,
    ) -> dict[str, Any]:
        """Find all assets that reference a given asset path or GUID.

        Args:
            asset_or_guid: Asset path or 32-char GUID to search for.
            scope: Directory to restrict search scope.
            max_results: Maximum number of results to return.
        """
        orch = session.get_orchestrator()
        resolved_scope = session.resolve_scope(scope)
        step = orch.reference_resolver.where_used(
            asset_or_guid=asset_or_guid,
            scope=resolved_scope,
            max_usages=max_results,
        )
        if not step.success:
            from mcp.server.fastmcp.exceptions import ToolError
            raise ToolError(step.message)

        usages = step.data.get("usages", [])
        return {
            "matches": usages,
            "target": asset_or_guid,
            "metadata": {
                "total_count": step.data.get("usage_count", len(usages)),
                "truncated": step.data.get("truncated_usages", 0) > 0,
                "scope": str(resolved_scope) if resolved_scope else None,
            },
        }

    @server.tool()
    def validate_refs(
        scope: str,
        details: bool = False,
        max_diagnostics: int = 200,
        top_missing_breakdown: bool = False,
        snapshot_save: str = "",
        snapshot_diff: str = "",
    ) -> dict[str, Any]:
        """Scan for broken GUID/fileID references in a Unity project scope.

        Args:
            scope: Directory or file path to scan.
            details: Include per-reference diagnostics.
            max_diagnostics: Cap on the number of diagnostics returned.
            top_missing_breakdown: Emit a per-source-file occurrence
                breakdown for each top missing GUID (issue #198).
            snapshot_save: When non-empty, persist this scan's result
                under the supplied snapshot name (issue #199).
            snapshot_diff: When non-empty, diff this scan against the
                snapshot of the same name (issue #199).
        """
        orch = session.get_orchestrator()
        resolved_scope = session.resolve_scope(scope) or scope
        resp = orch.validate_refs(
            scope=resolved_scope,
            details=details,
            max_diagnostics=max_diagnostics,
            top_missing_breakdown=top_missing_breakdown,
            snapshot_save=snapshot_save,
            snapshot_diff=snapshot_diff,
        )
        return resp.to_dict()

    @server.tool()
    def inspect_wiring(
        asset_path: str,
        udon_only: bool = False,
    ) -> dict[str, Any]:
        """Analyze MonoBehaviour field wiring in a Prefab or Scene.

        Args:
            asset_path: Asset file path (.prefab, .unity).
            udon_only: Only inspect UdonSharp components.
        """
        orch = session.get_orchestrator()
        resp = orch.inspect_wiring(target_path=asset_path, udon_only=udon_only)
        return resp.to_dict()

    @server.tool()
    def inspect_variant(
        asset_path: str,
        component_filter: str | None = None,
        show_origin: bool = False,
    ) -> dict[str, Any]:
        """Inspect a Prefab Variant's override chain and effective values.

        Args:
            asset_path: Variant prefab file path.
            component_filter: Filter overrides by component substring.
            show_origin: Show which Prefab in the chain set each value.
        """
        orch = session.get_orchestrator()
        resp = orch.inspect_variant(
            variant_path=asset_path,
            component_filter=component_filter,
            show_origin=show_origin,
        )
        return resp.to_dict()

    @server.tool()
    def diff_unity_symbols(
        asset_path: str,
        component_filter: str | None = None,
    ) -> dict[str, Any]:
        """Show only the differences between a Variant and its Base.

        Returns overridden properties with both variant and base values,
        plus origin annotations showing which Prefab in the chain set each value.

        Args:
            asset_path: Variant prefab file path.
            component_filter: Filter diffs by property path substring.
        """
        orch = session.get_orchestrator()
        resp = orch.diff_variant(
            variant_path=asset_path,
            component_filter=component_filter,
        )
        return resp.to_dict()

    @server.tool()
    def list_serialized_fields(
        script_or_guid: str,
        include_inherited: bool = False,
    ) -> dict[str, Any]:
        """List serialized C# fields for a Unity script.

        Parses the C# source to extract fields that Unity will serialize,
        enabling field coverage checks and rename impact analysis.

        Args:
            script_or_guid: .cs file path, class name (e.g. "NadeSharePuppetSpec"),
                or 32-char GUID string. Class name resolution requires an active project.
            include_inherited: If true, include fields from base classes
                (each annotated with source_class).
        """
        orch = session.get_orchestrator()
        resp = orch.list_serialized_fields(
            script_path_or_guid=script_or_guid,
            include_inherited=include_inherited,
        )
        return resp.to_dict()

    @server.tool()
    def validate_field_rename(
        script_or_guid: str,
        old_name: str,
        new_name: str,
        scope: str | None = None,
    ) -> dict[str, Any]:
        """Analyze the impact of renaming a serialized C# field (read-only).

        Scans YAML assets for MonoBehaviours using the script and reports
        which assets reference the field. Does NOT apply any changes.

        Args:
            script_or_guid: .cs file path, class name, or 32-char GUID string.
                Class name resolution requires an active project.
            old_name: Current field name to rename.
            new_name: Proposed new field name.
            scope: Directory to restrict impact search (default: project root).
        """
        orch = session.get_orchestrator()
        resolved_scope = session.resolve_scope(scope)
        resp = orch.validate_field_rename(
            script_path_or_guid=script_or_guid,
            old_name=old_name,
            new_name=new_name,
            scope=resolved_scope,
        )
        return resp.to_dict()

    @server.tool()
    def check_field_coverage(
        scope: str,
    ) -> dict[str, Any]:
        """Detect unused C# fields or orphaned YAML propertyPaths in scope.

        Compares serialized C# field definitions against YAML MonoBehaviour
        data to find mismatches: fields defined in code but absent in assets
        (unused), or fields present in assets but absent in code (orphaned).

        Args:
            scope: Directory or file path to scan.
        """
        orch = session.get_orchestrator()
        resolved_scope = session.resolve_scope(scope) or scope
        resp = orch.check_field_coverage(scope=resolved_scope)
        return resp.to_dict()

    @server.tool()
    def inspect_materials(asset_path: str) -> dict[str, Any]:
        """Show per-renderer material slot assignments with override/inherited markers.

        Args:
            asset_path: Path to a .prefab or .unity file.
        """
        orch = session.get_orchestrator()
        resp = orch.inspect_materials(target_path=asset_path)
        return resp.to_dict()

    @server.tool()
    def inspect_material_asset(asset_path: str) -> dict[str, Any]:
        """Inspect shader, properties, and texture references in a .mat file.

        Args:
            asset_path: Path to a .mat file.
        """
        orch = session.get_orchestrator()
        resp = orch.inspect_material_asset(target_path=asset_path)
        return resp.to_dict()

    @server.tool()
    def validate_structure(asset_path: str) -> dict[str, Any]:
        """Validate internal YAML structure (fileID duplicates, Transform consistency).

        Args:
            asset_path: Path to a .prefab, .unity, or .asset file.
        """
        orch = session.get_orchestrator()
        resp = orch.inspect_structure(target_path=asset_path)
        return resp.to_dict()

    @server.tool()
    def inspect_hierarchy(
        asset_path: str,
        depth: int | None = None,
        show_components: bool = True,
        expand_monobehaviour: bool = False,
    ) -> dict[str, Any]:
        """Display the GameObject hierarchy tree of a Unity asset.

        Args:
            asset_path: Path to a .prefab or .unity file.
            depth: Maximum tree depth to display (None = unlimited).
            show_components: Show component annotations (default: True).
            expand_monobehaviour: Substitute script class names for the
                generic ``MonoBehaviour`` label by resolving each
                component's script GUID through the project GUID index
                (issue #196, default: False).
        """
        orch = session.get_orchestrator()
        resp = orch.inspect_hierarchy(
            target_path=asset_path,
            max_depth=depth,
            show_components=show_components,
            expand_monobehaviour=expand_monobehaviour,
        )
        return resp.to_dict()

    @server.tool()
    def validate_all_wiring(
        asset_path: str = "",
    ) -> dict[str, Any]:
        """Scan all .prefab/.unity files in scope for null references.

        Aggregates inspect_wiring results across the entire scope (or a single file).
        Returns a summary with total component count, null reference count,
        and per-file breakdown.

        Args:
            asset_path: Single .unity/.prefab file to scan. Empty = scan entire scope.
        """
        orch = session.get_orchestrator()
        return orch.validate_all_wiring(target_path=asset_path).to_dict()

    @server.tool()
    def validate_runtime(
        asset_path: str,
        profile: str = "default",
        log_file: str | None = None,
        since_timestamp: str | None = None,
        allow_warnings: bool = False,
        max_diagnostics: int = 200,
    ) -> dict[str, Any]:
        """Run runtime validation: UdonSharp compile + ClientSim execution.

        Args:
            asset_path: Target Unity scene path.
            profile: Runtime profile label for ClientSim execution context.
            log_file: Unity log file path (default: <project>/Logs/Editor.log).
            since_timestamp: Log cursor label for filtering.
            allow_warnings: Treat warning-only findings as pass.
            max_diagnostics: Maximum diagnostics to include (default: 200).
        """
        orch = session.get_orchestrator()
        resp = orch.validate_runtime(
            scene_path=asset_path,
            profile=profile,
            log_file=log_file,
            since_timestamp=since_timestamp,
            allow_warnings=allow_warnings,
            max_diagnostics=max_diagnostics,
        )
        return resp.to_dict()
