using PrefabSentinel;
using Xunit;

namespace PrefabSentinel.Tests;

/// <summary>
/// Issue #11 (H-3) — exercises the console-capture phase predicates, phase
/// classification, and request validation extracted from the
/// <c>capture_console_logs</c> handler and <c>ConsoleLogBuffer</c>.
/// </summary>
public class ConsoleLogEntryPredicateTests
{
    [Theory]
    [InlineData("", "play", true)]    // empty filter admits every entry
    [InlineData("all", "play", true)] // catch-all token admits every entry
    [InlineData("play", "play", true)]
    [InlineData("play", "edit", false)]
    public void Phase_Filter_Admits_On_Catch_All_And_Otherwise_Requires_Exact_Match(
        string filter, string entryPhase, bool expected)
    {
        Assert.Equal(
            expected,
            ConsoleLogEntryPredicate.MatchesPhaseFilter(entryPhase, filter));
    }

    [Theory]
    [InlineData("edit", true)]
    [InlineData("play", true)]
    [InlineData("build", true)]
    [InlineData("", true)]
    [InlineData("staging", false)]
    public void Phase_Filter_Support_Reports_Membership(string value, bool expected)
    {
        Assert.Equal(expected, ConsoleLogEntryPredicate.IsSupportedPhaseFilter(value));
    }

    [Theory]
    [InlineData("non_fatal", true)]
    [InlineData("fatal", true)]
    [InlineData("", true)]
    [InlineData("noisy", false)]
    public void Classification_Filter_Support_Reports_Membership(
        string value, bool expected)
    {
        Assert.Equal(
            expected, ConsoleLogEntryPredicate.IsSupportedClassificationFilter(value));
    }
}

/// <summary>
/// Phase classification priority: build outranks play outranks edit.
/// </summary>
public class ConsoleLogPhaseClassifierTests
{
    [Theory]
    [InlineData(true, false, "build")]
    [InlineData(true, true, "build")]   // build wins even while playing
    [InlineData(false, true, "play")]
    [InlineData(false, false, "edit")]
    public void Classify_Applies_Build_Over_Play_Over_Edit(
        bool isBuildingPlayer, bool isPlaying, string expected)
    {
        Assert.Equal(
            expected,
            ConsoleLogPhaseClassifier.Classify(isBuildingPlayer, isPlaying));
    }
}

/// <summary>
/// Console capture request validation: ordering token, cursor, entry-count.
/// </summary>
public class ConsoleCaptureRequestValidatorTests
{
    private const int Capacity = 1000;

    [Theory]
    [InlineData(1)]
    [InlineData(Capacity)]
    public void Entry_Count_Accepts_Inclusive_Range(int maxEntries)
    {
        ConsoleCaptureValidation result = ConsoleCaptureRequestValidator.Validate(
            "newest_first", "", maxEntries, highestSeqId: 5, capacity: Capacity);

        Assert.Equal((true, string.Empty), (result.Success, result.ErrorCode));
    }

    [Theory]
    [InlineData(0)]
    [InlineData(Capacity + 1)]
    public void Entry_Count_Rejects_Outside_Inclusive_Range(int maxEntries)
    {
        ConsoleCaptureValidation result = ConsoleCaptureRequestValidator.Validate(
            "newest_first", "", maxEntries, highestSeqId: 5, capacity: Capacity);

        Assert.Equal(
            (false, ConsoleCaptureRequestValidator.MaxEntriesOutOfRangeCode),
            (result.Success, result.ErrorCode));
    }

    [Fact]
    public void Sequence_Selector_Disables_Request_Id_Selector()
    {
        Assert.Equal(
            (false, true),
            (
                ConsoleCaptureRequestValidator.UsesRequestIdSelector(7, "stale-request"),
                ConsoleCaptureRequestValidator.UsesRequestIdSelector(-1, "active-request")
            ));
    }

    [Fact]
    public void Cursor_Missing_The_Required_Prefix_Is_Rejected()
    {
        ConsoleCaptureValidation result = ConsoleCaptureRequestValidator.Validate(
            "newest_first", "12", maxEntries: 10, highestSeqId: 50, capacity: Capacity);

        Assert.False(result.Success);
        Assert.Equal(ConsoleCaptureRequestValidator.InvalidCursorCode, result.ErrorCode);
    }

    [Fact]
    public void Cursor_With_A_Non_Integer_Body_Is_Rejected()
    {
        ConsoleCaptureValidation result = ConsoleCaptureRequestValidator.Validate(
            "newest_first", "seq:abc", maxEntries: 10, highestSeqId: 50,
            capacity: Capacity);

        Assert.False(result.Success);
        Assert.Equal(ConsoleCaptureRequestValidator.InvalidCursorCode, result.ErrorCode);
    }

    [Fact]
    public void Cursor_Past_The_Highest_Sequence_Is_Rejected()
    {
        ConsoleCaptureValidation result = ConsoleCaptureRequestValidator.Validate(
            "newest_first", "seq:51", maxEntries: 10, highestSeqId: 50,
            capacity: Capacity);

        Assert.False(result.Success);
        Assert.Equal(ConsoleCaptureRequestValidator.InvalidCursorCode, result.ErrorCode);
    }

    [Fact]
    public void Well_Formed_In_Range_Cursor_Is_Accepted_With_Its_Sequence_As_The_Sentinel()
    {
        ConsoleCaptureValidation result = ConsoleCaptureRequestValidator.Validate(
            "newest_first", "seq:42", maxEntries: 10, highestSeqId: 50,
            capacity: Capacity);

        Assert.True(result.Success);
        // The post-validation cursor sentinel is the parsed sequence value.
        Assert.Equal(42L, result.CursorAfter);
    }

    [Fact]
    public void Empty_Cursor_Newest_First_Yields_The_Max_Value_Sentinel()
    {
        ConsoleCaptureValidation result = ConsoleCaptureRequestValidator.Validate(
            "newest_first", "", maxEntries: 10, highestSeqId: 50, capacity: Capacity);

        Assert.True(result.Success);
        Assert.True(result.NewestFirst);
        Assert.Equal(long.MaxValue, result.CursorAfter);
    }

    [Fact]
    public void Empty_Cursor_Oldest_First_Yields_The_Min_Value_Sentinel()
    {
        ConsoleCaptureValidation result = ConsoleCaptureRequestValidator.Validate(
            "oldest_first", "", maxEntries: 10, highestSeqId: 50, capacity: Capacity);

        Assert.True(result.Success);
        Assert.False(result.NewestFirst);
        Assert.Equal(long.MinValue, result.CursorAfter);
    }

    [Theory]
    [InlineData("newest_first")]
    [InlineData("oldest_first")]
    [InlineData("")]               // empty defaults to newest-first
    public void Recognized_Ordering_Tokens_Are_Accepted(string order)
    {
        ConsoleCaptureValidation result = ConsoleCaptureRequestValidator.Validate(
            order, "", maxEntries: 10, highestSeqId: 5, capacity: Capacity);

        Assert.True(result.Success);
    }

    [Fact]
    public void Unrecognized_Ordering_Token_Is_Rejected_With_The_Invalid_Order_Code()
    {
        ConsoleCaptureValidation result = ConsoleCaptureRequestValidator.Validate(
            "sideways", "", maxEntries: 10, highestSeqId: 5, capacity: Capacity);

        Assert.False(result.Success);
        Assert.Equal(ConsoleCaptureRequestValidator.InvalidOrderCode, result.ErrorCode);
    }

    [Fact]
    public void Since_Sequence_Below_Sentinel_Is_Rejected_With_The_Invalid_Sequence_Code()
    {
        ConsoleCaptureValidation result = ConsoleCaptureRequestValidator.Validate(
            "newest_first", "", maxEntries: 10, highestSeqId: 50,
            capacity: Capacity, sinceSequence: -2);

        Assert.False(result.Success);
        Assert.Equal(ConsoleCaptureRequestValidator.InvalidSinceSequenceCode, result.ErrorCode);
    }

    [Fact]
    public void Since_Sequence_Past_The_Highest_Sequence_Is_Rejected_With_The_Invalid_Sequence_Code()
    {
        ConsoleCaptureValidation result = ConsoleCaptureRequestValidator.Validate(
            "newest_first", "", maxEntries: 10, highestSeqId: 50,
            capacity: Capacity, sinceSequence: 51);

        Assert.False(result.Success);
        Assert.Equal(ConsoleCaptureRequestValidator.InvalidSinceSequenceCode, result.ErrorCode);
    }
}
