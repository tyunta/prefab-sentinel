using System;
using System.Collections.Generic;
using System.IO;
using UnityEditor;
using UnityEngine;

namespace PrefabSentinel
{
    /// <summary>
    /// Unity executeMethod endpoint for UNITYTOOL_UNITY_EXECUTE_METHOD.
    /// Applies a scoped subset of patch operations to prefab assets via SerializedObject.
    /// </summary>
    public static class UnityPatchBridge
    {
        private const int ProtocolVersion = 1;
        private const string RequestArg = "-unitytoolPatchRequest";
        private const string ResponseArg = "-unitytoolPatchResponse";

        [Serializable]
        private sealed class BridgeRequest
        {
            public int protocol_version = 0;
            public string target = string.Empty;
            public PatchOp[] ops = Array.Empty<PatchOp>();
        }

        [Serializable]
        private sealed class PatchOp
        {
            public string op = string.Empty;
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

            string assetPath;
            string resolveError;
            if (!TryResolveAssetPath(request.target, out assetPath, out resolveError))
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

            if (!string.Equals(Path.GetExtension(assetPath), ".prefab", StringComparison.OrdinalIgnoreCase))
            {
                WriteResponseSafe(
                    responsePath,
                    BuildError(
                        "UNITY_BRIDGE_TARGET_UNSUPPORTED",
                        "executeMethod apply currently supports .prefab only.",
                        request.target,
                        request.ops.Length,
                        executed: false
                    )
                );
                return;
            }

            WriteResponseSafe(responsePath, ApplyPrefabSetOperations(request, assetPath));
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

        private static BridgeResponse ApplyPrefabSetOperations(BridgeRequest request, string assetPath)
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
                    if (!TryApplySetOp(prefabRoot, request.target, op, i, diagnostics))
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

        private static bool TryResolveAssetPath(
            string target,
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
            if (!File.Exists(Path.Combine(projectRoot, assetPath)))
            {
                error = "target file was not found.";
                return false;
            }
            return true;
        }

        private static bool TryApplySetOp(
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
            if (!string.Equals(op.op, "set", StringComparison.Ordinal))
            {
                diagnostics.Add(
                    new BridgeDiagnostic
                    {
                        path = target,
                        location = $"ops[{opIndex}].op",
                        detail = "schema_error",
                        evidence = "executeMethod currently supports only op='set'"
                    }
                );
                return false;
            }
            if (string.IsNullOrWhiteSpace(op.component) || string.IsNullOrWhiteSpace(op.path))
            {
                diagnostics.Add(
                    new BridgeDiagnostic
                    {
                        path = target,
                        location = $"ops[{opIndex}]",
                        detail = "schema_error",
                        evidence = "component and path are required"
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

            SerializedObject serialized = new SerializedObject(component);
            SerializedProperty property = serialized.FindProperty(op.path);
            if (property == null)
            {
                diagnostics.Add(
                    new BridgeDiagnostic
                    {
                        path = target,
                        location = $"ops[{opIndex}].path",
                        detail = "apply_error",
                        evidence = $"property not found: '{op.path}'"
                    }
                );
                return false;
            }

            string valueError;
            if (!TryAssignPropertyValue(property, op, out valueError))
            {
                diagnostics.Add(
                    new BridgeDiagnostic
                    {
                        path = target,
                        location = $"ops[{opIndex}]",
                        detail = "apply_error",
                        evidence = valueError
                    }
                );
                return false;
            }

            serialized.ApplyModifiedPropertiesWithoutUndo();
            return true;
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
            Component[] components = root.GetComponentsInChildren<Component>(true);
            List<Component> matches = new List<Component>();
            for (int i = 0; i < components.Length; i++)
            {
                Component candidate = components[i];
                if (candidate == null)
                {
                    continue;
                }
                Type type = candidate.GetType();
                if (
                    string.Equals(type.FullName, selector, StringComparison.Ordinal)
                    || string.Equals(type.Name, selector, StringComparison.Ordinal)
                    || string.Equals(type.AssemblyQualifiedName, selector, StringComparison.Ordinal)
                )
                {
                    matches.Add(candidate);
                }
            }

            if (matches.Count == 1)
            {
                component = matches[0];
                return true;
            }
            if (matches.Count == 0)
            {
                error = $"component not found: '{selector}'";
                return false;
            }

            error = $"component selector is ambiguous: '{selector}' matched {matches.Count} components";
            return false;
        }

        private static bool TryAssignPropertyValue(
            SerializedProperty property,
            PatchOp op,
            out string error
        )
        {
            error = string.Empty;
            switch (property.propertyType)
            {
                case SerializedPropertyType.Integer:
                    if (string.Equals(op.value_kind, "int", StringComparison.Ordinal))
                    {
                        property.intValue = op.value_int;
                        return true;
                    }
                    error = "integer property requires value_kind='int'";
                    return false;
                case SerializedPropertyType.Float:
                    if (string.Equals(op.value_kind, "float", StringComparison.Ordinal))
                    {
                        property.floatValue = op.value_float;
                        return true;
                    }
                    if (string.Equals(op.value_kind, "int", StringComparison.Ordinal))
                    {
                        property.floatValue = op.value_int;
                        return true;
                    }
                    error = "float property requires value_kind='float' or 'int'";
                    return false;
                case SerializedPropertyType.Boolean:
                    if (string.Equals(op.value_kind, "bool", StringComparison.Ordinal))
                    {
                        property.boolValue = op.value_bool;
                        return true;
                    }
                    error = "boolean property requires value_kind='bool'";
                    return false;
                case SerializedPropertyType.String:
                    if (string.Equals(op.value_kind, "string", StringComparison.Ordinal))
                    {
                        property.stringValue = op.value_string ?? string.Empty;
                        return true;
                    }
                    if (string.Equals(op.value_kind, "null", StringComparison.Ordinal))
                    {
                        property.stringValue = string.Empty;
                        return true;
                    }
                    error = "string property requires value_kind='string' or 'null'";
                    return false;
                case SerializedPropertyType.ObjectReference:
                    if (string.Equals(op.value_kind, "null", StringComparison.Ordinal))
                    {
                        property.objectReferenceValue = null;
                        return true;
                    }
                    error = "ObjectReference currently supports value_kind='null' only";
                    return false;
                default:
                    error = $"SerializedPropertyType '{property.propertyType}' is not supported";
                    return false;
            }
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
                    File.WriteAllText(responsePath, JsonUtility.ToJson(response));
                    return;
                }
            }
            catch (Exception ex)
            {
                Debug.LogError($"[PrefabSentinel] Failed to write bridge response: {ex}");
                return;
            }

            Debug.LogError("[PrefabSentinel] Response path is empty; bridge response was not written.");
        }
    }
}
