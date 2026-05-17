using System.Collections.Generic;

// Editor-control action registry — Unity-free single source of truth for the
// supported-action and asynchronous-action sets (issue H-8). The bridge
// dispatcher sources its membership sets from here; routing itself stays in
// the dispatcher switch.
namespace PrefabSentinel
{
    /// <summary>
    /// The catalogue of editor-control actions and their async classification.
    /// </summary>
    internal static class ActionRegistry
    {
        /// <summary>
        /// Actions that write their response file asynchronously (issues
        /// #108 / #118 / #225 / #233).
        /// </summary>
        public static readonly HashSet<string> Async = new HashSet<string>
        {
            "vrcsdk_upload",
            "run_script",
            "editor_recompile_and_wait",
            "execute_menu_item",
            "run_script_submit",
            "run_script_poll",
        };

        /// <summary>All action strings handled by the editor-control bridge.</summary>
        public static readonly HashSet<string> Supported = new HashSet<string>
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
            "get_blend_shapes",
            "set_blend_shape",
            "list_menu_items",
            "execute_menu_item",
            "find_renderers_by_material",
            "editor_rename",
            "editor_add_component",
            "create_udon_program_asset",
            "editor_set_property",
            "safe_save_prefab",
            "editor_set_parent",
            "editor_create_empty",
            "editor_create_primitive",
            "editor_create_ui_element",
            "editor_batch_create",
            "editor_batch_set_property",
            "editor_batch_set_material_property",
            "editor_open_scene",
            "editor_save_scene",
            "editor_batch_add_component",
            "editor_remove_component",
            "editor_create_scene",
            "editor_reflect",
            "run_script",
            "editor_recompile_and_wait",
            "editor_add_udonsharp_component",
            "editor_set_udonsharp_field",
            "editor_wire_persistent_listener",
            "get_editor_state",
            "force_scene_view_refresh",
            "batch_set_blend_shape",
            "open_prefab",
            "close_prefab",
            "run_script_submit",
            "run_script_poll",
            "inspect_animation_clip",
            "create_animation_clip",
            "apply_animation_clip",
        };
    }
}
