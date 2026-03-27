# Prefab Sentinel v0.5.85 — フル MCP ワークフロー達成レポート

**日付**: 2026-03-27
**プロジェクト**: `D:\VRChatProject\PS-WORLD-TEST`
**前提**: v0.5.84 レポート (`report_20260327_full_tool_coverage.md`) の続き

---

## 1. 達成事項

**v0.5.85 で UdonSharp ワールド構築の全工程が MCP のみで完結した。**

v0.5.84 では YAML 直接編集が必要だった2つの操作が新ツールで解消:

| 操作 | v0.5.84 | v0.5.85 |
|------|---------|---------|
| UdonSharp フィールド配線 | YAML 直接編集 + `editor_refresh` | **`editor_set_property`** (`object_reference` でヒエラルキーパス指定) |
| Prefab 化 | YAML で Prefab ファイルを構築 | **`editor_save_as_prefab`** (`PrefabUtility.SaveAsPrefabAsset`) |

## 2. 新ツール検証結果

### editor_set_property
- **用途**: SerializedObject API 経由でコンポーネントのプロパティを設定
- **UdonSharp 対応**: UdonSharp フィールドへの ObjectReference 設定が可能
- **配線方法**: `object_reference` にヒエラルキーパス（例: `/HelloWorldSystem/ToggleTarget`）を指定
- **結果**: `targetObject` フィールドに ToggleTarget を正しく配線、`inspect_wiring` で null 0/1 を確認

### editor_save_as_prefab
- **用途**: シーンの GameObject を `PrefabUtility.SaveAsPrefabAsset` で正規 Prefab として保存
- **結果**: backing UdonBehaviour 含む全コンポーネントが正しく Prefab に含まれる
- **YAML 直接構築との違い**: 正規 API なので隠しコンポーネントの欠落リスクがない

## 3. 良い点

### 3.1 フル MCP ワークフローの完成
スクリプト作成からランタイム動作確認まで、Unity Inspector での手動操作が一切不要:
```
Write (.cs) → editor_recompile → editor_create_udon_program_asset
→ editor_execute_menu_item (Create Empty Child) → editor_rename
→ editor_add_component (UdonSharp) → editor_set_property (配線)
→ editor_save_as_prefab → inspect_wiring (検証)
```

### 3.2 editor_set_property の直感性
- ヒエラルキーパスで参照先を指定できるので、fileID を調べる必要がない
- コンポーネントタイプ名（`HelloWorldButton`）で対象を特定でき、GUID 不要
- UdonSharp の特殊なシリアライズ形式を内部で処理してくれる

### 3.3 editor_save_as_prefab の正規性
- `PrefabUtility.SaveAsPrefabAsset` を使うので、Unity の正規 Prefab 化と同等
- backing UdonBehaviour、Collider、全コンポーネントが漏れなく含まれる
- Prefab Instance と Prefab Variant の自動判別あり

### 3.4 Create Empty Child の活用
- `editor_select` → `editor_execute_menu_item("GameObject/Create Empty Child")` で親子関係をその場で構築
- 専用の「set parent」ツールがなくても階層構築が可能

## 4. 悪い点・課題

### 4.1 editor_add_component の型名解決が不安定
- `BoxCollider` → `TYPE_NOT_FOUND`。`UnityEngine.BoxCollider` なら成功
- `MeshFilter`, `MeshRenderer` は短縮名で成功
- `HelloWorldButton`（UdonSharp）は短縮名で成功
- **一貫性がない** — どの型で完全修飾名が必要か予測できない

### 4.2 ブリッジのプロトコルバージョン不一致
- v0.5.85 の新ツール（`editor_set_property`, `editor_save_as_prefab`）は新しいプロトコルバージョンを要求
- プロジェクト側のブリッジが古いと `UNITY_BRIDGE_PROTOCOL_VERSION` エラー
- ブリッジ更新を手動でコピーする必要がある

### 4.3 ブリッジ配布の手間
- 新しいプロジェクトで使う度に 5 ファイル（EditorBridge, UnityEditorControlBridge, UnityPatchBridge, UnityRuntimeValidationBridge, UnityIntegrationTests）をコピー
- バージョン不一致の検出はあるが、自動更新はない

## 5. 改善提案

| 優先度 | 提案 | 理由 |
|--------|------|------|
| High | `editor_add_component` の型名解決を改善（短縮名で UnityEngine 名前空間を自動検索） | `BoxCollider` で失敗するのは UX が悪い |
| Medium | ブリッジの自動デプロイ機能（`activate_project` 時にバージョン不一致なら自動コピー） | 毎回手動コピーが面倒 |
| Medium | `editor_set_parent` ツール新設（既存 GO の親子関係変更） | `Create Empty Child` は新規作成時のみ。既存 GO の reparent には対応できない |
| Low | `editor_add_component` 成功時に追加されたコンポーネントの型名を返す | 完全修飾名が使われた場合にどの型が追加されたか確認しやすくなる |

## 6. バージョン別ツール対応表

| ツール | v0.5.82 | v0.5.84 | v0.5.85 |
|--------|---------|---------|---------|
| editor_rename | - | NEW | OK |
| editor_add_component | - | NEW | OK |
| editor_create_udon_program_asset | - | NEW | OK |
| editor_set_property | - | - | NEW |
| editor_save_as_prefab | - | - | NEW |
| editor_find_renderers_by_material | - | NEW (v0.5.83) | OK |
| editor_set_material (material_path) | - | NEW (v0.5.83) | OK |

## 7. テスト実行ログ

| # | 操作 | 結果 |
|---|------|------|
| 1 | `editor_delete` /HelloWorldSystem (前回分削除) | OK |
| 2 | `editor_execute_menu_item` Create Empty | OK |
| 3 | `editor_rename` → HelloWorldSystem | OK |
| 4 | `editor_select` + Create Empty Child | OK |
| 5 | `editor_rename` → HelloButton | OK |
| 6 | `editor_select` + Create Empty Child | OK |
| 7 | `editor_rename` → ToggleTarget | OK |
| 8 | `editor_add_component` HelloWorldButton (UdonSharp) | OK |
| 9 | `editor_add_component` BoxCollider | NG → `UnityEngine.BoxCollider` で OK |
| 10 | `editor_add_component` MeshFilter | OK |
| 11 | `editor_add_component` MeshRenderer | OK |
| 12 | **`editor_set_property`** targetObject → /HelloWorldSystem/ToggleTarget | **OK** |
| 13 | **`editor_save_as_prefab`** → Assets/Prefabs/HelloWorldSystem2.prefab | **OK** |
| 14 | `inspect_hierarchy` | OK — 3 GO, 7 components |
| 15 | `inspect_wiring` | OK — null 0/1 |
