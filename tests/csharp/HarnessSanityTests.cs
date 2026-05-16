using Xunit;

namespace PrefabSentinel.Tests;

/// <summary>
/// Sanity-only Fact whose role is to prove that test discovery, the runner
/// adapter, the test SDK, and the lock file are mutually consistent. It
/// makes no assertion about bridge behavior. The follow-up migrations
/// enumerated under issue #290 add behavior-executing coverage on top of
/// this harness.
/// </summary>
public class HarnessSanityTests
{
    [Fact]
    public void Harness_Discovers_And_Runs_One_Fact()
    {
        // The assertion is a non-tautological identity rather than
        // `Assert.True(true)` so xUnit2021 ("always-passing assertion")
        // does not mask the sanity wiring proof, and so flipping the
        // expected value to any other integer produces a specific failure
        // message that pins T-1's Failure Mode Caught row (one failed test
        // alongside a non-zero exit).
        Assert.Equal(2, 1 + 1);
    }
}
