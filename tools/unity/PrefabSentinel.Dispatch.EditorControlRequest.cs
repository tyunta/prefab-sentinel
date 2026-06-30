// The DTO has null-by-default array fields (a null array means "not
// provided"); ``#nullable disable`` keeps the file warning-clean under both
// the Unity assembly (nullable off) and the test project (nullable on).
#nullable disable
using System;

// Editor-control request DTO — Unity-free value object relocated out of the
// UnityEditorControlBridge partial (issue H-8) so the xUnit harness can
// construct it and pin its field surface without a Unity assembly reference.
// JsonUtility wire round-trip parity stays a Python source-text invariant
// because JsonUtility is a Unity type.
namespace PrefabSentinel
{
[Serializable]
    public sealed class RenderTextureParameters
    {
        public int width = 0;
        public int height = 0;
        public int depth = 0;
        public string format = string.Empty;
        public string read_write = string.Empty;
        public string filter_mode = string.Empty;
        public string wrap_mode = string.Empty;
        public bool mip_map = false;
    }

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
        public string asset_paths_json = string.Empty;
        public int material_index = -1;
        public string material_guid = string.Empty;
        public string material_path = string.Empty;  // asset path alternative to GUID

        // Issue #116: generated asset create/move payload.
        public string asset_type = string.Empty;
        public string source_asset_path = string.Empty;
        public string destination_asset_path = string.Empty;
        public RenderTextureParameters parameters = new RenderTextureParameters();

        // capture_console_logs
        public int max_entries = 200;
        public string log_type_filter = "all"; // "all" | "error" | "warning" | "exception"
        public float since_seconds = 0f;       // 0 = no time filter
        public string order = string.Empty;
        public string cursor = string.Empty;

        // list_children
        public int depth = 1;

        // camera (get_camera / set_camera)
        public float[] camera_pivot = null;      // [x, y, z] pivot point
        public float yaw = float.NaN;           // NaN = keep current
        public float pitch = float.NaN;
        public float size = -1f;                 // SceneView.size; -1 = keep current
        public float[] camera_position = null;   // [x, y, z] camera world coords
        public float[] camera_look_at = null;    // [x, y, z] look-at target
        public int camera_orthographic = -1;     // -1 = keep, 0 = perspective, 1 = ortho

        // get_material_property
        public string property_name = string.Empty; // empty = list all properties

        // set_material_property; also the value carrier for
        // editor_set_property / editor_set_udonsharp_field.
        public string property_value = string.Empty;  // raw JSON string, manually parsed by handler
        public bool property_value_present = false;

        // vrcsdk_upload
        public string target_type = string.Empty;    // "avatar" or "world"
        public string blueprint_id = string.Empty;    // existing VRC asset ID
        public string description = string.Empty;     // empty = no change
        public string tags = string.Empty;            // JSON array string, empty = no change
        public string release_status = string.Empty;  // "public" | "private", empty = no change
        public bool confirm = false;                  // dry-run gate
        public string platforms = string.Empty;  // JSON array: "[\"windows\",\"android\"]"
        public bool force_original = false;       // break Prefab Instance before saving
        public string protect_components_json = string.Empty;

        // Phase 2: BlendShape
        public string filter = string.Empty;
        public string blend_shape_name = string.Empty;
        public float blend_shape_weight = 0f;

        // Phase 2: Menu
        public string menu_path = string.Empty;
        public bool assume_compiled = false;

        // Phase 4: Rename + AddComponent + Udon
        public string new_name = string.Empty;
        public string parent_hierarchy_path = string.Empty;
        public string component_type = string.Empty;
        public int component_index = -1;

        // Phase 5: SetProperty + SaveAsPrefab
        public string object_reference = string.Empty;

        // Issue #112: generic SerializedObject-backed property tools.
        public string property_path = string.Empty;
        public string root_property_path = string.Empty;
        public int cap = 50;
        public bool serialized_property_bool_value = false;
        public bool serialized_property_bool_value_present = false;
        public int serialized_property_int_value = 0;
        public bool serialized_property_int_value_present = false;
        public long serialized_property_long_value = 0L;
        public bool serialized_property_long_value_present = false;
        public float serialized_property_float_value = 0f;
        public bool serialized_property_float_value_present = false;
        public string serialized_property_string_value = string.Empty;
        public bool serialized_property_string_value_present = false;
        public string serialized_property_enum_name = string.Empty;
        public bool serialized_property_enum_name_present = false;
        public int serialized_property_enum_index = 0;
        public bool serialized_property_enum_index_present = false;
        public string serialized_property_object_reference_asset_path = string.Empty;
        public bool serialized_property_object_reference_asset_path_present = false;
        public string serialized_property_object_reference_hierarchy_path = string.Empty;
        public bool serialized_property_object_reference_hierarchy_path_present = false;
        public bool serialized_property_object_reference_null = false;
        public int serialized_property_array_size = 0;
        public bool serialized_property_array_size_present = false;

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
        public string code = string.Empty;
        public string change_reason = string.Empty;
        public string temp_id = string.Empty;

        // Issue #70: opt-in compile awareness for ``editor_refresh``.
        public bool wait_for_compile = false;

        // Phase 10: Caller-supplied compile-poll budget (#102)
        public int compile_timeout = 0;

        // Phase 11: Camera reset mode (#112).
        public bool reset_to_defaults = false;

        // Phase 11: Console capture classification filter (#117).
        public string classification_filter = "all";

        // Issue #118: synchronous recompile-and-wait budget, in seconds.
        public float timeout_sec = 0f;

        // Issue #239: phase filter selector for the console capture surface.
        public string phase_filter = "all";
        public long since_sequence = -1;
        public string since_request_id = string.Empty;

        // Issue #119: UdonSharp authoring surface payload.
        public string fields_json = string.Empty;
        public string field_name = string.Empty;
        public string values_json = string.Empty;
        public bool values_json_present = false;
        public int expected_length = -1;

        // ``editor_wire_persistent_listener`` payload.
        public string event_property_name = string.Empty;
        public string target_path = string.Empty;
        public string method = string.Empty;
        public string arg = string.Empty;

        // Geometry read surface (#98).
        public string bounds_source = "auto";
        public bool include_children = true;
        public string distance_mode = "pivot";

        // Issue #195: ``editor_create_ui_element`` payload.
        public string ui_rect_json = string.Empty;
        public string ui_properties_json = string.Empty;

        // Issue #249: caller-supplied region argument for ``capture_screenshot``.
        public string crop_roi = string.Empty;

        // Issue #84: target-oriented capture mode payload for ``capture_screenshot``.
        public string target = string.Empty;
        public string angle = string.Empty;
        public string target_mode = "auto";
        public float padding_ratio = 0.10f;
        public string projection = "auto";
        public string fit_mode = "max_axis";
        public string bounds_policy = "all_visible_renderers";

        // Issue #241: caller-supplied pagination knobs for ``get_blend_shapes``.
        public int offset = 0;
        public int limit = 200;

        // Issue #240: batch blend-shape payload.
        public string shapes_json = string.Empty;

        // Issue #236: Prefab Stage response payload.
        public bool save_on_close = true;

        // Issue #233: poll-surface inputs.
        public string request_id = string.Empty;
        public bool cleanup_on_timeout = false;

        // Issue #243 / #53: AnimationClip authoring payload.
        public string curves_json = string.Empty;
        public string target_hierarchy_path = string.Empty;
    }
}
