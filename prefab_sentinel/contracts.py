from __future__ import annotations

from collections.abc import Iterable
from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Any


class Severity(StrEnum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


@dataclass(slots=True)
class Diagnostic:
    path: str
    location: str
    detail: str
    evidence: str


@dataclass(slots=True)
class ToolResponse:
    success: bool
    severity: Severity
    code: str
    message: str
    data: dict[str, Any] = field(default_factory=dict)
    diagnostics: list[Diagnostic] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["severity"] = self.severity.value
        return payload


_SEVERITY_ORDER: dict[Severity, int] = {
    Severity.INFO: 0,
    Severity.WARNING: 1,
    Severity.ERROR: 2,
    Severity.CRITICAL: 3,
}


def max_severity(severities: Iterable[Severity]) -> Severity:
    levels = list(severities)
    if not levels:
        return Severity.INFO
    return max(levels, key=lambda level: _SEVERITY_ORDER[level])



