# Editor Bridge 新アクション + 修正設計

**日付:** 2026-03-27
**由来:** DualButtonSwitcher 作成レポート + Udon Program Asset 要件レポート + session3 検証レポート
**アプローチ:** C# EditorControlBridge + VRCSDKUploadHandler 修正 → Python MCP ラッパー

---

## 概要

5件: isFocused スクショ分岐、editor_rename、editor_create_udon_program_asset、editor_add_component、vrcsdk_upload World SDK 対応。

---

## 1. 同期スクショ + forceMatrixRecalculationPerRender

### 問題

`delayCall` による 1 フレーム遅延キャプチャは、非フォーカス時に delayCall が発火せずタイムアウト（100% 再現）。`SceneView.Focus()` も OS レベルのフォーカスを移さないため無効。根本原因: `Camera.Render()` 時に SkinnedMeshRenderer のスキニングが再計算されない。

### 設計

delayCall/deferred 構造を完全除去し、同期キャプチャに戻す。`Camera.Render()` 前に `SkinnedMeshRenderer.forceMatrixRecalculationPerRender = true` を設定し、スキニング再計算を強制する。

**dispatch:**
```csharp
case "capture_screenshot":
    response = HandleCaptureScreenshot(request, requestPath);
    break;
```

`deferred` 変数、`SceneView.Focus()`、`delayCall` ラムダを全て除去。

**HandleCaptureScreenshot の Scene view キャプチャ前:**
```csharp
// Force skinning recalculation for all SkinnedMeshRenderers
var smrs = UnityEngine.Object.FindObjectsOfType<SkinnedMeshRenderer>();
foreach (var smr in smrs)
    smr.forceMatrixRecalculationPerRender = true;

cam.Render();

foreach (var smr in smrs)
    smr.forceMatrixRecalculationPerRender = false;
```

**メリット:**
- 同期実行（delayCall 不要、レグレッションの根本原因を除去）
- フォーカス状態に依存しない（Win32 不要）
- Unity 公式 API のみ（全プラットフォーム対応）

---

## 2. editor_rename

### 設計

**C# (EditorControlRequest):** `new_name` フィールド追加（`string`, デフォルト空）。

**C# (SupportedActions):** `"editor_rename"` を追加。

**C# (HandleEditorRename):**

```csharp
private static EditorControlResponse HandleEditorRename(EditorControlRequest request)
{
    if (string.IsNullOrEmpty(request.hierarchy_path))
        return BuildError("EDITOR_CTRL_RENAME_NO_PATH", "hierarchy_path is required.");
    if (string.IsNullOrEmpty(request.new_name))
        return BuildError("EDITOR_CTRL_RENAME_NO_NAME", "new_name is required.");

    var go = GameObject.Find(request.hierarchy_path);
    if (go == null)
        return BuildError("EDITOR_CTRL_RENAME_NOT_FOUND",
            $"GameObject not found: {request.hierarchy_path}");

    string oldName = go.name;
    Undo.RecordObject(go, $"PrefabSentinel: Rename {oldName}");
    go.name = request.new_name;

    return BuildSuccess("EDITOR_CTRL_RENAME_OK",
        $"Renamed '{oldName}' to '{request.new_name}'",
        data: new EditorControlData { executed = true, read_only = false });
}
```

**Python:** `editor_rename(hierarchy_path: str, new_name: str)` MCP ツール追加。

---

## 3. editor_create_udon_program_asset

### 設計

リフレクション経由で `UdonSharpProgramAsset` を生成。

**C# (SupportedActions):** `"create_udon_program_asset"` を追加。

**C# (HandleCreateUdonProgramAsset):**

```csharp
private static EditorControlResponse HandleCreateUdonProgramAsset(EditorControlRequest request)
{
    if (string.IsNullOrEmpty(request.asset_path))
        return BuildError("EDITOR_CTRL_UDON_NO_SCRIPT", "asset_path (.cs) is required.");

    var script = AssetDatabase.LoadAssetAtPath<MonoScript>(request.asset_path);
    if (script == null)
        return BuildError("EDITOR_CTRL_UDON_SCRIPT_NOT_FOUND",
            $"MonoScript not found: {request.asset_path}");

    // UdonSharpProgramAsset をリフレクションで生成
    var assetType = System.Type.GetType(
        "UdonSharp.UdonSharpProgramAsset, UdonSharp.Editor");
    if (assetType == null)
        return BuildError("EDITOR_CTRL_UDON_NOT_AVAILABLE",
            "UdonSharp.Editor not found. Is UdonSharp installed?");

    var asset = ScriptableObject.CreateInstance(assetType);

    // sourceCsScript を設定
    var field = assetType.GetField("sourceCsScript",
        System.Reflection.BindingFlags.Public | System.Reflection.BindingFlags.NonPublic
        | System.Reflection.BindingFlags.Instance);
    if (field != null)
        field.SetValue(asset, script);

    // 出力パス（デフォルト: .cs と同じディレクトリに .asset）
    string outputPath = string.IsNullOrEmpty(request.description)
        ? request.asset_path.Replace(".cs", ".asset")
        : request.description;  // description フィールドを output_path として流用

    AssetDatabase.CreateAsset(asset, outputPath);
    AssetDatabase.SaveAssets();

    return BuildSuccess("EDITOR_CTRL_UDON_ASSET_CREATED",
        $"Created Udon Program Asset: {outputPath}",
        data: new EditorControlData
        {
            output_path = outputPath,
            asset_path = request.asset_path,
            executed = true,
        });
}
```

**注意:** `description` フィールドを `output_path` として流用（新フィールド追加を避ける）。Python 側で `output_path` パラメータ名にマッピング。

**Python:** `editor_create_udon_program_asset(script_path: str, output_path: str = "")` MCP ツール追加。

---

## 4. editor_add_component

### 設計

**新規 Bridge アクション** として実装（patch_apply の create-mode `add_component` とは独立）。

**C# (SupportedActions):** `"editor_add_component"` を追加。

**C# (EditorControlRequest):** `component_type` フィールド追加（`string`, デフォルト空）。

**C# (HandleEditorAddComponent):**

```csharp
private static EditorControlResponse HandleEditorAddComponent(EditorControlRequest request)
{
    if (string.IsNullOrEmpty(request.hierarchy_path))
        return BuildError("EDITOR_CTRL_ADD_COMP_NO_PATH", "hierarchy_path is required.");
    if (string.IsNullOrEmpty(request.component_type))
        return BuildError("EDITOR_CTRL_ADD_COMP_NO_TYPE", "component_type is required.");

    var go = GameObject.Find(request.hierarchy_path);
    if (go == null)
        return BuildError("EDITOR_CTRL_ADD_COMP_NOT_FOUND",
            $"GameObject not found: {request.hierarchy_path}");

    // 型を解決（Assembly-CSharp, UnityEngine 等から検索）
    System.Type compType = ResolveComponentType(request.component_type);
    if (compType == null)
        return BuildError("EDITOR_CTRL_ADD_COMP_TYPE_NOT_FOUND",
            $"Component type not found: {request.component_type}");

    Undo.AddComponent(go, compType);
    var added = go.GetComponent(compType);
    if (added == null)
        return BuildError("EDITOR_CTRL_ADD_COMP_FAILED",
            $"Failed to add component: {request.component_type}");

    return BuildSuccess("EDITOR_CTRL_ADD_COMP_OK",
        $"Added {request.component_type} to {request.hierarchy_path}",
        data: new EditorControlData
        {
            selected_object = go.name,
            executed = true,
            read_only = false,
        });
}

private static System.Type ResolveComponentType(string typeName)
{
    // 1. 完全修飾名で直接検索
    var t = System.Type.GetType(typeName);
    if (t != null) return t;

    // 2. 全アセンブリから検索
    foreach (var asm in System.AppDomain.CurrentDomain.GetAssemblies())
    {
        t = asm.GetType(typeName);
        if (t != null && typeof(Component).IsAssignableFrom(t))
            return t;
    }

    // 3. UnityEngine 名前空間を試行
    t = System.Type.GetType($"UnityEngine.{typeName}, UnityEngine.CoreModule");
    if (t != null) return t;

    return null;
}
```

**Python:** `editor_add_component(hierarchy_path: str, component_type: str)` MCP ツール追加。

---

## 5. vrcsdk_upload World SDK 対応

### 問題

`VRCSDKUploadHandler.cs` が `using VRC.SDK3.Avatars.Components` をトップレベルで宣言。World 専用プロジェクトで `VRC.SDK3.Avatars` が存在せずコンパイルエラー。

### 設計

Avatar SDK 固有の型参照をリフレクション化:

```csharp
// Before
using VRC.SDK3.Avatars.Components;
var descriptor = prefab.GetComponent<VRCAvatarDescriptor>();

// After
var descType = System.Type.GetType(
    "VRC.SDK3.Avatars.Components.VRCAvatarDescriptor, VRC.SDK3A");
if (descType == null)
    return BuildError("VRCSDK_AVATAR_SDK_NOT_FOUND",
        "Avatar SDK (VRC.SDK3A) not installed. Cannot upload avatars from this project.");
var descriptor = prefab.GetComponent(descType);
```

`using VRC.SDK3.Avatars.Components` と `using VRC.SDK3A.Editor` を除去。Avatar 関連の参照は全てリフレクション経由。

World 側の `VRC_SceneDescriptor`（`VRC.SDKBase` に含まれる）と `IVRCSdkWorldBuilderApi`（`VRC.SDK3.Editor` に含まれる）はリフレクション不要（World SDK は必ず存在する前提）。ただし安全のため `IVRCSdkWorldBuilderApi` もリフレクション化を検討。

---

## 実装順序

| ステップ | 内容 |
|---------|------|
| 1 | C#: isFocused 分岐（dispatch 修正） |
| 2 | C#: editor_rename + editor_add_component + create_udon_program_asset（新ハンドラ + SupportedActions + dispatch） |
| 3 | C#: VRCSDKUploadHandler Avatar SDK リフレクション化 |
| 4 | Python: 3 新規 MCP ツール + SUPPORTED_ACTIONS |
| 5 | テスト + lint |

---

## スコープ外

- patch_apply の open-mode add_component 対応（アーキテクチャ変更が大きい、editor_add_component で代替可能）
- UdonSharp backing 自動配線（editor_create_udon_program_asset + editor_add_component の2ステップで手動配線）
- Win32 フォーカス強制（isFocused 分岐で非フォーカス時は同期キャプチャにフォールバック）
