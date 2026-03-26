# Editor Bridge UX Improvements Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Unify editor_set_camera API, add position mode, enhance Repaint, improve error messages, and restructure guide/knowledge documentation.

**Architecture:** Layer-by-layer bottom-up: C# handlers first (EditorControlBridge.cs, EditorBridge.cs), then Python MCP wrappers (mcp_server.py), then documentation (guide skill + knowledge files). C# changes are validated via Unity integration tests; Python changes via unit tests with mocked bridge responses.

**Tech Stack:** C# (Unity Editor API), Python 3.11+ (FastMCP), Markdown (knowledge files)

**Spec:** `docs/superpowers/specs/2026-03-26-editor-bridge-ux-improvements-design.md`

---

## File Structure

### Modified Files

| File | Responsibility |
|------|---------------|
| `tools/unity/PrefabSentinel.UnityEditorControlBridge.cs` | C# request/response structs, camera handlers, Repaint, frame bounds |
| `tools/unity/PrefabSentinel.EditorBridge.cs` | Dispatch routing + unknown action error |
| `prefab_sentinel/mcp_server.py` | MCP tool wrappers for editor_set_camera, editor_frame |
| `tests/test_editor_bridge.py` | Python unit tests for camera parameter handling |
| `skills/guide/SKILL.md` | Slim down to reference-only |
| `knowledge/prefab-sentinel-editor-camera.md` | Update for new API |
| `README.md` | Bridge section cleanup |

### New Files

| File | Responsibility |
|------|---------------|
| `knowledge/prefab-sentinel-workflow-patterns.md` | Workflow decision trees extracted from guide |
| `knowledge/prefab-sentinel-variant-patterns.md` | Variant + VRChat avatar swap patterns |
| `knowledge/prefab-sentinel-material-operations.md` | Material editing patterns with examples |
| `knowledge/prefab-sentinel-patch-patterns.md` | Patch plan construction examples |
| `knowledge/prefab-sentinel-wiring-triage.md` | Wiring inspection interpretation guide |

---

## Task 1: C# — Update data structures

**Files:**
- Modify: `tools/unity/PrefabSentinel.UnityEditorControlBridge.cs:51-119` (EditorControlRequest)
- Modify: `tools/unity/PrefabSentinel.UnityEditorControlBridge.cs:183-233` (EditorControlData)

- [ ] **Step 1: Add `camera_look_at` to EditorControlRequest**

In `EditorControlRequest` (line 84-95), replace the camera section with:

```csharp
            // camera (get_camera / set_camera)
            // Pivot orbit: pivot + yaw/pitch/distance
            public float[] camera_pivot = null;      // [x, y, z] pivot point
            public float yaw = float.NaN;           // NaN = keep current
            public float pitch = float.NaN;
            public float distance = -1f;             // SceneView.size; -1 = keep current
            // Position mode: camera_position + camera_look_at or yaw/pitch
            public float[] camera_position = null;   // [x, y, z] camera world coords
            public float[] camera_look_at = null;    // [x, y, z] look-at target
            // Shared
            public int camera_orthographic = -1;     // -1 = keep, 0 = perspective, 1 = ortho
```

Note: `camera_rotation` and `camera_size` fields are removed. If JsonUtility receives JSON with these fields, unknown fields are silently ignored.

- [ ] **Step 2: Add previous camera state + bounds fields to EditorControlData**

In `EditorControlData` (after line 205), add:

```csharp
            // Previous camera state (set_camera only)
            public float[] previous_camera_position = null;
            public float[] previous_camera_euler = null;
            public float[] previous_camera_pivot = null;
            public float previous_camera_size = 0f;
            public bool previous_camera_orthographic = false;

            // Bounds info (frame_selected only)
            public float[] bounds_center = null;     // [x, y, z] world-space AABB center
            public float[] bounds_extents = null;    // [x, y, z] half-size
```

- [ ] **Step 3: Commit**

```bash
git add tools/unity/PrefabSentinel.UnityEditorControlBridge.cs
git commit -m "feat(bridge): add camera_look_at, previous state, bounds to C# structs"
```

---

## Task 2: C# — Add helper methods

**Files:**
- Modify: `tools/unity/PrefabSentinel.UnityEditorControlBridge.cs`

- [ ] **Step 1: Add CameraSnapshot struct and CaptureCameraState helper**

Add before `HandleGetCamera` (before line 833):

```csharp
        // ── Camera helpers ──

        private struct CameraSnapshot
        {
            public float[] position;
            public float[] rotation_quat;
            public float[] euler;
            public float[] pivot;
            public float size;
            public bool orthographic;
        }

        private static CameraSnapshot CaptureCameraState(SceneView sv)
        {
            Vector3 pos = sv.camera.transform.position;
            Quaternion rot = sv.rotation;
            Vector3 e = rot.eulerAngles;
            float yaw = (e.y + 180f) % 360f;
            float pitch = e.x > 180f ? e.x - 360f : e.x;
            return new CameraSnapshot
            {
                position = new[] { pos.x, pos.y, pos.z },
                rotation_quat = new[] { rot.x, rot.y, rot.z, rot.w },
                euler = new[] { yaw, pitch, 0f },
                pivot = new[] { sv.pivot.x, sv.pivot.y, sv.pivot.z },
                size = sv.size,
                orthographic = sv.orthographic
            };
        }

        private static EditorControlData BuildCameraData(CameraSnapshot current, CameraSnapshot? previous = null)
        {
            var data = new EditorControlData
            {
                camera_position = current.position,
                camera_rotation_quat = current.rotation_quat,
                camera_euler = current.euler,
                camera_pivot = current.pivot,
                camera_size = current.size,
                camera_orthographic = current.orthographic,
                executed = true
            };
            if (previous.HasValue)
            {
                var prev = previous.Value;
                data.previous_camera_position = prev.position;
                data.previous_camera_euler = prev.euler;
                data.previous_camera_pivot = prev.pivot;
                data.previous_camera_size = prev.size;
                data.previous_camera_orthographic = prev.orthographic;
            }
            return data;
        }
```

- [ ] **Step 2: Add RepaintAllViews helper**

Add after `BuildCameraData`:

```csharp
        /// <summary>
        /// Aggressive repaint: immediate + delayed, all views.
        /// Ensures changes are visible even when Unity is in the background.
        /// </summary>
        private static void RepaintAllViews(SceneView sceneView)
        {
            sceneView.Repaint();
            SceneView.RepaintAll();
            UnityEditorInternal.InternalEditorUtility.RepaintAllViews();
            EditorApplication.delayCall += () =>
            {
                sceneView.Repaint();
                SceneView.RepaintAll();
            };
        }
```

- [ ] **Step 3: Commit**

```bash
git add tools/unity/PrefabSentinel.UnityEditorControlBridge.cs
git commit -m "feat(bridge): add CameraSnapshot, BuildCameraData, RepaintAllViews helpers"
```

---

## Task 3: C# — Rewrite HandleSetCamera

**Files:**
- Modify: `tools/unity/PrefabSentinel.UnityEditorControlBridge.cs:862-950`

- [ ] **Step 1: Replace HandleSetCamera method**

Replace the entire `HandleSetCamera` method (lines 862-950) with:

```csharp
        private static EditorControlResponse HandleSetCamera(EditorControlRequest request)
        {
            SceneView sceneView = SceneView.lastActiveSceneView;
            if (sceneView == null)
                return BuildError("EDITOR_CTRL_NO_SCENE_VIEW", "No active SceneView found.");

            // Capture previous state before any changes
            CameraSnapshot previous = CaptureCameraState(sceneView);

            bool hasPosition = request.camera_position != null && request.camera_position.Length == 3;
            bool hasLookAt = request.camera_look_at != null && request.camera_look_at.Length == 3;
            bool hasPivot = request.camera_pivot != null && request.camera_pivot.Length == 3;
            bool hasYaw = !float.IsNaN(request.yaw);
            bool hasPitch = !float.IsNaN(request.pitch);
            bool hasDistance = request.distance >= 0f;

            // Conflict checks
            if (hasPosition && hasPivot)
                return BuildError("EDITOR_CTRL_CAMERA_CONFLICT",
                    "Cannot specify both 'position' (camera world coords) and 'pivot' (orbit center). Use one.");
            if (hasLookAt && !hasPosition)
                return BuildError("EDITOR_CTRL_CAMERA_CONFLICT",
                    "'look_at' requires 'position' to be set.");

            // Read current FoV before changes
            float fov = sceneView.camera.fieldOfView;

            if (hasPosition)
            {
                Vector3 cameraPos = new Vector3(
                    request.camera_position[0],
                    request.camera_position[1],
                    request.camera_position[2]);

                if (hasLookAt)
                {
                    // Position + look_at mode
                    Vector3 lookAt = new Vector3(
                        request.camera_look_at[0],
                        request.camera_look_at[1],
                        request.camera_look_at[2]);
                    Vector3 direction = (lookAt - cameraPos).normalized;
                    float dist = Vector3.Distance(cameraPos, lookAt);

                    sceneView.pivot = lookAt;
                    sceneView.rotation = Quaternion.LookRotation(direction);
                    if (sceneView.orthographic)
                        sceneView.size = dist * 0.5f;
                    else
                        sceneView.size = dist * Mathf.Tan(fov * 0.5f * Mathf.Deg2Rad);
                }
                else
                {
                    // Position + yaw/pitch mode: reverse-calculate pivot
                    if (hasDistance)
                        sceneView.size = request.distance;

                    Vector3 currentEuler = sceneView.rotation.eulerAngles;
                    float curYaw = (currentEuler.y + 180f) % 360f;
                    float curPitch = currentEuler.x > 180f ? currentEuler.x - 360f : currentEuler.x;
                    float newYaw = hasYaw ? request.yaw : curYaw;
                    float newPitch = hasPitch ? request.pitch : curPitch;
                    float internalYaw = (newYaw + 180f) % 360f;
                    Quaternion rot = Quaternion.Euler(newPitch, internalYaw, 0f);
                    sceneView.rotation = rot;

                    float cameraDistance;
                    if (sceneView.orthographic)
                        cameraDistance = sceneView.size * 2f;
                    else
                        cameraDistance = sceneView.size / Mathf.Tan(fov * 0.5f * Mathf.Deg2Rad);
                    sceneView.pivot = cameraPos + rot * new Vector3(0, 0, cameraDistance);
                }
            }
            else
            {
                // Pivot orbit mode (unified from old Mode A/B)
                if (hasPivot)
                {
                    sceneView.pivot = new Vector3(
                        request.camera_pivot[0],
                        request.camera_pivot[1],
                        request.camera_pivot[2]);
                }
                if (hasYaw || hasPitch)
                {
                    Vector3 currentEuler = sceneView.rotation.eulerAngles;
                    float curYaw = (currentEuler.y + 180f) % 360f;
                    float curPitch = currentEuler.x > 180f ? currentEuler.x - 360f : currentEuler.x;
                    float newYaw = hasYaw ? request.yaw : curYaw;
                    float newPitch = hasPitch ? request.pitch : curPitch;
                    float internalYaw = (newYaw + 180f) % 360f;
                    sceneView.rotation = Quaternion.Euler(newPitch, internalYaw, 0f);
                }
                if (hasDistance)
                    sceneView.size = request.distance;
            }

            if (request.camera_orthographic >= 0)
                sceneView.orthographic = request.camera_orthographic == 1;

            RepaintAllViews(sceneView);

            // Return previous + current state
            CameraSnapshot current = CaptureCameraState(sceneView);
            return BuildSuccess("EDITOR_CTRL_CAMERA_SET_OK", "Camera updated",
                data: BuildCameraData(current, previous));
        }
```

- [ ] **Step 2: Simplify HandleGetCamera to use helper**

Replace `HandleGetCamera` (lines 833-860) with:

```csharp
        private static EditorControlResponse HandleGetCamera()
        {
            SceneView sceneView = SceneView.lastActiveSceneView;
            if (sceneView == null)
                return BuildError("EDITOR_CTRL_NO_SCENE_VIEW", "No active SceneView found.");

            CameraSnapshot snap = CaptureCameraState(sceneView);
            return BuildSuccess("EDITOR_CTRL_CAMERA_GET_OK",
                $"Camera position=({snap.position[0]:F2}, {snap.position[1]:F2}, {snap.position[2]:F2})",
                data: BuildCameraData(snap));
        }
```

- [ ] **Step 3: Commit**

```bash
git add tools/unity/PrefabSentinel.UnityEditorControlBridge.cs
git commit -m "feat(bridge): unified set_camera API with position mode and previous state"
```

---

## Task 4: C# — Update HandleFrameSelected with bounds + camera

**Files:**
- Modify: `tools/unity/PrefabSentinel.UnityEditorControlBridge.cs:547-572`

- [ ] **Step 1: Replace HandleFrameSelected**

Replace lines 547-572 with:

```csharp
        private static EditorControlResponse HandleFrameSelected(EditorControlRequest request)
        {
            GameObject selectedGo = Selection.activeGameObject;
            if (selectedGo == null)
                return BuildError("EDITOR_CTRL_NO_SELECTION", "No GameObject is selected. Use select_object first.");

            SceneView sceneView = SceneView.lastActiveSceneView;
            if (sceneView == null)
                return BuildError("EDITOR_CTRL_NO_SCENE_VIEW", "No active SceneView found.");

            float zoom = request.zoom;
            string objectName = selectedGo.name;

            // Collect bounds before framing (renderer may be needed)
            float[] boundsCenter = null;
            float[] boundsExtents = null;
            Renderer renderer = selectedGo.GetComponentInChildren<Renderer>();
            if (renderer != null)
            {
                Bounds b = renderer.bounds;
                boundsCenter = new[] { b.center.x, b.center.y, b.center.z };
                boundsExtents = new[] { b.extents.x, b.extents.y, b.extents.z };
            }

            // Frame and capture camera state synchronously
            sceneView.FrameSelected();
            if (zoom > 0f)
                sceneView.size = zoom;
            RepaintAllViews(sceneView);

            CameraSnapshot cam = CaptureCameraState(sceneView);
            var data = BuildCameraData(cam);
            data.selected_object = objectName;
            data.bounds_center = boundsCenter;
            data.bounds_extents = boundsExtents;

            return BuildSuccess("EDITOR_CTRL_FRAME_OK",
                $"Framed: {objectName}" + (zoom > 0f ? $" (zoom={zoom})" : ""),
                data: data);
        }
```

**Important change:** `FrameSelected()` is now called synchronously instead of via `EditorApplication.delayCall`. This is required to capture the post-frame camera state in the response. `RepaintAllViews` handles the deferred repaint.

- [ ] **Step 2: Commit**

```bash
git add tools/unity/PrefabSentinel.UnityEditorControlBridge.cs
git commit -m "feat(bridge): return bounds + camera state from frame_selected"
```

---

## Task 5: C# — Update HandleRecompileScripts + apply RepaintAllViews

**Files:**
- Modify: `tools/unity/PrefabSentinel.UnityEditorControlBridge.cs`

- [ ] **Step 1: Add AssetDatabase.Refresh to HandleRecompileScripts**

Replace `HandleRecompileScripts` (lines 648-659) with:

```csharp
        private static EditorControlResponse HandleRecompileScripts()
        {
            // Refresh first so Unity sees newly copied/modified C# files
            AssetDatabase.Refresh(ImportAssetOptions.ForceSynchronousImport);
            // Schedule compilation on next frame so that the response JSON
            // is written to disk before domain reload destroys this context.
            EditorApplication.delayCall += () =>
            {
                CompilationPipeline.RequestScriptCompilation();
            };
            return BuildSuccess("EDITOR_CTRL_RECOMPILE_OK",
                "AssetDatabase.Refresh completed; script recompilation scheduled (domain reload will follow)",
                data: new EditorControlData { executed = true });
        }
```

- [ ] **Step 2: Add RepaintAllViews to HandleSetBlendShape**

Find `HandleSetBlendShape` (around line 1563, at the return). Before the final `return BuildSuccess(...)`, add:

```csharp
            SceneView sv = SceneView.lastActiveSceneView;
            if (sv != null) RepaintAllViews(sv);
```

- [ ] **Step 3: Add RepaintAllViews to HandleSetMaterialProperty**

Find `HandleSetMaterialProperty` (around line 1278, at the return). Before the final `return BuildSuccess(...)`, add:

```csharp
            SceneView sv = SceneView.lastActiveSceneView;
            if (sv != null) RepaintAllViews(sv);
```

- [ ] **Step 4: Commit**

```bash
git add tools/unity/PrefabSentinel.UnityEditorControlBridge.cs
git commit -m "feat(bridge): auto-refresh in recompile, RepaintAllViews in visual handlers"
```

---

## Task 6: C# — Fix EditorBridge dispatch error

**Files:**
- Modify: `tools/unity/PrefabSentinel.EditorBridge.cs:148-192`

- [ ] **Step 1: Replace dispatch logic with explicit routing**

In `ProcessRequest` method, replace the dispatch block (approx lines 163-180) with:

```csharp
            bool isRuntime = !string.IsNullOrEmpty(header.action)
                && UnityRuntimeValidationBridge.SupportedActions.Contains(header.action);

            bool isEditorControl = !string.IsNullOrEmpty(header.action)
                && UnityEditorControlBridge.SupportedActions.Contains(header.action);

            bool isPatch = !string.IsNullOrEmpty(header.action)
                && UnityPatchBridge.SupportedActions.Contains(header.action);

            if (isRuntime)
            {
                UnityRuntimeValidationBridge.RunFromPaths(requestPath, responsePath);
            }
            else if (isEditorControl)
            {
                UnityEditorControlBridge.RunFromPaths(requestPath, responsePath);
            }
            else if (isPatch)
            {
                UnityPatchBridge.ApplyFromPaths(requestPath, responsePath);
            }
            else
            {
                // Unknown action — no silent fallthrough
                string supported = string.Join(", ", UnityEditorControlBridge.SupportedActions);
                WriteErrorResponse(responsePath, "EDITOR_BRIDGE_UNKNOWN_ACTION",
                    $"Unknown action '{header.action}'. " +
                    $"EditorControlBridge supports: [{supported}]. " +
                    "Bridge C# scripts may need updating.",
                    UnityEditorControlBridge.ProtocolVersion);
            }
```

**Note:** This requires `UnityPatchBridge.SupportedActions` to exist. Check if PatchBridge exposes a SupportedActions set. If not, the `isPatch` check can be omitted and the `else` block handles both unknown actions and patch actions (with PatchBridge as the fallback for non-empty actions that aren't in the other two sets). In that case, use:

```csharp
            if (isRuntime)
                UnityRuntimeValidationBridge.RunFromPaths(requestPath, responsePath);
            else if (isEditorControl)
                UnityEditorControlBridge.RunFromPaths(requestPath, responsePath);
            else if (string.IsNullOrEmpty(header.action))
            {
                WriteErrorResponse(responsePath, "EDITOR_BRIDGE_UNKNOWN_ACTION",
                    "Empty action field in request.",
                    UnityEditorControlBridge.ProtocolVersion);
            }
            else
                // PatchBridge handles all remaining known actions.
                // If the action is truly unknown, PatchBridge will report its own error.
                UnityPatchBridge.ApplyFromPaths(requestPath, responsePath);
```

**Decision:** Check `PrefabSentinel.UnityPatchBridge.cs` for `SupportedActions`. Use the explicit routing version if available, otherwise use the fallback version above.

- [ ] **Step 2: Commit**

```bash
git add tools/unity/PrefabSentinel.EditorBridge.cs
git commit -m "fix(bridge): explicit action routing, no silent fallthrough to PatchBridge"
```

---

## Task 7: Python — Update editor_set_camera MCP tool

**Files:**
- Modify: `prefab_sentinel/mcp_server.py:912-969`
- Test: `tests/test_editor_bridge.py`

- [ ] **Step 1: Write failing test for new parameter handling**

Add to `tests/test_editor_bridge.py`:

```python
class TestSetCameraParams(unittest.TestCase):
    """Validate editor_set_camera parameter conversion."""

    def test_pivot_orbit_kwargs(self) -> None:
        """Pivot + yaw/pitch/distance → correct send_action kwargs."""
        from prefab_sentinel.mcp_server import _build_set_camera_kwargs

        kwargs = _build_set_camera_kwargs(
            pivot='{"x":0,"y":1.3,"z":0}',
            yaw=345.0,
            pitch=8.0,
            distance=0.28,
        )
        self.assertEqual(kwargs["camera_pivot"], [0, 1.3, 0])
        self.assertEqual(kwargs["yaw"], 345.0)
        self.assertEqual(kwargs["pitch"], 8.0)
        self.assertEqual(kwargs["distance"], 0.28)
        self.assertNotIn("camera_position", kwargs)
        self.assertNotIn("camera_look_at", kwargs)

    def test_position_look_at_kwargs(self) -> None:
        """Position + look_at → correct send_action kwargs."""
        from prefab_sentinel.mcp_server import _build_set_camera_kwargs

        kwargs = _build_set_camera_kwargs(
            position='{"x":0,"y":1.5,"z":-1}',
            look_at='{"x":0,"y":1.3,"z":0}',
        )
        self.assertEqual(kwargs["camera_position"], [0, 1.5, -1])
        self.assertEqual(kwargs["camera_look_at"], [0, 1.3, 0])
        self.assertNotIn("camera_pivot", kwargs)

    def test_position_yaw_pitch_kwargs(self) -> None:
        """Position + yaw/pitch (no look_at) → correct kwargs."""
        from prefab_sentinel.mcp_server import _build_set_camera_kwargs

        kwargs = _build_set_camera_kwargs(
            position='{"x":0,"y":1.5,"z":-1}',
            yaw=0.0,
            pitch=10.0,
            distance=0.5,
        )
        self.assertEqual(kwargs["camera_position"], [0, 1.5, -1])
        self.assertEqual(kwargs["yaw"], 0.0)
        self.assertEqual(kwargs["pitch"], 10.0)
        self.assertEqual(kwargs["distance"], 0.5)
        self.assertNotIn("camera_look_at", kwargs)

    def test_omitted_params_excluded(self) -> None:
        """Omitted optional params are not in kwargs."""
        from prefab_sentinel.mcp_server import _build_set_camera_kwargs

        kwargs = _build_set_camera_kwargs(yaw=180.0)
        self.assertEqual(kwargs, {"yaw": 180.0})

    def test_orthographic_passed(self) -> None:
        """orthographic flag is included when set."""
        from prefab_sentinel.mcp_server import _build_set_camera_kwargs

        kwargs = _build_set_camera_kwargs(orthographic=1)
        self.assertEqual(kwargs["camera_orthographic"], 1)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run python -m pytest tests/test_editor_bridge.py::TestSetCameraParams -v`
Expected: FAIL — `_build_set_camera_kwargs` does not exist yet.

- [ ] **Step 3: Extract parameter builder and rewrite editor_set_camera**

In `prefab_sentinel/mcp_server.py`, replace `editor_set_camera` (lines 912-969) with:

```python
def _build_set_camera_kwargs(
    *,
    pivot: str = "",
    yaw: float = float("nan"),
    pitch: float = float("nan"),
    distance: float = -1.0,
    orthographic: int = -1,
    position: str = "",
    look_at: str = "",
) -> dict[str, Any]:
    """Build send_action kwargs from set_camera parameters."""
    import json as _json
    import math

    kwargs: dict[str, Any] = {}

    if position:
        p = _json.loads(position)
        kwargs["camera_position"] = [p["x"], p["y"], p["z"]]
    if look_at:
        la = _json.loads(look_at)
        kwargs["camera_look_at"] = [la["x"], la["y"], la["z"]]
    if pivot:
        pv = _json.loads(pivot)
        kwargs["camera_pivot"] = [pv["x"], pv["y"], pv["z"]]
    if not math.isnan(yaw):
        kwargs["yaw"] = yaw
    if not math.isnan(pitch):
        kwargs["pitch"] = pitch
    if distance >= 0:
        kwargs["distance"] = distance
    if orthographic >= 0:
        kwargs["camera_orthographic"] = orthographic

    return kwargs


@server.tool()
def editor_set_camera(
    pivot: str = "",
    yaw: float = float("nan"),
    pitch: float = float("nan"),
    distance: float = -1.0,
    orthographic: int = -1,
    position: str = "",
    look_at: str = "",
) -> dict[str, Any]:
    """Set Scene view camera.

    Pivot orbit mode: pivot, yaw, pitch, distance
    Position mode: position + look_at, or position + yaw/pitch

    Cannot mix position and pivot. Euler convention: yaw=0 = front (+Z).
    Omitted params keep their current value.

    Returns previous and current camera state.

    Args:
        pivot: JSON '{"x":0,"y":0,"z":0}' — orbit center.
        yaw: Horizontal rotation in degrees.
        pitch: Vertical rotation in degrees.
        distance: SceneView.size (>=0 to set, -1 = keep).
        orthographic: -1=keep, 0=perspective, 1=orthographic.
        position: JSON '{"x":0,"y":1,"z":-5}' — camera world position.
        look_at: JSON '{"x":0,"y":1,"z":0}' — look-at target (requires position).
    """
    kwargs = _build_set_camera_kwargs(
        pivot=pivot, yaw=yaw, pitch=pitch, distance=distance,
        orthographic=orthographic, position=position, look_at=look_at,
    )
    return send_action(action="set_camera", **kwargs)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run python -m pytest tests/test_editor_bridge.py::TestSetCameraParams -v`
Expected: all 5 tests PASS.

- [ ] **Step 5: Update existing camera tests**

In `tests/test_editor_bridge.py`, find `TestCameraActions` (line ~197). Update any tests that reference old Mode A parameters (`rotation`, `size`, `camera_rotation`, `camera_size` as input kwargs). The action names `get_camera` and `set_camera` remain unchanged.

- [ ] **Step 6: Run full test suite**

Run: `uv run python -m pytest tests/test_editor_bridge.py -v`
Expected: all tests PASS.

- [ ] **Step 7: Commit**

```bash
git add prefab_sentinel/mcp_server.py tests/test_editor_bridge.py
git commit -m "feat(mcp): unified editor_set_camera with position + look_at mode"
```

---

## Task 8: Python — Update editor_frame MCP tool

**Files:**
- Modify: `prefab_sentinel/mcp_server.py:890-901`

- [ ] **Step 1: Update editor_frame docstring**

Replace `editor_frame` (lines 890-901) with:

```python
@server.tool()
def editor_frame(
    zoom: float = 0.0,
) -> dict[str, Any]:
    """Frame the selected object in Scene view.

    Returns bounds info (bounds_center, bounds_extents) and post-frame
    camera state. Use bounds to understand where the object center is
    (e.g., SkinnedMeshRenderer bounds may center at feet).

    Args:
        zoom: Scene view distance factor (SceneView.size). 0 = keep current.
            Larger values zoom OUT, smaller values zoom IN. Typical: 0.1-5.0.
    """
    return send_action(action="frame_selected", zoom=zoom)
```

No functional change needed — the C# side now returns bounds and camera in the response, and Python passes it through.

- [ ] **Step 2: Run tests**

Run: `uv run python -m pytest tests/test_editor_bridge.py -v`
Expected: all tests PASS.

- [ ] **Step 3: Commit**

```bash
git add prefab_sentinel/mcp_server.py
git commit -m "docs(mcp): update editor_frame docstring for bounds + camera response"
```

---

## Task 9: Documentation — Extract guide operational content to knowledge

**Files:**
- Modify: `skills/guide/SKILL.md:176-218`
- Create: `knowledge/prefab-sentinel-workflow-patterns.md`
- Create: `knowledge/prefab-sentinel-variant-patterns.md`

- [ ] **Step 1: Create workflow patterns knowledge file**

Write `knowledge/prefab-sentinel-workflow-patterns.md`:

```markdown
# prefab-sentinel ワークフローパターン

## 基本情報

| 項目 | 値 |
|------|---|
| 対象 | prefab-sentinel MCP ツール群のワークフロー |
| version_tested | prefab-sentinel 0.5.71 |
| last_updated | 2026-03-26 |
| confidence | high |

## L1: 確定パターン

### Prefab 編集（open モード）
`validate_structure` → `patch_apply`(dry-run) → `patch_apply`(confirm) → `validate_refs` → `validate_runtime`

### Prefab 新規作成（create モード）
パッチ計画作成 → `patch_apply`(dry-run) → `patch_apply`(confirm) → `validate_structure` → `validate_refs`

### Editor リモート操作
`editor_select` → `editor_frame` で対象を表示 → `editor_screenshot` で視覚確認。スクショはトリアージの起点として使い、データソースにしない（必ず `inspect_wiring` / `validate_refs` で裏取りする）。

### 見た目の反復調整
`editor_set_material` でスロット差し替え → `editor_set_camera` でアングル調整 → `editor_screenshot` で確認 → 確定後に `revert_overrides` / `patch_apply` で永続化

### 壊れた参照の修復
`validate_refs` → `find_referencing_assets` → `ignore_asset_guids` で偽陽性を除外 → safe_fix / decision_required

### オーバーライドの削除
`revert_overrides` → dry-run → confirm のゲート付き。Variant の特定の Modification 行を YAML から除去

## L2: 検査ワークフロー

### フィールド配線検査
`inspect_wiring` → null参照・fileID不整合の特定。重複は same-component（WARNING）/ cross-component（INFO）に分類。Variant ファイルではベース Prefab のコンポーネントを自動解析

### 階層構造の確認
`inspect_hierarchy` → ツリー構造・コンポーネント配置の把握。Variant ファイルではベース Prefab の階層を表示し、オーバーライド付きノードに `[overridden: N]` マーカーを付与

### マテリアル構成の確認
`inspect_materials` → Renderer ごとのマテリアルスロット一覧。Variant チェーンを考慮し、各スロットが `overridden` / `inherited` かを表示

### 内部構造の検証
`validate_structure` → fileID重複・Transform整合性・参照欠損の検出

### ランタイムエラー調査
`validate_runtime` → ログ分類 → アセット特定 → 修正提案

### ランタイム階層確認
`editor_list_children` でシーン実行中の子オブジェクト一覧を取得。`inspect_hierarchy` はファイルベースだが、こちらは Prefab Instance 内のネスト構造も表示

### ランタイムマテリアル確認
`editor_list_materials` で Unity API から直接 Renderer のマテリアルスロット一覧を取得。`inspect_materials` がオフラインで Variant/FBX チェーンを解決できない場合の代替

### Console ログ取得
`editor_console` でリアルタイムログをテキスト取得。batchmode 後の Editor.log 読みではなく、Editor 起動中のバッファからの取得

## 判断ルール

- `safe_fix`: 一意で決定的な修正のみ自動適用可
- `decision_required`: ユーザー合意まで保留
- `error` / `critical` が出たら停止し、修正または判断待ちへ回す
- Unity 環境がない場合は dry-run / 検査までで停止
- `patch_apply` の confirm モードでは `change_reason` を必須とする（監査ログ）
```

- [ ] **Step 2: Create variant patterns knowledge file**

Write `knowledge/prefab-sentinel-variant-patterns.md`:

```markdown
# prefab-sentinel Variant 操作パターン

## 基本情報

| 項目 | 値 |
|------|---|
| 対象 | Prefab Variant の検査・操作パターン |
| version_tested | prefab-sentinel 0.5.71 |
| last_updated | 2026-03-26 |
| confidence | high |

## L1: Variant チェーン分析

### いつ使うか
- マテリアルオーバーライドの調査・修正（`inspect_variant` + `inspect_materials`）
- プロパティ値の追跡（origin 付きで各プロパティがどのレベルの Prefab で設定されたか確認）
- Variant 固有の壊れた参照の修復

### Variant チェーン値の追跡
`inspect_variant` で各プロパティ値がどの Prefab で設定されたかを表示。3段 Variant (Base → Mid → Leaf) の場合、各値に `origin_path` と `origin_depth` が付与される。

## L2: VRChat アバター着せ替え（MA/NDMF）

MA (Modular Avatar) / NDMF ベースの VRChat アバターでは、コスメティックや衣装の差し替えに Variant チェーン分析は**不要**。

### MA/NDMF パターン（着せ替え）
- コスメティック（ネイル、リボン、アクセサリ等）はアバタールートの**子 Prefab として追加するだけ**で、MA がビルド時にマージする
- 差し替え = 旧 Prefab を `editor_delete` で削除 + 新 Prefab を `editor_instantiate` で追加
- Variant チェーンの fileID 比較や m_SourcePrefab 書き換えは**不要**
- 衣装差し替えも同様: 旧衣装 Prefab を削除 → 新衣装 Prefab を子として追加

### 判断フロー
```
着せ替え/差し替え作業？
├─ MA/NDMF ベース → 子オブジェクト操作（instantiate/delete）で完結
│   └─ Variant チェーン分析は不要
└─ 非 MA/NDMF or マテリアル修正 → Variant 分析が有効
    └─ inspect_variant / inspect_materials で調査
```
```

- [ ] **Step 3: Remove operational sections from guide, add knowledge links**

In `skills/guide/SKILL.md`, replace lines 176-218 (ワークフロー選択 + VRChat アバター着せ替え) with:

```markdown
## ワークフロー・実践パターン

詳細は knowledge ファイルを参照:
- `knowledge/prefab-sentinel-workflow-patterns.md` — ワークフロー選択、判断ルール
- `knowledge/prefab-sentinel-variant-patterns.md` — Variant 操作、VRChat 着せ替え
- `knowledge/prefab-sentinel-editor-camera.md` — カメラ操作パターン
- `knowledge/prefab-sentinel-material-operations.md` — マテリアル操作パターン
- `knowledge/prefab-sentinel-wiring-triage.md` — 配線検査の読み方
- `knowledge/prefab-sentinel-patch-patterns.md` — パッチ計画の実例集
```

Also remove the 判断ルール section (lines 260-265) since it's now in `prefab-sentinel-workflow-patterns.md`.

- [ ] **Step 4: Commit**

```bash
git add knowledge/prefab-sentinel-workflow-patterns.md knowledge/prefab-sentinel-variant-patterns.md skills/guide/SKILL.md
git commit -m "docs: extract operational content from guide to knowledge files"
```

---

## Task 10: Documentation — Create remaining knowledge files

**Files:**
- Create: `knowledge/prefab-sentinel-material-operations.md`
- Create: `knowledge/prefab-sentinel-patch-patterns.md`
- Create: `knowledge/prefab-sentinel-wiring-triage.md`

- [ ] **Step 1: Create material operations knowledge file**

Write `knowledge/prefab-sentinel-material-operations.md`:

```markdown
# prefab-sentinel マテリアル操作パターン

## 基本情報

| 項目 | 値 |
|------|---|
| 対象 | MCP ツールによるマテリアル読み書きパターン |
| version_tested | prefab-sentinel 0.5.71 |
| last_updated | 2026-03-26 |
| confidence | high |

## L1: ツール選択

| 目的 | ツール | 備考 |
|------|--------|------|
| オフラインで .mat の内容確認 | `inspect_material_asset` | Unity 不要 |
| ランタイムのスロット一覧 | `editor_list_materials` | Editor Bridge 必須 |
| ランタイムのプロパティ読み取り | `editor_get_material_property` | Editor Bridge 必須 |
| ランタイムでプロパティ変更（一時的） | `editor_set_material_property` | Undo 対応、再生停止で戻る |
| .mat ファイルを永続変更 | `set_material_property` | dry-run/confirm ゲート付き |
| マテリアルスロット差し替え | `editor_set_material` | Undo 対応 |

## L2: 実践パターン

### liltoon カラー変更
```
editor_get_material_property(property_name="_Color")
→ 現在値を確認
editor_set_material_property(property_name="_Color", property_value='{"r":1,"g":0.8,"b":0.7,"a":1}')
→ ランタイムで即時プレビュー
editor_screenshot()
→ 視覚確認
```
永続化する場合は `set_material_property` で .mat ファイルを直接編集。

### テクスチャ差し替え
```
editor_set_material_property(
    property_name="_MainTex",
    property_value='{"guid":"<新テクスチャのGUID>","fileID":2800000}'
)
```
fileID 2800000 はテクスチャアセットの標準 fileID。

### float プロパティ調整
```
editor_get_material_property(property_name="_MainColorPower")
→ 現在値を確認（liltoon: 0.0〜1.0、0.5 以下だと暗すぎることが多い）
editor_set_material_property(property_name="_MainColorPower", property_value="0.7")
```

## 実運用で学んだこと

### 2026-03-26: マテリアル操作の実測
- `editor_list_materials` / `editor_get_material_property` / `editor_set_material_property` はいずれも <1s で応答
- `editor_set_material_property` の型はシェーダー定義から自動判定される（明示不要）
- `editor_screenshot` と組み合わせた反復調整が非常にスムーズ
```

- [ ] **Step 2: Create patch patterns knowledge file**

Write `knowledge/prefab-sentinel-patch-patterns.md`:

```markdown
# prefab-sentinel パッチ計画パターン

## 基本情報

| 項目 | 値 |
|------|---|
| 対象 | patch_apply で使う JSON パッチ計画の実例 |
| version_tested | prefab-sentinel 0.5.71 |
| last_updated | 2026-03-26 |
| confidence | high |

## L1: 基本パターン

### 単一プロパティの変更（open モード）
```json
{
  "plan_version": 2,
  "resources": [
    {"id": "r1", "kind": "prefab", "path": "Assets/.../Target.prefab", "mode": "open"}
  ],
  "ops": [
    {"resource": "r1", "op": "set", "component": "SkinnedMeshRenderer", "path": "m_Enabled", "value": true}
  ]
}
```

### 配列要素の挿入
```json
{"resource": "r1", "op": "insert_array_element", "component": "MyScript", "path": "m_Items.Array.data", "index": 0, "value": "newItem"}
```
**注意:** パスは `.Array.data` で終わる必要がある。

### ObjectReference の設定（open モード）
```json
{"resource": "r1", "op": "set", "component": "MyScript", "path": "m_Target", "value": {"guid": "abc123...", "fileID": 10207}}
```
open モードではハンドル文字列（`"$root"` 等）は使えない。

## L2: 複合パターン

### Prefab 新規作成 + コンポーネント追加
```json
{
  "plan_version": 2,
  "resources": [
    {"id": "r1", "kind": "prefab", "path": "Assets/.../New.prefab", "mode": "create"}
  ],
  "ops": [
    {"resource": "r1", "op": "create_prefab", "name": "MyObject"},
    {"resource": "r1", "op": "add_component", "target": "root", "type": "MeshFilter", "result": "mf"},
    {"resource": "r1", "op": "add_component", "target": "root", "type": "MeshRenderer", "result": "mr"},
    {"resource": "r1", "op": "set", "target": "mf", "path": "m_Mesh", "value": {"fileID": 10207, "guid": "0000000000000000e000000000000000", "type": 0}},
    {"resource": "r1", "op": "save"}
  ]
}
```

### Scene 内のプロパティ変更
```json
{
  "plan_version": 2,
  "resources": [
    {"id": "s1", "kind": "scene", "path": "Assets/Scenes/Main.unity", "mode": "open"}
  ],
  "ops": [
    {"resource": "s1", "op": "open_scene"},
    {"resource": "s1", "op": "find_component", "target": "$scene", "type": "UnityEngine.Light", "result": "$light"},
    {"resource": "s1", "op": "set", "target": "$light", "path": "m_Intensity", "value": 2.5},
    {"resource": "s1", "op": "save_scene"}
  ]
}
```

## 実運用で学んだこと

- dry-run は必ず先に実行する。特に配列操作はインデックスのずれに注意
- `--change-reason` は後から監査ログを読み返すときに非常に有用
- 同型コンポーネントが複数ある場合は `TypeName@/hierarchy/path` で曖昧性を解消する
```

- [ ] **Step 3: Create wiring triage knowledge file**

Write `knowledge/prefab-sentinel-wiring-triage.md`:

```markdown
# prefab-sentinel 配線検査トリアージ

## 基本情報

| 項目 | 値 |
|------|---|
| 対象 | inspect_wiring の結果の読み方と対処法 |
| version_tested | prefab-sentinel 0.5.71 |
| last_updated | 2026-03-26 |
| confidence | high |

## L1: 分類と対処

### null 参照（severity: error）
- フィールドが null / missing で、スクリプトが必須参照として使用
- 対処: `set_property` または `patch_apply` で正しい参照を設定

### fileID 不整合（severity: error）
- 参照先の fileID が対象ファイル内に存在しない
- 原因: Base Prefab の構造変更後に Variant の override が追従できていない
- 対処: `validate_refs` で詳細確認 → `prefab-reference-repair` スキルで修復

### 重複参照
- **same-component（severity: warning）**: 同一コンポーネント内の複数フィールドが同じオブジェクトを参照
- **cross-component（severity: info）**: 異なるコンポーネントから同じオブジェクトを参照（通常は正常）

## L2: トリアージフロー

1. `inspect_wiring --path <target>` を実行
2. error 件数を確認 — 0 件なら配線は健全
3. null 参照の error を優先対処（ランタイム停止リスク）
4. fileID 不整合は `validate_refs` で追加調査
5. warning/info は状況に応じて対応（多くは許容可能）

### Variant ファイルの注意点
- Variant に対して `inspect_wiring` を実行すると、ベース Prefab のコンポーネントも自動解析される
- override で上書きされた参照は Variant 側の値が表示される
```

- [ ] **Step 4: Commit**

```bash
git add knowledge/prefab-sentinel-material-operations.md knowledge/prefab-sentinel-patch-patterns.md knowledge/prefab-sentinel-wiring-triage.md
git commit -m "docs: create material, patch, wiring triage knowledge files"
```

---

## Task 11: Documentation — Update camera knowledge + Bridge setup + README

**Files:**
- Modify: `knowledge/prefab-sentinel-editor-camera.md`
- Modify: `skills/guide/SKILL.md`
- Modify: `README.md`

- [ ] **Step 1: Update camera knowledge for new API**

In `knowledge/prefab-sentinel-editor-camera.md`, update the L1 section:

Replace the "2 つのモード（排他だが内部は同じ）" section with:

```markdown
### 統合 API（v0.5.72+）

旧 Mode A / Mode B は廃止され、単一の API に統合。

**Pivot orbit（基本）:**
- `pivot`: 注視点（SceneView.pivot）
- `yaw`: 水平角。0=正面(+Z方向を見る)
- `pitch`: 垂直角
- `distance`: SceneView.size（小さいほどズーム）

**Position モード（新規）:**
- `position` + `look_at`: カメラ位置と注視先を指定。pivot=look_at、rotation と distance を自動逆算
- `position` + `yaw`/`pitch`: カメラ位置と向きを指定。pivot を逆算

**制約:**
- `position` と `pivot` の同時指定はエラー
- `look_at` は `position` が必須

### レスポンス
`set_camera` は `previous`（変更前）と `current`（変更後）のカメラ状態を返す。`get_camera` で事前取得せずに元の状態に戻せる。
```

Update the "editor_set_camera 後のカメラジャンプ問題" section:

```markdown
### RepaintAllViews による自動リフレッシュ（v0.5.72+）
- `set_camera` 後に自動で `RepaintAllViews` が実行されるため、手動で `editor_refresh` を呼ぶ必要はなくなった
- バックグラウンドの Unity でも反映される（即時 + 1フレーム遅延の2段構え）
```

- [ ] **Step 2: Add Bridge transfer procedure to guide**

In `skills/guide/SKILL.md`, find the "Unity ブリッジセットアップ" section (line 267). Add a subsection after "セットアップ手順" (line 273):

```markdown
### C# スクリプトの転送（更新時）

Bridge の C# スクリプトを更新する場合:

1. `tools/unity/` から以下を Unity プロジェクトの `Assets/Editor/` に上書きコピー:
   - `PrefabSentinel.EditorBridge.cs`
   - `PrefabSentinel.UnityEditorControlBridge.cs`
   - `PrefabSentinel.UnityPatchBridge.cs`
   - `PrefabSentinel.UnityRuntimeValidationBridge.cs`
2. `editor_recompile` を実行（AssetDatabase.Refresh + スクリプト再コンパイルが自動実行される）
```

- [ ] **Step 3: Update guide tool table**

In `skills/guide/SKILL.md`, line 54, update the editor_camera entry:

Replace:
```markdown
| `editor_camera` | Scene ビューのカメラ方向設定（yaw/pitch/distance） |
```

With:
```markdown
| `editor_set_camera` | Scene ビューのカメラ設定（pivot orbit / position + look_at） |
| `editor_get_camera` | Scene ビューのカメラ状態取得 |
| `editor_get_blend_shapes` | SkinnedMeshRenderer の BlendShape 一覧取得 |
| `editor_set_blend_shape` | BlendShape ウェイトを名前で設定（Undo 対応） |
| `editor_set_material_property` | シェーダープロパティ値を設定（Undo 対応） |
```

- [ ] **Step 4: Slim README Bridge section**

In `README.md`, find the "Unity環境があるとできること" section. Replace the `editor camera` description:

Replace:
```markdown
`editor camera` で Scene View カメラの向き制御（yaw/pitch/distance）
```

With:
```markdown
`editor set-camera` で Scene View カメラ制御（pivot orbit / position + look_at、変更前の状態を自動返却）
```

- [ ] **Step 5: Commit**

```bash
git add knowledge/prefab-sentinel-editor-camera.md skills/guide/SKILL.md README.md
git commit -m "docs: update camera knowledge for unified API, add Bridge transfer procedure"
```

---

## Task 12: Final verification

- [ ] **Step 1: Run full Python test suite**

Run: `uv run python -m pytest tests/ -x -q`
Expected: all tests PASS.

- [ ] **Step 2: Lint check**

Run: `uv run ruff check prefab_sentinel/ tests/`
Expected: no errors.

- [ ] **Step 3: Verify knowledge file coverage**

Check all 7 knowledge files exist:
```bash
ls knowledge/prefab-sentinel-*.md
```

Expected:
```
knowledge/prefab-sentinel-editor-camera.md
knowledge/prefab-sentinel-material-operations.md
knowledge/prefab-sentinel-patch-patterns.md
knowledge/prefab-sentinel-variant-patterns.md
knowledge/prefab-sentinel-wiring-triage.md
knowledge/prefab-sentinel-workflow-patterns.md
```

- [ ] **Step 4: Note for manual verification**

The following require Unity Editor with Bridge running:
- `editor_set_camera` — pivot orbit mode, position + look_at mode, position + yaw/pitch mode
- `editor_frame` — bounds_center and bounds_extents in response
- `editor_recompile` — AssetDatabase.Refresh runs before recompilation
- Repaint enhancement — camera doesn't jump when Unity is in background
- SupportedActions error — send unknown action, verify descriptive error message
