# VRCSDK Upload Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add VRC SDK build + upload capability via MCP, enabling avatar/world update workflows through the Editor Bridge.

**Architecture:** New `VRCSDKUploadHandler.cs` file handles VRC SDK interaction, wrapped in `#if VRC_SDK_VRCSDK3`. Bridge dispatch in existing `UnityEditorControlBridge.cs` routes to the handler. Python MCP tool is a thin wrapper delegating to `send_action()` with extended timeout.

**Tech Stack:** Unity C# (VRC SDK 3.x), Python 3.11+, MCP (FastMCP), unittest

**Spec:** `docs/superpowers/specs/2026-03-25-vrcsdk-upload-design.md`

---

## File Structure

| Action | Path | Responsibility |
|--------|------|----------------|
| Modify | `tools/unity/PrefabSentinel.UnityEditorControlBridge.cs:22-42,47-98,147-172,213-274` | DTO fields + dispatch case + `SupportedActions` |
| Create | `tools/unity/PrefabSentinel.VRCSDKUploadHandler.cs` | VRC SDK validation, build, upload logic (`#if VRC_SDK_VRCSDK3` guarded) |
| Modify | `prefab_sentinel/editor_bridge.py:33-54` | Add `"vrcsdk_upload"` to `SUPPORTED_ACTIONS` |
| Modify | `prefab_sentinel/mcp_server.py` | Register `vrcsdk_upload` MCP tool |
| Modify | `tests/test_mcp_server.py:42-68` | Tool registration + count + delegation test |
| Modify | `tests/test_editor_bridge.py:167-188` | SUPPORTED_ACTIONS test |
| Modify | `README.md` | Add `vrcsdk_upload` to MCP tools table |

---

## Task 1: C# DTO Fields + Dispatch Case

**Files:**
- Modify: `tools/unity/PrefabSentinel.UnityEditorControlBridge.cs`

- [ ] **Step 1: Add `"vrcsdk_upload"` to SupportedActions (line 41)**

After `"run_integration_tests",` add:
```csharp
            "vrcsdk_upload",
```

- [ ] **Step 2: Add request DTO fields (after line 97)**

After `public string property_value = string.Empty;` add:
```csharp
            // vrcsdk_upload
            public string target_type = string.Empty;    // "avatar" or "world"
            public string blueprint_id = string.Empty;    // existing VRC asset ID
            public string description = string.Empty;     // empty = no change
            public string tags = string.Empty;            // JSON array string, empty = no change
            public string release_status = string.Empty;  // "public" | "private", empty = no change
            public bool confirm = false;                  // dry-run gate
```

Note: `asset_path` already exists on the DTO (used by `instantiate_to_scene`).

- [ ] **Step 3: Add response DTO fields (after line 171)**

After `public bool executed = false;` add:
```csharp
            // vrcsdk_upload response
            public string target_type = string.Empty;
            public string asset_path = string.Empty;
            public string blueprint_id = string.Empty;
            public string phase = string.Empty;           // "validated" or "complete"
            public float elapsed_sec = 0f;
```

Note: `EditorControlRequest` and `EditorControlData` are separate classes, so field names don't conflict. Verify by reading the file that `EditorControlData` has no existing `asset_path` field (it doesn't — only `EditorControlRequest` has one).

- [ ] **Step 4: Add dispatch case (after `run_integration_tests` case, line 268)**

After `case "run_integration_tests":` block, before the `default:` case, add:
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

- [ ] **Step 5: Commit**

```bash
git add tools/unity/PrefabSentinel.UnityEditorControlBridge.cs
git commit -m "feat(bridge): add vrcsdk_upload DTO fields and dispatch case"
```

---

## Task 2: C# `VRCSDKUploadHandler.cs`

**Files:**
- Create: `tools/unity/PrefabSentinel.VRCSDKUploadHandler.cs`

- [ ] **Step 1: Create the handler file**

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

namespace PrefabSentinel
{
    /// <summary>
    /// Handles VRC SDK build + upload operations via the Editor Bridge.
    /// Entry point: <see cref="Handle"/> called from UnityEditorControlBridge dispatch.
    /// </summary>
    public static class VRCSDKUploadHandler
    {
        public static EditorControlResponse Handle(EditorControlRequest request)
        {
            // --- Input validation ---
            if (string.IsNullOrEmpty(request.target_type))
                return BuildError("VRCSDK_INVALID_TARGET_TYPE", "target_type is required ('avatar' or 'world').");
            if (request.target_type != "avatar" && request.target_type != "world")
                return BuildError("VRCSDK_INVALID_TARGET_TYPE",
                    $"target_type must be 'avatar' or 'world', got '{request.target_type}'.");
            if (string.IsNullOrEmpty(request.asset_path))
                return BuildError("VRCSDK_ASSET_NOT_FOUND", "asset_path is required.");
            if (string.IsNullOrEmpty(request.blueprint_id))
                return BuildError("VRCSDK_MISSING_BLUEPRINT_ID", "blueprint_id is required (existing asset update only).");

            // --- Login check ---
            if (!APIUser.IsLoggedIn)
                return BuildError("VRCSDK_NOT_LOGGED_IN",
                    "VRC SDK not logged in. Log in via VRChat SDK control panel.");

            // --- Asset validation ---
            if (request.target_type == "avatar")
            {
                var prefab = AssetDatabase.LoadAssetAtPath<GameObject>(request.asset_path);
                if (prefab == null)
                    return BuildError("VRCSDK_ASSET_NOT_FOUND",
                        $"Asset not found or not a GameObject: {request.asset_path}");
                if (prefab.GetComponent<VRCAvatarDescriptor>() == null)
                    return BuildError("VRCSDK_MISSING_DESCRIPTOR",
                        $"No VRCAvatarDescriptor found on: {request.asset_path}");
            }
            else // world
            {
                var scene = EditorSceneManager.OpenScene(request.asset_path, OpenSceneMode.Additive);
                if (!scene.IsValid())
                    return BuildError("VRCSDK_ASSET_NOT_FOUND",
                        $"Scene not found or invalid: {request.asset_path}");
                var descriptor = UnityEngine.Object.FindObjectOfType<VRC_SceneDescriptor>();
                if (descriptor == null)
                {
                    EditorSceneManager.CloseScene(scene, true);
                    return BuildError("VRCSDK_MISSING_DESCRIPTOR",
                        $"No VRC_SceneDescriptor found in scene: {request.asset_path}");
                }
            }

            // --- dry-run return ---
            if (!request.confirm)
            {
                return BuildSuccess("VRCSDK_VALIDATED",
                    $"Validation passed for {request.target_type} at {request.asset_path}",
                    data: new EditorControlData
                    {
                        target_type = request.target_type,
                        asset_path = request.asset_path,
                        blueprint_id = request.blueprint_id,
                        phase = "validated",
                        elapsed_sec = 0f,
                        executed = false
                    });
            }

            // --- Build + Upload ---
            var sw = Stopwatch.StartNew();
            try
            {
                if (request.target_type == "avatar")
                {
                    BuildAndUploadAvatar(request);
                }
                else
                {
                    BuildAndUploadWorld(request);
                }
            }
            catch (Exception ex)
            {
                sw.Stop();
                // Distinguish build vs upload errors by message content
                string code = ex.Message.Contains("upload", StringComparison.OrdinalIgnoreCase)
                    ? "VRCSDK_UPLOAD_FAILED"
                    : "VRCSDK_BUILD_FAILED";
                return BuildError(code, $"{request.target_type} failed after {sw.Elapsed.TotalSeconds:F1}s: {ex.Message}");
            }
            sw.Stop();

            return BuildSuccess("VRCSDK_UPLOAD_OK",
                $"Uploaded {request.target_type} ({request.blueprint_id}) in {sw.Elapsed.TotalSeconds:F1}s",
                data: new EditorControlData
                {
                    target_type = request.target_type,
                    asset_path = request.asset_path,
                    blueprint_id = request.blueprint_id,
                    phase = "complete",
                    elapsed_sec = (float)sw.Elapsed.TotalSeconds,
                    executed = true
                });
        }

        private static void BuildAndUploadAvatar(EditorControlRequest request)
        {
            if (!VRCSdkControlPanel.TryGetBuilder<IVRCSdkAvatarBuilderApi>(out var builder))
                throw new InvalidOperationException("Failed to get IVRCSdkAvatarBuilderApi. Is VRC SDK properly installed?");

            var prefab = AssetDatabase.LoadAssetAtPath<GameObject>(request.asset_path);

            // Set blueprint ID on the descriptor
            var descriptor = prefab.GetComponent<VRCAvatarDescriptor>();
            var pipelineManager = prefab.GetComponent<PipelineManager>();
            if (pipelineManager != null)
                pipelineManager.blueprintId = request.blueprint_id;

            builder.BuildAndUpload(prefab, null).GetAwaiter().GetResult();
        }

        private static void BuildAndUploadWorld(EditorControlRequest request)
        {
            if (!VRCSdkControlPanel.TryGetBuilder<IVRCSdkWorldBuilderApi>(out var builder))
                throw new InvalidOperationException("Failed to get IVRCSdkWorldBuilderApi. Is VRC SDK properly installed?");

            // World build uses the currently open scene
            var scene = EditorSceneManager.OpenScene(request.asset_path, OpenSceneMode.Single);
            if (!scene.IsValid())
                throw new InvalidOperationException($"Failed to open scene: {request.asset_path}");

            var descriptor = UnityEngine.Object.FindObjectOfType<VRC_SceneDescriptor>();
            var pipelineManager = descriptor.GetComponent<PipelineManager>();
            if (pipelineManager != null)
                pipelineManager.blueprintId = request.blueprint_id;

            builder.BuildAndUpload(descriptor.gameObject, null).GetAwaiter().GetResult();
        }
    }
}
#endif
```

**Important notes for implementer:**
- The VRC SDK API (`IVRCSdkAvatarBuilderApi.BuildAndUpload`) may have a different signature depending on SDK version. Check the actual SDK source at implementation time. The `using` directives may also need adjustment.
- `VRCSdkControlPanel.TryGetBuilder<T>()` is the SDK 3.x pattern for obtaining builder instances.
- `PipelineManager.blueprintId` is how VRC SDK associates a prefab/scene with a VRC asset.
- If `GetAwaiter().GetResult()` deadlocks, replace with `EditorApplication.delayCall` + polling (see spec deadlock contingency).

- [ ] **Step 2: Verify it compiles (in SDK-present project)**

This step can only be done in a Unity project with VRC SDK installed. Skip in CI.

- [ ] **Step 3: Commit**

```bash
git add tools/unity/PrefabSentinel.VRCSDKUploadHandler.cs
git commit -m "feat(bridge): add VRCSDKUploadHandler for VRC SDK build + upload"
```

---

## Task 3: Python Bridge + MCP Tool + Tests

**Files:**
- Modify: `prefab_sentinel/editor_bridge.py:33-54`
- Modify: `prefab_sentinel/mcp_server.py`
- Modify: `tests/test_mcp_server.py:42-68`
- Modify: `tests/test_editor_bridge.py:167-188`

- [ ] **Step 1: Add `"vrcsdk_upload"` to SUPPORTED_ACTIONS in `editor_bridge.py`**

After `"run_integration_tests",` (line 52), add:
```python
        "vrcsdk_upload",
```

- [ ] **Step 2: Add `vrcsdk_upload` MCP tool in `mcp_server.py`**

After the `editor_run_tests` tool (around line 1036), add:

```python
    @server.tool()
    def vrcsdk_upload(
        target_type: str,
        asset_path: str,
        blueprint_id: str,
        description: str = "",
        tags: str = "",
        release_status: str = "",
        confirm: bool = False,
        change_reason: str = "",
        timeout_sec: int = 600,
    ) -> dict[str, Any]:
        """Build and upload an avatar or world to VRChat via VRC SDK.

        Existing asset update only (blueprint_id required).

        Two-phase workflow:
        - confirm=False (default): validates SDK login, asset, descriptor.
        - confirm=True: builds and uploads to VRChat.

        Args:
            target_type: "avatar" or "world".
            asset_path: Prefab path (avatar) or Scene path (world).
            blueprint_id: Existing VRC asset ID (e.g. "avtr_xxx..."). Required.
            description: Description text (empty = no change).
            tags: JSON array of tag strings (empty = no change).
            release_status: "public" or "private" (empty = no change).
            confirm: Set True to build + upload (False = validation only).
            change_reason: Required when confirm=True. Audit log reason.
            timeout_sec: Bridge timeout in seconds (default: 600).
        """
        if confirm and not change_reason:
            return {
                "success": False,
                "severity": "error",
                "code": "VRCSDK_REASON_REQUIRED",
                "message": "change_reason is required when confirm=True",
                "data": {},
                "diagnostics": [],
            }
        return send_action(
            action="vrcsdk_upload",
            timeout_sec=timeout_sec,
            target_type=target_type,
            asset_path=asset_path,
            blueprint_id=blueprint_id,
            description=description,
            tags=tags,
            release_status=release_status,
            confirm=confirm,
        )
```

- [ ] **Step 3: Update tool registration test in `tests/test_mcp_server.py`**

Add `"vrcsdk_upload"` to the expected tools set (after `"validate_structure"`).

Update `test_tool_count`: change `40` to `41`.

- [ ] **Step 4: Add delegation test in `tests/test_mcp_server.py`**

Add to the existing delegation test class:

```python
    def test_vrcsdk_upload_delegates(self) -> None:
        server = create_server()
        with patch("prefab_sentinel.mcp_server.send_action", return_value={"success": True}) as mock_send:
            _run(server.call_tool("vrcsdk_upload", {
                "target_type": "avatar",
                "asset_path": "Assets/Avatars/Test.prefab",
                "blueprint_id": "avtr_test123",
                "confirm": False,
            }))
        mock_send.assert_called_once_with(
            action="vrcsdk_upload",
            timeout_sec=600,
            target_type="avatar",
            asset_path="Assets/Avatars/Test.prefab",
            blueprint_id="avtr_test123",
            description="",
            tags="",
            release_status="",
            confirm=False,
        )

    def test_vrcsdk_upload_requires_change_reason(self) -> None:
        """confirm=True without change_reason returns error without calling bridge."""
        server = create_server()
        with patch("prefab_sentinel.mcp_server.send_action") as mock_send:
            _, result = _run(server.call_tool("vrcsdk_upload", {
                "target_type": "avatar",
                "asset_path": "Assets/Avatars/Test.prefab",
                "blueprint_id": "avtr_test123",
                "confirm": True,
                "change_reason": "",
            }))
            mock_send.assert_not_called()
        self.assertFalse(result["success"])
        self.assertEqual("VRCSDK_REASON_REQUIRED", result["code"])
```

- [ ] **Step 5: Update SUPPORTED_ACTIONS test in `tests/test_editor_bridge.py`**

Add `"vrcsdk_upload"` to the expected set in `TestSupportedActions.test_all_actions_present`.

- [ ] **Step 6: Run tests**

```bash
cd /mnt/d/git/prefab-sentinel && uv run --extra test python -m unittest tests.test_mcp_server.TestToolRegistration tests.test_editor_bridge.TestSupportedActions -v
```

Also run delegation + change_reason tests:
```bash
cd /mnt/d/git/prefab-sentinel && uv run --extra test python -m unittest tests.test_mcp_server -k "vrcsdk_upload" -v
```

Expected: All PASS

- [ ] **Step 7: Commit**

```bash
git add prefab_sentinel/editor_bridge.py prefab_sentinel/mcp_server.py tests/test_mcp_server.py tests/test_editor_bridge.py
git commit -m "feat(mcp): add vrcsdk_upload tool for VRC SDK build + upload"
```

---

## Task 4: Full Regression + README

**Files:**
- Run: full test suite
- Modify: `README.md`

- [ ] **Step 1: Run full test suite**

```bash
cd /mnt/d/git/prefab-sentinel && uv run --extra test python -m unittest discover -s tests 2>&1 | grep -E "^(Ran|FAILED|OK)"
```

Expected: All tests PASS

- [ ] **Step 2: Run lint**

```bash
cd /mnt/d/git/prefab-sentinel && uv run ruff check prefab_sentinel/ tests/ --select I001,F
```

Expected: No errors

- [ ] **Step 3: Update README tool table**

Add after `editor_set_material_property` row:

```markdown
| `vrcsdk_upload` | VRC SDK 経由でアバター/ワールドをビルド＋アップロード（dry-run/confirm ゲート付き、既存アセット更新のみ） |
```

- [ ] **Step 4: Commit**

```bash
git add README.md
git commit -m "docs: add vrcsdk_upload to README"
```
