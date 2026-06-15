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
        public const string BridgeVersion = "0.7.5";

        /// <summary>Actions that write their response file asynchronously (not on return).</summary>
        // Issue H-8: the membership sets are owned by ``ActionRegistry`` as the
        // single source of truth; the dispatcher and EditorBridge consume them
        // through these aliases. The async set lets the dispatcher skip the
        // synchronous "no response written" guard for compile-and-reload
        // observing actions (#108 / #118 / #225 / #233).
        public static readonly HashSet<string> AsyncActions = ActionRegistry.Async;

        /// <summary>All action strings handled by this bridge.</summary>
        public static readonly HashSet<string> SupportedActions = ActionRegistry.Supported;

        // ── Request / Response DTOs ──

        // Request / Response DTOs — the EditorControlRequest DTO is
        // relocated to PrefabSentinel.Dispatch.EditorControlRequest.cs (issue
        // H-8) so the Unity-free xUnit harness can construct it directly.

        [Serializable]
        public sealed class EditorControlDiagnostic
        {
            public string code = string.Empty;
            public string severity = string.Empty;
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
            public string request_id = string.Empty;
            // Issue #239: editor-phase tag assigned at ingestion under
            // the same capture lock.  Values come from
            // ``ConsoleLogEntryPredicate.SupportedPhaseFilters`` minus ``all``:
            // ``edit`` / ``play`` / ``build``.  The priority order
            // ``build > play > edit`` is implemented in
            // ``ConsoleLogPhaseClassifier.Classify``; ``OnLogMessage``
            // only calls it to snapshot the phase at ingestion.
            public string phase = string.Empty;
        }

        // Issue #239 / #40: editor-state snapshot returned by the dedicated
        // ``get_editor_state`` action.  Carries exactly the five live
        // editor flags surfaced by the ``get_project_status`` MCP tool's
        // ``editor_state`` field — adding a flag here is a contract
        // change for both the Python tool and the C# bridge.  The fifth
        // flag ``has_unsaved_changes`` (issue #40) reports whether the
        // open scene or the active Prefab Stage holds unsaved edits; the
        // offline symbol-reference tools consult it to attach a freshness
        // marker noting the offline tree reflects last-saved disk.
        [Serializable]
        public sealed class EditorStateSnapshot
        {
            public bool is_playing = false;
            public bool is_will_change_playmode = false;
            public bool is_compiling = false;
            public bool is_building_player = false;
            public bool has_unsaved_changes = false;
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
        public sealed class GeometryBoundsContributorEntry
        {
            public string source = string.Empty;
            public string hierarchy_path = string.Empty;
            public bool target = false;
            public float[] center = null;
            public float[] extents = null;
            public float[] min = null;
            public float[] max = null;
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
            public string[] deleted_paths = Array.Empty<string>();
            public string[] failed_paths = Array.Empty<string>();
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
            public float[] camera_look_at = null;
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
            public float[] bounds_size = null;
            public float[] bounds_min = null;
            public float[] bounds_max = null;
            public string hierarchy_path = string.Empty;
            public string parent_path = string.Empty;
            public float[] local_position = null;
            public float[] world_position = null;
            public float[] local_rotation_quat = null;
            public float[] world_rotation_quat = null;
            public float[] local_euler = null;
            public float[] world_euler = null;
            public float[] local_scale = null;
            public float[] lossy_scale = null;
            public bool active_self = false;
            public bool active_in_hierarchy = false;
            public string bounds_source = string.Empty;
            public string target_mode = string.Empty;
            public string projection = string.Empty;
            public float[] ui_normal = null;
            public bool include_children = false;
            public int contributor_count = 0;
            public GeometryBoundsContributorEntry[] bounds_contributors =
                Array.Empty<GeometryBoundsContributorEntry>();
            public string target_path = string.Empty;
            public float distance = 0f;
            public string distance_mode = string.Empty;
            public float[] from_point = null;
            public float[] to_point = null;
            public float[] target_bounds_center = null;
            public float[] target_bounds_extents = null;

            public bool read_only = true;
            public bool executed = false;

            // Issue #51: the dispatched action name.  Populated only on
            // the dispatch-boundary EDITOR_CTRL_HANDLER_EXCEPTION path
            // so callers can branch on the failing action as a
            // structured field rather than parsing the message string.
            public string action = string.Empty;

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

            // UdonSharp array write error context.
            public string field_name = string.Empty;
            public int element_index = -1;
            public string expected_type = string.Empty;

            // Phase 8: Reflection
            public string reflect_result_json = string.Empty;

            // Phase 9: Editor script exec (#74) — populated by run_script handler.
            // Issue #216: the bridge response carries no exception text in
            // the structured payload — every script-runner catch site
            // returns a fixed message and routes ``ex`` to the Unity
            // console via ``Debug.LogWarning``. The MCP client sees only
            // ``stdout`` (set on success), ``errors`` (compile errors),
            // and ``temp_id``.
            public string stdout = string.Empty;
            public string[] errors = Array.Empty<string>();
            public string temp_id = string.Empty;
            public RunScriptValue return_value = null;
            public RunScriptOutputEntry[] outputs = Array.Empty<RunScriptOutputEntry>();
            public string unsupported_output_key = string.Empty;
            public RunScriptExceptionSummary exception = null;
            public WslPathHint[] path_hints = Array.Empty<WslPathHint>();

            // Issue #239: live editor-state snapshot returned by the
            // dedicated ``get_editor_state`` action; consumed by the
            // ``get_project_status`` MCP tool so callers can tell
            // whether the editor is currently playing, transitioning,
            // compiling, or building without parsing logs.  ``null``
            // marks "not applicable to this action's response".
            public EditorStateSnapshot editor_state = null;

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

            // Issue #225: menu-execute response payload. ``recompile_waited``
            // tells the caller whether the implicit recompile barrier
            // actually fired (slow async path) or whether the handler
            // took the synchronous fast path. Always emitted on the
            // execute_menu_item action so callers can assert which path
            // ran without consulting external state.
            public bool recompile_waited = false;

            // Issue #249: screenshot region resolution. ``crop_roi_applied``
            // carries the resolved preset label (one of
            // ``eye_left | eye_right | mouth | auto_face``) or the literal
            // ``pixel_rect`` for the four-integer rectangle form.
            // ``crop_bounds`` reports the integer rectangle the bridge
            // actually cropped to so the caller can correlate the result
            // with their request without re-deriving the bounds.
            public string crop_roi_applied = string.Empty;
            public CropBoundsEntry crop_bounds = null;

            // Issue #242: scene-view refresh primitive response — count
            // of SkinnedMeshRenderers whose force-recalculate flag was
            // toggled in the round-trip.
            public int renderers_touched = 0;

            // Issue #240: batch blend-shape response.
            // ``set_count`` reports the number of applied shapes;
            // ``failed_shapes`` enumerates the entries the bridge could
            // not apply (typically unknown name).  The pagination
            // continuation token reuses the existing ``next_cursor``
            // field; the post-filter total reuses ``total_entries``.
            public int set_count = 0;
            public BatchBlendShapeFailure[] failed_shapes =
                Array.Empty<BatchBlendShapeFailure>();

            // Issue #236: Prefab Stage response payload.  ``stage_root_name``
            // identifies the prefab root GameObject's name on open;
            // the close path reuses the existing ``asset_path`` field
            // for the asset path of the closed stage, and ``saved``
            // reports whether the close persisted changes.
            public string stage_root_name = string.Empty;
            public bool saved = false;

            // Issue #233: async submit / poll response.
            // ``request_id`` is the opaque per-request identifier
            // returned from submit (reused for the poll input);
            // ``accepted_at`` is the server-side acceptance timestamp
            // (epoch milliseconds); ``status`` reports one of pending /
            // completed / failed on the poll surface.
            public string request_id = string.Empty;
            public long accepted_at = 0;
            public string status = string.Empty;

            // Issue #243: AnimationClip response payload.
            // ``curves`` carries the curve list emitted by the inspect
            // surface; ``length`` and ``frame_rate`` carry the clip's
            // timing; the create surface reports the written
            // ``asset_path`` (reusing the existing field) and
            // ``curve_count``; the apply surface reports
            // ``applied_curve_count``.
            public AnimationCurveEntry[] curves =
                Array.Empty<AnimationCurveEntry>();
            public float length = 0f;
            public float frame_rate = 60f;
            public int curve_count = 0;
            public int applied_curve_count = 0;
        }

        // Issue #249: integer rectangle returned alongside the resolved
        // region label on the screenshot response.  All four fields are
        // expressed in source-image pixel coordinates.
        [Serializable]
        public sealed class CropBoundsEntry
        {
            public int x = 0;
            public int y = 0;
            public int w = 0;
            public int h = 0;
        }

        // Issue #240: per-shape failure entry for the batch blend-shape
        // response.  Only emitted for shapes the bridge could not apply
        // (typically unknown name); successful shapes are not enumerated
        // because the per-shape success count carries them in aggregate.
        [Serializable]
        public sealed class BatchBlendShapeFailure
        {
            public string name = string.Empty;
            public string reason = string.Empty;
        }

        // Issue #243: AnimationClip curve specification entry.  Used both
        // as inspect-handler output and as create-handler input;
        // ``values`` is a single-keyframe scalar in JSON-array form on
        // the simple path, or a multi-keyframe sequence sampled at the
        // clip's default frame rate.
        [Serializable]
        public sealed class AnimationCurveEntry
        {
            public string relative_path = string.Empty;
            public string type = string.Empty;
            public string property = string.Empty;
            public float[] values = Array.Empty<float>();
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

private static string DeriveTransportRequestId(string requestPath)
        {
            string fileName = Path.GetFileName(requestPath);
            const string requestSuffix = ".request.json";
            if (fileName.EndsWith(requestSuffix, StringComparison.Ordinal))
                return fileName.Substring(0, fileName.Length - requestSuffix.Length);
            return Path.GetFileNameWithoutExtension(fileName);
        }

        public static void RunFromPaths(string requestPath, string responsePath)
        {
            string transportRequestId = DeriveTransportRequestId(requestPath);
            ConsoleLogBuffer.BeginRequest(transportRequestId);
            try
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

                // Issue #51: the whole action switch runs inside a dispatch-level
                // exception boundary. Any handler exception not caught internally
                // yields a typed ``EDITOR_CTRL_HANDLER_EXCEPTION`` envelope naming
                // the dispatched action, with the exception redacted to its type
                // name only - no stack trace crosses the MCP boundary. The full
                // detail is mirrored to the Unity console for local triage. The
                // watch-loop generic catch is no longer the sink for handler
                // exceptions; ``EDITOR_BRIDGE_ERROR`` remains only for genuine
                // watch-loop / pre-dispatch failures.
                EditorControlResponse response;
                try
                {
                    response = DispatchAction(
                        request, requestPath, responsePath, transportRequestId);
                }
                catch (Exception handlerEx)
                {
                    Debug.LogWarning(
                        $"[PrefabSentinel] RunFromPaths: handler for action "
                        + $"'{request.action}' threw: {handlerEx}");
                    response = BuildError(
                        "EDITOR_CTRL_HANDLER_EXCEPTION",
                        $"editor_control action '{request.action}' failed: the "
                        + $"handler raised {handlerEx.GetType().Name}. "
                        + "Inspect the Unity console for the full exception detail "
                        + "and retry once the underlying cause is resolved.",
                        new EditorControlData { action = request.action });
                }

                if (response != null)
                    WriteResponse(responsePath, response);
            }
            finally
            {
                ConsoleLogBuffer.EndRequest(transportRequestId);
            }
        }

        // Issue #51: the action dispatch switch, extracted so the
        // ``RunFromPaths`` exception boundary wraps every handler call in a
        // single try/catch. Returns ``null`` for handlers that write their
        // response file asynchronously (the dispatcher then skips the
        // synchronous WriteResponse).
        private static EditorControlResponse DispatchAction(
            EditorControlRequest request,
            string requestPath,
            string responsePath,
            string transportRequestId)
        {
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
                    // Issue #70: a compile-aware refresh may schedule an
                    // async barrier that writes the response after compile
                    // + reload completes. Pass the response path so the
                    // handler can defer the response and return null.
                    response = HandleRefreshAssetDatabase(request, responsePath);
                    break;
                case "set_material":
                    response = HandleSetMaterial(request);
                    break;
                case "delete_object":
                    response = HandleDeleteObject(request);
                    break;
                case "delete_assets":
                    response = HandleDeleteAssets(request);
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
                    // Issue #225: the menu-execute handler may schedule
                    // an async barrier pipeline that writes the response
                    // after compile + reload completes. Pass the
                    // response path so the handler can defer the
                    // response and signal the dispatcher to skip the
                    // synchronous WriteResponse call (return null).
                    response = HandleExecuteMenuItem(request, responsePath);
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
                    response = HandleRunScript(
                        request, responsePath, transportRequestId);
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
                case "get_editor_state":
                    response = HandleGetEditorState();
                    break;
                case "force_scene_view_refresh":
                    response = HandleForceSceneViewRefresh();
                    break;
                case "batch_set_blend_shape":
                    response = HandleBatchSetBlendShape(request);
                    break;
                case "open_prefab":
                    response = HandleOpenPrefab(request);
                    break;
                case "close_prefab":
                    response = HandleClosePrefab(request);
                    break;
                case "run_script_submit":
                    response = HandleRunScriptSubmit(request, responsePath);
                    break;
                case "run_script_poll":
                    response = HandleRunScriptPoll(request, responsePath);
                    break;
                case "get_transform":
                    response = HandleGetTransform(request);
                    break;
                case "get_bounds":
                    response = HandleGetBounds(request);
                    break;
                case "measure_distance":
                    response = HandleMeasureDistance(request);
                    break;
                case "inspect_animation_clip":
                    response = HandleInspectAnimationClip(request);
                    break;
                case "create_animation_clip":
                    response = HandleCreateAnimationClip(request);
                    break;
                case "apply_animation_clip":
                    response = HandleApplyAnimationClip(request);
                    break;
                default:
                    response = BuildError(
                        "EDITOR_CTRL_UNKNOWN_ACTION",
                        $"Unknown action: {request.action}");
                    break;
            }

            return response;
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
            public const int DefaultCapacity = 1000;

            private struct RawEntry
            {
                public string message;
                public string stackTrace;
                public LogType logType;
                public double timestamp;
                public long sequenceId;
                public string requestId;
                public string phase;
            }

            private static RawEntry[] _buffer;
            private static int _head;
            private static int _count;
            private static bool _capturing;
            private static long _nextSequenceId;
            private static string _currentRequestId = string.Empty;
            private static string _phaseSnapshot = "edit";
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
                    _phaseSnapshot = ClassifyCurrentEditorPhase();
                    _capturing = true;
                }
                EditorApplication.update += RefreshEditorPhaseSnapshot;
                Application.logMessageReceivedThreaded += OnLogMessage;
            }

            public static void StopCapture()
            {
                if (!_capturing) return;
                Application.logMessageReceivedThreaded -= OnLogMessage;
                EditorApplication.update -= RefreshEditorPhaseSnapshot;
                lock (_lock)
                {
                    _capturing = false;
                }
            }

            public static bool IsCapturing => _capturing;

            public static void BeginRequest(string requestId)
            {
                lock (_lock) { _currentRequestId = requestId ?? string.Empty; }
            }

            public static void EndRequest(string requestId)
            {
                lock (_lock)
                {
                    if (_currentRequestId == requestId) _currentRequestId = string.Empty;
                }
            }

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
                        timestamp = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds() / 1000.0,
                        sequenceId = _nextSequenceId++,
                        requestId = _currentRequestId,
                        phase = _phaseSnapshot,
                    };
                    _head = (_head + 1) % _buffer.Length;
                    if (_count < _buffer.Length) _count++;
                }
            }

            private static void RefreshEditorPhaseSnapshot()
            {
                string phase = ClassifyCurrentEditorPhase();
                lock (_lock)
                {
                    _phaseSnapshot = phase;
                }
            }

            private static string ClassifyCurrentEditorPhase()
            {
                return ConsoleLogPhaseClassifier.Classify(
                    BuildPipeline.isBuildingPlayer,
                    EditorApplication.isPlayingOrWillChangePlaymode);
            }

            public static (List<ConsoleLogEntry> entries, bool hasMore) GetEntries(
                int maxEntries,
                string logTypeFilter,
                float sinceSeconds,
                string classificationFilter,
                string phaseFilter,
                long sinceSequence,
                string sinceRequestId,
                bool newestFirst,
                long cursorAfterSequence)
            {
                var result = new List<ConsoleLogEntry>();
                bool hasMore = false;
                lock (_lock)
                {
                    if (_buffer == null || _count == 0) return (result, hasMore);

                    double now = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds() / 1000.0;
                    int start = (_head - _count + _buffer.Length) % _buffer.Length;
                    bool hasSequenceSelector = sinceSequence >= 0;
                    bool hasRequestSelector = ConsoleCaptureRequestValidator.UsesRequestIdSelector(
                        sinceSequence, sinceRequestId);
                    bool cursorIsEmpty = newestFirst
                        ? cursorAfterSequence == long.MaxValue
                        : cursorAfterSequence == long.MinValue;
                    bool hasCursorSelector = !hasSequenceSelector && !hasRequestSelector && !cursorIsEmpty;
                    bool hasTimeSelector = !hasSequenceSelector && !hasRequestSelector && !hasCursorSelector && sinceSeconds > 0f;

                    for (int i = 0; i < _count; i++)
                    {
                        int physicalIndex = newestFirst
                            ? (start + _count - 1 - i) % _buffer.Length
                            : (start + i) % _buffer.Length;
                        var entry = _buffer[physicalIndex];

                        if (hasCursorSelector)
                        {
                            if (newestFirst)
                            {
                                if (entry.sequenceId >= cursorAfterSequence) continue;
                            }
                            else
                            {
                                if (entry.sequenceId <= cursorAfterSequence) continue;
                            }
                        }
                        if (hasTimeSelector && (now - entry.timestamp) > sinceSeconds)
                            continue;
                        if (hasSequenceSelector && entry.sequenceId <= sinceSequence)
                            continue;
                        if (hasRequestSelector && entry.requestId != sinceRequestId)
                            continue;
                        if (!MatchesTypeFilter(entry.logType, logTypeFilter))
                            continue;
                        if (!MatchesClassificationFilter(
                                entry.message, entry.stackTrace, entry.logType,
                                classificationFilter))
                            continue;
                        if (!ConsoleLogEntryPredicate.MatchesPhaseFilter(entry.phase, phaseFilter))
                            continue;

                        if (result.Count >= maxEntries)
                        {
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
                            request_id = entry.requestId ?? string.Empty,
                            phase = entry.phase ?? string.Empty,
                        });
                    }
                }
                return (result, hasMore);
            }

            public static bool HasRequestId(string requestId)
            {
                if (string.IsNullOrEmpty(requestId)) return false;
                lock (_lock)
                {
                    if (_buffer == null || _count == 0) return false;
                    int start = (_head - _count + _buffer.Length) % _buffer.Length;
                    for (int i = 0; i < _count; i++)
                    {
                        var entry = _buffer[(start + i) % _buffer.Length];
                        if (entry.requestId == requestId) return true;
                    }
                    return false;
                }
            }

            public static long PeekHighestIngestedSequenceId()
            {
                lock (_lock) { return _nextSequenceId - 1; }
            }

            public static long PeekLowestRetainedSequenceId()
            {
                lock (_lock)
                {
                    if (_buffer == null || _count == 0) return _nextSequenceId;
                    int start = (_head - _count + _buffer.Length) % _buffer.Length;
                    return _buffer[start].sequenceId;
                }
            }

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
            // Issue #52: per-op value-present marker.  ``value`` alone
            // cannot tell an empty-string op value from an absent one;
            // when ``value_present`` is true the handler writes ``value``
            // even when empty, when false the op supplies no value.
            public bool value_present = false;
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
        // ``EditorApplication.update`` callback; the response file is
        // written only after the documented completion signals are
        // observed or the supplied budget is exceeded.  In-flight
        // requests are mirrored to ``SessionState`` so a domain reload
        // (triggered by the recompile itself) does not lose the entry.
        // The post-reload resumer (an ``[InitializeOnLoad]`` hook) walks
        // ``SessionState`` and re-registers each entry on the new
        // AppDomain so completion drainage continues from the same place.
        internal static class PendingAsyncRunner
        {
            private const string SessionStateKey =
                "PrefabSentinel_PendingAsyncRunner_v1";

            [Serializable]
            internal sealed class PersistedEntry
            {
                public string action = string.Empty;
                public string responsePath = string.Empty;
                public string requestJson = string.Empty;
                public long callTimeUnixMs;
                public long deadlineUnixMs;
                public string tempId = string.Empty;
                public string stuckKey = string.Empty;
                public string tempDirAbs = string.Empty;
                public string transportRequestId = string.Empty;
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
            // that owns the entry. Lost across domain reload; the
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
            /// current AppDomain - those subscriptions cannot survive a
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
        // ── Editor state snapshot (#239) ──

        /// <summary>
        /// Snapshot the five editor-state flags surfaced by the
        /// ``get_project_status`` MCP tool.  Pure read of editor APIs;
        /// no side effects.  Adding a field here is a contract change
        /// shared with ``EditorStateSnapshot`` and the Python tool's
        /// ``editor_state`` field.
        /// </summary>
        private static EditorControlResponse HandleGetEditorState()
        {
            var snapshot = new EditorStateSnapshot
            {
                is_playing = EditorApplication.isPlaying,
                is_will_change_playmode = EditorApplication.isPlayingOrWillChangePlaymode,
                is_compiling = EditorApplication.isCompiling,
                is_building_player = BuildPipeline.isBuildingPlayer,
                has_unsaved_changes = HasUnsavedEditorChanges(),
            };
            return BuildSuccess(
                "EDITOR_CTRL_EDITOR_STATE_OK",
                "Editor state snapshot captured.",
                new EditorControlData
                {
                    executed = true,
                    editor_state = snapshot,
                });
        }

        /// <summary>
        /// Issue #40: report whether the editor holds unsaved scene or
        /// Prefab Stage edits.  When a Prefab Stage is active, its preview
        /// scene's ``isDirty`` flag is authoritative — the open background
        /// scene is irrelevant while staging.  Otherwise every loaded
        /// scene is inspected and the result is the OR of their ``isDirty``
        /// flags.  Pure read; no side effects.
        /// </summary>
        private static bool HasUnsavedEditorChanges()
        {
            var stage = PrefabStageUtility.GetCurrentPrefabStage();
            if (stage != null)
                return stage.scene.isDirty;

            for (int i = 0; i < EditorSceneManager.sceneCount; i++)
            {
                if (EditorSceneManager.GetSceneAt(i).isDirty)
                    return true;
            }
            return false;
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

        // Issue H-4: fuzzy suggestion ranking and edit-distance computation
        // are owned by the Unity-free ``SuggestionRanker``; the property-write
        // handler delegates to it directly.

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
