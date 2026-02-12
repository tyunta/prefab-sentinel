using System;
using System.IO;
using UnityEditor;
using UnityEngine;

namespace PrefabSentinel
{
    /// <summary>
    /// Reference executeMethod endpoint for UNITYTOOL_UNITY_EXECUTE_METHOD.
    /// This scaffold validates request/response wiring only; SerializedObject apply is not implemented yet.
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

            // Phase 1.5 scaffold:
            // wiring is validated (args + JSON IO + protocol), but SerializedObject edit is not implemented.
            BridgeResponse response = new BridgeResponse
            {
                protocol_version = ProtocolVersion,
                success = false,
                severity = "warning",
                code = "UNITY_BRIDGE_NOT_IMPLEMENTED",
                message = "Unity executeMethod scaffold loaded, but SerializedObject apply is not implemented.",
                data = new BridgeData
                {
                    target = request.target,
                    op_count = request.ops?.Length ?? 0,
                    applied = 0,
                    read_only = false,
                    executed = false,
                    protocol_version = ProtocolVersion
                },
                diagnostics = Array.Empty<BridgeDiagnostic>()
            };

            WriteResponseSafe(responsePath, response);
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

        private static BridgeResponse BuildError(
            string code,
            string message,
            string target,
            int opCount,
            bool executed
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
                    applied = 0,
                    read_only = false,
                    executed = executed,
                    protocol_version = ProtocolVersion
                },
                diagnostics = Array.Empty<BridgeDiagnostic>()
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
