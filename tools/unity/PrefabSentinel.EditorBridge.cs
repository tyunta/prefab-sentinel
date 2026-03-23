using System;
using System.IO;
using UnityEditor;
using UnityEngine;

namespace PrefabSentinel
{
    /// <summary>
    /// EditorWindow that polls a watch directory for .request.json files
    /// and dispatches them to UnityPatchBridge or UnityRuntimeValidationBridge.
    /// This allows patch operations while the Unity Editor is running,
    /// avoiding the batchmode exclusion lock.
    /// </summary>
    public sealed class EditorBridgeWindow : EditorWindow
    {
        private const string WatchDirPrefKey = "PrefabSentinel_EditorBridge_WatchDir";
        private const string EnabledPrefKey = "PrefabSentinel_EditorBridge_Enabled";
        private const double PollIntervalSeconds = 0.5;
        private const string RequestSuffix = ".request.json";
        private const string ResponseSuffix = ".response.json";
        private const string TmpSuffix = ".tmp";

        [Serializable]
        private sealed class BridgeRequestHeader
        {
            public string action = string.Empty;
        }

        private string _watchDir = string.Empty;
        private bool _enabled;
        private double _lastPollTime;
        private int _processedCount;

        [MenuItem("PrefabSentinel/Editor Bridge")]
        private static void ShowWindow()
        {
            var window = GetWindow<EditorBridgeWindow>("Sentinel Bridge");
            window.minSize = new Vector2(340, 160);
            window.Show();
        }

        private void OnEnable()
        {
            _watchDir = EditorPrefs.GetString(WatchDirPrefKey, string.Empty);
            _enabled = EditorPrefs.GetBool(EnabledPrefKey, false);
            _lastPollTime = EditorApplication.timeSinceStartup;
            _processedCount = 0;
            EditorApplication.update -= OnEditorUpdate;
            EditorApplication.update += OnEditorUpdate;
            UnityEditorControlBridge.ConsoleLogBuffer.StartCapture();
        }

        private void OnDisable()
        {
            EditorApplication.update -= OnEditorUpdate;
            UnityEditorControlBridge.ConsoleLogBuffer.StopCapture();
        }

        private void OnGUI()
        {
            GUILayout.Label("Prefab Sentinel — Editor Bridge", EditorStyles.boldLabel);
            GUILayout.Space(4);

            EditorGUI.BeginChangeCheck();
            _watchDir = EditorGUILayout.TextField("Watch Directory", _watchDir);
            if (EditorGUI.EndChangeCheck())
            {
                EditorPrefs.SetString(WatchDirPrefKey, _watchDir);
            }

            EditorGUI.BeginChangeCheck();
            _enabled = EditorGUILayout.Toggle("Enabled", _enabled);
            if (EditorGUI.EndChangeCheck())
            {
                EditorPrefs.SetBool(EnabledPrefKey, _enabled);
            }

            GUILayout.Space(4);
            EditorGUILayout.LabelField("Processed", _processedCount.ToString());

            if (!_enabled)
            {
                EditorGUILayout.HelpBox("Bridge is disabled. Toggle 'Enabled' to start watching.", MessageType.Info);
            }
            else if (string.IsNullOrEmpty(_watchDir))
            {
                EditorGUILayout.HelpBox("Set a watch directory to enable file-based bridging.", MessageType.Warning);
            }
            else if (!Directory.Exists(_watchDir))
            {
                EditorGUILayout.HelpBox($"Watch directory does not exist:\n{_watchDir}", MessageType.Error);
            }
        }

        private void OnEditorUpdate()
        {
            if (!_enabled) return;
            if (string.IsNullOrEmpty(_watchDir)) return;

            double now = EditorApplication.timeSinceStartup;
            if (now - _lastPollTime < PollIntervalSeconds) return;
            _lastPollTime = now;

            if (!Directory.Exists(_watchDir)) return;

            string[] candidates;
            try
            {
                candidates = Directory.GetFiles(_watchDir, "*" + RequestSuffix);
            }
            catch (Exception ex)
            {
                Debug.LogWarning($"[PrefabSentinel.EditorBridge] Failed to list watch dir: {ex.Message}");
                return;
            }

            foreach (string requestPath in candidates)
            {
                // Skip files still being written (tmp extension present).
                if (File.Exists(requestPath + TmpSuffix)) continue;

                string baseName = requestPath.Substring(0, requestPath.Length - RequestSuffix.Length);
                string responsePath = baseName + ResponseSuffix;

                // Skip if response already exists (already processed or in progress).
                if (File.Exists(responsePath)) continue;

                try
                {
                    ProcessRequest(requestPath, responsePath);
                    _processedCount++;
                }
                catch (Exception ex)
                {
                    Debug.LogError($"[PrefabSentinel.EditorBridge] Error processing {Path.GetFileName(requestPath)}: {ex}");
                    WriteErrorResponse(responsePath, "EDITOR_BRIDGE_ERROR", ex.ToString());
                }
                finally
                {
                    // Clean up request file after processing.
                    TryDelete(requestPath);
                }

                Repaint();
            }
        }

        private static void ProcessRequest(string requestPath, string responsePath)
        {
            string requestJson = File.ReadAllText(requestPath);

            // Peek at the action field to decide which bridge to invoke.
            BridgeRequestHeader header;
            try
            {
                header = JsonUtility.FromJson<BridgeRequestHeader>(requestJson);
            }
            catch
            {
                header = new BridgeRequestHeader();
            }

            bool isRuntime = !string.IsNullOrEmpty(header.action)
                && UnityRuntimeValidationBridge.SupportedActions.Contains(header.action);

            bool isEditorControl = !string.IsNullOrEmpty(header.action)
                && UnityEditorControlBridge.SupportedActions.Contains(header.action);

            if (isRuntime)
            {
                UnityRuntimeValidationBridge.RunFromPaths(requestPath, responsePath);
            }
            else if (isEditorControl)
            {
                UnityEditorControlBridge.RunFromPaths(requestPath, responsePath);
            }
            else
            {
                UnityPatchBridge.ApplyFromPaths(requestPath, responsePath);
            }

            // Atomic write: the bridge methods write directly to responsePath.
            // If the response file doesn't exist at this point, something went wrong.
            if (!File.Exists(responsePath))
            {
                int pv = isEditorControl ? UnityEditorControlBridge.ProtocolVersion
                       : isRuntime ? UnityRuntimeValidationBridge.ProtocolVersion
                       : UnityPatchBridge.ProtocolVersion;
                WriteErrorResponse(responsePath, "EDITOR_BRIDGE_NO_RESPONSE",
                    "Bridge method completed but did not write a response file.", pv);
            }
        }

        private static void WriteErrorResponse(string responsePath, string code, string detail, int protocolVersion = 2)
        {
            string json = JsonUtility.ToJson(new ErrorResponse
            {
                protocol_version = protocolVersion,
                success = false,
                severity = "error",
                code = code,
                message = detail
            }, true);
            WriteAtomic(responsePath, json);
        }

        private static void WriteAtomic(string path, string content)
        {
            try
            {
                string tmpPath = path + TmpSuffix;
                File.WriteAllText(tmpPath, content);
                if (File.Exists(path)) File.Delete(path);
                File.Move(tmpPath, path);
            }
            catch
            {
                try { File.WriteAllText(path, content); }
                catch { /* best effort */ }
            }
        }

        private static void TryDelete(string path)
        {
            try { if (File.Exists(path)) File.Delete(path); }
            catch { /* best-effort cleanup */ }
        }

        [Serializable]
        private sealed class ErrorResponseData
        {
            public bool read_only = true;
            public bool executed = false;
        }

        [Serializable]
        private sealed class ErrorResponse
        {
            public int protocol_version = 2;
            public bool success = false;
            public string severity = "error";
            public string code = string.Empty;
            public string message = string.Empty;
            public ErrorResponseData data = new ErrorResponseData();
            public string[] diagnostics = Array.Empty<string>();
        }
    }
}
