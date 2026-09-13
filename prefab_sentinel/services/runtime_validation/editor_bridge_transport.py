"""Runtime Editor Bridge transport metadata and deadline policy."""

from __future__ import annotations

_CLIENTSIM_EXIT_CLEANUP_GRACE_SEC = 30
_EDITOR_DISPATCH_MARGIN_SEC = 5


def transport_failure_data(*, action: str, read_only: bool) -> dict[str, object]:
    return {"action": action, "read_only": read_only, "executed": False}


def transport_timeout_sec(
    action: str,
    profile: str | None,
    operation_timeout_sec: int,
) -> int:
    if action != "validate_runtime" or profile != "clientsim":
        return operation_timeout_sec
    return (
        operation_timeout_sec
        + _CLIENTSIM_EXIT_CLEANUP_GRACE_SEC
        + _EDITOR_DISPATCH_MARGIN_SEC
    )
