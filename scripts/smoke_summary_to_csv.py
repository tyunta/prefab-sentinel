from __future__ import annotations

# Backward-compatible script entrypoint that re-exports helper functions
# used by existing tests and local automation.
from prefab_sentinel.smoke_history import main
from prefab_sentinel.smoke_history_pipeline import _expand_inputs
from prefab_sentinel.smoke_history_report import (
    _is_smoke_batch_summary,
    _render_markdown_summary,
)
from prefab_sentinel.smoke_history_stats import (
    _build_target_stats,
    _build_timeout_profiles,
    _percentile,
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
