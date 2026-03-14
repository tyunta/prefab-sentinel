using System;
using System.Collections;
using System.IO;
using System.Reflection;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.SceneManagement;

namespace PrefabSentinel
{
    /// <summary>
    /// Unity executeMethod endpoint for runtime validation batchmode execution.
    /// Reads a JSON request file, performs compile or ClientSim startup checks,
    /// writes a JSON response file, and exits the editor process explicitly.
    /// </summary>
    public static class UnityRuntimeValidationBridge
    {
        private const int ProtocolVersion = 1;
        private const string RequestArg = "-unitytoolRuntimeRequest";
        private const string ResponseArg = "-unitytoolRuntimeResponse";
        private const string DefaultProjectRootName = "project";

        [Serializable]
        public sealed class RuntimeRequest
        {
            public int protocol_version = 0;
            public string action = string.Empty;
            public string project_root = string.Empty;
            public string scene_path = string.Empty;
            public string profile = string.Empty;
            public int timeout_sec = 60;
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

        public static void RunFromJson()
        {
            string requestPath;
            string responsePath;
            if (!TryGetArgValue(RequestArg, out requestPath) || !TryGetArgValue(ResponseArg, out responsePath))
            {
                Debug.LogError("Runtime validation bridge requires request and response file arguments.");
                EditorApplication.Exit(1);
                return;
            }

            RuntimeRequest request;
            try
            {
                string requestJson = File.ReadAllText(requestPath);
                request = JsonUtility.FromJson<RuntimeRequest>(requestJson);
            }
            catch (Exception ex)
            {
                ExitWithResponse(
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
                ExitWithResponse(
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
                ExitWithResponse(responsePath, ExecuteCompile(request));
                return;
            }

            if (string.Equals(request.action, "run_clientsim", StringComparison.Ordinal))
            {
                RuntimeValidationRunner.Begin(request, responsePath);
                return;
            }

            ExitWithResponse(
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

        internal static void ExitWithResponse(string responsePath, RuntimeResponse response)
        {
            try
            {
                File.WriteAllText(responsePath, JsonUtility.ToJson(response));
            }
            catch (Exception ex)
            {
                Debug.LogError($"Failed to write runtime validation response: {ex}");
                EditorApplication.Exit(1);
                return;
            }

            EditorApplication.Exit(0);
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
                    message: "UdonSharp compile completed in Unity batchmode.",
                    request: request,
                    udonProgramCount: programCount
                );
            }
            catch (TargetInvocationException ex)
            {
                return BuildError(
                    code: "RUN_COMPILE_FAILED",
                    message: $"UdonSharp compile threw an exception: {ex.InnerException?.Message ?? ex.Message}",
                    request: request,
                    diagnostics: new[]
                    {
                        new RuntimeDiagnostic
                        {
                            location = "compile_udonsharp",
                            detail = "exception",
                            evidence = (ex.InnerException ?? ex).ToString()
                        }
                    },
                    readOnly: false,
                    executed: true
                );
            }
            catch (Exception ex)
            {
                return BuildError(
                    code: "RUN_COMPILE_FAILED",
                    message: $"Unexpected compile exception: {ex.Message}",
                    request: request,
                    diagnostics: new[]
                    {
                        new RuntimeDiagnostic
                        {
                            location = "compile_udonsharp",
                            detail = "exception",
                            evidence = ex.ToString()
                        }
                    },
                    readOnly: false,
                    executed: true
                );
            }
        }

        internal static RuntimeResponse BuildSkip(string code, string message, RuntimeRequest request)
        {
            return new RuntimeResponse
            {
                success = true,
                severity = "warning",
                code = code,
                message = message,
                data = new RuntimeData
                {
                    project_root = string.IsNullOrWhiteSpace(request.project_root) ? DefaultProjectRootName : request.project_root,
                    scene_path = request.scene_path ?? string.Empty,
                    profile = request.profile ?? string.Empty,
                    timeout_sec = request.timeout_sec,
                    read_only = true,
                    executed = false,
                },
                diagnostics = Array.Empty<RuntimeDiagnostic>(),
            };
        }

        internal static RuntimeResponse BuildSuccess(
            string code,
            string message,
            RuntimeRequest request,
            int udonProgramCount = 0,
            bool clientSimReady = false
        )
        {
            return new RuntimeResponse
            {
                success = true,
                severity = "info",
                code = code,
                message = message,
                data = new RuntimeData
                {
                    project_root = string.IsNullOrWhiteSpace(request.project_root) ? DefaultProjectRootName : request.project_root,
                    scene_path = request.scene_path ?? string.Empty,
                    profile = request.profile ?? string.Empty,
                    timeout_sec = request.timeout_sec,
                    udon_program_count = udonProgramCount,
                    clientsim_ready = clientSimReady,
                    read_only = false,
                    executed = true,
                },
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
            bool clientSimReady = false
        )
        {
            return new RuntimeResponse
            {
                success = false,
                severity = "error",
                code = code,
                message = message,
                data = new RuntimeData
                {
                    project_root = string.IsNullOrWhiteSpace(request.project_root) ? DefaultProjectRootName : request.project_root,
                    scene_path = request.scene_path ?? string.Empty,
                    profile = request.profile ?? string.Empty,
                    timeout_sec = request.timeout_sec,
                    udon_program_count = udonProgramCount,
                    clientsim_ready = clientSimReady,
                    read_only = readOnly,
                    executed = executed,
                },
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

        internal static bool TryGetArgValue(string argName, out string value)
        {
            value = string.Empty;
            string[] args = Environment.GetCommandLineArgs();
            for (int i = 0; i < args.Length - 1; i++)
            {
                if (!string.Equals(args[i], argName, StringComparison.Ordinal))
                {
                    continue;
                }

                value = args[i + 1] ?? string.Empty;
                return !string.IsNullOrWhiteSpace(value);
            }

            return false;
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
                if (!fullScenePath.StartsWith(fullProjectRoot, StringComparison.OrdinalIgnoreCase))
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

    [ExecuteAlways]
    internal sealed class RuntimeValidationRunner : MonoBehaviour
    {
        private UnityRuntimeValidationBridge.RuntimeRequest _request;
        private string _responsePath = string.Empty;
        private bool _started;

        public static void Begin(UnityRuntimeValidationBridge.RuntimeRequest request, string responsePath)
        {
            GameObject host = new GameObject("__PrefabSentinelRuntimeValidationRunner");
            DontDestroyOnLoad(host);
            RuntimeValidationRunner runner = host.AddComponent<RuntimeValidationRunner>();
            runner._request = request;
            runner._responsePath = responsePath ?? string.Empty;
        }

        private void Start()
        {
            if (_started)
            {
                return;
            }

            _started = true;
            StartCoroutine(Run());
        }

        private IEnumerator Run()
        {
            UnityRuntimeValidationBridge.RuntimeResponse response = null;
            try
            {
                yield return ExecuteClientSim(result => response = result);
                Finish(
                    response ?? UnityRuntimeValidationBridge.BuildError(
                        code: "RUN002",
                        message: "ClientSim runner completed without producing a response.",
                        request: _request,
                        diagnostics: Array.Empty<UnityRuntimeValidationBridge.RuntimeDiagnostic>(),
                        readOnly: false,
                        executed: true
                    )
                );
            }
            catch (Exception ex)
            {
                Finish(
                    UnityRuntimeValidationBridge.BuildError(
                        code: "RUN002",
                        message: $"Unexpected ClientSim exception: {ex.Message}",
                        request: _request,
                        diagnostics: new[]
                        {
                            new UnityRuntimeValidationBridge.RuntimeDiagnostic
                            {
                                location = "run_clientsim",
                                detail = "exception",
                                evidence = ex.ToString()
                            }
                        },
                        readOnly: false,
                        executed: true
                    )
                );
            }
        }

        private IEnumerator ExecuteClientSim(Action<UnityRuntimeValidationBridge.RuntimeResponse> complete)
        {
            string sceneAssetPath;
            string sceneError;
            if (!UnityRuntimeValidationBridge.TryResolveSceneAssetPath(_request, out sceneAssetPath, out sceneError))
            {
                complete(
                    UnityRuntimeValidationBridge.BuildError(
                        code: "RUN002",
                        message: "ClientSim scene path is invalid.",
                        request: _request,
                        diagnostics: new[]
                        {
                            new UnityRuntimeValidationBridge.RuntimeDiagnostic
                            {
                                location = "scene_path",
                                detail = "schema_error",
                                evidence = sceneError
                            }
                        },
                        readOnly: true,
                        executed: false
                    )
                );
                yield break;
            }

            Type clientSimSettingsType = UnityRuntimeValidationBridge.FindType("VRC.SDK3.ClientSim.ClientSimSettings, VRC.ClientSim");
            Type clientSimMainType = UnityRuntimeValidationBridge.FindType("VRC.SDK3.ClientSim.ClientSimMain, VRC.ClientSim");
            if (clientSimSettingsType == null || clientSimMainType == null)
            {
                complete(
                    UnityRuntimeValidationBridge.BuildSkip(
                        code: "RUN_CLIENTSIM_SKIPPED",
                        message: "ClientSim runtime assembly was not found; smoke check skipped.",
                        request: _request
                    )
                );
                yield break;
            }

            MethodInfo createInstance = clientSimMainType.GetMethod("CreateInstance", BindingFlags.Public | BindingFlags.Static);
            MethodInfo removeInstance = clientSimMainType.GetMethod("RemoveInstance", BindingFlags.Public | BindingFlags.Static);
            MethodInfo isNetworkReady = clientSimMainType.GetMethod("IsNetworkReady", BindingFlags.Public | BindingFlags.Instance);
            MethodInfo initializeClientSim = clientSimMainType.GetMethod("InitializeClientSim", BindingFlags.NonPublic | BindingFlags.Instance);
            if (createInstance == null || removeInstance == null || isNetworkReady == null || initializeClientSim == null)
            {
                complete(
                    UnityRuntimeValidationBridge.BuildSkip(
                        code: "RUN_CLIENTSIM_SKIPPED",
                        message: "Required ClientSim startup APIs were not found; smoke check skipped.",
                        request: _request
                    )
                );
                yield break;
            }

            try
            {
                Scene openedScene = EditorSceneManager.OpenScene(sceneAssetPath, OpenSceneMode.Single);
                if (!openedScene.IsValid() || !openedScene.isLoaded)
                {
                    complete(
                        UnityRuntimeValidationBridge.BuildError(
                            code: "RUN002",
                            message: "Failed to open runtime validation scene.",
                            request: _request,
                            diagnostics: new[]
                            {
                                new UnityRuntimeValidationBridge.RuntimeDiagnostic
                                {
                                    location = "scene_path",
                                    detail = "apply_error",
                                    evidence = sceneAssetPath
                                }
                            },
                            readOnly: false,
                            executed: true
                        )
                    );
                    yield break;
                }

                object settings = Activator.CreateInstance(clientSimSettingsType);
                UnityRuntimeValidationBridge.SetFieldIfPresent(settings, "enableClientSim", true);
                UnityRuntimeValidationBridge.SetFieldIfPresent(settings, "displayLogs", true);
                UnityRuntimeValidationBridge.SetFieldIfPresent(settings, "deleteEditorOnly", true);
                UnityRuntimeValidationBridge.SetFieldIfPresent(settings, "spawnPlayer", true);
                UnityRuntimeValidationBridge.SetFieldIfPresent(settings, "hideMenuOnLaunch", true);
                UnityRuntimeValidationBridge.SetFieldIfPresent(settings, "initializationDelay", 0f);

                removeInstance.Invoke(null, null);
                createInstance.Invoke(null, new object[] { settings, null });

                UnityEngine.Object[] foundInstances = Resources.FindObjectsOfTypeAll(clientSimMainType);
                if (foundInstances == null || foundInstances.Length == 0)
                {
                    complete(
                        UnityRuntimeValidationBridge.BuildError(
                            code: "RUN002",
                            message: "ClientSim instance was not created for the target scene.",
                            request: _request,
                            diagnostics: Array.Empty<UnityRuntimeValidationBridge.RuntimeDiagnostic>(),
                            readOnly: false,
                            executed: true
                        )
                    );
                    yield break;
                }

                object instance = foundInstances[0];
                IEnumerator initRoutine = initializeClientSim.Invoke(instance, null) as IEnumerator;
                if (initRoutine == null)
                {
                    complete(
                        UnityRuntimeValidationBridge.BuildError(
                            code: "RUN002",
                            message: "ClientSim initialization coroutine was not available.",
                            request: _request,
                            diagnostics: Array.Empty<UnityRuntimeValidationBridge.RuntimeDiagnostic>(),
                            readOnly: false,
                            executed: true
                        )
                    );
                    yield break;
                }

                bool initComplete = false;
                Exception initFailure = null;
                Coroutine active = StartCoroutine(GuardCoroutine(initRoutine, () => initComplete = true, ex => initFailure = ex));
                double deadline = EditorApplication.timeSinceStartup + Math.Max(_request.timeout_sec, 1);
                while (!initComplete && initFailure == null && EditorApplication.timeSinceStartup < deadline)
                {
                    yield return null;
                }

                if (!initComplete && initFailure == null)
                {
                    if (active != null)
                    {
                        StopCoroutine(active);
                    }

                    complete(
                        UnityRuntimeValidationBridge.BuildError(
                            code: "RUN002",
                            message: "ClientSim initialization timed out.",
                            request: _request,
                            diagnostics: new[]
                            {
                                new UnityRuntimeValidationBridge.RuntimeDiagnostic
                                {
                                    location = "run_clientsim",
                                    detail = "timeout",
                                    evidence = $"timeout_sec={_request.timeout_sec}"
                                }
                            },
                            readOnly: false,
                            executed: true
                        )
                    );
                    yield break;
                }

                if (initFailure != null)
                {
                    complete(
                        UnityRuntimeValidationBridge.BuildError(
                            code: "RUN002",
                            message: $"ClientSim initialization failed: {initFailure.Message}",
                            request: _request,
                            diagnostics: new[]
                            {
                                new UnityRuntimeValidationBridge.RuntimeDiagnostic
                                {
                                    location = "run_clientsim",
                                    detail = "exception",
                                    evidence = initFailure.ToString()
                                }
                            },
                            readOnly: false,
                            executed: true
                        )
                    );
                    yield break;
                }

                bool ready = Convert.ToBoolean(isNetworkReady.Invoke(instance, null));
                if (!ready)
                {
                    complete(
                        UnityRuntimeValidationBridge.BuildError(
                            code: "RUN002",
                            message: "ClientSim coroutine completed without reaching ready state.",
                            request: _request,
                            diagnostics: Array.Empty<UnityRuntimeValidationBridge.RuntimeDiagnostic>(),
                            readOnly: false,
                            executed: true
                        )
                    );
                    yield break;
                }

                complete(
                    UnityRuntimeValidationBridge.BuildSuccess(
                        code: "RUN_CLIENTSIM_OK",
                        message: "ClientSim smoke completed in Unity batchmode.",
                        request: _request,
                        clientSimReady: true
                    )
                );
            }
            catch (TargetInvocationException ex)
            {
                complete(
                    UnityRuntimeValidationBridge.BuildError(
                        code: "RUN002",
                        message: $"ClientSim startup threw an exception: {ex.InnerException?.Message ?? ex.Message}",
                        request: _request,
                        diagnostics: new[]
                        {
                            new UnityRuntimeValidationBridge.RuntimeDiagnostic
                            {
                                location = "run_clientsim",
                                detail = "exception",
                                evidence = (ex.InnerException ?? ex).ToString()
                            }
                        },
                        readOnly: false,
                        executed: true
                    )
                );
            }
            finally
            {
                try
                {
                    removeInstance?.Invoke(null, null);
                }
                catch (Exception cleanupEx)
                {
                    Debug.LogWarning($"ClientSim cleanup failed: {cleanupEx}");
                }
            }
        }

        private IEnumerator GuardCoroutine(IEnumerator routine, Action onComplete, Action<Exception> onError)
        {
            while (true)
            {
                object current;
                try
                {
                    if (!routine.MoveNext())
                    {
                        onComplete?.Invoke();
                        yield break;
                    }

                    current = routine.Current;
                }
                catch (Exception ex)
                {
                    onError?.Invoke(ex);
                    yield break;
                }

                yield return current;
            }
        }

        private void Finish(UnityRuntimeValidationBridge.RuntimeResponse response)
        {
            UnityRuntimeValidationBridge.ExitWithResponse(_responsePath, response);
            if (this != null && gameObject != null)
            {
                DestroyImmediate(gameObject);
            }
        }
    }
}
