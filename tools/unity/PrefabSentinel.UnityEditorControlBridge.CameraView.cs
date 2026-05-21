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

        /// <summary>
        /// Resolve the Scene-view camera world position synchronously from
        /// the supplied view's already-settled pivot, rotation, size and
        /// projection (issue #74).  Unity recomputes
        /// ``camera.transform.position`` only on its next camera refresh,
        /// so a transform read taken in the same dispatch frame as a
        /// ``LookAt`` call reports the pre-call position.
        ///
        /// The camera distance is derived here from ``size`` and the
        /// projection flag rather than read from
        /// ``SceneView.cameraDistance``: across a same-call projection
        /// switch (issue #73) that property is transiently invalid — it
        /// evaluates the sine-based perspective distance against a
        /// field-of-view still mid-transition, blowing up to a
        /// near-divide-by-zero value.  ``size``, ``orthographic``,
        /// ``pivot`` and ``rotation`` are all settled synchronously by
        /// ``LookAt(instant:true)``, so this derivation is correct
        /// in-frame.  The transform mirrors
        /// SceneView.GetPerspectiveCameraDistance — ``size / Sin(fov/2)``
        /// for perspective, ``size * 2`` for orthographic.
        /// </summary>
        private static float[] ResolveSyncedCameraPosition(SceneView sv, float fov)
        {
            Vector3 forward = sv.rotation * Vector3.forward;
            float cameraDistance = sv.orthographic
                ? sv.size * 2f
                : sv.size / Mathf.Sin(fov * 0.5f * Mathf.Deg2Rad);
            Vector3 pos = sv.pivot - forward * cameraDistance;
            return new[] { pos.x, pos.y, pos.z };
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

        // HandleCaptureScreenshot moved to the dedicated Screenshot
        // partial (issue #123 + #249); the camera-state partial now owns
        // only camera state get / set / frame.

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
            if (!TryResolveGameObjectInActiveStage(
                request.hierarchy_path, out GameObject go, out var ambiguity))
            {
                if (ambiguity != null) return ambiguity;
                return BuildError("EDITOR_CTRL_OBJECT_NOT_FOUND",
                    $"GameObject not found: {request.hierarchy_path}");
            }

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

            // Issue #75: resolve a concrete Bounds so the frame can be
            // driven through SceneView.Frame(bounds, instant:true). The
            // animated FrameSelected() leaves pivot and size un-advanced
            // when CaptureCameraState reads them in the same dispatch
            // frame, so the response reported the entire pre-frame camera
            // snapshot. boundsCenter/Extents stay null for an object with
            // neither a Renderer nor a RectTransform — preserving the
            // response's existing "bounds unavailable" contract — while a
            // unit-size fallback Bounds still drives the instant frame.
            float[] boundsCenter = null;
            float[] boundsExtents = null;
            bool haveBounds = false;
            Bounds frameBounds = new Bounds(selectedGo.transform.position, Vector3.one);
            Renderer renderer = selectedGo.GetComponentInChildren<Renderer>();
            if (renderer != null)
            {
                frameBounds = renderer.bounds;
                haveBounds = true;
            }
            else
            {
                // RectTransform fallback: frame the world-space AABB of the
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
                    frameBounds = new Bounds((min + max) * 0.5f, max - min);
                    haveBounds = true;
                }
            }
            if (haveBounds)
            {
                boundsCenter = new[]
                    { frameBounds.center.x, frameBounds.center.y, frameBounds.center.z };
                boundsExtents = new[]
                    { frameBounds.extents.x, frameBounds.extents.y, frameBounds.extents.z };
            }

            // Frame synchronously (issue #75): SceneView.Frame with
            // instant:true settles pivot and size in this dispatch frame,
            // unlike the animated FrameSelected().
            sceneView.Frame(frameBounds, instant: true);
            if (request.zoom > 0f)
                sceneView.size = request.zoom;
            ForceRenderAndRepaint(sceneView);

            CameraSnapshot cam = CaptureCameraState(sceneView);
            // Issue #74 / #75: camera.transform.position is recomputed by
            // Unity only on its next camera refresh, so resolve it
            // synchronously from the now-settled pivot/rotation/size.
            cam.position = ResolveSyncedCameraPosition(
                sceneView, sceneView.camera.fieldOfView);
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
            // Issue #81: orbit-radius field is named ``size`` end-to-end.
            bool hasSize = request.size >= 0f;

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
                resetState.position = ResolveSyncedCameraPosition(
                    sceneView, sceneView.camera.fieldOfView);
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

            // Issue #73: apply the projection switch before the
            // field-of-view read and the position/pivot geometry.  A
            // single call that both switches projection and positions the
            // camera must compute its geometry — and pass the projection
            // flag into LookAt — under the requested projection, not the
            // pre-switch one.
            if (request.camera_orthographic >= 0)
                sceneView.orthographic = request.camera_orthographic == 1;

            // Issue #66: Unity's SceneView camera-distance contract is
            // sine-based — cameraDistance = size / Sin(fov/2), matching
            // SceneView.GetPerspectiveCameraDistance.  The perspective
            // position-mode reverse-solve below uses Mathf.Sin so the
            // set -> get round-trip closes and the camera lands at the
            // requested position.
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
                        : dist * Mathf.Sin(fov * 0.5f * Mathf.Deg2Rad);
                    sceneView.LookAt(
                        lookAt, rot, newSize, sceneView.orthographic,
                        instant: true);
                }
                else
                {
                    float newSize = hasSize ? request.size : sceneView.size;
                    Vector3 currentEuler = sceneView.rotation.eulerAngles;
                    float curYaw = (currentEuler.y + 180f) % 360f;
                    float curPitch = currentEuler.x > 180f ? currentEuler.x - 360f : currentEuler.x;
                    float newYaw = hasYaw ? request.yaw : curYaw;
                    float newPitch = hasPitch ? request.pitch : curPitch;
                    float internalYaw = (newYaw + 180f) % 360f;
                    Quaternion rot = Quaternion.Euler(newPitch, internalYaw, 0f);

                    float cameraDistance = sceneView.orthographic
                        ? newSize * 2f
                        : newSize / Mathf.Sin(fov * 0.5f * Mathf.Deg2Rad);
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

                float newSize = hasSize ? request.size : sceneView.size;
                sceneView.LookAt(
                    newPivot, newRot, newSize, sceneView.orthographic,
                    instant: true);
            }

            ForceRenderAndRepaint(sceneView);

            CameraSnapshot current = CaptureCameraState(sceneView);
            current.position = ResolveSyncedCameraPosition(sceneView, fov);
            return BuildSuccess("EDITOR_CTRL_CAMERA_SET_OK", "Camera updated",
                data: BuildCameraData(current, previous));
        }
    }
}
