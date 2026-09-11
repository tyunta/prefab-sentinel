using Xunit;

namespace PrefabSentinel.Tests;

public sealed class BridgeDeployPromotionEvidenceTests
{
    [Fact]
    public void Volume_Root_Resolution_Uses_The_Longest_Canonical_Match()
    {
        string outer = Path.Combine(Path.GetTempPath(), "prefab-sentinel-volume-root");
        string nested = Path.Combine(outer, "nested-mount");
        string target = Path.Combine(nested, "project", "Assets");

        bool resolved = BridgeDeployPromotionEvidence.TryResolveVolumeRoot(
            target,
            new[] { Path.GetPathRoot(outer)!, outer, nested },
            out var root);

        Assert.True(resolved);
        Assert.Equal(Path.GetFullPath(nested), root);
    }

    [Fact]
    public void Volume_Root_Resolution_Fails_Closed_When_No_Root_Matches()
    {
        string target = Path.Combine(Path.GetTempPath(), "project", "Assets");
        string unrelated = Path.Combine(Path.GetTempPath(), "other-root");

        bool resolved = BridgeDeployPromotionEvidence.TryResolveVolumeRoot(
            target,
            new[] { unrelated },
            out var root);

        Assert.False(resolved);
        Assert.Equal(string.Empty, root);
        Assert.False(BridgeDeployPromotionEvidence.ShareVolume(
            target,
            Path.Combine(target, "transaction"),
            new[] { unrelated }));
    }

    [Fact]
    public void Explicit_Different_Mount_Roots_Are_Not_The_Same_Volume()
    {
        string basePath = Path.Combine(Path.GetTempPath(), "prefab-sentinel-mounts");
        string firstRoot = Path.Combine(basePath, "first");
        string secondRoot = Path.Combine(basePath, "second");

        bool same = BridgeDeployPromotionEvidence.ShareVolume(
            Path.Combine(firstRoot, "Assets"),
            Path.Combine(secondRoot, "transaction"),
            new[] { firstRoot, secondRoot });

        Assert.False(same);
    }

    [Fact]
    public void Unix_DevShm_Is_Rejected_When_It_Is_A_Different_Reported_Mount()
    {
        if (OperatingSystem.IsWindows() || !Directory.Exists("/dev/shm"))
        {
            return;
        }
        string[] roots = DriveInfo.GetDrives()
            .Select(drive => drive.RootDirectory.FullName)
            .ToArray();
        if (!BridgeDeployPromotionEvidence.TryResolveVolumeRoot(
                Path.GetTempPath(), roots, out var tempRoot)
            || !BridgeDeployPromotionEvidence.TryResolveVolumeRoot(
                "/dev/shm", roots, out var shmRoot)
            || string.Equals(tempRoot, shmRoot, StringComparison.Ordinal))
        {
            return;
        }

        Assert.False(BridgeDeployPromotionEvidence.ShareVolume(
            Path.GetTempPath(),
            "/dev/shm",
            roots));
    }

    [Fact]
    public void Windows_Different_Drives_Are_Rejected_When_Available()
    {
        if (!OperatingSystem.IsWindows())
        {
            return;
        }
        string[] roots = DriveInfo.GetDrives()
            .Where(drive => drive.IsReady && Directory.Exists(drive.RootDirectory.FullName))
            .Select(drive => drive.RootDirectory.FullName)
            .Distinct(StringComparer.OrdinalIgnoreCase)
            .Take(2)
            .ToArray();
        if (roots.Length < 2)
        {
            return;
        }

        Assert.False(BridgeDeployPromotionEvidence.ShareVolume(
            roots[0],
            roots[1],
            roots));
    }

    [Theory]
    [InlineData("ready", true, false, true, "pre_backup_intact")]
    [InlineData("ready", false, true, true, "restore_backup")]
    [InlineData("old_backed_up", false, true, true, "restore_backup")]
    [InlineData("new_promoted", true, true, false, "move_promoted_then_restore")]
    [InlineData("ready", false, false, true, "ambiguous")]
    [InlineData("ready", true, false, false, "ambiguous")]
    [InlineData("new_promoted", true, false, false, "ambiguous")]
    public void Recovery_Decision_Requires_An_Exact_Filesystem_Layout(
        string state,
        bool targetExists,
        bool backupExists,
        bool stagingExists,
        string expected)
    {
        string decision = BridgeDeployPromotionEvidence.DecideRecovery(
            state,
            targetExists,
            backupExists,
            stagingExists);

        Assert.Equal(expected, decision);
    }

    [Fact]
    public void Final_Restoration_Evidence_Requires_Target_And_Staging_Without_Backup()
    {
        Assert.True(BridgeDeployPromotionEvidence.IsRestoredLayout(
            targetExists: true,
            backupExists: false,
            stagingExists: true));
        Assert.False(BridgeDeployPromotionEvidence.IsRestoredLayout(
            targetExists: true,
            backupExists: true,
            stagingExists: true));
        Assert.False(BridgeDeployPromotionEvidence.IsRestoredLayout(
            targetExists: true,
            backupExists: false,
            stagingExists: false));
    }


    [Theory]
    [InlineData(
        "PrefabSentinel.Test\n.cs",
        "5278f676d8c1e1d717a35151d53cc39b65845f94de79200ea0618bbf6e908054")]
    [InlineData(
        "PrefabSentinel.Test\t.asmdef",
        "e9c983aca17c20b422dd75a4aacab29ea246a4e7ce725208da368342073dd826")]
    [InlineData(
        "PrefabSentinel.Test\r.cs",
        "01e00d7146053e2634f219019f9eb23035f07bd1c160cc6bfe3f0e9ab41fdde9")]
    [InlineData(
        "PrefabSentinel.Test\0.asmdef",
        "36a691696c2cd28e4276cc6240b7505b78b9a7cf177430d7bb2aed0fed119c1d")]
    [InlineData(
        "PrefabSentinel.Test\u0085.cs",
        "d2f5159ea98d0ed24a6d66ae22d387aeeb5ada7378fc004fb967713cdce99bb3")]
    public void Manifest_Entries_Reject_Unicode_Control_Characters_With_Valid_Suffixes(
        string path,
        string aggregateHash)
    {
        var entry = new BridgeDeployManifestEntry
        {
            path = path,
            size = 11L,
            sha256 =
                "207f5fb13bb4ffd85df9cdd756681e02024218df1fcfd6e5774da904e4535935",
        };

        bool valid = BridgeDeployPromotionEvidence.ValidEntries(
            new[] { entry },
            aggregateHash);

        Assert.False(valid);
    }

    [Fact]
    public void Manifest_Entries_Reject_Invalid_Extension_Separately()
    {
        var entry = new BridgeDeployManifestEntry
        {
            path = "PrefabSentinel.Test.txt",
            size = 11L,
            sha256 =
                "207f5fb13bb4ffd85df9cdd756681e02024218df1fcfd6e5774da904e4535935",
        };

        bool valid = BridgeDeployPromotionEvidence.ValidEntries(
            new[] { entry },
            "ab1eb717bbbcaa71158c6f8b6c60db27ccf9e4ba5a1f2f3b382f31310faf1d6f");

        Assert.False(valid);
    }
}
