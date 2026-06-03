using System;
using System.Collections.Generic;
using System.Linq;

namespace PrefabSentinel
{
    public readonly struct GeometryBoundsContributor
    {
        public GeometryBoundsContributor(
            string source, double[] center, double[] extents, bool includeWithTargetOnly)
        {
            Source = source;
            Center = center;
            Extents = extents;
            IncludeWithTargetOnly = includeWithTargetOnly;
        }

        public string Source { get; }
        public double[] Center { get; }
        public double[] Extents { get; }
        public bool IncludeWithTargetOnly { get; }

        public static GeometryBoundsContributor Target(
            string source, double[] center, double[] extents)
        {
            return new GeometryBoundsContributor(source, center, extents, true);
        }

        public static GeometryBoundsContributor Child(
            string source, double[] center, double[] extents)
        {
            return new GeometryBoundsContributor(source, center, extents, false);
        }
    }

    public sealed class GeometryBoundsResult
    {
        public bool Success { get; init; }
        public string Source { get; init; } = string.Empty;
        public double[] Center { get; init; } = Array.Empty<double>();
        public double[] Extents { get; init; } = Array.Empty<double>();
        public string ErrorCode { get; init; } = string.Empty;
    }

    public sealed class GeometryDistanceResult
    {
        public bool Success { get; init; }
        public double Distance { get; init; }
        public string ErrorCode { get; init; } = string.Empty;
    }

    public static class GeometryBoundsMath
    {
        private static readonly string[] AutoSourcePriority =
        {
            "renderer",
            "collider",
            "rect_transform",
        };

        private static readonly HashSet<string> SupportedSources = new()
        {
            "auto",
            "renderer",
            "collider",
            "rect_transform",
            "combined",
        };

        public static GeometryBoundsResult Aggregate(
            IEnumerable<GeometryBoundsContributor> contributors,
            string source,
            bool includeChildren)
        {
            if (!SupportedSources.Contains(source))
            {
                return new GeometryBoundsResult
                {
                    Success = false,
                    ErrorCode = "EDITOR_CTRL_BOUNDS_SOURCE_INVALID",
                };
            }

            List<GeometryBoundsContributor> eligible = contributors
                .Where(c => includeChildren || c.IncludeWithTargetOnly)
                .ToList();
            string selectedSource = source == "combined"
                ? "combined"
                : ResolveBoundsSource(eligible, source);
            if (selectedSource.Length == 0 || eligible.Count == 0)
            {
                return new GeometryBoundsResult
                {
                    Success = false,
                    ErrorCode = "EDITOR_CTRL_BOUNDS_UNAVAILABLE",
                };
            }

            List<GeometryBoundsContributor> selected = selectedSource == "combined"
                ? eligible
                : eligible.Where(c => c.Source == selectedSource).ToList();
            double minX = selected.Min(c => c.Center[0] - c.Extents[0]);
            double minY = selected.Min(c => c.Center[1] - c.Extents[1]);
            double minZ = selected.Min(c => c.Center[2] - c.Extents[2]);
            double maxX = selected.Max(c => c.Center[0] + c.Extents[0]);
            double maxY = selected.Max(c => c.Center[1] + c.Extents[1]);
            double maxZ = selected.Max(c => c.Center[2] + c.Extents[2]);

            return new GeometryBoundsResult
            {
                Success = true,
                Source = selectedSource,
                Center = new[] { (minX + maxX) / 2d, (minY + maxY) / 2d, (minZ + maxZ) / 2d },
                Extents = new[] { (maxX - minX) / 2d, (maxY - minY) / 2d, (maxZ - minZ) / 2d },
            };
        }

        public static GeometryDistanceResult MeasureDistance(
            double[] centerA,
            double[] extentsA,
            double[] centerB,
            double[] extentsB,
            string mode)
        {
            if (mode == "pivot" || mode == "center" || mode == "bounds_center")
            {
                return new GeometryDistanceResult
                {
                    Success = true,
                    Distance = Distance(centerA, centerB),
                };
            }
            if (mode == "bounds_nearest" || mode == "surface")
            {
                double dx = Math.Max(0d, Math.Abs(centerA[0] - centerB[0]) - extentsA[0] - extentsB[0]);
                double dy = Math.Max(0d, Math.Abs(centerA[1] - centerB[1]) - extentsA[1] - extentsB[1]);
                double dz = Math.Max(0d, Math.Abs(centerA[2] - centerB[2]) - extentsA[2] - extentsB[2]);
                return new GeometryDistanceResult
                {
                    Success = true,
                    Distance = Math.Sqrt(dx * dx + dy * dy + dz * dz),
                };
            }
            return new GeometryDistanceResult
            {
                Success = false,
                ErrorCode = "EDITOR_CTRL_DISTANCE_MODE_INVALID",
            };
        }

        public static bool IsSupportedSource(string source)
        {
            return SupportedSources.Contains(source);
        }

        private static string ResolveBoundsSource(
            List<GeometryBoundsContributor> contributors, string source)
        {
            if (source != "auto")
                return contributors.Any(c => c.Source == source) ? source : string.Empty;

            foreach (string candidate in AutoSourcePriority)
            {
                if (contributors.Any(c => c.Source == candidate))
                    return candidate;
            }
            return string.Empty;
        }

        private static double Distance(double[] a, double[] b)
        {
            double dx = a[0] - b[0];
            double dy = a[1] - b[1];
            double dz = a[2] - b[2];
            return Math.Sqrt(dx * dx + dy * dy + dz * dz);
        }
    }
}
