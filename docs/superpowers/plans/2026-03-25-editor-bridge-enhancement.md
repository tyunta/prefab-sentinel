# Editor Bridge 基盤強化 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Enhance the Editor Bridge with camera GET/SET (6DoF), auto-refresh after write operations, and improved bridge status reporting.

**Architecture:** Three independent features layered on the existing file-based Bridge protocol. Camera GET/SET replaces the old `editor_camera` with two new actions (`get_camera`/`set_camera`). Auto-refresh adds a shared helper to the orchestrator that calls `refresh_asset_database` after confirmed writes. Bridge status extends `get_project_status` with connection info.

**Tech Stack:** Python 3.11+, Unity C# (EditorBridge), MCP (FastMCP), unittest

**Spec:** `docs/superpowers/specs/2026-03-25-prefab-sentinel-roadmap-design.md` — Phase 1

---

## File Structure

| Action | Path | Responsibility |
|--------|------|----------------|
| Modify | `prefab_sentinel/editor_bridge.py` | Replace `"camera"` with `"get_camera"`, `"set_camera"` in SUPPORTED_ACTIONS; add `bridge_status()` helper |
| Modify | `prefab_sentinel/mcp_server.py:834-849` | Replace `editor_camera` with `editor_get_camera` / `editor_set_camera`; update `get_project_status` |
| Modify | `prefab_sentinel/mcp_server.py:393-510,513-610,613-715,1106-1170,1177-1205` | Add auto-refresh call after confirmed writes |
| Modify | `prefab_sentinel/orchestrator.py` | Add `_maybe_auto_refresh()` helper |
| Modify | `tools/unity/PrefabSentinel.UnityEditorControlBridge.cs` | Replace `HandleCamera` with `HandleGetCamera`/`HandleSetCamera`; add camera fields to DTOs |
| Modify | `tests/test_editor_bridge.py` | Add tests for new actions, bridge_status |
| Create | `tests/test_auto_refresh.py` | Tests for auto-refresh logic |
| Modify | `tests/test_mcp_server.py` | Update tool registration test |

---

## Task 1: Camera GET — Unity C# Handler

**Files:**
- Modify: `tools/unity/PrefabSentinel.UnityEditorControlBridge.cs:22-40` (SupportedActions)
- Modify: `tools/unity/PrefabSentinel.UnityEditorControlBridge.cs:133-155` (EditorControlData)
- Modify: `tools/unity/PrefabSentinel.UnityEditorControlBridge.cs:220-245` (dispatch switch)
- Modify: `tools/unity/PrefabSentinel.UnityEditorControlBridge.cs:728-751` (HandleCamera → replace)

- [ ] **Step 1: Add `get_camera` to SupportedActions, replace `"camera"` with `"set_camera"`**

In `SupportedActions` (line 22-40), replace `"camera"` with `"get_camera"` and `"set_camera"`:

```csharp
public static readonly HashSet<string> SupportedActions = new HashSet<string>
{
    "capture_screenshot",
    "select_object",
    "frame_selected",
    "instantiate_to_scene",
    "ping_object",
    "capture_console_logs",
    "refresh_asset_database",
    "recompile_scripts",
    "set_material",
    "delete_object",
    "list_children",
    "list_materials",
    "get_camera",
    "set_camera",
    "list_roots",
    "get_material_property",
    "run_integration_tests",
};
```

- [ ] **Step 2: Extend EditorControlRequest with new camera fields**

In `EditorControlRequest` (line 78-81), replace the camera section:

```csharp
// camera (get_camera / set_camera)
// Mode A (absolute): position + rotation + size
public float[] camera_position = null;   // [x, y, z] world coords
public float[] camera_rotation = null;   // [yaw, pitch, roll] euler degrees
public float camera_size = -1f;          // SceneView.size; -1 = keep current
// Mode B (pivot orbit): pivot + yaw/pitch/distance
public float[] camera_pivot = null;      // [x, y, z] pivot point
public float yaw = float.NaN;           // NaN = keep current
public float pitch = float.NaN;
public float distance = -1f;             // -1 = keep current
// Shared
public int camera_orthographic = -1;     // -1 = keep, 0 = perspective, 1 = ortho
```

- [ ] **Step 3: Extend EditorControlData with full camera state**

In `EditorControlData` (line 134-155), replace `camera_yaw/pitch/distance` with:

```csharp
// Camera state (full 6DoF)
public float[] camera_position = null;     // [x, y, z]
public float[] camera_rotation_quat = null; // [x, y, z, w] quaternion
public float[] camera_euler = null;        // [yaw, pitch, roll]
public float[] camera_pivot = null;        // [x, y, z]
public float camera_size = 0f;
public bool camera_orthographic = false;
```

- [ ] **Step 4: Add dispatch cases for `get_camera` and `set_camera`**

In the switch statement (around line 234-236), replace:
```csharp
case "camera":
    response = HandleCamera(request);
    break;
```
with:
```csharp
case "get_camera":
    response = HandleGetCamera();
    break;
case "set_camera":
    response = HandleSetCamera(request);
    break;
```

- [ ] **Step 5: Implement `HandleGetCamera`**

Replace `HandleCamera` (line 728-751) with:

```csharp
private static EditorControlResponse HandleGetCamera()
{
    SceneView sceneView = SceneView.lastActiveSceneView;
    if (sceneView == null)
        return BuildError("EDITOR_CTRL_NO_SCENE_VIEW", "No active SceneView found.");

    Vector3 pos = sceneView.camera.transform.position;
    Quaternion rot = sceneView.rotation;
    Vector3 euler = rot.eulerAngles;
    Vector3 pivot = sceneView.pivot;

    // Convert Unity euler (y=yaw applied raw) to our convention: yaw=0 means front (+Z)
    float yaw = (euler.y + 180f) % 360f;
    float pitchVal = euler.x > 180f ? euler.x - 360f : euler.x;

    return BuildSuccess("EDITOR_CTRL_CAMERA_GET_OK",
        $"Camera position=({pos.x:F2}, {pos.y:F2}, {pos.z:F2})",
        data: new EditorControlData
        {
            camera_position = new[] { pos.x, pos.y, pos.z },
            camera_rotation_quat = new[] { rot.x, rot.y, rot.z, rot.w },
            camera_euler = new[] { yaw, pitchVal, 0f },
            camera_pivot = new[] { pivot.x, pivot.y, pivot.z },
            camera_size = sceneView.size,
            camera_orthographic = sceneView.orthographic,
            executed = true
        });
}
```

- [ ] **Step 6: Implement `HandleSetCamera`**

```csharp
private static EditorControlResponse HandleSetCamera(EditorControlRequest request)
{
    SceneView sceneView = SceneView.lastActiveSceneView;
    if (sceneView == null)
        return BuildError("EDITOR_CTRL_NO_SCENE_VIEW", "No active SceneView found.");

    bool hasModeA = request.camera_position != null || request.camera_rotation != null;
    bool hasModeB = request.camera_pivot != null || !float.IsNaN(request.yaw) || !float.IsNaN(request.pitch);

    if (hasModeA && hasModeB)
        return BuildError("EDITOR_CTRL_CAMERA_CONFLICT",
            "Cannot mix Mode A (position/rotation) and Mode B (pivot/yaw/pitch). Use one mode.");

    if (hasModeA)
    {
        // Mode A: absolute position/rotation
        if (request.camera_position != null && request.camera_position.Length == 3)
        {
            sceneView.pivot = new Vector3(
                request.camera_position[0],
                request.camera_position[1],
                request.camera_position[2]);
            // Position sets pivot directly; SceneView computes camera pos from pivot+rotation+size
        }
        if (request.camera_rotation != null && request.camera_rotation.Length == 3)
        {
            // Our convention: yaw=0 = front (+Z). Unity: yaw=0 = back. Offset by 180.
            float internalYaw = (request.camera_rotation[0] + 180f) % 360f;
            float internalPitch = request.camera_rotation[1];
            sceneView.rotation = Quaternion.Euler(internalPitch, internalYaw, 0f);
        }
        if (request.camera_size >= 0f)
            sceneView.size = request.camera_size;
    }
    else if (hasModeB)
    {
        // Mode B: pivot orbit
        if (request.camera_pivot != null && request.camera_pivot.Length == 3)
        {
            sceneView.pivot = new Vector3(
                request.camera_pivot[0],
                request.camera_pivot[1],
                request.camera_pivot[2]);
        }
        if (!float.IsNaN(request.yaw) || !float.IsNaN(request.pitch))
        {
            Vector3 currentEuler = sceneView.rotation.eulerAngles;
            // Recover current yaw in our convention
            float curYaw = (currentEuler.y + 180f) % 360f;
            float curPitch = currentEuler.x > 180f ? currentEuler.x - 360f : currentEuler.x;

            float newYaw = float.IsNaN(request.yaw) ? curYaw : request.yaw;
            float newPitch = float.IsNaN(request.pitch) ? curPitch : request.pitch;

            // Convert back to Unity convention
            float internalYaw = (newYaw + 180f) % 360f;
            sceneView.rotation = Quaternion.Euler(newPitch, internalYaw, 0f);
        }
        if (request.distance >= 0f)
            sceneView.size = request.distance;
    }
    // else: no camera params → no-op, just return current state

    if (request.camera_orthographic >= 0)
        sceneView.orthographic = request.camera_orthographic == 1;

    sceneView.Repaint();

    // Return updated state (same as HandleGetCamera)
    Vector3 pos = sceneView.camera.transform.position;
    Quaternion rot = sceneView.rotation;
    Vector3 euler = rot.eulerAngles;
    Vector3 piv = sceneView.pivot;
    float retYaw = (euler.y + 180f) % 360f;
    float retPitch = euler.x > 180f ? euler.x - 360f : euler.x;

    return BuildSuccess("EDITOR_CTRL_CAMERA_SET_OK",
        $"Camera updated",
        data: new EditorControlData
        {
            camera_position = new[] { pos.x, pos.y, pos.z },
            camera_rotation_quat = new[] { rot.x, rot.y, rot.z, rot.w },
            camera_euler = new[] { retYaw, retPitch, 0f },
            camera_pivot = new[] { piv.x, piv.y, piv.z },
            camera_size = sceneView.size,
            camera_orthographic = sceneView.orthographic,
            executed = true
        });
}
```

- [ ] **Step 7: Commit Unity C# changes**

```bash
git add tools/unity/PrefabSentinel.UnityEditorControlBridge.cs
git commit -m "feat(bridge): replace HandleCamera with HandleGetCamera/HandleSetCamera (6DoF + pivot)"
```

---

## Task 2: Camera GET/SET — Python Bridge + MCP Tools

**Files:**
- Modify: `prefab_sentinel/editor_bridge.py:33-52` (SUPPORTED_ACTIONS)
- Modify: `prefab_sentinel/mcp_server.py:834-849` (editor_camera → new tools)
- Modify: `tests/test_mcp_server.py:43-67` (tool registration)
- Modify: `tests/test_editor_bridge.py` (SUPPORTED_ACTIONS reference)

- [ ] **Step 1: Update SUPPORTED_ACTIONS in editor_bridge.py**

Replace `"camera"` (line 47) with `"get_camera"` and `"set_camera"`:

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
        "run_integration_tests",
    }
)
```

- [ ] **Step 2: Replace `editor_camera` with two new MCP tools in mcp_server.py**

Delete the `editor_camera` tool (lines 834-849) and replace with:

```python
@server.tool()
def editor_get_camera() -> dict[str, Any]:
    """Get current Scene view camera state.

    Returns position, rotation (quaternion + euler), pivot, size, and
    orthographic mode. Euler uses yaw=0 as front (+Z direction).
    """
    return send_action(action="get_camera")

@server.tool()
def editor_set_camera(
    position: str = "",
    rotation: str = "",
    size: float = -1.0,
    pivot: str = "",
    yaw: float = float("nan"),
    pitch: float = float("nan"),
    distance: float = -1.0,
    orthographic: int = -1,
) -> dict[str, Any]:
    """Set Scene view camera. Two modes (cannot mix):

    Mode A (absolute): position, rotation, size
    Mode B (pivot orbit): pivot, yaw, pitch, distance

    Euler convention: yaw=0 = front (+Z direction).
    Omitted params keep their current value.

    Args:
        position: JSON '{\"x\":0,\"y\":1,\"z\":-5}' — world position.
        rotation: JSON '{\"yaw\":0,\"pitch\":15,\"roll\":0}' — euler degrees.
        size: SceneView zoom level (>=0 to set, -1 = keep).
        pivot: JSON '{\"x\":0,\"y\":0,\"z\":0}' — orbit center.
        yaw: Horizontal rotation in degrees (Mode B).
        pitch: Vertical rotation in degrees (Mode B).
        distance: Distance from pivot (Mode B, >=0 to set, -1 = keep).
        orthographic: -1=keep, 0=perspective, 1=orthographic.
    """
    import json as _json
    import math

    kwargs: dict[str, Any] = {}

    # Mode A params
    if position:
        p = _json.loads(position)
        kwargs["camera_position"] = [p["x"], p["y"], p["z"]]
    if rotation:
        r = _json.loads(rotation)
        kwargs["camera_rotation"] = [r.get("yaw", 0), r.get("pitch", 0), r.get("roll", 0)]
    if size >= 0:
        kwargs["camera_size"] = size

    # Mode B params
    if pivot:
        pv = _json.loads(pivot)
        kwargs["camera_pivot"] = [pv["x"], pv["y"], pv["z"]]
    if not math.isnan(yaw):
        kwargs["yaw"] = yaw
    if not math.isnan(pitch):
        kwargs["pitch"] = pitch
    if distance >= 0:
        kwargs["distance"] = distance

    # Shared
    if orthographic >= 0:
        kwargs["camera_orthographic"] = orthographic

    return send_action(action="set_camera", **kwargs)
```

- [ ] **Step 3: Update tool registration test**

In `tests/test_mcp_server.py`, update the expected tool set (line 52):

Replace `"editor_camera"` with `"editor_get_camera", "editor_set_camera"`.

Update `test_tool_count` (line 67): change `37` to `38` (one tool became two).

- [ ] **Step 4: Run tests to verify registration**

```bash
cd /mnt/d/git/prefab-sentinel && uv run python -m pytest tests/test_mcp_server.py::TestToolRegistration -v
```

Expected: PASS (2 tests)

- [ ] **Step 5: Commit Python camera changes**

```bash
git add prefab_sentinel/editor_bridge.py prefab_sentinel/mcp_server.py tests/test_mcp_server.py
git commit -m "feat(mcp): replace editor_camera with editor_get_camera/editor_set_camera"
```

---

## Task 3: Camera Unit Tests

**Files:**
- Modify: `tests/test_editor_bridge.py`

- [ ] **Step 1: Write test for new action names in SUPPORTED_ACTIONS**

Add to `tests/test_editor_bridge.py`:

```python
class TestCameraActions(unittest.TestCase):
    """Tests for get_camera / set_camera action validation."""

    def test_get_camera_in_supported_actions(self) -> None:
        self.assertIn("get_camera", SUPPORTED_ACTIONS)

    def test_set_camera_in_supported_actions(self) -> None:
        self.assertIn("set_camera", SUPPORTED_ACTIONS)

    def test_old_camera_removed(self) -> None:
        self.assertNotIn("camera", SUPPORTED_ACTIONS)

    def test_get_camera_env_missing(self) -> None:
        """get_camera returns bridge error when env not configured."""
        with patch.dict(os.environ, {BRIDGE_MODE_ENV: ""}, clear=False):
            result = send_action(action="get_camera")
            self.assertFalse(result["success"])
            self.assertEqual("EDITOR_BRIDGE_MODE", result["code"])

    def test_set_camera_env_missing(self) -> None:
        """set_camera returns bridge error when env not configured."""
        with patch.dict(os.environ, {BRIDGE_MODE_ENV: ""}, clear=False):
            result = send_action(action="set_camera", yaw=0.0)
            self.assertFalse(result["success"])
            self.assertEqual("EDITOR_BRIDGE_MODE", result["code"])
```

- [ ] **Step 2: Run tests**

```bash
cd /mnt/d/git/prefab-sentinel && uv run python -m pytest tests/test_editor_bridge.py::TestCameraActions -v
```

Expected: PASS (5 tests)

- [ ] **Step 3: Commit**

```bash
git add tests/test_editor_bridge.py
git commit -m "test: add camera GET/SET action validation tests"
```

---

## Task 4: Auto-Refresh Helper in Orchestrator

**Files:**
- Modify: `prefab_sentinel/orchestrator.py:58-85` (Phase1Orchestrator class)
- Modify: `prefab_sentinel/editor_bridge.py` (add `bridge_status` helper)
- Create: `tests/test_auto_refresh.py`

- [ ] **Step 1: Write failing tests for auto-refresh**

Create `tests/test_auto_refresh.py`:

```python
"""Tests for auto-refresh after confirmed write operations."""

from __future__ import annotations

import os
import unittest
from unittest.mock import MagicMock, patch

from prefab_sentinel.editor_bridge import BRIDGE_MODE_ENV, BRIDGE_WATCH_DIR_ENV, bridge_status
from prefab_sentinel.orchestrator import Phase1Orchestrator


class TestBridgeStatus(unittest.TestCase):
    """Tests for bridge_status() helper."""

    def test_not_connected_when_mode_missing(self) -> None:
        with patch.dict(os.environ, {BRIDGE_MODE_ENV: ""}, clear=False):
            status = bridge_status()
            self.assertFalse(status["connected"])

    def test_not_connected_when_dir_missing(self) -> None:
        with patch.dict(
            os.environ,
            {BRIDGE_MODE_ENV: "editor", BRIDGE_WATCH_DIR_ENV: "/nonexistent/xyz"},
            clear=False,
        ):
            status = bridge_status()
            self.assertFalse(status["connected"])

    def test_connected_when_valid(self) -> None:
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.dict(
                os.environ,
                {BRIDGE_MODE_ENV: "editor", BRIDGE_WATCH_DIR_ENV: tmpdir},
                clear=False,
            ):
                status = bridge_status()
                self.assertTrue(status["connected"])
                self.assertEqual("editor", status["mode"])
                self.assertEqual(tmpdir, status["watch_dir"])


class TestMaybeAutoRefresh(unittest.TestCase):
    """Tests for Phase1Orchestrator._maybe_auto_refresh()."""

    def _make_orchestrator(self) -> Phase1Orchestrator:
        return Phase1Orchestrator(
            reference_resolver=MagicMock(),
            prefab_variant=MagicMock(),
            runtime_validation=MagicMock(),
            serialized_object=MagicMock(),
        )

    def test_skipped_when_bridge_not_connected(self) -> None:
        orch = self._make_orchestrator()
        with patch.dict(os.environ, {BRIDGE_MODE_ENV: ""}, clear=False):
            result = orch.maybe_auto_refresh()
            self.assertEqual("skipped", result)

    def test_true_when_refresh_succeeds(self) -> None:
        import tempfile
        orch = self._make_orchestrator()
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.dict(
                os.environ,
                {BRIDGE_MODE_ENV: "editor", BRIDGE_WATCH_DIR_ENV: tmpdir},
                clear=False,
            ):
                with patch("prefab_sentinel.orchestrator.send_action") as mock_send:
                    mock_send.return_value = {"success": True}
                    result = orch.maybe_auto_refresh()
                    self.assertEqual("true", result)
                    mock_send.assert_called_once_with(action="refresh_asset_database")

    def test_false_when_refresh_fails(self) -> None:
        import tempfile
        orch = self._make_orchestrator()
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.dict(
                os.environ,
                {BRIDGE_MODE_ENV: "editor", BRIDGE_WATCH_DIR_ENV: tmpdir},
                clear=False,
            ):
                with patch("prefab_sentinel.orchestrator.send_action") as mock_send:
                    mock_send.side_effect = OSError("bridge timeout")
                    result = orch.maybe_auto_refresh()
                    self.assertEqual("false", result)
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /mnt/d/git/prefab-sentinel && uv run python -m pytest tests/test_auto_refresh.py -v
```

Expected: FAIL (bridge_status not defined, maybe_auto_refresh not defined)

- [ ] **Step 3: Implement `bridge_status()` in editor_bridge.py**

Add at the end of `prefab_sentinel/editor_bridge.py` (after `send_action`):

```python
def bridge_status() -> dict[str, Any]:
    """Return current bridge connection status without making a request.

    Checks environment variables and watch directory existence only.
    Does not attempt an actual bridge request (no I/O cost).
    """
    mode = os.environ.get(BRIDGE_MODE_ENV, "")
    watch_dir = os.environ.get(BRIDGE_WATCH_DIR_ENV, "")
    connected = mode == "editor" and bool(watch_dir) and Path(watch_dir).is_dir()
    return {
        "connected": connected,
        "mode": mode or None,
        "watch_dir": watch_dir or None,
    }
```

- [ ] **Step 4: Implement `maybe_auto_refresh()` in orchestrator.py**

Add import at the top of `prefab_sentinel/orchestrator.py`:

```python
from prefab_sentinel.editor_bridge import bridge_status, send_action
```

Add method to `Phase1Orchestrator` class (after `default()` classmethod, around line 82):

```python
def maybe_auto_refresh(self) -> str:
    """Trigger AssetDatabase.Refresh if Editor Bridge is connected.

    Returns:
        "true" if refresh succeeded, "false" if refresh failed,
        "skipped" if bridge is not connected.
    """
    status = bridge_status()
    if not status["connected"]:
        return "skipped"
    try:
        send_action(action="refresh_asset_database")
        return "true"
    except Exception:
        return "false"
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
cd /mnt/d/git/prefab-sentinel && uv run python -m pytest tests/test_auto_refresh.py -v
```

Expected: PASS (6 tests)

- [ ] **Step 6: Commit**

```bash
git add prefab_sentinel/editor_bridge.py prefab_sentinel/orchestrator.py tests/test_auto_refresh.py
git commit -m "feat(bridge): add bridge_status() and maybe_auto_refresh() helpers"
```

---

## Task 5: Wire Auto-Refresh Into MCP Write Tools

**Files:**
- Modify: `prefab_sentinel/mcp_server.py:393-510` (set_property)
- Modify: `prefab_sentinel/mcp_server.py:513-610` (add_component)
- Modify: `prefab_sentinel/mcp_server.py:613-715` (remove_component)
- Modify: `prefab_sentinel/mcp_server.py:1106-1170` (patch_apply)
- Modify: `prefab_sentinel/mcp_server.py:1177-1205` (revert_overrides)

- [ ] **Step 1: Add auto-refresh to `set_property`**

After `session.invalidate_symbol_tree(resolved)` (line 499), add auto-refresh. Note: `orch` is already defined at line 489, so reuse it:

```python
        auto_refresh = "skipped"
        if confirm and resp.success:
            session.invalidate_symbol_tree(resolved)
            # Auto-refresh Editor to pick up file changes
            auto_refresh = orch.maybe_auto_refresh()

        # 7. Enrich response with symbol resolution metadata
        result = resp.to_dict()
        if confirm and resp.success:
            result["auto_refresh"] = auto_refresh
        result["symbol_resolution"] = {
```

- [ ] **Step 2: Add auto-refresh to `add_component`**

After `session.invalidate_symbol_tree(resolved)` (line 601), add. Note: `orch` is already at line 592:

```python
        auto_refresh = "skipped"
        if confirm and resp.success:
            session.invalidate_symbol_tree(resolved)
            auto_refresh = orch.maybe_auto_refresh()

        result = resp.to_dict()
        if confirm and resp.success:
            result["auto_refresh"] = auto_refresh
        result["symbol_resolution"] = {
```

- [ ] **Step 3: Add auto-refresh to `remove_component`**

After `session.invalidate_symbol_tree(resolved)` (line 706), add. Note: `orch` is already at line 697:

```python
        auto_refresh = "skipped"
        if confirm and resp.success:
            session.invalidate_symbol_tree(resolved)
            auto_refresh = orch.maybe_auto_refresh()

        result = resp.to_dict()
        if confirm and resp.success:
            result["auto_refresh"] = auto_refresh
        result["symbol_resolution"] = {
```

- [ ] **Step 4: Add auto-refresh to `patch_apply`**

After `return resp.to_dict()` (line 1170), modify to:

```python
        result = resp.to_dict()
        if confirm and resp.success:
            orch_ref = session.get_orchestrator()
            result["auto_refresh"] = orch_ref.maybe_auto_refresh()
        return result
```

- [ ] **Step 5: Add auto-refresh to `revert_overrides`**

After `return resp.to_dict()` (line 1205), modify to:

```python
        result = resp.to_dict()
        if confirm and resp.success:
            orch = session.get_orchestrator()
            result["auto_refresh"] = orch.maybe_auto_refresh()
        return result
```

- [ ] **Step 6: Run full test suite to verify no regressions**

```bash
cd /mnt/d/git/prefab-sentinel && uv run python -m pytest tests/ -v --timeout=60
```

Expected: All tests PASS (existing + new)

- [ ] **Step 7: Commit**

```bash
git add prefab_sentinel/mcp_server.py
git commit -m "feat(mcp): auto-refresh Editor after confirmed write operations"
```

---

## Task 6: Bridge Status in `get_project_status`

**Files:**
- Modify: `prefab_sentinel/mcp_server.py:144-157` (get_project_status)
- Modify: `prefab_sentinel/session.py:179-191` (status method)

- [ ] **Step 1: Add bridge status to session.status()**

In `prefab_sentinel/session.py`, add import at top:

```python
from prefab_sentinel.editor_bridge import bridge_status
```

In `status()` method (line 179-191), add `bridge` key:

```python
    def status(self) -> dict[str, Any]:
        """Return current cache diagnostics."""
        return {
            "project_root": str(self._project_root) if self._project_root else None,
            "scope": str(self._scope) if self._scope else None,
            "orchestrator_cached": self._orchestrator is not None,
            "script_map_size": len(self._script_name_map) if self._script_name_map else 0,
            "script_map_cached": self._script_name_map is not None,
            "symbol_tree_entries": len(self._symbol_cache),
            "symbol_tree_paths": sorted(str(p) for p in self._symbol_cache),
            "watcher_running": self._watcher_task is not None
            and not self._watcher_task.done(),
            "bridge": bridge_status(),
        }
```

- [ ] **Step 2: Run existing tests to verify no regression**

```bash
cd /mnt/d/git/prefab-sentinel && uv run python -m pytest tests/test_mcp_server.py -v
```

Expected: PASS

- [ ] **Step 3: Commit**

```bash
git add prefab_sentinel/session.py
git commit -m "feat(mcp): add bridge connection status to get_project_status"
```

---

## Task 7: Unified Bridge Error Message

**Files:**
- Modify: `prefab_sentinel/editor_bridge.py:72-94` (check_editor_bridge_env)

- [ ] **Step 1: Improve error messages with actionable hints**

Update `check_editor_bridge_env()` to include setup instructions in error messages:

```python
_BRIDGE_SETUP_HINT = (
    " Set UNITYTOOL_BRIDGE_MODE=editor and UNITYTOOL_BRIDGE_WATCH_DIR=<path>."
    " See README 'Unity Bridge セットアップ' section."
)


def check_editor_bridge_env() -> dict[str, Any] | None:
    """Return an error response if editor bridge env is not configured, else None."""
    mode = os.environ.get(BRIDGE_MODE_ENV, "")
    if mode != "editor":
        return _error_response(
            code="EDITOR_BRIDGE_MODE",
            message=f"Editor Bridge not connected: {BRIDGE_MODE_ENV} must be 'editor', got '{mode}'.{_BRIDGE_SETUP_HINT}",
            data={"env_var": BRIDGE_MODE_ENV, "value": mode},
        )
    watch_dir = os.environ.get(BRIDGE_WATCH_DIR_ENV, "")
    if not watch_dir:
        return _error_response(
            code="EDITOR_BRIDGE_WATCH_DIR_MISSING",
            message=f"Editor Bridge not connected: {BRIDGE_WATCH_DIR_ENV} is not set.{_BRIDGE_SETUP_HINT}",
            data={"env_var": BRIDGE_WATCH_DIR_ENV},
        )
    if not Path(watch_dir).is_dir():
        return _error_response(
            code="EDITOR_BRIDGE_WATCH_DIR_NOT_FOUND",
            message=f"Editor Bridge not connected: watch directory does not exist: {watch_dir}.{_BRIDGE_SETUP_HINT}",
            data={"env_var": BRIDGE_WATCH_DIR_ENV, "value": watch_dir},
        )
    return None
```

- [ ] **Step 2: Update test assertions for new message format**

In `tests/test_editor_bridge.py`, the existing tests check `code` not `message`, so they should still pass. Run to verify:

```bash
cd /mnt/d/git/prefab-sentinel && uv run python -m pytest tests/test_editor_bridge.py -v
```

Expected: PASS

- [ ] **Step 3: Commit**

```bash
git add prefab_sentinel/editor_bridge.py
git commit -m "fix(bridge): add actionable setup hints to bridge error messages"
```

---

## Task 8: Full Regression Test + README Update

**Files:**
- Run: full test suite
- Modify: `README.md` (tool table update)

- [ ] **Step 1: Run full test suite**

```bash
cd /mnt/d/git/prefab-sentinel && uv run python -m pytest tests/ -v --timeout=60
```

Expected: All tests PASS

- [ ] **Step 2: Run lint**

```bash
cd /mnt/d/git/prefab-sentinel && uv run ruff check prefab_sentinel/ tests/
```

Expected: No errors

- [ ] **Step 3: Update README tool table**

In `README.md`, replace the `editor_camera` row with:

```markdown
| `editor_get_camera` | Scene ビューのカメラ状態取得（position, rotation, pivot, size, orthographic） |
| `editor_set_camera` | Scene ビューのカメラ設定（Mode A: 絶対座標 / Mode B: pivot 周回。yaw=0 が正面） |
```

- [ ] **Step 4: Commit README**

```bash
git add README.md
git commit -m "docs: update README with new camera GET/SET tools and auto-refresh"
```
