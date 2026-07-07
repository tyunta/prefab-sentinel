from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any


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
