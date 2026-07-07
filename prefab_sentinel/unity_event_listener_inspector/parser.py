from __future__ import annotations

import re
from typing import Any

from .models import _Listener, _UnityEventNumericParseError


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
