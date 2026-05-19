using System;
using System.Collections.Generic;
using System.IO;
using System.Reflection;
using UnityEditor;
using UnityEngine;
namespace PrefabSentinel
{
    /// <summary>
    /// Unity executeMethod endpoint for UNITYTOOL_UNITY_EXECUTE_METHOD.
    /// Applies a scoped subset of patch operations to prefab assets via SerializedObject.
    /// </summary>
    public static partial class UnityPatchBridge
    {
        public const int ProtocolVersion = 2;
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
            // Issue #37: exact-fileID target for set ops; enables unique
            // addressing of same-type siblings on one GameObject.
            public string file_id = string.Empty;
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
    }
}
