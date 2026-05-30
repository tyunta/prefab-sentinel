using Xunit;

namespace PrefabSentinel.Tests;

public class GeometryDistanceMathTests
{
    [Fact]
    public void Pivot_Mode_Returns_Pivot_To_Pivot_Distance()
    {
        GeometryDistanceResult result = GeometryBoundsMath.MeasureDistance(
            new[] { 0d, 0d, 0d },
            new[] { 5d, 5d, 5d },
            new[] { 0d, 3d, 4d },
            new[] { 5d, 5d, 5d },
            "pivot");

        Assert.Equal((true, 5d), (result.Success, result.Distance));
    }

    [Fact]
    public void Center_Mode_Returns_Center_To_Center_Distance()
    {
        GeometryDistanceResult result = GeometryBoundsMath.MeasureDistance(
            new[] { 0d, 0d, 0d },
            new[] { 1d, 1d, 1d },
            new[] { 3d, 4d, 0d },
            new[] { 1d, 1d, 1d },
            "center");

        Assert.Equal((true, 5d), (result.Success, result.Distance));
    }

    [Fact]
    public void Surface_Mode_Returns_Closest_Aabb_Distance()
    {
        GeometryDistanceResult result = GeometryBoundsMath.MeasureDistance(
            new[] { 0d, 0d, 0d },
            new[] { 1d, 1d, 1d },
            new[] { 4d, 0d, 0d },
            new[] { 1d, 1d, 1d },
            "surface");

        Assert.Equal((true, 2d), (result.Success, result.Distance));
    }

    [Fact]
    public void Surface_Mode_Returns_Zero_For_Overlapping_Aabbs()
    {
        GeometryDistanceResult result = GeometryBoundsMath.MeasureDistance(
            new[] { 0d, 0d, 0d },
            new[] { 2d, 2d, 2d },
            new[] { 1d, 1d, 1d },
            new[] { 2d, 2d, 2d },
            "surface");

        Assert.Equal((true, 0d), (result.Success, result.Distance));
    }

    [Fact]
    public void Invalid_Mode_Returns_Typed_Distance_Mode_Error()
    {
        GeometryDistanceResult result = GeometryBoundsMath.MeasureDistance(
            new[] { 0d, 0d, 0d },
            new[] { 1d, 1d, 1d },
            new[] { 4d, 0d, 0d },
            new[] { 1d, 1d, 1d },
            "diagonal");

        Assert.Equal((false, "EDITOR_CTRL_DISTANCE_MODE_INVALID"), (result.Success, result.ErrorCode));
    }
}
