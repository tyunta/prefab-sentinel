# 実行リファレンス

MCP サーバー / patch スキーマ / Unity bridge 連携 / 代表レポート出力フォーマットの正本。`README.md` はこのドキュメントへのポインタのみを持つ。

v0.4.0 で CLI (`prefab-sentinel` コマンド) は廃止され、MCP サーバーが唯一のインターフェースとなった。
検査・編集は MCP ツール経由で実行する（ツール一覧は [docs/tools.md](./tools.md) を参照）。

## 実行方法

`prefab-sentinel-mcp` が MCP server entry point。検査・編集はすべて MCP ツール経由で行う（ツール一覧は [docs/tools.md](./tools.md)）。公開 capability は Tools のみで、transport/version ごとの surface は次のとおり。

| Transport / version | request | notification |
|---|---|---|
| stdio `2026-07-28` | `server/discover` / `tools/list` / `tools/call` | `notifications/cancelled` |
| stdio `2025-11-25` | `initialize` / `tools/list` / `tools/call` | `notifications/initialized` / `notifications/cancelled` |
| stdio `2025-06-18` | `initialize` / `tools/list` / `tools/call` | `notifications/initialized` / `notifications/cancelled` |
| Streamable HTTP `2026-07-28` | `server/discover` / `tools/list` / `tools/call` | なし |
| HTTP legacy（`2025-11-25` / `2025-06-18` を含む）または `2025-06-18` より古い legacy revision | 非対応 | 非対応 |

stdio runner は最初の request で選ばれた era/revision を process lifetime に固定する。`2025-11-25` と `2025-06-18` の各 legacy client は新しい process を `initialize` で開始して lifecycle を完了し、`2026-07-28` client は request `_meta` を使う。HTTP は両 legacy revision を拒否する。modern discovery の `supportedVersions` は HTTP と同じく `["2026-07-28"]` のみで、既存 connection 上で legacy を選び直すことはできない。

### stdio（既定）

```bash
uv run prefab-sentinel-mcp
uv run prefab-sentinel-mcp --project-root /path/to/unity/project
```

stdio は既定 transport で、ホスト側が server process の stdin / stdout を所有する。1 server process は 1 選択済み protocol era と 1 logical client / project scope として扱う。

### local Streamable HTTP

```bash
uv run prefab-sentinel-mcp --transport streamable-http --port 8000
```

endpoint は `http://127.0.0.1:<port>/mcp`。host は `127.0.0.1` 固定で設定項目を持たず、port の既定値は `8000`、受理範囲は `1..65535`。MCP request は `/mcp` への POST のみで、GET / DELETE による stream や session termination は提供しない。2026-07-28 の HTTP core protocol には client-to-server notification がないため、request ID のない `notifications/cancelled` 等は現行 gate が HTTP `400` / `-32600` で拒否する。legacy session header が送られても session は作られず、response に session ID を発行しない。remote / shared server、public bind、TLS termination、認証はこの process の責務外である。

transport にかかわらず `ProjectSession` は process-wide application state で、MCP protocol session ではない。`activate_project` は後続 request が暗黙利用する state と cache / watcher を更新し、`tools/call` は process-wide に直列実行する。複数 client / project を分離する場合は server process も分ける。この continuity は deliberate product constraint であり、MCP 2026-07-28 の per-request stateless model に対する既知の逸脱なので、現行 server について full conformance は主張しない。責務境界は [ARCHITECTURE.md](../ARCHITECTURE.md#mcpserver--protocol-boundary)、request metadata と result semantics は [tool-conventions.md](./tool-conventions.md#mcp-protocol--result-境界)、protocol error の優先順位と stdio transport 例外は [api-reference.md](./api-reference.md#エラーコード規約) を参照。

環境変数プレフィックス（`UNITYTOOL_*`）は互換性のため現状維持とする（一覧は [CONFIGURATION.md](../CONFIGURATION.md)）。unit test は `scripts/run_unit_tests.py` で並列実行する（「CI / テスト実行」節参照）。

## レポート / ignore-guid

## Local Unity Bridge acceptance execution (Issue #186)

Use the explicit command only against an already-running, configured Unity 2022.3 Editor and a saved/clean Scene setup. It requires VRChat SDK Base/Worlds and UdonSharp readiness, a clean checkout managed surface, an existing Editor log, and a report destination contained by the project but outside `Assets/`.

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

`--confirm-live` is required. Without it the CLI emits one JSON result with `ACCEPTANCE_OPT_IN_REQUIRED`, exits nonzero, and does not activate a project, deploy, publish Bridge requests, or create a fixture. The controller never restarts or repeats a failed phase. Its sole readiness wait is the post-reload environment probe described below.

The fixed execution order is: report reservation and source identity → Editor preflight → #193 safe deploy → Bridge-independent DLL/log compile observation → reconnect/recompile → post-reload environment/Console probes → bounded smoke → same-run lease cleanup → exact final postconditions → atomic terminal report. Source identity and the pre-deploy target comparison use #193's canonical Bridge bundle manifest builder; no-op additionally requires the running Bridge version to match the source version. The deploy comparison does not maintain a second digest algorithm or consume an invented `changed_deploy` response field. For a changed target, the independent observer drains bounded append-only log chunks before sleeping and requires both a new stable DLL identity and Unity's post-baseline script-compilation completion marker. After recompile returns, the environment probe waits at most 120 seconds for `data.bridge.connection_state="unavailable"` to become a fresh status and polls once per second; every other response, blocker, version mismatch, or package-readiness failure is evaluated immediately and is never retried. Terminal postconditions reuse that exact bounded wait after cleanup, whose asset deletion can create the same transient heartbeat gap. The explicit `--watch-dir` is bound to both the MCP child and the parent-side private smoke/cleanup calls as an instance value; the command does not mutate ambient `UNITYTOOL_BRIDGE_WATCH_DIR`, so parallel acceptance transports remain isolated. #193 owns deployment and #194 owns transient reload-heartbeat handling; neither is duplicated here, and accepted live evidence waits for both dependencies.

### `unity_bridge_acceptance.v1` report

The atomically published JSON and the one JSON stdout value are response-equal JSON objects. Formatting is intentionally independent (the report remains pretty JSON while stdout is compact); consumers must parse both and compare objects, not bytes. Its top-level keys are `schema_version`, `audit`, `source`, `environment`, `preflight`, `deploy`, `compile`, `smoke`, `cleanup`, and `result`; `schema_version` is `unity_bridge_acceptance.v1`. Every phase section is a structured `{executed, success, code, data, diagnostics}` snapshot. An unattempted section has `executed=false`, `success=null`, `code=null`, empty `data`, and empty `diagnostics`; this makes early failures explicit rather than fabricating success. `result` carries `success`, `severity`, stable `code`, `message`, `failed_phase`, `phases`, and diagnostics. The report records command arguments only after redaction, Git/package/Bridge/manifest identity, Unity/package readiness, independent compile evidence, bounded case results, and cleanup/postcondition evidence.

The following JSON is the canonical parseable contract for this report version. The controller constructs each section and each `result.phases` entry from a phase-specific allowlist; raw tool messages, data, diagnostics, exceptions, and absolute paths are never copied into either representation.

```json
{
  "schema_version": "unity_bridge_acceptance.v1",
  "top_level_keys": [
    "schema_version",
    "audit",
    "source",
    "environment",
    "preflight",
    "deploy",
    "compile",
    "smoke",
    "cleanup",
    "result"
  ],
  "result_keys": [
    "success",
    "severity",
    "code",
    "message",
    "failed_phase",
    "phases",
    "diagnostics"
  ],
  "unattempted_section": {
    "executed": false,
    "success": null,
    "code": null,
    "data": {},
    "diagnostics": []
  },
  "audit_required_keys": ["opt_in", "mode", "arguments", "deadlines"],
  "deadline_values": {
    "recompile_timeout_sec": 120,
    "environment_timeout_sec": 120,
    "smoke_timeout_sec": 300
  },
  "section_data_keys": {
    "source": [
      "head",
      "branch",
      "managed_source_clean",
      "managed_dirty_paths",
      "package_versions",
      "bridge_manifest_sha256",
      "bridge_files",
      "mcp_protocol_revision",
      "mcp_server"
    ],
    "environment": [
      "unity_version",
      "required_packages",
      "connection_identity",
      "project_session"
    ],
    "preflight": [
      "config_validation",
      "report_reservation",
      "editor_state",
      "blockers"
    ],
    "deploy": [
      "changed_deploy",
      "expected_manifest_sha256",
      "observed_manifest_sha256",
      "manifest_equal",
      "expected_bridge_version",
      "observed_bridge_version",
      "bridge_version_equal"
    ],
    "compile": [
      "baseline",
      "observation",
      "secondary_recompile",
      "console"
    ],
    "smoke": ["reflection", "runtime_probe", "suite"],
    "cleanup": ["status", "cleanup", "postconditions"]
  },
  "recovery_only_sections": [
    "audit",
    "environment",
    "preflight",
    "cleanup",
    "result"
  ],
  "terminal_precedence": [
    "cleanup_failure",
    "postcondition_failure",
    "smoke_failure",
    "success"
  ],
  "redaction": {
    "allowed_path_forms": ["project-relative", "redacted-marker"],
    "forbidden": [
      "absolute_paths",
      "raw_messages",
      "raw_diagnostics",
      "raw_exceptions"
    ]
  }
}
```

Public reports and diagnostics must not contain watch-directory identity marker contents, request/response absolute paths, raw exceptions, stack traces, credentials, environment dumps, or unredacted local user paths beyond the project identity required for the result. Before path validation, audit arguments use redacted placeholders; only normalized, project-contained scope/target/report forms enter a terminal report after validation. Preserve failure reports outside `Assets/`; never commit reports, Editor logs, watch artifacts, or generated fixtures.

### Unresolved lease recovery

`EDITOR_CTRL_ACCEPTANCE_LEASE_UNRESOLVED` blocks a new run before fixture mutation. Recovery is recovery-only and has one public CLI spelling:

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

The mode reserves/publishes a terminal report, activates the project, requests private same-run status then cleanup, verifies the final project state, and stops. It never begins source identity, deploy, compile, smoke, another acceptance run, or an automatic retry. `ACCEPTANCE_OK` means recovery/postcheck succeeded; `ACCEPTANCE_CLEANUP_FAILED` or `ACCEPTANCE_POSTCONDITION_FAILED` are terminal failures. Cross-run cleanup is rejected; repeated same-run cleanup after a deleted lease is a successful no-op.

- レポート変換（検査結果 JSON → Markdown / JSON / CSV）は v0.4.0 で廃止された旧 `report export` CLI コマンドの機能だった。MCP ツールとしても公開していない（**Non-Goal**）。変換ロジックは内部関数 `prefab_sentinel.reporting.export_report` に残るが、CLI / MCP いずれの公開インターフェースも持たない。代表的なレポート出力フォーマットはドキュメント末尾の「代表レポート出力フォーマット」節を参照。
- ignore-guid ファイルは UTF-8 テキスト（1 行 1 GUID、`#` 以降コメント可）。`validate_refs` MCP ツールは `<scope>/config/ignore_guids.txt` を auto-load し（存在しなければ無視）、caller 指定の `ignore_asset_guids` 引数と union-dedupe で併用する。詳細は [CONFIGURATION.md](../CONFIGURATION.md) を参照。
- `validate_refs` MCP ツールの `top_missing_asset_guids` を使って無視候補 GUID を特定できる。`top_missing_asset_guids` / `top_ignored_missing_asset_guids` には GUID→アセットパスのベストエフォート解決結果（`asset_name`）が含まれる。

## CI / テスト実行

- `scripts/run_unit_tests.py` は `unittest-parallel` を使って unit test を並列実行する共通入口で、既定では `-s tests -t . -v -j 0` を使う。推奨呼び出しは `uv run --extra test --extra mcp python scripts/run_unit_tests.py`（`--extra mcp` は MCP サーバーをインポートするテストの collection エラー回避に必須。issue #217）。追加引数はそのまま `python -m unittest_parallel` に渡すので、`python scripts/run_unit_tests.py -j 4 -k patch_apply` のように絞り込みや job 数調整もできる。preflight 段階で stale `mutants/` ディレクトリ（exit 3）/ `mcp` extras 不在（exit 4）/ `unittest_parallel` 不在（exit 2）を切り分けて exit する。`mcp` extras 不在の preflight は `PREFAB_SENTINEL_RUN_TESTS_SKIP_MCP_EXTRA` を非空値で export すると bypass できる。
- `.github/workflows/ci.yml` は通常の lint / typecheck / unit / C# gate に加え、weekly schedule と `workflow_dispatch` に限って performance benchmark を実行する。push / pull_request では実時間計測を行わず、manifest・dispatch・集計・budget・report 契約の deterministic tests だけを実行する。

## Performance benchmark

`scripts/run_performance_benchmarks.py` は `benchmarks/inspection-performance.v1.json` を正本として、決定的に生成した synthetic Unity project 上で次の 7 ケースを測定する。

- filtered `inspect_wiring` cold / warm
- expanded `inspect_hierarchy` cold / warm
- `inspect_material_asset(mode="summary")` cold / warm（64 float properties に加え、実在 GUID へ解決できる non-null `_MainTex` を fixture に持たせ、shader / main texture / selected properties の projection を同時に通す）
- broad `validate_materials` cold

interactive ケースは fresh session ごとに 5 sample、broad validation は 3 sample を保存する。warm ケースは各 session で 1 回だけ warm-up し、その値を sample に含めない。判定は median のみで、wiring / hierarchy は `<10s`、material summary は `<5s`、broad validation は `<60s`。p95 は nearest-rank、MAD は median absolute deviation とし、sample の削除・外れ値 retry・budget 緩和は行わない。

現在値だけを採取する場合:

```bash
uv run python scripts/run_performance_benchmarks.py \
  --manifest benchmarks/inspection-performance.v1.json \
  --out-report performance-report.json \
  --enforce
```

比較対象の baseline と同一 host / fixture で比較する場合は、利用可能な公開コミットを `BASELINE_REF` に指定する（個別の開発履歴には依存しない）:

```bash
uv run python scripts/run_performance_benchmarks.py \
  --manifest benchmarks/inspection-performance.v1.json \
  --baseline-ref "$BASELINE_REF" \
  --baseline-out benchmarks/baselines/pre-pr159.json \
  --out-report performance-report.json \
  --enforce
```

`--baseline-ref` と `--baseline-out` は必ず対で指定する。checked-in baseline は automation から更新しない。historical filtered wiring は native `script_filter` invocation、historical material inspection だけは当時存在しなかった `mode="summary"` / `property_names` を除いた実 invocation を `historical-equivalent` として記録し、current budget 判定には使用しない。

依存影響の正本は manifest と各 report の `dependency_mapping`（各 cell に evidence 付き）。要約は次のとおりで、`direct` / `indirect` / `non-impact` を省略しない。

| issue | wiring cold / warm | hierarchy cold / warm | material cold / warm | broad materials cold |
|---|---|---|---|---|
| #143 | direct / indirect | direct / indirect | direct / indirect | direct |
| #144 | direct / direct | direct / direct | direct / direct | direct |
| #145 | non-impact / non-impact | non-impact / non-impact | non-impact / non-impact | non-impact |
| #146 | non-impact / non-impact | non-impact / non-impact | non-impact / non-impact | non-impact |
| #147 | non-impact / non-impact | non-impact / non-impact | non-impact / non-impact | direct |
| #148 | non-impact / non-impact | non-impact / non-impact | non-impact / non-impact | non-impact |
| #149 | direct / direct | direct / direct | non-impact / non-impact | non-impact |
| #154 | direct / direct | direct / direct | direct / direct | direct |

## Tool discovery benchmark

`scripts/run_tool_discovery_benchmark.py` measures the current executable MCP
tool metadata against the committed bilingual fixture without changing the MCP
server or invoking a tool. It is a manual offline benchmark, not an enforcement
gate.

```bash
uv run --extra mcp python scripts/run_tool_discovery_benchmark.py \
  --fixture benchmarks/tool-discovery/queries.v1.json \
  --tools-doc docs/tools.md \
  --out-report /tmp/tool-discovery-report.json
```

All arguments are required:

| Argument | Meaning |
|---|---|
| `--fixture` | Versioned Japanese/English query fixture JSON. |
| `--tools-doc` | Canonical `docs/tools.md` category catalog. |
| `--out-report` | Destination for the atomically published JSON evidence. |

After successful argument parsing, the script prints one compact JSON status
object. Invalid CLI arguments are handled by `argparse` before application
execution: usage errors exit 2 on stderr and do not emit a JSON status object.
Its post-parse process contract is:

| Exit | Code | Meaning |
|---:|---|---|
| 0 | `TOOL_DISCOVERY_BENCHMARK_OK` | Measurement and report publication completed; low retrieval values remain valid output. |
| 2 | `TOOL_DISCOVERY_BENCHMARK_CONFIGURATION_INVALID` | Fixture, catalog/registry join, environment metadata, or output configuration is invalid. |
| 2 | `TOOL_DISCOVERY_BENCHMARK_REPORT_WRITE_FAILED` | The validated report could not be published. |

The published `tool-discovery-benchmark-report.v1` object has exactly these
top-level keys: `schema_version`, `environment`, `registry`, `fixture`,
`ranker`, `metrics`, `context_cost`, and `queries`. `environment` contains the
commit and runtime versions without local paths; `registry` contains tool/category
counts and a fingerprint; `fixture` contains the version, language counts, and
SHA-256; `metrics` contains overall and language-split recall@1/3/5/8, MRR,
zero scores, and unsafe false-positive data; `context_cost` contains full and
top-k schema costs; and `queries` contains rank and top-eight evidence without
query prose.

Schema size is compact sorted-key JSON (`ensure_ascii=False`, `separators=(",",
":")`) encoded as UTF-8. `utf8-bytes-div-4-ceiling.v1` estimates tokens as
`ceil(utf8_bytes / 4)`. This is a byte-only, model-independent estimate rather
than a provider tokenizer, billing, or context-window result. The checked-in
measurement and decision are [the 2026-09-02 tool-discovery result](./benchmarks/2026-09-02-tool-discovery.md).

## Patch / attestation
- `patch apply` は plan JSON のスキーマ検証と dry-run preview を実装済み。exactly one open Prefab は `instantiate_prefab` / `rename_object` / `find_game_object` / `find_component` / `set` の 5 種だけを受け入れる。material / ScriptableObject open mode は root asset mutation、scene open mode は `open_scene` / hierarchy / component / `save_scene`、create mode は各 resource kind の create / hierarchy / component / save 操作を扱う。`set_property` / `set_properties` が発行する fileID-addressed serialized-value op は dedicated writer route の契約であり、public `patch_apply` の open Prefab grammar には含まれない。
- prefab create mode の mutation op（`set` / `insert_array_element` / `remove_array_element`）は `component` selector ではなく、create mode 中に確保した component `$handle` を `target` に指定して適用する。
- material / ScriptableObject の open mode mutation は root asset `$asset` を `target` に指定し、create mode では `create_asset` が返す asset `$handle` を `target` に指定して適用する。
- scene mode は予約済み `$scene` handle を root parent として使い、hierarchy op の `parent` に指定する。scene 内 mutation op は `add_component` / `find_component` が返す component `$handle` を `target` に指定して適用する。
- `patch apply` は confirmed exactly one open Prefab transaction のみ `--out-report` を必須として terminal response と同一の JSON を保存する。それ以外の plan は transaction report contract の対象外で、`--out-report` を消費しない。
- `patch apply` は非 dry-run 時に `--confirm` と `--change-reason` を要求し、JSON ターゲット（`.json`）は内蔵バックエンドで実編集する。
- `patch_apply` MCP ツールは attestation ファイルから期待値（sha256 / signature）を読み取って適用前照合できる。
- `patch_apply` の public response は `data.target`、`data.targets[]`、`data.resources[].path`、および nested `steps[*].result.data.target` を project-relative POSIX path へ投影する。contained absolute path は orchestrator / Bridge 内部にのみ保持する。
- `patch_apply` MCP ツールは scope 指定時に `scan_broken_references` を事前実行し、`error` / `critical` で fail-fast 停止する。
- `patch_apply` MCP ツールは `.prefab` ターゲットで `list_overrides` を事前実行し、`error` / `critical` で fail-fast 停止する。
- `patch_apply` MCP ツールは Unity ターゲット（`.prefab` / `.unity` / `.asset` など）に対して `UNITYTOOL_PATCH_BRIDGE` 経由の外部 bridge を使って適用できる。
- `UNITYTOOL_PATCH_BRIDGE` は JSON 入力（stdin） / JSON 出力（stdout）の bridge コマンドを指定する（全 Editor Bridge route 共通の file-IPC `protocol_version: 2`）。patch payload の `plan_version: 2` は別のschema versionである。

### Exactly-one open Prefab composition transaction (#156)

`resources` が exactly one、かつ `kind="prefab"`, `mode="open"` の confirmed plan だけが transaction coordinator の対象になる。dry-run、multi-resource、mixed resource、Prefab create mode、non-Prefab open mode は既存 semantics のままで transaction state を追加しない。

open Prefab plan は `$root` を起点に `instantiate_prefab`、`rename_object`、`find_game_object`、`find_component`、既存 `set` を順に合成できる。既存 object は `symbol_path` / `file_id` の exactly one、generated descendant は generated handle + strict `relative_symbol_path` を使い、duplicate sibling は zero-based `#N` で disambiguate する。recursive/type auto-find、wiring-specific op、explicit save op は存在せず、Bridge が `SaveAsPrefabAsset` を exactly once 実行する。保存後の同期は対象 path だけを `AssetDatabase.ImportAsset(..., ForceSynchronousImport)` し、unrelated dirty asset を永続化する global `AssetDatabase.SaveAssets()` は呼ばない。

`set` の component handle が UdonSharp proxy を指す場合は、serialized mutation の直後かつ Prefab 保存前に、公開された一引数の `IsProxyBehaviour` で linked proxy を確認し、同じく一引数の `CopyProxyToUdon` で backing `UdonBehaviour` へ同期する。非 UdonSharp component はこの検査を通らない。utility 不在、compatible overload の不在・競合、unlinked proxy、同期例外は `udonsharp_sync_error` diagnostic を伴う apply rejection とし、confirmed transaction は exact preimage rollback を実行する。未同期の proxy 値だけを保存する fallback は行わない。

confirmed transaction は非空 `change_reason` と project-contained `out_report` を mutation 前に必須とする。coordinator は exact Prefab preimage、structure/reference diagnostic identities を保持し、既存 warning は同一 key のままなら許容する。apply/save、introduced diagnostic key、explicit postcondition、optional UdonSharp/ClientSim/runtime gate、または report finalization の失敗は preimage へ rollback する。rollback は disk preimage の atomic restore 後に接続中 Editor Bridge の asset refresh を成功させて初めて完了し、refresh が失敗または未接続なら editor state を unknown として `PATCH_ROLLBACK_FAILED` / `critical` で停止する。commit/rollback terminal response の `data.transaction` は `status`, `report_written`, `report_result`, `original_result`, `rollback_result`, `diagnostics_baseline`, selected `created_results`, `change_reason`, `out_report` を返す。上記の共通 public path projection に加え、transaction の `out_report`、rollback 成功時の `rollback_result.data.target`、および `original_result` 内の同じ path-bearing fields も project-relative POSIX path とし、予約・永続化・復元・Bridge 実行に使う contained resolved path は外部へ返さない。rollback 成功時は `rollback_result.data.auto_refresh="true"` を返す。rollback 中に raw applied step data の path projection が失敗した場合も exact preimage restoration と refresh を保持し、`original_result.data.boundary="projection"` の sanitized cause を持つ `rolled_back` terminal を構築して report を確定する。`report_written=true` の JSON は terminal MCP response と同一で、永続化不能時は予約ファイルを除去し response を authoritative evidence とする。

`out_report` の排他予約 descriptor は one-shot child process だけが所有し、child が終了して reap された後にのみ parent が結果判定と cleanup を行う。parent は descriptor を受け取らず、`close` 失敗後の所有状態を推測せず、同じ descriptor 番号を retry しない。child launch、timeout、abnormal exit、malformed status、exclusive create、release のいずれかが失敗した場合は mutation 前に `OUT_REPORT_WRITE_FAILED` を返す。post-exit cleanup が空の予約を除去できない場合、その `out_report` は non-finalized reservation として残り、外部で除去されるまで同一パスの再要求も mutation 前に `OUT_REPORT_WRITE_FAILED` で停止する。

## `patch_apply` 入力スキーマ（annotated examples）

`patch_apply` の `plan` パラメータに渡す JSON のスキーマ。`plan_version: 2` が唯一受け入れられる形状。`plan_version` を欠くペイロード（旧 `{"target": ..., "ops": [...]}` 形状を含む）は `normalize_patch_plan` が `ValueError` を送出して即時拒否する。外部 `unity_patch_bridge` は、トップレベルに `target` キーを含むリクエストを `BRIDGE_LEGACY_SCHEMA_REJECTED`（`severity="error"`, exit code `1`）で拒否する。互換レイヤや `target` → `resources[0].path` の自動補正は存在しない。

### スキーマ概要

```json
{
  "plan_version": 2,
  "resources": [
    {"id": "<resource-id>", "kind": "<kind>", "path": "<asset-path>", "mode": "<mode>"}
  ],
  "ops": [
    {"resource": "<resource-id>", "op": "<op-type>", ...}
  ],
  "postconditions": [
    {"type": "<check-type>", ...}
  ]
}
```

| フィールド | 必須 | 説明 |
|---|---|---|
| `plan_version` | ✅ | `2` 固定 |
| `resources` | ✅ | 操作対象アセットの一覧。最低 1 件 |
| `resources[].id` | ✅ | ops から参照する識別子（任意の文字列） |
| `resources[].path` | ✅ | Unity アセットパス（`Assets/...`） |
| `resources[].kind` | — | `prefab` / `scene` / `material` / `asset` / `json` / `animation` / `controller`。明示した supported kind は suffix より優先され、dry-run と confirm の全 resource 処理で同じ backend を選ぶ。省略時だけパス拡張子から推定 |
| `resources[].mode` | — | `open`（既存編集）/ `create`（新規作成）。既定 `open` |
| `ops` | ✅ | 操作配列（順序実行） |
| `ops[].resource` | ✅ | `resources[].id` への参照 |
| `ops[].op` | ✅ | 操作種別（下記参照） |
| `postconditions` | — | 適用後の検証条件（省略可） |

### op 種別一覧

**JSON document open mode（`kind="json"`）:**

JSON document はファイル root を操作対象とするため、`component` / `file_id` は不要。`set` は `path` と `value`、`insert_array_element` は `path` / `index` / `value`、`remove_array_element` は `path` / `index` を指定する。明示的な `kind="json"` は suffix と一致しなくても authoritative であり、たとえば JSON object である既存 `.asmdef` は dry-run / confirm とも JSON backend を使う。`kind` を省略した場合は従来どおり suffix inference が authority となり、未知 suffix は `asset` へ推定される。

**SerializedObject direct writer（`set_property` / `set_properties`）:**

この表は symbol 解決後の component fileID を使う dedicated serialized-value route の内部 op を示す。public `patch_apply` の exactly one open Prefab plan は上記 5 種の composable operation だけを受け入れ、`component` / `file_id` を直接持つ `set` と array op は `INVALID_PLAN_SCHEMA` で拒否する。

| op | 必須フィールド | 説明 |
|---|---|---|
| `set` | `component` または `file_id`, `path`, `value` | コンポーネントのプロパティ値を設定 |
| `insert_array_element` | `component` または `file_id`, `path`, `index`, `value` | 配列に要素を挿入 |
| `remove_array_element` | `component` または `file_id`, `path`, `index` | 配列から要素を削除 |

- Prefab の `component` はクラス名（例: `"PlayerScript"`, `"UnityEngine.MeshRenderer"`）または階層修飾 selector `TypeName@/hierarchy/path`（例: `"MeshRenderer@/Body/Head"`）。
- `set` / `insert_array_element` / `remove_array_element` は `component` selector の代わりに、対象 component の Unity local fileID を文字列で指定する `file_id` で対象を指定できる。少なくとも一方を持ち、両方指定時は `file_id` が優先する。fileID 経路は bridge が `GlobalObjectId` でアセット内の component を一意に解決するため、1 つの GameObject に同型 component が複数あっても確実に目的の component を指せる。offline writer は解決済み symbol node の `file_id` を全 value op へ保持する。Inspector profile の writable probe は surface の exact local fileID を identity gate とし、Prefab では `file_id`、ScriptableObject root では既存 open-asset grammar の `$asset` を使う。scene は exact component-handle grammar が無いため writable を fail-closed にする。`file_id` がアセット内のどの component にも解決しない場合は `apply_error` diagnostic（`SER_APPLY_REJECTED`）で fail-fast し、op は一切適用されない。
- Material / ScriptableObject の open mode では `component` の代わりに `"target": "$asset"` でルートを指定

**create mode（新規アセット作成）:**

| op | 必須フィールド | 説明 |
|---|---|---|
| `create_prefab` / `create_asset` / `create_scene` | — | アセットを新規作成 |
| `create_root` | `name` | Prefab ルート GameObject 作成 |
| `create_game_object` | `name`, `parent` | 子 GameObject 作成 |
| `instantiate_prefab` | `prefab`, `parent` | Prefab をシーンにインスタンス化 |
| `rename_object` | `target`, `name` | GameObject リネーム |
| `reparent` | `target`, `parent` | 親変更 |
| `add_component` | `target`, `type` | コンポーネント追加（`result` で `$handle` を返す） |
| `find_component` | `target`, `type` | 既存コンポーネント取得（`result` で `$handle` を返す） |
| `remove_component` | `target` | コンポーネント削除（`target` はコンポーネント `$handle`） |
| `set` | `target`, `path`, `value` | `$handle` 経由でプロパティ設定 |
| `save` / `save_scene` | — | ディスクに保存 |

### 例 1: Prefab のプロパティ編集（public `patch_apply` open mode）

```json
{
  "plan_version": 2,
  "resources": [
    {"id": "avatar", "path": "Assets/Avatars/MyAvatar.prefab"}
  ],
  "ops": [
    {
      "resource": "avatar",
      "op": "find_component",
      "target": "$root",
      "type": "VRC.SDK3.Avatars.Components.VRCAvatarDescriptor",
      "result": "$descriptor"
    },
    {
      "resource": "avatar",
      "op": "set",
      "target": "$descriptor",
      "path": "lipSync",
      "value": 5
    }
  ]
}
```

### 例 2: Prefab の配列サイズ更新（public `patch_apply` open mode）

```json
{
  "plan_version": 2,
  "resources": [
    {"id": "stage", "path": "Assets/Prefabs/Stage.prefab"}
  ],
  "ops": [
    {
      "resource": "stage",
      "op": "find_component",
      "target": "$root",
      "type": "StageLighting",
      "result": "$lighting"
    },
    {
      "resource": "stage",
      "op": "set",
      "target": "$lighting",
      "path": "lights.Array.size",
      "value": 3
    }
  ],
  "postconditions": [
    {"type": "asset_exists", "resource": "stage"},
    {"type": "broken_refs", "scope": "Assets/Prefabs", "expected_count": 0}
  ]
}
```

### 例 3: Material / ScriptableObject の編集（open mode）

Material と ScriptableObject は root asset mutation を使い、`"target": "$asset"` で対象を指定する:

```json
{
  "plan_version": 2,
  "resources": [
    {"id": "mat", "kind": "material", "path": "Assets/Materials/Hair.mat"}
  ],
  "ops": [
    {
      "resource": "mat",
      "op": "set",
      "target": "$asset",
      "path": "m_Shader",
      "value": {"guid": "aabb00112233445566778899aabbccdd", "file_id": "4800000"}
    }
  ]
}
```

> **Note:** Material の個別プロパティ編集（`_Color`, `_MainTex` 等）には `set_material_property`（YAML 直接編集）または `editor_set_material_property`（Editor Bridge 経由）を使う方が簡単。`patch_apply` は Material の `m_Shader` やシリアライズ済みフィールドへの直接書き込みに使う。

### 例 4: Scene のオブジェクト操作（create mode）

```json
{
  "plan_version": 2,
  "resources": [
    {"id": "scene", "kind": "scene", "path": "Assets/Scenes/Main.unity", "mode": "create"}
  ],
  "ops": [
    {"resource": "scene", "op": "create_scene"},
    {
      "resource": "scene",
      "op": "create_game_object",
      "name": "SpawnPoint",
      "parent": "$scene"
    },
    {
      "resource": "scene",
      "op": "instantiate_prefab",
      "prefab": "Assets/Prefabs/Stage.prefab",
      "parent": "$scene"
    },
    {"resource": "scene", "op": "save_scene"}
  ]
}
```

### 例 5: 複数リソースの一括操作

```json
{
  "plan_version": 2,
  "resources": [
    {"id": "base", "path": "Assets/Prefabs/Base.prefab"},
    {"id": "variant", "path": "Assets/Prefabs/Variant.prefab"}
  ],
  "ops": [
    {
      "resource": "base",
      "op": "set",
      "component": "AudioSource",
      "path": "m_Volume",
      "value": 0.8
    },
    {
      "resource": "variant",
      "op": "set",
      "component": "AudioSource",
      "path": "m_Pitch",
      "value": 1.2
    }
  ]
}
```

### postconditions 種別

| type | フィールド | 説明 |
|---|---|---|
| `asset_exists` | `resource` | 指定リソースのアセットファイルが存在するか検証 |
| `broken_refs` | `scope`, `expected_count` | スコープ内の壊れた参照数が期待値と一致するか検証 |

## `patch apply --confirm --out-report` の出力例

before / after diff + validation steps の抜粋:
```json
{
  "success": true,
  "severity": "info",
  "code": "PATCH_APPLY_RESULT",
  "message": "patch.apply completed.",
  "data": {
    "execution_id": "8f0c2b7c0e8f4f30a3d3a7f0f1f1e2aa",
    "executed_at_utc": "2026-02-17T00:00:00+00:00",
    "change_reason": "apply prefab patch",
    "steps": [
      {
        "step": "dry_run_patch",
        "result": {
          "code": "SER_DRY_RUN_OK",
          "data": {
            "diff": [
              {
                "op": "set",
                "path": "nested.value",
                "before": "(unknown)",
                "after": 42
              }
            ]
          }
        }
      },
      {
        "step": "apply_and_save",
        "result": {
          "code": "SER_APPLY_OK",
          "data": {
            "diff": [
              {
                "op": "set",
                "path": "nested.value",
                "before": 10,
                "after": 42
              }
            ]
          }
        }
      },
      {
        "step": "assert_no_critical_errors",
        "result": {
          "code": "RUN_ASSERT_OK"
        }
      }
    ]
  }
}
```

## `set_properties` パラメータ

issue #41 で `set_component_fields` から改名。`symbol_path` は GameObject ではなくコンポーネントを直接指す（独立した `component` 引数は廃止）。

| パラメータ | 型 | 必須 | デフォルト | 説明 |
|-----------|-----|------|-----------|------|
| `asset_path` | string | ✅ | — | アセットファイルパス（.prefab, .unity, .asset） |
| `symbol_path` | string | ✅ | — | 対象コンポーネントの人間可読パス（例: `"Controller/DualButtonController"`, `"Body/Head/MonoBehaviour(PlayerScript)"`） |
| `properties` | dict | ✅ | — | プロパティパス → 新しい値のマッピング（`{"propertyPath": value, ...}`） |
| `dry_run` | bool | — | `false` | `true` で変更を書き込まずプレビューする（`confirm=true` と同時に指定した場合は `dry_run` が優先） |
| `confirm` | bool | — | `false` | `true` で変更を適用（`change_reason` と `out_report` が必須） |
| `change_reason` | string | — | `null` | 変更理由（監査証跡用）。`confirm=true` 時は必須 |
| `out_report` | string | — | `null` | 結果 JSON を書き出すファイルパス。`confirm=true` 時は必須 |

**未解決時の挙動**: `symbol_path` がコンポーネントに解決できない場合は `SYMBOL_NOT_FOUND` / `SYMBOL_AMBIGUOUS` / `SYMBOL_NOT_COMPONENT` を返す。dry-run 段階で `properties` 内の property path がチェーン上に見つからない場合、`SER003`（severity=`error`）の error envelope を返す。`data.suggestions` に近似候補（最大 5 件）、`diagnostics[].detail` に `property_not_found` を載せる（issue #109）。発行する patch op は解決済み symbol node の `file_id` を `set` op の `file_id` ターゲットとして用いる（issue #37）。

**confirmed report 境界**: `confirm=true` は、canonical candidate の project containment を parent の存在確認より先に検証し、mutation 直前に同じ contained path を再検証して排他予約する。missing/non-directory parent、既存 destination、予約不能は writer dispatch 前に `OUT_REPORT_WRITE_FAILED` で停止する。operation response は予約済み destination へ atomic replace で確定する。operation 完了後の finalization failure は raw path/exception を公開せず、`OUT_REPORT_WRITE_FAILED` の `data.operation_result` または `data.operation_error` に authoritative operation response を保持する。空の予約は外部で確認・除去されるまで同じ path の再利用を拒否する。

**asset/symbol preflight boundary**: `set_property` / `set_properties` は asset path の project containment、file status/read/decode、symbol-tree parse を shared preflight で完了してから writer plan を構築する。この境界の例外は `PATCH_APPLY_RESULT`, `data.boundary="preflight"` の stable error として返し、resolved host path と raw exception を公開せず writer dispatch 前に停止する。通常の symbol not-found/ambiguous/not-component/unresolvable は既存の typed symbol envelope を維持する。

**returned failure uncertainty**: confirmed `set_property` / `set_properties` の dedicated writer が例外を送出せず `success=false` と `data.read_only=false` を返した場合、terminal response は `data.state_unknown=true` を返し、`set_properties` の reserved report にも同じ marker を保存する。caller は後続 write の前に serialized state を再検査する。この partition は thrown writer exception と同じ uncertainty marker を使うが、writer が返した failure data を保持する。

**使用例（dry-run）:**

```json
{
  "asset_path": "Assets/Prefabs/Controller.prefab",
  "symbol_path": "Controller/DualButtonController",
  "properties": {
    "clearDelaySeconds": 60.0,
    "buttonA": {"guid": "aabbccdd11223344aabbccdd11223344", "fileID": 12345, "type": 2}
  },
  "dry_run": true
}
```

**使用例（confirm）:**

```json
{
  "asset_path": "Assets/Prefabs/Controller.prefab",
  "symbol_path": "Controller/DualButtonController",
  "properties": {"clearDelaySeconds": 60.0},
  "confirm": true,
  "change_reason": "タイマー値を 30s から 60s に変更",
  "out_report": "reports/set_fields_result.json"
}
```

## `editor_set_properties` パラメータ

issue #41 で `editor_set_component_fields` から改名。

| パラメータ | 型 | 必須 | デフォルト | 説明 |
|-----------|-----|------|-----------|------|
| `hierarchy_path` | string | ✅ | — | 対象 GameObject の Hierarchy パス（例: `"/DualButtonController/Controller"`） |
| `component_type` | string | ✅ | — | コンポーネント型名（例: `"DualButtonController"`） |
| `properties` | list[dict] | ✅ | — | プロパティエントリのリスト（各要素は `property_name` + `value` または `property_name` + `object_reference`） |

**`properties` エントリ形式:**

| 形式 | フィールド | 説明 |
|------|-----------|------|
| プリミティブ値 | `{"property_name": "speed", "value": "60"}` | 数値・文字列・bool を文字列として渡す |
| オブジェクト参照 | `{"property_name": "areaCollider", "object_reference": "/DualButtonController/AreaCollider:BoxCollider"}` | Hierarchy パス + オプションコンポーネント型 |

各エントリは bridge 境界を越えて `value_present` マーカー（bool）を運び、空文字列 `value` と `value` 不在を区別する（issue #52）。

**使用例:**

```json
{
  "hierarchy_path": "/DualButtonController/Controller",
  "component_type": "DualButtonController",
  "properties": [
    {"property_name": "clearDelaySeconds", "value": "60"},
    {"property_name": "areaCollider", "object_reference": "/DualButtonController/AreaCollider:BoxCollider"}
  ]
}
```

すべてのプロパティ変更は単一 Undo グループにまとめられる。

## Unity bridge / runtime

> 環境変数 / ignore_guids.txt / scope config の正本は [CONFIGURATION.md](../CONFIGURATION.md) を参照。

- `tools/unity_patch_bridge.py` は常駐 Editor Bridge に JSON リクエスト / レスポンスファイルを介してパッチ計画を中継する（mutation op の `value` を Unity 側で扱える型情報へ正規化し、prefab create mode の hierarchy / component op、material / ScriptableObject create mode の `create_asset`、scene open/create mode の hierarchy / prefab instantiate / component op、および `save` / `save_scene` を中継する）。
- `patch_apply` と `tools/unity_patch_bridge.py` の外部 request は `plan_version: 2` + `resources[]` + `ops[]` を受け付け、resource ごとに分解する。operation が 1 件も割り当てられていない resource は core の dry-run / apply と IPC 送信から除外する一方、宣言済み resource は response metadata と postcondition 解決に保持する（plan 全体の `ops` は非空必須）。core の全 resource summary は実際の step history から `executed` / `applied` を返し、operation-free declaration は `executed: false` / `applied: 0` となる。外部 Bridge も operation-free resource summary を declaration-only metadata として同じ値で返し、実行されていない resource に `success` / `severity` / `code` を捏造しない。
- `tools/unity_patch_bridge.py` の mutation op は prefab open mode では `component`、material / ScriptableObject open mode では root asset `$asset` の `target`、scene mode では component `$handle` の `target`、create mode では component / asset `$handle` の `target` を受け付ける。
- `tools/unity_patch_bridge.py` は bridge 送信前に `ops` が非空配列であることと各 operation を検証し、空配列、`set` の `value` 欠落、配列操作の `index` 欠落などを `BRIDGE_REQUEST_SCHEMA` で fail-fast 停止する。
- `tools/unity_patch_bridge.py` は `UNITYTOOL_BRIDGE_WATCH_DIR` で Editor Bridge との接続先 watch ディレクトリを指定する。`UNITYTOOL_UNITY_TIMEOUT_SEC` でポーリング上限を、`UNITYTOOL_UNITY_PROJECT_PATH` / `UNITYTOOL_UNITY_LOG_FILE` で実行設定を制御できる。`UNITYTOOL_BRIDGE_WATCH_DIR` 未設定時は `BRIDGE_WATCH_DIR_MISSING` で fail-fast 停止する。
- WSL 環境対応: `prefab_sentinel/wsl_compat.py` が WSL 検出・パス変換（`wslpath` 経由）・スペース入りパスの復元を提供し、`unity_patch_bridge.py` と `runtime_validation/` から利用する。`UNITYTOOL_UNITY_PROJECT_PATH` / `UNITYTOOL_BRIDGE_WATCH_DIR` は Windows パス（`D:/...`）でも WSL パス（`/mnt/d/...`）でも受け付ける。`wslpath` 不在時はグレースフル・デグレードする。
- `tools/unity_patch_bridge.py` と core の Unity Bridge parser は Editor Bridge 応答を固定 schema と照合する。top-level は常に `protocol_version/success/severity/code/message/data/diagnostics` の exact field set。個別 resource の C# producer `data` は `target/op_count/applied/read_only/executed/protocol_version/created_results` を必須とし、検証後の `target/op_count/protocol_version` は request と外側 protocol から再構築する。複数 resource の外部 Bridge aggregate `data` は `plan_version/resource_count/op_count/applied/resources/read_only/executed/protocol_version` の別 exact field set を使い、core parser は検証済み aggregate metadata をそのまま保持する。`resources[]` は共通の `id/kind/path/mode/op_count/applied/executed` に加え、実行済み resource だけが `success/severity/code` を持ち、operation-free declaration はそれらを持たない。両 schema とも未知 field・型不一致・重複 resource id・count/applied 不整合・矛盾した execution state を `BRIDGE_UNITY_RESPONSE_SCHEMA` または `SER_BRIDGE_PROTOCOL` で fail-fast 停止する。各 diagnostic は `path/location/detail/evidence` の exact field set を必須とする。
- `prefab_sentinel/services/serialized_object/` の resource dispatch は `json` / `prefab` / `asset` / `material` / `scene` の adapter ごとに分離し、Unity 側に渡す resource plan は常に kind / mode を明示した bridge request へ正規化する。
- `prefab_sentinel/services/serialized_object/` は 1 ファイル 300 行以内の責務別モジュール構成に分割されている（`service.py` が facade、`patch_dispatch` / `patch_preview` / `patch_validator` / `patch_executor` / `patch_json_apply` が JSON ターゲット flow、`resource_bridge` / `resource_bridge_invoke` が Unity Editor bridge 構成、`resource_plan` / `resource_adapters` が resource scope 入り口、`asset_open_ops` / `asset_create_ops` / `asset_create_writers` / `scene_dispatch` / `scene_object_ops` / `scene_component_ops` / `scene_values` / `prefab_create_dispatch` / `prefab_create_structure` / `prefab_create_values` が open/create mode バリデータ群）。公開 API は `prefab_sentinel.services.serialized_object.SerializedObjectService` のみで、後方互換のための re-export やシムは置かない。
- `tools/unity/PrefabSentinel.UnityPatchBridge.cs` は Editor Bridge から呼び出される実装として `.prefab` の open mode `set` / `insert_array_element` / `remove_array_element`、`.mat` / `.asset` の open mode root asset mutation、`.unity` の open/create mode `open_scene` / `create_scene` / hierarchy / `instantiate_prefab` / component op / `save_scene`、および create mode の prefab root / hierarchy / component op、material / ScriptableObject の `create_asset`、`$handle` 参照 mutation、`save` を適用する（prefab mutation 時の `component` は一意一致必須、component 曖昧時は候補パス付きで fail-fast）。
- `prefab_sentinel/services/runtime_validation/` は `UNITYTOOL_BRIDGE_WATCH_DIR` が指す Editor Bridge 監視ディレクトリへ共通 `protocol_version: 2` の唯一の action `validate_runtime` を JSON で書き出し、`{uuid}.response.json` を待ち受ける。watch ディレクトリ未設定時は `RUN_CONFIG_ERROR` 応答で fail-fast し、未配線を明示する。write-class `compile_only` / `clientsim` は report path reservation と全 preflight を最初の副作用より前に完了し、`out_report` は project root 内かつ `Assets/` 外の atomic publication 可能な path だけを受理する。tool は no save、dirty-clear、revert、generated-asset cleanup を行わない。
- `tools/unity/PrefabSentinel.UnityRuntimeValidationBridge.cs` は runtime validation 用の Editor 内実装で、UdonSharp compile と ClientSim lifecycle を行い、`success/severity/code/message/data/diagnostics` 形式の応答を返す。compound `clientsim` は ClientSim の全 readiness/state/lease preflight を compile 前に完了し、initial Scene authorization を共有する。続いて compile を1回だけ実行し、compile success 時だけ ClientSim を始める。compile failure は ClientSim/Play Mode を始めず、compile / ClientSim とも partial failure evidence を保持する。ClientSim は requested scene が唯一 loaded かつ active の場合だけ、現在の in-memory scene を `playModeStartScene=null` で Play する。operation deadline は snapshot/preflight より前に固定し、期限切れなら lease 取得前に停止する。dirty asset 観測は既に loaded な persistent dirty native asset (`AssetDatabase.IsNativeAsset`) だけを列挙し、imported source object や全 project asset を load しない。full request と独立 restoration lease を `SessionState` に保持し、domain reload 後も exit → previous start-scene restore → after snapshot → response write → state clear の順序を再開する。restore 失敗時は lease を保持して再試行し、復元成功前に response/state clear を行わない。エントリーポイントは file-IPC 用 `RunFromPaths(requestPath, responsePath)` のみ。
- **Editor Bridge セットアップ**: Unity Editor で `PrefabSentinel > Editor Bridge` メニューから EditorWindow を開き、watch ディレクトリを指定する。Python 側は `UNITYTOOL_BRIDGE_WATCH_DIR` に `{uuid}.request.json` を書き込み、`{uuid}.response.json` または `{uuid}.publication-failed.json` の出現をポーリングする。Editor Bridge は `EditorApplication.update` で 500 ms 間隔ポーリングし、`action` フィールドで patch / runtime を自動判別する。response のアトミック書き込み（`.tmp` → rename）で読み取り競合を防止し、atomic/direct write の二重失敗時は元 request の rename で tagged terminal failure を通知する。
- `component` セレクタは `TypeName@Hierarchy/Path` 形式を受け付け、同型コンポーネントが複数ある場合に GameObject 階層で明示的に絞り込める。
- `set` の値デコードは `int/float/bool/string/null` に加えて `Character` / `LayerMask` / `ArraySize`、`enum`、`Color`、`Vector2/3/4`、`Vector2Int/3Int`、`Rect/RectInt`、`Bounds/BoundsInt`、`Quaternion`、`AnimationCurve`、`Gradient`、`ObjectReference` / `ExposedReference`（`value_kind=json` の `{guid,file_id}` または `{guid,fileID}`）、`ManagedReference`（`value_kind=json`、必要時 `{"__type":"Namespace.Type, Assembly"}` ヒント対応）、`Generic`（カスタム構造体の `value_json` 反映）を扱う。
- `ObjectReference` は Unity 組み込みリソース（`Library/unity default resources`、`Resources/unity_builtin_extra`）を解決できる。組み込みパス検出時は (1) `Library/unity default resources` と `Resources/unity_builtin_extra` の両パスに対して `AssetDatabase.LoadAllAssetsAtPath` で GUID+fileID マッチング、(2) 既知組み込みアセット名テーブルから `AssetDatabase.GetBuiltinExtraResource` / `Resources.GetBuiltinResource` で直接ロード+GUID+fileID 検証（Editor Bridge コンテキストで `LoadAllAssetsAtPath` が空を返す遅延ロード問題への対策）、(3) `Resources.FindObjectsOfTypeAll` 最終フォールバック の三段階で解決する。通常の `LoadMainAssetAtPath` パスはバイパスする。JSON キーは `fileID`（Unity ネイティブ形式、`plan_generators` 出力）と `file_id`（snake_case、example plan 互換）の両方を受け付ける。
- `AnimationCurve` は `value_kind=json` で `{ "keys":[{"time":0.0,"value":1.0,"in_tangent":0.0,"out_tangent":0.0}], "pre_wrap_mode":1, "post_wrap_mode":1 }` 形式を受け付ける（`value_kind=null` で null 設定）。
- `Gradient` は `value_kind=json` で `{ "color_keys":[{"color":{"r":1,"g":1,"b":1,"a":1},"time":0.0}], "alpha_keys":[{"alpha":1.0,"time":0.0}], "mode":0 }` 形式を受け付ける（`value_kind=null` で null 設定）。
- 配列操作パスの診断は `.Array.data` 形式を厳密検証し、`.Array.size` / index 付き誤指定時はヒント付きで停止する。
- fixed buffer 配列に対する `insert_array_element` / `remove_array_element` は未対応として明示的に fail-fast 停止し、要素更新は `set` で個別要素パスを指定する方針とする。
- patch plan v2 は任意の `postconditions` 配列を受け付け、`patch apply` 完了前に検証する。現状の対応型は `asset_exists`（`resource` または `path`）と `broken_refs`（`scope`, `expected_count`, `exclude_patterns`, `ignore_asset_guids`）で、不一致時は fail-fast で停止する。
- `validate_runtime` MCP ツールは `profile` で実行範囲を明示選択する。`compile_only` は force UdonSharp compile の後、`console_authority=unity_log|editor_bridge`（既定 `unity_log`）で一つの Console authority だけを選び、available evidence に対して console classification / assert を実行する。選択 authority が unavailable の場合は別 authority を自動探索・retry・fallbackせず、compile section を保持したまま terminal failure とし `RUN_ASSERT_OK` を返さない。`result.console_evidence` は authority / available / collection_code / line_count を公開する。`editor_console_only` は bridge-owned console buffer を読み、compile / ClientSim を実行しない。`clientsim` は shared preflight の後に compile を実行し、compile success 時だけ Play Mode を開始する。Bridge 応答の `data.executed` は必須 boolean で、`true` のとき side-effect report は before/runtime/after と、runtime-only change / post-cleanup residual を完全な型付き schema で返す。欠落・型不正・不完全差分は clean とみなさず warning にする。asset candidate は before/after の対称差分で、dirty 化と clean/unload の両方向を報告する。別 scene の additive open、private ClientSim initializer、Edit Mode runner object、暗黙 save/revert は行わない。
- `editor_console` は bridge-owned callback buffer を観測 authority とする。`since_sequence` は sequence cursor より優先され、`since_request_id` は run-script request correlation に使う。`since_request_id` には直前の Editor Bridge response top-level `request_id`（file-transport request id）を渡す。古い `since_seconds` は compatibility window として残るが、deterministic read では sequence / request id を使う。
- `editor_run_script` は stdout、primitive return value、structured outputs、runtime exception summary、WSL path hints を別 channel で返す。WSL mounted-drive path は guidance を返すだけで snippet source は変更しない。
- live geometry (`editor_get_transform` / `editor_get_bounds` / `editor_measure_distance`) は routine inspection 用の read-only bridge actions で、run-script snippets を代替しない。World Space UI screenshot framing は同じ RectTransform bounds semantics を使う。
- `collect_unity_console(runtime_root, log_file, ...)` は `log_file` を `runtime_root` 配下に封じ込める（どちらも symlink 解決後の絶対パスに正規化し、`runtime_root` 外を指す入力は `RUN_CONFIG_ERROR` で fail-fast）。collection payload は `console_authority="unity_log"` と `evidence_available` を持つ。存在する空ファイルは available な zero-line evidence、missing file と UTF-8 復号不能な `RUN_LOG_DECODE_WARN` は unavailable evidence であり、`compile_only` orchestrator は後二者を empty classification に変換しない。
- `PrefabVariantService.resolve_chain_values(variant_path, diagnostics=None)` および下層の `resolve_chain_values` module 関数は、復号失敗（`OSError` / `UnicodeDecodeError`）を沈黙で `{}` に丸めない。呼び出し側が `diagnostics: list[Diagnostic]` を渡した場合、該当ファイルについて `detail="unreadable_file"` の診断を末尾に追記する（診断 sink 未指定時も従来どおり `{}` を返すが、`revert_overrides` は sink を渡して応答の `diagnostics[]` に伝搬する）。

## Safe Bridge deployment transaction

`deploy_bridge` は source を target へ一件ずつ copy せず、project-global lock の内側で complete bundle を一つの transaction として扱う。

```text
source manifest を作成
  -> ownership / target / parent conflict を mutation 前に検証
  -> Library/PrefabSentinel/deploy-transactions/<run-id>/staged-target へ全件 copy
  -> staged bytes を再 hash し source-manifest-v1.json を atomic publication
  -> fresh target は complete directory rename、existing target は private promotion 1 回
  -> final target manifest を Python 側でも独立再検証
  -> Library/PrefabSentinel/deploy-ownership-v1.json を atomic publication
  -> recovery 不要の transaction を cleanup
```

transaction directory は `Assets` 外にあり、`staged-target` と必要時の `backup-target` を持つ。source manifest は sorted relative path、byte size、file SHA-256 から aggregate SHA-256 を作り、staging と final target の双方で同じ identity を再計算する。`.meta` は manifest hash に含めず、ownership record が証明した既存 source の partner だけを staging へ保存する。target parent と transaction は同一 filesystem 必須で、copy fallback はない。

absent または empty の fresh target は staging directory を一度だけ rename し、`promotion_state=installed_fresh`、`barrier_used=false` となる。現在の project root / Bridge instance ID と一致する fresh `get_editor_state` 応答で観測した running Bridge version、ownership record の manifest、source manifest、existing target の再計算 manifest がすべて一致する場合だけ `promotion_state=already_current` とし、staging transaction の cleanup だけを行う。target move、private action、refresh barrier、`AssetDatabase.Refresh` は実行しない。running identity / version 未観測・不一致、byte 不一致、legacy import のいずれかは no-op にせず、nonempty existing target として接続中 Bridge の private `promote_bridge_bundle` を必須とする。private handler は1回の同期処理内で `AssetDatabase.DisallowAutoRefresh()`、old target から `backup-target` への move、`staged-target` の promotion、manifest verification、必要な rollback、`finally` の対応する `AssetDatabase.AllowAutoRefresh()` を完結させる。host は IPC を跨いで barrier を保持しない。

`promotion_state` は `not_attempted` / `already_current` / `installed_fresh` / `promoted` / `rolled_back` / `rollback_failed`。promotion 後の target mismatch は同じ private action で exact old manifest の recovery を一度だけ試みる。rollback failure は canonical `backup-target` が previous manifest と byte-identical な場合だけ `backup_retained=true` とし、directory の存在や private action の自己申告だけでは保持を認めない。partial set を success としない。ownership publication と cleanup は final target verification 後だけ行う。

preflight / staging failure 後の transaction discard と、complete target outcome 後の cleanup は、削除失敗を `DEPLOY_CLEANUP_FAILED` として `transaction_retained` / `recovery_required` の実測値とともに返す。pre-promotion abort は `promotion_state=not_attempted` のまま Assets を変更しない。cleanup failure を `DEPLOY_STAGING_FAILED` に丸めず、raw exception や private path は公開しない。ambiguous transport、promotion、rollback、cleanup に no automatic retry とし、state を推測する fallback や旧 delete-then-copy 経路は持たない。

private action が安全な complete target outcome を書いた後、refresh barrier 保持中に `AssetDatabase.Refresh(ImportAssetOptions.ForceSynchronousImport)` を一度だけ呼ぶ。固定 begin/end ログで明示 import の範囲を記録し、その後 `finally` の対応する `AssetDatabase.AllowAutoRefresh()` で barrier を解放する。これにより barrier 解放と synchronous added / removed source inventory import の間を空けない。`already_current` は target bytesを変更しないためこの refresh 経路へ入らない。direct `CompilationPipeline.RequestScriptCompilation()`、Unity 2022.3.22f1 の実参照で利用不能な `AssetDatabase.ScheduleRefresh()`、`delayCall`、refresh と compile request の二重経路は持たない。`deploy_bridge` の責務は complete bytes、transaction outcome、必要な synchronous import の完了までであり、Unity compile / reload の成功は別の live gate が観測する（Issue #213）。

段階的 upgrade が必要である。promotion を実行するのは更新開始時点の active pre-fix handler なので、#244 のように source filename set を追加・削除する bundle 自身の新 handler に一回の更新中で切り替わることはない。まず同じ source filename set の refresh-aware same-layout bootstrap を配備して reload 後に active と検証し、その handler で filename-changing bundle を配備する。この staged-upgrade constraint は direct all-at-once upgrade が解決済みであるとは主張しない。

## read-only 検査ツール詳細

- `inspect_variant` は Prefab chain / overrides / stale 候補（重複 override・`Array.size` 不整合）を返す。
- `find_referencing_assets` は GUID / asset の参照元を scope 指定で検索し、`max_usages` 超過分を `truncated_usages` に集計する。
- `validate_refs` は `missing_asset` / `missing_local_id` を検出する。
- Unity 組み込み GUID（例: `0000000000000000e000000000000000` / `f000...`）は欠落判定から除外する。
- GUID インデックスは scope が属する Unity プロジェクトルート（最寄り `Assets` 親）で構築し、`Library` / `Logs` / `Temp` / `obj` など既定除外ディレクトリは走査しない。
- `validate_refs` の結果には `scan_project_root`（GUID インデックスに使った Unity プロジェクトルート）を含む。
- 外部 `*.prefab` 参照の fileID 検証は誤検知回避のため既定でスキップし、件数を `skipped_external_prefab_fileid_checks` に集計する。
- `validate_refs` の `categories` はユニーク問題件数（例: missing GUID 単位）を返し、発生回数は `categories_occurrences` / `broken_occurrences` で確認する。
- ノイズ判定に使えるよう、`top_missing_asset_guids` に missing GUID 上位を返す。
- `ignore_asset_guids` パラメータで missing GUID を一時的に無視でき、集計は `ignored_missing_asset_occurrences` / `top_ignored_missing_asset_guids` で確認できる。
- `find_referencing_assets` も同じ既定除外を適用し、`Library` など非本番スコープを走査しない。
- 書き込み操作は `patch_apply`（confirm モード）、`set_property`、`set_properties`、`copy_component_fields`、`add_component`、`remove_component`、`revert_overrides` の各 MCP ツールで利用可能。

## エラーヒント ("Did you mean...?")

- `SYMBOL_NOT_FOUND` エラー（`set_property`, `set_properties`, `copy_component_fields`, `add_component`, `remove_component`）は `data.suggestions` に類似 symbol_path のリスト（最大 3 件）を含む。
- `MAT_PROP_NOT_FOUND` エラー（`inspect_material_asset` の書き込みモード）は `data.suggestions` に類似プロパティ名のリスト（最大 3 件）を含む。既存の `data.available_properties`（全プロパティ名リスト）も維持される。
- `EDITOR_CTRL_PROPERTY_NOT_FOUND` エラー（`editor_get_material_property`, `editor_set_material_property`）は `data.suggestions` に類似シェーダープロパティ名のリスト（最大 3 件）を含む。
- 候補なしの場合は `suggestions` は空配列 `[]`。
- Python 側は `difflib.SequenceMatcher`、C# 側は Levenshtein 距離を使用（アルゴリズム差異あり、結果の完全一致は保証しない）。

## VRC SDK アップロード

## マルチプラットフォームアップロード

- `vrcsdk_upload` の `platforms` パラメータで複数プラットフォームへの順次ビルド+アップロードが可能。
- 有効値: `"windows"`, `"android"`, `"ios"`。デフォルト: `["windows"]`。
- 順次実行し、途中失敗で残りをスキップする。完了後は元のビルドターゲットに復元する。
- レスポンスの `data.platform_results` に per-platform の結果（成功/失敗/スキップ）を含む。
- `data.original_target_restored` で元のビルドターゲットの復元成否を確認できる。
- SDK の build/upload 例外は、公開応答では固定 code `VRCSDK_BUILD_FAILED` と固定 message `VRC SDK build or upload failed. See the Unity Console for details.` に投影する。失敗した `data.platform_results[].error` も同じ固定 message であり、元の例外型・message・stack trace・filesystem path は含めない。
- SDK API は build と upload を一つの呼び出しとして実行するため、例外 message の文面から失敗 phase を推測しない。完全な例外は private diagnostic として Unity Console に記録する。
- 複数プラットフォーム時は `timeout_sec` を `600 * len(platforms)` 程度に設定することを推奨。

## 代表レポート出力フォーマット

```md
# Prefab Sentinel Validation Report
- RunId: 20260211-235959-abc123
- Scope: Assets/MyProject
- Result: FAILED

### Findings
1. REF002 Missing local fileID
   - Location: MyGroup Variant.prefab / target_list.Array.data[0]
   - Evidence: fileID 6858960407220450596 not found
   - Suggested Fix: map to existing VRCPickup fileID 87704510201466299

2. RUN001 Udon runtime exception
   - Location: GameController.cs:200
   - Evidence: audio_sources[i] null
   - Suggested Fix: ignore invalid entries or set audio_sources size=0
```
