#if UNITY_EDITOR
using System;
using System.Collections.Generic;
using System.Reflection;
using UnityEditor;

namespace PrefabSentinel
{
    public static partial class UnityRuntimeValidationBridge
    {
        internal sealed class CompilePreflightResult
        {
            internal bool InventoryStable;
            internal RuntimeCompileAudit.Snapshot Before =
                new RuntimeCompileAudit.Snapshot();
            internal RuntimeCompileAudit.GeneratedAssetPlan GeneratedPlan =
                new RuntimeCompileAudit.GeneratedAssetPlan();
            internal string[] PrefabRepairPaths = Array.Empty<string>();
            internal Type ProgramAssetType;
            internal Type SerializedProgramAssetType;
            internal UnityEngine.Object[] ProgramAssets =
                Array.Empty<UnityEngine.Object>();
            internal string[] CanonicalGeneratedPaths = Array.Empty<string>();
            internal string[] ExistingGeneratedPathsBefore =
                Array.Empty<string>();
            internal readonly HashSet<string> WrongTypeCanonicalPathsBefore =
                new HashSet<string>(StringComparer.Ordinal);
            internal readonly Dictionary<int, UnityEngine.Object>
                CanonicalProgramByGeneratedInstanceId =
                    new Dictionary<int, UnityEngine.Object>();
            internal readonly List<RuntimeDiagnostic> Diagnostics =
                new List<RuntimeDiagnostic>();
        }

        internal static RuntimeCompileReport ExecuteAuthorizedCompile(
            RuntimeRequest request,
            CompilePreflightResult preflight)
        {
            var diagnostics = new List<RuntimeDiagnostic>(preflight.Diagnostics);
            var compileLogs = new List<CapturedUdonSharpCompileLog>();
            UnityEngine.Application.LogCallback compileLogHandler =
                (condition, stackTrace, logType) =>
                {
                    if (compileLogs.Count
                            >= MaxCapturedUdonSharpCompileErrors
                        || (logType != UnityEngine.LogType.Error
                            && logType != UnityEngine.LogType.Exception
                            && logType != UnityEngine.LogType.Assert))
                    {
                        return;
                    }
                    compileLogs.Add(new CapturedUdonSharpCompileLog
                    {
                        condition = TruncateCompileDiagnosticText(condition),
                        stackTrace =
                            TruncateCompileDiagnosticText(stackTrace),
                    });
                };
            var report = new RuntimeCompileReport
            {
                before = preflight.Before,
                generated_assets = GeneratedAssetReportFromPlan(preflight.GeneratedPlan),
                program_count = preflight.ProgramAssets.Length,
            };
            bool compileSucceeded = false;
            bool compileStatusObserved = false;
            bool afterCaptureComplete = true;
            RuntimeCompileAudit.Snapshot after;

            try
            {
                MethodInfo compileAllPrograms = preflight.ProgramAssetType?.GetMethod(
                    "CompileAllCsPrograms",
                    BindingFlags.Public | BindingFlags.Static,
                    null,
                    new[] { typeof(bool), typeof(bool) },
                    null);
                MethodInfo anyCompileErrors = preflight.ProgramAssetType?.GetMethod(
                    "AnyUdonSharpScriptHasError",
                    BindingFlags.Public | BindingFlags.Static);
                if (compileAllPrograms == null || anyCompileErrors == null)
                {
                    throw new MissingMethodException(
                        "Required UdonSharp compile APIs were not found.");
                }

                Type compilerType = FindType(
                    "UdonSharp.Compiler.UdonSharpCompilerV1, UdonSharp.Editor");
                MethodInfo waitForCompile = compilerType?.GetMethod(
                    "WaitForCompile",
                    BindingFlags.Public | BindingFlags.NonPublic
                        | BindingFlags.Static);

                UnityEngine.Application.logMessageReceived += compileLogHandler;
                try
                {
                    report.executed = true;
                    compileAllPrograms.Invoke(
                        null,
                        new object[] { true, true });
                    waitForCompile?.Invoke(null, null);
                    bool compileHasErrors = Convert.ToBoolean(
                        anyCompileErrors.Invoke(null, null));
                    compileStatusObserved = true;
                    compileSucceeded = !compileHasErrors;
                }
                finally
                {
                    UnityEngine.Application.logMessageReceived -= compileLogHandler;
                }
            }
            catch (Exception ex)
            {
                Exception inner =
                    (ex as TargetInvocationException)?.InnerException ?? ex;
                diagnostics.Add(new RuntimeDiagnostic
                {
                    location = "validate_runtime.compile",
                    detail = "exception",
                    evidence = inner.ToString(),
                });
            }
            finally
            {
                try
                {
                    after = CaptureCompileSnapshot(preflight);
                }
                catch (Exception ex)
                {
                    afterCaptureComplete = false;
                    diagnostics.Add(new RuntimeDiagnostic
                    {
                        location = "validate_runtime.compile.after",
                        detail = "snapshot_exception",
                        evidence = ex.ToString(),
                    });
                    after = IncompleteAfterSnapshot(preflight);
                }
            }

            if (compileStatusObserved && !compileSucceeded)
            {
                AppendUdonSharpCompileDiagnostics(
                    preflight,
                    compileLogs,
                    diagnostics);
            }

            RuntimeCompileAudit.Delta delta;
            try
            {
                delta = RuntimeCompileAudit.Diff(
                    preflight.Before,
                    after,
                    preflight.GeneratedPlan);
            }
            catch (Exception ex)
            {
                diagnostics.Add(new RuntimeDiagnostic
                {
                    location = "validate_runtime.compile.delta",
                    detail = "delta_exception",
                    evidence = ex.ToString(),
                });
                delta = PartialDelta(preflight.GeneratedPlan);
                compileSucceeded = false;
            }

            if (!afterCaptureComplete)
            {
                delta.attribution_unknown = MergePaths(
                    delta.attribution_unknown,
                    new[] { "post_compile_snapshot_incomplete" });
                compileSucceeded = false;
            }
            try
            {
                PopulateActualGeneratedChanges(preflight, delta);
            }
            catch (Exception ex)
            {
                diagnostics.Add(new RuntimeDiagnostic
                {
                    location = "validate_runtime.compile.generated_assets",
                    detail = "existence_probe_exception",
                    evidence = ex.ToString(),
                });
                delta.attribution_unknown = MergePaths(
                    delta.attribution_unknown,
                    new[] { "generated_asset_delta_incomplete" });
                compileSucceeded = false;
            }

            RuntimeCompileAudit.Classification classification =
                RuntimeCompileAudit.Classify(compileSucceeded, delta);
            report.success = compileSucceeded;
            report.severity = classification.severity;
            report.code = compileSucceeded
                ? "RUN_COMPILE_OK"
                : "RUN_COMPILE_FAILED";
            report.after = after;
            report.delta = classification.delta;
            report.generated_assets =
                GeneratedAssetReportFromDelta(classification.delta);
            report.diagnostics = diagnostics.ToArray();
            ApplyGeneratedAssetOutcomeValidation(report);
            return report;
        }

        internal static RuntimeResponse BuildCompilePreflightFailure(
            RuntimeRequest request,
            CompilePreflightResult preflight,
            string evidence)
        {
            preflight.InventoryStable = false;
            preflight.Before.inventory_stable = false;
            preflight.Diagnostics.Add(new RuntimeDiagnostic
            {
                location = "validate_runtime.compile.preflight",
                detail = "indeterminate",
                evidence = evidence ?? string.Empty,
            });
            return BuildCompileResponse(
                request,
                new RuntimeCompileReport
                {
                    code = "UDON_COMPILE_PREFLIGHT_INDETERMINATE",
                    severity = "error",
                    before = preflight.Before,
                    generated_assets = GeneratedAssetReportFromPlan(preflight.GeneratedPlan),
                    diagnostics = preflight.Diagnostics.ToArray(),
                });
        }

        internal static RuntimeResponse BuildCompilePreflightDenied(
            RuntimeRequest request,
            CompilePreflightResult preflight,
            RuntimeCompileAudit.PreflightDecision decision)
        {
            var diagnostics = new List<RuntimeDiagnostic>(preflight.Diagnostics);
            foreach (RuntimeCompileAudit.AssetIdentity asset
                in decision.offending_assets)
            {
                diagnostics.Add(new RuntimeDiagnostic
                {
                    path = asset.path,
                    location = "validate_runtime.compile.preflight",
                    detail = decision.code,
                    evidence =
                        $"{asset.guid}:{asset.local_file_id}:{asset.type}",
                });
            }
            foreach (string path in decision.offending_paths)
            {
                diagnostics.Add(new RuntimeDiagnostic
                {
                    path = path,
                    location = "validate_runtime.compile.preflight",
                    detail = decision.code,
                    evidence = path,
                });
            }
            var report = new RuntimeCompileReport
            {
                code = decision.code,
                severity = "error",
                program_count = string.Equals(
                    decision.code,
                    "UDON_COMPILE_PREFLIGHT_INDETERMINATE",
                    StringComparison.Ordinal)
                        ? 0
                        : preflight.ProgramAssets.Length,
                before = preflight.Before,
                generated_assets = GeneratedAssetReportFromPlan(preflight.GeneratedPlan),
                diagnostics = diagnostics.ToArray(),
            };
            report.delta.planned_created_paths =
                preflight.GeneratedPlan.planned_created_paths;
            report.delta.planned_deleted_paths =
                preflight.GeneratedPlan.planned_deleted_paths;
            return BuildCompileResponse(request, report);
        }

        internal static RuntimeResponse BuildCompileResponse(
            RuntimeRequest request,
            RuntimeCompileReport report)
        {
            RuntimeData data = BuildData(
                request,
                readOnly: !report.executed,
                executed: report.executed,
                udonProgramCount: report.program_count);
            data.compile = report;
            return new RuntimeResponse
            {
                success = report.success,
                severity = report.severity,
                code = report.code,
                message = report.success
                    ? "UdonSharp compile transaction completed."
                    : "UdonSharp compile transaction did not complete successfully.",
                data = data,
                diagnostics = report.diagnostics,
            };
        }

        private static RuntimeCompileAudit.Snapshot IncompleteAfterSnapshot(
            CompilePreflightResult preflight)
        {
            return RuntimeCompileAudit.SnapshotAfterCaptureFailure(
                preflight.Before,
                "post_compile_snapshot_incomplete");
        }

        private static RuntimeCompileAudit.Delta PartialDelta(
            RuntimeCompileAudit.GeneratedAssetPlan plan)
        {
            return new RuntimeCompileAudit.Delta
            {
                planned_created_paths =
                    plan?.planned_created_paths ?? Array.Empty<string>(),
                planned_deleted_paths =
                    plan?.planned_deleted_paths ?? Array.Empty<string>(),
                attribution_unknown = new[] { "compile_delta_incomplete" },
            };
        }

        private static void PopulateActualGeneratedChanges(
            CompilePreflightResult preflight,
            RuntimeCompileAudit.Delta delta)
        {
            var before = new HashSet<string>(
                preflight.ExistingGeneratedPathsBefore,
                StringComparer.Ordinal);
            var created = new List<string>(delta.actual_created_paths);
            foreach (string path
                in preflight.GeneratedPlan.planned_created_paths)
            {
                bool expectedTypeExists =
                    preflight.SerializedProgramAssetType != null
                    && AssetDatabase.LoadAssetAtPath(
                        path,
                        preflight.SerializedProgramAssetType) != null;
                if (expectedTypeExists
                    && (!before.Contains(path)
                        || preflight.WrongTypeCanonicalPathsBefore
                            .Contains(path)))
                {
                    AddUniquePath(created, path);
                }
            }

            var deleted = new List<string>(delta.actual_deleted_paths);
            foreach (string path
                in preflight.GeneratedPlan.planned_deleted_paths)
            {
                bool wrongTypeWasReplaced =
                    preflight.WrongTypeCanonicalPathsBefore.Contains(path)
                    && preflight.SerializedProgramAssetType != null
                    && AssetDatabase.LoadAssetAtPath(
                        path,
                        preflight.SerializedProgramAssetType) != null;
                if (before.Contains(path)
                    && (AssetDatabase.LoadMainAssetAtPath(path) == null
                        || wrongTypeWasReplaced))
                {
                    AddUniquePath(deleted, path);
                }
            }
            created.Sort(StringComparer.Ordinal);
            deleted.Sort(StringComparer.Ordinal);
            delta.actual_created_paths = created.ToArray();
            delta.actual_deleted_paths = deleted.ToArray();
        }

        private static string[] MergePaths(string[] left, string[] right)
        {
            var merged = new List<string>(left ?? Array.Empty<string>());
            foreach (string path in right ?? Array.Empty<string>())
                AddUniquePath(merged, path);
            merged.Sort(StringComparer.Ordinal);
            return merged.ToArray();
        }

        private static void AddUniquePath(List<string> paths, string value)
        {
            if (string.IsNullOrEmpty(value) || paths.Contains(value))
                return;
            paths.Add(value);
        }
    }
}
#endif
