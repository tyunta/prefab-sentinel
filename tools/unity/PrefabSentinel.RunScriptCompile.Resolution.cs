using System;

// RunScriptCompile recompile-resolution decisions — Unity-free extractions
// from HandleRecompileAndWait (issues #203 / #213 / H-7).
namespace PrefabSentinel
{
    /// <summary>
    /// Single-use claim guard shared between the pipeline-finished
    /// subscription and the deadline watchdog so exactly one envelope is
    /// written per recompile-and-wait request. Documented for single-threaded
    /// use — the bridge drives both observers from the editor main thread.
    /// </summary>
    internal sealed class RecompileResolutionGuard
    {
        private bool _claimed;

        /// <summary>
        /// Return true on the first call and false on every subsequent call
        /// against the same instance.
        /// </summary>
        public bool TryClaim()
        {
            if (_claimed)
            {
                return false;
            }
            _claimed = true;
            return true;
        }
    }

    /// <summary>
    /// Unity-free snapshot of a recompile outcome: the count of compile
    /// errors observed and whether any assembly actually compiled.
    /// </summary>
    internal readonly struct RecompileResultSnapshot
    {
        public int CompileErrorCount { get; }
        public bool CompiledAny { get; }

        public RecompileResultSnapshot(int compileErrorCount, bool compiledAny)
        {
            CompileErrorCount = compileErrorCount;
            CompiledAny = compiledAny;
        }
    }

    /// <summary>
    /// Classifies a recompile outcome. Compile errors outrank a no-op, which
    /// outranks the continue case where compilation succeeded and the
    /// handler must wait for the post-reload tick.
    /// </summary>
    internal static class RecompileOutcomeClassifier
    {
        internal const string FailedCode = "EDITOR_CTRL_RECOMPILE_FAILED";
        internal const string NoopCode = "EDITOR_CTRL_RECOMPILE_AND_WAIT_NOOP";

        // Not an envelope code: a sentinel telling the handler to switch over
        // to the post-reload wait poll rather than write a terminal envelope.
        internal const string ContinueSentinel = "continue";

        public static string Classify(RecompileResultSnapshot snapshot)
        {
            if (snapshot.CompileErrorCount > 0)
            {
                return FailedCode;
            }
            if (!snapshot.CompiledAny)
            {
                return NoopCode;
            }
            return ContinueSentinel;
        }
    }

    /// <summary>
    /// Deadline-elapsed predicate. The current time must be strictly past the
    /// deadline for the wait to be treated as elapsed.
    /// </summary>
    internal static class RecompileDeadline
    {
        public static bool HasElapsed(long nowMs, long deadlineMs)
        {
            return nowMs > deadlineMs;
        }
    }
}
