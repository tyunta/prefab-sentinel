using Xunit;

namespace PrefabSentinel.Tests;

public class GeometryBoundsMathTests
{
    [Theory]
    [InlineData("renderer")]
    [InlineData("collider")]
    [InlineData("rect_transform")]

    public void Source_Partitions_Select_Only_The_Requested_Contributor_Type(string source)
    {
        GeometryBoundsResult result = GeometryBoundsMath.Aggregate(
            new[]
            {
                GeometryBoundsContributor.Target(source, new[] { 0d, 0d, 0d }, new[] { 1d, 1d, 1d }),
                GeometryBoundsContributor.Child(source, new[] { 4d, 0d, 0d }, new[] { 1d, 1d, 1d }),
                GeometryBoundsContributor.Target("other_renderer", new[] { 20d, 0d, 0d }, new[] { 1d, 1d, 1d }),
                GeometryBoundsContributor.Target("other_collider", new[] { 30d, 0d, 0d }, new[] { 1d, 1d, 1d }),
                GeometryBoundsContributor.Target("other_rect_transform", new[] { 40d, 0d, 0d }, new[] { 1d, 1d, 1d }),
            },
            source,
            includeChildren: false);

        Assert.Equal((true, source), (result.Success, result.Source));
        Assert.Equal(new[] { 0d, 0d, 0d }, result.Center);
        Assert.Equal(new[] { 1d, 1d, 1d }, result.Extents);
    }

    [Fact]
    public void Combined_Source_Aggregates_All_Supported_Contributor_Types()
    {
        GeometryBoundsResult result = GeometryBoundsMath.Aggregate(
            new[]
            {
                GeometryBoundsContributor.Target("renderer", new[] { 0d, 0d, 0d }, new[] { 1d, 1d, 1d }),
                GeometryBoundsContributor.Target("collider", new[] { 4d, 0d, 0d }, new[] { 1d, 1d, 1d }),
                GeometryBoundsContributor.Target("rect_transform", new[] { 8d, 0d, 0d }, new[] { 1d, 1d, 1d }),
            },
            "combined",
            includeChildren: true);

        Assert.Equal((true, "combined"), (result.Success, result.Source));
        Assert.Equal(new[] { 4d, 0d, 0d }, result.Center);
        Assert.Equal(new[] { 5d, 1d, 1d }, result.Extents);
    }

    [Fact]
    public void Target_Only_Source_Excludes_Child_Contributors()
    {
        GeometryBoundsResult result = GeometryBoundsMath.Aggregate(
            new[]
            {
                GeometryBoundsContributor.Target("renderer", new[] { 0d, 0d, 0d }, new[] { 1d, 1d, 1d }),
                GeometryBoundsContributor.Child("renderer", new[] { 10d, 0d, 0d }, new[] { 1d, 1d, 1d }),
            },
            "renderer",
            includeChildren: false);

        Assert.Equal((true, "renderer"), (result.Success, result.Source));
        Assert.Equal(new[] { 0d, 0d, 0d }, result.Center);
        Assert.Equal(new[] { 1d, 1d, 1d }, result.Extents);
    }

    [Fact]
    public void Child_Including_Source_Aggregates_Target_And_Child_Contributors()
    {
        GeometryBoundsResult result = GeometryBoundsMath.Aggregate(
            new[]
            {
                GeometryBoundsContributor.Target("renderer", new[] { 0d, 0d, 0d }, new[] { 1d, 1d, 1d }),
                GeometryBoundsContributor.Child("renderer", new[] { 4d, 0d, 0d }, new[] { 1d, 1d, 1d }),
            },
            "renderer",
            includeChildren: true);

        Assert.Equal((true, "renderer"), (result.Success, result.Source));
        Assert.Equal(new[] { 2d, 0d, 0d }, result.Center);
        Assert.Equal(new[] { 3d, 1d, 1d }, result.Extents);
    }

    [Fact]
    public void Auto_Source_Uses_Renderer_Collider_RectTransform_Priority()
    {
        GeometryBoundsResult result = GeometryBoundsMath.Aggregate(
            new[]
            {
                GeometryBoundsContributor.Target("renderer", new[] { 0d, 0d, 0d }, new[] { 1d, 1d, 1d }),
                GeometryBoundsContributor.Target("collider", new[] { 4d, 0d, 0d }, new[] { 1d, 1d, 1d }),
                GeometryBoundsContributor.Target("rect_transform", new[] { 8d, 0d, 0d }, new[] { 1d, 1d, 1d }),
            },
            "auto",
            includeChildren: true);

        Assert.Equal((true, "renderer"), (result.Success, result.Source));
        Assert.Equal(new[] { 0d, 0d, 0d }, result.Center);
        Assert.Equal(new[] { 1d, 1d, 1d }, result.Extents);
    }

    [Fact]
    public void Auto_Source_Uses_Collider_When_Renderer_Is_Unavailable()
    {
        GeometryBoundsResult result = GeometryBoundsMath.Aggregate(
            new[]
            {
                GeometryBoundsContributor.Target("collider", new[] { 4d, 0d, 0d }, new[] { 1d, 1d, 1d }),
                GeometryBoundsContributor.Target("rect_transform", new[] { 8d, 0d, 0d }, new[] { 1d, 1d, 1d }),
            },
            "auto",
            includeChildren: true);

        Assert.Equal((true, "collider"), (result.Success, result.Source));
        Assert.Equal(new[] { 4d, 0d, 0d }, result.Center);
        Assert.Equal(new[] { 1d, 1d, 1d }, result.Extents);
    }

    [Fact]
    public void Auto_Source_Uses_RectTransform_When_No_Renderer_Or_Collider_Is_Available()
    {
        GeometryBoundsResult result = GeometryBoundsMath.Aggregate(
            new[]
            {
                GeometryBoundsContributor.Target("rect_transform", new[] { 8d, 0d, 0d }, new[] { 1d, 1d, 1d }),
            },
            "auto",
            includeChildren: true);

        Assert.Equal((true, "rect_transform"), (result.Success, result.Source));
        Assert.Equal(new[] { 8d, 0d, 0d }, result.Center);
        Assert.Equal(new[] { 1d, 1d, 1d }, result.Extents);
    }

    [Fact]
    public void Zero_Contributors_Returns_Typed_Bounds_Unavailable_Error()
    {
        GeometryBoundsResult result = GeometryBoundsMath.Aggregate(
            System.Array.Empty<GeometryBoundsContributor>(),
            "renderer",
            includeChildren: true);

        Assert.Equal((false, "EDITOR_CTRL_BOUNDS_UNAVAILABLE"), (result.Success, result.ErrorCode));
    }

    [Fact]
    public void Unsupported_Source_Returns_Typed_Source_Error()
    {
        GeometryBoundsResult result = GeometryBoundsMath.Aggregate(
            System.Array.Empty<GeometryBoundsContributor>(),
            "mesh",
            includeChildren: true);

        Assert.Equal((false, "EDITOR_CTRL_BOUNDS_SOURCE_INVALID"), (result.Success, result.ErrorCode));
    }
}
