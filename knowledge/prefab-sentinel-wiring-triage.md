# prefab-sentinel 配線検査トリアージ

## 基本情報

| 項目 | 値 |
|------|---|
| 対象 | inspect_wiring の結果の読み方と対処法 |
| version_tested | prefab-sentinel 0.5.71 |
| last_updated | 2026-03-26 |
| confidence | high |

## L1: 分類と対処

### null 参照（severity: error）
- フィールドが null / missing で、スクリプトが必須参照として使用
- 対処: `set_property` または `patch_apply` で正しい参照を設定

### fileID 不整合（severity: error）
- 参照先の fileID が対象ファイル内に存在しない
- 原因: Base Prefab の構造変更後に Variant の override が追従できていない
- 対処: `validate_refs` で詳細確認 → `prefab-reference-repair` スキルで修復

### 重複参照
- **same-component（severity: warning）**: 同一コンポーネント内の複数フィールドが同じオブジェクトを参照
- **cross-component（severity: info）**: 異なるコンポーネントから同じオブジェクトを参照（通常は正常）

## L2: トリアージフロー

1. `inspect_wiring --path <target>` を実行
2. error 件数を確認 -- 0 件なら配線は健全
3. null 参照の error を優先対処（ランタイム停止リスク）
4. fileID 不整合は `validate_refs` で追加調査
5. warning/info は状況に応じて対応（多くは許容可能）

### Variant ファイルの注意点
- Variant に対して `inspect_wiring` を実行すると、ベース Prefab のコンポーネントも自動解析される
- override で上書きされた参照は Variant 側の値が表示される
