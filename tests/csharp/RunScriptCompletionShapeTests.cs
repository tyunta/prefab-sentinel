using System.Text;
using Xunit;

namespace PrefabSentinel.Tests;

public sealed class RunScriptCompletionShapeTests
{
    [Theory]
    [InlineData("{\"success\":true,\"data\":{\"executed\":true}}")]
    [InlineData("{\"success\":false,\"data\":{\"executed\":true}}")]
    [InlineData("{\"data\":{}}")]
    [InlineData("{\"d\\u0061ta\":{}}")]
    public void Exactly_One_Top_Level_Object_Data_Is_Accepted(string json)
    {
        Assert.True(RunScriptCompletionShape.HasExactlyOneObjectData(json));
    }

    [Theory]
    [InlineData("{}")]
    [InlineData("{\"data\":null}")]
    [InlineData("{\"data\":[]}")]
    [InlineData("{\"data\":\"object\"}")]
    [InlineData("{\"data\":42}")]
    [InlineData("{\"data\":true}")]
    [InlineData("{\"data\":{},\"data\":{}}")]
    [InlineData("{\"data\":{},\"d\\u0061ta\":{}}")]
    [InlineData("{\"outer\":{\"data\":{}}}")]
    [InlineData("{\"message\":\"\\\"data\\\":{}\"}")]
    [InlineData("null")]
    [InlineData("[]")]
    [InlineData("\"root scalar\"")]
    [InlineData("42")]
    [InlineData("true")]
    [InlineData("")]
    [InlineData("{")]
    [InlineData("{\"data\":")]
    [InlineData("{\"data\":{}} trailing")]
    [InlineData("{\"data\":{}} {}")]
    public void Missing_Wrong_Type_Decoy_Or_Invalid_Json_Is_Rejected(string json)
    {
        Assert.False(RunScriptCompletionShape.HasExactlyOneObjectData(json));
    }

    [Fact]
    public void Recursive_Depth_256_Is_Accepted()
    {
        Assert.True(RunScriptCompletionShape.HasExactlyOneObjectData(
            NestedDataObject(childDepth: 257)));
    }

    [Fact]
    public void Recursive_Depth_Above_256_Is_Rejected()
    {
        Assert.False(RunScriptCompletionShape.HasExactlyOneObjectData(
            NestedDataObject(childDepth: 258)));
    }

    [Fact]
    public void Long_String_Does_Not_Inherit_The_Default_8KiB_Quota()
    {
        string json = "{\"data\":{\"stdout\":\""
            + new string('x', 16_384)
            + "\"}}";

        Assert.True(RunScriptCompletionShape.HasExactlyOneObjectData(json));
    }

    private static string NestedDataObject(int childDepth)
    {
        var json = new StringBuilder("{\"data\":");
        for (int index = 0; index < childDepth; index++)
            json.Append("{\"child\":");
        json.Append("{}");
        for (int index = 0; index < childDepth; index++)
            json.Append('}');
        return json.Append('}').ToString();
    }
}
