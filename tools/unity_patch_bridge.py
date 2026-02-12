from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

PROTOCOL_VERSION = 1
SUPPORTED_SUFFIXES = {
    ".prefab",
    ".unity",
    ".asset",
    ".mat",
    ".anim",
    ".controller",
}


def _emit(payload: dict[str, Any]) -> int:
    sys.stdout.write(json.dumps(payload, ensure_ascii=False))
    sys.stdout.write("\n")
    return 0


def _error_response(
    *,
    code: str,
    message: str,
    data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "protocol_version": PROTOCOL_VERSION,
        "success": False,
        "severity": "error",
        "code": code,
        "message": message,
        "data": data or {},
        "diagnostics": [],
    }


def main() -> int:
    raw = sys.stdin.read()
    if not raw.strip():
        return _emit(
            _error_response(
                code="BRIDGE_REQUEST_EMPTY",
                message="Bridge request body is empty.",
            )
        )

    try:
        request = json.loads(raw)
    except json.JSONDecodeError as exc:
        return _emit(
            _error_response(
                code="BRIDGE_REQUEST_JSON",
                message="Bridge request must be valid JSON.",
                data={"error": str(exc)},
            )
        )

    if not isinstance(request, dict):
        return _emit(
            _error_response(
                code="BRIDGE_REQUEST_SCHEMA",
                message="Bridge request root must be an object.",
            )
        )

    protocol_raw = request.get("protocol_version")
    try:
        protocol_version = int(protocol_raw)
    except (TypeError, ValueError):
        protocol_version = -1
    if protocol_version != PROTOCOL_VERSION:
        return _emit(
            _error_response(
                code="BRIDGE_PROTOCOL_VERSION",
                message="Bridge protocol version mismatch.",
                data={
                    "expected_protocol_version": PROTOCOL_VERSION,
                    "received_protocol_version": protocol_raw,
                },
            )
        )

    target = str(request.get("target", "")).strip()
    ops = request.get("ops", [])
    if not target:
        return _emit(
            _error_response(
                code="BRIDGE_REQUEST_SCHEMA",
                message="target is required.",
            )
        )
    if not isinstance(ops, list):
        return _emit(
            _error_response(
                code="BRIDGE_REQUEST_SCHEMA",
                message="ops must be an array.",
            )
        )

    target_path = Path(target)
    if target_path.suffix.lower() not in SUPPORTED_SUFFIXES:
        return _emit(
            _error_response(
                code="BRIDGE_UNSUPPORTED_TARGET",
                message="Bridge target extension is not supported.",
                data={"target": target},
            )
        )

    # This reference bridge validates protocol contract only.
    # A production bridge should invoke Unity batchmode and return actual apply results.
    return _emit(
        {
            "protocol_version": PROTOCOL_VERSION,
            "success": False,
            "severity": "warning",
            "code": "PHASE1_STUB",
            "message": (
                "Reference Unity bridge scaffold loaded, but Unity batchmode apply "
                "is not implemented."
            ),
            "data": {
                "target": target,
                "op_count": len(ops),
                "applied": 0,
                "read_only": False,
                "executed": False,
            },
            "diagnostics": [],
        }
    )


if __name__ == "__main__":
    raise SystemExit(main())
