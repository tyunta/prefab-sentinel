using System;

// Console-capture filter predicates and phase classification — Unity-free
// decisions extracted from the ConsoleCapture handler and ConsoleLogBuffer
// (issues #117 / #239 / H-3). Only the phase/classification-string predicates
// are extracted; the log-type-typed predicates (MatchesTypeFilter,
// MatchesClassificationFilter) take a UnityEngine.LogType and stay in the
// bridge.
namespace PrefabSentinel
{
    /// <summary>
    /// Filter-support and phase-match predicates for the console capture
    /// surface. All comparisons are ordinal string equality.
    /// </summary>
    internal static class ConsoleLogEntryPredicate
    {
        // Issue #239: ``all`` is the catch-all that admits every entry; the
        // remaining values mirror the per-entry phase tags.
        internal static readonly string[] SupportedPhaseFilters =
            { "all", "edit", "play", "build" };

        // Issue #117: supported classification filter values.
        internal static readonly string[] SupportedClassificationFilters =
            { "all", "non_fatal", "fatal" };

        private const string CatchAllToken = "all";

        /// <summary>
        /// An empty filter or the <c>all</c> catch-all token admits every
        /// entry; otherwise the entry's phase must equal the filter exactly.
        /// </summary>
        public static bool MatchesPhaseFilter(string entryPhase, string filter)
        {
            if (string.IsNullOrEmpty(filter) || filter == CatchAllToken)
            {
                return true;
            }
            return entryPhase == filter;
        }

        /// <summary>
        /// An empty value is accepted; otherwise the value must be a member
        /// of <see cref="SupportedPhaseFilters"/>.
        /// </summary>
        public static bool IsSupportedPhaseFilter(string value)
        {
            return string.IsNullOrEmpty(value)
                || Array.IndexOf(SupportedPhaseFilters, value) >= 0;
        }

        /// <summary>
        /// An empty value is accepted; otherwise the value must be a member
        /// of <see cref="SupportedClassificationFilters"/>.
        /// </summary>
        public static bool IsSupportedClassificationFilter(string value)
        {
            return string.IsNullOrEmpty(value)
                || Array.IndexOf(SupportedClassificationFilters, value) >= 0;
        }
    }

    /// <summary>
    /// Derives the capture phase tag from the two Unity editor-state flags
    /// the bridge reads at ingestion time. The priority order is
    /// build over play over edit because a player build can fire logs while
    /// the editor is in playmode.
    /// </summary>
    internal static class ConsoleLogPhaseClassifier
    {
        public static string Classify(
            bool isBuildingPlayer, bool isPlayingOrWillChangePlaymode)
        {
            if (isBuildingPlayer)
            {
                return "build";
            }
            if (isPlayingOrWillChangePlaymode)
            {
                return "play";
            }
            return "edit";
        }
    }
}
