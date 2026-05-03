using System.Collections.Generic;
using UnityEditor;

// Menu enumeration and execution handlers (small, cohesive).
namespace PrefabSentinel
{
    public static partial class UnityEditorControlBridge
    {
        private static readonly string[] MenuDenyPrefixes = new string[]
        {
            "File/New Scene",
            "File/New Project",
            "Assets/Delete",
        };

        private static EditorControlResponse HandleListMenuItems(EditorControlRequest request)
        {
            string prefix = request.filter ?? "";
            var items = new List<MenuItemEntry>();
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

            items.Sort((a, b) => string.Compare(a.path, b.path, System.StringComparison.Ordinal));

            return BuildSuccess("EDITOR_CTRL_MENU_LIST_OK",
                $"Found {items.Count} menu items (total: {totalScanned})",
                data: new EditorControlData
                {
                    menu_items = items.ToArray(),
                    total_entries = totalScanned,
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
    }
}
