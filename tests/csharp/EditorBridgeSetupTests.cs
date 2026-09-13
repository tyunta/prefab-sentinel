using PrefabSentinel.EditorBridge;
using Xunit;

public sealed class EditorBridgeSetupTests
{
    private const string InstanceId = "0123456789abcdef0123456789abcdef";

    [Fact]
    public void FreshProjectsUseDistinctEnabledLocalEndpoints()
    {
        var first = EditorBridgeSetup.Resolve("/projects/First", null, null);
        var second = EditorBridgeSetup.Resolve("/projects/Second", null, null);

        Assert.Equal(
            "/projects/First/Library/PrefabSentinel/BridgeWatch",
            first.WatchDirectory);
        Assert.Equal(
            "/projects/Second/Library/PrefabSentinel/BridgeWatch",
            second.WatchDirectory);
        Assert.True(first.Enabled);
        Assert.True(second.Enabled);
        Assert.True(first.CreateWatchDirectory);
        Assert.True(second.CreateWatchDirectory);
    }

    [Fact]
    public void PreferenceKeysAreScopedByCanonicalProjectRoot()
    {
        const string prefix = "PrefabSentinel_EditorBridge_WatchDir";

        Assert.Equal(
            "PrefabSentinel_EditorBridge_WatchDir.D:\\Projects\\First",
            EditorBridgeSetup.PreferenceKey(prefix, @"D:\Projects\First"));
        Assert.NotEqual(
            EditorBridgeSetup.PreferenceKey(prefix, @"D:\Projects\First"),
            EditorBridgeSetup.PreferenceKey(prefix, @"D:\Projects\Second"));
    }

    [Fact]
    public void SavedSettingsOverrideFirstRunDefaults()
    {
        var settings = EditorBridgeSetup.Resolve(
            @"D:\Projects\First",
            @"D:\BridgeWatch\First",
            false);

        Assert.Equal(
            @"D:\BridgeWatch\First",
            settings.WatchDirectory);
        Assert.False(settings.Enabled);
        Assert.False(settings.CreateWatchDirectory);
    }

    [Fact]
    public void ConnectionInfoContainsGeneratedIdentityWithoutASecretOrCommand()
    {
        string info = EditorBridgeSetup.ConnectionInfo(
            @"D:\Projects\First",
            @"D:\BridgeWatch\First",
            InstanceId);

        Assert.Equal(
            "Project: D:\\Projects\\First\n"
            + "Watch Directory: D:\\BridgeWatch\\First\n"
            + "Instance ID: 0123456789abcdef0123456789abcdef",
            info);
    }

    [Fact]
    public void BashCommandConvertsWindowsDrivePathsAndQuotesShellMetacharacters()
    {
        string command = EditorBridgeSetup.BashLaunchCommand(
            @"D:\VR Chat\A's",
            @"D:\VR Chat\A's\watch",
            InstanceId);

        Assert.Equal(
            "env UNITYTOOL_UNITY_PROJECT_PATH='/mnt/d/VR Chat/A'\"'\"'s' "
            + "UNITYTOOL_BRIDGE_WATCH_DIR='/mnt/d/VR Chat/A'\"'\"'s/watch' "
            + "UNITYTOOL_BRIDGE_INSTANCE_ID='0123456789abcdef0123456789abcdef' "
            + "codex -C '/mnt/d/VR Chat/A'\"'\"'s'",
            command);
    }

    [Fact]
    public void PowerShellCommandEscapesSingleQuotesAndUsesProcessEnvironment()
    {
        string command = EditorBridgeSetup.PowerShellLaunchCommand(
            @"D:\A's",
            @"D:\A's\watch",
            InstanceId);

        Assert.Equal(
            "$env:UNITYTOOL_UNITY_PROJECT_PATH='D:\\A''s'; "
            + "$env:UNITYTOOL_BRIDGE_WATCH_DIR='D:\\A''s\\watch'; "
            + "$env:UNITYTOOL_BRIDGE_INSTANCE_ID='0123456789abcdef0123456789abcdef'; "
            + "codex -C 'D:\\A''s'",
            command);
    }

    [Theory]
    [InlineData("")]
    [InlineData("not-an-id")]
    [InlineData("0123456789ABCDEF0123456789ABCDEF")]
    public void CopyPayloadsRejectInvalidInstanceIdentity(string instanceId)
    {
        var error = Assert.Throws<ArgumentException>(() =>
            EditorBridgeSetup.ConnectionInfo(
                @"D:\Projects\First",
                @"D:\BridgeWatch\First",
                instanceId));

        Assert.Equal("instanceId", error.ParamName);
    }
}
