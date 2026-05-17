using System;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;

// Prefab Stage handlers — open_prefab / close_prefab and the stage-aware hierarchy resolver (#236).
namespace PrefabSentinel
{
    /// <summary>
    /// Prefab Stage partial (issue #236) — owns the ``open_prefab`` and
    /// ``close_prefab`` handlers and the hierarchy resolver helper every
    /// hierarchy-bound handler delegates to.  When a Prefab Stage is
    /// active, the resolver confines lookups to the staged content; it
    /// does not consult the open scene under any circumstances while a
    /// stage is open (issue #264 — closing this leak prevents edits
    /// from landing on scene instances when the caller intended to edit
    /// staged prefab contents).
    /// </summary>
    public static partial class UnityEditorControlBridge
    {
        /// <summary>
        /// Resolve a hierarchy path against the active Prefab Stage when
        /// one exists, otherwise against the open scene.  Returns
        /// ``null`` when the lookup misses; callers report their own
        /// ``EDITOR_CTRL_*_NOT_FOUND`` envelope.
        /// </summary>
        internal static GameObject ResolveGameObjectInActiveStage(string hierarchyPath)
        {
            if (string.IsNullOrEmpty(hierarchyPath)) return null;
            var stage = PrefabStageUtility.GetCurrentPrefabStage();
            if (stage != null)
            {
                var stageRoot = stage.prefabContentsRoot;
                if (stageRoot == null) return null;
                // Absolute-style paths (``/Root/Child``) are accepted as
                // a convenience for callers that mirror Unity's
                // hierarchy log format; leading-slash normalization is
                // owned by the Unity-free StageHierarchyPathLogic so it
                // is exercised by the C# xUnit harness (issue #18).
                string normalized = StageHierarchyPathLogic.NormalizeStagePath(hierarchyPath);
                // A single-name path addresses the stage root rather
                // than a child of an unnamed pivot.
                if (stageRoot.name == normalized) return stageRoot;
                var t = stageRoot.transform.Find(normalized);
                return t != null ? t.gameObject : null;
            }
            return GameObject.Find(hierarchyPath);
        }

        private static EditorControlResponse HandleOpenPrefab(EditorControlRequest request)
        {
            if (string.IsNullOrEmpty(request.asset_path))
                return BuildError(
                    "EDITOR_CTRL_PREFAB_STAGE_NOT_FOUND",
                    "open_prefab requires a non-empty asset_path.");
            try
            {
                var stage = PrefabStageUtility.OpenPrefab(request.asset_path);
                if (stage == null)
                    return BuildError(
                        "EDITOR_CTRL_PREFAB_STAGE_OPEN_FAILED",
                        $"Unity refused to open the prefab stage for asset {request.asset_path}.");
                var root = stage.prefabContentsRoot;
                return BuildSuccess(
                    "EDITOR_CTRL_PREFAB_STAGE_OPEN_OK",
                    $"Opened prefab stage for {request.asset_path}.",
                    data: new EditorControlData
                    {
                        executed = true,
                        asset_path = request.asset_path,
                        stage_root_name = root != null ? root.name : string.Empty,
                    });
            }
            catch (Exception ex)
            {
                return BuildError(
                    "EDITOR_CTRL_PREFAB_STAGE_OPEN_FAILED",
                    $"Open prefab stage raised: {ex.Message}");
            }
        }

        private static EditorControlResponse HandleClosePrefab(EditorControlRequest request)
        {
            var stage = PrefabStageUtility.GetCurrentPrefabStage();
            if (stage == null)
                return BuildError(
                    "EDITOR_CTRL_PREFAB_STAGE_CLOSE_FAILED",
                    "close_prefab: no Prefab Stage is currently active.");
            string assetPath = stage.assetPath;
            try
            {
                bool didSave = false;
                if (request.save_on_close)
                {
                    // Issue #264: persist staged edits through the
                    // prefab-asset persistence API. ``SaveScene`` on the
                    // stage's preview scene is rejected by Unity in
                    // some versions and never writes the prefab asset
                    // even when accepted. The reported ``success`` flag
                    // is bound to the response envelope so callers see
                    // the truthful outcome.
                    var stageRoot = stage.prefabContentsRoot;
                    if (stageRoot == null)
                        return BuildError(
                            "EDITOR_CTRL_PREFAB_STAGE_CLOSE_FAILED",
                            "close_prefab: active stage has no prefab contents root.");
                    PrefabUtility.SaveAsPrefabAsset(stageRoot, assetPath, out didSave);
                    if (didSave)
                    {
                        // Clearing the dirty marker suppresses the
                        // editor's "Save?" modal on the subsequent
                        // ``GoToMainStage`` call.
                        stage.ClearDirtiness();
                    }
                }
                StageUtility.GoToMainStage();
                return BuildSuccess(
                    "EDITOR_CTRL_PREFAB_STAGE_CLOSE_OK",
                    $"Closed prefab stage for {assetPath} (saved={didSave}).",
                    data: new EditorControlData
                    {
                        executed = true,
                        asset_path = assetPath,
                        saved = didSave,
                    });
            }
            catch (Exception ex)
            {
                return BuildError(
                    "EDITOR_CTRL_PREFAB_STAGE_CLOSE_FAILED",
                    $"Close prefab stage raised: {ex.Message}");
            }
        }
    }
}
