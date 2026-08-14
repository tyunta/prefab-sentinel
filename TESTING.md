# Testing

PR を上げる前にローカルで走らせるテストの実行手順とテスト戦略の正本。ユニット / 統合 / 回帰 / mutmut の 4 系統と、CI（`ci.yml`）が回す内容、`source_text_invariant` マーカー、C# xUnit ハーネスの扱いを 1 箇所に集約する。運用ルールの正本は [AGENTS.md](./AGENTS.md)。

## Quickstart

最頻ユースケース（全ユニットテストを並列実行）:

```bash
uv run --extra test --extra mcp python scripts/run_unit_tests.py
```

## MCP 2026-07-28 protocol / wire conformance

MCP-focused regression gate は、protocol contract / distribution surface、middleware、stdio wire、HTTP gate を同時に固定する。migration 変更時は次を実行する。

```bash
uv run --extra mcp pytest \
  tests/test_mcp_distribution_contract.py \
  tests/test_mcp_protocol.py \
  tests/test_mcp_http.py \
  tests/test_mcp_stdio_transport.py \
  tests/test_mcp_http_transport.py -q
```

この gate は `2026-07-28` のみ、Tools-only request-method allowlist、request ごとの namespaced `_meta`、legacy lifecycle rejection、stdio `notifications/cancelled` forwarding、loopback HTTP wire、process-wide tool-call serialization を対象とする。domain envelope の `success=false`、tool execution error、top-level protocol error の区別は [docs/tool-conventions.md](./docs/tool-conventions.md#mcp-protocol--result-境界) を正本とする。

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

valid modern HTTP `initialize` は removed method として `404` / `-32601` になり、以前の RED gap は解消済みである。real stdio だけは pinned MCP Python SDK v2.0.0 が product middleware より先に `-32022` を返す transport 例外として test で固定する。process-wide `ProjectSession` と `activate_project` の暗黙 continuity は deliberate product constraint かつ per-request stateless model からの既知逸脱のままなので、選択した 3 scenario の通過を full 2026-07-28 conformance と表現しない。error precedence と SDK 例外は [docs/api-reference.md](./docs/api-reference.md#エラーコード規約) を参照。

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

## 回帰テスト

既知不具合の再現ケースをテストに固定し、同じ破損が再発したら即座に落とす。

- **対象** — Broken PPtr / missing fileID と、UdonSharp の null reference 例外。過去に 1 度でも発生した破損パターンは最小再現の YAML フィクスチャとして `tests/` に固定する。
- **方針** — 修正 PR は再現ケースを先に追加し、修正前は赤・修正後は緑になることを確認する。固定した再現ケースは四半期 mutation 走行の入力にもなる。
- **配置** — 回帰フィクスチャは対象モジュールの 1 テストファイル（[テストファイル配置](#テストファイル配置) 表）に追記する。

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

## CI workflow

`.github/workflows/ci.yml` が現行 CI の唯一の workflow（issue #270 で `unity-integration.yml` / `unity-live-nightly.yml` / `unity-smoke.yml` の Unity 連動 workflow 群は削除済み）。

| job | トリガ | 内容 |
|-----|--------|------|
| `lint` | 全 push / PR / manual / weekly | `ruff check` → production-only `mypy prefab_sentinel/` → モジュール行数ゲート（`scripts/check_module_line_limits.py`） |
| `typecheck-tests` | 全 push / PR / manual / weekly | `uv sync --extra lint --extra test --extra mcp` → full test-target `uv run mypy prefab_sentinel tests --show-error-codes` |
| `unit-tests` | 全 push / PR / manual / weekly | `uv sync --extra test --extra mcp` → `uv run python scripts/run_unit_tests.py` |
| `changes` | 全 push / PR / manual / weekly | `tools/unity/**` / `tests/csharp/**` / `global.json` / `ci.yml` 自身の変更を `dorny/paths-filter@v3` で検出し、`csharp` 出力フラグを立てる |
| `csharp-tests` | `changes.outputs.csharp == 'true'` または manual | `.NET SDK setup`（`global.json` で pin）→ `dotnet restore --locked-mode` → `dotnet build --no-restore --configuration Release` → `dotnet test --no-build` で `tests/csharp/` の sanity Fact を実行 |
| `performance-benchmarks` | weekly / manual のみ | synthetic 7-case benchmark を `--enforce` で実行し、成功・失敗にかかわらず JSON report を artifact として保存する。checked-in baseline は読み取り専用 |

Full test-target mypy は test 依存と MCP 依存も含むため、pre-commit には入れない。local / TAKT 検証では `uv run --extra lint mypy prefab_sentinel tests --show-error-codes` を走らせ、CI では `typecheck-tests` job が同じ対象を通常 gate として検証する。

`csharp-tests` は監視対象外の PR では skip され、branch protection 上では `skipped` 状態が success として扱われる（issue #290）。

## C# xUnit ハーネス

`tests/csharp/` の C# テストハーネス（issue #290 で導入したブートストラップ）。`tools/unity/` の C# 橋ソースを、Python 側の source-text grep（`tests/test_editor_control_bridge_source.py` 等）から段階的に挙動実行型テストへ移すための土台。本ブートストラップ自体は sanity Fact 1 本のみで、橋メソッドの移行は #290 のフォローアップ issue 群で個別に進める。

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
```

`dotnet test` の期待観測値は `Passed!  - Failed: 0, Passed: 1, Skipped: 0, Total: 1` と exit 0。`Assert.Equal(3, 1 + 1)` 等に flip すると Failed: 1 / 非ゼロ終了で sanity Fact のリグレッションが捕捉される。

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

## Unity 依存 Bridge C# のコンパイル検証

`tools/unity/` の C# 橋ソース 57 ファイルのうち、CI と上記 xUnit ハーネスがコンパイルするのは Unity 参照を持たない pure-logic 16 ファイルのみ。残る 41 ファイル（`UnityEditor` 参照 37 / `UnityEngine` のみ 4、うち 9 が VRChat SDK / UdonSharp surface に触れる）は **どのテストでもコンパイルされない**。これらは `source_text_invariant` の Tier 3 構造検証と `scripts/check_bridge_constants.py` の定数ドリフト検査の対象だが、いずれも型・メンバ参照を解決しない。ヘルパー抽出リファクタ（H 系）が呼び出し側を取りこぼした場合、実 Unity コンパイルでしか出ないエラー（旧入れ子型パス参照 `CS0426` / 無修飾呼び出し `CS0103` 等）が release をすり抜ける（issue #42 の実績）。

### CI コンパイルゲートを入れない理由（issue #43）

この 41 ファイルに対する CI 型解決ゲート（Roslyn 等）は検討の結果、採用しない。Bridge は本質的に Editor アセンブリ（`PrefabSentinel.Editor`）であり、検証が必要な 37 ファイルが `UnityEditor` に依存する。`UnityEditor.dll` には正規の再配布経路（公式 NuGet reference package 等）が存在せず — community NuGet（`Unity3D` / `UnityAssemblies` 系）はメタデータのみでローカル Unity install のパスを解決するだけ、実 DLL を含む版は `UnityEngine` のみ・旧バージョン・ライセンスがグレー — 加えて 9 ファイルが proprietary な VRChat SDK に依存する。reference assembly の調達には Unity install が不可避であり、Unity を入れる時点で「フル Unity コンパイルを避ける軽量ゲート」という前提が崩れる。GameCI 等によるフル Unity コンパイルは Unity ライセンス管理と CI 実行コストに見合わないと判断した。

### 安全網: 手動 deploy コンパイル確認

Unity 依存 Bridge C#（`tools/unity/` の `UnityEditor` / VRChat SDK 参照ファイル）を変更したら、`deploy_bridge` で実 Unity 2022.3 + VRChat SDK プロジェクトに配置し、Unity のコンパイルがエラー 0 件であることを手動で確認する。これが現状唯一のコンパイル検証経路。pure-logic を新規抽出して Unity 非依存にできた分は xUnit ハーネス（`<Compile Include>`）へ取り込み、検証対象を段階的に CI 側へ移すことで、この未検証 surface を縮小していく。

### ClientSim lifecycle regression

`run_clientsim` は通常の compile gate では実行しない。source invariants は runtime dispatcher が deferred response を待つこと、requested scene が sole loaded active scene であること、`playModeStartScene=null` の current-scene Play、public ClientSim settings/readiness API preflight、Resources prefab asset を除外した loaded live main instance の一意性、snapshot/preflight より前に固定した absolute deadline、全 project asset を load しない loaded-dirty asset 観測、full request + independent restoration lease の `SessionState` persistence、reload reconciliation、preflight/enter/ready/exit の別 timeout、before/runtime/after の3時点 report、duplicate multiplicity を保つ diff、asset candidate の before/after 対称差分、restore（失敗時は lease 保持）→ strict atomic response success → persisted-state clear の順序を固定する。Python service tests は operation timeout より cleanup 30 秒 + dispatch 5 秒だけ transport deadline が長いこと、`executed` が必須 boolean であること、実行済み report の欠落・型不正が fail-closed warning になること、runtime-only additions は警告せず post-exit residual additionsは警告すること、runtime snapshot のみ欠けても信頼できる before/after residual を落とさず、before/after snapshot 欠落時は推測差分を警告しないことを固定する。

修正確認では `deploy_bridge` 経路から Unity 2022.3 + VRChat SDK + ClientSim project へ Bridge files を配置し、recompile success と C# compiler error 0 件を確認した。ユーザーの作業 scene では ClientSim / Play Mode を起動しない。実 ClientSim acceptance が必要な場合は、operator が明示承認した disposable scene を唯一 loaded かつ active にし、before hierarchy を記録してから実行する。応答後は `residual_added_*` / `residual_removed_*` が空、元の `playModeStartScene` が復元済み、Editor が stable Edit Mode、scene が clean であることを確認し、最後に verified-clean scene を再読込する。

issue #112 の `editor_serialized_property_read` / `editor_serialized_property_list` / `editor_serialized_property_write` は `UnityEditorControlBridge.SerializedProperty` partial に実装されるため、Unity real-device validation はこの手動 deploy コンパイル確認の対象になる。`UnityIntegrationTests` には `SerializedPropertySmokeSupport` と `EditorCtrl_SerializedProperty_ReadListWriteDryRunNoOp` probe を置き、read / list / dry-run / confirmed write / no-op を同じ temporary GameObject で検証する。TAKT 内では source invariant までを自動確認し、実 Unity 2022.3 + VRChat SDK project で `deploy_bridge` 後に `editor_run_tests` から probe を実行することを follow-up 条件にする。

`UnityIntegrationTests.RunTestSuite` は開始時の scene setup を [`EditorSceneManager.GetSceneManagerSetup`](https://docs.unity3d.com/ja/2019.3/ScriptReference/SceneManagement.EditorSceneManager.GetSceneManagerSetup.html) で保存し、loaded scene がすべて保存済みかつ clean である場合だけ fixture mutation を開始する。unsaved / dirty scene はユーザー作業を暗黙に破棄できないため mutation 前に fail-fast する。各 case の fixture 再作成前と suite の `finally` では元 setup を復元し、test asset directory 配下の scene が loaded でないことを確認してから、戻り値を検査する [`AssetDatabase.DeleteAsset`](https://docs.unity3d.com/kr/2022.3/ScriptReference/AssetDatabase.DeleteAsset.html) で削除する。domain reload を伴う run-script stuck / recovery probe は同期 suite には含めず、Unity 非依存の `RunScriptCompilePendingCodeSelectorTests`（first timeout / threshold recovery）で固定する。

Issue #116 の `editor_create_generated_asset` / `editor_move_asset` は `UnityEditorControlBridge.AssetOps` partial に実装されるため、TAKT 内では Python wrapper tests、Unity-free C# `AssetOpsPathValidation` xUnit tests、source invariant までを自動確認する。実 Unity 2022.3 + VRChat SDK project では `deploy_bridge` 後に `editor_create_generated_asset` create dry-run、create confirm、`editor_move_asset` move dry-run、move confirm、lowercase `.rendertexture` reject、case-only move reject、confirm report equality を手動 smoke し、cleanup は既存 `delete_assets` で行う。

Issue #155 の `get_editor_state` dirty identity / blocker provenance は `UnityEditorControlBridge.EditorState` partial に実装されるため、TAKT 内では Python status/bridge tests と C# source invariant までを自動確認する。実 Unity 2022.3 + VRChat SDK project では `deploy_bridge` 後に Unity コンパイルエラー 0 件を確認し、dirty scene / Prefab Stage / material / ScriptableObject asset を用意した状態で `get_project_status` が `state_source="live_editor"`、dirty identity arrays、`dirty_or_save_blocker`、compile/playmode/stage blockers を返すことを手動 smoke する。

## Post-TAKT Unity Inspector verification

Issue #157 は Unity 2022.3 + VRChat SDK project の representative Inspector fixture で受入済み。Bridge 配置後に Unity compile error 0 件を確認し、component / ScriptableObject surface、array、ObjectReference、override-origin、custom-editor degraded path、invalid/incomplete profile、zipped-array mismatch、writer rejection、atomic promotion/rollback を live Unity layer と deterministic Python/C# tests の双方で固定した。writer probe は同一 surface identity を必須とし、Prefab component は exact `file_id`、ScriptableObject root は実 open-asset grammar の `$asset` で real orchestrator dry-run を通す。exact component handle を構築できない scene writable は false-positive を避けて fail-closed にする。

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

Issue #156 は real-Unity layer と deterministic fault-injection layer の双方で受入済み。live run では duplicate sibling、generated `#N` lookup、Camera scalar、Canvas ObjectReference を含む multi-op transaction、post-save distinct object IDs、apply/postcondition failure の exact-preimage rollback を確認した。UdonSharp proxy を含む representative Prefab では copy / instantiate / rename / field set の dry-run と confirm を通し、committed transaction と response-equal report を確認した。

保存後に target を再 load し、linked proxy、proxy value、backing public variables、backing→proxy round-trip の保持を確認した。`validate_structure` は duplicate fileID / Transform inconsistency / missing component / orphan Transform がすべて 0、`validate_refs` は broken GUID/fileID 0 件だった。package source と target の既存 unresolved-looking handles は type/property/instance signature が一致し、transaction が新規導入した参照不整合ではないことを確認した。save/report/rollback failure と introduced structure/reference diagnostics は同一 revision の deterministic tests で固定している。

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
