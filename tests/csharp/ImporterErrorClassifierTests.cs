using PrefabSentinel;
using Xunit;

namespace PrefabSentinel.Tests;

/// <summary>
/// Issue #45 (T-45-1) — exercises the Unity-free
/// <see cref="ImporterErrorClassifier"/> predicate the synchronous
/// recompile handler's no-op branch scans the console buffer with.
/// Each row pins one equivalence class: the two AssetDatabase
/// importer-error line shapes classify true, a benign line classifies
/// false.
/// </summary>
public class ImporterErrorClassifierTests
{
    [Theory]
    // The SourceAssetDB modification-time mismatch line.
    [InlineData(
        "Build asset version error: Assets/Foo.prefab has an unexpected hash",
        true)]
    // The import-worker warning line, e.g. as emitted by a build worker.
    [InlineData("[Worker0] Import Error Code:(4) Message", true)]
    // A benign compilation-progress line carries neither marker.
    [InlineData("Reloading assemblies after script compilation.", false)]
    public void IsImporterError_Classifies_Console_Lines(
        string consoleLine, bool expected)
    {
        Assert.Equal(expected, ImporterErrorClassifier.IsImporterError(consoleLine));
    }

    [Fact]
    public void IsImporterError_Treats_Null_And_Empty_As_Not_An_Error()
    {
        Assert.False(ImporterErrorClassifier.IsImporterError(null!));
        Assert.False(ImporterErrorClassifier.IsImporterError(string.Empty));
    }
}
