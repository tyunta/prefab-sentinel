using PrefabSentinel;
using Xunit;

namespace PrefabSentinel.Tests;

/// <summary>
/// Issue #310 — exercise the view-kind helper that owns the
/// scene-selector literal.  The bridge handler's scene-vs-game
/// routing decision delegates to
/// ``ScreenshotViewAllowlistClassifier.IsSceneView``; this test class
/// pins the classifier's contract over the documented two-selector
/// allowlist plus the rejected case/whitespace variants.
/// </summary>
public class ScreenshotViewKindTests
{
    [Theory]
    [InlineData("scene", true)]
    [InlineData("Scene", false)]
    [InlineData("game", false)]
    [InlineData("", false)]
    [InlineData(" scene", false)]
    public void IsSceneView_Matches_Documented_Acceptance(string selector, bool expected)
    {
        // C-1: ordinal equality against the literal ``"scene"`` is
        // the contract; case variants and whitespace variants are
        // rejected so the classifier and the wrapper-side allowlist
        // agree on the scene-selector form verbatim.
        bool result = ScreenshotViewAllowlistClassifier.IsSceneView(selector);

        Assert.Equal(expected, result);
    }
}
