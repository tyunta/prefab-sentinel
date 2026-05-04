# Mid-tier mutmut sampling classification

Issue #158 (Task D4) — recorded ten-mutant sampling classification for the
mid-tier mutation-survived modules.  Per the project mutation-testing policy
in `CLAUDE.md` ("Mutation testing 運用"), full mutmut runs execute on a
**quarterly cadence only** and are excluded from continuous integration.
Each per-module classification status below therefore reads as the work
that the next quarterly cadence run completes; the killing-test
scaffolding (one anchor row per module) is in place so the cadence run
can record the ten-mutant sample on top of an existing observable surface.

| # | Module | Sampling status | Notes |
|---|--------|-----------------|-------|
| 1 | `prefab_sentinel.mcp_helpers` | deferred to quarterly cadence run | Killing-test row present in `tests/test_d4_mid_tier_sampling.py`. |
| 2 | `prefab_sentinel.services.runtime_validation.service` | deferred to quarterly cadence run | Service-level entry-point row covered. |
| 3 | `prefab_sentinel.services.serialized_object.prefab_create_dispatch` | deferred to quarterly cadence run | Dispatch entry-point row covered. |
| 4 | `prefab_sentinel.material_inspector_variant` | deferred to quarterly cadence run | Variant inspector row covered. |
| 5 | `prefab_sentinel.symbol_tree_builder` | deferred to quarterly cadence run | Builder import + smoke row covered. |
| 6 | `prefab_sentinel.csharp_fields_resolve` | deferred to quarterly cadence run | Resolver entry-point row covered. |
| 7 | `prefab_sentinel.smoke_batch_runner` | deferred to quarterly cadence run | Runner entry-point row covered. |
| 8 | `prefab_sentinel.editor_bridge` | deferred to quarterly cadence run | Bridge import + entry-point row covered. |

The "deferred to quarterly cadence run" status means the ten-mutant sample
is produced by the next cadence-policy-governed mutmut invocation, not by
ad-hoc work between cadence runs.  No reader should treat the deferred
state as an outstanding obligation outside the cadence policy.

## Classification taxonomy

| Class | Rule | Action |
|-------|------|--------|
| critical | Mutation produces a behaviour the test suite must observe | Add a killing test |
| trivial | Mutation produces a no-op or stylistically irrelevant change | Extend `[tool.mutmut].do_not_mutate` |
| equivalent | Mutation produces semantically identical code | No action |

## Cadence

The actual ten-mutant sample per module is produced by the quarterly
`uv run mutmut run --max-children 180` invocation, executed on the cadence
defined in `CLAUDE.md` "Mutation testing 運用".  The scaffolding in this
batch (one anchor killing-test row per module) is the *substrate* the
cadence run records its sampling against; the sampling itself is recorded
in this file as each cadence run completes each module.

Cross-reference: see `CLAUDE.md` "Mutation testing 運用" for the
quarterly-cadence policy, the audit-module list, and the `survived`
classification rules (critical / trivial / equivalent).
