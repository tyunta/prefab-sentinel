using System;
using PrefabSentinel;
using Xunit;

namespace PrefabSentinel.Tests;

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
    public void Null_Input_Is_Rejected_With_The_Null_Input_Code()
    {
        string? raw = null;
        QuaternionParse result = QuaternionInputValidator.Validate(raw!);

        Assert.Equal(
            (false, "EDITOR_CTRL_SET_PROP_NULL_INPUT"),
            (result.Success, result.ErrorCode));
    }

    [Fact]
    public void Explicit_Empty_Input_Is_Rejected_With_The_Type_Mismatch_Code()
    {
        QuaternionParse result = QuaternionInputValidator.Validate(string.Empty);

        Assert.Equal(
            (false, QuaternionInputValidator.TypeMismatchCode),
            (result.Success, result.ErrorCode));
    }

    [Fact]
    public void A_Norm_Just_Outside_Tolerance_Is_Rejected_As_Not_Normalized()
    {
        QuaternionParse result = QuaternionInputValidator.Validate("0,0,0,1.001");

        Assert.False(result.Success);
        Assert.Equal(QuaternionInputValidator.NotNormalizedCode, result.ErrorCode);
    }

    [Fact]
    public void A_Norm_Just_Inside_Tolerance_Is_Accepted()
    {
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
