# Editor Set Property + Save As Prefab 設計

**日付:** 2026-03-27
**由来:** report_20260327_full_tool_coverage.md — UdonSharp フィールド配線と Prefab 化の MCP 対応
**アプローチ:** C# EditorControlBridge ハンドラ + Python MCP ラッパー (Editor Bridge 方式)

---

## 概要

2 ツール追加:

1. **`editor_set_property`** — Unity の `SerializedObject` API 経由でコンポーネントのフィールド値を設定。UdonSharp 含む全コンポーネント対応。
2. **`editor_save_as_prefab`** — シーン上の GO を Prefab / Variant として保存 (`PrefabUtility.SaveAsPrefabAsset`)。

---

## 1. editor_set_property

### 動機

`set_property` (YAML パッチ) は UdonSharp コンポーネントで `SYMBOL_UNRESOLVABLE` エラーとなる。UdonBehaviour の GUID がプロジェクトの `.cs` マップに無いため。Editor Bridge 経由なら Unity の `SerializedObject` API が Udon VM シリアライズを自動処理する。

### C# ハンドラ: HandleEditorSetProperty

**リクエストフィールド:**

| フィールド | 型 | 既存/新規 | 用途 |
|-----------|---|----------|------|
| `hierarchy_path` | string | 既存 | 対象 GO のヒエラルキーパス |
| `component_type` | string | 既存 | コンポーネント型名 (簡略名 or 完全修飾名) |
| `property_name` | string | 既存 | SerializedProperty パス (例: `targetObject`, `m_Speed`) |
| `property_value` | string | 既存 | プリミティブ / enum 値 (文字列表現) |
| `object_reference` | string | **新規** | ObjectReference 値 (ヒエラルキーパス or アセットパス) |

`property_value` と `object_reference` は排他。どちらか一方を指定。

**処理フロー:**

1. バリデーション: `hierarchy_path`, `component_type`, `property_name` 必須。`property_value` と `object_reference` のどちらかが必須
2. `GameObject.Find(hierarchy_path)` で GO 取得
3. `ResolveComponentType(component_type)` → `go.GetComponent(type)` でコンポーネント取得
4. `new SerializedObject(component)` → `FindProperty(property_name)` でプロパティ取得
5. `SerializedProperty.propertyType` で型を自動判定し、値をセット
6. `serializedObject.ApplyModifiedProperties()` で適用 (Undo 自動管理)

**型別パース:**

| propertyType | 入力例 | C# 処理 |
|---|---|---|
| Integer | `"42"` | `prop.intValue = int.Parse(v, InvariantCulture)` |
| Float | `"3.14"` | `prop.floatValue = float.Parse(v, InvariantCulture)` |
| Boolean | `"true"` | `prop.boolValue = bool.Parse(v)` |
| String | `"hello"` | `prop.stringValue = v` |
| Enum | `"Off"` or `"2"` | 名前→`enumNames` 照合、数値→`enumValueIndex` |
| Color | `"1,0.5,0,1"` | 4 float パース → `prop.colorValue` (RGBA) |
| Vector2 | `"1,2"` | 2 float パース → `prop.vector2Value` |
| Vector3 | `"1,2,3"` | 3 float パース → `prop.vector3Value` |
| Vector4 | `"1,2,3,4"` | 4 float パース → `prop.vector4Value` |
| ObjectReference | (別パラメータ) | 後述の ObjectReference 解決 |

**ObjectReference 解決順序:**

1. `GameObject.Find(object_reference)` — シーン内 GO を探す
2. GO 上のコンポーネント: パスに型情報が含まれる場合 (例: `/MyObj:AudioSource`)、GO 取得後 `GetComponent` で解決
3. `AssetDatabase.LoadAssetAtPath(object_reference)` — プロジェクトアセット

コロン区切り (`/path:ComponentType`) でコンポーネント参照を指定する規約とする。コロンが無い場合は GO 自体を参照する。

**エラーコード:**

| コード | 条件 |
|--------|------|
| `EDITOR_CTRL_SET_PROP_NO_PATH` | hierarchy_path 未指定 |
| `EDITOR_CTRL_SET_PROP_NO_COMP` | component_type 未指定 |
| `EDITOR_CTRL_SET_PROP_NO_FIELD` | property_name 未指定 |
| `EDITOR_CTRL_SET_PROP_NO_VALUE` | value も object_reference も未指定 |
| `EDITOR_CTRL_SET_PROP_NOT_FOUND` | GO が見つからない |
| `EDITOR_CTRL_SET_PROP_COMP_NOT_FOUND` | コンポーネントが見つからない |
| `EDITOR_CTRL_SET_PROP_FIELD_NOT_FOUND` | SerializedProperty が見つからない |
| `EDITOR_CTRL_SET_PROP_REF_NOT_FOUND` | object_reference の解決失敗 |
| `EDITOR_CTRL_SET_PROP_TYPE_MISMATCH` | 値の型変換失敗 |

### Python MCP ツール

```python
@server.tool()
def editor_set_property(
    hierarchy_path: str,
    component_type: str,
    property_name: str,
    value: str = "",
    object_reference: str = "",
) -> dict[str, Any]:
    """Set a serialized property on a component via Unity's SerializedObject API.

    Supports all SerializedProperty types including UdonSharp fields.
    Type is auto-detected from the property. Use value for primitives/enum,
    object_reference for ObjectReference fields.

    For object_reference, specify a hierarchy path (e.g. "/ToggleTarget")
    for scene objects, or an asset path (e.g. "Assets/Materials/Red.mat")
    for project assets. Append :ComponentType to reference a specific
    component (e.g. "/MyObj:AudioSource").

    Args:
        hierarchy_path: Hierarchy path to the GameObject.
        component_type: Component type name (simple or fully qualified).
        property_name: SerializedProperty path (e.g. "targetObject", "m_Speed").
        value: Value for primitive/enum properties (auto-parsed by type).
        object_reference: Hierarchy path or asset path for ObjectReference properties.
    """
    kwargs: dict[str, Any] = {
        "hierarchy_path": hierarchy_path,
        "component_type": component_type,
        "property_name": property_name,
    }
    if object_reference:
        kwargs["object_reference"] = object_reference
    else:
        kwargs["property_value"] = value
    return send_action(action="editor_set_property", **kwargs)
```

---

## 2. editor_save_as_prefab

### 動機

ヒエラルキーの GO を Prefab として保存する MCP ツールが無い。`PrefabUtility.SaveAsPrefabAsset` は GO の Prefab 接続状態に応じて自動的に新規 Prefab / Variant を作り分ける。

### C# ハンドラ: HandleSaveAsPrefab

**リクエストフィールド:**

| フィールド | 型 | 既存/新規 | 用途 |
|-----------|---|----------|------|
| `hierarchy_path` | string | 既存 | Prefab 化する GO |
| `asset_path` | string | 既存 | 出力 `.prefab` パス |

**処理フロー:**

1. バリデーション: `hierarchy_path`, `asset_path` 必須。`asset_path` が `.prefab` で終わること
2. `GameObject.Find(hierarchy_path)` で GO 取得
3. 出力ディレクトリが無ければ `Directory.CreateDirectory()` で作成
4. Variant 判定: `PrefabUtility.IsPartOfPrefabInstance(go)`
5. `PrefabUtility.SaveAsPrefabAsset(go, asset_path, out bool success)` で保存
6. `success` が false なら `EDITOR_CTRL_SAVE_PREFAB_FAILED`
7. 成功レスポンス: `output_path`, Variant かどうか、ベース Prefab パスを返す

**Variant 判定と情報取得:**

```csharp
bool isVariant = PrefabUtility.IsPartOfPrefabInstance(go);
string basePrefabPath = "";
if (isVariant)
{
    var basePrefab = PrefabUtility.GetCorrespondingObjectFromSource(go);
    basePrefabPath = AssetDatabase.GetAssetPath(basePrefab);
}
```

**エラーコード:**

| コード | 条件 |
|--------|------|
| `EDITOR_CTRL_SAVE_PREFAB_NO_PATH` | hierarchy_path 未指定 |
| `EDITOR_CTRL_SAVE_PREFAB_NO_OUTPUT` | asset_path 未指定 |
| `EDITOR_CTRL_SAVE_PREFAB_BAD_EXT` | `.prefab` 以外の拡張子 |
| `EDITOR_CTRL_SAVE_PREFAB_NOT_FOUND` | GO が見つからない |
| `EDITOR_CTRL_SAVE_PREFAB_FAILED` | SaveAsPrefabAsset 失敗 |

### Python MCP ツール

```python
@server.tool()
def editor_save_as_prefab(
    hierarchy_path: str,
    asset_path: str,
) -> dict[str, Any]:
    """Save a scene GameObject as a Prefab or Prefab Variant asset.

    If the GameObject is a Prefab instance (connected to a base),
    the result is automatically a Prefab Variant.
    If it's a plain GameObject, a new original Prefab is created.

    Args:
        hierarchy_path: Hierarchy path to the GameObject to save.
        asset_path: Output .prefab path (e.g. "Assets/Prefabs/MyObj.prefab").
    """
    return send_action(
        action="save_as_prefab",
        hierarchy_path=hierarchy_path,
        asset_path=asset_path,
    )
```

---

## 変更ファイル一覧

| ファイル | 変更 |
|---------|------|
| `tools/unity/PrefabSentinel.UnityEditorControlBridge.cs` | `object_reference` フィールド追加、SupportedActions 2件、HandleEditorSetProperty、HandleSaveAsPrefab |
| `prefab_sentinel/mcp_server.py` | `editor_set_property`, `editor_save_as_prefab` ツール追加 |
| `prefab_sentinel/editor_bridge.py` | SUPPORTED_ACTIONS に `editor_set_property`, `save_as_prefab` 追加 |
| `tests/test_editor_bridge.py` | SUPPORTED_ACTIONS テスト更新 |

## スコープ外

- `set_property` (YAML パッチ) の UdonSharp 対応 — Udon VM シリアライズの再実装は不要
- `editor_set_property` の配列要素操作 — 初回は単一プロパティのみ
- Prefab Variant の明示的ベース指定パラメータ — GO の接続状態で自動判定
