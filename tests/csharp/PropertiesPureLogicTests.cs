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

        Assert.Equal((true, 0f, 0f, 0f, 1.00005f), (
            result.Success,
            result.X,
            result.Y,
            result.Z,
            result.W));
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

        Assert.Equal((false, (float[]?)null), (ok, value.Components));
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

public class EnumPropertyWriteTests
{
    [Fact]
    public void Enum_Inputs_Accept_Name_Display_Index_And_Backing_Value()
    {
        var definition = new EnumPropertyDefinition(
            new[] { "AlphaMode", "BetaMode" },
            new[] { "Alpha Mode", "Beta Mode" },
            new[] { 10, 20 });

        Assert.Equal(0, EnumPropertyValueParser.Parse(definition, "AlphaMode").Index);
        Assert.Equal(1, EnumPropertyValueParser.Parse(definition, "beta mode").Index);
        Assert.Equal(1, EnumPropertyValueParser.Parse(definition, "index:1").Index);
        Assert.Equal(1, EnumPropertyValueParser.Parse(definition, "value:20").Index);
    }

    [Fact]
    public void Enum_Failures_Are_Typed_With_Candidates()
    {
        var ambiguous = new EnumPropertyDefinition(
            new[] { "Foo", "foo" },
            new[] { "Foo", "foo" },
            new[] { 0, 1 });

        EnumPropertyParseResult ambiguousResult =
            EnumPropertyValueParser.Parse(ambiguous, "FOO");
        EnumPropertyParseResult outOfRange =
            EnumPropertyValueParser.Parse(ambiguous, "index:9");
        EnumPropertyParseResult missingValue =
            EnumPropertyValueParser.Parse(ambiguous, "value:42");
        EnumPropertyParseResult badToken =
            EnumPropertyValueParser.Parse(ambiguous, "not-a-token");

        Assert.Equal(
            (
                "EDITOR_CTRL_SET_PROP_ENUM_AMBIGUOUS",
                "EDITOR_CTRL_SET_PROP_ENUM_INDEX_OUT_OF_RANGE",
                "EDITOR_CTRL_SET_PROP_ENUM_VALUE_NOT_FOUND",
                "EDITOR_CTRL_SET_PROP_ENUM_PARSE_FAILED",
                "Foo"
            ),
            (
                ambiguousResult.ErrorCode,
                outOfRange.ErrorCode,
                missingValue.ErrorCode,
                badToken.ErrorCode,
                badToken.Names[0]
            ));
    }
}

public class LayerMaskPropertyWriteTests
{
    [Fact]
    public void LayerMask_Inputs_Accept_Raw_Symbolic_Single_And_Array_Forms()
    {
        int Resolver(string name) => name switch
        {
            "Default" => 0,
            "UI" => 5,
            _ => -1,
        };
        string[] candidates = { "Default", "UI" };

        Assert.Equal(3, LayerMaskValueParser.Parse("3", Resolver, candidates).Mask);
        Assert.Equal(16, LayerMaskValueParser.Parse("0x10", Resolver, candidates).Mask);
        Assert.Equal(0, LayerMaskValueParser.Parse("Nothing", Resolver, candidates).Mask);
        Assert.Equal(-1, LayerMaskValueParser.Parse("Everything", Resolver, candidates).Mask);
        Assert.Equal(32, LayerMaskValueParser.Parse("UI", Resolver, candidates).Mask);
        Assert.Equal(33, LayerMaskValueParser.Parse("[\"Default\",\"UI\"]", Resolver, candidates).Mask);
    }

    [Fact]
    public void LayerMask_Failures_Are_Typed_With_Candidates()
    {
        int Resolver(string name) => name == "UI" ? 5 : -1;
        string[] candidates = { "UI" };

        LayerMaskParseResult invalid =
            LayerMaskValueParser.Parse("[5]", Resolver, candidates);
        LayerMaskParseResult unknown =
            LayerMaskValueParser.Parse("Environment", Resolver, candidates);

        Assert.Equal(
            (
                "EDITOR_CTRL_SET_PROP_LAYERMASK_PARSE_FAILED",
                "EDITOR_CTRL_SET_PROP_LAYERMASK_UNKNOWN_LAYER",
                "UI"
            ),
            (
                invalid.ErrorCode,
                unknown.ErrorCode,
                unknown.Candidates[0]
            ));
    }
}
