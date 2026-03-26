# prefab-sentinel Variant 操作パターン

## 基本情報

| 項目 | 値 |
|------|---|
| 対象 | Prefab Variant の検査・操作パターン |
| version_tested | prefab-sentinel 0.5.71 |
| last_updated | 2026-03-26 |
| confidence | high |

## L1: Variant チェーン分析

### いつ使うか
- マテリアルオーバーライドの調査・修正（`inspect_variant` + `inspect_materials`）
- プロパティ値の追跡（origin 付きで各プロパティがどのレベルの Prefab で設定されたか確認）
- Variant 固有の壊れた参照の修復

### Variant チェーン値の追跡
`inspect_variant` で各プロパティ値がどの Prefab で設定されたかを表示。3段 Variant (Base → Mid → Leaf) の場合、各値に `origin_path` と `origin_depth` が付与される。

## L2: VRChat アバター着せ替え（MA/NDMF）

MA (Modular Avatar) / NDMF ベースの VRChat アバターでは、コスメティックや衣装の差し替えに Variant チェーン分析は**不要**。

### MA/NDMF パターン（着せ替え）
- コスメティック（ネイル、リボン、アクセサリ等）はアバタールートの**子 Prefab として追加するだけ**で、MA がビルド時にマージする
- 差し替え = 旧 Prefab を `editor_delete` で削除 + 新 Prefab を `editor_instantiate` で追加
- Variant チェーンの fileID 比較や m_SourcePrefab 書き換えは**不要**
- 衣装差し替えも同様: 旧衣装 Prefab を削除 → 新衣装 Prefab を子として追加

### 判断フロー
```
着せ替え/差し替え作業？
├─ MA/NDMF ベース → 子オブジェクト操作（instantiate/delete）で完結
│   └─ Variant チェーン分析は不要
└─ 非 MA/NDMF or マテリアル修正 → Variant 分析が有効
    └─ inspect_variant / inspect_materials で調査
```
