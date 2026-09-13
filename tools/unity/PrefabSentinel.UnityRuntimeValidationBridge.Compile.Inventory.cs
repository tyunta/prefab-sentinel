#if UNITY_EDITOR
using System;
using System.Collections.Generic;
using UnityEditor;
using UnityEngine;
using Object = UnityEngine.Object;

namespace PrefabSentinel
{
    public static partial class UnityRuntimeValidationBridge
    {
        internal static CompilePreflightResult CaptureCompilePreflight(
            RuntimeRequest request)
        {
            var result = new CompilePreflightResult
            {
                InventoryStable = request != null,
            };
            Type programAssetType = FindType(
                "UdonSharp.UdonSharpProgramAsset, UdonSharp.Editor");
            Type udonProgramAssetType = FindType(
                "VRC.Udon.Editor.ProgramSources.UdonProgramAsset, VRC.Udon.Editor");
            Type serializedProgramAssetType = FindType(
                "VRC.Udon.ProgramSources.SerializedUdonProgramAsset, VRC.Udon");
            result.ProgramAssetType = programAssetType;
            result.SerializedProgramAssetType = serializedProgramAssetType;
            if (programAssetType == null || udonProgramAssetType == null
                || serializedProgramAssetType == null)
            {
                MarkInventoryUnstable(
                    result,
                    "type_resolution",
                    "UdonSharpProgramAsset, UdonProgramAsset, or SerializedUdonProgramAsset was not found.");
                result.Before = CaptureCompileSnapshot(result, true);
                return result;
            }

            CaptureProgramSearch(
                AssetDatabase.FindAssets("t:UdonSharpProgramAsset"),
                programAssetType,
                programAssetType,
                true,
                result,
                out Object[] programs,
                out RuntimeCompileAudit.AssetIdentity[] exactIdentities);
            result.ProgramAssets = programs;

            CaptureProgramSearch(
                AssetDatabase.FindAssets("t:UdonProgramAsset"),
                udonProgramAssetType,
                programAssetType,
                false,
                result,
                out _,
                out RuntimeCompileAudit.AssetIdentity[] broadIdentities);
            RuntimeCompileAudit.AssetIdentity[] loadedIdentities =
                CaptureLoadedPrograms(programAssetType, result);
            if (!RuntimeCompileAudit.InventorySetsAgree(
                    exactIdentities,
                    broadIdentities)
                || !RuntimeCompileAudit.InventorySetsAgree(
                    exactIdentities,
                    loadedIdentities))
            {
                MarkInventoryUnstable(
                    result,
                    "inventory_disagreement",
                    "Exact, base-type, and loaded persistent program sets differ.");
            }

            CaptureGeneratedAssetPlan(result, exactIdentities);
            result.PrefabRepairPaths = DetectPrefabRepairs(result);
            result.GeneratedPlan = new RuntimeCompileAudit.GeneratedAssetPlan
            {
                planned_created_paths =
                    SortedDistinct(result.GeneratedPlan.planned_created_paths),
                planned_deleted_paths =
                    SortedDistinct(result.GeneratedPlan.planned_deleted_paths),
            };
            result.Before = CaptureCompileSnapshot(result, true);
            result.Before.inventory_stable = result.InventoryStable;
            result.Before.prefab_repair_paths = result.PrefabRepairPaths;
            result.Before.generated_asset_plan = result.GeneratedPlan;
            return result;
        }

        private static void CaptureProgramSearch(
            string[] guids,
            Type loadType,
            Type exactRuntimeType,
            bool requireEveryAsset,
            CompilePreflightResult result,
            out Object[] assets,
            out RuntimeCompileAudit.AssetIdentity[] identities)
        {
            var foundAssets = new List<Object>();
            var foundIdentities =
                new List<RuntimeCompileAudit.AssetIdentity>();
            foreach (string searchGuid in guids ?? Array.Empty<string>())
            {
                string path = string.IsNullOrEmpty(searchGuid)
                    ? string.Empty
                    : AssetDatabase.GUIDToAssetPath(searchGuid);
                Object asset = string.IsNullOrEmpty(path)
                    ? null
                    : AssetDatabase.LoadAssetAtPath(path, loadType);
                if (asset == null)
                {
                    MarkInventoryUnstable(
                        result,
                        "program_load",
                        $"Program candidate '{searchGuid}' could not be loaded.");
                    continue;
                }
                if (asset.GetType() != exactRuntimeType)
                {
                    if (requireEveryAsset)
                    {
                        MarkInventoryUnstable(
                            result,
                            "program_runtime_type",
                            $"Program candidate '{path}' has an unexpected type.");
                    }
                    continue;
                }
                if (!TryCaptureIdentity(
                    asset,
                    path,
                    searchGuid,
                    true,
                    out RuntimeCompileAudit.AssetIdentity identity,
                    out string error))
                {
                    MarkInventoryUnstable(result, "program_identity", error);
                    continue;
                }
                foundAssets.Add(asset);
                foundIdentities.Add(identity);
            }
            if (HasDuplicateIdentityFacts(foundIdentities))
            {
                MarkInventoryUnstable(
                    result,
                    "duplicate_program_identity",
                    "Program GUID/path/local identity is duplicated.");
            }
            assets = foundAssets.ToArray();
            identities = foundIdentities.ToArray();
        }

        private static RuntimeCompileAudit.AssetIdentity[]
            CaptureLoadedPrograms(
                Type programAssetType,
                CompilePreflightResult result)
        {
            var identities =
                new List<RuntimeCompileAudit.AssetIdentity>();
            foreach (Object asset
                in Resources.FindObjectsOfTypeAll(programAssetType))
            {
                if (asset == null
                    || asset.GetType() != programAssetType
                    || !EditorUtility.IsPersistent(asset))
                {
                    continue;
                }
                if (TryCaptureIdentity(
                    asset,
                    null,
                    null,
                    true,
                    out RuntimeCompileAudit.AssetIdentity identity,
                    out string error))
                {
                    identities.Add(identity);
                }
                else
                {
                    MarkInventoryUnstable(
                        result,
                        "loaded_program_identity",
                        error);
                }
            }
            if (HasDuplicateIdentityFacts(identities))
            {
                MarkInventoryUnstable(
                    result,
                    "duplicate_loaded_program_identity",
                    "Loaded program identity is duplicated.");
            }
            return identities.ToArray();
        }

        private static void CaptureGeneratedAssetPlan(
            CompilePreflightResult result,
            RuntimeCompileAudit.AssetIdentity[] programIdentities)
        {
            var canonicalPaths = new List<string>();
            var existingPaths = new List<string>();
            var created = new List<string>();
            var deleted = new List<string>();
            for (int index = 0;
                index < result.ProgramAssets.Length;
                index++)
            {
                Object program = result.ProgramAssets[index];
                string guid = programIdentities[index].guid;
                string canonicalPath =
                    RuntimeCompileAudit.CanonicalGeneratedAssetPath(guid);
                AddUniquePath(canonicalPaths, canonicalPath);
                Object canonicalOccupant =
                    AssetDatabase.LoadMainAssetAtPath(canonicalPath);
                Object canonicalTyped = AssetDatabase.LoadAssetAtPath(
                    canonicalPath,
                    result.SerializedProgramAssetType);
                if (canonicalOccupant != null)
                    AddUniquePath(existingPaths, canonicalPath);
                if (canonicalOccupant != null && canonicalTyped == null)
                    result.WrongTypeCanonicalPathsBefore.Add(canonicalPath);

                SerializedProperty generatedProperty =
                    new SerializedObject(program).FindProperty(
                        "serializedUdonProgramAsset");
                if (generatedProperty == null)
                {
                    MarkInventoryUnstable(
                        result,
                        "generated_reference_surface",
                        $"serializedUdonProgramAsset is unavailable on '{programIdentities[index].path}'.");
                    continue;
                }
                Object referenced = generatedProperty.objectReferenceValue;
                string referencedName = referenced == null
                    ? string.Empty
                    : referenced.name;
                string referencedPath = referenced == null
                    ? string.Empty
                    : AssetDatabase.GetAssetPath(referenced);
                if (referenced != null
                    && string.IsNullOrEmpty(referencedPath))
                {
                    MarkInventoryUnstable(
                        result,
                        "generated_reference_identity",
                        $"Generated reference on '{programIdentities[index].path}' has no path.");
                    continue;
                }
                if (referenced != null
                    && string.Equals(
                        referencedPath,
                        canonicalPath,
                        StringComparison.Ordinal)
                    && canonicalTyped != referenced)
                {
                    MarkInventoryUnstable(
                        result,
                        "canonical_generated_disagreement",
                        $"Canonical generated asset '{canonicalPath}' resolves to another typed object.");
                }

                RuntimeCompileAudit.GeneratedAssetPlan plan =
                    RuntimeCompileAudit.PlanGeneratedAsset(
                        guid,
                        referenced != null,
                        referencedName,
                        referencedPath,
                        canonicalOccupant != null,
                        canonicalTyped != null);
                foreach (string path in plan.planned_created_paths)
                    AddUniquePath(created, path);
                foreach (string path in plan.planned_deleted_paths)
                {
                    AddUniquePath(deleted, path);
                    if (AssetDatabase.LoadMainAssetAtPath(path) != null)
                        AddUniquePath(existingPaths, path);
                }
                if (canonicalTyped != null)
                {
                    int generatedId = canonicalTyped.GetInstanceID();
                    if (result.CanonicalProgramByGeneratedInstanceId
                        .ContainsKey(generatedId))
                    {
                        MarkInventoryUnstable(
                            result,
                            "duplicate_generated_mapping",
                            $"Generated asset '{canonicalPath}' maps to multiple programs.");
                    }
                    else
                    {
                        result.CanonicalProgramByGeneratedInstanceId.Add(
                            generatedId,
                            program);
                    }
                }
            }
            result.CanonicalGeneratedPaths = canonicalPaths.ToArray();
            result.ExistingGeneratedPathsBefore = existingPaths.ToArray();
            result.GeneratedPlan = new RuntimeCompileAudit.GeneratedAssetPlan
            {
                planned_created_paths = created.ToArray(),
                planned_deleted_paths = deleted.ToArray(),
            };
        }

        private static bool TryCaptureIdentity(
            Object asset,
            string expectedPath,
            string expectedGuid,
            bool markDirtyAttribution,
            out RuntimeCompileAudit.AssetIdentity identity,
            out string error)
        {
            identity = null;
            error = string.Empty;
            if (asset == null
                || !AssetDatabase.TryGetGUIDAndLocalFileIdentifier(
                    asset,
                    out string guid,
                    out long localFileId))
            {
                error = "Asset GUID/local file ID is unavailable.";
                return false;
            }
            string path = AssetDatabase.GetAssetPath(asset);
            string type = asset.GetType().FullName;
            if (string.IsNullOrEmpty(guid)
                || string.IsNullOrEmpty(path)
                || string.IsNullOrEmpty(type)
                || (!string.IsNullOrEmpty(expectedPath)
                    && !string.Equals(
                        expectedPath,
                        path,
                        StringComparison.Ordinal))
                || (!string.IsNullOrEmpty(expectedGuid)
                    && !string.Equals(
                        expectedGuid,
                        guid,
                        StringComparison.Ordinal)))
            {
                error = $"Asset identity disagrees for '{path}'.";
                return false;
            }
            bool dirty = EditorUtility.IsDirty(asset);
            identity = new RuntimeCompileAudit.AssetIdentity
            {
                guid = guid,
                local_file_id = localFileId,
                path = path,
                type = type,
                dirty = dirty,
                attribution_unknown =
                    markDirtyAttribution && dirty
                        ? new[] { $"dirty_asset_before_compile:{path}" }
                        : Array.Empty<string>(),
            };
            return true;
        }

        private static bool HasDuplicateIdentityFacts(
            List<RuntimeCompileAudit.AssetIdentity> identities)
        {
            var paths = new HashSet<string>(StringComparer.Ordinal);
            var guids = new HashSet<string>(StringComparer.Ordinal);
            var objects = new HashSet<string>(StringComparer.Ordinal);
            foreach (RuntimeCompileAudit.AssetIdentity identity in identities)
            {
                if (!paths.Add(identity.path)
                    || !guids.Add(identity.guid)
                    || !objects.Add(
                        $"{identity.guid}\u001f{identity.local_file_id}"))
                {
                    return true;
                }
            }
            return false;
        }

        private static void MarkInventoryUnstable(
            CompilePreflightResult result,
            string detail,
            string evidence)
        {
            result.InventoryStable = false;
            result.Diagnostics.Add(new RuntimeDiagnostic
            {
                location = "validate_runtime.compile.preflight",
                detail = detail,
                evidence = evidence ?? string.Empty,
            });
        }

        private static string[] SortedDistinct(string[] values)
        {
            var unique = new HashSet<string>(
                values ?? Array.Empty<string>(),
                StringComparer.Ordinal);
            var sorted = new List<string>(unique);
            sorted.Sort(StringComparer.Ordinal);
            return sorted.ToArray();
        }
    }
}
#endif
