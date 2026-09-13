using System;

namespace PrefabSentinel
{
    // Unity-free adapter facts for exact program inventory and generated paths.
    internal static partial class RuntimeCompileAudit
    {
        internal static bool InventorySetsAgree(
            AssetIdentity[] expected,
            AssetIdentity[] observed)
        {
            if (expected == null
                || observed == null
                || expected.Length != observed.Length)
            {
                return false;
            }

            var matched = new bool[observed.Length];
            foreach (AssetIdentity expectedAsset in expected)
            {
                bool found = false;
                for (int index = 0; index < observed.Length; index++)
                {
                    if (!matched[index]
                        && expectedAsset != null
                        && observed[index] != null
                        && SameAssetIdentity(expectedAsset, observed[index]))
                    {
                        matched[index] = true;
                        found = true;
                        break;
                    }
                }
                if (!found)
                    return false;
            }
            return true;
        }

        internal static string CanonicalGeneratedAssetPath(
            string programGuid)
        {
            if (string.IsNullOrEmpty(programGuid))
            {
                throw new ArgumentException(
                    "Program GUID is required.",
                    nameof(programGuid));
            }
            return $"Assets/SerializedUdonPrograms/{programGuid}.asset";
        }

        internal static GeneratedAssetPlan PlanGeneratedAsset(
            string programGuid,
            bool hasReferencedAsset,
            string referencedName,
            string referencedPath,
            bool canonicalPathOccupied,
            bool canonicalHasExpectedType)
        {
            string canonicalPath =
                CanonicalGeneratedAssetPath(programGuid);
            if (hasReferencedAsset
                && string.Equals(
                    referencedName,
                    programGuid,
                    StringComparison.Ordinal))
            {
                return new GeneratedAssetPlan();
            }

            string referencedDeletePath = hasReferencedAsset
                ? $"Assets/SerializedUdonPrograms/{referencedName}.asset"
                : string.Empty;
            bool replacesCanonical =
                canonicalPathOccupied && !canonicalHasExpectedType;
            string[] deletedPaths;
            if (hasReferencedAsset
                && replacesCanonical
                && !string.Equals(
                    referencedDeletePath,
                    canonicalPath,
                    StringComparison.Ordinal))
            {
                deletedPaths = new[]
                {
                    canonicalPath,
                    referencedDeletePath,
                };
                Array.Sort(deletedPaths, StringComparer.Ordinal);
            }
            else if (hasReferencedAsset)
            {
                deletedPaths = new[] { referencedDeletePath };
            }
            else if (replacesCanonical)
            {
                deletedPaths = new[] { canonicalPath };
            }
            else
            {
                deletedPaths = Array.Empty<string>();
            }

            return new GeneratedAssetPlan
            {
                planned_created_paths = canonicalHasExpectedType
                    ? Array.Empty<string>()
                    : new[] { canonicalPath },
                planned_deleted_paths = deletedPaths,
            };
        }

        internal static Snapshot SnapshotAfterCaptureFailure(
            Snapshot before,
            string attribution)
        {
            if (before == null)
                throw new ArgumentNullException(nameof(before));
            if (!IsComplete(before))
                throw new ArgumentException(
                    "Snapshot evidence is incomplete.",
                    nameof(before));
            if (string.IsNullOrEmpty(attribution))
                throw new ArgumentException(
                    "Attribution is required.",
                    nameof(attribution));

            var assets = new AssetIdentity[before.related_assets.Length];
            for (int index = 0; index < assets.Length; index++)
            {
                AssetIdentity source = before.related_assets[index];
                assets[index] = new AssetIdentity
                {
                    guid = source.guid,
                    local_file_id = source.local_file_id,
                    path = source.path,
                    type = source.type,
                    dirty = source.dirty,
                    attribution_unknown = WithAttribution(
                        source.attribution_unknown,
                        attribution),
                };
            }

            var scenes = new SceneIdentity[before.loaded_scenes.Length];
            for (int index = 0; index < scenes.Length; index++)
            {
                SceneIdentity source = before.loaded_scenes[index];
                scenes[index] = new SceneIdentity
                {
                    path = source.path,
                    handle = source.handle,
                    dirty = source.dirty,
                    attribution_unknown = WithAttribution(
                        source.attribution_unknown,
                        attribution),
                };
            }

            return new Snapshot
            {
                inventory_stable = false,
                prefab_repair_paths =
                    (string[])before.prefab_repair_paths.Clone(),
                related_assets = assets,
                loaded_scenes = scenes,
                generated_asset_plan = new GeneratedAssetPlan
                {
                    planned_created_paths =
                        (string[])before.generated_asset_plan
                            .planned_created_paths.Clone(),
                    planned_deleted_paths =
                        (string[])before.generated_asset_plan
                            .planned_deleted_paths.Clone(),
                },
                project_dirty_paths =
                    (string[])before.project_dirty_paths.Clone(),
            };
        }

        private static string[] WithAttribution(
            string[] existing,
            string attribution)
        {
            if (Array.IndexOf(existing, attribution) >= 0)
                return (string[])existing.Clone();
            var result = new string[existing.Length + 1];
            Array.Copy(existing, result, existing.Length);
            result[result.Length - 1] = attribution;
            return result;
        }
    }
}
