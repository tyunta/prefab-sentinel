using Xunit;

namespace PrefabSentinel.Tests;

public class UnityAcceptanceRequestTests
{
    [Fact]
    public void Omitted_Profile_Fields_Preserve_The_Default_Suite()
    {
        var dto = new EditorControlRequest();

        bool valid = UnityAcceptanceRequest.TryCreate(
            testProfile: dto.test_profile,
            runLiveProbes: dto.run_live_probes,
            runId: dto.run_id,
            out UnityAcceptanceRequest request,
            out string errorCode,
            out _);

        Assert.True(valid);
        Assert.Equal(UnityIntegrationTestProfile.Default, request.Profile);
        Assert.False(request.RunLiveProbes);
        Assert.Equal(string.Empty, request.RunId);
        Assert.Equal(string.Empty, errorCode);
    }

    [Fact]
    public void Explicit_Acceptance_Request_Selects_The_Bounded_Profile()
    {
        bool valid = UnityAcceptanceRequest.TryCreate(
            testProfile: "bridge_acceptance",
            runLiveProbes: true,
            runId: new string('a', 32),
            out UnityAcceptanceRequest request,
            out string errorCode,
            out _);

        Assert.True(valid);
        Assert.Equal(UnityIntegrationTestProfile.BridgeAcceptance, request.Profile);
        Assert.True(request.RunLiveProbes);
        Assert.Equal(new string('a', 32), request.RunId);
        Assert.Equal(string.Empty, errorCode);
    }

    [Fact]
    public void Unknown_Profile_Is_Rejected()
    {
        bool valid = UnityAcceptanceRequest.TryCreate(
            testProfile: "historical_full",
            runLiveProbes: false,
            runId: string.Empty,
            out _,
            out string errorCode,
            out _);

        Assert.False(valid);
        Assert.Equal("EDITOR_CTRL_TEST_PROFILE_INVALID", errorCode);
    }

    [Theory]
    [InlineData("")]
    [InlineData(null)]
    public void Explicit_Empty_Or_Null_Profile_Is_Rejected(string? testProfile)
    {
        bool valid = UnityAcceptanceRequest.TryCreate(
            testProfile: testProfile!,
            runLiveProbes: false,
            runId: string.Empty,
            out _,
            out string errorCode,
            out _);

        Assert.False(valid);
        Assert.Equal("EDITOR_CTRL_TEST_PROFILE_INVALID", errorCode);
    }

    [Fact]
    public void Acceptance_Profile_Requires_Explicit_Live_Probe_Authorization()
    {
        bool valid = UnityAcceptanceRequest.TryCreate(
            testProfile: "bridge_acceptance",
            runLiveProbes: false,
            runId: new string('a', 32),
            out _,
            out string errorCode,
            out _);

        Assert.False(valid);
        Assert.Equal("EDITOR_CTRL_TEST_LIVE_PROBES_REQUIRED", errorCode);
    }

    [Theory]
    [InlineData("")]
    [InlineData("aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa")]
    [InlineData("aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa")]
    [InlineData("AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA")]
    [InlineData("gggggggggggggggggggggggggggggggg")]
    [InlineData("aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-")]
    public void Acceptance_Profile_Rejects_Malformed_Run_Ids(string runId)
    {
        bool valid = UnityAcceptanceRequest.TryCreate(
            testProfile: "bridge_acceptance",
            runLiveProbes: true,
            runId: runId,
            out _,
            out string errorCode,
            out _);

        Assert.False(valid);
        Assert.Equal("EDITOR_CTRL_TEST_RUN_ID_INVALID", errorCode);
    }

    [Theory]
    [InlineData(true, "")]
    [InlineData(false, "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa")]
    public void Default_Profile_Rejects_Acceptance_Only_Inputs(
        bool runLiveProbes,
        string runId)
    {
        bool valid = UnityAcceptanceRequest.TryCreate(
            testProfile: "default",
            runLiveProbes: runLiveProbes,
            runId: runId,
            out _,
            out string errorCode,
            out _);

        Assert.False(valid);
        Assert.Contains(
            errorCode,
            new[]
            {
                "EDITOR_CTRL_TEST_LIVE_PROBES_INVALID",
                "EDITOR_CTRL_TEST_RUN_ID_INVALID",
            });
    }
}
