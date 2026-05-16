using System;
using System.Collections.Generic;

// Pure view-allowlist classifier — Unity-free helper exposed for the C# xUnit
// harness. Issue #222 / #309 Phase 3: the screenshot handler's view-acceptance
// gate delegates to ``IsAccepted`` so the decision can be exercised end-to-end
// without dragging in any Unity assembly references.
namespace PrefabSentinel
{
    /// <summary>
    /// Ordinal-equality classifier over the bridge-side accepted-set for the
    /// screenshot view selector. The decision is governed by exact
    /// case-sensitive equality (no whitespace stripping, no case folding,
    /// no culture-sensitive comparison) so the bridge gate matches the
    /// wrapper-side allowlist verbatim.
    /// </summary>
    public static class ScreenshotViewAllowlistClassifier
    {
        /// <summary>
        /// Return true when <paramref name="selector"/> is ordinally equal to
        /// at least one member of <paramref name="acceptedSet"/>; return
        /// false when no member matches or when <paramref name="acceptedSet"/>
        /// is empty.
        ///
        /// The comparison is <see cref="StringComparison.Ordinal"/> so the
        /// classifier rejects case variants (``"Scene"``, ``"GAME"``) and
        /// surrounding-whitespace variants (``" scene"``) and matches the
        /// existing bridge handler's ``string.Equals(..., Ordinal)`` site
        /// verbatim. The handler's pre-conditions exclude a null
        /// <paramref name="acceptedSet"/>; a null reference from the caller
        /// propagates as a <see cref="NullReferenceException"/> rather than
        /// being silently treated as an empty set.
        /// </summary>
        public static bool IsAccepted(string selector, IEnumerable<string> acceptedSet)
        {
            foreach (var accepted in acceptedSet)
            {
                if (string.Equals(selector, accepted, StringComparison.Ordinal))
                {
                    return true;
                }
            }
            return false;
        }

        // Issue #310: the scene-selector literal is owned by the
        // classifier so the screenshot handler's scene/game routing
        // decision and the view-acceptance gate read the same source.
        // A future relaxation of the literal (case fold, whitespace
        // trim) propagates to both sites by changing this helper
        // alone.
        internal const string SceneSelector = "scene";

        /// <summary>
        /// Return true when <paramref name="view"/> is ordinally equal
        /// to the scene-view selector; return false on every other
        /// value (game-view selector, empty input, null, case
        /// variants, whitespace variants).  The comparison is
        /// <see cref="StringComparison.Ordinal"/> consistent with
        /// <see cref="IsAccepted"/>.
        /// </summary>
        public static bool IsSceneView(string view)
        {
            return string.Equals(view, SceneSelector, StringComparison.Ordinal);
        }
    }
}
