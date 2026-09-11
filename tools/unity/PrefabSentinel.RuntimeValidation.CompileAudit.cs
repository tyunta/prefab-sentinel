#nullable enable

using System;
using System.Collections.Generic;

namespace PrefabSentinel
{
    /// <summary>
    /// Unity-free value model for compile preflight decisions and compiler side-effect deltas.
    /// The Unity adapter supplies resolved facts; this model neither discovers nor mutates them.
    /// </summary>
    internal static partial class RuntimeCompileAudit
    {
        internal enum GeneratedAssetPolicy
        {
            Deny,
            Create,
            Replace,
        }

        [Serializable]
        internal sealed class AssetIdentity
        {
            public string guid = string.Empty;
            public long local_file_id;
            public string path = string.Empty;
            public string type = string.Empty;
            public bool dirty;
            public string[] attribution_unknown = Array.Empty<string>();
        }

        [Serializable]
        internal sealed class SceneIdentity
        {
            public string path = string.Empty;
            public int handle;
            public bool dirty;
            public string[] attribution_unknown = Array.Empty<string>();
        }

        [Serializable]
        internal sealed class GeneratedAssetPlan
        {
            public string[] planned_created_paths = Array.Empty<string>();
            public string[] planned_deleted_paths = Array.Empty<string>();
        }

        [Serializable]
        internal sealed class Snapshot
        {
            public bool inventory_stable = true;
            public string[] prefab_repair_paths = Array.Empty<string>();
            public AssetIdentity[] related_assets = Array.Empty<AssetIdentity>();
            public SceneIdentity[] loaded_scenes = Array.Empty<SceneIdentity>();
            public GeneratedAssetPlan generated_asset_plan = new GeneratedAssetPlan();
            public string[] project_dirty_paths = Array.Empty<string>();
        }

        [Serializable]
        internal sealed class PreflightDecision
        {
            public bool compile_allowed;
            public string code = string.Empty;
            public AssetIdentity[] offending_assets = Array.Empty<AssetIdentity>();
            public string[] offending_paths = Array.Empty<string>();
            public string[] attribution_unknown = Array.Empty<string>();
        }

        [Serializable]
        internal sealed class Delta
        {
            public string[] newly_dirty_paths = Array.Empty<string>();
            public string[] no_longer_dirty_paths = Array.Empty<string>();
            public string[] newly_dirty_scene_paths = Array.Empty<string>();
            public string[] no_longer_dirty_scene_paths = Array.Empty<string>();
            public string[] planned_created_paths = Array.Empty<string>();
            public string[] planned_deleted_paths = Array.Empty<string>();
            public string[] actual_created_paths = Array.Empty<string>();
            public string[] actual_deleted_paths = Array.Empty<string>();
            public string[] unrelated_dirty_paths_before = Array.Empty<string>();
            public string[] unrelated_dirty_paths_after = Array.Empty<string>();
            public string[] attribution_unknown = Array.Empty<string>();
        }

        [Serializable]
        internal sealed class Classification
        {
            public string severity = string.Empty;
            public Delta delta = new Delta();
        }

        internal static GeneratedAssetPolicy ParsePolicy(string value)
        {
            if (string.Equals(value, "deny", StringComparison.Ordinal)) return GeneratedAssetPolicy.Deny;
            if (string.Equals(value, "create", StringComparison.Ordinal)) return GeneratedAssetPolicy.Create;
            if (string.Equals(value, "replace", StringComparison.Ordinal)) return GeneratedAssetPolicy.Replace;
            throw new ArgumentException("generated_asset_policy must be deny, create, or replace.", nameof(value));
        }







        internal static PreflightDecision EvaluatePreflight(
            Snapshot? snapshot,
            GeneratedAssetPolicy policy,
            bool allowDirtyPrograms,
            bool allowDirtyScenes)
        {
            if (snapshot == null || !IsComplete(snapshot) || !snapshot.inventory_stable)
            {
                return Denied("UDON_COMPILE_PREFLIGHT_INDETERMINATE");
            }

            string[] prefabRepairPaths = SortedDistinct(snapshot.prefab_repair_paths);
            if (prefabRepairPaths.Length != 0) return Denied("UDON_COMPILE_PREFAB_REPAIR_REQUIRED", paths: prefabRepairPaths);

            AssetIdentity[] dirtyAssets = DirtyCompileAssets(snapshot.related_assets);
            if (dirtyAssets.Length != 0 && !allowDirtyPrograms) return Denied("UDON_COMPILE_DIRTY_PRECONDITION", assets: dirtyAssets);

            SceneIdentity[] dirtyScenes = DirtyScenes(snapshot.loaded_scenes);
            if (dirtyScenes.Length != 0 && !allowDirtyScenes) return Denied("UDON_COMPILE_DIRTY_SCENE_PRECONDITION", paths: ScenePaths(dirtyScenes));

            GeneratedAssetPlan plan = snapshot.generated_asset_plan;
            string[] plannedDeleted = SortedDistinct(plan.planned_deleted_paths);
            if (plannedDeleted.Length != 0 && policy != GeneratedAssetPolicy.Replace)
            {
                return Denied("UDON_GENERATED_ASSET_REPLACEMENT_REQUIRED", paths: plannedDeleted);
            }
            string[] plannedCreated = SortedDistinct(plan.planned_created_paths);
            if (plannedCreated.Length != 0 && policy == GeneratedAssetPolicy.Deny)
            {
                return Denied("UDON_GENERATED_ASSET_CREATION_REQUIRED", paths: plannedCreated);
            }

            var decision = new PreflightDecision { compile_allowed = true };
            if (allowDirtyPrograms && dirtyAssets.Length != 0) AddAttributionUnknown(decision.attribution_unknown, dirtyAssets, out decision.attribution_unknown);
            if (allowDirtyScenes && dirtyScenes.Length != 0) AddAttributionUnknown(decision.attribution_unknown, dirtyScenes, out decision.attribution_unknown);
            return decision;
        }

        internal static Delta Diff(Snapshot? before, Snapshot? after, GeneratedAssetPlan? planned)
        {
            if (before == null) throw new ArgumentNullException(nameof(before));
            if (after == null) throw new ArgumentNullException(nameof(after));
            if (planned == null) throw new ArgumentNullException(nameof(planned));
            if (!IsComplete(before)) throw new ArgumentException("Snapshot evidence is incomplete.", nameof(before));
            if (!IsComplete(after)) throw new ArgumentException("Snapshot evidence is incomplete.", nameof(after));
            if (!IsComplete(planned)) throw new ArgumentException("Generated asset plan is incomplete.", nameof(planned));

            AssetIdentity[] beforeAssets = before.related_assets;
            AssetIdentity[] afterAssets = after.related_assets;
            SceneIdentity[] beforeScenes = before.loaded_scenes;
            SceneIdentity[] afterScenes = after.loaded_scenes;
            var newlyDirty = new List<string>();
            var noLongerDirty = new List<string>();
            var actualCreated = new List<string>();
            var actualDeleted = new List<string>();

            foreach (AssetIdentity asset in afterAssets)
            {
                AssetIdentity? match = FindAsset(beforeAssets, asset);
                if (match == null) { actualCreated.Add(asset.path); if (asset.dirty) newlyDirty.Add(asset.path); continue; }
                if (asset.dirty && !match.dirty) newlyDirty.Add(asset.path);
                if (!asset.dirty && match.dirty) noLongerDirty.Add(asset.path);
            }
            foreach (AssetIdentity asset in beforeAssets)
            {
                if (FindAsset(afterAssets, asset) == null) { actualDeleted.Add(asset.path); if (asset.dirty) noLongerDirty.Add(asset.path); }
            }

            var newlyDirtyScenes = new List<string>();
            var noLongerDirtyScenes = new List<string>();
            foreach (SceneIdentity scene in afterScenes)
            {
                SceneIdentity? match = FindScene(beforeScenes, scene);
                if (match == null) { if (scene.dirty) newlyDirtyScenes.Add(scene.path); continue; }
                if (scene.dirty && !match.dirty) newlyDirtyScenes.Add(scene.path);
            }
            foreach (SceneIdentity scene in beforeScenes)
            {
                SceneIdentity? match = FindScene(afterScenes, scene);
                if (scene.dirty && (match == null || !match.dirty)) noLongerDirtyScenes.Add(scene.path);
            }

            var delta = new Delta
            {
                newly_dirty_paths = SortedDistinct(newlyDirty), no_longer_dirty_paths = SortedDistinct(noLongerDirty),
                newly_dirty_scene_paths = SortedDistinct(newlyDirtyScenes), no_longer_dirty_scene_paths = SortedDistinct(noLongerDirtyScenes),
                planned_created_paths = SortedDistinct(planned.planned_created_paths), planned_deleted_paths = SortedDistinct(planned.planned_deleted_paths),
                actual_created_paths = SortedDistinct(actualCreated), actual_deleted_paths = SortedDistinct(actualDeleted),
                unrelated_dirty_paths_before = SortedDistinct(before.project_dirty_paths), unrelated_dirty_paths_after = SortedDistinct(after.project_dirty_paths),
            };
            AddAttributionUnknown(delta.attribution_unknown, beforeAssets, out delta.attribution_unknown);
            AddAttributionUnknown(delta.attribution_unknown, afterAssets, out delta.attribution_unknown);
            AddAttributionUnknown(delta.attribution_unknown, beforeScenes, out delta.attribution_unknown);
            AddAttributionUnknown(delta.attribution_unknown, afterScenes, out delta.attribution_unknown);
            return delta;
        }

        internal static Classification Classify(bool compileSucceeded, Delta? delta)
        {
            if (delta == null) throw new ArgumentNullException(nameof(delta));
            if (!IsComplete(delta)) throw new ArgumentException("Delta evidence is incomplete.", nameof(delta));
            return new Classification
            {
                severity = !compileSucceeded ? "error" : HasSideEffects(delta) ? "warning" : "info",
                delta = delta,
            };
        }


        internal static bool GeneratedAssetOutcomeMatchesPlan(Delta? delta)
        {
            if (delta == null) throw new ArgumentNullException(nameof(delta));
            if (!IsComplete(delta))
                throw new ArgumentException(
                    "Delta evidence is incomplete.",
                    nameof(delta));
            return SamePaths(
                    delta.planned_created_paths,
                    delta.actual_created_paths)
                && SamePaths(
                    delta.planned_deleted_paths,
                    delta.actual_deleted_paths);
        }


        internal static bool GeneratedAssetOutcomeMismatchRequiresFailure(
            bool compileSucceeded,
            Delta? delta)
        {
            return compileSucceeded
                && !GeneratedAssetOutcomeMatchesPlan(delta);
        }

        private static PreflightDecision Denied(
            string code,
            AssetIdentity[]? assets = null,
            string[]? paths = null)
        {
            return new PreflightDecision
            {
                code = code,
                offending_assets = assets ?? Array.Empty<AssetIdentity>(),
                offending_paths = paths ?? Array.Empty<string>(),
            };
        }

        private static AssetIdentity[] DirtyCompileAssets(AssetIdentity[] assets)
        {
            var dirty = new List<AssetIdentity>();
            foreach (AssetIdentity asset in assets)
            {
                if (asset.dirty && !asset.path.EndsWith(".cs", StringComparison.Ordinal)) dirty.Add(asset);
            }
            dirty.Sort(CompareAssets);
            return dirty.ToArray();
        }

        private static SceneIdentity[] DirtyScenes(SceneIdentity[] scenes)
        {
            var dirty = new List<SceneIdentity>();
            foreach (SceneIdentity scene in scenes)
            {
                if (scene.dirty) dirty.Add(scene);
            }
            dirty.Sort((left, right) => StringComparer.Ordinal.Compare(left.path, right.path));
            return dirty.ToArray();
        }

        private static string[] ScenePaths(SceneIdentity[] scenes)
        {
            var paths = new List<string>();
            foreach (SceneIdentity scene in scenes) paths.Add(scene.path);
            return SortedDistinct(paths);
        }

        private static AssetIdentity? FindAsset(AssetIdentity[] candidates, AssetIdentity target)
        {
            foreach (AssetIdentity candidate in candidates)
                if (SameAssetIdentity(candidate, target)) return candidate;
            return null;
        }

        private static SceneIdentity? FindScene(SceneIdentity[] candidates, SceneIdentity target)
        {
            foreach (SceneIdentity candidate in candidates)
                if (string.Equals(candidate.path, target.path, StringComparison.Ordinal)
                    && candidate.handle == target.handle) return candidate;
            return null;
        }

        private static bool SameAssetIdentity(AssetIdentity left, AssetIdentity right)
        {
            return string.Equals(left.guid, right.guid, StringComparison.Ordinal)
                   && left.local_file_id == right.local_file_id
                   && string.Equals(left.path, right.path, StringComparison.Ordinal)
                   && string.Equals(left.type, right.type, StringComparison.Ordinal);
        }

        private static int CompareAssets(AssetIdentity left, AssetIdentity right)
        {
            int byPath = StringComparer.Ordinal.Compare(left.path, right.path);
            if (byPath != 0) return byPath;
            int byGuid = StringComparer.Ordinal.Compare(left.guid, right.guid);
            if (byGuid != 0) return byGuid;
            int byFileId = left.local_file_id.CompareTo(right.local_file_id);
            if (byFileId != 0) return byFileId;
            return StringComparer.Ordinal.Compare(left.type, right.type);
        }

        private static void AddAttributionUnknown(
            string[] existing,
            AssetIdentity[] assets,
            out string[] result)
        {
            var values = new List<string>(existing ?? Array.Empty<string>());
            foreach (AssetIdentity asset in assets)
            {
                if (asset.dirty) values.AddRange(asset.attribution_unknown ?? Array.Empty<string>());
            }
            result = SortedDistinct(values);
        }

        private static void AddAttributionUnknown(
            string[] existing,
            SceneIdentity[] scenes,
            out string[] result)
        {
            var values = new List<string>(existing ?? Array.Empty<string>());
            foreach (SceneIdentity scene in scenes)
            {
                if (scene.dirty) values.AddRange(scene.attribution_unknown ?? Array.Empty<string>());
            }
            result = SortedDistinct(values);
        }

        private static string[] SortedDistinct(IEnumerable<string> values)
        {
            var unique = new HashSet<string>(StringComparer.Ordinal);
            if (values != null)
            {
                foreach (string value in values)
                {
                    if (value != null) unique.Add(value);
                }
            }
            var result = new List<string>(unique);
            result.Sort(StringComparer.Ordinal);
            return result.ToArray();
        }

        private static bool IsComplete(Snapshot? snapshot)
        {
            return snapshot != null && NoEmptyStrings(snapshot.prefab_repair_paths)
                   && IsComplete(snapshot.related_assets) && IsComplete(snapshot.loaded_scenes)
                   && IsComplete(snapshot.generated_asset_plan)
                   && NoEmptyStrings(snapshot.project_dirty_paths);
        }

        private static bool IsComplete(GeneratedAssetPlan? plan)
        {
            return plan != null && NoEmptyStrings(plan.planned_created_paths) && NoEmptyStrings(plan.planned_deleted_paths);
        }

        private static bool IsComplete(Delta? delta)
        {
            return delta != null && NoEmptyStrings(delta.newly_dirty_paths) && NoEmptyStrings(delta.no_longer_dirty_paths)
                   && NoEmptyStrings(delta.newly_dirty_scene_paths) && NoEmptyStrings(delta.no_longer_dirty_scene_paths)
                   && NoEmptyStrings(delta.planned_created_paths) && NoEmptyStrings(delta.planned_deleted_paths)
                   && NoEmptyStrings(delta.actual_created_paths) && NoEmptyStrings(delta.actual_deleted_paths)
                   && NoEmptyStrings(delta.unrelated_dirty_paths_before) && NoEmptyStrings(delta.unrelated_dirty_paths_after)
                   && NoEmptyStrings(delta.attribution_unknown);
        }

        private static bool IsComplete(AssetIdentity[]? assets)
        {
            if (assets == null) return false;
            foreach (AssetIdentity? asset in assets)
            {
                if (asset == null || string.IsNullOrEmpty(asset.guid) || string.IsNullOrEmpty(asset.path)
                    || string.IsNullOrEmpty(asset.type) || !NoEmptyStrings(asset.attribution_unknown)) return false;
            }
            return true;
        }

        private static bool IsComplete(SceneIdentity[]? scenes)
        {
            if (scenes == null) return false;
            foreach (SceneIdentity? scene in scenes)
            {
                if (scene == null || string.IsNullOrEmpty(scene.path) || !NoEmptyStrings(scene.attribution_unknown)) return false;
            }
            return true;
        }

        private static bool NoEmptyStrings(string[]? values)
        {
            if (values == null) return false;
            foreach (string value in values) if (string.IsNullOrEmpty(value)) return false;
            return true;
        }

        private static bool SamePaths(string[] left, string[] right)
        {
            if (left.Length != right.Length) return false;
            for (int index = 0; index < left.Length; index++)
            {
                if (!string.Equals(left[index], right[index], StringComparison.Ordinal)) return false;
            }
            return true;
        }

        private static bool HasSideEffects(Delta delta)
        {
            return delta.newly_dirty_paths.Length != 0 || delta.no_longer_dirty_paths.Length != 0
                   || delta.newly_dirty_scene_paths.Length != 0 || delta.no_longer_dirty_scene_paths.Length != 0
                   || delta.planned_created_paths.Length != 0 || delta.planned_deleted_paths.Length != 0
                   || delta.actual_created_paths.Length != 0 || delta.actual_deleted_paths.Length != 0
                   || delta.attribution_unknown.Length != 0
                   || !SamePaths(delta.unrelated_dirty_paths_before, delta.unrelated_dirty_paths_after);
        }
    }
}
