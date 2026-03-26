# prefab-sentinel マテリアル操作パターン

## 基本情報

| 項目 | 値 |
|------|---|
| 対象 | MCP ツールによるマテリアル読み書きパターン |
| version_tested | prefab-sentinel 0.5.71 |
| last_updated | 2026-03-26 |
| confidence | high |

## L1: ツール選択

| 目的 | ツール | 備考 |
|------|--------|------|
| オフラインで .mat の内容確認 | `inspect_material_asset` | Unity 不要 |
| ランタイムのスロット一覧 | `editor_list_materials` | Editor Bridge 必須 |
| ランタイムのプロパティ読み取り | `editor_get_material_property` | Editor Bridge 必須 |
| ランタイムでプロパティ変更（一時的） | `editor_set_material_property` | Undo 対応、再生停止で戻る |
| .mat ファイルを永続変更 | `set_material_property` | dry-run/confirm ゲート付き |
| マテリアルスロット差し替え | `editor_set_material` | Undo 対応 |

## L2: 実践パターン

### liltoon カラー変更
```
editor_get_material_property(property_name="_Color")
→ 現在値を確認
editor_set_material_property(property_name="_Color", property_value='{"r":1,"g":0.8,"b":0.7,"a":1}')
→ ランタイムで即時プレビュー
editor_screenshot()
→ 視覚確認
```
永続化する場合は `set_material_property` で .mat ファイルを直接編集。

### テクスチャ差し替え
```
editor_set_material_property(
    property_name="_MainTex",
    property_value='{"guid":"<新テクスチャのGUID>","fileID":2800000}'
)
```
fileID 2800000 はテクスチャアセットの標準 fileID。

### float プロパティ調整
```
editor_get_material_property(property_name="_MainColorPower")
→ 現在値を確認（liltoon: 0.0〜1.0、0.5 以下だと暗すぎることが多い）
editor_set_material_property(property_name="_MainColorPower", property_value="0.7")
```

## 実運用で学んだこと

### 2026-03-26: マテリアル操作の実測
- `editor_list_materials` / `editor_get_material_property` / `editor_set_material_property` はいずれも <1s で応答
- `editor_set_material_property` の型はシェーダー定義から自動判定される（明示不要）
- `editor_screenshot` と組み合わせた反復調整が非常にスムーズ
