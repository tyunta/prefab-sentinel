using System;
using System.IO;
using System.Text.RegularExpressions;
using UnityEditor;
using UnityEngine;

namespace PrefabSentinel
{
    /// <summary>
    /// Asynchronous run-script partial (issue #233) — owns the
    /// ``run_script_submit`` and ``run_script_poll`` handlers.  Submit
    /// stages a C# snippet under a bridge-generated opaque per-request
    /// identifier and registers an asynchronous completion job whose
    /// terminal artefact is ``{watchDir}/{request_id}.complete.json``
    /// without waiting for compilation; poll reads that file or queries
    /// the registry.  The synchronous ``run_script`` handler in
    /// ``RunScriptCompile.cs`` remains in place for short-running tasks.
    /// </summary>
    public static partial class UnityEditorControlBridge
    {
        // Defence-in-depth bridge-side gate on the per-request identifier
        // shape; the Python wrapper rejects malformed identifiers
        // pre-bridge but the bridge runs the same regex so a direct
        // watch-dir caller (no wrapper) cannot supply a path-traversing
        // identifier that would land outside the completion-file area.
        private static readonly Regex RunScriptSubmitRequestIdRe =
            new Regex("^[0-9a-f]{32}$", RegexOptions.Compiled);

        private static EditorControlResponse HandleRunScriptSubmit(
            EditorControlRequest request, string responsePath)
        {
            if (string.IsNullOrEmpty(request.code))
                return BuildError(
                    "EDITOR_CTRL_RUN_SCRIPT_COMPILE",
                    "run_script_submit requires a non-empty `code` field.");

            // Generate an opaque per-request identifier.  Lower-case
            // hex (32 chars) matches the Python-side shape gate so a
            // round-trip through the wrapper validator does not reject
            // our own identifiers.
            string id = Guid.NewGuid().ToString("N");
            long acceptedAt = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds();

            string watchDir = Path.GetDirectoryName(responsePath);
            string completionFile = Path.Combine(
                watchDir, $"{id}.complete.json");

            // Stage the snippet to the same temp directory as the
            // synchronous run-script handler so the existing per-frame
            // compile poll and the startup cleanup hook recognise the
            // file; the per-request identifier becomes the temp id so
            // there is no collision with a concurrent synchronous call.
            string tempDirAbs = Path.Combine(
                Directory.GetCurrentDirectory(),
                RunScriptTempDir.Replace('/', Path.DirectorySeparatorChar));
            string scriptAbs = Path.Combine(tempDirAbs, id + ".cs");
            string metaAbs = scriptAbs + ".meta";

            try
            {
                if (!Directory.Exists(tempDirAbs))
                    Directory.CreateDirectory(tempDirAbs);
                File.WriteAllText(scriptAbs, request.code);
            }
            catch (Exception stagingEx)
            {
                // Mirror the synchronous run-script handler's redaction
                // policy: the MCP client receives a fixed surface-
                // identifying message; the full detail goes to the
                // Unity console for local triage.
                Debug.LogWarning(
                    $"[PrefabSentinel] HandleRunScriptSubmit: temp script staging failed at '{scriptAbs}': {stagingEx}");
                return BuildError(
                    "EDITOR_CTRL_RUN_SCRIPT_COMPILE",
                    "run_script_submit: failed to stage the temp script.");
            }

            int compilePollMs = request.compile_timeout > 0
                ? request.compile_timeout
                : RunScriptCompileTimeoutMs;
            long callTimeMs = acceptedAt;
            long deadlineMs = callTimeMs + compilePollMs + RunScriptEntryTypeTimeoutMs;

            // The completion file is the entry's response path; the
            // post-reload ``RunScriptPollFrame`` writes the inner result
            // there.  ``request.code`` is used by the snippet hash for
            // stuck detection, mirroring the synchronous handler.
            string stuckKey = "id:" + id;
            var entry = new PendingAsyncRunner.PersistedEntry
            {
                action = "run_script_submit",
                responsePath = completionFile,
                requestJson = JsonUtility.ToJson(request),
                callTimeUnixMs = callTimeMs,
                deadlineUnixMs = deadlineMs,
                tempId = id,
                stuckKey = stuckKey,
                tempDirAbs = tempDirAbs,
            };

            // Issue #68: hand the compile observation to the shared
            // ``ScheduleCompileBarrier`` mechanism.  A snippet that fails
            // to compile records a compile-failure response — carrying the
            // real compiler diagnostics — to the completion artefact
            // before the compile-poll budget elapses; a snippet that
            // compiles leaves the persisted entry in place so the startup
            // resumer installs ``RunScriptPollFrame`` post-reload.
            Action writeCompilePending = () =>
            {
                PendingAsyncRunner.Complete(completionFile);
                CleanupRunScriptTempFiles(scriptAbs, metaAbs);
                WriteResponse(completionFile, RunScriptCompilePendingResponse(
                    stuckKey, id, tempDirAbs,
                    "Script compilation did not complete within the bounded "
                    + "poll; a domain reload may still be pending. Retry the "
                    + "snippet once Unity finishes compiling."));
            };

            // The compile trigger runs synchronously inside
            // ScheduleCompileBarrier, so a rejected ``AssetDatabase.Refresh``
            // resolves before this method returns.  A schedule failure must
            // surface as the synchronous error envelope on the request's own
            // response path — not as an ``accepted`` envelope the caller
            // would then have to poll — so it is recorded here and returned.
            bool scheduleFailed = false;

            ScheduleCompileBarrier(new CompileBarrierSpec
            {
                preReloadEntry = entry,
                persistPreReloadEntry = true,
                deadlineMs = deadlineMs,
                compileTrigger = () =>
                    AssetDatabase.Refresh(ImportAssetOptions.ForceSynchronousImport),
                onCompileFailed = errors =>
                {
                    PendingAsyncRunner.Complete(completionFile);
                    CleanupRunScriptTempFiles(scriptAbs, metaAbs);
                    WriteResponse(completionFile, BuildError(
                        "EDITOR_CTRL_RUN_SCRIPT_COMPILE",
                        $"run_script_submit: the snippet reported {errors.Count} "
                        + "compile error(s).",
                        new EditorControlData
                        {
                            temp_id = id,
                            executed = false,
                            errors = errors.ToArray(),
                        }));
                },
                onCompiled = () => { },
                onNoAssemblyCompiled = writeCompilePending,
                onDeadlineExceeded = writeCompilePending,
                onScheduleFailure = () =>
                {
                    scheduleFailed = true;
                    PendingAsyncRunner.Complete(completionFile);
                    CleanupRunScriptTempFiles(scriptAbs, metaAbs);
                },
            });

            if (scheduleFailed)
                return BuildError(
                    "EDITOR_CTRL_RUN_SCRIPT_COMPILE",
                    "run_script_submit: AssetDatabase.Refresh failed before "
                    + "compile poll.",
                    new EditorControlData { temp_id = id, executed = false });

            return BuildSuccess(
                "EDITOR_CTRL_RUN_SCRIPT_SUBMIT_ACCEPTED",
                "run_script_submit accepted; poll for completion.",
                data: new EditorControlData
                {
                    executed = false,
                    request_id = id,
                    accepted_at = acceptedAt,
                    status = "pending",
                });
        }

        private static EditorControlResponse HandleRunScriptPoll(
            EditorControlRequest request, string responsePath)
        {
            if (string.IsNullOrEmpty(request.request_id))
                return BuildError(
                    "EDITOR_CTRL_RUN_SCRIPT_UNKNOWN_REQUEST",
                    "run_script_poll requires a non-empty request_id.");
            // Defence-in-depth: reject malformed identifiers before any
            // path is composed so a wrapper-less direct caller cannot
            // traverse out of the watch directory.
            if (!RunScriptSubmitRequestIdRe.IsMatch(request.request_id))
                return BuildError(
                    "EDITOR_CTRL_RUN_SCRIPT_UNKNOWN_REQUEST",
                    $"run_script_poll: request_id '{request.request_id}' is not a 32-char lower-case hex token.");

            string watchDir = Path.GetDirectoryName(responsePath);
            string completionFile = Path.Combine(
                watchDir, $"{request.request_id}.complete.json");
            if (File.Exists(completionFile))
            {
                try
                {
                    string body = File.ReadAllText(completionFile);
                    // The completion file is the inner EditorControlResponse
                    // serialised by ``RunScriptPollFrame``.  Map its
                    // ``data`` fields onto the outer poll response so
                    // callers see ``data.stdout`` as the actual script
                    // output rather than a raw JSON blob; the inner
                    // success flag drives the poll status (``completed``
                    // when the inner run succeeded, ``failed`` otherwise).
                    EditorControlResponse inner = null;
                    try
                    {
                        inner = JsonUtility.FromJson<EditorControlResponse>(body);
                    }
                    catch (Exception parseEx)
                    {
                        Debug.LogWarning(
                            $"[PrefabSentinel] HandleRunScriptPoll: failed to parse completion file '{completionFile}': {parseEx.Message}");
                    }
                    if (inner != null && inner.data != null)
                    {
                        string innerStdout = inner.data.stdout ?? string.Empty;
                        string innerMessage = string.IsNullOrEmpty(inner.message)
                            ? "run_script_poll: job completed."
                            : inner.message;
                        // Issue #68: a submit that failed to compile records
                        // the real compiler diagnostics in the completion
                        // artefact's ``data.errors``. Copy them onto the
                        // outer poll response so a ``failed`` poll surfaces
                        // why the snippet failed rather than an empty list.
                        return BuildSuccess(
                            "EDITOR_CTRL_RUN_SCRIPT_POLL_COMPLETED",
                            innerMessage,
                            data: new EditorControlData
                            {
                                executed = inner.data.executed,
                                request_id = request.request_id,
                                status = inner.success ? "completed" : "failed",
                                stdout = innerStdout,
                                errors = inner.data.errors ?? Array.Empty<string>(),
                            });
                    }
                    // Parse failure: surface the raw body so callers can
                    // still inspect the unparseable completion artefact.
                    return BuildSuccess(
                        "EDITOR_CTRL_RUN_SCRIPT_POLL_COMPLETED",
                        "run_script_poll: completion file present but unparseable; raw body surfaced via stdout.",
                        data: new EditorControlData
                        {
                            executed = true,
                            request_id = request.request_id,
                            status = "completed",
                            stdout = body,
                        });
                }
                catch (Exception ex)
                {
                    Debug.LogWarning(
                        $"[PrefabSentinel] HandleRunScriptPoll: failed to read completion file '{completionFile}': {ex}");
                    return BuildError(
                        "EDITOR_CTRL_RUN_SCRIPT_UNKNOWN_REQUEST",
                        "run_script_poll: completion file unreadable.");
                }
            }
            // Cleanup on timeout: when the bridge deadline elapsed and
            // the caller asked for teardown, remove the temp script
            // and the registered poll so the in-flight job is
            // finalised; report failed in the same call.
            if (request.cleanup_on_timeout)
            {
                string tempDirAbs = Path.Combine(
                    Directory.GetCurrentDirectory(),
                    RunScriptTempDir.Replace('/', Path.DirectorySeparatorChar));
                string scriptAbs = Path.Combine(tempDirAbs, request.request_id + ".cs");
                string metaAbs = scriptAbs + ".meta";
                PendingAsyncRunner.Complete(completionFile);
                try { if (File.Exists(scriptAbs)) File.Delete(scriptAbs); }
                catch (Exception ex)
                {
                    Debug.LogWarning(
                        $"[PrefabSentinel] HandleRunScriptPoll: cleanup of '{scriptAbs}' failed: {ex.Message}");
                }
                try { if (File.Exists(metaAbs)) File.Delete(metaAbs); }
                catch (Exception ex)
                {
                    Debug.LogWarning(
                        $"[PrefabSentinel] HandleRunScriptPoll: cleanup of '{metaAbs}' failed: {ex.Message}");
                }
                return BuildError(
                    "EDITOR_RUN_SCRIPT_SUBMIT_TIMEOUT",
                    "run_script_poll: deadline elapsed; staging torn down.");
            }
            return BuildSuccess(
                "EDITOR_CTRL_RUN_SCRIPT_POLL_PENDING",
                "run_script_poll: job is still pending.",
                data: new EditorControlData
                {
                    executed = false,
                    request_id = request.request_id,
                    status = "pending",
                });
        }
    }
}
