# prefab-sentinel ワークフローパターン

## 基本情報

| 項目 | 値 |
|------|---|
| 対象 | prefab-sentinel MCP ツール群のワークフロー |
| version_tested | prefab-sentinel 0.5.71 |
| last_updated | 2026-03-26 |
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

## 判断ルール

- `safe_fix`: 一意で決定的な修正のみ自動適用可
- `decision_required`: ユーザー合意まで保留
- `error` / `critical` が出たら停止し、修正または判断待ちへ回す
- Unity 環境がない場合は dry-run / 検査までで停止
- `patch_apply` の confirm モードでは `change_reason` を必須とする（監査ログ）
