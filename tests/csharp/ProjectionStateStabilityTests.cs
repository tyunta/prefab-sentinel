using PrefabSentinel.Camera;
using Xunit;

namespace PrefabSentinel.Tests;

public class ProjectionStateStabilityTests
{
    [Theory]
    [InlineData(false, false, 60f, true)]
    [InlineData(true, true, 0f, true)]
    [InlineData(false, true, 60f, false)]
    [InlineData(true, false, 60f, false)]
    [InlineData(false, false, 0f, false)]
    [InlineData(false, false, float.NaN, false)]
    [InlineData(false, false, float.PositiveInfinity, false)]
    public void Position_Mode_Stability_Follows_Public_Projection_State(
        bool sceneViewOrthographic,
        bool cameraOrthographic,
        float cameraFieldOfView,
        bool expected)
    {
        bool actual = ProjectionStateStability.IsStableForPositionMode(
            sceneViewOrthographic,
            cameraOrthographic,
            cameraFieldOfView);

        Assert.Equal(expected, actual);
    }
}
