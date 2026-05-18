using System;
using System.Collections.Generic;
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
        /// ``null`` when the lookup misses *or* when the path is
        /// ambiguous (same-named siblings with no ``#N``); callers report
        /// their own ``EDITOR_CTRL_*_NOT_FOUND`` envelope.  Handlers that
        /// need to distinguish an ambiguous path from a genuine miss call
        /// <see cref="TryResolveGameObjectInActiveStage"/> instead.
        /// </summary>
        internal static GameObject ResolveGameObjectInActiveStage(string hierarchyPath)
        {
            TryResolveGameObjectInActiveStage(
                hierarchyPath, out GameObject go, out _);
            return go;
        }

        /// <summary>
        /// Issue #38: resolve a ``/``-delimited hierarchy path against the
        /// active Prefab Stage, with each segment allowed to carry a
        /// ``name#N`` disambiguator.  Segment resolution is delegated to
        /// the Unity-free <see cref="SymbolPathResolver"/> so the live
        /// editor track shares one ``#N`` rule with the offline symbol
        /// tree and the patch selector; this resolver does no independent
        /// first-pick path walk.  When the path matches same-named
        /// siblings without a ``#N``, <paramref name="ambiguityEnvelope"/>
        /// is the ``EDITOR_CTRL_HIERARCHY_PATH_AMBIGUOUS`` error and
        /// <paramref name="go"/> is ``null`` — resolution stops rather
        /// than silently picking the first sibling.  A genuine miss
        /// leaves both ``null``.
        /// </summary>
        internal static bool TryResolveGameObjectInActiveStage(
            string hierarchyPath,
            out GameObject go,
            out EditorControlResponse ambiguityEnvelope)
        {
            go = null;
            ambiguityEnvelope = null;
            if (string.IsNullOrEmpty(hierarchyPath)) return false;

            var stage = PrefabStageUtility.GetCurrentPrefabStage();
            if (stage == null)
            {
                // No Prefab Stage open — fall back to the open scene.
                go = GameObject.Find(hierarchyPath);
                return go != null;
            }

            var stageRoot = stage.prefabContentsRoot;
            if (stageRoot == null) return false;

            // Absolute-style paths (``/Root/Child``) are accepted as a
            // convenience for callers that mirror Unity's hierarchy log
            // format; leading-slash normalization is owned by the
            // Unity-free StageHierarchyPathLogic so it is exercised by
            // the C# xUnit harness (issue #18).
            string normalized =
                StageHierarchyPathLogic.NormalizeStagePath(hierarchyPath);
            if (string.IsNullOrEmpty(normalized)) return false;

            // A single-name path equal to the stage root's name addresses
            // the stage root itself; descent paths are root-relative
            // (``Body/Head`` resolves under the root, not ``Root/Body/Head``),
            // preserving the pre-#38 path contract.
            if (stageRoot.name == normalized)
            {
                go = stageRoot;
                return true;
            }

            // Build a node tree from the stage root's *children* and
            // resolve the root-relative segments through the shared
            // Unity-free ``#N`` resolver; the resolver disambiguates
            // same-named siblings by ``#N`` and rejects an ambiguous
            // segment rather than first-picking.  ``Transform.GetChild``
            // order is the resolution-significant child order the
            // resolver expects.
            var idToTransform = new Dictionary<string, Transform>();
            var rootSiblings = new List<SymbolPathNode>(
                stageRoot.transform.childCount);
            for (int i = 0; i < stageRoot.transform.childCount; i++)
            {
                rootSiblings.Add(BuildStageNode(
                    stageRoot.transform.GetChild(i), idToTransform));
            }
            string[] segments = normalized.Split('/');

            SymbolPathResolution resolution = SymbolPathResolver.Resolve(
                rootSiblings, segments);

            if (resolution.Outcome == SymbolPathOutcome.Ambiguous)
            {
                ambiguityEnvelope = BuildError(
                    "EDITOR_CTRL_HIERARCHY_PATH_AMBIGUOUS",
                    $"hierarchy_path '{hierarchyPath}' matched "
                    + $"{resolution.MatchCount} same-named objects in the "
                    + "active Prefab Stage. Disambiguate a same-named "
                    + "segment with a '#N' suffix (0-based, child order), "
                    + "e.g. 'Body/Mesh#1'.");
                return false;
            }
            if (resolution.Outcome != SymbolPathOutcome.Unique) return false;

            if (idToTransform.TryGetValue(resolution.Node.Id, out Transform t)
                && t != null)
            {
                go = t.gameObject;
                return true;
            }
            return false;
        }

        /// <summary>
        /// Build a <see cref="SymbolPathNode"/> tree mirroring a live
        /// Prefab Stage transform subtree.  Each node's ``Id`` is a
        /// synthetic key registered in <paramref name="idToTransform"/>
        /// so the resolver's unique result maps back to the live
        /// ``Transform``.  Children are appended in
        /// ``Transform.GetChild`` order — the order the resolver treats
        /// as significant for ``#N``.
        /// </summary>
        private static SymbolPathNode BuildStageNode(
            Transform transform,
            Dictionary<string, Transform> idToTransform)
        {
            string id = transform.GetInstanceID().ToString();
            idToTransform[id] = transform;
            var children = new List<SymbolPathNode>(transform.childCount);
            for (int i = 0; i < transform.childCount; i++)
            {
                children.Add(BuildStageNode(transform.GetChild(i), idToTransform));
            }
            return new SymbolPathNode(id, transform.name, children);
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
