#if UNITY_EDITOR
using System;
using System.Collections.Generic;
using System.Reflection;
using UnityEditor;
using UnityEngine;
using UnityEngine.SceneManagement;
using Object = UnityEngine.Object;

namespace PrefabSentinel
{
    public static partial class UnityRuntimeValidationBridge
    {
        private const int MaxCapturedUdonSharpCompileErrors = 64;
        private const int MaxCapturedUdonSharpCompileErrorChars = 8192;

        private sealed class CapturedUdonSharpCompileLog
        {
            internal string condition = string.Empty;
            internal string stackTrace = string.Empty;
        }

        [Serializable]
        internal sealed class GeneratedAssetReport
        {
            public string[] planned_created_paths = Array.Empty<string>();
            public string[] planned_deleted_paths = Array.Empty<string>();
            public string[] actual_created_paths = Array.Empty<string>();
            public string[] actual_deleted_paths = Array.Empty<string>();
        }

        [Serializable]
        internal sealed class RuntimeCompileReport
        {
            public bool executed;
            public bool success;
            public string severity = "info";
            public string code = string.Empty;
            public int program_count;
            public RuntimeCompileAudit.Snapshot before =
                new RuntimeCompileAudit.Snapshot();
            public RuntimeCompileAudit.Snapshot after =
                new RuntimeCompileAudit.Snapshot();
            public RuntimeCompileAudit.Delta delta =
                new RuntimeCompileAudit.Delta();
            public GeneratedAssetReport generated_assets =
                new GeneratedAssetReport();
            public RuntimeDiagnostic[] diagnostics =
                Array.Empty<RuntimeDiagnostic>();
        }

        private static string TruncateCompileDiagnosticText(string value)
        {
            string text = value ?? string.Empty;
            return text.Length <= MaxCapturedUdonSharpCompileErrorChars
                ? text
                : text.Substring(
                    0,
                    MaxCapturedUdonSharpCompileErrorChars);
        }

        private static void AppendUdonSharpCompileDiagnostics(
            CompilePreflightResult preflight,
            List<CapturedUdonSharpCompileLog> compileLogs,
            List<RuntimeDiagnostic> diagnostics)
        {
            FieldInfo sourceCsScript = preflight.ProgramAssetType?.GetField(
                "sourceCsScript",
                BindingFlags.Public | BindingFlags.Instance);
            int initialDiagnosticCount = diagnostics.Count;
            foreach (UnityEngine.Object programAsset in preflight.ProgramAssets)
            {
                if (diagnostics.Count - initialDiagnosticCount
                    >= MaxCapturedUdonSharpCompileErrors)
                {
                    break;
                }

                string programPath =
                    AssetDatabase.GetAssetPath(programAsset)
                    ?? string.Empty;
                UnityEngine.Object sourceObject =
                    sourceCsScript?.GetValue(programAsset)
                    as UnityEngine.Object;
                string sourcePath = sourceObject == null
                    ? string.Empty
                    : AssetDatabase.GetAssetPath(sourceObject)
                        ?? string.Empty;
                foreach (CapturedUdonSharpCompileLog log in compileLogs)
                {
                    if (diagnostics.Count - initialDiagnosticCount
                            >= MaxCapturedUdonSharpCompileErrors
                        || string.IsNullOrEmpty(sourcePath))
                    {
                        break;
                    }

                    string normalizedCondition =
                        (log.condition ?? string.Empty).Replace('\\', '/');
                    string normalizedStackTrace =
                        (log.stackTrace ?? string.Empty).Replace('\\', '/');
                    if (normalizedCondition.IndexOf(
                            sourcePath,
                            StringComparison.Ordinal) < 0
                        && normalizedStackTrace.IndexOf(
                            sourcePath,
                            StringComparison.Ordinal) < 0)
                    {
                        continue;
                    }

                    diagnostics.Add(new RuntimeDiagnostic
                    {
                        path = programPath,
                        location = sourcePath,
                        detail = "udonsharp_compiler_error",
                        evidence = TruncateCompileDiagnosticText(
                            log.condition
                            + (string.IsNullOrEmpty(log.stackTrace)
                                ? string.Empty
                                : "\n" + log.stackTrace)),
                    });
                }
            }

            if (diagnostics.Count != initialDiagnosticCount)
                return;

            foreach (UnityEngine.Object programAsset in preflight.ProgramAssets)
            {
                if (diagnostics.Count - initialDiagnosticCount
                    >= MaxCapturedUdonSharpCompileErrors)
                {
                    break;
                }

                string programPath =
                    AssetDatabase.GetAssetPath(programAsset)
                    ?? string.Empty;
                UnityEngine.Object sourceObject =
                    sourceCsScript?.GetValue(programAsset)
                    as UnityEngine.Object;
                string sourcePath = sourceObject == null
                    ? string.Empty
                    : AssetDatabase.GetAssetPath(sourceObject)
                        ?? string.Empty;
                diagnostics.Add(new RuntimeDiagnostic
                {
                    path = programPath,
                    location = sourcePath,
                    detail = "udonsharp_compiler_error",
                    evidence =
                        "AnyUdonSharpScriptHasError returned true without "
                        + "a captured compiler log.",
                });
            }

            if (diagnostics.Count == initialDiagnosticCount)
            {
                diagnostics.Add(new RuntimeDiagnostic
                {
                    path = "UdonSharp",
                    location = "validate_runtime.compile",
                    detail = "udonsharp_compiler_error",
                    evidence =
                        "AnyUdonSharpScriptHasError returned true without "
                        + "a discoverable program asset.",
                });
            }
        }

        private static GeneratedAssetReport GeneratedAssetReportFromPlan(
            RuntimeCompileAudit.GeneratedAssetPlan plan)
        {
            return new GeneratedAssetReport
            {
                planned_created_paths = plan.planned_created_paths,
                planned_deleted_paths = plan.planned_deleted_paths,
            };
        }

        private static GeneratedAssetReport GeneratedAssetReportFromDelta(
            RuntimeCompileAudit.Delta delta)
        {
            return new GeneratedAssetReport
            {
                planned_created_paths = delta.planned_created_paths,
                planned_deleted_paths = delta.planned_deleted_paths,
                actual_created_paths = delta.actual_created_paths,
                actual_deleted_paths = delta.actual_deleted_paths,
            };
        }

        private static void ApplyGeneratedAssetOutcomeValidation(
            RuntimeCompileReport report)
        {
            if (!RuntimeCompileAudit.GeneratedAssetOutcomeMismatchRequiresFailure(
                    report.success,
                    report.delta))
            {
                return;
            }

            var diagnostics = new List<RuntimeDiagnostic>(
                report.diagnostics ?? Array.Empty<RuntimeDiagnostic>())
            {
                new RuntimeDiagnostic
                {
                    location =
                        "validate_runtime.compile.generated_assets",
                    detail = "generated_asset_outcome_mismatch",
                    evidence =
                        "planned_created=["
                        + string.Join(",", report.delta.planned_created_paths)
                        + "]; actual_created=["
                        + string.Join(",", report.delta.actual_created_paths)
                        + "]; planned_deleted=["
                        + string.Join(",", report.delta.planned_deleted_paths)
                        + "]; actual_deleted=["
                        + string.Join(",", report.delta.actual_deleted_paths)
                        + "]",
                },
            };
            report.success = false;
            report.severity = "error";
            report.code = "UDON_GENERATED_ASSET_OUTCOME_MISMATCH";
            report.diagnostics = diagnostics.ToArray();
        }

        internal static RuntimeCompileAudit.Snapshot CaptureCompileSnapshot(
            CompilePreflightResult inventory)
        {
            return CaptureCompileSnapshot(inventory, false);
        }

        private static RuntimeCompileAudit.Snapshot CaptureCompileSnapshot(
            CompilePreflightResult inventory,
            bool markDirtyAttribution)
        {
            var related = new List<RuntimeCompileAudit.AssetIdentity>();
            bool stable = inventory.InventoryStable;
            foreach (Object program in inventory.ProgramAssets)
            {
                if (TryCaptureIdentity(
                    program,
                    null,
                    null,
                    markDirtyAttribution,
                    out RuntimeCompileAudit.AssetIdentity identity,
                    out _))
                {
                    related.Add(identity);
                }
                else
                {
                    stable = false;
                }
            }
            foreach (string path in inventory.CanonicalGeneratedPaths)
            {
                Object generated = AssetDatabase.LoadMainAssetAtPath(path);
                if (generated == null)
                    continue;
                if (TryCaptureIdentity(
                    generated,
                    path,
                    null,
                    markDirtyAttribution,
                    out RuntimeCompileAudit.AssetIdentity identity,
                    out _))
                {
                    related.Add(identity);
                }
                else
                {
                    stable = false;
                }
            }

            RuntimeCompileAudit.SceneIdentity[] scenes =
                CaptureLoadedScenes(markDirtyAttribution, ref stable);
            var relatedPaths = new HashSet<string>(StringComparer.Ordinal);
            foreach (RuntimeCompileAudit.AssetIdentity identity in related)
                relatedPaths.Add(identity.path);
            return new RuntimeCompileAudit.Snapshot
            {
                inventory_stable = stable,
                prefab_repair_paths =
                    inventory.PrefabRepairPaths ?? Array.Empty<string>(),
                related_assets = related.ToArray(),
                loaded_scenes = scenes,
                generated_asset_plan =
                    inventory.GeneratedPlan
                        ?? new RuntimeCompileAudit.GeneratedAssetPlan(),
                project_dirty_paths =
                    CaptureLoadedDirtyAssetPaths(relatedPaths),
            };
        }

        private static RuntimeCompileAudit.SceneIdentity[]
            CaptureLoadedScenes(
                bool markDirtyAttribution,
                ref bool stable)
        {
            var scenes = new List<RuntimeCompileAudit.SceneIdentity>();
            for (int index = 0; index < SceneManager.sceneCount; index++)
            {
                Scene scene = SceneManager.GetSceneAt(index);
                if (!scene.IsValid() || !scene.isLoaded)
                    continue;
                if (string.IsNullOrEmpty(scene.path))
                {
                    stable = false;
                    continue;
                }
                scenes.Add(new RuntimeCompileAudit.SceneIdentity
                {
                    path = scene.path,
                    handle = scene.handle,
                    dirty = scene.isDirty,
                    attribution_unknown =
                        markDirtyAttribution && scene.isDirty
                            ? new[] { $"dirty_scene_before_compile:{scene.path}" }
                            : Array.Empty<string>(),
                });
            }
            return scenes.ToArray();
        }

        private static string[] CaptureLoadedDirtyAssetPaths(
            HashSet<string> relatedPaths)
        {
            var paths = new List<string>();
            foreach (Object asset
                in Resources.FindObjectsOfTypeAll<Object>())
            {
                if (asset == null
                    || !EditorUtility.IsPersistent(asset)
                    || !EditorUtility.IsDirty(asset)
                    || !AssetDatabase.IsNativeAsset(asset))
                {
                    continue;
                }
                string path = AssetDatabase.GetAssetPath(asset);
                if (!string.IsNullOrEmpty(path)
                    && !relatedPaths.Contains(path))
                {
                    AddUniquePath(paths, path);
                }
            }
            paths.Sort(StringComparer.Ordinal);
            return paths.ToArray();
        }
    }
}
#endif
