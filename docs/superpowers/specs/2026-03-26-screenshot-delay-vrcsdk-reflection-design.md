# スクショ遅延キャプチャ + VRCSDKUploadHandler リフレクション化設計

**日付:** 2026-03-26
**由来:** PR #2 バグ修正検証レポート (`report_20260326_session3_bugfix_verification.md`)
**アプローチ:** C# EditorControlBridge の2箇所修正

---

## 概要

PR #2 で導入した ForceRenderAndRepaint では解決しなかった2件を対処する。

---

## 1. スクショの 1 フレーム遅延キャプチャ

### 問題

`QueuePlayerLoopUpdate` は非同期で次の Editor update にスケジュールするのみ。`set_blend_shape` → `editor_screenshot` が別リクエストでも、SkinnedMeshRenderer のスキニング再計算が間に合わずスクショに反映されない。

### 設計

`HandleCaptureScreenshot` の Scene ビュー分岐を `delayCall` で 1 フレーム遅延。

```csharp
case "capture_screenshot":
    if (isSceneView)
    {
        EditorApplication.QueuePlayerLoopUpdate();
        EditorApplication.delayCall += () =>
        {
            var response = DoCaptureScreenshot(request, requestPath);
            WriteResponseAtomic(responsePath, response);
        };
        return; // メインパスではレスポンスを書かない
    }
    else
    {
        response = DoCaptureScreenshot(request, requestPath);
    }
    break;
```

**変更点:**
- `HandleCaptureScreenshot` のキャプチャロジックを `DoCaptureScreenshot` に抽出
- Scene ビューの場合のみ `delayCall` で 1 フレーム遅延
- `delayCall` コールバック内でレスポンスファイルを直接書き込み（`WriteResponseAtomic` を dispatch メソッドから抽出）
- dispatch のメインパスではレスポンス書き込みをスキップ（`return` で抜ける）

**影響:** Python 側は変更なし。ポーリング間隔 1 秒 >> 1 フレーム ~16ms。

---

## 2. VRCSDKUploadHandler リフレクション化

### 問題

`#if VRC_SDK_VRCSDK3` で `VRCSDKUploadHandler.Handle()` を直接呼ぶため、VRC SDK プロジェクトで `.cs` 未配置時にコンパイルエラー。

### 設計

dispatch の `#if` を除去し、リフレクションでランタイム検出。

```csharp
private static EditorControlResponse TryHandleVrcsdkUpload(EditorControlRequest request)
{
    var handlerType = System.Type.GetType(
        "PrefabSentinel.VRCSDKUploadHandler, Assembly-CSharp-Editor");
    if (handlerType == null)
        return BuildError("VRCSDK_NOT_AVAILABLE",
            "VRCSDKUploadHandler not found. Copy VRCSDKUploadHandler.cs to Assets/Editor/.");
    var method = handlerType.GetMethod("Handle",
        System.Reflection.BindingFlags.Public | System.Reflection.BindingFlags.Static);
    if (method == null)
        return BuildError("VRCSDK_NOT_AVAILABLE",
            "VRCSDKUploadHandler.Handle method not found.");
    return (EditorControlResponse)method.Invoke(null, new object[] { request });
}
```

**変更点:**
- dispatch の `#if VRC_SDK_VRCSDK3` ブロックを除去、`TryHandleVrcsdkUpload` に置換
- `VRCSDKUploadHandler.cs` 未配置 → 明確なエラーメッセージ（コンパイルは通る）
- `VRCSDKUploadHandler.cs` 配置済み → 従来通りリフレクション経由で動作
- `VRCSDKUploadHandler.cs` 自体は変更なし

---

## 実装順序

| ステップ | 内容 |
|---------|------|
| 1 | dispatch から WriteResponseAtomic を抽出、DoCaptureScreenshot を抽出 |
| 2 | Scene ビュースクショの delayCall 遅延 |
| 3 | TryHandleVrcsdkUpload リフレクション化、#if 除去 |
| 4 | テスト・lint |

---

## スコープ外

- Game ビューの遅延キャプチャ（Game ビューは通常フォーカス状態で使う）
- ForceRenderAndRepaint の除去（他の視覚更新系で引き続き有用）
