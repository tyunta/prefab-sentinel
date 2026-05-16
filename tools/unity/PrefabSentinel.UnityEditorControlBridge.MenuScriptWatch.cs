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
        // Issue #248: walk-root + Editor-segment filter for the
        // implicit-barrier predicate. Unity's editor-folder convention
        // recognises any directory named "Editor" at any depth under
        // Assets/ as an editor-assembly source root, so the walk
        // traverses the Assets root and the per-file filter selects
        // paths whose chain contains an Editor segment. The prior
        // single-root literal hid feature-scoped editor folders from
        // the dirty-source check.
        private const string MenuExecuteAssetsRoot = "Assets";
        private const string MenuExecuteEditorSegment = "Editor";

        // Issue #225: run-script temp-directory token excluded from the
        // editor-source mtime walk. The run-script handler stages
        // freshly-generated .cs files there (``RunScriptTempDir``);
        // including those would fire the barrier on every menu execute
        // that follows a run-script call, since the temp file mtime is
        // always recent.
        private const string MenuExecuteRunScriptTempExclusion =
            "_PrefabSentinelTemp";

        // Issue #225 / #248 / #255: implicit-barrier predicate.
        // Returns true when any C# source under an Editor-named
        // directory segment beneath the Assets root has been written
        // since baseline; the run-script temp area is excluded. Both
        // checks are case-sensitive whole-segment equality (no
        // substring match). I/O failures conservatively return true.
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
                    // Issue #255: both the Editor-segment match and
                    // the run-script temp-area exclusion are
                    // whole-segment equalities on the directory chain.
                    string rel = csAbs.Substring(assetsRootAbs.Length)
                        .TrimStart(separators);
                    string[] segments = rel.Split(
                        separators, StringSplitOptions.RemoveEmptyEntries);
                    bool hasEditorSegment = false;
                    bool inRunScriptTempArea = false;
                    for (int i = 0; i < segments.Length - 1; i++)
                    {
                        if (string.Equals(segments[i],
                                MenuExecuteRunScriptTempExclusion,
                                StringComparison.Ordinal))
                        { inRunScriptTempArea = true; break; }
                        if (string.Equals(segments[i],
                                MenuExecuteEditorSegment,
                                StringComparison.Ordinal))
                        { hasEditorSegment = true; }
                    }
                    if (inRunScriptTempArea || !hasEditorSegment) continue;
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
