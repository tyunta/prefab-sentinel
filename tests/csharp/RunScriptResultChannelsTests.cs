using Xunit;

namespace PrefabSentinel.Tests;

public class RunScriptResultChannelsTests
{
    [Fact]
    public void Primitive_Outputs_Are_Captured_As_Json_Safe_Values()
    {
        Output.BeginCapture();

        Output.Add("name", "WatchingButton");
        Output.Add("count", 3);
        Output.Add("visible", true);
        Output.Add("labels", new[] { "left", "right" });
        RunScriptOutputSnapshot snapshot = Output.EndCapture();

        Assert.Equal(4, snapshot.Outputs.Length);
        Assert.Equal(("name", "string", "WatchingButton"), (
            snapshot.Outputs[0].Key,
            snapshot.Outputs[0].Value.Kind,
            snapshot.Outputs[0].Value.StringValue));
        Assert.Equal(("count", "number", 3d), (
            snapshot.Outputs[1].Key,
            snapshot.Outputs[1].Value.Kind,
            snapshot.Outputs[1].Value.NumberValue));
        Assert.Equal(("visible", "bool", true), (
            snapshot.Outputs[2].Key,
            snapshot.Outputs[2].Value.Kind,
            snapshot.Outputs[2].Value.BoolValue));
        Assert.Equal(("labels", "string_array"), (
            snapshot.Outputs[3].Key,
            snapshot.Outputs[3].Value.Kind));
        Assert.Equal(new[] { "left", "right" }, snapshot.Outputs[3].Value.StringArray);
    }

    [Fact]
    public void Unsupported_Output_Value_Reports_Key_And_Keeps_Valid_Outputs()
    {
        Output.BeginCapture();

        Output.Add("valid", 1);
        Output.Add("bad", new object());
        RunScriptOutputSnapshot snapshot = Output.EndCapture();

        Assert.Equal(("bad", true), (snapshot.UnsupportedKey, snapshot.HasUnsupportedOutput));
        Assert.Single(snapshot.Outputs);
        Assert.Equal(("valid", "number", 1d), (
            snapshot.Outputs[0].Key,
            snapshot.Outputs[0].Value.Kind,
            snapshot.Outputs[0].Value.NumberValue));
    }

    [Fact]
    public void Blank_Output_Key_Does_Not_Clear_Unsupported_Output()
    {
        Output.BeginCapture();

        Output.Add("bad", new object());
        Output.Add("", 1);
        Output.Add("   ", 2);
        RunScriptOutputSnapshot snapshot = Output.EndCapture();

        Assert.Equal(("bad", true), (snapshot.UnsupportedKey, snapshot.HasUnsupportedOutput));
        Assert.Empty(snapshot.Outputs);
    }

    [Fact]
    public void Return_Value_Allows_Primitive_And_Null_Partitions()
    {
        Assert.Equal(("null", true), (
            RunScriptValue.FromReturnValue(null!).Kind,
            RunScriptValue.FromReturnValue(null!).IsNull));
        Assert.Equal(("string", "ok"), (
            RunScriptValue.FromReturnValue("ok").Kind,
            RunScriptValue.FromReturnValue("ok").StringValue));
        Assert.Equal(("number", 4d), (
            RunScriptValue.FromReturnValue(4).Kind,
            RunScriptValue.FromReturnValue(4).NumberValue));
        Assert.Equal(("bool", true), (
            RunScriptValue.FromReturnValue(true).Kind,
            RunScriptValue.FromReturnValue(true).BoolValue));
    }
}
