using System.Reflection;
using Xunit;

namespace PrefabSentinel.Tests;

public sealed class BridgeDeployPromotionTests
{
    private const string RunId = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";
    private const string FileHash =
        "207f5fb13bb4ffd85df9cdd756681e02024218df1fcfd6e5774da904e4535935";
    private const string ManifestHash =
        "0907e9625a77ed73983971c18ddf8925b1a760bc98454b29eabe56963e4244b5";
    private const string BridgeVersion = "0.9.130";
    private const string TargetRelative = "Assets/Editor/PrefabSentinel";
    private const string TransactionRelative =
        "Library/PrefabSentinel/deploy-transactions/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";

    [Fact]
    public void TryCreate_Loads_The_Exact_Private_Manifest_And_Starts_Ready()
    {
        using var fixture = PromotionFixture.Create();

        bool success = fixture.TryCreate(out var request, out var code);

        Assert.Equal((true, "DEPLOY_OK"), (success, code));
        Assert.Equal("ready", request.State);
        Assert.Equal(RunId, request.RunId);
        Assert.Equal(ManifestHash, request.ManifestSha256);
        Assert.Equal(BridgeVersion, request.BridgeVersion);
        Assert.True(request.SameFileSystem);
        Assert.Single(request.Entries);
        Assert.Equal(
            ("PrefabSentinel.Test.cs", 11L, FileHash),
            (
                request.Entries[0].path,
                request.Entries[0].size,
                request.Entries[0].sha256));
    }

    [Theory]
    [InlineData("AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA", ManifestHash, BridgeVersion, "DEPLOY_STAGING_MISMATCH")]
    [InlineData("aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", ManifestHash, BridgeVersion, "DEPLOY_STAGING_MISMATCH")]
    [InlineData(RunId, "BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB", BridgeVersion, "DEPLOY_STAGING_MISMATCH")]
    [InlineData(RunId, "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb", BridgeVersion, "DEPLOY_STAGING_MISMATCH")]
    [InlineData(RunId, ManifestHash, "version-current", "DEPLOY_STAGING_MISMATCH")]
    [InlineData(RunId, ManifestHash, "1.2", "DEPLOY_STAGING_MISMATCH")]
    public void TryCreate_Rejects_Invalid_Request_Identity_Before_Promotion(
        string runId,
        string manifestHash,
        string bridgeVersion,
        string expectedCode)
    {
        using var fixture = PromotionFixture.Create();

        bool success = BridgeDeployPromotionRequest.TryCreate(
            fixture.ProjectRoot,
            TargetRelative,
            TransactionRelative,
            runId,
            manifestHash,
            bridgeVersion,
            out _,
            out var code);

        Assert.False(success);
        Assert.Equal(expectedCode, code);
        Assert.True(Directory.Exists(fixture.TargetPath));
        Assert.False(Directory.Exists(fixture.BackupPath));
    }

    [Theory]
    [InlineData("../Assets/Editor/PrefabSentinel", TransactionRelative)]
    [InlineData("Assets", TransactionRelative)]
    [InlineData("Library/PrefabSentinel", TransactionRelative)]
    [InlineData(TargetRelative, "../Library/PrefabSentinel/deploy-transactions/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa")]
    [InlineData(TargetRelative, "Library/PrefabSentinel/deploy-transactions/bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb")]
    [InlineData(TargetRelative, "Assets/deploy-transactions/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa")]
    public void TryCreate_Rejects_Paths_Outside_Their_Exact_Project_Regions(
        string targetPath,
        string transactionPath)
    {
        using var fixture = PromotionFixture.Create();

        bool success = BridgeDeployPromotionRequest.TryCreate(
            fixture.ProjectRoot,
            targetPath,
            transactionPath,
            RunId,
            ManifestHash,
            BridgeVersion,
            out _,
            out var code);

        Assert.False(success);
        Assert.Equal("DEPLOY_OUTSIDE_PROJECT", code);
        Assert.True(Directory.Exists(fixture.TargetPath));
        Assert.False(Directory.Exists(fixture.BackupPath));
    }

    [Fact]
    public void TryCreate_Rejects_A_Link_In_The_Transaction_Chain()
    {
        using var fixture = PromotionFixture.CreateWithoutTransaction();
        string realTransaction = Path.Combine(fixture.ProjectRoot, "real-transaction");
        Directory.CreateDirectory(realTransaction);
        fixture.WriteTransactionContents(realTransaction);
        Directory.CreateDirectory(Path.GetDirectoryName(fixture.TransactionPath)!);
        Directory.CreateSymbolicLink(fixture.TransactionPath, realTransaction);

        bool success = fixture.TryCreate(out _, out var code);

        Assert.False(success);
        Assert.Equal("DEPLOY_STAGING_FAILED", code);
        Assert.True(Directory.Exists(fixture.TargetPath));
        Assert.False(Directory.Exists(fixture.BackupPath));
    }


    [Fact]
    public void TryCreate_Rejects_A_Target_Reparse_Point()
    {
        using var fixture = PromotionFixture.Create();
        string realTarget = Path.Combine(fixture.Root, "real-target");
        Directory.Move(fixture.TargetPath, realTarget);
        Directory.CreateSymbolicLink(fixture.TargetPath, realTarget);

        bool success = fixture.TryCreate(out _, out var code);

        Assert.False(success);
        Assert.Equal("DEPLOY_STAGING_FAILED", code);
        Assert.False(Directory.Exists(fixture.BackupPath));
    }

    [Fact]
    public void TryCreate_Rejects_A_Staging_Reparse_Point()
    {
        using var fixture = PromotionFixture.Create();
        string realStaging = Path.Combine(fixture.Root, "real-staging");
        Directory.Move(fixture.StagingPath, realStaging);
        Directory.CreateSymbolicLink(fixture.StagingPath, realStaging);

        bool success = fixture.TryCreate(out _, out var code);

        Assert.False(success);
        Assert.Equal("DEPLOY_STAGING_FAILED", code);
        Assert.True(Directory.Exists(fixture.TargetPath));
    }

    [Fact]
    public void TryCreate_Rejects_A_Manifest_Reparse_Point()
    {
        using var fixture = PromotionFixture.Create();
        string realManifest = Path.Combine(fixture.Root, "real-manifest.json");
        File.Move(fixture.ManifestPath, realManifest);
        File.CreateSymbolicLink(fixture.ManifestPath, realManifest);

        bool success = fixture.TryCreate(out _, out var code);

        Assert.False(success);
        Assert.Equal("DEPLOY_STAGING_FAILED", code);
        Assert.True(Directory.Exists(fixture.TargetPath));
    }

    [Theory]
    [InlineData(
        "{\"schema\":\"wrong\",\"run_id\":\"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\",\"target\":\"Assets/Editor/PrefabSentinel\",\"bridge_version\":\"0.9.130\",\"manifest_sha256\":\"0907e9625a77ed73983971c18ddf8925b1a760bc98454b29eabe56963e4244b5\",\"files\":[{\"path\":\"PrefabSentinel.Test.cs\",\"size\":11,\"sha256\":\"207f5fb13bb4ffd85df9cdd756681e02024218df1fcfd6e5774da904e4535935\"}]}")]
    [InlineData(
        "{\"schema\":\"prefab_sentinel_bridge_source_manifest.v1\",\"run_id\":\"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\",\"target\":\"Assets/Wrong\",\"bridge_version\":\"0.9.130\",\"manifest_sha256\":\"0907e9625a77ed73983971c18ddf8925b1a760bc98454b29eabe56963e4244b5\",\"files\":[{\"path\":\"PrefabSentinel.Test.cs\",\"size\":11,\"sha256\":\"207f5fb13bb4ffd85df9cdd756681e02024218df1fcfd6e5774da904e4535935\"}]}")]
    [InlineData(
        "{\"schema\":\"prefab_sentinel_bridge_source_manifest.v1\",\"run_id\":\"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\",\"target\":\"Assets/Editor/PrefabSentinel\",\"bridge_version\":\"0.9.130\",\"manifest_sha256\":\"0907e9625a77ed73983971c18ddf8925b1a760bc98454b29eabe56963e4244b5\",\"files\":[{\"path\":\"PrefabSentinel.Test.cs\",\"size\":11,\"sha256\":\"207f5fb13bb4ffd85df9cdd756681e02024218df1fcfd6e5774da904e4535935\"}],\"unexpected\":true}")]
    [InlineData("{")]
    public void TryCreate_Rejects_A_Manifest_That_Is_Not_The_Exact_Private_Document(
        string document)
    {
        using var fixture = PromotionFixture.Create();
        File.WriteAllText(fixture.ManifestPath, document);

        bool success = fixture.TryCreate(out _, out var code);

        Assert.False(success);
        Assert.Equal("DEPLOY_STAGING_MISMATCH", code);
        Assert.True(Directory.Exists(fixture.TargetPath));
        Assert.False(Directory.Exists(fixture.BackupPath));
    }

    [Fact]
    public void TryCreate_Rejects_Unsorted_Manifest_Entries()
    {
        using var fixture = PromotionFixture.Create();
        fixture.WriteManifest(
            filesJson:
                "[{\"path\":\"Z.cs\",\"size\":0,\"sha256\":\"" + FileHash
                + "\"},{\"path\":\"A.cs\",\"size\":0,\"sha256\":\"" + FileHash
                + "\"}]",
            manifestHash: new string('b', 64));

        bool success = BridgeDeployPromotionRequest.TryCreate(
            fixture.ProjectRoot,
            TargetRelative,
            TransactionRelative,
            RunId,
            new string('b', 64),
            BridgeVersion,
            out _,
            out var code);

        Assert.False(success);
        Assert.Equal("DEPLOY_STAGING_MISMATCH", code);
    }


    [Fact]
    public void TryCreate_Rejects_Duplicate_Manifest_Entries_Separately()
    {
        using var fixture = PromotionFixture.Create();
        fixture.WriteManifest(
            filesJson:
                "[{\"path\":\"A.cs\",\"size\":0,\"sha256\":\"" + FileHash
                + "\"},{\"path\":\"A.cs\",\"size\":0,\"sha256\":\"" + FileHash
                + "\"}]",
            manifestHash: new string('b', 64));

        bool success = BridgeDeployPromotionRequest.TryCreate(
            fixture.ProjectRoot,
            TargetRelative,
            TransactionRelative,
            RunId,
            new string('b', 64),
            BridgeVersion,
            out _,
            out var code);

        Assert.False(success);
        Assert.Equal("DEPLOY_STAGING_MISMATCH", code);
    }

    [Theory]
    [InlineData("PrefabSentinel.Test.cs", "PrefabSentinel.\\u0054est.cs")]
    [InlineData("PrefabSentinel.Test.cs", "PrefabSentinel.Test\\u000acs")]
    [InlineData("Assets/Editor/PrefabSentinel", "Assets\\/Editor/PrefabSentinel")]
    [InlineData(
        "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        "\\u0061aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa")]
    public void TryCreate_Rejects_Escaped_Or_Control_Character_Manifest_Identity(
        string original,
        string replacement)
    {
        using var fixture = PromotionFixture.Create();
        string document = File.ReadAllText(fixture.ManifestPath);
        File.WriteAllText(
            fixture.ManifestPath,
            document.Replace(original, replacement, StringComparison.Ordinal));

        bool success = fixture.TryCreate(out _, out var code);

        Assert.False(success);
        Assert.Equal("DEPLOY_STAGING_MISMATCH", code);
    }

    [Fact]
    public void TryCreate_Rejects_An_Aggregate_Hash_That_Does_Not_Match_Entries()
    {
        using var fixture = PromotionFixture.Create();
        fixture.WriteManifest(manifestHash: new string('b', 64));

        bool success = BridgeDeployPromotionRequest.TryCreate(
            fixture.ProjectRoot,
            TargetRelative,
            TransactionRelative,
            RunId,
            new string('b', 64),
            BridgeVersion,
            out _,
            out var code);

        Assert.False(success);
        Assert.Equal("DEPLOY_STAGING_MISMATCH", code);
    }

    [Fact]
    public void State_Transitions_Only_Through_The_Complete_Promotion_Order()
    {
        using var fixture = PromotionFixture.Create();
        Assert.True(fixture.TryCreate(out var request, out _));

        Assert.False(request.TryMarkNewPromoted(out var earlyCode));
        Assert.Equal("DEPLOY_PROMOTION_FAILED", earlyCode);
        Assert.Equal("ready", request.State);

        Assert.True(request.TryMarkOldBackedUp(out var backupCode));
        Assert.Equal("DEPLOY_OK", backupCode);
        Assert.Equal("old_backed_up", request.State);

        Assert.True(request.TryMarkNewPromoted(out var promotedCode));
        Assert.Equal("DEPLOY_OK", promotedCode);
        Assert.Equal("new_promoted", request.State);

        Assert.True(request.TryMarkVerified(out var verifiedCode));
        Assert.Equal("DEPLOY_OK", verifiedCode);
        Assert.Equal("verified", request.State);

        Assert.True(request.TryMarkComplete(out var completeCode));
        Assert.Equal("DEPLOY_OK", completeCode);
        Assert.Equal("complete", request.State);

        Assert.False(request.TryBeginRollback(out var terminalCode));
        Assert.Equal("DEPLOY_PROMOTION_FAILED", terminalCode);
        Assert.Equal("complete", request.State);
    }

    [Fact]
    public void State_Transitions_To_Rolled_Back_From_An_Old_Backup()
    {
        using var fixture = PromotionFixture.Create();
        Assert.True(fixture.TryCreate(out var request, out _));
        Assert.True(request.TryMarkOldBackedUp(out _));

        Assert.True(request.TryBeginRollback(out var beginCode));
        Assert.Equal("DEPLOY_OK", beginCode);
        Assert.Equal("rollback_started", request.State);
        Assert.True(request.TryMarkRolledBack(out var rolledBackCode));
        Assert.Equal("DEPLOY_OK", rolledBackCode);
        Assert.Equal("rolled_back", request.State);
    }


    [Fact]
    public void State_Can_Begin_A_Real_Rollback_From_A_Ready_But_Moved_Layout()
    {
        using var fixture = PromotionFixture.Create();
        Assert.True(fixture.TryCreate(out var request, out _));

        Assert.True(request.TryBeginRollback(out var beginCode));
        Assert.Equal("DEPLOY_OK", beginCode);
        Assert.Equal("rollback_started", request.State);
        Assert.True(request.TryMarkRolledBack(out _));
        Assert.Equal("rolled_back", request.State);
    }

    [Fact]
    public void State_Transitions_To_Rollback_Failed_From_A_Promoted_Target()
    {
        using var fixture = PromotionFixture.Create();
        Assert.True(fixture.TryCreate(out var request, out _));
        Assert.True(request.TryMarkOldBackedUp(out _));
        Assert.True(request.TryMarkNewPromoted(out _));

        Assert.True(request.TryBeginRollback(out _));
        Assert.True(request.TryMarkRollbackFailed(out var code));

        Assert.Equal("DEPLOY_OK", code);
        Assert.Equal("rollback_failed", request.State);
    }

    [Fact]
    public void Results_Distinguish_Verified_Rollback_From_Retained_Backup()
    {
        var restored = BridgeDeployPromotionResult.RolledBack(
            ManifestHash,
            BridgeVersion,
            attempted: true);
        var failed = BridgeDeployPromotionResult.RollbackFailed(
            ManifestHash,
            BridgeVersion,
            backupRetained: true);

        Assert.Equal(
            (
                "DEPLOY_ROLLED_BACK",
                "rolled_back",
                true,
                true,
                false,
                true),
            (
                restored.code,
                restored.promotion_state,
                restored.rollback_attempted,
                restored.rollback_restored,
                restored.backup_retained,
                restored.target_complete));
        Assert.Equal(
            (
                "DEPLOY_ROLLBACK_FAILED",
                "rollback_failed",
                true,
                false,
                true,
                false),
            (
                failed.code,
                failed.promotion_state,
                failed.rollback_attempted,
                failed.rollback_restored,
                failed.backup_retained,
                failed.target_complete));
    }

    [Fact]
    public void VerifyPromotedTarget_Independently_Hashes_The_Promoted_Bytes()
    {
        using var fixture = PromotionFixture.Create();
        Assert.True(fixture.TryCreate(out var request, out _));
        Directory.Delete(fixture.TargetPath, recursive: true);
        Directory.Move(fixture.StagingPath, fixture.TargetPath);

        Assert.True(request.VerifyPromotedTarget(out var verifiedCode));
        Assert.Equal("DEPLOY_OK", verifiedCode);

        File.AppendAllText(
            Path.Combine(fixture.TargetPath, "PrefabSentinel.Test.cs"),
            "corrupt");

        Assert.False(request.VerifyPromotedTarget(out var mismatchCode));
        Assert.Equal("DEPLOY_FINAL_MANIFEST_MISMATCH", mismatchCode);
    }

    [Fact]
    public void Result_Projects_Only_Path_Free_Promotion_And_Rollback_Evidence()
    {
        var result = BridgeDeployPromotionResult.RollbackFailed(
            ManifestHash,
            BridgeVersion,
            backupRetained: true);

        Assert.Equal(
            (
                false,
                "critical",
                "DEPLOY_ROLLBACK_FAILED",
                "rollback_failed",
                true,
                true,
                false,
                true,
                false,
                ManifestHash,
                BridgeVersion),
            (
                result.success,
                result.severity,
                result.code,
                result.promotion_state,
                result.barrier_used,
                result.rollback_attempted,
                result.rollback_restored,
                result.backup_retained,
                result.target_complete,
                result.manifest_sha256,
                result.bridge_version));

        string[] publicFieldNames = typeof(BridgeDeployPromotionResult)
            .GetFields(BindingFlags.Instance | BindingFlags.Public)
            .Select(field => field.Name)
            .ToArray();
        Assert.DoesNotContain(
            publicFieldNames,
            name => name.Contains("path", StringComparison.OrdinalIgnoreCase));
        Assert.DoesNotContain(
            publicFieldNames,
            name => name.Contains("exception", StringComparison.OrdinalIgnoreCase));
    }


    [Fact]
    public void Pre_Backup_Failure_Is_Not_Reported_As_Rolled_Back()
    {
        var result = BridgeDeployPromotionResult.PreBackupFailed(
            ManifestHash,
            BridgeVersion,
            targetComplete: true);

        Assert.Equal(
            (
                false,
                "error",
                "DEPLOY_PROMOTION_FAILED",
                "not_attempted",
                true,
                false,
                false,
                false,
                true),
            (
                result.success,
                result.severity,
                result.code,
                result.promotion_state,
                result.barrier_used,
                result.rollback_attempted,
                result.rollback_restored,
                result.backup_retained,
                result.target_complete));
    }

    [Fact]
    public void Pre_Backup_Failure_Does_Not_Invent_Target_Completeness()
    {
        var result = BridgeDeployPromotionResult.PreBackupFailed(
            ManifestHash,
            BridgeVersion,
            targetComplete: false);

        Assert.False(result.target_complete);
        Assert.False(result.rollback_attempted);
        Assert.False(result.rollback_restored);
    }

    [Fact]
    public void Barrier_Acquisition_Failure_Is_Path_Free_And_Not_Used()
    {
        var result = BridgeDeployPromotionResult.BarrierUnavailable(
            ManifestHash,
            BridgeVersion);

        Assert.Equal(
            (
                false,
                "error",
                "DEPLOY_BARRIER_UNAVAILABLE",
                "not_attempted",
                false,
                false,
                false),
            (
                result.success,
                result.severity,
                result.code,
                result.promotion_state,
                result.barrier_used,
                result.rollback_attempted,
                result.target_complete));
    }


    [Fact]
    public void Compilation_Request_Failure_Preserves_The_Complete_Target_Outcome()
    {
        var promoted = BridgeDeployPromotionResult.Promoted(
            ManifestHash,
            BridgeVersion);

        var result = BridgeDeployPromotionResult.CompilationRequestFailed(promoted);

        Assert.Equal(
            "Bridge bundle promotion completed but source refresh/import failed.",
            result.message);
        Assert.Equal(
            (
                false,
                "error",
                "DEPLOY_REFRESH_FAILED",
                "promoted",
                true,
                true,
                ManifestHash,
                BridgeVersion),
            (
                result.success,
                result.severity,
                result.code,
                result.promotion_state,
                result.barrier_used,
                result.target_complete,
                result.manifest_sha256,
                result.bridge_version));
    }

    private sealed class PromotionFixture : IDisposable
    {
        private PromotionFixture(string root)
        {
            Root = root;
            ProjectRoot = Path.Combine(root, "project");
            TargetPath = Path.Combine(ProjectRoot, TargetRelative.Replace('/', Path.DirectorySeparatorChar));
            TransactionPath = Path.Combine(
                ProjectRoot,
                TransactionRelative.Replace('/', Path.DirectorySeparatorChar));
            StagingPath = Path.Combine(TransactionPath, "staged-target");
            BackupPath = Path.Combine(TransactionPath, "backup-target");
            ManifestPath = Path.Combine(TransactionPath, "source-manifest-v1.json");
        }

        internal string Root { get; }
        internal string ProjectRoot { get; }
        internal string TargetPath { get; }
        internal string TransactionPath { get; }
        internal string StagingPath { get; }
        internal string BackupPath { get; }
        internal string ManifestPath { get; }

        internal static PromotionFixture Create()
        {
            var fixture = CreateWithoutTransaction();
            Directory.CreateDirectory(fixture.TransactionPath);
            fixture.WriteTransactionContents(fixture.TransactionPath);
            return fixture;
        }

        internal static PromotionFixture CreateWithoutTransaction()
        {
            string root = Path.Combine(
                Path.GetTempPath(),
                "prefab-sentinel-promotion-tests",
                Guid.NewGuid().ToString("N"));
            var fixture = new PromotionFixture(root);
            Directory.CreateDirectory(fixture.TargetPath);
            File.WriteAllText(
                Path.Combine(fixture.TargetPath, "PrefabSentinel.Old.cs"),
                "bridge-old");
            return fixture;
        }

        internal void WriteTransactionContents(string transactionPath)
        {
            string stagingPath = Path.Combine(transactionPath, "staged-target");
            Directory.CreateDirectory(stagingPath);
            File.WriteAllText(
                Path.Combine(stagingPath, "PrefabSentinel.Test.cs"),
                "bridge-new\n");
            string manifestPath = Path.Combine(transactionPath, "source-manifest-v1.json");
            WriteManifest(manifestPath: manifestPath);
        }

        internal void WriteManifest(
            string? filesJson = null,
            string? manifestHash = null,
            string? manifestPath = null)
        {
            filesJson ??=
                "[{\"path\":\"PrefabSentinel.Test.cs\",\"sha256\":\"" + FileHash
                + "\",\"size\":11}]";
            manifestHash ??= ManifestHash;
            manifestPath ??= ManifestPath;
            string document =
                "{\"bridge_version\":\"" + BridgeVersion
                + "\",\"files\":" + filesJson
                + ",\"manifest_sha256\":\"" + manifestHash
                + "\",\"run_id\":\"" + RunId
                + "\",\"schema\":\"prefab_sentinel_bridge_source_manifest.v1\""
                + ",\"target\":\"" + TargetRelative + "\"}\n";
            File.WriteAllText(manifestPath, document);
        }

        internal bool TryCreate(
            out BridgeDeployPromotionRequest request,
            out string code)
        {
            return BridgeDeployPromotionRequest.TryCreate(
                ProjectRoot,
                TargetRelative,
                TransactionRelative,
                RunId,
                ManifestHash,
                BridgeVersion,
                out request,
                out code);
        }

        public void Dispose()
        {
            if (Directory.Exists(Root))
            {
                Directory.Delete(Root, recursive: true);
            }
        }
    }
}
