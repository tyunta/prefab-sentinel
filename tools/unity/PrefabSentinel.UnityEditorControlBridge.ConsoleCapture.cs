using System;
using UnityEngine;

namespace PrefabSentinel
{
    /// <summary>
    /// Console-capture handler partial: wraps <see cref="UnityEditorControlBridge.ConsoleLogBuffer"/>
    /// behind the <c>capture_console_logs</c> action surface, validating the
    /// classification filter, ordering keyword, opaque cursor token, and
    /// max-entries bound before forwarding to the buffer.
    /// </summary>
    public static partial class UnityEditorControlBridge
    {
        private const string ConsoleCursorPrefix = "seq:";
        private static readonly string[] ConsoleSupportedOrders = { "newest_first", "oldest_first" };

        private static EditorControlResponse HandleCaptureConsoleLogs(EditorControlRequest request)
        {
            if (!ConsoleLogBuffer.IsCapturing)
                return BuildError("EDITOR_CTRL_CONSOLE_NOT_ACTIVE",
                    "Console log capture is not active. Enable Editor Bridge to start capturing.");

            // Issue #117: reject unsupported classification filter values
            // before we touch the buffer, so callers learn the contract
            // through a typed error instead of silent default behaviour.
            string classificationFilter = string.IsNullOrEmpty(request.classification_filter)
                ? "all"
                : request.classification_filter;
            if (!ConsoleLogBuffer.IsSupportedClassificationFilter(classificationFilter))
                return BuildError(
                    "EDITOR_CTRL_INVALID_CLASSIFICATION_FILTER",
                    "classification_filter must be one of: "
                    + string.Join(", ", ConsoleLogBuffer.SupportedClassificationFilters));

            // Issue #113: ordering keyword (default "newest_first") and
            // opaque continuation token. Both are validated up front so
            // an invalid request short-circuits before the buffer walk.
            string order = string.IsNullOrEmpty(request.order)
                ? "newest_first"
                : request.order;
            if (Array.IndexOf(ConsoleSupportedOrders, order) < 0)
                return BuildError(
                    "EDITOR_CTRL_INVALID_ORDER",
                    "order must be one of: " + string.Join(", ", ConsoleSupportedOrders));
            bool newestFirst = order == "newest_first";

            // Empty cursor = fresh page. Sentinels match the half-open
            // bounds GetEntries uses: long.MaxValue means "anything below"
            // for newest_first; long.MinValue means "anything above" for
            // oldest_first.
            long cursorAfter = newestFirst ? long.MaxValue : long.MinValue;
            string cursor = request.cursor ?? string.Empty;
            if (cursor.Length > 0)
            {
                if (!cursor.StartsWith(ConsoleCursorPrefix, StringComparison.Ordinal))
                    return BuildError(
                        "EDITOR_CTRL_INVALID_CURSOR",
                        $"cursor token must start with '{ConsoleCursorPrefix}' (opaque continuation token from a previous response).");
                string body = cursor.Substring(ConsoleCursorPrefix.Length);
                if (!long.TryParse(body, System.Globalization.NumberStyles.Integer,
                        System.Globalization.CultureInfo.InvariantCulture, out long parsed))
                    return BuildError(
                        "EDITOR_CTRL_INVALID_CURSOR",
                        $"cursor token '{cursor}' could not be parsed as an ingestion position.");
                long highest = ConsoleLogBuffer.PeekHighestIngestedSequenceId();
                // Empty buffer ⇒ highest = -1; reporting "[0, -1]" would be
                // misleading, so emit a dedicated message before the range
                // comparison runs.
                if (highest < 0)
                    return BuildError(
                        "EDITOR_CTRL_INVALID_CURSOR",
                        $"cursor token '{cursor}' cannot be resolved: no entries have been ingested yet.");
                if (parsed < 0 || parsed > highest)
                    return BuildError(
                        "EDITOR_CTRL_INVALID_CURSOR",
                        $"cursor token '{cursor}' references an ingestion position outside the captured range [0, {highest}].");
                cursorAfter = parsed;
            }

            // Issue #131: reject ``max_entries`` outside the inclusive
            // [1, ConsoleLogBuffer.DefaultCapacity] range before we touch
            // the buffer.  The upper bound mirrors the published capacity
            // because the ring buffer can never return more entries than
            // it has retained; the lower bound rejects 0 / negative
            // values that would degenerate into a no-op or an error.
            int maxEntries = request.max_entries;
            if (maxEntries < 1 || maxEntries > ConsoleLogBuffer.DefaultCapacity)
                return BuildError(
                    "EDITOR_CTRL_MAX_ENTRIES_OUT_OF_RANGE",
                    $"max_entries={maxEntries} is outside the inclusive range "
                    + $"[1, {ConsoleLogBuffer.DefaultCapacity}] (buffered console entries).");
            var (entries, hasMore) = ConsoleLogBuffer.GetEntries(
                maxEntries, request.log_type_filter, request.since_seconds,
                classificationFilter, newestFirst, cursorAfter);

            string nextCursor = string.Empty;
            if (hasMore && entries.Count > 0)
            {
                long lastSeq = entries[entries.Count - 1].sequence_id;
                nextCursor = ConsoleCursorPrefix + lastSeq.ToString(
                    System.Globalization.CultureInfo.InvariantCulture);
            }

            return BuildSuccess("EDITOR_CTRL_CONSOLE_OK",
                $"Captured {entries.Count} log entries",
                data: new EditorControlData
                {
                    total_entries = entries.Count,
                    entries = entries.ToArray(),
                    read_only = true,
                    executed = true,
                    next_cursor = nextCursor,
                });
        }
    }
}
