using PrefabSentinel;
using Xunit;

namespace PrefabSentinel.Tests;

public class UnityAcceptanceLeaseTests
{
    private const string RunId = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";
    private const string OtherRunId = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb";
    private const string RequestId = "cccccccccccccccccccccccccccccccc";

    private static UnityAcceptanceLeaseScene[] Scenes() =>
    [
        new UnityAcceptanceLeaseScene
        {
            path = "Assets/Scenes/Primary.unity",
            is_loaded = true,
            is_active = true,
            is_dirty = false,
        },
        new UnityAcceptanceLeaseScene
        {
            path = "Assets/Scenes/Secondary.unity",
            is_loaded = false,
            is_active = false,
            is_dirty = false,
        },
    ];

    private static string[] FixturePaths() =>
    [
        UnityAcceptanceLeaseState.FixtureRootForRun(RunId),
    ];

    private static string[] RequestPaths()
    {
        string directory = Path.GetTempPath().TrimEnd(
            Path.DirectorySeparatorChar,
            Path.AltDirectorySeparatorChar);
        const string id = "cccccccccccccccccccccccccccccccc";
        return
        [
            Path.Combine(directory, id + ".request.json"),
            Path.Combine(directory, id + ".request.json.tmp"),
            Path.Combine(directory, id + ".response.json"),
            Path.Combine(directory, id + ".response.json.tmp"),
            Path.Combine(directory, id + ".publication-failed.json"),
        ];
    }

    [Fact]
    public void Create_Reserves_A_Run_Owned_Lease()
    {
        UnityAcceptanceLeaseState lease = UnityAcceptanceLeaseState.Create(
            runId: RunId,
            originalScenes: Scenes(),
            fixturePaths: FixturePaths(),
            requestId: RequestId,
            requestPaths: RequestPaths());

        Assert.Equal(1, lease.schema_version);
        Assert.Equal(RunId, lease.run_id);
        Assert.Equal("reserved", lease.phase);
        Assert.True(
            lease.Owns(
                UnityAcceptanceLeaseState.FixtureRootForRun(RunId)
                    + "/AcceptanceFixture.prefab"));
        Assert.False(
            lease.Owns(
                UnityAcceptanceLeaseState.FixtureRootForRun(OtherRunId)
                    + "/AcceptanceFixture.prefab"));
        Assert.NotEqual(string.Empty, lease.created_utc);
        Assert.Equal(lease.created_utc, lease.updated_utc);
    }

    [Fact]
    public void Transitions_Advance_In_The_Only_Valid_Order()
    {
        UnityAcceptanceLeaseState lease = UnityAcceptanceLeaseState.Create(
            RunId,
            Scenes(),
            FixturePaths(),
            RequestId,
            RequestPaths());

        foreach (string nextPhase in new[]
        {
            "fixture_created",
            "smoke_complete",
            "cleanup_started",
            "cleaned",
        })
        {
            Assert.True(
                lease.TryTransition(
                    RunId,
                    nextPhase,
                    out string errorCode,
                    out _));
            Assert.Equal(string.Empty, errorCode);
            Assert.Equal(nextPhase, lease.phase);
        }
    }

    [Fact]
    public void Transition_Rejects_A_Skipped_Or_Unknown_Phase()
    {
        UnityAcceptanceLeaseState lease = UnityAcceptanceLeaseState.Create(
            RunId,
            Scenes(),
            FixturePaths(),
            RequestId,
            RequestPaths());

        Assert.False(
            lease.TryTransition(
                RunId,
                "smoke_complete",
                out string skippedCode,
                out _));
        Assert.Equal("EDITOR_CTRL_ACCEPTANCE_PHASE_INVALID", skippedCode);
        Assert.Equal("reserved", lease.phase);

        lease.phase = "future_phase";
        Assert.False(
            lease.TryValidate(out string unknownCode, out _));
        Assert.Equal("EDITOR_CTRL_ACCEPTANCE_LEASE_INVALID", unknownCode);
    }

    [Fact]
    public void Different_Run_Cannot_Status_Or_Cleanup_An_Active_Lease()
    {
        UnityAcceptanceLeaseState lease = UnityAcceptanceLeaseState.Create(
            RunId,
            Scenes(),
            FixturePaths(),
            RequestId,
            RequestPaths());

        Assert.False(
            lease.TryAssertOwner(
                OtherRunId,
                out string ownershipCode,
                out _));
        Assert.Equal("EDITOR_CTRL_ACCEPTANCE_RUN_MISMATCH", ownershipCode);
        Assert.False(
            lease.TryTransition(
                OtherRunId,
                "fixture_created",
                out string transitionCode,
                out _));
        Assert.Equal("EDITOR_CTRL_ACCEPTANCE_RUN_MISMATCH", transitionCode);
        Assert.Equal("reserved", lease.phase);
    }

    [Theory]
    [InlineData("")]
    [InlineData("aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa")]
    [InlineData("aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa")]
    [InlineData("AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA")]
    [InlineData("gggggggggggggggggggggggggggggggg")]
    public void Create_Rejects_Invalid_Run_Ids(string runId)
    {
        var exception = Assert.Throws<ArgumentException>(
            () => UnityAcceptanceLeaseState.Create(
                runId,
                Scenes(),
                FixturePaths()));

        Assert.Equal("runId", exception.ParamName);
    }

    [Fact]
    public void Validation_Rejects_A_Fixture_Path_Owned_By_Another_Run()
    {
        UnityAcceptanceLeaseState lease = UnityAcceptanceLeaseState.Create(
            RunId,
            Scenes(),
            FixturePaths(),
            RequestId,
            RequestPaths());
        lease.fixture_paths =
        [
            UnityAcceptanceLeaseState.FixtureRootForRun(OtherRunId),
        ];

        Assert.False(lease.TryValidate(out string errorCode, out _));
        Assert.Equal("EDITOR_CTRL_ACCEPTANCE_LEASE_INVALID", errorCode);
    }

    [Fact]
    public void Validation_Rejects_Missing_Or_Ambiguous_Active_Scene()
    {
        UnityAcceptanceLeaseState lease = UnityAcceptanceLeaseState.Create(
            RunId,
            Scenes(),
            FixturePaths(),
            RequestId,
            RequestPaths());
        lease.original_scenes[1].is_loaded = true;
        lease.original_scenes[1].is_active = true;

        Assert.False(lease.TryValidate(out string errorCode, out _));
        Assert.Equal("EDITOR_CTRL_ACCEPTANCE_LEASE_INVALID", errorCode);
    }

    [Fact]
    public void File_Publication_Replaces_The_Complete_Previous_Document()
    {
        string directory = Path.Combine(
            Path.GetTempPath(),
            "PrefabSentinelLeaseTests_" + Guid.NewGuid().ToString("N"));
        Directory.CreateDirectory(directory);
        string leasePath = Path.Combine(directory, "acceptance-lease-v1.json");

        try
        {
            UnityAcceptanceLeaseFile.Publish(leasePath, "{\"phase\":\"reserved\"}");
            UnityAcceptanceLeaseFile.Publish(leasePath, "{\"phase\":\"cleaned\"}");

            Assert.Equal(
                "{\"phase\":\"cleaned\"}",
                File.ReadAllText(leasePath));
            Assert.Empty(Directory.GetFiles(directory, "*.tmp"));
        }
        finally
        {
            Directory.Delete(directory, true);
        }
    }

    [Fact]
    public void FixtureRoot_Is_The_Integration_Fixture_Root_And_Rejects_Traversal()
    {
        UnityAcceptanceLeaseState lease = UnityAcceptanceLeaseState.Create(
            RunId,
            Scenes(),
            new[] { UnityAcceptanceLeaseState.FixtureRootForRun(RunId) },
            RequestId,
            RequestPaths());

        Assert.Equal(
            "Assets/PrefabSentinelIntegrationTests/" + RunId,
            UnityAcceptanceLeaseState.FixtureRootForRun(RunId));
        Assert.True(lease.Owns(
            UnityAcceptanceLeaseState.FixtureRootForRun(RunId)
                + "/AcceptanceFixture.prefab"));
        Assert.False(lease.Owns(
            UnityAcceptanceLeaseState.FixtureRootForRun(RunId)
                + "/../" + OtherRunId + "/AcceptanceFixture.prefab"));
        Assert.False(lease.Owns(
            UnityAcceptanceLeaseState.FixtureRootForRun(RunId)
                + "/./AcceptanceFixture.prefab"));
    }

    [Theory]
    [InlineData("reserved")]
    [InlineData("fixture_created")]
    [InlineData("smoke_complete")]
    [InlineData("cleanup_started")]
    public void BeginCleanup_Recovers_Each_Unfinished_Phase_Without_Claiming_Smoke(
        string phase)
    {
        UnityAcceptanceLeaseState lease = UnityAcceptanceLeaseState.Create(
            RunId,
            Scenes(),
            FixturePaths(),
            RequestId,
            RequestPaths());

        if (phase == "fixture_created" || phase == "smoke_complete")
        {
            Assert.True(lease.TryTransition(
                RunId,
                UnityAcceptanceLeaseState.FixtureCreatedPhase,
                out _,
                out _));
        }
        if (phase == "smoke_complete")
        {
            Assert.True(lease.TryTransition(
                RunId,
                UnityAcceptanceLeaseState.SmokeCompletePhase,
                out _,
                out _));
        }
        if (phase == "cleanup_started")
        {
            Assert.True(lease.TryBeginCleanup(RunId, out _, out _));
        }

        Assert.True(lease.TryBeginCleanup(
            RunId,
            out string code,
            out _));
        Assert.Equal(string.Empty, code);
        Assert.Equal(UnityAcceptanceLeaseState.CleanupStartedPhase, lease.phase);
    }

    [Fact]
    public void RequestArtifacts_Are_Retained_And_Traversal_Is_Rejected()
    {
        UnityAcceptanceLeaseState lease = UnityAcceptanceLeaseState.Create(
            RunId,
            Scenes(),
            FixturePaths(),
            RequestId,
            RequestPaths());

        Assert.Equal(RequestPaths(), lease.request_paths);
        lease.request_paths[0] =
            "C:/PrefabSentinelBridge/../other.request.json";

        Assert.False(lease.TryValidate(out string code, out _));
        Assert.Equal("EDITOR_CTRL_ACCEPTANCE_LEASE_INVALID", code);
    }

    [Fact]
    public void RequestArtifacts_Reject_Foreign_Id_Missing_And_Extra_Paths()
    {
        UnityAcceptanceLeaseState lease = UnityAcceptanceLeaseState.Create(
            RunId,
            Scenes(),
            FixturePaths(),
            RequestId,
            RequestPaths());

        lease.request_paths[2] =
            "C:/PrefabSentinelBridge/dddddddddddddddddddddddddddddddd.response.json";
        Assert.False(lease.TryValidate(out string foreignCode, out _));
        Assert.Equal("EDITOR_CTRL_ACCEPTANCE_LEASE_INVALID", foreignCode);

        lease.request_paths = RequestPaths()[..4];
        Assert.False(lease.TryValidate(out string missingCode, out _));
        Assert.Equal("EDITOR_CTRL_ACCEPTANCE_LEASE_INVALID", missingCode);

        lease.request_paths = [.. RequestPaths(), "C:/PrefabSentinelBridge/cccccccccccccccccccccccccccccccc.extra"];
        Assert.False(lease.TryValidate(out string extraCode, out _));
        Assert.Equal("EDITOR_CTRL_ACCEPTANCE_LEASE_INVALID", extraCode);
    }

    [Fact]
    public void ArtifactSet_Rejects_LeadingTraversal_NonRooted_Drift_And_Duplicate()
    {
        UnityAcceptanceLeaseState lease = UnityAcceptanceLeaseState.Create(
            RunId, Scenes(), FixturePaths(), RequestId, RequestPaths());

        lease.request_paths[0] = Path.Combine(
            Path.GetTempPath(), "..",
            "cccccccccccccccccccccccccccccccc.request.json");
        Assert.False(lease.TryValidate(out _, out _));

        lease.request_paths = RequestPaths();
        lease.request_paths[1] = "cccccccccccccccccccccccccccccccc.request.json.tmp";
        Assert.False(lease.TryValidate(out _, out _));

        lease.request_paths = RequestPaths();
        lease.request_paths[4] = Path.Combine(
            Path.GetTempPath(), "other",
            "cccccccccccccccccccccccccccccccc.publication-failed.json");
        Assert.False(lease.TryValidate(out _, out _));

        lease.request_paths = RequestPaths();
        lease.request_paths[4] = lease.request_paths[3];
        Assert.False(lease.TryValidate(out _, out _));
    }

    [Fact]
    public void Cleanup_Rejects_A_Foreign_Canonical_Set_Without_Deleting_Artifacts()
    {
        string root = Path.Combine(
            Path.GetTempPath(),
            "PrefabSentinelLeaseOwnership_" + Guid.NewGuid().ToString("N"));
        string trustedDirectory = Path.Combine(root, "trusted");
        string foreignDirectory = Path.Combine(root, "foreign");
        Directory.CreateDirectory(trustedDirectory);
        Directory.CreateDirectory(foreignDirectory);
        const string originalRequestId =
            "cccccccccccccccccccccccccccccccc";
        string[] foreignPaths =
        [
            Path.Combine(foreignDirectory, originalRequestId + ".request.json"),
            Path.Combine(foreignDirectory, originalRequestId + ".request.json.tmp"),
            Path.Combine(foreignDirectory, originalRequestId + ".response.json"),
            Path.Combine(foreignDirectory, originalRequestId + ".response.json.tmp"),
            Path.Combine(
                foreignDirectory,
                originalRequestId + ".publication-failed.json"),
        ];
        string currentCleanupRequestPath = Path.Combine(
            trustedDirectory,
            "dddddddddddddddddddddddddddddddd.request.json");

        try
        {
            foreach (string path in foreignPaths)
                File.WriteAllText(path, "owned by foreign directory");

            UnityAcceptanceLeaseState lease = UnityAcceptanceLeaseState.Create(
                RunId,
                Scenes(),
                FixturePaths(),
                originalRequestId,
                foreignPaths);

            Assert.False(lease.TryDeleteRequestArtifacts(
                currentCleanupRequestPath,
                out string errorCode,
                out _));
            Assert.Equal(
                "EDITOR_CTRL_ACCEPTANCE_LEASE_INVALID",
                errorCode);
            Assert.All(foreignPaths, path => Assert.True(File.Exists(path)));
        }
        finally
        {
            Directory.Delete(root, true);
        }
    }

    [Fact]
    public void SceneSetupComparer_Rejects_Each_Semantic_Difference()
    {
        UnityAcceptanceLeaseScene[] expected = Scenes();
        UnityAcceptanceLeaseScene[] actual = Scenes();

        Assert.True(UnityAcceptanceSceneSetupComparer.Matches(expected, actual));
        Assert.False(UnityAcceptanceSceneSetupComparer.Matches(
            expected, actual[..1]));

        actual = Scenes();
        (actual[0], actual[1]) = (actual[1], actual[0]);
        Assert.False(UnityAcceptanceSceneSetupComparer.Matches(expected, actual));

        actual = Scenes();
        actual[0].path = "Assets/Scenes/Changed.unity";
        Assert.False(UnityAcceptanceSceneSetupComparer.Matches(expected, actual));

        actual = Scenes();
        actual[1].is_loaded = true;
        Assert.False(UnityAcceptanceSceneSetupComparer.Matches(expected, actual));

        actual = Scenes();
        actual[0].is_active = false;
        Assert.False(UnityAcceptanceSceneSetupComparer.Matches(expected, actual));

        actual = Scenes();
        actual[0].is_dirty = true;
        Assert.False(UnityAcceptanceSceneSetupComparer.Matches(expected, actual));
    }
}
