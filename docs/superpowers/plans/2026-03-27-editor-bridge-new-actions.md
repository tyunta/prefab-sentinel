# Editor Bridge New Actions Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix screenshot rendering with forceMatrixRecalculationPerRender, add editor_rename / editor_add_component / create_udon_program_asset actions, and fix VRCSDKUploadHandler World SDK compatibility.

**Architecture:** C# EditorControlBridge changes first (screenshot fix, 3 new handlers, request fields), VRCSDKUploadHandler Avatar SDK refactoring, then Python MCP wrappers. Screenshot fix removes the deferred/delayCall structure entirely and returns to synchronous capture.

**Tech Stack:** C# (Unity Editor API, reflection), Python 3.11+ (FastMCP)

**Spec:** `docs/superpowers/specs/2026-03-27-editor-bridge-new-actions-design.md`

---

## File Structure

### Modified Files

| File | Responsibility |
|------|---------------|
| `tools/unity/PrefabSentinel.UnityEditorControlBridge.cs` | Screenshot fix, new_name/component_type fields, 3 new handlers, SupportedActions, dispatch |
| `tools/unity/PrefabSentinel.VRCSDKUploadHandler.cs` | Remove Avatar SDK using statements, reflection for VRCAvatarDescriptor |
| `prefab_sentinel/mcp_server.py` | 3 new MCP tools |
| `prefab_sentinel/editor_bridge.py` | SUPPORTED_ACTIONS update |
| `tests/test_editor_bridge.py` | SUPPORTED_ACTIONS test update |

---

## Task 1: C# — Screenshot forceMatrixRecalculationPerRender (remove deferred)

**Files:**
- Modify: `tools/unity/PrefabSentinel.UnityEditorControlBridge.cs:287-313,392-393`

- [ ] **Step 1: Revert dispatch to synchronous capture**

Replace lines 287-313 (the deferred/delayCall capture_screenshot case and deferred variable):

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
                        // Focus SceneView to ensure rendering pipeline runs even when unfocused,
                        // then defer capture by 1 frame for skinning recalculation
                        SceneView sv = SceneView.lastActiveSceneView;
                        if (sv != null) sv.Focus();
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
```

With simple synchronous dispatch:

```csharp
            EditorControlResponse response;
            switch (request.action)
            {
                case "capture_screenshot":
                    response = HandleCaptureScreenshot(request, requestPath);
                    break;
```

- [ ] **Step 2: Revert conditional WriteResponse**

Find (around line 392):
```csharp
            if (!deferred)
                WriteResponse(responsePath, response);
```

Replace with:
```csharp
            WriteResponse(responsePath, response);
```

- [ ] **Step 3: Add forceMatrixRecalculationPerRender in HandleCaptureScreenshot**

In `HandleCaptureScreenshot` (around line 410), find the Scene view camera render block:

```csharp
                        rt = new RenderTexture(w, h, 24);
                        RenderTexture prev = cam.targetTexture;
                        cam.targetTexture = rt;
                        cam.Render();
                        cam.targetTexture = prev;
```

Replace with:

```csharp
                        // Force skinning recalculation for all SkinnedMeshRenderers
                        // so that blend shape changes are reflected even when Unity is unfocused
                        var smrs = UnityEngine.Object.FindObjectsOfType<SkinnedMeshRenderer>();
                        foreach (var smr in smrs)
                            smr.forceMatrixRecalculationPerRender = true;

                        rt = new RenderTexture(w, h, 24);
                        RenderTexture prev = cam.targetTexture;
                        cam.targetTexture = rt;
                        cam.Render();
                        cam.targetTexture = prev;

                        // Restore to avoid per-frame overhead
                        foreach (var smr in smrs)
                            smr.forceMatrixRecalculationPerRender = false;
```

- [ ] **Step 4: Commit**

```bash
git add tools/unity/PrefabSentinel.UnityEditorControlBridge.cs
git commit -m "fix(bridge): synchronous screenshot with forceMatrixRecalculationPerRender"
```

---

## Task 2: C# — Add request fields + SupportedActions + dispatch for new actions

**Files:**
- Modify: `tools/unity/PrefabSentinel.UnityEditorControlBridge.cs`

- [ ] **Step 1: Add new_name and component_type to EditorControlRequest**

After `menu_path` (line 121), add:

```csharp
            public string menu_path = string.Empty;
            // Phase 4: Rename + AddComponent + Udon
            public string new_name = string.Empty;
            public string component_type = string.Empty;
```

- [ ] **Step 2: Add 3 actions to SupportedActions**

In the SupportedActions HashSet (after line 48 `"find_renderers_by_material"`), add:

```csharp
            // Phase 3: Material reverse lookup
            "find_renderers_by_material",
            // Phase 4: Rename + AddComponent + Udon
            "editor_rename",
            "editor_add_component",
            "create_udon_program_asset",
```

- [ ] **Step 3: Add dispatch cases**

In the switch block, before `case "vrcsdk_upload":`, add:

```csharp
                case "editor_rename":
                    response = HandleEditorRename(request);
                    break;
                case "editor_add_component":
                    response = HandleEditorAddComponent(request);
                    break;
                case "create_udon_program_asset":
                    response = HandleCreateUdonProgramAsset(request);
                    break;
```

- [ ] **Step 4: Commit**

```bash
git add tools/unity/PrefabSentinel.UnityEditorControlBridge.cs
git commit -m "feat(bridge): add request fields, SupportedActions, dispatch for rename/addComponent/udon"
```

---

## Task 3: C# — HandleEditorRename

**Files:**
- Modify: `tools/unity/PrefabSentinel.UnityEditorControlBridge.cs`

- [ ] **Step 1: Add HandleEditorRename method**

Add before `HandleListMenuItems`:

```csharp
        // ── Phase 4: Rename + AddComponent + Udon ──

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

            var resp = BuildSuccess("EDITOR_CTRL_RENAME_OK",
                $"Renamed '{oldName}' to '{request.new_name}'",
                data: new EditorControlData
                {
                    selected_object = request.new_name,
                    executed = true,
                    read_only = false,
                });
            resp.diagnostics = new[] { new EditorControlDiagnostic
            {
                detail = "Runtime modification — save the scene (File > Save) to persist.",
                evidence = "Undo.RecordObject"
            }};
            return resp;
        }
```

- [ ] **Step 2: Commit**

```bash
git add tools/unity/PrefabSentinel.UnityEditorControlBridge.cs
git commit -m "feat(bridge): add editor_rename handler"
```

---

## Task 4: C# — HandleEditorAddComponent + ResolveComponentType

**Files:**
- Modify: `tools/unity/PrefabSentinel.UnityEditorControlBridge.cs`

- [ ] **Step 1: Add ResolveComponentType helper**

Add after HandleEditorRename:

```csharp
        private static System.Type ResolveComponentType(string typeName)
        {
            // 1. Fully qualified name
            var t = System.Type.GetType(typeName);
            if (t != null && typeof(Component).IsAssignableFrom(t))
                return t;

            // 2. Search all loaded assemblies
            foreach (var asm in System.AppDomain.CurrentDomain.GetAssemblies())
            {
                t = asm.GetType(typeName);
                if (t != null && typeof(Component).IsAssignableFrom(t))
                    return t;
            }

            // 3. Try UnityEngine namespace
            t = System.Type.GetType($"UnityEngine.{typeName}, UnityEngine.CoreModule");
            if (t != null && typeof(Component).IsAssignableFrom(t))
                return t;

            return null;
        }
```

- [ ] **Step 2: Add HandleEditorAddComponent**

Add after ResolveComponentType:

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

            System.Type compType = ResolveComponentType(request.component_type);
            if (compType == null)
                return BuildError("EDITOR_CTRL_ADD_COMP_TYPE_NOT_FOUND",
                    $"Component type not found: {request.component_type}. " +
                    "Use fully qualified name (e.g. 'UnityEngine.BoxCollider').");

            var added = Undo.AddComponent(go, compType);
            if (added == null)
                return BuildError("EDITOR_CTRL_ADD_COMP_FAILED",
                    $"Failed to add component: {request.component_type}");

            var resp = BuildSuccess("EDITOR_CTRL_ADD_COMP_OK",
                $"Added {compType.Name} to {request.hierarchy_path}",
                data: new EditorControlData
                {
                    selected_object = go.name,
                    executed = true,
                    read_only = false,
                });
            resp.diagnostics = new[] { new EditorControlDiagnostic
            {
                detail = "Runtime modification — save the scene (File > Save) to persist.",
                evidence = "Undo.AddComponent"
            }};
            return resp;
        }
```

- [ ] **Step 3: Commit**

```bash
git add tools/unity/PrefabSentinel.UnityEditorControlBridge.cs
git commit -m "feat(bridge): add editor_add_component handler with type resolution"
```

---

## Task 5: C# — HandleCreateUdonProgramAsset

**Files:**
- Modify: `tools/unity/PrefabSentinel.UnityEditorControlBridge.cs`

- [ ] **Step 1: Add HandleCreateUdonProgramAsset**

Add after HandleEditorAddComponent:

```csharp
        private static EditorControlResponse HandleCreateUdonProgramAsset(EditorControlRequest request)
        {
            if (string.IsNullOrEmpty(request.asset_path))
                return BuildError("EDITOR_CTRL_UDON_NO_SCRIPT", "asset_path (.cs file) is required.");

            var script = AssetDatabase.LoadAssetAtPath<MonoScript>(request.asset_path);
            if (script == null)
                return BuildError("EDITOR_CTRL_UDON_SCRIPT_NOT_FOUND",
                    $"MonoScript not found: {request.asset_path}");

            // Resolve UdonSharpProgramAsset via reflection
            var assetType = System.Type.GetType(
                "UdonSharp.UdonSharpProgramAsset, UdonSharp.Editor");
            if (assetType == null)
                return BuildError("EDITOR_CTRL_UDON_NOT_AVAILABLE",
                    "UdonSharp.Editor not found. Is UdonSharp installed?");

            var asset = ScriptableObject.CreateInstance(assetType);

            // Set sourceCsScript field
            var field = assetType.GetField("sourceCsScript",
                System.Reflection.BindingFlags.Public | System.Reflection.BindingFlags.NonPublic
                | System.Reflection.BindingFlags.Instance);
            if (field != null)
                field.SetValue(asset, script);

            // Output path: use description field if provided, otherwise derive from .cs path
            string outputPath = string.IsNullOrEmpty(request.description)
                ? request.asset_path.Replace(".cs", ".asset")
                : request.description;

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

- [ ] **Step 2: Commit**

```bash
git add tools/unity/PrefabSentinel.UnityEditorControlBridge.cs
git commit -m "feat(bridge): add create_udon_program_asset handler via reflection"
```

---

## Task 6: C# — VRCSDKUploadHandler Avatar SDK reflection

**Files:**
- Modify: `tools/unity/PrefabSentinel.VRCSDKUploadHandler.cs`

- [ ] **Step 1: Remove Avatar SDK using statements**

Replace lines 1-12:

```csharp
#if VRC_SDK_VRCSDK3
using System;
using System.Diagnostics;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;
using VRC.Core;
using VRC.SDK3.Avatars.Components;
using VRC.SDK3A.Editor;
using VRC.SDKBase;
using VRC.SDKBase.Editor.Api;
using static PrefabSentinel.UnityEditorControlBridge;
```

With:

```csharp
#if VRC_SDK_VRCSDK3
using System;
using System.Diagnostics;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;
using VRC.Core;
using VRC.SDKBase;
using VRC.SDKBase.Editor.Api;
using static PrefabSentinel.UnityEditorControlBridge;
```

(Removed `using VRC.SDK3.Avatars.Components;` and `using VRC.SDK3A.Editor;`)

- [ ] **Step 2: Replace VRCAvatarDescriptor with reflection**

Find the avatar descriptor check (around line 47):
```csharp
var avatarDescriptor = prefab.GetComponent<VRCAvatarDescriptor>();
```

Replace with:
```csharp
                var avatarDescType = System.Type.GetType(
                    "VRC.SDK3.Avatars.Components.VRCAvatarDescriptor, VRC.SDK3A");
                Component avatarDescriptor = null;
                if (avatarDescType != null)
                    avatarDescriptor = prefab.GetComponent(avatarDescType);
```

- [ ] **Step 3: Replace IVRCSdkAvatarBuilderApi with reflection**

Find the avatar builder usage (around line 177):
```csharp
var builder = VRCSdkControlPanel.TryGetBuilder<IVRCSdkAvatarBuilderApi>();
```

Replace with:
```csharp
                    // Resolve IVRCSdkAvatarBuilderApi via reflection
                    var avatarBuilderType = System.Type.GetType(
                        "VRC.SDK3A.Editor.IVRCSdkAvatarBuilderApi, VRC.SDK3A.Editor");
                    if (avatarBuilderType == null)
                        return BuildError("VRCSDK_AVATAR_SDK_NOT_FOUND",
                            "Avatar SDK (VRC.SDK3A.Editor) not installed. Cannot upload avatars from this project.");
                    var tryGetMethod = typeof(VRCSdkControlPanel).GetMethod("TryGetBuilder")
                        ?.MakeGenericMethod(avatarBuilderType);
                    if (tryGetMethod == null)
                        return BuildError("VRCSDK_AVATAR_SDK_NOT_FOUND",
                            "VRCSdkControlPanel.TryGetBuilder not found.");
                    var builder = tryGetMethod.Invoke(null, null);
                    if (builder == null)
                        return BuildError("VRCSDK_BUILDER_INIT_FAIL",
                            "Avatar SDK builder not initialized. Open VRChat SDK panel first.");
```

And update the BuildAndUpload call to use reflection:
```csharp
                    var buildMethod = builder.GetType().GetMethod("BuildAndUpload");
                    if (buildMethod == null)
                        return BuildError("VRCSDK_AVATAR_SDK_NOT_FOUND",
                            "BuildAndUpload method not found on avatar builder.");
                    var task = buildMethod.Invoke(builder, new object[] { prefab, null });
                    task.GetType().GetMethod("GetAwaiter").Invoke(task, null)
                        .GetType().GetMethod("GetResult").Invoke(
                            task.GetType().GetMethod("GetAwaiter").Invoke(task, null), null);
```

**Note:** The reflection chain for `BuildAndUpload(...).GetAwaiter().GetResult()` is complex. A simpler approach: wrap in a `dynamic` call if supported, or use a helper method. Check at implementation time whether `VRCSdkControlPanel` itself needs reflection (it's in `VRC.SDKBase.Editor` which should be available in both Avatar and World projects). If `VRCSdkControlPanel` is available, only the `IVRCSdkAvatarBuilderApi` generic parameter needs reflection.

- [ ] **Step 4: Commit**

```bash
git add tools/unity/PrefabSentinel.VRCSDKUploadHandler.cs
git commit -m "fix(bridge): VRCSDKUploadHandler Avatar SDK references via reflection"
```

---

## Task 7: Python — 3 new MCP tools + SUPPORTED_ACTIONS

**Files:**
- Modify: `prefab_sentinel/mcp_server.py`
- Modify: `prefab_sentinel/editor_bridge.py:35-63`
- Modify: `tests/test_editor_bridge.py`

- [ ] **Step 1: Add 3 new actions to SUPPORTED_ACTIONS**

In `prefab_sentinel/editor_bridge.py`, add to the SUPPORTED_ACTIONS frozenset:

```python
        "find_renderers_by_material",
        # Phase 4: Rename + AddComponent + Udon
        "editor_rename",
        "editor_add_component",
        "create_udon_program_asset",
    }
```

- [ ] **Step 2: Add editor_rename MCP tool**

In `prefab_sentinel/mcp_server.py`, add after `editor_find_renderers_by_material`:

```python
    @server.tool()
    def editor_rename(
        hierarchy_path: str,
        new_name: str,
    ) -> dict[str, Any]:
        """Rename a GameObject in the scene (Undo-able).

        Args:
            hierarchy_path: Hierarchy path to the GameObject.
            new_name: New name for the GameObject.
        """
        return send_action(
            action="editor_rename",
            hierarchy_path=hierarchy_path,
            new_name=new_name,
        )
```

- [ ] **Step 3: Add editor_add_component MCP tool**

```python
    @server.tool()
    def editor_add_component(
        hierarchy_path: str,
        component_type: str,
    ) -> dict[str, Any]:
        """Add a component to a GameObject at runtime (Undo-able).

        Type resolution: tries fully qualified name, then searches all assemblies,
        then tries UnityEngine namespace.

        Args:
            hierarchy_path: Hierarchy path to the target GameObject.
            component_type: Component type name (e.g. "BoxCollider", "UnityEngine.AudioSource",
                "MyNamespace.MyComponent").
        """
        return send_action(
            action="editor_add_component",
            hierarchy_path=hierarchy_path,
            component_type=component_type,
        )
```

- [ ] **Step 4: Add editor_create_udon_program_asset MCP tool**

```python
    @server.tool()
    def editor_create_udon_program_asset(
        script_path: str,
        output_path: str = "",
    ) -> dict[str, Any]:
        """Create an UdonSharpProgramAsset (.asset) for an UdonSharp C# script.

        Requires UdonSharp to be installed in the Unity project.

        Args:
            script_path: Asset path to the .cs file (e.g. "Assets/Scripts/MyBehaviour.cs").
            output_path: Output .asset path. Defaults to same directory as script with .asset extension.
        """
        kwargs: dict[str, Any] = {"asset_path": script_path}
        if output_path:
            kwargs["description"] = output_path  # reuses description field for output path
        return send_action(action="create_udon_program_asset", **kwargs)
```

- [ ] **Step 5: Update SUPPORTED_ACTIONS test**

In `tests/test_editor_bridge.py`, find `TestSupportedActions.test_all_actions_present`. Add the 3 new actions to the `expected` set:

```python
            "find_renderers_by_material",
            "editor_rename",
            "editor_add_component",
            "create_udon_program_asset",
```

- [ ] **Step 6: Run tests + lint**

Run: `uv run python -m unittest tests.test_editor_bridge -v`
Expected: all tests PASS.

Run: `uv run ruff check prefab_sentinel/editor_bridge.py prefab_sentinel/mcp_server.py tests/test_editor_bridge.py`
Expected: All checks passed.

- [ ] **Step 7: Commit**

```bash
git add prefab_sentinel/mcp_server.py prefab_sentinel/editor_bridge.py tests/test_editor_bridge.py
git commit -m "feat(mcp): add editor_rename, editor_add_component, create_udon_program_asset tools"
```
