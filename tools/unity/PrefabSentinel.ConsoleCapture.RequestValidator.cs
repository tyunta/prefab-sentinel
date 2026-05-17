using System;
using System.Globalization;

// Console-capture request validation — Unity-free decision extracted from
// HandleCaptureConsoleLogs (issues #113 / #131 / H-3). The handler reads the
// request fields and the buffer's highest ingested sequence id, passes them as
// plain values, and applies the resulting cursor sentinel to the buffer walk.
namespace PrefabSentinel
{
    /// <summary>
    /// Result of <see cref="ConsoleCaptureRequestValidator.Validate"/>:
    /// a success flag, the rejection code and message on failure, the
    /// post-validation cursor sentinel, and the resolved ordering direction.
    /// </summary>
    internal readonly struct ConsoleCaptureValidation
    {
        public bool Success { get; }
        public string ErrorCode { get; }
        public string ErrorMessage { get; }
        public long CursorAfter { get; }
        public bool NewestFirst { get; }

        private ConsoleCaptureValidation(
            bool success, string errorCode, string errorMessage,
            long cursorAfter, bool newestFirst)
        {
            Success = success;
            ErrorCode = errorCode;
            ErrorMessage = errorMessage;
            CursorAfter = cursorAfter;
            NewestFirst = newestFirst;
        }

        public static ConsoleCaptureValidation Accepted(long cursorAfter, bool newestFirst)
        {
            return new ConsoleCaptureValidation(true, string.Empty, string.Empty,
                cursorAfter, newestFirst);
        }

        public static ConsoleCaptureValidation Rejected(string code, string message)
        {
            return new ConsoleCaptureValidation(false, code, message, 0L, false);
        }
    }

    /// <summary>
    /// Validates the <c>capture_console_logs</c> ordering token, opaque
    /// continuation cursor, and entry-count bound before the buffer walk.
    /// </summary>
    internal static class ConsoleCaptureRequestValidator
    {
        internal const string CursorPrefix = "seq:";
        internal const string NewestFirst = "newest_first";
        internal const string OldestFirst = "oldest_first";

        internal const string InvalidOrderCode = "EDITOR_CTRL_INVALID_ORDER";
        internal const string InvalidCursorCode = "EDITOR_CTRL_INVALID_CURSOR";
        internal const string MaxEntriesOutOfRangeCode =
            "EDITOR_CTRL_MAX_ENTRIES_OUT_OF_RANGE";

        /// <summary>
        /// Validate <paramref name="order"/>, <paramref name="cursor"/>, and
        /// <paramref name="maxEntries"/>. An empty order defaults to
        /// newest-first. The cursor sentinel for an empty cursor is
        /// <see cref="long.MaxValue"/> (newest-first) or
        /// <see cref="long.MinValue"/> (oldest-first).
        /// </summary>
        public static ConsoleCaptureValidation Validate(
            string order, string cursor, int maxEntries,
            long highestSeqId, int capacity)
        {
            string resolvedOrder = string.IsNullOrEmpty(order) ? NewestFirst : order;
            if (resolvedOrder != NewestFirst && resolvedOrder != OldestFirst)
            {
                return ConsoleCaptureValidation.Rejected(
                    InvalidOrderCode,
                    "order must be one of: " + NewestFirst + ", " + OldestFirst);
            }
            bool newestFirst = resolvedOrder == NewestFirst;

            long cursorAfter = newestFirst ? long.MaxValue : long.MinValue;
            string cursorToken = cursor ?? string.Empty;
            if (cursorToken.Length > 0)
            {
                if (!cursorToken.StartsWith(CursorPrefix, StringComparison.Ordinal))
                {
                    return ConsoleCaptureValidation.Rejected(
                        InvalidCursorCode,
                        $"cursor token must start with '{CursorPrefix}' "
                        + "(opaque continuation token from a previous response).");
                }
                string body = cursorToken.Substring(CursorPrefix.Length);
                if (!long.TryParse(body, NumberStyles.Integer,
                        CultureInfo.InvariantCulture, out long parsed))
                {
                    return ConsoleCaptureValidation.Rejected(
                        InvalidCursorCode,
                        $"cursor token '{cursorToken}' could not be parsed "
                        + "as an ingestion position.");
                }
                if (highestSeqId < 0)
                {
                    return ConsoleCaptureValidation.Rejected(
                        InvalidCursorCode,
                        $"cursor token '{cursorToken}' cannot be resolved: "
                        + "no entries have been ingested yet.");
                }
                if (parsed < 0 || parsed > highestSeqId)
                {
                    return ConsoleCaptureValidation.Rejected(
                        InvalidCursorCode,
                        $"cursor token '{cursorToken}' references an ingestion "
                        + $"position outside the captured range [0, {highestSeqId}].");
                }
                cursorAfter = parsed;
            }

            if (maxEntries < 1 || maxEntries > capacity)
            {
                return ConsoleCaptureValidation.Rejected(
                    MaxEntriesOutOfRangeCode,
                    $"max_entries={maxEntries} is outside the inclusive range "
                    + $"[1, {capacity}] (buffered console entries).");
            }

            return ConsoleCaptureValidation.Accepted(cursorAfter, newestFirst);
        }
    }
}
