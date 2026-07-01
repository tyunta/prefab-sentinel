"""Wiring inspection functions extracted from Phase1Orchestrator."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import cast

from prefab_sentinel.contracts import (
    Diagnostic,
    Severity,
    ToolResponse,
    error_response,
    success_response,
)
from prefab_sentinel.diagnostics_baseline import (
    DiagnosticKeyRecord,
    DiagnosticsBaseline,
    classify_current_keys,
)
from prefab_sentinel.orchestrator_variant import read_target_file, resolve_variant_base
from prefab_sentinel.services.prefab_variant import PrefabVariantService
from prefab_sentinel.services.reference_resolver import ReferenceResolverService
from prefab_sentinel.udon_wiring import ComponentWiring, WiringResult, analyze_wiring
from prefab_sentinel.unity_assets import (
    GAMEOBJECT_BEARING_SUFFIXES,
    collect_project_guid_index,
)
from prefab_sentinel.unity_yaml_parser import iter_nested_prefab_children

# Issue #197: pagination contract for inspect_wiring.
#
# Packaged scenes (e.g. a video-player world prefab + VVMW package) merge into a
# components list large enough to overflow the MCP token cap (65,859 chars
# was observed). We expose the merged list one page at a time via an
# opaque continuation token, mirroring the convention established by the
# console-capture surface (``ConsoleCursorPrefix = "seq:"``). The default
# page size and the inclusive bounds are pinned here as load-bearing
# constants so tests in ``tests/test_default_parameter_boundaries.py`` can
# anchor mutation testing on the literals.
INSPECT_WIRING_CURSOR_PREFIX = "pos:"
INSPECT_WIRING_PAGE_SIZE_DEFAULT = 50
INSPECT_WIRING_PAGE_SIZE_MIN = 1
INSPECT_WIRING_PAGE_SIZE_MAX = 500

# ------------------------------------------------------------------
# Module-level helpers (no self)
# ------------------------------------------------------------------


def _normalize_script_filter(value: str) -> str:
    """Issue #227 — normalise a caller-supplied script-class identifier.

    A bare class name is returned as-is; a dotted fully-qualified name
    is reduced to the suffix after the last dot. The merged-component
    list carries ``script_name`` as the .cs file stem (Unity convention
    for MonoBehaviour script names), so the normalised filter compares
    by exact equality against that stem regardless of which shape the
    caller supplied.
    """
    if not value:
        return value
    last_dot = value.rfind(".")
    if last_dot < 0:
        return value
    return value[last_dot + 1:]


def _relative_path(path: Path, root: Path) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


def _component_to_dict(
    comp: ComponentWiring,
    go_name: str,
    guid_to_name: dict[str, str],
    *,
    source_prefab: str | None = None,
) -> dict[str, object]:
    field_dicts = []
    for f in comp.fields:
        fd: dict[str, object] = {
            "name": f.name, "file_id": f.file_id,
            "guid": f.guid, "line": f.line,
        }
        if f.is_overridden:
            fd["is_overridden"] = True
        field_dicts.append(fd)
    # Issue #296: surface the per-field classification list under the
    # documented response key alongside the legacy flat list of names.
    null_field_classifications = [
        {
            "name": entry.name,
            "kind": entry.kind,
            "evidence": entry.evidence,
        }
        for entry in comp.null_field_classifications
    ]
    cd: dict[str, object] = {
        "file_id": comp.file_id,
        "game_object_file_id": comp.game_object_file_id,
        "game_object_name": go_name,
        "script_guid": comp.script_guid,
        "script_name": guid_to_name.get(comp.script_guid, ""),
        "is_udon_sharp": comp.is_udon_sharp,
        "field_count": len(comp.fields),
        "null_ratio": f"{len(comp.null_field_names)}/{len(comp.fields)}",
        "null_field_names": comp.null_field_names,
        "null_field_classifications": null_field_classifications,
        "fields": field_dicts,
    }
    if source_prefab is not None:
        cd["source_prefab"] = source_prefab
    if comp.override_count > 0:
        cd["override_count"] = comp.override_count
    return cd


def _collect_nested_wiring_components(
    text: str,
    *,
    udon_only: bool,
    guid_index: dict[str, Path],
    project_root: Path,
    guid_to_name: dict[str, str],
) -> tuple[list[dict[str, object]], list[WiringResult]]:
    components: list[dict[str, object]] = []
    nested_results: list[WiringResult] = []

    for child in iter_nested_prefab_children(text, guid_index, project_root):
        child_result = analyze_wiring(
            child.text, str(child.path), udon_only=udon_only,
        )
        nested_results.append(child_result)

        child_gos = child_result.game_objects
        for comp in child_result.components:
            go = child_gos.get(comp.game_object_file_id)
            go_name = go.name if go and go.name else ""
            components.append(
                _component_to_dict(comp, go_name, guid_to_name, source_prefab=child.rel_posix)
            )

    return components, nested_results


# ------------------------------------------------------------------
# Extracted methods
# ------------------------------------------------------------------


def inspect_where_used(
    reference_resolver: ReferenceResolverService,
    asset_or_guid: str,
    scope: str | None = None,
    exclude_patterns: tuple[str, ...] = (),
    max_usages: int = 500,
) -> ToolResponse:
    step = reference_resolver.where_used(
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


def _parse_inspect_wiring_cursor(
    cursor: str, total: int,
) -> int | ToolResponse:
    """Resolve the opaque continuation token to an integer offset.

    An empty cursor maps to position 0 (fresh page). A non-empty cursor
    must start with ``INSPECT_WIRING_CURSOR_PREFIX``; the body must
    parse as a non-negative integer in ``[0, total]``. ``total`` itself
    is a valid terminal position that yields a zero-length slice for
    callers that request one extra page after exhaustion.
    """
    if cursor == "":
        return 0
    if not cursor.startswith(INSPECT_WIRING_CURSOR_PREFIX):
        return error_response(
            "INSPECT_WIRING_INVALID_CURSOR",
            f"cursor token {cursor!r} must start with "
            f"'{INSPECT_WIRING_CURSOR_PREFIX}' (opaque continuation "
            f"token from a previous response).",
        )
    body = cursor[len(INSPECT_WIRING_CURSOR_PREFIX):]
    try:
        position = int(body)
    except ValueError:
        return error_response(
            "INSPECT_WIRING_INVALID_CURSOR",
            f"cursor token {cursor!r} could not be parsed as a "
            f"page offset.",
        )
    if position < 0 or position > total:
        return error_response(
            "INSPECT_WIRING_INVALID_CURSOR",
            f"cursor token {cursor!r} references position {position} "
            f"outside the merged components range [0, {total}].",
        )
    return position


def _diagnostic_wire_rows(
    diagnostics: list[Diagnostic],
    default_severity: Severity,
) -> list[dict[str, object]]:
    wire_diagnostics = ToolResponse(
        success=True,
        severity=default_severity,
        code="INSPECT_WIRING_DIAGNOSTICS",
        message="diagnostics",
        data={},
        diagnostics=diagnostics,
    ).to_dict()["diagnostics"]
    return cast(list[dict[str, object]], wire_diagnostics)


def _diagnostic_severity(diag: Diagnostic, default_severity: Severity) -> str:
    if diag.severity is not None:
        return diag.severity
    if diag.detail.startswith("Null reference: "):
        return Severity.WARNING.value
    if diag.detail.startswith("Internal fileID not found: "):
        return Severity.ERROR.value
    if diag.detail.startswith("[same-component] "):
        return Severity.WARNING.value
    if diag.detail.startswith("[cross-component] "):
        return Severity.INFO.value
    return default_severity.value


def _diagnostic_counts(
    diagnostics: list[Diagnostic],
    default_severity: Severity,
) -> dict[str, int]:
    counts = {"total": len(diagnostics), "info": 0, "warning": 0, "error": 0, "critical": 0}
    for diag in diagnostics:
        severity = _diagnostic_severity(diag, default_severity)
        if severity in counts:
            counts[severity] += 1
    return counts


def _max_diagnostic_severity(
    diagnostics: list[Diagnostic],
    default_severity: Severity,
) -> Severity:
    if not diagnostics:
        return Severity.INFO
    rank = {
        Severity.INFO.value: 0,
        Severity.WARNING.value: 1,
        Severity.ERROR.value: 2,
        Severity.CRITICAL.value: 3,
    }
    worst = max(
        (_diagnostic_severity(diag, default_severity) for diag in diagnostics),
        key=lambda severity: rank.get(severity, 0),
    )
    return Severity(worst) if worst in rank else Severity.INFO


def _copy_diagnostics_with_default_severity(
    diagnostics: list[Diagnostic],
    default_severity: Severity,
) -> list[Diagnostic]:
    return [
        Diagnostic(
            path=diag.path,
            location=diag.location,
            detail=diag.detail,
            evidence=diag.evidence,
            severity=_diagnostic_severity(diag, default_severity),
        )
        for diag in diagnostics
    ]


def _component_field_names(component: dict[str, object]) -> set[str]:
    fields = component.get("fields", [])
    if not isinstance(fields, list):
        return set()
    return {str(field["name"]) for field in fields if isinstance(field, dict) and "name" in field}


def _match_component_diagnostic(
    diag: Diagnostic,
    components: list[dict[str, object]],
    target_path: str,
) -> tuple[dict[str, object], str, str] | None:
    prefixes = (
        ("Null reference: ", "null_reference"),
        ("Internal fileID not found: ", "internal_broken_ref"),
    )
    for prefix, kind in prefixes:
        if not diag.detail.startswith(prefix):
            continue
        remainder = diag.detail[len(prefix):]
        owner, _, field_name = remainder.partition(".")
        field_name = field_name.split(" ", 1)[0]
        for component in components:
            if component.get("game_object_name") != owner:
                continue
            if field_name in _component_field_names(component):
                return component, kind, field_name
    for component in components:
        file_id = str(component.get("file_id", ""))
        if file_id and f"fileID:{file_id}." in diag.detail:
            branch = ""
            if diag.detail.startswith("["):
                label, separator, _ = diag.detail.partition("]")
                if separator:
                    branch = label.removeprefix("[")
            target = diag.location or diag.evidence
            field_name = f"{branch}:{target}" if branch and target else target
            return component, "duplicate_reference", field_name
    return None


def _diagnostic_key_for_component(
    target_path: str,
    component: dict[str, object],
    kind: str,
    field_name: str,
) -> str:
    source = str(component.get("source_prefab") or target_path)
    return f"inspect_wiring:{kind}:{source}:{component.get('file_id')}:{field_name}"


def _partition_component_diagnostics(
    diagnostics: list[Diagnostic],
    components: list[dict[str, object]],
    target_path: str,
    default_severity: Severity,
) -> tuple[list[Diagnostic], list[Diagnostic], tuple[DiagnosticKeyRecord, ...]]:
    filtered: list[Diagnostic] = []
    out_of_scope: list[Diagnostic] = []
    records: list[DiagnosticKeyRecord] = []
    for diag in diagnostics:
        match = _match_component_diagnostic(diag, components, target_path)
        if match is None:
            out_of_scope.append(diag)
            continue
        component, kind, field_name = match
        filtered.append(diag)
        records.append(
            DiagnosticKeyRecord(
                key=_diagnostic_key_for_component(target_path, component, kind, field_name),
                severity=_diagnostic_severity(diag, default_severity),
                message=diag.detail,
                data={
                    "category": kind,
                    "target_path": target_path,
                    "source_prefab": component.get("source_prefab", ""),
                    "component_file_id": str(component.get("file_id", "")),
                    "field_name": field_name,
                },
            )
        )
    return filtered, out_of_scope, tuple(records)

def inspect_wiring(
    prefab_variant: PrefabVariantService,
    reference_resolver: ReferenceResolverService,
    target_path: str,
    *,
    udon_only: bool = False,
    cursor: str = "",
    page_size: int = INSPECT_WIRING_PAGE_SIZE_DEFAULT,
    summary_only: bool = False,
    script_filter: str = "",
    include_out_of_scope_diagnostics: bool = False,
    diagnostics_baseline: DiagnosticsBaseline | None = None,
) -> ToolResponse:
    # Issue #197: validate page_size before any I/O so a misconfigured
    # caller short-circuits before the YAML scan.
    if page_size < INSPECT_WIRING_PAGE_SIZE_MIN or page_size > INSPECT_WIRING_PAGE_SIZE_MAX:
        return error_response(
            "INSPECT_WIRING_PAGE_SIZE_OUT_OF_RANGE",
            f"page_size={page_size} is outside the inclusive range "
            f"[{INSPECT_WIRING_PAGE_SIZE_MIN}, "
            f"{INSPECT_WIRING_PAGE_SIZE_MAX}].",
        )

    text_or_error = read_target_file(prefab_variant, target_path, "INSPECT_WIRING")
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

    text, is_variant, base_prefab_path, chain_diags = resolve_variant_base(
        prefab_variant, text, target_path, "INSPECT_WIRING",
    )
    override_map: dict[str, set[str]] | None = None
    diagnostics: list[Diagnostic] = list(chain_diags)

    if is_variant:
        ov_resp = prefab_variant.list_overrides(target_path)
        if ov_resp.success:
            omap: dict[str, set[str]] = {}
            for ov in ov_resp.data.get("overrides", []):
                fid = ov.get("target_file_id", "")
                pp = ov.get("property_path", "")
                if fid and pp:
                    omap.setdefault(fid, set()).add(pp)
            override_map = omap
        diagnostics.extend(ov_resp.diagnostics)

    result = analyze_wiring(
        text, target_path, udon_only=udon_only, override_map=override_map,
    )
    diagnostics.extend(
        result.null_references
        + result.internal_broken_refs
        + result.duplicate_references
    )

    proj_root: Path | None = None
    guid_index: dict[str, Path] = {}
    guid_to_name: dict[str, str] = {}
    try:
        proj_root = prefab_variant.project_root
        guid_index = collect_project_guid_index(proj_root, include_package_cache=False)
        for guid, asset_path in guid_index.items():
            if asset_path.suffix == ".cs":
                guid_to_name[guid] = asset_path.stem
    except Exception as exc:
        logging.getLogger(__name__).debug("GUID index build failed (best-effort): %s", exc)

    component_summaries: list[dict[str, object]] = []
    for comp in result.components:
        go = result.game_objects.get(comp.game_object_file_id)
        go_name = go.name if go and go.name else ""
        component_summaries.append(
            _component_to_dict(comp, go_name, guid_to_name)
        )

    nested_null_refs: list[Diagnostic] = []
    nested_broken_refs: list[Diagnostic] = []
    nested_dup_refs: list[Diagnostic] = []
    if proj_root is not None and guid_index:
        nested_components, nested_results = _collect_nested_wiring_components(
            text,
            udon_only=udon_only,
            guid_index=guid_index,
            project_root=proj_root,
            guid_to_name=guid_to_name,
        )
        component_summaries.extend(nested_components)
        for nr in nested_results:
            nested_null_refs.extend(nr.null_references)
            nested_broken_refs.extend(nr.internal_broken_refs)
            nested_dup_refs.extend(nr.duplicate_references)
            diagnostics.extend(
                _copy_diagnostics_with_default_severity(
                    nr.null_references
                    + nr.internal_broken_refs
                    + nr.duplicate_references,
                    nr.max_severity,
                )
            )

    # Default total counts before the optional script-class filter so the
    # filter logic can reduce them in-place against the filtered subset.
    null_reference_count = (
        len(result.null_references) + len(nested_null_refs)
    )
    internal_broken_ref_count = (
        len(result.internal_broken_refs) + len(nested_broken_refs)
    )
    duplicate_reference_count = (
        len(result.duplicate_references) + len(nested_dup_refs)
    )

    response_default_severity = _max_diagnostic_severity(
        diagnostics,
        result.max_severity,
    )
    success = response_default_severity not in (Severity.ERROR, Severity.CRITICAL)
    filtered_diagnostics = list(diagnostics)
    out_of_scope_diagnostics: list[Diagnostic] = []
    diagnostic_key_records: tuple[DiagnosticKeyRecord, ...] = ()
    diagnostic_counts: dict[str, dict[str, int]] | None = None
    response_success = success
    response_severity = response_default_severity
    response_diagnostics = diagnostics
    filtered_default_severity = response_default_severity
    out_of_scope_default_severity = response_default_severity

    # Issue #227: when a script-class filter is supplied, narrow the
    # merged component list to those whose recorded ``script_name`` (the
    # .cs file stem) matches the normalised filter. Counts are
    # recomputed against the filtered subset so summary mode and full
    # mode stay consistent with each other under the same filter. A
    # non-empty filter that matches no components surfaces as a
    # distinct warning so the caller can tell "filter spelled wrong"
    # apart from "target has no MonoBehaviours".
    normalized_filter = _normalize_script_filter(script_filter)
    filter_active = bool(normalized_filter)
    if filter_active:
        filtered = [
            cd for cd in component_summaries
            if cd.get("script_name") == normalized_filter
        ]
        if not filtered:
            return ToolResponse(
                success=True,
                severity=Severity.WARNING,
                code="INSPECT_WIRING_EMPTY_FILTER_RESULT",
                message=(
                    f"inspect.wiring script filter {script_filter!r} "
                    f"(normalised to {normalized_filter!r}) matched no "
                    f"components on the merged list."
                ),
                data={
                    "target_path": target_path,
                    "udon_only": udon_only,
                    "read_only": True,
                    "script_filter": script_filter,
                    "summary_only": summary_only,
                    "component_count": 0,
                    "null_reference_count": 0,
                    "internal_broken_ref_count": 0,
                    "duplicate_reference_count": 0,
                    "diagnostic_counts": {
                        "filtered": _diagnostic_counts([], Severity.INFO),
                        "out_of_scope": _diagnostic_counts([], Severity.INFO),
                    },
                },
                diagnostics=diagnostics,
            )
        component_summaries = filtered
        # Recompute counts against the filtered subset.
        null_reference_count = sum(
            len(names) if isinstance(names := cd.get("null_field_names"), list) else 0
            for cd in filtered
        )
        survivor_go_names = {
            cd.get("game_object_name", "") for cd in filtered
        }
        survivor_file_ids = {
            cd.get("file_id", "") for cd in filtered
        }
        # The internal-broken-ref diagnostic embeds the source GameObject
        # name in its ``detail`` field after a fixed prefix and before a
        # ``.``-separated field name (``udon_wiring.py:215`` constructs
        # ``Internal fileID not found: {go_name}.{field_name} -> ...``).
        # The duplicate-ref diagnostic embeds source ``fileID:<cid>``
        # tokens. Anchored matching keeps "Controller" from being
        # over-matched against a diagnostic for "SubController".
        all_internal_broken = (
            list(result.internal_broken_refs) + nested_broken_refs
        )
        internal_broken_ref_count = sum(
            1 for d in all_internal_broken
            if any(
                name and d.detail.startswith(
                    f"Internal fileID not found: {name}."
                )
                for name in survivor_go_names
            )
        )
        all_duplicates = (
            list(result.duplicate_references) + nested_dup_refs
        )
        duplicate_reference_count = sum(
            1 for d in all_duplicates
            if any(
                fid and f"fileID:{fid}." in d.detail
                for fid in survivor_file_ids
            )
        )
        (
            filtered_diagnostics,
            out_of_scope_diagnostics,
            diagnostic_key_records,
        ) = _partition_component_diagnostics(
            diagnostics,
            filtered,
            target_path,
            Severity.INFO,
        )
        filtered_default_severity = _max_diagnostic_severity(
            filtered_diagnostics,
            Severity.INFO,
        )
        out_of_scope_default_severity = _max_diagnostic_severity(
            out_of_scope_diagnostics,
            Severity.INFO,
        )
        diagnostic_counts = {
            "filtered": _diagnostic_counts(filtered_diagnostics, filtered_default_severity),
            "out_of_scope": _diagnostic_counts(
                out_of_scope_diagnostics,
                out_of_scope_default_severity,
            ),
        }
        response_severity = filtered_default_severity
        response_success = response_severity not in (Severity.ERROR, Severity.CRITICAL)
        response_diagnostics = _copy_diagnostics_with_default_severity(
            diagnostics,
            response_default_severity,
        )
    elif diagnostics_baseline is not None:
        _, _, diagnostic_key_records = _partition_component_diagnostics(
            diagnostics,
            component_summaries,
            target_path,
            response_default_severity,
        )

    # Issue #227: summary mode shapes the response to keep it under the
    # MCP token cap by suppressing the per-component slice and the
    # per-reference diagnostic list. The four diagnostic counts remain
    # so callers can judge severity without paginating.
    if summary_only:
        data: dict[str, object] = {
            "target_path": target_path,
            "udon_only": udon_only,
            "read_only": True,
            "summary_only": True,
            "component_count": len(component_summaries),
            "null_reference_count": null_reference_count,
            "internal_broken_ref_count": internal_broken_ref_count,
            "duplicate_reference_count": duplicate_reference_count,
        }
        if filter_active:
            data["script_filter"] = script_filter
            data["diagnostic_counts"] = diagnostic_counts
            if diagnostics_baseline is not None:
                data["diagnostics_baseline"] = classify_current_keys(
                    diagnostic_key_records,
                    diagnostics_baseline,
                ).to_dict()
        elif diagnostics_baseline is not None:
            data["diagnostics_baseline"] = classify_current_keys(
                diagnostic_key_records,
                diagnostics_baseline,
            ).to_dict()
        if is_variant:
            data["is_variant"] = True
            data["base_prefab_path"] = base_prefab_path
        return ToolResponse(
            success=response_success,
            severity=response_severity,
            code="INSPECT_WIRING_RESULT",
            message="inspect.wiring completed (read-only).",
            data=data,
            diagnostics=[],
        )

    # Pagination: page over the merged components list. Diagnostic counts
    # below remain page-independent (full merged totals modulo the
    # active script filter) so the caller can judge severity from any
    # page. Cursor validation runs after the merged total is known so
    # position can be range-checked against it.
    total = len(component_summaries)
    cursor_or_error = _parse_inspect_wiring_cursor(cursor, total)
    if isinstance(cursor_or_error, ToolResponse):
        return cursor_or_error
    position = cursor_or_error
    end = min(position + page_size, total)
    page_slice = component_summaries[position:end]
    next_cursor = (
        f"{INSPECT_WIRING_CURSOR_PREFIX}{end}" if end < total else ""
    )

    data = {
        "target_path": target_path,
        "udon_only": udon_only,
        "read_only": True,
        "component_count": total,
        "null_reference_count": null_reference_count,
        "internal_broken_ref_count": internal_broken_ref_count,
        "duplicate_reference_count": duplicate_reference_count,
        "components": page_slice,
        "page_slice_length": len(page_slice),
        "page_size": page_size,
        "cursor": cursor,
        "next_cursor": next_cursor,
    }
    if filter_active:
        data["script_filter"] = script_filter
        data["diagnostic_counts"] = diagnostic_counts
        data["filtered_diagnostics"] = _diagnostic_wire_rows(
            filtered_diagnostics,
            filtered_default_severity,
        )
        if include_out_of_scope_diagnostics:
            data["out_of_scope_diagnostics"] = _diagnostic_wire_rows(
                out_of_scope_diagnostics,
                out_of_scope_default_severity,
            )
        if diagnostics_baseline is not None:
            data["diagnostics_baseline"] = classify_current_keys(
                diagnostic_key_records,
                diagnostics_baseline,
            ).to_dict()
    elif diagnostics_baseline is not None:
        data["diagnostics_baseline"] = classify_current_keys(
            diagnostic_key_records,
            diagnostics_baseline,
        ).to_dict()
    if is_variant:
        data["is_variant"] = True
        data["base_prefab_path"] = base_prefab_path

    return ToolResponse(
        success=response_success,
        severity=response_severity,
        code="INSPECT_WIRING_RESULT",
        message="inspect.wiring completed (read-only).",
        data=data,
        diagnostics=response_diagnostics,
    )


def validate_all_wiring(
    prefab_variant: PrefabVariantService,
    reference_resolver: ReferenceResolverService,
    *,
    target_path: str = "",
    diagnostics_baseline: DiagnosticsBaseline | None = None,
) -> ToolResponse:
    if target_path:
        paths = [Path(target_path)]
    else:
        project_root = prefab_variant.project_root
        if project_root is None:
            return error_response(
                "VALIDATE_WIRING_NO_SCOPE",
                "No scope set. Call activate_project first.",
            )
        paths = sorted(
            p for p in reference_resolver.collect_scope_files(project_root)
            if p.suffix in (".prefab", ".unity")
        )

    if not paths:
        data: dict[str, object] = {
            "files_scanned": 0,
            "total_components": 0,
            "total_null_refs": 0,
        }
        if diagnostics_baseline is not None:
            data["diagnostics_baseline"] = classify_current_keys(
                (),
                diagnostics_baseline,
            ).to_dict()
        return success_response(
            "VALIDATE_WIRING_EMPTY",
            "No .prefab or .unity files found in scope.",
            data=data,
        )

    total_components = 0
    total_null_refs = 0
    null_refs_by_file: list[dict[str, object]] = []
    diagnostic_key_records: list[DiagnosticKeyRecord] = []
    baseline_key_set_complete = True
    collection_baseline = (
        DiagnosticsBaseline((), diagnostics_baseline.path, diagnostics_baseline.status)
        if diagnostics_baseline is not None else None
    )

    for p in paths:
        try:
            # Issue #197: pass the documented inclusive upper bound for
            # page_size so the per-file scan returns the merged
            # components list on a single page; the aggregate envelope
            # never paginates.
            result = inspect_wiring(
                prefab_variant, reference_resolver, target_path=str(p),
                page_size=INSPECT_WIRING_PAGE_SIZE_MAX,
                diagnostics_baseline=collection_baseline,
            )
            resp_dict = result.to_dict()
            if not resp_dict.get("success", False):
                baseline_key_set_complete = False
                continue
            response_data = resp_dict.get("data")
            if not isinstance(response_data, dict):
                baseline_key_set_complete = False
                continue
            raw_components = response_data.get("components", [])
            if not isinstance(raw_components, list):
                baseline_key_set_complete = False
                continue
            components = [
                component
                for component in raw_components
                if isinstance(component, dict)
            ]
            comp_count = len(components)
            null_count = sum(
                len(raw_names)
                for component in components
                for raw_names in (component.get("null_field_names"),)
                if isinstance(raw_names, list)
            )
            total_components += comp_count
            total_null_refs += null_count
            if diagnostics_baseline is not None:
                diagnostic_key_records.extend(
                    _diagnostic_key_records_from_classification(response_data)
                )
            if null_count > 0:
                null_refs_by_file.append({
                    "file": str(p),
                    "null_refs": null_count,
                    "components": comp_count,
                })
        except Exception as exc:
            baseline_key_set_complete = False
            logging.getLogger(__name__).debug(
                "validate_all_wiring: skipped %s: %s", p, exc,
            )
            continue

    data = {
        "files_scanned": len(paths),
        "total_components": total_components,
        "total_null_refs": total_null_refs,
        "null_refs_by_file": null_refs_by_file,
    }
    if diagnostics_baseline is not None and baseline_key_set_complete:
        # A skipped child scan cannot prove any known baseline key is resolved.
        data["diagnostics_baseline"] = classify_current_keys(
            tuple(diagnostic_key_records),
            diagnostics_baseline,
        ).to_dict()

    return success_response(
        "VALIDATE_WIRING_OK",
        f"Scanned {len(paths)} files: "
        f"{total_components} components, {total_null_refs} null references",
        data=data,
    )


def _diagnostic_key_records_from_classification(
    data: object,
) -> tuple[DiagnosticKeyRecord, ...]:
    if not isinstance(data, dict):
        return ()
    raw_classification = data.get("diagnostics_baseline")
    if not isinstance(raw_classification, dict):
        return ()
    raw_records = raw_classification.get("new")
    if not isinstance(raw_records, list):
        return ()

    records: list[DiagnosticKeyRecord] = []
    for raw_record in raw_records:
        if not isinstance(raw_record, dict):
            continue
        raw_data = raw_record.get("data")
        records.append(
            DiagnosticKeyRecord(
                key=str(raw_record.get("key", "")),
                severity=str(raw_record.get("severity", "warning")),
                message=str(raw_record.get("message", "")),
                data=raw_data if isinstance(raw_data, dict) else {},
            )
        )
    return tuple(record for record in records if record.key)
