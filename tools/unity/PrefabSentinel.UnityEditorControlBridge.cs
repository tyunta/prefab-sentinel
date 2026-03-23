using System;
using System.Collections.Generic;
using System.IO;
using UnityEditor;
using UnityEditor.Compilation;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.SceneManagement;

namespace PrefabSentinel
{
    /// <summary>
    /// Handles editor-control actions dispatched by EditorBridgeWindow:
    /// capture_screenshot, select_object, frame_selected, instantiate_to_scene, ping_object.
    /// Uses the same action-based protocol as UnityRuntimeValidationBridge.
    /// </summary>
    public static class UnityEditorControlBridge
    {
        public const int ProtocolVersion = 1;

        /// <summary>All action strings handled by this bridge.</summary>
        public static readonly HashSet<string> SupportedActions = new HashSet<string>
        {
            "capture_screenshot",
            "select_object",
            "frame_selected",
            "instantiate_to_scene",
            "ping_object",
            "capture_console_logs",
            "refresh_asset_database",
            "recompile_scripts",
            "set_material",
            "delete_object",
            "list_children",
            "list_materials",
            "camera",
            "list_roots",
            "get_material_property",
            "run_integration_tests",
        };

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
            public string prefab_asset_path = string.Empty; // non-empty = open Prefab Stage first

            // frame_selected
            public float zoom = 0f;         // 0 = keep current

            // instantiate_to_scene
            public string prefab_path = string.Empty;
            public string parent_path = string.Empty;
            public float[] position = null; // [x, y, z]

            // ping_object
            public string asset_path = string.Empty;

            // set_material
            public string renderer_path = string.Empty;
            public int material_index = -1;
            public string material_guid = string.Empty;

            // capture_console_logs
            public int max_entries = 200;
            public string log_type_filter = "all"; // "all" | "error" | "warning" | "exception"
            public float since_seconds = 0f;       // 0 = no time filter

            // list_children
            public int list_depth = 1;

            // camera
            public float yaw = 0f;
            public float pitch = 0f;
            public float distance = 0f;  // 0 = keep current

            // get_material_property
            public string property_name = string.Empty; // empty = list all properties
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
        public sealed class ConsoleLogEntry
        {
            public string message = string.Empty;
            public string stack_trace = string.Empty;
            public string log_type = string.Empty;
            public string timestamp = string.Empty;
        }

        [Serializable]
        public sealed class ChildEntry
        {
            public string name = string.Empty;
            public string path = string.Empty;
            public int child_count = 0;
            public int depth = 0;
        }

        [Serializable]
        public sealed class MaterialSlotEntry
        {
            public string renderer_path = string.Empty;
            public string renderer_type = string.Empty;
            public int slot_index = 0;
            public string material_name = string.Empty;
            public string material_asset_path = string.Empty;
            public string material_guid = string.Empty;
        }

        [Serializable]
        public sealed class MaterialPropertyEntry
        {
            public string property_name = string.Empty;
            public string property_type = string.Empty;
            public string value = string.Empty;
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
            public string deleted_object = string.Empty;
            public int deleted_child_count = 0;
            public int total_entries = 0;
            public ConsoleLogEntry[] entries = Array.Empty<ConsoleLogEntry>();
            public ChildEntry[] children = Array.Empty<ChildEntry>();
            public MaterialSlotEntry[] material_slots = Array.Empty<MaterialSlotEntry>();
            public MaterialPropertyEntry[] material_properties = Array.Empty<MaterialPropertyEntry>();
            public string[] root_objects = Array.Empty<string>();
            public float camera_yaw = 0f;
            public float camera_pitch = 0f;
            public float camera_distance = 0f;
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
                case "capture_console_logs":
                    response = HandleCaptureConsoleLogs(request);
                    break;
                case "refresh_asset_database":
                    response = HandleRefreshAssetDatabase();
                    break;
                case "recompile_scripts":
                    response = HandleRecompileScripts();
                    break;
                case "set_material":
                    response = HandleSetMaterial(request);
                    break;
                case "delete_object":
                    response = HandleDeleteObject(request);
                    break;
                case "list_children":
                    response = HandleListChildren(request);
                    break;
                case "list_materials":
                    response = HandleListMaterials(request);
                    break;
                case "camera":
                    response = HandleCamera(request);
                    break;
                case "list_roots":
                    response = HandleListRoots(request);
                    break;
                case "get_material_property":
                    response = HandleGetMaterialProperty(request);
                    break;
                case "run_integration_tests":
                    response = HandleRunIntegrationTests();
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
                    if (cam == null)
                        return BuildError("EDITOR_CTRL_NO_SCENE_CAMERA", "SceneView camera is null.");

                    RenderTexture rt = null;
                    Texture2D tex = null;
                    try
                    {
                        rt = new RenderTexture(w, h, 24);
                        RenderTexture prev = cam.targetTexture;
                        cam.targetTexture = rt;
                        cam.Render();
                        cam.targetTexture = prev;

                        RenderTexture.active = rt;
                        tex = new Texture2D(w, h, TextureFormat.RGB24, false);
                        tex.ReadPixels(new Rect(0, 0, w, h), 0, 0);
                        tex.Apply();
                        RenderTexture.active = null;

                        byte[] png = tex.EncodeToPNG();
                        File.WriteAllBytes(outputPath, png);
                    }
                    finally
                    {
                        if (tex != null) UnityEngine.Object.DestroyImmediate(tex);
                        if (rt != null) UnityEngine.Object.DestroyImmediate(rt);
                        RenderTexture.active = null;
                    }

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

                    RenderTexture rt = null;
                    try
                    {
                        if (request.width > 0 && request.height > 0)
                        {
                            // Resize if custom dimensions requested
                            rt = RenderTexture.GetTemporary(request.width, request.height);
                            Graphics.Blit(tex, rt);
                            UnityEngine.Object.DestroyImmediate(tex);

                            RenderTexture.active = rt;
                            tex = new Texture2D(request.width, request.height, TextureFormat.RGB24, false);
                            tex.ReadPixels(new Rect(0, 0, request.width, request.height), 0, 0);
                            tex.Apply();
                            RenderTexture.active = null;

                            w = request.width;
                            h = request.height;
                        }

                        byte[] png = tex.EncodeToPNG();
                        File.WriteAllBytes(outputPath, png);
                    }
                    finally
                    {
                        if (tex != null) UnityEngine.Object.DestroyImmediate(tex);
                        if (rt != null) RenderTexture.ReleaseTemporary(rt);
                        RenderTexture.active = null;
                    }

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

            // Prefab Stage mode: open the prefab and search within its stage root
            if (!string.IsNullOrEmpty(request.prefab_asset_path))
            {
                var stage = PrefabStageUtility.OpenPrefab(request.prefab_asset_path);
                if (stage == null)
                    return BuildError("EDITOR_CTRL_PREFAB_STAGE_FAILED",
                        $"Failed to open Prefab Stage: {request.prefab_asset_path}");

                var stageRoot = stage.prefabContentsRoot;
                if (stageRoot == null)
                    return BuildError("EDITOR_CTRL_PREFAB_STAGE_FAILED",
                        $"Prefab Stage root is null: {request.prefab_asset_path}");

                // Try finding the child by relative path under the stage root
                Transform target = stageRoot.transform.Find(request.hierarchy_path);
                // Also try searching directly if path matches the root name
                if (target == null && stageRoot.name == request.hierarchy_path)
                    target = stageRoot.transform;

                if (target == null)
                    return BuildError("EDITOR_CTRL_OBJECT_NOT_FOUND",
                        $"GameObject not found in Prefab Stage: {request.hierarchy_path}");

                Selection.activeGameObject = target.gameObject;
                var psv = SceneView.lastActiveSceneView;
                if (psv != null) { psv.FrameSelected(); psv.Repaint(); }
                return BuildSuccess("EDITOR_CTRL_SELECT_OK",
                    $"Selected in Prefab Stage: {request.hierarchy_path}",
                    data: new EditorControlData
                    {
                        selected_object = request.hierarchy_path,
                        executed = true
                    });
            }

            // Scene mode: search scene hierarchy
            GameObject go = GameObject.Find(request.hierarchy_path);
            if (go == null)
                return BuildError("EDITOR_CTRL_OBJECT_NOT_FOUND",
                    $"GameObject not found: {request.hierarchy_path}");

            Selection.activeGameObject = go;
            var sv = SceneView.lastActiveSceneView;
            if (sv != null) { sv.FrameSelected(); sv.Repaint(); }

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
                {
                    instance.transform.SetParent(parent.transform, false);
                }
                else
                {
                    UnityEngine.Object.DestroyImmediate(instance);
                    return BuildError("EDITOR_CTRL_PARENT_NOT_FOUND",
                        $"Parent not found: {request.parent_path}");
                }
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

        private static EditorControlResponse HandleRefreshAssetDatabase()
        {
            AssetDatabase.Refresh(ImportAssetOptions.ForceSynchronousImport);
            return BuildSuccess("EDITOR_CTRL_REFRESH_OK",
                "AssetDatabase.Refresh completed",
                data: new EditorControlData { executed = true });
        }

        private static EditorControlResponse HandleRecompileScripts()
        {
            // Schedule compilation on next frame so that the response JSON
            // is written to disk before domain reload destroys this context.
            EditorApplication.delayCall += () =>
            {
                CompilationPipeline.RequestScriptCompilation();
            };
            return BuildSuccess("EDITOR_CTRL_RECOMPILE_OK",
                "Script recompilation scheduled (domain reload will follow)",
                data: new EditorControlData { executed = true });
        }

        private static EditorControlResponse HandleRunIntegrationTests()
        {
            try
            {
                var result = UnityIntegrationTests.RunTestSuite();
                string json = JsonUtility.ToJson(result, true);
                if (result.success)
                    return BuildSuccess("EDITOR_CTRL_TESTS_PASSED", json,
                        data: new EditorControlData { executed = true });
                return BuildError("EDITOR_CTRL_TESTS_FAILED", json);
            }
            catch (Exception ex)
            {
                return BuildError("EDITOR_CTRL_TESTS_ERROR", ex.ToString());
            }
        }

        private static EditorControlResponse HandleSetMaterial(EditorControlRequest request)
        {
            if (string.IsNullOrEmpty(request.renderer_path))
                return BuildError("EDITOR_CTRL_SET_MATERIAL_NO_PATH", "renderer_path is required.");
            if (request.material_index < 0)
                return BuildError("EDITOR_CTRL_SET_MATERIAL_NO_INDEX", "material_index is required (>= 0).");
            if (string.IsNullOrEmpty(request.material_guid))
                return BuildError("EDITOR_CTRL_SET_MATERIAL_NO_GUID", "material_guid is required.");

            var go = GameObject.Find(request.renderer_path);
            if (go == null)
                return BuildError("EDITOR_CTRL_SET_MATERIAL_NOT_FOUND",
                    $"GameObject not found: {request.renderer_path}");

            var renderer = go.GetComponent<Renderer>();
            if (renderer == null)
                return BuildError("EDITOR_CTRL_SET_MATERIAL_NO_RENDERER",
                    $"No Renderer on: {request.renderer_path}");

            var mats = renderer.sharedMaterials;
            if (request.material_index >= mats.Length)
                return BuildError("EDITOR_CTRL_SET_MATERIAL_INDEX_OOB",
                    $"material_index {request.material_index} out of range (length={mats.Length}).");

            string assetPath = AssetDatabase.GUIDToAssetPath(request.material_guid);
            if (string.IsNullOrEmpty(assetPath))
                return BuildError("EDITOR_CTRL_SET_MATERIAL_GUID_NOT_FOUND",
                    $"No asset found for GUID: {request.material_guid}");

            var mat = AssetDatabase.LoadAssetAtPath<Material>(assetPath);
            if (mat == null)
                return BuildError("EDITOR_CTRL_SET_MATERIAL_LOAD_FAILED",
                    $"Failed to load Material at: {assetPath}");

            Undo.RecordObject(renderer, $"PrefabSentinel: Set material[{request.material_index}]");
            mats[request.material_index] = mat;
            renderer.sharedMaterials = mats;

            return BuildSuccess("EDITOR_CTRL_SET_MATERIAL_OK",
                $"Set material[{request.material_index}] to {assetPath}",
                data: new EditorControlData { executed = true, read_only = false });
        }

        private static EditorControlResponse HandleDeleteObject(EditorControlRequest request)
        {
            if (string.IsNullOrEmpty(request.hierarchy_path))
                return BuildError("EDITOR_CTRL_MISSING_PATH", "hierarchy_path is required for delete_object.");

            GameObject go = GameObject.Find(request.hierarchy_path);
            if (go == null)
                return BuildError("EDITOR_CTRL_OBJECT_NOT_FOUND",
                    $"GameObject not found: {request.hierarchy_path}");

            // Prefab instance roots cannot be directly destroyed; unpack first.
            if (PrefabUtility.IsPartOfPrefabInstance(go)
                && PrefabUtility.GetOutermostPrefabInstanceRoot(go) == go)
            {
                PrefabUtility.UnpackPrefabInstance(go, PrefabUnpackMode.Completely, InteractionMode.AutomatedAction);
            }

            string name = go.name;
            int childCount = go.transform.childCount;
            Undo.DestroyObjectImmediate(go);

            return BuildSuccess("EDITOR_CTRL_DELETE_OK",
                $"Deleted: {name} ({childCount} children)",
                data: new EditorControlData
                {
                    deleted_object = name,
                    deleted_child_count = childCount,
                    read_only = false,
                    executed = true
                });
        }

        private static EditorControlResponse HandleListChildren(EditorControlRequest request)
        {
            if (string.IsNullOrEmpty(request.hierarchy_path))
                return BuildError("EDITOR_CTRL_MISSING_PATH", "hierarchy_path is required for list_children.");

            GameObject go = GameObject.Find(request.hierarchy_path);
            if (go == null)
                return BuildError("EDITOR_CTRL_OBJECT_NOT_FOUND",
                    $"GameObject not found: {request.hierarchy_path}");

            int maxDepth = Math.Min(Math.Max(request.list_depth, 1), 50);
            var children = new List<ChildEntry>();
            CollectChildren(go.transform, maxDepth, 0, children);

            return BuildSuccess("EDITOR_CTRL_LIST_CHILDREN_OK",
                $"Found {children.Count} children under {go.name}",
                data: new EditorControlData
                {
                    children = children.ToArray(),
                    total_entries = children.Count,
                    read_only = true,
                    executed = true
                });
        }

        private static EditorControlResponse HandleListMaterials(EditorControlRequest request)
        {
            if (string.IsNullOrEmpty(request.hierarchy_path))
                return BuildError("EDITOR_CTRL_MISSING_PATH", "hierarchy_path is required for list_materials.");

            GameObject go = GameObject.Find(request.hierarchy_path);
            if (go == null)
                return BuildError("EDITOR_CTRL_OBJECT_NOT_FOUND",
                    $"GameObject not found: {request.hierarchy_path}");

            var renderers = go.GetComponentsInChildren<Renderer>();
            var slots = new List<MaterialSlotEntry>();

            foreach (var renderer in renderers)
            {
                string rendererPath = GetHierarchyPath(renderer.transform);
                string rendererType = renderer.GetType().Name;
                var materials = renderer.sharedMaterials;

                for (int i = 0; i < materials.Length; i++)
                {
                    var mat = materials[i];
                    string matName = mat != null ? mat.name : "(none)";
                    string matAssetPath = "";
                    string matGuid = "";

                    if (mat != null)
                    {
                        matAssetPath = AssetDatabase.GetAssetPath(mat);
                        matGuid = AssetDatabase.AssetPathToGUID(matAssetPath);
                    }

                    slots.Add(new MaterialSlotEntry
                    {
                        renderer_path = rendererPath,
                        renderer_type = rendererType,
                        slot_index = i,
                        material_name = matName,
                        material_asset_path = matAssetPath,
                        material_guid = matGuid
                    });
                }
            }

            return BuildSuccess("EDITOR_CTRL_LIST_MATERIALS_OK",
                $"Found {slots.Count} material slots on {renderers.Length} renderers under {go.name}",
                data: new EditorControlData
                {
                    material_slots = slots.ToArray(),
                    total_entries = slots.Count,
                    read_only = true,
                    executed = true
                });
        }

        private static EditorControlResponse HandleCamera(EditorControlRequest request)
        {
            SceneView sceneView = SceneView.lastActiveSceneView;
            if (sceneView == null)
                return BuildError("EDITOR_CTRL_NO_SCENE_VIEW", "No active SceneView found.");

            sceneView.rotation = Quaternion.Euler(request.pitch, request.yaw, 0f);

            if (request.distance > 0f)
                sceneView.size = request.distance;

            sceneView.Repaint();

            return BuildSuccess("EDITOR_CTRL_CAMERA_OK",
                $"Camera set: yaw={request.yaw}, pitch={request.pitch}" +
                (request.distance > 0f ? $", distance={request.distance}" : ""),
                data: new EditorControlData
                {
                    camera_yaw = request.yaw,
                    camera_pitch = request.pitch,
                    camera_distance = sceneView.size,
                    executed = true
                });
        }

        private static EditorControlResponse HandleListRoots(EditorControlRequest request)
        {
            var prefabStage = PrefabStageUtility.GetCurrentPrefabStage();
            if (prefabStage != null)
            {
                var root = prefabStage.prefabContentsRoot;
                if (root == null)
                    return BuildError("EDITOR_CTRL_PREFAB_STAGE_FAILED", "Prefab Stage root is null.");

                return BuildSuccess("EDITOR_CTRL_LIST_ROOTS_OK",
                    $"Prefab Stage root: {root.name}",
                    data: new EditorControlData
                    {
                        root_objects = new[] { root.name },
                        children = new[] { new ChildEntry
                        {
                            name = root.name,
                            path = "/" + root.name,
                            child_count = root.transform.childCount,
                            depth = 0
                        }},
                        total_entries = 1,
                        read_only = true,
                        executed = true
                    });
            }

            var scene = SceneManager.GetActiveScene();
            if (!scene.IsValid())
                return BuildError("EDITOR_CTRL_NO_SCENE", "No valid active scene found.");

            var rootObjects = scene.GetRootGameObjects();
            var names = new string[rootObjects.Length];
            var entries = new List<ChildEntry>();

            for (int i = 0; i < rootObjects.Length; i++)
            {
                names[i] = rootObjects[i].name;
                entries.Add(new ChildEntry
                {
                    name = rootObjects[i].name,
                    path = "/" + rootObjects[i].name,
                    child_count = rootObjects[i].transform.childCount,
                    depth = 0
                });
            }

            return BuildSuccess("EDITOR_CTRL_LIST_ROOTS_OK",
                $"Found {rootObjects.Length} root objects in scene '{scene.name}'",
                data: new EditorControlData
                {
                    root_objects = names,
                    children = entries.ToArray(),
                    total_entries = entries.Count,
                    read_only = true,
                    executed = true
                });
        }

        private static EditorControlResponse HandleGetMaterialProperty(EditorControlRequest request)
        {
            if (string.IsNullOrEmpty(request.renderer_path))
                return BuildError("EDITOR_CTRL_MISSING_PATH", "renderer_path is required for get_material_property.");
            if (request.material_index < 0)
                return BuildError("EDITOR_CTRL_MISSING_INDEX", "material_index is required (>= 0).");

            var go = GameObject.Find(request.renderer_path);
            if (go == null)
                return BuildError("EDITOR_CTRL_OBJECT_NOT_FOUND",
                    $"GameObject not found: {request.renderer_path}");

            var renderer = go.GetComponent<Renderer>();
            if (renderer == null)
                return BuildError("EDITOR_CTRL_NO_RENDERER",
                    $"No Renderer on: {request.renderer_path}");

            var mats = renderer.sharedMaterials;
            if (request.material_index >= mats.Length)
                return BuildError("EDITOR_CTRL_INDEX_OOB",
                    $"material_index {request.material_index} out of range (length={mats.Length}).");

            var mat = mats[request.material_index];
            if (mat == null)
                return BuildError("EDITOR_CTRL_MATERIAL_NULL",
                    $"Material at index {request.material_index} is null.");

            var shader = mat.shader;
            if (shader == null)
                return BuildError("EDITOR_CTRL_SHADER_NULL",
                    $"Material at index {request.material_index} has no shader assigned.");

            var properties = new List<MaterialPropertyEntry>();
            int propCount = shader.GetPropertyCount();

            for (int i = 0; i < propCount; i++)
            {
                string propName = shader.GetPropertyName(i);
                var propType = shader.GetPropertyType(i);

                if (!string.IsNullOrEmpty(request.property_name) && propName != request.property_name)
                    continue;

                string valueStr;
                switch (propType)
                {
                    case UnityEngine.Rendering.ShaderPropertyType.Color:
                        valueStr = mat.GetColor(propName).ToString();
                        break;
                    case UnityEngine.Rendering.ShaderPropertyType.Float:
                    case UnityEngine.Rendering.ShaderPropertyType.Range:
                        valueStr = mat.GetFloat(propName).ToString("G9");
                        break;
                    case UnityEngine.Rendering.ShaderPropertyType.Vector:
                        valueStr = mat.GetVector(propName).ToString();
                        break;
                    case UnityEngine.Rendering.ShaderPropertyType.Texture:
                        var tex = mat.GetTexture(propName);
                        valueStr = tex != null ? AssetDatabase.GetAssetPath(tex) : "(none)";
                        break;
                    case UnityEngine.Rendering.ShaderPropertyType.Int:
                        valueStr = mat.GetInteger(propName).ToString();
                        break;
                    default:
                        valueStr = "(unknown type)";
                        break;
                }

                properties.Add(new MaterialPropertyEntry
                {
                    property_name = propName,
                    property_type = propType.ToString(),
                    value = valueStr
                });
            }

            if (!string.IsNullOrEmpty(request.property_name) && properties.Count == 0)
                return BuildError("EDITOR_CTRL_PROPERTY_NOT_FOUND",
                    $"Property '{request.property_name}' not found on shader '{shader.name}'.");

            return BuildSuccess("EDITOR_CTRL_GET_MATERIAL_PROPERTY_OK",
                $"Found {properties.Count} properties on material '{mat.name}' (shader: {shader.name})",
                data: new EditorControlData
                {
                    material_properties = properties.ToArray(),
                    total_entries = properties.Count,
                    read_only = true,
                    executed = true
                });
        }

        private static void CollectChildren(Transform parent, int maxDepth, int currentDepth, List<ChildEntry> result)
        {
            for (int i = 0; i < parent.childCount; i++)
            {
                Transform child = parent.GetChild(i);
                result.Add(new ChildEntry
                {
                    name = child.name,
                    path = GetHierarchyPath(child),
                    child_count = child.childCount,
                    depth = currentDepth + 1
                });
                if (currentDepth + 1 < maxDepth)
                    CollectChildren(child, maxDepth, currentDepth + 1, result);
            }
        }

        private static string GetHierarchyPath(Transform t)
        {
            string path = t.name;
            while (t.parent != null)
            {
                t = t.parent;
                path = t.name + "/" + path;
            }
            return "/" + path;
        }

        // ── Console Log Buffer ──

        /// <summary>
        /// Ring buffer that captures Unity console logs via Application.logMessageReceived.
        /// Managed by EditorBridgeWindow (start/stop tied to window lifecycle).
        /// </summary>
        public static class ConsoleLogBuffer
        {
            private const int DefaultCapacity = 1000;

            private struct RawEntry
            {
                public string message;
                public string stackTrace;
                public LogType logType;
                public double timestamp; // EditorApplication.timeSinceStartup
            }

            private static RawEntry[] _buffer;
            private static int _head;
            private static int _count;
            private static bool _capturing;
            private static readonly object _lock = new object();

            public static void StartCapture()
            {
                if (_capturing) return;
                lock (_lock)
                {
                    _buffer = new RawEntry[DefaultCapacity];
                    _head = 0;
                    _count = 0;
                    _capturing = true;
                }
                Application.logMessageReceived += OnLogMessage;
            }

            public static void StopCapture()
            {
                if (!_capturing) return;
                Application.logMessageReceived -= OnLogMessage;
                lock (_lock)
                {
                    _capturing = false;
                }
            }

            public static bool IsCapturing => _capturing;

            private static void OnLogMessage(string message, string stackTrace, LogType type)
            {
                lock (_lock)
                {
                    if (!_capturing || _buffer == null) return;
                    _buffer[_head] = new RawEntry
                    {
                        message = message,
                        stackTrace = stackTrace,
                        logType = type,
                        timestamp = EditorApplication.timeSinceStartup,
                    };
                    _head = (_head + 1) % _buffer.Length;
                    if (_count < _buffer.Length) _count++;
                }
            }

            /// <summary>
            /// Snapshot the buffer, applying filters. Returns entries oldest-first.
            /// </summary>
            public static List<ConsoleLogEntry> GetEntries(int maxEntries, string logTypeFilter, float sinceSeconds)
            {
                var result = new List<ConsoleLogEntry>();
                lock (_lock)
                {
                    if (_buffer == null || _count == 0) return result;

                    double now = EditorApplication.timeSinceStartup;
                    int start = (_head - _count + _buffer.Length) % _buffer.Length;

                    for (int i = 0; i < _count; i++)
                    {
                        var entry = _buffer[(start + i) % _buffer.Length];

                        // Time filter
                        if (sinceSeconds > 0f && (now - entry.timestamp) > sinceSeconds)
                            continue;

                        // Type filter
                        if (!MatchesTypeFilter(entry.logType, logTypeFilter))
                            continue;

                        result.Add(new ConsoleLogEntry
                        {
                            message = entry.message ?? string.Empty,
                            stack_trace = entry.stackTrace ?? string.Empty,
                            log_type = entry.logType.ToString(),
                            timestamp = TimeSpan.FromSeconds(entry.timestamp).ToString(@"hh\:mm\:ss"),
                        });

                        if (result.Count >= maxEntries) break;
                    }
                }
                return result;
            }

            private static bool MatchesTypeFilter(LogType type, string filter)
            {
                if (string.IsNullOrEmpty(filter) || filter == "all") return true;
                switch (filter)
                {
                    case "error":     return type == LogType.Error || type == LogType.Exception || type == LogType.Assert;
                    case "warning":   return type == LogType.Warning;
                    case "exception": return type == LogType.Exception;
                    default:          return true;
                }
            }
        }

        private static EditorControlResponse HandleCaptureConsoleLogs(EditorControlRequest request)
        {
            if (!ConsoleLogBuffer.IsCapturing)
                return BuildError("EDITOR_CTRL_CONSOLE_NOT_ACTIVE",
                    "Console log capture is not active. Enable Editor Bridge to start capturing.");

            int maxEntries = request.max_entries > 0 ? request.max_entries : 200;
            var entries = ConsoleLogBuffer.GetEntries(maxEntries, request.log_type_filter, request.since_seconds);

            return BuildSuccess("EDITOR_CTRL_CONSOLE_OK",
                $"Captured {entries.Count} log entries",
                data: new EditorControlData
                {
                    total_entries = entries.Count,
                    entries = entries.ToArray(),
                    read_only = true,
                    executed = true,
                });
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
            try
            {
                string json = JsonUtility.ToJson(response, true);
                string tmpPath = responsePath + ".tmp";
                File.WriteAllText(tmpPath, json);
                if (File.Exists(responsePath)) File.Delete(responsePath);
                File.Move(tmpPath, responsePath);
            }
            catch
            {
                try { File.WriteAllText(responsePath, JsonUtility.ToJson(response, true)); }
                catch { /* best effort */ }
            }
        }
    }
}
