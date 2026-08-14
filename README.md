# Prefab Sentinel

[![CI](https://img.shields.io/github/actions/workflow/status/tyunta/prefab-sentinel/ci.yml?branch=main&label=CI)](https://github.com/tyunta/prefab-sentinel/actions/workflows/ci.yml)
[![License](https://img.shields.io/github/license/tyunta/prefab-sentinel.svg)](./LICENSE)
[![Python](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/)

Unity / VRChat プロジェクトの Prefab / Scene / Asset を安全に検査・編集する MCP サーバー。

`Variant` の override 衝突、`Broken PPtr` / missing fileID、Udon / ClientSim ランタイム例外を構造化された応答で診断し、手作業 YAML 編集を経由せずに修復する。AI エージェント前提の設計。

YAML-backed read-only 経路（`validate_refs` / `validate_materials` / `inspect_wiring` / `inspect_variant` / `inspect_hierarchy` / `find_referencing_assets` 等）は Unity を起動せずに完結する。`inspect_serialized_surface` / `inspect_with_profile` / `validate_inspector_profile` は、last-saved `SerializedObject` surface を常駐 Editor Bridge 経由で取得する。書き込み経路（`patch_apply` / `set_property` / `editor_*` 等）は常駐 Editor Bridge との file-IPC で動き、`confirm=True` + 非空 `change_reason` の監査ペアを欠く呼び出しは `CHANGE_REASON_REQUIRED` で拒否される。

公開 MCP 境界は **2026-07-28 のみ・Tools capability のみ**をサポートする。stdio が既定で、任意の HTTP 経路はローカル loopback の `/mcp` に限定する。これは full conformance の合格宣言ではなく、protocol error の優先順位と stdio transport 例外は [docs/api-reference.md](./docs/api-reference.md#エラーコード規約)、厳格 CI gate の対象範囲は [TESTING.md](./TESTING.md#mcp-2026-07-28-protocol--wire-conformance)、process-state の既知逸脱は [ARCHITECTURE.md](./ARCHITECTURE.md#mcpserver--protocol-boundary) を正本とする。対応する request method と transport は [docs/tool-conventions.md](./docs/tool-conventions.md)、[docs/execution-reference.md](./docs/execution-reference.md) を参照。

本 README は各専門ドキュメントへの入口（[ドキュメントマップ](#ドキュメントマップ) 参照）。仕様の正本は専門ドキュメント群、運用ルールの正本は [AGENTS.md](./AGENTS.md)。

## やること / やらないこと

**やること**

- Unity SerializedObject レベルの安全な編集基盤を提供し、Prefab Base / Variant / Scene インスタンスの実効値を追跡可能にする
- 参照解決（GUID + fileID）と整合性検証を API 化する
- 実行時検証（UdonSharp compile / ClientSim smoke / ログ分類）をパイプライン化する
- 決定的な synthetic workload と固定 budget で主要 inspection path の latency regression を検出する
- 人間の判断が要る変更と、機械的に実行できる変更を明確に分離する
- ModularAvatar / liltoon / VRCFury 等の VRChat エコシステムツールのドメイン知識を同梱し、AI エージェントの判断材料として供給する
- Skills として運用フローを標準化する（Claude Code / Codex CLI の両ホストに対応）

**やらないこと**

- YAML 文字列の直接置換を標準手段にしない
- Unity 内部参照を推測で補完しない
- 変更根拠のない自動最適化をしない
- 実プロジェクトを timing gate に使わず、weekly benchmark から baseline を自動更新しない
- ユーザー判断が要る仕様変更を勝手に適用しない
- legacy MCP の handshake / session lifecycle を互換維持せず、remote / shared HTTP server として公開しない

## Quickstart

プラグインとして導入する。ホスト（Claude Code / Codex CLI）に応じて 2 つの経路があり、いずれも marketplace から取得する。

**Claude Code**（Claude Code 内に入力するスラッシュコマンド）:

```text
/plugin marketplace add tyunta/prefab-sentinel
/plugin install prefab-sentinel@tyunta-prefab-sentinel
```

**Codex CLI**（シェルで marketplace を登録 → Codex CLI 内の `/plugins` TUI で有効化）:

```bash
codex plugin marketplace add tyunta/prefab-sentinel
```

登録後、Codex CLI 内で `/plugins` を開き、一覧から `prefab-sentinel` を選んで Install する（`codex plugin install` というシェルコマンドは存在しない）。

導入後の使い方は `guide` スキル（`/prefab-sentinel:guide`）が入口 — MCP ツールの一覧と呼び出し方、パッチスキーマ、Editor Bridge のセットアップ、エコシステムナレッジの案内がまとまっている。MCP ツールを実際に呼ぶのは AI エージェント側なので、エージェントにこの guide を参照させれば使い始められる。

各経路の詳細は [セットアップ](#セットアップ)、リポジトリから MCP サーバーを直接起動する開発者向け手順は [CONTRIBUTING.md](./CONTRIBUTING.md) を参照。

## セットアップ

### 前提条件

- Python 3.11 以上
- [uv](https://docs.astral.sh/uv/)（パッケージマネージャ）— インストール手順は [uv 公式ガイド](https://docs.astral.sh/uv/getting-started/installation/) 参照（Windows / macOS / Linux で異なる）
- Unity 2022.3 + VRChat SDK 3.x（Worlds / Avatars）— Editor Bridge 経由の書き込み・実行検証経路で必要

MCP サーバーは Plugin 内部で `uv` / `uvx` 経由でローカル起動されるため、Plugin 導入経路でも Python / uv は必要。ホスト（Claude Code / Codex CLI）と本ツールは Windows / macOS / Linux で動作する。

### Claude Code Plugin

[Quickstart](#quickstart) の 2 コマンドで導入する。インストールすると MCP サーバー・6 つのスキル・`knowledge/` ディレクトリが一括展開され、`/prefab-sentinel:guide` 等のスキルを Claude Code から直接呼び出せる。各スキル内のコマンドは `${CLAUDE_PLUGIN_ROOT}` テンプレート変数でローカルから実行される。

### Codex CLI Plugin

[Quickstart](#quickstart) の手順で導入する（シェルで `codex plugin marketplace add` → Codex CLI 内の `/plugins` TUI で `prefab-sentinel` を Install）。MCP サーバーは Plugin 定義（`.codex-plugin/plugin.json` の `mcpServers` が指す `.codex-plugin/mcp.json`）から登録され、skill bundle も同時に展開される。Codex の MCP サーバーは `uvx` が GitHub から本体を取得して起動するため、起動時にネットワーク接続が必要（Claude Code 経路はローカル導入物から起動する）。Plugin を更新したら Codex CLI セッションを再起動する。無効化・登録解除は `/plugins` TUI から行う。

### スキル

| スキル | 呼び出し | 説明 |
|--------|----------|------|
| guide | `/prefab-sentinel:guide` | MCP ツールリファレンス・パッチスキーマ・Bridge セットアップ・エコシステムナレッジ案内 |
| variant-safe-edit | `/prefab-sentinel:variant-safe-edit` | Prefab Variant の安全な編集ワークフロー |
| prefab-reference-repair | `/prefab-sentinel:prefab-reference-repair` | 壊れた参照の検出・修復ワークフロー |
| udon-log-triage | `/prefab-sentinel:udon-log-triage` | ランタイムログのトリアージワークフロー |
| knowledge-acquisition | `/prefab-sentinel:knowledge-acquisition` | VRChat エコシステムツールのナレッジ調査・蓄積 |
| inspector-profile-authoring | `/prefab-sentinel:inspector-profile-authoring` | last-saved SerializedObject surface とソース根拠から project-local Inspector profile を作成・修復するワークフロー |

### Unity Bridge

パッチ実適用・ランタイム検証などの書き込み経路と、last-saved `SerializedObject` surface を扱う Inspector profile 経路は、Unity Editor 内に常駐する Editor Bridge との file-IPC で動く。Bridge のセットアップ手順は `/prefab-sentinel:guide` スキルに、watch ディレクトリを指定する環境変数 `UNITYTOOL_BRIDGE_WATCH_DIR` は [CONFIGURATION.md](./CONFIGURATION.md) に記載。未設定で書き込み系ツールを呼ぶと `BRIDGE_WATCH_DIR_MISSING`、Inspector profile ツールを呼ぶと `INSPECTOR_SURFACE_UNAVAILABLE` で fail-fast 停止する。YAML-backed read-only 検査には Bridge 設定は不要。

Python wheel は `tools/unity/` と `knowledge/` の配布対象だけを package 内へ mapping し、nested `.serena` など workspace-local metadata は同梱しない。

## 代表的な MCP ツール

全 MCP ツールの正本カタログは [docs/tools.md](./docs/tools.md)、応答エンベロープ（`success / severity / code / message / data / diagnostics`）とエラーコードの正本は [docs/api-reference.md](./docs/api-reference.md)。下表は代表ツールのみ。

| ツール | 説明 |
|--------|------|
| `activate_project` | プロジェクトスコープ設定 + キャッシュ warm（サーバープロセス起動後に呼ぶ） |
| `validate_refs` | 壊れた GUID / fileID 参照のスキャン |
| `validate_materials` | `.mat` / renderer slot / TMP material preset / folder policy の静的検証。任意ルールは [CONFIGURATION.md](./CONFIGURATION.md#material_validation_rulesjson-形式仕様) を正本とする |
| `validate_structure` | YAML 内部構造の検証（fileID 重複・Transform 整合性） |
| `inspect_wiring` | MonoBehaviour フィールド配線の分析（null 参照の分類付き） |
| `inspect_variant` | Prefab Variant の override チェーン分析 |
| `inspect_hierarchy` | saved YAML の GameObject 階層表示。`expand_prefab_instances` で effective nested PrefabInstance 階層を read-only 展開 |
| `inspect_transform_effective_values` | offline `asset_path` + `symbol_path` の Transform default / override / effective 値を local/world で比較 |
| `inspect_unity_event_listeners` | Button / Slider / Toggle の UnityEvent persistent listener entries と UdonSharp 診断を 1 応答で取得 |
| `find_referencing_assets` | GUID / パスの参照元アセット検索 |
| `patch_apply` | パッチ計画の検証・適用。exactly one `mode="open"` Prefab は composable handle grammar と response-equal report、introduced-only validation、automatic rollback を持つ transaction。詳細は [docs/execution-reference.md](./docs/execution-reference.md)、payload/error は [docs/api-reference.md](./docs/api-reference.md)、実 Unity acceptance は [TESTING.md](./TESTING.md) |
| `delete_asset` / `delete_assets` | AssetDatabase-backed asset 削除の dry-run / confirm。削除後 broken-reference delta を返す |
| `editor_create_generated_asset` / `editor_move_asset` | RenderTexture generated asset 作成と AssetDatabase.MoveAsset-backed asset 移動。公開ツール一覧は [docs/tools.md](./docs/tools.md)、payload/error は [docs/api-reference.md](./docs/api-reference.md)、confirm audit/report requirements は [CONFIGURATION.md](./CONFIGURATION.md)、live Unity smoke は [TESTING.md](./TESTING.md) を正本とする |
| `validate_runtime` | 既定 `compile_only` の UdonSharp compile 検証。ClientSim は `profile="clientsim"` + audit pair で明示 opt-in とし、requested scene が唯一 loaded かつ active の場合だけ Play Mode lifecycle を実行 |
| `editor_get_transform` / `editor_get_bounds` / `editor_measure_distance` | Editor Bridge 経由の read-only live geometry 検査 |
| `editor_serialized_property_read` / `editor_serialized_property_list` / `editor_serialized_property_write` | SerializedObject-backed generic inspector / writer API。公開ツール一覧は [docs/tools.md](./docs/tools.md)、payload とエラーコードは [docs/api-reference.md](./docs/api-reference.md) を正本とする |
| `inspect_serialized_surface` / `inspect_with_profile` / `validate_inspector_profile` | last-saved raw Inspector surface と project-local declarative profile。3 ツールとも read-only だが、常駐 Editor Bridge が前提。ツールは [docs/tools.md](./docs/tools.md)、envelope/error は [docs/api-reference.md](./docs/api-reference.md)、profile path/writer gates は [CONFIGURATION.md](./CONFIGURATION.md)、live Unity protocol は [TESTING.md](./TESTING.md)、author/repair procedure は [skills/inspector-profile-authoring/SKILL.md](./skills/inspector-profile-authoring/SKILL.md) を正本とする |
| `editor_*` | Editor Bridge 経由の Scene / Hierarchy / Component / BlendShape / Animation 編集、スクリーンショット、Console、UdonSharp field / array write |

Routine CI / agent validation では `validate_runtime(profile="compile_only")` または `validate_runtime(profile="editor_console_only")` を使う。ClientSim は submission scene 向けの明示 opt-in で、`profile="clientsim"` + audit pair が揃い、requested scene が sole loaded active scene の場合だけ実行する。詳細な cleanup/restore/side-effect 契約は [docs/api-reference.md](./docs/api-reference.md) と [docs/execution-reference.md](./docs/execution-reference.md) を正本とする。

YAML-backed read-only 検査（`validate_refs` / `validate_materials` / `inspect_wiring` / `inspect_variant` / `inspect_hierarchy` / `find_referencing_assets` 等）は Unity 不要。Inspector profile の 3 ツールは read-only だが、常駐 Editor Bridge が前提で、`editor_*` 系と `patch_apply` の confirm 適用も同じ Bridge を使う。

`validate_refs` / `inspect_wiring` / `validate_all_wiring` / `validate_structure` / `validate_materials` は project root の `config/diagnostics_baseline.json` を読むと diagnostics を `new` / `known` / `resolved` に分類する。baseline は自動生成・暗黙更新せず、明示的な `update_diagnostics_baseline` だけが preview / audit-gated write を担う。baseline file 形式は [CONFIGURATION.md](./CONFIGURATION.md)、応答形状と update tool 契約は [docs/api-reference.md](./docs/api-reference.md)、公開 tool 一覧は [docs/tools.md](./docs/tools.md) を正本とする。

推奨フロー: `validate_refs` で参照破損を早期検出 → `inspect_variant` で override 衝突を実効値として可視化 → `patch_apply` の dry-run → 適用時は `confirm=True` + `change_reason`、exactly one open Prefab transaction ではさらに `out_report` を指定して監査ログ付きで適用。

## VRChat エコシステムナレッジ

`knowledge/` ディレクトリに ModularAvatar / liltoon / VRCFury / AvatarOptimizer 等のドメイン知識を 3 レベル（L1 概念 / L2 操作パターン / L3 SerializedProperty）で蓄積し、プラグインに同梱する。`guide` スキルが参照を案内し、AI エージェントが作業に応じて該当ナレッジを `knowledge/` から読む。ナレッジの調査・拡充は `knowledge-acquisition` スキルで行う。編集規約は [knowledge/STYLE_GUIDE.md](./knowledge/STYLE_GUIDE.md)。

## ドキュメントマップ

仕様は専門ドキュメントに分かれて置かれている。目的別の入口は下表のとおり。

| ドキュメント | 内容 |
|--------------|------|
| [ARCHITECTURE.md](./ARCHITECTURE.md) | 構成概観・レイヤ責務・サービス仕様・データモデル・用語集 |
| [docs/tools.md](./docs/tools.md) | 全 MCP ツールの正本カタログ |
| [docs/tool-conventions.md](./docs/tool-conventions.md) | MCP protocol / result 境界と、ツールの住所表現・引数命名・監査ペア要否の規約 |
| [docs/api-reference.md](./docs/api-reference.md) | MCP protocol error、ツール応答エンベロープ、domain error code の正本 |
| [docs/execution-reference.md](./docs/execution-reference.md) | MCP transport / 起動方法 / smoke-batch / ベンチマーク / patch スキーマ / レポート出力フォーマット |
| [TESTING.md](./TESTING.md) | ユニット / 統合 / 回帰 / mutation テストの実行手順とテスト戦略 |
| [CONFIGURATION.md](./CONFIGURATION.md) | `UNITYTOOL_*` 環境変数・`ignore_guids.txt`・scope config 規約 |
| [skills/inspector-profile-authoring/SKILL.md](./skills/inspector-profile-authoring/SKILL.md) | `inspector-profile.v1` の安全な project-local author / repair 手順 |
| [DEBUGGING.md](./DEBUGGING.md) | Bridge エンベロープ / Unity Console / broken reference の調査手順 |
| [CONTRIBUTING.md](./CONTRIBUTING.md) | 開発環境・MCP サーバーの直接起動・テスト・コミット規約・PR フロー |
| [AGENTS.md](./AGENTS.md) | 運用ルールと判断基準の正本 |
| [AGENT_GUIDE.md](./AGENT_GUIDE.md) | AI エージェント向け onboarding（最初の参照点） |
| [CHANGELOG.md](./CHANGELOG.md) | 変更履歴 |
