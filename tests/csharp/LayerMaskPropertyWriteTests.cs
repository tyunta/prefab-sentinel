using PrefabSentinel;
using Xunit;

namespace PrefabSentinel.Tests;

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
