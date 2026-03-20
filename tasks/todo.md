# TODO

- [x] Add a reusable parallel test runner entrypoint for local and CI use.
- [x] Verify the current suite passes under `unittest-parallel` and measure the command shape we should document.
- [x] Update CI and README to use the new parallel test command.
- [x] Run targeted review and refactor on the new test runner path.
- [x] Record verification results in this file.

## Verification

- `uv run --extra test python scripts/run_unit_tests.py` -> passed, 213 tests in parallel.
- `python3 scripts/run_unit_tests.py -k nonexistent` -> exits 2 with install guidance when `unittest_parallel` is missing.

## Authoring Sprint

- [x] Sync prefab create-mode hierarchy behavior and docs.
- [x] Add component lifecycle ops for prefab create mode.
- [x] Port create-mode mutation ops to component `$handle` targets.
- [x] Add material / ScriptableObject create-mode authoring and root asset `$handle` mutation.
- [x] Add scene open/create-mode authoring with `$scene` root parenting and prefab instantiation.
- [x] Route open-mode scene/material resource plans through explicit Unity bridge resource metadata.
- [x] Wire `validate runtime` compile / ClientSim execution to Unity batchmode request / response flow.
- [x] Add runtime regression coverage for configured project roots and CLI validation flow.
- [x] Split resource dispatch into dedicated `json` / `prefab` / `asset` / `material` / `scene` adapters.
- [x] Add plan `postconditions` with `asset_exists` / `broken_refs` evaluation in `patch apply`.
- [x] Verify the authoring path with parallel unit tests and compile checks.
- [x] Add repo-owned bridge smoke plans for `../UnityTool_sample` avatar/world targets.
- [x] Point sample smoke defaults at sibling `../UnityTool_sample` and repo `config/bridge_smoke/*.json`.

## Authoring Verification

- `python3 -m compileall prefab_sentinel/mcp/serialized_object.py tools/unity_patch_bridge.py tests/test_mcp_readonly.py tests/test_unity_patch_bridge.py tests/test_cli.py` -> passed.
- `python3 -m compileall prefab_sentinel/mcp/runtime_validation.py prefab_sentinel/orchestrator.py tests/test_mcp_readonly.py tests/test_cli.py` -> passed.
- `uv run --extra test python -m unittest tests.test_mcp_readonly.RuntimeValidationMcpTests.test_run_clientsim_runs_unity_command_when_configured tests.test_cli.CliTests.test_validate_runtime_runs_unity_when_configured` -> passed.
- `python3 -m compileall prefab_sentinel/patch_plan.py prefab_sentinel/mcp/serialized_object.py prefab_sentinel/orchestrator.py tests/test_mcp_readonly.py tests/test_cli.py` -> passed.
- `uv run --extra test python -m unittest tests.test_mcp_readonly.SerializedObjectMcpTests.test_load_patch_plan_normalizes_v2_resources tests.test_mcp_readonly.SerializedObjectMcpTests.test_apply_resource_plan_updates_open_json_target tests.test_mcp_readonly.SerializedObjectMcpTests.test_orchestrator_patch_apply_enforces_asset_exists_postcondition tests.test_mcp_readonly.SerializedObjectMcpTests.test_orchestrator_patch_apply_fails_broken_refs_postcondition tests.test_cli.CliTests.test_patch_apply_confirm_enforces_asset_exists_postcondition` -> passed.
- `uv run --extra test python scripts/run_unit_tests.py` -> passed, 250 tests in parallel.
- `python3 -m compileall prefab_sentinel/smoke_batch.py tests/test_bridge_smoke_samples.py` -> passed.
- `uv run --extra test python -m unittest tests.test_bridge_smoke_samples` -> passed.

## Phase 2: Unity Integration Test Harness

- [x] Extract `ApplyFromPaths` from `ApplyFromJson` in `UnityPatchBridge.cs`.
- [x] Create C# test harness `PrefabSentinel.UnityIntegrationTests.cs` (24 open-mode tests).
- [x] Create Python orchestrator `prefab_sentinel/integration_tests.py` + `scripts/unity_integration_tests.py`.
- [x] Add CLI subcommand `validate integration-tests`.
- [x] Add Python unit tests `tests/test_integration_tests.py`.
- [x] Add CI workflow `.github/workflows/unity-integration.yml`.
- [x] Fix 11 failing Unity integration tests (handle conflicts, property paths, array normalization).
- [x] Align protocol version to v2 across Python bridge, C# bridge, and test harness.
- [x] Add 13 create-mode integration tests (prefab/material/scene). Total: 37 Unity + 261 Python.
- [x] Update README / IDEAS_AND_ROADMAP / todo.md.
- [x] Add 4 variant E2E quality gate tests. Total: 41 Unity + 261 Python.

## Phase 3: Runtime Verification Bridge

- [x] Fix CS1626 (yield-in-try-catch) in `UnityRuntimeValidationBridge.cs` `Run()` method — GuardCoroutine pattern.
- [x] Fix CS1626 in `ExecuteClientSim()` — restructure try-catch-finally into outer try-finally with inner try-catch blocks.
- [x] Fix `DontDestroyOnLoad` crash in batchmode — guard with `Application.isPlaying`.
- [x] Add batchmode detection for `run_clientsim` — graceful skip (ClientSim requires play mode).
- [x] Verify `compile_udonsharp` in Unity batchmode (avatar project: `RUN_COMPILE_SKIPPED`, no UdonSharp).
- [x] Verify `run_clientsim` in Unity batchmode (avatar project: `RUN_CLIENTSIM_SKIPPED`, batchmode skip).
