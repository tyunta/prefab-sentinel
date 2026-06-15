from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from prefab_sentinel.contracts import (
    Diagnostic,
    Severity,
    ToolResponse,
    error_response,
    success_response,
)
from prefab_sentinel.effective_hierarchy import (
    EffectiveHierarchyNode,
    build_effective_hierarchy,
)
from prefab_sentinel.orchestrator_variant import read_target_file
from prefab_sentinel.services.prefab_variant import PrefabVariantService
from prefab_sentinel.services.prefab_variant.overrides import OverrideEntry
from prefab_sentinel.udon_wiring_parser import UDON_BEHAVIOUR_GUID
from prefab_sentinel.unity_assets import decode_text_file
from prefab_sentinel.unity_assets_path import resolve_scope_path
from prefab_sentinel.unity_yaml_parser import YamlBlock, split_yaml_blocks


class _UnityEventNumericParseError(ValueError):
    def __init__(self, location: str, value: str) -> None:
        super().__init__(f"Malformed UnityEvent numeric value at {location}: {value!r}")
        self.location = location
        self.value = value


class _UnityEventListenerBoundsError(ValueError):
    def __init__(self, location: str, listener_count: int, supported_count: int) -> None:
        super().__init__(
            "UnityEvent listener array size at "
            f"{location} requested {listener_count} entries, but only "
            f"{supported_count} entries have source or serialized member data."
        )
        self.location = location
        self.listener_count = listener_count
        self.supported_count = supported_count


_SUPPORTED_SURFACES = {
    ("Button", "onClick"): "m_OnClick",
    ("Slider", "onValueChanged"): "m_OnValueChanged",
    ("Toggle", "onValueChanged"): "m_OnValueChanged",
}
_SUPPORTED_SURFACES_TEXT = "Button.onClick, Slider.onValueChanged, Toggle.onValueChanged"
_LISTENER_OVERRIDE_RE = re.compile(
    r"^(?P<field>.+?)\.m_PersistentCalls\.m_Calls\.Array\.data\[(?P<index>\d+)\]\.(?P<member>.+)$"
)
_ARRAY_SIZE_SUFFIX = ".m_PersistentCalls.m_Calls.Array.size"
_UDON_PROXY_WARNING = "INSPECT_UNITY_EVENT_UDONSHARP_PROXY_TARGET"


@dataclass(slots=True)
class _Listener:
    target_file_id: str = "0"
    target_assembly_type: str = ""
    method: str = ""
    mode: int = 0
    argument: dict[str, Any] = field(default_factory=dict)
    call_state: int = 0
    override_fields: set[str] = field(default_factory=set)
    source_index: int | None = None


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


def _selector_data(
    asset_path: str,
    symbol_path: str,
    component_type: str,
    property_name: str,
) -> dict[str, Any]:
    return {
        "asset_path": asset_path,
        "symbol_path": symbol_path,
        "component_type": component_type,
        "property_name": property_name,
        "read_only": True,
    }


def _find_component_block(
    node: EffectiveHierarchyNode,
    blocks_by_file_id: dict[str, YamlBlock],
    component_type: str,
) -> YamlBlock | None:
    for component_file_id in node.component_file_ids:
        block = blocks_by_file_id.get(component_file_id)
        if block is not None and _component_type(block) == component_type:
            return block
    return None


def _parse_listener_block(field_block: str) -> list[_Listener]:
    listeners: list[_Listener] = []
    for source_index, call_text in enumerate(_call_texts(field_block)):
        listeners.append(
            _Listener(
                target_file_id=_file_id(_match_value(call_text, r"m_Target:\s*(\{[^}]+\})")),
                target_assembly_type=_match_value(
                    call_text, r"m_TargetAssemblyTypeName:\s*(.*)"
                ),
                method=_match_value(call_text, r"m_MethodName:\s*(.*)"),
                mode=_int_value(_match_value(call_text, r"m_Mode:\s*(.*)"), "m_Mode"),
                argument={
                    "object": _match_value(call_text, r"m_ObjectArgument:\s*(\{[^}]+\})"),
                    "object_type": _match_value(
                        call_text, r"m_ObjectArgumentAssemblyTypeName:\s*(.*)"
                    ),
                    "int": _int_value(
                        _match_value(call_text, r"m_IntArgument:\s*(.*)"),
                        "m_Arguments.m_IntArgument",
                    ),
                    "float": _float_value(
                        _match_value(call_text, r"m_FloatArgument:\s*(.*)"),
                        "m_Arguments.m_FloatArgument",
                    ),
                    "string": _match_value(call_text, r"m_StringArgument:\s*(.*)"),
                    "bool": _bool_value(
                        _match_value(call_text, r"m_BoolArgument:\s*(.*)"),
                        "m_Arguments.m_BoolArgument",
                    ),
                },
                call_state=_int_value(
                    _match_value(call_text, r"m_CallState:\s*(.*)"),
                    "m_CallState",
                ),
                source_index=source_index,
            )
        )
    return listeners


def _call_texts(field_block: str) -> list[str]:
    lines = field_block.splitlines()
    starts = [
        index
        for index, line in enumerate(lines)
        if re.match(r"\s*-\s+m_Target:", line)
    ]
    calls: list[str] = []
    for offset, start in enumerate(starts):
        end = starts[offset + 1] if offset + 1 < len(starts) else len(lines)
        calls.append("\n".join(lines[start:end]))
    return calls


def _apply_listener_overrides(
    source_listeners: list[_Listener],
    overrides: list[OverrideEntry],
    serialized_field: str,
) -> list[_Listener]:
    listener_count = len(source_listeners)
    array_size_overridden = False
    array_size_location = f"{serialized_field}{_ARRAY_SIZE_SUFFIX}"
    member_entries: list[tuple[int, Any, OverrideEntry]] = []
    indexed_slots: set[int] = set()

    for entry in overrides:
        if entry.property_path == array_size_location:
            listener_count = _int_value(entry.value, entry.property_path)
            if listener_count < 0:
                raise _UnityEventNumericParseError(entry.property_path, entry.value)
            array_size_overridden = True
            continue
        match = _LISTENER_OVERRIDE_RE.match(entry.property_path)
        if match is None or match.group("field") != serialized_field:
            continue
        index = int(match.group("index"))
        indexed_slots.add(index)
        member_entries.append((index, match, entry))

    supported_count = max(
        len(source_listeners),
        len(indexed_slots),
        1 if array_size_overridden else len(source_listeners),
    )
    if listener_count > supported_count:
        raise _UnityEventListenerBoundsError(
            array_size_location,
            listener_count,
            supported_count,
        )

    listeners = [
        _Listener(
            target_file_id=listener.target_file_id,
            target_assembly_type=listener.target_assembly_type,
            method=listener.method,
            mode=listener.mode,
            argument=dict(listener.argument),
            call_state=listener.call_state,
            source_index=source_index,
        )
        for source_index, listener in enumerate(source_listeners[:listener_count])
    ]
    while len(listeners) < listener_count:
        fields = {"Array.size"} if array_size_overridden else set()
        listeners.append(_Listener(argument=_default_argument(), override_fields=fields))

    for index, match, entry in member_entries:
        if index >= listener_count:
            continue
        _apply_listener_member(listeners[index], match.group("member"), entry)
    return listeners


def _apply_listener_member(
    listener: _Listener,
    member: str,
    entry: OverrideEntry,
) -> None:
    listener.override_fields.add(member)
    if member == "m_Target":
        listener.target_file_id = _file_id(entry.object_reference)
    elif member == "m_TargetAssemblyTypeName":
        listener.target_assembly_type = entry.value
    elif member == "m_MethodName":
        listener.method = entry.value
    elif member == "m_Mode":
        listener.mode = _int_value(entry.value, entry.property_path)
    elif member == "m_CallState":
        listener.call_state = _int_value(entry.value, entry.property_path)
    elif member == "m_Arguments.m_ObjectArgument":
        listener.argument["object"] = entry.object_reference
    elif member == "m_Arguments.m_ObjectArgumentAssemblyTypeName":
        listener.argument["object_type"] = entry.value
    elif member == "m_Arguments.m_IntArgument":
        listener.argument["int"] = _int_value(entry.value, entry.property_path)
    elif member == "m_Arguments.m_FloatArgument":
        listener.argument["float"] = _float_value(entry.value, entry.property_path)
    elif member == "m_Arguments.m_StringArgument":
        listener.argument["string"] = entry.value
    elif member == "m_Arguments.m_BoolArgument":
        listener.argument["bool"] = _bool_value(entry.value, entry.property_path)


def _listener_entry(
    listener: _Listener,
    node: EffectiveHierarchyNode,
    index: int,
    source_listener_count: int,
    component_index: dict[str, dict[str, str]],
    diagnostics: list[Diagnostic],
    asset_path: str,
    symbol_path: str,
) -> dict[str, Any]:
    target_key = _component_lookup_key(node, listener.target_file_id)
    target = component_index.get(target_key, {})
    if not target and listener.target_file_id not in ("", "0"):
        diagnostics.append(
            Diagnostic(
                path=asset_path,
                location=symbol_path,
                detail="INSPECT_UNITY_EVENT_TARGET_UNRESOLVED",
                evidence=(
                    "UnityEvent listener target component could not be resolved "
                    "within the selected effective prefab instance."
                ),
                severity=Severity.WARNING.value,
            )
        )
    _append_udonsharp_diagnostic(
        listener,
        target,
        diagnostics,
        asset_path,
        symbol_path,
    )
    return {
        "index": index,
        "target_object_path": target.get("symbol_path", ""),
        "target_component_type": target.get("component_type", ""),
        "method": listener.method,
        "persistent_listener_mode": listener.mode,
        "argument": listener.argument,
        "call_state": listener.call_state,
        "origin": _listener_origin(node, source_listener_count, listener),
    }


def _listener_origin(
    node: EffectiveHierarchyNode,
    source_listener_count: int,
    listener: _Listener,
) -> dict[str, Any]:
    kind = (
        "source_default"
        if listener.source_index is not None and not listener.override_fields
        else "host_override"
    )
    return {
        "kind": kind,
        "source": {
            **node.origin["source"],
            "listener_count": source_listener_count,
            "listener_index": listener.source_index,
        },
        "override_host": node.origin["override_host"],
        "effective": node.origin["effective"],
        "overridden_fields": sorted(listener.override_fields),
    }


def _append_udonsharp_diagnostic(
    listener: _Listener,
    target: dict[str, str],
    diagnostics: list[Diagnostic],
    asset_path: str,
    symbol_path: str,
) -> None:
    if target.get("is_udon_proxy") != "true":
        return
    if target.get("has_backing_udon_behaviour") != "true":
        return
    if listener.method == "SendCustomEvent":
        return
    diagnostics.append(
        Diagnostic(
            path=asset_path,
            location=symbol_path,
            detail=_UDON_PROXY_WARNING,
            evidence=(
                "UnityEvent targets an UdonSharp proxy method while the backing "
                "UdonBehaviour is present; wire SendCustomEvent on the backing "
                "UdonBehaviour for persistent listener dispatch."
            ),
            severity=Severity.WARNING.value,
        )
    )


def _component_lookup_key(node: EffectiveHierarchyNode, component_file_id: str) -> str:
    source_asset_path = str(node.origin["source"]["asset_path"])
    instance_key = str(node.origin["effective"].get("instance_key", ""))
    return f"{instance_key}|{source_asset_path}|{component_file_id}"


def _component_index(
    nodes: list[EffectiveHierarchyNode],
    project_root: Path,
    current_text: str,
    current_asset_path: str,
) -> dict[str, dict[str, str]]:
    index: dict[str, dict[str, str]] = {}
    blocks_cache: dict[str, dict[str, YamlBlock]] = {}
    for node in _walk_nodes(nodes):
        source_asset_path = str(node.origin["source"]["asset_path"])
        if source_asset_path not in blocks_cache:
            blocks_cache[source_asset_path] = _blocks_by_file_id(
                project_root,
                source_asset_path,
                current_text,
                current_asset_path,
            )
        blocks = blocks_cache[source_asset_path]
        for component_file_id in node.component_file_ids:
            block = blocks.get(component_file_id)
            if block is None:
                continue
            component_type = _component_type(block)
            backing_file_id = _backing_udon_file_id(block)
            has_backing = backing_file_id in blocks and _is_udon_behaviour(blocks[backing_file_id])
            index[_component_lookup_key(node, component_file_id)] = {
                "symbol_path": str(node.origin["effective"]["symbol_path"]),
                "component_type": component_type,
                "is_udon_proxy": str(bool(backing_file_id)).lower(),
                "has_backing_udon_behaviour": str(has_backing).lower(),
            }
    return index


def _component_overrides(
    node: EffectiveHierarchyNode,
    component_file_id: str,
    serialized_field: str,
) -> list[OverrideEntry]:
    prefix = f"{serialized_field}.m_PersistentCalls.m_Calls"
    return [
        entry
        for entry in node.override_entries
        if entry.target_file_id == component_file_id
        and (
            entry.property_path.startswith(prefix)
            or entry.property_path == f"{serialized_field}{_ARRAY_SIZE_SUFFIX}"
        )
    ]


def _blocks_by_file_id(
    project_root: Path,
    asset_path: str,
    current_text: str,
    current_asset_path: str,
) -> dict[str, YamlBlock]:
    text = current_text
    if asset_path != current_asset_path:
        text = decode_text_file(resolve_scope_path(asset_path, project_root))
    return {block.file_id: block for block in split_yaml_blocks(text)}


def _extract_field_block(block_text: str, field_name: str) -> str | None:
    lines = block_text.splitlines()
    for index, line in enumerate(lines):
        match = re.match(rf"^(?P<indent>\s+){re.escape(field_name)}:\s*$", line)
        if match is None:
            continue
        base_indent = len(match.group("indent"))
        captured = [line]
        for child in lines[index + 1:]:
            if child.strip():
                indent = len(child) - len(child.lstrip())
                if indent <= base_indent:
                    break
            captured.append(child)
        return "\n".join(captured)
    return None


def _component_type(block: YamlBlock) -> str:
    if _is_udon_behaviour(block):
        return "VRC.Udon.UdonBehaviour"
    for line in block.text.splitlines():
        match = re.match(r"\s+m_EditorClassIdentifier:\s*(.*)", line)
        if match is None:
            continue
        value = match.group(1).strip()
        if not value:
            break
        if "::" in value:
            value = value.rsplit("::", 1)[1]
        return value.rsplit(".", 1)[-1]
    return "MonoBehaviour"


def _is_udon_behaviour(block: YamlBlock) -> bool:
    return UDON_BEHAVIOUR_GUID in block.text


def _backing_udon_file_id(block: YamlBlock) -> str:
    match = re.search(r"_udonSharpBackingUdonBehaviour:\s*\{fileID:\s*(-?\d+)", block.text)
    return match.group(1) if match else ""


def _nodes_by_symbol(
    nodes: list[EffectiveHierarchyNode],
) -> dict[str, list[EffectiveHierarchyNode]]:
    found: dict[str, list[EffectiveHierarchyNode]] = {}
    for node in _walk_nodes(nodes):
        found.setdefault(str(node.origin["effective"]["symbol_path"]), []).append(node)
    return found


def _node_key(node: EffectiveHierarchyNode) -> str:
    return str(node.origin["effective"]["file_id"])


def _walk_nodes(nodes: list[EffectiveHierarchyNode]) -> Any:
    for node in nodes:
        yield node
        yield from _walk_nodes(node.children)


def _match_value(text: str, pattern: str) -> str:
    match = re.search(pattern, text)
    return match.group(1).strip() if match else ""


def _file_id(reference: str) -> str:
    match = re.search(r"fileID:\s*(-?\d+)", reference)
    return match.group(1) if match else "0"


def _int_value(value: str, location: str) -> int:
    if not value:
        return 0
    try:
        return int(value)
    except ValueError as exc:
        raise _UnityEventNumericParseError(location, value) from exc


def _float_value(value: str, location: str) -> float:
    if not value:
        return 0.0
    try:
        return float(value)
    except ValueError as exc:
        raise _UnityEventNumericParseError(location, value) from exc


def _bool_value(value: str, location: str) -> bool:
    return bool(_int_value(value, location))


def _default_argument() -> dict[str, Any]:
    return {
        "object": "{fileID: 0}",
        "object_type": "",
        "int": 0,
        "float": 0.0,
        "string": "",
        "bool": False,
    }


__all__ = ["inspect_unity_event_listeners"]
