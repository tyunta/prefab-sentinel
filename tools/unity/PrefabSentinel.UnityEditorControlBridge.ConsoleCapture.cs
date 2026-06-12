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

            string classificationFilter = string.IsNullOrEmpty(request.classification_filter)
                ? "all"
                : request.classification_filter;
            if (!ConsoleLogEntryPredicate.IsSupportedClassificationFilter(classificationFilter))
                return BuildError(
                    "EDITOR_CTRL_INVALID_CLASSIFICATION_FILTER",
                    "classification_filter must be one of: "
                    + string.Join(", ", ConsoleLogEntryPredicate.SupportedClassificationFilters));

            string phaseFilter = string.IsNullOrEmpty(request.phase_filter)
                ? "all"
                : request.phase_filter;
            if (!ConsoleLogEntryPredicate.IsSupportedPhaseFilter(phaseFilter))
                return BuildError(
                    "EDITOR_CTRL_INVALID_PHASE_FILTER",
                    "phase_filter must be one of: "
                    + string.Join(", ", ConsoleLogEntryPredicate.SupportedPhaseFilters));

            long highestSequence = ConsoleLogBuffer.PeekHighestIngestedSequenceId();
            ConsoleCaptureValidation validation = ConsoleCaptureRequestValidator.Validate(
                request.order,
                request.cursor,
                request.max_entries,
                highestSequence,
                ConsoleLogBuffer.DefaultCapacity,
                request.since_sequence,
                request.since_request_id);
            if (!validation.Success)
                return BuildError(validation.ErrorCode, validation.ErrorMessage);

            bool requestIdSelectorActive = ConsoleCaptureRequestValidator.UsesRequestIdSelector(
                request.since_sequence, request.since_request_id);
            bool knownRequestId = !requestIdSelectorActive
                || ConsoleLogBuffer.HasRequestId(request.since_request_id);

            var (entries, hasMore) = ConsoleLogBuffer.GetEntries(
                request.max_entries, request.log_type_filter, request.since_seconds,
                classificationFilter, phaseFilter,
                request.since_sequence, request.since_request_id,
                validation.NewestFirst, validation.CursorAfter);

            if (!knownRequestId)
                return BuildError(
                    "EDITOR_CTRL_UNKNOWN_REQUEST_ID",
                    $"No console entries were captured for request id '{request.since_request_id}'.");

            string nextCursor = string.Empty;
            if (hasMore && entries.Count > 0)
            {
                long lastSeq = entries[entries.Count - 1].sequence_id;
                nextCursor = ConsoleCaptureRequestValidator.CursorPrefix + lastSeq.ToString(
                    System.Globalization.CultureInfo.InvariantCulture);
            }

            var response = BuildSuccess("EDITOR_CTRL_CONSOLE_OK",
                $"Captured {entries.Count} log entries",
                data: new EditorControlData
                {
                    total_entries = entries.Count,
                    entries = entries.ToArray(),
                    read_only = true,
                    executed = true,
                    next_cursor = nextCursor,
                });

            long lowest = ConsoleLogBuffer.PeekLowestRetainedSequenceId();
            bool sequenceSelectorDropped = request.since_sequence >= 0
                && lowest > request.since_sequence + 1;
            bool cursorSelectorDropped = !string.IsNullOrEmpty(request.cursor)
                && validation.CursorAfter >= 0
                && lowest > validation.CursorAfter + 1;
            if (sequenceSelectorDropped || cursorSelectorDropped)
            {
                response.diagnostics = new[]
                {
                    new EditorControlDiagnostic
                    {
                        code = "EDITOR_CTRL_CONSOLE_BUFFER_RESET",
                        severity = "warning",
                        detail = "Requested console sequence is older than the retained ring-buffer window.",
                        evidence = $"lowest_retained_sequence={lowest}",
                    },
                };
                response.severity = "warning";
            }

            return response;
        }
    }
}
