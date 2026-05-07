using System;
using System.Collections.Generic;
using System.IO;
using System.Reflection;
using UnityEditor;
using UnityEditor.Compilation;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.SceneManagement;
using UnityEngine.UI;

namespace PrefabSentinel
{
    /// <summary>
    /// Handles editor-control actions dispatched by EditorBridgeWindow:
    /// capture_screenshot, select_object, frame_selected, instantiate_to_scene, ping_object.
    /// Uses the same action-based protocol as UnityRuntimeValidationBridge.
    /// </summary>
    public static partial class UnityEditorControlBridge
    {
        public const int ProtocolVersion = 1;
        public const string BridgeVersion = "0.5.163";

        /// <summary>Actions that write their response file asynchronously (not on return).</summary>
        // Issues #108 / #118: ``run_script`` and ``editor_recompile_and_wait``
        // both observe the compile-and-reload cycle through an
        // ``EditorApplication.update`` registry instead of blocking the
        // main thread on ``Thread.Sleep``; the dispatcher must therefore
        // skip the synchronous "no response written" guard for them.
        public static readonly System.Collections.Generic.HashSet<string> AsyncActions =
            new System.Collections.Generic.HashSet<string>
            {
                "vrcsdk_upload",
                "run_script",
                "editor_recompile_and_wait",
            };

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
            // Issue #193: ``safe_save_prefab`` replaces the legacy
            // ``save_as_prefab`` as the sole public prefab-save action.
            "safe_save_prefab",
            "editor_set_parent",
            // Phase 6: Batch Operations + Scene
            "editor_create_empty",
            "editor_create_primitive",
            // Issue #195: dedicated uGUI element creation surface;
            // canonical allowed type set is owned by the handler.
            "editor_create_ui_element",
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
            // Phase 9: Editor script exec (#74)
            "run_script",
            // Issue #118: synchronous recompile-and-wait surface
            "editor_recompile_and_wait",
            // Issue #119: high-level UdonSharp authoring surface — three
            // synchronous handlers wrapping the AddComponent →
            // RunBehaviourSetup → CopyProxyToUdon authoring chain, the
            // SerializedObject field-write surface, and the published
            // UnityEventTools persistent-listener entry point.
            "editor_add_udonsharp_component",
            "editor_set_udonsharp_field",
            "editor_wire_persistent_listener",
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
            // Issue #113: ordering keyword and opaque continuation token.
            // Empty ``order`` defaults to "newest_first" inside the
            // handler. Empty ``cursor`` starts a fresh page from the
            // most recent (or oldest, depending on ordering) entry.
            public string order = string.Empty;
            public string cursor = string.Empty;

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
            public bool force_original = false;       // break Prefab Instance before saving
            // Issue #193: caller-supplied non-empty JSON array of component type
            // names that the safe-save handler must keep attached on the saved
            // asset.  Mandatory on the safe_save_prefab action; rejected when
            // empty / malformed (EDITOR_CTRL_SAFE_SAVE_PREFAB_PROTECT_REQUIRED /
            // EDITOR_CTRL_SAFE_SAVE_PREFAB_BAD_JSON).
            public string protect_components_json = string.Empty;

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

            // Phase 9: Editor script exec (#74)
            // `code` is the full C# snippet (must define `public static class PrefabSentinelTempScript`
            // with `public static void Run()`). `change_reason` is audited on the Python side;
            // we accept it here only so JsonUtility doesn't fail on the extra field.
            public string code = string.Empty;
            public string change_reason = string.Empty;
            public string temp_id = string.Empty;  // optional; handler generates one when empty

            // Phase 10: Force re-import on recompile (#106)
            // When set, HandleRecompileScripts runs ImportAsset with
            // ForceUpdate | ForceSynchronousImport on each editor script
            // before scheduling compilation, so externally edited files
            // under Assets/Editor are picked up reliably.
            public bool force_reimport = false;

            // Phase 10: Caller-supplied compile-poll budget (#102)
            // When > 0, HandleRunScript uses this as the bounded compile
            // poll budget (milliseconds) instead of RunScriptCompileTimeoutMs.
            // 0 (default) means "use the bridge default".
            public int compile_timeout = 0;

            // Phase 11: Camera reset mode (#112).
            // When true, HandleSetCamera ignores the other camera fields and
            // restores the active SceneView to the documented default pivot,
            // rotation, size, and orthographic flag.
            public bool reset_to_defaults = false;

            // Phase 11: Console capture classification filter (#117).
            // ``all`` (default), ``non_fatal`` (only entries matching the
            // bridge-side non-fatal pattern table), or ``fatal`` (only
            // entries that do not match it). Validated by the handler so an
            // unsupported value yields ``EDITOR_CTRL_INVALID_CLASSIFICATION_FILTER``.
            public string classification_filter = "all";

            // Issue #118: synchronous recompile-and-wait budget, in seconds.
            // Consumed by ``HandleRecompileAndWait``; ignored by every
            // other handler.  ``0`` means "use the bridge default".
            public float timeout_sec = 0f;

            // Issue #119: UdonSharp authoring surface payload.
            // ``editor_add_udonsharp_component`` consumes ``component_type``
            // (already declared above) plus ``fields_json``: a JSON object
            // mapping serialized-field name to a string-encoded value
            // (parsed through the same ApplyPropertyValue surface as
            // ``editor_set_property``).  ``editor_set_udonsharp_field``
            // consumes ``hierarchy_path``, ``field_name``, and the
            // existing value-vs-reference pair (``property_value`` /
            // ``object_reference``).
            public string fields_json = string.Empty;
            public string field_name = string.Empty;

            // ``editor_wire_persistent_listener`` consumes the source
            // identity (``hierarchy_path`` + source component is taken
            // from the resolved object's component_type), the source
            // event field name (``event_path``), the target identity
            // (``target_path``), the method name on the target
            // (``method``), and the string argument bound at edit time
            // (``arg``).
            public string event_path = string.Empty;
            public string target_path = string.Empty;
            public string method = string.Empty;
            public string arg = string.Empty;

            // Issue #195: ``editor_create_ui_element`` payload.
            // ``new_name`` carries the GameObject name, ``component_type``
            // selects from the canonical allowed type set, and
            // ``hierarchy_path`` resolves the parent (empty = scene root).
            // ``ui_rect_json`` carries
            // ``{"anchorMin":[x,y], "anchorMax":[x,y], "sizeDelta":[x,y]}``
            // and ``ui_properties_json`` carries the recognized graphic
            // property keys (``color``, ``font``); both are forwarded as
            // JSON strings because Unity's JsonUtility cannot bind nested
            // dictionaries with heterogeneous value shapes.
            public string ui_rect_json = string.Empty;
            public string ui_properties_json = string.Empty;
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
            // Issue #113: monotonic ingestion sequence assigned under the
            // capture lock; the cursor token names a sequence position so
            // pagination is stable across calls even when the ring buffer
            // wraps.
            public long sequence_id = 0;
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

            // Phase 9: Editor script exec (#74) — populated by run_script handler.
            public string stdout = string.Empty;
            public string exception = string.Empty;
            public string[] errors = Array.Empty<string>();
            public string temp_id = string.Empty;

            // Phase 11: run-script stuck-detection diagnostics (issue #116).
            // Attached to every compile-pending response so the caller can
            // tell why the bridge rejected the snippet without rerunning.
            public bool diagnostic_compiling = false;
            public string[] diagnostic_temp_files = Array.Empty<string>();
            public string diagnostic_last_domain_reload = string.Empty;

            // Phase 11: non-fatal classification (issue #117).
            // Save / instantiate handlers populate this section with counts
            // of console entries that matched the bridge-side non-fatal
            // pattern table during the operation. ``udonsharp_obs_nre_count``
            // is broken out as a typed field for callers; the labels of all
            // matched patterns appear in ``nonfatal_patterns``.
            public EditorControlWarnings warnings = new EditorControlWarnings();

            // Issue #113: opaque continuation token. Non-empty whenever
            // additional matching entries remain past the returned page;
            // empty when the page exhausted the matching set.
            public string next_cursor = string.Empty;

            // Issue #119: high-level UdonSharp authoring response payload.
            // ``was_existing`` records whether the upsert reused a
            // pre-existing component; ``applied_fields`` lists the field
            // names the bridge actually wrote (subset of the request when
            // the run aborts mid-application); ``component_handle``
            // identifies the resolved component for the next call;
            // ``udon_program_asset_path`` reports the .asset that
            // RunBehaviourSetup linked, when discoverable.
            public bool was_existing = false;
            public string[] applied_fields = Array.Empty<string>();
            public UdonSharpComponentHandle component_handle =
                new UdonSharpComponentHandle();
            public string udon_program_asset_path = string.Empty;

            // Issue #193: safe-save response payload.
            // ``reattached_components`` lists the component type names the
            // safe-save handler re-attached during the save (when the raw
            // ``SaveAsPrefabAsset`` stripped them).  ``orphan_modifications``
            // lists the parent-prefab modification overrides that became
            // orphan as a result of the save, each entry identified by its
            // target object path and property path.
            public string[] reattached_components = Array.Empty<string>();
            public OrphanModificationEntry[] orphan_modifications =
                Array.Empty<OrphanModificationEntry>();
        }

        [Serializable]
        public sealed class OrphanModificationEntry
        {
            public string target_object_path = string.Empty;
            public string property_path = string.Empty;
        }

        // Issue #119: stable handle returned from
        // ``editor_add_udonsharp_component`` so callers can refer back to
        // the component without re-resolving by name.  Mirrors the
        // existing ``editor_add_component`` handle shape (path + type +
        // index) so client tooling can interpret either uniformly.
        [Serializable]
        public sealed class UdonSharpComponentHandle
        {
            public string hierarchy_path = string.Empty;
            public string type_full_name = string.Empty;
            public int component_index = -1;
        }

        [Serializable]
        public sealed class EditorControlWarnings
        {
            public int udonsharp_obs_nre_count = 0;
            public string[] nonfatal_patterns = Array.Empty<string>();
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
                    response = HandleRecompileScripts(request);
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
                case "safe_save_prefab":
                    response = HandleSafeSaveAsPrefab(request);
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
                case "editor_create_ui_element":
                    response = HandleEditorCreateUiElement(request);
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
                case "run_script":
                    response = HandleRunScript(request, responsePath);
                    break;
                case "editor_recompile_and_wait":
                    response = HandleRecompileAndWait(request, responsePath);
                    break;
                case "editor_add_udonsharp_component":
                    response = HandleAddUdonSharpComponent(request);
                    break;
                case "editor_set_udonsharp_field":
                    response = HandleSetUdonSharpField(request);
                    break;
                case "editor_wire_persistent_listener":
                    response = HandleWirePersistentListener(request);
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

        // ── Non-fatal exception classifier (issue #117) ──

        /// <summary>
        /// Pattern table that decides whether a console entry is a known
        /// non-fatal exception. Save and instantiate handlers consult this
        /// to count benign noise without losing signal; the console capture
        /// handler honours a classification filter against the same table.
        ///
        /// Adding a new pattern is a contract change — every consumer of
        /// the resulting label must be aware of it. Document new entries
        /// in ``knowledge/udonsharp.md`` and the README's non-fatal
        /// classification section.
        /// </summary>
        internal static class NonFatalExceptionClassifier
        {
            private static readonly (string Label,
                Func<string, string, LogType, bool> Match)[] Patterns =
            {
                (
                    "udonsharp_obs_nre",
                    (msg, stack, type) =>
                        type == LogType.Exception
                        && !string.IsNullOrEmpty(msg)
                        && msg.IndexOf(
                               "ArgumentNullException", StringComparison.Ordinal) >= 0
                        && !string.IsNullOrEmpty(stack)
                        && stack.IndexOf(
                               "OnBeforeSerialize", StringComparison.Ordinal) >= 0
                ),
            };

            /// <summary>
            /// Returns the matching pattern label, or ``null`` when the
            /// entry does not match any known non-fatal pattern.
            /// </summary>
            public static string Classify(string message, string stackTrace, LogType type)
            {
                string m = message ?? string.Empty;
                string s = stackTrace ?? string.Empty;
                foreach (var p in Patterns)
                {
                    if (p.Match(m, s, type)) return p.Label;
                }
                return null;
            }

            public static bool IsNonFatal(string message, string stackTrace, LogType type)
                => Classify(message, stackTrace, type) != null;
        }

        // ── Console Log Buffer ──

        /// <summary>
        /// Ring buffer that captures Unity console logs via Application.logMessageReceived.
        /// Managed by EditorBridgeWindow (start/stop tied to window lifecycle).
        /// </summary>
        public static class ConsoleLogBuffer
        {
            // Issue #131: ring-buffer capacity must be public so the
            // ``capture_console_logs`` request validator and the Python-side
            // mirror constant can reference the same single value. The
            // request bound mirrors this capacity — callers cannot ask for
            // more entries than the buffer has ever held.
            public const int DefaultCapacity = 1000;

            private struct RawEntry
            {
                public string message;
                public string stackTrace;
                public LogType logType;
                public double timestamp; // EditorApplication.timeSinceStartup
                // Issue #113: monotonic ingestion sequence assigned by
                // OnLogMessage under the capture lock. Stable across ring
                // buffer wraparounds so cursor tokens remain meaningful.
                public long sequenceId;
            }

            private static RawEntry[] _buffer;
            private static int _head;
            private static int _count;
            private static bool _capturing;
            private static long _nextSequenceId;
            private static readonly object _lock = new object();

            public static void StartCapture()
            {
                if (_capturing) return;
                lock (_lock)
                {
                    _buffer = new RawEntry[DefaultCapacity];
                    _head = 0;
                    _count = 0;
                    _nextSequenceId = 0;
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
                        sequenceId = _nextSequenceId++,
                    };
                    _head = (_head + 1) % _buffer.Length;
                    if (_count < _buffer.Length) _count++;
                }
            }

            /// <summary>
            /// Snapshot the buffer, applying filters and ordering.
            /// ``classificationFilter`` selects between ``all`` (default),
            /// ``non_fatal``, and ``fatal``. ``newestFirst`` walks the
            /// buffer in reverse ingestion order. ``cursorAfterSequence``
            /// is exclusive: only entries strictly past that ingestion
            /// position (in the requested direction) are considered;
            /// ``long.MinValue`` (oldest_first) or ``long.MaxValue``
            /// (newest_first) means "no cursor".
            ///
            /// Returns the page entries (in the requested direction) and
            /// a flag indicating whether at least one matching entry
            /// remains past the page; the caller turns that into the
            /// opaque continuation token.
            /// </summary>
            public static (List<ConsoleLogEntry> entries, bool hasMore) GetEntries(
                int maxEntries,
                string logTypeFilter,
                float sinceSeconds,
                string classificationFilter,
                bool newestFirst,
                long cursorAfterSequence)
            {
                var result = new List<ConsoleLogEntry>();
                bool hasMore = false;
                lock (_lock)
                {
                    if (_buffer == null || _count == 0) return (result, hasMore);

                    double now = EditorApplication.timeSinceStartup;
                    int start = (_head - _count + _buffer.Length) % _buffer.Length;

                    for (int i = 0; i < _count; i++)
                    {
                        // Walk index in the requested direction so the
                        // first matching entry returned is the one the
                        // caller asked for (newest or oldest), and the
                        // page can stop early.
                        int physicalIndex = newestFirst
                            ? (start + _count - 1 - i) % _buffer.Length
                            : (start + i) % _buffer.Length;
                        var entry = _buffer[physicalIndex];

                        // Exclusive cursor: skip up to and including the
                        // supplied sequence position so the next page
                        // starts strictly past it.
                        if (newestFirst)
                        {
                            if (entry.sequenceId >= cursorAfterSequence) continue;
                        }
                        else
                        {
                            if (entry.sequenceId <= cursorAfterSequence) continue;
                        }

                        if (sinceSeconds > 0f && (now - entry.timestamp) > sinceSeconds)
                            continue;
                        if (!MatchesTypeFilter(entry.logType, logTypeFilter))
                            continue;
                        if (!MatchesClassificationFilter(
                                entry.message, entry.stackTrace, entry.logType,
                                classificationFilter))
                            continue;

                        if (result.Count >= maxEntries)
                        {
                            // Found one more matching entry past the
                            // requested page size — flag continuation.
                            hasMore = true;
                            break;
                        }

                        result.Add(new ConsoleLogEntry
                        {
                            message = entry.message ?? string.Empty,
                            stack_trace = entry.stackTrace ?? string.Empty,
                            log_type = entry.logType.ToString(),
                            timestamp = TimeSpan.FromSeconds(entry.timestamp).ToString(@"hh\:mm\:ss"),
                            sequence_id = entry.sequenceId,
                        });
                    }
                }
                return (result, hasMore);
            }

            /// <summary>
            /// Returns the highest sequence id ever ingested, even after the
            /// ring buffer has wrapped. Used by ``HandleCaptureConsoleLogs``
            /// to validate cursor tokens against the realistic range of
            /// past ingestion positions.
            /// </summary>
            public static long PeekHighestIngestedSequenceId()
            {
                lock (_lock) { return _nextSequenceId - 1; }
            }

            /// <summary>
            /// Walk the buffer for entries whose timestamp is greater than
            /// or equal to ``sinceTimestamp`` and tally non-fatal pattern
            /// matches. Used by save/instantiate handlers to surface known
            /// noise as warnings without losing console signal.
            /// </summary>
            public static (int udonsharpObsNreCount, List<string> labels)
                CollectNonFatalCountsSince(double sinceTimestamp)
            {
                int obsNre = 0;
                var labels = new List<string>();
                lock (_lock)
                {
                    if (_buffer == null || _count == 0) return (0, labels);
                    int start = (_head - _count + _buffer.Length) % _buffer.Length;
                    for (int i = 0; i < _count; i++)
                    {
                        var entry = _buffer[(start + i) % _buffer.Length];
                        if (entry.timestamp < sinceTimestamp) continue;
                        string label = NonFatalExceptionClassifier.Classify(
                            entry.message, entry.stackTrace, entry.logType);
                        if (label == null) continue;
                        if (!labels.Contains(label)) labels.Add(label);
                        if (label == "udonsharp_obs_nre") obsNre++;
                    }
                }
                return (obsNre, labels);
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

            private static bool MatchesClassificationFilter(
                string message, string stackTrace, LogType type, string filter)
            {
                if (string.IsNullOrEmpty(filter) || filter == "all") return true;
                bool isNonFatal = NonFatalExceptionClassifier.IsNonFatal(
                    message, stackTrace, type);
                switch (filter)
                {
                    case "non_fatal": return isNonFatal;
                    case "fatal":     return !isNonFatal;
                    default:          return true;
                }
            }

            // Supported classification filter values; used by the handler
            // for both gating and the error message body.
            internal static readonly string[] SupportedClassificationFilters =
                { "all", "non_fatal", "fatal" };

            internal static bool IsSupportedClassificationFilter(string value)
            {
                if (string.IsNullOrEmpty(value)) return true;
                foreach (var v in SupportedClassificationFilters)
                {
                    if (v == value) return true;
                }
                return false;
            }
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
        // Issues #108 / #118: ``PendingAsyncRunner`` is the single async
        // completion registry shared by ``HandleRunScript`` and
        // ``HandleRecompileAndWait``.  Each entry registers an
        // ``EditorApplication.update`` callback that polls the editor's
        // compile-state and the compiled-assembly mtime; the response
        // file is written only after the documented completion signals
        // are observed or the supplied budget is exceeded.  In-flight
        // requests are mirrored to ``SessionState`` so a domain reload
        // (triggered by the recompile itself) does not lose the entry.
        // The post-reload resumer (an ``[InitializeOnLoad]`` hook) walks
        // ``SessionState`` and re-registers each entry on the new
        // AppDomain so completion drainage continues from the same place.
        internal static class PendingAsyncRunner
        {
            private const string SessionStateKey =
                "PrefabSentinel_PendingAsyncRunner_v1";
            // Path to the compiled assembly observed by both completion
            // signals.  The bridge tracks its mtime as the second
            // completion signal; the post-reload event provides the third.
            internal const string CompiledAssemblyRelPath =
                "Library/ScriptAssemblies/Assembly-CSharp.dll";

            [Serializable]
            internal sealed class PersistedEntry
            {
                public string action = string.Empty;
                public string responsePath = string.Empty;
                public string requestJson = string.Empty;
                public long callTimeUnixMs;
                public long deadlineUnixMs;
                public long callTimeAssemblyMtimeUnixMs;
                public string tempId = string.Empty;
                public string stuckKey = string.Empty;
                public string tempDirAbs = string.Empty;
            }

            [Serializable]
            internal sealed class PersistedEntryList
            {
                public List<PersistedEntry> items = new List<PersistedEntry>();
            }

            // Active entries on the *current* AppDomain. Survives domain
            // reload via the SessionState mirror.
            private static readonly Dictionary<string, PersistedEntry> ActiveEntries
                = new Dictionary<string, PersistedEntry>();

            // Each entry's poll delegate. Populated lazily by the handler
            // that owns the entry.  Lost across domain reload; the
            // post-reload resumer re-installs them.
            private static readonly Dictionary<string, EditorApplication.CallbackFunction>
                ActiveCallbacks =
                    new Dictionary<string, EditorApplication.CallbackFunction>();

            // Issue #203: response paths whose entry is transient (lives
            // only on the current AppDomain). Excluded from ``Persist`` so
            // a parallel handler's persistence call does not leak the
            // transient entry into SessionState.
            private static readonly HashSet<string> TransientResponsePaths
                = new HashSet<string>();

            // ``afterAssemblyReload`` fires on the new AppDomain after a
            // reload completes; we tick this counter so the
            // ``HandleRecompileAndWait`` poller can detect "the reload we
            // were waiting on has fired" without misfiring on a reload
            // that started before the request.
            internal static int AssemblyReloadCount { get; private set; }

            static PendingAsyncRunner()
            {
                AssemblyReloadEvents.afterAssemblyReload -= OnAfterAssemblyReload;
                AssemblyReloadEvents.afterAssemblyReload += OnAfterAssemblyReload;
            }

            private static void OnAfterAssemblyReload()
            {
                AssemblyReloadCount++;
            }

            internal static void Register(
                PersistedEntry entry,
                EditorApplication.CallbackFunction poll)
            {
                ActiveEntries[entry.responsePath] = entry;
                ActiveCallbacks[entry.responsePath] = poll;
                TransientResponsePaths.Remove(entry.responsePath);
                EditorApplication.update -= poll;
                EditorApplication.update += poll;
                Persist();
            }

            /// <summary>
            /// Issue #203: register a per-frame poll without mirroring the
            /// entry to SessionState. Used by ``HandleRecompileAndWait``'s
            /// pre-reload phase, which observes pipeline events on the
            /// current AppDomain — those subscriptions cannot survive a
            /// domain reload, so persisting the entry would resurrect a
            /// stale state on the new domain. The handler escalates to
            /// ``Register`` only when at least one assembly compiled and
            /// the post-reload wait must therefore survive a reload.
            /// </summary>
            internal static void RegisterTransient(
                PersistedEntry entry,
                EditorApplication.CallbackFunction poll)
            {
                ActiveEntries[entry.responsePath] = entry;
                ActiveCallbacks[entry.responsePath] = poll;
                TransientResponsePaths.Add(entry.responsePath);
                EditorApplication.update -= poll;
                EditorApplication.update += poll;
            }

            internal static void Complete(string responsePath)
            {
                if (ActiveCallbacks.TryGetValue(responsePath, out var poll))
                {
                    EditorApplication.update -= poll;
                    ActiveCallbacks.Remove(responsePath);
                }
                ActiveEntries.Remove(responsePath);
                TransientResponsePaths.Remove(responsePath);
                Persist();
            }

            internal static long ReadAssemblyMtimeUnixMs()
            {
                try
                {
                    string abs = Path.Combine(
                        Directory.GetCurrentDirectory(),
                        CompiledAssemblyRelPath.Replace('/', Path.DirectorySeparatorChar));
                    if (!File.Exists(abs)) return 0L;
                    DateTime mtime = File.GetLastWriteTimeUtc(abs);
                    return new DateTimeOffset(mtime).ToUnixTimeMilliseconds();
                }
                catch (Exception ex)
                {
                    Debug.LogWarning(
                        $"[PrefabSentinel] PendingAsyncRunner: assembly mtime read failed: {ex.Message}");
                    return 0L;
                }
            }

            private static void Persist()
            {
                try
                {
                    var list = new PersistedEntryList();
                    foreach (var kv in ActiveEntries)
                    {
                        if (TransientResponsePaths.Contains(kv.Key)) continue;
                        list.items.Add(kv.Value);
                    }
                    string json = JsonUtility.ToJson(list);
                    SessionState.SetString(SessionStateKey, json);
                }
                catch (Exception ex)
                {
                    Debug.LogWarning(
                        $"[PrefabSentinel] PendingAsyncRunner.Persist failed: {ex.Message}");
                }
            }

            internal static List<PersistedEntry> ReadPersisted()
            {
                try
                {
                    string json = SessionState.GetString(SessionStateKey, "");
                    if (string.IsNullOrEmpty(json))
                        return new List<PersistedEntry>();
                    var list = JsonUtility.FromJson<PersistedEntryList>(json);
                    return list?.items ?? new List<PersistedEntry>();
                }
                catch (Exception ex)
                {
                    Debug.LogWarning(
                        $"[PrefabSentinel] PendingAsyncRunner.ReadPersisted failed: {ex.Message}");
                    return new List<PersistedEntry>();
                }
            }

            internal static void RehydrateEntry(
                PersistedEntry entry,
                EditorApplication.CallbackFunction poll)
            {
                ActiveEntries[entry.responsePath] = entry;
                ActiveCallbacks[entry.responsePath] = poll;
                EditorApplication.update -= poll;
                EditorApplication.update += poll;
            }
        }
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
            catch (Exception atomicEx)
            {
                Debug.LogWarning(
                    $"[PrefabSentinel] WriteResponse: atomic move failed for '{responsePath}': {atomicEx.Message}; falling back to direct write.");
                try { File.WriteAllText(responsePath, JsonUtility.ToJson(response, true)); }
                catch (Exception directEx)
                {
                    Debug.LogWarning(
                        $"[PrefabSentinel] WriteResponse: direct write also failed for '{responsePath}': {directEx.Message}");
                }
            }
        }
    }
}
