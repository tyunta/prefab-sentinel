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

- `python3 -m compileall prefab_sentinel/services/serialized_object.py tools/unity_patch_bridge.py tests/test_services.py tests/test_unity_patch_bridge.py tests/test_cli.py` -> passed.
- `python3 -m compileall prefab_sentinel/services/runtime_validation.py prefab_sentinel/orchestrator.py tests/test_services.py tests/test_cli.py` -> passed.
- `uv run --extra test python -m unittest tests.test_services.RuntimeValidationServiceTests.test_run_clientsim_runs_unity_command_when_configured tests.test_cli.CliTests.test_validate_runtime_runs_unity_when_configured` -> passed.
- `python3 -m compileall prefab_sentinel/patch_plan.py prefab_sentinel/services/serialized_object.py prefab_sentinel/orchestrator.py tests/test_services.py tests/test_cli.py` -> passed.
- `uv run --extra test python -m unittest tests.test_services.SerializedObjectServiceTests.test_load_patch_plan_normalizes_v2_resources tests.test_services.SerializedObjectServiceTests.test_apply_resource_plan_updates_open_json_target tests.test_services.SerializedObjectServiceTests.test_orchestrator_patch_apply_enforces_asset_exists_postcondition tests.test_services.SerializedObjectServiceTests.test_orchestrator_patch_apply_fails_broken_refs_postcondition tests.test_cli.CliTests.test_patch_apply_confirm_enforces_asset_exists_postcondition` -> passed.
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

## Phase A: ブロンドヘアセッション改善 (2026-03-22)

- [x] P2: パス解決の CWD 非依存化 — `SerializedObjectService._resolve_target_path()` を `resolve_scope_path()` に置換
- [x] P5: `editor refresh` コマンド追加 — `AssetDatabase.Refresh()` をトリガー
- [x] P6: `editor select` Prefab Stage 対応 — `--prefab-stage` オプションで Prefab 内部オブジェクトを選択
- [x] P1: dry-run `before` 値の実効値解決 (Phase 1) — Variant オーバーライドの既存値を表示
- [x] P4: `inspect hierarchy` Variant 対応 — ベース Prefab の階層を表示、オーバーライドをアノテーション

## Phase B: ブロンドヘアセッション改善 (実装済み)

- [x] P3: `patch revert` コマンド — Variant の特定オーバーライドを YAML レベルで削除
- [x] P7: `inspect materials` コマンド — メッシュごとのマテリアルスロット一覧表示（Variant チェーン考慮）
- [x] P1 Phase 2: チェーン全体の before 値解決 — `resolve_chain_values()` でベース Prefab まで辿る
- [x] パス二重化検出 — `has_path_doubling()` + `resolve_scope_path()` の警告
- [x] P8: Game View スクリーンショット — 実装済み (C# HandleCaptureScreenshot, ScreenCapture + Camera.Render)

## Phase C: 目マテリアル比較セッション改善 (2026-03-22)

- [x] Issue 1+5: SKILL.md の component セレクタ誤記修正 + Scene パッチ計画ドキュメント追加 + 数値セレクタバリデーション — 既に実装済み (guide/SKILL.md:159-174, :220-239, serialized_object.py:2515-2530)
- [x] Issue 2: `inspect wiring` の Variant 対応 — `inspect_hierarchy` パターンを適用。コンポーネント override_count + フィールド is_overridden 注釈
- [x] Issue 4: `editor set-material` コマンド — 既に実装済み (cli.py:844-863, C# HandleSetMaterial:570-615)

## Phase D: セッションレポートから拾った未トラック課題

### 優先度高
- [x] `inspect materials` が Variant チェーンで空を返す — stripped レンダラーの m_Modifications フォールバック追加 (material_inspector.py)
- [x] WSL inspect パス解決不具合 — `_read_target_file()` を `resolve_scope_path()` に移行 (orchestrator.py)
- [x] Scene ビューのカメラ制御コマンド — 実装済み (`editor camera --yaw --pitch --distance`, cli.py:708-752)

### 優先度中
- [x] dry-run でのコンポーネントパス検証 — soft_warnings の evidence にコンポーネント名・`inspect wiring` 案内を追加 (serialized_object.py)
- [x] guide スキルのパスがバージョン固定 — 解消済み (`${CLAUDE_PLUGIN_ROOT}` 使用、v0.2.95)
- [x] `editor select` が1回だとビューが遠い — `EditorApplication.delayCall` で FrameSelected 遅延実行 (C# HandleSelectObject)
- [x] Scene/Prefab Stage ルート一覧取得 — 実装済み (`editor list-roots`, cli.py:885, C# HandleListRoots:948-1001)

## P5: ステートフルセッション (2026-03-24)

- [x] Commit 1: `session.py` — ProjectSession + test_session.py
- [x] Commit 2: `watcher.py` — watchfiles integration + test_watcher.py + pyproject.toml
- [x] Commit 3: MCP 統合 — mcp_server.py 移行 + activate/status ツール + test_mcp_server.py 更新
- [x] Commit 4: ドキュメント — README.md + ROADMAP 更新
- [x] 検証: 1139 テスト全パス
- [x] レビュー修正: re-activation reset, dead field除去, diagnostics追加, watcher例外ログ, テスト追加
- [x] リファクタ: unused import除去, tempfile helper抽出, redundant WSL path解決排除, unused `field` import除去
- [x] 最終検証: 1145 テスト全パス

## Phase 3: Runtime Verification Bridge

- [x] Fix CS1626 (yield-in-try-catch) in `UnityRuntimeValidationBridge.cs` `Run()` method — GuardCoroutine pattern.
- [x] Fix CS1626 in `ExecuteClientSim()` — restructure try-catch-finally into outer try-finally with inner try-catch blocks.
- [x] Fix `DontDestroyOnLoad` crash in batchmode — guard with `Application.isPlaying`.
- [x] Add batchmode detection for `run_clientsim` — graceful skip (ClientSim requires play mode).
- [x] Verify `compile_udonsharp` in Unity batchmode (avatar project: `RUN_COMPILE_SKIPPED`, no UdonSharp).
- [x] Verify `run_clientsim` in Unity batchmode (avatar project: `RUN_CLIENTSIM_SKIPPED`, batchmode skip).
