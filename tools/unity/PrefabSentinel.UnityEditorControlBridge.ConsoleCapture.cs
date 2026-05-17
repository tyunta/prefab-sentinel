using System;
using UnityEngine;

// Console-capture handler — exposes the in-memory ConsoleLogBuffer through the bridge.
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
        private static EditorControlResponse HandleCaptureConsoleLogs(EditorControlRequest request)
        {
            if (!ConsoleLogBuffer.IsCapturing)
                return BuildError("EDITOR_CTRL_CONSOLE_NOT_ACTIVE",
                    "Console log capture is not active. Enable Editor Bridge to start capturing.");

            // Issue #117 / H-3: reject unsupported classification filter
            // values before we touch the buffer. Filter-support membership
            // is owned by the Unity-free ``ConsoleLogEntryPredicate``.
            string classificationFilter = string.IsNullOrEmpty(request.classification_filter)
                ? "all"
                : request.classification_filter;
            if (!ConsoleLogEntryPredicate.IsSupportedClassificationFilter(classificationFilter))
                return BuildError(
                    "EDITOR_CTRL_INVALID_CLASSIFICATION_FILTER",
                    "classification_filter must be one of: "
                    + string.Join(", ", ConsoleLogEntryPredicate.SupportedClassificationFilters));

            // Issue #239: phase filter — same gating shape as the
            // classification filter so unsupported values yield a typed
            // error before the buffer walk.
            string phaseFilter = string.IsNullOrEmpty(request.phase_filter)
                ? "all"
                : request.phase_filter;
            if (!ConsoleLogEntryPredicate.IsSupportedPhaseFilter(phaseFilter))
                return BuildError(
                    "EDITOR_CTRL_INVALID_PHASE_FILTER",
                    "phase_filter must be one of: "
                    + string.Join(", ", ConsoleLogEntryPredicate.SupportedPhaseFilters));

            // Issue #113 / #131 / H-3: ordering keyword, opaque continuation
            // token, and the max-entries bound are validated up front by the
            // Unity-free ``ConsoleCaptureRequestValidator`` so an invalid
            // request short-circuits before the buffer walk.
            ConsoleCaptureValidation validation = ConsoleCaptureRequestValidator.Validate(
                request.order,
                request.cursor,
                request.max_entries,
                ConsoleLogBuffer.PeekHighestIngestedSequenceId(),
                ConsoleLogBuffer.DefaultCapacity);
            if (!validation.Success)
                return BuildError(validation.ErrorCode, validation.ErrorMessage);

            var (entries, hasMore) = ConsoleLogBuffer.GetEntries(
                request.max_entries, request.log_type_filter, request.since_seconds,
                classificationFilter, phaseFilter,
                validation.NewestFirst, validation.CursorAfter);

            string nextCursor = string.Empty;
            if (hasMore && entries.Count > 0)
            {
                long lastSeq = entries[entries.Count - 1].sequence_id;
                nextCursor = ConsoleCaptureRequestValidator.CursorPrefix + lastSeq.ToString(
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
