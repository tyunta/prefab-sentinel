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
        public const string BridgeVersion = "0.5.150";

        /// <summary>Actions that write their response file asynchronously (not on return).</summary>
        public static readonly System.Collections.Generic.HashSet<string> AsyncActions =
            new System.Collections.Generic.HashSet<string> { "vrcsdk_upload" };

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
            "get_camera",
            "set_camera",
            "list_roots",
            "get_material_property",
            "set_material_property",
            "run_integration_tests",
            "vrcsdk_upload",
            // Phase 2: BlendShape + Menu
            "get_blend_shapes", "set_blend_shape",
            "list_menu_items", "execute_menu_item",
            // Phase 3: Material reverse lookup
            "find_renderers_by_material",
            // Phase 4: Rename + AddComponent + Udon
            "editor_rename",
            "editor_add_component",
            "create_udon_program_asset",
            // Phase 5: SetProperty + SaveAsPrefab
            "editor_set_property",
            "save_as_prefab",
            "editor_set_parent",
            // Phase 6: Batch Operations + Scene
            "editor_create_empty",
            "editor_create_primitive",
            "editor_batch_create",
            "editor_batch_set_property",
            "editor_batch_set_material_property",
            "editor_open_scene",
            "editor_save_scene",
            // Phase 7: UX Review improvements
            "editor_batch_add_component",
            "editor_remove_component",
            "editor_create_scene",
            // Phase 8: Reflection
            "editor_reflect",
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

            // instantiate_to_scene (asset_path = prefab, hierarchy_path = parent)
            public float[] position = null; // [x, y, z]

            // ping_object / instantiate_to_scene
            public string asset_path = string.Empty;
            public int material_index = -1;
            public string material_guid = string.Empty;
            public string material_path = string.Empty;  // asset path alternative to GUID

            // capture_console_logs
            public int max_entries = 200;
            public string log_type_filter = "all"; // "all" | "error" | "warning" | "exception"
            public float since_seconds = 0f;       // 0 = no time filter

            // list_children
            public int depth = 1;

            // camera (get_camera / set_camera)
            // Pivot orbit: pivot + yaw/pitch/distance
            public float[] camera_pivot = null;      // [x, y, z] pivot point
            public float yaw = float.NaN;           // NaN = keep current
            public float pitch = float.NaN;
            public float distance = -1f;             // SceneView.size; -1 = keep current
            // Position mode: camera_position + camera_look_at or yaw/pitch
            public float[] camera_position = null;   // [x, y, z] camera world coords
            public float[] camera_look_at = null;    // [x, y, z] look-at target
            // Shared
            public int camera_orthographic = -1;     // -1 = keep, 0 = perspective, 1 = ortho

            // get_material_property
            public string property_name = string.Empty; // empty = list all properties

            // set_material_property
            public string property_value = string.Empty;  // raw JSON string, manually parsed by handler

            // vrcsdk_upload
            public string target_type = string.Empty;    // "avatar" or "world"
            public string blueprint_id = string.Empty;    // existing VRC asset ID
            public string description = string.Empty;     // empty = no change
            public string tags = string.Empty;            // JSON array string, empty = no change
            public string release_status = string.Empty;  // "public" | "private", empty = no change
            public bool confirm = false;                  // dry-run gate
            public string platforms = string.Empty;  // JSON array: "[\"windows\",\"android\"]"

            // Phase 2: BlendShape
            public string filter = string.Empty;            // name substring filter / menu prefix
            public string blend_shape_name = string.Empty;  // BlendShape name
            public float blend_shape_weight = 0f;           // BlendShape weight (0-100)

            // Phase 2: Menu
            public string menu_path = string.Empty;         // menu item path

            // Phase 4: Rename + AddComponent + Udon
            public string new_name = string.Empty;
            public string component_type = string.Empty;
            public int component_index = -1;  // -1 = unspecified

            // Phase 5: SetProperty + SaveAsPrefab
            public string object_reference = string.Empty;

            // Phase 6: Batch Operations + Scene
            public string primitive_type = string.Empty;
            public string scale = string.Empty;
            public string rotation = string.Empty;
            public string batch_objects_json = string.Empty;
            public string batch_operations_json = string.Empty;
            public string properties_json = string.Empty;
            public string open_scene_mode = "single";

            // Phase 8: Reflection
            public string reflect_action = string.Empty;
            public string query = string.Empty;
            public string scope = "all";
            public string class_name = string.Empty;
            public string member_name = string.Empty;
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
            public bool active = true;
            public string tag = "Untagged";
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
        public sealed class BlendShapeEntry
        {
            public int index = 0;
            public string name = string.Empty;
            public float weight = 0f;
        }

        [Serializable]
        public sealed class MenuItemEntry
        {
            public string path = string.Empty;
            public string shortcut = string.Empty;
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
            public int component_count = 0;
            public ConsoleLogEntry[] entries = Array.Empty<ConsoleLogEntry>();
            public ChildEntry[] children = Array.Empty<ChildEntry>();
            public MaterialSlotEntry[] material_slots = Array.Empty<MaterialSlotEntry>();
            public MaterialPropertyEntry[] material_properties = Array.Empty<MaterialPropertyEntry>();
            public string[] root_objects = Array.Empty<string>();
            // Camera state (full 6DoF)
            public float[] camera_position = null;     // [x, y, z]
            public float[] camera_rotation_quat = null; // [x, y, z, w] quaternion
            public float[] camera_euler = null;        // [yaw, pitch, roll]
            public float[] camera_pivot = null;        // [x, y, z]
            public float camera_size = 0f;
            public bool camera_orthographic = false;
            // Previous camera state (set_camera only)
            public float[] previous_camera_position = null;
            public float[] previous_camera_euler = null;
            public float[] previous_camera_pivot = null;
            public float previous_camera_size = 0f;
            public bool previous_camera_orthographic = false;
            // Bounds info (frame_selected only)
            public float[] bounds_center = null;     // [x, y, z] world-space AABB center
            public float[] bounds_extents = null;    // [x, y, z] half-size

            public bool read_only = true;
            public bool executed = false;

            // vrcsdk_upload response
            public string target_type = string.Empty;
            public string asset_path = string.Empty;
            public string blueprint_id = string.Empty;
            public string phase = string.Empty;           // "validated" or "complete"
            public float elapsed_sec = 0f;

            // multi-platform upload results
            public string platform_results_json = string.Empty;
            public bool original_target_restored = false;

            // Phase 2: BlendShape
            public BlendShapeEntry[] blend_shapes = Array.Empty<BlendShapeEntry>();
            public string renderer_path = string.Empty;
            public int blend_shape_index = 0;
            public string blend_shape_name = string.Empty;
            public float blend_shape_before = 0f;
            public float blend_shape_after = 0f;

            // Phase 2: Menu
            public MenuItemEntry[] menu_items = Array.Empty<MenuItemEntry>();

            // error hint suggestions
            public string[] suggestions = Array.Empty<string>();

            // Phase 8: Reflection
            public string reflect_result_json = string.Empty;
        }

        [Serializable]
        public sealed class EditorControlResponse
        {
            public int protocol_version = ProtocolVersion;
            public string bridge_version = BridgeVersion;
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
                    $"Bridge protocol v{request.protocol_version}, required v{ProtocolVersion}. " +
                    "Update Bridge: copy tools/unity/*.cs from prefab-sentinel to Assets/Editor/PrefabSentinel/"));
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
                case "get_camera":
                    response = HandleGetCamera();
                    break;
                case "set_camera":
                    response = HandleSetCamera(request);
                    break;
                case "list_roots":
                    response = HandleListRoots(request);
                    break;
                case "get_material_property":
                    response = HandleGetMaterialProperty(request);
                    break;
                case "set_material_property":
                    response = HandleSetMaterialProperty(request);
                    break;
                case "run_integration_tests":
                    response = HandleRunIntegrationTests();
                    break;
                // Phase 2: BlendShape + Menu
                case "get_blend_shapes":
                    response = HandleGetBlendShapes(request);
                    break;
                case "set_blend_shape":
                    response = HandleSetBlendShape(request);
                    break;
                case "list_menu_items":
                    response = HandleListMenuItems(request);
                    break;
                case "execute_menu_item":
                    response = HandleExecuteMenuItem(request);
                    break;
                case "find_renderers_by_material":
                    response = HandleFindRenderersByMaterial(request);
                    break;
                case "editor_rename":
                    response = HandleEditorRename(request);
                    break;
                case "editor_add_component":
                    response = HandleEditorAddComponent(request);
                    break;
                case "create_udon_program_asset":
                    response = HandleCreateUdonProgramAsset(request);
                    break;
                case "editor_set_property":
                    response = HandleEditorSetProperty(request);
                    break;
                case "save_as_prefab":
                    response = HandleSaveAsPrefab(request);
                    break;
                case "editor_set_parent":
                    response = HandleEditorSetParent(request);
                    break;
                case "editor_create_empty":
                    response = HandleEditorCreateEmpty(request);
                    break;
                case "editor_create_primitive":
                    response = HandleEditorCreatePrimitive(request);
                    break;
                case "editor_batch_create":
                    response = HandleEditorBatchCreate(request);
                    break;
                case "editor_batch_set_property":
                    response = HandleEditorBatchSetProperty(request);
                    break;
                case "editor_batch_set_material_property":
                    response = HandleEditorBatchSetMaterialProperty(request);
                    break;
                case "editor_open_scene":
                    response = HandleEditorOpenScene(request);
                    break;
                case "editor_save_scene":
                    response = HandleEditorSaveScene(request);
                    break;
                case "editor_batch_add_component":
                    response = HandleEditorBatchAddComponent(request);
                    break;
                case "editor_remove_component":
                    response = HandleEditorRemoveComponent(request);
                    break;
                case "editor_create_scene":
                    response = HandleEditorCreateScene(request);
                    break;
                case "vrcsdk_upload":
                    response = TryHandleVrcsdkUpload(request, responsePath);
                    break;
                case "editor_reflect":
                    response = EditorReflectHandler.Handle(request);
                    break;
                default:
                    response = BuildError(
                        "EDITOR_CTRL_UNKNOWN_ACTION",
                        $"Unknown action: {request.action}");
                    break;
            }

            if (response != null)
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
                        // Force skinning recalculation so blend shape / material
                        // changes are reflected even when Unity is unfocused
                        var smrs = UnityEngine.Object.FindObjectsOfType<SkinnedMeshRenderer>();
                        foreach (var smr in smrs)
                            smr.forceMatrixRecalculationPerRender = true;

                        rt = new RenderTexture(w, h, 24);
                        RenderTexture prev = cam.targetTexture;
                        cam.targetTexture = rt;
                        cam.Render();
                        cam.targetTexture = prev;

                        // Restore to avoid per-frame overhead
                        foreach (var smr in smrs)
                            smr.forceMatrixRecalculationPerRender = false;

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
                EditorApplication.delayCall += () =>
                {
                    var psv = SceneView.lastActiveSceneView;
                    if (psv != null) { psv.FrameSelected(); psv.Repaint(); }
                };
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
            EditorApplication.delayCall += () =>
            {
                var sv = SceneView.lastActiveSceneView;
                if (sv != null) { sv.FrameSelected(); sv.Repaint(); }
            };

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
            GameObject selectedGo = Selection.activeGameObject;
            if (selectedGo == null)
                return BuildError("EDITOR_CTRL_NO_SELECTION", "No GameObject is selected. Use select_object first.");

            SceneView sceneView = SceneView.lastActiveSceneView;
            if (sceneView == null)
                return BuildError("EDITOR_CTRL_NO_SCENE_VIEW", "No active SceneView found.");

            string objectName = selectedGo.name;

            // Collect bounds info
            float[] boundsCenter = null;
            float[] boundsExtents = null;
            Renderer renderer = selectedGo.GetComponentInChildren<Renderer>();
            if (renderer != null)
            {
                Bounds b = renderer.bounds;
                boundsCenter = new[] { b.center.x, b.center.y, b.center.z };
                boundsExtents = new[] { b.extents.x, b.extents.y, b.extents.z };
            }

            // Frame synchronously so we can capture post-frame camera state
            sceneView.FrameSelected();
            if (request.zoom > 0f)
                sceneView.size = request.zoom;
            ForceRenderAndRepaint(sceneView);

            CameraSnapshot cam = CaptureCameraState(sceneView);
            var data = BuildCameraData(cam);
            data.selected_object = objectName;
            data.bounds_center = boundsCenter;
            data.bounds_extents = boundsExtents;

            return BuildSuccess("EDITOR_CTRL_FRAME_OK",
                $"Framed: {objectName}" + (request.zoom > 0f ? $" (zoom={request.zoom})" : ""),
                data: data);
        }

        private static EditorControlResponse HandleInstantiateToScene(EditorControlRequest request)
        {
            if (string.IsNullOrEmpty(request.asset_path))
                return BuildError("EDITOR_CTRL_MISSING_PATH", "asset_path is required for instantiate_to_scene.");

            var prefab = AssetDatabase.LoadAssetAtPath<GameObject>(request.asset_path);
            if (prefab == null)
                return BuildError("EDITOR_CTRL_ASSET_NOT_FOUND",
                    $"Prefab not found at: {request.asset_path}");

            GameObject instance = (GameObject)PrefabUtility.InstantiatePrefab(prefab);
            if (instance == null)
                return BuildError("EDITOR_CTRL_INSTANTIATE_FAILED",
                    $"Failed to instantiate: {request.asset_path}");

            // Set parent if specified
            if (!string.IsNullOrEmpty(request.hierarchy_path))
            {
                GameObject parent = GameObject.Find(request.hierarchy_path);
                if (parent != null)
                {
                    instance.transform.SetParent(parent.transform, false);
                }
                else
                {
                    UnityEngine.Object.DestroyImmediate(instance);
                    return BuildError("EDITOR_CTRL_PARENT_NOT_FOUND",
                        $"Parent not found: {request.hierarchy_path}");
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
            // Refresh first so Unity sees newly copied/modified C# files
            AssetDatabase.Refresh(ImportAssetOptions.ForceSynchronousImport);
            // Schedule compilation on next frame so that the response JSON
            // is written to disk before domain reload destroys this context.
            EditorApplication.delayCall += () =>
            {
                CompilationPipeline.RequestScriptCompilation();
            };
            return BuildSuccess("EDITOR_CTRL_RECOMPILE_OK",
                "AssetDatabase.Refresh completed; script recompilation scheduled (domain reload will follow)",
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
            if (string.IsNullOrEmpty(request.hierarchy_path))
                return BuildError("EDITOR_CTRL_SET_MATERIAL_NO_PATH", "hierarchy_path is required.");
            if (request.material_index < 0)
                return BuildError("EDITOR_CTRL_SET_MATERIAL_NO_INDEX", "material_index is required (>= 0).");

            // Resolve material GUID from guid or path
            string guid = request.material_guid;
            if (!string.IsNullOrEmpty(request.material_path))
            {
                if (!string.IsNullOrEmpty(guid))
                    return BuildError("EDITOR_CTRL_SET_MATERIAL_CONFLICT",
                        "Cannot specify both material_guid and material_path. Use one.");
                guid = AssetDatabase.AssetPathToGUID(request.material_path);
                if (string.IsNullOrEmpty(guid))
                    return BuildError("EDITOR_CTRL_SET_MATERIAL_NOT_FOUND",
                        $"Material not found at path: {request.material_path}");
            }
            if (string.IsNullOrEmpty(guid))
                return BuildError("EDITOR_CTRL_SET_MATERIAL_NO_GUID",
                    "material_guid or material_path is required.");

            var go = GameObject.Find(request.hierarchy_path);
            if (go == null)
                return BuildError("EDITOR_CTRL_SET_MATERIAL_NOT_FOUND",
                    $"GameObject not found: {request.hierarchy_path}");

            var renderer = go.GetComponent<Renderer>();
            if (renderer == null)
                return BuildError("EDITOR_CTRL_SET_MATERIAL_NO_RENDERER",
                    $"No Renderer on: {request.hierarchy_path}");

            var mats = renderer.sharedMaterials;
            if (request.material_index >= mats.Length)
                return BuildError("EDITOR_CTRL_SET_MATERIAL_INDEX_OOB",
                    $"material_index {request.material_index} out of range (length={mats.Length}).");

            string assetPath = AssetDatabase.GUIDToAssetPath(guid);
            if (string.IsNullOrEmpty(assetPath))
                return BuildError("EDITOR_CTRL_SET_MATERIAL_GUID_NOT_FOUND",
                    $"No asset found for GUID: {guid}");

            var mat = AssetDatabase.LoadAssetAtPath<Material>(assetPath);
            if (mat == null)
                return BuildError("EDITOR_CTRL_SET_MATERIAL_LOAD_FAILED",
                    $"Failed to load Material at: {assetPath}");

            Undo.RecordObject(renderer, $"PrefabSentinel: Set material[{request.material_index}]");
            mats[request.material_index] = mat;
            renderer.sharedMaterials = mats;

            var resp = BuildSuccess("EDITOR_CTRL_SET_MATERIAL_OK",
                $"Set material[{request.material_index}] to {assetPath}",
                data: new EditorControlData { executed = true, read_only = false });
            resp.diagnostics = new[] { new EditorControlDiagnostic
            {
                detail = "Runtime modification — save the scene (File > Save) to persist.",
                evidence = "Undo.RecordObject"
            }};
            return resp;
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

            int maxDepth = Math.Min(Math.Max(request.depth, 1), 50);
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

        // ── Camera helpers ──

        private struct CameraSnapshot
        {
            public float[] position;
            public float[] rotation_quat;
            public float[] euler;
            public float[] pivot;
            public float size;
            public bool orthographic;
        }

        private static CameraSnapshot CaptureCameraState(SceneView sv)
        {
            Vector3 pos = sv.camera.transform.position;
            Quaternion rot = sv.rotation;
            Vector3 e = rot.eulerAngles;
            float yaw = (e.y + 180f) % 360f;
            float pitch = e.x > 180f ? e.x - 360f : e.x;
            return new CameraSnapshot
            {
                position = new[] { pos.x, pos.y, pos.z },
                rotation_quat = new[] { rot.x, rot.y, rot.z, rot.w },
                euler = new[] { yaw, pitch, 0f },
                pivot = new[] { sv.pivot.x, sv.pivot.y, sv.pivot.z },
                size = sv.size,
                orthographic = sv.orthographic
            };
        }

        private static EditorControlData BuildCameraData(CameraSnapshot current, CameraSnapshot? previous = null)
        {
            var data = new EditorControlData
            {
                camera_position = current.position,
                camera_rotation_quat = current.rotation_quat,
                camera_euler = current.euler,
                camera_pivot = current.pivot,
                camera_size = current.size,
                camera_orthographic = current.orthographic,
                executed = true
            };
            if (previous.HasValue)
            {
                var prev = previous.Value;
                data.previous_camera_position = prev.position;
                data.previous_camera_euler = prev.euler;
                data.previous_camera_pivot = prev.pivot;
                data.previous_camera_size = prev.size;
                data.previous_camera_orthographic = prev.orthographic;
            }
            return data;
        }

        /// <summary>
        /// Force GPU rendering + GUI repaint. Works even when Unity is unfocused.
        /// QueuePlayerLoopUpdate forces skinning/physics recalculation,
        /// alwaysRefresh ensures SceneView renders even without focus.
        /// </summary>
        private static void ForceRenderAndRepaint(SceneView sceneView)
        {
            EditorApplication.QueuePlayerLoopUpdate();

            bool wasAlwaysRefresh = sceneView.sceneViewState.alwaysRefresh;
            sceneView.sceneViewState.alwaysRefresh = true;

            sceneView.Repaint();
            SceneView.RepaintAll();
            UnityEditorInternal.InternalEditorUtility.RepaintAllViews();

            EditorApplication.delayCall += () =>
            {
                sceneView.sceneViewState.alwaysRefresh = wasAlwaysRefresh;
                sceneView.Repaint();
                SceneView.RepaintAll();
            };
        }

        // ── Camera handlers ──

        private static EditorControlResponse HandleGetCamera()
        {
            SceneView sceneView = SceneView.lastActiveSceneView;
            if (sceneView == null)
                return BuildError("EDITOR_CTRL_NO_SCENE_VIEW", "No active SceneView found.");

            CameraSnapshot snap = CaptureCameraState(sceneView);
            return BuildSuccess("EDITOR_CTRL_CAMERA_GET_OK",
                $"Camera position=({snap.position[0]:F2}, {snap.position[1]:F2}, {snap.position[2]:F2})",
                data: BuildCameraData(snap));
        }

        private static EditorControlResponse HandleSetCamera(EditorControlRequest request)
        {
            SceneView sceneView = SceneView.lastActiveSceneView;
            if (sceneView == null)
                return BuildError("EDITOR_CTRL_NO_SCENE_VIEW", "No active SceneView found.");

            // Capture previous state before any changes
            CameraSnapshot previous = CaptureCameraState(sceneView);

            bool hasPosition = request.camera_position != null && request.camera_position.Length == 3;
            bool hasLookAt = request.camera_look_at != null && request.camera_look_at.Length == 3;
            bool hasPivot = request.camera_pivot != null && request.camera_pivot.Length == 3;
            bool hasYaw = !float.IsNaN(request.yaw);
            bool hasPitch = !float.IsNaN(request.pitch);
            bool hasDistance = request.distance >= 0f;

            // Conflict checks
            if (hasPosition && hasPivot)
                return BuildError("EDITOR_CTRL_CAMERA_CONFLICT",
                    "Cannot specify both 'position' (camera world coords) and 'pivot' (orbit center). Use one.");
            if (hasLookAt && !hasPosition)
                return BuildError("EDITOR_CTRL_CAMERA_CONFLICT",
                    "'look_at' requires 'position' to be set.");

            // Read current FoV before changes
            float fov = sceneView.camera.fieldOfView;

            if (hasPosition)
            {
                Vector3 cameraPos = new Vector3(
                    request.camera_position[0],
                    request.camera_position[1],
                    request.camera_position[2]);

                if (hasLookAt)
                {
                    // Position + look_at mode
                    Vector3 lookAt = new Vector3(
                        request.camera_look_at[0],
                        request.camera_look_at[1],
                        request.camera_look_at[2]);
                    Vector3 direction = (lookAt - cameraPos).normalized;
                    float dist = Vector3.Distance(cameraPos, lookAt);

                    sceneView.pivot = lookAt;
                    sceneView.rotation = Quaternion.LookRotation(direction);
                    if (sceneView.orthographic)
                        sceneView.size = dist * 0.5f;
                    else
                        sceneView.size = dist * Mathf.Tan(fov * 0.5f * Mathf.Deg2Rad);
                }
                else
                {
                    // Position + yaw/pitch mode: reverse-calculate pivot
                    if (hasDistance)
                        sceneView.size = request.distance;

                    Vector3 currentEuler = sceneView.rotation.eulerAngles;
                    float curYaw = (currentEuler.y + 180f) % 360f;
                    float curPitch = currentEuler.x > 180f ? currentEuler.x - 360f : currentEuler.x;
                    float newYaw = hasYaw ? request.yaw : curYaw;
                    float newPitch = hasPitch ? request.pitch : curPitch;
                    float internalYaw = (newYaw + 180f) % 360f;
                    Quaternion rot = Quaternion.Euler(newPitch, internalYaw, 0f);
                    sceneView.rotation = rot;

                    float cameraDistance;
                    if (sceneView.orthographic)
                        cameraDistance = sceneView.size * 2f;
                    else
                        cameraDistance = sceneView.size / Mathf.Tan(fov * 0.5f * Mathf.Deg2Rad);
                    sceneView.pivot = cameraPos + rot * new Vector3(0, 0, cameraDistance);
                }
            }
            else
            {
                // Pivot orbit mode (unified from old Mode A/B)
                if (hasPivot)
                {
                    sceneView.pivot = new Vector3(
                        request.camera_pivot[0],
                        request.camera_pivot[1],
                        request.camera_pivot[2]);
                }
                if (hasYaw || hasPitch)
                {
                    Vector3 currentEuler = sceneView.rotation.eulerAngles;
                    float curYaw = (currentEuler.y + 180f) % 360f;
                    float curPitch = currentEuler.x > 180f ? currentEuler.x - 360f : currentEuler.x;
                    float newYaw = hasYaw ? request.yaw : curYaw;
                    float newPitch = hasPitch ? request.pitch : curPitch;
                    float internalYaw = (newYaw + 180f) % 360f;
                    sceneView.rotation = Quaternion.Euler(newPitch, internalYaw, 0f);
                }
                if (hasDistance)
                    sceneView.size = request.distance;
            }

            if (request.camera_orthographic >= 0)
                sceneView.orthographic = request.camera_orthographic == 1;

            ForceRenderAndRepaint(sceneView);

            // Return previous + current state
            CameraSnapshot current = CaptureCameraState(sceneView);
            return BuildSuccess("EDITOR_CTRL_CAMERA_SET_OK", "Camera updated",
                data: BuildCameraData(current, previous));
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
                            depth = 0,
                            active = root.activeSelf,
                            tag = root.tag
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
                    depth = 0,
                    active = rootObjects[i].activeSelf,
                    tag = rootObjects[i].tag
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
            if (string.IsNullOrEmpty(request.hierarchy_path))
                return BuildError("EDITOR_CTRL_MISSING_PATH", "hierarchy_path is required for get_material_property.");
            if (request.material_index < 0)
                return BuildError("EDITOR_CTRL_MISSING_INDEX", "material_index is required (>= 0).");

            var go = GameObject.Find(request.hierarchy_path);
            if (go == null)
                return BuildError("EDITOR_CTRL_OBJECT_NOT_FOUND",
                    $"GameObject not found: {request.hierarchy_path}");

            var renderer = go.GetComponent<Renderer>();
            if (renderer == null)
                return BuildError("EDITOR_CTRL_NO_RENDERER",
                    $"No Renderer on: {request.hierarchy_path}");

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
                    $"Property '{request.property_name}' not found on shader '{shader.name}'.",
                    new EditorControlData
                    {
                        suggestions = SuggestSimilar(request.property_name, CollectShaderPropertyNames(shader)),
                    });

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

        private static EditorControlResponse HandleSetMaterialProperty(EditorControlRequest request)
        {
            if (string.IsNullOrEmpty(request.hierarchy_path))
                return BuildError("EDITOR_CTRL_MISSING_PATH", "hierarchy_path is required for set_material_property.");
            if (request.material_index < 0)
                return BuildError("EDITOR_CTRL_MISSING_INDEX", "material_index is required (>= 0).");
            if (string.IsNullOrEmpty(request.property_name))
                return BuildError("EDITOR_CTRL_MISSING_PROPERTY", "property_name is required.");
            if (string.IsNullOrEmpty(request.property_value))
                return BuildError("EDITOR_CTRL_MISSING_VALUE", "property_value is required.");

            var go = GameObject.Find(request.hierarchy_path);
            if (go == null)
                return BuildError("EDITOR_CTRL_OBJECT_NOT_FOUND",
                    $"GameObject not found: {request.hierarchy_path}");

            var renderer = go.GetComponent<Renderer>();
            if (renderer == null)
                return BuildError("EDITOR_CTRL_NO_RENDERER",
                    $"No Renderer on: {request.hierarchy_path}");

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

            Undo.RecordObject(mat, $"Set {request.property_name}");

            var applyError = ApplyMaterialPropertyValue(mat, request.property_name, request.property_value);
            if (applyError != null) return applyError;

            var propType = shader.GetPropertyType(shader.FindPropertyIndex(request.property_name));
            string readBack;
            switch (propType)
            {
                case UnityEngine.Rendering.ShaderPropertyType.Color:
                    readBack = mat.GetColor(request.property_name).ToString();
                    break;
                case UnityEngine.Rendering.ShaderPropertyType.Float:
                case UnityEngine.Rendering.ShaderPropertyType.Range:
                    readBack = mat.GetFloat(request.property_name).ToString("G9");
                    break;
                case UnityEngine.Rendering.ShaderPropertyType.Vector:
                    readBack = mat.GetVector(request.property_name).ToString();
                    break;
                case UnityEngine.Rendering.ShaderPropertyType.Texture:
                    var readTex = mat.GetTexture(request.property_name);
                    readBack = readTex != null ? AssetDatabase.GetAssetPath(readTex) : "(none)";
                    break;
                case UnityEngine.Rendering.ShaderPropertyType.Int:
                    readBack = mat.GetInteger(request.property_name).ToString();
                    break;
                default:
                    readBack = "(unknown)";
                    break;
            }

            SceneView sv = SceneView.lastActiveSceneView;
            if (sv != null) ForceRenderAndRepaint(sv);

            var resp = BuildSuccess("EDITOR_CTRL_SET_MATERIAL_PROPERTY_OK",
                $"Set {request.property_name} on material '{mat.name}'",
                data: new EditorControlData
                {
                    material_properties = new[] { new MaterialPropertyEntry
                    {
                        property_name = request.property_name,
                        property_type = propType.ToString(),
                        value = readBack
                    }},
                    total_entries = 1,
                    executed = true
                });
            resp.diagnostics = new[] { new EditorControlDiagnostic
            {
                detail = "Runtime modification — save the scene (File > Save) to persist.",
                evidence = "Undo.RecordObject"
            }};
            return resp;
        }

        /// <summary>
        /// Apply a single shader property value to a material.
        /// Returns null on success, or an error response on failure.
        /// The caller is responsible for Undo.RecordObject before calling this method.
        /// </summary>
        private static EditorControlResponse ApplyMaterialPropertyValue(
            Material mat, string propertyName, string propertyValue)
        {
            var shader = mat.shader;
            int propIdx = shader.FindPropertyIndex(propertyName);
            if (propIdx < 0)
                return BuildError("EDITOR_CTRL_PROPERTY_NOT_FOUND",
                    $"Property '{propertyName}' not found on shader '{shader.name}'.",
                    new EditorControlData
                    {
                        suggestions = SuggestSimilar(propertyName, CollectShaderPropertyNames(shader)),
                    });

            var propType = shader.GetPropertyType(propIdx);
            string val = propertyValue;

            try
            {
                switch (propType)
                {
                    case UnityEngine.Rendering.ShaderPropertyType.Float:
                    case UnityEngine.Rendering.ShaderPropertyType.Range:
                        mat.SetFloat(propertyName, float.Parse(val, System.Globalization.CultureInfo.InvariantCulture));
                        break;

                    case UnityEngine.Rendering.ShaderPropertyType.Int:
                        mat.SetInteger(propertyName, int.Parse(val, System.Globalization.CultureInfo.InvariantCulture));
                        break;

                    case UnityEngine.Rendering.ShaderPropertyType.Color:
                    {
                        var trimmed = val.Trim('[', ']');
                        var parts = trimmed.Split(',');
                        if (parts.Length != 4)
                            return BuildError("EDITOR_CTRL_PROPERTY_TYPE_MISMATCH",
                                $"Color requires [r,g,b,a], got {parts.Length} components.");
                        mat.SetColor(propertyName, new Color(
                            float.Parse(parts[0].Trim(), System.Globalization.CultureInfo.InvariantCulture),
                            float.Parse(parts[1].Trim(), System.Globalization.CultureInfo.InvariantCulture),
                            float.Parse(parts[2].Trim(), System.Globalization.CultureInfo.InvariantCulture),
                            float.Parse(parts[3].Trim(), System.Globalization.CultureInfo.InvariantCulture)));
                        break;
                    }

                    case UnityEngine.Rendering.ShaderPropertyType.Vector:
                    {
                        var trimmed = val.Trim('[', ']');
                        var parts = trimmed.Split(',');
                        if (parts.Length != 4)
                            return BuildError("EDITOR_CTRL_PROPERTY_TYPE_MISMATCH",
                                $"Vector requires [x,y,z,w], got {parts.Length} components.");
                        mat.SetVector(propertyName, new Vector4(
                            float.Parse(parts[0].Trim(), System.Globalization.CultureInfo.InvariantCulture),
                            float.Parse(parts[1].Trim(), System.Globalization.CultureInfo.InvariantCulture),
                            float.Parse(parts[2].Trim(), System.Globalization.CultureInfo.InvariantCulture),
                            float.Parse(parts[3].Trim(), System.Globalization.CultureInfo.InvariantCulture)));
                        break;
                    }

                    case UnityEngine.Rendering.ShaderPropertyType.Texture:
                    {
                        if (string.IsNullOrEmpty(val))
                        {
                            mat.SetTexture(propertyName, null);
                        }
                        else if (val.StartsWith("guid:"))
                        {
                            string guid = val.Substring(5);
                            string texPath = AssetDatabase.GUIDToAssetPath(guid);
                            if (string.IsNullOrEmpty(texPath))
                                return BuildError("EDITOR_CTRL_PROPERTY_TYPE_MISMATCH",
                                    $"Texture GUID not found: {guid}");
                            var tex = AssetDatabase.LoadAssetAtPath<Texture>(texPath);
                            if (tex == null)
                            {
                                if (texPath.EndsWith(".mat"))
                                    return BuildError("EDITOR_CTRL_SET_MAT_PROP_WRONG_GUID",
                                        $"The specified GUID points to a material asset '{texPath}'. " +
                                        "Please specify a texture GUID instead.");
                                return BuildError("EDITOR_CTRL_PROPERTY_TYPE_MISMATCH",
                                    $"Failed to load texture from GUID '{guid}' (resolved to '{texPath}').");
                            }
                            mat.SetTexture(propertyName, tex);
                        }
                        else if (val.StartsWith("path:"))
                        {
                            string texPath = val.Substring(5);
                            var tex = AssetDatabase.LoadAssetAtPath<Texture>(texPath);
                            if (tex == null)
                                return BuildError("EDITOR_CTRL_PROPERTY_TYPE_MISMATCH",
                                    $"Texture not found at path: {texPath}");
                            mat.SetTexture(propertyName, tex);
                        }
                        else
                        {
                            return BuildError("EDITOR_CTRL_PROPERTY_TYPE_MISMATCH",
                                "Texture value must be 'guid:<hex>', 'path:<asset_path>', or empty string for null.");
                        }
                        break;
                    }

                    default:
                        return BuildError("EDITOR_CTRL_PROPERTY_TYPE_MISMATCH",
                            $"Unsupported property type: {propType}");
                }
            }
            catch (FormatException ex)
            {
                return BuildError("EDITOR_CTRL_PROPERTY_TYPE_MISMATCH",
                    $"Failed to parse value '{val}' for {propType}: {ex.Message}");
            }

            return null;
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
                    depth = currentDepth + 1,
                    active = child.gameObject.activeSelf,
                    tag = child.gameObject.tag
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

        // ── Phase 2: BlendShape + Menu ──

        // Deny-list: menu paths that could destroy data if executed by automation
        private static readonly string[] MenuDenyPrefixes = new string[]
        {
            "File/New Scene",
            "File/New Project",
            "Assets/Delete",
        };

        private static EditorControlResponse HandleGetBlendShapes(EditorControlRequest request)
        {
            if (string.IsNullOrEmpty(request.hierarchy_path))
                return BuildError("EDITOR_CTRL_MISSING_PATH", "hierarchy_path is required for get_blend_shapes");

            var go = GameObject.Find(request.hierarchy_path);
            if (go == null)
                return BuildError("EDITOR_CTRL_OBJECT_NOT_FOUND",
                    $"GameObject not found: {request.hierarchy_path}");

            var smr = go.GetComponent<SkinnedMeshRenderer>();
            if (smr == null)
                return BuildError("EDITOR_CTRL_NO_SMR",
                    $"No SkinnedMeshRenderer on: {request.hierarchy_path}");

            var mesh = smr.sharedMesh;
            if (mesh == null)
                return BuildError("EDITOR_CTRL_NO_SMR",
                    $"SkinnedMeshRenderer has no mesh: {request.hierarchy_path}");

            int count = mesh.blendShapeCount;
            var entries = new System.Collections.Generic.List<BlendShapeEntry>();
            string filter = request.filter ?? "";

            for (int i = 0; i < count; i++)
            {
                string shapeName = mesh.GetBlendShapeName(i);
                if (filter.Length > 0 && shapeName.IndexOf(filter, System.StringComparison.OrdinalIgnoreCase) < 0)
                    continue;
                entries.Add(new BlendShapeEntry
                {
                    index = i,
                    name = shapeName,
                    weight = smr.GetBlendShapeWeight(i),
                });
            }

            return BuildSuccess("EDITOR_CTRL_BLEND_SHAPES_OK",
                $"Found {entries.Count} blend shapes (total: {count})",
                data: new EditorControlData
                {
                    blend_shapes = entries.ToArray(),
                    total_entries = count,
                    renderer_path = GetRelativePath(go.transform, smr.transform),
                    read_only = true,
                    executed = true,
                });
        }

        /// <summary>Returns the relative path from root to target (or target name if same).</summary>
        private static string GetRelativePath(Transform root, Transform target)
        {
            if (root == target) return target.name;
            var parts = new System.Collections.Generic.List<string>();
            var current = target;
            while (current != null && current != root)
            {
                parts.Add(current.name);
                current = current.parent;
            }
            parts.Reverse();
            return string.Join("/", parts);
        }

        private static EditorControlResponse HandleSetBlendShape(EditorControlRequest request)
        {
            if (string.IsNullOrEmpty(request.hierarchy_path))
                return BuildError("EDITOR_CTRL_MISSING_PATH", "hierarchy_path is required for set_blend_shape");
            if (string.IsNullOrEmpty(request.blend_shape_name))
                return BuildError("EDITOR_CTRL_MISSING_PROPERTY", "blend_shape_name is required for set_blend_shape");

            var go = GameObject.Find(request.hierarchy_path);
            if (go == null)
                return BuildError("EDITOR_CTRL_OBJECT_NOT_FOUND",
                    $"GameObject not found: {request.hierarchy_path}");

            var smr = go.GetComponent<SkinnedMeshRenderer>();
            if (smr == null)
                return BuildError("EDITOR_CTRL_NO_SMR",
                    $"No SkinnedMeshRenderer on: {request.hierarchy_path}");

            var mesh = smr.sharedMesh;
            if (mesh == null)
                return BuildError("EDITOR_CTRL_NO_SMR",
                    $"SkinnedMeshRenderer has no mesh: {request.hierarchy_path}");

            int index = mesh.GetBlendShapeIndex(request.blend_shape_name);
            if (index < 0)
                return BuildError("EDITOR_CTRL_BLENDSHAPE_NOT_FOUND",
                    $"BlendShape not found: {request.blend_shape_name}");

            float before = smr.GetBlendShapeWeight(index);
            float weight = Mathf.Clamp(request.blend_shape_weight, 0f, 100f);

            Undo.RecordObject(smr, $"Set BlendShape {request.blend_shape_name}");
            smr.SetBlendShapeWeight(index, weight);

            SceneView sv = SceneView.lastActiveSceneView;
            if (sv != null) ForceRenderAndRepaint(sv);

            var resp = BuildSuccess("EDITOR_CTRL_BLEND_SHAPE_SET_OK",
                $"BlendShape '{request.blend_shape_name}' set from {before} to {weight}",
                data: new EditorControlData
                {
                    blend_shape_index = index,
                    blend_shape_name = request.blend_shape_name,
                    blend_shape_before = before,
                    blend_shape_after = weight,
                    executed = true,
                });
            resp.diagnostics = new[] { new EditorControlDiagnostic
            {
                detail = "Runtime modification — save the scene (File > Save) to persist.",
                evidence = "Undo.RecordObject"
            }};
            return resp;
        }

        // ── Material reverse lookup ──

        private static EditorControlResponse HandleFindRenderersByMaterial(EditorControlRequest request)
        {
            // Resolve material GUID from guid or path
            string guid = request.material_guid;
            if (!string.IsNullOrEmpty(request.material_path))
            {
                if (!string.IsNullOrEmpty(guid))
                    return BuildError("EDITOR_CTRL_FIND_RENDERERS_CONFLICT",
                        "Cannot specify both material_guid and material_path. Use one.");
                guid = AssetDatabase.AssetPathToGUID(request.material_path);
                if (string.IsNullOrEmpty(guid))
                    return BuildError("EDITOR_CTRL_MATERIAL_NOT_FOUND",
                        $"Material not found at path: {request.material_path}");
            }
            if (string.IsNullOrEmpty(guid))
                return BuildError("EDITOR_CTRL_MATERIAL_NOT_FOUND",
                    "material_guid or material_path is required.");

            string targetPath = AssetDatabase.GUIDToAssetPath(guid);
            if (string.IsNullOrEmpty(targetPath))
                return BuildError("EDITOR_CTRL_MATERIAL_NOT_FOUND",
                    $"No asset found for GUID: {guid}");

            var renderers = UnityEngine.Object.FindObjectsOfType<Renderer>();
            var matches = new System.Collections.Generic.List<MaterialSlotEntry>();
            foreach (var renderer in renderers)
            {
                var mats = renderer.sharedMaterials;
                for (int i = 0; i < mats.Length; i++)
                {
                    if (mats[i] == null) continue;
                    string matPath = AssetDatabase.GetAssetPath(mats[i]);
                    if (matPath == targetPath)
                    {
                        matches.Add(new MaterialSlotEntry
                        {
                            renderer_path = GetHierarchyPath(renderer.transform),
                            renderer_type = renderer.GetType().Name,
                            slot_index = i,
                            material_name = mats[i].name,
                            material_asset_path = matPath,
                            material_guid = guid,
                        });
                    }
                }
            }

            return BuildSuccess("EDITOR_CTRL_FIND_RENDERERS_OK",
                $"Found {matches.Count} slot(s) using material across {renderers.Length} renderers",
                data: new EditorControlData
                {
                    material_slots = matches.ToArray(),
                    total_entries = renderers.Length,
                    executed = true,
                });
        }

        // ── Phase 4: Rename + AddComponent + Udon ──

        private static EditorControlResponse HandleEditorRename(EditorControlRequest request)
        {
            if (string.IsNullOrEmpty(request.hierarchy_path))
                return BuildError("EDITOR_CTRL_RENAME_NO_PATH", "hierarchy_path is required.");
            if (string.IsNullOrEmpty(request.new_name))
                return BuildError("EDITOR_CTRL_RENAME_NO_NAME", "new_name is required.");

            var go = GameObject.Find(request.hierarchy_path);
            if (go == null)
                return BuildError("EDITOR_CTRL_RENAME_NOT_FOUND",
                    $"GameObject not found: {request.hierarchy_path}");

            string oldName = go.name;
            Undo.RecordObject(go, $"PrefabSentinel: Rename {oldName}");
            go.name = request.new_name;

            var resp = BuildSuccess("EDITOR_CTRL_RENAME_OK",
                $"Renamed '{oldName}' to '{request.new_name}'",
                data: new EditorControlData
                {
                    selected_object = request.new_name,
                    executed = true,
                    read_only = false,
                });
            resp.diagnostics = new[] { new EditorControlDiagnostic
            {
                detail = "Runtime modification — save the scene (File > Save) to persist.",
                evidence = "Undo.RecordObject"
            }};
            return resp;
        }

        private static EditorControlResponse HandleEditorSetParent(EditorControlRequest request)
        {
            if (string.IsNullOrEmpty(request.hierarchy_path))
                return BuildError("EDITOR_CTRL_SET_PARENT_NO_PATH", "hierarchy_path is required.");

            var child = GameObject.Find(request.hierarchy_path);
            if (child == null)
                return BuildError("EDITOR_CTRL_SET_PARENT_NOT_FOUND",
                    $"GameObject not found: {request.hierarchy_path}");

            Transform newParent = null;
            if (!string.IsNullOrEmpty(request.new_name))
            {
                var parentGo = GameObject.Find(request.new_name);
                if (parentGo == null)
                    return BuildError("EDITOR_CTRL_SET_PARENT_PARENT_NOT_FOUND",
                        $"Parent GameObject not found: {request.new_name}");
                newParent = parentGo.transform;
            }

            Undo.SetTransformParent(child.transform, newParent,
                $"PrefabSentinel: SetParent {child.name}");

            string parentName = newParent != null ? newParent.name : "(scene root)";
            var resp = BuildSuccess("EDITOR_CTRL_SET_PARENT_OK",
                $"Moved '{child.name}' under '{parentName}'",
                data: new EditorControlData
                {
                    selected_object = child.name,
                    executed = true,
                    read_only = false,
                });
            resp.diagnostics = new[] { new EditorControlDiagnostic
            {
                detail = "Runtime modification — save the scene (File > Save) to persist.",
                evidence = "Undo.SetTransformParent"
            }};
            return resp;
        }

        /// <summary>Apply a string value to a SerializedProperty based on its type (best-effort).</summary>
        private static bool ApplyPropertyValue(SerializedProperty prop, string v)
        {
            var ci = System.Globalization.CultureInfo.InvariantCulture;
            switch (prop.propertyType)
            {
                case SerializedPropertyType.Integer:
                    if (int.TryParse(v, System.Globalization.NumberStyles.Integer, ci, out int iv))
                    { prop.intValue = iv; return true; }
                    return false;
                case SerializedPropertyType.Float:
                    if (float.TryParse(v, System.Globalization.NumberStyles.Float, ci, out float fv))
                    { prop.floatValue = fv; return true; }
                    return false;
                case SerializedPropertyType.Boolean:
                    if (bool.TryParse(v, out bool bv))
                    { prop.boolValue = bv; return true; }
                    return false;
                case SerializedPropertyType.String:
                    prop.stringValue = v; return true;
                case SerializedPropertyType.Enum:
                {
#pragma warning disable 0618
                    int idx = System.Array.IndexOf(prop.enumNames, v);
#pragma warning restore 0618
                    if (idx >= 0) { prop.enumValueIndex = idx; return true; }
                    if (int.TryParse(v, out int ei)) { prop.enumValueIndex = ei; return true; }
                    return false;
                }
                case SerializedPropertyType.Vector3:
                {
                    var parts = v.Split(',');
                    if (parts.Length >= 3
                        && float.TryParse(parts[0].Trim(), System.Globalization.NumberStyles.Float, ci, out float x)
                        && float.TryParse(parts[1].Trim(), System.Globalization.NumberStyles.Float, ci, out float y)
                        && float.TryParse(parts[2].Trim(), System.Globalization.NumberStyles.Float, ci, out float z))
                    { prop.vector3Value = new Vector3(x, y, z); return true; }
                    return false;
                }
                case SerializedPropertyType.Color:
                {
                    var parts = v.Split(',');
                    if (parts.Length < 3) return false;
                    if (!float.TryParse(parts[0].Trim(), System.Globalization.NumberStyles.Float, ci, out float r)
                        || !float.TryParse(parts[1].Trim(), System.Globalization.NumberStyles.Float, ci, out float g)
                        || !float.TryParse(parts[2].Trim(), System.Globalization.NumberStyles.Float, ci, out float b))
                        return false;
                    float a = 1f;
                    if (parts.Length >= 4
                        && float.TryParse(parts[3].Trim(), System.Globalization.NumberStyles.Float, ci, out float aParsed))
                        a = aParsed;
                    prop.colorValue = new Color(r, g, b, a);
                    return true;
                }
                case SerializedPropertyType.Vector2:
                {
                    var parts = v.Split(',');
                    if (parts.Length >= 2
                        && float.TryParse(parts[0].Trim(), System.Globalization.NumberStyles.Float, ci, out float x)
                        && float.TryParse(parts[1].Trim(), System.Globalization.NumberStyles.Float, ci, out float y))
                    { prop.vector2Value = new Vector2(x, y); return true; }
                    return false;
                }
                case SerializedPropertyType.Vector4:
                {
                    var parts = v.Split(',');
                    if (parts.Length >= 4
                        && float.TryParse(parts[0].Trim(), System.Globalization.NumberStyles.Float, ci, out float x)
                        && float.TryParse(parts[1].Trim(), System.Globalization.NumberStyles.Float, ci, out float y)
                        && float.TryParse(parts[2].Trim(), System.Globalization.NumberStyles.Float, ci, out float z)
                        && float.TryParse(parts[3].Trim(), System.Globalization.NumberStyles.Float, ci, out float w))
                    { prop.vector4Value = new Vector4(x, y, z, w); return true; }
                    return false;
                }
                case SerializedPropertyType.ObjectReference:
                {
                    var (obj, _) = ResolveObjectReference(v);
                    if (obj != null)
                    { prop.objectReferenceValue = obj; return true; }
                    return false;
                }
                case SerializedPropertyType.ArraySize:
                case SerializedPropertyType.FixedBufferSize:
                    if (int.TryParse(v, System.Globalization.NumberStyles.Integer, ci, out int av))
                    { prop.intValue = av; return true; }
                    return false;
                default: return false;
            }
        }

        // ── Phase 6: Batch Operations + Scene ──

        private static bool TryParseVector3(string csv, out Vector3 result)
        {
            result = Vector3.zero;
            if (string.IsNullOrEmpty(csv)) return false;
            var parts = csv.Split(',');
            if (parts.Length < 3) return false;
            var ci = System.Globalization.CultureInfo.InvariantCulture;
            return float.TryParse(parts[0].Trim(), System.Globalization.NumberStyles.Float, ci, out result.x)
                && float.TryParse(parts[1].Trim(), System.Globalization.NumberStyles.Float, ci, out result.y)
                && float.TryParse(parts[2].Trim(), System.Globalization.NumberStyles.Float, ci, out result.z);
        }

        private static EditorControlResponse HandleEditorCreateEmpty(EditorControlRequest request)
        {
            if (string.IsNullOrEmpty(request.new_name))
                return BuildError("EDITOR_CTRL_CREATE_EMPTY_NO_NAME", "new_name (object name) is required.");

            // Validate parent before creating to avoid leaked GameObjects
            Transform parentTransform = null;
            if (!string.IsNullOrEmpty(request.hierarchy_path))
            {
                var parentGo = GameObject.Find(request.hierarchy_path);
                if (parentGo == null)
                    return BuildError("EDITOR_CTRL_CREATE_EMPTY_PARENT_NOT_FOUND",
                        $"Parent not found: {request.hierarchy_path}");
                parentTransform = parentGo.transform;
            }

            var go = new GameObject(request.new_name);
            Undo.RegisterCreatedObjectUndo(go, $"PrefabSentinel: Create {request.new_name}");

            if (parentTransform != null)
                Undo.SetTransformParent(go.transform, parentTransform,
                    $"PrefabSentinel: SetParent {request.new_name}");

            if (TryParseVector3(request.property_value, out Vector3 pos))
                go.transform.localPosition = pos;

            string path = GetHierarchyPath(go.transform);
            var resp = BuildSuccess("EDITOR_CTRL_CREATE_EMPTY_OK",
                $"Created empty GameObject '{request.new_name}' at {path}",
                data: new EditorControlData
                {
                    selected_object = request.new_name,
                    output_path = path,
                    executed = true,
                    read_only = false,
                });
            resp.diagnostics = new[] { new EditorControlDiagnostic
            {
                detail = "Runtime modification — save the scene (File > Save) to persist.",
                evidence = "Undo.RegisterCreatedObjectUndo"
            }};
            return resp;
        }

        private static EditorControlResponse HandleEditorCreatePrimitive(EditorControlRequest request)
        {
            if (string.IsNullOrEmpty(request.primitive_type))
                return BuildError("EDITOR_CTRL_CREATE_PRIM_NO_TYPE", "primitive_type is required.");

            PrimitiveType primType;
            try
            {
                primType = (PrimitiveType)System.Enum.Parse(typeof(PrimitiveType), request.primitive_type, true);
            }
            catch (System.ArgumentException)
            {
                return BuildError("EDITOR_CTRL_CREATE_PRIM_BAD_TYPE",
                    $"Invalid primitive_type: {request.primitive_type}. " +
                    "Valid: Cube, Sphere, Cylinder, Capsule, Plane, Quad.");
            }

            // Validate parent before creating to avoid leaked GameObjects
            Transform parentTransform = null;
            if (!string.IsNullOrEmpty(request.hierarchy_path))
            {
                var parentGo = GameObject.Find(request.hierarchy_path);
                if (parentGo == null)
                    return BuildError("EDITOR_CTRL_CREATE_PRIM_PARENT_NOT_FOUND",
                        $"Parent not found: {request.hierarchy_path}");
                parentTransform = parentGo.transform;
            }

            var go = GameObject.CreatePrimitive(primType);
            Undo.RegisterCreatedObjectUndo(go, $"PrefabSentinel: Create {request.primitive_type}");

            if (!string.IsNullOrEmpty(request.new_name))
            {
                Undo.RecordObject(go, $"PrefabSentinel: Rename {go.name}");
                go.name = request.new_name;
            }

            if (parentTransform != null)
                Undo.SetTransformParent(go.transform, parentTransform,
                    $"PrefabSentinel: SetParent {go.name}");

            if (TryParseVector3(request.property_value, out Vector3 position))
                go.transform.localPosition = position;
            if (TryParseVector3(request.scale, out Vector3 scl))
                go.transform.localScale = scl;
            if (TryParseVector3(request.rotation, out Vector3 rot))
                go.transform.localEulerAngles = rot;

            string primPath = GetHierarchyPath(go.transform);
            var resp = BuildSuccess("EDITOR_CTRL_CREATE_PRIM_OK",
                $"Created {request.primitive_type} '{go.name}' at {primPath}",
                data: new EditorControlData
                {
                    selected_object = go.name,
                    output_path = primPath,
                    executed = true,
                    read_only = false,
                });
            resp.diagnostics = new[] { new EditorControlDiagnostic
            {
                detail = "Runtime modification — save the scene (File > Save) to persist.",
                evidence = "Undo.RegisterCreatedObjectUndo"
            }};
            return resp;
        }

        private static EditorControlResponse HandleEditorBatchCreate(EditorControlRequest request)
        {
            if (string.IsNullOrEmpty(request.batch_objects_json))
                return BuildError("EDITOR_CTRL_BATCH_CREATE_NO_DATA", "batch_objects_json is required.");

            BatchObjectArray wrapper;
            try
            {
                wrapper = JsonUtility.FromJson<BatchObjectArray>(
                    "{\"items\":" + request.batch_objects_json + "}");
            }
            catch (System.Exception ex)
            {
                return BuildError("EDITOR_CTRL_BATCH_CREATE_JSON_ERROR",
                    $"Failed to parse batch_objects_json: {ex.Message}");
            }

            if (wrapper.items == null || wrapper.items.Length == 0)
                return BuildError("EDITOR_CTRL_BATCH_CREATE_EMPTY", "batch_objects_json is empty.");

            int undoGroup = Undo.GetCurrentGroup();
            Undo.SetCurrentGroupName("PrefabSentinel: Batch Create");

            var createdPaths = new System.Collections.Generic.List<string>();
            var warnings = new System.Collections.Generic.List<EditorControlDiagnostic>();

            foreach (var spec in wrapper.items)
            {
                GameObject go;
                if (!string.IsNullOrEmpty(spec.type) && !string.Equals(spec.type, "Empty", System.StringComparison.OrdinalIgnoreCase))
                {
                    try
                    {
                        var primType = (PrimitiveType)System.Enum.Parse(typeof(PrimitiveType), spec.type, true);
                        go = GameObject.CreatePrimitive(primType);
                    }
                    catch (System.ArgumentException)
                    {
                        Undo.CollapseUndoOperations(undoGroup);
                        return BuildError("EDITOR_CTRL_BATCH_CREATE_BAD_TYPE",
                            $"Invalid type at index {createdPaths.Count}: {spec.type}. " +
                            "Valid: Cube, Sphere, Cylinder, Capsule, Plane, Quad, Empty.");
                    }
                }
                else
                {
                    go = new GameObject("GameObject");
                }
                Undo.RegisterCreatedObjectUndo(go, $"PrefabSentinel: Create {spec.name}");

                if (!string.IsNullOrEmpty(spec.name))
                    go.name = spec.name;

                if (!string.IsNullOrEmpty(spec.parent))
                {
                    var parent = GameObject.Find(spec.parent);
                    if (parent != null)
                        Undo.SetTransformParent(go.transform, parent.transform,
                            $"PrefabSentinel: SetParent {go.name}");
                    else
                        warnings.Add(new EditorControlDiagnostic
                        {
                            path = spec.parent,
                            location = $"batch item index {createdPaths.Count}",
                            detail = $"Parent not found: {spec.parent}. Object '{go.name}' created at scene root.",
                            evidence = "GameObject.Find returned null"
                        });
                }

                if (TryParseVector3(spec.position, out Vector3 pos))
                    go.transform.localPosition = pos;
                if (TryParseVector3(spec.scale, out Vector3 scl))
                    go.transform.localScale = scl;
                if (TryParseVector3(spec.rotation, out Vector3 rot))
                    go.transform.localEulerAngles = rot;

                if (spec.components != null)
                {
                    foreach (var compTypeName in spec.components)
                    {
                        if (string.IsNullOrEmpty(compTypeName)) continue;
                        var compType = ResolveComponentType(compTypeName);
                        if (compType == null)
                        {
                            warnings.Add(new EditorControlDiagnostic
                            {
                                path = GetHierarchyPath(go.transform),
                                location = $"batch item index {createdPaths.Count}",
                                detail = $"Component type not found: {compTypeName}. Skipped.",
                                evidence = "ResolveComponentType returned null"
                            });
                            continue;
                        }
                        Undo.AddComponent(go, compType);
                    }
                }

                createdPaths.Add(GetHierarchyPath(go.transform));
            }

            Undo.CollapseUndoOperations(undoGroup);

            var batchCreateResp = BuildSuccess("EDITOR_CTRL_BATCH_CREATE_OK",
                $"Created {createdPaths.Count} objects",
                data: new EditorControlData
                {
                    executed = true,
                    read_only = false,
                    suggestions = createdPaths.ToArray(),
                });
            var diagList = new System.Collections.Generic.List<EditorControlDiagnostic>(warnings);
            diagList.Add(new EditorControlDiagnostic
            {
                detail = "Runtime modification — save the scene (File > Save) to persist.",
                evidence = "Undo.CollapseUndoOperations"
            });
            batchCreateResp.diagnostics = diagList.ToArray();
            if (warnings.Count > 0)
                batchCreateResp.severity = "warning";
            return batchCreateResp;
        }

        private static EditorControlResponse HandleEditorBatchSetProperty(EditorControlRequest request)
        {
            if (string.IsNullOrEmpty(request.batch_operations_json))
                return BuildError("EDITOR_CTRL_BATCH_SET_NO_DATA", "batch_operations_json is required.");

            BatchSetPropertyArray wrapper;
            try
            {
                wrapper = JsonUtility.FromJson<BatchSetPropertyArray>(
                    "{\"items\":" + request.batch_operations_json + "}");
            }
            catch (System.Exception ex)
            {
                return BuildError("EDITOR_CTRL_BATCH_SET_JSON_ERROR",
                    $"Failed to parse batch_operations_json: {ex.Message}");
            }

            if (wrapper.items == null || wrapper.items.Length == 0)
                return BuildError("EDITOR_CTRL_BATCH_SET_EMPTY", "batch_operations_json is empty.");

            int undoGroup = Undo.GetCurrentGroup();
            Undo.SetCurrentGroupName("PrefabSentinel: Batch SetProperty");

            var results = new System.Collections.Generic.List<string>();

            foreach (var op in wrapper.items)
            {
                var subReq = new EditorControlRequest
                {
                    action = "editor_set_property",
                    hierarchy_path = op.hierarchy_path,
                    component_type = op.component_type,
                    property_name = op.property_name,
                    property_value = op.value,
                    object_reference = op.object_reference,
                };
                var subResp = HandleEditorSetProperty(subReq);
                if (!subResp.success)
                {
                    Undo.CollapseUndoOperations(undoGroup);
                    return BuildError("EDITOR_CTRL_BATCH_SET_FAILED",
                        $"Operation failed at index {results.Count}: {subResp.message}");
                }
                results.Add($"{op.hierarchy_path}/{op.component_type}.{op.property_name}");
            }

            Undo.CollapseUndoOperations(undoGroup);

            var batchSetResp = BuildSuccess("EDITOR_CTRL_BATCH_SET_OK",
                $"Set {results.Count} properties",
                data: new EditorControlData
                {
                    executed = true,
                    read_only = false,
                    suggestions = results.ToArray(),
                });
            batchSetResp.diagnostics = new[] { new EditorControlDiagnostic
            {
                detail = "Runtime modification — save the scene (File > Save) to persist.",
                evidence = "Undo.CollapseUndoOperations"
            }};
            return batchSetResp;
        }

        private static EditorControlResponse HandleEditorBatchSetMaterialProperty(EditorControlRequest request)
        {
            if (string.IsNullOrEmpty(request.batch_operations_json))
                return BuildError("EDITOR_CTRL_BATCH_SET_MAT_PROP_NO_DATA",
                    "batch_operations_json is required.");

            BatchSetMaterialPropertyArray wrapper;
            try
            {
                wrapper = JsonUtility.FromJson<BatchSetMaterialPropertyArray>(
                    "{\"items\":" + request.batch_operations_json + "}");
            }
            catch (System.Exception ex)
            {
                return BuildError("EDITOR_CTRL_BATCH_SET_MAT_PROP_JSON_ERROR",
                    $"Failed to parse batch_operations_json: {ex.Message}");
            }

            if (wrapper.items == null || wrapper.items.Length == 0)
                return BuildError("EDITOR_CTRL_BATCH_SET_MAT_PROP_EMPTY",
                    "batch_operations_json is empty.");

            bool hasHierarchy = !string.IsNullOrEmpty(request.hierarchy_path);
            bool hasMatPath = !string.IsNullOrEmpty(request.material_path);
            bool hasMatGuid = !string.IsNullOrEmpty(request.material_guid);

            if (hasHierarchy && (hasMatPath || hasMatGuid))
                return BuildError("EDITOR_CTRL_BATCH_SET_MAT_PROP_CONFLICT",
                    "Cannot specify both hierarchy_path and material_path/material_guid. Use one targeting mode.");

            if (!hasHierarchy && !hasMatPath && !hasMatGuid)
                return BuildError("EDITOR_CTRL_BATCH_SET_MAT_PROP_NO_TARGET",
                    "Specify a target: hierarchy_path + material_index, or material_path, or material_guid.");

            int undoGroup = Undo.GetCurrentGroup();
            Undo.SetCurrentGroupName("PrefabSentinel: Batch SetMaterialProperty");

            var results = new System.Collections.Generic.List<string>();

            if (hasHierarchy)
            {
                foreach (var op in wrapper.items)
                {
                    var subReq = new EditorControlRequest
                    {
                        action = "set_material_property",
                        hierarchy_path = request.hierarchy_path,
                        material_index = request.material_index,
                        property_name = op.name,
                        property_value = op.value,
                    };
                    var subResp = HandleSetMaterialProperty(subReq);
                    if (!subResp.success)
                    {
                        Undo.CollapseUndoOperations(undoGroup);
                        return BuildError("EDITOR_CTRL_BATCH_SET_MAT_PROP_FAILED",
                            $"Operation failed at index {results.Count}: {subResp.message}");
                    }
                    results.Add(op.name);
                }
            }
            else
            {
                if (hasMatPath && hasMatGuid)
                    return BuildError("EDITOR_CTRL_BATCH_SET_MAT_PROP_MAT_CONFLICT",
                        "Cannot specify both material_path and material_guid. Use one.");

                string guid = request.material_guid;
                if (hasMatPath)
                {
                    guid = AssetDatabase.AssetPathToGUID(request.material_path);
                    if (string.IsNullOrEmpty(guid))
                        return BuildError("EDITOR_CTRL_BATCH_SET_MAT_PROP_NOT_FOUND",
                            $"Material not found at path: {request.material_path}");
                }

                string assetPath = AssetDatabase.GUIDToAssetPath(guid);
                if (string.IsNullOrEmpty(assetPath))
                    return BuildError("EDITOR_CTRL_BATCH_SET_MAT_PROP_NOT_FOUND",
                        $"No asset found for GUID: {guid}");

                var mat = AssetDatabase.LoadAssetAtPath<Material>(assetPath);
                if (mat == null)
                    return BuildError("EDITOR_CTRL_BATCH_SET_MAT_PROP_NOT_FOUND",
                        $"Failed to load Material at: {assetPath}");

                var shader = mat.shader;
                if (shader == null)
                    return BuildError("EDITOR_CTRL_BATCH_SET_MAT_PROP_NOT_FOUND",
                        $"Material '{mat.name}' has no shader assigned.");

                Undo.RecordObject(mat, "PrefabSentinel: Batch SetMaterialProperty");

                foreach (var op in wrapper.items)
                {
                    var applyError = ApplyMaterialPropertyValue(mat, op.name, op.value);
                    if (applyError != null)
                    {
                        Undo.CollapseUndoOperations(undoGroup);
                        return BuildError("EDITOR_CTRL_BATCH_SET_MAT_PROP_FAILED",
                            $"Operation failed at index {results.Count}: {applyError.message}");
                    }
                    results.Add(op.name);
                }

                SceneView sv = SceneView.lastActiveSceneView;
                if (sv != null) ForceRenderAndRepaint(sv);
            }

            Undo.CollapseUndoOperations(undoGroup);

            var resp = BuildSuccess("EDITOR_CTRL_BATCH_SET_MAT_PROP_OK",
                $"Set {results.Count} material properties",
                data: new EditorControlData
                {
                    executed = true, read_only = false,
                    suggestions = results.ToArray(),
                });
            resp.diagnostics = new[] { new EditorControlDiagnostic
            {
                detail = "Runtime modification — save the scene (File > Save) to persist.",
                evidence = "Undo.CollapseUndoOperations"
            }};
            return resp;
        }

        private static EditorControlResponse HandleEditorOpenScene(EditorControlRequest request)
        {
            if (string.IsNullOrEmpty(request.asset_path))
                return BuildError("EDITOR_CTRL_OPEN_SCENE_NO_PATH", "asset_path is required.");

            if (!System.IO.File.Exists(request.asset_path))
                return BuildError("EDITOR_CTRL_OPEN_SCENE_NOT_FOUND",
                    $"Scene file not found: {request.asset_path}");

            var mode = string.Equals(request.open_scene_mode, "additive",
                System.StringComparison.OrdinalIgnoreCase)
                ? OpenSceneMode.Additive
                : OpenSceneMode.Single;

            var scene = EditorSceneManager.OpenScene(request.asset_path, mode);

            return BuildSuccess("EDITOR_CTRL_OPEN_SCENE_OK",
                $"Opened scene: {request.asset_path} ({request.open_scene_mode})",
                data: new EditorControlData
                {
                    asset_path = request.asset_path,
                    output_path = scene.name,
                    executed = true,
                });
        }

        private static EditorControlResponse HandleEditorSaveScene(EditorControlRequest request)
        {
            if (!string.IsNullOrEmpty(request.asset_path))
            {
                var scene = SceneManager.GetActiveScene();
                bool ok = EditorSceneManager.SaveScene(scene, request.asset_path);
                if (!ok)
                    return BuildError("EDITOR_CTRL_SAVE_SCENE_FAILED",
                        $"Failed to save scene to: {request.asset_path}");
                return BuildSuccess("EDITOR_CTRL_SAVE_SCENE_OK",
                    $"Saved scene to: {request.asset_path}",
                    data: new EditorControlData
                    {
                        asset_path = request.asset_path,
                        executed = true,
                    });
            }
            else
            {
                bool ok = EditorSceneManager.SaveOpenScenes();
                if (!ok)
                    return BuildError("EDITOR_CTRL_SAVE_SCENE_FAILED",
                        "Failed to save open scenes.");
                var scene = SceneManager.GetActiveScene();
                return BuildSuccess("EDITOR_CTRL_SAVE_SCENE_OK",
                    $"Saved all open scenes (active: {scene.name})",
                    data: new EditorControlData
                    {
                        asset_path = scene.path,
                        executed = true,
                    });
            }
        }

        private static System.Type ResolveComponentType(string typeName)
        {
            // 1. Fully qualified name (fastest path)
            var t = System.Type.GetType(typeName);
            if (t != null && typeof(Component).IsAssignableFrom(t))
                return t;

            // 2. Search all loaded assemblies by full name
            foreach (var asm in System.AppDomain.CurrentDomain.GetAssemblies())
            {
                t = asm.GetType(typeName);
                if (t != null && typeof(Component).IsAssignableFrom(t))
                    return t;
            }

            // 3. Search all loaded assemblies by simple name (handles short names
            //    like "BoxCollider" that live in UnityEngine.PhysicsModule etc.)
            //    First match wins; use fully qualified name to disambiguate.
            foreach (var asm in System.AppDomain.CurrentDomain.GetAssemblies())
            {
                try
                {
                    foreach (var type in asm.GetExportedTypes())
                    {
                        if (type.Name == typeName && typeof(Component).IsAssignableFrom(type))
                            return type;
                    }
                }
                catch (System.Reflection.ReflectionTypeLoadException) { }
            }

            return null;
        }

        private static EditorControlResponse HandleEditorAddComponent(EditorControlRequest request)
        {
            if (string.IsNullOrEmpty(request.hierarchy_path))
                return BuildError("EDITOR_CTRL_ADD_COMP_NO_PATH", "hierarchy_path is required.");
            if (string.IsNullOrEmpty(request.component_type))
                return BuildError("EDITOR_CTRL_ADD_COMP_NO_TYPE", "component_type is required.");

            var go = GameObject.Find(request.hierarchy_path);
            if (go == null)
                return BuildError("EDITOR_CTRL_ADD_COMP_NOT_FOUND",
                    $"GameObject not found: {request.hierarchy_path}");

            System.Type compType = ResolveComponentType(request.component_type);
            if (compType == null)
                return BuildError("EDITOR_CTRL_ADD_COMP_TYPE_NOT_FOUND",
                    $"Component type not found: {request.component_type}. " +
                    "Short names (e.g. 'BoxCollider') and fully qualified names both work.");

            var added = Undo.AddComponent(go, compType);
            if (added == null)
                return BuildError("EDITOR_CTRL_ADD_COMP_FAILED",
                    $"Failed to add component: {request.component_type}");

            // Apply initial properties if provided
            if (!string.IsNullOrEmpty(request.properties_json))
            {
                try
                {
                    var propWrapper = JsonUtility.FromJson<PropertyEntryArray>(
                        "{\"items\":" + request.properties_json + "}");
                    if (propWrapper.items != null)
                    {
                        var so = new SerializedObject(added);
                        foreach (var entry in propWrapper.items)
                        {
                            var prop = so.FindProperty(entry.name);
                            if (prop == null) continue;
                            if (!string.IsNullOrEmpty(entry.object_reference))
                            {
                                var (obj, _) = ResolveObjectReference(entry.object_reference);
                                if (obj != null) prop.objectReferenceValue = obj;
                            }
                            else if (!string.IsNullOrEmpty(entry.value))
                            {
                                ApplyPropertyValue(prop, entry.value);
                            }
                        }
                        so.ApplyModifiedProperties();
                    }
                }
                catch (System.Exception) { /* best-effort; component already added */ }
            }

            var resp = BuildSuccess("EDITOR_CTRL_ADD_COMP_OK",
                $"Added {compType.FullName} to {request.hierarchy_path}",
                data: new EditorControlData
                {
                    selected_object = go.name,
                    asset_path = compType.FullName,
                    executed = true,
                    read_only = false,
                });
            resp.diagnostics = new[] { new EditorControlDiagnostic
            {
                detail = "Runtime modification — save the scene (File > Save) to persist.",
                evidence = "Undo.AddComponent"
            }};
            return resp;
        }

        private static EditorControlResponse HandleEditorRemoveComponent(EditorControlRequest request)
        {
            if (string.IsNullOrEmpty(request.hierarchy_path))
                return BuildError("EDITOR_CTRL_REM_COMP_NO_PATH", "hierarchy_path is required.");
            if (string.IsNullOrEmpty(request.component_type))
                return BuildError("EDITOR_CTRL_REM_COMP_NO_TYPE", "component_type is required.");

            var go = GameObject.Find(request.hierarchy_path);
            if (go == null)
                return BuildError("EDITOR_CTRL_REM_COMP_NOT_FOUND",
                    $"GameObject not found: {request.hierarchy_path}");

            System.Type compType = ResolveComponentType(request.component_type);
            if (compType == null)
                return BuildError("EDITOR_CTRL_REM_COMP_TYPE_NOT_FOUND",
                    $"Component type not found: {request.component_type}. " +
                    "Short names (e.g. 'BoxCollider') and fully qualified names both work.");

            var components = go.GetComponents(compType);
            if (components.Length == 0)
                return BuildError("EDITOR_CTRL_REM_COMP_NONE",
                    $"No {request.component_type} component found on {request.hierarchy_path}");

            Component target;
            if (request.component_index == -1)
            {
                if (components.Length == 1)
                {
                    target = components[0];
                }
                else
                {
                    return BuildError("EDITOR_CTRL_REM_COMP_AMBIGUOUS",
                        $"Found {components.Length} {request.component_type} components on {request.hierarchy_path}. " +
                        $"Specify index (0-{components.Length - 1}) to select.",
                        new EditorControlData { component_count = components.Length });
                }
            }
            else
            {
                if (request.component_index < 0 || request.component_index >= components.Length)
                    return BuildError("EDITOR_CTRL_REM_COMP_INDEX_OUT_OF_RANGE",
                        $"index {request.component_index} out of range. " +
                        $"{request.hierarchy_path} has {components.Length} {request.component_type} component(s) " +
                        $"(valid: 0-{components.Length - 1}).",
                        new EditorControlData { component_count = components.Length });
                target = components[request.component_index];
            }

            if (target is Transform)
                return BuildError("EDITOR_CTRL_REM_COMP_IS_TRANSFORM",
                    "Cannot remove Transform — it is a required component.");

            Undo.DestroyObjectImmediate(target);

            var resp = BuildSuccess("EDITOR_CTRL_REM_COMP_OK",
                $"Removed {compType.FullName} from {request.hierarchy_path}",
                data: new EditorControlData
                {
                    selected_object = go.name,
                    asset_path = compType.FullName,
                    executed = true,
                    read_only = false,
                });
            resp.diagnostics = new[] { new EditorControlDiagnostic
            {
                detail = "Runtime modification — save the scene (File > Save) to persist.",
                evidence = "Undo.DestroyObjectImmediate"
            }};
            return resp;
        }

        private static EditorControlResponse HandleCreateUdonProgramAsset(EditorControlRequest request)
        {
            if (string.IsNullOrEmpty(request.asset_path))
                return BuildError("EDITOR_CTRL_UDON_NO_SCRIPT", "asset_path (.cs file) is required.");

            var script = AssetDatabase.LoadAssetAtPath<MonoScript>(request.asset_path);
            if (script == null)
                return BuildError("EDITOR_CTRL_UDON_SCRIPT_NOT_FOUND",
                    $"MonoScript not found: {request.asset_path}");

            // Resolve UdonSharpProgramAsset via reflection
            var assetType = System.Type.GetType(
                "UdonSharp.UdonSharpProgramAsset, UdonSharp.Editor");
            if (assetType == null)
                return BuildError("EDITOR_CTRL_UDON_NOT_AVAILABLE",
                    "UdonSharp.Editor not found. Is UdonSharp installed?");

            var asset = ScriptableObject.CreateInstance(assetType);

            // Set sourceCsScript field
            var field = assetType.GetField("sourceCsScript",
                System.Reflection.BindingFlags.Public | System.Reflection.BindingFlags.NonPublic
                | System.Reflection.BindingFlags.Instance);
            if (field != null)
                field.SetValue(asset, script);

            // Output path: use description field if provided, otherwise derive from .cs path
            string outputPath = string.IsNullOrEmpty(request.description)
                ? request.asset_path.Replace(".cs", ".asset")
                : request.description;

            AssetDatabase.CreateAsset(asset, outputPath);
            AssetDatabase.SaveAssets();

            return BuildSuccess("EDITOR_CTRL_UDON_ASSET_CREATED",
                $"Created Udon Program Asset: {outputPath}",
                data: new EditorControlData
                {
                    output_path = outputPath,
                    asset_path = request.asset_path,
                    executed = true,
                });
        }

        // ── Phase 5: SetProperty + SaveAsPrefab ──

        /// <summary>
        /// Resolve an object reference string to a UnityEngine.Object.
        /// Returns (object, errorDetail). errorDetail is null on success.
        /// </summary>
        private static (UnityEngine.Object obj, string error) ResolveObjectReference(string reference)
        {
            if (string.IsNullOrEmpty(reference))
                return (null, "object_reference is empty.");

            // 1. Check for component specifier (path:ComponentType)
            string goPath = reference;
            string componentName = null;
            int colonIdx = reference.LastIndexOf(':');
            if (colonIdx > 0)
            {
                goPath = reference.Substring(0, colonIdx);
                componentName = reference.Substring(colonIdx + 1);
            }

            // 2. Try scene hierarchy
            var go = GameObject.Find(goPath);
            if (go != null)
            {
                if (componentName != null)
                {
                    var compType = ResolveComponentType(componentName);
                    if (compType == null)
                        return (null, $"Component type not found: {componentName}");
                    var comp = go.GetComponent(compType);
                    if (comp == null)
                        return (null, $"GameObject '{goPath}' has no {componentName} component.");
                    return (comp, null);
                }
                return (go, null);
            }

            // 3. Try asset path
            var asset = AssetDatabase.LoadAssetAtPath<UnityEngine.Object>(reference);
            if (asset != null)
                return (asset, null);

            return (null, $"Not found in scene hierarchy or project assets: {reference}");
        }

        private static EditorControlResponse HandleEditorSetProperty(EditorControlRequest request)
        {
            // ── Validation ──
            if (string.IsNullOrEmpty(request.hierarchy_path))
                return BuildError("EDITOR_CTRL_SET_PROP_NO_PATH", "hierarchy_path is required.");
            if (string.IsNullOrEmpty(request.component_type))
                return BuildError("EDITOR_CTRL_SET_PROP_NO_COMP", "component_type is required.");
            if (string.IsNullOrEmpty(request.property_name))
                return BuildError("EDITOR_CTRL_SET_PROP_NO_FIELD", "property_name is required.");

            bool hasValue = !string.IsNullOrEmpty(request.property_value);
            bool hasRef = !string.IsNullOrEmpty(request.object_reference);
            if (!hasValue && !hasRef)
                return BuildError("EDITOR_CTRL_SET_PROP_NO_VALUE",
                    "Either property_value or object_reference is required.");
            if (hasValue && hasRef)
                return BuildError("EDITOR_CTRL_SET_PROP_BOTH_VALUE",
                    "Provide property_value or object_reference, not both.");

            // ── Resolve target ──
            var go = GameObject.Find(request.hierarchy_path);
            if (go == null)
                return BuildError("EDITOR_CTRL_SET_PROP_NOT_FOUND",
                    $"GameObject not found: {request.hierarchy_path}");

            System.Type compType = ResolveComponentType(request.component_type);
            if (compType == null)
                return BuildError("EDITOR_CTRL_SET_PROP_COMP_NOT_FOUND",
                    $"Component type not found: {request.component_type}");

            var component = go.GetComponent(compType);
            if (component == null)
                return BuildError("EDITOR_CTRL_SET_PROP_COMP_NOT_FOUND",
                    $"Component {request.component_type} not found on {request.hierarchy_path}");

            // ── Find property ──
            var so = new SerializedObject(component);
            var prop = so.FindProperty(request.property_name);
            if (prop == null)
                return BuildError("EDITOR_CTRL_SET_PROP_FIELD_NOT_FOUND",
                    $"Property not found: {request.property_name} on {request.component_type}");

            // ── Set value by type ──
            string v = request.property_value;
            try
            {
                switch (prop.propertyType)
                {
                    case SerializedPropertyType.Integer:
                        prop.intValue = int.Parse(v, System.Globalization.CultureInfo.InvariantCulture);
                        break;
                    case SerializedPropertyType.Float:
                        prop.floatValue = float.Parse(v, System.Globalization.CultureInfo.InvariantCulture);
                        break;
                    case SerializedPropertyType.Boolean:
                        prop.boolValue = bool.Parse(v);
                        break;
                    case SerializedPropertyType.String:
                        prop.stringValue = v;
                        break;
                    case SerializedPropertyType.Enum:
                    {
                        // enumNames returns internal C# names (preferred for programmatic input).
                        // enumDisplayNames (Unity 2021.1+) returns formatted display names which
                        // may contain spaces; unsuitable for API input.
#pragma warning disable 0618  // enumNames deprecated but intentionally used
                        int idx = System.Array.IndexOf(prop.enumNames, v);
                        if (idx >= 0)
                            prop.enumValueIndex = idx;
                        else if (int.TryParse(v, out int numIdx))
                            prop.enumValueIndex = numIdx;
                        else
                            return BuildError("EDITOR_CTRL_SET_PROP_TYPE_MISMATCH",
                                $"Enum value '{v}' not found. Valid: {string.Join(", ", prop.enumNames)}");
#pragma warning restore 0618
                        break;
                    }
                    case SerializedPropertyType.Color:
                    {
                        var parts = v.Split(',');
                        if (parts.Length < 3)
                            return BuildError("EDITOR_CTRL_SET_PROP_TYPE_MISMATCH",
                                "Color requires 3 or 4 comma-separated floats (r,g,b[,a]).");
                        float r = float.Parse(parts[0].Trim(), System.Globalization.CultureInfo.InvariantCulture);
                        float g = float.Parse(parts[1].Trim(), System.Globalization.CultureInfo.InvariantCulture);
                        float b = float.Parse(parts[2].Trim(), System.Globalization.CultureInfo.InvariantCulture);
                        float a = parts.Length >= 4
                            ? float.Parse(parts[3].Trim(), System.Globalization.CultureInfo.InvariantCulture)
                            : 1f;
                        prop.colorValue = new Color(r, g, b, a);
                        break;
                    }
                    case SerializedPropertyType.Vector2:
                    {
                        var parts = v.Split(',');
                        if (parts.Length < 2)
                            return BuildError("EDITOR_CTRL_SET_PROP_TYPE_MISMATCH",
                                "Vector2 requires 2 comma-separated floats (x,y).");
                        prop.vector2Value = new Vector2(
                            float.Parse(parts[0].Trim(), System.Globalization.CultureInfo.InvariantCulture),
                            float.Parse(parts[1].Trim(), System.Globalization.CultureInfo.InvariantCulture));
                        break;
                    }
                    case SerializedPropertyType.Vector3:
                    {
                        var parts = v.Split(',');
                        if (parts.Length < 3)
                            return BuildError("EDITOR_CTRL_SET_PROP_TYPE_MISMATCH",
                                "Vector3 requires 3 comma-separated floats (x,y,z).");
                        prop.vector3Value = new Vector3(
                            float.Parse(parts[0].Trim(), System.Globalization.CultureInfo.InvariantCulture),
                            float.Parse(parts[1].Trim(), System.Globalization.CultureInfo.InvariantCulture),
                            float.Parse(parts[2].Trim(), System.Globalization.CultureInfo.InvariantCulture));
                        break;
                    }
                    case SerializedPropertyType.Vector4:
                    {
                        var parts = v.Split(',');
                        if (parts.Length < 4)
                            return BuildError("EDITOR_CTRL_SET_PROP_TYPE_MISMATCH",
                                "Vector4 requires 4 comma-separated floats (x,y,z,w).");
                        prop.vector4Value = new Vector4(
                            float.Parse(parts[0].Trim(), System.Globalization.CultureInfo.InvariantCulture),
                            float.Parse(parts[1].Trim(), System.Globalization.CultureInfo.InvariantCulture),
                            float.Parse(parts[2].Trim(), System.Globalization.CultureInfo.InvariantCulture),
                            float.Parse(parts[3].Trim(), System.Globalization.CultureInfo.InvariantCulture));
                        break;
                    }
                    case SerializedPropertyType.ArraySize:
                    case SerializedPropertyType.FixedBufferSize:
                        prop.intValue = int.Parse(v, System.Globalization.CultureInfo.InvariantCulture);
                        break;
                    case SerializedPropertyType.ObjectReference:
                    {
                        string refPath = hasRef ? request.object_reference : v;
                        var (obj, refError) = ResolveObjectReference(refPath);
                        if (obj == null)
                            return BuildError("EDITOR_CTRL_SET_PROP_REF_NOT_FOUND",
                                refError ?? $"Object reference not found: {refPath}");
                        prop.objectReferenceValue = obj;
                        break;
                    }
                    default:
                        return BuildError("EDITOR_CTRL_SET_PROP_TYPE_MISMATCH",
                            $"Unsupported property type: {prop.propertyType}");
                }
            }
            catch (System.FormatException ex)
            {
                return BuildError("EDITOR_CTRL_SET_PROP_TYPE_MISMATCH",
                    $"Failed to parse value '{v}' for {prop.propertyType}: {ex.Message}");
            }

            so.ApplyModifiedProperties();

            var resp = BuildSuccess("EDITOR_CTRL_SET_PROP_OK",
                $"Set {request.property_name} on {request.component_type} at {request.hierarchy_path}",
                data: new EditorControlData
                {
                    selected_object = go.name,
                    executed = true,
                    read_only = false,
                });
            resp.diagnostics = new[] { new EditorControlDiagnostic
            {
                detail = $"Property type: {prop.propertyType}. Save the scene to persist.",
                evidence = "SerializedObject.ApplyModifiedProperties"
            }};
            return resp;
        }

        private static EditorControlResponse HandleSaveAsPrefab(EditorControlRequest request)
        {
            if (string.IsNullOrEmpty(request.hierarchy_path))
                return BuildError("EDITOR_CTRL_SAVE_PREFAB_NO_PATH", "hierarchy_path is required.");
            if (string.IsNullOrEmpty(request.asset_path))
                return BuildError("EDITOR_CTRL_SAVE_PREFAB_NO_OUTPUT", "asset_path is required.");
            if (!request.asset_path.EndsWith(".prefab", System.StringComparison.OrdinalIgnoreCase))
                return BuildError("EDITOR_CTRL_SAVE_PREFAB_BAD_EXT",
                    $"asset_path must end with .prefab: {request.asset_path}");

            var go = GameObject.Find(request.hierarchy_path);
            if (go == null)
                return BuildError("EDITOR_CTRL_SAVE_PREFAB_NOT_FOUND",
                    $"GameObject not found: {request.hierarchy_path}");

            // Ensure output directory exists
            string dir = System.IO.Path.GetDirectoryName(request.asset_path);
            if (!string.IsNullOrEmpty(dir) && !System.IO.Directory.Exists(dir))
                System.IO.Directory.CreateDirectory(dir);

            // Detect if this will become a Variant
            bool isVariant = PrefabUtility.IsPartOfPrefabInstance(go);
            string basePrefabPath = "";
            if (isVariant)
            {
                var baseObj = PrefabUtility.GetCorrespondingObjectFromSource(go);
                if (baseObj != null)
                    basePrefabPath = AssetDatabase.GetAssetPath(baseObj);
            }

            bool success;
            PrefabUtility.SaveAsPrefabAsset(go, request.asset_path, out success);
            if (!success)
                return BuildError("EDITOR_CTRL_SAVE_PREFAB_FAILED",
                    $"SaveAsPrefabAsset failed for: {request.asset_path}");

            string kind = isVariant ? "Prefab Variant" : "Prefab";
            var resp = BuildSuccess("EDITOR_CTRL_SAVE_PREFAB_OK",
                $"Saved {request.hierarchy_path} as {kind}: {request.asset_path}",
                data: new EditorControlData
                {
                    output_path = request.asset_path,
                    asset_path = basePrefabPath,
                    executed = true,
                    read_only = false,
                });

            var diags = new System.Collections.Generic.List<EditorControlDiagnostic>();
            diags.Add(new EditorControlDiagnostic
            {
                detail = $"Created as {kind}.",
                evidence = "PrefabUtility.SaveAsPrefabAsset"
            });
            if (isVariant && !string.IsNullOrEmpty(basePrefabPath))
                diags.Add(new EditorControlDiagnostic
                {
                    detail = $"Base Prefab: {basePrefabPath}",
                    evidence = "PrefabUtility.GetCorrespondingObjectFromSource"
                });
            resp.diagnostics = diags.ToArray();
            return resp;
        }

        private static EditorControlResponse HandleListMenuItems(EditorControlRequest request)
        {
            string prefix = request.filter ?? "";
            var items = new System.Collections.Generic.List<MenuItemEntry>();
            int totalScanned = 0;  // pre-filter count (all non-validate [MenuItem])

            foreach (var assembly in System.AppDomain.CurrentDomain.GetAssemblies())
            {
                System.Type[] types;
                try
                {
                    types = assembly.GetTypes();
                }
                catch (System.Reflection.ReflectionTypeLoadException ex)
                {
                    types = System.Array.FindAll(ex.Types, t => t != null);
                }

                foreach (var type in types)
                {
                    var methods = type.GetMethods(
                        System.Reflection.BindingFlags.Static |
                        System.Reflection.BindingFlags.Public |
                        System.Reflection.BindingFlags.NonPublic);

                    foreach (var method in methods)
                    {
                        var attrs = method.GetCustomAttributes(typeof(UnityEditor.MenuItem), false);
                        foreach (UnityEditor.MenuItem attr in attrs)
                        {
                            // Skip validate methods
                            if (attr.validate)
                                continue;

                            totalScanned++;
                            string menuPath = attr.menuItem;
                            if (prefix.Length > 0 && !menuPath.StartsWith(prefix, System.StringComparison.Ordinal))
                                continue;

                            items.Add(new MenuItemEntry
                            {
                                path = menuPath,
                                shortcut = ExtractShortcut(menuPath),
                            });
                        }
                    }
                }
            }

            // Sort by path for stable output
            items.Sort((a, b) => string.Compare(a.path, b.path, System.StringComparison.Ordinal));

            return BuildSuccess("EDITOR_CTRL_MENU_LIST_OK",
                $"Found {items.Count} menu items (total: {totalScanned})",
                data: new EditorControlData
                {
                    menu_items = items.ToArray(),
                    total_entries = totalScanned,  // pre-filter count per spec
                    read_only = true,
                    executed = true,
                });
        }

        /// <summary>Extract keyboard shortcut from MenuItem path (e.g. "Tools/Foo %t" → "%t").</summary>
        private static string ExtractShortcut(string menuPath)
        {
            // Unity shortcut chars: % (Cmd/Ctrl), # (Shift), & (Alt), _ (no modifier)
            int spaceIdx = menuPath.LastIndexOf(' ');
            if (spaceIdx < 0) return "";
            string candidate = menuPath.Substring(spaceIdx + 1);
            if (candidate.Length > 0 && (candidate[0] == '%' || candidate[0] == '#' || candidate[0] == '&' || candidate[0] == '_'))
                return candidate;
            return "";
        }

        private static EditorControlResponse HandleExecuteMenuItem(EditorControlRequest request)
        {
            if (string.IsNullOrEmpty(request.menu_path))
                return BuildError("EDITOR_CTRL_MISSING_PATH", "menu_path is required for execute_menu_item");

            // Check deny-list
            foreach (var denied in MenuDenyPrefixes)
            {
                if (request.menu_path.StartsWith(denied, System.StringComparison.Ordinal))
                    return BuildError("EDITOR_CTRL_MENU_DENIED",
                        $"Menu item denied by safety policy: {request.menu_path}");
            }

            bool result = EditorApplication.ExecuteMenuItem(request.menu_path);
            if (!result)
                return BuildError("EDITOR_CTRL_MENU_NOT_FOUND",
                    $"Menu item not found or not executable: {request.menu_path}");

            return BuildSuccess("EDITOR_CTRL_MENU_EXEC_OK",
                $"Menu item executed: {request.menu_path}");
        }

        // ── Phase 7: UX Review improvements ──

        private static EditorControlResponse HandleEditorBatchAddComponent(EditorControlRequest request)
        {
            if (string.IsNullOrEmpty(request.batch_operations_json))
                return BuildError("EDITOR_CTRL_BATCH_ADD_COMP_NO_DATA",
                    "batch_operations_json is required.");

            BatchAddComponentArray wrapper;
            try
            {
                wrapper = JsonUtility.FromJson<BatchAddComponentArray>(
                    "{\"items\":" + request.batch_operations_json + "}");
            }
            catch (System.Exception ex)
            {
                return BuildError("EDITOR_CTRL_BATCH_ADD_COMP_JSON_ERROR",
                    $"Failed to parse batch_operations_json: {ex.Message}");
            }

            if (wrapper.items == null || wrapper.items.Length == 0)
                return BuildError("EDITOR_CTRL_BATCH_ADD_COMP_EMPTY",
                    "batch_operations_json is empty.");

            int undoGroup = Undo.GetCurrentGroup();
            Undo.SetCurrentGroupName("PrefabSentinel: Batch AddComponent");

            var results = new System.Collections.Generic.List<string>();

            foreach (var op in wrapper.items)
            {
                var subReq = new EditorControlRequest
                {
                    action = "editor_add_component",
                    hierarchy_path = op.hierarchy_path,
                    component_type = op.component_type,
                    properties_json = op.properties_json,
                };
                var subResp = HandleEditorAddComponent(subReq);
                if (!subResp.success)
                {
                    Undo.CollapseUndoOperations(undoGroup);
                    return BuildError("EDITOR_CTRL_BATCH_ADD_COMP_FAILED",
                        $"Operation failed at index {results.Count}: {subResp.message}");
                }
                results.Add($"{op.hierarchy_path}: {op.component_type}");
            }

            Undo.CollapseUndoOperations(undoGroup);

            var resp = BuildSuccess("EDITOR_CTRL_BATCH_ADD_COMP_OK",
                $"Added {results.Count} components",
                data: new EditorControlData
                {
                    executed = true,
                    read_only = false,
                    suggestions = results.ToArray(),
                });
            resp.diagnostics = new[] { new EditorControlDiagnostic
            {
                detail = "Runtime modification — save the scene (File > Save) to persist.",
                evidence = "Undo.CollapseUndoOperations"
            }};
            return resp;
        }

        private static EditorControlResponse HandleEditorCreateScene(EditorControlRequest request)
        {
            if (string.IsNullOrEmpty(request.asset_path))
                return BuildError("EDITOR_CTRL_CREATE_SCENE_NO_PATH", "asset_path is required.");

            if (!request.asset_path.EndsWith(".unity", System.StringComparison.OrdinalIgnoreCase))
                return BuildError("EDITOR_CTRL_CREATE_SCENE_BAD_EXT",
                    $"asset_path must end with .unity: {request.asset_path}");

            string dir = System.IO.Path.GetDirectoryName(request.asset_path);
            if (!string.IsNullOrEmpty(dir) && !System.IO.Directory.Exists(dir))
                System.IO.Directory.CreateDirectory(dir);

            var scene = EditorSceneManager.NewScene(NewSceneSetup.EmptyScene, NewSceneMode.Single);
            bool ok = EditorSceneManager.SaveScene(scene, request.asset_path);
            if (!ok)
                return BuildError("EDITOR_CTRL_CREATE_SCENE_FAILED",
                    $"Failed to save new scene to: {request.asset_path}");

            return BuildSuccess("EDITOR_CTRL_CREATE_SCENE_OK",
                $"Created new scene: {request.asset_path}",
                data: new EditorControlData
                {
                    asset_path = request.asset_path,
                    output_path = scene.name,
                    executed = true,
                    read_only = false,
                });
        }

        // ── Batch Operation DTOs ──

        [Serializable]
        private sealed class BatchObjectSpec
        {
            public string type = string.Empty;
            public string name = string.Empty;
            public string parent = string.Empty;
            public string position = string.Empty;
            public string scale = string.Empty;
            public string rotation = string.Empty;
            public string[] components;
        }

        [Serializable]
        private sealed class BatchObjectArray { public BatchObjectSpec[] items; }

        [Serializable]
        private sealed class BatchSetPropertyOp
        {
            public string hierarchy_path = string.Empty;
            public string component_type = string.Empty;
            public string property_name = string.Empty;
            public string value = string.Empty;
            public string object_reference = string.Empty;
        }

        [Serializable]
        private sealed class BatchSetPropertyArray { public BatchSetPropertyOp[] items; }

        [Serializable]
        private sealed class BatchSetMaterialPropertyOp
        {
            public string name = string.Empty;
            public string value = string.Empty;
        }

        [Serializable]
        private sealed class BatchSetMaterialPropertyArray { public BatchSetMaterialPropertyOp[] items; }

        [Serializable]
        private sealed class PropertyEntry
        {
            public string name = string.Empty;
            public string value = string.Empty;
            public string object_reference = string.Empty;
        }

        [Serializable]
        private sealed class PropertyEntryArray { public PropertyEntry[] items; }

        [Serializable]
        private sealed class BatchAddComponentOp
        {
            public string hierarchy_path = string.Empty;
            public string component_type = string.Empty;
            public string properties_json = string.Empty;
        }

        [Serializable]
        private sealed class BatchAddComponentArray { public BatchAddComponentOp[] items; }

        // ── Response Builders ──

        internal static EditorControlResponse BuildSuccess(string code, string message, EditorControlData data = null)
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

        internal static EditorControlResponse BuildError(string code, string message)
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

        internal static EditorControlResponse BuildError(string code, string message, EditorControlData data)
        {
            return new EditorControlResponse
            {
                protocol_version = ProtocolVersion,
                success = false,
                severity = "error",
                code = code,
                message = message,
                data = data
            };
        }

        private static int LevenshteinDistance(string a, string b)
        {
            if (string.IsNullOrEmpty(a)) return b?.Length ?? 0;
            if (string.IsNullOrEmpty(b)) return a.Length;

            var dp = new int[a.Length + 1, b.Length + 1];
            for (int i = 0; i <= a.Length; i++) dp[i, 0] = i;
            for (int j = 0; j <= b.Length; j++) dp[0, j] = j;

            for (int i = 1; i <= a.Length; i++)
            {
                for (int j = 1; j <= b.Length; j++)
                {
                    int cost = a[i - 1] == b[j - 1] ? 0 : 1;
                    dp[i, j] = Math.Min(
                        Math.Min(dp[i - 1, j] + 1, dp[i, j - 1] + 1),
                        dp[i - 1, j - 1] + cost
                    );
                }
            }
            return dp[a.Length, b.Length];
        }

        private static string[] SuggestSimilar(string word, List<string> candidates, int maxResults = 3)
        {
            if (string.IsNullOrEmpty(word) || candidates == null || candidates.Count == 0)
                return Array.Empty<string>();

            var scored = new List<(string name, int dist)>();
            foreach (var candidate in candidates)
            {
                int dist = LevenshteinDistance(word, candidate);
                int maxLen = Math.Max(word.Length, candidate.Length);
                if (maxLen > 0 && dist <= maxLen * 0.4f)
                    scored.Add((candidate, dist));
            }
            scored.Sort((a, b) => a.dist.CompareTo(b.dist));
            var result = new string[Math.Min(maxResults, scored.Count)];
            for (int i = 0; i < result.Length; i++)
                result[i] = scored[i].name;
            return result;
        }

        private static List<string> CollectShaderPropertyNames(Shader shader)
        {
            var names = new List<string>();
            int count = shader.GetPropertyCount();
            for (int i = 0; i < count; i++)
                names.Add(shader.GetPropertyName(i));
            return names;
        }

        private static EditorControlResponse TryHandleVrcsdkUpload(EditorControlRequest request, string responsePath)
        {
            var handlerType = typeof(UnityEditorControlBridge).Assembly.GetType(
                "PrefabSentinel.VRCSDKUploadHandler");
            if (handlerType == null)
                return BuildError("VRCSDK_NOT_AVAILABLE",
                    "VRCSDKUploadHandler not found. Deploy VRCSDKUploadHandler.cs to Assets/Editor/ " +
                    "or VRC SDK is not installed in this project.");
            var handleMethod = handlerType.GetMethod("Handle",
                System.Reflection.BindingFlags.Public | System.Reflection.BindingFlags.Static);
            if (handleMethod == null)
                return BuildError("VRCSDK_NOT_AVAILABLE",
                    "VRCSDKUploadHandler.Handle method not found. Check VRCSDKUploadHandler.cs version.");
            try
            {
                var response = (EditorControlResponse)handleMethod.Invoke(null, new object[] { request });
                if (response != null)
                    return response;

                // null means async path: invoke HandleAsync(request, responsePath)
                var handleAsyncMethod = handlerType.GetMethod("HandleAsync",
                    System.Reflection.BindingFlags.Public | System.Reflection.BindingFlags.Static);
                if (handleAsyncMethod == null)
                    return BuildError("VRCSDK_NOT_AVAILABLE",
                        "VRCSDKUploadHandler.HandleAsync method not found. Check VRCSDKUploadHandler.cs version.");
                handleAsyncMethod.Invoke(null, new object[] { request, responsePath });
                return null;
            }
            catch (System.Reflection.TargetInvocationException ex)
            {
                var inner = ex.InnerException ?? ex;
                return BuildError("VRCSDK_UPLOAD_FAILED", inner.Message);
            }
        }

        internal static void WriteResponse(string responsePath, EditorControlResponse response)
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
