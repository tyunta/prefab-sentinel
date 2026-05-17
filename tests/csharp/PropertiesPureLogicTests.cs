using System;
using PrefabSentinel;
using Xunit;

namespace PrefabSentinel.Tests;

/// <summary>
/// Issue #12 (H-4) — exercises the fuzzy suggestion ranker, edit-distance
/// computation, quaternion input validation, GameObject property allow-list,
/// and property-value parser extracted from the property-write handlers.
/// </summary>
public class SuggestionRankerTests
{
    [Fact]
    public void Suggestions_Are_Threshold_Filtered_And_Sorted_By_Ascending_Distance()
    {
        // "speedy" is edit-distance 1, "spede" is 2, "speedster" is 4 — past
        // the 0.4 distance-ratio threshold for a 5-character query.
        string[] result = SuggestionRanker.SuggestSimilar(
            "speed", new[] { "speedster", "spede", "speedy" });

        Assert.Equal(new[] { "speedy", "spede" }, result);
    }

    [Fact]
    public void Suggestion_Count_Is_Capped_At_The_Default_Result_Cap()
    {
        // Five within-threshold candidates; the default cap is 3.
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

/// <summary>Quaternion input validation: arity and unit-norm.</summary>
public class QuaternionInputValidatorTests
{
    [Fact]
    public void A_Unit_Quaternion_Is_Accepted_With_Its_Parsed_Components()
    {
        QuaternionParse result = QuaternionInputValidator.Validate("0,0,0,1");

        Assert.True(result.Success);
        Assert.Equal(0f, result.X);
        Assert.Equal(0f, result.Y);
        Assert.Equal(0f, result.Z);
        Assert.Equal(1f, result.W);
    }

    [Fact]
    public void A_Three_Component_Input_Is_Rejected_With_The_Type_Mismatch_Code()
    {
        QuaternionParse result = QuaternionInputValidator.Validate("0,0,1");

        Assert.False(result.Success);
        Assert.Equal(QuaternionInputValidator.TypeMismatchCode, result.ErrorCode);
    }

    [Fact]
    public void A_Norm_Just_Outside_Tolerance_Is_Rejected_As_Not_Normalized()
    {
        // norm = 1.001, |norm - 1| = 1e-3 > the 1e-4 tolerance.
        QuaternionParse result = QuaternionInputValidator.Validate("0,0,0,1.001");

        Assert.False(result.Success);
        Assert.Equal(QuaternionInputValidator.NotNormalizedCode, result.ErrorCode);
    }

    [Fact]
    public void A_Norm_Just_Inside_Tolerance_Is_Accepted()
    {
        // |1.00005 - 1| = 5e-5 < the 1e-4 tolerance.
        QuaternionParse result = QuaternionInputValidator.Validate("0,0,0,1.00005");

        Assert.True(result.Success);
    }

    [Fact]
    public void A_Non_Numeric_Component_Surfaces_A_Format_Exception()
    {
        FormatException ex = Assert.Throws<FormatException>(
            () => QuaternionInputValidator.Validate("0,0,0,abc"));

        Assert.Contains("abc", ex.Message);
    }
}

/// <summary>GameObject property allow-list membership.</summary>
public class GameObjectPropertyAllowlistTests
{
    [Theory]
    [InlineData("m_IsActive", true)]
    [InlineData("m_Layer", true)]
    [InlineData("m_Name", true)]
    [InlineData("m_TagString", true)]
    [InlineData("m_LocalPosition", false)]
    [InlineData("", false)]
    public void Only_Allow_Listed_Property_Names_Are_Permitted(
        string propertyName, bool expected)
    {
        Assert.Equal(expected, GameObjectPropertyAllowlist.IsAllowed(propertyName));
    }
}

/// <summary>Property-value parsing: color alpha default and vector arity.</summary>
public class PropertyValueParserTests
{
    [Fact]
    public void A_Three_Component_Color_Defaults_Alpha_To_Fully_Opaque()
    {
        bool ok = PropertyValueParser.TryParse(
            SerializedPropertyKind.Color, "0.1,0.2,0.3", out ParsedPropertyValue value);

        Assert.True(ok);
        Assert.Equal(1f, value.Components[3]);
    }

    [Fact]
    public void An_Unparseable_Fourth_Color_Component_Falls_Back_To_Opaque_Alpha()
    {
        bool ok = PropertyValueParser.TryParse(
            SerializedPropertyKind.Color, "0.1,0.2,0.3,nope",
            out ParsedPropertyValue value);

        Assert.True(ok);
        Assert.Equal(1f, value.Components[3]);
    }

    [Fact]
    public void An_Explicit_Fourth_Color_Component_Overrides_The_Alpha_Default()
    {
        bool ok = PropertyValueParser.TryParse(
            SerializedPropertyKind.Color, "0.1,0.2,0.3,0.5",
            out ParsedPropertyValue value);

        Assert.True(ok);
        Assert.Equal(0.5f, value.Components[3]);
    }

    [Fact]
    public void An_Under_Length_Vector_Is_Rejected()
    {
        bool ok = PropertyValueParser.TryParse(
            SerializedPropertyKind.Vector3, "1,2", out ParsedPropertyValue value);

        Assert.False(ok);
    }

    [Fact]
    public void A_Full_Length_Vector_Is_Parsed_Component_Wise()
    {
        bool ok = PropertyValueParser.TryParse(
            SerializedPropertyKind.Vector3, "1,2,3", out ParsedPropertyValue value);

        Assert.True(ok);
        Assert.Equal(new[] { 1f, 2f, 3f }, value.Components);
    }
}
