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

                    Camera cam = sceneView.camera;
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
        private static EditorControlResponse HandleObjectCaptureScreenshot(
            EditorControlRequest request, string outputPath)
        {
            string angle = string.IsNullOrEmpty(request.angle)
                ? "three_quarter"
                : request.angle;

            SceneView sceneView = SceneView.lastActiveSceneView;
            if (sceneView == null)
                return BuildError("EDITOR_CTRL_NO_SCENE_VIEW", "No active SceneView found.");
            Camera cam = sceneView.camera;
            if (cam == null)
                return BuildError("EDITOR_CTRL_NO_SCENE_CAMERA", "SceneView camera is null.");

            // Resolve the target through the stage-aware resolver so
            // the existing EDITOR_CTRL_HIERARCHY_PATH_AMBIGUOUS envelope
            // surfaces unchanged on ambiguous hierarchy paths.
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
            if (ShouldUseWorldSpaceUiCapture(request, target, out EditorControlResponse uiUnsupported))
            {
                if (uiUnsupported != null) return uiUnsupported;
                return HandleWorldSpaceUiCaptureScreenshot(
                    request, outputPath, sceneView, cam, target, angle);
            }

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

            // Aggregate active renderer bounds on the resolved subtree.
            // SkinnedMeshRenderer.BakeMesh gives a tight pose-current
            // AABB; Renderer.bounds is conservative (culling-oriented)
            // but adequate for non-skinned meshes.
            var records = new List<ObjectCaptureFramingMath.RendererBoundsRecord>();
            foreach (var smr in target.GetComponentsInChildren<SkinnedMeshRenderer>(includeInactive: false))
            {
                var baked = new Mesh();
                try
                {
                    smr.BakeMesh(baked);
                    Bounds local = baked.bounds;
                    Bounds world = TransformBoundsToWorld(local, smr.transform);
                    records.Add(new ObjectCaptureFramingMath.RendererBoundsRecord(
                        new float[] { world.center.x, world.center.y, world.center.z },
                        new float[] { world.extents.x, world.extents.y, world.extents.z }));
                }
                finally
                {
                    UnityEngine.Object.DestroyImmediate(baked);
                }
            }
            foreach (var r in target.GetComponentsInChildren<Renderer>(includeInactive: false))
            {
                if (r is SkinnedMeshRenderer) continue; // already baked
                if (!r.enabled) continue;
                Bounds world = r.bounds;
                records.Add(new ObjectCaptureFramingMath.RendererBoundsRecord(
                    new float[] { world.center.x, world.center.y, world.center.z },
                    new float[] { world.extents.x, world.extents.y, world.extents.z }));
            }
            if (records.Count == 0)
            {
                return BuildError(
                    "EDITOR_CTRL_SCREENSHOT_TARGET_NO_RENDERERS",
                    $"target='{request.target}' resolved to a subtree with "
                    + "no active SkinnedMeshRenderer or MeshRenderer.");
            }

            var kept = ObjectCaptureFramingMath.SelectFramingRenderers(records);
            Bounds aggregated = AggregateRendererBounds(kept);
            Vector3 c = aggregated.center;
            Vector3 e = aggregated.extents;
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

            // Resolve the preset to a world-space camera-position
            // direction relative to the target's yaw (Y-axis) rotation
            // only. Pitch / roll from ``target.transform.rotation`` are
            // discarded because sub-tree targets (face meshes, parts,
            // accessories) frequently inherit non-identity X / Z
            // rotations from FBX / PMX axis-import conventions — e.g.,
            // MMD face meshes are imported with X=-90 so the local
            // ``forward`` points along world +Y. Applying the preset
            // relative to that local frame produces nonsensical views
            // (front becomes top-down). Caller intent is virtually
            // always "frame this object from world-horizontal at its
            // facing direction", so we collapse the rotation to its
            // yaw component before composing the preset direction.
            // Avatar roots typically have identity or pure-yaw
            // rotations, so this is a no-op there.
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

            // Compose the orthonormal camera basis. The SceneView
            // looks at the pivot; the camera position is on the
            // ``+cameraDir`` side of the pivot, so the camera forward
            // is ``-cameraDir``. Right and up are derived as Unity's
            // ``Quaternion.LookRotation`` would (Y-up, with the world
            // ``Vector3.up`` as the reference up).
            Vector3 cameraDirV = new Vector3(cameraDir[0], cameraDir[1], cameraDir[2]).normalized;
            Vector3 cameraForward = -cameraDirV;
            Quaternion lookRot = Quaternion.LookRotation(cameraForward, Vector3.up);
            Vector3 cameraRight = lookRot * Vector3.right;
            Vector3 cameraUp = lookRot * Vector3.up;

            float fov = cam.fieldOfView;
            float aspect = (request.width > 0 && request.height > 0)
                ? ((float)request.width / (float)request.height)
                : cam.aspect;
            bool framingOk = ObjectCaptureFramingMath.TrySolveFramingForAabb(
                corners,
                new float[] { cameraRight.x, cameraRight.y, cameraRight.z },
                new float[] { cameraUp.x, cameraUp.y, cameraUp.z },
                new float[] { cameraDirV.x, cameraDirV.y, cameraDirV.z },
                fov, aspect, ObjectCaptureFramingMath.DefaultFramingMargin,
                ObjectCaptureFramingMath.RecenteringIterationCount,
                out float[] pivot, out float size, out string framingReason);
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
                sceneView.LookAt(
                    newPivotWorld,
                    lookRot, size, ortho: false, instant: true);

                // SceneView.LookAt(instant:true) sets m_Position /
                // m_Rotation / m_Size immediately, but the underlying
                // ``sceneView.camera.transform`` is only re-synced when
                // the next ``SceneView.OnGUI`` runs ``SetupCamera()``.
                // Because this handler then calls ``cam.Render()`` in
                // the same dispatch tick, without an explicit sync the
                // render would use the pre-LookAt transform and every
                // angle preset would produce an identical image. Mirror
                // the SceneView perspective-distance derivation
                // (issue #66: distance = size / sin(fov/2)) and apply
                // pivot / rotation directly so the in-call render
                // reflects the requested framing.
                float halfFovRad = cam.fieldOfView * 0.5f * Mathf.Deg2Rad;
                float cameraDistance = size / Mathf.Sin(halfFovRad);
                cam.transform.position = newPivotWorld + cameraDirV * cameraDistance;
                cam.transform.rotation = lookRot;
                cam.orthographic = false;

                ForceRenderAndRepaint(sceneView);

                int w = request.width > 0 ? request.width : (int)sceneView.position.width;
                int h = request.height > 0 ? request.height : (int)sceneView.position.height;

                RenderTexture rt = null;
                Texture2D tex = null;
                try
                {
                    int readX = 0, readY = 0, readW = w, readH = h;
                    CropBoundsEntry pixelRectApplied = null;
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

                    byte[] png = tex.EncodeToPNG();
                    File.WriteAllBytes(outputPath, png);

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
                            bounds_center = new float[] { c.x, c.y, c.z },
                            bounds_extents = new float[] { e.x, e.y, e.z },
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
                // Restore the previous SceneView camera state so the
                // caller observes no persistent framing change. This
                // mirrors the existing crop_roi preset branch.
                if (SceneView.lastActiveSceneView != null)
                {
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


        private static EditorControlResponse ValidateTargetScreenshotSelectors(
            EditorControlRequest request)
        {
            if (request.target_mode != "auto"
                && request.target_mode != "renderer"
                && request.target_mode != "world_space_ui")
            {
                return BuildError(
                    "SCREENSHOT_TARGET_MODE_INVALID",
                    $"target_mode='{request.target_mode}' is not one of auto, renderer, world_space_ui.");
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
            Camera cam,
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
            float aspect = request.width > 0 && request.height > 0
                ? (float)request.width / (float)request.height
                : cam.aspect;
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

                int w = request.width > 0 ? request.width : (int)sceneView.position.width;
                int h = request.height > 0 ? request.height : (int)sceneView.position.height;
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
                if (SceneView.lastActiveSceneView != null)
                {
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

        private static Bounds TransformBoundsToWorld(Bounds local, Transform t)
        {
            Vector3 c = local.center;
            Vector3 e = local.extents;
            Vector3[] corners = new Vector3[8];
            int idx = 0;
            for (int sx = -1; sx <= 1; sx += 2)
            for (int sy = -1; sy <= 1; sy += 2)
            for (int sz = -1; sz <= 1; sz += 2)
            {
                corners[idx++] = t.TransformPoint(new Vector3(
                    c.x + sx * e.x, c.y + sy * e.y, c.z + sz * e.z));
            }
            Vector3 min = corners[0];
            Vector3 max = corners[0];
            for (int i = 1; i < 8; i++)
            {
                min = Vector3.Min(min, corners[i]);
                max = Vector3.Max(max, corners[i]);
            }
            var world = new Bounds((min + max) * 0.5f, max - min);
            return world;
        }

        private static Bounds AggregateRendererBounds(
            IList<ObjectCaptureFramingMath.RendererBoundsRecord> records)
        {
            var first = records[0];
            Vector3 fc = new Vector3(
                first.CenterWorld[0], first.CenterWorld[1], first.CenterWorld[2]);
            Vector3 fe = new Vector3(
                first.ExtentsWorld[0], first.ExtentsWorld[1], first.ExtentsWorld[2]);
            Bounds aggregated = new Bounds(fc, fe * 2f);
            for (int i = 1; i < records.Count; i++)
            {
                var r = records[i];
                Vector3 rc = new Vector3(
                    r.CenterWorld[0], r.CenterWorld[1], r.CenterWorld[2]);
                Vector3 re = new Vector3(
                    r.ExtentsWorld[0], r.ExtentsWorld[1], r.ExtentsWorld[2]);
                aggregated.Encapsulate(new Bounds(rc, re * 2f));
            }
            return aggregated;
        }

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
        private static RenderTexture RenderSceneViewToTexture(Camera cam, int width, int height)
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
