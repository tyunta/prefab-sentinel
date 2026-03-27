# Editor Set Property + Save As Prefab Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `editor_set_property` (SerializedObject API 経由のフィールド設定、UdonSharp 対応) と `editor_save_as_prefab` (GO → Prefab/Variant 保存) の 2 MCP ツール。

**Architecture:** C# EditorControlBridge に 2 ハンドラ追加 (HandleEditorSetProperty, HandleSaveAsPrefab) + Python MCP ラッパー。既存の HandleEditorRename/HandleEditorAddComponent と同パターン。

**Tech Stack:** C# (Unity Editor API, SerializedObject, PrefabUtility), Python 3.11+ (FastMCP)

**Spec:** `docs/superpowers/specs/2026-03-27-editor-set-property-save-prefab-design.md`

---

## File Structure

### Modified Files

| File | Responsibility |
|------|---------------|
| `tools/unity/PrefabSentinel.UnityEditorControlBridge.cs` | `object_reference` フィールド追加、SupportedActions 2件追加、dispatch 2件追加、HandleEditorSetProperty + ResolveObjectReference、HandleSaveAsPrefab |
| `prefab_sentinel/mcp_server.py` | MCP ツール 2件追加 |
| `prefab_sentinel/editor_bridge.py` | SUPPORTED_ACTIONS に 2件追加 |
| `tests/test_editor_bridge.py` | SUPPORTED_ACTIONS テスト更新 |

---

## Task 1: C# — Request field + SupportedActions + dispatch scaffolding

**Files:**
- Modify: `tools/unity/PrefabSentinel.UnityEditorControlBridge.cs:23-53,127-130,368-385`

- [ ] **Step 1: Add `object_reference` field to EditorControlRequest**

In `EditorControlRequest` class, after line 129 (`public string component_type = string.Empty;`), before the closing `}` at line 130:

```csharp
            // Phase 4: Rename + AddComponent + Udon
            public string new_name = string.Empty;
            public string component_type = string.Empty;

            // Phase 5: SetProperty + SaveAsPrefab
            public string object_reference = string.Empty;
        }
```

- [ ] **Step 2: Add 2 actions to SupportedActions**

After line 52 (`"create_udon_program_asset",`), before the closing `};` at line 53:

```csharp
            // Phase 4: Rename + AddComponent + Udon
            "editor_rename",
            "editor_add_component",
            "create_udon_program_asset",
            // Phase 5: SetProperty + SaveAsPrefab
            "editor_set_property",
            "save_as_prefab",
        };
```

- [ ] **Step 3: Add dispatch cases**

In the switch block, after the `create_udon_program_asset` case (line 376) and before `case "vrcsdk_upload":` (line 377):

```csharp
                case "create_udon_program_asset":
                    response = HandleCreateUdonProgramAsset(request);
                    break;
                case "editor_set_property":
                    response = HandleEditorSetProperty(request);
                    break;
                case "save_as_prefab":
                    response = HandleSaveAsPrefab(request);
                    break;
                case "vrcsdk_upload":
```

- [ ] **Step 4: Add stub handlers (compile check)**

After `HandleCreateUdonProgramAsset` method (around line 1950), add stubs:

```csharp
        // ── Phase 5: SetProperty + SaveAsPrefab ──

        private static EditorControlResponse HandleEditorSetProperty(EditorControlRequest request)
        {
            return BuildError("EDITOR_CTRL_SET_PROP_NOT_IMPL", "Not yet implemented.");
        }

        private static EditorControlResponse HandleSaveAsPrefab(EditorControlRequest request)
        {
            return BuildError("EDITOR_CTRL_SAVE_PREFAB_NOT_IMPL", "Not yet implemented.");
        }
```

- [ ] **Step 5: Compile check**

Run: `python3 -m py_compile prefab_sentinel/editor_bridge.py`
Expected: no errors (Python side unchanged yet).

The C# compile check happens in Unity — verify no syntax errors by reviewing the changes.

- [ ] **Step 6: Commit**

```bash
git add tools/unity/PrefabSentinel.UnityEditorControlBridge.cs
git commit -m "feat(bridge): scaffold editor_set_property and save_as_prefab actions"
```

---

## Task 2: C# — HandleEditorSetProperty with value parsing + ObjectReference resolution

**Files:**
- Modify: `tools/unity/PrefabSentinel.UnityEditorControlBridge.cs`

- [ ] **Step 1: Add ResolveObjectReference helper**

After the stub `HandleEditorSetProperty`, add:

```csharp
        private static UnityEngine.Object ResolveObjectReference(string reference)
        {
            if (string.IsNullOrEmpty(reference))
                return null;

            // 1. Check for component specifier (path:ComponentType)
            string goPath = reference;
            string componentName = null;
            int colonIdx = reference.LastIndexOf(':');
            if (colonIdx > 0)
            {
                goPath = reference.Substring(0, colonIdx);
                componentName = reference.Substring(colonIdx + 1);
            }

            // 2. Try scene hierarchy
            var go = GameObject.Find(goPath);
            if (go != null)
            {
                if (componentName != null)
                {
                    var compType = ResolveComponentType(componentName);
                    if (compType != null)
                        return go.GetComponent(compType);
                    return null;
                }
                return go;
            }

            // 3. Try asset path
            return AssetDatabase.LoadAssetAtPath<UnityEngine.Object>(reference);
        }
```

- [ ] **Step 2: Implement HandleEditorSetProperty**

Replace the stub with the full implementation:

```csharp
        private static EditorControlResponse HandleEditorSetProperty(EditorControlRequest request)
        {
            // ── Validation ──
            if (string.IsNullOrEmpty(request.hierarchy_path))
                return BuildError("EDITOR_CTRL_SET_PROP_NO_PATH", "hierarchy_path is required.");
            if (string.IsNullOrEmpty(request.component_type))
                return BuildError("EDITOR_CTRL_SET_PROP_NO_COMP", "component_type is required.");
            if (string.IsNullOrEmpty(request.property_name))
                return BuildError("EDITOR_CTRL_SET_PROP_NO_FIELD", "property_name is required.");

            bool hasValue = !string.IsNullOrEmpty(request.property_value);
            bool hasRef = !string.IsNullOrEmpty(request.object_reference);
            if (!hasValue && !hasRef)
                return BuildError("EDITOR_CTRL_SET_PROP_NO_VALUE",
                    "Either property_value or object_reference is required.");

            // ── Resolve target ──
            var go = GameObject.Find(request.hierarchy_path);
            if (go == null)
                return BuildError("EDITOR_CTRL_SET_PROP_NOT_FOUND",
                    $"GameObject not found: {request.hierarchy_path}");

            System.Type compType = ResolveComponentType(request.component_type);
            if (compType == null)
                return BuildError("EDITOR_CTRL_SET_PROP_COMP_NOT_FOUND",
                    $"Component type not found: {request.component_type}");

            var component = go.GetComponent(compType);
            if (component == null)
                return BuildError("EDITOR_CTRL_SET_PROP_COMP_NOT_FOUND",
                    $"Component {request.component_type} not found on {request.hierarchy_path}");

            // ── Find property ──
            var so = new SerializedObject(component);
            var prop = so.FindProperty(request.property_name);
            if (prop == null)
                return BuildError("EDITOR_CTRL_SET_PROP_FIELD_NOT_FOUND",
                    $"Property not found: {request.property_name} on {request.component_type}");

            // ── Set value by type ──
            string v = request.property_value;
            try
            {
                switch (prop.propertyType)
                {
                    case SerializedPropertyType.Integer:
                        prop.intValue = int.Parse(v, System.Globalization.CultureInfo.InvariantCulture);
                        break;
                    case SerializedPropertyType.Float:
                        prop.floatValue = float.Parse(v, System.Globalization.CultureInfo.InvariantCulture);
                        break;
                    case SerializedPropertyType.Boolean:
                        prop.boolValue = bool.Parse(v);
                        break;
                    case SerializedPropertyType.String:
                        prop.stringValue = v;
                        break;
                    case SerializedPropertyType.Enum:
                    {
                        // Try name match first, then numeric index
                        int idx = System.Array.IndexOf(prop.enumNames, v);
                        if (idx >= 0)
                            prop.enumValueIndex = idx;
                        else if (int.TryParse(v, out int numIdx))
                            prop.enumValueIndex = numIdx;
                        else
                            return BuildError("EDITOR_CTRL_SET_PROP_TYPE_MISMATCH",
                                $"Enum value '{v}' not found. Valid: {string.Join(", ", prop.enumNames)}");
                        break;
                    }
                    case SerializedPropertyType.Color:
                    {
                        var parts = v.Split(',');
                        if (parts.Length < 3)
                            return BuildError("EDITOR_CTRL_SET_PROP_TYPE_MISMATCH",
                                "Color requires 3 or 4 comma-separated floats (r,g,b[,a]).");
                        float r = float.Parse(parts[0].Trim(), System.Globalization.CultureInfo.InvariantCulture);
                        float g = float.Parse(parts[1].Trim(), System.Globalization.CultureInfo.InvariantCulture);
                        float b = float.Parse(parts[2].Trim(), System.Globalization.CultureInfo.InvariantCulture);
                        float a = parts.Length >= 4
                            ? float.Parse(parts[3].Trim(), System.Globalization.CultureInfo.InvariantCulture)
                            : 1f;
                        prop.colorValue = new Color(r, g, b, a);
                        break;
                    }
                    case SerializedPropertyType.Vector2:
                    {
                        var parts = v.Split(',');
                        if (parts.Length < 2)
                            return BuildError("EDITOR_CTRL_SET_PROP_TYPE_MISMATCH",
                                "Vector2 requires 2 comma-separated floats (x,y).");
                        prop.vector2Value = new Vector2(
                            float.Parse(parts[0].Trim(), System.Globalization.CultureInfo.InvariantCulture),
                            float.Parse(parts[1].Trim(), System.Globalization.CultureInfo.InvariantCulture));
                        break;
                    }
                    case SerializedPropertyType.Vector3:
                    {
                        var parts = v.Split(',');
                        if (parts.Length < 3)
                            return BuildError("EDITOR_CTRL_SET_PROP_TYPE_MISMATCH",
                                "Vector3 requires 3 comma-separated floats (x,y,z).");
                        prop.vector3Value = new Vector3(
                            float.Parse(parts[0].Trim(), System.Globalization.CultureInfo.InvariantCulture),
                            float.Parse(parts[1].Trim(), System.Globalization.CultureInfo.InvariantCulture),
                            float.Parse(parts[2].Trim(), System.Globalization.CultureInfo.InvariantCulture));
                        break;
                    }
                    case SerializedPropertyType.Vector4:
                    {
                        var parts = v.Split(',');
                        if (parts.Length < 4)
                            return BuildError("EDITOR_CTRL_SET_PROP_TYPE_MISMATCH",
                                "Vector4 requires 4 comma-separated floats (x,y,z,w).");
                        prop.vector4Value = new Vector4(
                            float.Parse(parts[0].Trim(), System.Globalization.CultureInfo.InvariantCulture),
                            float.Parse(parts[1].Trim(), System.Globalization.CultureInfo.InvariantCulture),
                            float.Parse(parts[2].Trim(), System.Globalization.CultureInfo.InvariantCulture),
                            float.Parse(parts[3].Trim(), System.Globalization.CultureInfo.InvariantCulture));
                        break;
                    }
                    case SerializedPropertyType.ObjectReference:
                    {
                        string refPath = hasRef ? request.object_reference : v;
                        var obj = ResolveObjectReference(refPath);
                        if (obj == null)
                            return BuildError("EDITOR_CTRL_SET_PROP_REF_NOT_FOUND",
                                $"Object reference not found: {refPath}");
                        prop.objectReferenceValue = obj;
                        break;
                    }
                    default:
                        return BuildError("EDITOR_CTRL_SET_PROP_TYPE_MISMATCH",
                            $"Unsupported property type: {prop.propertyType}");
                }
            }
            catch (System.FormatException ex)
            {
                return BuildError("EDITOR_CTRL_SET_PROP_TYPE_MISMATCH",
                    $"Failed to parse value '{v}' for {prop.propertyType}: {ex.Message}");
            }

            so.ApplyModifiedProperties();

            var resp = BuildSuccess("EDITOR_CTRL_SET_PROP_OK",
                $"Set {request.property_name} on {request.component_type} at {request.hierarchy_path}",
                data: new EditorControlData
                {
                    selected_object = go.name,
                    executed = true,
                    read_only = false,
                });
            resp.diagnostics = new[] { new EditorControlDiagnostic
            {
                detail = $"Property type: {prop.propertyType}. Save the scene to persist.",
                evidence = "SerializedObject.ApplyModifiedProperties"
            }};
            return resp;
        }
```

- [ ] **Step 3: Commit**

```bash
git add tools/unity/PrefabSentinel.UnityEditorControlBridge.cs
git commit -m "feat(bridge): implement HandleEditorSetProperty with type-aware parsing"
```

---

## Task 3: C# — HandleSaveAsPrefab

**Files:**
- Modify: `tools/unity/PrefabSentinel.UnityEditorControlBridge.cs`

- [ ] **Step 1: Implement HandleSaveAsPrefab**

Replace the stub with the full implementation:

```csharp
        private static EditorControlResponse HandleSaveAsPrefab(EditorControlRequest request)
        {
            if (string.IsNullOrEmpty(request.hierarchy_path))
                return BuildError("EDITOR_CTRL_SAVE_PREFAB_NO_PATH", "hierarchy_path is required.");
            if (string.IsNullOrEmpty(request.asset_path))
                return BuildError("EDITOR_CTRL_SAVE_PREFAB_NO_OUTPUT", "asset_path is required.");
            if (!request.asset_path.EndsWith(".prefab", System.StringComparison.OrdinalIgnoreCase))
                return BuildError("EDITOR_CTRL_SAVE_PREFAB_BAD_EXT",
                    $"asset_path must end with .prefab: {request.asset_path}");

            var go = GameObject.Find(request.hierarchy_path);
            if (go == null)
                return BuildError("EDITOR_CTRL_SAVE_PREFAB_NOT_FOUND",
                    $"GameObject not found: {request.hierarchy_path}");

            // Ensure output directory exists
            string dir = System.IO.Path.GetDirectoryName(request.asset_path);
            if (!string.IsNullOrEmpty(dir) && !System.IO.Directory.Exists(dir))
                System.IO.Directory.CreateDirectory(dir);

            // Detect if this will become a Variant
            bool isVariant = PrefabUtility.IsPartOfPrefabInstance(go);
            string basePrefabPath = "";
            if (isVariant)
            {
                var baseObj = PrefabUtility.GetCorrespondingObjectFromSource(go);
                if (baseObj != null)
                    basePrefabPath = AssetDatabase.GetAssetPath(baseObj);
            }

            bool success;
            PrefabUtility.SaveAsPrefabAsset(go, request.asset_path, out success);
            if (!success)
                return BuildError("EDITOR_CTRL_SAVE_PREFAB_FAILED",
                    $"SaveAsPrefabAsset failed for: {request.asset_path}");

            string kind = isVariant ? "Prefab Variant" : "Prefab";
            var resp = BuildSuccess("EDITOR_CTRL_SAVE_PREFAB_OK",
                $"Saved {request.hierarchy_path} as {kind}: {request.asset_path}",
                data: new EditorControlData
                {
                    output_path = request.asset_path,
                    asset_path = basePrefabPath,
                    executed = true,
                    read_only = false,
                });

            var diags = new System.Collections.Generic.List<EditorControlDiagnostic>();
            diags.Add(new EditorControlDiagnostic
            {
                detail = $"Created as {kind}.",
                evidence = "PrefabUtility.SaveAsPrefabAsset"
            });
            if (isVariant && !string.IsNullOrEmpty(basePrefabPath))
                diags.Add(new EditorControlDiagnostic
                {
                    detail = $"Base Prefab: {basePrefabPath}",
                    evidence = "PrefabUtility.GetCorrespondingObjectFromSource"
                });
            resp.diagnostics = diags.ToArray();
            return resp;
        }
```

- [ ] **Step 2: Remove stub (if still present)**

Delete the stub `HandleSaveAsPrefab` that was added in Task 1 Step 4. The full implementation replaces it.

- [ ] **Step 3: Commit**

```bash
git add tools/unity/PrefabSentinel.UnityEditorControlBridge.cs
git commit -m "feat(bridge): implement HandleSaveAsPrefab with Variant auto-detection"
```

---

## Task 4: Python — SUPPORTED_ACTIONS + test update

**Files:**
- Modify: `prefab_sentinel/editor_bridge.py:35-67`
- Modify: `tests/test_editor_bridge.py:167-198`

- [ ] **Step 1: Add actions to SUPPORTED_ACTIONS**

In `prefab_sentinel/editor_bridge.py`, after line 65 (`"create_udon_program_asset",`):

```python
        # Phase 4: Rename + AddComponent + Udon
        "editor_rename",
        "editor_add_component",
        "create_udon_program_asset",
        # Phase 5: SetProperty + SaveAsPrefab
        "editor_set_property",
        "save_as_prefab",
    }
)
```

- [ ] **Step 2: Update test_all_actions_present**

In `tests/test_editor_bridge.py`, after line 196 (`"create_udon_program_asset",`):

```python
            "editor_rename",
            "editor_add_component",
            "create_udon_program_asset",
            "editor_set_property",
            "save_as_prefab",
        }
```

- [ ] **Step 3: Run test**

Run: `uv run python -m unittest tests.test_editor_bridge.TestSupportedActions.test_all_actions_present -v`
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add prefab_sentinel/editor_bridge.py tests/test_editor_bridge.py
git commit -m "feat(bridge): add editor_set_property and save_as_prefab to SUPPORTED_ACTIONS"
```

---

## Task 5: Python — MCP tools

**Files:**
- Modify: `prefab_sentinel/mcp_server.py` (insert after line 1418, before the `# Inspection tools` section)

- [ ] **Step 1: Add editor_set_property MCP tool**

After `editor_execute_menu_item` (line 1418), add:

```python
    @server.tool()
    def editor_set_property(
        hierarchy_path: str,
        component_type: str,
        property_name: str,
        value: str = "",
        object_reference: str = "",
    ) -> dict[str, Any]:
        """Set a serialized property on a component via Unity's SerializedObject API.

        Supports all SerializedProperty types including UdonSharp fields.
        Type is auto-detected from the property. Use value for primitives/enum,
        object_reference for ObjectReference fields.

        For object_reference, specify a hierarchy path (e.g. "/ToggleTarget")
        for scene objects, or an asset path (e.g. "Assets/Materials/Red.mat")
        for project assets. Append :ComponentType to reference a specific
        component (e.g. "/MyObj:AudioSource").

        Args:
            hierarchy_path: Hierarchy path to the GameObject.
            component_type: Component type name (simple or fully qualified).
            property_name: SerializedProperty path (e.g. "targetObject", "m_Speed").
            value: Value for primitive/enum properties (auto-parsed by type).
            object_reference: Hierarchy path or asset path for ObjectReference properties.
        """
        kwargs: dict[str, Any] = {
            "hierarchy_path": hierarchy_path,
            "component_type": component_type,
            "property_name": property_name,
        }
        if object_reference:
            kwargs["object_reference"] = object_reference
        else:
            kwargs["property_value"] = value
        return send_action(action="editor_set_property", **kwargs)
```

- [ ] **Step 2: Add editor_save_as_prefab MCP tool**

Immediately after `editor_set_property`:

```python
    @server.tool()
    def editor_save_as_prefab(
        hierarchy_path: str,
        asset_path: str,
    ) -> dict[str, Any]:
        """Save a scene GameObject as a Prefab or Prefab Variant asset.

        If the GameObject is a Prefab instance (connected to a base),
        the result is automatically a Prefab Variant.
        If it's a plain GameObject, a new original Prefab is created.

        Args:
            hierarchy_path: Hierarchy path to the GameObject to save.
            asset_path: Output .prefab path (e.g. "Assets/Prefabs/MyObj.prefab").
        """
        return send_action(
            action="save_as_prefab",
            hierarchy_path=hierarchy_path,
            asset_path=asset_path,
        )
```

- [ ] **Step 3: Compile check**

Run: `python3 -m compileall prefab_sentinel/mcp_server.py`
Expected: no errors.

- [ ] **Step 4: Commit**

```bash
git add prefab_sentinel/mcp_server.py
git commit -m "feat(mcp): add editor_set_property and editor_save_as_prefab tools"
```

---

## Task 6: Verification

**Files:** (none modified)

- [ ] **Step 1: Run full test suite**

Run: `uv run --extra test python scripts/run_unit_tests.py`
Expected: all tests pass (previous count + 0 new = same count, no regressions).

- [ ] **Step 2: Lint check**

Run: `uv run ruff check prefab_sentinel/editor_bridge.py prefab_sentinel/mcp_server.py tests/test_editor_bridge.py`
Expected: All checks passed.

- [ ] **Step 3: Verify MCP tool registration**

Run: `uv run python -c "from prefab_sentinel.mcp_server import create_server; s = create_server(); print(len(s._tool_manager._tools))"`
Expected: previous count + 2 (should be 51).

- [ ] **Step 4: Verify C# compiles (review)**

Verify `tools/unity/PrefabSentinel.UnityEditorControlBridge.cs` has no syntax errors by checking:
- All braces matched
- All switch cases have `break`
- `using` statements include what's needed (SerializedObject, SerializedPropertyType, PrefabUtility are in UnityEditor namespace, already imported)

- [ ] **Step 5: Update README MCP tool table**

Add 2 new tools to the MCP tool table in `README.md`:

| Tool | Description |
|------|-------------|
| `editor_set_property` | Set a serialized property on a component (supports UdonSharp) |
| `editor_save_as_prefab` | Save a scene GameObject as Prefab or Prefab Variant |

- [ ] **Step 6: Final commit**

```bash
git add README.md
git commit -m "docs: add editor_set_property and editor_save_as_prefab to README"
```
