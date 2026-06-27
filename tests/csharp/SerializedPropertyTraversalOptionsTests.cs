using PrefabSentinel;
using Xunit;

namespace PrefabSentinel.Tests;

public class SerializedPropertyTraversalOptionsTests
{
    [Fact]
    public void Default_Depth_And_Cap_Are_Accepted_With_No_Cursor()
    {
        SerializedPropertyTraversalOptions result =
            SerializedPropertyTraversalOptions.Parse(depth: 1, cap: 50, cursor: string.Empty);

        Assert.Equal(
            (true, 1, 50, 0),
            (result.Success, result.Depth, result.Cap, result.Cursor));
    }

    [Fact]
    public void Negative_Depth_Is_Rejected_With_Limit_Code()
    {
        SerializedPropertyTraversalOptions result =
            SerializedPropertyTraversalOptions.Parse(depth: -1, cap: 50, cursor: string.Empty);

        Assert.Equal(
            (false, SerializedPropertyTraversalOptions.LimitInvalidCode),
            (result.Success, result.ErrorCode));
    }

    [Theory]
    [InlineData(0)]
    [InlineData(201)]
    public void Cap_Outside_The_Accepted_Range_Is_Rejected_With_Limit_Code(int cap)
    {
        SerializedPropertyTraversalOptions result =
            SerializedPropertyTraversalOptions.Parse(depth: 1, cap: cap, cursor: string.Empty);

        Assert.Equal(
            (false, SerializedPropertyTraversalOptions.LimitInvalidCode),
            (result.Success, result.ErrorCode));
    }

    [Fact]
    public void The_Hard_Cap_Is_Accepted()
    {
        SerializedPropertyTraversalOptions result =
            SerializedPropertyTraversalOptions.Parse(
                depth: 1,
                cap: SerializedPropertyTraversalOptions.HardCap,
                cursor: string.Empty);

        Assert.Equal(
            (true, SerializedPropertyTraversalOptions.HardCap),
            (result.Success, result.Cap));
    }

    [Fact]
    public void Numeric_Cursor_Is_Accepted_As_The_Next_Start_Index()
    {
        SerializedPropertyTraversalOptions result =
            SerializedPropertyTraversalOptions.Parse(depth: 1, cap: 50, cursor: "42");

        Assert.Equal((true, 42), (result.Success, result.Cursor));
    }

    [Fact]
    public void Malformed_Cursor_Is_Rejected_With_Cursor_Code()
    {
        SerializedPropertyTraversalOptions result =
            SerializedPropertyTraversalOptions.Parse(depth: 1, cap: 50, cursor: "next");

        Assert.Equal(
            (false, SerializedPropertyTraversalOptions.CursorInvalidCode),
            (result.Success, result.ErrorCode));
    }
}
