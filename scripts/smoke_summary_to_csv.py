from __future__ import annotations

# Backward-compatible script entrypoint that re-exports helper functions
# used by existing tests and local automation.
from unitytool.smoke_history import (
    _build_target_stats,
    _build_timeout_profiles,
    _expand_inputs,
    _is_smoke_batch_summary,
    _percentile,
    _render_markdown_summary,
    main,
)

__all__ = [
    "_build_target_stats",
    "_build_timeout_profiles",
    "_expand_inputs",
    "_is_smoke_batch_summary",
    "_percentile",
    "_render_markdown_summary",
    "main",
]


if __name__ == "__main__":
    raise SystemExit(main())
