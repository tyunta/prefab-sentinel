using System;
using System.Collections.Generic;
using System.IO;
using UnityEditor;
using UnityEngine;

// Target screenshot capture concern for renderer and World Space UI targets.
namespace PrefabSentinel
{
    public static partial class UnityEditorControlBridge
    {
        private static EditorControlResponse ResolveTargetPixelCrop(
            EditorControlRequest request,
            int frameWidth,
            int frameHeight,
            out int readX,
            out int readY,
            out int readW,
            out int readH,
            out CropBoundsEntry pixelRectApplied)
        {
            readX = 0;
            readY = 0;
            readW = frameWidth;
            readH = frameHeight;
            pixelRectApplied = null;
            if (request.crop_roi == null)
            {
                return BuildError(
                    "EDITOR_CTRL_CROP_ROI_INVALID",
                    "crop_roi is null.");
            }
            if (request.crop_roi.Length == 0)
                return null;

            if (!TryResolveCropRoi(request.crop_roi, out string roiLabel, out CropBoundsEntry roiBounds)
                || roiLabel != "pixel_rect"
                || roiBounds == null)
            {
                return BuildError(
                    "EDITOR_CTRL_CROP_ROI_INVALID",
                    $"crop_roi='{request.crop_roi}' is not a pixel "
                    + "quadruple (face-feature presets are rejected "
                    + "at the wrapper when target is supplied).");
            }

            if (!ScreenshotCropBounds.FitsWithinFrame(
                    roiBounds.x, roiBounds.y, roiBounds.w, roiBounds.h,
                    frameWidth, frameHeight))
            {
                return BuildError(
                    "EDITOR_CTRL_CROP_ROI_OUT_OF_BOUNDS",
                    $"crop_roi pixel rectangle {request.crop_roi} does "
                    + $"not fit inside the rendered frame {frameWidth}x{frameHeight}.");
            }

            readX = roiBounds.x;
            readY = roiBounds.y;
            readW = roiBounds.w;
            readH = roiBounds.h;
            pixelRectApplied = roiBounds;
            return null;
        }

        private static EditorControlResponse HandleObjectCaptureScreenshot(
            EditorControlRequest request, string outputPath)
        {
            string angle = request.angle;

            SceneView sceneView = SceneView.lastActiveSceneView;
            if (sceneView == null)
                return BuildError("EDITOR_CTRL_NO_SCENE_VIEW", "No active SceneView found.");
            UnityEngine.Camera cam = sceneView.camera;
            if (cam == null)
                return BuildError("EDITOR_CTRL_NO_SCENE_CAMERA", "SceneView camera is null.");

            EditorControlResponse ambiguity;
            bool resolved = TryResolveGameObjectInActiveStage(
                request.target, out GameObject target, out ambiguity);
            if (ambiguity != null) return ambiguity;
            if (!resolved || target == null)
            {
                return BuildError(
                    "EDITOR_CTRL_SCREENSHOT_TARGET_NOT_FOUND",
                    $"target='{request.target}' matched no GameObject in "
                    + "the active Scene or Prefab Stage.");
            }

            EditorControlResponse selectorError = ValidateTargetScreenshotSelectors(request);
            if (selectorError != null) return selectorError;

            if (request.fit_mode != "max_axis" && request.fit_mode != "both_axes")
            {
                return BuildError(
                    "SCREENSHOT_FIT_MODE_INVALID",
                    $"fit_mode='{request.fit_mode}' is not one of max_axis, both_axes.");
            }
            if (!IsSupportedRendererBoundsPolicy(request.bounds_policy))
            {
                return BuildBoundsPolicyInvalidError(request.bounds_policy);
            }

            if (ShouldUseWorldSpaceUiCapture(request, target, out EditorControlResponse uiUnsupported))
            {
                if (uiUnsupported != null) return uiUnsupported;
                string uiAngle = string.IsNullOrEmpty(angle)
                    ? "front"
                    : angle;
                return HandleWorldSpaceUiCaptureScreenshot(
                    request, outputPath, sceneView, cam, target, uiAngle);
            }

            angle = string.IsNullOrEmpty(angle)
                ? "three_quarter"
                : angle;
            bool knownPreset = false;
            foreach (var preset in ObjectCaptureFramingMath.PresetNames)
            {
                if (string.Equals(preset, angle, StringComparison.Ordinal))
                {
                    knownPreset = true;
                    break;
                }
            }
            if (!knownPreset)
            {
                return BuildError(
                    "EDITOR_CTRL_SCREENSHOT_ANGLE_INVALID",
                    $"angle='{request.angle}' is not one of the accepted preset names "
                    + $"({string.Join(", ", ObjectCaptureFramingMath.PresetNames)}).");
            }

            if (!TryResolveRendererFramingBounds(
                target,
                request.bounds_policy,
                out Bounds aggregated,
                out IList<ObjectCaptureFramingMath.RendererBoundsRecord> includedRecords,
                out IList<ObjectCaptureFramingMath.RendererBoundsRecord> excludedRecords))
            {
                return BuildError(
                    "EDITOR_CTRL_SCREENSHOT_TARGET_NO_RENDERERS",
                    $"target='{request.target}' resolved to a subtree with "
                    + "no active enabled Renderer contributors.");
            }

            Vector3 c = aggregated.center;
            Vector3 e = aggregated.extents;
            float[] corners = BoundsCorners(aggregated);
            Quaternion targetRot = target.transform.rotation;
            float targetYawDeg = targetRot.eulerAngles.y;
            Quaternion targetYawOnly = Quaternion.Euler(0f, targetYawDeg, 0f);
            bool dirOk = ObjectCaptureFramingMath.TryResolvePresetDirection(
                angle,
                new float[] { targetYawOnly.x, targetYawOnly.y, targetYawOnly.z, targetYawOnly.w },
                out float[] cameraDir,
                out string dirReason);
            if (!dirOk)
            {
                return BuildError(
                    "EDITOR_CTRL_SCREENSHOT_ANGLE_INVALID",
                    $"angle='{request.angle}' rejected by framing math: {dirReason}.");
            }

            Vector3 cameraDirV = new Vector3(cameraDir[0], cameraDir[1], cameraDir[2]).normalized;
            Vector3 cameraForward = -cameraDirV;
            Quaternion lookRot = Quaternion.LookRotation(cameraForward, Vector3.up);
            Vector3 cameraRight = lookRot * Vector3.right;
            Vector3 cameraUp = lookRot * Vector3.up;
            float fov = cam.fieldOfView;
            float bothAxesAspect = 0f;
            if (request.fit_mode == "both_axes"
                && request.width <= 0
                && request.height <= 0)
            {
                bool aspectOk = ObjectCaptureFramingMath.TryResolveBothAxesAspectForAabb(
                    corners,
                    new float[] { cameraRight.x, cameraRight.y, cameraRight.z },
                    new float[] { cameraUp.x, cameraUp.y, cameraUp.z },
                    new float[] { cameraDirV.x, cameraDirV.y, cameraDirV.z },
                    fov,
                    ObjectCaptureFramingMath.DefaultFramingMargin,
                    out bothAxesAspect,
                    out string aspectReason);
                if (!aspectOk)
                {
                    return BuildError(
                        "EDITOR_CTRL_SCREENSHOT_FAILED",
                        $"Framing aspect solver failed: {aspectReason}.");
                }
            }

            int defaultWidth = (int)sceneView.position.width;
            int defaultHeight = (int)sceneView.position.height;
            bool sizeOk = ObjectCaptureFramingMath.ResolveOutputSizeForFitMode(
                request.fit_mode,
                request.width,
                request.height,
                defaultWidth,
                defaultHeight,
                bothAxesAspect,
                out int w,
                out int h,
                out float aspect,
                out string sizeReason);
            if (!sizeOk)
            {
                return BuildError(
                    "EDITOR_CTRL_SCREENSHOT_FAILED",
                    $"Screenshot fit sizing failed: {sizeReason}.");
            }

            bool framingOk = ObjectCaptureFramingMath.TrySolveFramingForAabb(
                corners,
                new float[] { cameraRight.x, cameraRight.y, cameraRight.z },
                new float[] { cameraUp.x, cameraUp.y, cameraUp.z },
                new float[] { cameraDirV.x, cameraDirV.y, cameraDirV.z },
                fov,
                aspect,
                ObjectCaptureFramingMath.DefaultFramingMargin,
                ObjectCaptureFramingMath.RecenteringIterationCount,
                out float[] pivot,
                out float size,
                out string framingReason);
            if (!framingOk)
            {
                return BuildError(
                    "EDITOR_CTRL_SCREENSHOT_FAILED",
                    $"Framing solver failed: {framingReason}.");
            }

            CameraSnapshot previous = CaptureCameraState(sceneView);
            try
            {
                Vector3 newPivotWorld = new Vector3(pivot[0], pivot[1], pivot[2]);
                sceneView.LookAt(newPivotWorld, lookRot, size, ortho: false, instant: true);
                float halfFovRad = cam.fieldOfView * 0.5f * Mathf.Deg2Rad;
                float cameraDistance = size / Mathf.Sin(halfFovRad);
                cam.transform.position = newPivotWorld + cameraDirV * cameraDistance;
                cam.transform.rotation = lookRot;
                cam.orthographic = false;
                ForceRenderAndRepaint(sceneView);

                int readX = 0, readY = 0, readW = w, readH = h;
                CropBoundsEntry pixelRectApplied = null;
                EditorControlResponse cropError = ResolveTargetPixelCrop(
                    request, w, h, out readX, out readY, out readW, out readH,
                    out pixelRectApplied);
                if (cropError != null) return cropError;

                RenderTexture rt = null;
                Texture2D tex = null;
                try
                {
                    rt = RenderSceneViewToTexture(cam, w, h);
                    RenderTexture.active = rt;
                    tex = new Texture2D(readW, readH, TextureFormat.RGB24, false);
                    tex.ReadPixels(new Rect(readX, readY, readW, readH), 0, 0);
                    tex.Apply();
                    RenderTexture.active = null;
                    File.WriteAllBytes(outputPath, tex.EncodeToPNG());

                    return BuildSuccess(
                        "EDITOR_CTRL_SCREENSHOT_OK",
                        $"Object-capture screenshot of '{request.target}' "
                        + $"(angle={angle}) captured to {outputPath}",
                        data: new EditorControlData
                        {
                            output_path = outputPath,
                            view = "scene",
                            width = readW,
                            height = readH,
                            executed = true,
                            bounds_source = "renderer",
                            bounds_policy = request.bounds_policy,
                            target_mode = request.target_mode,
                            projection = request.projection,
                            bounds_center = new float[] { c.x, c.y, c.z },
                            bounds_extents = new float[] { e.x, e.y, e.z },
                            contributor_count = includedRecords.Count,
                            excluded_count = excludedRecords.Count,
                            bounds_contributors = ToContributorEntries(includedRecords),
                            excluded_renderers = ToContributorEntries(excludedRecords),
                            crop_roi_applied = pixelRectApplied != null ? "pixel_rect" : string.Empty,
                            crop_bounds = pixelRectApplied,
                        });
                }
                finally
                {
                    if (tex != null) UnityEngine.Object.DestroyImmediate(tex);
                    if (rt != null) UnityEngine.Object.DestroyImmediate(rt);
                    RenderTexture.active = null;
                }
            }
            catch (Exception ex)
            {
                return BuildError(
                    "EDITOR_CTRL_SCREENSHOT_FAILED",
                    $"Object-capture screenshot failed: {ex.Message}");
            }
            finally
            {
                RestoreSceneViewCameraState(previous);
            }
        }

        private static EditorControlResponse ValidateTargetScreenshotSelectors(
            EditorControlRequest request)
        {
            if (request.target_mode != "auto"
                && request.target_mode != "object"
                && request.target_mode != "renderer"
                && request.target_mode != "world_space_ui")
            {
                return BuildError(
                    "SCREENSHOT_TARGET_MODE_INVALID",
                    $"target_mode='{request.target_mode}' is not one of auto, object, renderer, world_space_ui.");
            }
            if (request.projection != "auto"
                && request.projection != "perspective"
                && request.projection != "orthographic")
            {
                return BuildError(
                    "SCREENSHOT_PROJECTION_INVALID",
                    $"projection='{request.projection}' is not one of auto, perspective, orthographic.");
            }
            if (request.padding_ratio < 0f || request.padding_ratio > 1f)
            {
                return BuildError(
                    "SCREENSHOT_PADDING_RATIO_INVALID",
                    $"padding_ratio={request.padding_ratio} must be between 0.0 and 1.0 inclusive.");
            }
            return null;
        }

        private static float[] BoundsCorners(Bounds bounds)
        {
            Vector3 c = bounds.center;
            Vector3 e = bounds.extents;
            float[] corners = new float[24];
            int idx = 0;
            for (int sx = -1; sx <= 1; sx += 2)
            for (int sy = -1; sy <= 1; sy += 2)
            for (int sz = -1; sz <= 1; sz += 2)
            {
                corners[idx++] = c.x + sx * e.x;
                corners[idx++] = c.y + sy * e.y;
                corners[idx++] = c.z + sz * e.z;
            }
            return corners;
        }

        private static void RestoreSceneViewCameraState(CameraSnapshot previous)
        {
            if (SceneView.lastActiveSceneView == null) return;

            var sv = SceneView.lastActiveSceneView;
            var prevPivot = new Vector3(
                previous.pivot[0], previous.pivot[1], previous.pivot[2]);
            var prevRot = new Quaternion(
                previous.rotation_quat[0], previous.rotation_quat[1],
                previous.rotation_quat[2], previous.rotation_quat[3]);
            sv.LookAt(prevPivot, prevRot, previous.size, previous.orthographic, instant: true);
        }
    }
}
