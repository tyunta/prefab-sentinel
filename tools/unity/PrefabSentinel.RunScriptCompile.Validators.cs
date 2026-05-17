using System;

// RunScriptCompile validators — Unity-free decisions extracted from
// HandleRunScript / HandleRecompileAndWait / RunScriptCompilePendingResponse
// (issues #102 / #116 / #134 / #234 / H-6).
namespace PrefabSentinel
{
    /// <summary>
    /// Result of <see cref="RunScriptDeadline.Resolve"/>: the resolved
    /// compile-poll budget and the absolute deadline.
    /// </summary>
    internal readonly struct RunScriptDeadlineResult
    {
        public int PollBudgetMs { get; }
        public long DeadlineMs { get; }

        public RunScriptDeadlineResult(int pollBudgetMs, long deadlineMs)
        {
            PollBudgetMs = pollBudgetMs;
            DeadlineMs = deadlineMs;
        }
    }

    /// <summary>
    /// Resolves the run-script compile-poll budget and deadline. The request
    /// budget is used when positive; the bridge default is used otherwise.
    /// </summary>
    internal static class RunScriptDeadline
    {
        public static RunScriptDeadlineResult Resolve(
            int requestTimeoutMs, int bridgeDefaultMs,
            long callTimeMs, int entryTypeTimeoutMs)
        {
            int pollBudget = requestTimeoutMs > 0 ? requestTimeoutMs : bridgeDefaultMs;
            long deadline = callTimeMs + pollBudget + entryTypeTimeoutMs;
            return new RunScriptDeadlineResult(pollBudget, deadline);
        }
    }

    /// <summary>
    /// Result of <see cref="RecompileTimeoutValidator.Validate"/>: a success
    /// flag, the out-of-range rejection code on failure, and the resolved
    /// wait budget in seconds.
    /// </summary>
    internal readonly struct RecompileTimeoutResult
    {
        public bool Success { get; }
        public string ErrorCode { get; }
        public float BudgetSec { get; }

        private RecompileTimeoutResult(bool success, string errorCode, float budgetSec)
        {
            Success = success;
            ErrorCode = errorCode;
            BudgetSec = budgetSec;
        }

        public static RecompileTimeoutResult Accepted(float budgetSec)
        {
            return new RecompileTimeoutResult(true, string.Empty, budgetSec);
        }

        public static RecompileTimeoutResult Rejected(string code)
        {
            return new RecompileTimeoutResult(false, code, 0f);
        }
    }

    /// <summary>
    /// Validates the synchronous recompile-and-wait timeout. A negative value
    /// or a value above <see cref="MaxTimeoutSec"/> is rejected; zero maps to
    /// <see cref="DefaultTimeoutSec"/>; any other value is accepted as-is.
    /// </summary>
    internal static class RecompileTimeoutValidator
    {
        internal const string OutOfRangeCode = "EDITOR_CTRL_COMPILE_TIMEOUT_OUT_OF_RANGE";

        // Issue #134: inclusive upper bound on the wait budget; mirrors the
        // Python constant RECOMPILE_AND_WAIT_TIMEOUT_MAX_SEC.
        internal const float MaxTimeoutSec = 1800f;
        internal const float DefaultTimeoutSec = 60.0f;

        public static RecompileTimeoutResult Validate(float timeoutSec)
        {
            if (timeoutSec < 0f || timeoutSec > MaxTimeoutSec)
            {
                return RecompileTimeoutResult.Rejected(OutOfRangeCode);
            }
            float budget = timeoutSec > 0f ? timeoutSec : DefaultTimeoutSec;
            return RecompileTimeoutResult.Accepted(budget);
        }
    }

    /// <summary>
    /// Selects the run-script compile-pending response code. Once the
    /// incremented consecutive-stuck count reaches the threshold the recovery
    /// code is chosen; otherwise the dedicated compile-timeout code is used.
    /// </summary>
    internal static class RunScriptCompilePendingCodeSelector
    {
        internal const string RecoveryCode = "EDITOR_CTRL_RUN_SCRIPT_RECOVERY";
        internal const string TimeoutCode = "EDITOR_RUN_SCRIPT_COMPILE_TIMEOUT";

        public static string SelectCode(int priorStuckCount, int stuckThreshold)
        {
            int next = priorStuckCount + 1;
            return next >= stuckThreshold ? RecoveryCode : TimeoutCode;
        }
    }
}
