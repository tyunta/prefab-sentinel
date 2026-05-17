using PrefabSentinel;
using Xunit;

namespace PrefabSentinel.Tests;

/// <summary>
/// Issue #10 (H-2) — exercises the MenuScriptWatch Editor-source path
/// classifier end-to-end. The bridge change detector keeps the directory walk
/// and mtime comparison and delegates per-path classification to
/// <see cref="EditorScriptPathClassifier.IsEditorSourcePath"/>.
/// </summary>
public class EditorScriptPathClassifierTests
{
    [Theory]
    [InlineData("Editor/Foo.cs")]
    [InlineData("Editor/Sub/Bar.cs")]
    public void Path_Under_An_Editor_Segment_Is_Editor_Source(string relativePath)
    {
        Assert.True(
            EditorScriptPathClassifier.IsEditorSourcePath(relativePath),
            $"'{relativePath}' has an Editor directory segment and must qualify.");
    }

    [Fact]
    public void Editor_Segment_Below_The_Root_Still_Qualifies()
    {
        // The classifier inspects every directory segment, not only the top
        // one — a feature-scoped Editor folder must qualify.
        Assert.True(
            EditorScriptPathClassifier.IsEditorSourcePath("Feature/Editor/Deep/Tool.cs"),
            "A nested Editor segment must qualify.");
    }

    [Fact]
    public void Run_Script_Temp_Segment_Excludes_The_Path()
    {
        // The temporary-area exclusion takes precedence over the Editor match.
        Assert.False(
            EditorScriptPathClassifier.IsEditorSourcePath(
                "Editor/_PrefabSentinelTemp/Gen.cs"),
            "A whole _PrefabSentinelTemp segment must exclude the path even "
            + "though an Editor segment is also present.");
    }

    [Fact]
    public void Segment_Merely_Containing_The_Temp_Token_Is_Not_Excluded()
    {
        // Whole-segment match: '_PrefabSentinelTempX' is not '_PrefabSentinelTemp',
        // so a substring-based check would wrongly exclude this path.
        Assert.True(
            EditorScriptPathClassifier.IsEditorSourcePath(
                "Editor/_PrefabSentinelTempX/Tool.cs"),
            "A segment that only contains the temp token as a substring must "
            + "not be excluded.");
    }

    [Fact]
    public void Non_Editor_Path_Does_Not_Qualify()
    {
        Assert.False(
            EditorScriptPathClassifier.IsEditorSourcePath("Runtime/Scripts/Foo.cs"),
            "A path with no Editor directory segment must not qualify.");
    }

    [Fact]
    public void Backslash_Separated_Path_Is_Classified_By_Whole_Segment()
    {
        // The classifier accepts both directory separators so the Windows
        // GetFiles output is classified the same way as a POSIX path.
        Assert.True(
            EditorScriptPathClassifier.IsEditorSourcePath(@"Feature\Editor\Tool.cs"),
            "A backslash-separated path must be split into whole segments.");
    }
}
