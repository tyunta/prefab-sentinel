using System;
using System.IO;
using UnityEngine;

// MenuScriptWatch — editor-script change detector for the menu-execute implicit barrier (#262).
namespace PrefabSentinel
{
    /// <summary>
    /// MenuScriptWatch partial (issue #262) — owns the editor-script
    /// change detector that the implicit-barrier predicate consults
    /// before deciding whether a menu execute can take the fast path.
    /// Splitting this off keeps Menu.cs inside the per-partial line
    /// budget while leaving the menu-execute baseline timestamp
    /// co-located with the handlers that read and write it.
    /// </summary>
    public static partial class UnityEditorControlBridge
    {
        // Issue #248: walk-root for the implicit-barrier predicate. Unity's
        // editor-folder convention recognises any directory named "Editor"
        // at any depth under Assets/ as an editor-assembly source root, so
        // the walk traverses the Assets root and the per-file filter
        // (EditorScriptPathClassifier) selects qualifying paths.
        private const string MenuExecuteAssetsRoot = "Assets";

        // Issue #225 / #248 / #255 / H-2: implicit-barrier predicate.
        // Returns true when any C# source under an Editor-named directory
        // segment beneath the Assets root has been written since baseline;
        // the run-script temp area is excluded. The directory scan and mtime
        // comparison stay here; per-path Editor/temporary-area classification
        // is owned by the Unity-free ``EditorScriptPathClassifier``. I/O
        // failures conservatively return true.
        private static bool HasEditorScriptChangedSince(long sinceUnixMs)
        {
            try
            {
                string assetsRootAbs = Path.Combine(
                    Directory.GetCurrentDirectory(),
                    MenuExecuteAssetsRoot.Replace('/', Path.DirectorySeparatorChar));
                if (!Directory.Exists(assetsRootAbs)) return false;
                char[] separators = new char[] { Path.DirectorySeparatorChar, '/' };
                foreach (string csAbs in Directory.GetFiles(
                    assetsRootAbs, "*.cs", SearchOption.AllDirectories))
                {
                    string rel = csAbs.Substring(assetsRootAbs.Length)
                        .TrimStart(separators);
                    if (!EditorScriptPathClassifier.IsEditorSourcePath(rel)) continue;
                    long mtimeMs = new DateTimeOffset(
                        File.GetLastWriteTimeUtc(csAbs))
                        .ToUnixTimeMilliseconds();
                    if (mtimeMs > sinceUnixMs) return true;
                }
                return false;
            }
            catch (Exception ex)
            {
                Debug.LogWarning(
                    $"[PrefabSentinel] HasEditorScriptChangedSince failed: {ex.Message}");
                return true;
            }
        }
    }
}
