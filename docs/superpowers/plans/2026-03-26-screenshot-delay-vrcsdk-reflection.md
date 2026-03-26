# Screenshot Delay + VRCSDK Reflection Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix non-focus screenshot rendering by deferring Scene view capture by 1 frame, and make VRCSDKUploadHandler optional via reflection.

**Architecture:** Two independent C# changes in EditorControlBridge.cs. Screenshot handler is restructured to defer Scene view capture via delayCall, writing the response file directly from the callback. VRCSDK dispatch switches from compile-time #if to runtime reflection.

**Tech Stack:** C# (Unity Editor API)

**Spec:** `docs/superpowers/specs/2026-03-26-screenshot-delay-vrcsdk-reflection-design.md`

---

## File Structure

### Modified Files

| File | Responsibility |
|------|---------------|
| `tools/unity/PrefabSentinel.UnityEditorControlBridge.cs` | Screenshot deferred capture, VRCSDK reflection dispatch |

---

## Task 1: C# — Deferred Scene view screenshot capture

**Files:**
- Modify: `tools/unity/PrefabSentinel.UnityEditorControlBridge.cs:263-376` (RunFromPaths dispatch)
- Modify: `tools/unity/PrefabSentinel.UnityEditorControlBridge.cs:380-506` (HandleCaptureScreenshot)

- [ ] **Step 1: Restructure dispatch to support deferred responses**

In `RunFromPaths` (line 263), the current structure is:
```csharp
EditorControlResponse response;
switch (request.action) { ... }
WriteResponse(responsePath, response);
```

Change the `capture_screenshot` case to handle deferred writing. Replace the entire block from line 287 (`EditorControlResponse response;`) through line 375 (`WriteResponse(responsePath, response);`):

```csharp
            EditorControlResponse response = null;
            bool deferred = false;
            switch (request.action)
            {
                case "capture_screenshot":
                {
                    bool isScene = string.Equals(request.view, "scene", StringComparison.OrdinalIgnoreCase);
                    if (isScene)
                    {
                        // Defer Scene view capture by 1 frame for skinning recalculation
                        EditorApplication.QueuePlayerLoopUpdate();
                        string rp = responsePath;  // capture for closure
                        EditorApplication.delayCall += () =>
                        {
                            var r = HandleCaptureScreenshot(request, requestPath);
                            WriteResponse(rp, r);
                        };
                        deferred = true;
                    }
                    else
                    {
                        response = HandleCaptureScreenshot(request, requestPath);
                    }
                    break;
                }
                case "select_object":
                    response = HandleSelectObject(request);
                    break;
```

Keep all other cases exactly as-is (lines 293-372).

After the switch, replace the single `WriteResponse` call with:

```csharp
            if (!deferred)
                WriteResponse(responsePath, response);
```

- [ ] **Step 2: Remove pre-render setup from HandleCaptureScreenshot**

The Scene view branch in `HandleCaptureScreenshot` (lines 410-414) has pre-render setup that was added in PR #2. Since the 1-frame delay now handles this, remove the redundant lines:

Remove these lines (410-414):
```csharp
                        // Force scene state update before capture (non-focus safe)
                        EditorApplication.QueuePlayerLoopUpdate();
                        bool wasAlwaysRefresh = sceneView.sceneViewState.alwaysRefresh;
                        sceneView.sceneViewState.alwaysRefresh = true;
                        sceneView.Repaint();
```

And remove the restore line (422-423):
```csharp
                        // Restore alwaysRefresh
                        sceneView.sceneViewState.alwaysRefresh = wasAlwaysRefresh;
```

The `HandleCaptureScreenshot` Scene view try block should now start directly with:
```csharp
                    try
                    {
                        rt = new RenderTexture(w, h, 24);
                        RenderTexture prev = cam.targetTexture;
                        cam.targetTexture = rt;
                        cam.Render();
                        cam.targetTexture = prev;

                        RenderTexture.active = rt;
```

- [ ] **Step 3: Commit**

```bash
git add tools/unity/PrefabSentinel.UnityEditorControlBridge.cs
git commit -m "fix(bridge): defer Scene view screenshot by 1 frame for skinning recalculation"
```

---

## Task 2: C# — VRCSDKUploadHandler reflection dispatch

**Files:**
- Modify: `tools/unity/PrefabSentinel.UnityEditorControlBridge.cs:360-367` (vrcsdk_upload case)

- [ ] **Step 1: Replace #if block with reflection call**

Replace lines 360-367:
```csharp
                case "vrcsdk_upload":
#if VRC_SDK_VRCSDK3
                    response = VRCSDKUploadHandler.Handle(request);
#else
                    response = BuildError("VRCSDK_NOT_AVAILABLE",
                        "VRC SDK not found in project. Install VRChat SDK 3.x");
#endif
                    break;
```

With:
```csharp
                case "vrcsdk_upload":
                    response = TryHandleVrcsdkUpload(request);
                    break;
```

- [ ] **Step 2: Add TryHandleVrcsdkUpload method**

Add before the `WriteResponse` helper method (before line 1990):

```csharp
        private static EditorControlResponse TryHandleVrcsdkUpload(EditorControlRequest request)
        {
            var handlerType = System.Type.GetType(
                "PrefabSentinel.VRCSDKUploadHandler, Assembly-CSharp-Editor");
            if (handlerType == null)
                return BuildError("VRCSDK_NOT_AVAILABLE",
                    "VRCSDKUploadHandler not found. Deploy VRCSDKUploadHandler.cs to Assets/Editor/ " +
                    "or VRC SDK is not installed in this project.");
            var method = handlerType.GetMethod("Handle",
                System.Reflection.BindingFlags.Public | System.Reflection.BindingFlags.Static);
            if (method == null)
                return BuildError("VRCSDK_NOT_AVAILABLE",
                    "VRCSDKUploadHandler.Handle method not found. Check VRCSDKUploadHandler.cs version.");
            try
            {
                return (EditorControlResponse)method.Invoke(null, new object[] { request });
            }
            catch (System.Reflection.TargetInvocationException ex)
            {
                var inner = ex.InnerException ?? ex;
                return BuildError("VRCSDK_UPLOAD_FAILED", inner.Message);
            }
        }

```

Note: `TargetInvocationException` wrapping handles runtime errors from the reflected method gracefully.

- [ ] **Step 3: Commit**

```bash
git add tools/unity/PrefabSentinel.UnityEditorControlBridge.cs
git commit -m "fix(bridge): VRCSDK dispatch via reflection, remove compile-time dependency"
```

---

## Task 3: Final verification

- [ ] **Step 1: Lint Python (no changes expected, sanity check)**

Run: `uv run ruff check prefab_sentinel/ tests/ 2>&1 | head -5`
Expected: All checks passed (no Python changes in this PR).

- [ ] **Step 2: Run Python tests**

Run: `uv run python -m unittest tests.test_editor_bridge -v 2>&1 | tail -5`
Expected: all tests PASS (no Python changes).

- [ ] **Step 3: Note for Unity manual verification**

Verify in Unity:
1. **Deferred screenshot**: `set_blend_shape` (e.g. `vrc.v_aa=100`) → `editor_screenshot` with Unity **unfocused** → image should show updated blend shape
2. **VRCSDK reflection**:
   - Without `VRCSDKUploadHandler.cs` in project → `vrcsdk_upload` returns `VRCSDK_NOT_AVAILABLE` with "Deploy VRCSDKUploadHandler.cs" message
   - With `VRCSDKUploadHandler.cs` in project → `vrcsdk_upload` works as before
