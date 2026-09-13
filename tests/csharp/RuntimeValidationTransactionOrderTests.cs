using Xunit;

namespace PrefabSentinel.Tests;

public sealed class RuntimeValidationTransactionOrderTests
{
    [Theory]
    [InlineData(true, false, true, true, false, false, true, false, false, false)]
    [InlineData(true, true, false, true, false, false, true, false, false, false)]
    [InlineData(true, true, true, false, false, false, true, false, false, false)]
    [InlineData(true, true, true, true, false, false, false, false, true, false)]
    [InlineData(true, true, true, true, false, false, true, false, true, true)]
    [InlineData(true, true, true, true, true, true, true, true, true, true)]
    [InlineData(false, true, true, false, true, false, true, true, true, false)]
    public void Decide_Pins_The_Complete_Transaction_Decision_Table(
        bool clientSimRequested,
        bool reportAndAuditValid,
        bool compileInventoryValid,
        bool clientSimPreflightValid,
        bool initialSceneDirty,
        bool allowInitialDirtyScene,
        bool compileSucceeded,
        bool sceneDirtyAfterCompile,
        bool expectedCompile,
        bool expectedClientSim)
    {
        RuntimeValidationTransactionOrder.Decision decision =
            RuntimeValidationTransactionOrder.Decide(
                clientSimRequested,
                reportAndAuditValid,
                compileInventoryValid,
                clientSimPreflightValid,
                initialSceneDirty,
                allowInitialDirtyScene,
                compileSucceeded,
                sceneDirtyAfterCompile);

        Assert.Equal(expectedCompile, decision.compile_allowed);
        Assert.Equal(expectedClientSim, decision.clientsim_allowed);
    }

    [Fact]
    public void Decide_Preserves_Initial_Clean_Scene_Authorization_When_Compile_Dirties_It()
    {
        RuntimeValidationTransactionOrder.Decision decision =
            RuntimeValidationTransactionOrder.Decide(
                clientSimRequested: true,
                reportAndAuditValid: true,
                compileInventoryValid: true,
                clientSimPreflightValid: true,
                initialSceneDirty: false,
                allowInitialDirtyScene: false,
                compileSucceeded: true,
                sceneDirtyAfterCompile: true);

        Assert.True(decision.compile_allowed);
        Assert.True(decision.clientsim_allowed);
    }

    [Fact]
    public void Decide_Rejects_Initially_Dirty_Scene_Without_Override_Before_Compile()
    {
        RuntimeValidationTransactionOrder.Decision decision =
            RuntimeValidationTransactionOrder.Decide(
                clientSimRequested: true,
                reportAndAuditValid: true,
                compileInventoryValid: true,
                clientSimPreflightValid: true,
                initialSceneDirty: true,
                allowInitialDirtyScene: false,
                compileSucceeded: true,
                sceneDirtyAfterCompile: true);

        Assert.False(decision.compile_allowed);
        Assert.False(decision.clientsim_allowed);
    }
}
