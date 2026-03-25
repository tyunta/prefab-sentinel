# VRCSDK Multi-Platform Upload Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `platforms` parameter to `vrcsdk_upload` so a single MCP call can build+upload to Windows, Android, and iOS sequentially.

**Architecture:** Python MCP tool validates `platforms`, passes as JSON string to C# via Bridge. C# handler loops through platforms, switches build target, builds+uploads, restores original target. Per-platform results returned as JSON string in `EditorControlData`, converted to structured data in Python.

**Tech Stack:** Python `json` (stdlib), C# `EditorUserBuildSettings.SwitchActiveBuildTarget`, existing VRC SDK builder APIs

**Spec:** `docs/superpowers/specs/2026-03-25-vrcsdk-multiplatform-design.md`

---

### Task 1: Python `platforms` parameter + validation

**Files:**
- Modify: `prefab_sentinel/mcp_server.py` (lines 1075-1124, the `vrcsdk_upload` tool)
- Modify: `tests/test_mcp_server.py` (lines 1961-1995, existing vrcsdk tests)

- [ ] **Step 1: Write failing tests for `platforms` validation and conversion**

Add these tests after `test_vrcsdk_upload_requires_change_reason` in `tests/test_mcp_server.py`:

```python
    def test_vrcsdk_upload_invalid_platforms_empty(self) -> None:
        """Empty platforms list returns validation error."""
        server = create_server()
        with patch("prefab_sentinel.mcp_server.send_action") as mock_send:
            _, result = _run(server.call_tool("vrcsdk_upload", {
                "target_type": "avatar",
                "asset_path": "Assets/Avatars/Test.prefab",
                "blueprint_id": "avtr_test123",
                "platforms": [],
            }))
            mock_send.assert_not_called()
        self.assertFalse(result["success"])
        self.assertEqual("VRCSDK_INVALID_PLATFORMS", result["code"])

    def test_vrcsdk_upload_invalid_platforms_bad_value(self) -> None:
        """Invalid platform name returns validation error."""
        server = create_server()
        with patch("prefab_sentinel.mcp_server.send_action") as mock_send:
            _, result = _run(server.call_tool("vrcsdk_upload", {
                "target_type": "avatar",
                "asset_path": "Assets/Avatars/Test.prefab",
                "blueprint_id": "avtr_test123",
                "platforms": ["windows", "ps5"],
            }))
            mock_send.assert_not_called()
        self.assertFalse(result["success"])
        self.assertEqual("VRCSDK_INVALID_PLATFORMS", result["code"])

    def test_vrcsdk_upload_invalid_platforms_duplicate(self) -> None:
        """Duplicate platform returns validation error."""
        server = create_server()
        with patch("prefab_sentinel.mcp_server.send_action") as mock_send:
            _, result = _run(server.call_tool("vrcsdk_upload", {
                "target_type": "avatar",
                "asset_path": "Assets/Avatars/Test.prefab",
                "blueprint_id": "avtr_test123",
                "platforms": ["windows", "windows"],
            }))
            mock_send.assert_not_called()
        self.assertFalse(result["success"])
        self.assertEqual("VRCSDK_INVALID_PLATFORMS", result["code"])

    def test_vrcsdk_upload_delegates(self) -> None:
        """Default platforms=["windows"] is serialized and passed to send_action."""
        server = create_server()
        with patch("prefab_sentinel.mcp_server.send_action", return_value={"success": True, "data": {}}) as mock_send:
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
            platforms='["windows"]',
            description="",
            tags="",
            release_status="",
            confirm=False,
        )

    def test_vrcsdk_upload_converts_platform_results(self) -> None:
        """platform_results_json from C# is converted to platform_results list."""
        server = create_server()
        bridge_response = {
            "success": True,
            "data": {
                "phase": "complete",
                "platform_results_json": '[{"platform":"windows","success":true,"elapsed_sec":45.1}]',
                "original_target_restored": True,
            },
        }
        with patch("prefab_sentinel.mcp_server.send_action", return_value=bridge_response):
            _, result = _run(server.call_tool("vrcsdk_upload", {
                "target_type": "avatar",
                "asset_path": "Assets/Avatars/Test.prefab",
                "blueprint_id": "avtr_test123",
                "confirm": True,
                "change_reason": "test upload",
            }))
        self.assertIn("platform_results", result["data"])
        self.assertEqual(result["data"]["platform_results"][0]["platform"], "windows")
        self.assertNotIn("platform_results_json", result["data"])

    def test_vrcsdk_upload_converts_mixed_platform_results(self) -> None:
        """platform_results_json with success + failure + skipped is correctly parsed."""
        server = create_server()
        bridge_response = {
            "success": False,
            "data": {
                "phase": "failed",
                "platform_results_json": '[{"platform":"windows","success":true,"elapsed_sec":45.1},{"platform":"android","success":false,"elapsed_sec":9.9,"error":"Shader error"},{"platform":"ios","skipped":true}]',
                "original_target_restored": True,
            },
        }
        with patch("prefab_sentinel.mcp_server.send_action", return_value=bridge_response):
            _, result = _run(server.call_tool("vrcsdk_upload", {
                "target_type": "avatar",
                "asset_path": "Assets/Avatars/Test.prefab",
                "blueprint_id": "avtr_test123",
                "platforms": ["windows", "android", "ios"],
                "confirm": True,
                "change_reason": "test upload",
            }))
        pr = result["data"]["platform_results"]
        self.assertEqual(len(pr), 3)
        self.assertTrue(pr[0]["success"])
        self.assertFalse(pr[1]["success"])
        self.assertTrue(pr[2]["skipped"])
        self.assertNotIn("platform_results_json", result["data"])

    def test_vrcsdk_upload_dryrun_includes_platforms(self) -> None:
        """dry-run response includes platforms echo-back from Python."""
        server = create_server()
        bridge_response = {"success": True, "data": {"phase": "validated"}}
        with patch("prefab_sentinel.mcp_server.send_action", return_value=bridge_response):
            _, result = _run(server.call_tool("vrcsdk_upload", {
                "target_type": "avatar",
                "asset_path": "Assets/Avatars/Test.prefab",
                "blueprint_id": "avtr_test123",
                "platforms": ["windows", "android"],
            }))
        self.assertEqual(result["data"]["platforms"], ["windows", "android"])
```

Note: The above replaces the existing `test_vrcsdk_upload_delegates` test (which was at line 1961) and adds conversion/dry-run tests. Delete the old `test_vrcsdk_upload_delegates` if it still exists separately.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_mcp_server.py -k "vrcsdk_upload_invalid_platforms or vrcsdk_upload_delegates or vrcsdk_upload_converts or vrcsdk_upload_dryrun" -v`
Expected: FAIL (no `platforms` parameter exists yet)

- [ ] **Step 3: Add `import json` to `mcp_server.py`**

Add `import json` to the module-level imports (after `import logging` at line 13):

```python
import json
import logging
```

- [ ] **Step 4: Implement `platforms` validation in `vrcsdk_upload`**

Replace the existing `vrcsdk_upload` function (lines 1075-1124) with:

```python
    @server.tool()
    def vrcsdk_upload(
        target_type: str,
        asset_path: str,
        blueprint_id: str,
        platforms: list[str] | None = None,
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
            platforms: List of target platforms (default: ["windows"]).
                Valid values: "windows", "android", "ios".
            description: Description text (empty = no change).
            tags: JSON array of tag strings (empty = no change).
            release_status: "public" or "private" (empty = no change).
            confirm: Set True to build + upload (False = validation only).
            change_reason: Required when confirm=True. Audit log reason.
            timeout_sec: Bridge timeout in seconds (default: 600).
                For multi-platform, recommend 600 * len(platforms).
        """
        if platforms is None:
            platforms = ["windows"]

        _VALID_PLATFORMS = {"windows", "android", "ios"}
        if not platforms:
            return {
                "success": False,
                "severity": "error",
                "code": "VRCSDK_INVALID_PLATFORMS",
                "message": "platforms must not be empty",
                "data": {},
                "diagnostics": [],
            }
        invalid = [p for p in platforms if p not in _VALID_PLATFORMS]
        if invalid:
            return {
                "success": False,
                "severity": "error",
                "code": "VRCSDK_INVALID_PLATFORMS",
                "message": f"Invalid platform(s): {invalid}. Valid: {sorted(_VALID_PLATFORMS)}",
                "data": {},
                "diagnostics": [],
            }
        if len(platforms) != len(set(platforms)):
            return {
                "success": False,
                "severity": "error",
                "code": "VRCSDK_INVALID_PLATFORMS",
                "message": f"Duplicate platform(s) in: {platforms}",
                "data": {},
                "diagnostics": [],
            }

        if confirm and not change_reason:
            return {
                "success": False,
                "severity": "error",
                "code": "VRCSDK_REASON_REQUIRED",
                "message": "change_reason is required when confirm=True",
                "data": {},
                "diagnostics": [],
            }

        result = send_action(
            action="vrcsdk_upload",
            timeout_sec=timeout_sec,
            target_type=target_type,
            asset_path=asset_path,
            blueprint_id=blueprint_id,
            platforms=json.dumps(platforms),
            description=description,
            tags=tags,
            release_status=release_status,
            confirm=confirm,
        )

        # Post-process: convert C# platform_results_json to structured data
        data = result.setdefault("data", {})
        if isinstance(data, dict):
            prj = data.pop("platform_results_json", "")
            if prj:
                data["platform_results"] = json.loads(prj)
            if not confirm:
                data["platforms"] = platforms

        return result
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_mcp_server.py -k "vrcsdk_upload" -v`
Expected: All 8 vrcsdk tests pass (existing `requires_change_reason` + 7 new)

- [ ] **Step 6: Commit**

```bash
git add prefab_sentinel/mcp_server.py tests/test_mcp_server.py
git commit -m "feat: add platforms parameter to vrcsdk_upload MCP tool"
```

---

### Task 2: C# DTO changes (EditorControlRequest + EditorControlData)

**Files:**
- Modify: `tools/unity/PrefabSentinel.UnityEditorControlBridge.cs` (EditorControlRequest ~line 100, EditorControlData ~line 187)

- [ ] **Step 1: Add `platforms` to EditorControlRequest**

In `EditorControlRequest` (after `confirm` at line 106), add:

```csharp
            public string platforms = string.Empty;  // JSON array: "[\"windows\",\"android\"]"
```

- [ ] **Step 2: Add response fields to EditorControlData**

In `EditorControlData` (after `elapsed_sec` at line 187, before `suggestions`), add:

```csharp
            // multi-platform upload results
            public string platform_results_json = string.Empty;
            public bool original_target_restored = false;
```

- [ ] **Step 3: Commit**

```bash
git add tools/unity/PrefabSentinel.UnityEditorControlBridge.cs
git commit -m "feat: add multi-platform DTO fields to EditorControlRequest/Data"
```

---

### Task 3: C# VRCSDKUploadHandler multi-platform loop

**Files:**
- Modify: `tools/unity/PrefabSentinel.VRCSDKUploadHandler.cs` (entire file refactor)

- [ ] **Step 1: Add platform mapping helpers**

Add after the `BuildAndUploadWorld` method (before the closing `}` of the class):

```csharp
        private static BuildTarget ToBuildTarget(string platform) => platform switch
        {
            "windows" => BuildTarget.StandaloneWindows64,
            "android" => BuildTarget.Android,
            "ios" => BuildTarget.iOS,
            _ => throw new ArgumentException($"Unknown platform: {platform}")
        };

        private static BuildTargetGroup ToBuildTargetGroup(string platform) => platform switch
        {
            "windows" => BuildTargetGroup.Standalone,
            "android" => BuildTargetGroup.Android,
            "ios" => BuildTargetGroup.iOS,
            _ => throw new ArgumentException($"Unknown platform: {platform}")
        };

        private static string[] ParsePlatforms(string json)
        {
            if (string.IsNullOrEmpty(json))
                return new[] { "windows" };
            // Minimal JSON array parser for string arrays: ["windows","android"]
            json = json.Trim();
            if (!json.StartsWith("[") || !json.EndsWith("]"))
                return new[] { "windows" };
            json = json.Substring(1, json.Length - 2); // strip [ ]
            if (string.IsNullOrWhiteSpace(json))
                return new[] { "windows" };
            var parts = json.Split(',');
            var result = new string[parts.Length];
            for (int i = 0; i < parts.Length; i++)
                result[i] = parts[i].Trim().Trim('"');
            return result;
        }

        private static string BuildPlatformResultsJson(
            System.Collections.Generic.List<(string platform, bool success, float elapsed, string error, bool skipped)> results)
        {
            var sb = new System.Text.StringBuilder("[");
            for (int i = 0; i < results.Count; i++)
            {
                if (i > 0) sb.Append(",");
                var r = results[i];
                if (r.skipped)
                    sb.Append($"{{\"platform\":\"{r.platform}\",\"skipped\":true}}");
                else if (r.success)
                    sb.Append($"{{\"platform\":\"{r.platform}\",\"success\":true,\"elapsed_sec\":{r.elapsed:F1}}}");
                else
                {
                    var escapedError = r.error.Replace("\\", "\\\\").Replace("\"", "\\\"").Replace("\n", "\\n");
                    sb.Append($"{{\"platform\":\"{r.platform}\",\"success\":false,\"elapsed_sec\":{r.elapsed:F1},\"error\":\"{escapedError}\"}}");
                }
            }
            sb.Append("]");
            return sb.ToString();
        }
```

- [ ] **Step 2: Refactor Handle() to support multi-platform**

Replace the entire `Handle` method with:

```csharp
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

            // --- Parse platforms ---
            var platforms = ParsePlatforms(request.platforms);

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

            // --- Multi-platform Build + Upload ---
            var originalTarget = EditorUserBuildSettings.activeBuildTarget;
            var totalSw = Stopwatch.StartNew();
            var results = new System.Collections.Generic.List<(string platform, bool success, float elapsed, string error, bool skipped)>();
            bool failed = false;
            bool restored = false;
            string failCode = "VRCSDK_BUILD_FAILED";
            string failMessage = "";

            try
            {
                for (int i = 0; i < platforms.Length; i++)
                {
                    var platform = platforms[i];
                    var platSw = Stopwatch.StartNew();

                    try
                    {
                        // Switch build target
                        bool switched = EditorUserBuildSettings.SwitchActiveBuildTarget(
                            ToBuildTargetGroup(platform), ToBuildTarget(platform));
                        if (!switched)
                        {
                            platSw.Stop();
                            results.Add((platform, false, (float)platSw.Elapsed.TotalSeconds,
                                "Platform switch failed", false));
                            failCode = "VRCSDK_PLATFORM_SWITCH_FAILED";
                            failMessage = $"Failed to switch to platform '{platform}'";
                            failed = true;
                            break;
                        }

                        // Build + Upload
                        if (request.target_type == "avatar")
                            BuildAndUploadAvatar(request);
                        else
                            BuildAndUploadWorld(request);

                        platSw.Stop();
                        results.Add((platform, true, (float)platSw.Elapsed.TotalSeconds, "", false));
                    }
                    catch (Exception ex)
                    {
                        platSw.Stop();
                        results.Add((platform, false, (float)platSw.Elapsed.TotalSeconds, ex.Message, false));
                        failCode = ex.Message.Contains("upload", StringComparison.OrdinalIgnoreCase)
                            ? "VRCSDK_UPLOAD_FAILED"
                            : "VRCSDK_BUILD_FAILED";
                        failMessage = $"{request.target_type} failed on platform '{platform}' after {platSw.Elapsed.TotalSeconds:F1}s: {ex.Message}";
                        failed = true;
                        break;
                    }
                }
            }
            finally
            {
                // Mark remaining platforms as skipped
                for (int i = results.Count; i < platforms.Length; i++)
                    results.Add((platforms[i], false, 0f, "", true));

                // Restore original build target (always, even on failure)
                restored = EditorUserBuildSettings.SwitchActiveBuildTarget(
                    EditorUserBuildSettings.GetBuildTargetGroup(originalTarget), originalTarget);
            }

            totalSw.Stop();

            var data = new EditorControlData
            {
                target_type = request.target_type,
                asset_path = request.asset_path,
                blueprint_id = request.blueprint_id,
                phase = failed ? "failed" : "complete",
                elapsed_sec = (float)totalSw.Elapsed.TotalSeconds,
                executed = true,
                platform_results_json = BuildPlatformResultsJson(results),
                original_target_restored = restored,
            };

            if (failed)
            {
                return BuildError(failCode, failMessage, data);
            }

            int platCount = platforms.Length;
            return BuildSuccess("VRCSDK_UPLOAD_OK",
                $"Uploaded {request.target_type} to {platCount} platform(s) in {totalSw.Elapsed.TotalSeconds:F1}s",
                data: data);
        }
```

- [ ] **Step 3: Verify method names are present**

```bash
grep -n "SwitchActiveBuildTarget\|ParsePlatforms\|BuildPlatformResultsJson\|ToBuildTarget\|platform_results_json\|original_target_restored" tools/unity/PrefabSentinel.VRCSDKUploadHandler.cs
```

Expected: All new method names appear at expected locations. Note: full C# compilation requires Unity Editor and will be verified during manual integration testing.

- [ ] **Step 4: Commit**

```bash
git add tools/unity/PrefabSentinel.VRCSDKUploadHandler.cs
git commit -m "feat: add multi-platform build loop to VRCSDKUploadHandler"
```

---

### Task 4: README update + final verification

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Add multi-platform section to README**

Find `### 17.8.1 エラーヒント` section in README (line 1128) and add after it:

```markdown
### 17.9 VRC SDK アップロード

### 17.9.1 マルチプラットフォームアップロード

- `vrcsdk_upload` の `platforms` パラメータで複数プラットフォームへの順次ビルド+アップロードが可能。
- 有効値: `"windows"`, `"android"`, `"ios"`。デフォルト: `["windows"]`。
- 順次実行し、途中失敗で残りをスキップする。完了後は元のビルドターゲットに復元する。
- レスポンスの `data.platform_results` に per-platform の結果（成功/失敗/スキップ）を含む。
- `data.original_target_restored` で元のビルドターゲットの復元成否を確認できる。
- 複数プラットフォーム時は `timeout_sec` を `600 * len(platforms)` 程度に設定することを推奨。
```

- [ ] **Step 2: Run full test suite**

Run: `uv run pytest tests/test_mcp_server.py -k "vrcsdk_upload" -v`
Expected: All vrcsdk tests pass

- [ ] **Step 3: Run full regression check**

Run: `uv run pytest tests/ -q --tb=no`
Expected: No new failures beyond pre-existing ones

- [ ] **Step 4: Commit**

```bash
git add README.md
git commit -m "docs: add multi-platform upload section to README"
```
