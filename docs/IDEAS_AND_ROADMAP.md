# Prefab Sentinel Ideas And Roadmap

## Purpose
- Keep implementation ideas and execution order in one place.
- Distinguish between executable tasks and decision-required tasks.

## Current Status (Implemented)
- `validate refs` / `suggest ignore-guids` support:
  - `--ignore-guid`
  - `--ignore-guid-file`
- `suggest ignore-guids` supports:
  - `--out-ignore-guid-file`
  - `--out-ignore-guid-mode {replace|append}`
- Scope-aware GUID index for faster scans in multi-project repositories.
- `where_used` excludes heavy non-target directories by default.
- CLI `inspect where-used` with:
  - `--scope`
  - `--exclude`
  - `--max-usages`
- Markdown report includes noise-reduction summary metrics when ref-scan data exists.
- Added `scripts/benchmark_refs.py` for repeatable `validate refs` timing runs.
- `report export` supports markdown usage compaction:
  - `--md-max-usages`
  - `--md-omit-usages`
- `scripts/benchmark_refs.py` supports CSV output:
  - `--out-csv`
  - `--csv-append`
- `scripts/benchmark_refs.py` supports warm-up runs:
  - `--warmup-runs`
- Added `scripts/benchmark_history_to_csv.py` to combine JSON benchmarks into one CSV.
- `scripts/benchmark_refs.py` supports percentile output:
  - `p50`
  - `p90`
- `scripts/benchmark_refs.py` supports generated timestamp output:
  - `--include-generated-date`
  - `generated_at_utc`
- `scripts/benchmark_history_to_csv.py` supports filters:
  - `--scope-contains`
  - `--severity`
- `scripts/benchmark_history_to_csv.py` supports sorting:
  - `--sort-by {source|scope|avg_sec}`
  - `--sort-order {asc|desc}`
- `scripts/benchmark_history_to_csv.py` supports optional date column:
  - `--include-date-column`
  - `generated_date_utc`
- `scripts/benchmark_history_to_csv.py` supports generated date prefix filter:
  - `--generated-date-prefix`
- `scripts/benchmark_history_to_csv.py` supports slowest-row compaction:
  - `--top-slowest`
- `scripts/benchmark_history_to_csv.py` supports percentile threshold filter:
  - `--min-p90`
- `scripts/benchmark_history_to_csv.py` supports latest-row compaction:
  - `--latest-per-scope`
- `scripts/benchmark_history_to_csv.py` normalizes mixed scope path separators (`/` and `\`).
- `scripts/benchmark_history_to_csv.py` supports severity-split exports:
  - `--split-by-severity`
- `scripts/benchmark_history_to_csv.py` supports markdown snapshot output:
  - `--out-md`
- `scripts/benchmark_history_to_csv.py` ignores non-benchmark JSON schema rows.
- Added `scripts/benchmark_samples.py` for sample avatar/world benchmark orchestration.
- Added `scripts/benchmark_regression_report.py` for baseline/latest benchmark regression detection.
- `scripts/benchmark_samples.py` can run regression report in one flow:
  - `--run-regression`
  - `--regression-baseline-inputs`
- `scripts/benchmark_samples.py` supports baseline auto-discovery:
  - `--regression-baseline-auto-latest`
- `scripts/benchmark_regression_report.py` supports CI-oriented alerts:
  - `--alerts-only`
  - `--fail-on-regression`
- `scripts/benchmark_regression_report.py` supports CSV history append:
  - `--out-csv-append`
- `scripts/benchmark_regression_report.py` supports markdown summary export:
  - `--out-md`
- `scripts/benchmark_samples.py` can request regression markdown output:
  - `--regression-out-md`
- `scripts/benchmark_regression_report.py` supports per-scope baseline pinning:
  - `--baseline-pinning-file`
- `scripts/benchmark_samples.py` can forward baseline pinning file:
  - `--regression-baseline-pinning-file`
- Added runtime validation scaffold command:
  - `prefab-sentinel validate runtime --scene ...`
  - log-based classification (`BROKEN_PPTR`, `UDON_NULLREF`, etc.)
  - runtime assertion step (`assert_no_critical_errors`)
- Added patch apply scaffold command:
  - `prefab-sentinel patch hash --plan ...`
  - `prefab-sentinel patch sign --plan ...`
  - `prefab-sentinel patch attest --plan ...`
  - `prefab-sentinel patch verify --plan ...`
  - `prefab-sentinel patch apply --plan ... --dry-run`
  - optional attestation verification (`--attestation-file`)
  - optional plan digest verification (`--plan-sha256`)
  - optional plan signature verification (`--plan-signature`)
  - optional JSON report output (`--out-report`)
  - plan schema validation + dry-run diff preview
  - non-dry-run confirm gate (`--confirm`)
  - optional preflight reference scan (`--scope`)
  - optional prefab override preflight (`list_overrides` for `.prefab` target)
  - optional post-apply runtime validation sequence (`--runtime-scene` and related flags)
  - JSON target apply backend (`SER_APPLY_OK`, `.json` only)
  - Unity bridge adapter via `UNITYTOOL_PATCH_BRIDGE` (allowlisted external command)
  - bridge protocol version check (`protocol_version: 1`)
- Added Unity bridge script with batchmode command execution path:
  - `tools/unity_patch_bridge.py` (`UNITYTOOL_UNITY_COMMAND` / request-response file contract)
  - strict Unity response envelope validation (`BRIDGE_UNITY_RESPONSE_SCHEMA` on missing/invalid `success|severity|code|message|data|diagnostics`)
- Added Unity bridge smoke runner for end-to-end bridge invocation from patch plans:
  - `scripts/unity_bridge_smoke.py`
  - `--plan` required + bridge request shaping (`protocol_version` / `target` / `ops`)
  - Unity execution env overrides (`--unity-command` / `--unity-project-path` / `--unity-execute-method` / `--unity-timeout-sec` / `--unity-log-file`)
  - expectation checks (`--expect-failure` / `--expected-code` / `--expected-applied` / `--expect-applied-from-plan`) and optional response export (`--out`)
  - bridge response envelope validation (`success` / `severity` / `code` / `message` / `data` / `diagnostics`) with fail-fast errors
- Added smoke runner unit tests:
  - `tests/test_unity_bridge_smoke.py`
- Added shared bridge smoke library + CLI command:
  - `prefab_sentinel/bridge_smoke.py` (shared contract/exec logic for script + CLI)
  - `prefab-sentinel validate bridge-smoke ...` (`--expect-failure` / `--expected-code` / `--expected-applied` / `--expect-applied-from-plan` / `--out` supported)
  - output `data` now includes code assertion evidence (`expected_code` / `actual_code` / `code_matches`) and apply assertion evidence (`expected_applied` / `expected_applied_source` / `actual_applied` / `applied_matches`) when each assertion is enabled
  - CLI tests: `tests/test_cli.py` (`bridge-smoke` success + expectation mismatch)
- Added smoke automation runner for sample avatar/world:
  - `scripts/bridge_smoke_samples.py`
  - `prefab_sentinel/smoke_batch.py` (shared logic for script + CLI)
  - exposed as CLI command: `prefab-sentinel validate smoke-batch ...`
  - supports per-target response-code assertions (`--avatar-expected-code` / `--world-expected-code`)
  - supports timeout profile input (`--timeout-profile`) for history-based default timeout selection
  - supports per-target apply-count assertions (`--avatar-expected-applied` / `--world-expected-applied`)
  - supports plan-derived apply assertions (`--expect-applied-from-plan`, non-`expect-failure` targets only)
  - deterministic artifacts per target (`response.json` + `unity.log`) and aggregate `summary.json`/`summary.md`
  - transient failure retry controls (`--max-retries` / `--retry-delay-sec`)
  - per-target timeout tuning (`--avatar-unity-timeout-sec` / `--world-unity-timeout-sec`)
  - summary includes code assertion evidence (`expected_code` / `actual_code` / `code_matches`)
  - summary includes apply assertion evidence (`expected_applied` / `expected_applied_source` / `actual_applied` / `applied_matches`)
  - per-case duration telemetry (`duration_sec`) for timeout tuning evidence
  - unit tests: `tests/test_bridge_smoke_samples.py`
- Added smoke summary history export for timeout decision support:
  - `scripts/smoke_summary_to_csv.py`
  - aggregates `summary.json` rows into CSV and Markdown decision table by target
  - exports code assertion fields (`expected_code` / `actual_code` / `code_matches`) to CSV
  - exports apply assertion fields (`expected_applied` / `expected_applied_source` / `actual_applied` / `applied_matches`) to CSV
  - reports apply assertion quality (`applied_mismatches` / `applied_pass_pct`) per target in Markdown
  - supports code assertion quality gates (`--max-code-mismatches` / `--min-code-pass-pct`) with non-zero exit on threshold breach
  - supports applied assertion quality gates (`--max-applied-mismatches` / `--min-applied-pass-pct`) with non-zero exit on threshold breach
  - supports observed-timeout quality gates (`--max-observed-timeout-breaches` / `--min-observed-timeout-coverage-pct` / `--max-observed-timeout-breaches-per-target` / `--min-observed-timeout-coverage-pct-per-target`) with non-zero exit on threshold breach
  - supports timeout-profile quality gates (`--max-profile-timeout-breaches` / `--min-profile-timeout-coverage-pct` / `--max-profile-timeout-breaches-per-target` / `--min-profile-timeout-coverage-pct-per-target`) with non-zero exit on threshold breach
  - supports `--matched-only`, `--target`, `--duration-percentile`
  - supports timeout profile JSON export (`--out-timeout-profile`) with policy knobs (`--timeout-multiplier` / `--timeout-slack-sec` / `--timeout-min-sec` / `--timeout-round-sec`)
  - timeout profile includes history coverage metrics (`timeout_breach_count` / `timeout_coverage_pct`)
  - exposed as CLI command: `prefab-sentinel report smoke-history ...`
  - unit tests: `tests/test_smoke_summary_to_csv.py`
- Added CI workflow wiring:
  - `.github/workflows/ci.yml` runs `unittest` on push/PR/workflow_dispatch
  - `bridge-smoke-contract` job runs `prefab-sentinel validate smoke-batch` in expected-failure + expected-code mode, builds timeout decision artifacts via `prefab-sentinel report smoke-history` with code/timeout/profile quality gates (`--max-code-mismatches 0 --min-code-pass-pct 100 --max-observed-timeout-breaches 0 --min-observed-timeout-coverage-pct 100 --max-observed-timeout-breaches-per-target 0 --min-observed-timeout-coverage-pct-per-target 100 --max-profile-timeout-breaches 0 --min-profile-timeout-coverage-pct 100 --max-profile-timeout-breaches-per-target 0 --min-profile-timeout-coverage-pct-per-target 100`), and uploads `reports/bridge_smoke`
- Added Unity-enabled smoke workflow:
  - `.github/workflows/unity-smoke.yml` (`workflow_dispatch` + self-hosted Windows runner)
  - runs `prefab-sentinel validate smoke-batch` without `--*-expect-failure` and uploads `reports/bridge_smoke`
  - includes `targets` input (`all|avatar|world`) and preflight input checks (required paths + run-window/history policy numeric ranges)
  - supports `timeout_profile_path` input for history-derived timeout defaults
  - supports history timeout policy inputs (`history_duration_percentile` / `history_timeout_multiplier` / `history_timeout_slack_sec` / `history_timeout_min_sec` / `history_timeout_round_sec`)
  - supports per-target code assertion inputs (`avatar_expected_code` / `world_expected_code`)
  - supports `expect_applied_from_plan` input (default true) for plan-op-count assertions
  - supports smoke-history code quality gates (`max_code_mismatches` / `min_code_pass_pct`, default: disabled/empty)
  - supports smoke-history quality gates (`max_applied_mismatches` / `min_applied_pass_pct` / `max_observed_timeout_breaches` / `min_observed_timeout_coverage_pct` / `max_observed_timeout_breaches_per_target` / `min_observed_timeout_coverage_pct_per_target` / `max_profile_timeout_breaches` / `min_profile_timeout_coverage_pct` / `max_profile_timeout_breaches_per_target` / `min_profile_timeout_coverage_pct_per_target`, default: `0` / `100` / `0` / `100` / `0` / `100` / `0` / `100` / `0` / `100`)
  - supports optional UTC run-window gating (`run_window_start_utc_hour` / `run_window_end_utc_hour`)
  - builds decision artifacts via `prefab-sentinel report smoke-history` (`history.csv` / `history.md` / `timeout_profile.json`)
  - uploads split artifacts (`unity-smoke-summary`, `unity-smoke-avatar`, `unity-smoke-world`)
- Unity bridge now normalizes op values for executeMethod payload (`value_kind` fields).
- Unity patch bridge Python preflight now validates op shape (`component/path/index/value`) before Unity launch.
- Added Unity Editor executeMethod apply path for prefab patch operations:
  - `tools/unity/PrefabSentinel.UnityPatchBridge.cs` (`PrefabSentinel.UnityPatchBridge.ApplyFromJson`)
  - supports `.prefab` + `set` / `insert_array_element` / `remove_array_element`
  - `set` value decoding covers primitive/null + `Character`/`LayerMask`/`ArraySize` + `enum`/`Color`/`Vector2/3/4`/`Vector2Int/3Int`/`Rect/RectInt`/`Bounds/BoundsInt`/`Quaternion`/`AnimationCurve`/`Gradient`/`ObjectReference({guid,file_id})`/`ExposedReference` + `ManagedReference` (`__type` hint) + `Generic` custom struct payloads
  - component ambiguity and array-path mistakes return richer fail-fast diagnostics
  - fixed buffer arrays reject `insert_array_element` / `remove_array_element` with explicit fail-fast diagnostics
  - component selector accepts `TypeName@Hierarchy/Path` for explicit disambiguation
- `report export --format md` supports runtime summary section for `VALIDATE_RUNTIME_RESULT`.
- `report export --format md` supports `--md-max-steps` / `--md-omit-steps` to trim large `data.steps`.

## Next Executable Tasks

### P1: Smoke hardening
- Validate timeout policy knobs (`--timeout-multiplier` / `--timeout-slack-sec`) against accumulated real Unity runner history.
- Blocked on: accumulated test run history data from CI or local runs.

### P1: E2E integration quality gates — COMPLETE
- [x] Base edit E2E tests (24 open-mode integration tests).
- [x] Variant override integrity tests (4 variant tests: override, multi-override, persistence, base-change inheritance).
- [x] Scene edit E2E tests (4 create-mode scene tests).
- [x] Broken PPtr regression via smoke postconditions (`broken_refs: expected_count=0`).
- [x] CI gates: `unity-integration.yml` runs C# harness, smoke-batch validates postconditions.
- [x] Runtime verification: `compile_udonsharp` runs real compile checks, `run_clientsim` graceful-skips in batchmode (requires play mode by design).

### Completed
- ~~Extend Unity executeMethod apply coverage~~
- ~~Runtime verification wiring~~: C# runtime validation bridge compiles, both actions verified in batchmode. `compile_udonsharp` runs real checks; `run_clientsim` graceful-skips in batchmode.
- Protocol version alignment: C# bridge, Python bridge, integration tests all at v2
- Unity integration test harness (41 tests, all passing):
  - `tools/unity/PrefabSentinel.UnityIntegrationTests.cs`
  - 24 open-mode tests: set/insert/remove/error/persistence across prefab and material assets
  - 13 create-mode tests: prefab (root-only, hierarchy+component, find_component+mutate, rename+reparent, duplicate root rejection), material (Standard shader, named asset, missing shader rejection, already-exists rejection), scene (empty+GO, instantiate prefab, hierarchy+components, missing create_scene rejection)
  - 4 variant E2E tests: single override, multi-override, save/reopen persistence, base-change inheritance
  - `prefab_sentinel/integration_tests.py` + `scripts/unity_integration_tests.py` (Python orchestrator)
  - CLI: `prefab-sentinel validate integration-tests`
  - CI: `.github/workflows/unity-integration.yml` (workflow_dispatch, self-hosted Windows)
  - Production refactor: `ApplyFromPaths` extracted from `ApplyFromJson` for in-process test invocation

## Authoring Tool Sprint Plan

This section converts the generic Unity asset authoring roadmap into prioritized implementation sprints.
Assumption: one primary implementer, one sprint is roughly one to two weeks, and later sprints do not start until the exit criteria of the current sprint are satisfied.

### Sprint 1. Foundation And Compatibility — COMPLETE

**Priority**
- `P0`

**Goal**
- Introduce an authoring-capable plan model without breaking the existing patch flow.
- Keep audit, confirm-gate, and fail-fast behavior intact while adding multi-resource execution.

**Includes**
- `A01. Add authoring plan v2 schema and normalizer`
- `A02. Execute normalized authoring plans in the orchestrator`
- `A03. Add resource and handle context to the Unity bridge protocol`

**Why this sprint is first**
- Every later create or mutate workflow depends on resource IDs, handles, and a versioned bridge contract.
- This is the shortest path that unlocks authoring work without forcing a risky rewrite of the current v1 patch flow.

**Exit criteria**
- `patch apply --dry-run` accepts both v1 and v2 plans.
- v1 plans are normalized internally and still produce the current audit/report envelope.
- Unity bridge requests and responses are versioned and reject protocol mismatches with explicit diagnostics.
- Resource IDs and `$handle` references are represented consistently across Python and Unity bridge layers.

**Deferred to later sprints**
- Asset creation.
- Scene support.
- Runtime validation execution changes.

### Sprint 2. Prefab Creation Core — COMPLETE

**Priority**
- `P0`

**Goal**
- Make it possible to create a new prefab resource, build a hierarchy, and add components before any advanced validation work.

**Includes**
- `A04. Create prefab resources and save them from authoring plans`
- `A05. Add GameObject hierarchy authoring operations`
- `A06. Add component lifecycle operations`

**Why this sprint is second**
- Prefab creation is the smallest authoring slice with high user value.
- Hierarchy and component creation are prerequisites for meaningful authored content.

**Exit criteria**
- A plan can create a new prefab at a new `Assets/...` path and save it.
- A plan can create a root object, create children, reparent them, and rename them deterministically.
- A plan can add, remove, and find components, and later steps can reuse returned handles.
- Invalid output paths, bad parent handles, ambiguous component selections, and unsupported component types fail fast with structured diagnostics.

**Deferred to later sprints**
- Property mutation on newly created objects.
- Scene authoring.
- Runtime verification.

### Sprint 3. Prefab Authoring MVP Hardening — COMPLETE

**Priority**
- `P0`

**Goal**
- Reuse the current property mutation coverage on top of the new handle model and prove that prefab authoring actually survives save/reopen/validate.

**Includes**
- `A07. Port property mutation ops to v2 handles`
- `A08. Add prefab authoring end-to-end tests`

**Why this sprint is third**
- Prefab authoring is not credible until create, mutate, save, reopen, and reference validation all work together.
- This sprint turns the new execution model into a usable MVP instead of a partial API surface.

**Exit criteria**
- Newly created prefab objects and components can be mutated through v2 handle-based property ops.
- Existing v1-equivalent property mutation behavior remains intact.
- End-to-end tests cover create -> hierarchy -> component -> mutate -> save -> reopen -> validate.
- `scan_broken_references` shows zero newly introduced broken references on the prefab authoring test set.

**Deferred to later sprints**
- Non-prefab asset families.
- Scene flows.
- Real runtime compile and ClientSim execution.

### Sprint 4. Asset Family Expansion — COMPLETE

**Priority**
- `P1`

**Goal**
- Extend the authoring model beyond prefabs with the simplest additional asset families.

**Includes**
- `A09. Support ScriptableObject and Material authoring`

**Why this sprint is fourth**
- ScriptableObject and Material creation expands tool usefulness without introducing scene graph complexity.
- This is the cleanest test of whether the authoring model generalizes beyond prefabs.

**Exit criteria**
- A plan can create, save, reopen, and mutate ScriptableObject resources.
- A plan can create, save, reopen, and mutate Material resources.
- Prefab-side reference assignment to created ScriptableObjects or Materials is covered by tests where applicable.
- Unsupported asset type requests fail fast with deterministic diagnostics.

**Deferred to later sprints**
- Scene authoring.
- Runtime execution.
- Asset-family adapter split.

### Sprint 5. Scene Authoring Beta — COMPLETE

**Priority**
- `P1`

**Goal**
- Add the first scene-level authoring workflow so authored assets can be assembled into a runnable scene.

**Includes**
- `A10. Add scene authoring MVP`

**Why this sprint is fifth**
- Scene authoring becomes much simpler once prefab, hierarchy, component, and simple asset-family creation already exist.
- This is the first sprint that moves the tool from asset preparation toward world assembly automation.

**Exit criteria**
- A plan can create or open a scene, instantiate prefabs, perform hierarchy/component/property operations, and save the scene.
- Saved scenes can be reopened with object state preserved.
- Missing prefab references and invalid scene paths fail fast with structured diagnostics.

**Deferred to later sprints**
- Real runtime compile/ClientSim execution.
- Multi-scene editing.
- Advanced merge/conflict workflows.

### Sprint 6. Runtime Verification Closure — COMPLETE

**Priority**
- `P1`

**Goal**
- Replace stubbed runtime validation steps with real Unity-backed verification where the environment is available.

**Includes**
- `A11. Wire compile_udonsharp and run_clientsim to real Unity execution`

**Why this sprint is sixth**
- The repository policy already treats compile and ClientSim checks as mandatory verification gates.
- Authoring flows should be functionally useful before spending time on environment-sensitive runtime execution wiring.

**Exit criteria**
- `compile_udonsharp` executes real Unity batchmode compile checks when the environment is configured.
- `run_clientsim` executes real ClientSim smoke checks when the environment is configured.
- Structured `ToolResponse` envelopes report runtime failures without regressing current fallback behavior in unsupported environments.
- `patch apply --runtime-scene` can run authored outputs through end-to-end verification.

**Deferred to later sprints**
- Adapter refactoring.
- New asset-family support.

### Sprint 7. Architecture Hardening And Postconditions — COMPLETE

**Priority**
- `P2`

**Goal**
- Stabilize the design for long-term growth after prefab, asset, scene, and runtime flows are proven.

**Includes**
- `A12. Split asset-family adapters and add postcondition verification`

**Why this sprint is last**
- Adapter boundaries and postconditions are easier to design correctly after the main authoring paths exist in working form.
- Doing this too early increases abstraction cost without enough real execution feedback.

**Exit criteria**
- Prefab, scene, scriptable object, and material handling are split into dedicated adapters.
- Plans can declare postconditions such as `broken_refs == 0` or `asset_exists == true`.
- Postcondition failures stop execution with fail-fast diagnostics and remain compatible with the current reporting envelope.
- Adapter dispatch and postcondition evaluation are covered by unit and integration tests.

**Release checkpoints**
- `Authoring Alpha`: Sprint 1 complete. **REACHED**
- `Prefab Authoring MVP`: Sprint 3 complete. **REACHED** — 37 integration tests (24 open-mode + 13 create-mode) all passing.
- `Generic Asset Authoring Beta`: Sprint 5 complete. **REACHED** — material/scene create-mode tests included.
- `Verified Authoring Beta`: Sprint 6 complete. **REACHED** — C# bridge compiles, `compile_udonsharp` verified in batchmode, `run_clientsim` graceful-skips in batchmode (requires play mode), `--runtime-scene` Python orchestrator wired and tested.
- `Architecture Stabilization`: Sprint 7 complete. **REACHED** (adapter split + postconditions done).

## Authoring Tool Issue Drafts

This section keeps the issue-sized backlog that the sprint plan above is built from.
The IDs below are draft planning IDs, not GitHub issue numbers.

### A01. Add authoring plan v2 schema and normalizer — COMPLETE

**Background**
- The current plan model is `target + ops[]`, which assumes a single existing asset target.
- Generic authoring needs multiple resources, creation modes, and object handles that can be reused across steps.
- A versioned plan format is required so the existing patch flow can remain compatible while authoring support grows.

**Out of scope**
- Unity-side execution changes.
- New runtime validation behavior.
- Asset-family-specific create operations.

**Acceptance criteria**
- Define `plan_version: 2` with `resources[]` and `ops[]`.
- Support `result` handles and `$handle` references in op payloads.
- Add a normalizer that converts v1 `target + ops[]` plans into v2 internally.
- Reject invalid plans with fail-fast diagnostics that include the failing field path.
- Keep `patch apply --dry-run` working for both v1 and v2 plans.

**Test considerations**
- Unit tests for valid v2 parsing.
- Unit tests for v1-to-v2 normalization.
- Negative tests for missing `resources`, invalid handle references, and duplicate resource IDs.

**Depends on**
- None.

### A02. Execute normalized authoring plans in the orchestrator — COMPLETE

**Background**
- The current orchestrator executes a linear patch flow that expects one `target` and one `ops[]` array.
- Authoring plans need multi-resource execution, stable step recording, and audit metadata preservation.
- The CLI already has confirm gates, attestations, and audit outputs that should be reused rather than replaced.

**Out of scope**
- Unity bridge protocol redesign.
- New create or mutate backend implementations.
- Runtime execution wiring changes beyond orchestration hooks.

**Acceptance criteria**
- Accept normalized v2 plans in `patch apply`.
- Preserve `execution_id`, `executed_at_utc`, `change_reason`, and `out-report`.
- Record authoring steps in the result envelope in execution order.
- Preserve existing confirm gate, fail-fast, attestation, and digest verification behavior.
- Keep v1 plans executable through the normalization path.

**Test considerations**
- CLI tests for v2 dry-run and confirm flows.
- Envelope tests to ensure audit metadata remains present.
- Failure-path tests for step ordering and fail-fast behavior.

**Depends on**
- `A01`

### A03. Add resource and handle context to the Unity bridge protocol — COMPLETE (protocol version mismatch pending)

**Background**
- The current bridge protocol passes one `target` and raw `ops[]`.
- Generic authoring requires resource-scoped execution contexts and object handles that survive across multiple operations.
- Without explicit context, create operations cannot feed later mutation or reference-binding steps safely.

**Out of scope**
- Prefab-specific create implementation.
- Scene/material/scriptable object execution logic.
- Runtime validation behavior.

**Acceptance criteria**
- Extend the bridge request/response contract to carry resource IDs and handle references.
- Version the bridge contract so protocol mismatches fail-fast with explicit diagnostics.
- Define serialization rules for handles, resource references, and resource-local save steps.
- Keep existing bridge validation strict on required fields and protocol version.

**Test considerations**
- Unit tests for protocol version mismatch.
- Unit tests for malformed handle/resource payloads.
- Contract tests for request/response shaping in Python bridge code.

**Depends on**
- `A01`
- `A02`

### A04. Create prefab resources and save them from authoring plans — COMPLETE

**Background**
- The current Unity executeMethod path edits existing prefabs only.
- Generic authoring needs to create new prefab assets as first-class resources.
- Prefab creation is the smallest high-value create path and should anchor the authoring MVP.

**Out of scope**
- Scene authoring.
- Component add/remove operations.
- Runtime validation integration.

**Acceptance criteria**
- Add `create_prefab` and `save` operations for prefab resources.
- Support creating a prefab at a new `Assets/...` path.
- Return deterministic diagnostics when the path already exists or the path is invalid.
- Save the prefab through Unity APIs and make it reloadable by path.

**Test considerations**
- Unity-side integration test for create -> save -> reopen.
- Negative test for invalid or existing output path.
- Validation test that generated prefab files are recognized by reference scanners.

**Depends on**
- `A03`

### A05. Add GameObject hierarchy authoring operations — COMPLETE

**Background**
- Creating a prefab is not enough; authoring needs a hierarchy model.
- Existing mutation logic assumes a component can already be found on an existing object path.
- Hierarchy creation is required before component injection or property assignment on new objects.

**Out of scope**
- Component creation/removal.
- Scene hierarchy support.
- Non-prefab asset families.

**Acceptance criteria**
- Add `create_root`, `create_game_object`, `reparent`, and `rename_object` operations.
- Support returning handles for created objects.
- Ensure hierarchy path resolution is deterministic after reparent/rename.
- Fail-fast on ambiguous or invalid hierarchy targets.

**Test considerations**
- Unity-side tests for nested object creation and reparenting.
- Tests for handle reuse across multiple hierarchy operations.
- Negative tests for invalid parent handle or duplicate root creation.

**Depends on**
- `A04`

### A06. Add component lifecycle operations — COMPLETE

**Background**
- Generic authoring must create and remove components, not only mutate properties.
- The current prefab bridge resolves components by selector but does not create them.
- Component lifecycle operations are required before many real-world prefab assembly tasks can be expressed.

**Out of scope**
- Property mutation semantics for newly created components.
- Scene-specific component authoring.
- Runtime validation.

**Acceptance criteria**
- Add `add_component`, `remove_component`, and `find_component` operations.
- Return handles for created components.
- Support disambiguation diagnostics when multiple matching components exist.
- Preserve fail-fast behavior when a requested type cannot be added or found.

**Test considerations**
- Unity-side tests for add/remove/find on created and existing GameObjects.
- Tests for handle reuse in later operations.
- Negative tests for unsupported component types and ambiguous selectors.

**Depends on**
- `A05`

### A07. Port property mutation ops to v2 handles — COMPLETE

**Background**
- Existing mutation coverage is useful and should not be discarded.
- Generic authoring needs to apply the same property operations to newly created objects and components via handles.
- Reusing the current mutation backend reduces risk and shortens time to prefab authoring MVP.

**Out of scope**
- New asset-family create backends.
- Runtime validation execution.
- Postcondition design.

**Acceptance criteria**
- Add v2 property ops such as `set_property`, `insert_array_element`, and `remove_array_element`.
- Support both handle-based targets and legacy `component + path` selectors where useful.
- Preserve current type decoding coverage for primitives, references, managed references, and structured payloads.
- Preserve current fail-fast diagnostics for array path errors and unsupported shapes.

**Test considerations**
- Regression tests to ensure v1-equivalent property ops still work.
- Unity-side tests that mutate newly created prefab objects/components via handles.
- Negative tests for stale handles, invalid paths, and array bound violations.

**Depends on**
- `A06`

### A08. Add prefab authoring end-to-end tests — COMPLETE

**Background**
- Prefab authoring MVP is only credible if create, mutate, save, reopen, and validate all work together.
- The current tests cover JSON apply and bridge protocol behavior, but not Unity-backed authoring flows.
- This is the quality gate that separates "API exists" from "authoring is usable."

**Out of scope**
- Material/scriptable object authoring.
- Scene authoring.
- Runtime execution with real ClientSim.

**Acceptance criteria**
- Add E2E tests that create a prefab, build hierarchy, add components, mutate properties, save, reopen, and re-validate.
- Run `scan_broken_references` after authoring and require zero newly introduced broken references.
- Reopen the prefab and assert expected hierarchy/component/property state.
- Capture Unity logs and include them in test failure output.

**Test considerations**
- Integration tests on representative prefab fixtures.
- Regression fixtures for Broken PPtr-sensitive cases.
- Test matrix should include reference assignment and array mutation cases.

**Depends on**
- `A04`
- `A05`
- `A06`
- `A07`

### A09. Support ScriptableObject and Material authoring — COMPLETE

**Background**
- Prefabs alone are not enough for a generic authoring tool.
- ScriptableObjects and Materials are the next simplest asset families because they have direct asset creation APIs and clear save semantics.
- Supporting them expands authoring usefulness without the full complexity of scene graphs.

**Out of scope**
- Scene authoring.
- AnimatorController, Timeline, or importer-specific asset families.
- Runtime validation wiring.

**Acceptance criteria**
- Add `create_asset` support for ScriptableObject and Material resources.
- Support save and reopen of those assets.
- Support property mutation on the created resources through the same plan model.
- Preserve deterministic diagnostics for unsupported asset type requests.

**Test considerations**
- Unity-side tests for create -> mutate -> save -> reopen.
- Reference resolution tests where prefab fields point at created ScriptableObjects or Materials.
- Negative tests for invalid type names and invalid destination paths.

**Depends on**
- `A03`
- `A07`

### A10. Add scene authoring MVP — COMPLETE

**Background**
- A generic Unity authoring tool eventually needs scene-level assembly, not only asset-level mutation.
- Scene authoring introduces open/save semantics, root object management, and prefab instantiation workflows.
- This is the first step where authoring becomes useful for automation beyond asset preparation.

**Out of scope**
- Runtime validation execution.
- Advanced scene merge/conflict workflows.
- Multi-scene editing.

**Acceptance criteria**
- Add `open_scene`, `create_scene`, `instantiate_prefab`, and `save_scene` operations.
- Support hierarchy and component operations on scene objects through the same handle model.
- Preserve fail-fast diagnostics for invalid scene paths and missing prefab references.
- Reopen saved scenes and confirm object state is preserved.

**Test considerations**
- Unity-side integration tests for create/open/save/reopen scene flows.
- Tests for scene object handles and prefab instance creation.
- Negative tests for missing prefab resources and invalid save locations.

**Depends on**
- `A05`
- `A06`
- `A07`
- `A09`

### A11. Wire compile_udonsharp and run_clientsim to real Unity execution — COMPLETE

**Background**
- The current runtime validation layer classifies logs but skips actual compile and ClientSim execution.
- A generic authoring tool needs post-apply verification that matches the operational policy in this repository.
- Without real runtime execution, authoring can still silently produce invalid worlds or prefabs.

**Out of scope**
- New authoring plan semantics.
- Asset-family-specific create logic.
- CI policy redesign.

**Acceptance criteria**
- Replace `RUN_COMPILE_SKIPPED` and `RUN_CLIENTSIM_SKIPPED` with real Unity batchmode execution where environment is configured.
- Preserve scaffold fallback behavior when the environment is clearly unavailable.
- Surface compile/runtime failures through structured `ToolResponse` envelopes.
- Allow `patch apply --runtime-scene` to run end-to-end verification on authored outputs.

**Test considerations**
- Unit tests for environment detection and command construction.
- Integration tests with fake runners where possible.
- Real Unity smoke tests on supported runners for compile and ClientSim paths.

**Depends on**
- `A10`

### A12. Split asset-family adapters and add postcondition verification — COMPLETE

**Background**
- As authoring coverage expands, one large backend will become brittle.
- Different asset families need different creation, save, and validation semantics.
- Plans should be able to declare postconditions so authoring success is defined by verifiable outcomes, not only process completion.

**Out of scope**
- New asset family support beyond the adapters already implemented.
- UI or IDE integration.
- Workflow approval UX beyond current confirm gates.

**Acceptance criteria**
- Split prefab, scene, scriptable object, and material handling into dedicated adapters.
- Define a postcondition model for authoring plans, such as `broken_refs == 0` or `asset_exists == true`.
- Enforce postconditions in the orchestrator with fail-fast semantics.
- Keep reporting structured and compatible with existing `success / severity / code / message / data / diagnostics` envelopes.

**Test considerations**
- Unit tests for adapter dispatch and postcondition evaluation.
- Integration tests that intentionally violate postconditions and assert fail-fast behavior.
- Regression tests that verify reporting remains stable across asset families.

**Depends on**
- `A08`
- `A09`
- `A10`
- `A11`

## Decision-Required Queue
- Resolved (2026-02-16): ignore-guid policy -> Option B (`<scope>/config/ignore_guids.txt`, missing ignored).
- Resolved (2026-02-16): CI auto-apply allowed when `--out-ignore-guid-file` is explicitly set and branch is allowlisted.

## Notes
- Keep read-only policy for inspection/validation commands in Phase 1.
- `patch apply` is write-enabled only for explicit `--confirm`.
- JSON targets use built-in backend; Unity targets require external bridge command (`UNITYTOOL_PATCH_BRIDGE`).
- Continue fail-fast for invalid input and missing required paths.
- ~~Protocol version mismatch:~~ **RESOLVED** (commit `544e6ba`). C# bridge now accepts v2 envelope.

## Completion Plan
### Phase 0: Scope And Criteria
- [x] Confirm completion criteria and acceptance tests (Definition of Done baseline).
- [x] Declare target scope policy (scope is per-run via CLI; no fixed path).
- [x] Record scope + criteria in README.

### Phase 1: Decision-Required Items
- [x] Decide ignore-guid file policy (Option B, per-scope config).
- [x] Decide whether CI can auto-apply ignore-guid updates (allowed with explicit output flag).

### Phase 2: Unity ExecuteMethod Coverage — COMPLETE
- [x] Add Unity-side integration tests for `set` / `insert_array_element` / `remove_array_element`.
- [x] Add fixed buffer indexed element test cases (`set` with indexed element paths).
- [x] Automate batchmode assertions and capture Unity logs as artifacts.
- [x] 24 open-mode integration tests passing (2026-03-20).

### Phase 2.5: Protocol And Create-Mode Verification — COMPLETE
- [x] Bump C# bridge `ProtocolVersion` from 1 to 2; accept v2 `resources[]` envelope.
- [x] Add create-mode integration tests (prefab, material, scene).
- [x] Verify Python→C# create-mode E2E with real Unity batchmode.

### Phase 3: Smoke Hardening
- [ ] Collect real Unity runner history data for timeout tuning.
- [ ] Validate timeout policy knobs (`--timeout-multiplier`, `--timeout-slack-sec`) against history.
- [ ] Update defaults/constraints and add regression checks.

### Phase 4: MCP Boundaries And Orchestrator — COMPLETE
- [x] Verify MCP responsibility split (serialized-object / prefab-variant / reference-resolver / runtime-validation).
- [x] Ensure CLI orchestrator enforces dependency order and stop conditions.
- [x] Emit audit log with change reason, target, before/after diff, validation report.
- [x] Enforce `safe_fix` vs `decision_required` handling in workflow (suggest ignore-guids returns decision_required).

### Phase 5: End-to-End Pipeline — COMPLETE
- [x] Preflight: `list_overrides` + `scan_broken_references` for scope (when scope/target provided).
- [x] Patch flow: `dry_run_patch` -> `apply_and_save` (confirm gate).
- [x] Post-apply: `compile_udonsharp` + `run_clientsim` with log classification (when runtime scene provided).
- [x] Fail-fast on any `critical` or `error` and route to decision queue.

### Phase 6: Quality Gates And Tests — COMPLETE
- [x] Unit tests: propertyPath resolution, array bounds, reference reverse lookup. (261 tests passing)
- [x] Open-mode integration tests: set/insert/remove/error/persistence. (24 tests passing)
- [x] Create-mode integration tests: prefab/material/scene create E2E (13 tests, all passing).
- [x] Integration tests: Base / Variant / Scene edit E2E. (41 tests)
- [x] Regression tests: Broken PPtr and Udon nullref fixtures. (smoke postconditions)
- [x] CI gates: Broken PPtr 0, Variant override 100%, Udon runtime critical 0. (unity-integration.yml)

### Phase 7: Documentation And Examples — COMPLETE
- [x] Sync README policy sections and add plan/attestation/allowlist examples.
- [x] Include sample before/after diffs + validation report artifacts.
- [x] Update roadmap status when tasks complete.

