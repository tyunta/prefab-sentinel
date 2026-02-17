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
  - `unitytool/bridge_smoke.py` (shared contract/exec logic for script + CLI)
  - `prefab-sentinel validate bridge-smoke ...` (`--expect-failure` / `--expected-code` / `--expected-applied` / `--expect-applied-from-plan` / `--out` supported)
  - output `data` now includes code assertion evidence (`expected_code` / `actual_code` / `code_matches`) and apply assertion evidence (`expected_applied` / `expected_applied_source` / `actual_applied` / `applied_matches`) when each assertion is enabled
  - CLI tests: `tests/test_cli.py` (`bridge-smoke` success + expectation mismatch)
- Added smoke automation runner for sample avatar/world:
  - `scripts/bridge_smoke_samples.py`
  - `unitytool/smoke_batch.py` (shared logic for script + CLI)
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
- Extend Unity executeMethod apply coverage:
  - Unity-side integration tests against sample prefab assets (batchmode assertions)
  - fixed buffer element update cases (`set` with indexed element paths) as Unity-side integration assertions
- Add Unity smoke hardening:
  - validate timeout policy knobs (`--timeout-multiplier` / `--timeout-slack-sec`) against accumulated real Unity runner history

## Decision-Required Queue
- Resolved (2026-02-16): ignore-guid policy -> Option B (`<scope>/config/ignore_guids.txt`, missing ignored).
- Resolved (2026-02-16): CI auto-apply allowed when `--out-ignore-guid-file` is explicitly set and branch is allowlisted.

## Notes
- Keep read-only policy for inspection/validation commands in Phase 1.
- `patch apply` is write-enabled only for explicit `--confirm`.
- JSON targets use built-in backend; Unity targets require external bridge command (`UNITYTOOL_PATCH_BRIDGE`).
- Continue fail-fast for invalid input and missing required paths.

## Completion Plan
### Phase 0: Scope And Criteria
- [x] Confirm completion criteria and acceptance tests (Definition of Done baseline).
- [x] Declare target scope policy (scope is per-run via CLI; no fixed path).
- [x] Record scope + criteria in README.

### Phase 1: Decision-Required Items
- [x] Decide ignore-guid file policy (Option B, per-scope config).
- [x] Decide whether CI can auto-apply ignore-guid updates (allowed with explicit output flag).

### Phase 2: Unity ExecuteMethod Coverage
- [ ] Add Unity-side integration tests for `set` / `insert_array_element` / `remove_array_element`.
- [ ] Add fixed buffer indexed element test cases (`set` with indexed element paths).
- [ ] Automate batchmode assertions and capture Unity logs as artifacts.

### Phase 3: Smoke Hardening
- [ ] Collect real Unity runner history data for timeout tuning.
- [ ] Validate timeout policy knobs (`--timeout-multiplier`, `--timeout-slack-sec`) against history.
- [ ] Update defaults/constraints and add regression checks.

### Phase 4: MCP Boundaries And Orchestrator
- [x] Verify MCP responsibility split (serialized-object / prefab-variant / reference-resolver / runtime-validation).
- [x] Ensure CLI orchestrator enforces dependency order and stop conditions.
- [x] Emit audit log with change reason, target, before/after diff, validation report.
- [x] Enforce `safe_fix` vs `decision_required` handling in workflow (suggest ignore-guids returns decision_required).

### Phase 5: End-to-End Pipeline
- [x] Preflight: `list_overrides` + `scan_broken_references` for scope (when scope/target provided).
- [x] Patch flow: `dry_run_patch` -> `apply_and_save` (confirm gate).
- [x] Post-apply: `compile_udonsharp` + `run_clientsim` with log classification (when runtime scene provided).
- [x] Fail-fast on any `critical` or `error` and route to decision queue.

### Phase 6: Quality Gates And Tests
- [x] Unit tests: propertyPath resolution, array bounds, reference reverse lookup.
- [ ] Integration tests: Base / Variant / Scene edit E2E.
- [ ] Regression tests: Broken PPtr and Udon nullref fixtures.
- [ ] CI gates: Broken PPtr 0, Variant override 100%, Udon runtime critical 0.

### Phase 7: Documentation And Examples
- [x] Sync README policy sections and add plan/attestation/allowlist examples.
- [x] Include sample before/after diffs + validation report artifacts.
- [x] Update roadmap status when tasks complete.

