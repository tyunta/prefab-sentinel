using System;
using System.Collections.Generic;
using UnityEditor;
using UnityEditor.Compilation;
using UnityEngine;

namespace PrefabSentinel
{
    /// <summary>
    /// Shared compile-watch partial (issue #68).  Owns the single
    /// pre-reload compile observation consumed by every compile-aware
    /// handler — ``editor_recompile_and_wait``, ``execute_menu_item``,
    /// ``run_script`` / ``run_script_submit``, and the compile-aware
    /// ``editor_refresh``.
    ///
    /// Before this partial existed the ``CompilationPipeline`` event
    /// subscription, the compiler-error aggregation, the single-resolution
    /// guard, and the deadline watchdog were duplicated byte-for-byte
    /// between ``HandleRecompileAndWait`` and ``ScheduleMenuExecuteBarrier``
    /// (and ``run_script`` could not observe a compile failure at all).
    /// The barrier owns that observation exactly once; each handler owns
    /// only its own terminal outcomes and the compile action it hands in.
    /// </summary>
    public static partial class UnityEditorControlBridge
    {
        // Issue #70: provisional no-compile grace window for the
        // compile-aware refresh path.  An ``AssetDatabase.Refresh`` that
        // imports no changed script raises no ``CompilationPipeline``
        // event, so the barrier waits this long for a triggered compile to
        // *start* (observed through ``EditorApplication.isCompiling`` or a
        // per-assembly finished event) before concluding "no compile".
        // The value is a starting point; #70 requires it be confirmed
        // against the PF-TEST refresh matrix on real Unity — too short
        // misreads a slow-to-start compile as no-compile, too long makes
        // every compile-free refresh wait it out.
        private const int CompileBarrierNoCompileGraceWindowMs = 1500;

        /// <summary>
        /// Options for <see cref="ScheduleCompileBarrier"/>.  The caller
        /// supplies the compile-trigger action, one action per terminal
        /// outcome, the deadline, the pre-reload registration entry and
        /// mode, and — when the no-compile outcome is wanted — the grace
        /// window plus its action.
        /// </summary>
        private sealed class CompileBarrierSpec
        {
            // Triggers the compilation episode.  Runs after the pipeline
            // event subscriptions and the deadline watchdog are in place.
            public Action compileTrigger;

            // Terminal outcome actions — exactly one runs per episode.
            // ``onCompileFailed`` receives the aggregated compiler errors.
            public Action<List<string>> onCompileFailed;
            public Action onNoAssemblyCompiled;
            public Action onCompiled;
            public Action onDeadlineExceeded;
            public Action onScheduleFailure;

            // Issue #70 no-compile grace window.  When > 0 the barrier
            // resolves ``onNoCompileObserved`` if the window elapses with
            // no compile episode observed.  0 disables it (the recompile /
            // menu / run-script consumers always trigger a compile).
            public int noCompileGraceWindowMs;
            public Action onNoCompileObserved;

            // Absolute deadline (unix ms) for the pre-reload episode.
            public long deadlineMs;

            // Pre-reload registry entry driving the deadline / grace
            // watchdog.  Registered persisted when ``persistPreReloadEntry``
            // is true (the entry must survive the domain reload so the
            // post-reload resumer can pick it up), transient otherwise.
            public PendingAsyncRunner.PersistedEntry preReloadEntry;
            public bool persistPreReloadEntry;
        }

        /// <summary>
        /// Observe exactly one compilation episode and resolve exactly one
        /// terminal outcome — compile-failed, no-assembly-compiled,
        /// compiled, deadline-exceeded, schedule-failure, or (when the
        /// grace window is armed) no-compile-observed — invoking the
        /// caller-supplied action for whichever occurs first.  Aggregates
        /// the real compiler error diagnostics for the compile-failed
        /// outcome.  Never throws across its own boundary: a compile
        /// trigger that raises routes to ``onScheduleFailure``.
        /// </summary>
        private static void ScheduleCompileBarrier(CompileBarrierSpec spec)
        {
            // Issue H-7: the single-resolution guard shared between the
            // pipeline-finished subscription and the deadline / grace
            // watchdog so exactly one terminal outcome resolves even if
            // two observers see a terminal condition in the same frame.
            var resolutionGuard = new RecompileResolutionGuard();
            var compileErrors = new List<string>();
            bool compiledAny = false;

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
                            // Issue #68: ``CompilerMessage.message`` already
                            // carries the ``file(line,col):`` prefix emitted
                            // by the C# compiler.  Emit it verbatim so the
                            // reported diagnostic is not doubly prefixed.
                            compileErrors.Add(msg.message);
                        }
                    }
                }
                if (!asmHadError) compiledAny = true;
            };

            void Unsubscribe()
            {
                CompilationPipeline.assemblyCompilationFinished -= onAsmFinished;
                CompilationPipeline.compilationFinished -= onPipelineFinished;
            }

            onPipelineFinished = _ =>
            {
                if (!resolutionGuard.TryClaim()) return;
                Unsubscribe();

                // Outcome precedence (failed > no-op > compiled) is owned
                // by the Unity-free ``RecompileOutcomeClassifier``.
                string outcome = RecompileOutcomeClassifier.Classify(
                    new RecompileResultSnapshot(compileErrors.Count, compiledAny));
                if (outcome == RecompileOutcomeClassifier.FailedCode)
                {
                    spec.onCompileFailed(compileErrors);
                    return;
                }
                if (outcome == RecompileOutcomeClassifier.NoopCode)
                {
                    spec.onNoAssemblyCompiled();
                    return;
                }
                spec.onCompiled();
            };

            CompilationPipeline.assemblyCompilationFinished += onAsmFinished;
            CompilationPipeline.compilationFinished += onPipelineFinished;

            long graceDeadlineMs = spec.noCompileGraceWindowMs > 0
                ? DateTimeOffset.UtcNow.ToUnixTimeMilliseconds()
                  + spec.noCompileGraceWindowMs
                : 0L;

            EditorApplication.CallbackFunction prePoll = null;
            prePoll = () =>
            {
                long nowMs = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds();

                // Issue #70: no-compile grace window.  Resolve the
                // no-compile outcome only once the window has elapsed with
                // the editor not compiling.  ``EditorApplication.isCompiling``
                // stays true for the whole compilation pipeline — from the
                // moment a triggered compile starts until
                // ``compilationFinished`` fires (which claims the resolution
                // guard) — so the check keeps a real compile, whether
                // slow-to-start or in-progress, from being misread as
                // no-compile.
                if (graceDeadlineMs > 0L
                    && RecompileDeadline.HasElapsed(nowMs, graceDeadlineMs)
                    && !EditorApplication.isCompiling)
                {
                    if (!resolutionGuard.TryClaim()) return;
                    Unsubscribe();
                    spec.onNoCompileObserved();
                    return;
                }

                if (!RecompileDeadline.HasElapsed(nowMs, spec.deadlineMs)) return;
                if (!resolutionGuard.TryClaim()) return;
                Unsubscribe();
                spec.onDeadlineExceeded();
            };

            if (spec.persistPreReloadEntry)
                PendingAsyncRunner.Register(spec.preReloadEntry, prePoll);
            else
                PendingAsyncRunner.RegisterTransient(spec.preReloadEntry, prePoll);

            try
            {
                spec.compileTrigger();
            }
            catch (Exception ex)
            {
                // Issue #214: the caller-visible envelope must carry no
                // exception text; the full detail goes only to the Unity
                // console.  The caller's schedule-failure action writes the
                // redacted envelope.
                resolutionGuard.TryClaim();
                Unsubscribe();
                Debug.LogWarning(
                    "[PrefabSentinel] ScheduleCompileBarrier: compile trigger "
                    + $"rejected: {ex}");
                spec.onScheduleFailure();
            }
        }
    }
}
