from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True, slots=True)
class TargetIdentity:
    managed_type: str
    assembly: str | None
    script_guid: str | None
    script_file_id: int | None

    def __post_init__(self) -> None:
        if not self.managed_type.strip():
            raise ValueError("managed_type is required")
        if (self.script_guid is None) != (self.script_file_id is None):
            raise ValueError("script_guid and script_file_id must be supplied together")


def identity_match_priority(
    target: object,
    identity: TargetIdentity,
) -> tuple[int, str | None] | None:
    if not isinstance(target, Mapping):
        return None
    if (
        identity.script_guid is not None
        and target.get("script_guid") == identity.script_guid
        and target.get("script_file_id") == identity.script_file_id
    ):
        return 0, None
    if (
        identity.assembly is not None
        and target.get("managed_type") == identity.managed_type
        and target.get("assembly") == identity.assembly
    ):
        return 1, None
    if target.get("managed_type") == identity.managed_type:
        return 2, "Profile matched by managed_type only."
    return None


@dataclass(frozen=True, slots=True)
class SelectedProfile:
    path: Path
    source: str
    priority: int
    warning: str | None
    _document: dict[str, Any] = field(repr=False)

    def document(self) -> dict[str, Any]:
        return deepcopy(self._document)


@dataclass(frozen=True, slots=True)
class SurfaceProperty:
    path: str
    property_type: str
    source_value: Any
    effective_value: Any
    origin: dict[str, Any] | None
    array_size: int | None
    element_type: str | None


@dataclass(frozen=True, slots=True)
class SerializedSurface:
    target: TargetIdentity
    properties: tuple[SurfaceProperty, ...]
    local_file_id: str | None = None

    def property_map(self) -> dict[str, SurfaceProperty]:
        by_path = {item.path: item for item in self.properties}
        if len(by_path) != len(self.properties):
            raise ValueError("serialized surface contains duplicate property paths")
        return by_path
