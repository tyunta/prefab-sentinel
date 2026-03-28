# prefab-sentinel ワークフローパターン

## 基本情報

| 項目 | 値 |
|------|---|
| 対象 | prefab-sentinel MCP ツール群のワークフロー |
| version_tested | prefab-sentinel 0.5.131 |
| last_updated | 2026-03-28 |
| confidence | high |

## L1: 確定パターン

### Prefab 編集（open モード）
`validate_structure` → `patch_apply`(dry-run) → `patch_apply`(confirm) → `validate_refs` → `validate_runtime`

### Prefab 新規作成（create モード）
パッチ計画作成 → `patch_apply`(dry-run) → `patch_apply`(confirm) → `validate_structure` → `validate_refs`

### Editor リモート操作
`editor_select` → `editor_frame` で対象を表示 → `editor_screenshot` で視覚確認。スクショはトリアージの起点として使い、データソースにしない（必ず `inspect_wiring` / `validate_refs` で裏取りする）。

### 見た目の反復調整
`editor_set_material` でスロット差し替え → `editor_set_camera` でアングル調整 → `editor_screenshot` で確認 → 確定後に `revert_overrides` / `patch_apply` で永続化

### Variant の実態調査（Prefab とシーンの乖離）
Prefab ファイルが古い場合やシーン上にオーバーライドがある場合:
1. `get_unity_symbols(expand_nested=true)` で Variant チェーン全体の Nested Prefab 構成を把握
2. `editor_list_children` でシーン上の実際の子オブジェクト構成を確認
3. `editor_list_materials` でシーン上の実際のマテリアル割り当てを確認
4. `inspect_materials` / `inspect_hierarchy` はオフライン解析のため、シーン実態と異なる場合がある

### 壊れた参照の修復
`validate_refs` → `find_referencing_assets` → `ignore_asset_guids` で偽陽性を除外 → safe_fix / decision_required

### オーバーライドの削除
`revert_overrides` → dry-run → confirm のゲート付き。Variant の特定の Modification 行を YAML から除去

## L2: 検査ワークフロー

### フィールド配線検査
`inspect_wiring` → null参照・fileID不整合の特定。重複は same-component（WARNING）/ cross-component（INFO）に分類。Variant ファイルではベース Prefab のコンポーネントを自動解析

### 階層構造の確認
`inspect_hierarchy` → ツリー構造・コンポーネント配置の把握。Variant ファイルではベース Prefab の階層を表示し、オーバーライド付きノードに `[overridden: N]` マーカーを付与

### マテリアル構成の確認
`inspect_materials` → Renderer ごとのマテリアルスロット一覧。Variant チェーンを考慮し、各スロットが `overridden` / `inherited` かを表示

### 内部構造の検証
`validate_structure` → fileID重複・Transform整合性・参照欠損の検出

### ランタイムエラー調査
`validate_runtime` → ログ分類 → アセット特定 → 修正提案

### ランタイム階層確認
`editor_list_children` でシーン実行中の子オブジェクト一覧を取得。`inspect_hierarchy` はファイルベースだが、こちらは Prefab Instance 内のネスト構造も表示

### ランタイムマテリアル確認
`editor_list_materials` で Unity API から直接 Renderer のマテリアルスロット一覧を取得。`inspect_materials` がオフラインで Variant/FBX チェーンを解決できない場合の代替

### Console ログ取得
`editor_console` でリアルタイムログをテキスト取得。batchmode 後の Editor.log 読みではなく、Editor 起動中のバッファからの取得

### シーン一括構築（v0.5.110+）
`editor_batch_create` で構造物を一括生成 → `editor_add_component` でコンポーネント追加 → `editor_batch_set_property` で配線 → `editor_save_scene` で保存。batch 操作は Undo グループ化されるため、Ctrl+Z 1回で全て元に戻せる

### スクリプト開発 → シーン配線
C# ファイルを Write → `editor_recompile` → `editor_console(error)` でコンパイル確認 → `editor_create_udon_program_asset` でアセット作成 → シーン構築 → 配線。UdonSharp 非対応構文（`CompareTag` 等）は `editor_console` で即検出可能

### シーンの新規作成
`editor_open_scene` で既存シーンを開くか、既存 .unity ファイルをコピーして `editor_refresh` → `editor_delete` で不要オブジェクト除去。`File/New Scene` は `editor_execute_menu_item` の危険パスで拒否される

## 判断ルール

- `safe_fix`: 一意で決定的な修正のみ自動適用可
- `decision_required`: ユーザー合意まで保留
- `error` / `critical` が出たら停止し、修正または判断待ちへ回す
- Unity 環境がない場合は dry-run / 検査までで停止
- `patch_apply` の confirm モードでは `change_reason` を必須とする（監査ログ）

## 実運用で学んだこと

- `editor_batch_create` は1回で22オブジェクトまで問題なく動作確認済み（Undo グループ化あり）
- `editor_batch_set_property` は1回で29プロパティまで動作確認済み
- 配列型プロパティ（`Array.size`, `Array.data[N]`）は `editor_set_property` / `editor_batch_set_property` で未サポート。配列フィールドの配線は Inspector 手動操作が必要
- `editor_set_parent` 実行後はオブジェクトのパスが変わる（例: `/Wall_N` → `/Lobby/Wall_N`）。並列呼び出しでパスを参照する場合は移動後のパスを使うこと
- Bridge C# ファイルを Unity プロジェクトにコピーした後、`ResolveComponentType` の `System.ReflectionTypeLoadException` が Unity のデフォルトアセンブリでコンパイルエラーになる。`System.Reflection.ReflectionTypeLoadException` に手動修正が必要（v0.5.110 時点）
- `BridgeVersion` 定数（C# 側）が実際の Plugin バージョンと同期しておらず `"0.5.82"` のままハードコードされている
- Bridge ファイルを手動更新する場合、旧ファイルの位置（`Assets/Editor/` 直下）と新ファイルの位置（`Assets/Editor/PrefabSentinel/`）が異なると CS0101 重複定義エラーになる。旧ファイルを完全に削除してからコピーすること
- `VRCSDKUploadHandler.cs` はプロジェクトの VRC SDK バージョンや対象（Avatar/World）によってコンパイルエラーを起こす。不要なら配置しない
- シーン構築では batch_create → add_component → batch_set_property の3ステップが最も効率的。推定400+ → 実測65回に削減（約80%減）
