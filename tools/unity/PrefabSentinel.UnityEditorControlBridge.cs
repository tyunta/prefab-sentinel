using System;
using System.IO;
using UnityEditor;
using UnityEngine;

namespace PrefabSentinel
{
    /// <summary>
    /// Handles editor-control actions dispatched by EditorBridgeWindow:
    /// capture_screenshot, select_object, frame_selected, instantiate_to_scene, ping_object.
    /// Uses the same action-based protocol as UnityRuntimeValidationBridge.
    /// </summary>
    public static class UnityEditorControlBridge
    {
        private const int ProtocolVersion = 1;

        // ── Request / Response DTOs ──

        [Serializable]
        public sealed class EditorControlRequest
        {
            public int protocol_version = 0;
            public string action = string.Empty;

            // capture_screenshot
            public string view = "scene";   // "scene" | "game"
            public int width = 0;           // 0 = use current window size
            public int height = 0;

            // select_object
            public string hierarchy_path = string.Empty;

            // frame_selected
            public float zoom = 0f;         // 0 = keep current

            // instantiate_to_scene
            public string prefab_path = string.Empty;
            public string parent_path = string.Empty;
            public float[] position = null; // [x, y, z]

            // ping_object
            public string asset_path = string.Empty;
        }

        [Serializable]
        public sealed class EditorControlDiagnostic
        {
            public string path = string.Empty;
            public string location = string.Empty;
            public string detail = string.Empty;
            public string evidence = string.Empty;
        }

        [Serializable]
        public sealed class EditorControlData
        {
            public string output_path = string.Empty;
            public string view = string.Empty;
            public int width = 0;
            public int height = 0;
            public string selected_object = string.Empty;
            public string instantiated_object = string.Empty;
            public bool read_only = true;
            public bool executed = false;
        }

        [Serializable]
        public sealed class EditorControlResponse
        {
            public int protocol_version = ProtocolVersion;
            public bool success = false;
            public string severity = "error";
            public string code = string.Empty;
            public string message = string.Empty;
            public EditorControlData data = new EditorControlData();
            public EditorControlDiagnostic[] diagnostics = Array.Empty<EditorControlDiagnostic>();
        }

        // ── Entry Point ──

        public static void RunFromPaths(string requestPath, string responsePath)
        {
            EditorControlRequest request;
            try
            {
                string json = File.ReadAllText(requestPath);
                request = JsonUtility.FromJson<EditorControlRequest>(json);
            }
            catch (Exception ex)
            {
                WriteResponse(responsePath, BuildError(
                    "EDITOR_CTRL_PROTOCOL_ERROR",
                    $"Failed to read request: {ex.Message}"));
                return;
            }

            if (request.protocol_version != ProtocolVersion)
            {
                WriteResponse(responsePath, BuildError(
                    "EDITOR_CTRL_PROTOCOL_VERSION",
                    $"Expected protocol_version {ProtocolVersion}, got {request.protocol_version}."));
                return;
            }

            EditorControlResponse response;
            switch (request.action)
            {
                case "capture_screenshot":
                    response = HandleCaptureScreenshot(request, requestPath);
                    break;
                case "select_object":
                    response = HandleSelectObject(request);
                    break;
                case "frame_selected":
                    response = HandleFrameSelected(request);
                    break;
                case "instantiate_to_scene":
                    response = HandleInstantiateToScene(request);
                    break;
                case "ping_object":
                    response = HandlePingObject(request);
                    break;
                default:
                    response = BuildError(
                        "EDITOR_CTRL_UNKNOWN_ACTION",
                        $"Unknown action: {request.action}");
                    break;
            }

            WriteResponse(responsePath, response);
        }

        // ── Action Handlers ──

        private static EditorControlResponse HandleCaptureScreenshot(EditorControlRequest request, string requestPath)
        {
            string outputDir = Path.Combine(Path.GetDirectoryName(requestPath), "screenshots");
            if (!Directory.Exists(outputDir))
                Directory.CreateDirectory(outputDir);

            string timestamp = DateTime.Now.ToString("yyyyMMdd_HHmmss");
            string outputPath = Path.Combine(outputDir, $"{request.view}_{timestamp}.png");

            bool isScene = string.Equals(request.view, "scene", StringComparison.OrdinalIgnoreCase);

            try
            {
                if (isScene)
                {
                    SceneView sceneView = SceneView.lastActiveSceneView;
                    if (sceneView == null)
                        return BuildError("EDITOR_CTRL_NO_SCENE_VIEW", "No active SceneView found.");

                    int w = request.width > 0 ? request.width : (int)sceneView.position.width;
                    int h = request.height > 0 ? request.height : (int)sceneView.position.height;

                    Camera cam = sceneView.camera;
                    RenderTexture rt = new RenderTexture(w, h, 24);
                    RenderTexture prev = cam.targetTexture;
                    cam.targetTexture = rt;
                    cam.Render();
                    cam.targetTexture = prev;

                    RenderTexture.active = rt;
                    Texture2D tex = new Texture2D(w, h, TextureFormat.RGB24, false);
                    tex.ReadPixels(new Rect(0, 0, w, h), 0, 0);
                    tex.Apply();
                    RenderTexture.active = null;

                    byte[] png = tex.EncodeToPNG();
                    File.WriteAllBytes(outputPath, png);

                    UnityEngine.Object.DestroyImmediate(tex);
                    UnityEngine.Object.DestroyImmediate(rt);

                    return BuildSuccess("EDITOR_CTRL_SCREENSHOT_OK", $"Scene view captured to {outputPath}",
                        data: new EditorControlData
                        {
                            output_path = outputPath,
                            view = "scene",
                            width = w,
                            height = h,
                            executed = true
                        });
                }
                else
                {
                    // Game view: use ScreenCapture (requires game view to exist)
                    int w = request.width > 0 ? request.width : Screen.width;
                    int h = request.height > 0 ? request.height : Screen.height;

                    Texture2D tex = ScreenCapture.CaptureScreenshotAsTexture();
                    if (tex == null)
                        return BuildError("EDITOR_CTRL_NO_GAME_VIEW", "Failed to capture game view. Ensure Game view is visible.");

                    if (request.width > 0 && request.height > 0)
                    {
                        // Resize if custom dimensions requested
                        RenderTexture rt = RenderTexture.GetTemporary(request.width, request.height);
                        Graphics.Blit(tex, rt);
                        UnityEngine.Object.DestroyImmediate(tex);

                        RenderTexture.active = rt;
                        tex = new Texture2D(request.width, request.height, TextureFormat.RGB24, false);
                        tex.ReadPixels(new Rect(0, 0, request.width, request.height), 0, 0);
                        tex.Apply();
                        RenderTexture.active = null;
                        RenderTexture.ReleaseTemporary(rt);

                        w = request.width;
                        h = request.height;
                    }

                    byte[] png = tex.EncodeToPNG();
                    File.WriteAllBytes(outputPath, png);
                    UnityEngine.Object.DestroyImmediate(tex);

                    return BuildSuccess("EDITOR_CTRL_SCREENSHOT_OK", $"Game view captured to {outputPath}",
                        data: new EditorControlData
                        {
                            output_path = outputPath,
                            view = "game",
                            width = w,
                            height = h,
                            executed = true
                        });
                }
            }
            catch (Exception ex)
            {
                return BuildError("EDITOR_CTRL_SCREENSHOT_FAILED", $"Screenshot failed: {ex.Message}");
            }
        }

        private static EditorControlResponse HandleSelectObject(EditorControlRequest request)
        {
            if (string.IsNullOrEmpty(request.hierarchy_path))
                return BuildError("EDITOR_CTRL_MISSING_PATH", "hierarchy_path is required for select_object.");

            GameObject go = GameObject.Find(request.hierarchy_path);
            if (go == null)
                return BuildError("EDITOR_CTRL_OBJECT_NOT_FOUND",
                    $"GameObject not found: {request.hierarchy_path}");

            Selection.activeGameObject = go;

            return BuildSuccess("EDITOR_CTRL_SELECT_OK",
                $"Selected: {request.hierarchy_path}",
                data: new EditorControlData
                {
                    selected_object = request.hierarchy_path,
                    executed = true
                });
        }

        private static EditorControlResponse HandleFrameSelected(EditorControlRequest request)
        {
            if (Selection.activeGameObject == null)
                return BuildError("EDITOR_CTRL_NO_SELECTION", "No GameObject is selected. Use select_object first.");

            SceneView sceneView = SceneView.lastActiveSceneView;
            if (sceneView == null)
                return BuildError("EDITOR_CTRL_NO_SCENE_VIEW", "No active SceneView found.");

            sceneView.FrameSelected();

            if (request.zoom > 0f)
                sceneView.size = request.zoom;

            sceneView.Repaint();

            return BuildSuccess("EDITOR_CTRL_FRAME_OK",
                $"Framed: {Selection.activeGameObject.name}" + (request.zoom > 0f ? $" (zoom={request.zoom})" : ""),
                data: new EditorControlData
                {
                    selected_object = Selection.activeGameObject.name,
                    executed = true
                });
        }

        private static EditorControlResponse HandleInstantiateToScene(EditorControlRequest request)
        {
            if (string.IsNullOrEmpty(request.prefab_path))
                return BuildError("EDITOR_CTRL_MISSING_PATH", "prefab_path is required for instantiate_to_scene.");

            var prefab = AssetDatabase.LoadAssetAtPath<GameObject>(request.prefab_path);
            if (prefab == null)
                return BuildError("EDITOR_CTRL_ASSET_NOT_FOUND",
                    $"Prefab not found at: {request.prefab_path}");

            GameObject instance = (GameObject)PrefabUtility.InstantiatePrefab(prefab);
            if (instance == null)
                return BuildError("EDITOR_CTRL_INSTANTIATE_FAILED",
                    $"Failed to instantiate: {request.prefab_path}");

            // Set parent if specified
            if (!string.IsNullOrEmpty(request.parent_path))
            {
                GameObject parent = GameObject.Find(request.parent_path);
                if (parent != null)
                    instance.transform.SetParent(parent.transform, false);
                else
                    Debug.LogWarning($"[PrefabSentinel.EditorControl] Parent not found: {request.parent_path}");
            }

            // Set position if specified
            if (request.position != null && request.position.Length >= 3)
                instance.transform.localPosition = new Vector3(request.position[0], request.position[1], request.position[2]);

            Selection.activeGameObject = instance;
            Undo.RegisterCreatedObjectUndo(instance, $"PrefabSentinel: Instantiate {prefab.name}");

            return BuildSuccess("EDITOR_CTRL_INSTANTIATE_OK",
                $"Instantiated {prefab.name} as {instance.name}",
                data: new EditorControlData
                {
                    instantiated_object = instance.name,
                    selected_object = instance.name,
                    read_only = false,
                    executed = true
                });
        }

        private static EditorControlResponse HandlePingObject(EditorControlRequest request)
        {
            if (string.IsNullOrEmpty(request.asset_path))
                return BuildError("EDITOR_CTRL_MISSING_PATH", "asset_path is required for ping_object.");

            var obj = AssetDatabase.LoadAssetAtPath<UnityEngine.Object>(request.asset_path);
            if (obj == null)
                return BuildError("EDITOR_CTRL_ASSET_NOT_FOUND",
                    $"Asset not found at: {request.asset_path}");

            EditorGUIUtility.PingObject(obj);

            return BuildSuccess("EDITOR_CTRL_PING_OK",
                $"Pinged: {request.asset_path}",
                data: new EditorControlData { executed = true });
        }

        // ── Response Builders ──

        private static EditorControlResponse BuildSuccess(string code, string message, EditorControlData data = null)
        {
            return new EditorControlResponse
            {
                protocol_version = ProtocolVersion,
                success = true,
                severity = "info",
                code = code,
                message = message,
                data = data ?? new EditorControlData { executed = true }
            };
        }

        private static EditorControlResponse BuildError(string code, string message)
        {
            return new EditorControlResponse
            {
                protocol_version = ProtocolVersion,
                success = false,
                severity = "error",
                code = code,
                message = message,
                data = new EditorControlData()
            };
        }

        private static void WriteResponse(string responsePath, EditorControlResponse response)
        {
            string json = JsonUtility.ToJson(response, true);
            string tmpPath = responsePath + ".tmp";
            File.WriteAllText(tmpPath, json);
            if (File.Exists(responsePath)) File.Delete(responsePath);
            File.Move(tmpPath, responsePath);
        }
    }
}
