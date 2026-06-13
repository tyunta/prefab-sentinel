using System;
using PrefabSentinel;
using Xunit;

namespace PrefabSentinel.Tests;

public class SuggestionRankerTests
{
    [Fact]
    public void Suggestions_Are_Threshold_Filtered_And_Sorted_By_Ascending_Distance()
    {
        string[] result = SuggestionRanker.SuggestSimilar(
            "speed", new[] { "speedster", "spede", "speedy" });

        Assert.Equal(new[] { "speedy", "spede" }, result);
    }

    [Fact]
    public void Suggestion_Count_Is_Capped_At_The_Default_Result_Cap()
    {
        string[] result = SuggestionRanker.SuggestSimilar(
            "speed", new[] { "speedy", "speeds", "speet", "speec", "sped" });

        Assert.Equal(3, result.Length);
    }

    [Fact]
    public void Empty_Word_Yields_No_Suggestions()
    {
        Assert.Empty(SuggestionRanker.SuggestSimilar("", new[] { "speed", "speedy" }));
    }

    [Fact]
    public void Empty_Candidate_List_Yields_No_Suggestions()
    {
        Assert.Empty(SuggestionRanker.SuggestSimilar("speed", Array.Empty<string>()));
    }

    [Fact]
    public void Edit_Distance_Of_A_Single_Character_Difference_Is_One()
    {
        Assert.Equal(1, SuggestionRanker.LevenshteinDistance("cat", "cot"));
    }

    [Theory]
    [InlineData("", "hello", 5)]
    [InlineData("world", "", 5)]
    public void Edit_Distance_With_An_Empty_Operand_Is_The_Other_Operand_Length(
        string a, string b, int expected)
    {
        Assert.Equal(expected, SuggestionRanker.LevenshteinDistance(a, b));
    }
}
