using System;
using System.IO;
using UnityEditor;
using UnityEngine;

namespace PrefabSentinel
{
    /// <summary>
    /// EditorWindow that polls a watch directory for .request.json files
    /// and dispatches them via file IPC to <see cref="UnityPatchBridge"/>
    /// and <see cref="UnityRuntimeValidationBridge"/>.  The resident bridge
    /// keeps the Unity Editor process alive across requests so the
    /// surrounding asset cache and Inspector state survive between calls.
    /// </summary>
    public sealed class EditorBridgeWindow : EditorWindow
    {
        private const string WatchDirPrefKeyPrefix =
            "PrefabSentinel_EditorBridge_WatchDir";
        private const string EnabledPrefKeyPrefix =
            "PrefabSentinel_EditorBridge_Enabled";

        private static string WatchDirPrefKey =>
            global::PrefabSentinel.EditorBridge.EditorBridgeSetup.PreferenceKey(
                WatchDirPrefKeyPrefix, CurrentProjectRoot());

        private static string EnabledPrefKey =>
            global::PrefabSentinel.EditorBridge.EditorBridgeSetup.PreferenceKey(
                EnabledPrefKeyPrefix, CurrentProjectRoot());
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

        private double _lastWatchIdentityPublishTime = double.NegativeInfinity;
        private string _activeWatchIdentity = string.Empty;
        private string _watchIdentityProjectRoot = string.Empty;

        [MenuItem("PrefabSentinel/Editor Bridge")]
        private static void ShowWindow()
        {
            var window = GetWindow<EditorBridgeWindow>("Sentinel Bridge");
            window.minSize = new Vector2(340, 160);
            window.Show();
        }

        private void OnEnable()
        {
            string projectRoot = CurrentProjectRoot();
            var setup = global::PrefabSentinel.EditorBridge.EditorBridgeSetup.Resolve(
                projectRoot,
                EditorPrefs.HasKey(WatchDirPrefKey)
                    ? EditorPrefs.GetString(WatchDirPrefKey)
                    : null,
                EditorPrefs.HasKey(EnabledPrefKey)
                    ? (bool?)EditorPrefs.GetBool(EnabledPrefKey)
                    : null);
            _watchDir = setup.WatchDirectory;
            _enabled = setup.Enabled;
            if (setup.CreateWatchDirectory)
            {
                try
                {
                    Directory.CreateDirectory(_watchDir);
                    EditorPrefs.SetString(WatchDirPrefKey, _watchDir);
                    EditorPrefs.SetBool(EnabledPrefKey, _enabled);
                }
                catch (Exception exception)
                {
                    _enabled = false;
                    Debug.LogError(
                        "[PrefabSentinel.EditorBridge] Failed to create "
                        + "the project-local watch directory: "
                        + exception.Message);
                }
            }

            _lastPollTime = EditorApplication.timeSinceStartup;
            _processedCount = 0;
            EditorApplication.update -= OnEditorUpdate;
            EditorApplication.update += OnEditorUpdate;
            UnityEditorControlBridge.ConsoleLogBuffer.StartCapture();
        }

        private void OnDisable()
        {
            if (!string.IsNullOrEmpty(_watchIdentityProjectRoot))
            {
                EditorBridgeWatchIdentity.TryDeleteOwnedStatus(
                    _watchIdentityProjectRoot,
                    UnityEditorControlBridge.CurrentBridgeInstanceId,
                    exception => Debug.LogWarning(
                        $"[PrefabSentinel.EditorBridge] Watch identity cleanup failed: {exception}"));
            }

            EditorApplication.update -= OnEditorUpdate;
            UnityEditorControlBridge.ConsoleLogBuffer.StopCapture();
        }

        private void OnGUI()
        {
            GUILayout.Label("Prefab Sentinel — Editor Bridge", EditorStyles.boldLabel);
            GUILayout.Label(
                $"Bridge version {UnityEditorControlBridge.BridgeVersion}",
                EditorStyles.miniLabel);
            GUILayout.Space(4);

            EditorGUILayout.LabelField("Unity Project", EditorStyles.miniLabel);
            EditorGUILayout.SelectableLabel(
                CurrentProjectRoot(),
                EditorStyles.textField,
                GUILayout.Height(EditorGUIUtility.singleLineHeight));

            EditorGUILayout.LabelField(
                "Bridge Instance ID",
                EditorStyles.miniLabel);
            EditorGUILayout.SelectableLabel(
                UnityEditorControlBridge.CurrentBridgeInstanceId,
                EditorStyles.textField,
                GUILayout.Height(EditorGUIUtility.singleLineHeight));
            if (GUILayout.Button("Copy Instance ID"))
            {
                EditorGUIUtility.systemCopyBuffer =
                    UnityEditorControlBridge.CurrentBridgeInstanceId;
            }

            GUILayout.Space(4);
            EditorGUI.BeginChangeCheck();
            _watchDir = EditorGUILayout.TextField("Watch Directory", _watchDir);
            if (EditorGUI.EndChangeCheck())
            {
                EditorPrefs.SetString(WatchDirPrefKey, _watchDir);
                _activeWatchIdentity = string.Empty;
                _lastWatchIdentityPublishTime = double.NegativeInfinity;
            }

            EditorGUI.BeginChangeCheck();
            _enabled = EditorGUILayout.Toggle("Enabled", _enabled);
            if (EditorGUI.EndChangeCheck())
            {
                EditorPrefs.SetBool(EnabledPrefKey, _enabled);
            }

            GUILayout.Space(4);
            EditorGUILayout.LabelField(
                "Processed",
                _processedCount.ToString());

            if (!_enabled)
            {
                EditorGUILayout.HelpBox(
                    "Bridge is disabled. Toggle 'Enabled' to start watching.",
                    MessageType.Info);
            }
            else if (string.IsNullOrEmpty(_watchDir))
            {
                EditorGUILayout.HelpBox(
                    "Set a watch directory to enable file-based bridging.",
                    MessageType.Warning);
            }
            else if (!Directory.Exists(_watchDir))
            {
                EditorGUILayout.HelpBox(
                    $"Watch directory does not exist:\n{_watchDir}",
                    MessageType.Error);
            }

            using (new EditorGUI.DisabledScope(
                !_enabled || !Directory.Exists(_watchDir)))
            {
                if (GUILayout.Button("Copy Connection Info"))
                {
                    EditorGUIUtility.systemCopyBuffer =
                        global::PrefabSentinel.EditorBridge.EditorBridgeSetup.ConnectionInfo(
                            CurrentProjectRoot(),
                            _watchDir,
                            UnityEditorControlBridge.CurrentBridgeInstanceId);
                }
                if (GUILayout.Button("Copy Codex Command (WSL / Bash)"))
                    CopyLaunchCommand(true);
                if (GUILayout.Button("Copy Codex Command (PowerShell)"))
                    CopyLaunchCommand(false);
            }
        }


        private static string CurrentProjectRoot()
        {
            return Directory.GetParent(Application.dataPath).FullName;
        }

        private void CopyLaunchCommand(bool bash)
        {
            try
            {
                EditorGUIUtility.systemCopyBuffer = bash
                    ? global::PrefabSentinel.EditorBridge.EditorBridgeSetup.BashLaunchCommand(
                        CurrentProjectRoot(),
                        _watchDir,
                        UnityEditorControlBridge.CurrentBridgeInstanceId)
                    : global::PrefabSentinel.EditorBridge.EditorBridgeSetup.PowerShellLaunchCommand(
                        CurrentProjectRoot(),
                        _watchDir,
                        UnityEditorControlBridge.CurrentBridgeInstanceId);
                ShowNotification(new GUIContent("Launch command copied"));
            }
            catch (ArgumentException exception)
            {
                ShowNotification(new GUIContent(exception.Message));
            }
        }

        private void OnEditorUpdate()
        {
            if (_enabled && Directory.Exists(_watchDir))
            {
                PublishWatchIdentityStatus(EditorApplication.timeSinceStartup);
            }

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
                string publicationFailurePath =
                    baseName + EditorBridgeResponsePublisher.FailureSuffix;

                // Skip if response already exists (already processed or in progress).
                if (File.Exists(responsePath)) continue;

                bool deleteRequest = true;
                try
                {
                    ProcessRequest(requestPath, responsePath);
                    _processedCount++;
                }
                catch (Exception exception)
                {
                    try
                    {
                        EditorBridgeTerminalFailureBoundary.ReportFailure(
                            exception,
                            reportedException => Debug.LogError(
                                $"[PrefabSentinel.EditorBridge] Error processing {Path.GetFileName(requestPath)}: {reportedException}"),
                            message => WriteErrorResponse(
                                responsePath, "EDITOR_BRIDGE_ERROR", message));
                    }
                    catch (EditorBridgeResponsePublicationException publicationException)
                    {
                        Debug.LogError(
                            $"[PrefabSentinel.EditorBridge] Response publication failed for {Path.GetFileName(requestPath)}: {publicationException}");
                        deleteRequest = EditorBridgeResponsePublisher.TryPreserveRequest(
                            requestPath,
                            publicationFailurePath,
                            File.Move,
                            markerException => Debug.LogError(
                                $"[PrefabSentinel.EditorBridge] Failed to preserve publication marker for {Path.GetFileName(requestPath)}: {markerException}"));
                    }
                }
                finally
                {
                    if (deleteRequest)
                        TryDelete(requestPath);
                }

                Repaint();
            }
        }

        private void PublishWatchIdentityStatus(double now)
        {
            double heartbeatIntervalSeconds =
                EditorBridgeWatchIdentity.HeartbeatIntervalMilliseconds / 1000.0;
            if (now - _lastWatchIdentityPublishTime < heartbeatIntervalSeconds)
                return;

            string projectRoot = Directory.GetParent(Application.dataPath).FullName;
            if (string.IsNullOrEmpty(_activeWatchIdentity))
            {
                bool markerReady = EditorBridgeWatchIdentity.TryEnsureMarker(
                    _watchDir,
                    () => Guid.NewGuid().ToString("N"),
                    exception => Debug.LogWarning(
                        $"[PrefabSentinel.EditorBridge] Watch identity marker failed: {exception}"),
                    out _activeWatchIdentity);
                if (!markerReady) return;
            }

            bool published = EditorBridgeWatchIdentity.TryPublishStatus(
                projectRoot,
                _activeWatchIdentity,
                UnityEditorControlBridge.CurrentBridgeSessionId,
                UnityEditorControlBridge.CurrentBridgeInstanceId,
                DateTimeOffset.UtcNow.ToUnixTimeMilliseconds(),
                exception => Debug.LogWarning(
                    $"[PrefabSentinel.EditorBridge] Watch identity status failed: {exception}"));
            if (!published) return;

            _watchIdentityProjectRoot = projectRoot;
            _lastWatchIdentityPublishTime = now;
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
            catch (Exception ex)
            {
                Debug.LogWarning($"[PrefabSentinel] ProcessRequest: {ex.GetType().Name}: {ex.Message}");
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
            else if (string.IsNullOrEmpty(header.action))
            {
                WriteErrorResponse(
                    responsePath,
                    "EDITOR_BRIDGE_UNKNOWN_ACTION",
                    "Empty action field in request.");
            }
            else
            {
                // PatchBridge handles all remaining known actions (no SupportedActions set).
                // If the action is truly unknown, PatchBridge will report its own error.
                UnityPatchBridge.ApplyFromPaths(requestPath, responsePath);
            }

            // Async actions write the response file after their editor lifecycle completes.
            bool isAsyncAction = (isEditorControl
                && UnityEditorControlBridge.AsyncActions.Contains(header.action))
                || (isRuntime
                && UnityRuntimeValidationBridge.AsyncActions.Contains(header.action));

            // Atomic write: the bridge methods write directly to responsePath.
            // If the response file doesn't exist at this point, something went wrong.
            if (!isAsyncAction && !File.Exists(responsePath))
            {
                WriteErrorResponse(
                    responsePath,
                    "EDITOR_BRIDGE_NO_RESPONSE",
                    "Bridge method completed but did not write a response file.");
            }
        }

        private static void WriteErrorResponse(
            string responsePath,
            string code,
            string detail)
        {
            string json = JsonUtility.ToJson(new ErrorResponse
            {
                protocol_version = UnityEditorControlBridge.ProtocolVersion,
                success = false,
                severity = "error",
                code = code,
                message = detail
            }, true);
            WriteAtomic(responsePath, json);
        }

        private static void WriteAtomic(string path, string content)
        {
            EditorBridgeResponsePublisher.Publish(
                path,
                content,
                File.WriteAllText,
                File.Delete,
                File.Move,
                exception => Debug.LogWarning(
                    $"[PrefabSentinel] WriteAtomic: {exception.GetType().Name}: {exception.Message}"));
        }

        private static void TryDelete(string path)
        {
            try { if (File.Exists(path)) File.Delete(path); }
            catch (Exception ex)
            {
                Debug.LogWarning($"[PrefabSentinel] TryDelete: {ex.GetType().Name}: {ex.Message}");
            }
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
            public int protocol_version = UnityEditorControlBridge.ProtocolVersion;
            public bool success = false;
            public string severity = "error";
            public string code = string.Empty;
            public string message = string.Empty;
            public ErrorResponseData data = new ErrorResponseData();
            public string[] diagnostics = Array.Empty<string>();
        }
    }
}
