# Phase 2: Material 編集パス — Design Spec

## 背景

Phase 1 (Editor Bridge 基盤強化) 完了後の次ステップ。髪色チューニング・グラデーション適用セッションで判明した「Material プロパティの読み書きパス欠落」を解消する。

## スコープ

| # | 機能 | 状態 |
|---|------|------|
| 2.1 | `editor_set_material_property` — ランタイム Editor 経由のシェーダープロパティ編集 | 対象 |
| 2.2 | `set_material_property` — オフライン YAML 直接編集 | 対象 |
| 2.3 | SymbolTree .mat 対応 | **見送り** — `inspect_material_asset` で十分カバー。需要確認後に検討 |

実装順: 2.1 → 2.2（spec 通り）。

## やらないこと

- SymbolTree の .mat 対応（YAGNI — 具体的ユースケース未確認）
- `patch_apply` の Material root ハンドル暗黙解決
- バッチプロパティ編集（単一プロパティ × 連続呼び出しで対応）
- `m_TexEnvs` の `m_Scale` / `m_Offset` 書き換え（テクスチャ GUID のみ）

---

## 2.1 `editor_set_material_property` — ランタイム編集

### MCP ツール

```
editor_set_material_property(
    hierarchy_path: str,        # Renderer を持つ GameObject
    material_index: int,        # マテリアルスロット番号 (0-based)
    property_name: str,         # シェーダープロパティ名 ("_Color", "_MainTex" 等)
    value: str,                 # JSON 値 (型はシェーダー定義から自動判定)
)
```

### 型判定: シェーダー型ベース

C# 側で `shader.GetPropertyType()` からプロパティの型を取得し、値をパースする。値の形式から推測しない（シェーダーが正本）。

| シェーダー型 | value 例 | C# メソッド |
|---|---|---|
| Float / Range | `"0.5"` | `Material.SetFloat()` |
| Int | `"2"` | `Material.SetInteger()` |
| Color | `"[1, 0.8, 0.6, 1]"` | `Material.SetColor()` |
| Vector | `"[0, 1, 0, 0]"` | `Material.SetVector()` |
| Texture | `"guid:abc123..."` or `""` (null) | `Material.SetTexture()` |

Color と Vector は同じ 4 要素配列だが、`shader.GetPropertyType()` で一意に区別する。

### C# 実装: `HandleSetMaterialProperty`

`PrefabSentinel.UnityEditorControlBridge.cs` に追加。

処理フロー:
1. `hierarchy_path` → `GameObject.Find()` → Renderer 取得
2. `material_index` → `renderer.sharedMaterials[index]` で Material 取得
3. `shader.FindPropertyIndex(property_name)` でプロパティ存在確認 → 不在なら `EDITOR_CTRL_PROPERTY_NOT_FOUND`
4. `shader.GetPropertyType(index)` で型判定
5. `Undo.RecordObject(material, "Set Material Property")` で Undo 登録
6. 型に応じた `Set*()` 呼び出し:
   - Float/Range: `float.Parse(value)` → `SetFloat()`
   - Int: `int.Parse(value)` → `SetInteger()`
   - Color: JSON 配列 `[r,g,b,a]` パース → `SetColor()`
   - Vector: JSON 配列 `[x,y,z,w]` パース → `SetVector()`
   - Texture: `"guid:..."` → `AssetDatabase.GUIDToAssetPath()` → `LoadAssetAtPath<Texture>()` → `SetTexture()`。空文字列 → `SetTexture(null)`
7. パース失敗時 → `EDITOR_CTRL_PROPERTY_TYPE_MISMATCH`
8. 更新後の値を GET して返す（設定値の検証）

レスポンス `data`:
```json
{
  "property_name": "_Color",
  "property_type": "Color",
  "value": "(1.000, 0.800, 0.600, 1.000)",
  "executed": true
}
```

### EditorControlRequest 拡張

既存フィールドを再利用:
- `hierarchy_path` — 既存
- `material_index` — 既存
- `property_name` — 既存

新規追加:
- `string property_value = string.Empty;` — 設定する値 (JSON 文字列)

### Bridge アクション

`set_material_property` を `SUPPORTED_ACTIONS` に追加（Python + C#）。

### Python 側

- `editor_bridge.py`: `SUPPORTED_ACTIONS` に `"set_material_property"` 追加
- `mcp_server.py`: `editor_set_material_property` ツール登録。`send_action(action="set_material_property", ...)` に委譲

### エラーコード

| コード | 条件 |
|---|---|
| `EDITOR_CTRL_PROPERTY_NOT_FOUND` | `property_name` がシェーダーに存在しない |
| `EDITOR_CTRL_PROPERTY_TYPE_MISMATCH` | 値の形式がシェーダー型と不一致 |
| `EDITOR_CTRL_MISSING_PATH` | `hierarchy_path` 未指定 |
| `EDITOR_CTRL_OBJECT_NOT_FOUND` | GameObject 不在 |
| `EDITOR_CTRL_NO_RENDERER` | Renderer コンポーネント不在 |
| `EDITOR_CTRL_MATERIAL_INDEX` | `material_index` が範囲外 |

### テスト

- Python: MCP ツール登録テスト（tool count 更新）
- Python: `send_action` 委譲テスト（mock）
- Unity 統合: `editor_set_material_property` → `editor_get_material_property` で値読み戻し → 一致確認（Bridge 接続時のみ）

---

## 2.2 `set_material_property` — オフライン YAML 編集

### MCP ツール

```
set_material_property(
    asset_path: str,           # .mat ファイルパス
    property_name: str,        # "_MainGradationStrength" 等
    value: str,                # JSON 値
    confirm: bool = False,     # False = dry-run, True = 書き込み
    change_reason: str = "",   # confirm=True 時の監査ログ理由
)
```

### 処理フロー

1. `material_asset_inspector.inspect_material_asset()` で現在の Material をパース
2. `property_name` を 4 カテゴリ (`m_Floats`, `m_Ints`, `m_Colors`, `m_TexEnvs`) から検索
3. 見つからない場合 → `error` + 全プロパティ名リストを `diagnostics` に含める

#### dry-run (confirm=False)

変更をプレビューするのみ。ファイルは書き換えない。

レスポンス:
```json
{
  "success": true,
  "severity": "info",
  "code": "MAT_PROP_DRY_RUN",
  "message": "Would change _MainGradationStrength from 0.5 to 0.8",
  "data": {
    "asset_path": "Assets/Materials/Hair.mat",
    "property_name": "_MainGradationStrength",
    "category": "m_Floats",
    "before": "0.5",
    "after": "0.8"
  }
}
```

#### confirm=True

1. YAML テキストを読み込み
2. 対象セクション (`m_Floats` 等) を `_extract_section()` で特定
3. エントリ単位で regex 置換:
   - `m_Floats` / `m_Ints`: `- first:\n  {name}\n  second: {old_value}` → `second: {new_value}`
   - `m_Colors`: `second: {r: ..., g: ..., b: ..., a: ...}` → 新しい RGBA 値
   - `m_TexEnvs`: `m_Texture: {fileID: ..., guid: ...}` → 新しい GUID（`m_Scale` / `m_Offset` は保持）
4. ファイル書き戻し
5. `auto_refresh` 呼び出し

レスポンス:
```json
{
  "success": true,
  "severity": "info",
  "code": "MAT_PROP_APPLIED",
  "message": "Changed _MainGradationStrength from 0.5 to 0.8",
  "data": {
    "asset_path": "Assets/Materials/Hair.mat",
    "property_name": "_MainGradationStrength",
    "category": "m_Floats",
    "before": "0.5",
    "after": "0.8",
    "auto_refresh": "true"
  }
}
```

### value の形式（カテゴリ別）

| カテゴリ | value 例 | YAML 表現 |
|---|---|---|
| `m_Floats` | `"0.8"` | `second: 0.8` |
| `m_Ints` | `"2"` | `second: 2` |
| `m_Colors` | `"[1, 0.8, 0.6, 1]"` | `second: {r: 1, g: 0.8, b: 0.6, a: 1}` |
| `m_TexEnvs` | `"guid:abc123..."` | `m_Texture: {fileID: 2800000, guid: abc123..., type: 2}` |

テクスチャの null 化: `""` → `{fileID: 0, guid: , type: 0}`

### 実装箇所

| ファイル | 変更内容 |
|---|---|
| `prefab_sentinel/material_asset_inspector.py` | `write_material_property()` 追加 — パースロジック再利用、YAML regex 置換 |
| `prefab_sentinel/orchestrator.py` | `set_material_property()` メソッド追加 — dry-run/confirm 分岐、auto_refresh |
| `prefab_sentinel/mcp_server.py` | `set_material_property` MCP ツール登録 |

### エラーケース

| 条件 | severity | 対応 |
|---|---|---|
| `.mat` 以外のファイル | `error` | `"Expected .mat file"` |
| ファイル不在 | `error` | `"File not found"` |
| プロパティ名が見つからない | `error` | 全プロパティ名リストを `diagnostics` に含めて提示 |
| 値のパース失敗 | `error` | 期待される形式を `message` に含めて提示 |
| `confirm=True` で `change_reason` 未指定 | `error` | `"change_reason is required"` |

### テスト

- Unit: `write_material_property()` の 4 カテゴリ × dry-run / confirm
- Unit: プロパティ名不在時のエラー + 候補リスト
- Unit: 値パース失敗時のエラー
- Unit: MCP ツール登録 (tool count 更新)
- Integration: .mat ファイル書き換え → 再パース → 値一致確認

---

## テスト方針

### 2.1 テスト

| レイヤー | 内容 |
|---|---|
| Python unit | MCP ツール登録テスト、`send_action` 委譲テスト (mock) |
| Unity 統合 | `set → get → 値一致` 往復確認（Bridge 必要、opt-in） |

### 2.2 テスト

| レイヤー | 内容 |
|---|---|
| Python unit | `write_material_property()` の 4 カテゴリ書き換え |
| Python unit | dry-run プレビュー正確性 |
| Python unit | エラーケース (不在プロパティ、パース失敗、非 .mat ファイル) |
| Python unit | MCP ツール登録 |
| Python integration | .mat ファイル write → re-parse → 値一致 |
