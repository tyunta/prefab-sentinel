using System;
using System.Collections.Generic;
using UnityEditor;
using UnityEditor.Compilation;
using UnityEngine;

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

        // Issue #225: timestamp baseline for the mtime walk. Updated
        // on each successful menu execute. ``LastDomainReloadUtc``
        // (RunScriptCompile partial) is the post-reload baseline;
        // the walk takes the max of the two so a freshly-loaded
        // AppDomain does not falsely report "no changes".  The detector
        // that consumes this baseline lives in the MenuScriptWatch
        // partial (issue #262 split).
        private static long MenuExecuteLastSuccessfulUnixMs = 0L;

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

        private static EditorControlResponse HandleExecuteMenuItem(
            EditorControlRequest request, string responsePath)
        {
            if (string.IsNullOrEmpty(request.menu_path))
                return BuildError("EDITOR_CTRL_MISSING_PATH", "menu_path is required for execute_menu_item");

            foreach (var denied in MenuDenyPrefixes)
            {
                if (request.menu_path.StartsWith(denied, System.StringComparison.Ordinal))
                    return BuildError("EDITOR_CTRL_MENU_DENIED",
                        $"Menu item denied by safety policy: {request.menu_path}");
            }

            // Issue #225: implicit-barrier decision. Fast path when
            // assume_compiled is asserted OR no Editor scripts changed
            // since the prior execute AND the Editor is not compiling.
            // Otherwise schedule an async barrier that reuses the
            // recompile-and-wait pipeline events.
            long mtimeBaselineMs = Math.Max(
                MenuExecuteLastSuccessfulUnixMs,
                new DateTimeOffset(LastDomainReloadUtc).ToUnixTimeMilliseconds());
            bool scriptChanged = HasEditorScriptChangedSince(mtimeBaselineMs);
            bool isCompiling = EditorApplication.isCompiling;
            bool fastPath = request.assume_compiled
                || (!scriptChanged && !isCompiling);

            if (fastPath)
            {
                bool result = EditorApplication.ExecuteMenuItem(request.menu_path);
                if (!result)
                    return BuildError("EDITOR_CTRL_MENU_NOT_FOUND",
                        $"Menu item not found or not executable: {request.menu_path}");

                MenuExecuteLastSuccessfulUnixMs =
                    DateTimeOffset.UtcNow.ToUnixTimeMilliseconds();
                var data = new EditorControlData
                {
                    executed = true,
                    recompile_waited = false,
                };
                return BuildSuccess("EDITOR_CTRL_MENU_EXEC_OK",
                    $"Menu item executed: {request.menu_path}",
                    data: data);
            }

            ScheduleMenuExecuteBarrier(request, responsePath);
            return null;
        }

        // Issue #225: schedule the implicit-barrier async pipeline.
        // Reuses the recompile-and-wait pipeline events so the surface
        // inherits ``EDITOR_CTRL_RECOMPILE_FAILED`` /
        // ``EDITOR_CTRL_RECOMPILE_TIMEOUT`` verbatim and schedules the
        // menu execute on the post-reload tick.
        private static void ScheduleMenuExecuteBarrier(
            EditorControlRequest request, string responsePath)
        {
            long callTimeMs = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds();
            long deadlineMs = callTimeMs
                + (long)(RecompileAndWaitDefaultTimeoutSec * 1000f);
            int callTimeReloadCount = PendingAsyncRunner.AssemblyReloadCount;
            bool resolved = false;
            bool compiledAny = false;
            var compileErrors = new List<string>();

            Action<string, CompilerMessage[]> onAsmFinished = null;
            Action<object> onPipelineFinished = null;

            onAsmFinished = (asmPath, messages) =>
            {
                bool asmHadError = false;
                if (messages != null)
                {
                    foreach (var msg in messages)
                    {
                        if (msg.type == CompilerMessageType.Error)
                        {
                            asmHadError = true;
                            compileErrors.Add(
                                string.IsNullOrEmpty(msg.file)
                                    ? msg.message
                                    : $"{msg.file}({msg.line},{msg.column}): {msg.message}");
                        }
                    }
                }
                if (!asmHadError) compiledAny = true;
            };

            void Unsubscribe()
            {
                CompilationPipeline.assemblyCompilationFinished -= onAsmFinished;
                CompilationPipeline.compilationFinished -= onPipelineFinished;
            }

            onPipelineFinished = _ =>
            {
                if (resolved) return;
                resolved = true;
                Unsubscribe();

                if (compileErrors.Count > 0)
                {
                    PendingAsyncRunner.Complete(responsePath);
                    WriteResponse(responsePath, BuildError(
                        "EDITOR_CTRL_RECOMPILE_FAILED",
                        $"execute_menu_item: {compileErrors.Count} compile error(s).",
                        new EditorControlData
                        {
                            executed = false,
                            errors = compileErrors.ToArray(),
                            recompile_waited = true,
                        }));
                    return;
                }

                if (!compiledAny)
                {
                    InvokeMenuItemAndWriteResponse(
                        request.menu_path, responsePath, recompileWaited: true);
                    PendingAsyncRunner.Complete(responsePath);
                    return;
                }

                // ≥1 assembly compiled. Switch to the post-reload wait
                // so the menu item runs against the freshly loaded
                // assemblies.
                PendingAsyncRunner.Complete(responsePath);
                var reloadEntry = new PendingAsyncRunner.PersistedEntry
                {
                    action = "execute_menu_item",
                    responsePath = responsePath,
                    requestJson = JsonUtility.ToJson(request),
                    callTimeUnixMs = callTimeMs,
                    deadlineUnixMs = deadlineMs,
                };
                EditorApplication.CallbackFunction reloadPoll = null;
                reloadPoll = () =>
                {
                    long nowMs = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds();
                    if (nowMs > deadlineMs)
                    {
                        PendingAsyncRunner.Complete(responsePath);
                        WriteResponse(responsePath, BuildError(
                            "EDITOR_CTRL_RECOMPILE_TIMEOUT",
                            "execute_menu_item: timed out after domain reload "
                            + "before AssemblyReloadCount advanced."));
                        return;
                    }
                    if (PendingAsyncRunner.AssemblyReloadCount
                        <= callTimeReloadCount) return;

                    PendingAsyncRunner.Complete(responsePath);
                    InvokeMenuItemAndWriteResponse(
                        request.menu_path, responsePath, recompileWaited: true);
                };
                PendingAsyncRunner.Register(reloadEntry, reloadPoll);
            };

            CompilationPipeline.assemblyCompilationFinished += onAsmFinished;
            CompilationPipeline.compilationFinished += onPipelineFinished;

            EditorApplication.CallbackFunction prePoll = null;
            prePoll = () =>
            {
                if (resolved) return;
                long nowMs = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds();
                if (nowMs <= deadlineMs) return;
                resolved = true;
                Unsubscribe();
                PendingAsyncRunner.Complete(responsePath);
                WriteResponse(responsePath, BuildError(
                    "EDITOR_CTRL_RECOMPILE_TIMEOUT",
                    "execute_menu_item: timed out before "
                    + "CompilationPipeline.compilationFinished fired."));
            };

            var preEntry = new PendingAsyncRunner.PersistedEntry
            {
                action = "execute_menu_item",
                responsePath = responsePath,
                requestJson = JsonUtility.ToJson(request),
                callTimeUnixMs = callTimeMs,
                deadlineUnixMs = deadlineMs,
            };
            PendingAsyncRunner.RegisterTransient(preEntry, prePoll);

            try
            {
                CompilationPipeline.RequestScriptCompilation();
            }
            catch (Exception ex)
            {
                resolved = true;
                Unsubscribe();
                PendingAsyncRunner.Complete(responsePath);
                Debug.LogWarning(
                    $"[PrefabSentinel] HandleExecuteMenuItem: RequestScriptCompilation rejected: {ex}");
                WriteResponse(responsePath, BuildError(
                    "EDITOR_CTRL_RECOMPILE_SCHEDULE_FAILED",
                    "execute_menu_item: failed to schedule compilation."));
            }
        }

        private static void InvokeMenuItemAndWriteResponse(
            string menuPath, string responsePath, bool recompileWaited)
        {
            bool result = EditorApplication.ExecuteMenuItem(menuPath);
            if (!result)
            {
                WriteResponse(responsePath, BuildError(
                    "EDITOR_CTRL_MENU_NOT_FOUND",
                    $"Menu item not found or not executable: {menuPath}"));
                return;
            }
            MenuExecuteLastSuccessfulUnixMs =
                DateTimeOffset.UtcNow.ToUnixTimeMilliseconds();
            WriteResponse(responsePath, BuildSuccess(
                "EDITOR_CTRL_MENU_EXEC_OK",
                $"Menu item executed: {menuPath}",
                data: new EditorControlData
                {
                    executed = true,
                    recompile_waited = recompileWaited,
                }));
        }
    }
}
