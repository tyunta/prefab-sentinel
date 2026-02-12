from __future__ import annotations

# Backward-compatible script entrypoint that re-exports helper functions
# used by existing tests and local automation.
from unitytool.smoke_batch import (
    SmokeCase,
    _build_smoke_command,
    _render_markdown_summary,
    _resolve_targets,
    build_parser,
    main,
    run_from_args,
)

__all__ = [
    "SmokeCase",
    "_build_smoke_command",
    "_render_markdown_summary",
    "_resolve_targets",
    "build_parser",
    "main",
    "run_from_args",
]


if __name__ == "__main__":
    raise SystemExit(main())
