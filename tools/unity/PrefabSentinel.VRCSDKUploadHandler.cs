#if VRC_SDK_VRCSDK3
using System;
using System.Diagnostics;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;
using VRC.Core;
using VRC.SDKBase;
using VRC.SDKBase.Editor.Api;
using static PrefabSentinel.UnityEditorControlBridge;

namespace PrefabSentinel
{
    /// <summary>
    /// Handles VRC SDK build + upload operations via the Editor Bridge.
    /// Entry point: <see cref="Handle"/> called from UnityEditorControlBridge dispatch.
    /// </summary>
    public static class VRCSDKUploadHandler
    {
        public static EditorControlResponse Handle(EditorControlRequest request)
        {
            // --- Input validation ---
            if (string.IsNullOrEmpty(request.target_type))
                return BuildError("VRCSDK_INVALID_TARGET_TYPE", "target_type is required ('avatar' or 'world').");
            if (request.target_type != "avatar" && request.target_type != "world")
                return BuildError("VRCSDK_INVALID_TARGET_TYPE",
                    $"target_type must be 'avatar' or 'world', got '{request.target_type}'.");
            if (string.IsNullOrEmpty(request.asset_path))
                return BuildError("VRCSDK_ASSET_NOT_FOUND", "asset_path is required.");
            if (string.IsNullOrEmpty(request.blueprint_id))
                return BuildError("VRCSDK_MISSING_BLUEPRINT_ID", "blueprint_id is required (existing asset update only).");

            // --- Login check ---
            if (!APIUser.IsLoggedIn)
                return BuildError("VRCSDK_NOT_LOGGED_IN",
                    "VRC SDK not logged in. Log in via VRChat SDK control panel.");

            // --- Asset validation ---
            if (request.target_type == "avatar")
            {
                var prefab = AssetDatabase.LoadAssetAtPath<GameObject>(request.asset_path);
                if (prefab == null)
                    return BuildError("VRCSDK_ASSET_NOT_FOUND",
                        $"Asset not found or not a GameObject: {request.asset_path}");
                var avatarDescType = System.Type.GetType(
                    "VRC.SDK3.Avatars.Components.VRCAvatarDescriptor, VRC.SDK3A");
                if (avatarDescType == null)
                    return BuildError("VRCSDK_AVATAR_SDK_NOT_FOUND",
                        "Avatar SDK (VRC.SDK3A) not installed. Cannot upload avatars from this project.");
                if (prefab.GetComponent(avatarDescType) == null)
                    return BuildError("VRCSDK_MISSING_DESCRIPTOR",
                        $"No VRCAvatarDescriptor found on: {request.asset_path}");
            }
            else // world
            {
                var scene = EditorSceneManager.OpenScene(request.asset_path, OpenSceneMode.Additive);
                if (!scene.IsValid())
                    return BuildError("VRCSDK_ASSET_NOT_FOUND",
                        $"Scene not found or invalid: {request.asset_path}");
                var descriptor = UnityEngine.Object.FindObjectOfType<VRC_SceneDescriptor>();
                if (descriptor == null)
                {
                    EditorSceneManager.CloseScene(scene, true);
                    return BuildError("VRCSDK_MISSING_DESCRIPTOR",
                        $"No VRC_SceneDescriptor found in scene: {request.asset_path}");
                }
            }

            // --- Parse platforms ---
            var platforms = ParsePlatforms(request.platforms);

            // --- dry-run return ---
            if (!request.confirm)
            {
                return BuildSuccess("VRCSDK_VALIDATED",
                    $"Validation passed for {request.target_type} at {request.asset_path}",
                    data: new EditorControlData
                    {
                        target_type = request.target_type,
                        asset_path = request.asset_path,
                        blueprint_id = request.blueprint_id,
                        phase = "validated",
                        elapsed_sec = 0f,
                        executed = false
                    });
            }

            // --- Multi-platform Build + Upload ---
            var originalTarget = EditorUserBuildSettings.activeBuildTarget;
            var totalSw = Stopwatch.StartNew();
            var results = new System.Collections.Generic.List<(string platform, bool success, float elapsed, string error, bool skipped)>();
            bool failed = false;
            bool restored = false;
            string failCode = "VRCSDK_BUILD_FAILED";
            string failMessage = "";

            try
            {
                for (int i = 0; i < platforms.Length; i++)
                {
                    var platform = platforms[i];
                    var platSw = Stopwatch.StartNew();

                    try
                    {
                        // Switch build target
                        bool switched = EditorUserBuildSettings.SwitchActiveBuildTarget(
                            ToBuildTargetGroup(platform), ToBuildTarget(platform));
                        if (!switched)
                        {
                            platSw.Stop();
                            results.Add((platform, false, (float)platSw.Elapsed.TotalSeconds,
                                "Platform switch failed", false));
                            failCode = "VRCSDK_PLATFORM_SWITCH_FAILED";
                            failMessage = $"Failed to switch to platform '{platform}'";
                            failed = true;
                            break;
                        }

                        // Build + Upload
                        if (request.target_type == "avatar")
                            BuildAndUploadAvatar(request);
                        else
                            BuildAndUploadWorld(request);

                        platSw.Stop();
                        results.Add((platform, true, (float)platSw.Elapsed.TotalSeconds, "", false));
                    }
                    catch (Exception ex)
                    {
                        platSw.Stop();
                        results.Add((platform, false, (float)platSw.Elapsed.TotalSeconds, ex.Message, false));
                        failCode = ex.Message.Contains("upload", StringComparison.OrdinalIgnoreCase)
                            ? "VRCSDK_UPLOAD_FAILED"
                            : "VRCSDK_BUILD_FAILED";
                        failMessage = $"{request.target_type} failed on platform '{platform}' after {platSw.Elapsed.TotalSeconds:F1}s: {ex.Message}";
                        failed = true;
                        break;
                    }
                }
            }
            finally
            {
                // Mark remaining platforms as skipped
                for (int i = results.Count; i < platforms.Length; i++)
                    results.Add((platforms[i], false, 0f, "", true));

                // Restore original build target (always, even on failure)
                restored = EditorUserBuildSettings.SwitchActiveBuildTarget(
                    BuildPipeline.GetBuildTargetGroup(originalTarget), originalTarget);
            }

            totalSw.Stop();

            var data = new EditorControlData
            {
                target_type = request.target_type,
                asset_path = request.asset_path,
                blueprint_id = request.blueprint_id,
                phase = failed ? "failed" : "complete",
                elapsed_sec = (float)totalSw.Elapsed.TotalSeconds,
                executed = true,
                platform_results_json = BuildPlatformResultsJson(results),
                original_target_restored = restored,
            };

            if (failed)
            {
                return BuildError(failCode, failMessage, data);
            }

            int platCount = platforms.Length;
            return BuildSuccess("VRCSDK_UPLOAD_OK",
                $"Uploaded {request.target_type} to {platCount} platform(s) in {totalSw.Elapsed.TotalSeconds:F1}s",
                data: data);
        }

        private static object ResolveBuilder(string assemblyQualifiedTypeName, string sdkDisplayName)
        {
            var builderType = System.Type.GetType(assemblyQualifiedTypeName);
            if (builderType == null)
                throw new InvalidOperationException(
                    $"{sdkDisplayName} not installed. Cannot proceed.");

            var tryGetMethod = typeof(VRCSdkControlPanel).GetMethod("TryGetBuilder");
            if (tryGetMethod == null)
                throw new InvalidOperationException("VRCSdkControlPanel.TryGetBuilder not found.");

            var genericMethod = tryGetMethod.MakeGenericMethod(builderType);
            var args = new object[] { null };
            bool success = (bool)genericMethod.Invoke(null, args);
            if (!success || args[0] == null)
                throw new InvalidOperationException(
                    $"Failed to get builder for {sdkDisplayName}. Open VRChat SDK panel first.");

            return args[0];
        }

        private static void InvokeBuildAndUpload(object builder, GameObject target, string builderKind)
        {
            var buildMethod = builder.GetType().GetMethod("BuildAndUpload");
            if (buildMethod == null)
                throw new InvalidOperationException(
                    $"BuildAndUpload method not found on {builderKind}.");
            var task = (System.Threading.Tasks.Task)buildMethod.Invoke(
                builder, new object[] { target, null });
            task.GetAwaiter().GetResult();
        }

        private static void BuildAndUploadAvatar(EditorControlRequest request)
        {
            var builder = ResolveBuilder(
                "VRC.SDK3A.Editor.IVRCSdkAvatarBuilderApi, VRC.SDK3A.Editor",
                "Avatar SDK (VRC.SDK3A.Editor)");

            var prefab = AssetDatabase.LoadAssetAtPath<GameObject>(request.asset_path);
            var pipelineManager = prefab.GetComponent<PipelineManager>();
            if (pipelineManager != null)
                pipelineManager.blueprintId = request.blueprint_id;

            InvokeBuildAndUpload(builder, prefab, "avatar builder");
        }

        private static void BuildAndUploadWorld(EditorControlRequest request)
        {
            var builder = ResolveBuilder(
                "VRC.SDK3.Editor.IVRCSdkWorldBuilderApi, VRC.SDK3.Editor",
                "World SDK (VRC.SDK3.Editor)");

            var scene = EditorSceneManager.OpenScene(request.asset_path, OpenSceneMode.Single);
            if (!scene.IsValid())
                throw new InvalidOperationException($"Failed to open scene: {request.asset_path}");

            var descriptor = UnityEngine.Object.FindObjectOfType<VRC_SceneDescriptor>();
            var pipelineManager = descriptor.GetComponent<PipelineManager>();
            if (pipelineManager != null)
                pipelineManager.blueprintId = request.blueprint_id;

            InvokeBuildAndUpload(builder, descriptor.gameObject, "world builder");
        }

        private static BuildTarget ToBuildTarget(string platform) => platform switch
        {
            "windows" => BuildTarget.StandaloneWindows64,
            "android" => BuildTarget.Android,
            "ios" => BuildTarget.iOS,
            _ => throw new ArgumentException($"Unknown platform: {platform}")
        };

        private static BuildTargetGroup ToBuildTargetGroup(string platform) => platform switch
        {
            "windows" => BuildTargetGroup.Standalone,
            "android" => BuildTargetGroup.Android,
            "ios" => BuildTargetGroup.iOS,
            _ => throw new ArgumentException($"Unknown platform: {platform}")
        };

        private static string[] ParsePlatforms(string json)
        {
            if (string.IsNullOrEmpty(json))
                return new[] { "windows" };
            // Minimal JSON array parser for string arrays: ["windows","android"]
            json = json.Trim();
            if (!json.StartsWith("[") || !json.EndsWith("]"))
                return new[] { "windows" };
            json = json.Substring(1, json.Length - 2); // strip [ ]
            if (string.IsNullOrWhiteSpace(json))
                return new[] { "windows" };
            var parts = json.Split(',');
            var result = new string[parts.Length];
            for (int i = 0; i < parts.Length; i++)
                result[i] = parts[i].Trim().Trim('"');
            return result;
        }

        private static string BuildPlatformResultsJson(
            System.Collections.Generic.List<(string platform, bool success, float elapsed, string error, bool skipped)> results)
        {
            var sb = new System.Text.StringBuilder("[");
            for (int i = 0; i < results.Count; i++)
            {
                if (i > 0) sb.Append(",");
                var r = results[i];
                if (r.skipped)
                    sb.Append($"{{\"platform\":\"{r.platform}\",\"skipped\":true}}");
                else if (r.success)
                    sb.Append($"{{\"platform\":\"{r.platform}\",\"success\":true,\"elapsed_sec\":{r.elapsed.ToString("F1", System.Globalization.CultureInfo.InvariantCulture)}}}");
                else
                {
                    var escapedError = r.error.Replace("\\", "\\\\").Replace("\"", "\\\"").Replace("\n", "\\n").Replace("\r", "\\r").Replace("\t", "\\t");
                    sb.Append($"{{\"platform\":\"{r.platform}\",\"success\":false,\"elapsed_sec\":{r.elapsed.ToString("F1", System.Globalization.CultureInfo.InvariantCulture)},\"error\":\"{escapedError}\"}}");
                }
            }
            sb.Append("]");
            return sb.ToString();
        }
    }
}
#endif
