using PrefabSentinel;
using Xunit;

namespace PrefabSentinel.Tests;

/// <summary>
/// Issue #18 (H-10 T1) — exercises the stage hierarchy path normalization
/// extracted from the active-stage branch of
/// <c>ResolveGameObjectInActiveStage</c>. Each row pins one equivalence
/// class or boundary of the leading-slash normalization contract.
/// </summary>
public class StageHierarchyPathLogicTests
{
    [Theory]
    // Absolute multi-segment path: the single leading slash is removed so
    // the path resolves under the stage root via Transform.Find.
    [InlineData("/Root/Child", "Root/Child")]
    // Relative path: no leading slash, returned verbatim — the first
    // character of a relative path must not be over-stripped.
    [InlineData("Root/Child", "Root/Child")]
    // Single-segment absolute path: boundary one character past the slash.
    [InlineData("/Root", "Root")]
    // Slash-only input: substring boundary at length one normalizes to
    // the empty string.
    [InlineData("/", "")]
    // Double leading slash: exactly one slash is removed, never more.
    [InlineData("//Root", "/Root")]
    public void NormalizeStagePath_Strips_At_Most_One_Leading_Slash(
        string input, string expected)
    {
        Assert.Equal(expected, StageHierarchyPathLogic.NormalizeStagePath(input));
    }
}
