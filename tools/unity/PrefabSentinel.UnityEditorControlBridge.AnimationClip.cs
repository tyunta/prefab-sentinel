using System;
using System.Collections.Generic;
using System.IO;
using UnityEditor;
using UnityEngine;

namespace PrefabSentinel
{
    /// <summary>
    /// AnimationClip partial (issue #243) — owns the three
    /// AnimationClip-shaped handlers: inspect a clip's curves and
    /// timing, create a clip from a curve specification, and preview-
    /// apply an existing clip against a live hierarchy target under
    /// Unity's animation-mode preview API so the resulting state is
    /// recorded as a single undo group.
    /// </summary>
    public static partial class UnityEditorControlBridge
    {
        [Serializable]
        private sealed class CreateAnimationClipCurveSpec
        {
            public string relative_path = string.Empty;
            public string type = string.Empty;
            public string property = string.Empty;
            // Scalar (single keyframe) or list (multi-keyframe) value;
            // the bridge handler reads it through ``values`` after the
            // JSON pre-parse step.
            public float[] values = Array.Empty<float>();
        }

        [Serializable]
        private sealed class CreateAnimationClipCurveSpecArray
        {
            public CreateAnimationClipCurveSpec[] items =
                Array.Empty<CreateAnimationClipCurveSpec>();
        }

        private static EditorControlResponse HandleInspectAnimationClip(EditorControlRequest request)
        {
            if (string.IsNullOrEmpty(request.asset_path))
                return BuildError(
                    "EDITOR_CTRL_ANIMATION_CLIP_NOT_FOUND",
                    "inspect_animation_clip requires a non-empty asset_path.");
            var clip = AssetDatabase.LoadAssetAtPath<AnimationClip>(request.asset_path);
            if (clip == null)
                return BuildError(
                    "EDITOR_CTRL_ANIMATION_CLIP_NOT_FOUND",
                    $"AnimationClip not found at {request.asset_path}.");
            var entries = new List<AnimationCurveEntry>();
            var bindings = AnimationUtility.GetCurveBindings(clip);
            foreach (var binding in bindings)
            {
                var curve = AnimationUtility.GetEditorCurve(clip, binding);
                if (curve == null) continue;
                float[] values = new float[curve.length];
                for (int i = 0; i < curve.length; i++)
                    values[i] = curve.keys[i].value;
                entries.Add(new AnimationCurveEntry
                {
                    relative_path = binding.path,
                    type = binding.type != null ? binding.type.FullName : string.Empty,
                    property = binding.propertyName,
                    values = values,
                });
            }
            return BuildSuccess(
                "EDITOR_CTRL_ANIMATION_CLIP_INSPECT_OK",
                $"Inspected {entries.Count} curve(s).",
                data: new EditorControlData
                {
                    executed = true,
                    curves = entries.ToArray(),
                    length = clip.length,
                    frame_rate = clip.frameRate,
                });
        }

        private static EditorControlResponse HandleCreateAnimationClip(EditorControlRequest request)
        {
            // Issue #53: the caller supplies a single full asset path;
            // the bridge derives the destination directory and clip
            // filename from it.
            if (string.IsNullOrEmpty(request.asset_path))
                return BuildError(
                    "EDITOR_CTRL_ANIMATION_CLIP_WRITE_FAILED",
                    "create_animation_clip requires asset_path.");
            string assetPath = request.asset_path.Replace('\\', '/');
            if (!assetPath.StartsWith("Assets/", StringComparison.Ordinal))
                return BuildError(
                    "EDITOR_CTRL_ANIMATION_CLIP_WRITE_FAILED",
                    $"asset_path must be under Assets/: {request.asset_path}");
            if (!assetPath.EndsWith(".anim", StringComparison.OrdinalIgnoreCase))
                return BuildError(
                    "EDITOR_CTRL_ANIMATION_CLIP_WRITE_FAILED",
                    $"asset_path must end with the .anim extension: {request.asset_path}");
            // Issue #243 / security: defence-in-depth path traversal gate.
            // ``StartsWith("Assets/")`` permits ``Assets/../etc`` which
            // resolves outside the project assets root. Reject any value
            // carrying traversal segments or backslash separators before
            // we touch the asset database.
            if (HasUnsafePathSegment(request.asset_path))
                return BuildError(
                    "EDITOR_CTRL_ANIMATION_CLIP_WRITE_FAILED",
                    "create_animation_clip rejects '..' segments or backslash "
                    + "separators in asset_path.");
            string projectRoot = Directory.GetCurrentDirectory();
            string assetsRootAbs = Path.GetFullPath(Path.Combine(projectRoot, "Assets"));
            string targetDir = Path.GetDirectoryName(assetPath) ?? string.Empty;
            string targetDirAbs = Path.GetFullPath(Path.Combine(projectRoot, targetDir));
            if (!IsPathUnder(assetsRootAbs, targetDirAbs))
                return BuildError(
                    "EDITOR_CTRL_ANIMATION_CLIP_WRITE_FAILED",
                    $"asset_path canonical directory escapes Assets/: {request.asset_path}");
            string path = assetPath;
            try
            {
                var clip = new AnimationClip();
                int writtenCurveCount = 0;
                if (!string.IsNullOrEmpty(request.curves_json))
                {
                    var wrapped = "{\"items\":" + request.curves_json + "}";
                    var arr = JsonUtility.FromJson<CreateAnimationClipCurveSpecArray>(wrapped);
                    if (arr != null && arr.items != null)
                    {
                        // Issue #243: attach each parsed curve via
                        // AnimationUtility.SetEditorCurve before the
                        // asset is committed. Single-keyframe values
                        // (``values.Length == 1``) anchor a constant
                        // curve at t=0; multi-keyframe lists distribute
                        // keyframes at one-per-frame at the clip's
                        // default frame rate so the round-trip preserves
                        // sample order.
                        float frameRate = clip.frameRate > 0f ? clip.frameRate : 60f;
                        foreach (var spec in arr.items)
                        {
                            if (spec == null) continue;
                            if (string.IsNullOrEmpty(spec.property)) continue;
                            if (spec.values == null || spec.values.Length == 0) continue;
                            Type compType = ResolveComponentType(spec.type);
                            if (compType == null) continue;
                            var binding = new EditorCurveBinding
                            {
                                path = spec.relative_path ?? string.Empty,
                                type = compType,
                                propertyName = spec.property,
                            };
                            var keys = new Keyframe[spec.values.Length];
                            if (spec.values.Length == 1)
                            {
                                keys[0] = new Keyframe(0f, spec.values[0]);
                            }
                            else
                            {
                                for (int i = 0; i < spec.values.Length; i++)
                                    keys[i] = new Keyframe(i / frameRate, spec.values[i]);
                            }
                            var curve = new AnimationCurve(keys);
                            AnimationUtility.SetEditorCurve(clip, binding, curve);
                            writtenCurveCount++;
                        }
                    }
                }
                AssetDatabase.CreateAsset(clip, path);
                AssetDatabase.SaveAssets();
                return BuildSuccess(
                    "EDITOR_CTRL_ANIMATION_CLIP_CREATE_OK",
                    $"Created AnimationClip at {path} with {writtenCurveCount} curve(s).",
                    data: new EditorControlData
                    {
                        executed = true,
                        asset_path = path,
                        curve_count = writtenCurveCount,
                    });
            }
            catch (Exception ex)
            {
                return BuildError(
                    "EDITOR_CTRL_ANIMATION_CLIP_WRITE_FAILED",
                    $"AnimationClip write failed: {ex.Message}");
            }
        }

        /// <summary>
        /// Reject any value carrying ``..`` traversal segments, embedded
        /// NULs, or backslash separators. Used as the pre-canonicalisation
        /// fence on the AnimationClip writer; the canonical
        /// ``Path.GetFullPath`` check then verifies that the resolved
        /// directory still lives under the project assets root.
        /// </summary>
        private static bool HasUnsafePathSegment(string value)
        {
            if (string.IsNullOrEmpty(value)) return false;
            if (value.IndexOf('\0') >= 0) return true;
            if (value.IndexOf('\\') >= 0) return true;
            foreach (var part in value.Split('/'))
            {
                if (part == "..") return true;
            }
            return false;
        }

        /// <summary>
        /// Returns ``true`` when ``candidate`` (assumed canonical) lives
        /// under ``root`` (also canonical). Uses a normalised separator
        /// suffix to avoid matching ``Assets`` against ``AssetsExternal``.
        /// </summary>
        private static bool IsPathUnder(string root, string candidate)
        {
            if (string.IsNullOrEmpty(root) || string.IsNullOrEmpty(candidate)) return false;
            string normRoot = root.TrimEnd(Path.DirectorySeparatorChar, '/') + Path.DirectorySeparatorChar;
            string normCand = candidate.TrimEnd(Path.DirectorySeparatorChar, '/') + Path.DirectorySeparatorChar;
            return normCand.StartsWith(normRoot, StringComparison.Ordinal);
        }

        private static EditorControlResponse HandleApplyAnimationClip(EditorControlRequest request)
        {
            if (string.IsNullOrEmpty(request.asset_path))
                return BuildError(
                    "EDITOR_CTRL_ANIMATION_CLIP_NOT_FOUND",
                    "apply_animation_clip requires asset_path.");
            var clip = AssetDatabase.LoadAssetAtPath<AnimationClip>(request.asset_path);
            if (clip == null)
                return BuildError(
                    "EDITOR_CTRL_ANIMATION_CLIP_NOT_FOUND",
                    $"AnimationClip not found at {request.asset_path}.");
            if (!TryResolveGameObjectInActiveStage(
                request.target_hierarchy_path, out GameObject go, out var ambiguity))
            {
                if (ambiguity != null) return ambiguity;
                return BuildError(
                    "EDITOR_CTRL_ANIMATION_CLIP_TARGET_NOT_FOUND",
                    $"Apply target not found: {request.target_hierarchy_path}");
            }
            try
            {
                int undoGroup = Undo.GetCurrentGroup();
                Undo.SetCurrentGroupName(
                    $"PrefabSentinel: apply AnimationClip {request.asset_path}");
                AnimationMode.StartAnimationMode();
                AnimationMode.SampleAnimationClip(go, clip, 0f);
                AnimationMode.StopAnimationMode();
                Undo.CollapseUndoOperations(undoGroup);
                int applied = AnimationUtility.GetCurveBindings(clip).Length;
                return BuildSuccess(
                    "EDITOR_CTRL_ANIMATION_CLIP_APPLY_OK",
                    $"Applied {applied} curve binding(s) to {request.target_hierarchy_path}.",
                    data: new EditorControlData
                    {
                        executed = true,
                        applied_curve_count = applied,
                    });
            }
            catch (Exception ex)
            {
                return BuildError(
                    "EDITOR_CTRL_ANIMATION_CLIP_APPLY_FAILED",
                    $"AnimationClip apply failed: {ex.Message}");
            }
        }
    }
}
