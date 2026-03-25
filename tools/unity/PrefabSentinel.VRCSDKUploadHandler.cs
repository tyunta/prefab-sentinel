#if VRC_SDK_VRCSDK3
using System;
using System.Diagnostics;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;
using VRC.Core;
using VRC.SDK3.Avatars.Components;
using VRC.SDK3A.Editor;
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
                if (prefab.GetComponent<VRCAvatarDescriptor>() == null)
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

            // --- Build + Upload ---
            var sw = Stopwatch.StartNew();
            try
            {
                if (request.target_type == "avatar")
                    BuildAndUploadAvatar(request);
                else
                    BuildAndUploadWorld(request);
            }
            catch (Exception ex)
            {
                sw.Stop();
                string code = ex.Message.Contains("upload", StringComparison.OrdinalIgnoreCase)
                    ? "VRCSDK_UPLOAD_FAILED"
                    : "VRCSDK_BUILD_FAILED";
                return BuildError(code, $"{request.target_type} failed after {sw.Elapsed.TotalSeconds:F1}s: {ex.Message}");
            }
            sw.Stop();

            return BuildSuccess("VRCSDK_UPLOAD_OK",
                $"Uploaded {request.target_type} ({request.blueprint_id}) in {sw.Elapsed.TotalSeconds:F1}s",
                data: new EditorControlData
                {
                    target_type = request.target_type,
                    asset_path = request.asset_path,
                    blueprint_id = request.blueprint_id,
                    phase = "complete",
                    elapsed_sec = (float)sw.Elapsed.TotalSeconds,
                    executed = true
                });
        }

        private static void BuildAndUploadAvatar(EditorControlRequest request)
        {
            if (!VRCSdkControlPanel.TryGetBuilder<IVRCSdkAvatarBuilderApi>(out var builder))
                throw new InvalidOperationException("Failed to get IVRCSdkAvatarBuilderApi. Is VRC SDK properly installed?");

            var prefab = AssetDatabase.LoadAssetAtPath<GameObject>(request.asset_path);
            var pipelineManager = prefab.GetComponent<PipelineManager>();
            if (pipelineManager != null)
                pipelineManager.blueprintId = request.blueprint_id;

            builder.BuildAndUpload(prefab, null).GetAwaiter().GetResult();
        }

        private static void BuildAndUploadWorld(EditorControlRequest request)
        {
            if (!VRCSdkControlPanel.TryGetBuilder<IVRCSdkWorldBuilderApi>(out var builder))
                throw new InvalidOperationException("Failed to get IVRCSdkWorldBuilderApi. Is VRC SDK properly installed?");

            var scene = EditorSceneManager.OpenScene(request.asset_path, OpenSceneMode.Single);
            if (!scene.IsValid())
                throw new InvalidOperationException($"Failed to open scene: {request.asset_path}");

            var descriptor = UnityEngine.Object.FindObjectOfType<VRC_SceneDescriptor>();
            var pipelineManager = descriptor.GetComponent<PipelineManager>();
            if (pipelineManager != null)
                pipelineManager.blueprintId = request.blueprint_id;

            builder.BuildAndUpload(descriptor.gameObject, null).GetAwaiter().GetResult();
        }
    }
}
#endif
