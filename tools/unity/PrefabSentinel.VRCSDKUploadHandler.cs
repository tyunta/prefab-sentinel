#if VRC_SDK_VRCSDK3 && PS_VRCSDK_BASE_3_8_0
using System;
using System.Diagnostics;
using System.Reflection;
using System.Threading;
using System.Threading.Tasks;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.SceneManagement;
using VRC.Core;
using VRC.SDKBase;
using VRC.SDKBase.Editor.Api;
using static PrefabSentinel.UnityEditorControlBridge;

namespace PrefabSentinel
{
    /// <summary>
    /// Handles VRC SDK build + upload operations via the Editor Bridge.
    /// Entry point: <see cref="Handle"/> called from UnityEditorControlBridge dispatch.
    /// For confirm=true, Handle returns null and HandleAsync writes the response file asynchronously.
    /// </summary>
    public static class VRCSDKUploadHandler
    {
        private const string VRCApiTypeName = "VRC.SDKBase.Editor.Api.VRCApi, VRC.SDKBase.Editor";
        private const string ShowSdkPanelMenuItem = "VRChat SDK/Show Control Panel";
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
            else
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
                EditorSceneManager.CloseScene(scene, true);
            }

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

            // confirm=true: async path handles build+upload and writes response
            return null;
        }

        public static async void HandleAsync(EditorControlRequest request, string responsePath)
        {
            var platforms = ParsePlatforms(request.platforms);
            var originalTarget = EditorUserBuildSettings.activeBuildTarget;
            var totalSw = Stopwatch.StartNew();
            var results = new System.Collections.Generic.List<(string platform, bool success, float elapsed, string error, bool skipped)>();
            bool failed = false;
            bool restored = false;
            string failCode = "VRCSDK_BUILD_FAILED";
            string failMessage = "";

            try
            {
                if (!APIUser.IsLoggedIn)
                {
                    EditorApplication.ExecuteMenuItem(ShowSdkPanelMenuItem);
                    const int loginPollIntervalMs = 100;
                    const int loginPollMaxIterations = 300; // 30 seconds total
                    for (int i = 0; i < loginPollMaxIterations; i++)
                    {
                        await Task.Delay(loginPollIntervalMs);
                        if (APIUser.IsLoggedIn) break;
                    }
                    if (!APIUser.IsLoggedIn)
                    {
                        WriteResponse(responsePath, BuildError("VRCSDK_NOT_LOGGED_IN",
                            "Timed out waiting for VRC SDK login (30s). Log in via the VRChat SDK control panel."));
                        return;
                    }
                }
                try
                {
                    for (int i = 0; i < platforms.Length; i++)
                    {
                        var platform = platforms[i];
                        var platSw = Stopwatch.StartNew();

                        try
                        {
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

                            if (request.target_type == "avatar")
                                await BuildAndUploadAvatarAsync(request);
                            else
                                await BuildAndUploadWorldAsync(request);

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
                    try
                    {
                        for (int i = results.Count; i < platforms.Length; i++)
                            results.Add((platforms[i], false, 0f, "", true));

                        restored = EditorUserBuildSettings.SwitchActiveBuildTarget(
                            BuildPipeline.GetBuildTargetGroup(originalTarget), originalTarget);
                    }
                    catch (Exception ex)
                    {
                        UnityEngine.Debug.LogError(
                            $"[VRCSDKUploadHandler] Failed to restore build target in finally block: {ex}");
                    }
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
                    WriteResponse(responsePath, BuildError(failCode, failMessage, data));
                    return;
                }

                int platCount = platforms.Length;
                WriteResponse(responsePath, BuildSuccess("VRCSDK_UPLOAD_OK",
                    $"Uploaded {request.target_type} to {platCount} platform(s) in {totalSw.Elapsed.TotalSeconds:F1}s",
                    data: data));
            }
            catch (Exception ex)
            {
                totalSw.Stop();
                WriteResponse(responsePath, BuildError("VRCSDK_BUILD_FAILED", ex.Message));
            }
        }

        private static async Task<object> ResolveBuilderAsync(string assemblyQualifiedTypeName, string sdkDisplayName)
        {
            var builderType = System.Type.GetType(assemblyQualifiedTypeName);
            if (builderType == null)
                throw new InvalidOperationException(
                    $"{sdkDisplayName} not installed. Cannot proceed.");

            var tryGetMethod = Array.Find(
                typeof(VRCSdkControlPanel).GetMethods(),
                m => m.Name == "TryGetBuilder" && m.IsGenericMethodDefinition && m.GetParameters().Length == 1);
            if (tryGetMethod == null)
                throw new InvalidOperationException("VRCSdkControlPanel.TryGetBuilder not found.");

            var genericMethod = tryGetMethod.MakeGenericMethod(builderType);
            var args = new object[] { null };
            bool success = (bool)genericMethod.Invoke(null, args);
            if (!success || args[0] == null)
            {
                EditorApplication.ExecuteMenuItem(ShowSdkPanelMenuItem);
                const int builderPollIntervalMs = 100;
                const int builderPollMaxIterations = 50; // 5 seconds total
                for (int i = 0; i < builderPollMaxIterations; i++)
                {
                    await Task.Delay(builderPollIntervalMs);
                    args[0] = null;
                    success = (bool)genericMethod.Invoke(null, args);
                    if (success && args[0] != null) return args[0];
                }
                throw new InvalidOperationException(
                    $"Timed out waiting for builder for {sdkDisplayName} (5s). Open VRChat SDK panel first.");
            }

            return args[0];
        }

        private static async Task BuildAndUploadAvatarAsync(EditorControlRequest request)
        {
            var builder = await ResolveBuilderAsync(
                "VRC.SDK3A.Editor.IVRCSdkAvatarBuilderApi, VRC.SDK3A.Editor",
                "Avatar SDK (VRC.SDK3A.Editor)");

            var prefab = AssetDatabase.LoadAssetAtPath<GameObject>(request.asset_path);
            var pipelineManager = prefab.GetComponent<PipelineManager>();
            if (pipelineManager != null)
                pipelineManager.blueprintId = request.blueprint_id;

            var vrcAvatar = await InvokeStaticAsync<object>(
                VRCApiTypeName,
                "GetAvatar", new object[] { request.blueprint_id, true, CancellationToken.None });

            var buildMethod = Array.Find(
                builder.GetType().GetMethods(),
                m => m.Name == "BuildAndUpload"
                    && m.GetParameters().Length == 4
                    && m.GetParameters()[0].ParameterType == typeof(GameObject));
            if (buildMethod == null)
                throw new InvalidOperationException(
                    "BuildAndUpload(GameObject, VRCAvatar, string, CancellationToken) not found on avatar builder.");

            var task = (Task)buildMethod.Invoke(
                builder, new object[] { prefab, vrcAvatar, null, CancellationToken.None });
            await task;
        }

        private static async Task BuildAndUploadWorldAsync(EditorControlRequest request)
        {
            var builder = await ResolveBuilderAsync(
                "VRC.SDK3.Editor.IVRCSdkWorldBuilderApi, VRC.SDK3.Editor",
                "World SDK (VRC.SDK3.Editor)");

            // Skip scene reload if already active
            var activeScene = SceneManager.GetActiveScene();
            if (activeScene.path != request.asset_path)
            {
                var scene = EditorSceneManager.OpenScene(request.asset_path, OpenSceneMode.Single);
                if (!scene.IsValid())
                    throw new InvalidOperationException($"Failed to open scene: {request.asset_path}");
            }

            var descriptor = UnityEngine.Object.FindObjectOfType<VRC_SceneDescriptor>();
            if (descriptor == null)
                throw new InvalidOperationException(
                    $"No VRC_SceneDescriptor found in scene: {request.asset_path}");
            var pipelineManager = descriptor.GetComponent<PipelineManager>();
            if (pipelineManager != null)
                pipelineManager.blueprintId = request.blueprint_id;

            var vrcWorld = await InvokeStaticAsync<object>(
                VRCApiTypeName,
                "GetWorld", new object[] { request.blueprint_id, true, CancellationToken.None });

            var buildMethod = Array.Find(
                builder.GetType().GetMethods(),
                m => m.Name == "BuildAndUpload"
                    && m.GetParameters().Length == 3
                    && m.GetParameters()[0].ParameterType.Name == "VRCWorld");
            if (buildMethod == null)
                throw new InvalidOperationException(
                    "BuildAndUpload(VRCWorld, string, CancellationToken) not found on world builder.");

            var task = (Task)buildMethod.Invoke(
                builder, new object[] { vrcWorld, null, CancellationToken.None });
            await task;
        }

        /// <summary>
        /// Invokes a static async method by reflection and awaits its result.
        /// Used for cross-assembly calls to VRCApi (GetAvatar, GetWorld).
        /// </summary>
        private static async Task<T> InvokeStaticAsync<T>(
            string assemblyQualifiedTypeName, string methodName, object[] args)
        {
            var type = System.Type.GetType(assemblyQualifiedTypeName);
            if (type == null)
                throw new InvalidOperationException($"Type not found: {assemblyQualifiedTypeName}");

            var method = Array.Find(
                type.GetMethods(BindingFlags.Public | BindingFlags.Static),
                m => m.Name == methodName && m.GetParameters().Length == args.Length);
            if (method == null)
                throw new InvalidOperationException($"{type.Name}.{methodName} not found.");

            var result = method.Invoke(null, args);
            if (result is Task task)
            {
                await task;
                // Extract result from Task<T> via reflection
                var resultProperty = task.GetType().GetProperty("Result");
                if (resultProperty == null)
                    throw new InvalidOperationException($"{type.Name}.{methodName} did not return a Task<T>.");
                return (T)resultProperty.GetValue(task);
            }
            throw new InvalidOperationException($"{type.Name}.{methodName} did not return a Task.");
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
            json = json.Trim();
            if (!json.StartsWith("[") || !json.EndsWith("]"))
                return new[] { "windows" };
            json = json.Substring(1, json.Length - 2);
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
#endif // VRC_SDK_VRCSDK3 && PS_VRCSDK_BASE_3_8_0
