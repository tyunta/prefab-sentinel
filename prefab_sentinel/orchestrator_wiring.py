"""Wiring inspection functions extracted from Phase1Orchestrator."""

from __future__ import annotations

import logging
from pathlib import Path

from prefab_sentinel.contracts import (
    Diagnostic,
    Severity,
    ToolResponse,
    error_response,
    success_response,
)
from prefab_sentinel.orchestrator_variant import _read_target_file, _resolve_variant_base
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
# Packaged scenes (e.g. NadeVision.prefab + VVMW package) merge into a
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


def inspect_wiring(
    prefab_variant: PrefabVariantService,
    reference_resolver: ReferenceResolverService,
    target_path: str,
    *,
    udon_only: bool = False,
    cursor: str = "",
    page_size: int = INSPECT_WIRING_PAGE_SIZE_DEFAULT,
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

    text_or_error = _read_target_file(prefab_variant, target_path, "INSPECT_WIRING")
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

    text, is_variant, base_prefab_path, chain_diags = _resolve_variant_base(
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
    success = result.max_severity not in (Severity.ERROR, Severity.CRITICAL)

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

    # Pagination: page over the merged components list. Diagnostic counts
    # below remain page-independent (full merged totals) so the caller can
    # judge severity from any page. Cursor validation runs after the merged
    # total is known so position can be range-checked against it.
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

    data: dict[str, object] = {
        "target_path": target_path,
        "udon_only": udon_only,
        "read_only": True,
        "component_count": total,
        "null_reference_count": len(result.null_references) + len(nested_null_refs),
        "internal_broken_ref_count": len(result.internal_broken_refs) + len(nested_broken_refs),
        "duplicate_reference_count": len(result.duplicate_references) + len(nested_dup_refs),
        "components": page_slice,
        "page_slice_length": len(page_slice),
        "page_size": page_size,
        "cursor": cursor,
        "next_cursor": next_cursor,
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


def validate_all_wiring(
    prefab_variant: PrefabVariantService,
    reference_resolver: ReferenceResolverService,
    *,
    target_path: str = "",
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
        return success_response(
            "VALIDATE_WIRING_EMPTY",
            "No .prefab or .unity files found in scope.",
            data={"files_scanned": 0, "total_components": 0, "total_null_refs": 0},
        )

    total_components = 0
    total_null_refs = 0
    null_refs_by_file: list[dict[str, object]] = []

    for p in paths:
        try:
            # Issue #197: pass the documented inclusive upper bound for
            # page_size so the per-file scan returns the merged
            # components list on a single page; the aggregate envelope
            # never paginates.
            result = inspect_wiring(
                prefab_variant, reference_resolver, target_path=str(p),
                page_size=INSPECT_WIRING_PAGE_SIZE_MAX,
            )
            resp_dict = result.to_dict()
            if not resp_dict.get("success", False):
                continue
            components = resp_dict.get("data", {}).get("components", [])
            comp_count = len(components)
            null_count = sum(
                len(c.get("null_field_names") or [])
                for c in components
            )
            total_components += comp_count
            total_null_refs += null_count
            if null_count > 0:
                null_refs_by_file.append({
                    "file": str(p),
                    "null_refs": null_count,
                    "components": comp_count,
                })
        except Exception as exc:
            logging.getLogger(__name__).debug(
                "validate_all_wiring: skipped %s: %s", p, exc,
            )
            continue

    return success_response(
        "VALIDATE_WIRING_OK",
        f"Scanned {len(paths)} files: "
        f"{total_components} components, {total_null_refs} null references",
        data={
            "files_scanned": len(paths),
            "total_components": total_components,
            "total_null_refs": total_null_refs,
            "null_refs_by_file": null_refs_by_file,
        },
    )
