"""Test-only startup checkpoints for one MCP CLI child.

This launcher imports the production module and calls ``main`` explicitly, so
its measurements are not bit-for-bit equivalent to ``python -m`` startup.  The
ordinary launcher remains covered by every child that does not opt into this
probe.
"""

from __future__ import annotations

import builtins
import json
import sys
import time
from collections.abc import Mapping, Sequence
from types import ModuleType
from typing import cast

_PREFIX = "MCP_STARTUP_PROBE "


def _checkpoint(name: str, **extra: object) -> None:
    payload = {
        "name": name,
        "wall": time.monotonic(),
        "cpu": time.process_time(),
        **extra,
    }
    print(
        _PREFIX + json.dumps(payload, separators=(",", ":")),
        file=sys.stderr,
        flush=True,
    )


def main() -> None:
    """Run the real CLI main with bounded, stderr-only import checkpoints."""

    _checkpoint("before_module_import")
    import prefab_sentinel.mcp_server as mcp_server

    _checkpoint("after_module_import")
    if mcp_server.__file__ is not None:
        sys.argv[0] = mcp_server.__file__
    _checkpoint("before_main")

    original_import = builtins.__import__
    observed: set[str] = set()
    checkpoints = {
        "uvicorn": "uvicorn_import",
        "prefab_sentinel.mcp_http": "mcp_http_import",
    }

    def observing_import(
        name: str,
        globals: Mapping[str, object] | None = None,
        locals: Mapping[str, object] | None = None,
        fromlist: Sequence[str] | None = None,
        level: int = 0,
    ) -> ModuleType:
        checkpoint = checkpoints.get(name) if globals is mcp_server.__dict__ else None
        if checkpoint is not None and checkpoint not in observed:
            observed.add(checkpoint)
            _checkpoint(f"before_{checkpoint}")
            imported = original_import(
                name,
                globals,
                locals,
                cast(Sequence[str], fromlist),
                level,
            )
            _checkpoint(f"after_{checkpoint}")
            return imported
        return original_import(
            name,
            globals,
            locals,
            cast(Sequence[str], fromlist),
            level,
        )

    builtins.__import__ = observing_import
    try:
        try:
            mcp_server.main()
        except SystemExit as exc:
            _checkpoint("system_exit", code=exc.code)
            raise
    finally:
        builtins.__import__ = original_import


if __name__ == "__main__":
    main()
