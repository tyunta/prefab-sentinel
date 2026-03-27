# UX Review Plan A: C# Bridge Fixes + Features

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix ReflectionTypeLoadException + BridgeVersion, add ArraySize support to set_property, add editor_batch_add_component + editor_create_scene, and improve protocol error messages.

**Architecture:** 5 improvements to the C# EditorControlBridge + Python MCP wrappers. Bug fixes first (Task 1), then features (Tasks 2-4), then Python + tests (Task 5), then verification (Task 6).

**Tech Stack:** C# (Unity Editor API), Python 3.11+ (FastMCP)

**Spec:** `docs/superpowers/specs/2026-03-27-ux-review-improvements-design.md` (Spec A section)

---

## File Structure

### Modified Files

| File | Responsibility |
|------|---------------|
| `tools/unity/PrefabSentinel.UnityEditorControlBridge.cs` | BridgeVersion fix, ReflectionTypeLoadException fix, ArraySize/FixedBufferSize cases, protocol error message, HandleEditorBatchAddComponent, HandleEditorCreateScene, SupportedActions +2, dispatch +2, DTO 2 classes |
| `prefab_sentinel/mcp_server.py` | 2 new MCP tools |
| `prefab_sentinel/editor_bridge.py` | SUPPORTED_ACTIONS +2 |
| `tests/test_editor_bridge.py` | SUPPORTED_ACTIONS test |
| `tests/test_mcp_server.py` | Tool registration test (58 → 60) |
| `README.md` | Tool table update |

---

## Task 1: C# — Bug fixes (BridgeVersion + ReflectionTypeLoadException + protocol error)

**Files:**
- Modify: `tools/unity/PrefabSentinel.UnityEditorControlBridge.cs:20,312-314,2356`

- [ ] **Step 1: Fix BridgeVersion**

Line 20, change:

```csharp
        public const string BridgeVersion = "0.5.82";
```

To:

```csharp
        public const string BridgeVersion = "0.5.110";
```

- [ ] **Step 2: Fix ReflectionTypeLoadException**

Line 2356, change:

```csharp
                catch (System.ReflectionTypeLoadException) { }
```

To:

```csharp
                catch (System.Reflection.ReflectionTypeLoadException) { }
```

- [ ] **Step 3: Improve protocol version error message**

Lines 312-314, change:

```csharp
                WriteResponse(responsePath, BuildError(
                    "EDITOR_CTRL_PROTOCOL_VERSION",
                    $"Expected protocol_version {ProtocolVersion}, got {request.protocol_version}."));
```

To:

```csharp
                WriteResponse(responsePath, BuildError(
                    "EDITOR_CTRL_PROTOCOL_VERSION",
                    $"Bridge protocol v{request.protocol_version}, required v{ProtocolVersion}. " +
                    "Update Bridge: copy tools/unity/*.cs from prefab-sentinel to Assets/Editor/PrefabSentinel/"));
```

- [ ] **Step 4: Commit**

```bash
git add tools/unity/PrefabSentinel.UnityEditorControlBridge.cs
git commit -m "fix(bridge): BridgeVersion 0.5.110, ReflectionTypeLoadException fqn, protocol error message"
```

---

## Task 2: C# — ArraySize + FixedBufferSize support in HandleEditorSetProperty

**Files:**
- Modify: `tools/unity/PrefabSentinel.UnityEditorControlBridge.cs:2650-2662`

- [ ] **Step 1: Add ArraySize and FixedBufferSize cases**

Before the `ObjectReference` case (line 2650), add:

```csharp
                    case SerializedPropertyType.ArraySize:
                    case SerializedPropertyType.FixedBufferSize:
                        prop.intValue = int.Parse(v, System.Globalization.CultureInfo.InvariantCulture);
                        break;
                    case SerializedPropertyType.ObjectReference:
```

- [ ] **Step 2: Also add to ApplyPropertyValue helper**

Find the `ApplyPropertyValue` method (around line 1960). After the `Vector3` case, before `default:`, add:

```csharp
                case SerializedPropertyType.ArraySize:
                case SerializedPropertyType.FixedBufferSize:
                    if (int.TryParse(v, System.Globalization.NumberStyles.Integer, ci, out int av))
                    { prop.intValue = av; return true; }
                    return false;
```

- [ ] **Step 3: Commit**

```bash
git add tools/unity/PrefabSentinel.UnityEditorControlBridge.cs
git commit -m "feat(bridge): support ArraySize and FixedBufferSize in set_property"
```

---

## Task 3: C# — HandleEditorBatchAddComponent

**Files:**
- Modify: `tools/unity/PrefabSentinel.UnityEditorControlBridge.cs`

- [ ] **Step 1: Add DTOs**

Before the `// ── Response Builders ──` section, after existing batch DTOs, add:

```csharp
        [Serializable]
        private sealed class BatchAddComponentOp
        {
            public string hierarchy_path = string.Empty;
            public string component_type = string.Empty;
            public string properties_json = string.Empty;
        }

        [Serializable]
        private sealed class BatchAddComponentArray { public BatchAddComponentOp[] items; }
```

- [ ] **Step 2: Add to SupportedActions**

After line 63 (`"editor_save_scene",`):

```csharp
            "editor_save_scene",
            // Phase 7: UX Review improvements
            "editor_batch_add_component",
            "editor_create_scene",
        };
```

- [ ] **Step 3: Add dispatch cases**

After `editor_save_scene` case (line 425), before `case "vrcsdk_upload":`:

```csharp
                case "editor_save_scene":
                    response = HandleEditorSaveScene(request);
                    break;
                case "editor_batch_add_component":
                    response = HandleEditorBatchAddComponent(request);
                    break;
                case "editor_create_scene":
                    response = HandleEditorCreateScene(request);
                    break;
                case "vrcsdk_upload":
```

- [ ] **Step 4: Implement HandleEditorBatchAddComponent**

After `HandleEditorSaveScene`, before `// ── Batch Operation DTOs ──`:

```csharp
        // ── Phase 7: UX Review improvements ──

        private static EditorControlResponse HandleEditorBatchAddComponent(EditorControlRequest request)
        {
            if (string.IsNullOrEmpty(request.batch_operations_json))
                return BuildError("EDITOR_CTRL_BATCH_ADD_COMP_NO_DATA",
                    "batch_operations_json is required.");

            BatchAddComponentArray wrapper;
            try
            {
                wrapper = JsonUtility.FromJson<BatchAddComponentArray>(
                    "{\"items\":" + request.batch_operations_json + "}");
            }
            catch (System.Exception ex)
            {
                return BuildError("EDITOR_CTRL_BATCH_ADD_COMP_JSON_ERROR",
                    $"Failed to parse batch_operations_json: {ex.Message}");
            }

            if (wrapper.items == null || wrapper.items.Length == 0)
                return BuildError("EDITOR_CTRL_BATCH_ADD_COMP_EMPTY",
                    "batch_operations_json is empty.");

            int undoGroup = Undo.GetCurrentGroup();
            Undo.SetCurrentGroupName("PrefabSentinel: Batch AddComponent");

            var results = new System.Collections.Generic.List<string>();

            foreach (var op in wrapper.items)
            {
                var subReq = new EditorControlRequest
                {
                    action = "editor_add_component",
                    hierarchy_path = op.hierarchy_path,
                    component_type = op.component_type,
                    properties_json = op.properties_json,
                };
                var subResp = HandleEditorAddComponent(subReq);
                if (!subResp.success)
                {
                    Undo.CollapseUndoOperations(undoGroup);
                    return BuildError("EDITOR_CTRL_BATCH_ADD_COMP_FAILED",
                        $"Operation failed at index {results.Count}: {subResp.message}");
                }
                results.Add($"{op.hierarchy_path}: {op.component_type}");
            }

            Undo.CollapseUndoOperations(undoGroup);

            var resp = BuildSuccess("EDITOR_CTRL_BATCH_ADD_COMP_OK",
                $"Added {results.Count} components",
                data: new EditorControlData
                {
                    executed = true,
                    read_only = false,
                    suggestions = results.ToArray(),
                });
            resp.diagnostics = new[] { new EditorControlDiagnostic
            {
                detail = "Runtime modification — save the scene (File > Save) to persist.",
                evidence = "Undo.CollapseUndoOperations"
            }};
            return resp;
        }
```

- [ ] **Step 5: Implement HandleEditorCreateScene**

After `HandleEditorBatchAddComponent`:

```csharp
        private static EditorControlResponse HandleEditorCreateScene(EditorControlRequest request)
        {
            if (string.IsNullOrEmpty(request.asset_path))
                return BuildError("EDITOR_CTRL_CREATE_SCENE_NO_PATH", "asset_path is required.");

            if (!request.asset_path.EndsWith(".unity", System.StringComparison.OrdinalIgnoreCase))
                return BuildError("EDITOR_CTRL_CREATE_SCENE_BAD_EXT",
                    $"asset_path must end with .unity: {request.asset_path}");

            // Ensure output directory exists
            string dir = System.IO.Path.GetDirectoryName(request.asset_path);
            if (!string.IsNullOrEmpty(dir) && !System.IO.Directory.Exists(dir))
                System.IO.Directory.CreateDirectory(dir);

            var scene = EditorSceneManager.NewScene(NewSceneSetup.EmptyScene, NewSceneMode.Single);
            bool ok = EditorSceneManager.SaveScene(scene, request.asset_path);
            if (!ok)
                return BuildError("EDITOR_CTRL_CREATE_SCENE_FAILED",
                    $"Failed to save new scene to: {request.asset_path}");

            return BuildSuccess("EDITOR_CTRL_CREATE_SCENE_OK",
                $"Created new scene: {request.asset_path}",
                data: new EditorControlData
                {
                    asset_path = request.asset_path,
                    output_path = scene.name,
                    executed = true,
                });
        }
```

- [ ] **Step 6: Commit**

```bash
git add tools/unity/PrefabSentinel.UnityEditorControlBridge.cs
git commit -m "feat(bridge): editor_batch_add_component and editor_create_scene"
```

---

## Task 4: Python — SUPPORTED_ACTIONS + MCP tools + tests

**Files:**
- Modify: `prefab_sentinel/editor_bridge.py`
- Modify: `prefab_sentinel/mcp_server.py`
- Modify: `tests/test_editor_bridge.py`
- Modify: `tests/test_mcp_server.py`

- [ ] **Step 1: Add 2 actions to SUPPORTED_ACTIONS**

In `prefab_sentinel/editor_bridge.py`, after `"editor_save_scene",`:

```python
        "editor_save_scene",
        # Phase 7: UX Review improvements
        "editor_batch_add_component",
        "editor_create_scene",
    }
)
```

- [ ] **Step 2: Add MCP tools**

In `prefab_sentinel/mcp_server.py`, after `editor_save_scene` tool, before `# Inspection tools`:

```python
    # ------------------------------------------------------------------
    # Phase 7: UX Review improvements
    # ------------------------------------------------------------------

    @server.tool()
    def editor_batch_add_component(
        operations: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Add components to multiple GameObjects in a single request (Undo-grouped).

        Each operation dict must contain: hierarchy_path, component_type.
        Optional: properties (list of {name, value/object_reference} dicts).

        Args:
            operations: List of add-component operations.
        """
        import json

        return send_action(
            action="editor_batch_add_component",
            batch_operations_json=json.dumps(operations, ensure_ascii=False),
        )

    @server.tool()
    def editor_create_scene(
        scene_path: str,
    ) -> dict[str, Any]:
        """Create a new empty Unity scene and save it to the specified path.

        Replaces the current scene with a new empty one. Use editor_save_scene
        first if you need to preserve the current scene.

        Args:
            scene_path: Asset path for the new scene (e.g. "Assets/Scenes/NewScene.unity").
        """
        return send_action(
            action="editor_create_scene",
            asset_path=scene_path,
        )
```

- [ ] **Step 3: Update test_editor_bridge.py**

Add to expected set:

```python
            "editor_save_scene",
            "editor_batch_add_component",
            "editor_create_scene",
        }
```

- [ ] **Step 4: Update test_mcp_server.py**

Add to expected set after `"editor_save_scene",`:

```python
            "editor_open_scene", "editor_save_scene",
            "editor_batch_add_component", "editor_create_scene",
            # Inspection + orchestrator tools
```

Change count: `self.assertEqual(60, len(tools))`

- [ ] **Step 5: Run tests + lint**

Run: `uv run python -m unittest tests.test_editor_bridge.TestSupportedActions.test_all_actions_present -v`
Expected: PASS

Run: `python3 -m compileall prefab_sentinel/mcp_server.py`
Expected: no errors.

Run: `uv run ruff check prefab_sentinel/editor_bridge.py prefab_sentinel/mcp_server.py`
Expected: All checks passed.

- [ ] **Step 6: Commit**

```bash
git add prefab_sentinel/editor_bridge.py prefab_sentinel/mcp_server.py tests/test_editor_bridge.py tests/test_mcp_server.py
git commit -m "feat(mcp): add editor_batch_add_component and editor_create_scene (60 tools)"
```

---

## Task 5: Verification + README

- [ ] **Step 1: Full test suite**

Run: `uv run --extra test python scripts/run_unit_tests.py`
Expected: all tests pass.

- [ ] **Step 2: Verify MCP tool count**

Run: `uv run --extra mcp python -c "from prefab_sentinel.mcp_server import create_server; s = create_server(); print(len(s._tool_manager._tools))"`
Expected: 60

- [ ] **Step 3: Update README**

Add 2 tools to the MCP tool table:

```
| `editor_batch_add_component` | 複数オブジェクトにコンポーネントを一括追加 (Undo グループ、初期値対応) |
| `editor_create_scene` | 新規空シーンを作成して保存 |
```

- [ ] **Step 4: Commit**

```bash
git add README.md
git commit -m "docs: add editor_batch_add_component and editor_create_scene to README"
```
