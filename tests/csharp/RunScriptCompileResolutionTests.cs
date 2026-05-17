using System;
using PrefabSentinel;
using Xunit;

namespace PrefabSentinel.Tests;

/// <summary>
/// Issue #15 (H-7) — exercises the single-claim resolution guard, recompile
/// outcome classifier, deadline-elapsed predicate, and diagnostic redaction
/// extracted from the recompile-and-wait handler.
/// </summary>
public class RecompileResolutionGuardTests
{
    [Fact]
    public void The_First_Claim_Succeeds_And_Every_Later_Claim_Fails()
    {
        var guard = new RecompileResolutionGuard();

        Assert.True(guard.TryClaim());
        Assert.False(guard.TryClaim());
        Assert.False(guard.TryClaim());
    }

    [Fact]
    public void Separate_Instances_Do_Not_Share_Claim_State()
    {
        var first = new RecompileResolutionGuard();
        var second = new RecompileResolutionGuard();

        Assert.True(first.TryClaim());
        Assert.True(second.TryClaim());
    }
}

/// <summary>Recompile outcome classification precedence.</summary>
public class RecompileOutcomeClassifierTests
{
    [Fact]
    public void Compile_Errors_Select_The_Failed_Code()
    {
        string outcome = RecompileOutcomeClassifier.Classify(
            new RecompileResultSnapshot(compileErrorCount: 1, compiledAny: true));

        Assert.Equal(RecompileOutcomeClassifier.FailedCode, outcome);
    }

    [Fact]
    public void Nothing_Compiled_Selects_The_No_Op_Code()
    {
        string outcome = RecompileOutcomeClassifier.Classify(
            new RecompileResultSnapshot(compileErrorCount: 0, compiledAny: false));

        Assert.Equal(RecompileOutcomeClassifier.NoopCode, outcome);
    }

    [Fact]
    public void A_Clean_Compile_Selects_The_Continue_Sentinel()
    {
        string outcome = RecompileOutcomeClassifier.Classify(
            new RecompileResultSnapshot(compileErrorCount: 0, compiledAny: true));

        Assert.Equal(RecompileOutcomeClassifier.ContinueSentinel, outcome);
    }
}

/// <summary>Deadline-elapsed predicate (strict).</summary>
public class RecompileDeadlineTests
{
    private const long Deadline = 5000L;

    [Theory]
    [InlineData(Deadline - 1, false)]
    [InlineData(Deadline, false)]      // strictly past — at the deadline is not elapsed
    [InlineData(Deadline + 1, true)]
    public void Elapsed_Is_True_Only_Strictly_Past_The_Deadline(
        long nowMs, bool expected)
    {
        Assert.Equal(expected, RecompileDeadline.HasElapsed(nowMs, Deadline));
    }
}

/// <summary>Diagnostic redaction: no exception text crosses the boundary.</summary>
public class RunScriptCompileRedactionTests
{
    [Fact]
    public void The_Schedule_Failure_Message_Is_Fixed_And_Carries_No_Exception_Text()
    {
        string message = ScheduleFailureEnvelope.RedactedMessage();

        Assert.Equal(
            "editor_recompile_and_wait: failed to schedule compilation.", message);
    }

    [Fact]
    public void Reimport_Diagnostic_Evidence_Is_The_Type_Name_Without_The_Message_Body()
    {
        var ex = new InvalidOperationException(
            "host path /home/secret/Library leaked into the message");

        string evidence = ReimportDiagnostic.Evidence(ex);

        Assert.Equal("InvalidOperationException", evidence);
        Assert.DoesNotContain("secret", evidence);
    }
}
