"""MCP tools for asset inspection and validation."""
from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP

from prefab_sentinel.contracts import Severity, ToolResponse, error_response, success_response
from prefab_sentinel.diagnostics_baseline import (
    DiagnosticsBaseline,
    load_diagnostics_baseline,
)
from prefab_sentinel.diagnostics_baseline_update import (
    compute_diagnostics_baseline_update,
    write_diagnostics_baseline,
)
from prefab_sentinel.ignore_guids_io import (
    IGNORE_GUIDS_RELATIVE_PATH,
    load_ignore_guids_file,
    merge_ignore_guids as _merge_ignore_guids,
)
from prefab_sentinel.mcp_validation import require_change_reason
from prefab_sentinel.orchestrator_wiring import INSPECT_WIRING_PAGE_SIZE_DEFAULT
from prefab_sentinel.session import ProjectSession

__all__ = ["register_validation_tools"]



_SUPPORTED_BASELINE_UPDATE_SOURCES = (
    "validate_refs",
    "inspect_wiring",
    "validate_all_wiring",
    "validate_structure",
    "validate_materials",
)
_BASELINE_UPDATE_MODES = ("preview", "write")


def _diagnostics_baseline_project_root_required() -> dict[str, Any]:
    return error_response(
        "DIAGNOSTICS_BASELINE_PROJECT_ROOT_REQUIRED",
        "update_diagnostics_baseline requires an activated project_root.",
        data={"read_only": True},
    ).to_dict()


def _diagnostics_baseline_source_invalid(source: str) -> dict[str, Any]:
    return error_response(
        "DIAGNOSTICS_BASELINE_SOURCE_INVALID",
        "update_diagnostics_baseline source is not supported.",
        data={
            "source": source,
            "supported_sources": list(_SUPPORTED_BASELINE_UPDATE_SOURCES),
        },
    ).to_dict()


def _diagnostics_baseline_mode_invalid(mode: str) -> dict[str, Any] | None:
    if mode in _BASELINE_UPDATE_MODES:
        return None
    return error_response(
        "DIAGNOSTICS_BASELINE_MODE_INVALID",
        "diagnostics baseline update mode must be preview or write.",
        data={"mode": mode},
    ).to_dict()


def _diagnostics_baseline_write_audit_error(
    mode: str,
    confirm: bool,
    change_reason: str,
) -> dict[str, Any] | None:
    if mode != "write":
        return None
    audit_reason = change_reason.strip() if confirm else None
    return require_change_reason(True, audit_reason)


def _validate_refs_with_baseline(
    session: ProjectSession,
    *,
    scope: str,
    details: bool,
    max_diagnostics: int = 200,
    top_missing_breakdown: bool = False,
    snapshot_save: str = "",
    snapshot_diff: str = "",
    refresh_guid_index: bool = False,
    ignore_asset_guids: list[str] | None = None,
    baseline: DiagnosticsBaseline,
) -> dict[str, Any]:
    resolved_scope = session.resolve_scope(scope) or scope
    orch = session.get_orchestrator()
    scope_path = Path(resolved_scope)
    file_entries = load_ignore_guids_file(scope_path)
    merged = _merge_ignore_guids(ignore_asset_guids, file_entries)

    resp = orch.validate_refs(
        scope=resolved_scope,
        details=details,
        max_diagnostics=max_diagnostics,
        top_missing_breakdown=top_missing_breakdown,
        snapshot_save=snapshot_save,
        snapshot_diff=snapshot_diff,
        refresh_guid_index=refresh_guid_index,
        ignore_asset_guids=merged,
        diagnostics_baseline=baseline,
    )
    payload = resp.to_dict()
    if file_entries:
        file_path = scope_path / IGNORE_GUIDS_RELATIVE_PATH
        payload["diagnostics"].append({
            "severity": "info",
            "code": "IGNORE_GUIDS_FILE_LOADED",
            "message": (
                f"Auto-loaded {len(file_entries)} ignore-GUID "
                f"entries from {file_path}."
            ),
            "data": {
                "path": str(file_path),
                "count": len(file_entries),
            },
        })
    return payload

def _run_diagnostics_baseline_source(
    session: ProjectSession,
    *,
    source: str,
    target: str,
    details: bool,
    include_details: bool,
    baseline: DiagnosticsBaseline,
) -> dict[str, Any]:
    if source == "validate_refs":
        return _validate_refs_with_baseline(
            session,
            scope=target,
            details=details,
            baseline=baseline,
        )

    orch = session.get_orchestrator()
    if source == "inspect_wiring":
        inspect_wiring_response: ToolResponse = orch.inspect_wiring(
            target_path=target,
            diagnostics_baseline=baseline,
        )
        return inspect_wiring_response.to_dict()
    if source == "validate_all_wiring":
        validate_all_wiring_response: ToolResponse = orch.validate_all_wiring(
            target_path=target,
            diagnostics_baseline=baseline,
        )
        return validate_all_wiring_response.to_dict()
    if source == "validate_structure":
        validate_structure_response: ToolResponse = orch.inspect_structure(
            target_path=target,
            diagnostics_baseline=baseline,
        )
        return validate_structure_response.to_dict()
    if source == "validate_materials":
        validate_materials_response: ToolResponse = orch.validate_materials(
            scope=target,
            include_details=include_details,
            diagnostics_baseline=baseline,
        )
        return validate_materials_response.to_dict()
    raise AssertionError(f"unsupported diagnostics baseline source: {source}")


def _diagnostics_baseline_source_failed(
    *,
    source: str,
    source_response: Mapping[str, Any],
) -> dict[str, Any]:
    return error_response(
        "DIAGNOSTICS_BASELINE_SOURCE_FAILED",
        "diagnostics baseline source validation failed.",
        data={"source": source, "source_response": dict(source_response)},
    ).to_dict()


def _source_diagnostics_classification(
    *,
    source: str,
    source_response: Mapping[str, Any],
) -> tuple[Mapping[str, object] | None, dict[str, Any] | None]:
    data = source_response.get("data")
    if not isinstance(data, Mapping):
        return None, _source_missing_classification(source)
    classification = data.get("diagnostics_baseline")
    if not isinstance(classification, Mapping):
        return None, _source_missing_classification(source)
    return classification, None


def _source_missing_classification(source: str) -> dict[str, Any]:
    return error_response(
        "DIAGNOSTICS_BASELINE_SOURCE_MISSING_CLASSIFICATION",
        "source response data.diagnostics_baseline is missing or malformed.",
        data={"source": source, "field": "data.diagnostics_baseline"},
    ).to_dict()


def _write_diagnostics_baseline_update(
    *,
    project_root: str | Path,
    update_response: ToolResponse,
) -> dict[str, Any]:
    data = dict(update_response.data)
    write_error = write_diagnostics_baseline(project_root, data["known_diagnostics"])
    if write_error is not None:
        return write_error.to_dict()
    data["written"] = True
    return success_response(
        "DIAGNOSTICS_BASELINE_UPDATE_WRITTEN",
        "Diagnostics baseline updated.",
        data=data,
    ).to_dict()

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
            raise ToolError(f"{step.code}: {step.message}")

        usages = step.data.get("usages", [])
        return {
            "matches": usages,
            "target": asset_or_guid,
            "metadata": {
                "total_count": step.data.get("usage_count", len(usages)),
                "truncated": step.data.get("truncated_usages", 0) > 0,
                "scope": str(resolved_scope) if resolved_scope else None,
                "asset_path": step.data.get("asset_path"),
                "asset_missing": step.data.get("asset_missing", False),
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
        refresh_guid_index: bool = False,
        ignore_asset_guids: list[str] | None = None,
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
            refresh_guid_index: When ``True``, invalidate the project
                session's GUID index cache before scanning so a newly
                created asset is observable on the same call (issue
                #229). The validate-refs failure path also emits a
                stale-cache hint diagnostic when the cached resolver
                reports a missing GUID whose meta file actually exists
                on disk; the hint suggests retrying with this flag.
            ignore_asset_guids: Caller-supplied GUIDs to exclude from
                missing-asset reports (issue #237). The MCP boundary
                additionally auto-loads ``<scope>/config/ignore_guids.txt``
                when present and unions its entries with this list
                before forwarding to the orchestrator; an informational
                diagnostic names the file path and contribution count
                when the file fired.  Malformed entries surface through
                the existing service-layer ``REF001`` envelope.
        """
        baseline_result = load_diagnostics_baseline(session.project_root)
        if baseline_result.error is not None:
            return baseline_result.error.to_dict()
        return _validate_refs_with_baseline(
            session,
            scope=scope,
            details=details,
            max_diagnostics=max_diagnostics,
            top_missing_breakdown=top_missing_breakdown,
            snapshot_save=snapshot_save,
            snapshot_diff=snapshot_diff,
            refresh_guid_index=refresh_guid_index,
            ignore_asset_guids=ignore_asset_guids,
            baseline=baseline_result.baseline,
        )

    @server.tool()
    def validate_materials(
        scope: str | None = None,
        include_details: bool = False,
    ) -> dict[str, Any]:
        """Run static material/shader/TMP/icon-font validation for a scope.

        Args:
            scope: File or directory scope. Uses the activated session scope
                when omitted.
            include_details: Include detailed static evidence when true.
        """
        resolved_scope = None if scope is not None and not scope.strip() else session.resolve_scope(scope)
        if resolved_scope is None:
            return ToolResponse(
                success=False,
                severity=Severity.ERROR,
                code="MATERIAL_VALIDATION_SCOPE_REQUIRED",
                message=(
                    "validate_materials requires an explicit scope or an "
                    "activated session scope."
                ),
                data={"read_only": True, "scope": None},
                diagnostics=[],
            ).to_dict()

        baseline_result = load_diagnostics_baseline(session.project_root)
        if baseline_result.error is not None:
            return baseline_result.error.to_dict()
        orch = session.get_orchestrator()
        return orch.validate_materials(
            scope=resolved_scope,
            include_details=include_details,
            diagnostics_baseline=baseline_result.baseline,
        ).to_dict()

    @server.tool()
    def inspect_wiring(
        asset_path: str,
        udon_only: bool = False,
        cursor: str = "",
        page_size: int = INSPECT_WIRING_PAGE_SIZE_DEFAULT,
        summary_only: bool = False,
        script_filter: str = "",
        include_out_of_scope_diagnostics: bool = False,
    ) -> dict[str, Any]:
        """Analyze MonoBehaviour field wiring in a Prefab or Scene.

        Issue #197: the merged components list (root + nested package
        prefabs) is paginated to keep the response under the MCP token
        cap on packaged scenes. Pass an empty ``cursor`` to request the
        first page; subsequent calls echo back the ``next_cursor`` value
        from the previous response. ``data.next_cursor`` is empty when
        the slice has exhausted the list. ``data.component_count``
        always reports the full merged total; ``data.components``
        carries the current page slice.

        Issue #227: the optional ``summary_only`` flag returns only the
        four diagnostic counts (no slice, no diagnostics) so callers can
        keep the response under the token cap on very large prefabs.
        The optional ``script_filter`` flag narrows the merged
        component list to entries whose recorded script class matches
        the supplied identifier; bare class names and dotted
        fully-qualified names both work. A non-empty filter that
        matches no components returns
        ``INSPECT_WIRING_EMPTY_FILTER_RESULT`` with ``severity=warning``.

        Args:
            asset_path: Asset file path (.prefab, .unity).
            udon_only: Only inspect UdonSharp components.
            cursor: Opaque continuation token from a previous response.
            page_size: Maximum components per page (inclusive bounds
                ``[1, 500]``; default ``50``).
            summary_only: Return only diagnostic counts.
            script_filter: Class-name filter (bare or dotted).
            include_out_of_scope_diagnostics: Include diagnostic rows
                for components outside ``script_filter``.
        """
        baseline_result = load_diagnostics_baseline(session.project_root)
        if baseline_result.error is not None:
            return baseline_result.error.to_dict()
        orch = session.get_orchestrator()
        resp = orch.inspect_wiring(
            target_path=asset_path,
            udon_only=udon_only,
            cursor=cursor,
            page_size=page_size,
            summary_only=summary_only,
            script_filter=script_filter,
            include_out_of_scope_diagnostics=include_out_of_scope_diagnostics,
            diagnostics_baseline=baseline_result.baseline,
        )
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
            script_or_guid: .cs file path, class name (e.g. "PlayerController"),
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
        baseline_result = load_diagnostics_baseline(session.project_root)
        if baseline_result.error is not None:
            return baseline_result.error.to_dict()
        orch = session.get_orchestrator()
        resp = orch.inspect_structure(
            target_path=asset_path,
            diagnostics_baseline=baseline_result.baseline,
        )
        return resp.to_dict()

    @server.tool()
    def inspect_hierarchy(
        asset_path: str,
        depth: int | None = None,
        show_components: bool = True,
        expand_monobehaviour: bool = False,
        expand_prefab_instances: bool = False,
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
            expand_prefab_instances: Expand nested PrefabInstance source
                children into the saved-YAML effective hierarchy (issue #96,
                default: False).
        """
        orch = session.get_orchestrator()
        resp = orch.inspect_hierarchy(
            target_path=asset_path,
            max_depth=depth,
            show_components=show_components,
            expand_monobehaviour=expand_monobehaviour,
            expand_prefab_instances=expand_prefab_instances,
        )
        return resp.to_dict()


    @server.tool()
    def inspect_transform_effective_values(
        asset_path: str,
        symbol_path: str,
    ) -> dict[str, Any]:
        """Inspect Transform default, override, and effective values."""
        orch = session.get_orchestrator()
        resp = orch.inspect_transform_effective_values(
            asset_path=asset_path,
            symbol_path=symbol_path,
        )
        return resp.to_dict()


    @server.tool()
    def inspect_unity_event_listeners(
        asset_path: str,
        symbol_path: str,
        component_type: str,
        property_name: str,
    ) -> dict[str, Any]:
        """Inspect supported uGUI UnityEvent persistent listeners."""
        orch = session.get_orchestrator()
        resp = orch.inspect_unity_event_listeners(
            asset_path=asset_path,
            symbol_path=symbol_path,
            component_type=component_type,
            property_name=property_name,
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
        baseline_result = load_diagnostics_baseline(session.project_root)
        if baseline_result.error is not None:
            return baseline_result.error.to_dict()
        orch = session.get_orchestrator()
        return orch.validate_all_wiring(
            target_path=asset_path,
            diagnostics_baseline=baseline_result.baseline,
        ).to_dict()

    @server.tool()
    def update_diagnostics_baseline(
        source: str,
        target: str,
        mode: str = "preview",
        prune_resolved: bool = False,
        confirm: bool = False,
        change_reason: str = "",
        details: bool = False,
        include_details: bool = False,
    ) -> dict[str, Any]:
        """Preview or write the project diagnostics baseline from a source scan."""
        project_root = session.project_root
        if project_root is None:
            return _diagnostics_baseline_project_root_required()
        if source not in _SUPPORTED_BASELINE_UPDATE_SOURCES:
            return _diagnostics_baseline_source_invalid(source)
        mode_error = _diagnostics_baseline_mode_invalid(mode)
        if mode_error is not None:
            return mode_error
        audit_error = _diagnostics_baseline_write_audit_error(
            mode,
            confirm,
            change_reason,
        )
        if audit_error is not None:
            return audit_error

        baseline_result = load_diagnostics_baseline(project_root)
        if baseline_result.error is not None:
            return baseline_result.error.to_dict()
        baseline = baseline_result.baseline
        source_response = _run_diagnostics_baseline_source(
            session,
            source=source,
            target=target,
            details=details,
            include_details=include_details,
            baseline=baseline,
        )
        if not source_response["success"]:
            return _diagnostics_baseline_source_failed(
                source=source,
                source_response=source_response,
            )
        classification, classification_error = _source_diagnostics_classification(
            source=source,
            source_response=source_response,
        )
        if classification_error is not None:
            return classification_error
        if classification is None:
            return _source_missing_classification(source)

        update_response = compute_diagnostics_baseline_update(
            baseline=baseline,
            classification=classification,
            mode=mode,
            prune_resolved=prune_resolved,
        )
        if not update_response.success:
            return update_response.to_dict()
        if mode != "write":
            return update_response.to_dict()
        return _write_diagnostics_baseline_update(
            project_root=project_root,
            update_response=update_response,
        )

    @server.tool()
    def validate_runtime(
        asset_path: str,
        profile: str = "compile_only",
        log_file: str | None = None,
        since_timestamp: str | None = None,
        allow_warnings: bool = False,
        max_diagnostics: int = 200,
        confirm: bool = False,
        change_reason: str | None = None,
        allow_dirty_before_clientsim: bool = False,
    ) -> dict[str, Any]:
        orch = session.get_orchestrator()
        resp = orch.validate_runtime(
            scene_path=asset_path,
            profile=profile,
            log_file=log_file,
            since_timestamp=since_timestamp,
            allow_warnings=allow_warnings,
            max_diagnostics=max_diagnostics,
            confirm=confirm,
            change_reason=change_reason,
            allow_dirty_before_clientsim=allow_dirty_before_clientsim,
        )
        return resp.to_dict()
