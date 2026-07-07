// The helper exposes ``out float[]`` parameters that are set to ``null``
// on the failure branch; ``#nullable disable`` keeps the file warning-
// clean under both the Unity assembly (nullable off) and the xUnit test
// project (nullable on), matching the precedent of the request-DTO
// partial ``PrefabSentinel.Dispatch.EditorControlRequest.cs``.
#nullable disable
using System;
using System.Collections.Generic;

// Pure framing math for the target-oriented screenshot capture mode
// (issue #84). The helper is Unity-free so the C# xUnit harness can
// exercise the math end-to-end without a Unity assembly reference;
// the bridge-side ``HandleCaptureScreenshot`` partial consumes its
// outputs (preset direction, outlier-filtered renderer subset,
// framing pivot + Scene-view half-width) and then applies them via
// ``SceneView.LookAt``.
//
// Naming/placement precedent: PrefabSentinel.Screenshot.ViewAllowlistClassifier.cs.
//
// Conventions match the issue body verbatim:
//   * Preset (yaw, pitch) seeds are target-LOCAL; the camera-position
//     direction is composed as
//     ``targetRotation * Q.Euler(pitch, yaw, 0) * Vector3.forward``
//     under Unity's left-handed Y-up convention (``yaw=0`` faces +Z).
//   * ``cameraDirectionWorld`` is the camera-POSITION direction:
//     a unit vector pointing FROM the framing pivot TOWARD the
//     camera (= ``-cameraForward``).  ``cameraRight``/``cameraUp``
//     complete the orthonormal basis as the Unity SceneView would
//     derive them via ``Quaternion.LookRotation(-cameraDir, worldUp)``.
//   * Outlier-renderer inclusion margin is
//     ``max(0.30 * core_largest_extent_dimension, 0.1)`` world-space
//     units, applied to the AABB-center-to-AABB-center distance from
//     the largest-extent renderer.
//   * Perspective-correct two-end binding: per-axis enumerate every
//     ordered corner pair (i, j), accept pairs whose D > 0 and where
//     every other corner stays inside the ±K frame, take the smallest
//     such D, then re-center the non-binding axis at the final
//     ``max(D_horizontal, D_vertical)``.  The re-centering iteration
//     bound is 3 (the issue body's "1〜3 iterations" upper limit).
namespace PrefabSentinel
{
    /// <summary>
    /// Pure-math helpers for the target-oriented screenshot capture
    /// branch (issue #84).  Exposed as a static class so the bridge
    /// handler and the C# xUnit harness consume the same source.
    /// </summary>
    public static class ObjectCaptureFramingMath
    {
        /// <summary>
        /// Canonical six-member preset name list in the issue-body order:
        /// ``front`` / ``three_quarter`` / ``back`` / ``right`` / ``left`` / ``top``.
        /// </summary>
        public static readonly string[] PresetNames =
        {
            "front",
            "three_quarter",
            "back",
            "right",
            "left",
            "top",
        };

        /// <summary>
        /// Issue #84 body authored (yaw_degrees, pitch_degrees) seed
        /// pairs.  Index aligns with <see cref="PresetNames"/>.
        ///
        /// The seeds are target-LOCAL.  A change to one entry here must
        /// be reflected in the per-preset T1 row and the issue-body-seed
        /// row in the test class so drift between the helper and the
        /// authored seed surfaces rather than going silent.
        /// </summary>
        public static readonly float[] PresetYawDegrees =
            { 0f, 35f, 180f, 90f, -90f, 0f };

        public static readonly float[] PresetPitchDegrees =
            { -5f, -10f, -5f, -5f, -5f, -89f };

        /// <summary>
        /// Outlier-filter inclusion margin: include other renderers
        /// whose AABB center lies within
        /// ``max(OutlierMarginRelative * core_largest_extent_dim,
        /// OutlierMarginFloor)`` world-space units of the core
        /// renderer's center.  Both constants are exposed so the C#
        /// xUnit harness pins them.
        /// </summary>
        public const float OutlierMarginRelative = 0.30f;
        public const float OutlierMarginFloor = 0.1f;

        /// <summary>
        /// Internal framing margin factor (issue body: PoC verified
        /// 1.0).  Multiplies the K = tan(fov/2) bound so the rendered
        /// subject can be padded by a fixed fraction without exposing
        /// a continuous ``fill`` parameter on the MCP surface.
        /// </summary>
        public const float DefaultFramingMargin = 1.0f;

        /// <summary>
        /// Re-centering iteration count for the non-binding axis
        /// (issue body: "1〜3 反復で収束").
        /// </summary>
        public const int RecenteringIterationCount = 3;

        // Failure reason literals returned through the Try* helpers
        // so callers (bridge handler, xUnit tests) can branch on a
        // structured value instead of message parsing.
        public const string FailureUnknownPreset = "unknown_preset";
        public const string FailureDegenerateAabb = "degenerate_aabb";
        public const string FailureNoValidBinding = "no_valid_binding";

        /// <summary>
        /// Resolve <paramref name="preset"/> to the issue-body authored
        /// target-local (yaw, pitch) pair.  Returns true and writes the
        /// pair into the out parameters when the name is in
        /// <see cref="PresetNames"/>; returns false otherwise.
        /// </summary>
        public static bool TryGetPresetAngles(
            string preset,
            out float yawDegrees,
            out float pitchDegrees)
        {
            for (int i = 0; i < PresetNames.Length; i++)
            {
                if (string.Equals(preset, PresetNames[i], StringComparison.Ordinal))
                {
                    yawDegrees = PresetYawDegrees[i];
                    pitchDegrees = PresetPitchDegrees[i];
                    return true;
                }
            }
            yawDegrees = 0f;
            pitchDegrees = 0f;
            return false;
        }

        /// <summary>
        /// Resolve <paramref name="preset"/> to a world-space camera-
        /// position direction unit vector.  Composition order matches
        /// Unity's ``targetRotation * Quaternion.Euler(pitch, yaw, 0)
        /// * Vector3.forward`` (the +Z convention documented in
        /// ``docs/api-reference.md``).  The output is a length-3 unit
        /// vector pointing FROM the framing pivot TOWARD the camera.
        ///
        /// <paramref name="targetRotationQuaternionXYZW"/> is the
        /// target's world rotation as ``[x, y, z, w]``; the identity
        /// rotation is ``[0, 0, 0, 1]``.
        /// </summary>
        public static bool TryResolvePresetDirection(
            string preset,
            float[] targetRotationQuaternionXYZW,
            out float[] cameraDirectionWorld,
            out string failureReason)
        {
            cameraDirectionWorld = null;
            failureReason = string.Empty;
            if (!TryGetPresetAngles(preset, out float yawDeg, out float pitchDeg))
            {
                failureReason = FailureUnknownPreset;
                return false;
            }
            if (targetRotationQuaternionXYZW == null
                || targetRotationQuaternionXYZW.Length != 4)
            {
                throw new ArgumentException(
                    "targetRotationQuaternionXYZW must be length 4 (x, y, z, w).",
                    nameof(targetRotationQuaternionXYZW));
            }

            // Target-local direction = Q.Euler(pitch, yaw, 0) * (0, 0, 1).
            // Unity ZXY intrinsic order reduces to RotY(yaw) * RotX(pitch)
            // when the Z angle is zero. The closed form (with Unity's
            // sign convention for RotX) yields:
            //   localDir.x = cos(pitch) * sin(yaw)
            //   localDir.y = -sin(pitch)
            //   localDir.z = cos(pitch) * cos(yaw)
            double yawRad = yawDeg * Math.PI / 180.0;
            double pitchRad = pitchDeg * Math.PI / 180.0;
            double cp = Math.Cos(pitchRad);
            double sp = Math.Sin(pitchRad);
            double cy = Math.Cos(yawRad);
            double sy = Math.Sin(yawRad);
            double lx = cp * sy;
            double ly = -sp;
            double lz = cp * cy;

            // Rotate local direction by the target's world rotation:
            //   v' = v + 2 * cross(q.xyz, cross(q.xyz, v) + q.w * v)
            double qx = targetRotationQuaternionXYZW[0];
            double qy = targetRotationQuaternionXYZW[1];
            double qz = targetRotationQuaternionXYZW[2];
            double qw = targetRotationQuaternionXYZW[3];

            double tx = qy * lz - qz * ly + qw * lx;
            double ty = qz * lx - qx * lz + qw * ly;
            double tz = qx * ly - qy * lx + qw * lz;
            double wx = lx + 2.0 * (qy * tz - qz * ty);
            double wy = ly + 2.0 * (qz * tx - qx * tz);
            double wz = lz + 2.0 * (qx * ty - qy * tx);

            cameraDirectionWorld = NormalizeOrFallback(wx, wy, wz);
            return true;
        }

        /// <summary>
        /// Renderer-bounds record: one axis-aligned bounding box in
        /// world space, described by its center and per-axis half-
        /// extents.  Plain POCO so the C# xUnit harness consumes the
        /// helper without dragging in Unity's ``Bounds`` type.
        /// </summary>
        public sealed class RendererBoundsRecord
        {
            public float[] CenterWorld;
            public float[] ExtentsWorld;

            public RendererBoundsRecord(
                float[] centerWorld, float[] extentsWorld)
            {
                if (centerWorld == null || centerWorld.Length != 3)
                {
                    throw new ArgumentException(
                        "centerWorld must be length 3.",
                        nameof(centerWorld));
                }
                if (extentsWorld == null || extentsWorld.Length != 3)
                {
                    throw new ArgumentException(
                        "extentsWorld must be length 3.",
                        nameof(extentsWorld));
                }
                CenterWorld = centerWorld;
                ExtentsWorld = extentsWorld;
            }

            public float LargestExtentDimension()
            {
                float ex = Math.Abs(ExtentsWorld[0]);
                float ey = Math.Abs(ExtentsWorld[1]);
                float ez = Math.Abs(ExtentsWorld[2]);
                float max = ex;
                if (ey > max) max = ey;
                if (ez > max) max = ez;
                return max;
            }
        }

        /// <summary>
        /// Outlier filter (issue #84): select the renderer with the
        /// largest per-axis extent dimension as the core envelope,
        /// then keep every other renderer whose AABB center lies
        /// within ``max(OutlierMarginRelative * core_largest_extent,
        /// OutlierMarginFloor)`` of the core renderer's center.
        ///
        /// Returns the input unchanged on empty or single-member
        /// input (nothing to filter against).  Stable order: the
        /// core renderer appears first, then every kept candidate
        /// in input order.
        /// </summary>
        public static IList<RendererBoundsRecord> SelectFramingRenderers(
            IList<RendererBoundsRecord> renderers)
        {
            if (renderers == null) return Array.Empty<RendererBoundsRecord>();
            if (renderers.Count <= 1) return renderers;

            int coreIndex = 0;
            float coreExtent = renderers[0].LargestExtentDimension();
            for (int i = 1; i < renderers.Count; i++)
            {
                float ex = renderers[i].LargestExtentDimension();
                if (ex > coreExtent)
                {
                    coreExtent = ex;
                    coreIndex = i;
                }
            }

            // Per-axis AABB containment matches the PoC reference
            // (PFPoCFraming.Frame ``Bounds.Contains`` with a per-axis
            // expansion vector). A euclidean-sphere distance test —
            // the bridge's initial port — over-aggressively drops
            // renderers far from the core along a single axis: a
            // VRChat avatar's head sits ~1m above the body's center,
            // so a sphere threshold of ``OutlierMarginRelative *
            // largest-extent`` (~0.24 m for an 0.8 m core) excludes
            // it, the aggregate AABB then covers only the body, and
            // framing clips the head out of frame.
            var core = renderers[coreIndex];
            float allowX = core.ExtentsWorld[0]
                + Math.Max(OutlierMarginRelative * core.ExtentsWorld[0], OutlierMarginFloor);
            float allowY = core.ExtentsWorld[1]
                + Math.Max(OutlierMarginRelative * core.ExtentsWorld[1], OutlierMarginFloor);
            float allowZ = core.ExtentsWorld[2]
                + Math.Max(OutlierMarginRelative * core.ExtentsWorld[2], OutlierMarginFloor);

            var kept = new List<RendererBoundsRecord> { core };
            for (int i = 0; i < renderers.Count; i++)
            {
                if (i == coreIndex) continue;
                var r = renderers[i];
                float dx = Math.Abs(r.CenterWorld[0] - core.CenterWorld[0]);
                float dy = Math.Abs(r.CenterWorld[1] - core.CenterWorld[1]);
                float dz = Math.Abs(r.CenterWorld[2] - core.CenterWorld[2]);
                if (dx <= allowX && dy <= allowY && dz <= allowZ)
                {
                    kept.Add(r);
                }
            }
            return kept;
        }

        /// <summary>
        /// Solve the perspective-correct framing for the AABB whose
        /// eight world-space corners are supplied flat as 24 floats
        /// (``[x0,y0,z0, x1,y1,z1, ..., x7,y7,z7]``).
        ///
        /// On success writes the world-space framing pivot and the
        /// corresponding Scene-view half-width (``SceneView.size``)
        /// to the out parameters.  Returns false when the AABB is
        /// degenerate (every projected extent is below the numerical
        /// epsilon) or when no valid binding pair exists at any
        /// orientation (e.g., every candidate D > 0 places at least
        /// one corner behind the camera plane).
        ///
        /// <paramref name="recenteringIterations"/> is the number of
        /// re-centering passes for the non-binding axis at the final
        /// max(D_horizontal, D_vertical).  3 is the issue body's
        /// upper bound; tests pass 4+ to verify convergence stability.
        /// </summary>
private readonly struct ProjectedAabb
        {
            public ProjectedAabb(
                double centerX,
                double centerY,
                double centerZ,
                double[] rightProjection,
                double[] upProjection,
                double[] depthProjection)
            {
                CenterX = centerX;
                CenterY = centerY;
                CenterZ = centerZ;
                RightProjection = rightProjection;
                UpProjection = upProjection;
                DepthProjection = depthProjection;
            }

            public double CenterX { get; }
            public double CenterY { get; }
            public double CenterZ { get; }
            public double[] RightProjection { get; }
            public double[] UpProjection { get; }
            public double[] DepthProjection { get; }
        }

        private static bool TryProjectAabb(
            float[] cornersWorld,
            float[] cameraRightWorld,
            float[] cameraUpWorld,
            float[] cameraDirectionWorld,
            out ProjectedAabb projected,
            out string failureReason)
        {
            projected = default;
            failureReason = string.Empty;
            if (cornersWorld == null || cornersWorld.Length != 24)
            {
                throw new ArgumentException(
                    "cornersWorld must carry 8 corners flattened to 24 floats.",
                    nameof(cornersWorld));
            }
            RequireLength3(cameraRightWorld, nameof(cameraRightWorld));
            RequireLength3(cameraUpWorld, nameof(cameraUpWorld));
            RequireLength3(cameraDirectionWorld, nameof(cameraDirectionWorld));

            double centerX = 0.0;
            double centerY = 0.0;
            double centerZ = 0.0;
            for (int i = 0; i < 8; i++)
            {
                centerX += cornersWorld[i * 3 + 0];
                centerY += cornersWorld[i * 3 + 1];
                centerZ += cornersWorld[i * 3 + 2];
            }
            centerX /= 8.0;
            centerY /= 8.0;
            centerZ /= 8.0;

            double[] rightProjection = new double[8];
            double[] upProjection = new double[8];
            double[] depthProjection = new double[8];
            double maxExtentSq = 0.0;
            for (int i = 0; i < 8; i++)
            {
                double dx = cornersWorld[i * 3 + 0] - centerX;
                double dy = cornersWorld[i * 3 + 1] - centerY;
                double dz = cornersWorld[i * 3 + 2] - centerZ;
                rightProjection[i] = dx * cameraRightWorld[0]
                                   + dy * cameraRightWorld[1]
                                   + dz * cameraRightWorld[2];
                upProjection[i] = dx * cameraUpWorld[0]
                                + dy * cameraUpWorld[1]
                                + dz * cameraUpWorld[2];
                depthProjection[i] = dx * cameraDirectionWorld[0]
                                   + dy * cameraDirectionWorld[1]
                                   + dz * cameraDirectionWorld[2];
                double extentSq = dx * dx + dy * dy + dz * dz;
                if (extentSq > maxExtentSq) maxExtentSq = extentSq;
            }

            const double DegenerateEpsilon = 1e-10;
            if (maxExtentSq < DegenerateEpsilon)
            {
                failureReason = FailureDegenerateAabb;
                return false;
            }

            projected = new ProjectedAabb(
                centerX,
                centerY,
                centerZ,
                rightProjection,
                upProjection,
                depthProjection);
            return true;
        }

        public static bool TryResolveBothAxesAspectForAabb(
            float[] cornersWorld,
            float[] cameraRightWorld,
            float[] cameraUpWorld,
            float[] cameraDirectionWorld,
            float fovDegrees,
            float margin,
            out float aspect,
            out string failureReason)
        {
            aspect = 0f;
            failureReason = string.Empty;
            if (!TryProjectAabb(
                    cornersWorld,
                    cameraRightWorld,
                    cameraUpWorld,
                    cameraDirectionWorld,
                    out ProjectedAabb projected,
                    out failureReason))
            {
                return false;
            }

            double tanHalfFov = Math.Tan(fovDegrees * 0.5 * Math.PI / 180.0);
            if (!IsPositiveFinite(tanHalfFov) || !IsPositiveFinite(margin))
            {
                failureReason = FailureNoValidBinding;
                return false;
            }

            double verticalK = tanHalfFov * margin;
            if (!TrySolveAxis(
                    projected.UpProjection,
                    projected.DepthProjection,
                    verticalK,
                    out double verticalDistance,
                    out _))
            {
                failureReason = FailureNoValidBinding;
                return false;
            }

            const double MinAspect = 1e-4;
            const double MaxAspect = 1e4;
            if (!TrySolveHorizontalDistance(
                    projected,
                    tanHalfFov,
                    margin,
                    MinAspect,
                    out double lowDistance)
                || lowDistance < verticalDistance)
            {
                failureReason = FailureNoValidBinding;
                return false;
            }

            double highAspect = 1.0;
            if (!TrySolveHorizontalDistance(
                    projected,
                    tanHalfFov,
                    margin,
                    highAspect,
                    out double highDistance))
            {
                failureReason = FailureNoValidBinding;
                return false;
            }
            while (highDistance > verticalDistance)
            {
                highAspect *= 2.0;
                if (highAspect > MaxAspect
                    || !TrySolveHorizontalDistance(
                        projected,
                        tanHalfFov,
                        margin,
                        highAspect,
                        out highDistance))
                {
                    failureReason = FailureNoValidBinding;
                    return false;
                }
            }

            double lowAspect = MinAspect;
            for (int i = 0; i < 48; i++)
            {
                double midAspect = (lowAspect + highAspect) * 0.5;
                if (!TrySolveHorizontalDistance(
                        projected,
                        tanHalfFov,
                        margin,
                        midAspect,
                        out double midDistance))
                {
                    failureReason = FailureNoValidBinding;
                    return false;
                }
                if (midDistance > verticalDistance)
                {
                    lowAspect = midAspect;
                }
                else
                {
                    highAspect = midAspect;
                }
            }

            aspect = (float)highAspect;
            return true;
        }

        public static bool ResolveOutputSizeForFitMode(
            string fitMode,
            int requestWidth,
            int requestHeight,
            int defaultWidth,
            int defaultHeight,
            float bothAxesAspect,
            out int width,
            out int height,
            out float aspect,
            out string failureReason)
        {
            width = 0;
            height = 0;
            aspect = 0f;
            failureReason = string.Empty;
            if (fitMode != "max_axis" && fitMode != "both_axes")
            {
                failureReason = $"Unknown fit_mode '{fitMode}'. Expected max_axis or both_axes.";
                return false;
            }
            if (defaultWidth <= 0 || defaultHeight <= 0)
            {
                failureReason = "default dimensions must be positive.";
                return false;
            }

            bool hasWidth = requestWidth > 0;
            bool hasHeight = requestHeight > 0;
            if (fitMode == "max_axis" || hasWidth || hasHeight)
            {
                width = hasWidth ? requestWidth : defaultWidth;
                height = hasHeight ? requestHeight : defaultHeight;
                aspect = width / (float)height;
                return true;
            }

            if (!IsPositiveFinite(bothAxesAspect))
            {
                failureReason = "bothAxesAspect must be finite and positive.";
                return false;
            }

            int longEdge = Math.Max(defaultWidth, defaultHeight);
            if (bothAxesAspect >= 1f)
            {
                width = longEdge;
                height = Math.Max(1, (int)Math.Round(longEdge / bothAxesAspect, MidpointRounding.AwayFromZero));
            }
            else
            {
                height = longEdge;
                width = Math.Max(1, (int)Math.Round(longEdge * bothAxesAspect, MidpointRounding.AwayFromZero));
            }
            aspect = width / (float)height;
            return true;
        }

        private static bool TrySolveHorizontalDistance(
            ProjectedAabb projected,
            double tanHalfFov,
            double margin,
            double aspect,
            out double distance)
        {
            distance = 0.0;
            double horizontalK = tanHalfFov * aspect * margin;
            return TrySolveAxis(
                projected.RightProjection,
                projected.DepthProjection,
                horizontalK,
                out distance,
                out _);
        }





        private static bool IsPositiveFinite(double value)
        {
            return !double.IsNaN(value) && !double.IsInfinity(value) && value > 0.0;
        }

        private static bool IsPositiveFinite(float value)
        {
            return !float.IsNaN(value) && !float.IsInfinity(value) && value > 0f;
        }

        public static bool TrySolveFramingForAabb(
            float[] cornersWorld,
            float[] cameraRightWorld,
            float[] cameraUpWorld,
            float[] cameraDirectionWorld,
            float fovDegrees,
            float aspect,
            float margin,
            int recenteringIterations,
            out float[] pivotWorld,
            out float size,
            out string failureReason)
        {
            pivotWorld = null;
            size = 0f;
            failureReason = string.Empty;
            if (!TryProjectAabb(
                    cornersWorld,
                    cameraRightWorld,
                    cameraUpWorld,
                    cameraDirectionWorld,
                    out ProjectedAabb projected,
                    out failureReason))
            {
                return false;
            }

            double tanHalfFov = Math.Tan(fovDegrees * 0.5 * Math.PI / 180.0);
            double kH = tanHalfFov * aspect * margin;
            double kV = tanHalfFov * margin;

            if (!TrySolveAxis(projected.RightProjection, projected.DepthProjection, kH, out double dH, out double deltaH))
            {
                failureReason = FailureNoValidBinding;
                return false;
            }
            if (!TrySolveAxis(projected.UpProjection, projected.DepthProjection, kV, out double dV, out double deltaV))
            {
                failureReason = FailureNoValidBinding;
                return false;
            }

            double dFinal = Math.Max(dH, dV);
            if (dV > dH)
            {
                deltaH = Recenter(projected.RightProjection, projected.DepthProjection, dFinal, deltaH, recenteringIterations);
            }
            else if (dH > dV)
            {
                deltaV = Recenter(projected.UpProjection, projected.DepthProjection, dFinal, deltaV, recenteringIterations);
            }

            pivotWorld = new float[3]
            {
                (float)(projected.CenterX
                    + deltaH * cameraRightWorld[0]
                    + deltaV * cameraUpWorld[0]),
                (float)(projected.CenterY
                    + deltaH * cameraRightWorld[1]
                    + deltaV * cameraUpWorld[1]),
                (float)(projected.CenterZ
                    + deltaH * cameraRightWorld[2]
                    + deltaV * cameraUpWorld[2]),
            };
            size = (float)(dFinal * Math.Sin(fovDegrees * 0.5 * Math.PI / 180.0));
            return true;
        }

        // Per-axis two-end binding solve. Returns the smallest valid
        // (D, delta) over all ordered corner pairs (i, j) for which
        // every other corner stays inside the ±K frame at depth D.
        private static bool TrySolveAxis(
            double[] proj, double[] hProj, double k,
            out double bestD, out double bestDelta)
        {
            bestD = double.PositiveInfinity;
            bestDelta = 0.0;
            bool found = false;
            const double DepthEpsilon = 1e-6;
            // The validity bound includes a small slack so the binding
            // pair's own corners (which sit exactly on |val|/depth = K)
            // do not fall out of the tolerance bound.
            double kBound = k * (1.0 + 1e-4) + 1e-6;
            for (int i = 0; i < 8; i++)
            {
                for (int j = 0; j < 8; j++)
                {
                    if (i == j) continue;
                    double d = (proj[i] - proj[j]) / (2.0 * k)
                             + (hProj[i] + hProj[j]) / 2.0;
                    if (d <= DepthEpsilon) continue;
                    double delta = (proj[i] + proj[j]) / 2.0
                                 + k * (hProj[i] - hProj[j]) / 2.0;
                    bool valid = true;
                    for (int kc = 0; kc < 8; kc++)
                    {
                        double depth = d - hProj[kc];
                        if (depth <= DepthEpsilon) { valid = false; break; }
                        double lateral = proj[kc] - delta;
                        double ratio = lateral / depth;
                        if (ratio > kBound || ratio < -kBound)
                        {
                            valid = false;
                            break;
                        }
                    }
                    if (valid && d < bestD)
                    {
                        bestD = d;
                        bestDelta = delta;
                        found = true;
                    }
                }
            }
            return found;
        }

        // Re-center the non-binding axis at the final D: pick the
        // current max- and min-projection corners (each at its own
        // depth) and shift delta so the lateral / depth projections
        // balance.  Iterate to handle corner swaps after the shift.
        private static double Recenter(
            double[] proj, double[] hProj, double dFinal,
            double delta, int iterations)
        {
            for (int iter = 0; iter < iterations; iter++)
            {
                double maxProj = double.NegativeInfinity;
                double minProj = double.PositiveInfinity;
                double depthAtMax = 1.0;
                double depthAtMin = 1.0;
                for (int k = 0; k < 8; k++)
                {
                    double depth = dFinal - hProj[k];
                    if (depth <= 0.0) continue;
                    double p = (proj[k] - delta) / depth;
                    if (p > maxProj) { maxProj = p; depthAtMax = depth; }
                    if (p < minProj) { minProj = p; depthAtMin = depth; }
                }
                double denom = 1.0 / depthAtMax + 1.0 / depthAtMin;
                if (denom <= 0.0) break;
                double shift = (maxProj + minProj) / denom;
                if (Math.Abs(shift) < 1e-9) break;
                delta += shift;
            }
            return delta;
        }

        private static void RequireLength3(float[] vec, string name)
        {
            if (vec == null || vec.Length != 3)
            {
                throw new ArgumentException(
                    $"{name} must be length 3.", name);
            }
        }

        private static float[] NormalizeOrFallback(double x, double y, double z)
        {
            double len = Math.Sqrt(x * x + y * y + z * z);
            if (len < 1e-9)
            {
                return new float[] { 0f, 0f, 1f };
            }
            double inv = 1.0 / len;
            return new float[] { (float)(x * inv), (float)(y * inv), (float)(z * inv) };
        }
    }
}
