# Editor Bridge パス指定パラメータ + マテリアル逆引き設計

**日付:** 2026-03-26
**由来:** v0.5.82 セッション2 追加レポート (`report_20260326_session2_findings.md`)
**アプローチ:** レイヤー別ボトムアップ（C# → Python）

---

## 概要

GUID 手動取得の手間を省くため、既存ツールにパス指定パラメータを追加し、マテリアル逆引きの新規ツールを追加する。3件。

---

## 1. editor_set_material に material_path パラメータ追加

### 問題

`editor_set_material` は `material_guid` のみ受け付ける。マテリアル差し替えのたびに `.meta` ファイルから GUID を grep する必要があり、操作フローが途切れる。

### 設計

**C# (EditorControlRequest):**

`material_path` フィールドを追加（`string`, デフォルト空）。

**C# (HandleSetMaterial):**

マテリアルロード前に `material_path` → GUID 変換を追加:

```csharp
string guid = request.material_guid;
if (!string.IsNullOrEmpty(request.material_path))
{
    if (!string.IsNullOrEmpty(guid))
        return BuildError("EDITOR_CTRL_SET_MATERIAL_CONFLICT",
            "Cannot specify both material_guid and material_path. Use one.");
    guid = AssetDatabase.AssetPathToGUID(request.material_path);
    if (string.IsNullOrEmpty(guid))
        return BuildError("EDITOR_CTRL_SET_MATERIAL_NOT_FOUND",
            $"Material not found at path: {request.material_path}");
}
```

以降は既存の GUID ベース処理がそのまま動く。

**Python (editor_set_material):**

`material_path: str = ""` パラメータを追加。C# へ `material_path` フィールドとしてそのまま渡す。

---

## 2. editor_set_material_property テクスチャに path: プレフィックス対応

### 問題

テクスチャ値は `guid:xxx` 形式のみ。パスで指定したい場合も GUID を事前に取得する必要がある。

### 設計

**C# (HandleSetMaterialProperty):**

テクスチャ分岐に `path:` プレフィックスを追加:

```csharp
case ShaderPropertyType.Texture:
{
    if (string.IsNullOrEmpty(val))
    {
        mat.SetTexture(request.property_name, null);
    }
    else if (val.StartsWith("guid:"))
    {
        // 既存の GUID ベース処理（変更なし）
    }
    else if (val.StartsWith("path:"))
    {
        string texPath = val.Substring(5);
        var tex = AssetDatabase.LoadAssetAtPath<Texture>(texPath);
        if (tex == null)
            return BuildError("EDITOR_CTRL_PROPERTY_TYPE_MISMATCH",
                $"Texture not found at path: {texPath}");
        mat.SetTexture(request.property_name, tex);
    }
    else
    {
        return BuildError("EDITOR_CTRL_PROPERTY_TYPE_MISMATCH",
            "Texture value must be 'guid:<hex>', 'path:<asset_path>', or empty string for null.");
    }
    break;
}
```

**Python:** 変更なし。docstring に `path:` 形式を追記。

---

## 3. editor_find_renderers_by_material 新規ツール

### 問題

マテリアルを使用しているレンダラーを逆引きする手段がない。`editor_list_materials` を個別に叩いて手動調査するしかない。

### 設計

**C# (SupportedActions):**

`find_renderers_by_material` を追加。

**C# (EditorControlRequest):**

既存の `material_guid` と `material_path` フィールドを共用。

**C# (HandleFindRenderersByMaterial):**

```csharp
private static EditorControlResponse HandleFindRenderersByMaterial(EditorControlRequest request)
{
    // GUID 解決（material_guid or material_path）
    string guid = request.material_guid;
    if (!string.IsNullOrEmpty(request.material_path))
    {
        guid = AssetDatabase.AssetPathToGUID(request.material_path);
        if (string.IsNullOrEmpty(guid))
            return BuildError("EDITOR_CTRL_MATERIAL_NOT_FOUND",
                $"Material not found at path: {request.material_path}");
    }
    if (string.IsNullOrEmpty(guid))
        return BuildError("EDITOR_CTRL_MATERIAL_NOT_FOUND",
            "material_guid or material_path is required.");

    string targetPath = AssetDatabase.GUIDToAssetPath(guid);

    // 全レンダラーを走査
    var renderers = Object.FindObjectsOfType<Renderer>();
    var matches = new List<MaterialSlotEntry>();
    foreach (var renderer in renderers)
    {
        var mats = renderer.sharedMaterials;
        for (int i = 0; i < mats.Length; i++)
        {
            if (mats[i] == null) continue;
            string matPath = AssetDatabase.GetAssetPath(mats[i]);
            if (matPath == targetPath)
            {
                string rendererPath = GetHierarchyPath(renderer.transform);
                matches.Add(new MaterialSlotEntry
                {
                    renderer_path = rendererPath,
                    renderer_type = renderer.GetType().Name,
                    slot_index = i,
                    material_name = mats[i].name,
                    material_asset_path = matPath,
                    material_guid = guid,
                });
            }
        }
    }

    return BuildSuccess("EDITOR_CTRL_FIND_RENDERERS_OK",
        $"Found {matches.Count} slot(s) using material across {renderers.Length} renderers",
        data: new EditorControlData
        {
            material_slots = matches.ToArray(),
            total_entries = renderers.Length,
            executed = true,
        });
}
```

`GetHierarchyPath` は既存ヘルパー（Transform → "/" 区切りパス）。`MaterialSlotEntry` は既存の構造体を再利用。

**Python (editor_find_renderers_by_material):**

```python
@server.tool()
def editor_find_renderers_by_material(
    material_guid: str = "",
    material_path: str = "",
) -> dict[str, Any]:
    """Find all renderers using a specific material in the current scene.

    Returns renderer paths and slot indices. Specify either material_guid
    or material_path (not both).
    """
    kwargs = {}
    if material_guid:
        kwargs["material_guid"] = material_guid
    if material_path:
        kwargs["material_path"] = material_path
    return send_action(action="find_renderers_by_material", **kwargs)
```

---

## 実装順序

| ステップ | 層 | 内容 |
|---------|-----|------|
| 1 | C# | material_path フィールド追加、HandleSetMaterial パス→GUID 変換、テクスチャ path: プレフィックス、HandleFindRenderersByMaterial 新設 |
| 2 | Python | editor_set_material に material_path パラメータ、editor_find_renderers_by_material 新設、docstring 更新 |
| 3 | テスト | Python パラメータ変換テスト |

---

## スコープ外

- editor_set_material_property の material_path 指定（対象マテリアルの指定は既に hierarchy_path + material_index で行っている）
- 非フォーカス描画問題（PR #2 で ForceRenderAndRepaint 導入済み、Unity テスト待ち）
