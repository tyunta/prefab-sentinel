using PrefabSentinel;
using Xunit;

namespace PrefabSentinel.Tests;

public class PropertyValueParserTests
{
    [Fact]
    public void String_Null_Input_Returns_Failure_With_Default_Value()
    {
        string? raw = null;
        bool ok = PropertyValueParser.TryParse(
            SerializedPropertyKind.String, raw!, out ParsedPropertyValue value);

        Assert.Equal((false, (string?)null), (ok, value.StringValue));
    }

    [Fact]
    public void Explicit_Empty_String_Remains_A_Valid_String_Value()
    {
        bool ok = PropertyValueParser.TryParse(
            SerializedPropertyKind.String, string.Empty, out ParsedPropertyValue value);

        Assert.Equal(
            (true, SerializedPropertyKind.String, string.Empty),
            (ok, value.Kind, value.StringValue));
    }

    [Theory]
    [InlineData((int)SerializedPropertyKind.Integer)]
    [InlineData((int)SerializedPropertyKind.Vector3)]
    [InlineData((int)SerializedPropertyKind.Color)]
    public void Explicit_Empty_String_Remains_A_Failure_For_Non_String_Kinds(
        int kindValue)
    {
        var kind = (SerializedPropertyKind)kindValue;
        bool ok = PropertyValueParser.TryParse(
            kind, string.Empty, out ParsedPropertyValue value);

        Assert.Equal((false, (string?)null, (float[]?)null), (
            ok,
            value.StringValue,
            value.Components));
    }

    [Fact]
    public void Vector_Null_Input_Returns_Failure_With_Default_Components()
    {
        string? raw = null;
        bool ok = PropertyValueParser.TryParse(
            SerializedPropertyKind.Vector3, raw!, out ParsedPropertyValue value);

        Assert.Equal((false, (float[]?)null), (ok, value.Components));
    }

    [Fact]
    public void Color_Null_Input_Returns_Failure_With_Default_Components()
    {
        string? raw = null;
        bool ok = PropertyValueParser.TryParse(
            SerializedPropertyKind.Color, raw!, out ParsedPropertyValue value);

        Assert.Equal((false, (float[]?)null), (ok, value.Components));
    }

    [Fact]
    public void Vector_Helper_Null_Input_Returns_Failure_With_Default_Components()
    {
        object?[] args =
        {
            SerializedPropertyKind.Vector3,
            null,
            3,
            default(ParsedPropertyValue),
        };

        bool ok = InvokePrivateTryParse("TryParseVector", args);
        var value = (ParsedPropertyValue)args[3]!;

        Assert.Equal((false, (float[]?)null), (ok, value.Components));
    }

    [Fact]
    public void Color_Helper_Null_Input_Returns_Failure_With_Default_Components()
    {
        object?[] args = { null, default(ParsedPropertyValue) };

        bool ok = InvokePrivateTryParse("TryParseColor", args);
        var value = (ParsedPropertyValue)args[1]!;

        Assert.Equal((false, (float[]?)null), (ok, value.Components));
    }

    [Fact]
    public void A_Three_Component_Color_Defaults_Alpha_To_Fully_Opaque()
    {
        bool ok = PropertyValueParser.TryParse(
            SerializedPropertyKind.Color, "0.1,0.2,0.3", out ParsedPropertyValue value);

        Assert.Equal((true, SerializedPropertyKind.Color), (ok, value.Kind));
        Assert.Equal(new[] { 0.1f, 0.2f, 0.3f, 1f }, value.Components);
    }

    [Fact]
    public void An_Unparseable_Fourth_Color_Component_Falls_Back_To_Opaque_Alpha()
    {
        bool ok = PropertyValueParser.TryParse(
            SerializedPropertyKind.Color, "0.1,0.2,0.3,nope",
            out ParsedPropertyValue value);

        Assert.Equal((true, SerializedPropertyKind.Color), (ok, value.Kind));
        Assert.Equal(new[] { 0.1f, 0.2f, 0.3f, 1f }, value.Components);
    }

    [Fact]
    public void An_Explicit_Fourth_Color_Component_Overrides_The_Alpha_Default()
    {
        bool ok = PropertyValueParser.TryParse(
            SerializedPropertyKind.Color, "0.1,0.2,0.3,0.5",
            out ParsedPropertyValue value);

        Assert.Equal((true, SerializedPropertyKind.Color), (ok, value.Kind));
        Assert.Equal(new[] { 0.1f, 0.2f, 0.3f, 0.5f }, value.Components);
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

        Assert.Equal((true, SerializedPropertyKind.Vector3), (ok, value.Kind));
        Assert.Equal(new[] { 1f, 2f, 3f }, value.Components);
    }

    [Fact]

    public void Missing_Private_TryParse_Helper_Names_Reflection_Contract()
    {
        Exception failure = Record.Exception(() =>
            InvokePrivateTryParse(
                "TryParseMissingForDiagnostics",
                new object?[] { "1,2,3", default(ParsedPropertyValue) }));
        Assert.IsAssignableFrom<Xunit.Sdk.XunitException>(failure);
        string message = failure.Message;

        Assert.Contains("TryParseMissingForDiagnostics", message);
        Assert.Contains("PropertyValueParser", message);
        Assert.Contains("NonPublic", message);
        Assert.Contains("Static", message);
        Assert.Contains("bool", message);
        Assert.Contains("private TryParse", message);
    }

    private static bool InvokePrivateTryParse(string methodName, object?[] args)
    {
        var bindingFlags = System.Reflection.BindingFlags.NonPublic
            | System.Reflection.BindingFlags.Static;
        var method = typeof(PropertyValueParser).GetMethod(methodName, bindingFlags);
        Assert.True(
            method is not null,
            "Missing private TryParse helper '" + methodName
            + "' on PropertyValueParser. Expected NonPublic | Static "
            + "BindingFlags, bool return, and the private TryParse "
            + "reflection helper contract.");
        return (bool)method!.Invoke(null, args)!;
    }
}
