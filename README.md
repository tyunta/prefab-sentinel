# Prefab Sentinel

[![CI](https://img.shields.io/github/actions/workflow/status/tyunta/prefab-sentinel/ci.yml?branch=main&label=CI)](https://github.com/tyunta/prefab-sentinel/actions/workflows/ci.yml)
[![License](https://img.shields.io/github/license/tyunta/prefab-sentinel.svg)](./LICENSE)
[![Python](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/)

Unity / VRChat プロジェクトの Prefab / Scene / Asset を安全に検査・編集する MCP サーバー。

`Variant` の override 衝突、`Broken PPtr` / missing fileID、Udon / ClientSim ランタイム例外を構造化された応答で診断し、手作業 YAML 編集を経由せずに修復する。AI エージェント前提の設計。

read-only 経路（`validate_refs` / `inspect_*` / `find_*` 等）は Unity を起動せず YAML 直読みで完結する。書き込み経路（`patch_apply` / `set_property` / `editor_*` 等）は常駐 Editor Bridge との file-IPC で動き、`confirm=True` + 非空 `change_reason` の監査ペアを欠く呼び出しは `CHANGE_REASON_REQUIRED` で拒否される。

本 README は各専門ドキュメントへの入口（[ドキュメントマップ](#ドキュメントマップ) 参照）。仕様の正本は専門ドキュメント群、運用ルールの正本は [CLAUDE.md](./CLAUDE.md)。

## やること / やらないこと

**やること**

- Unity SerializedObject レベルの安全な編集基盤を提供し、Prefab Base / Variant / Scene インスタンスの実効値を追跡可能にする
- 参照解決（GUID + fileID）と整合性検証を API 化する
- 実行時検証（UdonSharp compile / ClientSim smoke / ログ分類）をパイプライン化する
- 人間の判断が要る変更と、機械的に実行できる変更を明確に分離する
- ModularAvatar / liltoon / VRCFury 等の VRChat エコシステムツールのドメイン知識を同梱し、AI エージェントの判断材料として供給する
- Skills として運用フローを標準化する（Claude Code / Codex CLI の両ホストに対応）

**やらないこと**

- YAML 文字列の直接置換を標準手段にしない
- Unity 内部参照を推測で補完しない
- 変更根拠のない自動最適化をしない
- ユーザー判断が要る仕様変更を勝手に適用しない

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

[Quickstart](#quickstart) の 2 コマンドで導入する。インストールすると MCP サーバー・5 つのスキル・`knowledge/` ディレクトリが一括展開され、`/prefab-sentinel:guide` 等のスキルを Claude Code から直接呼び出せる。各スキル内のコマンドは `${CLAUDE_PLUGIN_ROOT}` テンプレート変数でローカルから実行される。

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

### Unity Bridge

パッチ実適用・ランタイム検証など書き込み系の経路は、Unity Editor 内に常駐する Editor Bridge との file-IPC で動く。Bridge のセットアップ手順は `/prefab-sentinel:guide` スキルに、watch ディレクトリを指定する環境変数 `UNITYTOOL_BRIDGE_WATCH_DIR` は [CONFIGURATION.md](./CONFIGURATION.md) に記載。未設定で書き込み系ツールを呼ぶと `BRIDGE_WATCH_DIR_MISSING` で fail-fast 停止する（read-only 検査のみなら設定不要）。

## 代表的な MCP ツール

全 MCP ツールの正本カタログは [docs/tools.md](./docs/tools.md)、応答エンベロープ（`success / severity / code / message / data / diagnostics`）とエラーコードの正本は [docs/api-reference.md](./docs/api-reference.md)。下表は代表ツールのみ。

| ツール | 説明 |
|--------|------|
| `activate_project` | プロジェクトスコープ設定 + キャッシュ warm（セッション開始時に呼ぶ） |
| `validate_refs` | 壊れた GUID / fileID 参照のスキャン |
| `validate_structure` | YAML 内部構造の検証（fileID 重複・Transform 整合性） |
| `inspect_wiring` | MonoBehaviour フィールド配線の分析（null 参照の分類付き） |
| `inspect_variant` | Prefab Variant の override チェーン分析 |
| `find_referencing_assets` | GUID / パスの参照元アセット検索 |
| `patch_apply` | パッチ計画の検証・適用（dry-run / confirm ゲート付き） |
| `validate_runtime` | 既定 `compile_only` の UdonSharp compile 検証。ClientSim は `profile="clientsim"` + audit pair で明示 opt-in |
| `editor_get_transform` / `editor_get_bounds` / `editor_measure_distance` | Editor Bridge 経由の read-only live geometry 検査 |
| `editor_*` | Editor Bridge 経由の Scene / Hierarchy / Component / BlendShape / Animation 編集、スクリーンショット、Console、UdonSharp field / array write |

Routine CI / agent validation では `validate_runtime(profile="compile_only")` または `validate_runtime(profile="editor_console_only")` を使う。ClientSim は submission scene 向けの明示 opt-in で、`profile="clientsim"` + audit pair が揃った場合だけ実行する。

read-only 検査（`validate_*` / `inspect_*` / `find_*`）は Unity 不要、`editor_*` 系と `patch_apply` の confirm 適用は Editor Bridge 常駐が前提。

推奨フロー: `validate_refs` で参照破損を早期検出 → `inspect_variant` で override 衝突を実効値として可視化 → `patch_apply` の dry-run → `confirm=True` + `change_reason` + `out_report` で監査ログ付きの適用。

## VRChat エコシステムナレッジ

`knowledge/` ディレクトリに ModularAvatar / liltoon / VRCFury / AvatarOptimizer 等のドメイン知識を 3 レベル（L1 概念 / L2 操作パターン / L3 SerializedProperty）で蓄積し、プラグインに同梱する。`guide` スキルが参照を案内し、AI エージェントが作業に応じて該当ナレッジを `knowledge/` から読む。ナレッジの調査・拡充は `knowledge-acquisition` スキルで行う。編集規約は [knowledge/STYLE_GUIDE.md](./knowledge/STYLE_GUIDE.md)。

## ドキュメントマップ

仕様は専門ドキュメントに分かれて置かれている。目的別の入口は下表のとおり。

| ドキュメント | 内容 |
|--------------|------|
| [ARCHITECTURE.md](./ARCHITECTURE.md) | 構成概観・レイヤ責務・サービス仕様・データモデル・用語集 |
| [docs/tools.md](./docs/tools.md) | 全 MCP ツールの正本カタログ |
| [docs/tool-conventions.md](./docs/tool-conventions.md) | MCP ツールの住所表現・引数命名・監査ペア要否の規約 |
| [docs/api-reference.md](./docs/api-reference.md) | MCP 応答エンベロープの形状とエラーコードの正本 |
| [docs/execution-reference.md](./docs/execution-reference.md) | MCP サーバーの実行リファレンス / smoke-batch / ベンチマーク / patch スキーマ / レポート出力フォーマット |
| [TESTING.md](./TESTING.md) | ユニット / 統合 / 回帰 / mutation テストの実行手順とテスト戦略 |
| [CONFIGURATION.md](./CONFIGURATION.md) | `UNITYTOOL_*` 環境変数・`ignore_guids.txt`・scope config 規約 |
| [DEBUGGING.md](./DEBUGGING.md) | Bridge エンベロープ / Unity Console / broken reference の調査手順 |
| [CONTRIBUTING.md](./CONTRIBUTING.md) | 開発環境・MCP サーバーの直接起動・テスト・コミット規約・PR フロー |
| [CLAUDE.md](./CLAUDE.md) | 運用ルールと判断基準の正本 |
| [AGENT_GUIDE.md](./AGENT_GUIDE.md) | AI エージェント向け onboarding（最初の参照点） |
| [CHANGELOG.md](./CHANGELOG.md) | 変更履歴 |
