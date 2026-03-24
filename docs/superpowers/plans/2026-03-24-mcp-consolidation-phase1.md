# MCP Consolidation Phase 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Migrate 18 CLI-only tools to MCP so AI agents can access all prefab-sentinel functionality without shell execution.

**Architecture:** Add 15 editor bridge tools, 2 orchestrator inspection tools, and 1 revert tool to `mcp_server.py`. Editor tools call `editor_bridge.send_action()` directly. Inspection tools wrap existing orchestrator methods. Tests mock at the boundary (`send_action` / orchestrator / `patch_revert`).

**Tech Stack:** Python 3.11+, FastMCP, unittest, unittest.mock

**Spec:** `docs/superpowers/specs/2026-03-24-mcp-consolidation-design.md`

---

## File Structure

| File | Responsibility |
|------|----------------|
| Modify: `prefab_sentinel/mcp_server.py` | 18 new `@server.tool()` functions + `editor_bridge` import |
| Modify: `tests/test_mcp_server.py` | Test classes for all 18 tools + updated registration expectations |
| Modify: `README.md` | MCP tool table update |

All new code goes into existing files — no new modules.

---

## Pre-Implementation

### Task 0: Git Tag

- [ ] **Step 1: Create cli-final tag**

```bash
git tag v0.3.0-cli-final
```

- [ ] **Step 2: Verify tag**

```bash
git tag -l 'v0.3.0*'
```

Expected: `v0.3.0` and `v0.3.0-cli-final` listed.

---

## Batch A: Editor Tools (15 tools)

Editor tools share a single pattern: delegate to `send_action()`, return response directly. Group into one task per natural cluster.

### Task 1: Tool Registration Update + editor_bridge Import

Update expected tool count and add the `send_action` import before adding tools.

**Files:**
- Modify: `prefab_sentinel/mcp_server.py:26` (imports)
- Modify: `tests/test_mcp_server.py:39-65` (registration tests)

- [ ] **Step 1: Write updated registration test**

In `tests/test_mcp_server.py`, update `TestToolRegistration`:

```python
def test_all_tools_registered(self) -> None:
    server = create_server()
    tools = _run(server.list_tools())
    tool_names = {t.name for t in tools}
    expected = {
        # Existing 15 tools
        "activate_project", "get_project_status",
        "get_unity_symbols", "find_unity_symbol", "find_referencing_assets",
        "validate_refs", "inspect_wiring", "inspect_variant",
        "diff_unity_symbols", "set_property",
        "add_component", "remove_component",
        "list_serialized_fields", "validate_field_rename", "check_field_coverage",
        # New 18 tools
        "editor_screenshot", "editor_select", "editor_frame", "editor_camera",
        "editor_refresh", "editor_recompile", "editor_instantiate",
        "editor_set_material", "editor_delete",
        "editor_list_children", "editor_list_materials", "editor_list_roots",
        "editor_get_material_property", "editor_console", "editor_run_tests",
        "inspect_materials", "validate_structure", "revert_overrides",
    }
    self.assertEqual(expected, tool_names)

def test_tool_count(self) -> None:
    server = create_server()
    tools = _run(server.list_tools())
    self.assertEqual(33, len(tools))
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run --extra test python -m unittest tests.test_mcp_server.TestToolRegistration -v
```

Expected: FAIL — 18 tools missing.

- [ ] **Step 3: Add imports to mcp_server.py**

Add at module level (after `from prefab_sentinel.wsl_compat import to_wsl_path`, line 36):

```python
from prefab_sentinel.editor_bridge import send_action
from prefab_sentinel.patch_revert import revert_overrides as revert_overrides_impl
```

Mock targets will be `prefab_sentinel.mcp_server.send_action` and `prefab_sentinel.mcp_server.revert_overrides_impl`.

- [ ] **Step 4: Verify import compiles**

```bash
uv run python -c "from prefab_sentinel.mcp_server import create_server"
```

Expected: No errors.

---

### Task 2: Read-Only Editor Tools (9 tools)

`editor_screenshot`, `editor_select`, `editor_frame`, `editor_camera`, `editor_list_children`, `editor_list_materials`, `editor_list_roots`, `editor_get_material_property`, `editor_console`

**Files:**
- Modify: `prefab_sentinel/mcp_server.py` (before `return server`)
- Modify: `tests/test_mcp_server.py`

- [ ] **Step 1: Write tests for read-only editor tools**

Add `TestEditorReadOnlyTools` class to `tests/test_mcp_server.py`:

```python
class TestEditorReadOnlyTools(unittest.TestCase):
    """Test read-only editor bridge MCP tools."""

    def test_editor_screenshot_delegates(self) -> None:
        server = create_server()
        mock_response = {"success": True, "data": {"output_path": "/tmp/shot.png"}}
        with patch("prefab_sentinel.mcp_server.send_action", return_value=mock_response):
            _, result = _run(server.call_tool("editor_screenshot", {"view": "game", "width": 1920}))
        self.assertEqual(mock_response, result)

    def test_editor_screenshot_defaults(self) -> None:
        server = create_server()
        with patch("prefab_sentinel.mcp_server.send_action", return_value={"success": True}) as mock_send:
            _run(server.call_tool("editor_screenshot", {}))
        mock_send.assert_called_once_with(action="capture_screenshot", view="scene", width=0, height=0)

    def test_editor_select_delegates(self) -> None:
        server = create_server()
        with patch("prefab_sentinel.mcp_server.send_action", return_value={"success": True}) as mock_send:
            _, result = _run(server.call_tool("editor_select", {
                "hierarchy_path": "/Canvas/Panel",
                "prefab_asset_path": "Assets/UI.prefab",
            }))
        mock_send.assert_called_once_with(
            action="select_object", hierarchy_path="/Canvas/Panel", prefab_asset_path="Assets/UI.prefab",
        )
        self.assertTrue(result["success"])

    def test_editor_select_omits_empty_prefab_asset_path(self) -> None:
        server = create_server()
        with patch("prefab_sentinel.mcp_server.send_action", return_value={"success": True}) as mock_send:
            _run(server.call_tool("editor_select", {"hierarchy_path": "/Root/Child"}))
        _, kwargs = mock_send.call_args
        self.assertNotIn("prefab_asset_path", kwargs)

    def test_editor_frame_delegates(self) -> None:
        server = create_server()
        with patch("prefab_sentinel.mcp_server.send_action", return_value={"success": True}) as mock_send:
            _run(server.call_tool("editor_frame", {"zoom": 2.5}))
        mock_send.assert_called_once_with(action="frame_selected", zoom=2.5)

    def test_editor_camera_delegates(self) -> None:
        server = create_server()
        with patch("prefab_sentinel.mcp_server.send_action", return_value={"success": True}) as mock_send:
            _run(server.call_tool("editor_camera", {"yaw": 45.0, "pitch": 15.0, "distance": 3.0}))
        mock_send.assert_called_once_with(action="camera", yaw=45.0, pitch=15.0, distance=3.0)

    def test_editor_list_children_delegates(self) -> None:
        server = create_server()
        mock_response = {"success": True, "data": {"children": ["A", "B"]}}
        with patch("prefab_sentinel.mcp_server.send_action", return_value=mock_response):
            _, result = _run(server.call_tool("editor_list_children", {
                "hierarchy_path": "/Root", "list_depth": 2,
            }))
        self.assertEqual(mock_response, result)

    def test_editor_list_materials_delegates(self) -> None:
        server = create_server()
        with patch("prefab_sentinel.mcp_server.send_action", return_value={"success": True}) as mock_send:
            _run(server.call_tool("editor_list_materials", {"hierarchy_path": "/Body"}))
        mock_send.assert_called_once_with(action="list_materials", hierarchy_path="/Body")

    def test_editor_list_roots_delegates(self) -> None:
        server = create_server()
        with patch("prefab_sentinel.mcp_server.send_action", return_value={"success": True}) as mock_send:
            _run(server.call_tool("editor_list_roots", {}))
        mock_send.assert_called_once_with(action="list_roots")

    def test_editor_get_material_property_delegates(self) -> None:
        server = create_server()
        with patch("prefab_sentinel.mcp_server.send_action", return_value={"success": True}) as mock_send:
            _run(server.call_tool("editor_get_material_property", {
                "renderer_path": "/Body", "material_index": 0, "property_name": "_Color",
            }))
        mock_send.assert_called_once_with(
            action="get_material_property",
            renderer_path="/Body", material_index=0, property_name="_Color",
        )

    def test_editor_get_material_property_default_property_name(self) -> None:
        server = create_server()
        with patch("prefab_sentinel.mcp_server.send_action", return_value={"success": True}) as mock_send:
            _run(server.call_tool("editor_get_material_property", {
                "renderer_path": "/Body", "material_index": 0,
            }))
        mock_send.assert_called_once_with(
            action="get_material_property",
            renderer_path="/Body", material_index=0, property_name="",
        )

    def test_editor_console_delegates(self) -> None:
        server = create_server()
        with patch("prefab_sentinel.mcp_server.send_action", return_value={"success": True}) as mock_send:
            _run(server.call_tool("editor_console", {
                "max_entries": 50, "log_type_filter": "error", "since_seconds": 10.0,
            }))
        mock_send.assert_called_once_with(
            action="capture_console_logs",
            max_entries=50, log_type_filter="error", since_seconds=10.0,
        )

    def test_editor_console_defaults(self) -> None:
        server = create_server()
        with patch("prefab_sentinel.mcp_server.send_action", return_value={"success": True}) as mock_send:
            _run(server.call_tool("editor_console", {}))
        mock_send.assert_called_once_with(
            action="capture_console_logs",
            max_entries=200, log_type_filter="all", since_seconds=0.0,
        )

    def test_editor_frame_defaults(self) -> None:
        server = create_server()
        with patch("prefab_sentinel.mcp_server.send_action", return_value={"success": True}) as mock_send:
            _run(server.call_tool("editor_frame", {}))
        mock_send.assert_called_once_with(action="frame_selected", zoom=0.0)

    def test_editor_camera_defaults(self) -> None:
        server = create_server()
        with patch("prefab_sentinel.mcp_server.send_action", return_value={"success": True}) as mock_send:
            _run(server.call_tool("editor_camera", {}))
        mock_send.assert_called_once_with(action="camera", yaw=0.0, pitch=0.0, distance=0.0)

    def test_editor_list_children_default_depth(self) -> None:
        server = create_server()
        with patch("prefab_sentinel.mcp_server.send_action", return_value={"success": True}) as mock_send:
            _run(server.call_tool("editor_list_children", {"hierarchy_path": "/Root"}))
        mock_send.assert_called_once_with(action="list_children", hierarchy_path="/Root", list_depth=1)
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run --extra test python -m unittest tests.test_mcp_server.TestEditorReadOnlyTools -v
```

Expected: FAIL — tools not defined.

- [ ] **Step 3: Implement 9 read-only editor tools**

Add to `mcp_server.py` before `return server` (after the `check_field_coverage` tool, around line 782). Use a section comment:

```python
    # ------------------------------------------------------------------
    # Editor bridge tools (read-only)
    # ------------------------------------------------------------------

    @server.tool()
    def editor_screenshot(
        view: str = "scene",
        width: int = 0,
        height: int = 0,
    ) -> dict[str, Any]:
        """Capture a screenshot of the Unity Editor.

        Args:
            view: Which view to capture ("scene" or "game").
            width: Capture width in pixels (0 = current window size).
            height: Capture height in pixels (0 = current window size).
        """
        return send_action(action="capture_screenshot", view=view, width=width, height=height)

    @server.tool()
    def editor_select(
        hierarchy_path: str,
        prefab_asset_path: str = "",
    ) -> dict[str, Any]:
        """Select a GameObject in the Unity Hierarchy.

        Args:
            hierarchy_path: Hierarchy path of the GameObject (e.g. /Canvas/Panel/Button).
            prefab_asset_path: Asset path of a Prefab to open in Prefab Stage before selecting.
        """
        kwargs: dict[str, Any] = {"hierarchy_path": hierarchy_path}
        if prefab_asset_path:
            kwargs["prefab_asset_path"] = prefab_asset_path
        return send_action(action="select_object", **kwargs)

    @server.tool()
    def editor_frame(
        zoom: float = 0.0,
    ) -> dict[str, Any]:
        """Frame the selected object in Scene view.

        Args:
            zoom: Scene view distance factor (SceneView.size). 0 = keep current.
                Larger values zoom OUT, smaller values zoom IN. Typical: 0.1-5.0.
        """
        return send_action(action="frame_selected", zoom=zoom)

    @server.tool()
    def editor_camera(
        yaw: float = 0.0,
        pitch: float = 0.0,
        distance: float = 0.0,
    ) -> dict[str, Any]:
        """Set Scene view camera orientation.

        All parameters default to 0.0 (no change). Set only the values you want to modify.

        Args:
            yaw: Horizontal rotation in degrees.
            pitch: Vertical rotation in degrees.
            distance: Distance from pivot point.
        """
        return send_action(action="camera", yaw=yaw, pitch=pitch, distance=distance)

    @server.tool()
    def editor_list_children(
        hierarchy_path: str,
        list_depth: int = 1,
    ) -> dict[str, Any]:
        """List children of a GameObject in the running scene.

        Args:
            hierarchy_path: Hierarchy path to the parent GameObject.
            list_depth: Maximum depth to traverse (default: 1).
        """
        return send_action(action="list_children", hierarchy_path=hierarchy_path, list_depth=list_depth)

    @server.tool()
    def editor_list_materials(
        hierarchy_path: str,
    ) -> dict[str, Any]:
        """List material slots on renderers under a GameObject at runtime.

        Args:
            hierarchy_path: Hierarchy path to the root GameObject.
        """
        return send_action(action="list_materials", hierarchy_path=hierarchy_path)

    @server.tool()
    def editor_list_roots() -> dict[str, Any]:
        """List root GameObjects in the current Scene or Prefab Stage."""
        return send_action(action="list_roots")

    @server.tool()
    def editor_get_material_property(
        renderer_path: str,
        material_index: int,
        property_name: str = "",
    ) -> dict[str, Any]:
        """Read shader property values from a material at runtime.

        Args:
            renderer_path: Hierarchy path to the GameObject with a Renderer.
            material_index: Material slot index (0-based).
            property_name: Shader property to read (empty = list all properties).
        """
        return send_action(
            action="get_material_property",
            renderer_path=renderer_path, material_index=material_index,
            property_name=property_name,
        )

    @server.tool()
    def editor_console(
        max_entries: int = 200,
        log_type_filter: str = "all",
        since_seconds: float = 0.0,
    ) -> dict[str, Any]:
        """Capture Unity Console log entries as structured data.

        Args:
            max_entries: Maximum number of log entries to retrieve (default: 200).
            log_type_filter: Filter by log type: "all", "error", "warning", "exception".
            since_seconds: Only entries from the last N seconds (0 = no time filter).
        """
        return send_action(
            action="capture_console_logs",
            max_entries=max_entries, log_type_filter=log_type_filter,
            since_seconds=since_seconds,
        )
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
uv run --extra test python -m unittest tests.test_mcp_server.TestEditorReadOnlyTools -v
```

Expected: All 16 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add prefab_sentinel/mcp_server.py tests/test_mcp_server.py
git commit -m "feat(mcp): add 9 read-only editor bridge tools"
```

---

### Task 3: Side-Effect Editor Tools (3 tools)

`editor_refresh`, `editor_recompile`, `editor_run_tests`

**Files:**
- Modify: `prefab_sentinel/mcp_server.py`
- Modify: `tests/test_mcp_server.py`

- [ ] **Step 1: Write tests**

```python
class TestEditorSideEffectTools(unittest.TestCase):
    """Test side-effect editor bridge MCP tools."""

    def test_editor_refresh_delegates(self) -> None:
        server = create_server()
        with patch("prefab_sentinel.mcp_server.send_action", return_value={"success": True}) as mock_send:
            _, result = _run(server.call_tool("editor_refresh", {}))
        mock_send.assert_called_once_with(action="refresh_asset_database")
        self.assertTrue(result["success"])

    def test_editor_recompile_delegates(self) -> None:
        server = create_server()
        with patch("prefab_sentinel.mcp_server.send_action", return_value={"success": True}) as mock_send:
            _run(server.call_tool("editor_recompile", {}))
        mock_send.assert_called_once_with(action="recompile_scripts")

    def test_editor_run_tests_delegates(self) -> None:
        server = create_server()
        with patch("prefab_sentinel.mcp_server.send_action", return_value={"success": True}) as mock_send:
            _run(server.call_tool("editor_run_tests", {}))
        mock_send.assert_called_once_with(action="run_integration_tests", timeout_sec=300)

    def test_editor_run_tests_custom_timeout(self) -> None:
        server = create_server()
        with patch("prefab_sentinel.mcp_server.send_action", return_value={"success": True}) as mock_send:
            _run(server.call_tool("editor_run_tests", {"timeout_sec": 600}))
        mock_send.assert_called_once_with(action="run_integration_tests", timeout_sec=600)
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run --extra test python -m unittest tests.test_mcp_server.TestEditorSideEffectTools -v
```

- [ ] **Step 3: Implement 3 side-effect editor tools**

Add to `mcp_server.py` after the read-only tools:

```python
    # ------------------------------------------------------------------
    # Editor bridge tools (side-effect)
    # ------------------------------------------------------------------

    @server.tool()
    def editor_refresh() -> dict[str, Any]:
        """Trigger AssetDatabase.Refresh() in the running Unity Editor."""
        return send_action(action="refresh_asset_database")

    @server.tool()
    def editor_recompile() -> dict[str, Any]:
        """Trigger C# script recompilation in the running Unity Editor."""
        return send_action(action="recompile_scripts")

    @server.tool()
    def editor_run_tests(
        timeout_sec: int = 300,
    ) -> dict[str, Any]:
        """Run Unity integration tests via Editor Bridge.

        Args:
            timeout_sec: Maximum wait time in seconds (default: 300).
        """
        return send_action(action="run_integration_tests", timeout_sec=timeout_sec)
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
uv run --extra test python -m unittest tests.test_mcp_server.TestEditorSideEffectTools -v
```

Expected: All 4 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add prefab_sentinel/mcp_server.py tests/test_mcp_server.py
git commit -m "feat(mcp): add 3 side-effect editor bridge tools"
```

---

### Task 4: Write Editor Tools (3 tools)

`editor_instantiate`, `editor_set_material`, `editor_delete`

**Files:**
- Modify: `prefab_sentinel/mcp_server.py`
- Modify: `tests/test_mcp_server.py`

- [ ] **Step 1: Write tests**

```python
class TestEditorWriteTools(unittest.TestCase):
    """Test write/mutation editor bridge MCP tools."""

    def test_editor_instantiate_delegates(self) -> None:
        server = create_server()
        with patch("prefab_sentinel.mcp_server.send_action", return_value={"success": True}) as mock_send:
            _run(server.call_tool("editor_instantiate", {
                "prefab_path": "Assets/Prefabs/Mic.prefab",
                "parent_path": "/Canvas",
                "position": "0,1.5,0",
            }))
        mock_send.assert_called_once_with(
            action="instantiate_to_scene",
            prefab_path="Assets/Prefabs/Mic.prefab",
            parent_path="/Canvas",
            position=[0.0, 1.5, 0.0],
        )

    def test_editor_instantiate_no_position(self) -> None:
        server = create_server()
        with patch("prefab_sentinel.mcp_server.send_action", return_value={"success": True}) as mock_send:
            _run(server.call_tool("editor_instantiate", {
                "prefab_path": "Assets/Prefabs/Mic.prefab",
            }))
        mock_send.assert_called_once_with(
            action="instantiate_to_scene",
            prefab_path="Assets/Prefabs/Mic.prefab",
            parent_path="",
        )

    def test_editor_instantiate_invalid_position_count(self) -> None:
        server = create_server()
        with patch("prefab_sentinel.mcp_server.send_action"):
            _, result = _run(server.call_tool("editor_instantiate", {
                "prefab_path": "Assets/X.prefab",
                "position": "1,2",
            }))
        self.assertFalse(result["success"])
        self.assertEqual("INVALID_POSITION", result["code"])

    def test_editor_instantiate_invalid_position_value(self) -> None:
        server = create_server()
        with patch("prefab_sentinel.mcp_server.send_action"):
            _, result = _run(server.call_tool("editor_instantiate", {
                "prefab_path": "Assets/X.prefab",
                "position": "a,b,c",
            }))
        self.assertFalse(result["success"])
        self.assertEqual("INVALID_POSITION", result["code"])

    def test_editor_set_material_delegates(self) -> None:
        server = create_server()
        with patch("prefab_sentinel.mcp_server.send_action", return_value={"success": True}) as mock_send:
            _run(server.call_tool("editor_set_material", {
                "renderer_path": "/Body",
                "material_index": 0,
                "material_guid": "abc123def456",
            }))
        mock_send.assert_called_once_with(
            action="set_material",
            renderer_path="/Body", material_index=0, material_guid="abc123def456",
        )

    def test_editor_delete_delegates(self) -> None:
        server = create_server()
        with patch("prefab_sentinel.mcp_server.send_action", return_value={"success": True}) as mock_send:
            _run(server.call_tool("editor_delete", {"hierarchy_path": "/OldObject"}))
        mock_send.assert_called_once_with(action="delete_object", hierarchy_path="/OldObject")

```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run --extra test python -m unittest tests.test_mcp_server.TestEditorWriteTools -v
```

- [ ] **Step 3: Implement 3 write editor tools**

Add to `mcp_server.py` after the side-effect tools:

```python
    # ------------------------------------------------------------------
    # Editor bridge tools (write / mutation)
    # ------------------------------------------------------------------

    @server.tool()
    def editor_instantiate(
        prefab_path: str,
        parent_path: str = "",
        position: str = "",
    ) -> dict[str, Any]:
        """Instantiate a Prefab into the current Scene.

        Args:
            prefab_path: Asset path of the prefab (e.g. Assets/Prefabs/Mic.prefab).
            parent_path: Hierarchy path of the parent GameObject (empty = scene root).
            position: Local position as "x,y,z" string (e.g. "0,1.5,0"). Empty = default.
        """
        kwargs: dict[str, Any] = {"prefab_path": prefab_path, "parent_path": parent_path}
        if position:
            try:
                parts = [float(v) for v in position.split(",")]
            except ValueError:
                return {
                    "success": False, "severity": "error", "code": "INVALID_POSITION",
                    "message": f"Non-numeric position values: {position} (expected x,y,z)",
                    "data": {}, "diagnostics": [],
                }
            if len(parts) != 3:
                return {
                    "success": False, "severity": "error", "code": "INVALID_POSITION",
                    "message": f"position requires exactly 3 values (x,y,z), got {len(parts)}",
                    "data": {}, "diagnostics": [],
                }
            kwargs["position"] = parts
        return send_action(action="instantiate_to_scene", **kwargs)

    @server.tool()
    def editor_set_material(
        renderer_path: str,
        material_index: int,
        material_guid: str,
    ) -> dict[str, Any]:
        """Replace a material slot on a Renderer at runtime (Undo-able).

        Args:
            renderer_path: Hierarchy path to the GameObject with a Renderer.
            material_index: Material slot index (0-based).
            material_guid: GUID of the replacement Material asset (32-char hex).
        """
        return send_action(
            action="set_material",
            renderer_path=renderer_path, material_index=material_index,
            material_guid=material_guid,
        )

    @server.tool()
    def editor_delete(
        hierarchy_path: str,
    ) -> dict[str, Any]:
        """Delete a GameObject from the scene hierarchy (Undo-able).

        Args:
            hierarchy_path: Hierarchy path to the GameObject to delete.
        """
        return send_action(action="delete_object", hierarchy_path=hierarchy_path)
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
uv run --extra test python -m unittest tests.test_mcp_server.TestEditorWriteTools -v
```

Expected: All 6 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add prefab_sentinel/mcp_server.py tests/test_mcp_server.py
git commit -m "feat(mcp): add 3 write editor bridge tools"
```

---

## Batch B: Inspection & Revert Tools (3 tools)

### Task 5: inspect_materials + validate_structure

**Files:**
- Modify: `prefab_sentinel/mcp_server.py`
- Modify: `tests/test_mcp_server.py`

- [ ] **Step 1: Write tests**

```python
class TestInspectionTools(unittest.TestCase):
    """Test inspect_materials and validate_structure MCP tools."""

    def test_inspect_materials_delegates(self) -> None:
        mock_resp = MagicMock()
        mock_resp.to_dict.return_value = {
            "success": True, "data": {"renderers": []},
        }
        mock_orch = MagicMock()
        mock_orch.inspect_materials.return_value = mock_resp

        server = create_server()
        with patch("prefab_sentinel.session.Phase1Orchestrator") as mock_cls:
            mock_cls.default.return_value = mock_orch
            _, result = _run(server.call_tool("inspect_materials", {
                "path": "Assets/Avatar.prefab",
            }))

        self.assertTrue(result["success"])
        mock_orch.inspect_materials.assert_called_once_with(
            target_path="Assets/Avatar.prefab",
        )

    def test_validate_structure_delegates(self) -> None:
        mock_resp = MagicMock()
        mock_resp.to_dict.return_value = {
            "success": True, "data": {"issues": []},
        }
        mock_orch = MagicMock()
        mock_orch.inspect_structure.return_value = mock_resp

        server = create_server()
        with patch("prefab_sentinel.session.Phase1Orchestrator") as mock_cls:
            mock_cls.default.return_value = mock_orch
            _, result = _run(server.call_tool("validate_structure", {
                "path": "Assets/Scene.unity",
            }))

        self.assertTrue(result["success"])
        mock_orch.inspect_structure.assert_called_once_with(
            target_path="Assets/Scene.unity",
        )
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run --extra test python -m unittest tests.test_mcp_server.TestInspectionTools -v
```

- [ ] **Step 3: Implement inspect_materials and validate_structure**

Add to `mcp_server.py` after the editor tools:

```python
    # ------------------------------------------------------------------
    # Inspection tools (orchestrator-backed)
    # ------------------------------------------------------------------

    @server.tool()
    def inspect_materials(path: str) -> dict[str, Any]:
        """Show per-renderer material slot assignments with override/inherited markers.

        Args:
            path: Path to a .prefab or .unity file.
        """
        orch = session.get_orchestrator()
        resp = orch.inspect_materials(target_path=path)
        return resp.to_dict()

    @server.tool()
    def validate_structure(path: str) -> dict[str, Any]:
        """Validate internal YAML structure (fileID duplicates, Transform consistency).

        Args:
            path: Path to a .prefab, .unity, or .asset file.
        """
        orch = session.get_orchestrator()
        resp = orch.inspect_structure(target_path=path)
        return resp.to_dict()
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
uv run --extra test python -m unittest tests.test_mcp_server.TestInspectionTools -v
```

Expected: All 2 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add prefab_sentinel/mcp_server.py tests/test_mcp_server.py
git commit -m "feat(mcp): add inspect_materials and validate_structure tools"
```

---

### Task 6: revert_overrides

**Files:**
- Modify: `prefab_sentinel/mcp_server.py`
- Modify: `tests/test_mcp_server.py`

- [ ] **Step 1: Write tests**

```python
class TestRevertOverridesTool(unittest.TestCase):
    """Test revert_overrides MCP tool."""

    def test_dry_run_default(self) -> None:
        mock_resp = MagicMock()
        mock_resp.to_dict.return_value = {
            "success": True, "code": "REVERT_DRY_RUN",
            "data": {"match_count": 1, "read_only": True},
        }
        server = create_server()
        with patch(
            "prefab_sentinel.mcp_server.revert_overrides_impl",
            return_value=mock_resp,
        ) as mock_revert:
            _, result = _run(server.call_tool("revert_overrides", {
                "variant_path": "Assets/V.prefab",
                "target_file_id": "12345",
                "property_path": "m_Color.r",
            }))

        mock_revert.assert_called_once_with(
            variant_path="Assets/V.prefab",
            target_file_id="12345",
            property_path="m_Color.r",
            dry_run=True,
            confirm=False,
            change_reason=None,
        )
        self.assertTrue(result["success"])

    def test_confirm_mode(self) -> None:
        mock_resp = MagicMock()
        mock_resp.to_dict.return_value = {
            "success": True, "code": "REVERT_APPLIED",
            "data": {"match_count": 1, "read_only": False},
        }
        server = create_server()
        with patch(
            "prefab_sentinel.mcp_server.revert_overrides_impl",
            return_value=mock_resp,
        ) as mock_revert:
            _, result = _run(server.call_tool("revert_overrides", {
                "variant_path": "Assets/V.prefab",
                "target_file_id": "12345",
                "property_path": "m_Color.r",
                "confirm": True,
                "change_reason": "Remove unwanted override",
            }))

        mock_revert.assert_called_once_with(
            variant_path="Assets/V.prefab",
            target_file_id="12345",
            property_path="m_Color.r",
            dry_run=False,
            confirm=True,
            change_reason="Remove unwanted override",
        )
        self.assertTrue(result["success"])

    def test_empty_change_reason_becomes_none(self) -> None:
        mock_resp = MagicMock()
        mock_resp.to_dict.return_value = {"success": True}
        server = create_server()
        with patch(
            "prefab_sentinel.mcp_server.revert_overrides_impl",
            return_value=mock_resp,
        ) as mock_revert:
            _run(server.call_tool("revert_overrides", {
                "variant_path": "Assets/V.prefab",
                "target_file_id": "12345",
                "property_path": "m_Color.r",
                "change_reason": "",
            }))

        _, kwargs = mock_revert.call_args
        self.assertIsNone(kwargs["change_reason"])
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run --extra test python -m unittest tests.test_mcp_server.TestRevertOverridesTool -v
```

- [ ] **Step 3: Implement revert_overrides**

Add tool to `mcp_server.py` after inspection tools (import was already added in Task 1):

```python
    # ------------------------------------------------------------------
    # Revert tool
    # ------------------------------------------------------------------

    @server.tool()
    def revert_overrides(
        variant_path: str,
        target_file_id: str,
        property_path: str,
        confirm: bool = False,
        change_reason: str = "",
    ) -> dict[str, Any]:
        """Remove a specific property override from a Prefab Variant.

        Two-phase workflow:
        - confirm=False (default): dry-run preview showing what would be removed.
        - confirm=True: applies the removal and writes back.

        Args:
            variant_path: Path to the Prefab Variant file.
            target_file_id: fileID of the target component in the parent prefab.
            property_path: propertyPath of the override to remove.
            confirm: Set True to apply (False = dry-run only).
            change_reason: Required when confirm=True. Audit log reason.
        """
        resp = revert_overrides_impl(
            variant_path=variant_path,
            target_file_id=target_file_id,
            property_path=property_path,
            dry_run=not confirm,
            confirm=confirm,
            change_reason=change_reason or None,
        )
        return resp.to_dict()
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
uv run --extra test python -m unittest tests.test_mcp_server.TestRevertOverridesTool -v
```

Expected: All 3 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add prefab_sentinel/mcp_server.py tests/test_mcp_server.py
git commit -m "feat(mcp): add revert_overrides tool"
```

---

## Batch C: Registration Verification + Docs

### Task 7: Full Registration Test + Full Test Suite

- [ ] **Step 1: Run registration test**

```bash
uv run --extra test python -m unittest tests.test_mcp_server.TestToolRegistration -v
```

Expected: PASS — 33 tools registered.

- [ ] **Step 2: Run full test suite**

```bash
uv run --extra test python scripts/run_unit_tests.py
```

Expected: All tests pass (previous count + ~28 new tests).

- [ ] **Step 3: Compile check**

```bash
uv run python -m compileall prefab_sentinel/mcp_server.py tests/test_mcp_server.py
```

Expected: No compilation errors.

---

### Task 8: README Update

**Files:**
- Modify: `README.md` (MCP tool table section)

- [ ] **Step 1: Find MCP tool section in README**

Search for the existing MCP tools table in `README.md`.

- [ ] **Step 2: Add 18 new tools to the table**

Add the following entries to the MCP tools table, grouped by category:

**Editor Bridge (read-only):**
| `editor_screenshot` | Capture Scene/Game view screenshot | `view`, `width`, `height` |
| `editor_select` | Select a GameObject in the Hierarchy | `hierarchy_path`, `prefab_asset_path` |
| `editor_frame` | Frame selected object in Scene view | `zoom` |
| `editor_camera` | Set Scene view camera orientation | `yaw`, `pitch`, `distance` |
| `editor_list_children` | List children of a GameObject | `hierarchy_path`, `list_depth` |
| `editor_list_materials` | List material slots on renderers | `hierarchy_path` |
| `editor_list_roots` | List root GameObjects | — |
| `editor_get_material_property` | Read shader property values | `renderer_path`, `material_index`, `property_name` |

**Editor Bridge (side-effect):**
| `editor_refresh` | Trigger AssetDatabase.Refresh() | — |
| `editor_recompile` | Trigger script recompilation | — |
| `editor_run_tests` | Run integration tests | `timeout_sec` |

**Editor Bridge (write):**
| `editor_instantiate` | Instantiate Prefab into Scene | `prefab_path`, `parent_path`, `position` |
| `editor_set_material` | Replace a material slot | `renderer_path`, `material_index`, `material_guid` |
| `editor_delete` | Delete a GameObject | `hierarchy_path` |
| `editor_console` | Capture console log entries | `max_entries`, `log_type_filter`, `since_seconds` |

**Inspection:**
| `inspect_materials` | Show material slot assignments | `path` |
| `validate_structure` | Validate YAML structure | `path` |

**Revert:**
| `revert_overrides` | Remove Variant property override | `variant_path`, `target_file_id`, `property_path`, `confirm`, `change_reason` |

- [ ] **Step 3: Commit**

```bash
git add README.md
git commit -m "docs: add 18 new MCP tools to README"
```

---

### Task 9: Final Verification

- [ ] **Step 1: Run full test suite one more time**

```bash
uv run --extra test python scripts/run_unit_tests.py
```

- [ ] **Step 2: Record test count and result**

Update `tasks/todo.md` with verification results.
