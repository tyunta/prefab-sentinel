# prefab-sentinel マテリアル操作パターン

## 基本情報

| 項目 | 値 |
|------|---|
| 対象 | MCP ツールによるマテリアル読み書きパターン |
| version_tested | prefab-sentinel 0.5.131 |
| last_updated | 2026-03-28 |
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
    value="path:Assets/Foo/Bar/texture.png"
)
```
`path:Assets/...` 形式で指定可能（GUID 指定も可: `guid:abc123...`）。

liltoon では同一テクスチャが複数スロットに設定されていることが多い。髪マテリアルの場合:
- `_MainTex`, `_BaseMap`, `_BaseColorMap`: メインカラー
- `_ShadowColorTex`: 影色テクスチャ（メインと同じことが多い）
- `_OutlineTex`: アウトライン色（メインと同じことが多い）

全スロットを揃えて変更しないと色が不整合になる。

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

### 2026-03-28: Variant とシーン実体の乖離
- `inspect_materials` はオフラインで Prefab ファイルを解析するため、シーン上でオーバーライドされたマテリアル割り当てが反映されない場合がある
- Prefab Variant が深いネスト（Variant → Base → Nested Prefab）の場合、`inspect_materials` は最も深い Nested Prefab のレンダラーしか返さないことがある
- **実態確認は `editor_list_materials` を使う**。特に「Prefab が古い」とユーザーが言った場合は必ずシーン側を確認

### 2026-03-28: 外部マテリアルの編集手順
- Assets/Tyunta 以外のマテリアルは readonly ルールに従いコピーしてから編集
- 手順: bash で .mat をコピー → `editor_refresh` → `editor_set_material` でスロット差し替え → `editor_set_material_property` で調整
- .mat.meta もコピーすると GUID が重複するため、コピーせず Unity に新規生成させる方が安全

### 2026-03-28: editor_screenshot のタイミング問題
- `editor_recompile` 後のドメインリロード中はスクショが失敗する（レスポンスファイル未生成）
- スクショ自体は撮れているがレスポンスが返らないケースがある。`ls -t screenshots/` でファイル存在を確認し、Read で取得すれば回避可能
