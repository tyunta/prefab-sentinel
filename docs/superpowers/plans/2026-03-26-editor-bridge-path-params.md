# Editor Bridge Path Params + Material Reverse Lookup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add asset path parameters to editor_set_material and texture values, plus a new editor_find_renderers_by_material reverse-lookup tool.

**Architecture:** C# EditorControlBridge changes first (request struct, handlers, SupportedActions), then Python MCP wrappers. Existing MaterialSlotEntry and GetHierarchyPath are reused.

**Tech Stack:** C# (Unity Editor API), Python 3.11+ (FastMCP)

**Spec:** `docs/superpowers/specs/2026-03-26-editor-bridge-path-params-design.md`

---

## File Structure

### Modified Files

| File | Responsibility |
|------|---------------|
| `tools/unity/PrefabSentinel.UnityEditorControlBridge.cs` | material_path field, HandleSetMaterial path support, texture path: prefix, HandleFindRenderersByMaterial, SupportedActions |
| `prefab_sentinel/mcp_server.py` | editor_set_material path param, editor_find_renderers_by_material new tool, docstring updates |
| `prefab_sentinel/editor_bridge.py` | SUPPORTED_ACTIONS update |
| `tests/test_editor_bridge.py` | SUPPORTED_ACTIONS test update |

---

## Task 1: C# — Add material_path field + update HandleSetMaterial

**Files:**
- Modify: `tools/unity/PrefabSentinel.UnityEditorControlBridge.cs:75` (EditorControlRequest)
- Modify: `tools/unity/PrefabSentinel.UnityEditorControlBridge.cs:713-760` (HandleSetMaterial)

- [ ] **Step 1: Add material_path to EditorControlRequest**

At line 75 (after `material_guid`), add:

```csharp
            public string material_guid = string.Empty;
            public string material_path = string.Empty;  // asset path alternative to GUID
```

- [ ] **Step 2: Update HandleSetMaterial for path→GUID resolution**

Replace the beginning of `HandleSetMaterial` (lines 713-720) — change the GUID validation to support path resolution:

```csharp
        private static EditorControlResponse HandleSetMaterial(EditorControlRequest request)
        {
            if (string.IsNullOrEmpty(request.hierarchy_path))
                return BuildError("EDITOR_CTRL_SET_MATERIAL_NO_PATH", "hierarchy_path is required.");
            if (request.material_index < 0)
                return BuildError("EDITOR_CTRL_SET_MATERIAL_NO_INDEX", "material_index is required (>= 0).");

            // Resolve material GUID from guid or path
            string guid = request.material_guid;
            if (!string.IsNullOrEmpty(request.material_path))
            {
                if (!string.IsNullOrEmpty(guid))
                    return BuildError("EDITOR_CTRL_SET_MATERIAL_CONFLICT",
                        "Cannot specify both material_guid and material_path. Use one.");
                guid = AssetDatabase.AssetPathToGUID(request.material_path);
                if (string.IsNullOrEmpty(guid))
                    return BuildError("EDITOR_CTRL_SET_MATERIAL_NOT_FOUND",
                        $"Material not found at path: {request.material_path}");
            }
            if (string.IsNullOrEmpty(guid))
                return BuildError("EDITOR_CTRL_SET_MATERIAL_NO_GUID",
                    "material_guid or material_path is required.");
```

The rest of the method (GameObject.Find through return) remains unchanged, using the resolved `guid` variable. Replace `request.material_guid` with `guid` on the existing `AssetDatabase.GUIDToAssetPath(request.material_guid)` line (around line 739).

- [ ] **Step 3: Commit**

```bash
git add tools/unity/PrefabSentinel.UnityEditorControlBridge.cs
git commit -m "feat(bridge): add material_path parameter to editor_set_material"
```

---

## Task 2: C# — Add path: prefix to texture values

**Files:**
- Modify: `tools/unity/PrefabSentinel.UnityEditorControlBridge.cs` (HandleSetMaterialProperty texture case)

- [ ] **Step 1: Add path: branch in texture handling**

Find the texture case in HandleSetMaterialProperty. After the `else if (val.StartsWith("guid:"))` block and before the `else` block, add the `path:` branch:

```csharp
                    else if (val.StartsWith("path:"))
                    {
                        string texPath = val.Substring(5);
                        var tex = AssetDatabase.LoadAssetAtPath<Texture>(texPath);
                        if (tex == null)
                            return BuildError("EDITOR_CTRL_PROPERTY_TYPE_MISMATCH",
                                $"Texture not found at path: {texPath}");
                        mat.SetTexture(request.property_name, tex);
                    }
```

Also update the error message in the final `else` block:

```csharp
                    else
                    {
                        return BuildError("EDITOR_CTRL_PROPERTY_TYPE_MISMATCH",
                            "Texture value must be 'guid:<hex>', 'path:<asset_path>', or empty string for null.");
                    }
```

- [ ] **Step 2: Commit**

```bash
git add tools/unity/PrefabSentinel.UnityEditorControlBridge.cs
git commit -m "feat(bridge): support path: prefix for texture values in set_material_property"
```

---

## Task 3: C# — Add HandleFindRenderersByMaterial

**Files:**
- Modify: `tools/unity/PrefabSentinel.UnityEditorControlBridge.cs:23-47` (SupportedActions)
- Modify: `tools/unity/PrefabSentinel.UnityEditorControlBridge.cs` (dispatch switch + new handler)

- [ ] **Step 1: Add action to SupportedActions**

In the `SupportedActions` HashSet (line 23-47), add after `"execute_menu_item"`:

```csharp
            // Phase 2: BlendShape + Menu
            "get_blend_shapes", "set_blend_shape",
            "list_menu_items", "execute_menu_item",
            // Phase 3: Material reverse lookup
            "find_renderers_by_material",
```

- [ ] **Step 2: Add dispatch case**

In the switch block (find `case "execute_menu_item":`), add after it:

```csharp
                    case "find_renderers_by_material":
                        response = HandleFindRenderersByMaterial(request);
                        break;
```

- [ ] **Step 3: Add HandleFindRenderersByMaterial method**

Add before `HandleListMenuItems` (or after the last handler):

```csharp
        private static EditorControlResponse HandleFindRenderersByMaterial(EditorControlRequest request)
        {
            // Resolve material GUID from guid or path
            string guid = request.material_guid;
            if (!string.IsNullOrEmpty(request.material_path))
            {
                if (!string.IsNullOrEmpty(guid))
                    return BuildError("EDITOR_CTRL_FIND_RENDERERS_CONFLICT",
                        "Cannot specify both material_guid and material_path. Use one.");
                guid = AssetDatabase.AssetPathToGUID(request.material_path);
                if (string.IsNullOrEmpty(guid))
                    return BuildError("EDITOR_CTRL_MATERIAL_NOT_FOUND",
                        $"Material not found at path: {request.material_path}");
            }
            if (string.IsNullOrEmpty(guid))
                return BuildError("EDITOR_CTRL_MATERIAL_NOT_FOUND",
                    "material_guid or material_path is required.");

            string targetPath = AssetDatabase.GUIDToAssetPath(guid);
            if (string.IsNullOrEmpty(targetPath))
                return BuildError("EDITOR_CTRL_MATERIAL_NOT_FOUND",
                    $"No asset found for GUID: {guid}");

            var renderers = UnityEngine.Object.FindObjectsOfType<Renderer>();
            var matches = new System.Collections.Generic.List<MaterialSlotEntry>();
            foreach (var renderer in renderers)
            {
                var mats = renderer.sharedMaterials;
                for (int i = 0; i < mats.Length; i++)
                {
                    if (mats[i] == null) continue;
                    string matPath = AssetDatabase.GetAssetPath(mats[i]);
                    if (matPath == targetPath)
                    {
                        matches.Add(new MaterialSlotEntry
                        {
                            renderer_path = GetHierarchyPath(renderer.transform),
                            renderer_type = renderer.GetType().Name,
                            slot_index = i,
                            material_name = mats[i].name,
                            material_asset_path = matPath,
                            material_guid = guid,
                        });
                    }
                }
            }

            return BuildSuccess("EDITOR_CTRL_FIND_RENDERERS_OK",
                $"Found {matches.Count} slot(s) using material across {renderers.Length} renderers",
                data: new EditorControlData
                {
                    material_slots = matches.ToArray(),
                    total_entries = renderers.Length,
                    executed = true,
                });
        }
```

- [ ] **Step 4: Commit**

```bash
git add tools/unity/PrefabSentinel.UnityEditorControlBridge.cs
git commit -m "feat(bridge): add find_renderers_by_material action"
```

---

## Task 4: Python — Update editor_set_material + add find tool

**Files:**
- Modify: `prefab_sentinel/mcp_server.py:1234-1250` (editor_set_material)
- Modify: `prefab_sentinel/mcp_server.py` (add editor_find_renderers_by_material)
- Modify: `prefab_sentinel/mcp_server.py:1020-1025` (editor_set_material_property docstring)
- Modify: `prefab_sentinel/editor_bridge.py` (SUPPORTED_ACTIONS)
- Modify: `tests/test_editor_bridge.py` (SUPPORTED_ACTIONS test)

- [ ] **Step 1: Update editor_set_material with material_path**

Replace `editor_set_material` (lines 1234-1250) with:

```python
    @server.tool()
    def editor_set_material(
        hierarchy_path: str,
        material_index: int,
        material_guid: str = "",
        material_path: str = "",
    ) -> dict[str, Any]:
        """Replace a material slot on a Renderer at runtime (Undo-able).

        Specify either material_guid or material_path (not both).

        Args:
            hierarchy_path: Hierarchy path to the GameObject with a Renderer.
            material_index: Material slot index (0-based).
            material_guid: GUID of the replacement Material asset (32-char hex).
            material_path: Asset path of the replacement Material (e.g. "Assets/Materials/Foo.mat").
        """
        kwargs: dict[str, Any] = {
            "hierarchy_path": hierarchy_path,
            "material_index": material_index,
        }
        if material_guid:
            kwargs["material_guid"] = material_guid
        if material_path:
            kwargs["material_path"] = material_path
        return send_action(action="set_material", **kwargs)
```

- [ ] **Step 2: Add editor_find_renderers_by_material**

Add after `editor_set_material`:

```python
    @server.tool()
    def editor_find_renderers_by_material(
        material_guid: str = "",
        material_path: str = "",
    ) -> dict[str, Any]:
        """Find all renderers using a specific material in the current scene.

        Returns renderer paths and slot indices. Specify either material_guid
        or material_path (not both).

        Args:
            material_guid: GUID of the material to search for.
            material_path: Asset path of the material (e.g. "Assets/Materials/Foo.mat").
        """
        kwargs: dict[str, Any] = {}
        if material_guid:
            kwargs["material_guid"] = material_guid
        if material_path:
            kwargs["material_path"] = material_path
        return send_action(action="find_renderers_by_material", **kwargs)
```

- [ ] **Step 3: Update editor_set_material_property docstring**

Find the docstring of `editor_set_material_property` (around line 1025). Update the Texture line:

```python
            Texture: "guid:abc123..." or "path:Assets/Tex/foo.png" or "" (null)
```

- [ ] **Step 4: Add find_renderers_by_material to SUPPORTED_ACTIONS**

In `prefab_sentinel/editor_bridge.py`, find the `SUPPORTED_ACTIONS` frozenset and add `"find_renderers_by_material"`.

- [ ] **Step 5: Update test_all_actions_present**

In `tests/test_editor_bridge.py`, find `TestSupportedActions.test_all_actions_present`. Add `"find_renderers_by_material"` to the `expected` set.

- [ ] **Step 6: Run tests**

Run: `uv run python -m unittest tests.test_editor_bridge -v`
Expected: all tests PASS.

- [ ] **Step 7: Lint**

Run: `uv run ruff check prefab_sentinel/editor_bridge.py prefab_sentinel/mcp_server.py tests/test_editor_bridge.py`
Expected: All checks passed.

- [ ] **Step 8: Commit**

```bash
git add prefab_sentinel/mcp_server.py prefab_sentinel/editor_bridge.py tests/test_editor_bridge.py
git commit -m "feat(mcp): editor_set_material path param, find_renderers_by_material, texture path: prefix docs"
```
