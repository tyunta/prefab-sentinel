using PrefabSentinel;
using Xunit;

namespace PrefabSentinel.Tests;

public class JsonArrayScalarParserTests
{
    [Fact]
    public void Valid_Array_Values_Preserve_Json_Types_And_Decode_Escapes()
    {
        bool ok = JsonArrayScalarParser.TryParse(
            "[\"line\\n\\u0041\",-12,3.5e2,true,false,null]",
            out List<JsonArrayScalar> elements);

        Assert.Equal((
            true,
            6,
            (JsonArrayScalarKind.String, "line\nA"),
            (JsonArrayScalarKind.Number, "-12"),
            (JsonArrayScalarKind.Number, "3.5e2"),
            (JsonArrayScalarKind.Boolean, "true"),
            (JsonArrayScalarKind.Boolean, "false"),
            true), (
            ok,
            elements.Count,
            (elements[0].Kind, elements[0].Value),
            (elements[1].Kind, elements[1].Value),
            (elements[2].Kind, elements[2].Value),
            (elements[3].Kind, elements[3].Value),
            (elements[4].Kind, elements[4].Value),
            elements[5].IsNull));
    }

    [Theory]
    [InlineData("")]
    [InlineData("[] trailing")]
    [InlineData("[alpha]")]
    [InlineData("[\"unterminated]")]
    [InlineData("[\"bad\\q\"]")]
    [InlineData("[01]")]
    [InlineData("[1,]")]
    public void Malformed_Or_Non_Json_Tokens_Are_Rejected(string json)
    {
        bool ok = JsonArrayScalarParser.TryParse(json, out List<JsonArrayScalar> elements);

        Assert.Equal((false, 0), (ok, elements.Count));
    }
}
