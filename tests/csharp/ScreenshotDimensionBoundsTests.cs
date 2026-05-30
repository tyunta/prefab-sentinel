using Xunit;

namespace PrefabSentinel.Tests;

public class ScreenshotDimensionBoundsTests
{
    [Theory]
    [InlineData(0, 0)]
    [InlineData(1, 1)]
    [InlineData(0, ScreenshotDimensionBounds.MaxDimension)]
    [InlineData(ScreenshotDimensionBounds.MaxDimension, 0)]
    [InlineData(ScreenshotDimensionBounds.MaxDimension, ScreenshotDimensionBounds.MaxDimension)]
    public void Dimensions_Accept_Default_And_Maximum_Boundary(int width, int height)
    {
        Assert.Equal(
            (true, 0, 4096),
            (
                ScreenshotDimensionBounds.Accepts(width, height),
                ScreenshotDimensionBounds.MinDimension,
                ScreenshotDimensionBounds.MaxDimension));
    }

    [Theory]
    [InlineData(-1, 0)]
    [InlineData(0, -1)]
    [InlineData(ScreenshotDimensionBounds.MaxDimension + 1, 1)]
    [InlineData(1, ScreenshotDimensionBounds.MaxDimension + 1)]
    public void Dimensions_Reject_Negative_And_Oversized_Values(int width, int height)
    {
        Assert.Equal(
            (false, "EDITOR_CTRL_SCREENSHOT_DIMENSIONS_OUT_OF_RANGE"),
            (
                ScreenshotDimensionBounds.Accepts(width, height),
                ScreenshotDimensionBounds.BridgeOutOfRangeCode));
        Assert.Contains($"width={width}", ScreenshotDimensionBounds.BuildMessage(width, height));
        Assert.Contains($"height={height}", ScreenshotDimensionBounds.BuildMessage(width, height));
        Assert.Contains("[1, 4096]", ScreenshotDimensionBounds.BuildMessage(width, height));
    }
}
