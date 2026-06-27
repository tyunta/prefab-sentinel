# AGENT_GUIDE.md — AI エージェント向け onboarding

本ファイルはこのリポジトリで作業を始める AI エージェント（Claude Code / Codex / その他自律エージェント）の最初の参照点。仕様の正本は [README.md](./README.md)、運用ルールの正本は [AGENTS.md](./AGENTS.md)、設定の正本は [CONFIGURATION.md](./CONFIGURATION.md)。本ガイドはそれらへの導線と作業全体の流れを示すだけで、独立した仕様は持たない。

## 最初に読む 5 ファイル

下記の順で読む。途中の判断（既読 / 不要）は許容するが、5 番目までは作業前に最低 1 度は通す。

1. [AGENTS.md](./AGENTS.md) — 運用ルール正本。設計原則・責務境界・必須フロー・API/エラー規約・テスト方針・mutation testing 運用。
2. [README.md](./README.md) — セットアップ手順、「やること / やる内容 / やらないこと」、ドキュメントマップ（各専門ドキュメントへの導線）。
3. [ARCHITECTURE.md](./ARCHITECTURE.md) — mermaid 図と用語集。コンポーネント責務とデータフロー原則の補足。
4. [CONFIGURATION.md](./CONFIGURATION.md) — `UNITYTOOL_*` 環境変数、`ignore_guids.txt` 形式、scope config 規約、`confirm` / `change_reason` 必須対象一覧。
5. 作業領域の `knowledge/*.md` — VRChat エコシステムツール（ModularAvatar / liltoon / VRCFury / AvatarOptimizer 等）に触れる場合は対応する knowledge を Read してから判断する。ファイル特定はコンポーネント型名 / シェーダー名 / パッケージ名から行う（`knowledge/` を Glob して候補を絞る）。

MCP ツールの正本一覧は [docs/tools.md](./docs/tools.md)、エラーコードの正本は [docs/api-reference.md「エラーコード規約」](./docs/api-reference.md#エラーコード規約)。

## AGENTS.md / memory / knowledge の使い分け

エージェントの参照テキストは保存先で役割が分かれる。`memory/` はユーザー個人の状況、`knowledge/` はツールのドメイン知識、AGENTS.md は運用ルール、README.md は仕様。

| 種類 | 保存先 | 例 | 書き戻し条件 |
|------|--------|----|--------------|
| ユーザーの好み・フィードバック | `memory/` | 「コミットは /commit を使う」 | ユーザー指摘・修正依頼を受けたとき |
| プロジェクト固有の状況 | `memory/` | 「Phase 2.3 未実装」 | 状態が変わったとき |
| ツールのドメイン知識 | `knowledge/` | 「MA Merge Armature の mergeTarget は Transform 参照」 | ソースコードから未知の情報を発見・実環境で再現可能なパターンが確立・失敗の原因と回避策が判明したとき |
| 作業で得た技術的知見 | `knowledge/` | 「liltoon の `_MainColorPower` は 0.5 以下だと暗すぎる」 | 同上。inspect で実測した値は confidence を `high` に昇格する |

`knowledge/*.md` の書き戻し詳細は [AGENTS.md §VRChat エコシステムナレッジの自動適用 → 自動書き戻し](./AGENTS.md#自動書き戻し) を参照。

## 自己ルール参照表

エージェントの行動規範はグローバル（`~/.claude/CLAUDE.md`、ユーザー横断）とプロジェクト（このリポジトリの [AGENTS.md](./AGENTS.md)）の 2 層で構成される。判断に迷ったらまず該当層を読む。

| ルール領域 | 正本 | 適用範囲 |
|------------|------|----------|
| 設計原則（Simple > Easy、根拠優先、検証可能性） | [AGENTS.md §設計原則](./AGENTS.md#設計原則) | プロジェクト |
| 責務境界（Services / Skills / MCP） | [AGENTS.md §責務境界](./AGENTS.md#責務境界services--skills--mcp) | プロジェクト |
| 変更時の必須フロー（dry-run → confirm、`change_reason` 必須） | [AGENTS.md §変更時の必須フロー](./AGENTS.md#変更時の必須フロー) | プロジェクト |
| API / エラー規約（envelope 形状、エラーコード） | [AGENTS.md §API / エラー規約](./AGENTS.md#api--エラー規約) + [docs/api-reference.md「レスポンスフォーマット」「エラーコード規約」](./docs/api-reference.md#レスポンスフォーマット) | プロジェクト |
| Editor リモート操作の行動規約（スクショは起点、`inspect wiring` で裏取り） | [AGENTS.md §Editor リモート操作の行動規約](./AGENTS.md#editor-リモート操作の行動規約) | プロジェクト |
| VRChat ナレッジ自動適用 | [AGENTS.md §VRChat エコシステムナレッジの自動適用](./AGENTS.md#vrchat-エコシステムナレッジの自動適用) | プロジェクト |
| Mutation testing 運用 | [AGENTS.md §Mutation testing 運用](./AGENTS.md#mutation-testing-運用) + [TESTING.md §Mutation testing](./TESTING.md#mutation-testing) | プロジェクト |
| Plan モード / サブエージェント戦略 / 裏取り規律 | `~/.claude/CLAUDE.md` | グローバル |
| 並列ツール呼び出し / 自己改善ループ | `~/.claude/CLAUDE.md` | グローバル |

グローバルルールは個別ユーザー環境に置かれているため、レビューでは内容を断定しない（「グローバル `CLAUDE.md` 参照」とだけ示す）。プロジェクトルールはコード化されているので diff で確認する。

## スキル選択ガイド

`/prefab-sentinel:*` の各スキルをいつ呼ぶか。スキルは MCP ツールをゲート付きで組み合わせた運用プロトコル。単発ツールを呼ぶより先にスキル経路を検討する。

| スキル | いつ呼ぶか | 主に使う MCP ツール |
|--------|-----------|---------------------|
| [`/prefab-sentinel:guide`](./skills/guide/SKILL.md) | MCP ツールリファレンスが必要なとき・どのツールを使えばいいか判断するとき・パッチ計画 JSON の構造を確認したいとき | 全 84 ツールのリファレンス（[docs/tools.md](./docs/tools.md) と相互参照） |
| [`/prefab-sentinel:variant-safe-edit`](./skills/variant-safe-edit/SKILL.md) | Prefab / Scene / Asset を編集するとき・パッチ計画を適用するとき（preflight → dry-run → confirm → validate を 1 経路で踏む） | `inspect_variant` / `validate_refs` / `patch_apply` / `validate_runtime` / `revert_overrides` / `set_property` |
| [`/prefab-sentinel:prefab-reference-repair`](./skills/prefab-reference-repair/SKILL.md) | `validate_refs` で broken GUID / fileID が検出されたとき・ignore-guid ポリシーで noise を整理したいとき | `validate_refs` / `find_referencing_assets` |
| [`/prefab-sentinel:udon-log-triage`](./skills/udon-log-triage/SKILL.md) | ランタイム例外 / Udon / ClientSim ログエラーが発生したとき・log-based regression のトリアージ | `validate_runtime` / `find_referencing_assets` / `inspect_wiring` |
| [`/prefab-sentinel:knowledge-acquisition`](./skills/knowledge-acquisition/SKILL.md) | 新しい VRChat コミュニティツールに初めて遭遇したとき・既存ナレッジの confidence が low で作業に支障があるとき | `inspect_wiring` / `get_unity_symbols` / `inspect_hierarchy` / `inspect_materials` / `inspect_material_asset` + Web 検索 |

迷ったらまず `/prefab-sentinel:guide` を呼ぶ。`guide` の冒頭にある「30 秒で動く 3 つの例」「最初に困ったらこの 3 ツール」を読めば、ほとんどの初手は決まる。

## offline / live の権威境界

MCP ツールには 2 つの権威系統がある。混同すると編集結果が観測とずれる。

- **offline 系**（`get_unity_symbols` / `find_unity_symbol` / `set_property` / `set_properties` 等）— `symbol_path` で住所し、last-saved disk YAML を権威とする。Unity 起動不要。
- **live 系**（`editor_*` 系）— `hierarchy_path` で住所し、live editor / 開いている Prefab Stage を権威とする。Editor Bridge 常駐が前提。
- 引数名で側が判別できる: `*_symbol_path` / `*_asset_path` は project / offline、`*_hierarchy_path` は scene / live（規約の正本は [docs/tool-conventions.md](./docs/tool-conventions.md) §1）。
- Editor Bridge 接続中かつ live に未保存変更がある間、offline symbol-reference ツールはペイロードに freshness マーカーを付け、結果が last-saved disk を反映し live と乖離しうることを通知する（issue #40）。マーカーが付いていたら live 編集を保存してから offline 結果を信頼する。
