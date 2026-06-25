using System;
using System.Collections.Generic;
using PrefabSentinel;
using Xunit;

namespace PrefabSentinel.Tests;

/// <summary>
/// Issue #84 — exercise <see cref="ObjectCaptureFramingMath"/> end-to-end
/// in the Unity-free C# xUnit harness.  These tests are the behavioral
/// guarantee for the target-oriented screenshot mode's pure math
/// (preset → camera-position direction, outlier-renderer selection,
/// perspective-correct two-end binding solve + non-binding re-centering).
///
/// Convention recap (matches the issue body + spec.md):
///   * <c>cameraDirection</c> is the camera-POSITION direction: a unit
///     vector pointing FROM the framing pivot TOWARD the camera.
///   * <c>cameraRight</c>/<c>cameraUp</c> complete the orthonormal basis
///     in the orientation Unity's <c>SceneView.LookAt</c> would establish
///     when the camera points at the pivot.
///   * Front preset (yaw=0, pitch=-5) places the camera at a 5° elevation
///     in front of the target (looking slightly down at the target).
/// </summary>
public class ObjectCaptureFramingMathTests
{
    private const float DirectionTolerance = 1e-4f;
    private const float FramingTolerance = 1e-3f;

    // Identity quaternion as (x, y, z, w).
    private static float[] Identity() => new float[] { 0f, 0f, 0f, 1f };

    // Expected target-local direction for each preset, computed from
    // the issue-body authored (yaw, pitch) seeds under Unity's
    // ``Q.Euler(pitch, yaw, 0) * Vector3.forward`` composition:
    //   localDir = (cos(pitch)*sin(yaw), -sin(pitch), cos(pitch)*cos(yaw))
    private static float[] ExpectedLocalDirection(float yawDeg, float pitchDeg)
    {
        double yawRad = yawDeg * Math.PI / 180.0;
        double pitchRad = pitchDeg * Math.PI / 180.0;
        double cp = Math.Cos(pitchRad);
        double sp = Math.Sin(pitchRad);
        double cy = Math.Cos(yawRad);
        double sy = Math.Sin(yawRad);
        double x = cp * sy;
        double y = -sp;
        double z = cp * cy;
        double len = Math.Sqrt(x * x + y * y + z * z);
        return new float[]
        {
            (float)(x / len),
            (float)(y / len),
            (float)(z / len),
        };
    }

    private static void AssertDirectionsEqual(
        float[] expected, float[] actual, string preset)
    {
        Assert.NotNull(actual);
        Assert.Equal(3, actual.Length);
        for (int i = 0; i < 3; i++)
        {
            float diff = Math.Abs(expected[i] - actual[i]);
            Assert.True(
                diff <= DirectionTolerance,
                $"Preset '{preset}' axis[{i}] expected {expected[i]} ± "
                + $"{DirectionTolerance}, got {actual[i]} (diff {diff}).");
        }
    }

    // -------- Preset camera direction (front) --------

    [Fact]
    public void Front_Preset_Composes_To_The_Issue_Body_Authored_Seed_Direction()
    {
        bool ok = ObjectCaptureFramingMath.TryResolvePresetDirection(
            "front", Identity(), out float[] dir, out string reason);

        Assert.True(ok, $"TryResolvePresetDirection failed: '{reason}'.");
        Assert.Equal(string.Empty, reason);
        AssertDirectionsEqual(ExpectedLocalDirection(0f, -5f), dir, "front");
    }

    // -------- Preset camera direction (each non-default preset) --------

    [Theory]
    [InlineData("three_quarter", 35f, -10f)]
    [InlineData("back", 180f, -5f)]
    [InlineData("right", 90f, -5f)]
    [InlineData("left", -90f, -5f)]
    [InlineData("top", 0f, -89f)]
    public void Each_Non_Default_Preset_Composes_To_Its_Authored_Seed(
        string preset, float yawDeg, float pitchDeg)
    {
        bool ok = ObjectCaptureFramingMath.TryResolvePresetDirection(
            preset, Identity(), out float[] dir, out string reason);

        Assert.True(ok, $"TryResolvePresetDirection failed: '{reason}'.");
        AssertDirectionsEqual(
            ExpectedLocalDirection(yawDeg, pitchDeg), dir, preset);
    }

    // -------- Helper preset table values track the issue-body seed --------

    [Fact]
    public void Preset_Table_Yaw_And_Pitch_Match_The_Issue_Body_Seed_Values()
    {
        // Pin the table at the issue-body seeds verbatim. If the
        // implementer retunes during manual in-Editor verification,
        // BOTH this row and the helper table are updated together —
        // a unilateral helper edit must surface here.
        Assert.Equal(
            new[] { "front", "three_quarter", "back", "right", "left", "top" },
            ObjectCaptureFramingMath.PresetNames);
        Assert.Equal(
            new[] { 0f, 35f, 180f, 90f, -90f, 0f },
            ObjectCaptureFramingMath.PresetYawDegrees);
        Assert.Equal(
            new[] { -5f, -10f, -5f, -5f, -5f, -89f },
            ObjectCaptureFramingMath.PresetPitchDegrees);
    }

    // -------- Unknown preset rejected by helper --------

    [Fact]
    public void Unknown_Preset_Name_Yields_A_Typed_Failure()
    {
        bool ok = ObjectCaptureFramingMath.TryResolvePresetDirection(
            "banana", Identity(), out float[] dir, out string reason);

        Assert.False(ok, "Unknown preset must yield a typed failure.");
        Assert.Equal(
            ObjectCaptureFramingMath.FailureUnknownPreset, reason);
        Assert.Null(dir);
    }

    // -------- Camera basis along -Z (used by the framing-solver tests) --------

    private static float[] CameraRightAlongMinusZ() => new float[] { 1f, 0f, 0f };
    private static float[] CameraUpAlongMinusZ() => new float[] { 0f, 1f, 0f };
    private static float[] CameraDirectionAlongPlusZ() => new float[] { 0f, 0f, 1f };

    // Build 8 corners of an axis-aligned cuboid centered at the origin
    // with the supplied half-extents.
    private static float[] CuboidCorners(float ex, float ey, float ez)
    {
        var arr = new float[24];
        int k = 0;
        for (int sx = -1; sx <= 1; sx += 2)
        {
            for (int sy = -1; sy <= 1; sy += 2)
            {
                for (int sz = -1; sz <= 1; sz += 2)
                {
                    arr[k++] = sx * ex;
                    arr[k++] = sy * ey;
                    arr[k++] = sz * ez;
                }
            }
        }
        return arr;
    }


    private static double Dot(float x, float y, float z, float[] axis)
    {
        return x * axis[0] + y * axis[1] + z * axis[2];
    }

    private static double MaxAxisBindingPressure(
        float[] corners,
        float[] axis,
        float[] cameraDirection,
        float[] pivot,
        float cameraDistance)
    {
        double centerX = 0.0;
        double centerY = 0.0;
        double centerZ = 0.0;
        for (int i = 0; i < 8; i++)
        {
            centerX += corners[i * 3 + 0];
            centerY += corners[i * 3 + 1];
            centerZ += corners[i * 3 + 2];
        }
        centerX /= 8.0;
        centerY /= 8.0;
        centerZ /= 8.0;

        double pivotOffset = Dot(
            (float)(pivot[0] - centerX),
            (float)(pivot[1] - centerY),
            (float)(pivot[2] - centerZ),
            axis);
        double maxPressure = 0.0;
        for (int i = 0; i < 8; i++)
        {
            float dx = (float)(corners[i * 3 + 0] - centerX);
            float dy = (float)(corners[i * 3 + 1] - centerY);
            float dz = (float)(corners[i * 3 + 2] - centerZ);
            double depth = cameraDistance - Dot(dx, dy, dz, cameraDirection);
            if (depth <= 0.0)
            {
                throw new InvalidOperationException("Framing pressure requires positive corner depth.");
            }
            double lateral = Dot(dx, dy, dz, axis) - pivotOffset;
            maxPressure = Math.Max(maxPressure, Math.Abs(lateral / depth));
        }
        return maxPressure;
    }

    // -------- Framing solver — cubic AABB centered --------

    [Fact]
    public void Cubic_Aabb_Centered_Returns_Pivot_At_Origin_And_The_Closed_Form_Size()
    {
        const float fov = 60f;
        const float aspect = 1f;
        const float margin = 1f;
        float[] corners = CuboidCorners(0.5f, 0.5f, 0.5f);

        bool ok = ObjectCaptureFramingMath.TrySolveFramingForAabb(
            corners,
            CameraRightAlongMinusZ(),
            CameraUpAlongMinusZ(),
            CameraDirectionAlongPlusZ(),
            fov, aspect, margin,
            ObjectCaptureFramingMath.RecenteringIterationCount,
            out float[] pivot, out float size, out string reason);

        Assert.True(ok, $"Solver failed: '{reason}'.");
        Assert.NotNull(pivot);

        // Closed-form: camera distance D = 0.5 (front face) + 0.5/tan(fov/2);
        // size = D * sin(fov/2). For fov=60° this is 0.5*sin(30°) + 0.5*cos(30°)
        // = 0.25 + 0.4330127 = 0.6830127.
        double tanHalfFov = Math.Tan(fov * 0.5 * Math.PI / 180.0);
        double sinHalfFov = Math.Sin(fov * 0.5 * Math.PI / 180.0);
        double cosHalfFov = Math.Cos(fov * 0.5 * Math.PI / 180.0);
        double expectedD = 0.5 + 0.5 / tanHalfFov;
        float expectedSize = (float)(expectedD * sinHalfFov);

        Assert.InRange(pivot[0], -FramingTolerance, FramingTolerance);
        Assert.InRange(pivot[1], -FramingTolerance, FramingTolerance);
        Assert.InRange(pivot[2], -FramingTolerance, FramingTolerance);
        Assert.InRange(size, expectedSize - FramingTolerance, expectedSize + FramingTolerance);
        // Sanity: the closed-form should equal 0.5*(sin+cos) for the cube.
        Assert.InRange(
            expectedSize,
            (float)(0.5 * (sinHalfFov + cosHalfFov)) - 1e-5f,
            (float)(0.5 * (sinHalfFov + cosHalfFov)) + 1e-5f);
    }

    // -------- Framing solver — wide AABB (X binding) --------

    [Fact]
    public void Wide_Aabb_Binds_On_The_Horizontal_Axis_And_Recenters_Vertical()
    {
        const float fov = 60f;
        const float aspect = 1f;
        const float margin = 1f;
        float[] corners = CuboidCorners(1f, 0.5f, 0.5f);

        bool ok = ObjectCaptureFramingMath.TrySolveFramingForAabb(
            corners,
            CameraRightAlongMinusZ(),
            CameraUpAlongMinusZ(),
            CameraDirectionAlongPlusZ(),
            fov, aspect, margin,
            ObjectCaptureFramingMath.RecenteringIterationCount,
            out float[] pivot, out float size, out string reason);

        Assert.True(ok, $"Solver failed: '{reason}'.");

        // Horizontal-binding closed-form: camera distance
        // D = 1.0 / tan(fov/2) + 0.5; size = D * sin(fov/2).
        double tanHalfFov = Math.Tan(fov * 0.5 * Math.PI / 180.0);
        double sinHalfFov = Math.Sin(fov * 0.5 * Math.PI / 180.0);
        double expectedD = 1.0 / tanHalfFov + 0.5;
        float expectedSize = (float)(expectedD * sinHalfFov);
        Assert.InRange(size, expectedSize - FramingTolerance, expectedSize + FramingTolerance);

        // Pivot must remain on the AABB center for a symmetric AABB:
        // both axes are symmetric around 0, so the binding corners
        // touch ±K and the non-binding axis re-centers to 0.
        Assert.InRange(pivot[0], -FramingTolerance, FramingTolerance);
        Assert.InRange(pivot[1], -FramingTolerance, FramingTolerance);

        // Binding constraint: a corner pair (+1, *, +0.5) and (-1, *, +0.5)
        // satisfies (u - pivot.x) / (D - h) = ±K_h. With aspect=1.0,
        // margin=1.0: K_h = tan(fov/2).
        double k = tanHalfFov * aspect * margin;
        double d = size / sinHalfFov;
        double depthNearFace = d - 0.5;
        double ratioRight = (1f - pivot[0]) / depthNearFace;
        double ratioLeft = (-1f - pivot[0]) / depthNearFace;
        Assert.InRange(ratioRight, k - 1e-3, k + 1e-3);
        Assert.InRange(ratioLeft, -k - 1e-3, -k + 1e-3);
    }

    // -------- Framing solver — tall AABB (Y binding) --------

    [Fact]
    public void Tall_Aabb_Binds_On_The_Vertical_Axis_And_Recenters_Horizontal()
    {
        const float fov = 60f;
        const float aspect = 1f;
        const float margin = 1f;
        float[] corners = CuboidCorners(0.5f, 1f, 0.5f);

        bool ok = ObjectCaptureFramingMath.TrySolveFramingForAabb(
            corners,
            CameraRightAlongMinusZ(),
            CameraUpAlongMinusZ(),
            CameraDirectionAlongPlusZ(),
            fov, aspect, margin,
            ObjectCaptureFramingMath.RecenteringIterationCount,
            out float[] pivot, out float size, out string reason);

        Assert.True(ok, $"Solver failed: '{reason}'.");

        double tanHalfFov = Math.Tan(fov * 0.5 * Math.PI / 180.0);
        double sinHalfFov = Math.Sin(fov * 0.5 * Math.PI / 180.0);
        double expectedD = 1.0 / tanHalfFov + 0.5;
        float expectedSize = (float)(expectedD * sinHalfFov);
        Assert.InRange(size, expectedSize - FramingTolerance, expectedSize + FramingTolerance);
        Assert.InRange(pivot[0], -FramingTolerance, FramingTolerance);
        Assert.InRange(pivot[1], -FramingTolerance, FramingTolerance);

        // Binding constraint on Y: a pair (*, +1, +0.5) and (*, -1, +0.5)
        // satisfies (v - pivot.y) / (D - h) = ±K_v. With margin=1.0
        // K_v = tan(fov/2).
        double k = tanHalfFov * margin;
        double d = size / sinHalfFov;
        double depthNearFace = d - 0.5;
        double ratioTop = (1f - pivot[1]) / depthNearFace;
        double ratioBot = (-1f - pivot[1]) / depthNearFace;
        Assert.InRange(ratioTop, k - 1e-3, k + 1e-3);
        Assert.InRange(ratioBot, -k - 1e-3, -k + 1e-3);
    }

    // -------- Framing solver — re-centering iteration converges --------

    [Fact]
    public void Recentering_Converges_Within_The_Documented_Iteration_Bound()
    {
        // Asymmetric AABB: offset wedge that pushes the vertical axis
        // off-center under horizontal binding. After horizontal-axis
        // binding settles D_h, the vertical max/min projection corners
        // sit at different depths so a single shift is insufficient;
        // 3 iterations must converge to the same pivot as 6 iterations.
        const float fov = 60f;
        const float aspect = 1f;
        const float margin = 1f;
        var corners = new float[]
        {
            -1.0f, -0.2f, -0.3f,
            -1.0f, -0.2f,  0.3f,
            -1.0f,  0.6f, -0.3f,
            -1.0f,  0.6f,  0.3f,
             1.0f, -0.2f, -0.3f,
             1.0f, -0.2f,  0.3f,
             1.0f,  0.6f, -0.3f,
             1.0f,  0.6f,  0.3f,
        };

        bool okA = ObjectCaptureFramingMath.TrySolveFramingForAabb(
            corners,
            CameraRightAlongMinusZ(),
            CameraUpAlongMinusZ(),
            CameraDirectionAlongPlusZ(),
            fov, aspect, margin,
            ObjectCaptureFramingMath.RecenteringIterationCount,
            out float[] pivotA, out float sizeA, out _);
        bool okB = ObjectCaptureFramingMath.TrySolveFramingForAabb(
            corners,
            CameraRightAlongMinusZ(),
            CameraUpAlongMinusZ(),
            CameraDirectionAlongPlusZ(),
            fov, aspect, margin,
            ObjectCaptureFramingMath.RecenteringIterationCount + 3,
            out float[] pivotB, out float sizeB, out _);

        Assert.True(okA && okB);
        // The documented iteration bound (3) converges to the same
        // pivot as one extra batch of iterations (6) — no oscillation.
        for (int i = 0; i < 3; i++)
        {
            float diff = Math.Abs(pivotA[i] - pivotB[i]);
            Assert.True(
                diff <= 1e-4f,
                $"Pivot drifted between 3-iter and 6-iter runs at axis {i}: "
                + $"|{pivotA[i]} - {pivotB[i]}| = {diff}.");
        }
        Assert.InRange(sizeA - sizeB, -1e-4f, 1e-4f);

        // Horizontal binding constraint must still hold (the wide
        // ±1 X-extent dominates the vertical 0.4 spread).
        double tanHalfFov = Math.Tan(fov * 0.5 * Math.PI / 180.0);
        double sinHalfFov = Math.Sin(fov * 0.5 * Math.PI / 180.0);
        double k = tanHalfFov * aspect * margin;
        double d = sizeA / sinHalfFov;
        // Find the near-face X-binding corner pair (h=+0.3, |u|=1).
        double depthNearFace = d - 0.3;
        double ratioRight = (1f - pivotA[0]) / depthNearFace;
        double ratioLeft = (-1f - pivotA[0]) / depthNearFace;
        Assert.InRange(ratioRight, k - 1e-3, k + 1e-3);
        Assert.InRange(ratioLeft, -k - 1e-3, -k + 1e-3);
    }

    // -------- Framing solver — degenerate AABB --------

    [Fact]
    public void Degenerate_Aabb_Yields_A_Typed_Failure_Instead_Of_Dividing_By_Zero()
    {
        var corners = new float[24]; // every corner at (0, 0, 0)

        bool ok = ObjectCaptureFramingMath.TrySolveFramingForAabb(
            corners,
            CameraRightAlongMinusZ(),
            CameraUpAlongMinusZ(),
            CameraDirectionAlongPlusZ(),
            60f, 1f, 1f,
            ObjectCaptureFramingMath.RecenteringIterationCount,
            out float[] pivot, out float size, out string reason);

        Assert.False(ok, "Degenerate AABB must yield a typed failure.");
        Assert.Equal(ObjectCaptureFramingMath.FailureDegenerateAabb, reason);
        Assert.Null(pivot);
        Assert.Equal(0f, size);
    }

    // -------- Outlier filter selects largest-extent renderer as core --------

    [Fact]
    public void Outlier_Filter_Picks_The_Largest_Extent_Renderer_As_The_Core()
    {
        // Per-axis AABB-containment outlier filter (matches the PoC
        // reference): core extents (1.0, 0.5, 0.5) ⇒ per-axis allow
        // box = extents + max(0.3 * extents, 0.1) = (1.3, 0.65, 0.65).
        // Candidates whose absolute center offset fits inside the box
        // along *every* axis are kept; ones that escape along even a
        // single axis are dropped. The previous euclidean-sphere form
        // dropped renderers far along one axis (e.g., a VRChat avatar's
        // head 1 m above the body center) — this regression caused #84
        // to clip the head out of frame in real-Unity visual verification.
        var renderers = new List<ObjectCaptureFramingMath.RendererBoundsRecord>
        {
            new ObjectCaptureFramingMath.RendererBoundsRecord(
                new float[] { 1.2f, 0f, 0f }, new float[] { 0.05f, 0.05f, 0.05f }),
            new ObjectCaptureFramingMath.RendererBoundsRecord(
                new float[] { 0f, 0f, 0f }, new float[] { 1.0f, 0.5f, 0.5f }),
            new ObjectCaptureFramingMath.RendererBoundsRecord(
                new float[] { 0f, 0f, 1.0f }, new float[] { 0.05f, 0.05f, 0.05f }),
            new ObjectCaptureFramingMath.RendererBoundsRecord(
                new float[] { 1.5f, 0f, 0f }, new float[] { 0.05f, 0.05f, 0.05f }),
        };

        IList<ObjectCaptureFramingMath.RendererBoundsRecord> kept =
            ObjectCaptureFramingMath.SelectFramingRenderers(renderers);

        // Stable order: core first, then in-input-order kept candidates.
        // renderers[0] kept (|dx|=1.2 < 1.3 allowX);
        // renderers[2] dropped (|dz|=1.0 > 0.65 allowZ);
        // renderers[3] dropped (|dx|=1.5 > 1.3 allowX).
        Assert.Equal(2, kept.Count);
        Assert.Same(renderers[1], kept[0]); // core (largest extent)
        Assert.Same(renderers[0], kept[1]); // inside per-axis box
    }

    // -------- Outlier filter per-axis box = extents + max(0.30 * extents, 0.1) --------

    [Theory]
    // Core expansion: (1.0, 0.5, 0.5) to (1.3, 0.65, 0.65); test each axis.
    [InlineData(1.0f, 1.29f, 0f, 0f, true)]   // x 1.29 < 1.3
    [InlineData(1.0f, 1.31f, 0f, 0f, false)]  // x 1.31 > 1.3
    [InlineData(1.0f, 0f, 0.64f, 0f, true)]   // y 0.64 < 0.65
    [InlineData(1.0f, 0f, 0.66f, 0f, false)]  // y 0.66 > 0.65
    [InlineData(1.0f, 0f, 0f, 0.64f, true)]   // z 0.64 < 0.65
    [InlineData(1.0f, 0f, 0f, 0.66f, false)]  // z 0.66 > 0.65
    // Tiny core (0.01, 0.005, 0.005): floor 0.1 m kicks in on every
    // axis; allow ≈ (0.11, 0.105, 0.105).
    [InlineData(0.01f, 0.10f, 0f, 0f, true)]
    [InlineData(0.01f, 0.12f, 0f, 0f, false)]
    public void Outlier_Filter_Per_Axis_Inclusion_Box_Matches_Max_Of_Relative_And_Floor(
        float coreLargestExtent, float dx, float dy, float dz, bool expectedKept)
    {
        var core = new ObjectCaptureFramingMath.RendererBoundsRecord(
            new float[] { 0f, 0f, 0f },
            new float[] { coreLargestExtent, coreLargestExtent * 0.5f, coreLargestExtent * 0.5f });
        var candidate = new ObjectCaptureFramingMath.RendererBoundsRecord(
            new float[] { dx, dy, dz },
            new float[] { 0.001f, 0.001f, 0.001f });
        var input = new List<ObjectCaptureFramingMath.RendererBoundsRecord>
        {
            core, candidate,
        };

        IList<ObjectCaptureFramingMath.RendererBoundsRecord> kept =
            ObjectCaptureFramingMath.SelectFramingRenderers(input);

        bool candidateKept = false;
        foreach (var r in kept)
        {
            if (ReferenceEquals(r, candidate)) { candidateKept = true; break; }
        }
        Assert.True(
            candidateKept == expectedKept,
            $"core_extent={coreLargestExtent}, offset=({dx},{dy},{dz}), "
            + $"expected kept={expectedKept}, observed kept={candidateKept}.");
    }

    // -------- Framing solver consumes the supplied aspect --------

    [Fact]
    public void Wider_Aspect_Reduces_The_Required_SceneView_Size()
    {
        // Hold the AABB / direction / fov constant, vary aspect from 1.0 to 2.0.
        // Aspect 2.0 doubles the horizontal frame budget, so the X-binding
        // axis can satisfy the constraint at a smaller distance and size.
        const float fov = 60f;
        const float margin = 1f;
        float[] corners = CuboidCorners(1f, 0.5f, 0.5f);

        bool okA = ObjectCaptureFramingMath.TrySolveFramingForAabb(
            corners,
            CameraRightAlongMinusZ(), CameraUpAlongMinusZ(),
            CameraDirectionAlongPlusZ(),
            fov, 1.0f, margin,
            ObjectCaptureFramingMath.RecenteringIterationCount,
            out float[] _, out float sizeNarrow, out _);
        bool okB = ObjectCaptureFramingMath.TrySolveFramingForAabb(
            corners,
            CameraRightAlongMinusZ(), CameraUpAlongMinusZ(),
            CameraDirectionAlongPlusZ(),
            fov, 2.0f, margin,
            ObjectCaptureFramingMath.RecenteringIterationCount,
            out float[] _, out float sizeWide, out _);

        Assert.True(okA && okB);
        Assert.True(
            sizeWide < sizeNarrow,
            $"Expected size(aspect=2.0)={sizeWide} < size(aspect=1.0)="
            + $"{sizeNarrow}; a wider rendered frame requires less "
            + $"SceneView half-width to bound the same AABB.");
    }


    [Fact]
    public void Both_Axes_Aspect_For_Narrow_Depth_Tall_Target_Balances_Binding_Pressure()
    {
        const float fov = 60f;
        const float margin = 1f;
        float[] corners = CuboidCorners(0.25f, 1f, 0.5f);

        bool aspectOk = ObjectCaptureFramingMath.TryResolveBothAxesAspectForAabb(
            corners,
            CameraRightAlongMinusZ(),
            CameraUpAlongMinusZ(),
            CameraDirectionAlongPlusZ(),
            fov,
            margin,
            out float aspect,
            out string aspectFailureReason);

        Assert.True(aspectOk, $"Both-axis aspect solver failed: '{aspectFailureReason}'.");
        Assert.True(
            aspect is > 0f and < 1f,
            $"Expected portrait aspect in range (0, 1), got {aspect}.");

        bool framingOk = ObjectCaptureFramingMath.TrySolveFramingForAabb(
            corners,
            CameraRightAlongMinusZ(),
            CameraUpAlongMinusZ(),
            CameraDirectionAlongPlusZ(),
            fov,
            aspect,
            margin,
            ObjectCaptureFramingMath.RecenteringIterationCount,
            out float[] pivot,
            out float size,
            out string framingFailureReason);

        Assert.True(framingOk, $"Framing solver failed at aspect {aspect}: '{framingFailureReason}'.");

        double sinHalfFov = Math.Sin(fov * 0.5 * Math.PI / 180.0);
        double cameraDistance = size / sinHalfFov;
        double horizontalPressure = MaxAxisBindingPressure(
            corners,
            CameraRightAlongMinusZ(),
            CameraDirectionAlongPlusZ(),
            pivot,
            (float)cameraDistance) / aspect;
        double verticalPressure = MaxAxisBindingPressure(
            corners,
            CameraUpAlongMinusZ(),
            CameraDirectionAlongPlusZ(),
            pivot,
            (float)cameraDistance);
        Assert.InRange(horizontalPressure, verticalPressure - 1e-3, verticalPressure + 1e-3);
    }



    [Fact]
    public void Both_Axes_Output_Size_With_No_Explicit_Dimensions_Preserves_Default_Long_Edge()
    {
        bool ok = ObjectCaptureFramingMath.ResolveOutputSizeForFitMode(
            "both_axes",
            requestWidth: 0,
            requestHeight: 0,
            defaultWidth: 1000,
            defaultHeight: 600,
            bothAxesAspect: 0.25f,
            out int width,
            out int height,
            out float aspect,
            out string reason);

        Assert.Equal((true, 250, 1000, 0.25f, string.Empty), (ok, width, height, aspect, reason));
    }

    [Fact]
    public void Both_Axes_Output_Size_With_Explicit_Dimensions_Uses_Caller_Aspect()
    {
        bool ok = ObjectCaptureFramingMath.ResolveOutputSizeForFitMode(
            "both_axes",
            requestWidth: 480,
            requestHeight: 1920,
            defaultWidth: 1000,
            defaultHeight: 600,
            bothAxesAspect: 0.5f,
            out int width,
            out int height,
            out float aspect,
            out string reason);

        Assert.Equal((true, 480, 1920, 0.25f, string.Empty), (ok, width, height, aspect, reason));
    }

    [Fact]
    public void Both_Axes_Output_Size_With_One_Explicit_Side_Keeps_The_Default_Other_Side()
    {
        bool ok = ObjectCaptureFramingMath.ResolveOutputSizeForFitMode(
            "both_axes",
            requestWidth: 480,
            requestHeight: 0,
            defaultWidth: 600,
            defaultHeight: 1000,
            bothAxesAspect: 0.25f,
            out int width,
            out int height,
            out float aspect,
            out string reason);

        Assert.Equal((true, 480, 1000, 0.48f, string.Empty), (ok, width, height, aspect, reason));
    }

    [Fact]
    public void Unknown_Fit_Mode_Is_Rejected_By_Output_Size_Resolution()
    {
        bool ok = ObjectCaptureFramingMath.ResolveOutputSizeForFitMode(
            "fill",
            requestWidth: 0,
            requestHeight: 0,
            defaultWidth: 1000,
            defaultHeight: 600,
            bothAxesAspect: 0.25f,
            out int width,
            out int height,
            out float aspect,
            out string reason);

        Assert.Equal((false, 0, 0, 0f), (ok, width, height, aspect));
        Assert.Contains("fill", reason, StringComparison.Ordinal);
    }

    [Theory]
    [InlineData(0, 600, 0.25f, "default")]
    [InlineData(1000, 0, 0.25f, "default")]
    [InlineData(1000, 600, 0f, "bothAxesAspect")]
    public void Invalid_Output_Size_Prerequisites_Are_Rejected(
        int defaultWidth,
        int defaultHeight,
        float bothAxesAspect,
        string expectedReasonFragment)
    {
        bool ok = ObjectCaptureFramingMath.ResolveOutputSizeForFitMode(
            "both_axes",
            requestWidth: 0,
            requestHeight: 0,
            defaultWidth,
            defaultHeight,
            bothAxesAspect,
            out int width,
            out int height,
            out float aspect,
            out string reason);

        Assert.Equal((false, 0, 0, 0f), (ok, width, height, aspect));
        Assert.Contains(expectedReasonFragment, reason, StringComparison.Ordinal);
    }

    [Fact]
    public void Malformed_Both_Axes_Aabb_Raises_The_Same_Vector_Length_Error_As_The_Frame_Solver()
    {
        ArgumentException error = Assert.Throws<ArgumentException>(() =>
            ObjectCaptureFramingMath.TryResolveBothAxesAspectForAabb(
                new float[3],
                CameraRightAlongMinusZ(),
                CameraUpAlongMinusZ(),
                CameraDirectionAlongPlusZ(),
                60f,
                1f,
                out _,
                out _));

        Assert.Equal("cornersWorld", error.ParamName);
    }

    [Fact]
    public void Degenerate_Both_Axes_Aabb_Returns_A_Typed_Failure_Instead_Of_An_Aspect()
    {
        bool ok = ObjectCaptureFramingMath.TryResolveBothAxesAspectForAabb(
            new float[24],
            CameraRightAlongMinusZ(),
            CameraUpAlongMinusZ(),
            CameraDirectionAlongPlusZ(),
            60f,
            1f,
            out float aspect,
            out string reason);

        Assert.Equal((false, 0f, ObjectCaptureFramingMath.FailureDegenerateAabb), (ok, aspect, reason));
    }
}
