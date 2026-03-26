# Editor Bridge Bugfixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix 4 bugs and 2 quality issues from v0.5.82 field testing: non-focus rendering, VRCSDKUploadHandler compile errors, relative paths, unsaved warnings, and GUID error messages.

**Architecture:** Layer-by-layer bottom-up: C# handlers first (EditorControlBridge.cs, VRCSDKUploadHandler.cs), then Python (mcp_server.py path fix, version check), then tests. Screenshot handler already uses Camera.Render() so the focus is on pre-render state updates.

**Tech Stack:** C# (Unity Editor API), Python 3.11+ (FastMCP)

**Spec:** `docs/superpowers/specs/2026-03-26-editor-bridge-bugfixes-design.md`

---

## File Structure

### Modified Files

| File | Responsibility |
|------|---------------|
| `tools/unity/PrefabSentinel.UnityEditorControlBridge.cs` | ForceRenderAndRepaint, screenshot pre-render, BuildError/BuildSuccess access, BridgeVersion, unsaved warnings, GUID error |
| `tools/unity/PrefabSentinel.VRCSDKUploadHandler.cs` | VRC SDK API fixes (if needed after access modifier change) |
| `prefab_sentinel/mcp_server.py` | _read_asset path resolution, get_project_status version check |
| `prefab_sentinel/session.py` | Cache bridge_version |
| `tests/test_editor_bridge.py` | Path resolution tests |

---

## Task 1: C# — Replace RepaintAllViews with ForceRenderAndRepaint

**Files:**
- Modify: `tools/unity/PrefabSentinel.UnityEditorControlBridge.cs:915-925`

- [ ] **Step 1: Replace RepaintAllViews method**

Find `RepaintAllViews` (line 915) and replace with:

```csharp
        /// <summary>
        /// Force GPU rendering + GUI repaint. Works even when Unity is unfocused.
        /// QueuePlayerLoopUpdate forces skinning/physics recalculation,
        /// alwaysRefresh ensures SceneView renders even without focus.
        /// </summary>
        private static void ForceRenderAndRepaint(SceneView sceneView)
        {
            EditorApplication.QueuePlayerLoopUpdate();

            bool wasAlwaysRefresh = sceneView.sceneViewState.alwaysRefresh;
            sceneView.sceneViewState.alwaysRefresh = true;

            sceneView.Repaint();
            SceneView.RepaintAll();
            UnityEditorInternal.InternalEditorUtility.RepaintAllViews();

            EditorApplication.delayCall += () =>
            {
                sceneView.sceneViewState.alwaysRefresh = wasAlwaysRefresh;
                sceneView.Repaint();
                SceneView.RepaintAll();
            };
        }
```

- [ ] **Step 2: Rename all call sites**

Replace all `RepaintAllViews(` with `ForceRenderAndRepaint(` at these lines:
- Line 583 (HandleFrameSelected)
- Line 1042 (HandleSetCamera)
- Line 1364 (HandleSetMaterialProperty)
- Line 1655 (HandleSetBlendShape)

Use find-and-replace: `RepaintAllViews` → `ForceRenderAndRepaint` (4 occurrences).

- [ ] **Step 3: Commit**

```bash
git add tools/unity/PrefabSentinel.UnityEditorControlBridge.cs
git commit -m "fix(bridge): replace RepaintAllViews with ForceRenderAndRepaint for unfocused rendering"
```

---

## Task 2: C# — Screenshot pre-render enhancement

**Files:**
- Modify: `tools/unity/PrefabSentinel.UnityEditorControlBridge.cs:372-421`

- [ ] **Step 1: Add QueuePlayerLoopUpdate + alwaysRefresh before Camera.Render**

In `HandleCaptureScreenshot`, find the scene view branch (line 385). Before `cam.Render()` (line 405), add pre-render setup:

```csharp
                    try
                    {
                        // Force scene state update before capture (non-focus safe)
                        EditorApplication.QueuePlayerLoopUpdate();
                        bool wasAlwaysRefresh = sceneView.sceneViewState.alwaysRefresh;
                        sceneView.sceneViewState.alwaysRefresh = true;
                        sceneView.Repaint();

                        rt = new RenderTexture(w, h, 24);
                        RenderTexture prev = cam.targetTexture;
                        cam.targetTexture = rt;
                        cam.Render();
                        cam.targetTexture = prev;

                        // Restore alwaysRefresh
                        sceneView.sceneViewState.alwaysRefresh = wasAlwaysRefresh;
```

Insert the 4 new lines (`QueuePlayerLoopUpdate`, `wasAlwaysRefresh`, `alwaysRefresh = true`, `Repaint`) before the existing `rt = new RenderTexture(...)` line. Add the restore line after `cam.targetTexture = prev;`.

- [ ] **Step 2: Commit**

```bash
git add tools/unity/PrefabSentinel.UnityEditorControlBridge.cs
git commit -m "fix(bridge): force scene state update before screenshot capture"
```

---

## Task 3: C# — BuildError/BuildSuccess access modifier + BridgeVersion

**Files:**
- Modify: `tools/unity/PrefabSentinel.UnityEditorControlBridge.cs:19,1768,1781,1794`

- [ ] **Step 1: Change BuildError/BuildSuccess from private to internal**

At line 1768:
```csharp
// Before
private static EditorControlResponse BuildSuccess(string code, string message, EditorControlData data = null)
// After
internal static EditorControlResponse BuildSuccess(string code, string message, EditorControlData data = null)
```

At line 1781:
```csharp
// Before
private static EditorControlResponse BuildError(string code, string message)
// After
internal static EditorControlResponse BuildError(string code, string message)
```

At line 1794:
```csharp
// Before
private static EditorControlResponse BuildError(string code, string message, EditorControlData data)
// After
internal static EditorControlResponse BuildError(string code, string message, EditorControlData data)
```

- [ ] **Step 2: Add BridgeVersion constant**

At line 19 (next to `ProtocolVersion`), add:

```csharp
        public const int ProtocolVersion = 1;
        public const string BridgeVersion = "0.5.82";
```

- [ ] **Step 3: Add bridge_version to EditorControlResponse**

In the `EditorControlResponse` class (line 245), add:

```csharp
        public sealed class EditorControlResponse
        {
            public int protocol_version = ProtocolVersion;
            public string bridge_version = BridgeVersion;
            public bool success = false;
```

- [ ] **Step 4: Verify VRCSDKUploadHandler compiles**

Check `tools/unity/PrefabSentinel.VRCSDKUploadHandler.cs` — it uses `using static PrefabSentinel.UnityEditorControlBridge;` (line 12) to access `BuildError`/`BuildSuccess`. With `internal` access, this should now compile.

If `EditorUserBuildSettings.GetBuildTargetGroup` (line 147) causes a deprecation error, replace with:

```csharp
// Before
EditorUserBuildSettings.GetBuildTargetGroup(originalTarget)
// After
BuildPipeline.GetBuildTargetGroup(originalTarget)
```

- [ ] **Step 5: Commit**

```bash
git add tools/unity/PrefabSentinel.UnityEditorControlBridge.cs tools/unity/PrefabSentinel.VRCSDKUploadHandler.cs
git commit -m "fix(bridge): internal BuildError/BuildSuccess, add BridgeVersion"
```

---

## Task 4: C# — Unsaved warning diagnostics

**Files:**
- Modify: `tools/unity/PrefabSentinel.UnityEditorControlBridge.cs`

- [ ] **Step 1: Add unsaved warning to HandleSetBlendShape**

Find the return statement in HandleSetBlendShape (line 1657). Change:

```csharp
            return BuildSuccess("EDITOR_CTRL_BLEND_SHAPE_SET_OK",
                $"BlendShape '{request.blend_shape_name}' set from {before} to {weight}",
                data: new EditorControlData
                {
                    blend_shape_index = index,
                    blend_shape_name = request.blend_shape_name,
                    blend_shape_before = before,
                    blend_shape_after = weight,
                    executed = true,
                });
```

To:

```csharp
            var resp = BuildSuccess("EDITOR_CTRL_BLEND_SHAPE_SET_OK",
                $"BlendShape '{request.blend_shape_name}' set from {before} to {weight}",
                data: new EditorControlData
                {
                    blend_shape_index = index,
                    blend_shape_name = request.blend_shape_name,
                    blend_shape_before = before,
                    blend_shape_after = weight,
                    executed = true,
                });
            resp.diagnostics = new[] { new EditorControlDiagnostic
            {
                detail = "Runtime modification — save the scene (File > Save) to persist.",
                evidence = "Undo.RecordObject"
            }};
            return resp;
```

- [ ] **Step 2: Add unsaved warning to HandleSetMaterialProperty**

Find the return statement for `EDITOR_CTRL_SET_MATERIAL_PROPERTY_OK` (around line 1366). Apply the same pattern:

```csharp
            var resp = BuildSuccess("EDITOR_CTRL_SET_MATERIAL_PROPERTY_OK", ...);
            resp.diagnostics = new[] { new EditorControlDiagnostic
            {
                detail = "Runtime modification — save the scene (File > Save) to persist.",
                evidence = "Undo.RecordObject"
            }};
            return resp;
```

- [ ] **Step 3: Add unsaved warning to HandleSetMaterial**

Find the return statement for `EDITOR_CTRL_SET_MATERIAL_OK` (around line 740). Apply the same pattern:

```csharp
            var resp = BuildSuccess("EDITOR_CTRL_SET_MATERIAL_OK",
                $"Set material[{request.material_index}] to {assetPath}",
                data: new EditorControlData { executed = true, read_only = false });
            resp.diagnostics = new[] { new EditorControlDiagnostic
            {
                detail = "Runtime modification — save the scene (File > Save) to persist.",
                evidence = "Undo.RecordObject"
            }};
            return resp;
```

- [ ] **Step 4: Commit**

```bash
git add tools/unity/PrefabSentinel.UnityEditorControlBridge.cs
git commit -m "fix(bridge): add unsaved runtime modification warnings to diagnostics"
```

---

## Task 5: C# — Material GUID error message improvement

**Files:**
- Modify: `tools/unity/PrefabSentinel.UnityEditorControlBridge.cs:1299-1324`

- [ ] **Step 1: Improve texture GUID error message**

Find the texture case in HandleSetMaterialProperty (line 1299). Replace the existing error handling:

```csharp
                case UnityEngine.Rendering.ShaderPropertyType.Texture:
                {
                    if (string.IsNullOrEmpty(val))
                    {
                        mat.SetTexture(request.property_name, null);
                    }
                    else if (val.StartsWith("guid:"))
                    {
                        string guid = val.Substring(5);
                        string texPath = AssetDatabase.GUIDToAssetPath(guid);
                        if (string.IsNullOrEmpty(texPath))
                            return BuildError("EDITOR_CTRL_PROPERTY_TYPE_MISMATCH",
                                $"Texture GUID not found: {guid}");
                        var tex = AssetDatabase.LoadAssetAtPath<Texture>(texPath);
                        if (tex == null)
                        {
                            // Check if the GUID points to a non-texture asset
                            if (texPath.EndsWith(".mat"))
                                return BuildError("EDITOR_CTRL_SET_MAT_PROP_WRONG_GUID",
                                    $"The specified GUID points to a material asset '{texPath}'. " +
                                    "Please specify a texture GUID instead.");
                            return BuildError("EDITOR_CTRL_PROPERTY_TYPE_MISMATCH",
                                $"Failed to load texture from GUID '{guid}' (resolved to '{texPath}').");
                        }
                        mat.SetTexture(request.property_name, tex);
                    }
                    else
                    {
                        return BuildError("EDITOR_CTRL_PROPERTY_TYPE_MISMATCH",
                            "Texture value must be 'guid:<hex>' or empty string for null.");
                    }
                    break;
                }
```

The key change: the `if (tex == null)` block now checks `texPath.EndsWith(".mat")` and returns a specific `EDITOR_CTRL_SET_MAT_PROP_WRONG_GUID` error code with a clear message.

- [ ] **Step 2: Commit**

```bash
git add tools/unity/PrefabSentinel.UnityEditorControlBridge.cs
git commit -m "fix(bridge): clear error when material GUID used as texture GUID"
```

---

## Task 6: Python — _read_asset relative path fix

**Files:**
- Modify: `prefab_sentinel/mcp_server.py:85-95`
- Test: `tests/test_editor_bridge.py`

- [ ] **Step 1: Write failing test**

Add to `tests/test_editor_bridge.py`:

```python
class TestReadAssetPathResolution(unittest.TestCase):
    """Validate _read_asset resolves Assets/... paths with project root."""

    def test_relative_assets_path_resolved(self) -> None:
        """Assets/... path is joined with project root when file doesn't exist as relative."""
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmpdir:
            # Create a fake asset at project_root/Assets/test.prefab
            assets_dir = Path(tmpdir) / "Assets"
            assets_dir.mkdir()
            fake_asset = assets_dir / "test.prefab"
            fake_asset.write_text("%YAML 1.1\n--- !u!1 &1\nGameObject:\n  m_Name: Test\n")

            from prefab_sentinel.mcp_server import _resolve_asset_path

            resolved = _resolve_asset_path("Assets/test.prefab", Path(tmpdir))
            self.assertEqual(resolved, fake_asset)

    def test_absolute_path_unchanged(self) -> None:
        """Absolute paths are not modified."""
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmpdir:
            fake_asset = Path(tmpdir) / "test.prefab"
            fake_asset.write_text("%YAML 1.1\n--- !u!1 &1\nGameObject:\n  m_Name: Test\n")

            from prefab_sentinel.mcp_server import _resolve_asset_path

            resolved = _resolve_asset_path(str(fake_asset), Path(tmpdir))
            self.assertEqual(resolved, fake_asset)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run python -m unittest tests.test_editor_bridge.TestReadAssetPathResolution -v`
Expected: FAIL — `_resolve_asset_path` does not exist.

- [ ] **Step 3: Extract path resolution helper and update _read_asset**

In `prefab_sentinel/mcp_server.py`, add a helper before `_read_asset` (around line 83):

```python
    def _resolve_asset_path(path: str, project_root: Path | None) -> Path:
        """Resolve asset path, joining Assets/... paths with project root."""
        resolved = Path(to_wsl_path(path))
        if not resolved.is_file() and project_root and not resolved.is_absolute():
            joined = (project_root / resolved).resolve()
            if joined.is_file():
                return joined
        return resolved
```

Then update `_read_asset` (line 85):

```python
    def _read_asset(path: str) -> tuple[str, Path]:
        """Read a Unity asset file, returning (text, resolved_path)."""
        resolved = _resolve_asset_path(path, session.project_root)
        if not resolved.is_file():
            msg = f"File not found: {path}"
            raise FileNotFoundError(msg)
        text = decode_text_file(resolved)
        if text is None:
            msg = f"Unable to decode file: {path}"
            raise ValueError(msg)
        return text, resolved
```

**Note:** `_resolve_asset_path` is defined inside the server setup closure so it has access to scope. For testing, we need to import it. Since it's a closure-local function, we'll need to either:
- Move it to `editor_bridge.py` (like `build_set_camera_kwargs`)
- Or make it a module-level function

Move it to `prefab_sentinel/unity_assets.py` alongside `resolve_scope_path`:

```python
def resolve_asset_path(path: str, project_root: Path | None) -> Path:
    """Resolve asset path, joining Assets/... paths with project root.

    If *path* is relative (e.g. ``Assets/...``) and doesn't exist as-is,
    tries joining with *project_root*.
    """
    from prefab_sentinel.wsl_compat import to_wsl_path

    resolved = Path(to_wsl_path(path))
    if not resolved.is_file() and project_root and not resolved.is_absolute():
        joined = (project_root / resolved).resolve()
        if joined.is_file():
            return joined
    return resolved
```

Update the import in test to use `from prefab_sentinel.unity_assets import resolve_asset_path` and rename test accordingly.

- [ ] **Step 4: Update _read_asset to use resolve_asset_path**

In `prefab_sentinel/mcp_server.py`, update `_read_asset`:

```python
    def _read_asset(path: str) -> tuple[str, Path]:
        """Read a Unity asset file, returning (text, resolved_path)."""
        resolved = resolve_asset_path(path, session.project_root)
        if not resolved.is_file():
            msg = f"File not found: {path}"
            raise FileNotFoundError(msg)
        text = decode_text_file(resolved)
        if text is None:
            msg = f"Unable to decode file: {path}"
            raise ValueError(msg)
        return text, resolved
```

Add import at top of server setup: `from prefab_sentinel.unity_assets import resolve_asset_path`.

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run python -m unittest tests.test_editor_bridge.TestReadAssetPathResolution -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add prefab_sentinel/unity_assets.py prefab_sentinel/mcp_server.py tests/test_editor_bridge.py
git commit -m "fix(mcp): resolve Assets/... relative paths with project root in get_unity_symbols"
```

---

## Task 7: Python — Bridge version check in get_project_status

**Files:**
- Modify: `prefab_sentinel/session.py`
- Modify: `prefab_sentinel/editor_bridge.py`
- Modify: `prefab_sentinel/mcp_server.py:160-173`

- [ ] **Step 1: Add bridge_version caching to send_action**

In `prefab_sentinel/editor_bridge.py`, in `send_action` (around line 180 where response is parsed), cache bridge_version:

```python
    # After parsing response JSON, before return:
    if "bridge_version" in payload:
        _last_bridge_version = payload["bridge_version"]
```

Add module-level variable at top (around line 32):

```python
_last_bridge_version: str | None = None
```

Add getter function:

```python
def get_last_bridge_version() -> str | None:
    """Return the bridge_version from the last successful response, or None."""
    return _last_bridge_version
```

- [ ] **Step 2: Update get_project_status to include version comparison**

In `prefab_sentinel/mcp_server.py`, update `get_project_status` (line 160):

```python
    @server.tool()
    def get_project_status() -> dict[str, Any]:
        """Show current session state: cached items, scope, project root.

        Use this to check whether caches are warm or if activate_project
        needs to be called.
        """
        from importlib.metadata import version as pkg_version

        python_version = pkg_version("prefab-sentinel")
        bridge_ver = get_last_bridge_version()

        diagnostics = []
        if bridge_ver and bridge_ver != python_version:
            diagnostics.append({
                "detail": f"Bridge version mismatch: Bridge={bridge_ver}, Python={python_version}. "
                          "Update Bridge C# files and run editor_recompile.",
                "evidence": f"bridge_version={bridge_ver}, package_version={python_version}",
            })

        status = session.status()
        status["python_version"] = python_version
        status["bridge_version"] = bridge_ver

        return {
            "success": True,
            "severity": "warning" if diagnostics else "info",
            "code": "SESSION_STATUS",
            "message": "Current session status",
            "data": status,
            "diagnostics": diagnostics,
        }
```

Add import: `from prefab_sentinel.editor_bridge import get_last_bridge_version`

- [ ] **Step 3: Commit**

```bash
git add prefab_sentinel/editor_bridge.py prefab_sentinel/mcp_server.py
git commit -m "feat(mcp): bridge version check in get_project_status"
```

---

## Task 8: Final verification

- [ ] **Step 1: Run full Python test suite**

Run: `uv run python -m unittest tests.test_editor_bridge -v`
Expected: all tests PASS.

- [ ] **Step 2: Lint check**

Run: `uv run ruff check prefab_sentinel/editor_bridge.py prefab_sentinel/mcp_server.py prefab_sentinel/unity_assets.py tests/test_editor_bridge.py`
Expected: no errors.

- [ ] **Step 3: Note for manual Unity verification**

The following require Unity Editor with Bridge running:
- ForceRenderAndRepaint: set_blend_shape → screenshot with Unity unfocused
- Screenshot pre-render: same test as above, verify image shows updated blend shape
- VRCSDKUploadHandler: deploy both .cs files with VRC SDK project, verify compilation
- BridgeVersion: call any bridge action, verify `bridge_version` in response JSON
- Unsaved warning: call set_blend_shape, verify `diagnostics` array in response
- Material GUID error: pass a .mat GUID as texture value, verify error mentions "material asset"
- Relative paths: call get_unity_symbols with `Assets/...` path after activate_project
