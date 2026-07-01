using PrefabSentinel;
using Xunit;

namespace PrefabSentinel.Tests;

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
