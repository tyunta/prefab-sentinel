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
    /// <item><description>The per-frame compile / load poller for ``run_script`` (issue
    ///       #108) and its stuck-detection / temp-area-recovery
    ///       helpers (issue #116).</description></item>
    /// <item><description>The startup cleanup hook that resumes in-flight async runner
    ///       entries on the new AppDomain after a domain reload.</description></item>
    /// </list>
    /// </summary>
    public static partial class UnityEditorControlBridge
    {
        // ── Handlers shared with Editor refresh / recompile / tests ──

        private static EditorControlResponse HandleRefreshAssetDatabase()
        {
            AssetDatabase.Refresh(ImportAssetOptions.ForceSynchronousImport);
            return BuildSuccess("EDITOR_CTRL_REFRESH_OK",
                "AssetDatabase.Refresh completed",
                data: new EditorControlData { executed = true });
        }

        private static EditorControlResponse HandleRecompileScripts(EditorControlRequest request)
        {
            var diagnostics = new List<EditorControlDiagnostic>();

            // When force_reimport is requested, synchronously re-import every
            // C# file under Assets/Editor with ForceUpdate so externally
            // edited scripts are guaranteed to round-trip through Unity's
            // import pipeline before compilation is scheduled.
            if (request.force_reimport)
            {
                string editorRoot = "Assets/Editor";
                string editorRootAbs = Path.Combine(
                    Directory.GetCurrentDirectory(),
                    editorRoot.Replace('/', Path.DirectorySeparatorChar));
                if (Directory.Exists(editorRootAbs))
                {
                    foreach (string csAbs in Directory.GetFiles(
                        editorRootAbs, "*.cs", SearchOption.AllDirectories))
                    {
                        string rel = csAbs
                            .Substring(Directory.GetCurrentDirectory().Length)
                            .TrimStart(Path.DirectorySeparatorChar, '/')
                            .Replace(Path.DirectorySeparatorChar, '/');
                        try
                        {
                            AssetDatabase.ImportAsset(
                                rel,
                                ImportAssetOptions.ForceUpdate
                                | ImportAssetOptions.ForceSynchronousImport);
                        }
                        catch (Exception ex)
                        {
                            diagnostics.Add(new EditorControlDiagnostic
                            {
                                path = rel,
                                location = "force_reimport",
                                detail = "warning",
                                evidence = ex.Message,
                            });
                        }
                    }
                }
            }

            AssetDatabase.Refresh(ImportAssetOptions.ForceSynchronousImport);
            // Schedule compilation on next frame so that the response JSON
            // is written to disk before domain reload destroys this context.
            EditorApplication.delayCall += () =>
            {
                CompilationPipeline.RequestScriptCompilation();
            };
            var response = BuildSuccess("EDITOR_CTRL_RECOMPILE_OK",
                request.force_reimport
                    ? "Force re-import of editor scripts completed; AssetDatabase.Refresh completed; script recompilation scheduled (domain reload will follow)"
                    : "AssetDatabase.Refresh completed; script recompilation scheduled (domain reload will follow)",
                data: new EditorControlData { executed = true });
            if (diagnostics.Count > 0)
            {
                response.diagnostics = diagnostics.ToArray();
            }
            return response;
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
                return BuildError("EDITOR_CTRL_TESTS_ERROR", ex.ToString());
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

        // ── Recompile-and-wait (#118 / #134) ──

        private const float RecompileAndWaitDefaultTimeoutSec = 60.0f;

        // Issue #134: inclusive upper bound on the synchronous
        // recompile-and-wait wait budget.  Mirrors the Python constant
        // ``RECOMPILE_AND_WAIT_TIMEOUT_MAX_SEC`` so a request that slips
        // past a stale client still gets rejected by the bridge.  The
        // lower bound is exclusive at zero — a request payload of 0 means
        // "use the default" per the existing handler contract; any
        // negative request value is an explicit out-of-range request.
        private const float RecompileAndWaitTimeoutMaxSec = 1800f;

        /// <summary>
        /// Builds the post-reload poll used by ``editor_recompile_and_wait``
        /// once compilation has finished and at least one assembly was
        /// recompiled. Issue #203: the post-reload phase observes only
        /// the reload counter and the deadline — the
        /// ``CompilationPipeline.compilationFinished`` event is the
        /// authoritative pre-reload terminator and lives in
        /// ``HandleRecompileAndWait`` itself, so this poll never reads
        /// the assembly modification time.
        /// </summary>
        private static EditorApplication.CallbackFunction BuildRecompileReloadWaitPoll(
            string responsePath,
            long deadlineMs,
            int reloadCountThreshold,
            string timeoutDetail)
        {
            EditorApplication.CallbackFunction poll = null;
            poll = () =>
            {
                long nowMs = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds();
                if (nowMs > deadlineMs)
                {
                    PendingAsyncRunner.Complete(responsePath);
                    WriteResponse(responsePath, BuildError(
                        "EDITOR_CTRL_RECOMPILE_TIMEOUT",
                        timeoutDetail));
                    return;
                }
                if (PendingAsyncRunner.AssemblyReloadCount <= reloadCountThreshold) return;

                PendingAsyncRunner.Complete(responsePath);
                WriteResponse(responsePath, BuildSuccess(
                    "EDITOR_CTRL_RECOMPILE_AND_WAIT_OK",
                    "editor_recompile_and_wait: compilation completed and assembly reloaded.",
                    new EditorControlData { executed = true }));
            };
            return poll;
        }

        private static EditorControlResponse HandleRecompileAndWait(
            EditorControlRequest request, string responsePath)
        {
            // Issue #134: validate the wait budget against the published
            // acceptance range before doing any work.  ``timeout_sec == 0``
            // is the default-marker (use ``RecompileAndWaitDefaultTimeoutSec``);
            // negative values and values above the upper bound are explicit
            // out-of-range requests that must be rejected with the dedicated
            // error code, mirroring the client-side check.
            if (request.timeout_sec < 0f
                || request.timeout_sec > RecompileAndWaitTimeoutMaxSec)
            {
                return BuildError(
                    "EDITOR_CTRL_COMPILE_TIMEOUT_OUT_OF_RANGE",
                    $"editor_recompile_and_wait: timeout_sec={request.timeout_sec} "
                    + $"is outside the accepted range (0, {RecompileAndWaitTimeoutMaxSec}] "
                    + "(seconds).");
            }

            float budgetSec = request.timeout_sec > 0f
                ? request.timeout_sec
                : RecompileAndWaitDefaultTimeoutSec;
            long callTimeMs = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds();
            long deadlineMs = callTimeMs + (long)(budgetSec * 1000f);
            int callTimeReloadCount = PendingAsyncRunner.AssemblyReloadCount;

            // Issue #203: event-driven completion. The mtime polling
            // approach failed because Unity does not advance
            // ``Library/ScriptAssemblies/Assembly-CSharp.dll``'s mtime
            // when every assembly is reported as not requiring
            // compilation, so the old completion check could never fire
            // and the surface always timed out on no-op compiles. The
            // event-driven approach observes:
            //
            //   * ``assemblyCompilationFinished`` for per-assembly
            //     outcome (records compile errors of error severity and
            //     marks ``compiledAny`` when a real compile occurred).
            //   * ``compilationFinished`` as the always-fires terminator
            //     that synthesises the outcome before Unity enters the
            //     domain reload that destroys this AppDomain (issue #213).
            //     The no-op case is determined passively at this point:
            //     ``compiledAny == false`` means no per-assembly finished
            //     event ever fired, which corresponds to every assembly
            //     having been reported as not requiring compilation.
            //
            // Issue #213 root cause: the previous implementation only set
            // a flag inside ``compilationFinished`` and deferred outcome
            // processing to the next ``EditorApplication.update`` tick.
            // Unity begins domain reload in the same frame as
            // ``compilationFinished``, so the deferred lambda was
            // destroyed before it could write the response — every call
            // surfaced as a transport timeout. Synthesising the outcome
            // synchronously inside the pipeline-finished subscription
            // guarantees the response file is written while the original
            // AppDomain is still alive. ``resolved`` is the shared
            // re-entry guard between the subscription and the deadline
            // watchdog so exactly one envelope is written per request
            // even if both observe a terminal condition in the same frame.
            bool compiledAny = false;
            bool resolved = false;
            var compileErrors = new List<string>();

            // Per-assembly compile-finished delegate type (issue #213
            // secondary bug A / CS0426): Unity 2022.3 publishes the event
            // signature as ``Action<string, CompilerMessage[]>``. No
            // nested delegate type exists on the public compilation-
            // pipeline API surface in this version.
            Action<string, CompilerMessage[]> onAsmFinished = null;
            Action<object> onPipelineFinished = null;

            onAsmFinished = (asmPath, messages) =>
            {
                bool asmHadError = false;
                if (messages != null)
                {
                    foreach (var msg in messages)
                    {
                        if (msg.type == CompilerMessageType.Error)
                        {
                            asmHadError = true;
                            compileErrors.Add(
                                string.IsNullOrEmpty(msg.file)
                                    ? msg.message
                                    : $"{msg.file}({msg.line},{msg.column}): {msg.message}");
                        }
                    }
                }
                if (!asmHadError)
                    compiledAny = true;
            };

            void Unsubscribe()
            {
                CompilationPipeline.assemblyCompilationFinished -= onAsmFinished;
                CompilationPipeline.compilationFinished -= onPipelineFinished;
            }

            onPipelineFinished = _ =>
            {
                if (resolved) return;
                resolved = true;
                Unsubscribe();

                if (compileErrors.Count > 0)
                {
                    PendingAsyncRunner.Complete(responsePath);
                    WriteResponse(responsePath, BuildError(
                        "EDITOR_CTRL_RECOMPILE_FAILED",
                        $"editor_recompile_and_wait: {compileErrors.Count} compile error(s).",
                        data: new EditorControlData
                        {
                            executed = true,
                            errors = compileErrors.ToArray(),
                        }));
                    return;
                }

                if (!compiledAny)
                {
                    // No per-assembly finished event ever fired, which
                    // corresponds to every assembly having been reported
                    // as not requiring compilation. No domain reload will
                    // follow, so synthesise the no-op success synchronously
                    // and skip the SessionState mirror — there is nothing
                    // to resume after a reload that never happens.
                    PendingAsyncRunner.Complete(responsePath);
                    WriteResponse(responsePath, BuildSuccess(
                        "EDITOR_CTRL_RECOMPILE_AND_WAIT_NOOP",
                        "editor_recompile_and_wait: every assembly was reported "
                        + "as not requiring compilation; no domain reload occurred.",
                        new EditorControlData { executed = true }));
                    return;
                }

                // At least one assembly compiled. Switch over to the
                // post-reload wait poll and persist the entry so the
                // wait survives the inevitable domain reload that Unity
                // begins immediately after this subscription returns.
                PendingAsyncRunner.Complete(responsePath);
                var reloadEntry = new PendingAsyncRunner.PersistedEntry
                {
                    action = "editor_recompile_and_wait",
                    responsePath = responsePath,
                    requestJson = JsonUtility.ToJson(request),
                    callTimeUnixMs = callTimeMs,
                    deadlineUnixMs = deadlineMs,
                };
                EditorApplication.CallbackFunction reloadPoll = BuildRecompileReloadWaitPoll(
                    responsePath,
                    deadlineMs,
                    callTimeReloadCount,
                    $"editor_recompile_and_wait: timed out after {budgetSec:F1}s waiting "
                    + "for the post-reload AssemblyReloadCount tick.");
                PendingAsyncRunner.Register(reloadEntry, reloadPoll);
            };

            CompilationPipeline.assemblyCompilationFinished += onAsmFinished;
            CompilationPipeline.compilationFinished += onPipelineFinished;

            EditorApplication.CallbackFunction prePoll = null;
            prePoll = () =>
            {
                if (resolved) return;
                long nowMs = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds();
                if (nowMs <= deadlineMs) return;

                resolved = true;
                Unsubscribe();
                PendingAsyncRunner.Complete(responsePath);
                WriteResponse(responsePath, BuildError(
                    "EDITOR_CTRL_RECOMPILE_TIMEOUT",
                    $"editor_recompile_and_wait: timed out after {budgetSec:F1}s "
                    + "before CompilationPipeline.compilationFinished fired."));
            };

            // Pre-reload entry: not persisted to SessionState — the
            // pipeline-event subscriptions live on this AppDomain and
            // cannot survive a reload. Persistence happens only on the
            // ``compiledAny`` switchover path inside the
            // ``compilationFinished`` subscription. We still register
            // the watchdog against the response path so the
            // ``EditorApplication.update`` loop drives it as a fallback
            // for the case where ``compilationFinished`` never fires.
            var preEntry = new PendingAsyncRunner.PersistedEntry
            {
                action = "editor_recompile_and_wait",
                responsePath = responsePath,
                requestJson = JsonUtility.ToJson(request),
                callTimeUnixMs = callTimeMs,
                deadlineUnixMs = deadlineMs,
            };
            PendingAsyncRunner.RegisterTransient(preEntry, prePoll);

            try
            {
                CompilationPipeline.RequestScriptCompilation();
            }
            catch (Exception ex)
            {
                // Issue #204: editor-side rejection of the compilation
                // request is a schedule-failure, not a deadline-elapsed
                // condition. Use a dedicated code so callers can
                // distinguish "Unity refused to start" from "we waited
                // and got no response".
                resolved = true;
                Unsubscribe();
                PendingAsyncRunner.Complete(responsePath);
                return BuildError(
                    "EDITOR_CTRL_RECOMPILE_SCHEDULE_FAILED",
                    $"editor_recompile_and_wait: failed to schedule compilation: {ex.Message}");
            }

            return null;
        }

        private static EditorControlResponse HandleRunScript(
            EditorControlRequest request, string responsePath)
        {
            // Issue #108: this handler is now async / frame-driven. It
            // stages the temp .cs file, kicks off a synchronous Refresh,
            // and registers an ``EditorApplication.update`` poller via
            // ``PendingAsyncRunner`` instead of blocking the main thread
            // on a busy-sleep loop.  The poller observes the same
            // completion conditions (compile finished + assembly mtime
            // advanced + afterAssemblyReload fired) used by the
            // ``editor_recompile_and_wait`` surface, then invokes the
            // entry point and writes the response.
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

            int compilePollMs = request.compile_timeout > 0
                ? request.compile_timeout
                : RunScriptCompileTimeoutMs;

            try
            {
                if (!Directory.Exists(tempDirAbs))
                    Directory.CreateDirectory(tempDirAbs);
                File.WriteAllText(scriptAbs, request.code);
            }
            catch (Exception stagingEx)
            {
                return BuildError("EDITOR_CTRL_RUN_SCRIPT_COMPILE",
                    $"run_script: failed to stage temp script '{scriptAbs}': {stagingEx.Message}",
                    new EditorControlData { temp_id = tempId, executed = false });
            }

            long callTimeMs = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds();
            long deadlineMs = callTimeMs + compilePollMs + RunScriptEntryTypeTimeoutMs;
            long callTimeAssemblyMtime =
                PendingAsyncRunner.ReadAssemblyMtimeUnixMs();

            var entry = new PendingAsyncRunner.PersistedEntry
            {
                action = "run_script",
                responsePath = responsePath,
                requestJson = JsonUtility.ToJson(request),
                callTimeUnixMs = callTimeMs,
                deadlineUnixMs = deadlineMs,
                callTimeAssemblyMtimeUnixMs = callTimeAssemblyMtime,
                tempId = tempId,
                stuckKey = stuckKey,
                tempDirAbs = tempDirAbs,
            };

            EditorApplication.CallbackFunction poll = null;
            poll = () => RunScriptPollFrame(
                entry, scriptAbs, metaAbs);
            PendingAsyncRunner.Register(entry, poll);

            // Trigger the synchronous Refresh after the poller is registered
            // so the SessionState mirror reflects the in-flight entry before
            // a domain reload triggered by Refresh occurs.
            try
            {
                AssetDatabase.Refresh(ImportAssetOptions.ForceSynchronousImport);
            }
            catch (Exception refreshEx)
            {
                PendingAsyncRunner.Complete(responsePath);
                TryDeleteFile(scriptAbs);
                TryDeleteFile(metaAbs);
                return BuildError("EDITOR_CTRL_RUN_SCRIPT_COMPILE",
                    $"run_script: AssetDatabase.Refresh failed: {refreshEx.Message}",
                    new EditorControlData { temp_id = tempId, executed = false });
            }

            return null;
        }

        /// <summary>
        /// Frame poller for an in-flight ``run_script`` request.  Runs each
        /// editor frame until the documented completion conditions are
        /// observed, then invokes the entry point and writes the response.
        /// Cleans up the temp .cs / .cs.meta files on every termination
        /// path (success, runtime exception, compile timeout, recovery).
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

            // ── Compile-pending timeout ──
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
            long currentMtime = PendingAsyncRunner.ReadAssemblyMtimeUnixMs();
            if (currentMtime <= entry.callTimeAssemblyMtimeUnixMs) return;

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
                    "(must be `public static void Run()`).",
                    new EditorControlData { temp_id = tempId, executed = false }));
                return;
            }

            System.IO.TextWriter originalOut = Console.Out;
            var buffer = new System.IO.StringWriter();
            Console.SetOut(buffer);
            EditorControlResponse response;
            try
            {
                runMethod.Invoke(null, null);
                RunScriptConsecutiveCompilePending.Remove(stuckKey);
                response = BuildSuccess("EDITOR_CTRL_RUN_SCRIPT_OK",
                    $"PrefabSentinelTempScript.Run() completed (temp_id={tempId}).",
                    new EditorControlData
                    {
                        temp_id = tempId,
                        executed = true,
                        stdout = buffer.ToString(),
                    });
            }
            catch (TargetInvocationException tie)
            {
                Exception inner = tie.InnerException ?? tie;
                response = BuildError("EDITOR_CTRL_RUN_SCRIPT_RUNTIME",
                    $"Run() threw {inner.GetType().Name}: {inner.Message}",
                    new EditorControlData
                    {
                        temp_id = tempId,
                        executed = true,
                        exception = inner.ToString(),
                        stdout = buffer.ToString(),
                    });
            }
            catch (Exception ex)
            {
                response = BuildError("EDITOR_CTRL_RUN_SCRIPT_RUNTIME",
                    $"Run() threw {ex.GetType().Name}: {ex.Message}",
                    new EditorControlData
                    {
                        temp_id = tempId,
                        executed = true,
                        exception = ex.ToString(),
                        stdout = buffer.ToString(),
                    });
            }
            finally
            {
                Console.SetOut(originalOut);
            }

            PendingAsyncRunner.Complete(responsePath);
            CleanupRunScriptTempFiles(scriptAbs, metaAbs);
            WriteResponse(responsePath, response);
        }

        private static void CleanupRunScriptTempFiles(string scriptAbs, string metaAbs)
        {
            TryDeleteFile(scriptAbs);
            TryDeleteFile(metaAbs);
            try { AssetDatabase.Refresh(ImportAssetOptions.ForceSynchronousImport); }
            catch (Exception refreshEx)
            {
                Debug.LogWarning(
                    $"[PrefabSentinel] HandleRunScript: post-run AssetDatabase.Refresh failed: {refreshEx.Message}");
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
            int next = prior + 1;
            RunScriptConsecutiveCompilePending[stuckKey] = next;

            EditorControlData data = BuildRunScriptDiagnosticsData(tempId, tempDirAbs);

            if (next >= RunScriptStuckThreshold)
            {
                RunScriptRecoverTempArea(tempDirAbs);
                RunScriptConsecutiveCompilePending.Remove(stuckKey);
                EditorControlData recovered = BuildRunScriptDiagnosticsData(tempId, tempDirAbs);
                return new EditorControlResponse
                {
                    protocol_version = ProtocolVersion,
                    success = false,
                    severity = "warning",
                    code = "EDITOR_CTRL_RUN_SCRIPT_RECOVERY",
                    message = "Script compile appeared stuck; ran recovery cleanup. Retry the script.",
                    data = recovered,
                };
            }

            return BuildError("EDITOR_CTRL_RUN_SCRIPT_COMPILE", baseMessage, data);
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
                        if (entry.action == "run_script"
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
                    if (entry.action == "run_script")
                    {
                        string scriptAbs = Path.Combine(
                            entry.tempDirAbs, entry.tempId + ".cs");
                        string metaAbs = scriptAbs + ".meta";
                        EditorApplication.CallbackFunction poll = null;
                        poll = () => RunScriptPollFrame(entry, scriptAbs, metaAbs);
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
                            "editor_recompile_and_wait: timed out after domain reload.");
                        PendingAsyncRunner.RehydrateEntry(entry, poll);
                    }
                }
            }
        }
    }
}
