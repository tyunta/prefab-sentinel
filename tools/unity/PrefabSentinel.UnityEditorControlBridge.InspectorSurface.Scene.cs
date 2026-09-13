using System;
using System.Collections.Generic;
using System.Runtime.ExceptionServices;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.SceneManagement;

namespace PrefabSentinel
{
    public static partial class UnityEditorControlBridge
    {
        internal static Func<Scene, bool, bool> InspectorCloseScene =
            EditorSceneManager.CloseScene;
        internal static Func<Scene, bool> InspectorSetActiveScene =
            SceneManager.SetActiveScene;
        internal static Func<InspectorSceneLifecycle.Snapshot> InspectorCaptureSnapshot =
            CaptureInspectorSceneSnapshot;

        private static partial EditorControlResponse InspectSceneSerializedSurface(
            EditorControlRequest request,
            string assetPath)
        {
            InspectorSceneLifecycle.Snapshot before = InspectorCaptureSnapshot();
            InspectorSceneLifecycle.Decision decision = InspectorSceneLifecycle.Decide(
                before,
                NormalizeInspectorScenePath(assetPath));
            string ownershipToken = decision.Ownership.ToString().ToLowerInvariant();

            if (decision.State == InspectorSceneLifecycle.DecisionState.Dirty)
            {
                InspectorSceneLifecycle.Snapshot dirtyAfter = InspectorCaptureSnapshot();
                return BuildInspectorSceneFailure(
                    "EDITOR_CTRL_INSPECTOR_SCENE_DIRTY",
                    "The loaded Scene has unsaved changes; save or discard them before "
                    + "inspecting its last-saved serialized surface.",
                    assetPath,
                    ownershipToken,
                    decision.MatchedHandles,
                    false,
                    "not_attempted",
                    false,
                    "not_attempted",
                    before,
                    dirtyAfter,
                    Array.Empty<string>());
            }

            if (decision.State == InspectorSceneLifecycle.DecisionState.Ambiguous)
            {
                InspectorSceneLifecycle.Snapshot ambiguousAfter =
                    InspectorCaptureSnapshot();
                return BuildInspectorSceneFailure(
                    "EDITOR_CTRL_INSPECTOR_SCENE_AMBIGUOUS",
                    "The requested Scene is loaded more than once; close duplicate instances "
                    + "before inspecting its serialized surface.",
                    assetPath,
                    ownershipToken,
                    decision.MatchedHandles,
                    false,
                    "not_attempted",
                    false,
                    "not_attempted",
                    before,
                    ambiguousAfter,
                    Array.Empty<string>());
            }

            Scene targetScene = default(Scene);
            int targetHandle = decision.TargetHandle;
            if (decision.Ownership == InspectorSceneLifecycle.Ownership.Borrowed)
            {
                targetScene =
                    FindLoadedInspectorSceneByHandle(decision.TargetHandle);
            }
            else if (decision.Ownership == InspectorSceneLifecycle.Ownership.Owned)
            {
                targetScene = EditorSceneManager.OpenScene(
                    assetPath,
                    OpenSceneMode.Additive);
                targetHandle = targetScene.handle;
            }

            EditorControlResponse surfaceResponse = null;
            ExceptionDispatchInfo surfaceException = null;
            bool cleanupAttempted = false;
            bool closeSucceeded = false;
            try
            {
                UnityEngine.Object target = ResolveInspectorComponent(
                    targetScene.GetRootGameObjects(),
                    request.symbol_path);
                surfaceResponse = target == null
                    ? InspectorSurfaceTargetNotFound()
                    : BuildInspectorSurfaceResponse(request, target);
            }
            catch (Exception exception)
            {
                surfaceException = ExceptionDispatchInfo.Capture(exception);
            }
            finally
            {
                if (decision.Ownership == InspectorSceneLifecycle.Ownership.Owned)
                {
                    cleanupAttempted = true;
                    closeSucceeded = InspectorCloseScene(targetScene, true);
                }
            }

            Scene currentActive = SceneManager.GetActiveScene();
            bool activeRestoreRequired =
                before.active_handle != 0
                && (!currentActive.IsValid()
                    || !currentActive.isLoaded
                    || currentActive.handle != before.active_handle
                    || !string.Equals(
                        NormalizeInspectorScenePath(currentActive.path),
                        before.active_path,
                        StringComparison.Ordinal));
            bool activeRestoreAttempted = false;
            bool activeRestoreSucceeded = false;
            if (activeRestoreRequired)
            {
                Scene originalActive =
                    FindLoadedInspectorSceneByHandle(before.active_handle);
                if (originalActive.IsValid() && originalActive.isLoaded)
                {
                    activeRestoreAttempted = true;
                    activeRestoreSucceeded = InspectorSetActiveScene(originalActive);
                }
            }

            InspectorSceneLifecycle.Snapshot after = InspectorCaptureSnapshot();
            InspectorSceneLifecycle.Completion completion =
                InspectorSceneLifecycle.EvaluateCompletion(
                    before,
                    after,
                    decision.Ownership,
                    targetHandle,
                    cleanupAttempted,
                    closeSucceeded,
                    activeRestoreRequired,
                    activeRestoreSucceeded);
            if (!completion.Success)
            {
                return BuildInspectorSceneFailure(
                    "EDITOR_CTRL_INSPECTOR_SCENE_RESTORE_FAILED",
                    "Scene inspection could not restore the Editor Scene state.",
                    assetPath,
                    ownershipToken,
                    decision.MatchedHandles,
                    cleanupAttempted,
                    cleanupAttempted
                        ? closeSucceeded ? "succeeded" : "failed"
                        : "not_attempted",
                    activeRestoreAttempted,
                    activeRestoreAttempted
                        ? activeRestoreSucceeded ? "succeeded" : "failed"
                        : "not_attempted",
                    before,
                    after,
                    completion.FailedPostconditions);
            }

            if (surfaceException != null)
            {
                surfaceException.Throw();
            }

            return surfaceResponse;
        }


        private static Scene FindLoadedInspectorSceneByHandle(int handle)
        {
            for (int index = 0; index < SceneManager.sceneCount; index++)
            {
                Scene scene = SceneManager.GetSceneAt(index);
                if (scene.isLoaded && scene.handle == handle)
                {
                    return scene;
                }
            }

            return default(Scene);
        }

        private static InspectorSceneLifecycle.Snapshot CaptureInspectorSceneSnapshot()
        {
            var entries = new List<InspectorSceneLifecycle.Entry>();
            for (int index = 0; index < SceneManager.sceneCount; index++)
            {
                Scene scene = SceneManager.GetSceneAt(index);
                if (!scene.isLoaded) continue;
                entries.Add(new InspectorSceneLifecycle.Entry
                {
                    order = entries.Count,
                    handle = scene.handle,
                    path = NormalizeInspectorScenePath(scene.path),
                    dirty = scene.isDirty,
                });
            }

            Scene active = SceneManager.GetActiveScene();
            return new InspectorSceneLifecycle.Snapshot
            {
                scenes = entries.ToArray(),
                active_handle = active.IsValid() && active.isLoaded ? active.handle : 0,
                active_path = active.IsValid() && active.isLoaded
                    ? NormalizeInspectorScenePath(active.path)
                    : string.Empty,
            };
        }

        private static string NormalizeInspectorScenePath(string path)
        {
            return (path ?? string.Empty).Replace('\\', '/');
        }

        private static EditorControlResponse BuildInspectorSceneFailure(
            string rawCode,
            string fixedDetail,
            string assetPath,
            string ownershipToken,
            int[] matchedHandles,
            bool cleanupAttempted,
            string closeResult,
            bool activeRestoreAttempted,
            string activeRestoreResult,
            InspectorSceneLifecycle.Snapshot before,
            InspectorSceneLifecycle.Snapshot after,
            string[] failedPostconditions)
        {
            var evidence = new InspectorSceneLifecycle.Evidence
            {
                asset_path = assetPath,
                ownership = ownershipToken,
                matched_handles = matchedHandles,
                cleanup_attempted = cleanupAttempted,
                close_result = closeResult,
                active_restore_attempted = activeRestoreAttempted,
                active_restore_result = activeRestoreResult,
                before = before,
                after = after,
                failed_postconditions = failedPostconditions,
            };
            EditorControlResponse response = BuildError(rawCode, fixedDetail);
            response.diagnostics = new[]
            {
                new EditorControlDiagnostic
                {
                    code = rawCode,
                    severity = "error",
                    path = assetPath,
                    location = ownershipToken,
                    detail = fixedDetail,
                    evidence = JsonUtility.ToJson(evidence),
                }
            };
            return response;
        }
    }
}
