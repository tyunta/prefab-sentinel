using System;
using System.Collections.Generic;
using System.IO;
using System.Reflection;
using UnityEditor;
using UnityEditor.Compilation;
using UnityEngine;

namespace PrefabSentinel
{
    /// <summary>
    /// Run-script + compile + recompile-and-wait partial.  Owns:
    /// <list type="bullet">
    /// <item><description>Refresh / recompile / run-integration-tests handlers.</description></item>
    /// <item><description>Synchronous recompile-and-wait handler (issue #118) and its
    ///       upper-bound check (issue #134).</description></item>
    /// <item><description>The two-phase ``run_script`` completion detection — the
    ///       pre-reload compile-pending watchdog and the post-reload
    ///       completion poll (issues #108 / #64) — and its
    ///       stuck-detection / temp-area-recovery helpers (issue #116).</description></item>
    /// <item><description>The startup cleanup hook that resumes in-flight async runner
    ///       entries on the new AppDomain after a domain reload.</description></item>
    /// </list>
    /// </summary>
    public static partial class UnityEditorControlBridge
    {
        // ── Handlers shared with Editor refresh / recompile / tests ──

        /// <summary>
        /// Issue #70: ``editor_refresh`` handler.  Without compile
        /// awareness it refreshes the asset database synchronously and
        /// reports refresh-OK — the cheap path the screenshot / deploy
        /// refreshes depend on.  When the caller opts into compile
        /// awareness it observes the refresh-triggered compile through the
        /// shared compile-watch barrier and reports refresh-OK (no
        /// compile), compile-success, or compile-failure with real
        /// compiler diagnostics.
        /// </summary>
        private static EditorControlResponse HandleRefreshAssetDatabase(
            EditorControlRequest request, string responsePath)
        {
            if (!request.wait_for_compile)
            {
                AssetDatabase.Refresh(ImportAssetOptions.ForceSynchronousImport);
                return BuildSuccess("EDITOR_CTRL_REFRESH_OK",
                    "AssetDatabase.Refresh completed",
                    data: new EditorControlData { executed = true });
            }

            long callTimeMs = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds();
            long deadlineMs = callTimeMs
                + (long)(RecompileAndWaitDefaultTimeoutSec * 1000f);
            int callTimeReloadCount = PendingAsyncRunner.AssemblyReloadCount;
            var preEntry = new PendingAsyncRunner.PersistedEntry
            {
                action = "refresh_asset_database",
                responsePath = responsePath,
                requestJson = JsonUtility.ToJson(request),
                callTimeUnixMs = callTimeMs,
                deadlineUnixMs = deadlineMs,
            };

            ScheduleCompileBarrier(new CompileBarrierSpec
            {
                preReloadEntry = preEntry,
                persistPreReloadEntry = false,
                deadlineMs = deadlineMs,
                noCompileGraceWindowMs = CompileBarrierNoCompileGraceWindowMs,
                compileTrigger = () =>
                    AssetDatabase.Refresh(ImportAssetOptions.ForceSynchronousImport),
                onNoCompileObserved = () =>
                {
                    PendingAsyncRunner.Complete(responsePath);
                    WriteResponse(responsePath, BuildSuccess(
                        "EDITOR_CTRL_REFRESH_OK",
                        "AssetDatabase.Refresh completed; no compile was triggered.",
                        new EditorControlData { executed = true }));
                },
                onNoAssemblyCompiled = () =>
                {
                    PendingAsyncRunner.Complete(responsePath);
                    WriteResponse(responsePath, BuildSuccess(
                        "EDITOR_CTRL_REFRESH_OK",
                        "AssetDatabase.Refresh completed; no assembly required "
                        + "compilation.",
                        new EditorControlData { executed = true }));
                },
                onCompileFailed = errors =>
                {
                    PendingAsyncRunner.Complete(responsePath);
                    WriteResponse(responsePath, BuildError(
                        "EDITOR_CTRL_REFRESH_COMPILE_FAILED",
                        $"editor_refresh: the triggered compile reported "
                        + $"{errors.Count} compile error(s).",
                        new EditorControlData
                        {
                            executed = false,
                            errors = errors.ToArray(),
                        }));
                },
                onCompiled = () =>
                {
                    PendingAsyncRunner.Complete(responsePath);
                    var reloadEntry = new PendingAsyncRunner.PersistedEntry
                    {
                        action = "refresh_asset_database",
                        responsePath = responsePath,
                        requestJson = JsonUtility.ToJson(request),
                        callTimeUnixMs = callTimeMs,
                        deadlineUnixMs = deadlineMs,
                    };
                    EditorApplication.CallbackFunction reloadPoll =
                        BuildRecompileReloadWaitPoll(
                            responsePath, deadlineMs, callTimeReloadCount,
                            "editor_refresh: timed out waiting for the "
                            + "post-reload AssemblyReloadCount tick after a "
                            + "compile-aware refresh.",
                            BuildRefreshCompileSuccessReloadComplete(responsePath));
                    PendingAsyncRunner.Register(reloadEntry, reloadPoll);
                },
                onDeadlineExceeded = () =>
                {
                    PendingAsyncRunner.Complete(responsePath);
                    WriteResponse(responsePath, BuildError(
                        "EDITOR_CTRL_REFRESH_COMPILE_TIMEOUT",
                        "editor_refresh: timed out waiting for the triggered "
                        + "compile to finish."));
                },
                onScheduleFailure = () =>
                {
                    PendingAsyncRunner.Complete(responsePath);
                    WriteResponse(responsePath, BuildError(
                        "EDITOR_CTRL_REFRESH_SCHEDULE_FAILED",
                        "editor_refresh: failed to schedule the asset refresh."));
                },
            });

            return null;
        }

        /// <summary>
        /// Post-reload terminal action for a compile-aware ``editor_refresh``
        /// whose triggered compile passed and reloaded the domain (issue
        /// #70).  Drains the import queue synchronously, then writes the
        /// refresh compile-success envelope.
        /// </summary>
        private static Action BuildRefreshCompileSuccessReloadComplete(
            string responsePath)
        {
            return () =>
            {
                DrainImportQueueBestEffort("BuildRefreshCompileSuccessReloadComplete");
                PendingAsyncRunner.Complete(responsePath);
                WriteResponse(responsePath, BuildSuccess(
                    "EDITOR_CTRL_REFRESH_COMPILE_SUCCESS",
                    "editor_refresh: the triggered compile completed "
                    + "successfully.",
                    new EditorControlData { executed = true }));
            };
        }

        private static EditorControlResponse HandleRunIntegrationTests()
        {
            try
            {
                var result = UnityIntegrationTests.RunTestSuite();
                string json = JsonUtility.ToJson(result, true);
                if (result.success)
                    return BuildSuccess("EDITOR_CTRL_TESTS_PASSED", json,
                        data: new EditorControlData { executed = true });
                return BuildError("EDITOR_CTRL_TESTS_FAILED", json);
            }
            catch (Exception ex)
            {
                // Issue #251: align with the leak-safe pattern adopted
                // for the four run-script catch sites under issue #216.
                // The MCP-bound envelope carries a fixed surface-
                // identifying string only; the full exception detail is
                // mirrored to the Unity Console via Debug.LogWarning so
                // a local operator can still triage the failure.
                Debug.LogWarning(
                    $"[PrefabSentinel] HandleRunIntegrationTests: integration-test suite threw: {ex}");
                return BuildError(
                    "EDITOR_CTRL_TESTS_ERROR",
                    "editor_run_tests: integration-test suite threw an exception.");
            }
        }

        // ── Run-script (#74 / #108 / #116) ──

        // Compiles and runs an arbitrary caller-supplied C# snippet inside a
        // fixed temp directory, through the fixed entry point
        // ``PrefabSentinelTempScript.Run()`` (``public static void``).  Temp
        // files are always removed before the response is emitted.

        private const string RunScriptTempDir = "Assets/Editor/_PrefabSentinelTemp";
        private const string RunScriptTypeName = "PrefabSentinelTempScript";
        private const string RunScriptEntryPoint = "Run";
        // Bounded compile-state poll budget: a brief flip of isCompiling
        // immediately after Refresh is normal; we wait up to this many
        // milliseconds for it to settle before reporting COMPILE.
        private const int RunScriptCompileTimeoutMs = 15000;
        // Bounded entry-type retry budget: once compilation settles the
        // newly built assembly may take a moment to load into the AppDomain.
        private const int RunScriptEntryTypeTimeoutMs = 4000;
        private const int RunScriptPollIntervalMs = 50;

        // Issue #116 stuck detection: when the same snippet is rejected as
        // compile-pending twice in a row we trigger the temp-area recovery
        // path.
        private static readonly Dictionary<string, int>
            RunScriptConsecutiveCompilePending =
                new Dictionary<string, int>();
        private const int RunScriptStuckThreshold = 2;

        // Track the time of the most recent domain reload so the diagnostics
        // payload can show how long ago Unity last reloaded scripts.  Set in
        // ``RunScriptStartupCleanup`` since [InitializeOnLoad] static
        // constructors run on every domain reload.
        private static DateTime LastDomainReloadUtc = DateTime.UtcNow;

        // ── Recompile-and-wait (#118 / #134 / H-6) ──

        // Issue H-6: the recompile-and-wait timeout bounds are owned by the
        // Unity-free ``RecompileTimeoutValidator``. This alias is retained
        // because the menu-execute barrier (Menu.cs) reads the default budget.
        private const float RecompileAndWaitDefaultTimeoutSec =
            RecompileTimeoutValidator.DefaultTimeoutSec;

        /// <summary>
        /// Builds the single post-reload reload-wait poll (issue #69).
        /// It waits for the domain reload to finish — observing only the
        /// reload counter and the deadline — and then runs the
        /// caller-supplied reload-complete action.  Issue #203: the
        /// ``CompilationPipeline.compilationFinished`` event is the
        /// authoritative pre-reload terminator (owned by the shared
        /// compile-watch barrier), so this poll never reads the assembly
        /// modification time.  Every reload-wait consumer —
        /// ``editor_recompile_and_wait``, both ``execute_menu_item`` paths,
        /// and the compile-aware ``editor_refresh`` — registers this one
        /// builder with its own reload-complete action, so the poll holds
        /// no handler-specific terminal outcome.
        /// </summary>
        private static EditorApplication.CallbackFunction BuildRecompileReloadWaitPoll(
            string responsePath,
            long deadlineMs,
            int reloadCountThreshold,
            string timeoutDetail,
            Action onReloadComplete)
        {
            EditorApplication.CallbackFunction poll = null;
            poll = () =>
            {
                long nowMs = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds();
                if (RecompileDeadline.HasElapsed(nowMs, deadlineMs))
                {
                    PendingAsyncRunner.Complete(responsePath);
                    WriteResponse(responsePath, BuildError(
                        "EDITOR_CTRL_RECOMPILE_TIMEOUT",
                        timeoutDetail));
                    return;
                }
                if (PendingAsyncRunner.AssemblyReloadCount <= reloadCountThreshold) return;
                onReloadComplete();
            };
            return poll;
        }

        /// <summary>
        /// Issue #235: drain the AssetDatabase import queue synchronously
        /// after a domain reload so a freshly compiled asset path resolves
        /// on the call immediately following a post-reload success
        /// envelope.  The assembly-reload watermark advancing does not
        /// imply the import queue has drained;
        /// ``AssetDatabase.Refresh(ForceSynchronousImport)`` is Unity's
        /// published synchronous drain.  A drain failure does not affect
        /// the envelope outcome — the contract concerns compilation, not
        /// import completion — so the failure is mirrored to the Unity
        /// Console only.
        /// </summary>
        private static void DrainImportQueueBestEffort(string context)
        {
            try
            {
                AssetDatabase.Refresh(ImportAssetOptions.ForceSynchronousImport);
            }
            catch (Exception drainEx)
            {
                Debug.LogWarning(
                    $"[PrefabSentinel] {context}: post-reload "
                    + $"AssetDatabase.Refresh failed: {drainEx}");
            }
        }

        /// <summary>
        /// Issue #69: the post-reload terminal action for
        /// ``editor_recompile_and_wait`` — drains the import queue and
        /// writes the recompile-and-wait success envelope.
        /// </summary>
        private static Action BuildRecompileAndWaitReloadComplete(
            string responsePath)
        {
            return () =>
            {
                DrainImportQueueBestEffort("BuildRecompileAndWaitReloadComplete");
                PendingAsyncRunner.Complete(responsePath);
                WriteResponse(responsePath, BuildSuccess(
                    "EDITOR_CTRL_RECOMPILE_AND_WAIT_OK",
                    "editor_recompile_and_wait: compilation completed and "
                    + "assembly reloaded.",
                    new EditorControlData { executed = true }));
            };
        }

        /// <summary>
        /// Issue #45: scan the console log buffer for AssetDatabase
        /// importer-error lines and return one warning diagnostic per
        /// match. The match decision is owned by the Unity-free
        /// ``ImporterErrorClassifier`` predicate; this method only
        /// snapshots the buffer and maps matches into diagnostics. An
        /// empty list means no importer error was observed.
        /// </summary>
        private static List<EditorControlDiagnostic> CollectImporterErrorDiagnostics()
        {
            var diagnostics = new List<EditorControlDiagnostic>();
            // Snapshot the full buffer (oldest-first, no time / type /
            // phase filter, no sequence/request selector, no cursor) so
            // every captured line is checked.
            var snapshot = ConsoleLogBuffer.GetEntries(
                ConsoleLogBuffer.DefaultCapacity,
                "all",
                0f,
                "all",
                "all",
                -1,
                string.Empty,
                newestFirst: false,
                cursorAfterSequence: long.MinValue);
            foreach (ConsoleLogEntry entry in snapshot.entries)
            {
                if (!ImporterErrorClassifier.IsImporterError(entry.message))
                    continue;
                diagnostics.Add(new EditorControlDiagnostic
                {
                    location = "importer_error",
                    detail = "warning",
                    evidence = entry.message,
                });
            }
            return diagnostics;
        }

        private static EditorControlResponse HandleRecompileAndWait(
            EditorControlRequest request, string responsePath)
        {
            // Issue #134 / H-6: validate the wait budget against the
            // published acceptance range before doing any work. Range
            // validation and the zero-maps-to-default rule are owned by the
            // Unity-free ``RecompileTimeoutValidator``.
            RecompileTimeoutResult timeout =
                RecompileTimeoutValidator.Validate(request.timeout_sec);
            if (!timeout.Success)
            {
                return BuildError(
                    timeout.ErrorCode,
                    $"editor_recompile_and_wait: timeout_sec={request.timeout_sec} "
                    + $"is outside the accepted range "
                    + $"(0, {RecompileTimeoutValidator.MaxTimeoutSec}] "
                    + "(seconds).");
            }

            float budgetSec = timeout.BudgetSec;
            long callTimeMs = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds();
            long deadlineMs = callTimeMs + (long)(budgetSec * 1000f);
            int callTimeReloadCount = PendingAsyncRunner.AssemblyReloadCount;

            // Issue #68: the pre-reload compile observation is owned by the
            // shared ``ScheduleCompileBarrier`` mechanism; this handler
            // supplies only the compile trigger and its terminal outcomes.
            // The pre-reload entry is transient — the pipeline-event
            // subscriptions live on this AppDomain and cannot survive a
            // domain reload; only the ``compiled`` switchover persists a
            // post-reload entry.
            var preEntry = new PendingAsyncRunner.PersistedEntry
            {
                action = "editor_recompile_and_wait",
                responsePath = responsePath,
                requestJson = JsonUtility.ToJson(request),
                callTimeUnixMs = callTimeMs,
                deadlineUnixMs = deadlineMs,
            };

            ScheduleCompileBarrier(new CompileBarrierSpec
            {
                preReloadEntry = preEntry,
                persistPreReloadEntry = false,
                deadlineMs = deadlineMs,
                compileTrigger = () => CompilationPipeline.RequestScriptCompilation(),
                onCompileFailed = errors =>
                {
                    PendingAsyncRunner.Complete(responsePath);
                    WriteResponse(responsePath, BuildError(
                        RecompileOutcomeClassifier.FailedCode,
                        $"editor_recompile_and_wait: {errors.Count} compile error(s).",
                        new EditorControlData
                        {
                            executed = true,
                            errors = errors.ToArray(),
                        }));
                },
                onNoAssemblyCompiled = () => WriteRecompileNoOpResponse(responsePath),
                onCompiled = () =>
                {
                    // At least one assembly compiled. Switch over to the
                    // post-reload wait poll and persist the entry so the
                    // wait survives the domain reload Unity begins
                    // immediately after the pipeline-finished event.
                    PendingAsyncRunner.Complete(responsePath);
                    var reloadEntry = new PendingAsyncRunner.PersistedEntry
                    {
                        action = "editor_recompile_and_wait",
                        responsePath = responsePath,
                        requestJson = JsonUtility.ToJson(request),
                        callTimeUnixMs = callTimeMs,
                        deadlineUnixMs = deadlineMs,
                    };
                    EditorApplication.CallbackFunction reloadPoll =
                        BuildRecompileReloadWaitPoll(
                            responsePath,
                            deadlineMs,
                            callTimeReloadCount,
                            $"editor_recompile_and_wait: timed out after "
                            + $"{budgetSec:F1}s waiting for the post-reload "
                            + "AssemblyReloadCount tick.",
                            BuildRecompileAndWaitReloadComplete(responsePath));
                    PendingAsyncRunner.Register(reloadEntry, reloadPoll);
                },
                onDeadlineExceeded = () =>
                {
                    PendingAsyncRunner.Complete(responsePath);
                    WriteResponse(responsePath, BuildError(
                        "EDITOR_CTRL_RECOMPILE_TIMEOUT",
                        $"editor_recompile_and_wait: timed out after {budgetSec:F1}s "
                        + "before CompilationPipeline.compilationFinished fired."));
                },
                onScheduleFailure = () =>
                {
                    // Issue #214 / H-7: the caller-visible message is the
                    // fixed redacted string owned by ScheduleFailureEnvelope;
                    // the full exception detail goes only to the Unity
                    // console (logged by the barrier).
                    PendingAsyncRunner.Complete(responsePath);
                    WriteResponse(responsePath, BuildError(
                        "EDITOR_CTRL_RECOMPILE_SCHEDULE_FAILED",
                        ScheduleFailureEnvelope.RedactedMessage()));
                },
            });

            return null;
        }

        /// <summary>
        /// Issue #45 / #68: write the ``editor_recompile_and_wait`` no-op
        /// response. A no-op compile can mask an AssetDatabase importer
        /// failure (the "Build asset version error" / "Import Error Code"
        /// shapes); the console buffer is scanned through the Unity-free
        /// ``ImporterErrorClassifier`` predicate and, when importer errors
        /// are present, the response is downgraded from a silent success
        /// to a ``warning``-severity response carrying the offending lines
        /// so the failure is not lost.
        /// </summary>
        private static void WriteRecompileNoOpResponse(string responsePath)
        {
            PendingAsyncRunner.Complete(responsePath);
            var importerErrors = CollectImporterErrorDiagnostics();
            if (importerErrors.Count > 0)
            {
                WriteResponse(responsePath, new EditorControlResponse
                {
                    protocol_version = ProtocolVersion,
                    success = true,
                    severity = "warning",
                    code = RecompileOutcomeClassifier.NoopCode,
                    message = "editor_recompile_and_wait: every assembly "
                        + "was reported as not requiring compilation, but "
                        + $"{importerErrors.Count} AssetDatabase importer "
                        + "error(s) are present on the console — the "
                        + "no-op may be masking an import failure.",
                    data = new EditorControlData { executed = true },
                    diagnostics = importerErrors.ToArray(),
                    operator_context = BuildEditorOperatorContext(),
                });
                return;
            }
            WriteResponse(responsePath, BuildSuccess(
                RecompileOutcomeClassifier.NoopCode,
                "editor_recompile_and_wait: every assembly was reported "
                + "as not requiring compilation; no domain reload occurred.",
                new EditorControlData { executed = true }));
        }

        private static EditorControlResponse HandleRunScript(
            EditorControlRequest request,
            string responsePath,
            string transportRequestId)
        {
            // Issue #108 / #64 / #68: this handler is async / frame-driven.
            // It stages the temp .cs file and hands the compile observation
            // to the shared ``ScheduleCompileBarrier`` mechanism.  A snippet
            // that fails to compile resolves to a compile-error response
            // carrying the real compiler diagnostics before the compile-poll
            // budget elapses; a snippet that compiles triggers a domain
            // reload, after which the startup resumer installs
            // ``RunScriptPollFrame`` as the completion poll that resolves
            // and invokes the freshly compiled temp type.
            if (string.IsNullOrEmpty(request.code))
            {
                return BuildError("EDITOR_CTRL_RUN_SCRIPT_COMPILE",
                    "run_script requires a non-empty `code` field.");
            }

            string tempId = string.IsNullOrEmpty(request.temp_id)
                ? Guid.NewGuid().ToString("N")
                : request.temp_id;

            if (!IsSafeTempId(tempId))
            {
                return BuildError("EDITOR_CTRL_RUN_SCRIPT_BAD_ID",
                    $"temp_id '{tempId}' is not safe (must be alphanumeric + '-_', no path separators or whitespace).");
            }

            // Issue #116: stuck-detection key. Hash the snippet code so the
            // counter survives auto-generated temp_id values (which differ
            // every call) but still distinguishes one stuck snippet from a
            // different one. When the caller supplied an explicit temp_id
            // we honour it as the key.
            string stuckKey = string.IsNullOrEmpty(request.temp_id)
                ? "code:" + ComputeStableHash(request.code)
                : "id:" + request.temp_id;

            string tempDirAbs = Path.Combine(
                Directory.GetCurrentDirectory(),
                RunScriptTempDir.Replace('/', Path.DirectorySeparatorChar));
            string scriptAbs = Path.Combine(tempDirAbs, tempId + ".cs");
            string metaAbs = scriptAbs + ".meta";

            try
            {
                if (!Directory.Exists(tempDirAbs))
                    Directory.CreateDirectory(tempDirAbs);
                File.WriteAllText(scriptAbs, request.code);
            }
            catch (Exception stagingEx)
            {
                // Issue #216: the envelope returned to the MCP client
                // must not embed exception text - Unity exception
                // strings can leak host filesystem paths and OS-level
                // details.  Route the original exception detail to the
                // Unity console only; the MCP client receives a fixed
                // surface-identifying message.
                Debug.LogWarning(
                    $"[PrefabSentinel] HandleRunScript: temp script staging failed at '{scriptAbs}': {stagingEx}");
                return BuildError("EDITOR_CTRL_RUN_SCRIPT_COMPILE",
                    "run_script: failed to stage the temp script.",
                    new EditorControlData { temp_id = tempId, executed = false });
            }

            long callTimeMs = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds();
            // Issue H-6: the compile-poll budget (request override vs bridge
            // default) and the absolute deadline are resolved by the
            // Unity-free ``RunScriptDeadline``.
            RunScriptDeadlineResult deadline = RunScriptDeadline.Resolve(
                request.compile_timeout, RunScriptCompileTimeoutMs,
                callTimeMs, RunScriptEntryTypeTimeoutMs);
            long deadlineMs = deadline.DeadlineMs;

            var entry = new PendingAsyncRunner.PersistedEntry
            {
                action = "run_script",
                responsePath = responsePath,
                requestJson = JsonUtility.ToJson(request),
                callTimeUnixMs = callTimeMs,
                deadlineUnixMs = deadlineMs,
                tempId = tempId,
                stuckKey = stuckKey,
                tempDirAbs = tempDirAbs,
                transportRequestId = transportRequestId,
            };

            // Compile did not produce a runnable assembly within budget:
            // tear down the staging area and write the compile-pending
            // response (shared by the no-assembly-compiled and the
            // deadline-exceeded outcomes).
            Action writeCompilePending = () =>
            {
                PendingAsyncRunner.Complete(responsePath);
                CleanupRunScriptTempFiles(scriptAbs, metaAbs);
                WriteResponse(responsePath, RunScriptCompilePendingResponse(
                    stuckKey, tempId, tempDirAbs,
                    "Script compilation did not complete within the bounded "
                    + "poll; a domain reload may still be pending. Retry after "
                    + "Unity finishes compiling. If the freshly compiled type "
                    + "still cannot be located, run the snippet through "
                    + "`editor_execute_menu_item` against a persistent editor "
                    + "helper script committed under `Assets/Editor/`."));
            };

            ScheduleCompileBarrier(new CompileBarrierSpec
            {
                preReloadEntry = entry,
                // The entry must survive the domain reload that a compiled
                // snippet triggers so the startup resumer can install
                // ``RunScriptPollFrame``.
                persistPreReloadEntry = true,
                deadlineMs = deadlineMs,
                compileTrigger = () =>
                    AssetDatabase.Refresh(ImportAssetOptions.ForceSynchronousImport),
                onCompileFailed = errors =>
                {
                    PendingAsyncRunner.Complete(responsePath);
                    CleanupRunScriptTempFiles(scriptAbs, metaAbs);
                    WriteResponse(responsePath, BuildError(
                        "EDITOR_CTRL_RUN_SCRIPT_COMPILE",
                        $"run_script: the snippet reported {errors.Count} "
                        + "compile error(s).",
                        new EditorControlData
                        {
                            temp_id = tempId,
                            executed = false,
                            errors = errors.ToArray(),
                        }));
                },
                // A compiled snippet leaves the persisted entry in place;
                // the post-reload resumer installs ``RunScriptPollFrame``,
                // which resolves the temp type and invokes ``Run()``.
                onCompiled = () => { },
                onNoAssemblyCompiled = writeCompilePending,
                onDeadlineExceeded = writeCompilePending,
                onScheduleFailure = () =>
                {
                    PendingAsyncRunner.Complete(responsePath);
                    CleanupRunScriptTempFiles(scriptAbs, metaAbs);
                    WriteResponse(responsePath, BuildError(
                        "EDITOR_CTRL_RUN_SCRIPT_COMPILE",
                        "run_script: AssetDatabase.Refresh failed before compile poll.",
                        new EditorControlData { temp_id = tempId, executed = false }));
                },
            });

            return null;
        }

        /// <summary>
        /// Issue #64: post-reload completion poll for an in-flight
        /// ``run_script`` / ``run_script_submit`` request, installed by
        /// the startup resumer after the domain reload.  Completion is
        /// the resolution of the freshly compiled temp script type; there
        /// is no assembly-modification-time gate, so an editor-only
        /// snippet is detected exactly like a runtime one.  Invokes the
        /// entry point and writes the response; cleans up the temp .cs /
        /// .cs.meta files on every termination path (success, runtime
        /// exception, compile timeout, recovery).
        /// </summary>
        private static void RunScriptPollFrame(
            PendingAsyncRunner.PersistedEntry entry,
            string scriptAbs,
            string metaAbs)
        {
            long nowMs = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds();
            string tempId = entry.tempId;
            string stuckKey = entry.stuckKey;
            string tempDirAbs = entry.tempDirAbs;
            string responsePath = entry.responsePath;
            EditorControlRequest persistedRequest = JsonUtility.FromJson<EditorControlRequest>(entry.requestJson);
            string sourceCode = persistedRequest.code;

            if (nowMs > entry.deadlineUnixMs)
            {
                PendingAsyncRunner.Complete(responsePath);
                CleanupRunScriptTempFiles(scriptAbs, metaAbs);
                EditorControlResponse pending = RunScriptCompilePendingResponse(
                    stuckKey, tempId, tempDirAbs,
                    "Script compilation did not complete within the bounded poll; " +
                    "a domain reload may still be pending or the freshly compiled type " +
                    "could not be located. Retry after Unity finishes compiling. " +
                    "If the freshly compiled type still cannot be located, run the snippet " +
                    "through `editor_execute_menu_item` against a persistent editor helper " +
                    "script committed under `Assets/Editor/`.");
                WriteResponse(responsePath, pending);
                return;
            }

            if (EditorApplication.isCompiling) return;

            Type scriptType = FindTempScriptType();
            if (scriptType == null) return;

            MethodInfo runMethod = scriptType.GetMethod(
                RunScriptEntryPoint,
                BindingFlags.Public | BindingFlags.Static);
            if (runMethod == null)
            {
                PendingAsyncRunner.Complete(responsePath);
                CleanupRunScriptTempFiles(scriptAbs, metaAbs);
                WriteResponse(responsePath, BuildError(
                    "EDITOR_CTRL_RUN_SCRIPT_COMPILE",
                    $"Entry point '{RunScriptTypeName}.{RunScriptEntryPoint}()' not found " +
                    "(must be `public static Run()` with a void or primitive return).",
                    new EditorControlData { temp_id = tempId, executed = false }));
                return;
            }

            System.IO.TextWriter originalOut = Console.Out;
            var buffer = new System.IO.StringWriter();
            Console.SetOut(buffer);
            EditorControlResponse response;
            Output.BeginCapture();
            ConsoleLogBuffer.BeginRequest(entry.transportRequestId);
            try
            {
                object returnObject = runMethod.Invoke(null, null);
                RunScriptOutputSnapshot outputSnapshot = Output.EndCapture();
                RunScriptConsecutiveCompilePending.Remove(stuckKey);
                if (!RunScriptValue.TryCreate(returnObject, out RunScriptValue returnValue))
                {
                    response = BuildError(
                        "EDITOR_CTRL_RUN_SCRIPT_OUTPUT_UNSUPPORTED",
                        "run_script: return value is not a JSON-safe primitive or primitive array.",
                        new EditorControlData
                        {
                            temp_id = tempId,
                            executed = true,
                            stdout = buffer.ToString(),
                            outputs = outputSnapshot.Outputs,
                            unsupported_output_key = "return_value",
                            path_hints = WslPathHintDetector.FindHints(sourceCode),
                        });
                }
                else if (outputSnapshot.HasUnsupportedOutput)
                {
                    response = BuildError(
                        "EDITOR_CTRL_RUN_SCRIPT_OUTPUT_UNSUPPORTED",
                        $"run_script: output '{outputSnapshot.UnsupportedKey}' is not a JSON-safe primitive or primitive array.",
                        new EditorControlData
                        {
                            temp_id = tempId,
                            executed = true,
                            stdout = buffer.ToString(),
                            return_value = returnValue,
                            outputs = outputSnapshot.Outputs,
                            unsupported_output_key = outputSnapshot.UnsupportedKey,
                            path_hints = WslPathHintDetector.FindHints(sourceCode),
                        });
                }
                else
                {
                    response = BuildSuccess("EDITOR_CTRL_RUN_SCRIPT_OK",
                        $"PrefabSentinelTempScript.Run() completed (temp_id={tempId}).",
                        new EditorControlData
                        {
                            temp_id = tempId,
                            executed = true,
                            stdout = buffer.ToString(),
                            return_value = returnValue,
                            outputs = outputSnapshot.Outputs,
                            path_hints = WslPathHintDetector.FindHints(sourceCode),
                        });
                }
            }
            catch (TargetInvocationException tie)
            {
                RunScriptOutputSnapshot outputSnapshot = Output.EndCapture();
                Exception inner = tie.InnerException ?? tie;
                Debug.LogWarning(
                    $"[PrefabSentinel] RunScriptPollFrame: Run() threw (TargetInvocationException): {inner}");
                response = BuildError("EDITOR_CTRL_RUN_SCRIPT_RUNTIME",
                    "run_script: Run() threw a runtime exception.",
                    new EditorControlData
                    {
                        temp_id = tempId,
                        executed = true,
                        stdout = buffer.ToString(),
                        outputs = outputSnapshot.Outputs,
                        exception = RunScriptExceptionSummary.FromException(inner),
                        path_hints = WslPathHintDetector.FindHints(sourceCode, inner),
                    });
            }
            catch (Exception ex)
            {
                RunScriptOutputSnapshot outputSnapshot = Output.EndCapture();
                Debug.LogWarning(
                    $"[PrefabSentinel] RunScriptPollFrame: Run() threw: {ex}");
                response = BuildError("EDITOR_CTRL_RUN_SCRIPT_RUNTIME",
                    "run_script: Run() threw a runtime exception.",
                    new EditorControlData
                    {
                        temp_id = tempId,
                        executed = true,
                        stdout = buffer.ToString(),
                        outputs = outputSnapshot.Outputs,
                        exception = RunScriptExceptionSummary.FromException(ex),
                        path_hints = WslPathHintDetector.FindHints(sourceCode, ex),
                    });
            }
            finally
            {
                ConsoleLogBuffer.EndRequest(entry.transportRequestId);
                Console.SetOut(originalOut);
            }

            PendingAsyncRunner.Complete(responsePath);
            CleanupRunScriptTempFiles(scriptAbs, metaAbs);
            WriteResponse(responsePath, AttachRunScriptPathHintDiagnostics(response));
        }

        private static EditorControlResponse AttachRunScriptPathHintDiagnostics(EditorControlResponse response)
        {
            if (response == null || response.data == null || response.data.path_hints == null
                || response.data.path_hints.Length == 0)
                return response;

            var diagnostics = new List<EditorControlDiagnostic>();
            if (response.diagnostics != null) diagnostics.AddRange(response.diagnostics);
            foreach (var hint in response.data.path_hints)
            {
                diagnostics.Add(new EditorControlDiagnostic
                {
                    code = "EDITOR_CTRL_RUN_SCRIPT_WSL_PATH",
                    severity = "warning",
                    detail = "WSL mounted-drive path detected in run-script input or exception text.",
                    evidence = hint.detected_path,
                });
            }
            response.diagnostics = diagnostics.ToArray();
            if (response.success) response.severity = "warning";
            return response;
        }

        private static void CleanupRunScriptTempFiles(string scriptAbs, string metaAbs)
        {
            TryDeleteFile(scriptAbs);
            TryDeleteFile(metaAbs);
            try { AssetDatabase.Refresh(ImportAssetOptions.ForceSynchronousImport); }
            catch (Exception refreshEx)
            {
                // Issue #252: interpolate the full caught exception
                // detail rather than the message-only form so the stack
                // trace and inner-exception chain reach the Unity
                // Console, aligning with the four run-script catch
                // sites fixed under issue #216.
                Debug.LogWarning(
                    $"[PrefabSentinel] HandleRunScript: post-run AssetDatabase.Refresh failed: {refreshEx}");
            }
        }

        // ── Run-script stuck detection helpers (issue #116) ──

        /// <summary>
        /// Build the compile-pending response (or the recovery response on
        /// the second consecutive stuck rejection of the same snippet).
        /// Always attaches the diagnostics payload (compilation flag,
        /// temp-folder file list, last domain-reload timestamp) so the
        /// caller can act without rerunning the snippet.
        /// </summary>
        private static EditorControlResponse RunScriptCompilePendingResponse(
            string stuckKey, string tempId, string tempDirAbs, string baseMessage)
        {
            int prior;
            RunScriptConsecutiveCompilePending.TryGetValue(stuckKey, out prior);
            RunScriptConsecutiveCompilePending[stuckKey] = prior + 1;

            EditorControlData data = BuildRunScriptDiagnosticsData(tempId, tempDirAbs);

            // Issue #234 / H-6: the response code (recovery once the
            // incremented stuck count reaches the threshold, dedicated
            // compile-timeout code otherwise) is owned by the Unity-free
            // ``RunScriptCompilePendingCodeSelector``.
            string code = RunScriptCompilePendingCodeSelector.SelectCode(
                prior, RunScriptStuckThreshold);

            if (code == RunScriptCompilePendingCodeSelector.RecoveryCode)
            {
                RunScriptRecoverTempArea(tempDirAbs);
                RunScriptConsecutiveCompilePending.Remove(stuckKey);
                EditorControlData recovered = BuildRunScriptDiagnosticsData(tempId, tempDirAbs);
                return new EditorControlResponse
                {
                    protocol_version = ProtocolVersion,
                    success = false,
                    severity = "warning",
                    code = code,
                    message = "Script compile appeared stuck; ran recovery cleanup. Retry the script.",
                    data = recovered,
                    operator_context = BuildEditorOperatorContext(),
                };
            }

            return BuildError(code, baseMessage, data);
        }

        /// <summary>
        /// Snapshot the diagnostics facts surfaced on every compile-pending
        /// response: ``EditorApplication.isCompiling``, the current temp
        /// directory contents, and the last recorded domain-reload time.
        /// </summary>
        private static EditorControlData BuildRunScriptDiagnosticsData(
            string tempId, string tempDirAbs)
        {
            string[] tempFiles = Array.Empty<string>();
            try
            {
                if (Directory.Exists(tempDirAbs))
                    tempFiles = Directory.GetFiles(tempDirAbs);
            }
            catch (Exception ex)
            {
                Debug.LogWarning(
                    $"[PrefabSentinel] BuildRunScriptDiagnosticsData: failed to list temp dir '{tempDirAbs}': {ex.Message}");
            }

            return new EditorControlData
            {
                temp_id = tempId,
                executed = false,
                diagnostic_compiling = EditorApplication.isCompiling,
                diagnostic_temp_files = tempFiles,
                diagnostic_last_domain_reload =
                    LastDomainReloadUtc.ToString("o", System.Globalization.CultureInfo.InvariantCulture),
            };
        }

        /// <summary>
        /// Recovery: delete every ``.cs`` / ``.cs.meta`` in the temp dir and
        /// request a fresh synchronous import so Unity drops the stale
        /// references. Used by the stuck-detection path; the next call can
        /// re-create its temp script from a clean slate.
        /// </summary>
        private static void RunScriptRecoverTempArea(string tempDirAbs)
        {
            try
            {
                if (!Directory.Exists(tempDirAbs)) return;
                foreach (string path in Directory.GetFiles(tempDirAbs, "*.cs"))
                    TryDeleteFile(path);
                foreach (string path in Directory.GetFiles(tempDirAbs, "*.cs.meta"))
                    TryDeleteFile(path);
            }
            catch (Exception ex)
            {
                Debug.LogWarning(
                    $"[PrefabSentinel] RunScriptRecoverTempArea: failed to enumerate temp dir '{tempDirAbs}': {ex.Message}");
            }
            try
            {
                AssetDatabase.Refresh(ImportAssetOptions.ForceSynchronousImport);
            }
            catch (Exception ex)
            {
                Debug.LogWarning(
                    $"[PrefabSentinel] RunScriptRecoverTempArea: AssetDatabase.Refresh failed: {ex.Message}");
            }
        }

        /// <summary>
        /// Stable, deterministic hash of the snippet contents — used as the
        /// stuck-detection key when the caller did not pin a ``temp_id``.
        /// FNV-1a 64-bit; we only need collision resistance across the few
        /// snippets a single editor session might produce.
        /// </summary>
        private static string ComputeStableHash(string text)
        {
            if (string.IsNullOrEmpty(text)) return "0";
            unchecked
            {
                ulong hash = 0xcbf29ce484222325UL;
                foreach (char c in text)
                {
                    hash ^= c;
                    hash *= 0x100000001b3UL;
                }
                return hash.ToString("x16");
            }
        }

        private static bool IsSafeTempId(string id)
        {
            if (string.IsNullOrEmpty(id))
                return false;
            foreach (char c in id)
            {
                bool ok = (c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z')
                          || (c >= '0' && c <= '9') || c == '-' || c == '_';
                if (!ok)
                    return false;
            }
            return true;
        }

        private static Type FindTempScriptType()
        {
            foreach (var asm in AppDomain.CurrentDomain.GetAssemblies())
            {
                Type t = asm.GetType(RunScriptTypeName, throwOnError: false, ignoreCase: false);
                if (t != null)
                    return t;
            }
            return null;
        }

        private static void TryDeleteFile(string path)
        {
            try { if (File.Exists(path)) File.Delete(path); }
            catch (Exception ex)
            {
                Debug.LogWarning(
                    $"[PrefabSentinel] TryDeleteFile: failed to delete '{path}': {ex.Message}");
            }
        }

        /// <summary>
        /// Editor-startup cleanup: removes any ``.cs`` / ``.cs.meta`` leftovers
        /// from crashed ``run_script`` invocations in the temp directory.
        /// Non-recursive; only the fixed file extensions are touched.
        /// </summary>
        [InitializeOnLoad]
        internal static class RunScriptStartupCleanup
        {
            static RunScriptStartupCleanup()
            {
                LastDomainReloadUtc = DateTime.UtcNow;
                EditorApplication.delayCall += Cleanup;
                EditorApplication.delayCall += ResumePendingAsyncRunners;
            }

            private static void Cleanup()
            {
                try
                {
                    string dir = Path.Combine(
                        Directory.GetCurrentDirectory(),
                        RunScriptTempDir.Replace('/', Path.DirectorySeparatorChar));
                    if (!Directory.Exists(dir))
                        return;
                    HashSet<string> pendingTempIds = new HashSet<string>();
                    foreach (var entry in PendingAsyncRunner.ReadPersisted())
                    {
                        // Issue #233: include ``run_script_submit`` so
                        // its staged temp .cs is preserved across the
                        // startup cleanup pass while the async poll
                        // still has a pending completion to write.
                        if ((entry.action == "run_script"
                                || entry.action == "run_script_submit")
                            && !string.IsNullOrEmpty(entry.tempId))
                            pendingTempIds.Add(entry.tempId);
                    }
                    foreach (string path in Directory.GetFiles(dir, "*.cs"))
                    {
                        string id = Path.GetFileNameWithoutExtension(path);
                        if (pendingTempIds.Contains(id)) continue;
                        TryDeleteFile(path);
                    }
                    foreach (string path in Directory.GetFiles(dir, "*.cs.meta"))
                    {
                        string id = Path.GetFileNameWithoutExtension(
                            Path.GetFileNameWithoutExtension(path));
                        if (pendingTempIds.Contains(id)) continue;
                        TryDeleteFile(path);
                    }
                }
                catch (Exception ex)
                {
                    Debug.LogWarning(
                        $"[PrefabSentinel] RunScriptStartupCleanup: failed during temp-dir sweep: {ex.Message}");
                }
            }

            private static void ResumePendingAsyncRunners()
            {
                foreach (var entry in PendingAsyncRunner.ReadPersisted())
                {
                    if (entry.action == "run_script"
                        || entry.action == "run_script_submit")
                    {
                        // Issue #233: ``run_script_submit`` reuses the
                        // same per-frame poll as ``run_script`` so the
                        // compile / load completion contract is
                        // identical.  The poller writes to
                        // ``entry.responsePath``, which the submit
                        // handler set to the per-request completion
                        // file under the watch directory; the
                        // synchronous handler set it to the original
                        // response path.
                        string scriptAbs = Path.Combine(
                            entry.tempDirAbs, entry.tempId + ".cs");
                        string metaAbs = scriptAbs + ".meta";
                        EditorApplication.CallbackFunction poll = null;
                        poll = () => RunScriptPollFrame(entry, scriptAbs, metaAbs);
                        PendingAsyncRunner.RehydrateEntry(entry, poll);
                    }
                    else if (entry.action == "execute_menu_item")
                    {
                        // Issue #225 / #69: the menu-execute slow path may
                        // persist a SessionState entry before the domain
                        // reload that follows compilation. The resumer
                        // rebuilds the single post-reload reload-wait poll
                        // with the menu reload-complete action so the menu
                        // item runs against the freshly-loaded assemblies.
                        EditorControlRequest req = JsonUtility.FromJson<EditorControlRequest>(
                            entry.requestJson);
                        string menuPath = req != null ? req.menu_path : "";
                        EditorApplication.CallbackFunction poll = BuildRecompileReloadWaitPoll(
                            entry.responsePath,
                            entry.deadlineUnixMs,
                            -1,
                            "execute_menu_item: timed out after domain reload.",
                            BuildMenuExecuteReloadComplete(menuPath, entry.responsePath));
                        PendingAsyncRunner.RehydrateEntry(entry, poll);
                    }
                    else if (entry.action == "editor_recompile_and_wait")
                    {
                        // Issue #203: only the ``compiledAny=true`` path
                        // persists a SessionState entry — the no-op and
                        // failed paths complete synchronously inside the
                        // pre-reload phase and never reach this resumer.
                        // The post-reload poll (``BuildRecompileReloadWaitPoll``)
                        // therefore only needs to observe the reload counter
                        // and the deadline; the pipeline-event subscriptions
                        // do not survive the reload and would not fire again.
                        //
                        // Issue #191: the resumer running already implies a
                        // domain reload has occurred — the post-reload counter
                        // on this AppDomain starts at 0. A threshold of -1
                        // satisfies ``AssemblyReloadCount > threshold`` on the
                        // very first tick (0 > -1) regardless of whether
                        // ``[InitializeOnLoad]`` static constructors run before
                        // or after the ``afterAssemblyReload`` increment.
                        EditorApplication.CallbackFunction poll = BuildRecompileReloadWaitPoll(
                            entry.responsePath,
                            entry.deadlineUnixMs,
                            -1,
                            "editor_recompile_and_wait: timed out after domain reload.",
                            BuildRecompileAndWaitReloadComplete(entry.responsePath));
                        PendingAsyncRunner.RehydrateEntry(entry, poll);
                    }
                    else if (entry.action == "refresh_asset_database")
                    {
                        // Issue #70: a compile-aware ``editor_refresh`` whose
                        // triggered compile reloaded the domain is resumed
                        // here. The post-reload poll observes the reload
                        // counter and writes the refresh compile-success
                        // envelope (the -1 threshold rationale matches the
                        // recompile-and-wait branch above).
                        EditorApplication.CallbackFunction poll = BuildRecompileReloadWaitPoll(
                            entry.responsePath,
                            entry.deadlineUnixMs,
                            -1,
                            "editor_refresh: timed out after domain reload "
                            + "waiting for the AssemblyReloadCount tick.",
                            BuildRefreshCompileSuccessReloadComplete(entry.responsePath));
                        PendingAsyncRunner.RehydrateEntry(entry, poll);
                    }
                }
            }
        }
    }
}
