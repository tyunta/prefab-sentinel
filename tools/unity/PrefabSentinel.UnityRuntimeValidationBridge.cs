using System;
using System.Collections;
using System.Collections.Generic;
using System.IO;
using System.Reflection;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.SceneManagement;

namespace PrefabSentinel
{
    /// <summary>
    /// Runtime validation bridge invoked through the resident Editor Bridge
    /// file-IPC handshake.  Reads a JSON request file, performs UdonSharp
    /// compile or ClientSim startup checks, and writes a JSON response file
    /// without exiting the editor process.
    /// </summary>
    public static class UnityRuntimeValidationBridge
    {
        public const int ProtocolVersion = 1;
        private const string DefaultProjectRootName = "project";

        /// <summary>All action strings handled by this bridge.</summary>
        public static readonly HashSet<string> SupportedActions = new HashSet<string>
        {
            "compile_udonsharp",
            "run_clientsim",
        };


        public static readonly HashSet<string> AsyncActions = new HashSet<string>
        {
            "run_clientsim",
        };

        [Serializable]
        public sealed class RuntimeRequest
        {
            public int protocol_version = 0;
            public string action = string.Empty;
            public string project_root = string.Empty;
            public string scene_path = string.Empty;
            public string profile = string.Empty;
            public int timeout_sec = 120;
            public bool confirm = false;
            public string change_reason = string.Empty;
            public bool allow_dirty_before = false;
        }

        [Serializable]
        public sealed class RuntimeDiagnostic
        {
            public string path = string.Empty;
            public string location = string.Empty;
            public string detail = string.Empty;
            public string evidence = string.Empty;
        }

        [Serializable]
        public sealed class RuntimeData
        {
            public string project_root = string.Empty;
            public string scene_path = string.Empty;
            public string profile = string.Empty;
            public int timeout_sec = 0;
            public int udon_program_count = 0;
            public bool clientsim_ready = false;
            public bool read_only = true;
            public bool executed = false;
            public ClientSimSideEffectReport side_effect_report = null;
        }

        [Serializable]
        public sealed class ClientSimSideEffectReport
        {
            public bool diff_complete = true;
            public string[] diff_warnings = Array.Empty<string>();
            public string scene_path = string.Empty;
            public string[] roots_before = Array.Empty<string>();
            public string[] roots_runtime = Array.Empty<string>();
            public string[] roots_after = Array.Empty<string>();
            public string[] hierarchy_before = Array.Empty<string>();
            public string[] hierarchy_runtime = Array.Empty<string>();
            public string[] hierarchy_after = Array.Empty<string>();
            public string[] components_before = Array.Empty<string>();
            public string[] components_runtime = Array.Empty<string>();
            public string[] components_after = Array.Empty<string>();
            public string[] added_gameobjects = Array.Empty<string>();
            public string[] removed_gameobjects = Array.Empty<string>();
            public string[] added_components = Array.Empty<string>();
            public string[] removed_components = Array.Empty<string>();
            public string[] residual_added_gameobjects = Array.Empty<string>();
            public string[] residual_removed_gameobjects = Array.Empty<string>();
            public string[] residual_added_components = Array.Empty<string>();
            public string[] residual_removed_components = Array.Empty<string>();
            public bool dirty_before = false;
            public bool dirty_runtime = false;
            public bool dirty_after = false;
            public int dirty_count_before = 0;
            public int dirty_count_runtime = 0;
            public int dirty_count_after = 0;
            public string[] asset_change_candidates = Array.Empty<string>();
        }

        [Serializable]
        internal sealed class SceneSideEffectSnapshot
        {
            public string[] Roots = Array.Empty<string>();
            public string[] Hierarchy = Array.Empty<string>();
            public string[] Components = Array.Empty<string>();
            public string[] AssetChangeCandidates = Array.Empty<string>();
            public bool Dirty;
            public int DirtyCount;
        }

        [Serializable]
        public sealed class RuntimeResponse
        {
            public bool success = false;
            public string severity = "error";
            public string code = string.Empty;
            public string message = string.Empty;
            public RuntimeData data = new RuntimeData();
            public RuntimeDiagnostic[] diagnostics = Array.Empty<RuntimeDiagnostic>();
        }

        /// <summary>
        /// File-IPC entry point invoked by the resident Editor Bridge.  The
        /// caller hands over the request and response paths; this method
        /// dispatches on the action field and returns control to the Unity
        /// main loop without terminating the editor process.
        /// </summary>
        public static void RunFromPaths(string requestPath, string responsePath)
        {
            RuntimeRequest request;
            try
            {
                string requestJson = File.ReadAllText(requestPath);
                request = JsonUtility.FromJson<RuntimeRequest>(requestJson);
            }
            catch (Exception ex)
            {
                WriteResponse(
                    responsePath,
                    BuildError(
                        code: "RUN_PROTOCOL_ERROR",
                        message: "Runtime validation request could not be read.",
                        request: new RuntimeRequest(),
                        diagnostics: new[]
                        {
                            new RuntimeDiagnostic
                            {
                                location = "request",
                                detail = "read_error",
                                evidence = ex.ToString()
                            }
                        },
                        readOnly: true,
                        executed: false
                    )
                );
                return;
            }

            if (request == null || request.protocol_version != ProtocolVersion)
            {
                WriteResponse(
                    responsePath,
                    BuildError(
                        code: "RUN_PROTOCOL_ERROR",
                        message: "Runtime validation request protocol mismatch.",
                        request: request ?? new RuntimeRequest(),
                        diagnostics: new[]
                        {
                            new RuntimeDiagnostic
                            {
                                location = "protocol_version",
                                detail = "schema_error",
                                evidence = $"expected {ProtocolVersion}, got {(request == null ? "null" : request.protocol_version.ToString())}"
                            }
                        },
                        readOnly: true,
                        executed: false
                    )
                );
                return;
            }

            if (string.Equals(request.action, "compile_udonsharp", StringComparison.Ordinal))
            {
                RuntimeResponse compileResponse = ExecuteCompile(request);
                WriteResponse(responsePath, compileResponse);
                return;
            }

            if (string.Equals(request.action, "run_clientsim", StringComparison.Ordinal))
            {
                RuntimeValidationClientSimController.Begin(request, responsePath);
                return;
            }

            WriteResponse(
                responsePath,
                BuildError(
                    code: "RUN_PROTOCOL_ERROR",
                    message: $"Unsupported runtime validation action '{request.action}'.",
                    request: request,
                    diagnostics: new[]
                    {
                        new RuntimeDiagnostic
                        {
                            location = "action",
                            detail = "schema_error",
                            evidence = request.action ?? string.Empty
                        }
                    },
                    readOnly: true,
                    executed: false
                )
            );
        }

        internal static void WriteResponse(string responsePath, RuntimeResponse response)
        {
            if (TryWriteResponseAtomically(responsePath, response))
            {
                return;
            }

            // Synchronous operations have no reload-owned lease to retry from.
            try
            {
                File.WriteAllText(responsePath, JsonUtility.ToJson(response));
            }
            catch (Exception ex)
            {
                Debug.LogWarning(
                    $"[PrefabSentinel] WriteResponse: {ex.GetType().Name}: {ex.Message}");
            }
        }

        internal static bool TryWriteResponseAtomically(
            string responsePath,
            RuntimeResponse response)
        {
            string tmpPath = responsePath + ".tmp";
            try
            {
                string json = JsonUtility.ToJson(response);
                File.WriteAllText(tmpPath, json);
                if (File.Exists(responsePath))
                {
                    File.Delete(responsePath);
                }
                File.Move(tmpPath, responsePath);
                return true;
            }
            catch (Exception ex)
            {
                Debug.LogWarning(
                    $"[PrefabSentinel] TryWriteResponseAtomically: {ex.GetType().Name}: {ex.Message}");
                try
                {
                    if (File.Exists(tmpPath))
                    {
                        File.Delete(tmpPath);
                    }
                }
                catch (Exception cleanupEx)
                {
                    Debug.LogWarning(
                        $"[PrefabSentinel] TryWriteResponseAtomically cleanup: {cleanupEx.GetType().Name}: {cleanupEx.Message}");
                }
                return false;
            }
        }

        internal static RuntimeResponse ExecuteCompile(RuntimeRequest request)
        {
            try
            {
                Type programAssetType = FindType("UdonSharp.UdonSharpProgramAsset, UdonSharp.Editor");
                if (programAssetType == null)
                {
                    return BuildSkip(
                        code: "RUN_COMPILE_SKIPPED",
                        message: "UdonSharp editor assembly was not found; compile check skipped.",
                        request: request
                    );
                }

                MethodInfo getAllPrograms = programAssetType.GetMethod("GetAllUdonSharpPrograms", BindingFlags.Public | BindingFlags.Static);
                MethodInfo compileAllPrograms = programAssetType.GetMethod("CompileAllCsPrograms", BindingFlags.Public | BindingFlags.Static);
                MethodInfo anyCompileErrors = programAssetType.GetMethod("AnyUdonSharpScriptHasError", BindingFlags.Public | BindingFlags.Static);
                if (getAllPrograms == null || compileAllPrograms == null || anyCompileErrors == null)
                {
                    return BuildSkip(
                        code: "RUN_COMPILE_SKIPPED",
                        message: "Required UdonSharp compile APIs were not found; compile check skipped.",
                        request: request
                    );
                }

                Array programs = getAllPrograms.Invoke(null, null) as Array;
                int programCount = programs == null ? 0 : programs.Length;
                if (programCount == 0)
                {
                    return BuildSuccess(
                        code: "RUN_COMPILE_OK",
                        message: "No UdonSharp programs were found; compile check completed.",
                        request: request,
                        udonProgramCount: 0
                    );
                }

                Type compilerType = FindType("UdonSharp.Compiler.UdonSharpCompilerV1, UdonSharp.Editor");
                MethodInfo waitForCompile = compilerType == null
                    ? null
                    : compilerType.GetMethod("WaitForCompile", BindingFlags.NonPublic | BindingFlags.Static);
                MethodInfo compileSync = compilerType == null
                    ? null
                    : compilerType.GetMethod("CompileSync", BindingFlags.Public | BindingFlags.Static);

                AssetDatabase.Refresh();
                compileAllPrograms.Invoke(null, new object[] { true, true });
                waitForCompile?.Invoke(null, null);

                bool hasErrors = Convert.ToBoolean(anyCompileErrors.Invoke(null, null));
                if (hasErrors && compileSync != null)
                {
                    compileSync.Invoke(null, new object[] { null });
                    hasErrors = Convert.ToBoolean(anyCompileErrors.Invoke(null, null));
                }

                AssetDatabase.Refresh();
                if (hasErrors)
                {
                    return BuildError(
                        code: "RUN_COMPILE_FAILED",
                        message: "UdonSharp compile reported errors.",
                        request: request,
                        diagnostics: Array.Empty<RuntimeDiagnostic>(),
                        readOnly: false,
                        executed: true,
                        udonProgramCount: programCount
                    );
                }

                return BuildSuccess(
                    code: "RUN_COMPILE_OK",
                    message: "UdonSharp compile completed via the Editor Bridge.",
                    request: request,
                    udonProgramCount: programCount
                );
            }
            catch (Exception ex)
            {
                Exception inner = (ex as TargetInvocationException)?.InnerException ?? ex;
                return BuildError(
                    code: "RUN_COMPILE_FAILED",
                    message: $"UdonSharp compile threw an exception: {inner.Message}",
                    request: request,
                    diagnostics: new[]
                    {
                        new RuntimeDiagnostic
                        {
                            location = "compile_udonsharp",
                            detail = "exception",
                            evidence = inner.ToString()
                        }
                    },
                    readOnly: false,
                    executed: true
                );
            }
        }

        private static RuntimeData BuildData(
            RuntimeRequest request,
            bool readOnly,
            bool executed,
            int udonProgramCount = 0,
            bool clientSimReady = false,
            ClientSimSideEffectReport sideEffectReport = null
        )
        {
            return new RuntimeData
            {
                project_root = string.IsNullOrWhiteSpace(request.project_root) ? DefaultProjectRootName : request.project_root,
                scene_path = request.scene_path ?? string.Empty,
                profile = request.profile ?? string.Empty,
                timeout_sec = request.timeout_sec,
                udon_program_count = udonProgramCount,
                clientsim_ready = clientSimReady,
                read_only = readOnly,
                executed = executed,
                side_effect_report = sideEffectReport,
            };
        }

        internal static RuntimeResponse BuildSkip(string code, string message, RuntimeRequest request)
        {
            return new RuntimeResponse
            {
                success = true,
                severity = "warning",
                code = code,
                message = message,
                data = BuildData(request, readOnly: true, executed: false),
                diagnostics = Array.Empty<RuntimeDiagnostic>(),
            };
        }

        internal static RuntimeResponse BuildSuccess(
            string code,
            string message,
            RuntimeRequest request,
            int udonProgramCount = 0,
            bool clientSimReady = false,
            ClientSimSideEffectReport sideEffectReport = null
        )
        {
            return new RuntimeResponse
            {
                success = true,
                severity = "info",
                code = code,
                message = message,
                data = BuildData(
                    request,
                    readOnly: false,
                    executed: true,
                    udonProgramCount: udonProgramCount,
                    clientSimReady: clientSimReady,
                    sideEffectReport: sideEffectReport),
                diagnostics = Array.Empty<RuntimeDiagnostic>(),
            };
        }

        internal static RuntimeResponse BuildError(
            string code,
            string message,
            RuntimeRequest request,
            RuntimeDiagnostic[] diagnostics,
            bool readOnly,
            bool executed,
            int udonProgramCount = 0,
            bool clientSimReady = false,
            ClientSimSideEffectReport sideEffectReport = null
        )
        {
            return new RuntimeResponse
            {
                success = false,
                severity = "error",
                code = code,
                message = message,
                data = BuildData(
                    request,
                    readOnly: readOnly,
                    executed: executed,
                    udonProgramCount: udonProgramCount,
                    clientSimReady: clientSimReady,
                    sideEffectReport: sideEffectReport),
                diagnostics = diagnostics ?? Array.Empty<RuntimeDiagnostic>(),
            };
        }

        internal static Type FindType(string qualifiedName)
        {
            Type direct = Type.GetType(qualifiedName, false);
            if (direct != null)
            {
                return direct;
            }

            string typeName = qualifiedName;
            string assemblyName = null;
            int separator = qualifiedName.IndexOf(',');
            if (separator >= 0)
            {
                typeName = qualifiedName.Substring(0, separator).Trim();
                assemblyName = qualifiedName.Substring(separator + 1).Trim();
            }

            foreach (Assembly assembly in AppDomain.CurrentDomain.GetAssemblies())
            {
                if (!string.IsNullOrWhiteSpace(assemblyName) && !string.Equals(assembly.GetName().Name, assemblyName, StringComparison.Ordinal))
                {
                    continue;
                }

                Type found = assembly.GetType(typeName, false);
                if (found != null)
                {
                    return found;
                }
            }

            return null;
        }

        internal static bool TryResolveSceneAssetPath(RuntimeRequest request, out string sceneAssetPath, out string error)
        {
            sceneAssetPath = string.Empty;
            error = string.Empty;
            string rawScenePath = request.scene_path ?? string.Empty;
            if (string.IsNullOrWhiteSpace(rawScenePath))
            {
                error = "scene_path is required";
                return false;
            }

            string normalized = rawScenePath.Replace('\\', '/').Trim();
            if (Path.IsPathRooted(normalized))
            {
                string fullScenePath = Path.GetFullPath(normalized);
                string fullProjectRoot = Path.GetFullPath(
                    string.IsNullOrWhiteSpace(request.project_root)
                        ? Path.Combine(Application.dataPath, "..")
                        : request.project_root
                ).TrimEnd(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar);
                if (!fullScenePath.StartsWith(fullProjectRoot + Path.DirectorySeparatorChar, StringComparison.OrdinalIgnoreCase)
                    && !string.Equals(fullScenePath, fullProjectRoot, StringComparison.OrdinalIgnoreCase))
                {
                    error = $"scene path is outside the Unity project root: '{rawScenePath}'";
                    return false;
                }

                string relative = fullScenePath.Substring(fullProjectRoot.Length).TrimStart(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar);
                normalized = relative.Replace('\\', '/');
            }

            if (!normalized.EndsWith(".unity", StringComparison.OrdinalIgnoreCase))
            {
                error = "scene_path must point to a .unity asset";
                return false;
            }

            string fullPath = Path.GetFullPath(Path.Combine(Application.dataPath, "..", normalized));
            if (!File.Exists(fullPath))
            {
                error = $"scene asset was not found: '{normalized}'";
                return false;
            }

            sceneAssetPath = normalized;
            return true;
        }

        internal static void SetFieldIfPresent(object instance, string fieldName, object value)
        {
            if (instance == null)
            {
                return;
            }

            FieldInfo field = instance.GetType().GetField(fieldName, BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic);
            if (field == null)
            {
                return;
            }

            field.SetValue(instance, value);
        }
    }

    [InitializeOnLoad]
    internal static class RuntimeValidationClientSimController
    {
        private const string OperationStateKey =
            "PrefabSentinel.RuntimeValidation.ClientSim.Operation.v1";
        private const string RestorationLeaseKey =
            "PrefabSentinel.RuntimeValidation.ClientSim.RestorationLease.v1";
        private const int ExitCleanupTimeoutSeconds = 30;
        private const string EnteringPlayModePhase = "entering_play_mode";
        private const string WaitingForClientSimPhase = "waiting_for_clientsim";
        private const string ExitingPlayModePhase = "exiting_play_mode";

        [Serializable]
        private sealed class OperationState
        {
            public UnityRuntimeValidationBridge.RuntimeRequest request =
                new UnityRuntimeValidationBridge.RuntimeRequest();
            public string responsePath = string.Empty;
            public string sceneAssetPath = string.Empty;
            public string phase = string.Empty;
            public double operationDeadline = 0;
            public double exitDeadline = 0;
            public bool exitTimeoutRecorded = false;
            public bool terminalSet = false;
            public bool terminalSuccess = false;
            public string terminalCode = string.Empty;
            public string terminalMessage = string.Empty;
            public bool terminalClientSimReady = false;
            public bool terminalReadOnly = false;
            public bool terminalExecuted = true;
            public UnityRuntimeValidationBridge.RuntimeDiagnostic[] terminalDiagnostics =
                Array.Empty<UnityRuntimeValidationBridge.RuntimeDiagnostic>();
            public UnityRuntimeValidationBridge.SceneSideEffectSnapshot beforeSnapshot;
            public UnityRuntimeValidationBridge.SceneSideEffectSnapshot runtimeSnapshot;
        }

        [Serializable]
        private sealed class RestorationLease
        {
            public bool previousStartSceneWasNull = true;
            public string previousStartSceneGuid = string.Empty;
            public string targetSceneGuid = string.Empty;
            public string targetScenePath = string.Empty;
            public string responsePath = string.Empty;
        }

        static RuntimeValidationClientSimController()
        {
            EnsureSubscribed();
            EditorApplication.delayCall += ReconcilePersistedState;
        }

        public static void Begin(
            UnityRuntimeValidationBridge.RuntimeRequest request,
            string responsePath)
        {
            string safeResponsePath = responsePath ?? string.Empty;
            if (request == null)
            {
                WriteImmediate(
                    safeResponsePath,
                    UnityRuntimeValidationBridge.BuildError(
                        code: "RUN_PROTOCOL_ERROR",
                        message: "ClientSim request is missing.",
                        request: new UnityRuntimeValidationBridge.RuntimeRequest(),
                        diagnostics: DiagFrom("request", "schema_error", "request was null"),
                        readOnly: true,
                        executed: false));
                return;
            }

            if (!string.Equals(request.profile, "clientsim", StringComparison.Ordinal)
                || !request.confirm
                || string.IsNullOrWhiteSpace(request.change_reason))
            {
                WriteImmediate(
                    safeResponsePath,
                    UnityRuntimeValidationBridge.BuildError(
                        code: "CLIENTSIM_CONFIRM_REQUIRED",
                        message: "ClientSim requires profile=clientsim, confirm=true, and a non-empty change_reason.",
                        request: request,
                        diagnostics: DiagFrom(
                            "confirm",
                            "audit_required",
                            "profile=clientsim, confirm=true, and change_reason are required"),
                        readOnly: true,
                        executed: false));
                return;
            }

            double operationDeadline =
                EditorApplication.timeSinceStartup + Math.Max(request.timeout_sec, 1);

            if (HasPersistedState())
            {
                WriteImmediate(
                    safeResponsePath,
                    UnityRuntimeValidationBridge.BuildError(
                        code: "CLIENTSIM_ALREADY_RUNNING",
                        message: "Another ClientSim validation operation already owns Play Mode cleanup.",
                        request: request,
                        diagnostics: DiagFrom("run_clientsim", "busy", "persisted operation or restoration lease exists"),
                        readOnly: true,
                        executed: false));
                return;
            }

            if (EditorApplication.isPlayingOrWillChangePlaymode)
            {
                WriteImmediate(
                    safeResponsePath,
                    UnityRuntimeValidationBridge.BuildError(
                        code: "CLIENTSIM_EDITOR_NOT_READY",
                        message: "ClientSim validation requires Unity to be in stable Edit Mode.",
                        request: request,
                        diagnostics: DiagFrom("run_clientsim", "editor_state", "Unity is playing or changing Play Mode"),
                        readOnly: true,
                        executed: false));
                return;
            }

            string sceneAssetPath;
            string sceneError;
            if (!UnityRuntimeValidationBridge.TryResolveSceneAssetPath(
                    request,
                    out sceneAssetPath,
                    out sceneError))
            {
                WriteImmediate(
                    safeResponsePath,
                    UnityRuntimeValidationBridge.BuildError(
                        code: "RUN002",
                        message: "ClientSim scene path is invalid.",
                        request: request,
                        diagnostics: DiagFrom("scene_path", "schema_error", sceneError),
                        readOnly: true,
                        executed: false));
                return;
            }

            Scene activeScene = SceneManager.GetActiveScene();
            if (SceneManager.sceneCount != 1
                || !activeScene.IsValid()
                || !activeScene.isLoaded
                || !string.Equals(activeScene.path, sceneAssetPath, StringComparison.Ordinal))
            {
                WriteImmediate(
                    safeResponsePath,
                    UnityRuntimeValidationBridge.BuildError(
                        code: "CLIENTSIM_ACTIVE_SCENE_REQUIRED",
                        message: "ClientSim requires the requested scene to be the only loaded active scene.",
                        request: request,
                        diagnostics: DiagFrom(
                            "scene_path",
                            "editor_state",
                            $"requested={sceneAssetPath}; active={activeScene.path}; loaded_scene_count={SceneManager.sceneCount}"),
                        readOnly: true,
                        executed: false));
                return;
            }

            UnityRuntimeValidationBridge.SceneSideEffectSnapshot beforeSnapshot =
                CaptureSceneSnapshot(activeScene);
            if (beforeSnapshot.Dirty && !request.allow_dirty_before)
            {
                WriteImmediate(
                    safeResponsePath,
                    UnityRuntimeValidationBridge.BuildError(
                        code: "CLIENTSIM_DIRTY_SCENE",
                        message: "ClientSim validation refused to run because the active scene is already dirty.",
                        request: request,
                        diagnostics: DiagFrom("scene_path", "dirty_scene", sceneAssetPath),
                        readOnly: true,
                        executed: false,
                        sideEffectReport: BuildSideEffectReport(
                            sceneAssetPath,
                            beforeSnapshot,
                            null,
                            beforeSnapshot)));
                return;
            }

            UnityRuntimeValidationBridge.RuntimeResponse preflightFailure =
                TryPreflightClientSim(request);
            if (preflightFailure != null)
            {
                WriteImmediate(safeResponsePath, preflightFailure);
                return;
            }

            SceneAsset previousStartScene = EditorSceneManager.playModeStartScene;
            string previousStartScenePath = previousStartScene == null
                ? string.Empty
                : AssetDatabase.GetAssetPath(previousStartScene);
            string previousStartSceneGuid = string.IsNullOrEmpty(previousStartScenePath)
                ? string.Empty
                : AssetDatabase.AssetPathToGUID(previousStartScenePath);
            if (previousStartScene != null && string.IsNullOrEmpty(previousStartSceneGuid))
            {
                WriteImmediate(
                    safeResponsePath,
                    UnityRuntimeValidationBridge.BuildError(
                        code: "CLIENTSIM_START_SCENE_UNRESTORABLE",
                        message: "The existing Play Mode start scene cannot be restored by GUID.",
                        request: request,
                        diagnostics: DiagFrom(
                            "playModeStartScene",
                            "restore_preflight",
                            previousStartScenePath),
                        readOnly: true,
                        executed: false));
                return;
            }

            string targetSceneGuid = AssetDatabase.AssetPathToGUID(sceneAssetPath);
            if (string.IsNullOrEmpty(targetSceneGuid))
            {
                WriteImmediate(
                    safeResponsePath,
                    UnityRuntimeValidationBridge.BuildError(
                        code: "RUN002",
                        message: "The ClientSim target scene has no asset GUID.",
                        request: request,
                        diagnostics: DiagFrom("scene_path", "asset_identity", sceneAssetPath),
                        readOnly: true,
                        executed: false));
                return;
            }

            if (EditorApplication.timeSinceStartup >= operationDeadline)
            {
                WriteImmediate(
                    safeResponsePath,
                    UnityRuntimeValidationBridge.BuildError(
                        code: "CLIENTSIM_PREFLIGHT_TIMEOUT",
                        message: "ClientSim preflight exceeded the operation deadline before Play Mode was entered.",
                        request: request,
                        diagnostics: DiagFrom(
                            "run_clientsim",
                            "timeout",
                            $"deadline={operationDeadline}"),
                        readOnly: true,
                        executed: false,
                        sideEffectReport: BuildSideEffectReport(
                            sceneAssetPath,
                            beforeSnapshot,
                            null,
                            beforeSnapshot)));
                return;
            }

            var lease = new RestorationLease
            {
                previousStartSceneWasNull = previousStartScene == null,
                previousStartSceneGuid = previousStartSceneGuid,
                targetSceneGuid = targetSceneGuid,
                targetScenePath = sceneAssetPath,
                responsePath = safeResponsePath,
            };
            EnsureSubscribed();
            SaveRestorationLease(lease);

            var state = new OperationState
            {
                request = request,
                responsePath = safeResponsePath,
                sceneAssetPath = sceneAssetPath,
                phase = EnteringPlayModePhase,
                operationDeadline = operationDeadline,
                beforeSnapshot = beforeSnapshot,
            };
            SaveOperation(state);

            try
            {
                EditorSceneManager.playModeStartScene = null;
                EditorApplication.EnterPlaymode();
            }
            catch (Exception ex)
            {
                SetTerminal(
                    state,
                    success: false,
                    code: "CLIENTSIM_ENTER_PLAY_MODE_FAILED",
                    message: "Unity failed to enter Play Mode for ClientSim validation.",
                    diagnostics: DiagFrom("run_clientsim", "exception", ex.ToString()),
                    clientSimReady: false,
                    readOnly: false,
                    executed: true);
                Finish(state);
            }
        }

        private static UnityRuntimeValidationBridge.RuntimeResponse TryPreflightClientSim(
            UnityRuntimeValidationBridge.RuntimeRequest request)
        {
            try
            {
                Type settingsType = UnityRuntimeValidationBridge.FindType(
                    "VRC.SDK3.ClientSim.ClientSimSettings, VRC.ClientSim");
                Type mainType = UnityRuntimeValidationBridge.FindType(
                    "VRC.SDK3.ClientSim.ClientSimMain, VRC.ClientSim");
                if (settingsType == null || mainType == null)
                {
                    return UnityRuntimeValidationBridge.BuildSkip(
                        code: "RUN_CLIENTSIM_SKIPPED",
                        message: "ClientSim runtime assembly was not found; smoke check skipped.",
                        request: request);
                }

                PropertyInfo instanceProperty = settingsType.GetProperty(
                    "Instance",
                    BindingFlags.Public | BindingFlags.Static);
                FieldInfo enabledField = settingsType.GetField(
                    "enableClientSim",
                    BindingFlags.Public | BindingFlags.Instance);
                MethodInfo hasInstance = mainType.GetMethod(
                    "HasInstance",
                    BindingFlags.Public | BindingFlags.Static);
                MethodInfo isNetworkReady = mainType.GetMethod(
                    "IsNetworkReady",
                    BindingFlags.Public | BindingFlags.Instance);
                if (instanceProperty == null
                    || enabledField == null
                    || hasInstance == null
                    || isNetworkReady == null)
                {
                    return UnityRuntimeValidationBridge.BuildSkip(
                        code: "RUN_CLIENTSIM_SKIPPED",
                        message: "Required public ClientSim lifecycle APIs were not found; smoke check skipped.",
                        request: request);
                }

                object settings = instanceProperty.GetValue(null, null);
                bool enabled = settings != null
                    && Convert.ToBoolean(enabledField.GetValue(settings));
                if (!enabled)
                {
                    return UnityRuntimeValidationBridge.BuildSkip(
                        code: "RUN_CLIENTSIM_DISABLED",
                        message: "ClientSim is disabled in the current project settings; smoke check skipped.",
                        request: request);
                }
                return null;
            }
            catch (Exception ex)
            {
                Exception inner = (ex as TargetInvocationException)?.InnerException ?? ex;
                return UnityRuntimeValidationBridge.BuildError(
                    code: "RUN002",
                    message: "ClientSim settings preflight failed.",
                    request: request,
                    diagnostics: DiagFrom("run_clientsim", "exception", inner.ToString()),
                    readOnly: true,
                    executed: false);
            }
        }

        private static void EnsureSubscribed()
        {
            EditorApplication.update -= OnEditorUpdate;
            EditorApplication.update += OnEditorUpdate;
            EditorApplication.playModeStateChanged -= OnPlayModeStateChanged;
            EditorApplication.playModeStateChanged += OnPlayModeStateChanged;
        }

        private static void Unsubscribe()
        {
            EditorApplication.update -= OnEditorUpdate;
            EditorApplication.playModeStateChanged -= OnPlayModeStateChanged;
        }

        private static void OnEditorUpdate()
        {
            ReconcilePersistedState();
        }

        private static void OnPlayModeStateChanged(PlayModeStateChange change)
        {
            string loadError;
            OperationState state = LoadOperation(out loadError);
            if (state == null)
            {
                if (!string.IsNullOrEmpty(loadError))
                {
                    RecoverCorruptState(loadError);
                }
                return;
            }

            if (change == PlayModeStateChange.EnteredPlayMode
                && string.Equals(
                    state.phase,
                    EnteringPlayModePhase,
                    StringComparison.Ordinal))
            {
                state.phase = WaitingForClientSimPhase;
                SaveOperation(state);
                return;
            }

            if (change == PlayModeStateChange.EnteredPlayMode
                && string.Equals(state.phase, ExitingPlayModePhase, StringComparison.Ordinal))
            {
                TryRequestExitPlayMode(state);
                return;
            }

            if (change == PlayModeStateChange.EnteredPlayMode)
            {
                ReconcilePersistedState();
                return;
            }

            if (change == PlayModeStateChange.EnteredEditMode)
            {
                if (!state.terminalSet)
                {
                    SetTerminal(
                        state,
                        success: false,
                        code: "CLIENTSIM_UNEXPECTED_PLAY_MODE_EXIT",
                        message: "Play Mode exited before ClientSim reached a terminal result.",
                        diagnostics: DiagFrom(
                            "run_clientsim",
                            "editor_state",
                            "EnteredEditMode without a terminal ClientSim outcome"),
                        clientSimReady: false,
                        readOnly: false,
                        executed: true);
                }
                Finish(state);
            }
        }

        private static void ReconcilePersistedState()
        {
            string loadError;
            OperationState state = LoadOperation(out loadError);
            if (state == null)
            {
                if (!string.IsNullOrEmpty(loadError) || HasRestorationLease())
                {
                    RecoverCorruptState(
                        string.IsNullOrEmpty(loadError)
                            ? "ClientSim operation state is missing."
                            : loadError);
                }
                return;
            }

            bool isPlaying = EditorApplication.isPlaying;
            bool isTransitioning = EditorApplication.isPlayingOrWillChangePlaymode;
            double now = EditorApplication.timeSinceStartup;

            if (state.terminalSet
                && !string.Equals(
                    state.phase,
                    ExitingPlayModePhase,
                    StringComparison.Ordinal))
            {
                BeginExit(state);
                return;
            }

            if (string.Equals(state.phase, EnteringPlayModePhase, StringComparison.Ordinal))
            {
                if (isPlaying)
                {
                    state.phase = WaitingForClientSimPhase;
                    SaveOperation(state);
                    ReconcilePersistedState();
                    return;
                }
                if (now >= state.operationDeadline)
                {
                    SetTerminal(
                        state,
                        success: false,
                        code: "CLIENTSIM_ENTER_PLAY_MODE_TIMEOUT",
                        message: "Timed out waiting for Unity to enter Play Mode.",
                        diagnostics: DiagFrom(
                            "run_clientsim",
                            "timeout",
                            $"deadline={state.operationDeadline}"),
                        clientSimReady: false,
                        readOnly: false,
                        executed: true);
                    BeginExit(state);
                }
                return;
            }

            if (string.Equals(state.phase, WaitingForClientSimPhase, StringComparison.Ordinal))
            {
                if (!isPlaying)
                {
                    if (!isTransitioning)
                    {
                        SetTerminal(
                            state,
                            success: false,
                            code: "CLIENTSIM_UNEXPECTED_PLAY_MODE_EXIT",
                            message: "Play Mode ended before ClientSim became ready.",
                            diagnostics: DiagFrom(
                                "run_clientsim",
                                "editor_state",
                                "editor returned to Edit Mode while waiting for ClientSim"),
                            clientSimReady: false,
                            readOnly: false,
                            executed: true);
                        Finish(state);
                    }
                    return;
                }

                bool ready;
                string readyError;
                if (!TryGetClientSimReady(out ready, out readyError))
                {
                    SetTerminal(
                        state,
                        success: false,
                        code: "CLIENTSIM_READY_CHECK_FAILED",
                        message: "ClientSim readiness could not be inspected.",
                        diagnostics: DiagFrom("run_clientsim", "exception", readyError),
                        clientSimReady: false,
                        readOnly: false,
                        executed: true);
                    BeginExit(state);
                    return;
                }

                if (ready)
                {
                    state.runtimeSnapshot = CaptureSceneSnapshot(SceneManager.GetActiveScene());
                    SetTerminal(
                        state,
                        success: true,
                        code: "RUN_CLIENTSIM_OK",
                        message: "ClientSim smoke reached network-ready state via Play Mode.",
                        diagnostics: Array.Empty<UnityRuntimeValidationBridge.RuntimeDiagnostic>(),
                        clientSimReady: true,
                        readOnly: false,
                        executed: true);
                    BeginExit(state);
                    return;
                }

                if (now >= state.operationDeadline)
                {
                    SetTerminal(
                        state,
                        success: false,
                        code: "CLIENTSIM_READY_TIMEOUT",
                        message: "Timed out waiting for ClientSim network-ready state.",
                        diagnostics: DiagFrom(
                            "run_clientsim",
                            "timeout",
                            $"deadline={state.operationDeadline}"),
                        clientSimReady: false,
                        readOnly: false,
                        executed: true);
                    BeginExit(state);
                }
                return;
            }

            if (string.Equals(state.phase, ExitingPlayModePhase, StringComparison.Ordinal))
            {
                if (!isPlaying && !isTransitioning)
                {
                    Finish(state);
                    return;
                }

                if (!state.exitTimeoutRecorded
                    && state.exitDeadline > 0
                    && now >= state.exitDeadline)
                {
                    state.exitTimeoutRecorded = true;
                    SetTerminal(
                        state,
                        success: false,
                        code: "CLIENTSIM_EXIT_PLAY_MODE_TIMEOUT",
                        message: "Timed out waiting for Unity to exit Play Mode; cleanup ownership is retained.",
                        diagnostics: DiagFrom(
                            "run_clientsim",
                            "timeout",
                            $"deadline={state.exitDeadline}"),
                        clientSimReady: false,
                        readOnly: false,
                        executed: true);
                    TryRequestExitPlayMode(state);
                }
                return;
            }

            SetTerminal(
                state,
                success: false,
                code: "CLIENTSIM_STATE_INVALID",
                message: "ClientSim operation phase is invalid.",
                diagnostics: DiagFrom("run_clientsim", "state", state.phase ?? string.Empty),
                clientSimReady: false,
                readOnly: false,
                executed: true);
            BeginExit(state);
        }

        private static bool TryGetClientSimReady(out bool ready, out string error)
        {
            ready = false;
            error = string.Empty;
            try
            {
                Type mainType = UnityRuntimeValidationBridge.FindType(
                    "VRC.SDK3.ClientSim.ClientSimMain, VRC.ClientSim");
                if (mainType == null)
                {
                    error = "ClientSimMain type disappeared after entering Play Mode.";
                    return false;
                }

                MethodInfo hasInstance = mainType.GetMethod(
                    "HasInstance",
                    BindingFlags.Public | BindingFlags.Static);
                MethodInfo isNetworkReady = mainType.GetMethod(
                    "IsNetworkReady",
                    BindingFlags.Public | BindingFlags.Instance);
                if (hasInstance == null || isNetworkReady == null)
                {
                    error = "Required public ClientSim readiness APIs are unavailable.";
                    return false;
                }

                bool hasMain = Convert.ToBoolean(hasInstance.Invoke(null, null));
                if (!hasMain)
                {
                    return true;
                }

                UnityEngine.Object[] instances = Resources.FindObjectsOfTypeAll(mainType);
                var liveInstances = new List<UnityEngine.Object>();
                foreach (UnityEngine.Object candidate in instances)
                {
                    Component component = candidate as Component;
                    if (component == null
                        || EditorUtility.IsPersistent(component.gameObject))
                    {
                        continue;
                    }

                    Scene instanceScene = component.gameObject.scene;
                    if (!instanceScene.IsValid() || !instanceScene.isLoaded)
                    {
                        continue;
                    }
                    liveInstances.Add(candidate);
                }

                if (liveInstances.Count == 0)
                {
                    error =
                        "ClientSim reports an instance but no loaded live instance was found.";
                    return false;
                }
                if (liveInstances.Count != 1)
                {
                    error =
                        $"ClientSim live instance is ambiguous: {liveInstances.Count}.";
                    return false;
                }

                ready = Convert.ToBoolean(
                    isNetworkReady.Invoke(liveInstances[0], null));
                return true;
            }
            catch (Exception ex)
            {
                Exception inner = (ex as TargetInvocationException)?.InnerException ?? ex;
                error = inner.ToString();
                return false;
            }
        }

        private static void BeginExit(OperationState state)
        {
            if (EditorApplication.isPlaying && state.runtimeSnapshot == null)
            {
                state.runtimeSnapshot = CaptureSceneSnapshot(SceneManager.GetActiveScene());
            }
            state.phase = ExitingPlayModePhase;
            state.exitDeadline =
                EditorApplication.timeSinceStartup + ExitCleanupTimeoutSeconds;
            SaveOperation(state);

            if (!EditorApplication.isPlayingOrWillChangePlaymode)
            {
                Finish(state);
                return;
            }
            TryRequestExitPlayMode(state);
        }

        private static void TryRequestExitPlayMode(OperationState state)
        {
            try
            {
                EditorApplication.ExitPlaymode();
            }
            catch (Exception ex)
            {
                SetTerminal(
                    state,
                    success: false,
                    code: "CLIENTSIM_EXIT_PLAY_MODE_FAILED",
                    message: "Unity rejected the ClientSim Play Mode exit request.",
                    diagnostics: DiagFrom("run_clientsim", "exception", ex.ToString()),
                    clientSimReady: false,
                    readOnly: false,
                    executed: true);
            }
        }

        private static void SetTerminal(
            OperationState state,
            bool success,
            string code,
            string message,
            UnityRuntimeValidationBridge.RuntimeDiagnostic[] diagnostics,
            bool clientSimReady,
            bool readOnly,
            bool executed)
        {
            state.terminalSet = true;
            state.terminalSuccess = success;
            state.terminalCode = code ?? string.Empty;
            state.terminalMessage = message ?? string.Empty;
            state.terminalDiagnostics =
                diagnostics ?? Array.Empty<UnityRuntimeValidationBridge.RuntimeDiagnostic>();
            state.terminalClientSimReady = clientSimReady;
            state.terminalReadOnly = readOnly;
            state.terminalExecuted = executed;
            SaveOperation(state);
        }

        private static void Finish(OperationState state)
        {
            if (EditorApplication.isPlayingOrWillChangePlaymode)
            {
                return;
            }

            string leaseError;
            RestorationLease lease = LoadRestorationLease(out leaseError);
            if (lease == null)
            {
                SetTerminal(
                    state,
                    success: false,
                    code: "CLIENTSIM_RESTORE_FAILED",
                    message: "ClientSim restoration lease is missing or invalid.",
                    diagnostics: DiagFrom("playModeStartScene", "restore_error", leaseError),
                    clientSimReady: false,
                    readOnly: false,
                    executed: true);
            }
            else
            {
                string restoreError;
                if (!RestorePlayModeStartScene(lease, out restoreError))
                {
                    SetTerminal(
                        state,
                        success: false,
                        code: "CLIENTSIM_RESTORE_FAILED",
                        message: "The previous Play Mode start scene could not be restored.",
                        diagnostics: DiagFrom(
                            "playModeStartScene",
                            "restore_error",
                            restoreError),
                        clientSimReady: false,
                        readOnly: false,
                        executed: true);
                    return;
                }
            }

            Scene activeScene = SceneManager.GetActiveScene();
            UnityRuntimeValidationBridge.SceneSideEffectSnapshot afterSnapshot =
                activeScene.IsValid()
                && activeScene.isLoaded
                && string.Equals(
                    activeScene.path,
                    state.sceneAssetPath,
                    StringComparison.Ordinal)
                    ? CaptureSceneSnapshot(activeScene)
                    : null;
            UnityRuntimeValidationBridge.ClientSimSideEffectReport report =
                BuildSideEffectReport(
                    state.sceneAssetPath,
                    state.beforeSnapshot,
                    state.runtimeSnapshot,
                    afterSnapshot);

            if (!state.terminalSet)
            {
                SetTerminal(
                    state,
                    success: false,
                    code: "CLIENTSIM_STATE_INVALID",
                    message: "ClientSim reached cleanup without a terminal outcome.",
                    diagnostics: DiagFrom("run_clientsim", "state", state.phase ?? string.Empty),
                    clientSimReady: false,
                    readOnly: false,
                    executed: true);
            }

            UnityRuntimeValidationBridge.RuntimeResponse response =
                state.terminalSuccess
                    ? UnityRuntimeValidationBridge.BuildSuccess(
                        code: state.terminalCode,
                        message: state.terminalMessage,
                        request: state.request,
                        clientSimReady: state.terminalClientSimReady,
                        sideEffectReport: report)
                    : UnityRuntimeValidationBridge.BuildError(
                        code: state.terminalCode,
                        message: state.terminalMessage,
                        request: state.request,
                        diagnostics: state.terminalDiagnostics,
                        readOnly: state.terminalReadOnly,
                        executed: state.terminalExecuted,
                        sideEffectReport: report);

            SaveOperation(state);
            bool responsePublished = File.Exists(state.responsePath)
                || UnityRuntimeValidationBridge.TryWriteResponseAtomically(
                    state.responsePath,
                    response);
            if (responsePublished)
            {
                ClearPersistedState();
            }
        }

        private static bool RestorePlayModeStartScene(
            RestorationLease lease,
            out string error)
        {
            error = string.Empty;
            if (lease.previousStartSceneWasNull)
            {
                EditorSceneManager.playModeStartScene = null;
                return EditorSceneManager.playModeStartScene == null;
            }

            string path = AssetDatabase.GUIDToAssetPath(lease.previousStartSceneGuid);
            if (string.IsNullOrEmpty(path))
            {
                error =
                    $"No SceneAsset resolves from GUID {lease.previousStartSceneGuid}.";
                return false;
            }

            SceneAsset previous = AssetDatabase.LoadAssetAtPath<SceneAsset>(path);
            if (previous == null)
            {
                error = $"SceneAsset could not be loaded from '{path}'.";
                return false;
            }

            EditorSceneManager.playModeStartScene = previous;
            if (EditorSceneManager.playModeStartScene != previous)
            {
                error = $"Unity did not retain restored Play Mode start scene '{path}'.";
                return false;
            }
            return true;
        }

        private static void ReconcileCorruptLeaseOnly(
            RestorationLease lease,
            string evidence)
        {
            string restoreError;
            bool restored = RestorePlayModeStartScene(lease, out restoreError);
            if (!restored)
            {
                return;
            }

            var request = new UnityRuntimeValidationBridge.RuntimeRequest
            {
                action = "run_clientsim",
                scene_path = lease.targetScenePath,
                profile = "clientsim",
            };
            UnityRuntimeValidationBridge.RuntimeResponse response =
                UnityRuntimeValidationBridge.BuildError(
                    code: "CLIENTSIM_STATE_CORRUPT",
                    message: "ClientSim operation state was corrupt; editor state was restored.",
                    request: request,
                    diagnostics: DiagFrom(
                        "run_clientsim",
                        "state",
                        evidence),
                    readOnly: false,
                    executed: true);

            bool responsePublished = File.Exists(lease.responsePath)
                || UnityRuntimeValidationBridge.TryWriteResponseAtomically(
                    lease.responsePath,
                    response);
            if (responsePublished)
            {
                ClearPersistedState();
            }
        }

        private static void RecoverCorruptState(string evidence)
        {
            if (EditorApplication.isPlayingOrWillChangePlaymode)
            {
                try
                {
                    EditorApplication.ExitPlaymode();
                }
                catch (Exception ex)
                {
                    Debug.LogError(
                        $"[PrefabSentinel] ClientSim corrupt-state exit failed: {ex}");
                }
                return;
            }

            string leaseError;
            RestorationLease lease = LoadRestorationLease(out leaseError);
            if (lease == null)
            {
                Debug.LogError(
                    $"[PrefabSentinel] ClientSim state is unrecoverable: {evidence}; {leaseError}");
                return;
            }
            ReconcileCorruptLeaseOnly(lease, evidence);
        }

        private static bool HasPersistedState()
        {
            return !string.IsNullOrEmpty(
                    SessionState.GetString(OperationStateKey, string.Empty))
                || HasRestorationLease();
        }

        private static bool HasRestorationLease()
        {
            return !string.IsNullOrEmpty(
                SessionState.GetString(RestorationLeaseKey, string.Empty));
        }

        private static void SaveOperation(OperationState state)
        {
            SessionState.SetString(
                OperationStateKey,
                JsonUtility.ToJson(state));
        }

        private static OperationState LoadOperation(out string error)
        {
            error = string.Empty;
            string json = SessionState.GetString(OperationStateKey, string.Empty);
            if (string.IsNullOrEmpty(json))
            {
                return null;
            }
            try
            {
                OperationState state = JsonUtility.FromJson<OperationState>(json);
                if (state == null
                    || state.request == null
                    || string.IsNullOrEmpty(state.responsePath)
                    || string.IsNullOrEmpty(state.sceneAssetPath)
                    || string.IsNullOrEmpty(state.phase))
                {
                    error = "ClientSim operation record is incomplete.";
                    return null;
                }
                return state;
            }
            catch (Exception ex)
            {
                error = ex.ToString();
                return null;
            }
        }

        private static void SaveRestorationLease(RestorationLease lease)
        {
            SessionState.SetString(
                RestorationLeaseKey,
                JsonUtility.ToJson(lease));
        }

        private static RestorationLease LoadRestorationLease(out string error)
        {
            error = string.Empty;
            string json = SessionState.GetString(
                RestorationLeaseKey,
                string.Empty);
            if (string.IsNullOrEmpty(json))
            {
                error = "ClientSim restoration lease is missing.";
                return null;
            }
            try
            {
                RestorationLease lease =
                    JsonUtility.FromJson<RestorationLease>(json);
                if (lease == null
                    || string.IsNullOrEmpty(lease.targetSceneGuid)
                    || string.IsNullOrEmpty(lease.targetScenePath)
                    || string.IsNullOrEmpty(lease.responsePath))
                {
                    error = "ClientSim restoration lease is incomplete.";
                    return null;
                }
                if (!lease.previousStartSceneWasNull
                    && string.IsNullOrEmpty(lease.previousStartSceneGuid))
                {
                    error = "ClientSim restoration lease lost the previous start scene GUID.";
                    return null;
                }
                return lease;
            }
            catch (Exception ex)
            {
                error = ex.ToString();
                return null;
            }
        }

        private static void ClearPersistedState()
        {
            SessionState.EraseString(OperationStateKey);
            SessionState.EraseString(RestorationLeaseKey);
            Unsubscribe();
        }

        private static void WriteImmediate(
            string responsePath,
            UnityRuntimeValidationBridge.RuntimeResponse response)
        {
            UnityRuntimeValidationBridge.WriteResponse(
                responsePath,
                response);
        }

        private static UnityRuntimeValidationBridge.SceneSideEffectSnapshot
            CaptureSceneSnapshot(Scene scene)
        {
            var roots = new List<string>();
            var hierarchy = new List<string>();
            var components = new List<string>();
            if (scene.IsValid() && scene.isLoaded)
            {
                foreach (GameObject root in scene.GetRootGameObjects())
                {
                    roots.Add(root.name);
                    CaptureGameObjectSnapshot(
                        root.transform,
                        root.name,
                        hierarchy,
                        components);
                }
            }
            return new UnityRuntimeValidationBridge.SceneSideEffectSnapshot
            {
                Roots = roots.ToArray(),
                Hierarchy = hierarchy.ToArray(),
                Components = components.ToArray(),
                AssetChangeCandidates = DirtyAssetChangeCandidates(),
                Dirty = scene.IsValid() && scene.isDirty,
                DirtyCount = DirtySceneCount(),
            };
        }

        private static void CaptureGameObjectSnapshot(
            Transform transform,
            string path,
            List<string> hierarchy,
            List<string> components)
        {
            hierarchy.Add(path);
            foreach (Component component in transform.GetComponents<Component>())
            {
                string componentName = component == null
                    ? "<missing>"
                    : component.GetType().FullName ?? component.GetType().Name;
                components.Add(path + ":" + componentName);
            }
            for (int i = 0; i < transform.childCount; i++)
            {
                Transform child = transform.GetChild(i);
                CaptureGameObjectSnapshot(
                    child,
                    path + "/" + child.name,
                    hierarchy,
                    components);
            }
        }

        private static int DirtySceneCount()
        {
            int count = 0;
            for (int i = 0; i < SceneManager.sceneCount; i++)
            {
                Scene scene = SceneManager.GetSceneAt(i);
                if (scene.IsValid() && scene.isDirty)
                {
                    count++;
                }
            }
            return count;
        }

        private static string[] DirtyAssetChangeCandidates()
        {
            var candidates = new List<string>();
            foreach (string path in DirtyScenePaths())
            {
                AddUnique(candidates, path);
            }
            foreach (string path in DirtyAssetPaths())
            {
                AddUnique(candidates, path);
            }
            candidates.Sort(StringComparer.Ordinal);
            return candidates.ToArray();
        }

        private static string[] DirtyScenePaths()
        {
            var paths = new List<string>();
            for (int i = 0; i < SceneManager.sceneCount; i++)
            {
                Scene scene = SceneManager.GetSceneAt(i);
                if (scene.IsValid()
                    && scene.isDirty
                    && !string.IsNullOrEmpty(scene.path))
                {
                    paths.Add(scene.path);
                }
            }
            return paths.ToArray();
        }

        private static string[] DirtyAssetPaths()
        {
            var paths = new List<string>();
            foreach (
                UnityEngine.Object asset
                in Resources.FindObjectsOfTypeAll<UnityEngine.Object>())
            {
                if (asset == null
                    || !EditorUtility.IsPersistent(asset)
                    || !EditorUtility.IsDirty(asset))
                {
                    continue;
                }

                string path = AssetDatabase.GetAssetPath(asset);
                if (!path.StartsWith("Assets/", StringComparison.Ordinal))
                {
                    continue;
                }
                AddUnique(paths, path);
            }
            return paths.ToArray();
        }

        private static void AddUnique(List<string> values, string value)
        {
            if (!values.Contains(value))
            {
                values.Add(value);
            }
        }

        private static UnityRuntimeValidationBridge.ClientSimSideEffectReport
            BuildSideEffectReport(
                string sceneAssetPath,
                UnityRuntimeValidationBridge.SceneSideEffectSnapshot before,
                UnityRuntimeValidationBridge.SceneSideEffectSnapshot runtime,
                UnityRuntimeValidationBridge.SceneSideEffectSnapshot after)
        {
            var warnings = new List<string>();
            if (before == null)
            {
                warnings.Add("CLIENTSIM_SIDE_EFFECT_BEFORE_UNAVAILABLE");
            }
            if (runtime == null)
            {
                warnings.Add("CLIENTSIM_SIDE_EFFECT_RUNTIME_UNAVAILABLE");
            }
            if (after == null)
            {
                warnings.Add("CLIENTSIM_SIDE_EFFECT_AFTER_UNAVAILABLE");
            }

            return new UnityRuntimeValidationBridge.ClientSimSideEffectReport
            {
                diff_complete = before != null && runtime != null && after != null,
                diff_warnings = warnings.ToArray(),
                scene_path = sceneAssetPath,
                roots_before = before?.Roots ?? Array.Empty<string>(),
                roots_runtime = runtime?.Roots ?? Array.Empty<string>(),
                roots_after = after?.Roots ?? Array.Empty<string>(),
                hierarchy_before = before?.Hierarchy ?? Array.Empty<string>(),
                hierarchy_runtime = runtime?.Hierarchy ?? Array.Empty<string>(),
                hierarchy_after = after?.Hierarchy ?? Array.Empty<string>(),
                components_before = before?.Components ?? Array.Empty<string>(),
                components_runtime = runtime?.Components ?? Array.Empty<string>(),
                components_after = after?.Components ?? Array.Empty<string>(),
                added_gameobjects = Difference(
                    runtime?.Hierarchy,
                    before?.Hierarchy),
                removed_gameobjects = Difference(
                    before?.Hierarchy,
                    runtime?.Hierarchy),
                added_components = Difference(
                    runtime?.Components,
                    before?.Components),
                removed_components = Difference(
                    before?.Components,
                    runtime?.Components),
                residual_added_gameobjects = Difference(
                    after?.Hierarchy,
                    before?.Hierarchy),
                residual_removed_gameobjects = Difference(
                    before?.Hierarchy,
                    after?.Hierarchy),
                residual_added_components = Difference(
                    after?.Components,
                    before?.Components),
                residual_removed_components = Difference(
                    before?.Components,
                    after?.Components),
                dirty_before = before != null && before.Dirty,
                dirty_runtime = runtime != null && runtime.Dirty,
                dirty_after = after != null && after.Dirty,
                dirty_count_before = before?.DirtyCount ?? 0,
                dirty_count_runtime = runtime?.DirtyCount ?? 0,
                dirty_count_after = after?.DirtyCount ?? 0,
                asset_change_candidates = SymmetricDifference(
                    after?.AssetChangeCandidates,
                    before?.AssetChangeCandidates),
            };
        }

        private static string[] Difference(string[] left, string[] right)
        {
            var remaining = new Dictionary<string, int>(StringComparer.Ordinal);
            foreach (string value in right ?? Array.Empty<string>())
            {
                if (remaining.TryGetValue(value, out int count))
                {
                    remaining[value] = count + 1;
                }
                else
                {
                    remaining[value] = 1;
                }
            }

            var diff = new List<string>();
            foreach (string value in left ?? Array.Empty<string>())
            {
                if (!remaining.TryGetValue(value, out int count))
                {
                    diff.Add(value);
                    continue;
                }
                if (count == 1)
                {
                    remaining.Remove(value);
                }
                else
                {
                    remaining[value] = count - 1;
                }
            }
            return diff.ToArray();
        }

        private static string[] SymmetricDifference(string[] left, string[] right)
        {
            var combined = new List<string>();
            combined.AddRange(Difference(left, right));
            combined.AddRange(Difference(right, left));
            return combined.ToArray();
        }

        private static UnityRuntimeValidationBridge.RuntimeDiagnostic[] DiagFrom(
            string location,
            string detail,
            string evidence)
        {
            return new[]
            {
                new UnityRuntimeValidationBridge.RuntimeDiagnostic
                {
                    location = location,
                    detail = detail,
                    evidence = evidence
                }
            };
        }
    }
}
