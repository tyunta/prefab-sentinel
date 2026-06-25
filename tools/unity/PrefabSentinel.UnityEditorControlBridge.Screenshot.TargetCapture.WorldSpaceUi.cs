using System;
using System.Collections.Generic;
using System.IO;
using UnityEditor;
using UnityEngine;

// World Space UI target screenshot capture concern.
namespace PrefabSentinel
{
    public static partial class UnityEditorControlBridge
    {
        private static bool ShouldUseWorldSpaceUiCapture(
            EditorControlRequest request,
            GameObject target,
            out EditorControlResponse unsupported)
        {
            unsupported = null;
            bool wantsUi = request.target_mode == "world_space_ui";
            if (request.target_mode == "renderer") return false;

            Canvas canvas = ResolveRelevantCanvas(target);
            bool hasRect = target.GetComponent<RectTransform>() != null
                || target.GetComponentInChildren<RectTransform>(includeInactive: false) != null;
            if (!hasRect)
            {
                if (!wantsUi) return false;
                unsupported = BuildError(
                    "EDITOR_CTRL_SCREENSHOT_UI_UNSUPPORTED",
                    $"target='{request.target}' has no active RectTransform contributors.");
                return true;
            }
            if (canvas == null || canvas.renderMode != RenderMode.WorldSpace)
            {
                unsupported = BuildError(
                    "EDITOR_CTRL_SCREENSHOT_UI_UNSUPPORTED",
                    $"target='{request.target}' is not under a World Space Canvas.");
                return true;
            }
            return true;
        }

        private static Canvas ResolveRelevantCanvas(GameObject target)
        {
            Canvas ownOrParent = target.GetComponentInParent<Canvas>();
            if (ownOrParent != null) return ownOrParent;
            return target.GetComponentInChildren<Canvas>(includeInactive: false);
        }

        private static EditorControlResponse HandleWorldSpaceUiCaptureScreenshot(
            EditorControlRequest request,
            string outputPath,
            SceneView sceneView,
            UnityEngine.Camera cam,
            GameObject target,
            string angle)
        {
            if (angle != "front" && angle != "back" && angle != "current_camera")
            {
                return BuildError(
                    "EDITOR_CTRL_SCREENSHOT_ANGLE_INVALID",
                    $"World Space UI target capture accepts angle='front', 'back', or 'current_camera'; got '{angle}'.");
            }

            RectTransform anchor = target.GetComponent<RectTransform>();
            if (anchor == null)
                anchor = target.GetComponentInChildren<RectTransform>(includeInactive: false);
            if (anchor == null)
            {
                return BuildError(
                    "EDITOR_CTRL_SCREENSHOT_UI_UNSUPPORTED",
                    $"target='{request.target}' has no active RectTransform contributors.");
            }

            List<GeometryContributorRecord> records = CollectGeometryContributors(
                target, includeChildren: true);
            GeometryBoundsResult bounds = GeometryBoundsMath.Aggregate(
                ToBoundsContributors(records),
                "rect_transform",
                includeChildren: true);
            if (!bounds.Success)
            {
                return BuildError(
                    bounds.ErrorCode,
                    $"World Space UI bounds unavailable for target='{request.target}'.");
            }

            Vector3 center = DoubleArrayToVector3(bounds.Center);
            Vector3 extents = DoubleArrayToVector3(bounds.Extents);
            Vector3 uiNormal = (anchor.rotation * Vector3.forward).normalized;
            Vector3 cameraDir = -uiNormal;
            Vector3 up = (anchor.rotation * Vector3.up).normalized;
            if (angle == "back")
            {
                cameraDir = uiNormal;
            }
            else if (angle == "current_camera")
            {
                cameraDir = (-cam.transform.forward).normalized;
                up = cam.transform.up.normalized;
            }
            Quaternion lookRot = Quaternion.LookRotation(-cameraDir, up);
            int w = request.width > 0 ? request.width : (int)sceneView.position.width;
            int h = request.height > 0 ? request.height : (int)sceneView.position.height;
            float aspect = (float)w / (float)h;
            float paddedHalfHeight = Math.Max(extents.y, extents.x / Math.Max(aspect, 0.001f))
                * (1f + request.padding_ratio);
            if (paddedHalfHeight <= 0f) paddedHalfHeight = 0.01f;
            float distance = Math.Max(extents.z + paddedHalfHeight, 0.1f);
            Vector3 cameraPosition = center + cameraDir * distance;
            bool orthographic = request.projection != "perspective";

            CameraSnapshot previous = CaptureCameraState(sceneView);
            try
            {
                sceneView.LookAt(center, lookRot, paddedHalfHeight, orthographic, instant: true);
                cam.transform.position = cameraPosition;
                cam.transform.rotation = lookRot;
                cam.orthographic = orthographic;
                cam.orthographicSize = paddedHalfHeight;
                ForceRenderAndRepaint(sceneView);

                int readX = 0, readY = 0, readW = w, readH = h;
                CropBoundsEntry pixelRectApplied = null;
                RenderTexture rt = null;
                Texture2D tex = null;
                try
                {
                    EditorControlResponse cropError = ResolveTargetPixelCrop(
                        request, w, h, out readX, out readY, out readW, out readH,
                        out pixelRectApplied);
                    if (cropError != null) return cropError;

                    rt = RenderSceneViewToTexture(cam, w, h);
                    RenderTexture.active = rt;
                    tex = new Texture2D(readW, readH, TextureFormat.RGB24, false);
                    tex.ReadPixels(new Rect(readX, readY, readW, readH), 0, 0);
                    tex.Apply();
                    RenderTexture.active = null;
                    File.WriteAllBytes(outputPath, tex.EncodeToPNG());
                }
                finally
                {
                    if (tex != null) UnityEngine.Object.DestroyImmediate(tex);
                    if (rt != null) UnityEngine.Object.DestroyImmediate(rt);
                    RenderTexture.active = null;
                }

                return BuildSuccess(
                    "EDITOR_CTRL_SCREENSHOT_OK",
                    $"World Space UI screenshot of '{request.target}' captured to {outputPath}",
                    data: new EditorControlData
                    {
                        output_path = outputPath,
                        view = "scene",
                        width = readW,
                        height = readH,
                        executed = true,
                        target_mode = "world_space_ui",
                        projection = orthographic ? "orthographic" : "perspective",
                        bounds_source = "rect_transform",
                        bounds_center = Vector3ToArray(center),
                        bounds_extents = Vector3ToArray(extents),
                        ui_normal = Vector3ToArray(uiNormal),
                        camera_position = Vector3ToArray(cameraPosition),
                        camera_look_at = Vector3ToArray(center),
                        camera_orthographic = orthographic,
                        camera_size = paddedHalfHeight,
                        crop_roi_applied = pixelRectApplied != null ? "pixel_rect" : string.Empty,
                        crop_bounds = pixelRectApplied,
                    });
            }
            finally
            {
                RestoreSceneViewCameraState(previous);
            }
        }
    }
}
