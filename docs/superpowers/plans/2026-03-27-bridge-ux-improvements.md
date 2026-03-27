# Bridge UX Improvements Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix `ResolveComponentType` short name resolution, add `editor_set_parent` tool, and return full type name from `editor_add_component`.

**Architecture:** All changes in C# EditorControlBridge + Python MCP wrappers. ResolveComponentType gets a new step 3 (simple name search). editor_set_parent follows the HandleEditorRename pattern. add_component response adds `compType.FullName`.

**Tech Stack:** C# (Unity Editor API, reflection), Python 3.11+ (FastMCP)

**Spec:** `docs/superpowers/specs/2026-03-27-bridge-ux-improvements-design.md`

---

## File Structure

### Modified Files

| File | Responsibility |
|------|---------------|
| `tools/unity/PrefabSentinel.UnityEditorControlBridge.cs` | ResolveComponentType rewrite, HandleEditorSetParent, HandleEditorAddComponent response fix, SupportedActions +1, dispatch +1 |
| `prefab_sentinel/mcp_server.py` | `editor_set_parent` tool |
| `prefab_sentinel/editor_bridge.py` | SUPPORTED_ACTIONS +1 |
| `tests/test_editor_bridge.py` | SUPPORTED_ACTIONS test |
| `tests/test_mcp_server.py` | Tool registration test (52 tools) |

---

## Task 1: C# — ResolveComponentType + add_component response + set_parent handler

**Files:**
- Modify: `tools/unity/PrefabSentinel.UnityEditorControlBridge.cs`

- [ ] **Step 1: Rewrite ResolveComponentType**

Replace lines 1859-1880 (the entire `ResolveComponentType` method):

```csharp
        private static System.Type ResolveComponentType(string typeName)
        {
            // 1. Fully qualified name (fastest path)
            var t = System.Type.GetType(typeName);
            if (t != null && typeof(Component).IsAssignableFrom(t))
                return t;

            // 2. Search all loaded assemblies by full name
            foreach (var asm in System.AppDomain.CurrentDomain.GetAssemblies())
            {
                t = asm.GetType(typeName);
                if (t != null && typeof(Component).IsAssignableFrom(t))
                    return t;
            }

            // 3. Search all loaded assemblies by simple name (handles short names
            //    like "BoxCollider" that live in UnityEngine.PhysicsModule etc.)
            foreach (var asm in System.AppDomain.CurrentDomain.GetAssemblies())
            {
                try
                {
                    foreach (var type in asm.GetExportedTypes())
                    {
                        if (type.Name == typeName && typeof(Component).IsAssignableFrom(type))
                            return type;
                    }
                }
                catch (System.ReflectionTypeLoadException) { }
            }

            return null;
        }
```

- [ ] **Step 2: Fix HandleEditorAddComponent response**

In `HandleEditorAddComponent`, find the success response (around line 1905):

```csharp
            var resp = BuildSuccess("EDITOR_CTRL_ADD_COMP_OK",
                $"Added {compType.Name} to {request.hierarchy_path}",
                data: new EditorControlData
                {
                    selected_object = go.name,
                    executed = true,
                    read_only = false,
                });
```

Replace with:

```csharp
            var resp = BuildSuccess("EDITOR_CTRL_ADD_COMP_OK",
                $"Added {compType.FullName} to {request.hierarchy_path}",
                data: new EditorControlData
                {
                    selected_object = go.name,
                    asset_path = compType.FullName,
                    executed = true,
                    read_only = false,
                });
```

- [ ] **Step 3: Add `editor_set_parent` to SupportedActions**

After line 55 (`"save_as_prefab",`):

```csharp
            // Phase 5: SetProperty + SaveAsPrefab
            "editor_set_property",
            "save_as_prefab",
            "editor_set_parent",
        };
```

- [ ] **Step 4: Add dispatch case for editor_set_parent**

After `save_as_prefab` case (line 388), before `case "vrcsdk_upload":`:

```csharp
                case "save_as_prefab":
                    response = HandleSaveAsPrefab(request);
                    break;
                case "editor_set_parent":
                    response = HandleEditorSetParent(request);
                    break;
                case "vrcsdk_upload":
```

- [ ] **Step 5: Add HandleEditorSetParent**

After `HandleEditorRename` (after line 1857), before `ResolveComponentType`:

```csharp
        private static EditorControlResponse HandleEditorSetParent(EditorControlRequest request)
        {
            if (string.IsNullOrEmpty(request.hierarchy_path))
                return BuildError("EDITOR_CTRL_SET_PARENT_NO_PATH", "hierarchy_path is required.");

            var child = GameObject.Find(request.hierarchy_path);
            if (child == null)
                return BuildError("EDITOR_CTRL_SET_PARENT_NOT_FOUND",
                    $"GameObject not found: {request.hierarchy_path}");

            Transform newParent = null;
            if (!string.IsNullOrEmpty(request.new_name))
            {
                var parentGo = GameObject.Find(request.new_name);
                if (parentGo == null)
                    return BuildError("EDITOR_CTRL_SET_PARENT_PARENT_NOT_FOUND",
                        $"Parent GameObject not found: {request.new_name}");
                newParent = parentGo.transform;
            }

            Undo.SetTransformParent(child.transform, newParent,
                $"PrefabSentinel: SetParent {child.name}");

            string parentName = newParent != null ? newParent.name : "(scene root)";
            var resp = BuildSuccess("EDITOR_CTRL_SET_PARENT_OK",
                $"Moved '{child.name}' under '{parentName}'",
                data: new EditorControlData
                {
                    selected_object = child.name,
                    executed = true,
                    read_only = false,
                });
            resp.diagnostics = new[] { new EditorControlDiagnostic
            {
                detail = "Runtime modification — save the scene (File > Save) to persist.",
                evidence = "Undo.SetTransformParent"
            }};
            return resp;
        }

```

- [ ] **Step 6: Commit**

```bash
git add tools/unity/PrefabSentinel.UnityEditorControlBridge.cs
git commit -m "feat(bridge): fix type resolution, add editor_set_parent, return FullName from add_component"
```

---

## Task 2: Python — SUPPORTED_ACTIONS + MCP tool + tests

**Files:**
- Modify: `prefab_sentinel/editor_bridge.py:62-67`
- Modify: `prefab_sentinel/mcp_server.py` (after line 1493)
- Modify: `tests/test_editor_bridge.py`
- Modify: `tests/test_mcp_server.py`

- [ ] **Step 1: Add `editor_set_parent` to SUPPORTED_ACTIONS**

In `prefab_sentinel/editor_bridge.py`, after `"save_as_prefab",`:

```python
        # Phase 5: SetProperty + SaveAsPrefab
        "editor_set_property",
        "save_as_prefab",
        "editor_set_parent",
    }
)
```

- [ ] **Step 2: Add MCP tool**

In `prefab_sentinel/mcp_server.py`, after `editor_save_as_prefab` (line 1493), before the `# Inspection tools` section:

```python
    @server.tool()
    def editor_set_parent(
        hierarchy_path: str,
        parent_path: str = "",
    ) -> dict[str, Any]:
        """Set the parent of a GameObject in the scene hierarchy (Undo-able).

        Move an existing GameObject under a new parent, or to the scene root.

        Args:
            hierarchy_path: Hierarchy path to the child GameObject to move.
            parent_path: Hierarchy path to the new parent. Empty = move to scene root.
        """
        return send_action(
            action="editor_set_parent",
            hierarchy_path=hierarchy_path,
            new_name=parent_path,
        )
```

- [ ] **Step 3: Update test_editor_bridge.py**

In `tests/test_editor_bridge.py`, add to the expected set:

```python
            "editor_set_property",
            "save_as_prefab",
            "editor_set_parent",
        }
```

- [ ] **Step 4: Update test_mcp_server.py**

In `tests/test_mcp_server.py`:

1. Add `"editor_set_parent",` after `"editor_save_as_prefab",` in the expected set.
2. Change `self.assertEqual(51, len(tools))` to `self.assertEqual(52, len(tools))`.

- [ ] **Step 5: Run tests**

Run: `uv run python -m unittest tests.test_editor_bridge.TestSupportedActions.test_all_actions_present -v`
Expected: PASS

Run: `python3 -m compileall prefab_sentinel/mcp_server.py`
Expected: no errors.

- [ ] **Step 6: Commit**

```bash
git add prefab_sentinel/editor_bridge.py prefab_sentinel/mcp_server.py tests/test_editor_bridge.py tests/test_mcp_server.py
git commit -m "feat(mcp): add editor_set_parent tool, update tests (52 tools)"
```

---

## Task 3: Verification + README

**Files:** (none created)

- [ ] **Step 1: Full test suite**

Run: `uv run --extra test python scripts/run_unit_tests.py`
Expected: all tests pass, no regressions.

- [ ] **Step 2: Lint check**

Run: `uv run ruff check prefab_sentinel/editor_bridge.py prefab_sentinel/mcp_server.py tests/test_editor_bridge.py tests/test_mcp_server.py`
Expected: All checks passed.

- [ ] **Step 3: Verify MCP tool count**

Run: `uv run --extra mcp python -c "from prefab_sentinel.mcp_server import create_server; s = create_server(); print(len(s._tool_manager._tools))"`
Expected: 52

- [ ] **Step 4: Update README**

Add `editor_set_parent` to the MCP tool table in `README.md`:

```
| `editor_set_parent` | 既存 GameObject の親子関係を変更（Undo 対応） |
```

- [ ] **Step 5: Commit**

```bash
git add README.md
git commit -m "docs: add editor_set_parent to README"
```
