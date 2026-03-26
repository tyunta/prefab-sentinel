# Editor Bridge Phase 2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add 4 new MCP tools (BlendShape get/set + Menu list/execute) to the Editor Bridge.

**Architecture:** Python MCP tools call `send_action()` which writes a JSON request to the bridge watch directory. The C# Unity Editor script polls for requests, dispatches to the appropriate handler, and writes a JSON response. Each new tool follows the existing handler pattern with validation, Undo support, and structured error codes.

**Tech Stack:** Python 3.12 (MCP server), C# (Unity Editor), pytest, uv

---

## File Structure

| File | Action | Responsibility |
|------|--------|---------------|
| `prefab_sentinel/editor_bridge.py` | Modify (line 33-55) | Add 4 action strings to `SUPPORTED_ACTIONS` |
| `prefab_sentinel/mcp_server.py` | Modify (after line 1241) | Add 4 `@server.tool()` functions |
| `tests/test_editor_bridge.py` | Modify (line 167-189) | Update `test_all_actions_present` expected set |
| `tests/test_mcp_server.py` | Modify (line 60-86) | Update `EXPECTED_TOOLS` set + `test_tool_count` |
| `tools/unity/PrefabSentinel.UnityEditorControlBridge.cs` | Modify | DTO classes, request fields, 4 handlers, switch cases, `SupportedActions` |
| `README.md` | Modify | Add 4 new tools to tool listing |

---

### Task 1: Python — Add 4 actions to SUPPORTED_ACTIONS (TDD)

**Files:**
- Test: `tests/test_editor_bridge.py:167-189`
- Modify: `prefab_sentinel/editor_bridge.py:33-55`

- [ ] **Step 1: Write the failing test**

Update the `test_all_actions_present` expected set in `tests/test_editor_bridge.py` to include the 4 new action strings:

```python
    def test_all_actions_present(self) -> None:
        expected = {
            "capture_screenshot",
            "select_object",
            "frame_selected",
            "instantiate_to_scene",
            "ping_object",
            "capture_console_logs",
            "recompile_scripts",
            "refresh_asset_database",
            "set_material",
            "delete_object",
            "list_children",
            "list_materials",
            "get_camera",
            "set_camera",
            "list_roots",
            "get_material_property",
            "set_material_property",
            "run_integration_tests",
            "vrcsdk_upload",
            # Phase 2: BlendShape + Menu
            "get_blend_shapes",
            "set_blend_shape",
            "list_menu_items",
            "execute_menu_item",
        }
        self.assertEqual(expected, SUPPORTED_ACTIONS)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /mnt/d/git/prefab-sentinel && uv run pytest tests/test_editor_bridge.py::TestSupportedActions::test_all_actions_present -v`
Expected: FAIL with `AssertionError` — 4 actions missing from `SUPPORTED_ACTIONS`

- [ ] **Step 3: Write minimal implementation**

Add 4 actions to the `SUPPORTED_ACTIONS` frozenset in `prefab_sentinel/editor_bridge.py` (after `"vrcsdk_upload"`):

```python
SUPPORTED_ACTIONS = frozenset(
    {
        "capture_screenshot",
        "select_object",
        "frame_selected",
        "instantiate_to_scene",
        "ping_object",
        "capture_console_logs",
        "recompile_scripts",
        "refresh_asset_database",
        "set_material",
        "delete_object",
        "list_children",
        "list_materials",
        "get_camera",
        "set_camera",
        "list_roots",
        "get_material_property",
        "set_material_property",
        "run_integration_tests",
        "vrcsdk_upload",
        # Phase 2: BlendShape + Menu
        "get_blend_shapes",
        "set_blend_shape",
        "list_menu_items",
        "execute_menu_item",
    }
)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /mnt/d/git/prefab-sentinel && uv run pytest tests/test_editor_bridge.py -v`
Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
git add prefab_sentinel/editor_bridge.py tests/test_editor_bridge.py
git commit -m "feat: add Phase 2 actions to SUPPORTED_ACTIONS (BlendShape + Menu)"
```

---

### Task 2: Python — Add 4 MCP tool functions (TDD)

**Files:**
- Test: `tests/test_mcp_server.py:60-86`
- Modify: `prefab_sentinel/mcp_server.py` (insert after line 1241, before `# Inspection tools`)

**Context:** Each `@server.tool()` function follows the existing pattern: thin wrapper that maps MCP parameters to `send_action()` kwargs. The MCP tool name differs from the bridge action name (e.g., `editor_get_blend_shapes` tool calls action `get_blend_shapes`). Parameter name mapping follows the spec's DTO table: Python `name` → C# `blend_shape_name`, Python `weight` → C# `blend_shape_weight`, `prefix` → `filter`.

- [ ] **Step 1: Update test expectations**

In `tests/test_mcp_server.py`, update the `expected` set in `test_all_tools_registered` (line 60-80) to add 4 new tools. Also update `test_tool_count` (line 86) from 41 to 45.

Add these 4 entries to the `expected` set (after the existing editor tools, before `inspect_materials`):

```python
            "editor_get_blend_shapes", "editor_set_blend_shape",
            "editor_list_menu_items", "editor_execute_menu_item",
```

Update tool count:

```python
        self.assertEqual(45, len(tools))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /mnt/d/git/prefab-sentinel && uv run pytest tests/test_mcp_server.py::TestToolRegistration -v`
Expected: FAIL — 4 tools missing

- [ ] **Step 3: Add the 4 MCP tool functions**

Insert the following in `prefab_sentinel/mcp_server.py` after `editor_delete` (line 1241) and before the `# Inspection tools` comment (line 1243):

```python
    # ------------------------------------------------------------------
    # Editor Bridge – Phase 2: BlendShape + Menu
    # ------------------------------------------------------------------

    @server.tool()
    def editor_get_blend_shapes(
        hierarchy_path: str,
        filter: str = "",
    ) -> dict[str, Any]:
        """Get BlendShape names and current weight values from a SkinnedMeshRenderer.

        Args:
            hierarchy_path: Hierarchy path to the GameObject with a SkinnedMeshRenderer.
            filter: Substring filter on BlendShape names (empty = return all).
        """
        return send_action(
            action="get_blend_shapes",
            hierarchy_path=hierarchy_path,
            filter=filter,
        )

    @server.tool()
    def editor_set_blend_shape(
        hierarchy_path: str,
        name: str,
        weight: float,
    ) -> dict[str, Any]:
        """Set a BlendShape weight by name on a SkinnedMeshRenderer (Undo-able).

        Args:
            hierarchy_path: Hierarchy path to the GameObject with a SkinnedMeshRenderer.
            name: BlendShape name (exact match).
            weight: Weight value (0-100).
        """
        return send_action(
            action="set_blend_shape",
            hierarchy_path=hierarchy_path,
            blend_shape_name=name,
            blend_shape_weight=weight,
        )

    @server.tool()
    def editor_list_menu_items(
        prefix: str = "",
    ) -> dict[str, Any]:
        """List Unity Editor menu items registered via [MenuItem] attribute.

        Args:
            prefix: Path prefix filter (e.g. "Tools/", "CONTEXT/"). Empty = all items.
        """
        return send_action(
            action="list_menu_items",
            filter=prefix,
        )

    @server.tool()
    def editor_execute_menu_item(
        menu_path: str,
    ) -> dict[str, Any]:
        """Execute a Unity Editor menu item by path.

        Some menu items may display modal dialogs that block the Editor.
        Dangerous paths (File/New Scene, File/New Project, Assets/Delete) are denied.

        Args:
            menu_path: Full menu path (e.g. "Tools/NDMF/Manual Bake").
        """
        return send_action(
            action="execute_menu_item",
            menu_path=menu_path,
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /mnt/d/git/prefab-sentinel && uv run pytest tests/test_mcp_server.py::TestToolRegistration -v`
Expected: ALL PASS

- [ ] **Step 5: Add delegation tests**

Add the following tests to `tests/test_mcp_server.py` in the `TestEditorBridgeTools` class (after the existing `test_editor_delete_delegates` / `test_vrcsdk_upload_delegates` tests). These verify the parameter mapping from MCP tool to `send_action()`:

```python
    def test_editor_get_blend_shapes_delegates(self) -> None:
        server = create_server()
        with patch("prefab_sentinel.mcp_server.send_action", return_value={"success": True}) as mock_send:
            _run(server.call_tool("editor_get_blend_shapes", {
                "hierarchy_path": "/Avatar/Body", "filter": "vrc.v_",
            }))
        mock_send.assert_called_once_with(
            action="get_blend_shapes",
            hierarchy_path="/Avatar/Body", filter="vrc.v_",
        )

    def test_editor_get_blend_shapes_default_filter(self) -> None:
        server = create_server()
        with patch("prefab_sentinel.mcp_server.send_action", return_value={"success": True}) as mock_send:
            _run(server.call_tool("editor_get_blend_shapes", {
                "hierarchy_path": "/Avatar/Body",
            }))
        mock_send.assert_called_once_with(
            action="get_blend_shapes",
            hierarchy_path="/Avatar/Body", filter="",
        )

    def test_editor_set_blend_shape_delegates(self) -> None:
        server = create_server()
        with patch("prefab_sentinel.mcp_server.send_action", return_value={"success": True}) as mock_send:
            _run(server.call_tool("editor_set_blend_shape", {
                "hierarchy_path": "/Avatar/Body", "name": "vrc.blink", "weight": 75.0,
            }))
        mock_send.assert_called_once_with(
            action="set_blend_shape",
            hierarchy_path="/Avatar/Body",
            blend_shape_name="vrc.blink",
            blend_shape_weight=75.0,
        )

    def test_editor_list_menu_items_delegates(self) -> None:
        server = create_server()
        with patch("prefab_sentinel.mcp_server.send_action", return_value={"success": True}) as mock_send:
            _run(server.call_tool("editor_list_menu_items", {"prefix": "Tools/"}))
        mock_send.assert_called_once_with(action="list_menu_items", filter="Tools/")

    def test_editor_list_menu_items_default_prefix(self) -> None:
        server = create_server()
        with patch("prefab_sentinel.mcp_server.send_action", return_value={"success": True}) as mock_send:
            _run(server.call_tool("editor_list_menu_items", {}))
        mock_send.assert_called_once_with(action="list_menu_items", filter="")

    def test_editor_execute_menu_item_delegates(self) -> None:
        server = create_server()
        with patch("prefab_sentinel.mcp_server.send_action", return_value={"success": True}) as mock_send:
            _run(server.call_tool("editor_execute_menu_item", {
                "menu_path": "Tools/NDMF/Manual Bake",
            }))
        mock_send.assert_called_once_with(
            action="execute_menu_item", menu_path="Tools/NDMF/Manual Bake",
        )
```

- [ ] **Step 6: Run full test suite**

Run: `cd /mnt/d/git/prefab-sentinel && uv run pytest tests/test_editor_bridge.py tests/test_mcp_server.py -v`
Expected: ALL PASS

- [ ] **Step 7: Commit**

```bash
git add prefab_sentinel/mcp_server.py tests/test_mcp_server.py
git commit -m "feat: add 4 Phase 2 MCP tools (BlendShape get/set, Menu list/execute)"
```

---

### Task 3: C# — DTO additions (BlendShapeEntry, MenuItemEntry, request/response fields)

**Files:**
- Modify: `tools/unity/PrefabSentinel.UnityEditorControlBridge.cs`

**Context:** The C# bridge uses flat DTO classes serialized as JSON. `EditorControlRequest` carries input parameters, `EditorControlData` carries response data. New serializable classes are needed for array entries (same pattern as existing `ConsoleLogEntry`, `ChildEntry`, `MaterialSlotEntry`).

- [ ] **Step 1: Add BlendShapeEntry and MenuItemEntry classes**

Insert after `MaterialPropertyEntry` class (after line 154, before `EditorControlData`):

```csharp
        [Serializable]
        public sealed class BlendShapeEntry
        {
            public int index = 0;
            public string name = string.Empty;
            public float weight = 0f;
        }

        [Serializable]
        public sealed class MenuItemEntry
        {
            public string path = string.Empty;
            public string shortcut = string.Empty;
        }
```

- [ ] **Step 2: Add request fields to EditorControlRequest**

Insert before the closing brace of `EditorControlRequest` (before line 108):

```csharp
            // Phase 2: BlendShape
            public string filter = string.Empty;            // name substring filter / menu prefix
            public string blend_shape_name = string.Empty;  // BlendShape name
            public float blend_shape_weight = 0f;           // BlendShape weight (0-100)

            // Phase 2: Menu
            public string menu_path = string.Empty;         // menu item path
```

- [ ] **Step 3: Add response fields to EditorControlData**

Insert before the `// error hint suggestions` comment in `EditorControlData` (before line 194):

```csharp
            // Phase 2: BlendShape
            public BlendShapeEntry[] blend_shapes = Array.Empty<BlendShapeEntry>();
            public string renderer_path = string.Empty;
            public int blend_shape_index = 0;
            public string blend_shape_name = string.Empty;
            public float blend_shape_before = 0f;
            public float blend_shape_after = 0f;

            // Phase 2: Menu
            public MenuItemEntry[] menu_items = Array.Empty<MenuItemEntry>();
```

- [ ] **Step 4: Add 4 actions to SupportedActions**

Add to the `SupportedActions` HashSet (after `"vrcsdk_upload"`):

```csharp
            // Phase 2: BlendShape + Menu
            "get_blend_shapes", "set_blend_shape",
            "list_menu_items", "execute_menu_item",
```

- [ ] **Step 5: Add 4 switch cases to ProcessRequest**

Insert before the `case "vrcsdk_upload":` case (before line 293):

```csharp
                // Phase 2: BlendShape + Menu
                case "get_blend_shapes":
                    response = HandleGetBlendShapes(request);
                    break;
                case "set_blend_shape":
                    response = HandleSetBlendShape(request);
                    break;
                case "list_menu_items":
                    response = HandleListMenuItems(request);
                    break;
                case "execute_menu_item":
                    response = HandleExecuteMenuItem(request);
                    break;
```

- [ ] **Step 6: Add stub handlers (compile-safe)**

Insert before the `// ── Response Builders ──` comment (before line 1396). These stubs will be replaced in Tasks 4 and 5:

```csharp
        // ── Phase 2: BlendShape + Menu ──

        private static EditorControlResponse HandleGetBlendShapes(EditorControlRequest request)
        {
            return BuildError("NOT_IMPLEMENTED", "HandleGetBlendShapes not yet implemented");
        }

        private static EditorControlResponse HandleSetBlendShape(EditorControlRequest request)
        {
            return BuildError("NOT_IMPLEMENTED", "HandleSetBlendShape not yet implemented");
        }

        private static EditorControlResponse HandleListMenuItems(EditorControlRequest request)
        {
            return BuildError("NOT_IMPLEMENTED", "HandleListMenuItems not yet implemented");
        }

        private static EditorControlResponse HandleExecuteMenuItem(EditorControlRequest request)
        {
            return BuildError("NOT_IMPLEMENTED", "HandleExecuteMenuItem not yet implemented");
        }
```

- [ ] **Step 7: Commit**

```bash
git add tools/unity/PrefabSentinel.UnityEditorControlBridge.cs
git commit -m "feat: add Phase 2 DTOs, switch cases, and stub handlers"
```

---

### Task 4: C# — BlendShape handler implementations

**Files:**
- Modify: `tools/unity/PrefabSentinel.UnityEditorControlBridge.cs`

**Context:** Replace the stub handlers from Task 3. The handlers follow the existing pattern: validate required fields → resolve GameObject → get component → perform operation → return structured response. `HandleSetBlendShape` must call `Undo.RecordObject` before mutation (same pattern as `HandleSetMaterialProperty`). Use `SkinnedMeshRenderer.sharedMesh` for name/index resolution and `GetBlendShapeWeight`/`SetBlendShapeWeight` for values.

- [ ] **Step 1: Implement HandleGetBlendShapes**

Replace the `HandleGetBlendShapes` stub with:

```csharp
        private static EditorControlResponse HandleGetBlendShapes(EditorControlRequest request)
        {
            if (string.IsNullOrEmpty(request.hierarchy_path))
                return BuildError("EDITOR_CTRL_MISSING_PATH", "hierarchy_path is required for get_blend_shapes");

            var go = GameObject.Find(request.hierarchy_path);
            if (go == null)
                return BuildError("EDITOR_CTRL_OBJECT_NOT_FOUND",
                    $"GameObject not found: {request.hierarchy_path}");

            var smr = go.GetComponent<SkinnedMeshRenderer>();
            if (smr == null)
                return BuildError("EDITOR_CTRL_NO_SMR",
                    $"No SkinnedMeshRenderer on: {request.hierarchy_path}");

            var mesh = smr.sharedMesh;
            if (mesh == null)
                return BuildError("EDITOR_CTRL_NO_SMR",
                    $"SkinnedMeshRenderer has no mesh: {request.hierarchy_path}");

            int count = mesh.blendShapeCount;
            var entries = new System.Collections.Generic.List<BlendShapeEntry>();
            string filter = request.filter ?? "";

            for (int i = 0; i < count; i++)
            {
                string shapeName = mesh.GetBlendShapeName(i);
                if (filter.Length > 0 && shapeName.IndexOf(filter, System.StringComparison.OrdinalIgnoreCase) < 0)
                    continue;
                entries.Add(new BlendShapeEntry
                {
                    index = i,
                    name = shapeName,
                    weight = smr.GetBlendShapeWeight(i),
                });
            }

            return BuildSuccess("EDITOR_CTRL_BLEND_SHAPES_OK",
                $"Found {entries.Count} blend shapes (total: {count})",
                data: new EditorControlData
                {
                    blend_shapes = entries.ToArray(),
                    total_entries = count,
                    renderer_path = GetRelativePath(go.transform, smr.transform),
                    read_only = true,
                    executed = true,
                });
        }

        /// <summary>Returns the relative path from root to target (or target name if same).</summary>
        private static string GetRelativePath(Transform root, Transform target)
        {
            if (root == target) return target.name;
            var parts = new System.Collections.Generic.List<string>();
            var current = target;
            while (current != null && current != root)
            {
                parts.Add(current.name);
                current = current.parent;
            }
            parts.Reverse();
            return string.Join("/", parts);
        }
```

- [ ] **Step 2: Implement HandleSetBlendShape**

Replace the `HandleSetBlendShape` stub with:

```csharp
        private static EditorControlResponse HandleSetBlendShape(EditorControlRequest request)
        {
            if (string.IsNullOrEmpty(request.hierarchy_path))
                return BuildError("EDITOR_CTRL_MISSING_PATH", "hierarchy_path is required for set_blend_shape");
            if (string.IsNullOrEmpty(request.blend_shape_name))
                return BuildError("EDITOR_CTRL_MISSING_PROPERTY", "blend_shape_name is required for set_blend_shape");

            var go = GameObject.Find(request.hierarchy_path);
            if (go == null)
                return BuildError("EDITOR_CTRL_OBJECT_NOT_FOUND",
                    $"GameObject not found: {request.hierarchy_path}");

            var smr = go.GetComponent<SkinnedMeshRenderer>();
            if (smr == null)
                return BuildError("EDITOR_CTRL_NO_SMR",
                    $"No SkinnedMeshRenderer on: {request.hierarchy_path}");

            var mesh = smr.sharedMesh;
            if (mesh == null)
                return BuildError("EDITOR_CTRL_NO_SMR",
                    $"SkinnedMeshRenderer has no mesh: {request.hierarchy_path}");

            int index = mesh.GetBlendShapeIndex(request.blend_shape_name);
            if (index < 0)
                return BuildError("EDITOR_CTRL_BLENDSHAPE_NOT_FOUND",
                    $"BlendShape not found: {request.blend_shape_name}");

            float before = smr.GetBlendShapeWeight(index);
            float weight = Mathf.Clamp(request.blend_shape_weight, 0f, 100f);

            Undo.RecordObject(smr, $"Set BlendShape {request.blend_shape_name}");
            smr.SetBlendShapeWeight(index, weight);

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
        }
```

- [ ] **Step 3: Commit**

```bash
git add tools/unity/PrefabSentinel.UnityEditorControlBridge.cs
git commit -m "feat: implement HandleGetBlendShapes and HandleSetBlendShape"
```

---

### Task 5: C# — Menu handler implementations with deny-list

**Files:**
- Modify: `tools/unity/PrefabSentinel.UnityEditorControlBridge.cs`

**Context:** `HandleListMenuItems` uses reflection to scan all loaded assemblies for `[MenuItem]` attributes. Must handle `ReflectionTypeLoadException` by falling back to `ex.Types` (filtering nulls). Validate methods (`[MenuItem("path", true)]` second arg = true) must be excluded. `HandleExecuteMenuItem` wraps `EditorApplication.ExecuteMenuItem()` with a deny-list of dangerous paths. The deny-list is a static string array checked via `StartsWith`.

- [ ] **Step 1: Add deny-list constant**

Insert before the handler methods (near the `// ── Phase 2` comment):

```csharp
        // Deny-list: menu paths that could destroy data if executed by automation
        private static readonly string[] MenuDenyPrefixes = new string[]
        {
            "File/New Scene",
            "File/New Project",
            "Assets/Delete",
        };
```

- [ ] **Step 2: Implement HandleListMenuItems**

Replace the `HandleListMenuItems` stub with:

```csharp
        private static EditorControlResponse HandleListMenuItems(EditorControlRequest request)
        {
            string prefix = request.filter ?? "";
            var items = new System.Collections.Generic.List<MenuItemEntry>();
            int totalScanned = 0;  // pre-filter count (all non-validate [MenuItem])

            foreach (var assembly in System.AppDomain.CurrentDomain.GetAssemblies())
            {
                System.Type[] types;
                try
                {
                    types = assembly.GetTypes();
                }
                catch (System.Reflection.ReflectionTypeLoadException ex)
                {
                    types = System.Array.FindAll(ex.Types, t => t != null);
                }

                foreach (var type in types)
                {
                    var methods = type.GetMethods(
                        System.Reflection.BindingFlags.Static |
                        System.Reflection.BindingFlags.Public |
                        System.Reflection.BindingFlags.NonPublic);

                    foreach (var method in methods)
                    {
                        var attrs = method.GetCustomAttributes(typeof(UnityEditor.MenuItem), false);
                        foreach (UnityEditor.MenuItem attr in attrs)
                        {
                            // Skip validate methods
                            if (attr.validate)
                                continue;

                            totalScanned++;
                            string menuPath = attr.menuItem;
                            if (prefix.Length > 0 && !menuPath.StartsWith(prefix, System.StringComparison.Ordinal))
                                continue;

                            items.Add(new MenuItemEntry
                            {
                                path = menuPath,
                                shortcut = ExtractShortcut(menuPath),
                            });
                        }
                    }
                }
            }

            // Sort by path for stable output
            items.Sort((a, b) => string.Compare(a.path, b.path, System.StringComparison.Ordinal));

            return BuildSuccess("EDITOR_CTRL_MENU_LIST_OK",
                $"Found {items.Count} menu items (total: {totalScanned})",
                data: new EditorControlData
                {
                    menu_items = items.ToArray(),
                    total_entries = totalScanned,  // pre-filter count per spec
                    read_only = true,
                    executed = true,
                });
        }

        /// <summary>Extract keyboard shortcut from MenuItem path (e.g. "Tools/Foo %t" → "%t").</summary>
        private static string ExtractShortcut(string menuPath)
        {
            // Unity shortcut chars: % (Cmd/Ctrl), # (Shift), & (Alt), _ (no modifier)
            int spaceIdx = menuPath.LastIndexOf(' ');
            if (spaceIdx < 0) return "";
            string candidate = menuPath.Substring(spaceIdx + 1);
            if (candidate.Length > 0 && (candidate[0] == '%' || candidate[0] == '#' || candidate[0] == '&' || candidate[0] == '_'))
                return candidate;
            return "";
        }
```

- [ ] **Step 3: Implement HandleExecuteMenuItem**

Replace the `HandleExecuteMenuItem` stub with:

```csharp
        private static EditorControlResponse HandleExecuteMenuItem(EditorControlRequest request)
        {
            if (string.IsNullOrEmpty(request.menu_path))
                return BuildError("EDITOR_CTRL_MISSING_PATH", "menu_path is required for execute_menu_item");

            // Check deny-list
            foreach (var denied in MenuDenyPrefixes)
            {
                if (request.menu_path.StartsWith(denied, System.StringComparison.Ordinal))
                    return BuildError("EDITOR_CTRL_MENU_DENIED",
                        $"Menu item denied by safety policy: {request.menu_path}");
            }

            bool result = EditorApplication.ExecuteMenuItem(request.menu_path);
            if (!result)
                return BuildError("EDITOR_CTRL_MENU_NOT_FOUND",
                    $"Menu item not found or not executable: {request.menu_path}");

            return BuildSuccess("EDITOR_CTRL_MENU_EXEC_OK",
                $"Menu item executed: {request.menu_path}");
        }
```

- [ ] **Step 4: Run Python tests to verify no regressions**

Run: `cd /mnt/d/git/prefab-sentinel && uv run pytest tests/test_editor_bridge.py tests/test_mcp_server.py -v`
Expected: ALL PASS (C# changes don't affect Python tests, but verifies nothing was accidentally broken)

- [ ] **Step 5: Commit**

```bash
git add tools/unity/PrefabSentinel.UnityEditorControlBridge.cs
git commit -m "feat: implement HandleListMenuItems and HandleExecuteMenuItem with deny-list"
```

---

### Task 6: README update

**Files:**
- Modify: `README.md`

**Context:** The README documents all available MCP tools. The 4 new tools need to be added to the Editor Bridge tools section. Follow the existing table format.

- [ ] **Step 1: Find the Editor Bridge tools section in README**

Search for the section listing `editor_screenshot`, `editor_select`, etc. It will be a markdown table or list.

- [ ] **Step 2: Add 4 new tools**

Add these entries to the Editor Bridge tools listing, grouped as "Phase 2: BlendShape + Menu":

| Tool | Description |
|------|-------------|
| `editor_get_blend_shapes` | Get BlendShape names and weights from a SkinnedMeshRenderer |
| `editor_set_blend_shape` | Set a BlendShape weight by name (Undo-able) |
| `editor_list_menu_items` | List `[MenuItem]` entries via reflection |
| `editor_execute_menu_item` | Execute a menu item by path (with deny-list) |

- [ ] **Step 3: Run full test suite**

Run: `cd /mnt/d/git/prefab-sentinel && uv run pytest tests/ -v`
Expected: ALL PASS (no regressions)

- [ ] **Step 4: Commit**

```bash
git add README.md
git commit -m "docs: add Phase 2 editor tools to README"
```
