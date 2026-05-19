// Unity-free AssetDatabase importer-error line predicate (issue #45).
// The synchronous recompile handler's no-op branch scans the console log
// buffer through this predicate: when an importer error is present the
// no-op response is downgraded from a silent success to a `warning`-
// severity response carrying the offending lines as diagnostics. The
// class references only the base class library so the xUnit harness can
// exercise it directly; runtime behavior of the buffer scan is verified
// via `deploy_bridge`.
using System;

namespace PrefabSentinel
{
    /// <summary>
    /// Classifies a single console-log line as an AssetDatabase importer
    /// error. Unity surfaces the underlying mtime-mismatch import failure
    /// in two shapes — a <c>Build asset version error</c> line (the
    /// SourceAssetDB modification-time mismatch) and an
    /// <c>Import Error Code</c> line (the import-worker warning, e.g.
    /// <c>[Worker0] Import Error Code:(4)</c>). A no-op recompile that
    /// leaves either shape on the console is masking a real failure;
    /// this predicate lets the handler surface it as a diagnostic.
    /// </summary>
    internal static class ImporterErrorClassifier
    {
        // The two recognized importer-error line markers. Matched as a
        // case-sensitive substring because Unity emits these markers
        // verbatim; a benign line carries neither.
        private const string BuildAssetVersionMarker = "Build asset version error";
        private const string ImportErrorCodeMarker = "Import Error Code";

        /// <summary>
        /// Return whether <paramref name="consoleLine"/> is an
        /// AssetDatabase importer-error line. A null or empty line is not
        /// an importer error. Pure predicate — no side effects.
        /// </summary>
        public static bool IsImporterError(string consoleLine)
        {
            if (string.IsNullOrEmpty(consoleLine)) return false;
            return consoleLine.IndexOf(
                       BuildAssetVersionMarker, StringComparison.Ordinal) >= 0
                   || consoleLine.IndexOf(
                       ImportErrorCodeMarker, StringComparison.Ordinal) >= 0;
        }
    }
}
