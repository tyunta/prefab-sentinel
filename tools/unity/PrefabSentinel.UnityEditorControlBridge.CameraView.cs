using System;
using System.IO;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.UI;

namespace PrefabSentinel
{
    /// <summary>
    /// Camera + view partial: capture_screenshot, select_object, ping_object,
    /// frame_selected, get_camera, set_camera, plus the camera-snapshot
    /// helpers shared between the get / set / frame paths.  RectTransform /
    /// physics synchronisation needed by ``frame_selected`` to read accurate
    /// post-edit bounds also lives here so all view-side concerns stay
    /// together.
    /// </summary>
    public static partial class UnityEditorControlBridge
    {
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

        // Documented Scene-view defaults restored by ``reset_to_defaults``.
        // See README "Editor camera modes" — kept here so the contract is
        // legible alongside the reset path.
        private static readonly Vector3 DefaultScenePivot = Vector3.zero;
        private static readonly Quaternion DefaultSceneRotation =
            Quaternion.Euler(30f, -45f, 0f);
        private const float DefaultSceneSize = 10f;
        private const bool DefaultSceneOrthographic = false;

        // ── Action handlers ──

        private static EditorControlResponse HandleCaptureScreenshot(EditorControlRequest request, string requestPath)
        {
            string outputDir = Path.Combine(Path.GetDirectoryName(requestPath), "screenshots");
            if (!Directory.Exists(outputDir))
                Directory.CreateDirectory(outputDir);

            string timestamp = DateTime.Now.ToString("yyyyMMdd_HHmmss");
            string outputPath = Path.Combine(outputDir, $"{request.view}_{timestamp}.png");

            bool isScene = string.Equals(request.view, "scene", StringComparison.OrdinalIgnoreCase);

            try
            {
                if (isScene)
                {
                    SceneView sceneView = SceneView.lastActiveSceneView;
                    if (sceneView == null)
                        return BuildError("EDITOR_CTRL_NO_SCENE_VIEW", "No active SceneView found.");

                    int w = request.width > 0 ? request.width : (int)sceneView.position.width;
                    int h = request.height > 0 ? request.height : (int)sceneView.position.height;

                    Camera cam = sceneView.camera;
                    if (cam == null)
                        return BuildError("EDITOR_CTRL_NO_SCENE_CAMERA", "SceneView camera is null.");

                    RenderTexture rt = null;
                    Texture2D tex = null;
                    try
                    {
                        // Force skinning recalculation so blend shape / material
                        // changes are reflected even when Unity is unfocused
                        var smrs = UnityEngine.Object.FindObjectsOfType<SkinnedMeshRenderer>();
                        foreach (var smr in smrs)
                            smr.forceMatrixRecalculationPerRender = true;

                        rt = new RenderTexture(w, h, 24);
                        RenderTexture prev = cam.targetTexture;
                        cam.targetTexture = rt;
                        cam.Render();
                        cam.targetTexture = prev;

                        // Restore to avoid per-frame overhead
                        foreach (var smr in smrs)
                            smr.forceMatrixRecalculationPerRender = false;

                        RenderTexture.active = rt;
                        tex = new Texture2D(w, h, TextureFormat.RGB24, false);
                        tex.ReadPixels(new Rect(0, 0, w, h), 0, 0);
                        tex.Apply();
                        RenderTexture.active = null;

                        byte[] png = tex.EncodeToPNG();
                        File.WriteAllBytes(outputPath, png);
                    }
                    finally
                    {
                        if (tex != null) UnityEngine.Object.DestroyImmediate(tex);
                        if (rt != null) UnityEngine.Object.DestroyImmediate(rt);
                        RenderTexture.active = null;
                    }

                    return BuildSuccess("EDITOR_CTRL_SCREENSHOT_OK", $"Scene view captured to {outputPath}",
                        data: new EditorControlData
                        {
                            output_path = outputPath,
                            view = "scene",
                            width = w,
                            height = h,
                            executed = true
                        });
                }
                else
                {
                    // Game view: use ScreenCapture (requires game view to exist)
                    int w = request.width > 0 ? request.width : Screen.width;
                    int h = request.height > 0 ? request.height : Screen.height;

                    Texture2D tex = ScreenCapture.CaptureScreenshotAsTexture();
                    if (tex == null)
                        return BuildError("EDITOR_CTRL_NO_GAME_VIEW", "Failed to capture game view. Ensure Game view is visible.");

                    RenderTexture rt = null;
                    try
                    {
                        if (request.width > 0 && request.height > 0)
                        {
                            // Resize if custom dimensions requested
                            rt = RenderTexture.GetTemporary(request.width, request.height);
                            Graphics.Blit(tex, rt);
                            UnityEngine.Object.DestroyImmediate(tex);

                            RenderTexture.active = rt;
                            tex = new Texture2D(request.width, request.height, TextureFormat.RGB24, false);
                            tex.ReadPixels(new Rect(0, 0, request.width, request.height), 0, 0);
                            tex.Apply();
                            RenderTexture.active = null;

                            w = request.width;
                            h = request.height;
                        }

                        byte[] png = tex.EncodeToPNG();
                        File.WriteAllBytes(outputPath, png);
                    }
                    finally
                    {
                        if (tex != null) UnityEngine.Object.DestroyImmediate(tex);
                        if (rt != null) RenderTexture.ReleaseTemporary(rt);
                        RenderTexture.active = null;
                    }

                    return BuildSuccess("EDITOR_CTRL_SCREENSHOT_OK", $"Game view captured to {outputPath}",
                        data: new EditorControlData
                        {
                            output_path = outputPath,
                            view = "game",
                            width = w,
                            height = h,
                            executed = true
                        });
                }
            }
            catch (Exception ex)
            {
                return BuildError("EDITOR_CTRL_SCREENSHOT_FAILED", $"Screenshot failed: {ex.Message}");
            }
        }

        private static EditorControlResponse HandleSelectObject(EditorControlRequest request)
        {
            if (string.IsNullOrEmpty(request.hierarchy_path))
                return BuildError("EDITOR_CTRL_MISSING_PATH", "hierarchy_path is required for select_object.");

            // Prefab Stage mode: open the prefab and search within its stage root
            if (!string.IsNullOrEmpty(request.prefab_asset_path))
            {
                var stage = PrefabStageUtility.OpenPrefab(request.prefab_asset_path);
                if (stage == null)
                    return BuildError("EDITOR_CTRL_PREFAB_STAGE_FAILED",
                        $"Failed to open Prefab Stage: {request.prefab_asset_path}");

                var stageRoot = stage.prefabContentsRoot;
                if (stageRoot == null)
                    return BuildError("EDITOR_CTRL_PREFAB_STAGE_FAILED",
                        $"Prefab Stage root is null: {request.prefab_asset_path}");

                Transform target = stageRoot.transform.Find(request.hierarchy_path);
                if (target == null && stageRoot.name == request.hierarchy_path)
                    target = stageRoot.transform;

                if (target == null)
                    return BuildError("EDITOR_CTRL_OBJECT_NOT_FOUND",
                        $"GameObject not found in Prefab Stage: {request.hierarchy_path}");

                Selection.activeGameObject = target.gameObject;
                EditorApplication.delayCall += () =>
                {
                    var psv = SceneView.lastActiveSceneView;
                    if (psv != null) { psv.FrameSelected(); psv.Repaint(); }
                };
                return BuildSuccess("EDITOR_CTRL_SELECT_OK",
                    $"Selected in Prefab Stage: {request.hierarchy_path}",
                    data: new EditorControlData
                    {
                        selected_object = request.hierarchy_path,
                        executed = true
                    });
            }

            // Scene mode: search scene hierarchy
            GameObject go = GameObject.Find(request.hierarchy_path);
            if (go == null)
                return BuildError("EDITOR_CTRL_OBJECT_NOT_FOUND",
                    $"GameObject not found: {request.hierarchy_path}");

            Selection.activeGameObject = go;
            EditorApplication.delayCall += () =>
            {
                var sv = SceneView.lastActiveSceneView;
                if (sv != null) { sv.FrameSelected(); sv.Repaint(); }
            };

            return BuildSuccess("EDITOR_CTRL_SELECT_OK",
                $"Selected: {request.hierarchy_path}",
                data: new EditorControlData
                {
                    selected_object = request.hierarchy_path,
                    executed = true
                });
        }

        /// <summary>
        /// Bring UGUI canvas state, RectTransform layout, and physics
        /// transforms up to date for the supplied subtree (issue #115).
        /// Without this, ``editor_frame`` can read stale bounds when a
        /// caller sets a RectTransform property and immediately frames it.
        /// </summary>
        private static void SynchronizeBoundsSourcesForFrame(GameObject root)
        {
            if (root == null) return;

            Canvas.ForceUpdateCanvases();

            var rectTransforms = root.GetComponentsInChildren<RectTransform>(true);
            foreach (var rt in rectTransforms)
            {
                LayoutRebuilder.ForceRebuildLayoutImmediate(rt);
            }

            if (root.GetComponentInChildren<Collider>(true) != null)
            {
                Physics.SyncTransforms();
            }
        }

        private static EditorControlResponse HandleFrameSelected(EditorControlRequest request)
        {
            GameObject selectedGo = Selection.activeGameObject;
            if (selectedGo == null)
                return BuildError("EDITOR_CTRL_NO_SELECTION", "No GameObject is selected. Use select_object first.");

            SceneView sceneView = SceneView.lastActiveSceneView;
            if (sceneView == null)
                return BuildError("EDITOR_CTRL_NO_SCENE_VIEW", "No active SceneView found.");

            string objectName = selectedGo.name;

            // Pre-bounds synchronization (issue #115): bring UGUI canvas
            // state, RectTransform layout, and physics transforms up to
            // date before reading bounds so post-edit framing is accurate.
            SynchronizeBoundsSourcesForFrame(selectedGo);

            float[] boundsCenter = null;
            float[] boundsExtents = null;
            Renderer renderer = selectedGo.GetComponentInChildren<Renderer>();
            if (renderer != null)
            {
                Bounds b = renderer.bounds;
                boundsCenter = new[] { b.center.x, b.center.y, b.center.z };
                boundsExtents = new[] { b.extents.x, b.extents.y, b.extents.z };
            }
            else
            {
                // RectTransform fallback: report the world-space AABB of the
                // selected RectTransform when no Renderer is in the subtree.
                var rect = selectedGo.GetComponent<RectTransform>();
                if (rect != null)
                {
                    var corners = new Vector3[4];
                    rect.GetWorldCorners(corners);
                    Vector3 min = corners[0], max = corners[0];
                    for (int i = 1; i < 4; i++)
                    {
                        min = Vector3.Min(min, corners[i]);
                        max = Vector3.Max(max, corners[i]);
                    }
                    Vector3 center = (min + max) * 0.5f;
                    Vector3 extents = (max - min) * 0.5f;
                    boundsCenter = new[] { center.x, center.y, center.z };
                    boundsExtents = new[] { extents.x, extents.y, extents.z };
                }
            }

            // Frame synchronously so we can capture post-frame camera state
            sceneView.FrameSelected();
            if (request.zoom > 0f)
                sceneView.size = request.zoom;
            ForceRenderAndRepaint(sceneView);

            CameraSnapshot cam = CaptureCameraState(sceneView);
            var data = BuildCameraData(cam);
            data.selected_object = objectName;
            data.bounds_center = boundsCenter;
            data.bounds_extents = boundsExtents;

            return BuildSuccess("EDITOR_CTRL_FRAME_OK",
                $"Framed: {objectName}" + (request.zoom > 0f ? $" (zoom={request.zoom})" : ""),
                data: data);
        }

        private static EditorControlResponse HandlePingObject(EditorControlRequest request)
        {
            if (string.IsNullOrEmpty(request.asset_path))
                return BuildError("EDITOR_CTRL_MISSING_PATH", "asset_path is required for ping_object.");

            var obj = AssetDatabase.LoadAssetAtPath<UnityEngine.Object>(request.asset_path);
            if (obj == null)
                return BuildError("EDITOR_CTRL_ASSET_NOT_FOUND",
                    $"Asset not found at: {request.asset_path}");

            EditorGUIUtility.PingObject(obj);

            return BuildSuccess("EDITOR_CTRL_PING_OK",
                $"Pinged: {request.asset_path}",
                data: new EditorControlData { executed = true });
        }

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

        private static EditorControlResponse HandleSetCamera(EditorControlRequest request)
        {
            SceneView sceneView = SceneView.lastActiveSceneView;
            if (sceneView == null)
                return BuildError("EDITOR_CTRL_NO_SCENE_VIEW", "No active SceneView found.");

            CameraSnapshot previous = CaptureCameraState(sceneView);

            bool hasPosition = request.camera_position != null && request.camera_position.Length == 3;
            bool hasLookAt = request.camera_look_at != null && request.camera_look_at.Length == 3;
            bool hasPivot = request.camera_pivot != null && request.camera_pivot.Length == 3;
            bool hasYaw = !float.IsNaN(request.yaw);
            bool hasPitch = !float.IsNaN(request.pitch);
            bool hasDistance = request.distance >= 0f;

            // Reset mode (issue #112): restore the SceneView to documented
            // defaults via the public synchronous LookAt entry point and
            // ignore the other camera fields entirely. The reset response
            // still reports the previous state for diff-style auditing.
            if (request.reset_to_defaults)
            {
                sceneView.LookAt(
                    DefaultScenePivot,
                    DefaultSceneRotation,
                    DefaultSceneSize,
                    DefaultSceneOrthographic,
                    instant: true);
                sceneView.orthographic = DefaultSceneOrthographic;
                ForceRenderAndRepaint(sceneView);
                CameraSnapshot resetState = CaptureCameraState(sceneView);
                return BuildSuccess(
                    "EDITOR_CTRL_CAMERA_SET_OK",
                    "Camera reset to defaults",
                    data: BuildCameraData(resetState, previous));
            }

            if (hasPosition && hasPivot)
                return BuildError("EDITOR_CTRL_CAMERA_CONFLICT",
                    "Cannot specify both 'position' and 'pivot'; specify one.");
            if (hasLookAt && !hasPosition)
                return BuildError("EDITOR_CTRL_CAMERA_CONFLICT",
                    "'look_at' requires 'position' to be set.");

            float fov = sceneView.camera.fieldOfView;

            if (hasPosition)
            {
                Vector3 cameraPos = new Vector3(
                    request.camera_position[0],
                    request.camera_position[1],
                    request.camera_position[2]);

                if (hasLookAt)
                {
                    // Position + look_at mode (issue #112): drive the SceneView
                    // through LookAt(instant=true) so the achieved camera
                    // position is observable in the response without waiting
                    // for an asynchronous transform refresh.
                    Vector3 lookAt = new Vector3(
                        request.camera_look_at[0],
                        request.camera_look_at[1],
                        request.camera_look_at[2]);
                    Vector3 direction = (lookAt - cameraPos).normalized;
                    float dist = Vector3.Distance(cameraPos, lookAt);
                    Quaternion rot = Quaternion.LookRotation(direction);
                    float newSize = sceneView.orthographic
                        ? dist * 0.5f
                        : dist * Mathf.Tan(fov * 0.5f * Mathf.Deg2Rad);
                    sceneView.LookAt(
                        lookAt, rot, newSize, sceneView.orthographic,
                        instant: true);
                }
                else
                {
                    float newSize = hasDistance ? request.distance : sceneView.size;
                    Vector3 currentEuler = sceneView.rotation.eulerAngles;
                    float curYaw = (currentEuler.y + 180f) % 360f;
                    float curPitch = currentEuler.x > 180f ? currentEuler.x - 360f : currentEuler.x;
                    float newYaw = hasYaw ? request.yaw : curYaw;
                    float newPitch = hasPitch ? request.pitch : curPitch;
                    float internalYaw = (newYaw + 180f) % 360f;
                    Quaternion rot = Quaternion.Euler(newPitch, internalYaw, 0f);

                    float cameraDistance = sceneView.orthographic
                        ? newSize * 2f
                        : newSize / Mathf.Tan(fov * 0.5f * Mathf.Deg2Rad);
                    Vector3 newPivot = cameraPos + rot * new Vector3(0, 0, cameraDistance);
                    sceneView.LookAt(
                        newPivot, rot, newSize, sceneView.orthographic,
                        instant: true);
                }
            }
            else
            {
                // Pivot orbit mode.
                Vector3 newPivot = hasPivot
                    ? new Vector3(
                        request.camera_pivot[0],
                        request.camera_pivot[1],
                        request.camera_pivot[2])
                    : sceneView.pivot;

                Quaternion newRot = sceneView.rotation;
                if (hasYaw || hasPitch)
                {
                    Vector3 currentEuler = sceneView.rotation.eulerAngles;
                    float curYaw = (currentEuler.y + 180f) % 360f;
                    float curPitch = currentEuler.x > 180f ? currentEuler.x - 360f : currentEuler.x;
                    float newYaw = hasYaw ? request.yaw : curYaw;
                    float newPitch = hasPitch ? request.pitch : curPitch;
                    float internalYaw = (newYaw + 180f) % 360f;
                    newRot = Quaternion.Euler(newPitch, internalYaw, 0f);
                }

                float newSize = hasDistance ? request.distance : sceneView.size;
                sceneView.LookAt(
                    newPivot, newRot, newSize, sceneView.orthographic,
                    instant: true);
            }

            if (request.camera_orthographic >= 0)
                sceneView.orthographic = request.camera_orthographic == 1;

            ForceRenderAndRepaint(sceneView);

            CameraSnapshot current = CaptureCameraState(sceneView);
            return BuildSuccess("EDITOR_CTRL_CAMERA_SET_OK", "Camera updated",
                data: BuildCameraData(current, previous));
        }
    }
}
