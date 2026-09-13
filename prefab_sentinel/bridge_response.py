"""Common validation for Unity Bridge response envelopes."""

from __future__ import annotations

from typing import Any, TypeGuard

from prefab_sentinel.bridge_constants import VALID_SEVERITIES

_ERROR_SEVERITIES = frozenset({"error", "critical"})


def is_bridge_response_envelope(payload: object) -> TypeGuard[dict[str, Any]]:
    """Return whether *payload* has the common six-field Bridge envelope."""
    if not isinstance(payload, dict):
        return False
    success = payload.get("success")
    severity = payload.get("severity")
    if type(success) is not bool:
        return False
    if not isinstance(severity, str) or severity not in VALID_SEVERITIES:
        return False
    if not isinstance(payload.get("code"), str):
        return False
    if not isinstance(payload.get("message"), str):
        return False
    if not isinstance(payload.get("data"), dict):
        return False
    if not isinstance(payload.get("diagnostics"), list):
        return False
    return not (success and severity in _ERROR_SEVERITIES)
