# prefab-sentinel パッチ計画パターン

## 基本情報

| 項目 | 値 |
|------|---|
| 対象 | patch_apply で使う JSON パッチ計画の実例 |
| version_tested | prefab-sentinel 0.5.71 |
| last_updated | 2026-03-26 |
| confidence | high |

## L1: 基本パターン

### 単一プロパティの変更（open モード）
```json
{
  "plan_version": 2,
  "resources": [
    {"id": "r1", "kind": "prefab", "path": "Assets/.../Target.prefab", "mode": "open"}
  ],
  "ops": [
    {"resource": "r1", "op": "set", "component": "SkinnedMeshRenderer", "path": "m_Enabled", "value": true}
  ]
}
```

### 配列要素の挿入
```json
{"resource": "r1", "op": "insert_array_element", "component": "MyScript", "path": "m_Items.Array.data", "index": 0, "value": "newItem"}
```
**注意:** パスは `.Array.data` で終わる必要がある。

### ObjectReference の設定（open モード）
```json
{"resource": "r1", "op": "set", "component": "MyScript", "path": "m_Target", "value": {"guid": "abc123...", "fileID": 10207}}
```
open モードではハンドル文字列（`"$root"` 等）は使えない。

## L2: 複合パターン

### Prefab 新規作成 + コンポーネント追加
```json
{
  "plan_version": 2,
  "resources": [
    {"id": "r1", "kind": "prefab", "path": "Assets/.../New.prefab", "mode": "create"}
  ],
  "ops": [
    {"resource": "r1", "op": "create_prefab", "name": "MyObject"},
    {"resource": "r1", "op": "add_component", "target": "root", "type": "MeshFilter", "result": "mf"},
    {"resource": "r1", "op": "add_component", "target": "root", "type": "MeshRenderer", "result": "mr"},
    {"resource": "r1", "op": "set", "target": "mf", "path": "m_Mesh", "value": {"fileID": 10207, "guid": "0000000000000000e000000000000000", "type": 0}},
    {"resource": "r1", "op": "save"}
  ]
}
```

### Scene 内のプロパティ変更
```json
{
  "plan_version": 2,
  "resources": [
    {"id": "s1", "kind": "scene", "path": "Assets/Scenes/Main.unity", "mode": "open"}
  ],
  "ops": [
    {"resource": "s1", "op": "open_scene"},
    {"resource": "s1", "op": "find_component", "target": "$scene", "type": "UnityEngine.Light", "result": "$light"},
    {"resource": "s1", "op": "set", "target": "$light", "path": "m_Intensity", "value": 2.5},
    {"resource": "s1", "op": "save_scene"}
  ]
}
```

## 実運用で学んだこと

- dry-run は必ず先に実行する。特に配列操作はインデックスのずれに注意
- `--change-reason` は後から監査ログを読み返すときに非常に有用
- 同型コンポーネントが複数ある場合は `TypeName@/hierarchy/path` で曖昧性を解消する
