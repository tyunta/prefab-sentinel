using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Reflection;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.SceneManagement;
namespace PrefabSentinel
{
    /// <summary>
    /// Unity executeMethod endpoint for UNITYTOOL_UNITY_EXECUTE_METHOD.
    /// Applies a scoped subset of patch operations to prefab assets via SerializedObject.
    /// </summary>
    public static class UnityPatchBridge
    {
        private const int ProtocolVersion = 2;
        private const string RequestArg = "-sentinelPatchRequest";
        private const string ResponseArg = "-sentinelPatchResponse";
        private const string ArrayDataSuffix = ".Array.data";
        private const string SceneHandleName = "scene";
        private const string AssetHandleName = "asset";
        [ThreadStatic]
        private static Dictionary<string, UnityEngine.Object> s_currentHandles;

        private static readonly PropertyInfo SerializedPropertyGradientValueProperty = typeof(SerializedProperty)
            .GetProperty("gradientValue", BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic);
        private static readonly PropertyInfo SerializedPropertyIsFixedBufferProperty = typeof(SerializedProperty)
            .GetProperty("isFixedBuffer", BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic);
        private static readonly PropertyInfo SerializedPropertyFixedBufferSizeProperty = typeof(SerializedProperty)
            .GetProperty("fixedBufferSize", BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic);

        [Serializable]
        private sealed class BridgeRequest
        {
            public int protocol_version = 0;
            public string target = string.Empty;
            public string kind = string.Empty;
            public string mode = "open";
            public PatchOp[] ops = Array.Empty<PatchOp>();
        }

        [Serializable]
        private sealed class PatchOp
        {
            public string op = string.Empty;
            public string name = string.Empty;
            public string result = string.Empty;
            public string parent = string.Empty;
            public string target = string.Empty;
            public string type = string.Empty;
            public string shader = string.Empty;
            public string prefab = string.Empty;
            public string component = string.Empty;
            public string path = string.Empty;
            public int index = 0;
            public string value_kind = string.Empty;
            public string value_string = string.Empty;
            public int value_int = 0;
            public float value_float = 0f;
            public bool value_bool = false;
            public string value_json = string.Empty;
        }

        [Serializable]
        private sealed class ColorPayload
        {
            public float r = 0f;
            public float g = 0f;
            public float b = 0f;
            public float a = 1f;
        }

        [Serializable]
        private sealed class Vector2Payload
        {
            public float x = 0f;
            public float y = 0f;
        }

        [Serializable]
        private sealed class Vector3Payload
        {
            public float x = 0f;
            public float y = 0f;
            public float z = 0f;
        }

        [Serializable]
        private sealed class Vector4Payload
        {
            public float x = 0f;
            public float y = 0f;
            public float z = 0f;
            public float w = 0f;
        }

        [Serializable]
        private sealed class QuaternionPayload
        {
            public float x = 0f;
            public float y = 0f;
            public float z = 0f;
            public float w = 1f;
        }

        [Serializable]
        private sealed class ObjectReferencePayload
        {
            public string guid = string.Empty;
            public long fileID = 0;   // Unity native format (plan_generators output)
            public long file_id = 0;  // snake_case format (example plan compat)
        }

        [Serializable]
        private sealed class RectPayload
        {
            public float x = 0f;
            public float y = 0f;
            public float width = 0f;
            public float height = 0f;
        }

        [Serializable]
        private sealed class BoundsPayload
        {
            public Vector3Payload center = new Vector3Payload();
            public Vector3Payload size = new Vector3Payload();
        }

        [Serializable]
        private sealed class Vector2IntPayload
        {
            public int x = 0;
            public int y = 0;
        }

        [Serializable]
        private sealed class Vector3IntPayload
        {
            public int x = 0;
            public int y = 0;
            public int z = 0;
        }

        [Serializable]
        private sealed class RectIntPayload
        {
            public int x = 0;
            public int y = 0;
            public int width = 0;
            public int height = 0;
        }

        [Serializable]
        private sealed class BoundsIntPayload
        {
            public Vector3IntPayload position = new Vector3IntPayload();
            public Vector3IntPayload size = new Vector3IntPayload();
        }

        [Serializable]
        private sealed class AnimationCurvePayload
        {
            public AnimationCurveKeyPayload[] keys = Array.Empty<AnimationCurveKeyPayload>();
            public int pre_wrap_mode = (int)WrapMode.Default;
            public int post_wrap_mode = (int)WrapMode.Default;
        }

        [Serializable]
        private sealed class AnimationCurveKeyPayload
        {
            public float time = 0f;
            public float value = 0f;
            public float in_tangent = 0f;
            public float out_tangent = 0f;
        }

        [Serializable]
        private sealed class GradientPayload
        {
            public GradientColorKeyPayload[] color_keys = Array.Empty<GradientColorKeyPayload>();
            public GradientAlphaKeyPayload[] alpha_keys = Array.Empty<GradientAlphaKeyPayload>();
            public int mode = 0;
        }

        [Serializable]
        private sealed class GradientColorKeyPayload
        {
            public ColorPayload color = new ColorPayload();
            public float time = 0f;
        }

        [Serializable]
        private sealed class GradientAlphaKeyPayload
        {
            public float alpha = 1f;
            public float time = 0f;
        }

        [Serializable]
        private sealed class ManagedReferenceTypeHintPayload
        {
            public string __type = string.Empty;
        }

        [Serializable]
        private sealed class BridgeResponse
        {
            public int protocol_version = ProtocolVersion;
            public bool success = false;
            public string severity = "error";
            public string code = "UNITY_BRIDGE_ERROR";
            public string message = "Unity bridge failed.";
            public BridgeData data = new BridgeData();
            public BridgeDiagnostic[] diagnostics = Array.Empty<BridgeDiagnostic>();
        }

        [Serializable]
        private sealed class BridgeData
        {
            public string target = string.Empty;
            public int op_count = 0;
            public int applied = 0;
            public bool read_only = false;
            public bool executed = false;
            public int protocol_version = ProtocolVersion;
        }

        [Serializable]
        private sealed class BridgeDiagnostic
        {
            public string path = string.Empty;
            public string location = string.Empty;
            public string detail = string.Empty;
            public string evidence = string.Empty;
        }

        public static void ApplyFromJson()
        {
            string[] args = Environment.GetCommandLineArgs();
            string requestPath = GetArgValue(args, RequestArg);
            string responsePath = GetArgValue(args, ResponseArg);
            if (string.IsNullOrWhiteSpace(requestPath) || string.IsNullOrWhiteSpace(responsePath))
            {
                WriteResponseSafe(
                    responsePath,
                    BuildError(
                        "UNITY_BRIDGE_ARGS",
                        "Missing required command-line args for request/response paths.",
                        target: string.Empty,
                        opCount: 0,
                        executed: false
                    )
                );
                return;
            }

            ApplyFromPaths(requestPath, responsePath);
        }

        /// <summary>
        /// Core bridge logic: reads request JSON, routes to the appropriate handler, writes response JSON.
        /// Extracted from <see cref="ApplyFromJson"/> so that integration tests can invoke it
        /// directly without relying on command-line arguments.
        /// </summary>
        public static void ApplyFromPaths(string requestPath, string responsePath)
        {
            BridgeRequest request;
            try
            {
                string requestJson = File.ReadAllText(requestPath);
                request = JsonUtility.FromJson<BridgeRequest>(requestJson);
                if (request == null)
                {
                    throw new InvalidOperationException("Request JSON root is null.");
                }
            }
            catch (Exception ex)
            {
                WriteResponseSafe(
                    responsePath,
                    BuildError(
                        "UNITY_BRIDGE_REQUEST_JSON",
                        $"Failed to parse request JSON: {ex.Message}",
                        target: string.Empty,
                        opCount: 0,
                        executed: false
                    )
                );
                return;
            }

            if (request.protocol_version != ProtocolVersion)
            {
                WriteResponseSafe(
                    responsePath,
                    BuildError(
                        "UNITY_BRIDGE_PROTOCOL_VERSION",
                        "Bridge protocol version mismatch.",
                        request.target,
                        request.ops?.Length ?? 0,
                        executed: false
                    )
                );
                return;
            }

            if (string.IsNullOrWhiteSpace(request.target))
            {
                WriteResponseSafe(
                    responsePath,
                    BuildError(
                        "UNITY_BRIDGE_SCHEMA",
                        "target is required.",
                        target: string.Empty,
                        opCount: request.ops?.Length ?? 0,
                        executed: false
                    )
                );
                return;
            }

            if (request.ops == null)
            {
                WriteResponseSafe(
                    responsePath,
                    BuildError(
                        "UNITY_BRIDGE_SCHEMA",
                        "ops is required.",
                        request.target,
                        opCount: 0,
                        executed: false
                    )
                );
                return;
            }

            string mode = string.IsNullOrWhiteSpace(request.mode)
                ? "open"
                : request.mode.Trim();
            bool createMode = string.Equals(mode, "create", StringComparison.OrdinalIgnoreCase);

            string assetPath;
            string resolveError;
            if (!TryResolveAssetPath(request.target, createMode, out assetPath, out resolveError))
            {
                WriteResponseSafe(
                    responsePath,
                    BuildError(
                        "UNITY_BRIDGE_TARGET_PATH",
                        resolveError,
                        request.target,
                        request.ops.Length,
                        executed: false
                    )
                );
                return;
            }

            string assetExtension = Path.GetExtension(assetPath);
            if (string.Equals(assetExtension, ".prefab", StringComparison.OrdinalIgnoreCase))
            {
                if (createMode)
                {
                    WriteResponseSafe(responsePath, ApplyPrefabCreateOperations(request, assetPath));
                    return;
                }

                WriteResponseSafe(responsePath, ApplyPrefabOperations(request, assetPath));
                return;
            }

            if (
                string.Equals(assetExtension, ".mat", StringComparison.OrdinalIgnoreCase)
                || string.Equals(assetExtension, ".asset", StringComparison.OrdinalIgnoreCase)
            )
            {
                if (createMode)
                {
                    WriteResponseSafe(responsePath, ApplyAssetCreateOperations(request, assetPath));
                    return;
                }

                WriteResponseSafe(responsePath, ApplyAssetOperations(request, assetPath));
                return;
            }

            if (string.Equals(assetExtension, ".unity", StringComparison.OrdinalIgnoreCase))
            {
                if (createMode)
                {
                    WriteResponseSafe(responsePath, ApplySceneOperations(request, assetPath, true));
                    return;
                }

                WriteResponseSafe(responsePath, ApplySceneOperations(request, assetPath, false));
                return;
            }

            WriteResponseSafe(
                responsePath,
                BuildError(
                    "UNITY_BRIDGE_TARGET_UNSUPPORTED",
                    "executeMethod apply currently supports .prefab, .mat, .asset, and .unity targets only.",
                    request.target,
                    request.ops.Length,
                    executed: false
                )
            );
        }

        private static string GetArgValue(string[] args, string key)
        {
            for (int i = 0; i < args.Length - 1; i++)
            {
                if (string.Equals(args[i], key, StringComparison.Ordinal))
                {
                    return args[i + 1];
                }
            }
            return string.Empty;
        }

        private static BridgeResponse ApplyPrefabOperations(BridgeRequest request, string assetPath)
        {
            int applied = 0;
            List<BridgeDiagnostic> diagnostics = new List<BridgeDiagnostic>();
            GameObject prefabRoot = null;
            try
            {
                prefabRoot = PrefabUtility.LoadPrefabContents(assetPath);
                if (prefabRoot == null)
                {
                    return BuildError(
                        "UNITY_BRIDGE_PREFAB_LOAD",
                        "Failed to load prefab contents.",
                        request.target,
                        request.ops.Length,
                        executed: true
                    );
                }

                for (int i = 0; i < request.ops.Length; i++)
                {
                    PatchOp op = request.ops[i];
                    if (!TryApplyOp(prefabRoot, request.target, op, i, diagnostics))
                    {
                        return BuildError(
                            "UNITY_BRIDGE_APPLY",
                            "SerializedObject apply failed.",
                            request.target,
                            request.ops.Length,
                            executed: true,
                            applied: applied,
                            diagnostics: diagnostics.ToArray()
                        );
                    }
                    applied += 1;
                }

                PrefabUtility.SaveAsPrefabAsset(prefabRoot, assetPath);
                AssetDatabase.SaveAssets();
                AssetDatabase.Refresh(ImportAssetOptions.ForceSynchronousImport);
                return new BridgeResponse
                {
                    protocol_version = ProtocolVersion,
                    success = true,
                    severity = "info",
                    code = "SER_APPLY_OK",
                    message = "SerializedObject patch applied via Unity executeMethod.",
                    data = new BridgeData
                    {
                        target = request.target,
                        op_count = request.ops.Length,
                        applied = applied,
                        read_only = false,
                        executed = true,
                        protocol_version = ProtocolVersion
                    },
                    diagnostics = Array.Empty<BridgeDiagnostic>()
                };
            }
            catch (Exception ex)
            {
                diagnostics.Add(
                    new BridgeDiagnostic
                    {
                        path = request.target,
                        location = "apply",
                        detail = "exception",
                        evidence = ex.ToString()
                    }
                );
                return BuildError(
                    "UNITY_BRIDGE_APPLY_EXCEPTION",
                    $"Unexpected apply exception: {ex.Message}",
                    request.target,
                    request.ops.Length,
                    executed: true,
                    applied: applied,
                    diagnostics: diagnostics.ToArray()
                );
            }
            finally
            {
                if (prefabRoot != null)
                {
                    PrefabUtility.UnloadPrefabContents(prefabRoot);
                }
            }
        }

        private static BridgeResponse ApplyPrefabCreateOperations(BridgeRequest request, string assetPath)
        {
            int applied = 0;
            List<BridgeDiagnostic> diagnostics = new List<BridgeDiagnostic>();
            GameObject prefabRoot = null;
            bool saved = false;
            Dictionary<string, UnityEngine.Object> handles = new Dictionary<string, UnityEngine.Object>(StringComparer.Ordinal);
            try
            {
                if (File.Exists(Path.Combine(Path.GetFullPath(Path.Combine(Application.dataPath, "..")), assetPath)))
                {
                    return BuildError(
                        "UNITY_BRIDGE_TARGET_EXISTS",
                        "target file already exists.",
                        request.target,
                        request.ops.Length,
                        executed: false
                    );
                }

                string fullAssetPath = Path.Combine(
                    Path.GetFullPath(Path.Combine(Application.dataPath, "..")),
                    assetPath
                );
                string parentDir = Path.GetDirectoryName(fullAssetPath);
                if (string.IsNullOrWhiteSpace(parentDir) || !Directory.Exists(parentDir))
                {
                    return BuildError(
                        "UNITY_BRIDGE_TARGET_PATH",
                        "target directory was not found.",
                        request.target,
                        request.ops.Length,
                        executed: false
                    );
                }

                for (int i = 0; i < request.ops.Length; i++)
                {
                    PatchOp op = request.ops[i];
                    string opName = (op?.op ?? string.Empty).Trim();
                    if (
                        string.Equals(opName, "create_prefab", StringComparison.Ordinal)
                        || string.Equals(opName, "create_root", StringComparison.Ordinal)
                    )
                    {
                        if (prefabRoot != null)
                        {
                            diagnostics.Add(
                                new BridgeDiagnostic
                                {
                                    path = request.target,
                                    location = $"ops[{i}].op",
                                    detail = "schema_error",
                                    evidence = "prefab root may be created only once"
                                }
                            );
                            return BuildError(
                                "UNITY_BRIDGE_SCHEMA",
                                "Invalid prefab create plan.",
                                request.target,
                                request.ops.Length,
                                executed: false,
                                applied: applied,
                                diagnostics: diagnostics.ToArray()
                            );
                        }

                        string rootName;
                        if (string.Equals(opName, "create_root", StringComparison.Ordinal))
                        {
                            if (string.IsNullOrWhiteSpace(op.name))
                            {
                                diagnostics.Add(
                                    new BridgeDiagnostic
                                    {
                                        path = request.target,
                                        location = $"ops[{i}].name",
                                        detail = "schema_error",
                                        evidence = "create_root requires name"
                                    }
                                );
                                return BuildError(
                                    "UNITY_BRIDGE_SCHEMA",
                                    "Invalid prefab create plan.",
                                    request.target,
                                    request.ops.Length,
                                    executed: false,
                                    applied: applied,
                                    diagnostics: diagnostics.ToArray()
                                );
                            }
                            rootName = op.name.Trim();
                        }
                        else
                        {
                            rootName = string.IsNullOrWhiteSpace(op.name)
                                ? Path.GetFileNameWithoutExtension(assetPath)
                                : op.name.Trim();
                        }
                        prefabRoot = new GameObject(rootName);
                        handles["root"] = prefabRoot;
                        if (!TryRegisterHandle(op.result, prefabRoot, handles, request.target, i, diagnostics))
                        {
                            return BuildError(
                                "UNITY_BRIDGE_SCHEMA",
                                "Invalid prefab create plan.",
                                request.target,
                                request.ops.Length,
                                executed: false,
                                applied: applied,
                                diagnostics: diagnostics.ToArray()
                            );
                        }
                        applied += 1;
                        continue;
                    }

                    if (string.Equals(opName, "create_game_object", StringComparison.Ordinal))
                    {
                        if (prefabRoot == null)
                        {
                            diagnostics.Add(
                                new BridgeDiagnostic
                                {
                                    path = request.target,
                                    location = $"ops[{i}].op",
                                    detail = "schema_error",
                                    evidence = "create_game_object requires a prefab root first"
                                }
                            );
                            return BuildError(
                                "UNITY_BRIDGE_SCHEMA",
                                "Invalid prefab create plan.",
                                request.target,
                                request.ops.Length,
                                executed: false,
                                applied: applied,
                                diagnostics: diagnostics.ToArray()
                            );
                        }
                        if (string.IsNullOrWhiteSpace(op.name))
                        {
                            diagnostics.Add(
                                new BridgeDiagnostic
                                {
                                    path = request.target,
                                    location = $"ops[{i}].name",
                                    detail = "schema_error",
                                    evidence = "create_game_object requires name"
                                }
                            );
                            return BuildError(
                                "UNITY_BRIDGE_SCHEMA",
                                "Invalid prefab create plan.",
                                request.target,
                                request.ops.Length,
                                executed: false,
                                applied: applied,
                                diagnostics: diagnostics.ToArray()
                            );
                        }
                        GameObject parentObject;
                        string handleError;
                        if (!TryResolveGameObjectHandle(op.parent, handles, out parentObject, out handleError))
                        {
                            diagnostics.Add(
                                new BridgeDiagnostic
                                {
                                    path = request.target,
                                    location = $"ops[{i}].parent",
                                    detail = "schema_error",
                                    evidence = handleError
                                }
                            );
                            return BuildError(
                                "UNITY_BRIDGE_SCHEMA",
                                "Invalid prefab create plan.",
                                request.target,
                                request.ops.Length,
                                executed: false,
                                applied: applied,
                                diagnostics: diagnostics.ToArray()
                            );
                        }
                        GameObject child = new GameObject(op.name.Trim());
                        child.transform.SetParent(parentObject.transform, false);
                        if (!TryRegisterHandle(op.result, child, handles, request.target, i, diagnostics))
                        {
                            UnityEngine.Object.DestroyImmediate(child);
                            return BuildError(
                                "UNITY_BRIDGE_SCHEMA",
                                "Invalid prefab create plan.",
                                request.target,
                                request.ops.Length,
                                executed: false,
                                applied: applied,
                                diagnostics: diagnostics.ToArray()
                            );
                        }
                        applied += 1;
                        continue;
                    }

                    if (string.Equals(opName, "add_component", StringComparison.Ordinal))
                    {
                        GameObject targetObject;
                        string targetHandleError;
                        if (!TryResolveGameObjectHandle(op.target, handles, out targetObject, out targetHandleError))
                        {
                            diagnostics.Add(
                                new BridgeDiagnostic
                                {
                                    path = request.target,
                                    location = $"ops[{i}].target",
                                    detail = "schema_error",
                                    evidence = targetHandleError
                                }
                            );
                            return BuildError(
                                "UNITY_BRIDGE_SCHEMA",
                                "Invalid prefab create plan.",
                                request.target,
                                request.ops.Length,
                                executed: false,
                                applied: applied,
                                diagnostics: diagnostics.ToArray()
                            );
                        }
                        Type componentType;
                        string typeError;
                        if (!TryResolveComponentType(op.type, out componentType, out typeError))
                        {
                            diagnostics.Add(
                                new BridgeDiagnostic
                                {
                                    path = request.target,
                                    location = $"ops[{i}].type",
                                    detail = "apply_error",
                                    evidence = typeError
                                }
                            );
                            return BuildError(
                                "UNITY_BRIDGE_APPLY",
                                "Failed to add component.",
                                request.target,
                                request.ops.Length,
                                executed: true,
                                applied: applied,
                                diagnostics: diagnostics.ToArray()
                            );
                        }
                        if (componentType.IsAbstract || componentType.ContainsGenericParameters)
                        {
                            diagnostics.Add(
                                new BridgeDiagnostic
                                {
                                    path = request.target,
                                    location = $"ops[{i}].type",
                                    detail = "apply_error",
                                    evidence = $"component type '{componentType.FullName ?? componentType.Name}' cannot be instantiated"
                                }
                            );
                            return BuildError(
                                "UNITY_BRIDGE_APPLY",
                                "Failed to add component.",
                                request.target,
                                request.ops.Length,
                                executed: true,
                                applied: applied,
                                diagnostics: diagnostics.ToArray()
                            );
                        }
                        if (componentType == typeof(Transform))
                        {
                            diagnostics.Add(
                                new BridgeDiagnostic
                                {
                                    path = request.target,
                                    location = $"ops[{i}].type",
                                    detail = "apply_error",
                                    evidence = "Transform is implicit and cannot be added"
                                }
                            );
                            return BuildError(
                                "UNITY_BRIDGE_APPLY",
                                "Failed to add component.",
                                request.target,
                                request.ops.Length,
                                executed: true,
                                applied: applied,
                                diagnostics: diagnostics.ToArray()
                            );
                        }

                        Component addedComponent = targetObject.AddComponent(componentType);
                        if (addedComponent == null)
                        {
                            diagnostics.Add(
                                new BridgeDiagnostic
                                {
                                    path = request.target,
                                    location = $"ops[{i}]",
                                    detail = "apply_error",
                                    evidence = $"AddComponent returned null for '{componentType.FullName ?? componentType.Name}'"
                                }
                            );
                            return BuildError(
                                "UNITY_BRIDGE_APPLY",
                                "Failed to add component.",
                                request.target,
                                request.ops.Length,
                                executed: true,
                                applied: applied,
                                diagnostics: diagnostics.ToArray()
                            );
                        }
                        string resultHandle = NormalizeHandle(op.result);
                        if (!TrySetupUdonSharpBacking(
                            targetObject, addedComponent, componentType, handles,
                            resultHandle, request.target, i, diagnostics))
                        {
                            UnityEngine.Object.DestroyImmediate(addedComponent);
                            return BuildError(
                                "UNITY_BRIDGE_APPLY",
                                "Failed to setup UdonSharp backing.",
                                request.target,
                                request.ops.Length,
                                executed: true,
                                applied: applied,
                                diagnostics: diagnostics.ToArray()
                            );
                        }
                        if (!TryRegisterHandle(op.result, addedComponent, handles, request.target, i, diagnostics))
                        {
                            UnityEngine.Object.DestroyImmediate(addedComponent);
                            return BuildError(
                                "UNITY_BRIDGE_SCHEMA",
                                "Invalid prefab create plan.",
                                request.target,
                                request.ops.Length,
                                executed: false,
                                applied: applied,
                                diagnostics: diagnostics.ToArray()
                            );
                        }
                        applied += 1;
                        continue;
                    }

                    if (string.Equals(opName, "find_component", StringComparison.Ordinal))
                    {
                        GameObject targetObject;
                        string targetHandleError;
                        if (!TryResolveGameObjectHandle(op.target, handles, out targetObject, out targetHandleError))
                        {
                            diagnostics.Add(
                                new BridgeDiagnostic
                                {
                                    path = request.target,
                                    location = $"ops[{i}].target",
                                    detail = "schema_error",
                                    evidence = targetHandleError
                                }
                            );
                            return BuildError(
                                "UNITY_BRIDGE_SCHEMA",
                                "Invalid prefab create plan.",
                                request.target,
                                request.ops.Length,
                                executed: false,
                                applied: applied,
                                diagnostics: diagnostics.ToArray()
                            );
                        }
                        Component foundComponent;
                        string componentError;
                        if (!TryFindUniqueComponentOnObject(targetObject, op.type, out foundComponent, out componentError))
                        {
                            diagnostics.Add(
                                new BridgeDiagnostic
                                {
                                    path = request.target,
                                    location = $"ops[{i}].type",
                                    detail = "apply_error",
                                    evidence = componentError
                                }
                            );
                            return BuildError(
                                "UNITY_BRIDGE_APPLY",
                                "Failed to resolve component.",
                                request.target,
                                request.ops.Length,
                                executed: true,
                                applied: applied,
                                diagnostics: diagnostics.ToArray()
                            );
                        }
                        if (!TryRegisterHandle(op.result, foundComponent, handles, request.target, i, diagnostics))
                        {
                            return BuildError(
                                "UNITY_BRIDGE_SCHEMA",
                                "Invalid prefab create plan.",
                                request.target,
                                request.ops.Length,
                                executed: false,
                                applied: applied,
                                diagnostics: diagnostics.ToArray()
                            );
                        }
                        applied += 1;
                        continue;
                    }

                    if (string.Equals(opName, "remove_component", StringComparison.Ordinal))
                    {
                        Component targetComponent;
                        string componentHandleError;
                        if (!TryResolveComponentHandle(op.target, handles, out targetComponent, out componentHandleError))
                        {
                            diagnostics.Add(
                                new BridgeDiagnostic
                                {
                                    path = request.target,
                                    location = $"ops[{i}].target",
                                    detail = "schema_error",
                                    evidence = componentHandleError
                                }
                            );
                            return BuildError(
                                "UNITY_BRIDGE_SCHEMA",
                                "Invalid prefab create plan.",
                                request.target,
                                request.ops.Length,
                                executed: false,
                                applied: applied,
                                diagnostics: diagnostics.ToArray()
                            );
                        }
                        if (targetComponent is Transform)
                        {
                            diagnostics.Add(
                                new BridgeDiagnostic
                                {
                                    path = request.target,
                                    location = $"ops[{i}].target",
                                    detail = "apply_error",
                                    evidence = "Transform cannot be removed"
                                }
                            );
                            return BuildError(
                                "UNITY_BRIDGE_APPLY",
                                "Failed to remove component.",
                                request.target,
                                request.ops.Length,
                                executed: true,
                                applied: applied,
                                diagnostics: diagnostics.ToArray()
                            );
                        }
                        UnityEngine.Object.DestroyImmediate(targetComponent);
                        applied += 1;
                        continue;
                    }

                    if (
                        string.Equals(opName, "set", StringComparison.Ordinal)
                        || string.Equals(opName, "insert_array_element", StringComparison.Ordinal)
                        || string.Equals(opName, "remove_array_element", StringComparison.Ordinal)
                    )
                    {
                        Component targetComponent;
                        string componentHandleError;
                        if (!TryResolveComponentHandle(op.target, handles, out targetComponent, out componentHandleError))
                        {
                            diagnostics.Add(
                                new BridgeDiagnostic
                                {
                                    path = request.target,
                                    location = $"ops[{i}].target",
                                    detail = "schema_error",
                                    evidence = componentHandleError
                                }
                            );
                            return BuildError(
                                "UNITY_BRIDGE_SCHEMA",
                                "Invalid prefab create plan.",
                                request.target,
                                request.ops.Length,
                                executed: false,
                                applied: applied,
                                diagnostics: diagnostics.ToArray()
                            );
                        }
                        s_currentHandles = handles;
                        try
                        {
                            if (!TryApplyMutationOpToComponent(targetComponent, request.target, op, i, diagnostics))
                            {
                                return BuildError(
                                    "UNITY_BRIDGE_APPLY",
                                    "Failed to apply component mutation.",
                                    request.target,
                                    request.ops.Length,
                                    executed: true,
                                    applied: applied,
                                    diagnostics: diagnostics.ToArray()
                                );
                            }
                        }
                        finally
                        {
                            s_currentHandles = null;
                        }
                        applied += 1;
                        continue;
                    }

                    if (string.Equals(opName, "rename_object", StringComparison.Ordinal))
                    {
                        GameObject targetObject;
                        string handleError;
                        if (!TryResolveGameObjectHandle(op.target, handles, out targetObject, out handleError))
                        {
                            diagnostics.Add(
                                new BridgeDiagnostic
                                {
                                    path = request.target,
                                    location = $"ops[{i}].target",
                                    detail = "schema_error",
                                    evidence = handleError
                                }
                            );
                            return BuildError(
                                "UNITY_BRIDGE_SCHEMA",
                                "Invalid prefab create plan.",
                                request.target,
                                request.ops.Length,
                                executed: false,
                                applied: applied,
                                diagnostics: diagnostics.ToArray()
                            );
                        }
                        if (string.IsNullOrWhiteSpace(op.name))
                        {
                            diagnostics.Add(
                                new BridgeDiagnostic
                                {
                                    path = request.target,
                                    location = $"ops[{i}].name",
                                    detail = "schema_error",
                                    evidence = "rename_object requires name"
                                }
                            );
                            return BuildError(
                                "UNITY_BRIDGE_SCHEMA",
                                "Invalid prefab create plan.",
                                request.target,
                                request.ops.Length,
                                executed: false,
                                applied: applied,
                                diagnostics: diagnostics.ToArray()
                            );
                        }
                        targetObject.name = op.name.Trim();
                        applied += 1;
                        continue;
                    }

                    if (string.Equals(opName, "reparent", StringComparison.Ordinal))
                    {
                        GameObject targetObject;
                        string targetHandleError;
                        if (!TryResolveGameObjectHandle(op.target, handles, out targetObject, out targetHandleError))
                        {
                            diagnostics.Add(
                                new BridgeDiagnostic
                                {
                                    path = request.target,
                                    location = $"ops[{i}].target",
                                    detail = "schema_error",
                                    evidence = targetHandleError
                                }
                            );
                            return BuildError(
                                "UNITY_BRIDGE_SCHEMA",
                                "Invalid prefab create plan.",
                                request.target,
                                request.ops.Length,
                                executed: false,
                                applied: applied,
                                diagnostics: diagnostics.ToArray()
                            );
                        }
                        GameObject parentObject;
                        string parentHandleError;
                        if (!TryResolveGameObjectHandle(op.parent, handles, out parentObject, out parentHandleError))
                        {
                            diagnostics.Add(
                                new BridgeDiagnostic
                                {
                                    path = request.target,
                                    location = $"ops[{i}].parent",
                                    detail = "schema_error",
                                    evidence = parentHandleError
                                }
                            );
                            return BuildError(
                                "UNITY_BRIDGE_SCHEMA",
                                "Invalid prefab create plan.",
                                request.target,
                                request.ops.Length,
                                executed: false,
                                applied: applied,
                                diagnostics: diagnostics.ToArray()
                            );
                        }
                        if (ReferenceEquals(targetObject, prefabRoot))
                        {
                            diagnostics.Add(
                                new BridgeDiagnostic
                                {
                                    path = request.target,
                                    location = $"ops[{i}].target",
                                    detail = "schema_error",
                                    evidence = "root handle cannot be reparented"
                                }
                            );
                            return BuildError(
                                "UNITY_BRIDGE_SCHEMA",
                                "Invalid prefab create plan.",
                                request.target,
                                request.ops.Length,
                                executed: false,
                                applied: applied,
                                diagnostics: diagnostics.ToArray()
                            );
                        }
                        if (ReferenceEquals(targetObject, parentObject))
                        {
                            diagnostics.Add(
                                new BridgeDiagnostic
                                {
                                    path = request.target,
                                    location = $"ops[{i}]",
                                    detail = "schema_error",
                                    evidence = "target and parent handles must differ"
                                }
                            );
                            return BuildError(
                                "UNITY_BRIDGE_SCHEMA",
                                "Invalid prefab create plan.",
                                request.target,
                                request.ops.Length,
                                executed: false,
                                applied: applied,
                                diagnostics: diagnostics.ToArray()
                            );
                        }
                        targetObject.transform.SetParent(parentObject.transform, false);
                        applied += 1;
                        continue;
                    }

                    if (string.Equals(opName, "save", StringComparison.Ordinal))
                    {
                        if (prefabRoot == null)
                        {
                            diagnostics.Add(
                                new BridgeDiagnostic
                                {
                                    path = request.target,
                                    location = $"ops[{i}].op",
                                    detail = "schema_error",
                                    evidence = "save requires a prefab root first"
                                }
                            );
                            return BuildError(
                                "UNITY_BRIDGE_SCHEMA",
                                "Invalid prefab create plan.",
                                request.target,
                                request.ops.Length,
                                executed: false,
                                applied: applied,
                                diagnostics: diagnostics.ToArray()
                            );
                        }
                        if (i != request.ops.Length - 1)
                        {
                            diagnostics.Add(
                                new BridgeDiagnostic
                                {
                                    path = request.target,
                                    location = $"ops[{i}].op",
                                    detail = "schema_error",
                                    evidence = "save must be the final operation in create mode"
                                }
                            );
                            return BuildError(
                                "UNITY_BRIDGE_SCHEMA",
                                "Invalid prefab create plan.",
                                request.target,
                                request.ops.Length,
                                executed: false,
                                applied: applied,
                                diagnostics: diagnostics.ToArray()
                            );
                        }

                        GameObject savedPrefab = PrefabUtility.SaveAsPrefabAsset(prefabRoot, assetPath);
                        if (savedPrefab == null)
                        {
                            diagnostics.Add(
                                new BridgeDiagnostic
                                {
                                    path = request.target,
                                    location = $"ops[{i}].op",
                                    detail = "apply_error",
                                    evidence = "PrefabUtility.SaveAsPrefabAsset returned null"
                                }
                            );
                            return BuildError(
                                "UNITY_BRIDGE_APPLY",
                                "Failed to save prefab asset.",
                                request.target,
                                request.ops.Length,
                                executed: true,
                                applied: applied,
                                diagnostics: diagnostics.ToArray()
                            );
                        }
                        AssetDatabase.SaveAssets();
                        AssetDatabase.Refresh(ImportAssetOptions.ForceSynchronousImport);
                        saved = true;
                        applied += 1;
                        continue;
                    }

                    diagnostics.Add(
                        new BridgeDiagnostic
                        {
                            path = request.target,
                            location = $"ops[{i}].op",
                            detail = "schema_error",
                            evidence = $"unsupported prefab create op '{opName}'"
                        }
                    );
                    return BuildError(
                        "UNITY_BRIDGE_SCHEMA",
                        "Invalid prefab create plan.",
                        request.target,
                        request.ops.Length,
                        executed: false,
                        applied: applied,
                        diagnostics: diagnostics.ToArray()
                    );
                }

                if (prefabRoot == null || !saved)
                {
                    return BuildError(
                        "UNITY_BRIDGE_SCHEMA",
                        "Prefab create mode requires a root creation operation and save.",
                        request.target,
                        request.ops.Length,
                        executed: false,
                        applied: applied
                    );
                }

                return new BridgeResponse
                {
                    protocol_version = ProtocolVersion,
                    success = true,
                    severity = "info",
                    code = "SER_APPLY_OK",
                    message = "Prefab create plan applied via Unity executeMethod.",
                    data = new BridgeData
                    {
                        target = request.target,
                        op_count = request.ops.Length,
                        applied = applied,
                        read_only = false,
                        executed = true,
                        protocol_version = ProtocolVersion
                    },
                    diagnostics = Array.Empty<BridgeDiagnostic>()
                };
            }
            catch (Exception ex)
            {
                diagnostics.Add(
                    new BridgeDiagnostic
                    {
                        path = request.target,
                        location = "apply",
                        detail = "exception",
                        evidence = ex.ToString()
                    }
                );
                return BuildError(
                    "UNITY_BRIDGE_APPLY_EXCEPTION",
                    $"Unexpected apply exception: {ex.Message}",
                    request.target,
                    request.ops.Length,
                    executed: true,
                    applied: applied,
                    diagnostics: diagnostics.ToArray()
                );
            }
            finally
            {
                if (prefabRoot != null)
                {
                    UnityEngine.Object.DestroyImmediate(prefabRoot);
                }
            }
        }

        private static BridgeResponse ApplyAssetOperations(BridgeRequest request, string assetPath)
        {
            int applied = 0;
            List<BridgeDiagnostic> diagnostics = new List<BridgeDiagnostic>();
            UnityEngine.Object assetObject = null;
            try
            {
                assetObject = AssetDatabase.LoadMainAssetAtPath(assetPath);
                if (assetObject == null)
                {
                    return BuildError(
                        "UNITY_BRIDGE_ASSET_LOAD",
                        "Failed to load asset contents.",
                        request.target,
                        request.ops.Length,
                        executed: true
                    );
                }

                string assetExtension = Path.GetExtension(assetPath);
                if (
                    string.Equals(assetExtension, ".mat", StringComparison.OrdinalIgnoreCase)
                    && !(assetObject is Material)
                )
                {
                    return BuildError(
                        "UNITY_BRIDGE_TARGET_UNSUPPORTED",
                        "Material operations require a .mat asset whose main object is UnityEngine.Material.",
                        request.target,
                        request.ops.Length,
                        executed: false
                    );
                }
                if (
                    string.Equals(assetExtension, ".asset", StringComparison.OrdinalIgnoreCase)
                    && !(assetObject is ScriptableObject)
                )
                {
                    return BuildError(
                        "UNITY_BRIDGE_TARGET_UNSUPPORTED",
                        "Asset operations currently support ScriptableObject main assets only.",
                        request.target,
                        request.ops.Length,
                        executed: false
                    );
                }

                Dictionary<string, UnityEngine.Object> handles = new Dictionary<string, UnityEngine.Object>(StringComparer.Ordinal)
                {
                    [AssetHandleName] = assetObject
                };

                for (int i = 0; i < request.ops.Length; i++)
                {
                    PatchOp op = request.ops[i];
                    string opName = (op?.op ?? string.Empty).Trim();
                    if (
                        !string.Equals(opName, "set", StringComparison.Ordinal)
                        && !string.Equals(opName, "insert_array_element", StringComparison.Ordinal)
                        && !string.Equals(opName, "remove_array_element", StringComparison.Ordinal)
                    )
                    {
                        diagnostics.Add(
                            new BridgeDiagnostic
                            {
                                path = request.target,
                                location = $"ops[{i}].op",
                                detail = "schema_error",
                                evidence = $"unsupported asset open op '{opName}'"
                            }
                        );
                        return BuildError(
                            "UNITY_BRIDGE_SCHEMA",
                            "Invalid asset patch plan.",
                            request.target,
                            request.ops.Length,
                            executed: false,
                            applied: applied,
                            diagnostics: diagnostics.ToArray()
                        );
                    }

                    UnityEngine.Object targetAsset;
                    string handleError;
                    if (!TryResolveAssetHandle(op.target, handles, out targetAsset, out handleError))
                    {
                        diagnostics.Add(
                            new BridgeDiagnostic
                            {
                                path = request.target,
                                location = $"ops[{i}].target",
                                detail = "schema_error",
                                evidence = handleError
                            }
                        );
                        return BuildError(
                            "UNITY_BRIDGE_SCHEMA",
                            "Invalid asset patch plan.",
                            request.target,
                            request.ops.Length,
                            executed: false,
                            applied: applied,
                            diagnostics: diagnostics.ToArray()
                        );
                    }

                    if (!TryApplyMutationOpToObject(targetAsset, request.target, op, i, diagnostics))
                    {
                        return BuildError(
                            "UNITY_BRIDGE_APPLY",
                            "Failed to apply asset mutation.",
                            request.target,
                            request.ops.Length,
                            executed: true,
                            applied: applied,
                            diagnostics: diagnostics.ToArray()
                        );
                    }
                    applied += 1;
                }

                EditorUtility.SetDirty(assetObject);
                AssetDatabase.SaveAssets();
                AssetDatabase.Refresh(ImportAssetOptions.ForceSynchronousImport);
                UnityEngine.Object reopened = AssetDatabase.LoadMainAssetAtPath(assetPath);
                if (reopened == null)
                {
                    diagnostics.Add(
                        new BridgeDiagnostic
                        {
                            path = request.target,
                            location = "save",
                            detail = "apply_error",
                            evidence = "AssetDatabase.LoadMainAssetAtPath returned null after save"
                        }
                    );
                    return BuildError(
                        "UNITY_BRIDGE_APPLY",
                        "Failed to reopen asset after save.",
                        request.target,
                        request.ops.Length,
                        executed: true,
                        applied: applied,
                        diagnostics: diagnostics.ToArray()
                    );
                }

                return new BridgeResponse
                {
                    protocol_version = ProtocolVersion,
                    success = true,
                    severity = "info",
                    code = "SER_APPLY_OK",
                    message = "SerializedObject patch applied to asset via Unity executeMethod.",
                    data = new BridgeData
                    {
                        target = request.target,
                        op_count = request.ops.Length,
                        applied = applied,
                        read_only = false,
                        executed = true,
                        protocol_version = ProtocolVersion
                    },
                    diagnostics = Array.Empty<BridgeDiagnostic>()
                };
            }
            catch (Exception ex)
            {
                diagnostics.Add(
                    new BridgeDiagnostic
                    {
                        path = request.target,
                        location = "apply",
                        detail = "exception",
                        evidence = ex.ToString()
                    }
                );
                return BuildError(
                    "UNITY_BRIDGE_APPLY_EXCEPTION",
                    $"Unexpected apply exception: {ex.Message}",
                    request.target,
                    request.ops.Length,
                    executed: true,
                    applied: applied,
                    diagnostics: diagnostics.ToArray()
                );
            }
        }

        private static BridgeResponse ApplyAssetCreateOperations(BridgeRequest request, string assetPath)
        {
            int applied = 0;
            List<BridgeDiagnostic> diagnostics = new List<BridgeDiagnostic>();
            UnityEngine.Object assetObject = null;
            bool saved = false;
            Dictionary<string, UnityEngine.Object> handles = new Dictionary<string, UnityEngine.Object>(StringComparer.Ordinal);
            try
            {
                string fullAssetPath = Path.Combine(
                    Path.GetFullPath(Path.Combine(Application.dataPath, "..")),
                    assetPath
                );
                if (File.Exists(fullAssetPath))
                {
                    return BuildError(
                        "UNITY_BRIDGE_TARGET_EXISTS",
                        "target file already exists.",
                        request.target,
                        request.ops.Length,
                        executed: false
                    );
                }

                string parentDir = Path.GetDirectoryName(fullAssetPath);
                if (string.IsNullOrWhiteSpace(parentDir) || !Directory.Exists(parentDir))
                {
                    return BuildError(
                        "UNITY_BRIDGE_TARGET_PATH",
                        "target directory was not found.",
                        request.target,
                        request.ops.Length,
                        executed: false
                    );
                }

                string assetExtension = Path.GetExtension(assetPath);
                for (int i = 0; i < request.ops.Length; i++)
                {
                    PatchOp op = request.ops[i];
                    string opName = (op?.op ?? string.Empty).Trim();
                    if (string.Equals(opName, "create_asset", StringComparison.Ordinal))
                    {
                        if (assetObject != null)
                        {
                            diagnostics.Add(
                                new BridgeDiagnostic
                                {
                                    path = request.target,
                                    location = $"ops[{i}].op",
                                    detail = "schema_error",
                                    evidence = "asset root may be created only once"
                                }
                            );
                            return BuildError(
                                "UNITY_BRIDGE_SCHEMA",
                                "Invalid asset create plan.",
                                request.target,
                                request.ops.Length,
                                executed: false,
                                applied: applied,
                                diagnostics: diagnostics.ToArray()
                            );
                        }

                        string assetName = string.IsNullOrWhiteSpace(op.name)
                            ? Path.GetFileNameWithoutExtension(assetPath)
                            : op.name.Trim();
                        if (
                            string.Equals(assetExtension, ".mat", StringComparison.OrdinalIgnoreCase)
                        )
                        {
                            if (!string.IsNullOrWhiteSpace(op.type))
                            {
                                Type materialType;
                                string typeError;
                                if (!TryResolveType(op.type, out materialType, out typeError))
                                {
                                    diagnostics.Add(
                                        new BridgeDiagnostic
                                        {
                                            path = request.target,
                                            location = $"ops[{i}].type",
                                            detail = "apply_error",
                                            evidence = typeError
                                        }
                                    );
                                    return BuildError(
                                        "UNITY_BRIDGE_APPLY",
                                        "Failed to create material asset.",
                                        request.target,
                                        request.ops.Length,
                                        executed: true,
                                        applied: applied,
                                        diagnostics: diagnostics.ToArray()
                                    );
                                }
                                if (!typeof(Material).IsAssignableFrom(materialType))
                                {
                                    diagnostics.Add(
                                        new BridgeDiagnostic
                                        {
                                            path = request.target,
                                            location = $"ops[{i}].type",
                                            detail = "apply_error",
                                            evidence = $"type '{materialType.FullName ?? materialType.Name}' is not assignable to UnityEngine.Material"
                                        }
                                    );
                                    return BuildError(
                                        "UNITY_BRIDGE_APPLY",
                                        "Failed to create material asset.",
                                        request.target,
                                        request.ops.Length,
                                        executed: true,
                                        applied: applied,
                                        diagnostics: diagnostics.ToArray()
                                    );
                                }
                            }

                            string shaderName = (op.shader ?? string.Empty).Trim();
                            if (string.IsNullOrWhiteSpace(shaderName))
                            {
                                diagnostics.Add(
                                    new BridgeDiagnostic
                                    {
                                        path = request.target,
                                        location = $"ops[{i}].shader",
                                        detail = "schema_error",
                                        evidence = "create_asset requires shader for material resources"
                                    }
                                );
                                return BuildError(
                                    "UNITY_BRIDGE_SCHEMA",
                                    "Invalid asset create plan.",
                                    request.target,
                                    request.ops.Length,
                                    executed: false,
                                    applied: applied,
                                    diagnostics: diagnostics.ToArray()
                                );
                            }

                            Shader shader = Shader.Find(shaderName);
                            if (shader == null)
                            {
                                diagnostics.Add(
                                    new BridgeDiagnostic
                                    {
                                        path = request.target,
                                        location = $"ops[{i}].shader",
                                        detail = "apply_error",
                                        evidence = $"shader '{shaderName}' was not found"
                                    }
                                );
                                return BuildError(
                                    "UNITY_BRIDGE_APPLY",
                                    "Failed to create material asset.",
                                    request.target,
                                    request.ops.Length,
                                    executed: true,
                                    applied: applied,
                                    diagnostics: diagnostics.ToArray()
                                );
                            }

                            Material material = new Material(shader) { name = assetName };
                            AssetDatabase.CreateAsset(material, assetPath);
                            assetObject = material;
                        }
                        else if (
                            string.Equals(assetExtension, ".asset", StringComparison.OrdinalIgnoreCase)
                        )
                        {
                            Type assetType;
                            string typeError;
                            if (!TryResolveType(op.type, out assetType, out typeError))
                            {
                                diagnostics.Add(
                                    new BridgeDiagnostic
                                    {
                                        path = request.target,
                                        location = $"ops[{i}].type",
                                        detail = "apply_error",
                                        evidence = typeError
                                    }
                                );
                                return BuildError(
                                    "UNITY_BRIDGE_APPLY",
                                    "Failed to create ScriptableObject asset.",
                                    request.target,
                                    request.ops.Length,
                                    executed: true,
                                    applied: applied,
                                    diagnostics: diagnostics.ToArray()
                                );
                            }
                            if (
                                !typeof(ScriptableObject).IsAssignableFrom(assetType)
                                || assetType.IsAbstract
                                || assetType.ContainsGenericParameters
                            )
                            {
                                diagnostics.Add(
                                    new BridgeDiagnostic
                                    {
                                        path = request.target,
                                        location = $"ops[{i}].type",
                                        detail = "apply_error",
                                        evidence = $"type '{assetType.FullName ?? assetType.Name}' is not a concrete ScriptableObject"
                                    }
                                );
                                return BuildError(
                                    "UNITY_BRIDGE_APPLY",
                                    "Failed to create ScriptableObject asset.",
                                    request.target,
                                    request.ops.Length,
                                    executed: true,
                                    applied: applied,
                                    diagnostics: diagnostics.ToArray()
                                );
                            }

                            ScriptableObject scriptableObject = ScriptableObject.CreateInstance(assetType);
                            if (scriptableObject == null)
                            {
                                diagnostics.Add(
                                    new BridgeDiagnostic
                                    {
                                        path = request.target,
                                        location = $"ops[{i}].type",
                                        detail = "apply_error",
                                        evidence = $"ScriptableObject.CreateInstance returned null for '{assetType.FullName ?? assetType.Name}'"
                                    }
                                );
                                return BuildError(
                                    "UNITY_BRIDGE_APPLY",
                                    "Failed to create ScriptableObject asset.",
                                    request.target,
                                    request.ops.Length,
                                    executed: true,
                                    applied: applied,
                                    diagnostics: diagnostics.ToArray()
                                );
                            }
                            scriptableObject.name = assetName;
                            AssetDatabase.CreateAsset(scriptableObject, assetPath);
                            assetObject = scriptableObject;
                        }
                        else
                        {
                            return BuildError(
                                "UNITY_BRIDGE_TARGET_UNSUPPORTED",
                                "Asset create mode currently supports .mat and .asset targets only.",
                                request.target,
                                request.ops.Length,
                                executed: false
                            );
                        }

                        handles[AssetHandleName] = assetObject;
                        if (!TryRegisterHandle(op.result, assetObject, handles, request.target, i, diagnostics))
                        {
                            return BuildError(
                                "UNITY_BRIDGE_SCHEMA",
                                "Invalid asset create plan.",
                                request.target,
                                request.ops.Length,
                                executed: false,
                                applied: applied,
                                diagnostics: diagnostics.ToArray()
                            );
                        }
                        applied += 1;
                        continue;
                    }

                    if (
                        string.Equals(opName, "set", StringComparison.Ordinal)
                        || string.Equals(opName, "insert_array_element", StringComparison.Ordinal)
                        || string.Equals(opName, "remove_array_element", StringComparison.Ordinal)
                    )
                    {
                        if (assetObject == null)
                        {
                            diagnostics.Add(
                                new BridgeDiagnostic
                                {
                                    path = request.target,
                                    location = $"ops[{i}].op",
                                    detail = "schema_error",
                                    evidence = $"{opName} requires a create_asset operation first"
                                }
                            );
                            return BuildError(
                                "UNITY_BRIDGE_SCHEMA",
                                "Invalid asset create plan.",
                                request.target,
                                request.ops.Length,
                                executed: false,
                                applied: applied,
                                diagnostics: diagnostics.ToArray()
                            );
                        }

                        UnityEngine.Object targetAsset;
                        string handleError;
                        if (!TryResolveAssetHandle(op.target, handles, out targetAsset, out handleError))
                        {
                            diagnostics.Add(
                                new BridgeDiagnostic
                                {
                                    path = request.target,
                                    location = $"ops[{i}].target",
                                    detail = "schema_error",
                                    evidence = handleError
                                }
                            );
                            return BuildError(
                                "UNITY_BRIDGE_SCHEMA",
                                "Invalid asset create plan.",
                                request.target,
                                request.ops.Length,
                                executed: false,
                                applied: applied,
                                diagnostics: diagnostics.ToArray()
                            );
                        }

                        if (!TryApplyMutationOpToObject(targetAsset, request.target, op, i, diagnostics))
                        {
                            return BuildError(
                                "UNITY_BRIDGE_APPLY",
                                "Failed to apply asset mutation.",
                                request.target,
                                request.ops.Length,
                                executed: true,
                                applied: applied,
                                diagnostics: diagnostics.ToArray()
                            );
                        }
                        applied += 1;
                        continue;
                    }

                    if (string.Equals(opName, "save", StringComparison.Ordinal))
                    {
                        if (assetObject == null)
                        {
                            diagnostics.Add(
                                new BridgeDiagnostic
                                {
                                    path = request.target,
                                    location = $"ops[{i}].op",
                                    detail = "schema_error",
                                    evidence = "save requires a create_asset operation first"
                                }
                            );
                            return BuildError(
                                "UNITY_BRIDGE_SCHEMA",
                                "Invalid asset create plan.",
                                request.target,
                                request.ops.Length,
                                executed: false,
                                applied: applied,
                                diagnostics: diagnostics.ToArray()
                            );
                        }
                        if (i != request.ops.Length - 1)
                        {
                            diagnostics.Add(
                                new BridgeDiagnostic
                                {
                                    path = request.target,
                                    location = $"ops[{i}].op",
                                    detail = "schema_error",
                                    evidence = "save must be the final operation in create mode"
                                }
                            );
                            return BuildError(
                                "UNITY_BRIDGE_SCHEMA",
                                "Invalid asset create plan.",
                                request.target,
                                request.ops.Length,
                                executed: false,
                                applied: applied,
                                diagnostics: diagnostics.ToArray()
                            );
                        }

                        EditorUtility.SetDirty(assetObject);
                        AssetDatabase.SaveAssets();
                        AssetDatabase.Refresh(ImportAssetOptions.ForceSynchronousImport);
                        UnityEngine.Object reopened = AssetDatabase.LoadMainAssetAtPath(assetPath);
                        if (reopened == null)
                        {
                            diagnostics.Add(
                                new BridgeDiagnostic
                                {
                                    path = request.target,
                                    location = $"ops[{i}].op",
                                    detail = "apply_error",
                                    evidence = "AssetDatabase.LoadMainAssetAtPath returned null after save"
                                }
                            );
                            return BuildError(
                                "UNITY_BRIDGE_APPLY",
                                "Failed to reopen asset after save.",
                                request.target,
                                request.ops.Length,
                                executed: true,
                                applied: applied,
                                diagnostics: diagnostics.ToArray()
                            );
                        }
                        handles[AssetHandleName] = reopened;
                        saved = true;
                        applied += 1;
                        continue;
                    }

                    diagnostics.Add(
                        new BridgeDiagnostic
                        {
                            path = request.target,
                            location = $"ops[{i}].op",
                            detail = "schema_error",
                            evidence = $"unsupported asset create op '{opName}'"
                        }
                    );
                    return BuildError(
                        "UNITY_BRIDGE_SCHEMA",
                        "Invalid asset create plan.",
                        request.target,
                        request.ops.Length,
                        executed: false,
                        applied: applied,
                        diagnostics: diagnostics.ToArray()
                    );
                }

                if (assetObject == null || !saved)
                {
                    return BuildError(
                        "UNITY_BRIDGE_SCHEMA",
                        "Asset create mode requires a create_asset operation and save.",
                        request.target,
                        request.ops.Length,
                        executed: false,
                        applied: applied
                    );
                }

                return new BridgeResponse
                {
                    protocol_version = ProtocolVersion,
                    success = true,
                    severity = "info",
                    code = "SER_APPLY_OK",
                    message = "Asset create plan applied via Unity executeMethod.",
                    data = new BridgeData
                    {
                        target = request.target,
                        op_count = request.ops.Length,
                        applied = applied,
                        read_only = false,
                        executed = true,
                        protocol_version = ProtocolVersion
                    },
                    diagnostics = Array.Empty<BridgeDiagnostic>()
                };
            }
            catch (Exception ex)
            {
                diagnostics.Add(
                    new BridgeDiagnostic
                    {
                        path = request.target,
                        location = "apply",
                        detail = "exception",
                        evidence = ex.ToString()
                    }
                );
                return BuildError(
                    "UNITY_BRIDGE_APPLY_EXCEPTION",
                    $"Unexpected apply exception: {ex.Message}",
                    request.target,
                    request.ops.Length,
                    executed: true,
                    applied: applied,
                    diagnostics: diagnostics.ToArray()
                );
            }
            finally
            {
                if (!saved && AssetDatabase.LoadMainAssetAtPath(assetPath) != null)
                {
                    AssetDatabase.DeleteAsset(assetPath);
                    AssetDatabase.SaveAssets();
                    AssetDatabase.Refresh(ImportAssetOptions.ForceSynchronousImport);
                }
            }
        }

        private static BridgeResponse ApplySceneOperations(BridgeRequest request, string assetPath, bool createMode)
        {
            int applied = 0;
            List<BridgeDiagnostic> diagnostics = new List<BridgeDiagnostic>();
            Scene scene = default(Scene);
            bool sceneOpened = false;
            bool saved = false;
            Dictionary<string, UnityEngine.Object> handles = new Dictionary<string, UnityEngine.Object>(StringComparer.Ordinal);
            try
            {
                string fullAssetPath = Path.Combine(
                    Path.GetFullPath(Path.Combine(Application.dataPath, "..")),
                    assetPath
                );
                if (createMode)
                {
                    if (File.Exists(fullAssetPath))
                    {
                        return BuildError(
                            "UNITY_BRIDGE_TARGET_EXISTS",
                            "target file already exists.",
                            request.target,
                            request.ops.Length,
                            executed: false
                        );
                    }
                    string parentDir = Path.GetDirectoryName(fullAssetPath);
                    if (string.IsNullOrWhiteSpace(parentDir) || !Directory.Exists(parentDir))
                    {
                        return BuildError(
                            "UNITY_BRIDGE_TARGET_PATH",
                            "target directory was not found.",
                            request.target,
                            request.ops.Length,
                            executed: false
                        );
                    }
                }

                string requiredInitialOp = createMode ? "create_scene" : "open_scene";
                for (int i = 0; i < request.ops.Length; i++)
                {
                    PatchOp op = request.ops[i];
                    string opName = (op?.op ?? string.Empty).Trim();
                    if (i == 0)
                    {
                        if (!string.Equals(opName, requiredInitialOp, StringComparison.Ordinal))
                        {
                            diagnostics.Add(
                                new BridgeDiagnostic
                                {
                                    path = request.target,
                                    location = "ops[0].op",
                                    detail = "schema_error",
                                    evidence = $"scene {(createMode ? "create" : "open")} mode must start with {requiredInitialOp}"
                                }
                            );
                            return BuildError(
                                "UNITY_BRIDGE_SCHEMA",
                                "Invalid scene plan.",
                                request.target,
                                request.ops.Length,
                                executed: false,
                                applied: applied,
                                diagnostics: diagnostics.ToArray()
                            );
                        }

                        scene = createMode
                            ? EditorSceneManager.NewScene(NewSceneSetup.EmptyScene, NewSceneMode.Single)
                            : EditorSceneManager.OpenScene(assetPath, OpenSceneMode.Single);
                        if (!scene.IsValid() || !scene.isLoaded)
                        {
                            return BuildError(
                                createMode ? "UNITY_BRIDGE_SCENE_CREATE" : "UNITY_BRIDGE_SCENE_OPEN",
                                createMode ? "Failed to create empty scene." : "Failed to open scene.",
                                request.target,
                                request.ops.Length,
                                executed: true
                            );
                        }
                        sceneOpened = true;
                        applied += 1;
                        continue;
                    }

                    if (
                        string.Equals(opName, "create_scene", StringComparison.Ordinal)
                        || string.Equals(opName, "open_scene", StringComparison.Ordinal)
                    )
                    {
                        diagnostics.Add(
                            new BridgeDiagnostic
                            {
                                path = request.target,
                                location = $"ops[{i}].op",
                                detail = "schema_error",
                                evidence = $"{opName} may appear only as the first operation"
                            }
                        );
                        return BuildError(
                            "UNITY_BRIDGE_SCHEMA",
                            "Invalid scene plan.",
                            request.target,
                            request.ops.Length,
                            executed: false,
                            applied: applied,
                            diagnostics: diagnostics.ToArray()
                        );
                    }

                    if (!sceneOpened)
                    {
                        return BuildError(
                            "UNITY_BRIDGE_SCHEMA",
                            "Invalid scene plan.",
                            request.target,
                            request.ops.Length,
                            executed: false,
                            applied: applied
                        );
                    }

                    if (string.Equals(opName, "create_game_object", StringComparison.Ordinal))
                    {
                        if (string.IsNullOrWhiteSpace(op.name))
                        {
                            diagnostics.Add(
                                new BridgeDiagnostic
                                {
                                    path = request.target,
                                    location = $"ops[{i}].name",
                                    detail = "schema_error",
                                    evidence = "create_game_object requires name"
                                }
                            );
                            return BuildError(
                                "UNITY_BRIDGE_SCHEMA",
                                "Invalid scene plan.",
                                request.target,
                                request.ops.Length,
                                executed: false,
                                applied: applied,
                                diagnostics: diagnostics.ToArray()
                            );
                        }
                        GameObject parentObject;
                        bool parentIsSceneRoot;
                        string parentHandleError;
                        if (!TryResolveSceneParentHandle(op.parent, handles, out parentObject, out parentIsSceneRoot, out parentHandleError))
                        {
                            diagnostics.Add(
                                new BridgeDiagnostic
                                {
                                    path = request.target,
                                    location = $"ops[{i}].parent",
                                    detail = "schema_error",
                                    evidence = parentHandleError
                                }
                            );
                            return BuildError(
                                "UNITY_BRIDGE_SCHEMA",
                                "Invalid scene plan.",
                                request.target,
                                request.ops.Length,
                                executed: false,
                                applied: applied,
                                diagnostics: diagnostics.ToArray()
                            );
                        }

                        GameObject child = new GameObject(op.name.Trim());
                        SceneManager.MoveGameObjectToScene(child, scene);
                        if (!parentIsSceneRoot)
                        {
                            child.transform.SetParent(parentObject.transform, false);
                        }
                        if (!TryRegisterHandle(op.result, child, handles, request.target, i, diagnostics))
                        {
                            UnityEngine.Object.DestroyImmediate(child);
                            return BuildError(
                                "UNITY_BRIDGE_SCHEMA",
                                "Invalid scene plan.",
                                request.target,
                                request.ops.Length,
                                executed: false,
                                applied: applied,
                                diagnostics: diagnostics.ToArray()
                            );
                        }
                        applied += 1;
                        continue;
                    }

                    if (string.Equals(opName, "instantiate_prefab", StringComparison.Ordinal))
                    {
                        string prefabTarget;
                        string prefabResolveError;
                        if (!TryResolveAssetPath(op.prefab, allowMissing: false, out prefabTarget, out prefabResolveError))
                        {
                            diagnostics.Add(
                                new BridgeDiagnostic
                                {
                                    path = request.target,
                                    location = $"ops[{i}].prefab",
                                    detail = "apply_error",
                                    evidence = prefabResolveError
                                }
                            );
                            return BuildError(
                                "UNITY_BRIDGE_APPLY",
                                "Failed to resolve prefab asset.",
                                request.target,
                                request.ops.Length,
                                executed: true,
                                applied: applied,
                                diagnostics: diagnostics.ToArray()
                            );
                        }
                        GameObject prefabAsset = AssetDatabase.LoadAssetAtPath<GameObject>(prefabTarget);
                        if (prefabAsset == null)
                        {
                            diagnostics.Add(
                                new BridgeDiagnostic
                                {
                                    path = request.target,
                                    location = $"ops[{i}].prefab",
                                    detail = "apply_error",
                                    evidence = $"prefab asset was not found: '{prefabTarget}'"
                                }
                            );
                            return BuildError(
                                "UNITY_BRIDGE_APPLY",
                                "Failed to resolve prefab asset.",
                                request.target,
                                request.ops.Length,
                                executed: true,
                                applied: applied,
                                diagnostics: diagnostics.ToArray()
                            );
                        }
                        GameObject parentObject;
                        bool parentIsSceneRoot;
                        string parentHandleError;
                        if (!TryResolveSceneParentHandle(op.parent, handles, out parentObject, out parentIsSceneRoot, out parentHandleError))
                        {
                            diagnostics.Add(
                                new BridgeDiagnostic
                                {
                                    path = request.target,
                                    location = $"ops[{i}].parent",
                                    detail = "schema_error",
                                    evidence = parentHandleError
                                }
                            );
                            return BuildError(
                                "UNITY_BRIDGE_SCHEMA",
                                "Invalid scene plan.",
                                request.target,
                                request.ops.Length,
                                executed: false,
                                applied: applied,
                                diagnostics: diagnostics.ToArray()
                            );
                        }

                        GameObject instantiated = PrefabUtility.InstantiatePrefab(prefabAsset, scene) as GameObject;
                        if (instantiated == null)
                        {
                            diagnostics.Add(
                                new BridgeDiagnostic
                                {
                                    path = request.target,
                                    location = $"ops[{i}]",
                                    detail = "apply_error",
                                    evidence = "PrefabUtility.InstantiatePrefab returned null"
                                }
                            );
                            return BuildError(
                                "UNITY_BRIDGE_APPLY",
                                "Failed to instantiate prefab.",
                                request.target,
                                request.ops.Length,
                                executed: true,
                                applied: applied,
                                diagnostics: diagnostics.ToArray()
                            );
                        }
                        if (!parentIsSceneRoot)
                        {
                            instantiated.transform.SetParent(parentObject.transform, false);
                        }
                        if (!TryRegisterHandle(op.result, instantiated, handles, request.target, i, diagnostics))
                        {
                            UnityEngine.Object.DestroyImmediate(instantiated);
                            return BuildError(
                                "UNITY_BRIDGE_SCHEMA",
                                "Invalid scene plan.",
                                request.target,
                                request.ops.Length,
                                executed: false,
                                applied: applied,
                                diagnostics: diagnostics.ToArray()
                            );
                        }
                        applied += 1;
                        continue;
                    }

                    if (string.Equals(opName, "rename_object", StringComparison.Ordinal))
                    {
                        GameObject targetObject;
                        string handleError;
                        if (!TryResolveGameObjectHandle(op.target, handles, out targetObject, out handleError))
                        {
                            diagnostics.Add(
                                new BridgeDiagnostic
                                {
                                    path = request.target,
                                    location = $"ops[{i}].target",
                                    detail = "schema_error",
                                    evidence = handleError
                                }
                            );
                            return BuildError(
                                "UNITY_BRIDGE_SCHEMA",
                                "Invalid scene plan.",
                                request.target,
                                request.ops.Length,
                                executed: false,
                                applied: applied,
                                diagnostics: diagnostics.ToArray()
                            );
                        }
                        if (string.IsNullOrWhiteSpace(op.name))
                        {
                            diagnostics.Add(
                                new BridgeDiagnostic
                                {
                                    path = request.target,
                                    location = $"ops[{i}].name",
                                    detail = "schema_error",
                                    evidence = "rename_object requires name"
                                }
                            );
                            return BuildError(
                                "UNITY_BRIDGE_SCHEMA",
                                "Invalid scene plan.",
                                request.target,
                                request.ops.Length,
                                executed: false,
                                applied: applied,
                                diagnostics: diagnostics.ToArray()
                            );
                        }
                        targetObject.name = op.name.Trim();
                        applied += 1;
                        continue;
                    }

                    if (string.Equals(opName, "reparent", StringComparison.Ordinal))
                    {
                        GameObject targetObject;
                        string targetHandleError;
                        if (!TryResolveGameObjectHandle(op.target, handles, out targetObject, out targetHandleError))
                        {
                            diagnostics.Add(
                                new BridgeDiagnostic
                                {
                                    path = request.target,
                                    location = $"ops[{i}].target",
                                    detail = "schema_error",
                                    evidence = targetHandleError
                                }
                            );
                            return BuildError(
                                "UNITY_BRIDGE_SCHEMA",
                                "Invalid scene plan.",
                                request.target,
                                request.ops.Length,
                                executed: false,
                                applied: applied,
                                diagnostics: diagnostics.ToArray()
                            );
                        }
                        GameObject parentObject;
                        bool parentIsSceneRoot;
                        string parentHandleError;
                        if (!TryResolveSceneParentHandle(op.parent, handles, out parentObject, out parentIsSceneRoot, out parentHandleError))
                        {
                            diagnostics.Add(
                                new BridgeDiagnostic
                                {
                                    path = request.target,
                                    location = $"ops[{i}].parent",
                                    detail = "schema_error",
                                    evidence = parentHandleError
                                }
                            );
                            return BuildError(
                                "UNITY_BRIDGE_SCHEMA",
                                "Invalid scene plan.",
                                request.target,
                                request.ops.Length,
                                executed: false,
                                applied: applied,
                                diagnostics: diagnostics.ToArray()
                            );
                        }
                        if (!parentIsSceneRoot && ReferenceEquals(targetObject, parentObject))
                        {
                            diagnostics.Add(
                                new BridgeDiagnostic
                                {
                                    path = request.target,
                                    location = $"ops[{i}]",
                                    detail = "schema_error",
                                    evidence = "target and parent handles must differ"
                                }
                            );
                            return BuildError(
                                "UNITY_BRIDGE_SCHEMA",
                                "Invalid scene plan.",
                                request.target,
                                request.ops.Length,
                                executed: false,
                                applied: applied,
                                diagnostics: diagnostics.ToArray()
                            );
                        }
                        if (parentIsSceneRoot)
                        {
                            targetObject.transform.SetParent(null, false);
                            SceneManager.MoveGameObjectToScene(targetObject, scene);
                        }
                        else
                        {
                            targetObject.transform.SetParent(parentObject.transform, false);
                        }
                        applied += 1;
                        continue;
                    }

                    if (string.Equals(opName, "add_component", StringComparison.Ordinal))
                    {
                        GameObject targetObject;
                        string targetHandleError;
                        if (!TryResolveGameObjectHandle(op.target, handles, out targetObject, out targetHandleError))
                        {
                            diagnostics.Add(
                                new BridgeDiagnostic
                                {
                                    path = request.target,
                                    location = $"ops[{i}].target",
                                    detail = "schema_error",
                                    evidence = targetHandleError
                                }
                            );
                            return BuildError(
                                "UNITY_BRIDGE_SCHEMA",
                                "Invalid scene plan.",
                                request.target,
                                request.ops.Length,
                                executed: false,
                                applied: applied,
                                diagnostics: diagnostics.ToArray()
                            );
                        }
                        Type componentType;
                        string typeError;
                        if (!TryResolveComponentType(op.type, out componentType, out typeError))
                        {
                            diagnostics.Add(
                                new BridgeDiagnostic
                                {
                                    path = request.target,
                                    location = $"ops[{i}].type",
                                    detail = "apply_error",
                                    evidence = typeError
                                }
                            );
                            return BuildError(
                                "UNITY_BRIDGE_APPLY",
                                "Failed to add component.",
                                request.target,
                                request.ops.Length,
                                executed: true,
                                applied: applied,
                                diagnostics: diagnostics.ToArray()
                            );
                        }
                        if (componentType.IsAbstract || componentType.ContainsGenericParameters)
                        {
                            diagnostics.Add(
                                new BridgeDiagnostic
                                {
                                    path = request.target,
                                    location = $"ops[{i}].type",
                                    detail = "apply_error",
                                    evidence = $"component type '{componentType.FullName ?? componentType.Name}' cannot be instantiated"
                                }
                            );
                            return BuildError(
                                "UNITY_BRIDGE_APPLY",
                                "Failed to add component.",
                                request.target,
                                request.ops.Length,
                                executed: true,
                                applied: applied,
                                diagnostics: diagnostics.ToArray()
                            );
                        }
                        if (componentType == typeof(Transform))
                        {
                            diagnostics.Add(
                                new BridgeDiagnostic
                                {
                                    path = request.target,
                                    location = $"ops[{i}].type",
                                    detail = "apply_error",
                                    evidence = "Transform is implicit and cannot be added"
                                }
                            );
                            return BuildError(
                                "UNITY_BRIDGE_APPLY",
                                "Failed to add component.",
                                request.target,
                                request.ops.Length,
                                executed: true,
                                applied: applied,
                                diagnostics: diagnostics.ToArray()
                            );
                        }
                        Component addedComponent = targetObject.AddComponent(componentType);
                        if (addedComponent == null)
                        {
                            diagnostics.Add(
                                new BridgeDiagnostic
                                {
                                    path = request.target,
                                    location = $"ops[{i}]",
                                    detail = "apply_error",
                                    evidence = $"AddComponent returned null for '{componentType.FullName ?? componentType.Name}'"
                                }
                            );
                            return BuildError(
                                "UNITY_BRIDGE_APPLY",
                                "Failed to add component.",
                                request.target,
                                request.ops.Length,
                                executed: true,
                                applied: applied,
                                diagnostics: diagnostics.ToArray()
                            );
                        }
                        string resultHandle = NormalizeHandle(op.result);
                        if (!TrySetupUdonSharpBacking(
                            targetObject, addedComponent, componentType, handles,
                            resultHandle, request.target, i, diagnostics))
                        {
                            UnityEngine.Object.DestroyImmediate(addedComponent);
                            return BuildError(
                                "UNITY_BRIDGE_APPLY",
                                "Failed to setup UdonSharp backing.",
                                request.target,
                                request.ops.Length,
                                executed: true,
                                applied: applied,
                                diagnostics: diagnostics.ToArray()
                            );
                        }
                        if (!TryRegisterHandle(op.result, addedComponent, handles, request.target, i, diagnostics))
                        {
                            UnityEngine.Object.DestroyImmediate(addedComponent);
                            return BuildError(
                                "UNITY_BRIDGE_SCHEMA",
                                "Invalid scene plan.",
                                request.target,
                                request.ops.Length,
                                executed: false,
                                applied: applied,
                                diagnostics: diagnostics.ToArray()
                            );
                        }
                        applied += 1;
                        continue;
                    }

                    if (string.Equals(opName, "find_component", StringComparison.Ordinal))
                    {
                        GameObject targetObject;
                        string targetHandleError;
                        if (!TryResolveGameObjectHandle(op.target, handles, out targetObject, out targetHandleError))
                        {
                            diagnostics.Add(
                                new BridgeDiagnostic
                                {
                                    path = request.target,
                                    location = $"ops[{i}].target",
                                    detail = "schema_error",
                                    evidence = targetHandleError
                                }
                            );
                            return BuildError(
                                "UNITY_BRIDGE_SCHEMA",
                                "Invalid scene plan.",
                                request.target,
                                request.ops.Length,
                                executed: false,
                                applied: applied,
                                diagnostics: diagnostics.ToArray()
                            );
                        }
                        Component foundComponent;
                        string componentError;
                        if (!TryFindUniqueComponentOnObject(targetObject, op.type, out foundComponent, out componentError))
                        {
                            diagnostics.Add(
                                new BridgeDiagnostic
                                {
                                    path = request.target,
                                    location = $"ops[{i}].type",
                                    detail = "apply_error",
                                    evidence = componentError
                                }
                            );
                            return BuildError(
                                "UNITY_BRIDGE_APPLY",
                                "Failed to resolve component.",
                                request.target,
                                request.ops.Length,
                                executed: true,
                                applied: applied,
                                diagnostics: diagnostics.ToArray()
                            );
                        }
                        if (!TryRegisterHandle(op.result, foundComponent, handles, request.target, i, diagnostics))
                        {
                            return BuildError(
                                "UNITY_BRIDGE_SCHEMA",
                                "Invalid scene plan.",
                                request.target,
                                request.ops.Length,
                                executed: false,
                                applied: applied,
                                diagnostics: diagnostics.ToArray()
                            );
                        }
                        applied += 1;
                        continue;
                    }

                    if (string.Equals(opName, "remove_component", StringComparison.Ordinal))
                    {
                        Component targetComponent;
                        string componentHandleError;
                        if (!TryResolveComponentHandle(op.target, handles, out targetComponent, out componentHandleError))
                        {
                            diagnostics.Add(
                                new BridgeDiagnostic
                                {
                                    path = request.target,
                                    location = $"ops[{i}].target",
                                    detail = "schema_error",
                                    evidence = componentHandleError
                                }
                            );
                            return BuildError(
                                "UNITY_BRIDGE_SCHEMA",
                                "Invalid scene plan.",
                                request.target,
                                request.ops.Length,
                                executed: false,
                                applied: applied,
                                diagnostics: diagnostics.ToArray()
                            );
                        }
                        if (targetComponent is Transform)
                        {
                            diagnostics.Add(
                                new BridgeDiagnostic
                                {
                                    path = request.target,
                                    location = $"ops[{i}].target",
                                    detail = "apply_error",
                                    evidence = "Transform cannot be removed"
                                }
                            );
                            return BuildError(
                                "UNITY_BRIDGE_APPLY",
                                "Failed to remove component.",
                                request.target,
                                request.ops.Length,
                                executed: true,
                                applied: applied,
                                diagnostics: diagnostics.ToArray()
                            );
                        }
                        UnityEngine.Object.DestroyImmediate(targetComponent);
                        applied += 1;
                        continue;
                    }

                    if (
                        string.Equals(opName, "set", StringComparison.Ordinal)
                        || string.Equals(opName, "insert_array_element", StringComparison.Ordinal)
                        || string.Equals(opName, "remove_array_element", StringComparison.Ordinal)
                    )
                    {
                        Component targetComponent;
                        string componentHandleError;
                        if (!TryResolveComponentHandle(op.target, handles, out targetComponent, out componentHandleError))
                        {
                            diagnostics.Add(
                                new BridgeDiagnostic
                                {
                                    path = request.target,
                                    location = $"ops[{i}].target",
                                    detail = "schema_error",
                                    evidence = componentHandleError
                                }
                            );
                            return BuildError(
                                "UNITY_BRIDGE_SCHEMA",
                                "Invalid scene plan.",
                                request.target,
                                request.ops.Length,
                                executed: false,
                                applied: applied,
                                diagnostics: diagnostics.ToArray()
                            );
                        }
                        s_currentHandles = handles;
                        try
                        {
                            if (!TryApplyMutationOpToComponent(targetComponent, request.target, op, i, diagnostics))
                            {
                                return BuildError(
                                    "UNITY_BRIDGE_APPLY",
                                    "Failed to apply component mutation.",
                                    request.target,
                                    request.ops.Length,
                                    executed: true,
                                    applied: applied,
                                    diagnostics: diagnostics.ToArray()
                                );
                            }
                        }
                        finally
                        {
                            s_currentHandles = null;
                        }
                        applied += 1;
                        continue;
                    }

                    if (string.Equals(opName, "save_scene", StringComparison.Ordinal))
                    {
                        if (i != request.ops.Length - 1)
                        {
                            diagnostics.Add(
                                new BridgeDiagnostic
                                {
                                    path = request.target,
                                    location = $"ops[{i}].op",
                                    detail = "schema_error",
                                    evidence = "save_scene must be the final operation in scene mode"
                                }
                            );
                            return BuildError(
                                "UNITY_BRIDGE_SCHEMA",
                                "Invalid scene plan.",
                                request.target,
                                request.ops.Length,
                                executed: false,
                                applied: applied,
                                diagnostics: diagnostics.ToArray()
                            );
                        }
                        if (!EditorSceneManager.SaveScene(scene, assetPath))
                        {
                            diagnostics.Add(
                                new BridgeDiagnostic
                                {
                                    path = request.target,
                                    location = $"ops[{i}].op",
                                    detail = "apply_error",
                                    evidence = "EditorSceneManager.SaveScene returned false"
                                }
                            );
                            return BuildError(
                                "UNITY_BRIDGE_APPLY",
                                "Failed to save scene asset.",
                                request.target,
                                request.ops.Length,
                                executed: true,
                                applied: applied,
                                diagnostics: diagnostics.ToArray()
                            );
                        }
                        AssetDatabase.SaveAssets();
                        AssetDatabase.Refresh(ImportAssetOptions.ForceSynchronousImport);
                        Scene reopened = EditorSceneManager.OpenScene(assetPath, OpenSceneMode.Single);
                        if (!reopened.IsValid() || !reopened.isLoaded)
                        {
                            diagnostics.Add(
                                new BridgeDiagnostic
                                {
                                    path = request.target,
                                    location = $"ops[{i}].op",
                                    detail = "apply_error",
                                    evidence = "failed to reopen scene after save"
                                }
                            );
                            return BuildError(
                                "UNITY_BRIDGE_APPLY",
                                "Failed to reopen scene after save.",
                                request.target,
                                request.ops.Length,
                                executed: true,
                                applied: applied,
                                diagnostics: diagnostics.ToArray()
                            );
                        }
                        scene = reopened;
                        saved = true;
                        applied += 1;
                        continue;
                    }

                    diagnostics.Add(
                        new BridgeDiagnostic
                        {
                            path = request.target,
                            location = $"ops[{i}].op",
                            detail = "schema_error",
                            evidence = $"unsupported scene op '{opName}'"
                        }
                    );
                    return BuildError(
                        "UNITY_BRIDGE_SCHEMA",
                        "Invalid scene plan.",
                        request.target,
                        request.ops.Length,
                        executed: false,
                        applied: applied,
                        diagnostics: diagnostics.ToArray()
                    );
                }

                if (!sceneOpened || !saved)
                {
                    return BuildError(
                        "UNITY_BRIDGE_SCHEMA",
                        "Scene mode requires an initial open/create op and save_scene.",
                        request.target,
                        request.ops.Length,
                        executed: false,
                        applied: applied
                    );
                }

                return new BridgeResponse
                {
                    protocol_version = ProtocolVersion,
                    success = true,
                    severity = "info",
                    code = "SER_APPLY_OK",
                    message = "Scene plan applied via Unity executeMethod.",
                    data = new BridgeData
                    {
                        target = request.target,
                        op_count = request.ops.Length,
                        applied = applied,
                        read_only = false,
                        executed = true,
                        protocol_version = ProtocolVersion
                    },
                    diagnostics = Array.Empty<BridgeDiagnostic>()
                };
            }
            catch (Exception ex)
            {
                diagnostics.Add(
                    new BridgeDiagnostic
                    {
                        path = request.target,
                        location = "apply",
                        detail = "exception",
                        evidence = ex.ToString()
                    }
                );
                return BuildError(
                    "UNITY_BRIDGE_APPLY_EXCEPTION",
                    $"Unexpected apply exception: {ex.Message}",
                    request.target,
                    request.ops.Length,
                    executed: true,
                    applied: applied,
                    diagnostics: diagnostics.ToArray()
                );
            }
        }

        private static string NormalizeHandle(string raw)
        {
            string normalized = (raw ?? string.Empty).Trim();
            if (normalized.StartsWith("$", StringComparison.Ordinal))
            {
                normalized = normalized.Substring(1);
            }
            return normalized.Trim();
        }

        private static bool TryRegisterHandle(
            string rawHandle,
            UnityEngine.Object obj,
            Dictionary<string, UnityEngine.Object> handles,
            string requestTarget,
            int opIndex,
            List<BridgeDiagnostic> diagnostics
        )
        {
            string handle = NormalizeHandle(rawHandle);
            if (string.IsNullOrWhiteSpace(handle))
            {
                return true;
            }
            if (handles.ContainsKey(handle))
            {
                diagnostics.Add(
                    new BridgeDiagnostic
                    {
                        path = requestTarget,
                        location = $"ops[{opIndex}].result",
                        detail = "schema_error",
                        evidence = $"handle '{handle}' is already defined"
                    }
                );
                return false;
            }
            handles[handle] = obj;
            return true;
        }

        private static bool TryResolveHandle(
            string rawHandle,
            Dictionary<string, UnityEngine.Object> handles,
            out UnityEngine.Object obj,
            out string error
        )
        {
            obj = null;
            string handle = NormalizeHandle(rawHandle);
            if (string.IsNullOrWhiteSpace(handle))
            {
                error = "handle is required";
                return false;
            }
            if (!handles.TryGetValue(handle, out obj) || obj == null)
            {
                error = $"unknown handle '{handle}'";
                return false;
            }
            error = string.Empty;
            return true;
        }

        private static bool TryResolveGameObjectHandle(
            string rawHandle,
            Dictionary<string, UnityEngine.Object> handles,
            out GameObject obj,
            out string error
        )
        {
            obj = null;
            UnityEngine.Object handleObject;
            if (!TryResolveHandle(rawHandle, handles, out handleObject, out error))
            {
                return false;
            }
            obj = handleObject as GameObject;
            if (obj == null)
            {
                error = $"handle '{NormalizeHandle(rawHandle)}' does not reference a GameObject";
                return false;
            }
            return true;
        }

        private static bool TryResolveComponentHandle(
            string rawHandle,
            Dictionary<string, UnityEngine.Object> handles,
            out Component component,
            out string error
        )
        {
            component = null;
            UnityEngine.Object handleObject;
            if (!TryResolveHandle(rawHandle, handles, out handleObject, out error))
            {
                return false;
            }
            component = handleObject as Component;
            if (component == null)
            {
                error = $"handle '{NormalizeHandle(rawHandle)}' does not reference a Component";
                return false;
            }
            return true;
        }

        private static bool TryResolveAssetHandle(
            string rawHandle,
            Dictionary<string, UnityEngine.Object> handles,
            out UnityEngine.Object assetObject,
            out string error
        )
        {
            assetObject = null;
            UnityEngine.Object handleObject;
            if (!TryResolveHandle(rawHandle, handles, out handleObject, out error))
            {
                return false;
            }
            if (handleObject is GameObject || handleObject is Component)
            {
                error = $"handle '{NormalizeHandle(rawHandle)}' does not reference an asset";
                return false;
            }
            assetObject = handleObject;
            return true;
        }

        private static bool TryResolveSceneParentHandle(
            string rawHandle,
            Dictionary<string, UnityEngine.Object> handles,
            out GameObject parentObject,
            out bool isSceneRoot,
            out string error
        )
        {
            parentObject = null;
            isSceneRoot = false;
            string normalized = NormalizeHandle(rawHandle);
            if (string.Equals(normalized, SceneHandleName, StringComparison.Ordinal))
            {
                error = string.Empty;
                isSceneRoot = true;
                return true;
            }
            return TryResolveGameObjectHandle(rawHandle, handles, out parentObject, out error);
        }

        private static bool TrySetupUdonSharpBacking(
            GameObject targetObject,
            Component addedComponent,
            Type componentType,
            Dictionary<string, UnityEngine.Object> handles,
            string handleName,
            string requestTarget,
            int opIndex,
            List<BridgeDiagnostic> diagnostics
        )
        {
            Type usbType = null;
            foreach (Assembly assembly in AppDomain.CurrentDomain.GetAssemblies())
            {
                usbType = assembly.GetType("UdonSharp.UdonSharpBehaviour", false);
                if (usbType != null) break;
            }
            if (usbType == null || !usbType.IsAssignableFrom(componentType))
            {
                return true;
            }

            Type udonBehaviourType = null;
            foreach (Assembly assembly in AppDomain.CurrentDomain.GetAssemblies())
            {
                udonBehaviourType = assembly.GetType("VRC.Udon.UdonBehaviour", false);
                if (udonBehaviourType != null) break;
            }
            if (udonBehaviourType == null)
            {
                diagnostics.Add(
                    new BridgeDiagnostic
                    {
                        path = requestTarget,
                        location = $"ops[{opIndex}]",
                        detail = "apply_error",
                        evidence = "UdonSharpBehaviour detected but VRC.Udon.UdonBehaviour type not found"
                    }
                );
                return false;
            }

            Component backing = targetObject.AddComponent(udonBehaviourType);
            if (backing == null)
            {
                diagnostics.Add(
                    new BridgeDiagnostic
                    {
                        path = requestTarget,
                        location = $"ops[{opIndex}]",
                        detail = "apply_error",
                        evidence = "Failed to create backing UdonBehaviour"
                    }
                );
                return false;
            }

            SerializedObject usbSerialized = new SerializedObject(addedComponent);
            SerializedProperty backingProp = usbSerialized.FindProperty("_udonSharpBackingUdonBehaviour");
            if (backingProp != null)
            {
                backingProp.objectReferenceValue = backing;
                usbSerialized.ApplyModifiedPropertiesWithoutUndo();
            }

            Type programAssetType = null;
            foreach (Assembly assembly in AppDomain.CurrentDomain.GetAssemblies())
            {
                programAssetType = assembly.GetType("UdonSharp.UdonSharpProgramAsset", false);
                if (programAssetType != null) break;
            }
            if (programAssetType != null)
            {
                MethodInfo getAllPrograms = programAssetType.GetMethod(
                    "GetAllUdonSharpPrograms",
                    BindingFlags.Public | BindingFlags.Static
                );
                if (getAllPrograms != null)
                {
                    Array programs = getAllPrograms.Invoke(null, null) as Array;
                    if (programs != null)
                    {
                        PropertyInfo csScriptProp = programAssetType.GetProperty(
                            "sourceCsScript",
                            BindingFlags.Public | BindingFlags.Instance
                        );
                        foreach (object program in programs)
                        {
                            if (csScriptProp == null) continue;
                            MonoScript script = csScriptProp.GetValue(program) as MonoScript;
                            if (script != null && script.GetClass() == componentType)
                            {
                                SerializedObject backingSO = new SerializedObject(backing);
                                SerializedProperty programSourceProp =
                                    backingSO.FindProperty("programSource");
                                if (programSourceProp != null)
                                {
                                    programSourceProp.objectReferenceValue =
                                        program as UnityEngine.Object;
                                    backingSO.ApplyModifiedPropertiesWithoutUndo();
                                }
                                break;
                            }
                        }
                    }
                }
            }

            if (!string.IsNullOrWhiteSpace(handleName))
            {
                string backingHandle = $"backing_{handleName}";
                if (!handles.ContainsKey(backingHandle))
                {
                    handles[backingHandle] = backing;
                }
            }

            return true;
        }

        private static bool TryResolveComponentType(
            string rawTypeName,
            out Type componentType,
            out string error
        )
        {
            componentType = null;
            error = string.Empty;
            Type resolvedType;
            if (!TryResolveType(rawTypeName, out resolvedType, out error))
            {
                return false;
            }
            if (!typeof(Component).IsAssignableFrom(resolvedType))
            {
                error = $"type '{resolvedType.FullName ?? resolvedType.Name}' is not a UnityEngine.Component";
                return false;
            }
            componentType = resolvedType;
            return true;
        }

        private static bool TryFindUniqueComponentOnObject(
            GameObject targetObject,
            string rawTypeName,
            out Component component,
            out string error
        )
        {
            component = null;
            error = string.Empty;
            Type targetType;
            if (!TryResolveComponentType(rawTypeName, out targetType, out error))
            {
                return false;
            }

            Component[] components = targetObject.GetComponents<Component>();
            List<Component> matches = new List<Component>();
            HashSet<string> availableTypeNames = new HashSet<string>(StringComparer.Ordinal);
            for (int i = 0; i < components.Length; i++)
            {
                Component candidate = components[i];
                if (candidate == null)
                {
                    continue;
                }

                Type candidateType = candidate.GetType();
                availableTypeNames.Add(candidateType.FullName ?? candidateType.Name);
                if (targetType.IsAssignableFrom(candidateType))
                {
                    matches.Add(candidate);
                }
            }

            string objectPath = BuildHierarchyPath(targetObject.transform);
            if (matches.Count == 1)
            {
                component = matches[0];
                return true;
            }
            if (matches.Count == 0)
            {
                string available = BuildTypeNameSample(availableTypeNames, 8);
                error = string.IsNullOrEmpty(available)
                    ? $"component type '{rawTypeName}' was not found on '{objectPath}'"
                    : $"component type '{rawTypeName}' was not found on '{objectPath}'. available types: {available}";
                return false;
            }

            error = $"component type '{rawTypeName}' matched {matches.Count} components on '{objectPath}'";
            return false;
        }

        private static bool TryResolveAssetPath(
            string target,
            bool allowMissing,
            out string assetPath,
            out string error
        )
        {
            assetPath = (target ?? string.Empty).Trim().Replace('\\', '/');
            error = string.Empty;
            if (string.IsNullOrWhiteSpace(assetPath))
            {
                error = "target is empty.";
                return false;
            }

            string projectRoot = Path.GetFullPath(Path.Combine(Application.dataPath, ".."));
            if (Path.IsPathRooted(assetPath))
            {
                string fullTarget = Path.GetFullPath(assetPath).Replace('\\', '/');
                string fullProjectRoot = projectRoot.Replace('\\', '/');
                string prefix = fullProjectRoot + "/";
                if (!fullTarget.StartsWith(prefix, StringComparison.OrdinalIgnoreCase))
                {
                    error = "absolute target must be inside the Unity project root.";
                    return false;
                }
                assetPath = fullTarget.Substring(prefix.Length);
            }

            assetPath = assetPath.Replace('\\', '/');
            if (!assetPath.StartsWith("Assets/", StringComparison.OrdinalIgnoreCase))
            {
                error = "target must resolve to an Assets/ path.";
                return false;
            }
            if (!allowMissing && !File.Exists(Path.Combine(projectRoot, assetPath)))
            {
                error = "target file was not found.";
                return false;
            }
            return true;
        }

        private static bool TryApplyOp(
            GameObject prefabRoot,
            string target,
            PatchOp op,
            int opIndex,
            List<BridgeDiagnostic> diagnostics
        )
        {
            if (op == null)
            {
                diagnostics.Add(
                    new BridgeDiagnostic
                    {
                        path = target,
                        location = $"ops[{opIndex}]",
                        detail = "schema_error",
                        evidence = "operation is null"
                    }
                );
                return false;
            }
            if (
                !string.Equals(op.op, "set", StringComparison.Ordinal)
                && !string.Equals(op.op, "insert_array_element", StringComparison.Ordinal)
                && !string.Equals(op.op, "remove_array_element", StringComparison.Ordinal)
            )
            {
                diagnostics.Add(
                    new BridgeDiagnostic
                    {
                        path = target,
                        location = $"ops[{opIndex}].op",
                        detail = "schema_error",
                        evidence = $"unsupported op '{op.op}'"
                    }
                );
                return false;
            }
            if (string.IsNullOrWhiteSpace(op.component))
            {
                diagnostics.Add(
                    new BridgeDiagnostic
                    {
                        path = target,
                        location = $"ops[{opIndex}].component",
                        detail = "schema_error",
                        evidence = "component is required"
                    }
                );
                return false;
            }

            Component component;
            string componentError;
            if (!TryFindUniqueComponent(prefabRoot, op.component, out component, out componentError))
            {
                diagnostics.Add(
                    new BridgeDiagnostic
                    {
                        path = target,
                        location = $"ops[{opIndex}].component",
                        detail = "apply_error",
                        evidence = componentError
                    }
                );
                return false;
            }

            return TryApplyMutationOpToObject(component, target, op, opIndex, diagnostics);
        }

        private static bool TryApplyMutationOpToObject(
            UnityEngine.Object targetObject,
            string target,
            PatchOp op,
            int opIndex,
            List<BridgeDiagnostic> diagnostics
        )
        {
            if (targetObject == null)
            {
                diagnostics.Add(
                    new BridgeDiagnostic
                    {
                        path = target,
                        location = $"ops[{opIndex}]",
                        detail = "apply_error",
                        evidence = "target object resolved to null"
                    }
                );
                return false;
            }
            if (string.IsNullOrWhiteSpace(op.path))
            {
                diagnostics.Add(
                    new BridgeDiagnostic
                    {
                        path = target,
                        location = $"ops[{opIndex}].path",
                        detail = "schema_error",
                        evidence = "path is required"
                    }
                );
                return false;
            }

            SerializedObject serialized = new SerializedObject(targetObject);
            string opName = (op.op ?? string.Empty).Trim();
            if (string.Equals(opName, "set", StringComparison.Ordinal))
            {
                SerializedProperty property = serialized.FindProperty(op.path);
                if (property == null)
                {
                    string hint = BuildSetPathHint(op.path);
                    diagnostics.Add(
                        new BridgeDiagnostic
                        {
                            path = target,
                            location = $"ops[{opIndex}].path",
                            detail = "apply_error",
                            evidence = string.IsNullOrEmpty(hint)
                                ? $"property not found: '{op.path}'"
                                : $"property not found: '{op.path}'. {hint}"
                        }
                    );
                    return false;
                }

                string setError;
                if (!TryAssignPropertyValue(property, op, out setError))
                {
                    diagnostics.Add(
                        new BridgeDiagnostic
                        {
                            path = target,
                            location = $"ops[{opIndex}]",
                            detail = "apply_error",
                            evidence = setError
                        }
                    );
                    return false;
                }

                serialized.ApplyModifiedPropertiesWithoutUndo();
                return true;
            }

            SerializedProperty arrayProperty;
            string arrayError;
            if (!TryResolveArrayProperty(serialized, op.path, out arrayProperty, out arrayError))
            {
                diagnostics.Add(
                    new BridgeDiagnostic
                    {
                        path = target,
                        location = $"ops[{opIndex}]",
                        detail = "apply_error",
                        evidence = arrayError
                    }
                );
                return false;
            }

            if (string.Equals(opName, "insert_array_element", StringComparison.Ordinal))
            {
                if (op.index < 0 || op.index > arrayProperty.arraySize)
                {
                    diagnostics.Add(
                        new BridgeDiagnostic
                        {
                            path = target,
                            location = $"ops[{opIndex}].index",
                            detail = "apply_error",
                            evidence = $"insert index {op.index} is out of bounds"
                        }
                    );
                    return false;
                }
                arrayProperty.InsertArrayElementAtIndex(op.index);
                SerializedProperty inserted = arrayProperty.GetArrayElementAtIndex(op.index);
                if (!string.IsNullOrWhiteSpace(op.value_kind))
                {
                    string insertValueError;
                    if (!TryAssignPropertyValue(inserted, op, out insertValueError))
                    {
                        diagnostics.Add(
                            new BridgeDiagnostic
                            {
                                path = target,
                                location = $"ops[{opIndex}]",
                                detail = "apply_error",
                                evidence = insertValueError
                            }
                        );
                        return false;
                    }
                }
                serialized.ApplyModifiedPropertiesWithoutUndo();
                return true;
            }

            if (op.index < 0 || op.index >= arrayProperty.arraySize)
            {
                diagnostics.Add(
                    new BridgeDiagnostic
                    {
                        path = target,
                        location = $"ops[{opIndex}].index",
                        detail = "apply_error",
                        evidence = $"remove index {op.index} is out of bounds"
                    }
                );
                return false;
            }

            int beforeSize = arrayProperty.arraySize;
            arrayProperty.DeleteArrayElementAtIndex(op.index);
            if (arrayProperty.arraySize == beforeSize)
            {
                arrayProperty.DeleteArrayElementAtIndex(op.index);
            }
            if (arrayProperty.arraySize != beforeSize - 1)
            {
                diagnostics.Add(
                    new BridgeDiagnostic
                    {
                        path = target,
                        location = $"ops[{opIndex}]",
                        detail = "apply_error",
                        evidence = "remove array element did not change array size as expected"
                    }
                );
                return false;
            }
            serialized.ApplyModifiedPropertiesWithoutUndo();
            return true;
        }

        private static bool TryApplyMutationOpToComponent(
            Component component,
            string target,
            PatchOp op,
            int opIndex,
            List<BridgeDiagnostic> diagnostics
        )
        {
            return TryApplyMutationOpToObject(component, target, op, opIndex, diagnostics);
        }

        private static bool TryResolveArrayProperty(
            SerializedObject serialized,
            string propertyPath,
            out SerializedProperty arrayProperty,
            out string error
        )
        {
            arrayProperty = null;
            error = string.Empty;
            if (string.IsNullOrWhiteSpace(propertyPath))
            {
                error = "array operation path is empty";
                return false;
            }
            if (propertyPath.EndsWith(".Array.size", StringComparison.Ordinal))
            {
                error = "array operation path must target '.Array.data'; use set with '.Array.size' for resize";
                return false;
            }
            if (propertyPath.IndexOf(".Array.data[", StringComparison.Ordinal) >= 0)
            {
                error = "array operation path must target the array itself; remove element index from the path";
                return false;
            }
            if (!propertyPath.EndsWith(ArrayDataSuffix, StringComparison.Ordinal))
            {
                error = $"array operation path must end with '{ArrayDataSuffix}'";
                return false;
            }
            string arrayPath = propertyPath.Substring(0, propertyPath.Length - ArrayDataSuffix.Length);
            if (string.IsNullOrWhiteSpace(arrayPath))
            {
                error = "array operation path must include a property prefix before '.Array.data'";
                return false;
            }
            arrayProperty = serialized.FindProperty(arrayPath);
            if (arrayProperty == null)
            {
                error = $"array property not found: '{arrayPath}'";
                return false;
            }
            if (!arrayProperty.isArray || arrayProperty.propertyType == SerializedPropertyType.String)
            {
                error = $"property is not an array: '{arrayPath}'";
                return false;
            }
            if (TryIsFixedBufferProperty(arrayProperty, out int fixedBufferSize) && fixedBufferSize >= 0)
            {
                string detail = fixedBufferSize > 0
                    ? $"property is fixed buffer (size={fixedBufferSize}); insert/remove are not supported"
                    : "property is fixed buffer; insert/remove are not supported";
                error = $"{detail}: '{arrayPath}'";
                return false;
            }
            return true;
        }

        private static bool TryIsFixedBufferProperty(SerializedProperty property, out int fixedBufferSize)
        {
            fixedBufferSize = -1;
            if (property == null)
            {
                return false;
            }
            if (SerializedPropertyIsFixedBufferProperty != null)
            {
                try
                {
                    object rawIsFixedBuffer = SerializedPropertyIsFixedBufferProperty.GetValue(property, null);
                    if (rawIsFixedBuffer is bool boolValue && !boolValue)
                    {
                        return false;
                    }
                    if (rawIsFixedBuffer is bool)
                    {
                        fixedBufferSize = 0;
                    }
                }
                catch
                {
                }
            }
            if (SerializedPropertyFixedBufferSizeProperty != null)
            {
                try
                {
                    object rawSize = SerializedPropertyFixedBufferSizeProperty.GetValue(property, null);
                    if (rawSize is int size && size > 0)
                    {
                        fixedBufferSize = size;
                        return true;
                    }
                }
                catch
                {
                }
            }
            return fixedBufferSize == 0;
        }

        private static bool TryFindUniqueComponent(
            GameObject root,
            string selector,
            out Component component,
            out string error
        )
        {
            component = null;
            error = string.Empty;

            string typeSelector;
            string hierarchySelector;
            if (!TryParseComponentSelector(selector, out typeSelector, out hierarchySelector, out error))
            {
                return false;
            }

            Component[] components = root.GetComponentsInChildren<Component>(true);
            List<Component> matches = new List<Component>();
            List<Component> typeMatches = new List<Component>();
            HashSet<string> availableTypeNames = new HashSet<string>(StringComparer.Ordinal);
            for (int i = 0; i < components.Length; i++)
            {
                Component candidate = components[i];
                if (candidate == null)
                {
                    continue;
                }

                Type type = candidate.GetType();
                if (!string.IsNullOrEmpty(type.FullName))
                {
                    availableTypeNames.Add(type.FullName);
                }
                else
                {
                    availableTypeNames.Add(type.Name);
                }

                if (
                    !string.Equals(type.FullName, typeSelector, StringComparison.Ordinal)
                    && !string.Equals(type.Name, typeSelector, StringComparison.Ordinal)
                    && !string.Equals(type.AssemblyQualifiedName, typeSelector, StringComparison.Ordinal)
                )
                {
                    continue;
                }

                typeMatches.Add(candidate);
                if (!string.IsNullOrWhiteSpace(hierarchySelector))
                {
                    string candidatePath = BuildHierarchyPath(candidate.transform).Replace('\\', '/');
                    if (!string.Equals(candidatePath, hierarchySelector, StringComparison.OrdinalIgnoreCase))
                    {
                        continue;
                    }
                }
                matches.Add(candidate);
            }

            if (matches.Count == 1)
            {
                component = matches[0];
                return true;
            }
            if (matches.Count == 0)
            {
                if (!string.IsNullOrWhiteSpace(hierarchySelector) && typeMatches.Count > 0)
                {
                    string candidates = BuildComponentSample(typeMatches, 5);
                    error = string.IsNullOrEmpty(candidates)
                        ? $"component path filter did not match any '{typeSelector}' components: '{hierarchySelector}'"
                        : $"component path filter did not match any '{typeSelector}' components at '{hierarchySelector}'. available paths: {candidates}";
                    return false;
                }

                string available = BuildTypeNameSample(availableTypeNames, 8);
                error = string.IsNullOrEmpty(available)
                    ? $"component not found: '{selector}'"
                    : $"component not found: '{selector}'. available types: {available}";
                return false;
            }

            string matchedCandidates = BuildComponentSample(matches, 5);
            error = string.IsNullOrEmpty(matchedCandidates)
                ? $"component selector is ambiguous: '{selector}' matched {matches.Count} components"
                : $"component selector is ambiguous: '{selector}' matched {matches.Count} components ({matchedCandidates})";
            return false;
        }

        private static bool TryParseComponentSelector(
            string selector,
            out string typeSelector,
            out string hierarchySelector,
            out string error
        )
        {
            typeSelector = string.Empty;
            hierarchySelector = string.Empty;
            error = string.Empty;

            string raw = (selector ?? string.Empty).Trim();
            if (string.IsNullOrWhiteSpace(raw))
            {
                error = "component selector is empty";
                return false;
            }

            int delimiter = raw.IndexOf('@');
            if (delimiter < 0)
            {
                typeSelector = raw;
                return true;
            }

            typeSelector = raw.Substring(0, delimiter).Trim();
            hierarchySelector = raw.Substring(delimiter + 1).Trim().Replace('\\', '/');
            if (string.IsNullOrWhiteSpace(typeSelector))
            {
                error = "component selector must include type before '@'";
                return false;
            }
            if (string.IsNullOrWhiteSpace(hierarchySelector))
            {
                error = "component selector must include hierarchy path after '@'";
                return false;
            }
            return true;
        }

        private static bool TryAssignPropertyValue(
            SerializedProperty property,
            PatchOp op,
            out string error
        )
        {
            error = string.Empty;
            string valueKind = (op.value_kind ?? string.Empty).Trim();
            switch (property.propertyType)
            {
                case SerializedPropertyType.Integer:
                {
                    int intValue;
                    if (!TryReadIntegerValue(op, valueKind, out intValue, out error))
                    {
                        return false;
                    }
                    property.intValue = intValue;
                    return true;
                }
                case SerializedPropertyType.Float:
                {
                    float floatValue;
                    if (!TryReadFloatValue(op, valueKind, out floatValue, out error))
                    {
                        return false;
                    }
                    property.floatValue = floatValue;
                    return true;
                }
                case SerializedPropertyType.Boolean:
                {
                    bool boolValue;
                    if (!TryReadBoolValue(op, valueKind, out boolValue, out error))
                    {
                        return false;
                    }
                    property.boolValue = boolValue;
                    return true;
                }
                case SerializedPropertyType.Character:
                {
                    int charValue;
                    if (!TryReadCharacterValue(op, valueKind, out charValue, out error))
                    {
                        return false;
                    }
                    property.intValue = charValue;
                    return true;
                }
                case SerializedPropertyType.String:
                    if (string.Equals(valueKind, "string", StringComparison.Ordinal))
                    {
                        property.stringValue = op.value_string ?? string.Empty;
                        return true;
                    }
                    if (string.Equals(valueKind, "null", StringComparison.Ordinal))
                    {
                        property.stringValue = string.Empty;
                        return true;
                    }
                    error = "string property requires value_kind='string' or 'null'";
                    return false;
                case SerializedPropertyType.Enum:
                {
                    int enumIndex;
                    if (!TryReadEnumValue(property, op, valueKind, out enumIndex, out error))
                    {
                        return false;
                    }
                    property.enumValueIndex = enumIndex;
                    return true;
                }
                case SerializedPropertyType.Color:
                {
                    Color colorValue;
                    if (!TryReadColorValue(op, valueKind, out colorValue, out error))
                    {
                        return false;
                    }
                    property.colorValue = colorValue;
                    return true;
                }
                case SerializedPropertyType.Vector2:
                {
                    Vector2 value;
                    if (!TryReadVector2Value(op, valueKind, out value, out error))
                    {
                        return false;
                    }
                    property.vector2Value = value;
                    return true;
                }
                case SerializedPropertyType.Vector3:
                {
                    Vector3 value;
                    if (!TryReadVector3Value(op, valueKind, out value, out error))
                    {
                        return false;
                    }
                    property.vector3Value = value;
                    return true;
                }
                case SerializedPropertyType.Vector4:
                {
                    Vector4 value;
                    if (!TryReadVector4Value(op, valueKind, out value, out error))
                    {
                        return false;
                    }
                    property.vector4Value = value;
                    return true;
                }
                case SerializedPropertyType.Vector2Int:
                {
                    Vector2Int value;
                    if (!TryReadVector2IntValue(op, valueKind, out value, out error))
                    {
                        return false;
                    }
                    property.vector2IntValue = value;
                    return true;
                }
                case SerializedPropertyType.Vector3Int:
                {
                    Vector3Int value;
                    if (!TryReadVector3IntValue(op, valueKind, out value, out error))
                    {
                        return false;
                    }
                    property.vector3IntValue = value;
                    return true;
                }
                case SerializedPropertyType.Rect:
                {
                    Rect value;
                    if (!TryReadRectValue(op, valueKind, out value, out error))
                    {
                        return false;
                    }
                    property.rectValue = value;
                    return true;
                }
                case SerializedPropertyType.RectInt:
                {
                    RectInt value;
                    if (!TryReadRectIntValue(op, valueKind, out value, out error))
                    {
                        return false;
                    }
                    property.rectIntValue = value;
                    return true;
                }
                case SerializedPropertyType.Bounds:
                {
                    Bounds value;
                    if (!TryReadBoundsValue(op, valueKind, out value, out error))
                    {
                        return false;
                    }
                    property.boundsValue = value;
                    return true;
                }
                case SerializedPropertyType.BoundsInt:
                {
                    BoundsInt value;
                    if (!TryReadBoundsIntValue(op, valueKind, out value, out error))
                    {
                        return false;
                    }
                    property.boundsIntValue = value;
                    return true;
                }
                case SerializedPropertyType.Quaternion:
                {
                    Quaternion value;
                    if (!TryReadQuaternionValue(op, valueKind, out value, out error))
                    {
                        return false;
                    }
                    property.quaternionValue = value;
                    return true;
                }
                case SerializedPropertyType.AnimationCurve:
                {
                    AnimationCurve value;
                    if (!TryReadAnimationCurveValue(op, valueKind, out value, out error))
                    {
                        return false;
                    }
                    property.animationCurveValue = value;
                    return true;
                }
                case SerializedPropertyType.Gradient:
                {
                    object value;
                    if (!TryReadGradientValue(op, valueKind, out value, out error))
                    {
                        return false;
                    }
                    if (SerializedPropertyGradientValueProperty == null)
                    {
                        error = "Gradient property is not supported in this Unity version";
                        return false;
                    }
                    try
                    {
                        SerializedPropertyGradientValueProperty.SetValue(property, value, null);
                    }
                    catch (Exception ex)
                    {
                        error = $"failed to assign Gradient value: {ex.Message}";
                        return false;
                    }
                    return true;
                }
                case SerializedPropertyType.ObjectReference:
                {
                    if (string.Equals(valueKind, "handle", StringComparison.Ordinal))
                    {
                        if (s_currentHandles == null)
                        {
                            error = "handle-based ObjectReference is only supported in create mode";
                            return false;
                        }
                        string handleName = (op.value_string ?? string.Empty).Trim();
                        UnityEngine.Object handleObj;
                        string handleError;
                        if (!TryResolveHandle(handleName, s_currentHandles, out handleObj, out handleError))
                        {
                            error = $"ObjectReference handle resolution failed: {handleError}";
                            return false;
                        }
                        property.objectReferenceValue = handleObj;
                        return true;
                    }
                    UnityEngine.Object referenceValue;
                    if (!TryReadObjectReferenceValue(op, valueKind, out referenceValue, out error))
                    {
                        return false;
                    }
                    property.objectReferenceValue = referenceValue;
                    return true;
                }
                case SerializedPropertyType.ExposedReference:
                {
                    if (string.Equals(valueKind, "handle", StringComparison.Ordinal))
                    {
                        if (s_currentHandles == null)
                        {
                            error = "handle-based ExposedReference is only supported in create mode";
                            return false;
                        }
                        string handleName = (op.value_string ?? string.Empty).Trim();
                        UnityEngine.Object handleObj;
                        string handleError;
                        if (!TryResolveHandle(handleName, s_currentHandles, out handleObj, out handleError))
                        {
                            error = $"ExposedReference handle resolution failed: {handleError}";
                            return false;
                        }
                        property.exposedReferenceValue = handleObj;
                        return true;
                    }
                    UnityEngine.Object referenceValue;
                    if (!TryReadObjectReferenceValue(op, valueKind, out referenceValue, out error))
                    {
                        return false;
                    }
                    property.exposedReferenceValue = referenceValue;
                    return true;
                }
                case SerializedPropertyType.LayerMask:
                case SerializedPropertyType.ArraySize:
                {
                    int intValue;
                    if (!TryReadIntegerValue(op, valueKind, out intValue, out error))
                    {
                        return false;
                    }
                    property.intValue = intValue;
                    return true;
                }
                case SerializedPropertyType.ManagedReference:
                {
                    object managedReferenceValue;
                    if (
                        !TryReadManagedReferenceValue(
                            property,
                            op,
                            valueKind,
                            out managedReferenceValue,
                            out error
                        )
                    )
                    {
                        return false;
                    }
                    property.managedReferenceValue = managedReferenceValue;
                    return true;
                }
                case SerializedPropertyType.Generic:
                {
                    object genericValue;
                    if (!TryReadGenericValue(property, op, valueKind, out genericValue, out error))
                    {
                        return false;
                    }
                    try
                    {
                        property.boxedValue = genericValue;
                    }
                    catch (Exception ex)
                    {
                        error = $"failed to assign generic value: {ex.Message}";
                        return false;
                    }
                    return true;
                }
                default:
                    if (
                        string.Equals(
                            property.propertyType.ToString(),
                            "FixedBufferSize",
                            StringComparison.Ordinal
                        )
                    )
                    {
                        error = "FixedBufferSize is read-only; set individual fixed buffer elements instead";
                        return false;
                    }
                    error = $"SerializedPropertyType '{property.propertyType}' is not supported";
                    return false;
            }
        }

        private static bool TryReadCharacterValue(
            PatchOp op,
            string valueKind,
            out int value,
            out string error
        )
        {
            value = 0;
            error = string.Empty;
            if (string.Equals(valueKind, "int", StringComparison.Ordinal))
            {
                if (op.value_int < char.MinValue || op.value_int > char.MaxValue)
                {
                    error = $"character integer value is out of range: {op.value_int}";
                    return false;
                }
                value = op.value_int;
                return true;
            }
            if (string.Equals(valueKind, "string", StringComparison.Ordinal))
            {
                string raw = op.value_string ?? string.Empty;
                if (raw.Length != 1)
                {
                    error = "character property requires single-character value_string";
                    return false;
                }
                value = raw[0];
                return true;
            }
            error = "character property requires value_kind='int' or 'string'";
            return false;
        }

        private static bool TryReadIntegerValue(
            PatchOp op,
            string valueKind,
            out int value,
            out string error
        )
        {
            value = 0;
            error = string.Empty;
            if (string.Equals(valueKind, "int", StringComparison.Ordinal))
            {
                value = op.value_int;
                return true;
            }
            if (string.Equals(valueKind, "float", StringComparison.Ordinal))
            {
                float rounded = Mathf.Round(op.value_float);
                if (!Mathf.Approximately(rounded, op.value_float))
                {
                    error = "integer property requires a whole-number float value";
                    return false;
                }
                value = (int)rounded;
                return true;
            }
            if (string.Equals(valueKind, "bool", StringComparison.Ordinal))
            {
                value = op.value_bool ? 1 : 0;
                return true;
            }
            if (string.Equals(valueKind, "string", StringComparison.Ordinal))
            {
                if (int.TryParse(op.value_string, NumberStyles.Integer, CultureInfo.InvariantCulture, out value))
                {
                    return true;
                }
                error = $"failed to parse integer from value_string '{op.value_string}'";
                return false;
            }
            error = "integer property requires value_kind='int' (or compatible float/bool/string)";
            return false;
        }

        private static bool TryReadFloatValue(
            PatchOp op,
            string valueKind,
            out float value,
            out string error
        )
        {
            value = 0f;
            error = string.Empty;
            if (string.Equals(valueKind, "float", StringComparison.Ordinal))
            {
                value = op.value_float;
                return true;
            }
            if (string.Equals(valueKind, "int", StringComparison.Ordinal))
            {
                value = op.value_int;
                return true;
            }
            if (string.Equals(valueKind, "bool", StringComparison.Ordinal))
            {
                value = op.value_bool ? 1f : 0f;
                return true;
            }
            if (string.Equals(valueKind, "string", StringComparison.Ordinal))
            {
                if (
                    float.TryParse(
                        op.value_string,
                        NumberStyles.Float | NumberStyles.AllowThousands,
                        CultureInfo.InvariantCulture,
                        out value
                    )
                )
                {
                    return true;
                }
                error = $"failed to parse float from value_string '{op.value_string}'";
                return false;
            }
            error = "float property requires value_kind='float' (or compatible int/bool/string)";
            return false;
        }

        private static bool TryReadBoolValue(
            PatchOp op,
            string valueKind,
            out bool value,
            out string error
        )
        {
            value = false;
            error = string.Empty;
            if (string.Equals(valueKind, "bool", StringComparison.Ordinal))
            {
                value = op.value_bool;
                return true;
            }
            if (string.Equals(valueKind, "int", StringComparison.Ordinal))
            {
                value = op.value_int != 0;
                return true;
            }
            if (string.Equals(valueKind, "string", StringComparison.Ordinal))
            {
                if (bool.TryParse(op.value_string, out value))
                {
                    return true;
                }
                int intValue;
                if (int.TryParse(op.value_string, NumberStyles.Integer, CultureInfo.InvariantCulture, out intValue))
                {
                    value = intValue != 0;
                    return true;
                }
                error = $"failed to parse bool from value_string '{op.value_string}'";
                return false;
            }
            error = "boolean property requires value_kind='bool' (or compatible int/string)";
            return false;
        }

        private static bool TryReadEnumValue(
            SerializedProperty property,
            PatchOp op,
            string valueKind,
            out int enumIndex,
            out string error
        )
        {
            enumIndex = 0;
            error = string.Empty;
            if (string.Equals(valueKind, "int", StringComparison.Ordinal))
            {
                enumIndex = op.value_int;
            }
            else if (string.Equals(valueKind, "string", StringComparison.Ordinal))
            {
                string raw = op.value_string ?? string.Empty;
                for (int i = 0; i < property.enumDisplayNames.Length; i++)
                {
                    if (
                        string.Equals(property.enumDisplayNames[i], raw, StringComparison.OrdinalIgnoreCase)
                        || string.Equals(property.enumNames[i], raw, StringComparison.OrdinalIgnoreCase)
                    )
                    {
                        enumIndex = i;
                        return true;
                    }
                }
                error = $"failed to map enum value from value_string '{raw}'";
                return false;
            }
            else
            {
                error = "enum property requires value_kind='int' or 'string'";
                return false;
            }

            if (enumIndex < 0 || enumIndex >= property.enumDisplayNames.Length)
            {
                error = $"enum index out of range: {enumIndex}";
                return false;
            }
            return true;
        }

        private static bool TryReadColorValue(
            PatchOp op,
            string valueKind,
            out Color value,
            out string error
        )
        {
            value = default(Color);
            error = string.Empty;
            if (string.Equals(valueKind, "string", StringComparison.Ordinal))
            {
                if (ColorUtility.TryParseHtmlString(op.value_string, out value))
                {
                    return true;
                }
                error = $"failed to parse color from value_string '{op.value_string}'";
                return false;
            }
            if (string.Equals(valueKind, "json", StringComparison.Ordinal))
            {
                ColorPayload payload;
                if (!TryParseJsonPayload(op.value_json, out payload, out error))
                {
                    error = $"failed to parse color value_json: {error}";
                    return false;
                }
                value = new Color(payload.r, payload.g, payload.b, payload.a);
                return true;
            }

            error = "color property requires value_kind='string' (#RRGGBB/#RRGGBBAA) or 'json'";
            return false;
        }

        private static bool TryReadVector2Value(
            PatchOp op,
            string valueKind,
            out Vector2 value,
            out string error
        )
        {
            value = default(Vector2);
            error = string.Empty;
            if (!string.Equals(valueKind, "json", StringComparison.Ordinal))
            {
                error = "Vector2 property requires value_kind='json' with {x,y}";
                return false;
            }
            Vector2Payload payload;
            if (!TryParseJsonPayload(op.value_json, out payload, out error))
            {
                error = $"failed to parse Vector2 value_json: {error}";
                return false;
            }
            value = new Vector2(payload.x, payload.y);
            return true;
        }

        private static bool TryReadVector3Value(
            PatchOp op,
            string valueKind,
            out Vector3 value,
            out string error
        )
        {
            value = default(Vector3);
            error = string.Empty;
            if (!string.Equals(valueKind, "json", StringComparison.Ordinal))
            {
                error = "Vector3 property requires value_kind='json' with {x,y,z}";
                return false;
            }
            Vector3Payload payload;
            if (!TryParseJsonPayload(op.value_json, out payload, out error))
            {
                error = $"failed to parse Vector3 value_json: {error}";
                return false;
            }
            value = new Vector3(payload.x, payload.y, payload.z);
            return true;
        }

        private static bool TryReadVector4Value(
            PatchOp op,
            string valueKind,
            out Vector4 value,
            out string error
        )
        {
            value = default(Vector4);
            error = string.Empty;
            if (!string.Equals(valueKind, "json", StringComparison.Ordinal))
            {
                error = "Vector4 property requires value_kind='json' with {x,y,z,w}";
                return false;
            }
            Vector4Payload payload;
            if (!TryParseJsonPayload(op.value_json, out payload, out error))
            {
                error = $"failed to parse Vector4 value_json: {error}";
                return false;
            }
            value = new Vector4(payload.x, payload.y, payload.z, payload.w);
            return true;
        }

        private static bool TryReadVector2IntValue(
            PatchOp op,
            string valueKind,
            out Vector2Int value,
            out string error
        )
        {
            value = default(Vector2Int);
            error = string.Empty;
            if (!string.Equals(valueKind, "json", StringComparison.Ordinal))
            {
                error = "Vector2Int property requires value_kind='json' with {x,y}";
                return false;
            }
            Vector2IntPayload payload;
            if (!TryParseJsonPayload(op.value_json, out payload, out error))
            {
                error = $"failed to parse Vector2Int value_json: {error}";
                return false;
            }
            value = new Vector2Int(payload.x, payload.y);
            return true;
        }

        private static bool TryReadVector3IntValue(
            PatchOp op,
            string valueKind,
            out Vector3Int value,
            out string error
        )
        {
            value = default(Vector3Int);
            error = string.Empty;
            if (!string.Equals(valueKind, "json", StringComparison.Ordinal))
            {
                error = "Vector3Int property requires value_kind='json' with {x,y,z}";
                return false;
            }
            Vector3IntPayload payload;
            if (!TryParseJsonPayload(op.value_json, out payload, out error))
            {
                error = $"failed to parse Vector3Int value_json: {error}";
                return false;
            }
            value = new Vector3Int(payload.x, payload.y, payload.z);
            return true;
        }

        private static bool TryReadRectIntValue(
            PatchOp op,
            string valueKind,
            out RectInt value,
            out string error
        )
        {
            value = default(RectInt);
            error = string.Empty;
            if (!string.Equals(valueKind, "json", StringComparison.Ordinal))
            {
                error = "RectInt property requires value_kind='json' with {x,y,width,height}";
                return false;
            }
            RectIntPayload payload;
            if (!TryParseJsonPayload(op.value_json, out payload, out error))
            {
                error = $"failed to parse RectInt value_json: {error}";
                return false;
            }
            value = new RectInt(payload.x, payload.y, payload.width, payload.height);
            return true;
        }

        private static bool TryReadBoundsIntValue(
            PatchOp op,
            string valueKind,
            out BoundsInt value,
            out string error
        )
        {
            value = default(BoundsInt);
            error = string.Empty;
            if (!string.Equals(valueKind, "json", StringComparison.Ordinal))
            {
                error = "BoundsInt property requires value_kind='json' with {position:{x,y,z},size:{x,y,z}}";
                return false;
            }
            BoundsIntPayload payload;
            if (!TryParseJsonPayload(op.value_json, out payload, out error))
            {
                error = $"failed to parse BoundsInt value_json: {error}";
                return false;
            }
            if (payload.position == null || payload.size == null)
            {
                error = "BoundsInt value_json requires both position and size objects";
                return false;
            }
            value = new BoundsInt(
                new Vector3Int(payload.position.x, payload.position.y, payload.position.z),
                new Vector3Int(payload.size.x, payload.size.y, payload.size.z)
            );
            return true;
        }

        private static bool TryReadRectValue(
            PatchOp op,
            string valueKind,
            out Rect value,
            out string error
        )
        {
            value = default(Rect);
            error = string.Empty;
            if (!string.Equals(valueKind, "json", StringComparison.Ordinal))
            {
                error = "Rect property requires value_kind='json' with {x,y,width,height}";
                return false;
            }
            RectPayload payload;
            if (!TryParseJsonPayload(op.value_json, out payload, out error))
            {
                error = $"failed to parse Rect value_json: {error}";
                return false;
            }
            value = new Rect(payload.x, payload.y, payload.width, payload.height);
            return true;
        }

        private static bool TryReadBoundsValue(
            PatchOp op,
            string valueKind,
            out Bounds value,
            out string error
        )
        {
            value = default(Bounds);
            error = string.Empty;
            if (!string.Equals(valueKind, "json", StringComparison.Ordinal))
            {
                error = "Bounds property requires value_kind='json' with {center:{x,y,z},size:{x,y,z}}";
                return false;
            }
            BoundsPayload payload;
            if (!TryParseJsonPayload(op.value_json, out payload, out error))
            {
                error = $"failed to parse Bounds value_json: {error}";
                return false;
            }
            if (payload.center == null || payload.size == null)
            {
                error = "Bounds value_json requires both center and size objects";
                return false;
            }
            value = new Bounds(
                new Vector3(payload.center.x, payload.center.y, payload.center.z),
                new Vector3(payload.size.x, payload.size.y, payload.size.z)
            );
            return true;
        }

        private static bool TryReadQuaternionValue(
            PatchOp op,
            string valueKind,
            out Quaternion value,
            out string error
        )
        {
            value = default(Quaternion);
            error = string.Empty;
            if (!string.Equals(valueKind, "json", StringComparison.Ordinal))
            {
                error = "Quaternion property requires value_kind='json' with {x,y,z,w}";
                return false;
            }
            QuaternionPayload payload;
            if (!TryParseJsonPayload(op.value_json, out payload, out error))
            {
                error = $"failed to parse Quaternion value_json: {error}";
                return false;
            }
            value = new Quaternion(payload.x, payload.y, payload.z, payload.w);
            return true;
        }

        private static bool TryReadAnimationCurveValue(
            PatchOp op,
            string valueKind,
            out AnimationCurve value,
            out string error
        )
        {
            value = null;
            error = string.Empty;
            if (string.Equals(valueKind, "null", StringComparison.Ordinal))
            {
                return true;
            }
            if (!string.Equals(valueKind, "json", StringComparison.Ordinal))
            {
                error = "AnimationCurve property requires value_kind='null' or 'json'";
                return false;
            }

            AnimationCurvePayload payload;
            if (!TryParseJsonPayload(op.value_json, out payload, out error))
            {
                error = $"failed to parse AnimationCurve value_json: {error}";
                return false;
            }
            if (payload.keys == null)
            {
                error = "AnimationCurve value_json requires keys array";
                return false;
            }
            if (!Enum.IsDefined(typeof(WrapMode), payload.pre_wrap_mode))
            {
                error = $"AnimationCurve pre_wrap_mode is invalid: {payload.pre_wrap_mode}";
                return false;
            }
            if (!Enum.IsDefined(typeof(WrapMode), payload.post_wrap_mode))
            {
                error = $"AnimationCurve post_wrap_mode is invalid: {payload.post_wrap_mode}";
                return false;
            }

            Keyframe[] keys = new Keyframe[payload.keys.Length];
            for (int i = 0; i < payload.keys.Length; i++)
            {
                AnimationCurveKeyPayload keyPayload = payload.keys[i];
                if (keyPayload == null)
                {
                    error = $"AnimationCurve key at index {i} is null";
                    return false;
                }
                keys[i] = new Keyframe(
                    keyPayload.time,
                    keyPayload.value,
                    keyPayload.in_tangent,
                    keyPayload.out_tangent
                );
            }

            value = new AnimationCurve(keys);
            value.preWrapMode = (WrapMode)payload.pre_wrap_mode;
            value.postWrapMode = (WrapMode)payload.post_wrap_mode;
            return true;
        }

        private static bool TryReadGradientValue(
            PatchOp op,
            string valueKind,
            out object value,
            out string error
        )
        {
            value = null;
            error = string.Empty;
            if (string.Equals(valueKind, "null", StringComparison.Ordinal))
            {
                return true;
            }
            if (!string.Equals(valueKind, "json", StringComparison.Ordinal))
            {
                error = "Gradient property requires value_kind='null' or 'json'";
                return false;
            }

            GradientPayload payload;
            if (!TryParseJsonPayload(op.value_json, out payload, out error))
            {
                error = $"failed to parse Gradient value_json: {error}";
                return false;
            }
            if (payload.color_keys == null)
            {
                error = "Gradient value_json requires color_keys array";
                return false;
            }
            if (payload.alpha_keys == null)
            {
                error = "Gradient value_json requires alpha_keys array";
                return false;
            }

            GradientColorKey[] colorKeys = new GradientColorKey[payload.color_keys.Length];
            for (int i = 0; i < payload.color_keys.Length; i++)
            {
                GradientColorKeyPayload colorKeyPayload = payload.color_keys[i];
                if (colorKeyPayload == null || colorKeyPayload.color == null)
                {
                    error = $"Gradient color key at index {i} is null";
                    return false;
                }
                colorKeys[i] = new GradientColorKey(
                    new Color(
                        colorKeyPayload.color.r,
                        colorKeyPayload.color.g,
                        colorKeyPayload.color.b,
                        colorKeyPayload.color.a
                    ),
                    colorKeyPayload.time
                );
            }

            GradientAlphaKey[] alphaKeys = new GradientAlphaKey[payload.alpha_keys.Length];
            for (int i = 0; i < payload.alpha_keys.Length; i++)
            {
                GradientAlphaKeyPayload alphaKeyPayload = payload.alpha_keys[i];
                if (alphaKeyPayload == null)
                {
                    error = $"Gradient alpha key at index {i} is null";
                    return false;
                }
                alphaKeys[i] = new GradientAlphaKey(alphaKeyPayload.alpha, alphaKeyPayload.time);
            }

            Gradient gradient = new Gradient();
            try
            {
                gradient.SetKeys(colorKeys, alphaKeys);
            }
            catch (Exception ex)
            {
                error = $"failed to assign Gradient keys: {ex.Message}";
                return false;
            }

            PropertyInfo modeProperty = typeof(Gradient).GetProperty("mode", BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic);
            if (modeProperty != null && modeProperty.CanWrite)
            {
                try
                {
                    Type modeType = modeProperty.PropertyType;
                    if (modeType.IsEnum && Enum.IsDefined(modeType, payload.mode))
                    {
                        object modeValue = Enum.ToObject(modeType, payload.mode);
                        modeProperty.SetValue(gradient, modeValue, null);
                    }
                }
                catch
                {
                }
            }

            value = gradient;
            return true;
        }

        private const string BuiltinDefaultResourcesPath = "Library/unity default resources";
        private const string BuiltinExtraResourcesPath = "Resources/unity_builtin_extra";

        private static readonly string[] BuiltinAssetPaths = new[]
        {
            BuiltinDefaultResourcesPath,
            BuiltinExtraResourcesPath,
        };

        private readonly struct BuiltinAssetEntry
        {
            public readonly System.Type type;
            public readonly string name;
            public readonly bool isExtra; // true = unity_builtin_extra, false = unity default resources

            public BuiltinAssetEntry(System.Type type, string name, bool isExtra)
            {
                this.type = type;
                this.name = name;
                this.isExtra = isExtra;
            }
        }

        private static readonly BuiltinAssetEntry[] KnownBuiltinAssets = new[]
        {
            // Resources/unity_builtin_extra — Materials
            new BuiltinAssetEntry(typeof(Material), "Default-Material.mat", true),
            new BuiltinAssetEntry(typeof(Material), "Default-Particle.mat", true),
            new BuiltinAssetEntry(typeof(Material), "Default-Line.mat", true),
            new BuiltinAssetEntry(typeof(Material), "Default-Diffuse.mat", true),
            new BuiltinAssetEntry(typeof(Material), "Default-Skybox.mat", true),
            new BuiltinAssetEntry(typeof(Material), "Sprites-Default.mat", true),
            new BuiltinAssetEntry(typeof(Material), "Sprites-Mask.mat", true),
            new BuiltinAssetEntry(typeof(Material), "Default-Terrain-Standard.mat", true),
            new BuiltinAssetEntry(typeof(Material), "Default-Terrain-Diffuse.mat", true),
            new BuiltinAssetEntry(typeof(Material), "Default-Terrain-Specular.mat", true),
            // Resources/unity_builtin_extra — Fonts
            new BuiltinAssetEntry(typeof(Font), "Arial.ttf", true),
            new BuiltinAssetEntry(typeof(Font), "LegacyRuntime.ttf", true),
            // Library/unity default resources — Meshes (safety net)
            new BuiltinAssetEntry(typeof(Mesh), "Sphere.fbx", false),
            new BuiltinAssetEntry(typeof(Mesh), "Cube.fbx", false),
            new BuiltinAssetEntry(typeof(Mesh), "Cylinder.fbx", false),
            new BuiltinAssetEntry(typeof(Mesh), "Capsule.fbx", false),
            new BuiltinAssetEntry(typeof(Mesh), "Plane.fbx", false),
            new BuiltinAssetEntry(typeof(Mesh), "Quad.fbx", false),
        };

        private static bool IsBuiltinAssetPath(string assetPath)
        {
            return string.Equals(assetPath, BuiltinDefaultResourcesPath, StringComparison.Ordinal)
                || string.Equals(assetPath, BuiltinExtraResourcesPath, StringComparison.Ordinal);
        }

        private static bool TryLoadBuiltinAssetByName(
            string guid, long fileID,
            out UnityEngine.Object value)
        {
            value = null;
            for (int i = 0; i < KnownBuiltinAssets.Length; i++)
            {
                BuiltinAssetEntry entry = KnownBuiltinAssets[i];
                UnityEngine.Object candidate;
                try
                {
                    candidate = entry.isExtra
                        ? AssetDatabase.GetBuiltinExtraResource(entry.type, entry.name)
                        : Resources.GetBuiltinResource(entry.type, entry.name);
                }
                catch (System.ArgumentException ex)
                {
                    // Deprecated assets (e.g. Arial.ttf in Unity 2022.3+) throw ArgumentException.
                    // Skip and continue to next entry.
                    Debug.Log($"[PrefabSentinel] BuiltinAssetByName: {entry.name} ({entry.type.Name}) threw ArgumentException: {ex.Message}");
                    continue;
                }
                if (candidate == null)
                {
                    Debug.Log($"[PrefabSentinel] BuiltinAssetByName: {entry.name} ({entry.type.Name}) returned null");
                    continue;
                }
                string cGuid;
                long cId;
                if (!AssetDatabase.TryGetGUIDAndLocalFileIdentifier(candidate, out cGuid, out cId))
                {
                    Debug.Log($"[PrefabSentinel] BuiltinAssetByName: {entry.name} ({entry.type.Name}) loaded '{candidate.name}' but TryGetGUIDAndLocalFileIdentifier returned false");
                    continue;
                }
                Debug.Log($"[PrefabSentinel] BuiltinAssetByName: {entry.name} ({entry.type.Name}) loaded '{candidate.name}' => guid={cGuid}, fileID={cId} (searching guid={guid}, fileID={fileID})");
                if (string.Equals(cGuid, guid, StringComparison.OrdinalIgnoreCase) && cId == fileID)
                {
                    value = candidate;
                    return true;
                }
            }
            return false;
        }

        // assetPath: kept for call-site compatibility; search uses BuiltinAssetPaths instead
        private static bool TryLoadBuiltinAsset(
            string assetPath, string guid, long fileID,
            out UnityEngine.Object value)
        {
            value = null;
            // 1. Try LoadAllAssetsAtPath on each known builtin path
            //    AssetDatabase.GUIDToAssetPath returns only one path, but builtin
            //    assets are split across two locations, so we search both explicitly.
            for (int p = 0; p < BuiltinAssetPaths.Length; p++)
            {
                UnityEngine.Object[] candidates = AssetDatabase.LoadAllAssetsAtPath(BuiltinAssetPaths[p]);
                Debug.Log($"[PrefabSentinel] LoadAllAssetsAtPath(\"{BuiltinAssetPaths[p]}\") returned {candidates.Length} candidates (searching guid={guid}, fileID={fileID})");
                for (int i = 0; i < candidates.Length; i++)
                {
                    if (candidates[i] == null) continue;
                    string cGuid;
                    long cId;
                    if (AssetDatabase.TryGetGUIDAndLocalFileIdentifier(candidates[i], out cGuid, out cId)
                        && string.Equals(cGuid, guid, StringComparison.OrdinalIgnoreCase)
                        && cId == fileID)
                    {
                        value = candidates[i];
                        return true;
                    }
                }
            }
            // 2. Try name-based loading from known builtin assets table
            //    LoadAllAssetsAtPath may return empty for unity_builtin_extra
            //    in Editor Bridge context due to lazy loading.
            if (TryLoadBuiltinAssetByName(guid, fileID, out value))
            {
                Debug.Log($"[PrefabSentinel] Resolved builtin asset via name-based loading: guid={guid}, fileID={fileID}, asset={value.name}");
                return true;
            }
            // 3. Fallback: search all loaded objects
            UnityEngine.Object[] all = Resources.FindObjectsOfTypeAll<UnityEngine.Object>();
            Debug.Log($"[PrefabSentinel] FindObjectsOfTypeAll fallback: {all.Length} objects (searching guid={guid}, fileID={fileID})");
            for (int i = 0; i < all.Length; i++)
            {
                if (all[i] == null) continue;
                string cGuid;
                long cId;
                if (AssetDatabase.TryGetGUIDAndLocalFileIdentifier(all[i], out cGuid, out cId)
                    && string.Equals(cGuid, guid, StringComparison.OrdinalIgnoreCase)
                    && cId == fileID)
                {
                    value = all[i];
                    return true;
                }
            }
            return false;
        }

        private static bool TryReadObjectReferenceValue(
            PatchOp op,
            string valueKind,
            out UnityEngine.Object value,
            out string error
        )
        {
            value = null;
            error = string.Empty;
            if (string.Equals(valueKind, "null", StringComparison.Ordinal))
            {
                return true;
            }
            if (!string.Equals(valueKind, "json", StringComparison.Ordinal))
            {
                error = "ObjectReference requires value_kind='null' or 'json' ({guid,file_id})";
                return false;
            }

            ObjectReferencePayload payload;
            if (!TryParseJsonPayload(op.value_json, out payload, out error))
            {
                error = $"failed to parse ObjectReference value_json: {error}";
                return false;
            }

            string guid = (payload.guid ?? string.Empty).Trim();
            if (string.IsNullOrWhiteSpace(guid))
            {
                error = "ObjectReference value_json requires non-empty guid";
                return false;
            }

            // Accept both "fileID" (Unity native) and "file_id" (snake_case) JSON keys
            long effectiveFileId = payload.fileID != 0 ? payload.fileID : payload.file_id;
            if (effectiveFileId < 0)
            {
                error = "ObjectReference file_id must be >= 0";
                return false;
            }

            string assetPath = AssetDatabase.GUIDToAssetPath(guid);
            if (string.IsNullOrWhiteSpace(assetPath))
            {
                error = $"ObjectReference guid could not be resolved: '{guid}'";
                return false;
            }

            // Builtin resource paths require special loading
            if (IsBuiltinAssetPath(assetPath))
            {
                if (effectiveFileId == 0)
                {
                    error = $"ObjectReference fileID 0 is not valid for builtin path '{assetPath}'";
                    return false;
                }
                if (TryLoadBuiltinAsset(assetPath, guid, effectiveFileId, out value))
                    return true;
                error = $"ObjectReference builtin asset not found: guid='{guid}', fileID={effectiveFileId}";
                return false;
            }

            if (effectiveFileId == 0)
            {
                value = AssetDatabase.LoadMainAssetAtPath(assetPath);
                if (value == null)
                {
                    error = $"ObjectReference main asset not found at '{assetPath}'";
                    return false;
                }
                return true;
            }

            UnityEngine.Object[] candidates = AssetDatabase.LoadAllAssetsAtPath(assetPath);
            for (int i = 0; i < candidates.Length; i++)
            {
                UnityEngine.Object candidate = candidates[i];
                if (candidate == null)
                {
                    continue;
                }

                string candidateGuid;
                long localFileId;
                if (!AssetDatabase.TryGetGUIDAndLocalFileIdentifier(candidate, out candidateGuid, out localFileId))
                {
                    continue;
                }
                if (
                    string.Equals(candidateGuid, guid, StringComparison.OrdinalIgnoreCase)
                    && localFileId == effectiveFileId
                )
                {
                    value = candidate;
                    return true;
                }
            }

            error = $"ObjectReference file_id '{effectiveFileId}' was not found in asset '{assetPath}'";
            return false;
        }

        private static bool TryReadManagedReferenceValue(
            SerializedProperty property,
            PatchOp op,
            string valueKind,
            out object value,
            out string error
        )
        {
            value = null;
            error = string.Empty;
            if (string.Equals(valueKind, "null", StringComparison.Ordinal))
            {
                return true;
            }
            if (!string.Equals(valueKind, "json", StringComparison.Ordinal))
            {
                error = "ManagedReference requires value_kind='null' or 'json'";
                return false;
            }

            Type targetType;
            if (!TryResolveManagedReferenceTargetType(property, op.value_json, out targetType, out error))
            {
                return false;
            }
            if (!TryDecodeJsonToType(op.value_json, targetType, out value, out error))
            {
                error = $"failed to parse ManagedReference value_json: {error}";
                return false;
            }
            return true;
        }

        private static bool TryReadGenericValue(
            SerializedProperty property,
            PatchOp op,
            string valueKind,
            out object value,
            out string error
        )
        {
            value = null;
            error = string.Empty;
            object current;
            try
            {
                current = property.boxedValue;
            }
            catch (Exception ex)
            {
                error = $"failed to read generic boxedValue: {ex.Message}";
                return false;
            }

            if (string.Equals(valueKind, "null", StringComparison.Ordinal))
            {
                if (current != null && current.GetType().IsValueType)
                {
                    error = $"generic value type '{current.GetType().FullName}' cannot be set to null";
                    return false;
                }
                return true;
            }
            if (!string.Equals(valueKind, "json", StringComparison.Ordinal))
            {
                error = "generic property requires value_kind='json' (or 'null' for nullable references)";
                return false;
            }

            if (current == null)
            {
                error =
                    "generic property boxedValue is null; set child properties directly or use ManagedReference with __type";
                return false;
            }
            Type targetType = current.GetType();
            if (!TryDecodeJsonToType(op.value_json, targetType, out value, out error))
            {
                error = $"failed to parse generic value_json for type '{targetType.FullName}': {error}";
                return false;
            }
            return true;
        }

        private static bool TryResolveManagedReferenceTargetType(
            SerializedProperty property,
            string rawJson,
            out Type targetType,
            out string error
        )
        {
            targetType = null;
            error = string.Empty;

            string typeHint;
            if (TryReadManagedReferenceTypeHint(rawJson, out typeHint))
            {
                if (!TryResolveType(typeHint, out targetType, out error))
                {
                    error = $"failed to resolve managed reference __type '{typeHint}': {error}";
                    return false;
                }
                return true;
            }

            object current = property.managedReferenceValue;
            if (current != null)
            {
                targetType = current.GetType();
                return true;
            }

            if (!TryResolveManagedReferenceFieldType(property, out targetType, out error))
            {
                return false;
            }
            if (targetType.IsInterface || targetType.IsAbstract)
            {
                error =
                    $"managed reference field type '{targetType.FullName}' is abstract/interface; provide __type in value_json";
                return false;
            }
            return true;
        }

        private static bool TryResolveManagedReferenceFieldType(
            SerializedProperty property,
            out Type fieldType,
            out string error
        )
        {
            fieldType = null;
            error = string.Empty;
            string raw = property.managedReferenceFieldTypename ?? string.Empty;
            if (string.IsNullOrWhiteSpace(raw))
            {
                error = "managedReferenceFieldTypename is empty";
                return false;
            }
            int separator = raw.IndexOf(" ", StringComparison.Ordinal);
            if (separator <= 0 || separator >= raw.Length - 1)
            {
                error = $"managedReferenceFieldTypename has invalid format: '{raw}'";
                return false;
            }
            string assemblyName = raw.Substring(0, separator).Trim();
            string typeName = raw.Substring(separator + 1).Trim();
            if (!TryResolveType($"{typeName}, {assemblyName}", out fieldType, out error))
            {
                error = $"failed to resolve managed reference field type '{raw}': {error}";
                return false;
            }
            return true;
        }

        private static bool TryReadManagedReferenceTypeHint(string rawJson, out string typeName)
        {
            typeName = string.Empty;
            if (string.IsNullOrWhiteSpace(rawJson))
            {
                return false;
            }
            try
            {
                ManagedReferenceTypeHintPayload payload = JsonUtility.FromJson<ManagedReferenceTypeHintPayload>(rawJson);
                if (payload == null || string.IsNullOrWhiteSpace(payload.__type))
                {
                    return false;
                }
                typeName = payload.__type.Trim();
                return true;
            }
            catch
            {
                return false;
            }
        }

        private static bool TryResolveType(string rawTypeName, out Type type, out string error)
        {
            type = null;
            error = string.Empty;
            string candidate = (rawTypeName ?? string.Empty).Trim();
            if (string.IsNullOrWhiteSpace(candidate))
            {
                error = "type name is empty";
                return false;
            }

            type = Type.GetType(candidate, false);
            if (type != null)
            {
                return true;
            }

            int commaIndex = candidate.IndexOf(",");
            string typeName = commaIndex >= 0 ? candidate.Substring(0, commaIndex).Trim() : candidate;
            string assemblyName = commaIndex >= 0 ? candidate.Substring(commaIndex + 1).Trim() : string.Empty;

            Assembly[] assemblies = AppDomain.CurrentDomain.GetAssemblies();
            for (int i = 0; i < assemblies.Length; i++)
            {
                Assembly assembly = assemblies[i];
                if (!string.IsNullOrWhiteSpace(assemblyName))
                {
                    string shortName = assembly.GetName().Name ?? string.Empty;
                    string fullName = assembly.FullName ?? string.Empty;
                    if (
                        !string.Equals(shortName, assemblyName, StringComparison.Ordinal)
                        && !string.Equals(fullName, assemblyName, StringComparison.Ordinal)
                    )
                    {
                        continue;
                    }
                }

                type = assembly.GetType(typeName, false);
                if (type != null)
                {
                    return true;
                }
            }

            if (string.IsNullOrWhiteSpace(assemblyName))
            {
                error = $"type '{typeName}' was not found";
            }
            else
            {
                error = $"type '{typeName}' was not found in assembly '{assemblyName}'";
            }
            return false;
        }

        private static bool TryDecodeJsonToType(
            string raw,
            Type targetType,
            out object value,
            out string error
        )
        {
            value = null;
            error = string.Empty;
            if (string.IsNullOrWhiteSpace(raw))
            {
                error = "value_json is empty";
                return false;
            }
            if (targetType == null)
            {
                error = "target type is null";
                return false;
            }

            try
            {
                value = JsonUtility.FromJson(raw, targetType);
            }
            catch (Exception ex)
            {
                error = ex.Message;
                return false;
            }

            if (value != null)
            {
                return true;
            }
            if (!targetType.IsValueType)
            {
                error = $"value_json decoded to null for type '{targetType.FullName}'";
                return false;
            }

            try
            {
                value = Activator.CreateInstance(targetType);
                return true;
            }
            catch (Exception ex)
            {
                error = $"failed to create default instance for value type '{targetType.FullName}': {ex.Message}";
                return false;
            }
        }

        private static bool TryParseJsonPayload<T>(
            string raw,
            out T payload,
            out string error
        ) where T : class
        {
            payload = null;
            error = string.Empty;
            if (string.IsNullOrWhiteSpace(raw))
            {
                error = "value_json is empty";
                return false;
            }

            try
            {
                payload = JsonUtility.FromJson<T>(raw);
            }
            catch (Exception ex)
            {
                error = ex.Message;
                return false;
            }

            if (payload == null)
            {
                error = "value_json decoded to null";
                return false;
            }
            return true;
        }

        private static string BuildTypeNameSample(HashSet<string> availableTypeNames, int maxItems)
        {
            if (availableTypeNames == null || availableTypeNames.Count == 0)
            {
                return string.Empty;
            }

            List<string> values = new List<string>(availableTypeNames);
            values.Sort(StringComparer.Ordinal);
            int take = Math.Min(maxItems, values.Count);
            List<string> sample = new List<string>();
            for (int i = 0; i < take; i++)
            {
                sample.Add(values[i]);
            }
            if (values.Count > take)
            {
                sample.Add("...");
            }
            return string.Join(", ", sample.ToArray());
        }

        private static string BuildComponentSample(List<Component> matches, int maxItems)
        {
            if (matches == null || matches.Count == 0)
            {
                return string.Empty;
            }

            int take = Math.Min(maxItems, matches.Count);
            List<string> sample = new List<string>();
            for (int i = 0; i < take; i++)
            {
                sample.Add(DescribeComponent(matches[i]));
            }
            if (matches.Count > take)
            {
                sample.Add("...");
            }
            return string.Join("; ", sample.ToArray());
        }

        private static string DescribeComponent(Component component)
        {
            if (component == null)
            {
                return "(missing component)";
            }
            Type type = component.GetType();
            string typeName = type.FullName ?? type.Name;
            return $"{typeName} @ {BuildHierarchyPath(component.transform)}";
        }

        private static string BuildHierarchyPath(Transform transform)
        {
            if (transform == null)
            {
                return "(unknown)";
            }

            List<string> parts = new List<string>();
            Transform current = transform;
            while (current != null)
            {
                parts.Add(current.name);
                current = current.parent;
            }
            parts.Reverse();
            return string.Join("/", parts.ToArray());
        }

        private static string BuildSetPathHint(string propertyPath)
        {
            if (string.IsNullOrWhiteSpace(propertyPath))
            {
                return string.Empty;
            }
            if (propertyPath.EndsWith(ArrayDataSuffix, StringComparison.Ordinal))
            {
                return "set path cannot end with '.Array.data'; use '.Array.size' or '.Array.data[index].field'";
            }

            int index = propertyPath.IndexOf(".Array.data", StringComparison.Ordinal);
            if (index < 0)
            {
                return string.Empty;
            }

            string suffix = propertyPath.Substring(index + ".Array.data".Length);
            if (suffix.Length == 0)
            {
                return "array element path should include an index like '.Array.data[0]'";
            }
            if (!suffix.StartsWith("[", StringComparison.Ordinal))
            {
                return "array element path should include an index like '.Array.data[0]'";
            }
            if (suffix.IndexOf(']') < 0)
            {
                return "array element index is missing closing ']'";
            }
            return string.Empty;
        }

        private static BridgeResponse BuildError(
            string code,
            string message,
            string target,
            int opCount,
            bool executed,
            int applied = 0,
            BridgeDiagnostic[] diagnostics = null
        )
        {
            return new BridgeResponse
            {
                protocol_version = ProtocolVersion,
                success = false,
                severity = "error",
                code = code,
                message = message,
                data = new BridgeData
                {
                    target = target ?? string.Empty,
                    op_count = opCount,
                    applied = applied,
                    read_only = false,
                    executed = executed,
                    protocol_version = ProtocolVersion
                },
                diagnostics = diagnostics ?? Array.Empty<BridgeDiagnostic>()
            };
        }

        private static void WriteResponseSafe(string responsePath, BridgeResponse response)
        {
            try
            {
                if (!string.IsNullOrWhiteSpace(responsePath))
                {
                    string dir = Path.GetDirectoryName(responsePath);
                    if (!string.IsNullOrWhiteSpace(dir))
                    {
                        Directory.CreateDirectory(dir);
                    }
                    string json = JsonUtility.ToJson(response);
                    string tmpPath = responsePath + ".tmp";
                    File.WriteAllText(tmpPath, json);
                    if (File.Exists(responsePath)) File.Delete(responsePath);
                    File.Move(tmpPath, responsePath);
                    return;
                }
            }
            catch (Exception ex)
            {
                Debug.LogError($"[PrefabSentinel] Failed to write bridge response: {ex}");
                // Fallback: direct write if atomic rename failed.
                try { File.WriteAllText(responsePath, JsonUtility.ToJson(response)); }
                catch { /* best effort */ }
                return;
            }

            Debug.LogError("[PrefabSentinel] Response path is empty; bridge response was not written.");
        }
    }
}
