# Editor Bridge Batch Operations Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add 6 new MCP tools (create_empty, create_primitive, batch_create, batch_set_property, open_scene, save_scene) and extend editor_add_component with initial properties support, reducing MCP round-trips for scene construction from ~500 to ~30.

**Architecture:** C# EditorControlBridge handlers + Python MCP wrappers. Batch operations use JSON string fields parsed by handlers, wrapped in Undo groups. create_empty/create_primitive are standalone handlers. open_scene/save_scene delegate to EditorSceneManager.

**Tech Stack:** C# (Unity Editor API, EditorSceneManager, PrefabUtility, JsonUtility), Python 3.11+ (FastMCP)

**Spec:** `docs/superpowers/specs/2026-03-27-editor-bridge-batch-operations-design.md`

---

## File Structure

### Modified Files

| File | Responsibility |
|------|---------------|
| `tools/unity/PrefabSentinel.UnityEditorControlBridge.cs` | 4 new request fields, 6 new SupportedActions, 6 new handlers, add_component extension, 3 helper DTOs |
| `prefab_sentinel/mcp_server.py` | 6 new MCP tools, add_component signature extension |
| `prefab_sentinel/editor_bridge.py` | SUPPORTED_ACTIONS +6 |
| `tests/test_editor_bridge.py` | SUPPORTED_ACTIONS test |
| `tests/test_mcp_server.py` | Tool registration test (52 → 58) |
| `README.md` | Tool table update |

---

## Task 1: C# — Request fields + SupportedActions + dispatch + helper DTOs

**Files:**
- Modify: `tools/unity/PrefabSentinel.UnityEditorControlBridge.cs`

- [ ] **Step 1: Add request fields**

After line 136 (`public string object_reference = string.Empty;`), before the closing `}`:

```csharp
            // Phase 5: SetProperty + SaveAsPrefab
            public string object_reference = string.Empty;

            // Phase 6: Batch Operations + Scene
            public string primitive_type = string.Empty;
            public string scale = string.Empty;
            public string rotation = string.Empty;
            public string batch_objects_json = string.Empty;
            public string batch_operations_json = string.Empty;
            public string properties_json = string.Empty;
            public string open_scene_mode = "single";
        }
```

- [ ] **Step 2: Add 6 actions to SupportedActions**

After line 56 (`"editor_set_parent",`):

```csharp
            "editor_set_parent",
            // Phase 6: Batch Operations + Scene
            "editor_create_empty",
            "editor_create_primitive",
            "editor_batch_create",
            "editor_batch_set_property",
            "editor_open_scene",
            "editor_save_scene",
        };
```

- [ ] **Step 3: Add dispatch cases**

After `editor_set_parent` case (line 392), before `case "vrcsdk_upload":`:

```csharp
                case "editor_set_parent":
                    response = HandleEditorSetParent(request);
                    break;
                case "editor_create_empty":
                    response = HandleEditorCreateEmpty(request);
                    break;
                case "editor_create_primitive":
                    response = HandleEditorCreatePrimitive(request);
                    break;
                case "editor_batch_create":
                    response = HandleEditorBatchCreate(request);
                    break;
                case "editor_batch_set_property":
                    response = HandleEditorBatchSetProperty(request);
                    break;
                case "editor_open_scene":
                    response = HandleEditorOpenScene(request);
                    break;
                case "editor_save_scene":
                    response = HandleEditorSaveScene(request);
                    break;
                case "vrcsdk_upload":
```

- [ ] **Step 4: Add helper DTOs for batch JSON parsing**

Before the `// ── Response Builders ──` section (around line 2150), add:

```csharp
        // ── Batch Operation DTOs ──

        [Serializable]
        private sealed class BatchObjectSpec
        {
            public string type = string.Empty;
            public string name = string.Empty;
            public string parent = string.Empty;
            public string position = string.Empty;
            public string scale = string.Empty;
            public string rotation = string.Empty;
            public string components = string.Empty;
        }

        [Serializable]
        private sealed class BatchObjectArray
        {
            public BatchObjectSpec[] items;
        }

        [Serializable]
        private sealed class BatchSetPropertyOp
        {
            public string hierarchy_path = string.Empty;
            public string component_type = string.Empty;
            public string property_name = string.Empty;
            public string value = string.Empty;
            public string object_reference = string.Empty;
        }

        [Serializable]
        private sealed class BatchSetPropertyArray
        {
            public BatchSetPropertyOp[] items;
        }

        [Serializable]
        private sealed class PropertyEntry
        {
            public string name = string.Empty;
            public string value = string.Empty;
            public string object_reference = string.Empty;
        }

        [Serializable]
        private sealed class PropertyEntryArray
        {
            public PropertyEntry[] items;
        }
```

- [ ] **Step 5: Add stubs for all 6 handlers**

After `HandleEditorSetParent` (around line 1935), before `ResolveComponentType`:

```csharp
        // ── Phase 6: Batch Operations + Scene ──

        private static EditorControlResponse HandleEditorCreateEmpty(EditorControlRequest request)
        {
            return BuildError("NOT_IMPL", "Not yet implemented.");
        }

        private static EditorControlResponse HandleEditorCreatePrimitive(EditorControlRequest request)
        {
            return BuildError("NOT_IMPL", "Not yet implemented.");
        }

        private static EditorControlResponse HandleEditorBatchCreate(EditorControlRequest request)
        {
            return BuildError("NOT_IMPL", "Not yet implemented.");
        }

        private static EditorControlResponse HandleEditorBatchSetProperty(EditorControlRequest request)
        {
            return BuildError("NOT_IMPL", "Not yet implemented.");
        }

        private static EditorControlResponse HandleEditorOpenScene(EditorControlRequest request)
        {
            return BuildError("NOT_IMPL", "Not yet implemented.");
        }

        private static EditorControlResponse HandleEditorSaveScene(EditorControlRequest request)
        {
            return BuildError("NOT_IMPL", "Not yet implemented.");
        }

```

- [ ] **Step 6: Commit**

```bash
git add tools/unity/PrefabSentinel.UnityEditorControlBridge.cs
git commit -m "feat(bridge): scaffold 6 batch/scene actions with request fields and DTOs"
```

---

## Task 2: C# — HandleEditorCreateEmpty + HandleEditorCreatePrimitive

**Files:**
- Modify: `tools/unity/PrefabSentinel.UnityEditorControlBridge.cs`

- [ ] **Step 1: Add ParseVector3 helper**

Before the batch stubs, add a reusable helper:

```csharp
        private static bool TryParseVector3(string csv, out Vector3 result)
        {
            result = Vector3.zero;
            if (string.IsNullOrEmpty(csv)) return false;
            var parts = csv.Split(',');
            if (parts.Length < 3) return false;
            var ci = System.Globalization.CultureInfo.InvariantCulture;
            return float.TryParse(parts[0].Trim(), System.Globalization.NumberStyles.Float, ci, out result.x)
                && float.TryParse(parts[1].Trim(), System.Globalization.NumberStyles.Float, ci, out result.y)
                && float.TryParse(parts[2].Trim(), System.Globalization.NumberStyles.Float, ci, out result.z);
        }
```

- [ ] **Step 2: Implement HandleEditorCreateEmpty**

Replace the stub:

```csharp
        private static EditorControlResponse HandleEditorCreateEmpty(EditorControlRequest request)
        {
            if (string.IsNullOrEmpty(request.new_name))
                return BuildError("EDITOR_CTRL_CREATE_EMPTY_NO_NAME", "new_name (object name) is required.");

            var go = new GameObject(request.new_name);
            Undo.RegisterCreatedObjectUndo(go, $"PrefabSentinel: Create {request.new_name}");

            // Parent
            if (!string.IsNullOrEmpty(request.hierarchy_path))
            {
                var parent = GameObject.Find(request.hierarchy_path);
                if (parent == null)
                    return BuildError("EDITOR_CTRL_CREATE_EMPTY_PARENT_NOT_FOUND",
                        $"Parent not found: {request.hierarchy_path}");
                Undo.SetTransformParent(go.transform, parent.transform,
                    $"PrefabSentinel: SetParent {request.new_name}");
            }

            // Position
            if (TryParseVector3(request.property_value, out Vector3 pos))
                go.transform.localPosition = pos;

            string path = GetHierarchyPath(go);
            return BuildSuccess("EDITOR_CTRL_CREATE_EMPTY_OK",
                $"Created empty GameObject '{request.new_name}' at {path}",
                data: new EditorControlData
                {
                    selected_object = request.new_name,
                    output_path = path,
                    executed = true,
                    read_only = false,
                });
        }

        private static string GetHierarchyPath(GameObject go)
        {
            string path = "/" + go.name;
            var t = go.transform.parent;
            while (t != null)
            {
                path = "/" + t.name + path;
                t = t.parent;
            }
            return path;
        }
```

Note: `hierarchy_path` is reused as parent path, `new_name` as object name, `property_value` as position CSV. This avoids new fields for simple create operations.

- [ ] **Step 3: Implement HandleEditorCreatePrimitive**

Replace the stub:

```csharp
        private static EditorControlResponse HandleEditorCreatePrimitive(EditorControlRequest request)
        {
            if (string.IsNullOrEmpty(request.primitive_type))
                return BuildError("EDITOR_CTRL_CREATE_PRIM_NO_TYPE", "primitive_type is required.");

            PrimitiveType primType;
            try
            {
                primType = (PrimitiveType)System.Enum.Parse(typeof(PrimitiveType), request.primitive_type, true);
            }
            catch (System.ArgumentException)
            {
                return BuildError("EDITOR_CTRL_CREATE_PRIM_BAD_TYPE",
                    $"Invalid primitive_type: {request.primitive_type}. " +
                    "Valid: Cube, Sphere, Cylinder, Capsule, Plane, Quad.");
            }

            var go = GameObject.CreatePrimitive(primType);
            Undo.RegisterCreatedObjectUndo(go, $"PrefabSentinel: Create {request.primitive_type}");

            // Name
            if (!string.IsNullOrEmpty(request.new_name))
            {
                Undo.RecordObject(go, $"PrefabSentinel: Rename {go.name}");
                go.name = request.new_name;
            }

            // Parent
            if (!string.IsNullOrEmpty(request.hierarchy_path))
            {
                var parent = GameObject.Find(request.hierarchy_path);
                if (parent == null)
                    return BuildError("EDITOR_CTRL_CREATE_PRIM_PARENT_NOT_FOUND",
                        $"Parent not found: {request.hierarchy_path}");
                Undo.SetTransformParent(go.transform, parent.transform,
                    $"PrefabSentinel: SetParent {go.name}");
            }

            // Transform
            if (TryParseVector3(request.property_value, out Vector3 pos))
                go.transform.localPosition = pos;
            if (TryParseVector3(request.scale, out Vector3 scl))
                go.transform.localScale = scl;
            if (TryParseVector3(request.rotation, out Vector3 rot))
                go.transform.localEulerAngles = rot;

            string path = GetHierarchyPath(go);
            return BuildSuccess("EDITOR_CTRL_CREATE_PRIM_OK",
                $"Created {request.primitive_type} '{go.name}' at {path}",
                data: new EditorControlData
                {
                    selected_object = go.name,
                    output_path = path,
                    executed = true,
                    read_only = false,
                });
        }
```

- [ ] **Step 4: Commit**

```bash
git add tools/unity/PrefabSentinel.UnityEditorControlBridge.cs
git commit -m "feat(bridge): implement editor_create_empty and editor_create_primitive"
```

---

## Task 3: C# — HandleEditorBatchCreate + HandleEditorBatchSetProperty

**Files:**
- Modify: `tools/unity/PrefabSentinel.UnityEditorControlBridge.cs`

- [ ] **Step 1: Implement HandleEditorBatchCreate**

Replace the stub:

```csharp
        private static EditorControlResponse HandleEditorBatchCreate(EditorControlRequest request)
        {
            if (string.IsNullOrEmpty(request.batch_objects_json))
                return BuildError("EDITOR_CTRL_BATCH_CREATE_NO_DATA", "batch_objects_json is required.");

            BatchObjectArray wrapper;
            try
            {
                wrapper = JsonUtility.FromJson<BatchObjectArray>(
                    "{\"items\":" + request.batch_objects_json + "}");
            }
            catch (System.Exception ex)
            {
                return BuildError("EDITOR_CTRL_BATCH_CREATE_JSON_ERROR",
                    $"Failed to parse batch_objects_json: {ex.Message}");
            }

            if (wrapper.items == null || wrapper.items.Length == 0)
                return BuildError("EDITOR_CTRL_BATCH_CREATE_EMPTY", "batch_objects_json is empty.");

            int undoGroup = Undo.GetCurrentGroup();
            Undo.SetCurrentGroupName("PrefabSentinel: Batch Create");

            var createdPaths = new System.Collections.Generic.List<string>();

            foreach (var spec in wrapper.items)
            {
                GameObject go;
                if (!string.IsNullOrEmpty(spec.type) && spec.type != "Empty")
                {
                    try
                    {
                        var primType = (PrimitiveType)System.Enum.Parse(typeof(PrimitiveType), spec.type, true);
                        go = GameObject.CreatePrimitive(primType);
                    }
                    catch (System.ArgumentException)
                    {
                        Undo.CollapseUndoOperations(undoGroup);
                        return BuildError("EDITOR_CTRL_BATCH_CREATE_BAD_TYPE",
                            $"Invalid type: {spec.type}. Valid: Cube, Sphere, Cylinder, Capsule, Plane, Quad, Empty.");
                    }
                }
                else
                {
                    go = new GameObject("GameObject");
                }
                Undo.RegisterCreatedObjectUndo(go, $"PrefabSentinel: Create {spec.name}");

                if (!string.IsNullOrEmpty(spec.name))
                    go.name = spec.name;

                if (!string.IsNullOrEmpty(spec.parent))
                {
                    var parent = GameObject.Find(spec.parent);
                    if (parent != null)
                        Undo.SetTransformParent(go.transform, parent.transform,
                            $"PrefabSentinel: SetParent {go.name}");
                }

                if (TryParseVector3(spec.position, out Vector3 pos))
                    go.transform.localPosition = pos;
                if (TryParseVector3(spec.scale, out Vector3 scl))
                    go.transform.localScale = scl;
                if (TryParseVector3(spec.rotation, out Vector3 rot))
                    go.transform.localEulerAngles = rot;

                createdPaths.Add(GetHierarchyPath(go));
            }

            Undo.CollapseUndoOperations(undoGroup);

            return BuildSuccess("EDITOR_CTRL_BATCH_CREATE_OK",
                $"Created {createdPaths.Count} objects",
                data: new EditorControlData
                {
                    executed = true,
                    read_only = false,
                    suggestions = createdPaths.ToArray(),
                });
        }
```

- [ ] **Step 2: Implement HandleEditorBatchSetProperty**

Replace the stub:

```csharp
        private static EditorControlResponse HandleEditorBatchSetProperty(EditorControlRequest request)
        {
            if (string.IsNullOrEmpty(request.batch_operations_json))
                return BuildError("EDITOR_CTRL_BATCH_SET_NO_DATA", "batch_operations_json is required.");

            BatchSetPropertyArray wrapper;
            try
            {
                wrapper = JsonUtility.FromJson<BatchSetPropertyArray>(
                    "{\"items\":" + request.batch_operations_json + "}");
            }
            catch (System.Exception ex)
            {
                return BuildError("EDITOR_CTRL_BATCH_SET_JSON_ERROR",
                    $"Failed to parse batch_operations_json: {ex.Message}");
            }

            if (wrapper.items == null || wrapper.items.Length == 0)
                return BuildError("EDITOR_CTRL_BATCH_SET_EMPTY", "batch_operations_json is empty.");

            int undoGroup = Undo.GetCurrentGroup();
            Undo.SetCurrentGroupName("PrefabSentinel: Batch SetProperty");

            var results = new System.Collections.Generic.List<string>();

            foreach (var op in wrapper.items)
            {
                // Build a synthetic request and delegate to HandleEditorSetProperty
                var subReq = new EditorControlRequest
                {
                    action = "editor_set_property",
                    hierarchy_path = op.hierarchy_path,
                    component_type = op.component_type,
                    property_name = op.property_name,
                    property_value = op.value,
                    object_reference = op.object_reference,
                };
                var subResp = HandleEditorSetProperty(subReq);
                if (!subResp.success)
                {
                    Undo.CollapseUndoOperations(undoGroup);
                    return BuildError("EDITOR_CTRL_BATCH_SET_FAILED",
                        $"Operation failed at index {results.Count}: {subResp.message}");
                }
                results.Add($"{op.hierarchy_path}/{op.component_type}.{op.property_name}");
            }

            Undo.CollapseUndoOperations(undoGroup);

            return BuildSuccess("EDITOR_CTRL_BATCH_SET_OK",
                $"Set {results.Count} properties",
                data: new EditorControlData
                {
                    executed = true,
                    read_only = false,
                    suggestions = results.ToArray(),
                });
        }
```

- [ ] **Step 3: Commit**

```bash
git add tools/unity/PrefabSentinel.UnityEditorControlBridge.cs
git commit -m "feat(bridge): implement editor_batch_create and editor_batch_set_property with Undo groups"
```

---

## Task 4: C# — editor_add_component extension + open/save scene

**Files:**
- Modify: `tools/unity/PrefabSentinel.UnityEditorControlBridge.cs`

- [ ] **Step 1: Extend HandleEditorAddComponent with properties_json**

In `HandleEditorAddComponent`, after the `Undo.AddComponent` call (line 1955) and before the success response (line 1960), add:

```csharp
            var added = Undo.AddComponent(go, compType);
            if (added == null)
                return BuildError("EDITOR_CTRL_ADD_COMP_FAILED",
                    $"Failed to add component: {request.component_type}");

            // Apply initial properties if provided
            if (!string.IsNullOrEmpty(request.properties_json))
            {
                try
                {
                    var propWrapper = JsonUtility.FromJson<PropertyEntryArray>(
                        "{\"items\":" + request.properties_json + "}");
                    if (propWrapper.items != null)
                    {
                        var so = new SerializedObject(added);
                        foreach (var entry in propWrapper.items)
                        {
                            var prop = so.FindProperty(entry.name);
                            if (prop == null) continue;
                            if (!string.IsNullOrEmpty(entry.object_reference))
                            {
                                var (obj, _) = ResolveObjectReference(entry.object_reference);
                                if (obj != null) prop.objectReferenceValue = obj;
                            }
                            else if (!string.IsNullOrEmpty(entry.value))
                            {
                                ApplyPropertyValue(prop, entry.value);
                            }
                        }
                        so.ApplyModifiedProperties();
                    }
                }
                catch (System.Exception) { /* best-effort; component already added */ }
            }

            var resp = BuildSuccess("EDITOR_CTRL_ADD_COMP_OK",
```

- [ ] **Step 2: Extract ApplyPropertyValue helper**

Add before the handler methods:

```csharp
        /// <summary>Apply a string value to a SerializedProperty based on its type.</summary>
        private static bool ApplyPropertyValue(SerializedProperty prop, string v)
        {
            var ci = System.Globalization.CultureInfo.InvariantCulture;
            switch (prop.propertyType)
            {
                case SerializedPropertyType.Integer:
                    if (int.TryParse(v, System.Globalization.NumberStyles.Integer, ci, out int iv))
                    { prop.intValue = iv; return true; }
                    return false;
                case SerializedPropertyType.Float:
                    if (float.TryParse(v, System.Globalization.NumberStyles.Float, ci, out float fv))
                    { prop.floatValue = fv; return true; }
                    return false;
                case SerializedPropertyType.Boolean:
                    if (bool.TryParse(v, out bool bv))
                    { prop.boolValue = bv; return true; }
                    return false;
                case SerializedPropertyType.String:
                    prop.stringValue = v; return true;
                case SerializedPropertyType.Enum:
                {
#pragma warning disable 0618
                    int idx = System.Array.IndexOf(prop.enumNames, v);
#pragma warning restore 0618
                    if (idx >= 0) { prop.enumValueIndex = idx; return true; }
                    if (int.TryParse(v, out int ei)) { prop.enumValueIndex = ei; return true; }
                    return false;
                }
                case SerializedPropertyType.Vector3:
                {
                    var parts = v.Split(',');
                    if (parts.Length >= 3
                        && float.TryParse(parts[0].Trim(), System.Globalization.NumberStyles.Float, ci, out float x)
                        && float.TryParse(parts[1].Trim(), System.Globalization.NumberStyles.Float, ci, out float y)
                        && float.TryParse(parts[2].Trim(), System.Globalization.NumberStyles.Float, ci, out float z))
                    { prop.vector3Value = new Vector3(x, y, z); return true; }
                    return false;
                }
                default: return false;
            }
        }
```

- [ ] **Step 3: Refactor HandleEditorSetProperty to use ApplyPropertyValue**

In `HandleEditorSetProperty`, replace the switch block body for Integer/Float/Boolean/String/Enum/Vector3 cases with calls to `ApplyPropertyValue`. Keep Color/Vector2/Vector4/ObjectReference inline since they have extra logic (alpha defaults, error messages). This is optional — skip if the refactor is too risky for this PR.

- [ ] **Step 4: Implement HandleEditorOpenScene**

Replace the stub:

```csharp
        private static EditorControlResponse HandleEditorOpenScene(EditorControlRequest request)
        {
            if (string.IsNullOrEmpty(request.asset_path))
                return BuildError("EDITOR_CTRL_OPEN_SCENE_NO_PATH", "asset_path is required.");

            if (!System.IO.File.Exists(request.asset_path))
                return BuildError("EDITOR_CTRL_OPEN_SCENE_NOT_FOUND",
                    $"Scene file not found: {request.asset_path}");

            var mode = string.Equals(request.open_scene_mode, "additive",
                System.StringComparison.OrdinalIgnoreCase)
                ? OpenSceneMode.Additive
                : OpenSceneMode.Single;

            var scene = EditorSceneManager.OpenScene(request.asset_path, mode);

            return BuildSuccess("EDITOR_CTRL_OPEN_SCENE_OK",
                $"Opened scene: {request.asset_path} ({request.open_scene_mode})",
                data: new EditorControlData
                {
                    asset_path = request.asset_path,
                    output_path = scene.name,
                    executed = true,
                });
        }
```

- [ ] **Step 5: Implement HandleEditorSaveScene**

Replace the stub:

```csharp
        private static EditorControlResponse HandleEditorSaveScene(EditorControlRequest request)
        {
            if (!string.IsNullOrEmpty(request.asset_path))
            {
                // Save to specific path
                var scene = SceneManager.GetActiveScene();
                bool ok = EditorSceneManager.SaveScene(scene, request.asset_path);
                if (!ok)
                    return BuildError("EDITOR_CTRL_SAVE_SCENE_FAILED",
                        $"Failed to save scene to: {request.asset_path}");
                return BuildSuccess("EDITOR_CTRL_SAVE_SCENE_OK",
                    $"Saved scene to: {request.asset_path}",
                    data: new EditorControlData
                    {
                        asset_path = request.asset_path,
                        executed = true,
                    });
            }
            else
            {
                // Save all open scenes
                bool ok = EditorSceneManager.SaveOpenScenes();
                if (!ok)
                    return BuildError("EDITOR_CTRL_SAVE_SCENE_FAILED",
                        "Failed to save open scenes.");
                var scene = SceneManager.GetActiveScene();
                return BuildSuccess("EDITOR_CTRL_SAVE_SCENE_OK",
                    $"Saved all open scenes (active: {scene.name})",
                    data: new EditorControlData
                    {
                        asset_path = scene.path,
                        executed = true,
                    });
            }
        }
```

- [ ] **Step 6: Commit**

```bash
git add tools/unity/PrefabSentinel.UnityEditorControlBridge.cs
git commit -m "feat(bridge): add_component properties, open/save scene, ApplyPropertyValue helper"
```

---

## Task 5: Python — SUPPORTED_ACTIONS + MCP tools + tests

**Files:**
- Modify: `prefab_sentinel/editor_bridge.py`
- Modify: `prefab_sentinel/mcp_server.py`
- Modify: `tests/test_editor_bridge.py`
- Modify: `tests/test_mcp_server.py`

- [ ] **Step 1: Add 6 actions to SUPPORTED_ACTIONS**

In `prefab_sentinel/editor_bridge.py`:

```python
        "editor_set_parent",
        # Phase 6: Batch Operations + Scene
        "editor_create_empty",
        "editor_create_primitive",
        "editor_batch_create",
        "editor_batch_set_property",
        "editor_open_scene",
        "editor_save_scene",
    }
)
```

- [ ] **Step 2: Add 6 MCP tools**

In `prefab_sentinel/mcp_server.py`, after `editor_set_parent` tool, before `# Inspection tools`:

```python
    @server.tool()
    def editor_create_empty(
        name: str,
        parent_path: str = "",
        position: str = "",
    ) -> dict[str, Any]:
        """Create an empty GameObject with name, optional parent and position.

        Args:
            name: Name for the new GameObject.
            parent_path: Hierarchy path to parent. Empty = scene root.
            position: Local position as "x,y,z". Empty = origin.
        """
        return send_action(
            action="editor_create_empty",
            new_name=name,
            hierarchy_path=parent_path,
            property_value=position,
        )

    @server.tool()
    def editor_create_primitive(
        primitive_type: str,
        name: str = "",
        parent_path: str = "",
        position: str = "",
        scale: str = "",
        rotation: str = "",
    ) -> dict[str, Any]:
        """Create a primitive GameObject (Cube, Sphere, Cylinder, Capsule, Plane, Quad).

        Args:
            primitive_type: Primitive shape. One of: Cube, Sphere, Cylinder, Capsule, Plane, Quad.
            name: Name for the object. Empty = default Unity name.
            parent_path: Hierarchy path to parent. Empty = scene root.
            position: Local position as "x,y,z".
            scale: Local scale as "x,y,z".
            rotation: Euler angles as "x,y,z".
        """
        kwargs: dict[str, Any] = {"primitive_type": primitive_type}
        if name:
            kwargs["new_name"] = name
        if parent_path:
            kwargs["hierarchy_path"] = parent_path
        if position:
            kwargs["property_value"] = position
        if scale:
            kwargs["scale"] = scale
        if rotation:
            kwargs["rotation"] = rotation
        return send_action(action="editor_create_primitive", **kwargs)

    @server.tool()
    def editor_batch_create(
        objects: list[dict[str, str]],
    ) -> dict[str, Any]:
        """Create multiple GameObjects in a single request (Undo-grouped).

        Each object dict may contain: type, name, parent, position, scale, rotation.
        type can be "Empty", "Cube", "Sphere", "Cylinder", "Capsule", "Plane", "Quad".

        Args:
            objects: List of object specifications.
        """
        import json

        return send_action(
            action="editor_batch_create",
            batch_objects_json=json.dumps(objects, ensure_ascii=False),
        )

    @server.tool()
    def editor_batch_set_property(
        operations: list[dict[str, str]],
    ) -> dict[str, Any]:
        """Set multiple properties in a single request (Undo-grouped).

        Each operation dict must contain: hierarchy_path, component_type, property_name.
        Plus either value (for primitives) or object_reference (for ObjectReference).

        Args:
            operations: List of set-property operations.
        """
        import json

        return send_action(
            action="editor_batch_set_property",
            batch_operations_json=json.dumps(operations, ensure_ascii=False),
        )

    @server.tool()
    def editor_open_scene(
        scene_path: str,
        mode: str = "single",
    ) -> dict[str, Any]:
        """Open a Unity scene by asset path.

        Args:
            scene_path: Asset path to .unity file (e.g. "Assets/Scenes/Main.unity").
            mode: "single" (replace current) or "additive" (add to current).
        """
        return send_action(
            action="editor_open_scene",
            asset_path=scene_path,
            open_scene_mode=mode,
        )

    @server.tool()
    def editor_save_scene(
        path: str = "",
    ) -> dict[str, Any]:
        """Save the current scene. If path is empty, saves all open scenes in place.

        Args:
            path: Asset path to save to. Empty = save all open scenes.
        """
        kwargs: dict[str, Any] = {}
        if path:
            kwargs["asset_path"] = path
        return send_action(action="editor_save_scene", **kwargs)
```

- [ ] **Step 3: Extend editor_add_component with properties parameter**

In `prefab_sentinel/mcp_server.py`, modify the existing `editor_add_component`:

```python
    @server.tool()
    def editor_add_component(
        hierarchy_path: str,
        component_type: str,
        properties: list[dict[str, str]] | None = None,
    ) -> dict[str, Any]:
        """Add a component to a GameObject at runtime (Undo-able).

        Type resolution: tries fully qualified name, then searches all assemblies
        by simple name.

        Args:
            hierarchy_path: Hierarchy path to the target GameObject.
            component_type: Component type name (e.g. "BoxCollider", "UnityEngine.AudioSource").
            properties: Optional initial property values. Each dict has "name" and "value"
                (or "object_reference") keys. Applied after component is added.
        """
        kwargs: dict[str, Any] = {
            "hierarchy_path": hierarchy_path,
            "component_type": component_type,
        }
        if properties:
            import json

            kwargs["properties_json"] = json.dumps(properties, ensure_ascii=False)
        return send_action(action="editor_add_component", **kwargs)
```

- [ ] **Step 4: Update test_editor_bridge.py**

Add 6 actions to the expected set:

```python
            "editor_set_property",
            "save_as_prefab",
            "editor_set_parent",
            "editor_create_empty",
            "editor_create_primitive",
            "editor_batch_create",
            "editor_batch_set_property",
            "editor_open_scene",
            "editor_save_scene",
        }
```

- [ ] **Step 5: Update test_mcp_server.py**

Add 6 tools to expected set and update count:

```python
            "editor_set_property", "editor_save_as_prefab",
            "editor_set_parent",
            "editor_create_empty", "editor_create_primitive",
            "editor_batch_create", "editor_batch_set_property",
            "editor_open_scene", "editor_save_scene",
            # Inspection + orchestrator tools
```

Change count: `self.assertEqual(58, len(tools))`

- [ ] **Step 6: Run tests + lint**

Run: `uv run python -m unittest tests.test_editor_bridge.TestSupportedActions.test_all_actions_present -v`
Expected: PASS

Run: `python3 -m compileall prefab_sentinel/mcp_server.py`
Expected: no errors.

Run: `uv run ruff check prefab_sentinel/editor_bridge.py prefab_sentinel/mcp_server.py`
Expected: All checks passed.

- [ ] **Step 7: Commit**

```bash
git add prefab_sentinel/editor_bridge.py prefab_sentinel/mcp_server.py tests/test_editor_bridge.py tests/test_mcp_server.py
git commit -m "feat(mcp): add 6 batch/scene tools, extend add_component with properties (58 tools)"
```

---

## Task 6: Verification + README

- [ ] **Step 1: Full test suite**

Run: `uv run --extra test python scripts/run_unit_tests.py`
Expected: all tests pass.

- [ ] **Step 2: Verify MCP tool count**

Run: `uv run --extra mcp python -c "from prefab_sentinel.mcp_server import create_server; s = create_server(); print(len(s._tool_manager._tools))"`
Expected: 58

- [ ] **Step 3: Update README**

Add 6 tools to the MCP tool table:

```
| `editor_create_empty` | 空の GameObject を名前・親・位置指定で作成 |
| `editor_create_primitive` | プリミティブ (Cube/Sphere 等) を1回で作成 (位置・スケール・回転指定) |
| `editor_batch_create` | 複数オブジェクトを1リクエストで一括生成 (Undo グループ) |
| `editor_batch_set_property` | 複数プロパティを1リクエストで一括設定 (Undo グループ) |
| `editor_open_scene` | シーンを開く (single/additive) |
| `editor_save_scene` | シーンを保存 |
```

- [ ] **Step 4: Commit**

```bash
git add README.md
git commit -m "docs: add 6 batch/scene tools to README"
```
