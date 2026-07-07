from __future__ import annotations

from typing import Any

from prefab_sentinel.effective_hierarchy import EffectiveHierarchyNode
from prefab_sentinel.services.prefab_variant.overrides import OverrideEntry

from .models import (
    _ARRAY_SIZE_SUFFIX,
    _LISTENER_OVERRIDE_RE,
    _Listener,
    _UnityEventListenerBoundsError,
    _UnityEventNumericParseError,
)
from .parser import _bool_value, _default_argument, _file_id, _float_value, _int_value


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
