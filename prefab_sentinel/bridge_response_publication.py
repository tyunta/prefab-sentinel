"""Shared Editor Bridge response-publication failure contract."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from prefab_sentinel.bridge_constants import (
    PROTOCOL_VERSION,
    RESPONSE_PUBLICATION_FAILURE_SUFFIX,
)
from prefab_sentinel.contracts import ToolResponse, error_response

PublicationArtifacts = tuple[Path, Path, Path]


def publication_artifact_paths(
    watch_dir: Path,
    request_id: str,
) -> tuple[Path, Path]:
    """Return the response temporary path and tagged failure marker path."""
    return (
        watch_dir / f"{request_id}.response.json.tmp",
        watch_dir / f"{request_id}{RESPONSE_PUBLICATION_FAILURE_SUFFIX}",
    )


def _cleanup_publication_failure_artifacts(
    artifacts: PublicationArtifacts,
    delete: Callable[[Path], None],
) -> None:
    for artifact in artifacts:
        delete(artifact)


def _publication_failure_contract(
    *,
    action: str,
    runtime: bool,
) -> tuple[str, str, dict[str, object]]:
    if runtime:
        return (
            "RUN_EDITOR_BRIDGE_RESPONSE_PUBLICATION_FAILED",
            (
                "Editor Bridge processed the runtime request but could not "
                "publish its response."
            ),
            {
                "action": action,
                "read_only": False,
                "executed": False,
                "state_unknown": True,
            },
        )
    return (
        "EDITOR_BRIDGE_RESPONSE_PUBLICATION_FAILED",
        "Editor Bridge processed the request but could not publish its response.",
        {"action": action, "state_unknown": True},
    )


def editor_failure_response(
    action: str,
    artifacts: PublicationArtifacts,
    delete: Callable[[Path], None],
) -> dict[str, object]:
    """Consume a tagged editor-control publication failure."""
    _cleanup_publication_failure_artifacts(artifacts, delete)
    code, message, data = _publication_failure_contract(
        action=action,
        runtime=False,
    )
    return {
        "protocol_version": PROTOCOL_VERSION,
        "success": False,
        "severity": "error",
        "code": code,
        "message": message,
        "data": data,
        "diagnostics": [],
    }


def runtime_failure_response(
    action: str,
    artifacts: PublicationArtifacts,
    delete: Callable[[Path], None],
) -> ToolResponse:
    """Consume a tagged runtime publication failure."""
    _cleanup_publication_failure_artifacts(artifacts, delete)
    code, message, data = _publication_failure_contract(
        action=action,
        runtime=True,
    )
    return error_response(code, message, data=data)
