using Xunit;

namespace PrefabSentinel.Tests;

public class ScreenshotCropBoundsTests
{
    [Fact]
    public void Normal_In_Range_Crop_Rectangle_Is_Accepted()
    {
        Assert.Equal(
            (true, 10, 20, 300, 200, 640, 480),
            (
                ScreenshotCropBounds.FitsWithinFrame(
                    10, 20, 300, 200, 640, 480),
                10, 20, 300, 200, 640, 480));
    }

    [Fact]
    public void Overflow_Prone_Crop_Rectangle_Is_Rejected()
    {
        Assert.Equal(
            (false, int.MaxValue, 0, int.MaxValue, 1, 4096, 4096),
            (
                ScreenshotCropBounds.FitsWithinFrame(
                    int.MaxValue, 0, int.MaxValue, 1, 4096, 4096),
                int.MaxValue, 0, int.MaxValue, 1, 4096, 4096));
    }

    [Fact]
    public void Exact_Edge_Is_Accepted_And_One_Pixel_Beyond_Is_Rejected()
    {
        Assert.Equal(
            (true, false),
            (
                ScreenshotCropBounds.FitsWithinFrame(
                    4095, 0, 1, 1, 4096, 4096),
                ScreenshotCropBounds.FitsWithinFrame(
                    4096, 0, 1, 1, 4096, 4096)));
    }

    [Theory]
    [InlineData(-1, 0, 1, 1, 4096, 4096)]
    [InlineData(0, 0, 0, 1, 4096, 4096)]
    [InlineData(0, 0, 1, 1, 0, 4096)]
    public void Invalid_Crop_Geometry_Is_Rejected(
        int x,
        int y,
        int width,
        int height,
        int frameWidth,
        int frameHeight)
    {
        Assert.Equal(
            (false, x, y, width, height, frameWidth, frameHeight),
            (
                ScreenshotCropBounds.FitsWithinFrame(
                    x, y, width, height, frameWidth, frameHeight),
                x, y, width, height, frameWidth, frameHeight));
    }
}
