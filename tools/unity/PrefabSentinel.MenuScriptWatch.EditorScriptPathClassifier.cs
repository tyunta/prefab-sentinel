using System;

// Editor-script path classification — Unity-free decision extracted from the
// MenuScriptWatch change detector (issues #225 / #248 / #255 / H-2). The
// directory walk and mtime comparison stay in the bridge handler; this class
// owns the per-path Editor/temporary-area classification so it can run in the
// xUnit harness without a Unity assembly reference.
namespace PrefabSentinel
{
    /// <summary>
    /// Classifies a relative path as Editor source for the implicit-barrier
    /// dirty-source check. A path qualifies when its directory chain contains
    /// an <see cref="EditorSegment"/> segment and contains no
    /// <see cref="RunScriptTempSegment"/> segment; the temporary-area
    /// exclusion takes precedence over the Editor match. Segment matching is
    /// whole-segment and case-sensitive (<see cref="StringComparison.Ordinal"/>).
    /// </summary>
    internal static class EditorScriptPathClassifier
    {
        // Unity recognises any directory named "Editor" at any depth under
        // Assets/ as an editor-assembly source root.
        internal const string EditorSegment = "Editor";

        // Issue #225: the run-script handler stages freshly-generated .cs
        // files under this directory; the barrier must not fire on them.
        internal const string RunScriptTempSegment = "_PrefabSentinelTemp";

        private static readonly char[] Separators = { '/', '\\' };

        /// <summary>
        /// Return true when <paramref name="relativePath"/> lies under an
        /// Editor directory segment and outside the run-script temporary
        /// area. The final path component is treated as the file name and is
        /// not inspected. A path inside the temporary area returns false even
        /// when it also has an Editor segment.
        /// </summary>
        public static bool IsEditorSourcePath(string relativePath)
        {
            if (string.IsNullOrEmpty(relativePath))
            {
                return false;
            }

            string[] segments = relativePath.Split(
                Separators, StringSplitOptions.RemoveEmptyEntries);

            bool hasEditorSegment = false;
            // segments[length - 1] is the file name; only directory segments
            // are classified.
            for (int i = 0; i < segments.Length - 1; i++)
            {
                if (string.Equals(segments[i], RunScriptTempSegment, StringComparison.Ordinal))
                {
                    return false;
                }
                if (string.Equals(segments[i], EditorSegment, StringComparison.Ordinal))
                {
                    hasEditorSegment = true;
                }
            }
            return hasEditorSegment;
        }
    }
}
