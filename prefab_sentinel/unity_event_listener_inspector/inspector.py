from __future__ import annotations

from prefab_sentinel.contracts import Diagnostic, Severity, ToolResponse, error_response, success_response
from prefab_sentinel.effective_hierarchy import build_effective_hierarchy
from prefab_sentinel.orchestrator_variant import read_target_file
from prefab_sentinel.services.prefab_variant import PrefabVariantService

from .diagnostics import _listener_entry
from .models import (
    _SUPPORTED_SURFACES,
    _SUPPORTED_SURFACES_TEXT,
    _UnityEventListenerBoundsError,
    _UnityEventNumericParseError,
)
from .overrides import _apply_listener_overrides
from .parser import _parse_listener_block
from .selectors import (
    _blocks_by_file_id,
    _component_index,
    _component_overrides,
    _extract_field_block,
    _find_component_block,
    _node_key,
    _nodes_by_symbol,
    _selector_data,
)


def inspect_unity_event_listeners(
    prefab_variant: PrefabVariantService,
    asset_path: str,
    symbol_path: str,
    component_type: str,
    property_name: str,
) -> ToolResponse:
    serialized_field = _SUPPORTED_SURFACES.get((component_type, property_name))
    selector_data = _selector_data(asset_path, symbol_path, component_type, property_name)
    if serialized_field is None:
        return error_response(
            "INSPECT_UNITY_EVENT_UNSUPPORTED_SURFACE",
            f"Supported UnityEvent surfaces: {_SUPPORTED_SURFACES_TEXT}.",
            data=selector_data,
        )

    text = read_target_file(prefab_variant, asset_path, "INSPECT_UNITY_EVENT")
    if isinstance(text, ToolResponse):
        return text

    hierarchy = build_effective_hierarchy(prefab_variant.project_root, asset_path, text)
    matches = _nodes_by_symbol(hierarchy.roots).get(symbol_path, [])
    if not matches:
        return error_response(
            "INSPECT_UNITY_EVENT_OBJECT_NOT_FOUND",
            f"GameObject symbol path was not found: {symbol_path}",
            data=selector_data,
        )
    if len(matches) > 1:
        return error_response(
            "INSPECT_UNITY_EVENT_OBJECT_AMBIGUOUS",
            f"GameObject symbol path matched multiple effective nodes: {symbol_path}",
            data={
                **selector_data,
                "match_count": len(matches),
                "matches": [_node_key(node) for node in matches],
            },
        )
    node = matches[0]

    source_asset_path = str(node.origin["source"]["asset_path"])
    blocks_by_file_id = _blocks_by_file_id(prefab_variant.project_root, source_asset_path, text, asset_path)
    component_block = _find_component_block(node, blocks_by_file_id, component_type)
    if component_block is None:
        return error_response(
            "INSPECT_UNITY_EVENT_COMPONENT_NOT_FOUND",
            f"{component_type} component was not found at {symbol_path}.",
            data=selector_data,
        )
    field_block = _extract_field_block(component_block.text, serialized_field)
    if field_block is None:
        return error_response(
            "INSPECT_UNITY_EVENT_FIELD_NOT_FOUND",
            f"{component_type}.{property_name} was not serialized at {symbol_path}.",
            data=selector_data,
        )

    diagnostics = list(hierarchy.diagnostics)
    try:
        source_listeners = _parse_listener_block(field_block)
        listeners = _apply_listener_overrides(
            source_listeners,
            _component_overrides(node, component_block.file_id, serialized_field),
            serialized_field,
        )
    except _UnityEventNumericParseError as exc:
        diagnostic = Diagnostic(
            path=asset_path,
            location=exc.location,
            detail="INSPECT_UNITY_EVENT_NUMERIC_PARSE_ERROR",
            evidence=str(exc),
            severity=Severity.ERROR.value,
        )
        return error_response(
            "INSPECT_UNITY_EVENT_NUMERIC_PARSE_ERROR",
            str(exc),
            data=selector_data,
            diagnostics=[*diagnostics, diagnostic],
        )
    except _UnityEventListenerBoundsError as exc:
        diagnostic = Diagnostic(
            path=asset_path,
            location=exc.location,
            detail="INSPECT_UNITY_EVENT_LISTENER_BOUNDS_ERROR",
            evidence=str(exc),
            severity=Severity.ERROR.value,
        )
        return error_response(
            "INSPECT_UNITY_EVENT_LISTENER_BOUNDS_ERROR",
            str(exc),
            data={
                **selector_data,
                "requested_listener_count": exc.listener_count,
                "supported_listener_count": exc.supported_count,
            },
            diagnostics=[*diagnostics, diagnostic],
        )
    component_index = _component_index(hierarchy.roots, prefab_variant.project_root, text, asset_path)
    payload_entries = [
        _listener_entry(
            listener,
            node,
            index,
            len(source_listeners),
            component_index,
            diagnostics,
            asset_path,
            symbol_path,
        )
        for index, listener in enumerate(listeners)
    ]
    severity = Severity.WARNING if diagnostics else Severity.INFO
    return success_response(
        "INSPECT_UNITY_EVENT_LISTENERS",
        "UnityEvent persistent listeners inspected.",
        severity=severity,
        data={
            **selector_data,
            "serialized_field": serialized_field,
            "source_listener_count": len(source_listeners),
            "effective_listener_count": len(payload_entries),
            "listeners": payload_entries,
        },
        diagnostics=diagnostics,
    )
