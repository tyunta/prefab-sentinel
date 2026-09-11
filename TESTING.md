# Testing

PR を上げる前にローカルで走らせるテストの実行手順とテスト戦略の正本。ユニット / 統合 / 回帰 / mutmut の 4 系統と、CI（`ci.yml`）が回す内容、`source_text_invariant` マーカー、C# xUnit ハーネスの扱いを 1 箇所に集約する。運用ルールの正本は [AGENTS.md](./AGENTS.md)。

## Quickstart

最頻ユースケース（全ユニットテストを並列実行）:

```bash
uv run --extra test --extra mcp python scripts/run_unit_tests.py
```

## MCP three-revision stdio / modern HTTP protocol / wire conformance

MCP-focused regression gate は、protocol contract / distribution surface、middleware、stdio wire、HTTP gate を同時に固定する。migration 変更時は次を実行する。

```bash
uv run --extra mcp pytest \
  tests/test_mcp_distribution_contract.py \
  tests/test_mcp_protocol.py \
  tests/test_mcp_http.py \
  tests/test_mcp_stdio_transport.py \
  tests/test_mcp_http_transport.py -q
```

この gate は Tools-only request-method allowlist、modern request ごとの namespaced `_meta`、legacy `initialize` / `notifications/initialized` lifecycle、stdio `notifications/cancelled` forwarding、loopback modern HTTP wire、process-wide tool-call serialization を対象とする。raw-wire assertion は `2025-11-25` と `2025-06-18` の各 legacy revision を別 process で独立に実行し、それぞれの initialize identity / capability、legacy と modern の `tools/list` / `tools/call`、mixed-era rejection が process state を変更しないこと、legacy response に modern-only field が出ないことを固定する。HTTP は各 legacy revision について、true legacy body が `-32602`、legacy version を持つ modern envelope が `-32022` になる二つの rejection shape を固定する。domain envelope の `success=false`、tool execution error、top-level protocol error の区別は [docs/tool-conventions.md](./docs/tool-conventions.md#mcp-protocol--result-境界) を正本とする。

Issue #265 の port 0 回帰だけは test-owned の startup probe を明示選択し、module import、`main`、`uvicorn` / `mcp_http` import、`SystemExit` を newline + flush 済み stderr checkpoint と monotonic wall / process CPU clock で区切る。他の子 process は従来どおり `python -m prefab_sentinel.mcp_server` を使う。probe は production module を import して `main` を明示呼出しするため `-m` と同一の起動測定ではなく、`SystemExit` は process exit の証明でもない。process exit は親の bounded wait、marker 前後の区間は親の launch/failure timing で判断する。wall と CPU の差だけでは scheduler 待ちと I/O 待ちを区別できず、この診断は historical 15 秒 timeout の原因解決を主張しない。

公式 conformance runner は server を別 process で起動し、各 scenario を個別の output directory へ保存する。

```bash
uv run prefab-sentinel-mcp --transport streamable-http --port 8000
```

別 shell で:

```bash
for scenario in \
  tools-list \
  dns-rebinding-protection \
  http-header-validation
do
  npx --yes @modelcontextprotocol/conformance@0.2.0-alpha.11 \
    server \
    --url http://127.0.0.1:8000/mcp \
    --scenario "$scenario" \
    --spec-version 2026-07-28 \
    --output-dir "results/$scenario"
done
```

各 `results/<scenario>/checks.json` を検査し、failure が 0 件であることを acceptance criterion とする。normative docs や unit test の成功を、この runner の代替証跡にしない。

CI の strict gate は `tools-list`、`dns-rebinding-protection`、`http-header-validation` の 3 scenario だけを実行する。baseline、expected-failures、`continue-on-error`、`--suite`、`--requirements`、diagnostic tool は使わず、3 exit のいずれかが非 0、またはいずれかの `checks.json` に failure / warning があれば失敗とする。

`server-stateless` は alpha.11 が `test_missing_capability` structural diagnostic tool と `-32021` response を要求するため対象外とする。現行の固定 101-tool product には client capability を必要とする tool がなく、この probe のために public tool や hidden conformance hook は追加しない。初回の 4-scenario run と公式 source audit は historical evidence として保持し、upstream が non-applicable structural probe の skip をサポートした時点で scenario 採用を再検討する。

valid modern HTTP `initialize` は removed method として `404` / `-32601` になり、以前の RED gap は解消済みである。mixed-era stdio error は SDK が所有するため、test は error code と machine-readable structure / semantics を固定し SDK prose は固定しない。process-wide `ProjectSession` と `activate_project` の暗黙 continuity は deliberate product constraint かつ per-request stateless model からの既知逸脱のままなので、選択した 3 scenario の通過を full 2026-07-28 conformance と表現しない。error precedence と SDK 例外は [docs/api-reference.md](./docs/api-reference.md#エラーコード規約) を参照。

### Codex CLI 0.147.0 manual acceptance（非 CI）

raw-wire CI に加え、Issue #169 を閉じる前に exact `codex-cli 0.147.0` を isolated configuration で実行する。installed marketplace copy を測らないため、次の command は working tree server を明示 override する。packaged Codex MCP marker を同じく与え、`mcp_2026_07_28` を disabled（legacy `2025-06-18`）と enabled（modern `2026-07-28`）の両方で acceptance を取る。authentication、provider、または exact binary が無い場合は代替 version で置き換えず manual gate を blocked と記録する。

preflight では `codex features list` の出力に `mcp_2026_07_28` が存在することを確認する。top-level の `-a never` は Codex agent の承認方針、`mcp_servers.prefab-sentinel.tools.get_project_status.approval_mode="approve"` はこの non-mutating MCP tool だけの承認であり、別の制御である。distributed plugin は後者の承認を自動付与しないため、closure gate は両方を明示する。`default_tools_approval_mode="approve"`、`--yolo`、sandbox bypass は使わない。

```bash
codex --version
codex features list
codex mcp get prefab-sentinel \
  -c 'mcp_servers.prefab-sentinel.command="uv"' \
  -c 'mcp_servers.prefab-sentinel.args=["run","--extra","mcp","prefab-sentinel-mcp"]' \
  -c 'mcp_servers.prefab-sentinel.cwd="/path/to/prefab-sentinel"'

codex -a never exec --ephemeral --json \
  -C /path/to/prefab-sentinel \
  --disable mcp_2026_07_28 \
  -c 'mcp_servers.prefab-sentinel.command="uv"' \
  -c 'mcp_servers.prefab-sentinel.args=["run","--extra","mcp","prefab-sentinel-mcp"]' \
  -c 'mcp_servers.prefab-sentinel.cwd="/path/to/prefab-sentinel"' \
  -c 'mcp_servers.prefab-sentinel.env={CODEX_MCP_PROTOCOL_VERSION="2026-07-28"}' \
  -c 'mcp_servers.prefab-sentinel.required=true' \
  -c 'mcp_servers.prefab-sentinel.tools.get_project_status.approval_mode="approve"' \
  'Use only the prefab-sentinel MCP server. Call get_project_status exactly once with an empty argument object. Do not use shell tools and do not modify files. Print PASS only if the MCP tool call returns a structured result with code SESSION_STATUS.'

codex -a never exec --ephemeral --json \
  -C /path/to/prefab-sentinel \
  --enable mcp_2026_07_28 \
  -c 'mcp_servers.prefab-sentinel.command="uv"' \
  -c 'mcp_servers.prefab-sentinel.args=["run","--extra","mcp","prefab-sentinel-mcp"]' \
  -c 'mcp_servers.prefab-sentinel.cwd="/path/to/prefab-sentinel"' \
  -c 'mcp_servers.prefab-sentinel.env={CODEX_MCP_PROTOCOL_VERSION="2026-07-28"}' \
  -c 'mcp_servers.prefab-sentinel.required=true' \
  -c 'mcp_servers.prefab-sentinel.tools.get_project_status.approval_mode="approve"' \
  'Use only the prefab-sentinel MCP server. Call get_project_status exactly once with an empty argument object. Do not use shell tools and do not modify files. Print PASS only if the MCP tool call returns a structured result with code SESSION_STATUS.'
```

`mcp_2026_07_28` が feature list に無い場合は、この gate を blocked とする。両 feature mode で server startup、tool discovery、1 回の `get_project_status({})`、structured `SESSION_STATUS`、final `PASS` を確認する。

### Claude Code 2.1.218 isolated-config acceptance（非 CI）

Issue #169 を閉じる前に exact `claude 2.1.218` を、毎回新しい `CLAUDE_CONFIG_DIR` を持つ isolated MCP configuration で確認する。現 correction tree は `Connected`、modern-only base は `Failed to connect`、`2025-11-25`-only compatibility tree は `Connected` でなければならない。この three-way health check の handshake/discovery health が必須 protocol gate である。Claude model authentication が利用可能な場合だけ、代表 `get_project_status({})` tool call acceptance も試行して記録する。model authentication は MCP startup と別なので、利用不能でも health check の合否を置き換えない。

## ユニットテスト

`scripts/run_unit_tests.py` が `unittest_parallel` のラッパーで、3 段の preflight（stale `mutants/` 検出 → `mcp` extra 検出 → `unittest_parallel` 検出）を順に通してからテストを発火する。

mutmut sanity tests は repository root ではなく一時コピーした isolated project root を `cwd` として実行する。sanity fixture は `prefab_sentinel/contracts.py` と専用の最小 pytest test だけをコピーし、`mutmut.__main__.cli()` を import-shim 経由で呼ぶ。これにより sanity 実行中の `mutants/` artifact は temp tree 側へ閉じ込められ、既定の `unittest_parallel` worker が repository-root `mutants/` を import 対象として観測する race と、`python -m mutmut` 経由で発生する `multiprocessing.set_start_method('fork')` double-init を作らない。repository root に既存 `mutants/` がある場合の stale preflight は引き続き exit code 3 で停止する。

```bash
# 全テスト（並列、verbose）— `--extra mcp` は MCP サーバーをインポートする ~14 テストの collection エラー回避に必須（issue #217）
uv run --extra test --extra mcp python scripts/run_unit_tests.py

# 特定テストだけ（-k は unittest_parallel が pytest と同じセマンティクスで解釈）
uv run --extra test --extra mcp python scripts/run_unit_tests.py -k patch_plan

# pytest 経由（verbose）
uv run --extra test --extra mcp python -m pytest tests/ -v

# mcp extra をどうしても避けたい（mutmut セッション中など）場合の opt-out
PREFAB_SENTINEL_RUN_TESTS_SKIP_MCP_EXTRA=1 uv run --extra test python scripts/run_unit_tests.py
```

ランナー固有の終了コード（CI / 運用が失敗モードを切り分けるためのもの）:

- `0` — テスト全件 pass
- `1` — テスト少なくとも 1 件 fail（`unittest_parallel` のデフォルト）
- `2` — `unittest_parallel` がインストールされていない（`--extra test` 未付与など）
- `3` — リポジトリルートに stale `mutants/` ツリーが残っている（mutmut の作業ツリーがインポート対象を遮蔽するため preflight が中止する。`rm -rf mutants/` で回復）
- `4` — `mcp` optional dependency が import できない（`--extra mcp` 未付与など。`PREFAB_SENTINEL_RUN_TESTS_SKIP_MCP_EXTRA=1` で迂回可）

ユニットテストの対象は propertyPath 解決・配列操作の境界値・参照逆引き、patch plan の正規化（v1→v2 変換、リソースバッチ、ブリッジリクエスト構築）、bridge response のバリデーション、orchestrator のサービス連携パイプライン（モック経由）。

テストファイルは原則 1 ソースモジュールにつき 1 ファイル（[テストファイル配置](#テストファイル配置) 表が正本）。新規テストは envelope value-pinning（`tests._assertion_helpers.assert_error_envelope`）を使い、code / severity / field / message を値で固定する。`assertRaises` 系も value-pin 必須（`tests/test_assertion_density.py` が AST meta-test として強制）。

### テストファイル配置

D3 オーケストレータのスナップショット試験（issue #148）はファイル名 `tests/test_d3_orchestrator_snapshots.py` を正本とする（issue #161）。`tests/test_orchestrator.py` と `tests/test_orchestrator_patch.py` は既に高ボリュームの MagicMock 駆動テストおよび missing-GUID コントラクトテストで占有されており、スナップショットテスト一式を移植する利得がない。新しい inspect / wiring / patch オーケストレータのスナップショットは同ファイルへ追記する。

| ソースモジュール | テストファイル |
|---|---|
| `prefab_sentinel/unity_assets.py` | `tests/test_unity_assets.py` |
| `prefab_sentinel/patch_plan.py` | `tests/test_patch_plan.py` |
| `prefab_sentinel/orchestrator.py` | `tests/test_orchestrator.py` |
| `prefab_sentinel/contracts.py` | `tests/test_contracts.py` |
| `prefab_sentinel/services/*.py` | `tests/test_services.py` |
| `tools/unity_patch_bridge.py` | `tests/test_unity_patch_bridge.py` |
| `prefab_sentinel/udon_wiring.py` | `tests/test_udon_wiring.py` |

## 統合テスト

`tests/test_*_integration*.py` 系は実 Unity Editor + 常駐 Editor Bridge を前提とする。対象は Base / Variant / Scene の三層編集と、参照修復から実行検証までの E2E。CI では走らせず、ローカルでのみ実行する。

- `UNITYTOOL_BRIDGE_WATCH_DIR` を Unity Editor の Editor Bridge ウィンドウで指定した watch ディレクトリに向けて export する。
- Unity Editor を起動し、`PrefabSentinel > Editor Bridge` メニューから EditorWindow を開いて watch ディレクトリを有効化する。
- `UNITYTOOL_UNITY_PROJECT_PATH` を対象 Unity プロジェクトルート（`Assets/` の親）に設定する。WSL では Windows パス（`D:/...`）と WSL パス（`/mnt/d/...`）の両方を受け付ける。
- 必要に応じて `UNITYTOOL_UNITY_LOG_FILE`（`collect_unity_console` 用）と `UNITYTOOL_UNITY_TIMEOUT_SEC`（ポーリング上限秒、既定 120）を設定する。
- 実行: `uv run --extra test --extra mcp python -m pytest tests/test_mcp_server.py -v -k integration` など。
- ホストシェルに `UNITYTOOL_BRIDGE_WATCH_DIR` が残っていてもテストは決定的な未配線状態から開始する（`setUp` / サブプロセス起動時に pop される、issue #88 / #89 / #270）。export を切らずにユニットテストを走らせても `scripts/run_unit_tests.py` は green を維持する。
- live Editor Bridge E2E（`tests/test_mcp_server.py::test_*_live_*`）はさらに `UNITYTOOL_BRIDGE_E2E_LIVE=1` の opt-in が必要。未設定時は skip される。

### live valid-response compatibility probe

Bridge C# を変更した release candidate は、`deploy_bridge` 後に Unity Console の compiler error が 0 件であることを operator が確認してから、同じ local MCP server process で read-only probe を行う。`activate_project(project_root=..., scope=...)`、`get_project_status()`、`editor_reflect(action="search", query="GameObject", scope="unity")`、`validate_runtime(profile="editor_console_only")` の順に呼ぶ。status は session/live project root 一致、Bridge 接続済み、version mismatch なし、共通6フィールドの型が正しい `severity="info"` を受理する。transport / schema / version / compiler blocker は失敗とする。reflection と console-only runtime は valid envelope でなければならない。transport は response file を symlink でない regular file として 16 MiB 以下（ちょうど 16 MiB は許可）で読み取る。ClientSim はこの compatibility probe で起動しない。

Issue #167 の live compile acceptance は disposable / operator-authorized scope でのみ行う。実行前後の loaded dirty Scene と dirty native asset identities (`AssetDatabase.IsNativeAsset`) を read-only に記録し、`validate_runtime(profile="compile_only", confirm=True, change_reason=..., out_report="Audit/runtime-validation.json", generated_asset_policy=...)` の response と report を比較する。`out_report` は `Assets/` 外で、strict runtime Bridge payload の `preflight` / `compile` / `clientsim` / terminal `result` を含む `runtime_validation_report.v1` でなければならない。`compile_only` では `clientsim.executed=false` を明示し、generated / dirty delta、partial compile failure、no save / revert / dirty-clear / generated cleanup を確認する。compound `clientsim` の live acceptance は Task 専用の disposable scene と明示承認がある場合だけに限る。

## 回帰テスト

既知不具合の再現ケースをテストに固定し、同じ破損が再発したら即座に落とす。

- **対象** — Broken PPtr / missing fileID と、UdonSharp の null reference 例外。過去に 1 度でも発生した破損パターンは最小再現の YAML フィクスチャとして `tests/` に固定する。
- **方針** — 修正 PR は再現ケースを先に追加し、修正前は赤・修正後は緑になることを確認する。固定した再現ケースは四半期 mutation 走行の入力にもなる。
- **配置** — 回帰フィクスチャは対象モジュールの 1 テストファイル（[テストファイル配置](#テストファイル配置) 表）に追記する。

## Local Unity Bridge acceptance (Issue #186)

`scripts/run_unity_bridge_acceptance.py` is the only local acceptance command for a Bridge-changing checkout. It is deliberately outside normal unit tests and CI: it needs a running Unity 2022.3 Editor with an already configured Editor Bridge, a saved/clean loaded Scene setup, VRChat SDK Base/Worlds and UdonSharp readiness, a clean managed source surface, an existing Unity Editor log whose startup `-projectPath` identifies that same project, and a project-contained report path outside `Assets/`. Multiple simultaneous Editors must use a target-specific Unity `-logFile` path; the shared default `Editor.log` is not valid evidence when another project owns it.

The command is an explicit mutation opt-in. Without `--confirm-live` it returns `ACCEPTANCE_OPT_IN_REQUIRED` before project activation, deploy, Bridge request publication, or fixture creation. It never starts, saves, foregrounds, or discards the user's Editor work.

```bash
uv run --extra mcp python scripts/run_unity_bridge_acceptance.py \
  --project-root /path/to/UnityProject \
  --scope Assets/AcceptanceScope \
  --target-dir /path/to/UnityProject/Assets/Editor/PrefabSentinel \
  --watch-dir 'D:\\UnityProject\\prefab-sentinel' \
  --unity-log-file /path/to/Editor.log \
  --out-report /path/to/UnityProject/IssueAcceptanceReports/issue-186.json \
  --confirm-live
```

The fixed order is `source → preflight → deploy → compile/reload → environment → smoke → cleanup`: the controller first reserves the report and proves source plus clean Editor-state preconditions through the already installed Bridge, deploys only through #193's safe-deploy contract, and observes the first changed compile from path-free DLL/log identity evidence outside the new Bridge API. The canonical pre-deploy target manifest plus the running Bridge version determine changed versus no-op; matching files with an older loaded DLL remain a changed deployment. The post-deploy response must carry #193's applied promotion state, final manifest, and version, while the report's `changed_deploy` is derived from that pre-deploy comparison instead of a non-canonical response field. Changed compile observation drains immediately available append-only log evidence in 64 KiB chunks, sleeping only at EOF, and accepts a new stable DLL only after Unity's post-baseline `Reloading assemblies after finishing script compilation.` marker, so unrelated DLL/log activity cannot complete the phase. After the new Bridge reloads, a fresh status must supply Unity `2022.3.*` and ready VRChat SDK Base/Worlds/UdonSharp versions before any fixture mutation begins; this post-reload boundary lets one command upgrade an older compatible Bridge without treating #186-only status fields as pre-deploy inputs. The environment gate waits at most 120 seconds, polling once per second only while the structured Bridge state is `unavailable`; other blockers and readiness failures remain immediate failures. The terminal postcondition observation reuses the same bounded wait because cleanup asset refresh can create the same heartbeat gap. The controller then runs the bounded six-case smoke and same-run cleanup. `activate_project` may synchronously build the project-wide GUID/script caches, so the acceptance transport gives that one call the existing 300-second maximum deadline while ordinary public probes retain the 30-second deadline. Once the fixture-owning smoke request begins, status, idempotent cleanup, final project state, and final Console are mandatory even when smoke/status fails or is interrupted. Terminal precedence is cleanup failure, then postcondition failure, then smoke failure; every result retains the postcondition observation. A clean normal result additionally requires the six exact passing cases, the echoed run ID, `fixture_owned=true`, `lease_phase="smoke_complete"`, same-run status requiring cleanup, and cleanup evidence for restored Scene setup, one fixture root, five request artifacts, and lease removal. #193 and #194 must both be merged before a live result is accepted; #194 owns the transient reload-heartbeat noise boundary. This repository is not itself a Unity project, so offline checks never substitute for the live compile/smoke evidence.

The terminal JSON is `unity_bridge_acceptance.v1`, is atomically published to `--out-report`, and must equal the command's one JSON stdout value as parsed objects. `audit`, semantic evidence sections, and `result.phases` are independently allowlisted: absolute project/watch/log/request/lease/report paths, raw tool messages/diagnostics, and raw exceptions are forbidden. Preserve a failure report as evidence; a zero exit code is reserved for `ACCEPTANCE_OK` only.

Compile observer regressions pin three evidence boundaries: log inputs must be regular files (a FIFO is rejected even without a writer), replacing a valid baseline log with a same-path FIFO fails continuity without waiting for a writer, and a success-ready observation must finish strictly before the deadline. The deadline tests include the canonical reload marker and two identical observations of a new DLL, then consume the remaining time during the second observation so the final timeout check, not a loop-entry check, decides the result. These cases cover Issues #220, #271, and #221 without changing the live compile protocol.

Issue #253 also pins marker arrival after the third or fourth observation of an already stable new DLL. Stability means at least two consecutive identical observations and remains valid while that identity is unchanged; a later post-baseline marker can complete the observation only before the original deadline. Missing or pre-baseline markers, identity changes, and completion at or after the deadline remain covered as rejection boundaries.

A compiler error may move to sanitized `superseded_compiler_errors` history only when Tundra's additional-run boundary is followed by the same `ScriptAssemblies` graph and that generation proves, in order, the PrefabSentinel.Editor Csc, DLL copy, complete build-success prefix, and reload marker with no later error; that stricter evidence is required because a success string alone cannot identify which failed generation it replaced. The DLL must still differ from baseline and remain identical for two consecutive samples, while an unscoped error, another graph, incomplete evidence, deadline, or log-continuity loss remains a failure. The report records unresolved and superseded compiler-error counts plus only the sanitized superseded CS codes; graph paths and compiler messages remain private.

Non-error generation events are committed only after their logical line ends and each unfinished decoded line is limited to 4,096 characters. This bound is over twice the measured v296 maximum of 1,565 bytes; overflow fails closed because retaining or discarding a larger unfinished line would make generation evidence ambiguous. At current EOF, a compiler code followed by an observed non-word delimiter is already failure evidence. Any other non-whitespace pending text blocks success until LF resolves it; this deliberately means an unrelated unterminated final log line can time out rather than risk accepting hidden failure evidence. The exact fixed reload marker is the only successful EOF event and must occupy the whole pending line, preserving the pre-existing no-final-newline completion contract without accepting a suffix match after error text.

### PR evidence checklist

- Record the exact offline gate commands and counts, including the schema fixture verification.
- Record the live command, terminal report path, SHA-256, Unity version, package readiness, checkout/package/Bridge/manifest identity, and the #193/#194 merge references.
- Record independent compile observation, Console error count, bounded smoke case results, lease cleanup, and final Scene/dirty-state comparison.
- Run compiler-error, Bridge-timeout-after-lease, smoke-failure, and cleanup-failure canaries in isolated runs; verify cleanup between runs.
- Do not commit local reports, Unity-generated fixtures, Editor logs, or watch-directory artifacts.

An unresolved lease is a recovery-only condition. Supply the same 32-character lowercase-hex ID with all normal required arguments and explicit opt-in:

```bash
uv run --extra mcp python scripts/run_unity_bridge_acceptance.py \
  --project-root /path/to/UnityProject \
  --scope Assets/AcceptanceScope \
  --target-dir /path/to/UnityProject/Assets/Editor/PrefabSentinel \
  --watch-dir 'D:\\UnityProject\\prefab-sentinel' \
  --unity-log-file /path/to/Editor.log \
  --out-report /path/to/UnityProject/IssueAcceptanceReports/issue-186-recovery.json \
  --confirm-live \
  --recover-run-id aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
```

Recovery reserves/publishes its report, activates the configured project, requests same-run status and cleanup, verifies the final project state, and stops. It never runs source identity, deploy, compile, smoke, a new run, or automatic retry. Successful recovery returns `ACCEPTANCE_OK`; status/cleanup failure returns `ACCEPTANCE_CLEANUP_FAILED`, and an unsafe final project state returns `ACCEPTANCE_POSTCONDITION_FAILED`.

## Mutation testing

[mutmut](https://github.com/boxed/mutmut) による mutation testing は **四半期ごとに 1 回フル走行する**（対象 mutmut バージョン: **3.5.0**）。CI には組み込まない。設定は `pyproject.toml` の `[tool.mutmut]` テーブルが正本（audited path = `prefab_sentinel/`、`do_not_mutate`、`also_copy` リスト、`pytest_add_cli_args_test_selection` のマーカーフィルタ）。

`do_not_mutate` は mutmut 3.5.0 では **ソースファイルパスに対する `fnmatch` グロブ**として評価される除外リストである（`Config.should_ignore_for_mutation` がファイルパスを `fnmatch` するだけで、コード構造・式・mutant 名にはマッチしない）。構造単位（`*logger.*` のようなコード式パターン）の抑制には使えず、現状は **空** で運用する — ファイルパスを列挙すれば campaign が mutate する path を狭めてしまい、これは Non-Goal（監査対象 path・モジュール集合を狭めない）に反するため。trivial な構造単位 survivor は `do_not_mutate` ではなく四半期 survivor 分類で扱う（下記）。

監査対象モジュール（P0/P1、6 件）:

- `prefab_sentinel.services.reference_resolver`
- `prefab_sentinel.services.prefab_variant`
- `prefab_sentinel.services.serialized_object.patch_validator`
- `prefab_sentinel.services.runtime_validation.classification`
- `prefab_sentinel.orchestrator_postcondition`
- `prefab_sentinel.orchestrator_validation`

```bash
# パッケージ全体に対してフル走行（[tool.mutmut].paths_to_mutate を使用）
uv run mutmut run --max-children 180

# 1 モジュールだけ走行（mutmut 3.5 の positional 引数は mutant 名フィルタであり
# ファイルパスではない。ファイルパスを渡すと clean tree 上で
# `AssertionError: Filtered for specific mutants, but nothing matches` で停止する。
# dotted モジュール名のグロブを渡す — 生成は package 全体、実行をそのモジュールに絞る）
uv run mutmut run 'prefab_sentinel.services.reference_resolver.*' --max-children 180

# 走行直後に killed / survived を集計（CI 永続化なし、次の `mutmut run` で前回状態は失われる）
uv run mutmut results

# 集計を Markdown / CSV / JSON で出力（`scripts/mutmut_score_report.py`）
uv run python scripts/mutmut_score_report.py --audited-only --format markdown
```

`mutants/` は mutmut の作業ツリーで、`.gitignore` と `[tool.ruff].extend-exclude` で除外済み（走行後の `git status` / `ruff check` には現れない）。survived は critical / trivial / equivalent の三分類で四半期レポートに記録する：critical はテストでキル、trivial は四半期 survivor 分類に証跡を残すにとどめる（`do_not_mutate` はファイルパスグロブで構造単位の trivial mutant を抑制できないため追加しない）、equivalent も四半期レポートで証跡を残す。詳細運用カデンスは [AGENTS.md の Mutation testing 運用](./AGENTS.md#mutation-testing-運用) を参照。

並列ワーカー数 `--max-children 180` は固定値で運用する：開発機の物理コア数（最大 64 想定）の約 3 倍に取り、CPU バウンド・I/O 待ち混在の走行で待ち時間を埋めつつ、ワーカー間で `pytest` プロセスがスラッシングしない値として実測で選定した。`mutmut` の走行状態は実行間で永続化されないため、集計は `mutmut results` を同じ走行直後に呼ぶ。

**スコア集計（`scripts/mutmut_score_report.py`）** — 四半期走行直後の `mutmut results` 出力をモジュール単位で集計する専用スクリプト（issue #169）。Markdown 表 / CSV / JSON で出力でき、CSV ヘッダには走行日 (`run_date`) / mutmut version / `parallelism` を含めて推移を時系列で蓄積する。スコアは `(killed + timeout) / (killed + survived + timeout)` で計算する（`not_checked` は分母から除外）。`mutmut results` が非ゼロ終了した場合はスクリプトが exit code 4（`MUTMUT_SUBPROCESS_FAILURE_EXIT_CODE`）で停止し、stderr を透過する。四半期レポートは `docs/quarterly_mutmut_report_template.md` のテンプレートを起点に作成する。

**Orphan-test detection（`scripts/find_orphan_tests.py`）** — 既存の mutmut キャッシュに対し「ある test file を除外しても killed-mutant set が縮まらない」テスト（= 0 kill のテスト）を洗い出す候補リスト出力用スクリプト（issue #272）。CI には組み込まない四半期手動カデンス。検出 sentinel は `mutants/mutmut-stats.json` で、sentinel が無い状態では `SystemExit(2)` で停止して `uv run mutmut run` を促す。出力は作業ディレクトリ直下の `mutmut_orphan_tests.json`。

**Trivially-passing assertion meta-test** — `tests/test_assertion_density.py::TestTriviallyPassingAssertions` が `assertEqual(x, x)` / `assertIs(x, x)` / `assertTrue(True)` / `assertFalse(False)` の 4 形を検出し、ミューテーション検知に寄与しない自明アサーションがソースツリーに混入することを meta-test レベルで拒否する（issue #272）。

**非監査 low-score モジュールの監査保留** — `prefab_sentinel.watcher`（`watchfiles` 依存と Editor Bridge file-IPC ポーリングループにより unit 環境で再現できない経路を多数含む）と `prefab_sentinel.editor_bridge`（file-IPC 経由でしか執行できないハンドラ群）は監査対象 6 モジュールに含めず、`[tool.mutmut].do_not_mutate` 拡張または untestable-mark を次サイクルで議論する（issue #211）。

**テストの書き方（envelope value-pinning）** — 新規テストは `tests._assertion_helpers.assert_error_envelope` を使い、code / severity / field / message-pattern を値で固定する。「例外が出る」だけのアサートはミューテーションが拾えない。`assertRaises` 系も同様に値固定が必須で、`tests/test_assertion_density.py` がリポジトリ全体を AST で歩いて全 `assertRaises` サイトにこのルールを meta-test として強制する。同じルールは [AGENTS.md の Mutation testing 運用](./AGENTS.md#mutation-testing-運用) にも置かれている。

### 四半期 run チェックリスト

四半期 mutation サイクルは以下を **必須ステップ** として 1 回の run 内で完結させる。項目 2〜4 は
かつて standing GitHub issue（#210 do_not_mutate 実効性検証 / #211 低スコアモジュール survived
分類 / #272 orphan-test 棚卸し）として恒久 open されていた自己監査タスクであり、本チェックリスト
への組み込みにより新規 standing issue を生まない self-contained な運用に移行する（issue #7）。

1. **フル走行とスコア集計** — `uv run mutmut run --max-children 180` を走らせ、直後に
   `uv run python scripts/mutmut_score_report.py --audited-only --format csv` で監査対象 6
   モジュールのスコアを集計する（`mutmut` の走行状態は run 間で永続化されないため集計は同一
   run 直後に行う）。
2. **`do_not_mutate` の検査**（旧 issue #210 / issue #28） — `[tool.mutmut].do_not_mutate` が
   **空のまま**であることを確認する。mutmut 3.5.0 の `do_not_mutate` はソースファイルパスへの
   `fnmatch` グロブであり、コード構造・式パターンには 1 件もマッチしない（過去の `*logger.*`
   等の構造グロブは完全に inert だった）。ファイルパスを列挙すれば campaign が mutate する
   path を狭めるため（Non-Goal 違反）、このリストにはエントリを追加しない。結果は四半期
   レポート §3 に記録する。
3. **survived ミュータントの三分類**（旧 issue #211） — 監査対象 6 モジュールに加え、非監査
   low-score モジュール（`prefab_sentinel.watcher` / `prefab_sentinel.editor_bridge`）の
   survived を critical / trivial / equivalent に分類する。critical はテストでキル、trivial は
   本ステップの分類記録（四半期レポート §3）に証跡を残すにとどめる（`do_not_mutate` には
   追加しない — 上記 2 参照）、equivalent はレポートに証跡を残す。
4. **orphan-test の棚卸し**（旧 issue #272） — 同一 run の mutmut キャッシュに対し
   `uv run python scripts/find_orphan_tests.py` を走らせ、`mutmut_orphan_tests.json` の
   0-kill テスト候補を確認する。各候補は削除・補強・据え置きのいずれかを判断し、根拠を
   レポートの action items に残す。
5. **四半期レポートの記録** — 上記の結果を `docs/quarterly_mutmut_report_template.md` の
   テンプレート（§1 run context / §2 スコア履歴 / §3 suppression-impact / §4 抑制パターン
   roster / §5 action items）へ転記し、CSV は `reports/mutmut_history.csv` に追記する。

## source_text_invariant マーカー

`tools/unity/` や `knowledge/` 等の **un-mutated tree を読むだけのリポジトリ同期テスト** は、`prefab_sentinel/` のミューテーションを観測できないノイズとなるため、`@pytest.mark.source_text_invariant` をモジュールスコープで宣言して mutmut の選択から除外する。

```python
# tests/test_xxx_source.py の冒頭に置く
import pytest

pytestmark = pytest.mark.source_text_invariant
```

宣言だけで `[tool.mutmut].pytest_add_cli_args_test_selection` の `-m "not source_text_invariant"` 単一フィルタから一括除外される。per-file の `--ignore=` を増やす必要はない。新規のリポジトリ同期テスト（AGENTS.md inventory との drift 検出など）を追加する際もこのマーカーで対応する。

### Tier 3 — 構造不変条件のみ

`source_text_invariant` マーカー付きの source-text テスト（`tests/test_*_source.py`）は **Tier 3 = 構造不変条件のみ**を pin する。C# Bridge の*振る舞い*検証は `tests/csharp/` の xUnit ハーネスへ移行済みであり（clean-win concern は H-2…H-8 / H-11 で完了。per-concern Tier 分類の正本は [`docs/csharp_bridge_tier_migration.md`](./docs/csharp_bridge_tier_migration.md)）、source-text テストに残るのは partial 構成・定義の唯一性・命名規約・定数ドリフト・xUnit クラスへの委譲配線といった、実行では検証できない構造事実のみである。各 source-text grep は照合前に C# コメント（`//` / `/* ... */`）を除去し、コメント中のリテラルが false-green を生まないようにする（issue #5 / #358）。新しい振る舞いアサーションは source-text テストではなく xUnit ハーネスへ書く。

例外的に、UnityEditor 参照が必要で xUnit ハーネスに取り込めない Bridge partial の live Editor API surface は、TAKT 内では source invariant で構造契約だけを pin する。`UnityEditorControlBridge.EditorState` partial は `EditorStateSnapshot` の dirty identity / `state_source` fields、`HandleGetEditorState` dispatch target、`EDITOR_STATE_ENUMERATION_LIMITED` diagnostic branch、root bridge constants の不移動を `tests/test_editor_control_bridge_source.py` で固定する。実 Unity compile / dirty identity smoke は下記「Unity 依存 Bridge C# のコンパイル検証」の手動 follow-up に回す。

## skip-reason の検証

`@unittest.skipUnless(condition, reason)` / `@unittest.skipIf(condition, reason)` は、**スキップが実際に適用されたときだけ** 対象クラスに `__unittest_skip_why__` 属性（値は `reason`）を設定する。スキップされない側（テストが実行される regime）では属性そのものが存在しない。

このため skip reason を検証する meta-test を素朴に書くと、検証が暗黙に無効化される:

```python
# ✗ 危険: テストが実行される regime では __unittest_skip_why__ が無く、
#    getattr の既定（空文字）と「reason が空」を区別できない
why = getattr(cls, "__unittest_skip_why__", "")
self.assertIn("PREFAB_SENTINEL_RUN_CSHARP_TESTS", why)  # 属性なし時は ""、無条件で空振り
```

正しくは、**検証対象クラスが実際にスキップされる側の条件で meta-test 自身をガード**する。検証対象が `skipUnless(cond, ...)` なら meta-test は同じ `cond` の補（`skipIf(cond, ...)`）でガードし、`__unittest_skip_why__` が確実に populate されている regime でのみ実行する:

```python
# 検証対象は skipUnless(opt_in, ...) — opt-in 未設定なら skip
# meta-test は skipIf(opt_in, ...) — opt-in 設定時は skip（= 補集合）
@unittest.skipIf(
    _is_opt_in_set(),
    f"Verifies the no-opt-in skip path; only runs when {OPT_IN_ENV_VAR} is unset.",
)
class CsharpHarnessCollectionSkipTests(unittest.TestCase):
    def test_skip_reason_names_the_opt_in_environment_variable(self) -> None:
        why = getattr(cls, "__unittest_skip_why__", "")
        self.assertIn(OPT_IN_ENV_VAR, why)
```

参照実装: `tests/test_csharp_screenshot_view_allowlist.py` の `CsharpHarnessCollectionSkipTests`。C# xUnit ハーネスの opt-in gate（`PREFAB_SENTINEL_RUN_CSHARP_TESTS`）が、スキップ時に環境変数名を含む reason を出すことを検証する。

## Tool discovery benchmark

`scripts/run_tool_discovery_benchmark.py` is an offline, deterministic manual
measurement of natural-language tool selection. It joins the executable tool
registry to `docs/tools.md`, then ranks the bilingual fixture at
`benchmarks/tool-discovery/queries.v1.json`; it neither calls an MCP tool nor
requires Unity or an Editor Bridge. Run it when discovery metadata, the
canonical catalog, or the fixture changes, and before making a discovery-product
decision. It is not a CI quality gate.

```bash
uv run --extra mcp python scripts/run_tool_discovery_benchmark.py \
  --fixture benchmarks/tool-discovery/queries.v1.json \
  --tools-doc docs/tools.md \
  --out-report /tmp/tool-discovery-report.json
```

The report records overall and Japanese/English recall@1/3/5/8, MRR,
zero-score queries, unsafe false-positive placements and query rate, and full
versus top-k schema context cost. Low recall is a successful measurement result,
not a failing process exit; only invalid configuration or report publication
returns a nonzero exit. The first measured result and evidence-backed decision
are kept in [docs/benchmarks/2026-09-02-tool-discovery.md](./docs/benchmarks/2026-09-02-tool-discovery.md).

## CI workflow

`.github/workflows/ci.yml` が現行 CI の唯一の workflow（issue #270 で `unity-integration.yml` / `unity-live-nightly.yml` / `unity-smoke.yml` の Unity 連動 workflow 群は削除済み）。

| job | トリガ | 内容 |
|-----|--------|------|
| `lint` | 全 push / PR / manual / weekly | `ruff check` → production-only `mypy prefab_sentinel/` → モジュール行数ゲート（`scripts/check_module_line_limits.py`） |
| `typecheck-tests` | 全 push / PR / manual / weekly | `uv sync --extra lint --extra test --extra mcp` → full test-target `uv run mypy prefab_sentinel tests --show-error-codes` |
| `unit-tests` | 全 push / PR / manual / weekly | `uv sync --extra test --extra mcp` → `uv run python scripts/run_unit_tests.py` |
| `changes` | 全 push / PR / manual / weekly | `tools/unity/**` / `tests/csharp/**` / `global.json` / `ci.yml` 自身の変更を `dorny/paths-filter` で検出し、`csharp` 出力フラグを立てる。版数の重複管理による drift を避けるため、pin の正本は [CI workflow](./.github/workflows/ci.yml) とする |
| `csharp-tests` | `changes.outputs.csharp == 'true'` または manual | `.NET SDK setup`（`global.json` で pin）→ `dotnet restore --locked-mode` → `dotnet build --no-restore --configuration Release` → `dotnet test --no-build --configuration Release` で `tests/csharp/` の全 suite を実行 |
| `performance-benchmarks` | weekly / manual のみ | synthetic 7-case benchmark を `--enforce` で実行し、成功・失敗にかかわらず JSON report を artifact として保存する。checked-in baseline は読み取り専用 |

Full test-target mypy は test 依存と MCP 依存も含むため、pre-commit には入れない。local / TAKT 検証では `uv run --extra lint mypy prefab_sentinel tests --show-error-codes` を走らせ、CI では `typecheck-tests` job が同じ対象を通常 gate として検証する。

`csharp-tests` は監視対象外の PR では skip され、branch protection 上では `skipped` 状態が success として扱われる（issue #290）。

## C# xUnit ハーネス

`tests/csharp/` の C# テストハーネス。`tools/unity/` の C# 橋ソースを、Python 側の source-text grep（`tests/test_editor_control_bridge_source.py` 等）から段階的に挙動実行型テストへ移すための土台。issue #290 の導入当初は sanity Fact 1 本のみだったが、現在の全 suite は後続 issue で追加した橋の pure-logic テストも含む。歴史的な bootstrap の件数を現行 suite の成功条件に流用しない（issue #225）。

#### nullable context の共有ソース方針（Issue #214）

nullable reference annotations を使う Bridge 共有ソースは、ファイル自身で `#nullable enable` を宣言して annotations と warnings の両方を有効にする。Unity のプロジェクト全体の compiler option や、xUnit project の `<Nullable>enable</Nullable>` に依存して注釈を成立させない。既存の null 判定、型注釈、nullable-enabled harness は維持する。

`PrefabSentinel.Tests.csproj` の `CheckBridgeNullableContext` は通常の `CoreCompile` 前に、同 project の linked `Compile` items を再利用して補助コンパイルを実行する。補助 pass の既定 context は `disable`、`CS8632` は error とし、対象リストを別途保守しない。DTO の代入側 consumer を含まないため出る `CS0649` だけを補助 pass 内で除外する。通常の harness / Unity の warning 設定は変更しない。補助 DLL は intermediate directory にのみ生成し、製品へ配布しない。

これは .NET SDK の compiler / reference assemblies を使う Unity-free regression gate であり、Unity 依存 partial や Unity 2022.3 の compiler そのものを検証しない。実 Unity の新規 compile で CS8632=0 / compiler error=0 を確認する受入条件は別途必要である。main 未収録の #202 専用 Bridge ファイルは、#202 候補へ同方針を適用し、その候補で実測するまで未検証として扱う。

#### 構成

| ファイル | 役割 |
|---|---|
| `global.json` | .NET SDK ピン。`10.0.100` を要求し `rollForward: latestFeature` で feature/patch 上振れを許容する。major drift は SDK 解決時に reject される |
| `tests/csharp/PrefabSentinel.Tests.csproj` | テストプロジェクト。`net10.0` / `RestorePackagesWithLockFile=true` で locked-mode restore を gate にする。依存は xUnit `2.9.3` / `xunit.runner.visualstudio 3.1.5` / `Microsoft.NET.Test.Sdk 18.5.1` |
| `tests/csharp/HarnessSanityTests.cs` | sanity-only Fact 1 本（`Assert.Equal(2, 1 + 1)`）。Discovery / 実行 / アダプタ / lock file の整合を実証するだけで、橋への参照は持たない |
| `tests/csharp/packages.lock.json` | コミット済みの NuGet 依存 lock。CI の `dotnet restore --locked-mode` が csproj とのドリフトを起動時に検知する |

#### ローカル実行

```bash
# 依存復元（plain restore は lock を再生成。CI 差分が出る場合はコミットする）
dotnet restore tests/csharp/PrefabSentinel.Tests.csproj

# ロックモード復元 then ビルド then テスト（CI と同型）
dotnet restore tests/csharp/PrefabSentinel.Tests.csproj --locked-mode
dotnet build  tests/csharp/PrefabSentinel.Tests.csproj --no-restore --configuration Release
dotnet test   tests/csharp/PrefabSentinel.Tests.csproj --no-build  --configuration Release

# 現在の全 suite の discovery を確認（実行せず一覧を表示）
dotnet test   tests/csharp/PrefabSentinel.Tests.csproj --no-build  --configuration Release --list-tests
```

全 suite は filter を付けずに実行し、restore / build / test がそれぞれ exit 0、テスト結果が `Failed: 0` / `Skipped: 0` で、全ケースが成功していることを確認する。`Passed` / `Total` はテスト追加に伴って変わるため固定値にせず、検証した revision とともに実際の discovery 一覧項目数と実行結果を別々に記録する。[xUnit の theory データ列挙](https://xunit.net/docs/config-runsettings#preenumeratetheories)は discovery 時または実行時に行われるため、一覧項目数と実行ケースの `Total` が常に等しいとは限らない。sanity Fact だけの discovery や、意図しない件数減少は正常な全 suite 実行と見なさず、上記の一覧で橋の pure-logic テストが含まれているか確認する。

`Passed: 1 / Total: 1` は、issue #290 の導入時に `HarnessSanityTests` の sanity Fact だけを対象にした歴史的な観測値である。現在の project 全体を実行する上記コマンドの期待値ではない。

#### 命名規約（フォローアップ抽出 issue 向け）

橋ソース内の pure-logic を抽出する際は、抽出先クラスを次の規約で名付ける:

- **真偽判定（boolean classifier）の抽出** — `PrefabSentinel.<concern>.<Name>Classifier`（例: `PrefabSentinel.Mutation.ValueKindClassifier`）。名前で「分類関数」であることを宣言する。
- **状態を持つヘルパー（stateful helper）の抽出** — `PrefabSentinel.<concern>.<Name>Buffer` または `PrefabSentinel.<concern>.<Name>Store`（例: `PrefabSentinel.ConsoleCapture.ConsoleLogBuffer`）。名前でライフタイムを持つことを宣言する。

#### クロスプロジェクトのソース取り込み

抽出した pure-logic クラスは `tools/unity/` 配下に物理ファイルとして置き、`tests/csharp/` 側から MSBuild の `<Compile Include="..">` で取り込む。物理ソースは橋側に 1 部、テスト側は複製しない。

```xml
<ItemGroup>
  <Compile Include="..\..\tools\unity\PrefabSentinel.ConsoleCapture.ConsoleLogBuffer.cs"
           Link="Shared\PrefabSentinel.ConsoleCapture.ConsoleLogBuffer.cs" />
</ItemGroup>
```

Unity の `internal` メンバを参照する必要が生じた段階で初めて、橋アセンブリに `InternalsVisibleTo("PrefabSentinel.Tests")` を追加する。issue #222 Phase 3 の `PrefabSentinel.Screenshot.ViewAllowlistClassifier` がこの取り込みパターンの初例。Python 側からは `PREFAB_SENTINEL_RUN_CSHARP_TESTS` を立てた場合のみ `tests/_csharp_harness.py:run_csharp_tests` 経由で `dotnet test` をサブプロセス起動し、未設定時は collection 時点で skip する。

## Watch identity status verification

deterministic tests は marker の生成・reuse・collision、private status の 5-field schema、4096-byte 上限、1000 ms Bridge heartbeat と 5000 ms host freshness（境界を含む）を固定する。fresh mismatch は `SESSION_STATUS` warning、`EDITOR_BRIDGE_WATCH_DIR_MISMATCH`、`misconfigured`、watch-dir blocker 1件になり、`get_editor_state` を送らないことを確認する。

reload transition は injected millisecond clock で `fresh match -> missing at 4999 / 5000 -> fresh match` を artifact / public session の両層で通し、missing が `EDITOR_BRIDGE_STATUS_TRANSIENT`、warning、blocker 0件、editor state absent、live request 0件、private ERROR 0件になり、recovery 後は connected、blocker 0件、editor state present、live request exactly 1件へ戻ることを固定する。persistent outage は `fresh match -> missing at 5001 -> repeated missing -> fresh match -> missing at 5001` を通し、各 outage で `EDITOR_BRIDGE_STATUS_UNAVAILABLE`、bridge-connection blocker 1件、live request 0件、private ERROR exactly 1件になることを固定する。initial missing、schema / shape / size invalid、permission/read failure は private reason と固定 log message を個別に value-pin する。marker PermissionError の2回pollも ERROR exactly 1件とし、fresh observation / tracker reset 後にだけ再armする。successful `activate_project` 後に prior freshness が失われることも独立に検査する。public result / diagnostics に marker、watch path、status path、timestamp、private status、raw exception が含まれないことを value-pin する。

live acceptance では Bridge を deploy・compile errors = 0 と確認した後、Codex と Unity が同じ `UNITYTOOL_BRIDGE_WATCH_DIR` を使う status（info、blocker 0）を得る。次に disposable な別 directory を host にだけ設定し、Unity が元 directory を監視したまま 5 秒以内の `get_project_status` が上記 mismatch projection を返し、live request を送らず実 directory や marker が漏れないことを確認する。終了後は host 設定を元へ戻し、disposable directory の marker を削除してから directory を除去する。ClientSim はこの受入対象外である。

## Safe Bridge deployment verification (#193 / #186)

Issue #193 の offline gate は Unity Editor Bridge の host 設定を継承しない状態で実行する。Python full suite、型/lint、module/partial inventory、Bridge constant drift、locked C# restore/build/test、diff whitespace を一組として通す。

```bash
env -u UNITYTOOL_BRIDGE_WATCH_DIR \
    -u UNITYTOOL_BRIDGE_INSTANCE_ID \
    -u UNITYTOOL_UNITY_PROJECT_PATH \
    uv run --extra test --extra mcp python -m pytest -q

uv run --extra lint mypy prefab_sentinel tests --show-error-codes
uv run --extra lint ruff check .
uv run python scripts/check_module_line_limits.py
uv run python scripts/check_bridge_constants.py

dotnet restore tests/csharp/PrefabSentinel.Tests.csproj --locked-mode
dotnet build tests/csharp/PrefabSentinel.Tests.csproj --no-restore --configuration Release
dotnet test tests/csharp/PrefabSentinel.Tests.csproj --no-build --configuration Release
git diff --check
```

この offline gate は pure promotion model と Unity-only handler の source invariant までを検証する。promotion 後の handler は refresh barrier を保持したまま、complete promoted / rolled-back target の場合だけ `AssetDatabase.Refresh(ImportAssetOptions.ForceSynchronousImport)` を一度呼び、synchronous added / removed source inventory import を行う。固定 begin/end ログはその呼出しを挟み、`finally` の `AssetDatabase.AllowAutoRefresh()` はその後に実行する。barrier 解放と明示 import の間を空けないためである。direct `CompilationPipeline.RequestScriptCompilation()`、`AssetDatabase.ScheduleRefresh()`、`EditorApplication.delayCall`、refresh と compile request の二重経路を持たないことも固定する。deploy_bridge は Unity コンパイル成功を証明しない。`tools/unity/` の Unity dependency を含む partial は、次節の実 Unity gate まで未コンパイルである（Issue #213）。

Task 7 の live acceptance は Unity 2022.3 + VRChat SDK project で次を別に記録する。

1. legacy Bridge からの one-time bootstrap を safe redeploy 証跡と分離する。
2. 新 private action が接続された後、同一 bundle を `deploy_bridge` で再配備し、現在の project root / Bridge instance ID と一致する fresh Bridge 応答を経て、response の `manifest_sha256` / `bridge_version` が checkout と一致し、`promotion_state=already_current`、`barrier_used=false`、`target_complete=true`、`ownership_published=true`、`transaction_retained=false` であることを確認する。同一 target の timestamp と loaded Unity asset state が変化しないことも確認する。
3. Unity compile error 0 件と reload 後の Bridge identity を独立に観測する。
4. deterministic promotion failure が exact old bundle へ rollback し、unmanaged entry や不要な transaction residue を残さないことを確認する。
5. valid bundle を再配備し、compile error 0 件を再確認する。

Issue #213 の source filename 追加・削除 gate は staged-upgrade とする。更新開始時点の active pre-fix handler が promotion を実行するため、#244 のような filename-changing bundle へ直接一度で更新した結果を修正証拠にしない。まず同じ source filename set の refresh-aware same-layout bootstrap を配備し、reload 後にその handler が active であることを確認する。その後に add/delete bundle を配備し、promotion、synchronous import、compilation、reload、active manifest を別々に記録する。

Issue #193 は Issue #186 の final live acceptance より先に merge する。#186 は safe deploy response の `manifest_sha256` と `bridge_version` を消費し、Editor log と DLL identity で compilation/reload を独立検証する。Issue #194 の clean live log gate も #186 の別 dependency である。

## Unity 依存 Bridge C# のコンパイル検証

`tools/unity/` の C# 橋ソース 57 ファイルのうち、CI と上記 xUnit ハーネスがコンパイルするのは Unity 参照を持たない pure-logic 16 ファイルのみ。残る 41 ファイル（`UnityEditor` 参照 37 / `UnityEngine` のみ 4、うち 9 が VRChat SDK / UdonSharp surface に触れる）は **どのテストでもコンパイルされない**。これらは `source_text_invariant` の Tier 3 構造検証と `scripts/check_bridge_constants.py` の定数ドリフト検査の対象だが、いずれも型・メンバ参照を解決しない。ヘルパー抽出リファクタ（H 系）が呼び出し側を取りこぼした場合、実 Unity コンパイルでしか出ないエラー（旧入れ子型パス参照 `CS0426` / 無修飾呼び出し `CS0103` 等）が release をすり抜ける（issue #42 の実績）。

### CI コンパイルゲートを入れない理由（issue #43）

この 41 ファイルに対する CI 型解決ゲート（Roslyn 等）は検討の結果、採用しない。Bridge は本質的に Editor アセンブリ（`PrefabSentinel.Editor`）であり、検証が必要な 37 ファイルが `UnityEditor` に依存する。`UnityEditor.dll` には正規の再配布経路（公式 NuGet reference package 等）が存在せず — community NuGet（`Unity3D` / `UnityAssemblies` 系）はメタデータのみでローカル Unity install のパスを解決するだけ、実 DLL を含む版は `UnityEngine` のみ・旧バージョン・ライセンスがグレー — 加えて 9 ファイルが proprietary な VRChat SDK に依存する。reference assembly の調達には Unity install が不可避であり、Unity を入れる時点で「フル Unity コンパイルを避ける軽量ゲート」という前提が崩れる。GameCI 等によるフル Unity コンパイルは Unity ライセンス管理と CI 実行コストに見合わないと判断した。

### 安全網: 手動 deploy コンパイル確認

Unity 依存 Bridge C#（`tools/unity/` の `UnityEditor` / VRChat SDK 参照ファイル）を変更したら、`deploy_bridge` で実 Unity 2022.3 + VRChat SDK プロジェクトに配置し、Unity のコンパイルがエラー 0 件であることを手動で確認する。これが実 Unity 環境での受入 gate である。pure-logic を新規抽出して Unity 非依存にできた分は xUnit ハーネス（`<Compile Include>`）へ取り込み、検証対象を段階的に CI 側へ移すことで、この未検証 surface を縮小していく。

### ローカル C# 解析 project（issue #246）

Bridge の別 partial にまたがる意味的参照は、実 Unity compiler response file の条件定義と、同じ compilation の全 assembly references を対応させた解析 project で確認する。[tools/unity/README.md](tools/unity/README.md) に metadata 生成、明示的な参照準備、build、Serena の project-load / cross-partial 検証を定める。loose-file / miscellaneous project の空 diagnostics はコンパイル健全性の証拠にならない。解析 build はローカルでの型解決を検証するもので、上記の実 Unity gate や xUnit ハーネスを置き換えない。この区別を追加した理由は、意味的参照解析の前提を再現可能にしつつ runtime の受入境界を維持するためである。

### ClientSim lifecycle regression

`validate_runtime(profile="clientsim")` は通常の compile gate では実行しない。source invariants は runtime dispatcher が deferred response を待つこと、requested scene が sole loaded active scene であること、`playModeStartScene=null` の current-scene Play、public ClientSim settings/readiness API preflight、Resources prefab asset を除外した loaded live main instance の一意性、snapshot/preflight より前に固定した absolute deadline、全 project asset を load しない loaded-dirty asset 観測、full request + independent restoration lease の `SessionState` persistence、preflight/enter/ready/exit の別 timeout、before/runtime/after の3時点 report、duplicate multiplicity を保つ diff、asset candidate の before/after 対称差分、restore（失敗時は lease 保持）→ strict atomic response success → persisted-state clear の順序を固定する。Python service tests は operation timeout より cleanup 30 秒 + dispatch 5 秒だけ transport deadline が長いこと、`executed` が必須 boolean であること、実行済み report の欠落・型不正が fail-closed warning になること、runtime-only additions は警告せず post-exit residual additionsは警告すること、runtime snapshot のみ欠けても信頼できる before/after residual を落とさず、before/after snapshot 欠落時は推測差分を警告しないことを固定する。

2026-07-22 の修正確認は historical evidence であり、現行 acceptance procedure ではない。実 ClientSim acceptance は operator が明示承認した disposable scene に対する `validate_runtime(profile="clientsim", ...)` に限定する。開始前に Scene/asset dirty identity を記録し、response と `runtime_validation_report.v1` の compile / clientsim section を比較する。終端では `residual_added_*` / `residual_removed_*`、元の `playModeStartScene`、stable Edit Mode、最終 dirty identity を記録して終了する。acceptance の後に reload、save、revert、dirty-clear、generated-asset cleanup を行わない（without reload、without save）。

issue #112 の `editor_serialized_property_read` / `editor_serialized_property_list` / `editor_serialized_property_write` は `UnityEditorControlBridge.SerializedProperty` partial に実装されるため、Unity real-device validation はこの手動 deploy コンパイル確認の対象になる。`UnityIntegrationTests` には `SerializedPropertySmokeSupport` と `EditorCtrl_SerializedProperty_ReadListWriteDryRunNoOp` probe を置き、read / list / dry-run / confirmed write / no-op を同じ temporary GameObject で検証する。TAKT 内では source invariant までを自動確認し、実 Unity 2022.3 + VRChat SDK project で `deploy_bridge` 後に `editor_run_tests` から probe を実行することを follow-up 条件にする。

`UnityIntegrationTests.RunTestSuite` は開始時の scene setup を [`EditorSceneManager.GetSceneManagerSetup`](https://docs.unity3d.com/ja/2019.3/ScriptReference/SceneManagement.EditorSceneManager.GetSceneManagerSetup.html) で保存し、loaded scene がすべて保存済みかつ clean である場合だけ fixture mutation を開始する。unsaved / dirty scene はユーザー作業を暗黙に破棄できないため mutation 前に fail-fast する。各 case の fixture 再作成前と suite の `finally` では元 setup を復元し、test asset directory 配下の scene が loaded でないことを確認してから、戻り値を検査する [`AssetDatabase.DeleteAsset`](https://docs.unity3d.com/kr/2022.3/ScriptReference/AssetDatabase.DeleteAsset.html) で削除する。domain reload を伴う run-script stuck / recovery probe は同期 suite には含めず、Unity 非依存の `RunScriptCompilePendingCodeSelectorTests`（first timeout / threshold recovery）で固定する。

Issue #116 の `editor_create_generated_asset` / `editor_move_asset` は `UnityEditorControlBridge.AssetOps` partial に実装されるため、TAKT 内では Python wrapper tests、Unity-free C# `AssetOpsPathValidation` xUnit tests、source invariant までを自動確認する。実 Unity 2022.3 + VRChat SDK project では `deploy_bridge` 後に `editor_create_generated_asset` create dry-run、create confirm、`editor_move_asset` move dry-run、move confirm、lowercase `.rendertexture` reject、case-only move reject、confirm report equality を手動 smoke し、cleanup は既存 `delete_assets` で行う。

Issue #155 の `get_editor_state` dirty identity / blocker provenance は `UnityEditorControlBridge.EditorState` partial に実装されるため、TAKT 内では Python status/bridge tests と C# source invariant までを自動確認する。実 Unity 2022.3 + VRChat SDK project では `deploy_bridge` 後に Unity コンパイルエラー 0 件を確認し、dirty scene / Prefab Stage / material / ScriptableObject asset を用意した状態で `get_project_status` が `state_source="live_editor"`、dirty identity arrays、`dirty_or_save_blocker`、compile/playmode/stage blockers を返すことを手動 smoke する。

## Post-TAKT Unity Inspector verification

Issue #157 は 2026-07-22 に Unity 2022.3 + VRChat SDK + VizVid 導入済み project で受入済み。MCP Python / Bridge C# / plugin version はすべて `0.8.6` に揃え、Bridge 93 files の配置後に Unity compile error 0 件、Unity integration suite 103/103 passing を確認した。実 VizVid `JLChnToZ.VRC.VVMW.Core` fixture では raw SerializedObject surface 139 properties、7 arrays、null/local/texture/material ObjectReference、override-origin 有無を value-pin した。`screen_targets` profile は read-only view として成功し、未定義 Core view は available view と authoring evidence を伴う `INSPECTOR_PROFILE_INCOMPLETE`、保存済み profile は `valid=true`, `confidence=high` を返した。custom-editor degraded path、invalid/incomplete profile、zipped-array mismatch、writer rejection、atomic promotion/rollback は同一 revision の deterministic Python/C# tests で固定し、full suite 3274 passed / 7 skipped / 1405 subtests と targeted review suite 630 passed / 1140 subtests で再確認した。0.8.8 follow-up は inspected target 自身の numeric `local_file_id` を surface に追加した。writer probe は同一 surface identity を必須とし、Prefab component は exact `file_id`、ScriptableObject root は実 open-asset grammar の `$asset` で real orchestrator dry-run を通す。exact component handle を構築できない scene writable は false-positive を避けて fail-closed にする。

Issue #166 の repository-owned Unity integration suite は、`Assets/PrefabSentinelIntegrationTests/` 以下だけに保存する disposable Scene と実 `editor_inspect_serialized_surface` action を使い、次の九ケースを固定する。各ケースは operation 前後の loaded handle/path order、active identity、dirty state、target presence、および controlled operation window 内の warning count を比較する。injected ケースは feature-local な close / active restore / snapshot delegate だけを使い、捕捉した三 delegate の正確な値を `finally` で直接復元する。

| case | layer | required outcome |
|------|-------|------------------|
| borrow unique loaded clean target | real Unity operation | `Borrowed` target の surface を返し、open/close せず startup state と target presence を保つ。 |
| reject unique loaded dirty target | real Unity operation | raw dirty error、surface なし、open/close なし、dirty target と startup state を保つ。 |
| own unowned target and restore clean active Scene | real Unity operation | additive open した `Owned` target の surface を返し、target を閉じ、clean active identity と startup state を復元する。 |
| own target while unrelated active Scene is dirty and preserve it | real Unity operation | clean target を inspect し、unrelated active Scene の dirty bit と active identity をそのまま保つ。 |
| borrow non-active target among multiple loaded Scenes | real Unity operation | unique non-active target だけを borrow し、全 loaded order と別 Scene の active identity を保つ。 |
| target-not-found with owned cleanup | real Unity operation | exact raw target-not-found error を返し、normal surface を返さず、owned target を閉じて startup state を復元する。 |
| injected CloseScene=false | deterministic seam | close 呼び出しを 1 回記録して `false` を返し、restore-failed raw code、exact failed conditions、normal surface なし、意図した leaked-target after-state を確認する。 |
| injected active restoration failure | deterministic seam | cleanup 中に別の loaded Scene を active にした後、active-restore delegate が `false` を返し、restore-failed raw code と exact active failure conditions を確認する。 |
| injected postcondition mismatch | deterministic seam | cleanup 後の 2 回目 snapshot だけに 1 個の dirty-state mismatch を注入し、restore-failed raw code と単一の failed condition を確認する。 |

`tests/test_editor_control_bridge_source.py` の登録・seam cleanup invariant はこの matrix が suite から到達可能であることを補助的に検査するだけで、live behavior の証明ではない。受入は repository-owned integration case の実結果と Task 5 の exact-HEAD Unity run を正本とし、source token の一致だけでは完了扱いしない。既存 integration-suite harness はケース間の隔離に限って `RestoreOriginalSceneSetup` を使えるが、production Scene inspection は `RestoreSceneManagerSetup` を使わない。

再検証では次の protocol を使う:

1. Run `activate_project` and `deploy_bridge`, wait for compilation, and record `compile errors = 0`.
2. Create the synthetic `ExampleVideoCore` component fixture plus handler/module references, a ScriptableObject root, enum and array fields, null/missing/local/asset ObjectReference values, materials, and a nested Prefab variant.
3. Run `inspect_serialized_surface` for the component and ScriptableObject addresses. Value-pin ordered raw paths, array sizes, enum values, effective values, one-hop ObjectReference identity, and the absence/presence of origin when `include_override_origin` is false/true.
4. On the nested and variant fixture, confirm source/default, host override, effective value, and override origin are read from the intended layer.
5. Exercise candidate discovery with a runtime script, no custom editor, one active custom editor, and an unavailable assembly/editor case. Confirm bounded candidates and exact degraded reasons.
6. Run `inspect_with_profile` through `INSPECTOR_PROFILE_REQUIRED`, `INSPECTOR_PROFILE_INCOMPLETE`, `INSPECTOR_PROFILE_INVALID`, surface-unavailable, requested zipped mismatch, and valid Core/screen views.
7. Stage a read-only draft outside discovery, run `validate_inspector_profile`, atomically promote it, and rerun every intended view. For a writer-enabled draft, value-pin the numeric target `local_file_id` from that same surface and verify addressability uses the actual resource grammar with `dry_run=true` / `confirm=false`: exact `file_id` for Prefab components and `$asset` for a ScriptableObject root without `symbol_path`. Verify set/array operations through the real orchestrator, and verify a missing ID, unsupported scene component address, or writer rejection disables the declaration. Repeat with an invalid draft and verify the existing profile remains byte-identical.
8. Preserve the console output and MCP envelopes as issue evidence. Every value pin must pass before accepting a later Inspector-profile change.

## Post-TAKT Unity open Prefab transaction verification

Issue #156 は real-Unity layer と deterministic fault-injection layer の双方で受入済み。duplicate sibling、generated `#N` lookup、scalar / ObjectReference 変更、Prefab instantiation と component lookup を組み合わせた transaction で、post-save の異なる GameObject/Transform identity、source connection の保持、response-equal report、apply/postcondition failure 時の exact-preimage rollback を確認した。公開文書には再現可能な検証内容のみを残し、個別実行の ID や取得したオブジェクト識別値は掲載しない。

保存後の target 再読込と backing→proxy round-trip で、設定値と参照の保持を確認した。`validate_structure` で構造不整合がないこと、`validate_refs` で新たな broken GUID/fileID がないことを検証し、source に既存の unresolved-looking handles と transaction が導入した参照不整合を区別した。save/report/rollback failure と introduced structure/reference diagnostics は deterministic tests で固定している。

この live acceptance plan では `runtime_scene` を指定していないため ClientSim gate は起動しておらず、ClientSim の結果を #156 の受入証跡には数えていない。実 ClientSim を再検証する場合は、ユーザーの作業 scene ではなく disposable scene を使い、operator の明示合意、終了時 cleanup、verified-clean scene の再読込までを一組の手順とする。

再検証では次の protocol を使う。real-Unity layer は public MCP/Bridge contract で到達可能な状態を、deterministic fault-injection layer は valid Unity operations では任意に作れない corruption/persistence failure を覆う。Unit evidence は live success/rollback path の代替にしない:

1. Run `activate_project` and `deploy_bridge`, then record Unity compile errors = 0.
2. Create disposable target/source Prefabs containing duplicate-name siblings, a reference-bearing component, and a nested Prefab connection.
3. Dry-run and confirm one plan that composes `instantiate_prefab` → rename → generated relative `#N` lookup → component lookup → scalar/reference `set`, with non-empty `change_reason` and contained `out_report`.
4. Reload the saved target and value-pin source Prefab connection, post-save symbol path, GameObject/Transform IDs, scalar value, ObjectReference identity, actual override pairs, exactly one save route, and response-equal report.
5. Through the public MCP surface, exercise apply rejection, missing-component lookup, an explicit postcondition failure, and the configured compile / runtime failure path. Run a real ClientSim gate only in a disposable scene with explicit operator approval and cleanup; otherwise cover its transaction-failure contract with the deterministic harness. For each post-mutation failure, compare the restored Prefab byte/identity state to the preimage and inspect original/rollback/report results.
6. In deterministic transaction tests, inject save failure; introduced duplicate-fileID, orphaned-Transform, and broken-reference diagnostics; report-finalization failure; preimage-restoration failure; and rollback-refresh failure. Value-pin the diagnostic partitions and, for rollback failure, `critical`, `PATCH_ROLLBACK_FAILED`, the exact message, and preservation of both causes. Preserve the live envelopes and deterministic test log as issue evidence; both layers must pass before accepting a later transaction change.

### 安全網: dev 経路での visual 検証

コンパイルが 0 件でも、Unity 依存箇所の振る舞いは実機 SceneView で動かさないと確認できない（`SceneView.LookAt(instant:true)` の camera 同期挙動、`BakeMesh` の現ポーズ bounds、preset 角度の見え方など。issue #84 で実証）。リリース前に main / public mirror を待たず、dev 作業ブランチの資材だけで visual 検証する経路を残す。

**前提と制約**:
- visual 検証には **MCP plugin Python と Bridge C# が同じ commit の資材で揃っている必要**がある。bridge だけ手動配置しても plugin 側のリクエスト形が古いと検証が成立しない。
- Claude Code / Codex CLI が起動済みの plugin プロセス（`~/.claude/plugins/cache/.../prefab-sentinel-mcp` 等）は session 開始時に固定されるため、session 内で同 plugin を最新ソースに張り替えても反映されない。`.mcp.json` を編集して再起動するか、本節の ad-hoc 経路を使う。

**経路**: `uvx --from <local-path>[mcp] prefab-sentinel-mcp` で dev ソースから MCP server を一時起動し、stdio で `activate_project` → `deploy_bridge` → 検証ツールを呼ぶ Python script を走らせる。Python SDK 2.x の `Client` を `mode="2026-07-28"` で使い、legacy handshake を挟まず request ごとの namespaced `_meta` を送る：

```python
import os

from mcp import Client, StdioServerParameters
from mcp.client.stdio import stdio_client

params = StdioServerParameters(
    command="uvx",
    args=[
        "--from", "/path/to/prefab-sentinel[mcp]",
        "prefab-sentinel-mcp",
    ],
    env={**os.environ, "UNITYTOOL_BRIDGE_WATCH_DIR": "D:\\UnitySampleProject\\prefab-sentinel"},
)
async with Client(stdio_client(params), mode="2026-07-28") as client:
    assert client.protocol_version == "2026-07-28"
    await client.call_tool("activate_project", {...})
    await client.call_tool("deploy_bridge", {})
    await client.call_tool("<verify_tool>", {...})
```

実行は `uv run --with 'mcp>=2,<3' python /tmp/verify_<topic>.py`。Claude Code 設定の永続変更も bg uvx 残置もなく完結する。

**uv キャッシュの落とし穴**（astral-sh/uv#16196）:
- `uvx --from <local-path>` のデフォルト cache key は `pyproject.toml` / `setup.py` / `setup.cfg` のみ。`tools/unity/*.cs` を編集しても **wheel が rebuild されない** ため、修正済みソースが配置されず古い bridge が deploy される事故が起こる。
- 恒久対策として `pyproject.toml` に `[tool.uv] cache-keys` を追加し、`tools/unity/**/*.cs` / `*.asmdef` / `knowledge/**/*.md` を cache key に含めている（issue #84 修正のコミットで追加）。
- 即時のリカバリは `uvx --reinstall --no-cache --from ...` で起動するか、`uv cache clean prefab-sentinel` でパッケージキャッシュを落としてから起動する。

**Unity 側の手順**:
1. `deploy_bridge` 直後は `Library/ScriptAssemblies/PrefabSentinel.Editor.dll` の mtime / サイズが変わっていることを確認（変化なしなら Unity がまだ import していない）。
2. Unity Editor を**最前面に出して `Ctrl+R`** で AssetDatabase.Refresh を強制（background 化中は domain reload が保留される — グローバルメモリ `feedback-unity-background-defers-compile`）。
3. `editor_console`（severity=error）で `CS****` が残っていないことを確認。
4. 検証ツール（`editor_screenshot` 等）を呼んで結果を観察。screenshot は `D:\UnitySampleProject\<bridge-watch-dir>\screenshots\` に保存される。

**bridge dispatch 経路の確認**: 新しい branch を追加した bridge handler は、応答の `message` / `code` フィールドで分岐先が確認できる。例えば issue #84 の `HandleObjectCaptureScreenshot` 成功時は `"Object-capture screenshot of '...' (angle=...)"` を返し、既存 SceneView capture 経路の `"Scene view captured to ..."` と区別できる。視覚以前に文字列で経路同定する習慣をつける。

**issue #92/#93/#94/#95/#98/#101/#102/#103 batch probes**:
- Python focused: `uv run --extra mcp pytest tests/test_orchestrator_validation.py tests/test_mcp_tools_editor_exec.py tests/test_mcp_tools_editor_view.py tests/test_mcp_tools_editor_geometry.py tests/test_mcp_tools_editor_udonsharp.py tests/test_mcp_server.py tests/test_services.py`
- Unity-free C#: `dotnet test tests/csharp/PrefabSentinel.Tests.csproj --no-restore`
- Live Unity opt-in (`UNITYTOOL_BRIDGE_E2E_LIVE=1`): validate `profile="clientsim"` side-effect report, deterministic `editor_console` request correlation, `editor_screenshot(target_mode="world_space_ui")`, geometry chair-to-TargetButton distance, typed `editor_set_property`, and UdonSharp `values_json` array sync. Unity-dependent bridge partials still require `deploy_bridge` + Editor compile confirmation because CI/xUnit does not compile files that reference UnityEditor / VRChat SDK assemblies.
