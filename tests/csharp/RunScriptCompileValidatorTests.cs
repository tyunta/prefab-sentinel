using PrefabSentinel;
using Xunit;

namespace PrefabSentinel.Tests;

/// <summary>
/// Issue #14 (H-6) — exercises the compile-deadline resolver, recompile
/// timeout validator, and pending-code selector extracted from the
/// run-script / recompile-and-wait handlers.
/// </summary>
public class RunScriptDeadlineTests
{
    private const int BridgeDefaultMs = 15000;
    private const int EntryTypeMs = 4000;
    private const long CallTime = 1_000_000L;

    [Fact]
    public void A_Zero_Request_Timeout_Uses_The_Bridge_Default_Poll_Budget()
    {
        RunScriptDeadlineResult result = RunScriptDeadline.Resolve(
            requestTimeoutMs: 0, BridgeDefaultMs, CallTime, EntryTypeMs);

        Assert.Equal(BridgeDefaultMs, result.PollBudgetMs);
        Assert.Equal(CallTime + BridgeDefaultMs + EntryTypeMs, result.DeadlineMs);
    }

    [Fact]
    public void A_Positive_Request_Timeout_Overrides_The_Bridge_Default()
    {
        RunScriptDeadlineResult result = RunScriptDeadline.Resolve(
            requestTimeoutMs: 8000, BridgeDefaultMs, CallTime, EntryTypeMs);

        Assert.Equal(8000, result.PollBudgetMs);
        Assert.Equal(CallTime + 8000 + EntryTypeMs, result.DeadlineMs);
    }
}

/// <summary>Recompile timeout validation around the zero / maximum bounds.</summary>
public class RecompileTimeoutValidatorTests
{
    [Fact]
    public void A_Negative_Timeout_Is_Rejected_As_Out_Of_Range()
    {
        RecompileTimeoutResult result = RecompileTimeoutValidator.Validate(-0.1f);

        Assert.False(result.Success);
        Assert.Equal(RecompileTimeoutValidator.OutOfRangeCode, result.ErrorCode);
    }

    [Fact]
    public void A_Zero_Timeout_Maps_To_The_Default_Budget()
    {
        RecompileTimeoutResult result = RecompileTimeoutValidator.Validate(0f);

        Assert.True(result.Success);
        Assert.Equal(RecompileTimeoutValidator.DefaultTimeoutSec, result.BudgetSec);
    }

    [Fact]
    public void The_Maximum_Timeout_Is_Accepted()
    {
        RecompileTimeoutResult result = RecompileTimeoutValidator.Validate(
            RecompileTimeoutValidator.MaxTimeoutSec);

        Assert.True(result.Success);
        Assert.Equal(RecompileTimeoutValidator.MaxTimeoutSec, result.BudgetSec);
    }

    [Fact]
    public void A_Timeout_Just_Above_The_Maximum_Is_Rejected()
    {
        RecompileTimeoutResult result = RecompileTimeoutValidator.Validate(
            RecompileTimeoutValidator.MaxTimeoutSec + 0.1f);

        Assert.False(result.Success);
        Assert.Equal(RecompileTimeoutValidator.OutOfRangeCode, result.ErrorCode);
    }
}

/// <summary>Pending-code selection around the stuck-count threshold.</summary>
public class RunScriptCompilePendingCodeSelectorTests
{
    private const int StuckThreshold = 2;

    [Fact]
    public void Below_The_Threshold_The_Dedicated_Timeout_Code_Is_Selected()
    {
        // prior 0 -> incremented count 1, below the threshold of 2.
        string code = RunScriptCompilePendingCodeSelector.SelectCode(
            priorStuckCount: 0, StuckThreshold);

        Assert.Equal(RunScriptCompilePendingCodeSelector.TimeoutCode, code);
    }

    [Fact]
    public void At_The_Threshold_The_Recovery_Code_Is_Selected()
    {
        // prior 1 -> incremented count 2, reaching the threshold of 2.
        string code = RunScriptCompilePendingCodeSelector.SelectCode(
            priorStuckCount: 1, StuckThreshold);

        Assert.Equal(RunScriptCompilePendingCodeSelector.RecoveryCode, code);
    }
}
