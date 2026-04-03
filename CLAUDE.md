# プロジェクト運用ルール

- リポジトリ: https://github.com/tyunta/prefab-sentinel

本ファイルは `prefab-sentinel` リポジトリの運用ルール正本。仕様の正本は `README.md`。
仕様に関わる変更を行う場合は、実装変更と同時に `README.md` の該当箇所を更新する。

## 設計原則
- Easy より Simple: 目的達成に必要な最小実装を優先する。
- 根拠優先: 前提・不変条件・判断理由を明文化する。
- 検証可能性: 設定値と結果の対応を説明できる実装のみ採用する。
- 必須参照の欠落は補完せず `error` で停止する（fail-fast）。

## 責務境界（Services / Skills / MCP）
- `serialized-object`: 何を書き換えるか（操作実行）。
- `prefab-variant`: どこが上書きされているか（差分可視化）。
- `reference-resolver`: 参照が有効か（実体照合）。
- `runtime-validation`: 実行時に壊れていないか（結果検証）。
- `symbol-tree`: 人間可読なシンボルパスで Unity オブジェクトをアドレッシングする（名前→fileID 解決）。
- `mcp-server`: AI エージェントに MCP ツールとして検査機能を公開する（symbol-tree + orchestrator のラッパー）。
- Skills: どの順で使うか（運用プロトコル）。

## 変更時の必須フロー
1. 変更対象の scope（Prefab / Scene / Assets）を宣言する。
2. `list_overrides` と `scan_broken_references` で事前診断する。
3. 変更は `dry_run_patch` で差分確認後に `apply_and_save` する。
4. 適用後に `compile_udonsharp` と `run_clientsim` で実行検証する。
5. `critical` / `error` が 1 件でもあれば停止し、修正または判断待ちへ回す。
- `patch apply --confirm` は `--change-reason` と `--out-report` を必須とする（監査ログのため）。

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
- 主要コード: `SER001`, `SER002`, `PVR001`, `PVR002`, `PVR003`, `REF001`, `REF002`, `RUN001`, `RUN002`。
- `severity` は `info | warning | error | critical` を使用する。
- MCP レベルの例外（`ToolError`）はインフラエラー（ファイル不在、import 失敗）のみ。

## セキュリティと実行制御
- 既定は read-only inspection とし、書き込みは明示モード時のみ許可する。
- 重要操作は `--confirm` または署名付き実行計画を要求する。
- 外部プロセス実行（Unity batchmode）は許可リスト制とする。

## テスト方針
- Unit: propertyPath 解決、配列境界、参照逆引き。
- Integration: Base / Variant / Scene 三層編集の E2E。
- Regression: Broken PPtr / Udon nullref の既知再現ケース固定。

## ドキュメント運用
- README は「やること / やる内容 / やらないこと」を維持する。
- 運用ルール変更時は本ファイルに追記し、理由を簡潔に残す。
- 仕様との齟齬が出た場合は README を優先して同期する。

## バージョン管理
- pre-commit hook は `ruff check` → パッチバンプ → `uv lock` の順で実行される（`.git/hooks/pre-commit`）。lint 失敗時はコミット中断。
- minor/major バンプは手動: `uv run bump-my-version bump minor|major`。
- パッチバンプはフィーチャーブランチを含む全コミットで走らせる（`SKIP_BUMP=1` は原則使わない）。
- バージョン記述箇所は `pyproject.toml`、`.claude-plugin/plugin.json`、`tools/unity/PrefabSentinel.UnityEditorControlBridge.cs` の 3 箇所（`[tool.bumpversion]` で一括管理）。

## Editor リモート操作の行動規約
- スクショ（`editor screenshot`）はトリアージの起点として使い、データソースにしない。
- スクショで見えた情報は `inspect wiring` / `validate refs` で裏取りする。
- Inspector の表示名と SerializedProperty の `propertyPath` は異なるため、目視だけで patch plan を書かない。
- Editor 操作は Editor Bridge 常駐が前提。`UNITYTOOL_BRIDGE_MODE=editor` が未設定の場合はエラーで停止する。

## VRChat エコシステムナレッジの自動適用
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
- CI での反映は `--out-ignore-guid-file` 明示指定時かつ許可ブランチのみ。既定は `main` / `release/*`（`UNITYTOOL_IGNORE_GUID_ALLOW_BRANCHES` で上書き可）。
