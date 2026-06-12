# プロジェクト運用ルール

- AI agent 初回参照: [AGENT_GUIDE.md](./AGENT_GUIDE.md)

本ファイルは `prefab-sentinel` リポジトリの運用ルール正本。仕様の正本は専門ドキュメント（`docs/api-reference.md` / `docs/tool-conventions.md` / `docs/execution-reference.md` / `ARCHITECTURE.md` / `TESTING.md` / `CONFIGURATION.md`）に分かれて置かれ、`README.md` はそれらへの入口（エントリポイント兼ルーター）として機能する。
仕様に関わる変更を行う場合は、実装変更と同時に対応する専門ドキュメントの該当箇所を更新する。

## 設計原則
- Easy より Simple: 目的達成に必要な最小実装を優先する。
- 根拠優先: 前提・不変条件・判断理由を明文化する。
- 検証可能性: 設定値と結果の対応を説明できる実装のみ採用する。
- 必須参照の欠落は補完せず `error` で停止する（fail-fast）。
- ファイルサイズ目安（200〜400 行）は **partial 単位**で評価する。1 ファイル合計ではなく `partial class` ごとの責務単位で行数を判定する。partial 構成は disk 上のファイル名（`tools/unity/PrefabSentinel.Unity*Bridge.<Concern>.cs`）が正本で、test が CLAUDE.md inventory との drift を検出する（`tests/test_editor_control_bridge_source.py::TestOperationalRulesPartialInventory` / `tests/test_unity_patch_bridge_source.py::TestPatchBridgeOperationalRulesInventory`）。
- 現在の per-concern token inventory（test 用、追加・削除時に同期）:
  - `UnityEditorControlBridge`: AnimationClip / BlendShape / CameraView / CompileBarrier / Components / ConsoleCapture / Geometry / GeometryContributors / Helpers / Hierarchy / MaterialBatch / MaterialQuery / MaterialWrite / Menu / MenuScriptWatch / PrefabStage / Properties / PropertyObjectReference / PropertyWrite / RunScriptAsync / RunScriptCompile / SaveInstantiate / Screenshot / UdonSharpAddComponent / UdonSharpArrayWrite / UdonSharpFieldWrite / UdonSharpInvocation / UdonSharpListenerWiring / UiElement
  - `UnityPatchBridge`: Payloads / Prefab / Asset / Scene / Resolve / Mutation / ManagedReference / Diagnostics
- `Mutation` partial は value-kind dispatcher と各 `SerializedPropertyType` 用 reader が 1 つの cohesive concern を構成するため、200〜400 行ガイドラインも issue #129 暫定の 1,000 行上限も意図的に超過する（issue #129 spec.md "further sub-split" / Non-Goals 参照）。

## 責務境界（Services / Skills / MCP）
- `serialized-object`: 何を書き換えるか（操作実行）。
- `prefab-variant`: どこが上書きされているか（差分可視化）。
- `reference-resolver`: 参照が有効か（実体照合）。
- `runtime-validation`: 実行時に壊れていないか（結果検証）。
- `symbol-tree`: 人間可読なシンボルパスで Unity オブジェクトをアドレッシングする（名前→fileID 解決）。
- `mcp-server`: AI エージェントに MCP ツールとして検査機能を公開する（symbol-tree + orchestrator のラッパー）。MCP ツールの正本一覧は `docs/tools.md`。
- Skills: どの順で使うか（運用プロトコル）。

## 変更時の必須フロー
1. 変更対象の scope（Prefab / Scene / Assets）を宣言する。
2. `list_overrides` と `scan_broken_references` で事前診断する。
3. 変更は `dry_run_patch` で差分確認後に `apply_and_save` する。
4. 適用後に `compile_udonsharp` と `run_clientsim` で実行検証する。
5. `critical` / `error` が 1 件でもあれば停止し、修正または判断待ちへ回す。
- 書き込み系ツール（`set_property`, `add_component`, `remove_component`, `copy_component_fields`, `set_properties`, `set_material_property`, `copy_asset`, `rename_asset`, `revert_overrides`, `patch_apply`）は `confirm=True` 時に `change_reason` を必須とする（監査ログのため）。`patch_apply` および `set_properties` はさらに `out_report` も必須。

## 意思決定ルール
- 自動修復可能で根拠があるもののみ `safe_fix` として提案・適用する。
- 複数候補や仕様判断が必要なものは `decision_required` として保留する。
- `decision_required` はユーザー合意後にのみ適用する。

## 品質ゲート
- Broken PPtr 再発率: 0件（指定テストセット）。
- Variant override 整合性: 100%。
- Udon runtime critical: 0件（スモークシーン）。
- 各変更に `before/after` 差分と validation report を必ず添付する。

## API / エラー規約
- **操作系・検証系ツール**（`set_property`, `validate_refs`, `validate_field_rename`, `check_field_coverage`, `activate_project` 等）: `success / severity / code / message / data / diagnostics` エンベロープを返す。`diagnostics` が意味のあるデータを運ぶため。
- **参照系ツール**（`get_unity_symbols`, `find_unity_symbol`, `find_referencing_assets`）: ペイロードを直接返す（エンベロープなし）。該当なしは空 `matches` 配列で表現し、エラーとしない。インフラエラー（ファイル不在等）は MCP `ToolError` で伝播。
- **orchestrator 系ツール**（`inspect_wiring`, `inspect_variant` 等）: `ToolResponse.to_dict()` 経由でエンベロープを返す。
- エラーコードの正本は `docs/api-reference.md`。CLAUDE.md には掲載しない（新規コードを追加した場合も `docs/api-reference.md` へ追記する）。
- `severity` は `info | warning | error | critical` を使用する。
- MCP レベルの例外（`ToolError`）はインフラエラー（ファイル不在、import 失敗）のみ。

## セキュリティと実行制御
- 既定は read-only inspection とし、書き込みは明示モード時のみ許可する。
- 重要操作は `--confirm` または署名付き実行計画を要求する。
- Unity Editor 連携は常駐 Editor Bridge の file-IPC のみとする（履歴的には Unity batchmode 経路もあったが、issue #270 で削除済み）。

## テスト方針
- Unit: propertyPath 解決、配列境界、参照逆引き。
- Integration: Base / Variant / Scene 三層編集の E2E。
- Regression: Broken PPtr / Udon nullref の既知再現ケース固定。
- Bridge C# コンパイル検証: `tools/unity/` の Unity 依存ファイル（`UnityEditor` / VRChat SDK 参照、55 中 40 ファイル）は CI でも xUnit ハーネスでもコンパイルされない。これらを変更したら `deploy_bridge` で実 Unity プロジェクトに配置しコンパイル 0 件を手動確認する。CI ゲートを入れない理由（reference assembly 調達に Unity install が不可避 = issue #43）と検証プロトコルの正本は [`TESTING.md` の「Unity 依存 Bridge C# のコンパイル検証」節](./TESTING.md#unity-依存-bridge-c-のコンパイル検証)。

## ドキュメント運用
- `README.md` はエントリポイント兼ルーター。「やること / やる内容 / やらないこと」とドキュメントマップ（各専門ドキュメントへの導線）を維持し、仕様本文は持たない。
- 仕様の正本は専門ドキュメントに分割して置く: API エンベロープ / エラーコードは `docs/api-reference.md`、MCP ツールの住所表現・引数命名・監査ペア要否の規約は `docs/tool-conventions.md`、実行・CLI・patch スキーマは `docs/execution-reference.md`、構成・レイヤ責務・サービス仕様・データモデルは `ARCHITECTURE.md`、テスト戦略は `TESTING.md`、設定は `CONFIGURATION.md`。
- 運用ルール変更時は本ファイルに追記し、理由を簡潔に残す。
- 仕様との齟齬が出た場合は、対応する専門ドキュメントを優先して同期する。

## バージョン管理
- pre-commit hook は `ruff check` → パッチバンプ → `uv lock` の順で実行される。lint 失敗時はコミット中断。
- フック本体は `.githooks/pre-commit` に tracked。`.git/hooks/` 配下は version 管理外で `git add` 対象のドリフトが見えないため（issue #338 で 4 番目の version ファイルを追加した際、旧 `.git/hooks/pre-commit` の `git add` リストが追従せず drift した実績がある）。クローン時に一度だけ `git config core.hooksPath .githooks` を実行すること（`core.hooksPath` 設定時、git は `.git/hooks/` を無視して当該ディレクトリのフックのみ使う）。
- minor/major バンプは手動: `uv run bump-my-version bump minor|major`。
- パッチバンプはフィーチャーブランチを含む全コミットで走らせる（`SKIP_BUMP=1` は原則使わない）。
- バージョン記述箇所は `pyproject.toml`、`.claude-plugin/plugin.json`、`.codex-plugin/plugin.json`、`tools/unity/PrefabSentinel.UnityEditorControlBridge.cs` の 4 箇所（`[tool.bumpversion]` で一括管理）。`.codex-plugin/plugin.json` は issue #338 で Codex CLI 配布経路として追加された（バージョンは Claude Code Plugin と同期）。
- 正本パスへの依存（issue #139）: `scripts/check_bridge_constants.py`（バージョン文字列・プロトコルバージョン・severity 語彙のドリフト検査）と `pyproject.toml [tool.bumpversion]`（`search` パターンのアンカー）はいずれも正本パートとして `tools/unity/PrefabSentinel.UnityEditorControlBridge.cs` に置かれた 3 つの load-bearing 定数 — `BridgeVersion`、`ProtocolVersion`、`ConsoleLogBuffer.DefaultCapacity` — に依存する。これらの定数を別 partial に移動する場合は、ドリフトチェッカーと bumpversion 設定の双方を同時に更新する必要がある（片方だけの更新では検査が静かに無効化される）。

## Editor リモート操作の行動規約
- スクショ（`editor screenshot`）はトリアージの起点として使い、データソースにしない。
- スクショで見えた情報は `inspect wiring` / `validate refs` で裏取りする。
- Inspector の表示名と SerializedProperty の `propertyPath` は異なるため、目視だけで patch plan を書かない。
- Editor 操作は Editor Bridge 常駐が前提。`UNITYTOOL_BRIDGE_WATCH_DIR` が未設定または指定パスが存在しない場合はエラーで停止する。
- Prefab Stage が開いた状態では、scene 内 GameObject を `hierarchy_path` で指す hierarchy-bound 書き込み系（`editor_force_scene_view_refresh` 等）は active Prefab Stage のみを検索し scene root にフォールバックしないため `NOT_FOUND` を返す（issue #268 / post-#264）。Scene-bound オブジェクトを操作する前に `editor_close_prefab` で Prefab Stage を閉じる。
- ユニットテストは Editor Bridge ディスパッチ環境変数（`UNITYTOOL_BRIDGE_WATCH_DIR`）をホストシェルから継承しないように `setUp` / サブプロセス起動時に pop する。ホストが watch ディレクトリを export していてもテストは決定的な watch-dir 未配線状態から開始する必要がある（issue #88, #89, #270）。

## VRChat エコシステムナレッジの自動適用
- `knowledge/*.md` の編集規約・新規ファイル作成手順は [knowledge/STYLE_GUIDE.md](./knowledge/STYLE_GUIDE.md) を正本とする（スケルトンは [knowledge/TEMPLATE.md](./knowledge/TEMPLATE.md)）。
- 作業中に VRChat エコシステムツール（ModularAvatar、liltoon、VRCFury、AvatarOptimizer 等）に関する判断が必要になったら、対応する `knowledge/*.md` を Read してから判断する。
- ファイル特定: コンポーネント型名・シェーダー名・パッケージ名からファイルを特定する。不明な場合は `knowledge/` を Glob して候補を探す。
- 読み込み判断: 判断に迷ったときのみ読む。同セッション内で既読なら再読み込み不要。
- confidence が `low` のナレッジを判断材料にする場合、その旨をユーザーに伝える。
- ナレッジファイルが存在しないツールに遭遇したら、その旨を報告し集中調査の要否を確認する。

### 自動書き戻し
- 以下に該当したら `knowledge/*.md` に追記する: ソースコードから未知の情報を発見した / 作業が成功して再現可能なパターンが確立した / 作業が失敗して原因と回避策が判明した / ユーザーから教わった知識。
- 追記前に同じ事実が L1〜L3 セクションに存在するか確認し、存在すれば追記せず必要なら既存記述を更新する。矛盾する発見は既存記述を修正する。
- 新規の発見は「実運用で学んだこと」セクションに追記する。
- 書き戻し時に `version_tested` と `last_updated` を更新する。
- inspect 実測値を書き戻した場合、該当項目の confidence を `high` に昇格する。

### memory との棲み分け
| 種類 | 保存先 | 例 |
|------|--------|---|
| ユーザーの好み・フィードバック | `memory/` | 「コミットは /commit を使う」 |
| プロジェクト固有の状況 | `memory/` | 「Phase 2.3 未実装」 |
| ツールのドメイン知識 | `knowledge/` | 「MA Merge Armature の mergeTarget は Transform 参照」 |
| 作業で得た技術的知見 | `knowledge/` | 「liltoon の _MainColorPower は 0.5 以下だと暗すぎる」 |

## ignore-guid 運用
- `<scope>/config/ignore_guids.txt` を既定とし、存在しなければ無視する。
- `validate_refs` MCP ツールは `ignore_asset_guids` 引数（caller-supplied list）と上記 conventional file の auto-load を併用する（issue #237）。両ソースは union-dedupe で orchestrator に転送され、file が寄与した場合は `IGNORE_GUIDS_FILE_LOADED` info diagnostic でパスと件数を通知する。malformed エントリは既存の `REF001` envelope で表面化する。
- CI での反映は `--out-ignore-guid-file` 明示指定時かつ許可ブランチのみ。既定は `main` / `release/*`（`UNITYTOOL_IGNORE_GUID_ALLOW_BRANCHES` で上書き可）。

## Mutation testing 運用
- 仕様の正本は [`TESTING.md` の Mutation testing 節](./TESTING.md#mutation-testing)。本節は運用判断に必要な最小定義のみ置く。
- カデンス: 四半期ごとに 1 回フル走行。CI には組み込まない。
- 監査対象モジュール (P0/P1):
  - `prefab_sentinel.services.reference_resolver`
  - `prefab_sentinel.services.prefab_variant`
  - `prefab_sentinel.services.serialized_object.patch_validator`
  - `prefab_sentinel.services.runtime_validation.classification`
  - `prefab_sentinel.orchestrator_postcondition`
  - `prefab_sentinel.orchestrator_validation`
- survived は critical / trivial / equivalent の三分類で四半期 survivor 分類（四半期レポート）に記録する。critical はテストでキルする。trivial は survivor 分類に証跡を残すにとどめ、`[tool.mutmut].do_not_mutate` には追加しない — mutmut 3.5.0 の `do_not_mutate` はソースファイルパスへの `fnmatch` グロブであり、構造単位（コード式）の trivial mutant を抑制できない（ファイルパスを足せば campaign の mutate 対象を狭め Non-Goal に反する）。`do_not_mutate` は空で運用する（issue #28）。
- 新規テストは `tests._assertion_helpers.assert_error_envelope` で code / severity / field / message を値で固定する。例外発生のみのアサートは禁止。
- `assertRaises` 値固定ルール（issue #180）: 全ての `with assertRaises(...)` ブロックは、同じテストメソッド内に value-pin（`as cm:` で捕捉した例外への参照、または `assertEqual` / `assertIn` / `assertRegex` / `assertNotEqual` 等の値比較系アサーション、または `assertRaisesRegex` 形式そのもの）を必ず伴うこと（受理される value-pin アサーション全集合は `tests/test_assertion_density.py` の `_VALUE_PIN_ASSERTIONS` 定数を正本とする）。例外型のみが契約として意味を持つインフラ系例外（`FileNotFoundError` / `OSError` / `SystemExit` / `JSONDecodeError` / `UnicodeDecodeError` 等）を直接 `assertRaises` で受ける場合のみ、value-pin を省略してよい。`tests/test_assertion_density.py` がリポジトリ全体の AST を歩いてこのルールを meta-test として強制するので、新規テストもこの基準を満たす必要がある（infra 例外の正式な許可リストは同テスト内の `INFRA_EXCEPTION_ALLOWLIST` 定数）。
- 新規のリポジトリ同期テスト（`tools/unity/` や `knowledge/` の un-mutated tree を読むだけで `prefab_sentinel/` のミューテーションを観測できないテスト）は、`@pytest.mark.source_text_invariant` をモジュールスコープで宣言する。これが mutmut のテスト選択（`-m "not source_text_invariant"` 単一フィルタ）からの除外メカニズム。
- 数値リテラルがデフォルトパラメータとして truncation cap / size limit / threshold を担う場合、`±1` 境界 3 点（cap-1 / cap / cap+1）のテストを `tests/test_default_parameter_boundaries.py` に追加する（issue #179）。テスト本体ではデフォルトを明示的に渡さず、デフォルトリテラルを必ず発火させること（オーバーライドするとミューテーションが境界条件をすり抜ける）。発見手順は `grep -rnE "def .* = [0-9]+" prefab_sentinel/` を再走行し、ファイル冒頭の docstring に列挙されている既知サイトと差分を取って未カバーを追補する。
