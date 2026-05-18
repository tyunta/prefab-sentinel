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

        // set_material_property; also the value carrier for
        // editor_set_property / editor_set_udonsharp_field.
        public string property_value = string.Empty;  // raw JSON string, manually parsed by handler

        // Issue #52: value-present marker for editor_set_property /
        // editor_set_udonsharp_field.  ``property_value`` cannot itself
        // distinguish "write the empty string" from "no value supplied"
        // — an empty string is a legal write.  When ``property_value_present``
        // is true the handler writes ``property_value`` even when empty;
        // when false ``property_value`` is treated as no value supplied.
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
        // Issue #225: caller-supplied opt-out for the implicit
        // recompile barrier on menu execution. When true, the
        // handler skips the barrier and invokes the menu item
        // synchronously — only safe when the caller has already
        // verified compile state. Defaults to false so accidental
        // omission keeps the barrier active.
        public bool assume_compiled = false;

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

        // Issue #45: caller-supplied reimport-target paths for the
        // fire-and-return recompile (``recompile_scripts``).  When
        // non-empty, HandleRecompileScripts force-reimports each listed
        // asset path with ImportAssetOptions.ForceUpdate before scheduling
        // compilation, so an externally edited script the caller names —
        // including scripts outside Assets/Editor — round-trips through
        // Unity's import pipeline reliably.  A null / empty array means
        // "no targeted reimport"; this replaces the old blanket
        // ``force_reimport`` bool (#106) which only covered Assets/Editor.
        public string[] reimport_paths = null;

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

        // Issue #239: phase filter selector for the console capture
        // surface.  ``all`` (default), ``edit``, ``play``, or ``build``.
        // The handler validates the selector against the
        // ``ConsoleLogEntryPredicate.SupportedPhaseFilters`` set before
        // touching the buffer; unsupported values yield the dedicated
        // ``EDITOR_CTRL_INVALID_PHASE_FILTER`` error envelope.
        public string phase_filter = "all";

        // Issue #119: UdonSharp authoring surface payload.
        // ``editor_add_udonsharp_component`` consumes ``component_type``
        // (already declared above) plus ``fields_json``: a JSON object
        // mapping serialized-field name to a string-encoded value
        // (parsed through the same unified WritePropertyValue layer as
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

        // Issue #249: caller-supplied region argument for
        // ``capture_screenshot``.  Accepts one of the four named
        // presets (``eye_left | eye_right | mouth | auto_face``) or
        // a comma-separated pixel quadruple ``"x,y,w,h"``.  Empty =
        // no region, capture full frame.
        public string crop_roi = string.Empty;

        // Issue #241: caller-supplied pagination knobs for
        // ``get_blend_shapes``.  Defaults reproduce the pre-pagination
        // behaviour for callers that have not opted in.
        public int offset = 0;
        public int limit = 200;

        // Issue #240: batch blend-shape payload — JSON array of
        // ``{"name": string, "weight": float}`` entries forwarded as
        // a single JSON string because Unity's JsonUtility cannot
        // bind heterogeneous value shapes.
        public string shapes_json = string.Empty;

        // Issue #236: ``close_prefab`` save flag.  Defaults to
        // ``true`` so callers can omit it on the common path.
        // Name matches the Python-side ``save_on_close`` argument.
        public bool save_on_close = true;

        // Issue #233: poll-surface inputs.  ``request_id`` identifies
        // the asynchronous job to poll; ``cleanup_on_timeout`` asks
        // the bridge to tear down the staging area on deadline elapse.
        public string request_id = string.Empty;
        public bool cleanup_on_timeout = false;

        // Issue #243: AnimationClip authoring payload.  ``target_dir``
        // and ``animation_clip_name`` locate where to write a new clip;
        // ``curves_json`` carries the curve specification as a JSON
        // array; ``target_hierarchy_path`` locates the live GameObject
        // for the apply surface.
        public string target_dir = string.Empty;
        public string animation_clip_name = string.Empty;
        public string curves_json = string.Empty;
        public string target_hierarchy_path = string.Empty;
    }
}
