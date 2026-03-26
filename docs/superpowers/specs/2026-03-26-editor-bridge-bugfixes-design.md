# Editor Bridge バグ修正・品質改善設計

**日付:** 2026-03-26
**由来:** v0.5.82 機能テスト＆レビュー (`report_20260326_feature_test_and_review.md`)
**アプローチ:** レイヤー別ボトムアップ（C# → Python → ドキュメント）

---

## 概要

v0.5.82 の実地テストで発見されたバグ 4 件 + 品質改善 2 件を修正する。

---

## 1. ForceRenderAndRepaint（非フォーカス描画対策）

### 問題

`RepaintAllViews` は GUI 再描画のみ。GPU レンダリングパイプライン（SkinnedMesh スキニング、マテリアル再描画）は非フォーカス時にスキップされる。`set_blend_shape` / `editor_set_material_property` 後の `editor_screenshot` で変更前の画像がキャプチャされる。

### 設計

既存の `RepaintAllViews` を `ForceRenderAndRepaint` に置き換える。

```csharp
private static void ForceRenderAndRepaint(SceneView sceneView)
{
    // 1. プレイヤーループ強制キュー（スキニング等の再計算）
    EditorApplication.QueuePlayerLoopUpdate();

    // 2. 一時的に常時リフレッシュ ON（非フォーカスでもレンダリング走行）
    bool wasAlwaysRefresh = sceneView.sceneViewState.alwaysRefresh;
    sceneView.sceneViewState.alwaysRefresh = true;

    // 3. GUI 再描画
    sceneView.Repaint();
    SceneView.RepaintAll();
    UnityEditorInternal.InternalEditorUtility.RepaintAllViews();

    // 4. 1フレーム後に復元
    EditorApplication.delayCall += () =>
    {
        sceneView.sceneViewState.alwaysRefresh = wasAlwaysRefresh;
        sceneView.Repaint();
        SceneView.RepaintAll();
    };
}
```

**適用先:** `set_camera`, `frame_selected`, `set_blend_shape`, `set_material_property`, `set_material` — 既存の `RepaintAllViews` 呼び出しを全て置き換え。

### スクショ時の追加保証

`capture_screenshot` ハンドラの Scene ビューキャプチャ前に `sceneView.camera.Render()` を呼ぶ:

```csharp
// フォーカス不問でレンダリングを強制
sceneView.camera.Render();
```

これにより `ForceRenderAndRepaint` が効かない環境でもスクショ時は確実に最新フレームが得られる。

---

## 2. VRCSDKUploadHandler コンパイルエラー修正

### 問題

`VRCSDKUploadHandler.cs` を配置すると `BuildError`/`BuildSuccess` が `private` でアクセス不可（15件のコンパイルエラー）。Bridge 全体がコンパイル不能になり全エディタ操作が停止。

### 設計

#### アクセス修飾子の修正

`UnityEditorControlBridge.cs` の `BuildError`/`BuildSuccess` を `private` → `internal` に変更:

```csharp
internal static EditorControlResponse BuildError(string code, string message, ...)
internal static EditorControlResponse BuildSuccess(string code, string message, ...)
```

加えて、`#if VRC_SDK_VRCSDK3` ブロック内の VRC SDK API 不整合を全件修正:
- `GetBuildTargetGroup` の廃止対応
- `IVRCSdkWorldBuilderApi` の型不整合
- その他コンパイルエラー

#### ブリッジバージョン検出

`EditorControlBridge` に定数を追加:

```csharp
public const string BridgeVersion = "0.5.82";
```

Bridge レスポンスの既存 `protocol_version` に加え `bridge_version` を返す。Python 側の `get_project_status` で Python パッケージバージョンと Bridge バージョンの不一致を `warning` 診断として出力。

---

## 3. get_unity_symbols の相対パス解決

### 問題

`get_unity_symbols` で `Assets/...` 形式の相対パスが `File not found` エラーになる。他のツール（`inspect_materials` 等）は相対パスで動作するため一貫性がない。

### 設計

`get_unity_symbols` の入力パス処理で、`Assets/...` 形式を検出したら `activate_project` で設定済みの Unity プロジェクトルートを先頭に付加して絶対パスに変換:

```python
if asset_path.startswith("Assets/"):
    project_root = get_active_project_root()
    if project_root:
        asset_path = os.path.join(project_root, asset_path)
```

他のツールで既にこのパターンを使っている箇所を確認し、共通ヘルパーがあればそれを使う。

### 影響範囲

`get_unity_symbols` のみ。他のツールは既に相対パスが動作しているため変更不要。

---

## 4. set_blend_shape の unsaved warning

### 問題

`set_blend_shape` はランタイム変更（`Undo.RecordObject` 対応）だが、シーンを明示的に保存しないと失われる。レスポンスにその旨の警告がない。

### 設計

`set_blend_shape` のレスポンスに `diagnostics` を追加:

```csharp
diagnostics = new[] { new EditorControlDiagnostic
{
    detail = "BlendShape change is a runtime modification. Save the scene (File > Save) to persist.",
    evidence = "Undo.RecordObject"
} }
```

同様の warning を以下にも追加:
- `editor_set_material_property`（マテリアルのランタイム変更）
- `editor_set_material`（マテリアルスロットのランタイム差し替え）

---

## 5. マテリアル GUID エラーメッセージ改善

### 問題

`editor_set_material_property` でテクスチャ GUID 指定時にマテリアル GUID を誤って渡すと「Failed to load texture at: .mat path」という不明瞭なエラーが出る。

### 設計

`HandleSetMaterialProperty` のテクスチャロード失敗時に、渡された GUID が `.mat` アセットを指しているかチェック:

```csharp
Texture tex = AssetDatabase.LoadAssetAtPath<Texture>(path);
if (tex == null)
{
    string assetPath = AssetDatabase.GUIDToAssetPath(guid);
    if (assetPath.EndsWith(".mat"))
        return BuildError("EDITOR_CTRL_SET_MAT_PROP_WRONG_GUID",
            $"The specified GUID points to a material asset '{assetPath}'. " +
            "Please specify a texture GUID instead.");
    return BuildError("EDITOR_CTRL_SET_MAT_PROP_TEXTURE_LOAD_FAIL",
        $"Failed to load texture from GUID '{guid}' (resolved to '{assetPath}').");
}
```

---

## 実装順序

レイヤー別ボトムアップ:

| ステップ | 層 | 内容 |
|---------|-----|------|
| 1 | C# | ForceRenderAndRepaint 置換、Camera.Render スクショ強化、VRCSDKUploadHandler 修正、BridgeVersion 定数、unsaved warning、GUID エラー改善 |
| 2 | Python | get_unity_symbols パス解決、get_project_status バージョンチェック |
| 3 | テスト | Python パス解決のユニットテスト、バージョンチェックテスト |

---

## スコープ外

- Win32 フォーカス強制（ForceRenderAndRepaint で不十分な場合に次バッチで検討）
- Bridge C# の条件付き配布（VRC SDK 有無に応じたファイル分割）
- VRCSDKUploadHandler の partial class 化（internal 化で十分なら不要）
