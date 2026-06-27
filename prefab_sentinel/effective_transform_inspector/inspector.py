from __future__ import annotations

from prefab_sentinel.contracts import Diagnostic, Severity, ToolResponse, error_response, success_response
from prefab_sentinel.effective_hierarchy import build_effective_hierarchy
from prefab_sentinel.orchestrator_variant import read_target_file
from prefab_sentinel.services.prefab_variant import PrefabVariantService

from .node_lookup import _node_key, _nodes_by_symbol, _parent_map
from .state import _local_value_table, _TransformNumericParseError, _world_value_table


def inspect_transform_effective_values(
    prefab_variant: PrefabVariantService,
    asset_path: str,
    symbol_path: str,
) -> ToolResponse:
    text = read_target_file(prefab_variant, asset_path, "INSPECT_TRANSFORM")
    if isinstance(text, ToolResponse):
        return text

    hierarchy = build_effective_hierarchy(
        prefab_variant.project_root,
        asset_path,
        text,
    )
    parent_by_key = _parent_map(hierarchy.roots)
    nodes_by_symbol = _nodes_by_symbol(hierarchy.roots)
    matches = nodes_by_symbol.get(symbol_path, [])
    if not matches or matches[0].transform is None:
        return error_response(
            "INSPECT_TRANSFORM_SYMBOL_NOT_FOUND",
            f"Transform symbol path was not found: {symbol_path}",
            data={
                "asset_path": asset_path,
                "symbol_path": symbol_path,
                "read_only": True,
            },
        )
    if len(matches) > 1:
        return error_response(
            "INSPECT_TRANSFORM_SYMBOL_AMBIGUOUS",
            f"Transform symbol path matched multiple effective nodes: {symbol_path}",
            data={
                "asset_path": asset_path,
                "symbol_path": symbol_path,
                "match_count": len(matches),
                "matches": [_node_key(node) for node in matches],
                "read_only": True,
            },
        )
    node = matches[0]

    diagnostics = list(hierarchy.diagnostics)
    try:
        local_values = _local_value_table(node)
        world_values = _world_value_table(
            node,
            parent_by_key,
            diagnostics,
            asset_path,
            symbol_path,
        )
    except _TransformNumericParseError as exc:
        diagnostic = Diagnostic(
            path=asset_path,
            location=exc.property_path,
            detail="INSPECT_TRANSFORM_NUMERIC_PARSE_ERROR",
            evidence=str(exc),
            severity=Severity.ERROR.value,
        )
        return error_response(
            "INSPECT_TRANSFORM_NUMERIC_PARSE_ERROR",
            str(exc),
            data={
                "asset_path": asset_path,
                "symbol_path": symbol_path,
                "read_only": True,
            },
            diagnostics=[*diagnostics, diagnostic],
        )
    values = {**local_values, **world_values}
    severity = Severity.WARNING if diagnostics else Severity.INFO
    return success_response(
        "INSPECT_TRANSFORM_VALUES",
        "Transform default, override, and effective values inspected.",
        severity=severity,
        data={
            "asset_path": asset_path,
            "symbol_path": symbol_path,
            "values": values,
            "read_only": True,
        },
        diagnostics=diagnostics,
    )
