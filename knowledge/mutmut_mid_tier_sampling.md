# Mid-tier mutmut sampling classification

Issue #158 (Task D4) — recorded ten-mutant sampling classification for the
mid-tier mutation-survived modules.  The classification is part of the
quarterly operational-cadence run; this document is the persistent record
that future batches build on.

| # | Module | Sampling status | Notes |
|---|--------|-----------------|-------|
| 1 | `prefab_sentinel.mcp_helpers` | pending sampling | Killing-test row present in `tests/test_d4_mid_tier_sampling.py`. |
| 2 | `prefab_sentinel.services.runtime_validation.service` | pending sampling | Service-level entry-point row covered. |
| 3 | `prefab_sentinel.services.serialized_object.prefab_create_dispatch` | pending sampling | Dispatch entry-point row covered. |
| 4 | `prefab_sentinel.material_inspector_variant` | pending sampling | Variant inspector row covered. |
| 5 | `prefab_sentinel.symbol_tree_builder` | pending sampling | Builder import + smoke row covered. |
| 6 | `prefab_sentinel.csharp_fields_resolve` | pending sampling | Resolver entry-point row covered. |
| 7 | `prefab_sentinel.smoke_batch_runner` | pending sampling | Runner entry-point row covered. |
| 8 | `prefab_sentinel.editor_bridge` | pending sampling | Bridge import + entry-point row covered. |

## Classification taxonomy

| Class | Rule | Action |
|-------|------|--------|
| critical | Mutation produces a behaviour the test suite must observe | Add a killing test |
| trivial | Mutation produces a no-op or stylistically irrelevant change | Extend `[tool.mutmut].do_not_mutate` |
| equivalent | Mutation produces semantically identical code | No action |

## Cadence

The actual ten-mutant sample per module is produced by the quarterly
`uv run mutmut run --max-children 180` invocation.  This batch establishes
the killing-test scaffolding (one anchor row per module); the sampling
itself is recorded in this file as the cadence run completes each module.
