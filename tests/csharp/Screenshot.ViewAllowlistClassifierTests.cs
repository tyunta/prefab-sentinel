using System;
using PrefabSentinel;
using Xunit;

namespace PrefabSentinel.Tests;

/// <summary>
/// Issue #222 Phase 3 — exercise the pure view-allowlist classifier end-to-end.
///
/// The bridge handler ``HandleCaptureScreenshot`` delegates its view-acceptance
/// gate to ``ScreenshotViewAllowlistClassifier.IsAccepted``; this test class
/// pins the classifier's contract over the documented accepted-set (the two
/// lower-case ASCII selectors ``"scene"`` and ``"game"``), the reject-set
/// including case and whitespace variants, and the empty-accept-set
/// degenerate case.
/// </summary>
public class ScreenshotViewAllowlistClassifierTests
{
    private static readonly string[] AcceptedSet = new[] { "scene", "game" };

    [Theory]
    [InlineData("scene")]
    [InlineData("game")]
    public void Accepts_Every_Member_Of_The_Accepted_Set(string selector)
    {
        // C-1: ordinal equality against an accepted-set member yields true.
        bool result = ScreenshotViewAllowlistClassifier.IsAccepted(selector, AcceptedSet);

        Assert.True(
            result,
            $"selector \"{selector}\" must be accepted; AcceptedSet={{ scene, game }}.");
    }

    [Theory]
    [InlineData("Scene")]
    [InlineData("SCENE")]
    [InlineData("Game")]
    [InlineData("GAME")]
    [InlineData(" scene")]
    [InlineData("scene ")]
    [InlineData("scene\t")]
    [InlineData("")]
    [InlineData("hierarchy")]
    [InlineData("inspector")]
    public void Rejects_Selectors_Not_Ordinally_Equal_To_Any_Member(string selector)
    {
        // C-2: case variants, whitespace variants, and content variants are
        // all rejected because comparison is ``StringComparison.Ordinal``.
        bool result = ScreenshotViewAllowlistClassifier.IsAccepted(selector, AcceptedSet);

        Assert.False(
            result,
            $"selector \"{selector}\" must be rejected (ordinal comparison; AcceptedSet={{ scene, game }}).");
    }

    [Theory]
    [InlineData("scene")]
    [InlineData("game")]
    [InlineData("anything")]
    [InlineData("")]
    public void Rejects_Every_Selector_Against_An_Empty_Accept_Set(string selector)
    {
        // C-3: an empty accepted-set is the open-by-default protection;
        // every selector must be rejected, including selectors that would
        // succeed against the documented two-member set.
        bool result = ScreenshotViewAllowlistClassifier.IsAccepted(selector, Array.Empty<string>());

        Assert.False(
            result,
            $"selector \"{selector}\" must be rejected against an empty accepted-set.");
    }
}
