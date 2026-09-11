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
    public static partial class UnityRuntimeValidationBridge
    {
        public const int ProtocolVersion = UnityEditorControlBridge.ProtocolVersion;
        private const string DefaultProjectRootName = "project";

        /// <summary>All action strings handled by this bridge.</summary>
        public static readonly HashSet<string> SupportedActions = new HashSet<string>
        {
            "validate_runtime",
        };


        public static readonly HashSet<string> AsyncActions = new HashSet<string>
        {
            "validate_runtime",
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
            public string generated_asset_policy = "deny";
            public bool allow_dirty_program_assets_before_compile = false;
            public bool allow_dirty_scenes_before_compile = false;
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
            [SerializeField]
            internal RuntimeCompileReport compile =
                new RuntimeCompileReport();
            [SerializeField]
            internal RuntimeClientSimReport clientsim =
                new RuntimeClientSimReport();
        }

        [Serializable]
        internal sealed class RuntimeClientSimReport
        {
            public bool executed = false;
            public RuntimeCompileAudit.SceneIdentity[] initial_scene_snapshot =
                Array.Empty<RuntimeCompileAudit.SceneIdentity>();
            public SceneSideEffectSnapshot before;
            public SceneSideEffectSnapshot runtime;
            public SceneSideEffectSnapshot after;
            public ClientSimSideEffectReport side_effect_report;
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
            public int protocol_version = ProtocolVersion;
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

            if (!string.Equals(
                request.action,
                "validate_runtime",
                StringComparison.Ordinal))
            {
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
                return;
            }

            if (string.Equals(
                request.profile,
                "compile_only",
                StringComparison.Ordinal))
            {
                WriteResponse(responsePath, ExecuteCompile(request));
                return;
            }

            if (string.Equals(
                request.profile,
                "clientsim",
                StringComparison.Ordinal))
            {
                RuntimeValidationClientSimController.Begin(
                    request,
                    responsePath);
                return;
            }

            WriteResponse(
                responsePath,
                BuildError(
                    code: "RUN_PROTOCOL_ERROR",
                    message: $"Unsupported runtime validation profile '{request.profile}'.",
                    request: request,
                    diagnostics: new[]
                    {
                        new RuntimeDiagnostic
                        {
                            location = "profile",
                            detail = "schema_error",
                            evidence = request.profile ?? string.Empty
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

        internal static RuntimeResponse ExecuteCompile(
            RuntimeRequest request)
        {
            CompilePreflightResult preflight;
            RuntimeResponse preflightFailure =
                TryPrepareCompile(request, out preflight);
            if (preflightFailure != null)
            {
                return preflightFailure;
            }
            return BuildCompileResponse(
                request,
                ExecuteAuthorizedCompile(request, preflight));
        }

        internal static RuntimeResponse TryPrepareCompile(
            RuntimeRequest request,
            out CompilePreflightResult preflight)
        {
            try
            {
                preflight = CaptureCompilePreflight(request);
            }
            catch (Exception ex)
            {
                preflight = new CompilePreflightResult
                {
                    InventoryStable = false,
                    Before = new RuntimeCompileAudit.Snapshot
                    {
                        inventory_stable = false,
                    },
                };
                return BuildCompilePreflightFailure(
                    request,
                    preflight,
                    ex.ToString());
            }

            RuntimeCompileAudit.PreflightDecision decision;
            try
            {
                RuntimeCompileAudit.GeneratedAssetPolicy policy =
                    RuntimeCompileAudit.ParsePolicy(
                        request.generated_asset_policy);
                decision = RuntimeCompileAudit.EvaluatePreflight(
                    preflight.Before,
                    policy,
                    request.allow_dirty_program_assets_before_compile,
                    request.allow_dirty_scenes_before_compile);
            }
            catch (Exception ex)
            {
                return BuildCompilePreflightFailure(
                    request,
                    preflight,
                    ex.ToString());
            }

            if (!decision.compile_allowed)
            {
                return BuildCompilePreflightDenied(
                    request,
                    preflight,
                    decision);
            }
            return null;
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

        internal sealed class PreparedClientSim
        {
            internal string SceneAssetPath = string.Empty;
            internal string TargetSceneGuid = string.Empty;
            internal bool PreviousStartSceneWasNull;
            internal string PreviousStartSceneGuid = string.Empty;
            internal double OperationDeadline;
            internal RuntimeCompileAudit.SceneIdentity[] InitialScenes =
                Array.Empty<RuntimeCompileAudit.SceneIdentity>();
        }

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
            public bool clientSimExecuted = false;
            public UnityRuntimeValidationBridge.RuntimeDiagnostic[] terminalDiagnostics =
                Array.Empty<UnityRuntimeValidationBridge.RuntimeDiagnostic>();
            public UnityRuntimeValidationBridge.RuntimeCompileReport compileReport =
                new UnityRuntimeValidationBridge.RuntimeCompileReport();
            public RuntimeCompileAudit.SceneIdentity[] initialSceneSnapshot =
                Array.Empty<RuntimeCompileAudit.SceneIdentity>();
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
            public bool clientSimExecuted = false;
            public UnityRuntimeValidationBridge.RuntimeCompileReport compileReport =
                new UnityRuntimeValidationBridge.RuntimeCompileReport();
            public RuntimeCompileAudit.SceneIdentity[] initialSceneSnapshot =
                Array.Empty<RuntimeCompileAudit.SceneIdentity>();
            public UnityRuntimeValidationBridge.SceneSideEffectSnapshot beforeSnapshot;
            public UnityRuntimeValidationBridge.SceneSideEffectSnapshot runtimeSnapshot;
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
            PreparedClientSim prepared;
            UnityRuntimeValidationBridge.RuntimeResponse prepareFailure;
            if (!TryPrepareClientSim(
                    request,
                    out prepared,
                    out prepareFailure))
            {
                WriteImmediate(safeResponsePath, prepareFailure);
                return;
            }

            UnityRuntimeValidationBridge.CompilePreflightResult compilePreflight;
            UnityRuntimeValidationBridge.RuntimeResponse compilePreflightFailure =
                UnityRuntimeValidationBridge.TryPrepareCompile(
                    request,
                    out compilePreflight);
            bool initialSceneDirty =
                prepared.InitialScenes.Length == 1
                && prepared.InitialScenes[0].dirty;
            RuntimeValidationTransactionOrder.Decision preCompileDecision =
                RuntimeValidationTransactionOrder.Decide(
                    clientSimRequested: true,
                    reportAndAuditValid: true,
                    compileInventoryValid: compilePreflightFailure == null,
                    clientSimPreflightValid: true,
                    initialSceneDirty: initialSceneDirty,
                    allowInitialDirtyScene:
                        request.allow_dirty_scenes_before_compile,
                    compileSucceeded: false,
                    sceneDirtyAfterCompile: false);
            if (!preCompileDecision.compile_allowed)
            {
                WriteImmediate(
                    safeResponsePath,
                    AttachTransactionEvidence(
                        compilePreflightFailure,
                        compilePreflightFailure.data.compile,
                        prepared.InitialScenes,
                        clientSimExecuted: false,
                        beforeSnapshot: null,
                        runtimeSnapshot: null,
                        afterSnapshot: null,
                        sideEffectReport: null));
                return;
            }

            UnityRuntimeValidationBridge.RuntimeCompileReport compileReport =
                UnityRuntimeValidationBridge.ExecuteAuthorizedCompile(
                    request,
                    compilePreflight);
            UnityRuntimeValidationBridge.RuntimeResponse compileResponse =
                UnityRuntimeValidationBridge.BuildCompileResponse(
                    request,
                    compileReport);
            Scene postCompileScene = SceneManager.GetActiveScene();
            bool sceneDirtyAfterCompile =
                postCompileScene.IsValid()
                && postCompileScene.isLoaded
                && postCompileScene.isDirty;
            RuntimeValidationTransactionOrder.Decision postCompileDecision =
                RuntimeValidationTransactionOrder.Decide(
                    clientSimRequested: true,
                    reportAndAuditValid: true,
                    compileInventoryValid: true,
                    clientSimPreflightValid: true,
                    initialSceneDirty: initialSceneDirty,
                    allowInitialDirtyScene:
                        request.allow_dirty_scenes_before_compile,
                    compileSucceeded: compileReport.success,
                    sceneDirtyAfterCompile: sceneDirtyAfterCompile);
            if (!postCompileDecision.clientsim_allowed)
            {
                WriteImmediate(
                    safeResponsePath,
                    AttachTransactionEvidence(
                        compileResponse,
                        compileReport,
                        prepared.InitialScenes,
                        clientSimExecuted: false,
                        beforeSnapshot: null,
                        runtimeSnapshot: null,
                        afterSnapshot: null,
                        sideEffectReport: null));
                return;
            }

            BeginPreparedClientSim(
                request,
                safeResponsePath,
                prepared,
                compileReport);
        }

        internal static bool TryPrepareClientSim(
            UnityRuntimeValidationBridge.RuntimeRequest request,
            out PreparedClientSim prepared,
            out UnityRuntimeValidationBridge.RuntimeResponse failure)
        {
            prepared = null;
            failure = null;
            if (request == null)
            {
                failure = UnityRuntimeValidationBridge.BuildError(
                    code: "RUN_PROTOCOL_ERROR",
                    message: "ClientSim request is missing.",
                    request: new UnityRuntimeValidationBridge.RuntimeRequest(),
                    diagnostics: DiagFrom(
                        "request",
                        "schema_error",
                        "request was null"),
                    readOnly: true,
                    executed: false);
                return false;
            }

            double operationDeadline =
                EditorApplication.timeSinceStartup
                + Math.Max(request.timeout_sec, 1);
            if (!string.Equals(
                    request.profile,
                    "clientsim",
                    StringComparison.Ordinal)
                || !request.confirm
                || string.IsNullOrWhiteSpace(request.change_reason))
            {
                failure = UnityRuntimeValidationBridge.BuildError(
                    code: "CLIENTSIM_CONFIRM_REQUIRED",
                    message: "ClientSim requires profile=clientsim, confirm=true, and a non-empty change_reason.",
                    request: request,
                    diagnostics: DiagFrom(
                        "confirm",
                        "audit_required",
                        "profile=clientsim, confirm=true, and change_reason are required"),
                    readOnly: true,
                    executed: false);
                return false;
            }

            if (HasPersistedState())
            {
                failure = UnityRuntimeValidationBridge.BuildError(
                    code: "CLIENTSIM_ALREADY_RUNNING",
                    message: "Another ClientSim validation operation already owns Play Mode cleanup.",
                    request: request,
                    diagnostics: DiagFrom(
                        "validate_runtime",
                        "busy",
                        "persisted operation or restoration lease exists"),
                    readOnly: true,
                    executed: false);
                return false;
            }

            if (EditorApplication.isPlayingOrWillChangePlaymode)
            {
                failure = UnityRuntimeValidationBridge.BuildError(
                    code: "CLIENTSIM_EDITOR_NOT_READY",
                    message: "ClientSim validation requires Unity to be in stable Edit Mode.",
                    request: request,
                    diagnostics: DiagFrom(
                        "validate_runtime",
                        "editor_state",
                        "Unity is playing or changing Play Mode"),
                    readOnly: true,
                    executed: false);
                return false;
            }

            string sceneAssetPath;
            string sceneError;
            if (!UnityRuntimeValidationBridge.TryResolveSceneAssetPath(
                    request,
                    out sceneAssetPath,
                    out sceneError))
            {
                failure = UnityRuntimeValidationBridge.BuildError(
                    code: "RUN002",
                    message: "ClientSim scene path is invalid.",
                    request: request,
                    diagnostics: DiagFrom(
                        "scene_path",
                        "schema_error",
                        sceneError),
                    readOnly: true,
                    executed: false);
                return false;
            }

            Scene activeScene = SceneManager.GetActiveScene();
            if (SceneManager.sceneCount != 1
                || !activeScene.IsValid()
                || !activeScene.isLoaded
                || !string.Equals(
                    activeScene.path,
                    sceneAssetPath,
                    StringComparison.Ordinal))
            {
                failure = UnityRuntimeValidationBridge.BuildError(
                    code: "CLIENTSIM_ACTIVE_SCENE_REQUIRED",
                    message: "ClientSim requires the requested scene to be the only loaded active scene.",
                    request: request,
                    diagnostics: DiagFrom(
                        "scene_path",
                        "editor_state",
                        $"requested={sceneAssetPath}; active={activeScene.path}; loaded_scene_count={SceneManager.sceneCount}"),
                    readOnly: true,
                    executed: false);
                return false;
            }

            var initialScene = new RuntimeCompileAudit.SceneIdentity
            {
                path = activeScene.path,
                handle = activeScene.handle,
                dirty = activeScene.isDirty,
                attribution_unknown =
                    activeScene.isDirty
                    && request.allow_dirty_scenes_before_compile
                        ? new[]
                        {
                            $"dirty_scene_before_compile:{activeScene.path}",
                        }
                        : Array.Empty<string>(),
            };
            RuntimeCompileAudit.SceneIdentity[] initialScenes =
                new[] { initialScene };
            if (initialScene.dirty
                && !request.allow_dirty_scenes_before_compile)
            {
                failure = AttachPreparedPrecompileFailure(
                    UnityRuntimeValidationBridge.BuildError(
                        code: "CLIENTSIM_DIRTY_SCENE",
                        message: "ClientSim validation refused to run because the active scene is already dirty.",
                        request: request,
                        diagnostics: DiagFrom(
                            "scene_path",
                            "dirty_scene",
                            sceneAssetPath),
                        readOnly: true,
                        executed: false),
                    initialScenes);
                return false;
            }

            UnityRuntimeValidationBridge.RuntimeResponse preflightFailure =
                TryPreflightClientSim(request);
            if (preflightFailure != null)
            {
                failure = AttachPreparedPrecompileFailure(
                    preflightFailure,
                    initialScenes);
                return false;
            }

            SceneAsset previousStartScene =
                EditorSceneManager.playModeStartScene;
            string previousStartScenePath = previousStartScene == null
                ? string.Empty
                : AssetDatabase.GetAssetPath(previousStartScene);
            string previousStartSceneGuid =
                string.IsNullOrEmpty(previousStartScenePath)
                    ? string.Empty
                    : AssetDatabase.AssetPathToGUID(
                        previousStartScenePath);
            if (previousStartScene != null
                && string.IsNullOrEmpty(previousStartSceneGuid))
            {
                failure = AttachPreparedPrecompileFailure(
                    UnityRuntimeValidationBridge.BuildError(
                        code: "CLIENTSIM_START_SCENE_UNRESTORABLE",
                        message: "The existing Play Mode start scene cannot be restored by GUID.",
                        request: request,
                        diagnostics: DiagFrom(
                            "playModeStartScene",
                            "restore_preflight",
                            previousStartScenePath),
                        readOnly: true,
                        executed: false),
                    initialScenes);
                return false;
            }

            string targetSceneGuid =
                AssetDatabase.AssetPathToGUID(sceneAssetPath);
            if (string.IsNullOrEmpty(targetSceneGuid))
            {
                failure = AttachPreparedPrecompileFailure(
                    UnityRuntimeValidationBridge.BuildError(
                        code: "RUN002",
                        message: "The ClientSim target scene has no asset GUID.",
                        request: request,
                        diagnostics: DiagFrom(
                            "scene_path",
                            "asset_identity",
                            sceneAssetPath),
                        readOnly: true,
                        executed: false),
                    initialScenes);
                return false;
            }

            if (EditorApplication.timeSinceStartup >= operationDeadline)
            {
                failure = AttachPreparedPrecompileFailure(
                    UnityRuntimeValidationBridge.BuildError(
                        code: "CLIENTSIM_PREFLIGHT_TIMEOUT",
                        message: "ClientSim preflight exceeded the operation deadline before compile.",
                        request: request,
                        diagnostics: DiagFrom(
                            "validate_runtime",
                            "timeout",
                            $"deadline={operationDeadline}"),
                        readOnly: true,
                        executed: false),
                    initialScenes);
                return false;
            }

            prepared = new PreparedClientSim
            {
                SceneAssetPath = sceneAssetPath,
                TargetSceneGuid = targetSceneGuid,
                PreviousStartSceneWasNull =
                    previousStartScene == null,
                PreviousStartSceneGuid =
                    previousStartSceneGuid,
                OperationDeadline = operationDeadline,
                InitialScenes = initialScenes,
            };
            return true;
        }

        private static UnityRuntimeValidationBridge.RuntimeResponse
            AttachPreparedPrecompileFailure(
                UnityRuntimeValidationBridge.RuntimeResponse failure,
                RuntimeCompileAudit.SceneIdentity[] initialScenes)
        {
            return AttachTransactionEvidence(
                failure,
                new UnityRuntimeValidationBridge.RuntimeCompileReport(),
                initialScenes,
                clientSimExecuted: false,
                beforeSnapshot: null,
                runtimeSnapshot: null,
                afterSnapshot: null,
                sideEffectReport: null);
        }

        internal static void BeginPreparedClientSim(
            UnityRuntimeValidationBridge.RuntimeRequest request,
            string responsePath,
            PreparedClientSim prepared,
            UnityRuntimeValidationBridge.RuntimeCompileReport compileReport)
        {
            Scene activeScene;
            UnityRuntimeValidationBridge.RuntimeResponse revalidationFailure;
            if (!TryRevalidatePreparedClientSim(
                    request,
                    prepared,
                    out activeScene,
                    out revalidationFailure))
            {
                WriteImmediate(
                    responsePath,
                    AttachTransactionEvidence(
                        revalidationFailure,
                        compileReport,
                        prepared?.InitialScenes,
                        clientSimExecuted: false,
                        beforeSnapshot: null,
                        runtimeSnapshot: null,
                        afterSnapshot: null,
                        sideEffectReport: null));
                return;
            }

            UnityRuntimeValidationBridge.SceneSideEffectSnapshot beforeSnapshot =
                CaptureSceneSnapshot(activeScene);
            var lease = new RestorationLease
            {
                previousStartSceneWasNull =
                    prepared.PreviousStartSceneWasNull,
                previousStartSceneGuid =
                    prepared.PreviousStartSceneGuid,
                targetSceneGuid = prepared.TargetSceneGuid,
                targetScenePath = prepared.SceneAssetPath,
                responsePath = responsePath,
                clientSimExecuted = false,
                compileReport = compileReport,
                initialSceneSnapshot = prepared.InitialScenes,
                beforeSnapshot = beforeSnapshot,
            };
            var state = new OperationState
            {
                request = request,
                responsePath = responsePath,
                sceneAssetPath = prepared.SceneAssetPath,
                phase = EnteringPlayModePhase,
                operationDeadline = prepared.OperationDeadline,
                clientSimExecuted = false,
                compileReport = compileReport,
                initialSceneSnapshot = prepared.InitialScenes,
                beforeSnapshot = beforeSnapshot,
            };
            EnsureSubscribed();
            SaveRestorationLease(lease);
            SaveOperation(state);

            state.clientSimExecuted = true;
            lease.clientSimExecuted = true;
            SaveRestorationLease(lease);
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
                    diagnostics: DiagFrom(
                        "validate_runtime",
                        "exception",
                        ex.ToString()),
                    clientSimReady: false,
                    readOnly: false,
                    executed: true);
                Finish(state);
            }
        }

        private static bool TryRevalidatePreparedClientSim(
            UnityRuntimeValidationBridge.RuntimeRequest request,
            PreparedClientSim prepared,
            out Scene activeScene,
            out UnityRuntimeValidationBridge.RuntimeResponse failure)
        {
            activeScene = default(Scene);
            failure = null;
            if (prepared == null
                || prepared.InitialScenes == null
                || prepared.InitialScenes.Length != 1)
            {
                failure = UnityRuntimeValidationBridge.BuildError(
                    code: "CLIENTSIM_STATE_INVALID",
                    message: "Prepared ClientSim identity evidence is incomplete.",
                    request: request,
                    diagnostics: DiagFrom(
                        "validate_runtime",
                        "state",
                        "prepared ClientSim identity was missing"),
                    readOnly: false,
                    executed: false);
                return false;
            }

            if (HasPersistedState())
            {
                failure = UnityRuntimeValidationBridge.BuildError(
                    code: "CLIENTSIM_ALREADY_RUNNING",
                    message: "Another ClientSim validation operation acquired cleanup ownership during compile.",
                    request: request,
                    diagnostics: DiagFrom(
                        "validate_runtime",
                        "busy",
                        "persisted operation or restoration lease exists"),
                    readOnly: false,
                    executed: false);
                return false;
            }

            if (EditorApplication.isPlayingOrWillChangePlaymode)
            {
                failure = UnityRuntimeValidationBridge.BuildError(
                    code: "CLIENTSIM_EDITOR_NOT_READY",
                    message: "Unity left stable Edit Mode during compile.",
                    request: request,
                    diagnostics: DiagFrom(
                        "validate_runtime",
                        "editor_state",
                        "Unity is playing or changing Play Mode"),
                    readOnly: false,
                    executed: false);
                return false;
            }

            if (EditorApplication.timeSinceStartup
                >= prepared.OperationDeadline)
            {
                failure = UnityRuntimeValidationBridge.BuildError(
                    code: "CLIENTSIM_PREFLIGHT_TIMEOUT",
                    message: "The compound runtime deadline expired after compile and before Play Mode.",
                    request: request,
                    diagnostics: DiagFrom(
                        "validate_runtime",
                        "timeout",
                        $"deadline={prepared.OperationDeadline}"),
                    readOnly: false,
                    executed: false);
                return false;
            }

            activeScene = SceneManager.GetActiveScene();
            RuntimeCompileAudit.SceneIdentity initialScene =
                prepared.InitialScenes[0];
            if (SceneManager.sceneCount != 1
                || !activeScene.IsValid()
                || !activeScene.isLoaded
                || !string.Equals(
                    activeScene.path,
                    prepared.SceneAssetPath,
                    StringComparison.Ordinal)
                || activeScene.handle != initialScene.handle)
            {
                failure = UnityRuntimeValidationBridge.BuildError(
                    code: "CLIENTSIM_ACTIVE_SCENE_REQUIRED",
                    message: "The requested sole active Scene identity changed during compile.",
                    request: request,
                    diagnostics: DiagFrom(
                        "scene_path",
                        "editor_state",
                        $"requested={prepared.SceneAssetPath}; active={activeScene.path}; loaded_scene_count={SceneManager.sceneCount}"),
                    readOnly: false,
                    executed: false);
                return false;
            }

            string targetSceneGuid =
                AssetDatabase.AssetPathToGUID(activeScene.path);
            if (!string.Equals(
                    targetSceneGuid,
                    prepared.TargetSceneGuid,
                    StringComparison.Ordinal))
            {
                failure = UnityRuntimeValidationBridge.BuildError(
                    code: "RUN002",
                    message: "The ClientSim target Scene identity changed during compile.",
                    request: request,
                    diagnostics: DiagFrom(
                        "scene_path",
                        "asset_identity",
                        activeScene.path),
                    readOnly: false,
                    executed: false);
                return false;
            }

            SceneAsset currentStartScene =
                EditorSceneManager.playModeStartScene;
            bool currentStartSceneWasNull =
                currentStartScene == null;
            string currentStartScenePath =
                currentStartSceneWasNull
                    ? string.Empty
                    : AssetDatabase.GetAssetPath(
                        currentStartScene);
            string currentStartSceneGuid =
                string.IsNullOrEmpty(currentStartScenePath)
                    ? string.Empty
                    : AssetDatabase.AssetPathToGUID(
                        currentStartScenePath);
            if (currentStartSceneWasNull
                    != prepared.PreviousStartSceneWasNull
                || !string.Equals(
                    currentStartSceneGuid,
                    prepared.PreviousStartSceneGuid,
                    StringComparison.Ordinal))
            {
                failure = UnityRuntimeValidationBridge.BuildError(
                    code: "CLIENTSIM_START_SCENE_UNRESTORABLE",
                    message: "The Play Mode start Scene changed during compile.",
                    request: request,
                    diagnostics: DiagFrom(
                        "playModeStartScene",
                        "restore_preflight",
                        currentStartScenePath),
                    readOnly: false,
                    executed: false);
                return false;
            }

            UnityRuntimeValidationBridge.RuntimeResponse preflightFailure =
                TryPreflightClientSim(request);
            if (preflightFailure != null)
            {
                failure = preflightFailure;
                return false;
            }
            return true;
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
                diagnostics
                ?? Array.Empty<UnityRuntimeValidationBridge.RuntimeDiagnostic>();
            state.terminalClientSimReady = clientSimReady;
            state.terminalReadOnly = readOnly;
            state.terminalExecuted = executed;
            SaveOperation(state);
            PersistLeaseEvidence(state);
        }

        private static void PersistLeaseEvidence(
            OperationState state)
        {
            RestorationLease lease =
                LoadRestorationLease(out _);
            if (lease == null)
            {
                return;
            }
            lease.clientSimExecuted =
                state.clientSimExecuted;
            lease.compileReport = state.compileReport;
            lease.initialSceneSnapshot =
                state.initialSceneSnapshot;
            lease.beforeSnapshot = state.beforeSnapshot;
            lease.runtimeSnapshot = state.runtimeSnapshot;
            SaveRestorationLease(lease);
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
                    diagnostics: DiagFrom(
                        "playModeStartScene",
                        "restore_error",
                        leaseError),
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
                    diagnostics: DiagFrom(
                        "validate_runtime",
                        "state",
                        state.phase ?? string.Empty),
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
                        clientSimReady:
                            state.terminalClientSimReady,
                        sideEffectReport: report)
                    : UnityRuntimeValidationBridge.BuildError(
                        code: state.terminalCode,
                        message: state.terminalMessage,
                        request: state.request,
                        diagnostics: state.terminalDiagnostics,
                        readOnly: state.terminalReadOnly,
                        executed: state.terminalExecuted,
                        sideEffectReport: report);
            response = AttachTransactionEvidence(
                response,
                state.compileReport,
                state.initialSceneSnapshot,
                state.clientSimExecuted,
                state.beforeSnapshot,
                state.runtimeSnapshot,
                afterSnapshot,
                report);

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
            bool restored =
                RestorePlayModeStartScene(lease, out restoreError);
            if (!restored)
            {
                return;
            }

            var request =
                new UnityRuntimeValidationBridge.RuntimeRequest
                {
                    action = "validate_runtime",
                    scene_path = lease.targetScenePath,
                    profile = "clientsim",
                };
            Scene activeScene = SceneManager.GetActiveScene();
            UnityRuntimeValidationBridge.SceneSideEffectSnapshot afterSnapshot =
                activeScene.IsValid()
                && activeScene.isLoaded
                && string.Equals(
                    activeScene.path,
                    lease.targetScenePath,
                    StringComparison.Ordinal)
                    ? CaptureSceneSnapshot(activeScene)
                    : null;
            UnityRuntimeValidationBridge.ClientSimSideEffectReport report =
                BuildSideEffectReport(
                    lease.targetScenePath,
                    lease.beforeSnapshot,
                    lease.runtimeSnapshot,
                    afterSnapshot);
            UnityRuntimeValidationBridge.RuntimeResponse response =
                UnityRuntimeValidationBridge.BuildError(
                    code: "CLIENTSIM_STATE_CORRUPT",
                    message: "ClientSim operation state was corrupt; editor state was restored.",
                    request: request,
                    diagnostics: DiagFrom(
                        "validate_runtime",
                        "state",
                        evidence),
                    readOnly: false,
                    executed: lease.clientSimExecuted,
                    sideEffectReport: report);
            response = AttachTransactionEvidence(
                response,
                lease.compileReport,
                lease.initialSceneSnapshot,
                lease.clientSimExecuted,
                lease.beforeSnapshot,
                lease.runtimeSnapshot,
                afterSnapshot,
                report);

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
            string json = SessionState.GetString(
                OperationStateKey,
                string.Empty);
            if (string.IsNullOrEmpty(json))
            {
                return null;
            }
            try
            {
                OperationState state =
                    JsonUtility.FromJson<OperationState>(json);
                if (state == null
                    || state.request == null
                    || string.IsNullOrEmpty(state.responsePath)
                    || string.IsNullOrEmpty(state.sceneAssetPath)
                    || string.IsNullOrEmpty(state.phase)
                    || state.compileReport == null
                    || state.initialSceneSnapshot == null
                    || state.beforeSnapshot == null)
                {
                    error =
                        "ClientSim operation record is incomplete.";
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

        private static RestorationLease LoadRestorationLease(
            out string error)
        {
            error = string.Empty;
            string json = SessionState.GetString(
                RestorationLeaseKey,
                string.Empty);
            if (string.IsNullOrEmpty(json))
            {
                error =
                    "ClientSim restoration lease is missing.";
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
                    error =
                        "ClientSim restoration lease is incomplete.";
                    return null;
                }
                if (!lease.previousStartSceneWasNull
                    && string.IsNullOrEmpty(
                        lease.previousStartSceneGuid))
                {
                    error =
                        "ClientSim restoration lease lost the previous start scene GUID.";
                    return null;
                }
                if (lease.compileReport == null)
                {
                    lease.compileReport =
                        new UnityRuntimeValidationBridge.RuntimeCompileReport();
                }
                if (lease.initialSceneSnapshot == null)
                {
                    lease.initialSceneSnapshot =
                        Array.Empty<RuntimeCompileAudit.SceneIdentity>();
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

        private static UnityRuntimeValidationBridge.RuntimeResponse
            AttachTransactionEvidence(
                UnityRuntimeValidationBridge.RuntimeResponse response,
                UnityRuntimeValidationBridge.RuntimeCompileReport compileReport,
                RuntimeCompileAudit.SceneIdentity[] initialSceneSnapshot,
                bool clientSimExecuted,
                UnityRuntimeValidationBridge.SceneSideEffectSnapshot beforeSnapshot,
                UnityRuntimeValidationBridge.SceneSideEffectSnapshot runtimeSnapshot,
                UnityRuntimeValidationBridge.SceneSideEffectSnapshot afterSnapshot,
                UnityRuntimeValidationBridge.ClientSimSideEffectReport sideEffectReport)
        {
            response.data.compile = compileReport;
            response.data.clientsim =
                new UnityRuntimeValidationBridge.RuntimeClientSimReport
                {
                    executed = clientSimExecuted,
                    initial_scene_snapshot =
                        initialSceneSnapshot,
                    before = beforeSnapshot,
                    runtime = runtimeSnapshot,
                    after = afterSnapshot,
                    side_effect_report = sideEffectReport,
                };
            response.data.udon_program_count =
                compileReport.program_count;
            response.data.executed = clientSimExecuted;
            response.data.read_only =
                !compileReport.executed
                && !clientSimExecuted;
            response.data.side_effect_report =
                sideEffectReport;
            if (response.success
                && string.Equals(
                    compileReport.severity,
                    "warning",
                    StringComparison.Ordinal))
            {
                response.severity = "warning";
            }
            return response;
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
                    || !EditorUtility.IsDirty(asset)
                    || !AssetDatabase.IsNativeAsset(asset))
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
