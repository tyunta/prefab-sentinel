using System;

// Stage hierarchy path normalization — Unity-free decision extracted from
// the active-stage branch of ResolveGameObjectInActiveStage (PrefabStage.cs,
// issue #18 / H-10 T1). The resolver reads the request path, delegates
// leading-slash normalization here, and resolves the result against the
// active Prefab Stage root.
namespace PrefabSentinel
{
    /// <summary>
    /// Normalizes a hierarchy path for resolution against an active Prefab
    /// Stage root. Absolute-style paths (``/Root/Child``) are accepted as a
    /// convenience for callers that mirror Unity's hierarchy log format;
    /// the leading slash is stripped because <c>Transform.Find</c> rejects
    /// it. The component is Unity-free (base-class-library only) so the C#
    /// xUnit harness can exercise it directly.
    /// </summary>
    internal static class StageHierarchyPathLogic
    {
        /// <summary>
        /// Return <paramref name="hierarchyPath"/> with a single leading
        /// forward slash removed when one is present, and the input
        /// unchanged otherwise. Exactly one slash is removed: ``//Root``
        /// normalizes to ``/Root``, not ``Root``. A null argument
        /// propagates a <see cref="NullReferenceException"/> rather than
        /// being treated as an empty path, matching the documented null
        /// contract of comparable extracted helpers.
        /// </summary>
        public static string NormalizeStagePath(string hierarchyPath)
        {
            return hierarchyPath.StartsWith("/", StringComparison.Ordinal)
                ? hierarchyPath.Substring(1)
                : hierarchyPath;
        }
    }
}
