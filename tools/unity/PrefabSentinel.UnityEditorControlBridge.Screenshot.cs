using System;
using System.Collections.Generic;
using System.IO;
using UnityEditor;
using UnityEngine;
using UnityEngine.UI;

namespace PrefabSentinel
{
    /// <summary>
    /// Screenshot partial (issues #249, #242) — owns the
    /// ``capture_screenshot`` handler with region preset / pixel
    /// rectangle resolution, and the ``force_scene_view_refresh``
    /// primitive that flips ``forceMatrixRecalculationPerRender`` on
    /// every active ``SkinnedMeshRenderer`` and drives the editor
    /// player-loop tick in one round-trip.
    ///
    /// The four named region presets share one allowlist with the
    /// caller-side wrapper so an unrecognised preset short-circuits at
    /// the wrapper without ever reaching the bridge.
    /// </summary>
    public static partial class UnityEditorControlBridge
    {
        // Issue #249: canonical preset name allowlist; mirrors the
        // wrapper-side allowlist in ``mcp_tools_editor_view.py``.
        internal static readonly string[] SupportedScreenshotPresets =
            { "eye_left", "eye_right", "mouth", "auto_face" };

        // Issue #259: bridge-side view-selector allowlist. The view
        // selector is interpolated into the output filename below, so
        // an unvalidated value would let a caller compose path
        // separators or traversal sequences into the screenshots
        // directory write.  The set is identical to the wrapper-side
        // ``SCREENSHOT_VIEW_ALLOWLIST`` (the two lower-case ASCII
        // selectors ``"scene"`` and ``"game"``) so the two layers
        // cannot drift.  Comparison is exact case-sensitive equality.
        internal static readonly string[] SupportedScreenshotViews =
            { "scene", "game" };

        /// <summary>
        /// Resolve a screenshot region argument to a ``(presetLabel,
        /// rect)`` pair. Returns ``(null, null)`` when the argument is
        /// empty (caller asked for a full frame); returns the literal
        /// ``pixel_rect`` together with the integer rectangle when the
        /// argument is a comma-separated quadruple of non-negative
        /// integers; returns the preset name and a placeholder rect
        /// when the argument matches an allowlisted preset.  An
        /// unrecognised value yields a ``EDITOR_CTRL_CROP_ROI_INVALID``
        /// error envelope via the caller.
        /// </summary>
        internal static bool TryResolveCropRoi(string value, out string label, out CropBoundsEntry bounds)
        {
            label = string.Empty;
            bounds = null;
            if (value == null) return false;
            if (value.Length == 0) return true;
            foreach (var preset in SupportedScreenshotPresets)
            {
                if (preset == value)
                {
                    label = preset;
                    bounds = new CropBoundsEntry();
                    return true;
                }
            }
            // Pixel quadruple "x,y,w,h" — every part must parse to a
            // non-negative integer.  Anything else is rejected.
            var parts = value.Split(',');
            if (parts.Length != 4) return false;
            int[] vals = new int[4];
            for (int i = 0; i < 4; i++)
            {
                if (!int.TryParse(parts[i].Trim(), out vals[i])) return false;
                if (vals[i] < 0) return false;
            }
            label = "pixel_rect";
            bounds = new CropBoundsEntry { x = vals[0], y = vals[1], w = vals[2], h = vals[3] };
            return true;
        }

        /// <summary>
        /// Resolve a preset label to a world-space framing target.
        /// Returns the target Transform when a likely match is found in
        /// the active scene or Prefab Stage by transform-name fuzzy
        /// match; returns ``null`` when no plausible target exists so
        /// the handler can surface ``EDITOR_CTRL_CROP_ROI_NO_TARGET``.
        /// </summary>
        private static Transform ResolvePresetTarget(string preset)
        {
            // Issue #249: framing targets are derived by name fuzzy-
            // match. Match patterns are intentionally permissive
            // (substring, case-insensitive) so the four named presets
            // light up across the avatar naming conventions seen in
            // the project's typical content (LeftEye / Eye_L / etc.).
            string[] candidates;
            switch (preset)
            {
                case "eye_left":
                    candidates = new[] { "lefteye", "eye_l", "eyel", "l_eye", "eye.l" };
                    break;
                case "eye_right":
                    candidates = new[] { "righteye", "eye_r", "eyer", "r_eye", "eye.r" };
                    break;
                case "mouth":
                    candidates = new[] { "mouth", "jaw", "lip" };
                    break;
                case "auto_face":
                    candidates = new[] { "head", "face" };
                    break;
                default:
                    return null;
            }
            foreach (var t in UnityEngine.Object.FindObjectsOfType<Transform>())
            {
                string lower = t.name.ToLowerInvariant();
                foreach (var pat in candidates)
                {
                    if (lower.Contains(pat)) return t;
                }
            }
            return null;
        }

        /// <summary>
        /// Capture a screenshot of the Unity Editor (issues #249, #84).
        ///
        /// When ``crop_roi`` is non-empty the handler resolves it to a
        /// preset label or a pixel rectangle and surfaces the resolution
        /// on the response as ``crop_roi_applied`` + ``crop_bounds``.
        /// The preset branch wraps the framing operation in
        /// ``CaptureCameraState`` + ``SceneView.LookAt(..., instant:true)``
        /// so the editor's scene view pivot returns to its pre-call
        /// value before the response is built. The pixel-rect branch
        /// crops the encoded PNG to the supplied rectangle.
        ///
        /// Issue #84: when ``request.target`` is non-empty the handler
        /// re-frames the SceneView onto the named GameObject's
        /// world-space AABB at the requested angle preset via
        /// ``ObjectCaptureFramingMath`` and ``SceneView.LookAt``, then
        /// restores the previous SceneView camera state before the
        /// response is built. The response carries the framing
        /// AABB on the existing ``bounds_center`` / ``bounds_extents``
        /// fields (additive: these fields are unset on the existing
        /// capture paths).
        /// </summary>
        private static EditorControlResponse HandleCaptureScreenshot(EditorControlRequest request, string requestPath)
        {
            // Issue #259 / #222 Phase 3: defense-in-depth view-selector
            // allowlist.  The decision is delegated to the pure
            // ``ScreenshotViewAllowlistClassifier`` so the bridge gate
            // can be exercised by the Unity-free C# xUnit harness; the
            // gate fires BEFORE any output-path composition or
            // filesystem activity so a rejected request never writes a
            // file or creates a screenshots directory.
            if (!ScreenshotViewAllowlistClassifier.IsAccepted(request.view, SupportedScreenshotViews))
            {
                return BuildError(
                    "EDITOR_CTRL_SCREENSHOT_VIEW_INVALID",
                    $"view='{request.view}' is not one of the accepted selectors " +
                    "(scene, game). The view selector is interpolated into the " +
                    "output filename so non-allowlisted inputs are rejected " +
                    "before any filesystem activity.");
            }

            if (!ScreenshotDimensionBounds.Accepts(request.width, request.height))
            {
                return BuildError(
                    ScreenshotDimensionBounds.BridgeOutOfRangeCode,
                    ScreenshotDimensionBounds.BuildMessage(request.width, request.height));
            }

            string outputDir = Path.Combine(Path.GetDirectoryName(requestPath), "screenshots");
            if (!Directory.Exists(outputDir))
                Directory.CreateDirectory(outputDir);

            string timestamp = DateTime.Now.ToString("yyyyMMdd_HHmmss");
            string outputPath = Path.Combine(outputDir, $"{request.view}_{timestamp}.png");

            // Issue #84: target-oriented capture mode is dispatched
            // here, BEFORE the crop_roi resolution. The wrapper rejects
            // ``target`` + face-feature crop_roi, so on the bridge side
            // a non-empty ``target`` only ever arrives with empty
            // crop_roi or a pixel rectangle (the latter is orthogonal
            // to framing and applied post-render by
            // ``HandleObjectCaptureScreenshot``). The branch owns its
            // own render and camera-state restore; it never falls
            // through to the existing scene/game switch below.
            if (!string.IsNullOrEmpty(request.target))
            {
                return HandleObjectCaptureScreenshot(request, outputPath);
            }

            // Issue #249: resolve the optional region argument before
            // engaging the renderer.  An unrecognised value short-circuits
            // here so callers see a typed error rather than a silent
            // full-frame fallback.
            string cropLabel;
            CropBoundsEntry cropBounds;
            if (!TryResolveCropRoi(request.crop_roi, out cropLabel, out cropBounds))
            {
                return BuildError(
                    "EDITOR_CTRL_CROP_ROI_INVALID",
                    $"crop_roi='{request.crop_roi}' is neither one of the four named presets " +
                    "(eye_left | eye_right | mouth | auto_face) nor a comma-separated " +
                    "quadruple of non-negative integers 'x,y,w,h'.");
            }
            // Issue #249: save the scene-view camera state via
            // CaptureCameraState before any preset-driven framing; the
            // restore via SceneView.LookAt at the bottom of the preset
            // branch returns the pivot to its pre-call value.
            CameraSnapshot? previousCameraState = null;
            bool isPresetPath = !string.IsNullOrEmpty(cropLabel) && cropLabel != "pixel_rect";
            if (isPresetPath)
            {
                var sv = SceneView.lastActiveSceneView;
                if (sv != null) previousCameraState = CaptureCameraState(sv);
            }

            // Issue #310: the scene-vs-game routing decision is owned
            // by the view-kind helper so the scene-selector literal
            // lives in exactly one place (the classifier). A future
            // relaxation of the gate (case fold, whitespace trim)
            // propagates to both the allowlist gate and the routing
            // discriminator by changing the helper alone.
            bool isScene = ScreenshotViewAllowlistClassifier.IsSceneView(request.view);

            try
            {
                if (isScene)
                {
                    SceneView sceneView = SceneView.lastActiveSceneView;
                    if (sceneView == null)
                        return BuildError("EDITOR_CTRL_NO_SCENE_VIEW", "No active SceneView found.");

                    int w = request.width > 0 ? request.width : (int)sceneView.position.width;
                    int h = request.height > 0 ? request.height : (int)sceneView.position.height;

                    UnityEngine.Camera cam = sceneView.camera;
                    if (cam == null)
                        return BuildError("EDITOR_CTRL_NO_SCENE_CAMERA", "SceneView camera is null.");

                    // Issue #249: pixel-rect path validates the
                    // supplied rectangle against the rendered frame
                    // before we engage the renderer so an out-of-frame
                    // request produces a typed envelope rather than a
                    // silently empty PNG.
                    if (cropLabel == "pixel_rect")
                    {
                        if (cropBounds == null
                            || !ScreenshotCropBounds.FitsWithinFrame(
                                cropBounds.x, cropBounds.y,
                                cropBounds.w, cropBounds.h, w, h))
                        {
                            return BuildError(
                                "EDITOR_CTRL_CROP_ROI_OUT_OF_BOUNDS",
                                $"crop_roi pixel rectangle {request.crop_roi} does not fit inside the rendered frame {w}x{h}.");
                        }
                    }

                    // Issue #249: preset path frames the scene-view
                    // camera onto a target derived from the preset
                    // name before rendering. A missing target is a
                    // typed error so callers see why the preset did
                    // nothing rather than a frame at the previous
                    // camera state.
                    if (isPresetPath)
                    {
                        Transform target = ResolvePresetTarget(cropLabel);
                        if (target == null)
                        {
                            // Restore camera state defensively; nothing
                            // was framed but the contract is that the
                            // caller observes no persistent change.
                            return BuildError(
                                "EDITOR_CTRL_CROP_ROI_NO_TARGET",
                                $"crop_roi preset '{cropLabel}' resolved to no framing target in the active scene or Prefab Stage.");
                        }
                        // SceneView.Frame uses world-space bounds and
                        // instant: true so the framing applies before
                        // the next Render() call rather than animating.
                        var renderer = target.GetComponentInChildren<Renderer>();
                        Bounds bounds = renderer != null
                            ? renderer.bounds
                            : new Bounds(target.position, Vector3.one * 0.1f);
                        sceneView.Frame(bounds, instant: true);
                        // For the preset path the cropBounds payload
                        // reports the full rendered frame because the
                        // preset framing already zooms onto the target.
                        cropBounds = new CropBoundsEntry { x = 0, y = 0, w = w, h = h };
                    }

                    RenderTexture rt = null;
                    Texture2D tex = null;
                    try
                    {
                        rt = RenderSceneViewToTexture(cam, w, h);
                        RenderTexture.active = rt;
                        if (cropLabel == "pixel_rect" && cropBounds != null)
                        {
                            // Issue #249: pixel-rect path reads only
                            // the supplied sub-rectangle so the saved
                            // PNG actually carries the crop the caller
                            // asked for rather than a full frame the
                            // caller would have to re-crop downstream.
                            tex = new Texture2D(cropBounds.w, cropBounds.h, TextureFormat.RGB24, false);
                            tex.ReadPixels(
                                new Rect(cropBounds.x, cropBounds.y, cropBounds.w, cropBounds.h),
                                0, 0);
                        }
                        else
                        {
                            tex = new Texture2D(w, h, TextureFormat.RGB24, false);
                            tex.ReadPixels(new Rect(0, 0, w, h), 0, 0);
                        }
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

                    // Issue #249: restore via SceneView.LookAt so the
                    // editor's pivot returns to its pre-call value
                    // before control returns to the caller.
                    if (previousCameraState.HasValue && SceneView.lastActiveSceneView != null)
                    {
                        var prev = previousCameraState.Value;
                        var sv = SceneView.lastActiveSceneView;
                        var prevPivot = new Vector3(prev.pivot[0], prev.pivot[1], prev.pivot[2]);
                        var prevRot = new Quaternion(
                            prev.rotation_quat[0], prev.rotation_quat[1],
                            prev.rotation_quat[2], prev.rotation_quat[3]);
                        sv.LookAt(prevPivot, prevRot, prev.size, prev.orthographic, instant: true);
                    }
                    return BuildSuccess("EDITOR_CTRL_SCREENSHOT_OK", $"Scene view captured to {outputPath}",
                        data: new EditorControlData
                        {
                            output_path = outputPath,
                            view = "scene",
                            width = cropLabel == "pixel_rect" && cropBounds != null ? cropBounds.w : w,
                            height = cropLabel == "pixel_rect" && cropBounds != null ? cropBounds.h : h,
                            executed = true,
                            crop_roi_applied = cropLabel,
                            crop_bounds = cropBounds,
                        });
                }
                else
                {
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
                            executed = true,
                        });
                }
            }
            catch (Exception ex)
            {
                return BuildError("EDITOR_CTRL_SCREENSHOT_FAILED", $"Screenshot failed: {ex.Message}");
            }
        }

        /// <summary>
        /// Issue #84 — target-oriented capture branch of
        /// ``HandleCaptureScreenshot``.  Resolves ``request.target``
        /// through the existing stage-aware resolver, aggregates
        /// renderer bounds (SkinnedMeshRenderer baked at the current
        /// pose; MeshRenderer corners world-transformed), runs the
        /// Unity-free outlier filter + framing solver, applies the
        /// computed pivot/rotation/size via ``SceneView.LookAt``,
        /// renders + encodes, and restores the previous SceneView
        /// state before returning.  Returns the response envelope
        /// (success or error); the caller dispatches on a non-null
        /// return.
        /// </summary>


        /// <summary>
        /// Render the current scene through ``cam`` into a freshly
        /// allocated ``RenderTexture`` sized ``width × height``. Sets
        /// ``forceMatrixRecalculationPerRender`` on every active
        /// ``SkinnedMeshRenderer`` for the duration of the render so
        /// blendshape-driven meshes evaluate at the current pose
        /// (knowledge/blendshape-capture-pipeline.md; issue #242). The
        /// caller owns the returned ``RenderTexture`` and is responsible
        /// for ``DestroyImmediate``-ing it.
        /// </summary>
        private static RenderTexture RenderSceneViewToTexture(UnityEngine.Camera cam, int width, int height)
        {
            var smrs = UnityEngine.Object.FindObjectsOfType<SkinnedMeshRenderer>();
            foreach (var smr in smrs)
                smr.forceMatrixRecalculationPerRender = true;

            var rt = new RenderTexture(width, height, 24);
            RenderTexture prev = cam.targetTexture;
            cam.targetTexture = rt;
            cam.Render();
            cam.targetTexture = prev;

            foreach (var smr in smrs)
                smr.forceMatrixRecalculationPerRender = false;
            return rt;
        }

        /// <summary>
        /// Apply ``forceMatrixRecalculationPerRender`` to every active
        /// ``SkinnedMeshRenderer`` and drive a player-loop tick so a
        /// caller running camera-render passes outside the screenshot
        /// path can opt into the same workaround without per-render
        /// bridge round-trips.  Reports the integer count of renderers
        /// whose flag was set.
        /// </summary>
        private static EditorControlResponse HandleForceSceneViewRefresh()
        {
            try
            {
                var smrs = UnityEngine.Object.FindObjectsOfType<SkinnedMeshRenderer>();
                foreach (var smr in smrs)
                    smr.forceMatrixRecalculationPerRender = true;
                EditorApplication.QueuePlayerLoopUpdate();
                return BuildSuccess(
                    "EDITOR_CTRL_FORCE_REFRESH_OK",
                    $"Force-refresh applied to {smrs.Length} renderer(s).",
                    data: new EditorControlData
                    {
                        executed = true,
                        renderers_touched = smrs.Length,
                    });
            }
            catch (Exception ex)
            {
                return BuildError(
                    "EDITOR_CTRL_FORCE_REFRESH_FAILED",
                    $"Force scene-view refresh failed: {ex.Message}");
            }
        }
    }
}
