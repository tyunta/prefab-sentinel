using System;
using System.Collections.Generic;
using UnityEngine;

// Geometry endpoint handlers expose live transform, bounds, and distance reads.
namespace PrefabSentinel
{
    public static partial class UnityEditorControlBridge
    {
        private static EditorControlResponse HandleGetTransform(EditorControlRequest request)
        {
            if (!TryResolveGeometryObject(
                request.hierarchy_path, out GameObject go, out EditorControlResponse error))
            {
                return error;
            }

            Transform t = go.transform;
            Transform parent = t.parent;
            return BuildSuccess(
                "EDITOR_CTRL_TRANSFORM_OK",
                $"Read transform for {request.hierarchy_path}.",
                data: new EditorControlData
                {
                    hierarchy_path = request.hierarchy_path,
                    parent_path = parent != null ? BuildTransformPath(parent) : string.Empty,
                    local_position = Vector3ToArray(t.localPosition),
                    world_position = Vector3ToArray(t.position),
                    local_rotation_quat = QuaternionToArray(t.localRotation),
                    world_rotation_quat = QuaternionToArray(t.rotation),
                    local_euler = Vector3ToArray(t.localEulerAngles),
                    world_euler = Vector3ToArray(t.eulerAngles),
                    local_scale = Vector3ToArray(t.localScale),
                    lossy_scale = Vector3ToArray(t.lossyScale),
                    active_self = go.activeSelf,
                    active_in_hierarchy = go.activeInHierarchy,
                    read_only = true,
                    executed = true,
                });
        }

        private static EditorControlResponse HandleGetBounds(EditorControlRequest request)
        {
            if (!TryResolveGeometryObject(
                request.hierarchy_path, out GameObject go, out EditorControlResponse error))
            {
                return error;
            }

            List<GeometryContributorRecord> records = CollectGeometryContributors(
                go, request.include_children);
            GeometryBoundsResult result = GeometryBoundsMath.Aggregate(
                ToBoundsContributors(records),
                request.bounds_source,
                request.include_children);
            if (!result.Success)
            {
                return BuildError(
                    result.ErrorCode,
                    $"Bounds unavailable for {request.hierarchy_path} with source '{request.bounds_source}'.");
            }

            EditorControlResponse response = BuildSuccess(
                "EDITOR_CTRL_BOUNDS_OK",
                $"Read bounds for {request.hierarchy_path}.",
                data: BuildBoundsData(
                    request.hierarchy_path,
                    result.Source,
                    request.include_children,
                    result,
                    SelectContributorEntries(records, result.Source, request.include_children)));
            if (IsEmptyBounds(result))
            {
                response.severity = "warning";
                response.diagnostics = new[]
                {
                    new EditorControlDiagnostic
                    {
                        code = "EDITOR_CTRL_EMPTY_BOUNDS_ONLY",
                        severity = "warning",
                        path = request.hierarchy_path,
                        detail = "Selected bounds contributors produced a zero-size world-space AABB.",
                    },
                };
            }
            return response;
        }

        private static EditorControlResponse HandleMeasureDistance(EditorControlRequest request)
        {
            if (!TryResolveGeometryObject(
                request.hierarchy_path, out GameObject a, out EditorControlResponse errorA))
            {
                return errorA;
            }
            if (!TryResolveGeometryObject(
                request.target_path, out GameObject b, out EditorControlResponse errorB))
            {
                return errorB;
            }

            EditorControlResponse boundsSourceError = ValidateBoundsSourceSelector(
                request.bounds_source);
            if (boundsSourceError != null) return boundsSourceError;

            if (request.distance_mode == "pivot")
            {
                GeometryDistanceResult pivotResult = GeometryBoundsMath.MeasureDistance(
                    Vector3ToDoubleArray(a.transform.position),
                    new[] { 0d, 0d, 0d },
                    Vector3ToDoubleArray(b.transform.position),
                    new[] { 0d, 0d, 0d },
                    request.distance_mode);
                return BuildDistanceResponse(
                    request,
                    pivotResult,
                    a.transform.position,
                    b.transform.position,
                    null,
                    null);
            }

            GeometryBoundsResult boundsA = ResolveBoundsForDistance(
                a, request.bounds_source, out EditorControlResponse boundsErrorA);
            if (boundsErrorA != null) return boundsErrorA;
            GeometryBoundsResult boundsB = ResolveBoundsForDistance(
                b, request.bounds_source, out EditorControlResponse boundsErrorB);
            if (boundsErrorB != null) return boundsErrorB;

            GeometryDistanceResult distanceResult = GeometryBoundsMath.MeasureDistance(
                boundsA.Center,
                boundsA.Extents,
                boundsB.Center,
                boundsB.Extents,
                request.distance_mode);
            return BuildDistanceResponse(
                request,
                distanceResult,
                DoubleArrayToVector3(boundsA.Center),
                DoubleArrayToVector3(boundsB.Center),
                boundsA,
                boundsB);
        }

        private static bool TryResolveGeometryObject(
            string hierarchyPath,
            out GameObject go,
            out EditorControlResponse error)
        {
            go = null;
            error = null;
            if (!TryResolveGameObjectInActiveStage(
                hierarchyPath, out go, out EditorControlResponse ambiguity))
            {
                if (ambiguity != null)
                {
                    error = ambiguity;
                    return false;
                }
                error = BuildError(
                    "EDITOR_CTRL_TRANSFORM_TARGET_NOT_FOUND",
                    $"hierarchy_path '{hierarchyPath}' matched no GameObject.");
                return false;
            }
            return true;
        }

        private static EditorControlResponse ValidateBoundsSourceSelector(string boundsSource)
        {
            if (GeometryBoundsMath.IsSupportedSource(boundsSource)) return null;
            return BuildError(
                "EDITOR_CTRL_BOUNDS_SOURCE_INVALID",
                $"bounds_source '{boundsSource}' is not supported.");
        }

        private static GeometryBoundsResult ResolveBoundsForDistance(
            GameObject go,
            string boundsSource,
            out EditorControlResponse error)
        {
            error = null;
            GeometryBoundsResult result = GeometryBoundsMath.Aggregate(
                ToBoundsContributors(CollectGeometryContributors(go, includeChildren: true)),
                boundsSource,
                includeChildren: true);
            if (!result.Success)
            {
                error = BuildError(
                    result.ErrorCode,
                    $"Bounds unavailable for {go.name} with source '{boundsSource}'.");
            }
            return result;
        }

        private static EditorControlResponse BuildDistanceResponse(
            EditorControlRequest request,
            GeometryDistanceResult result,
            Vector3 pointA,
            Vector3 pointB,
            GeometryBoundsResult boundsA,
            GeometryBoundsResult boundsB)
        {
            if (!result.Success)
            {
                return BuildError(
                    result.ErrorCode,
                    $"distance_mode '{request.distance_mode}' is not supported.");
            }

            EditorControlData data = new EditorControlData
            {
                hierarchy_path = request.hierarchy_path,
                target_path = request.target_path,
                distance_mode = request.distance_mode,
                bounds_source = request.bounds_source,
                distance = (float)result.Distance,
                from_point = Vector3ToArray(pointA),
                to_point = Vector3ToArray(pointB),
                read_only = true,
                executed = true,
            };
            if (boundsA != null)
            {
                data.bounds_center = ToFloatArray(boundsA.Center);
                data.bounds_extents = ToFloatArray(boundsA.Extents);
            }
            if (boundsB != null)
            {
                data.target_bounds_center = ToFloatArray(boundsB.Center);
                data.target_bounds_extents = ToFloatArray(boundsB.Extents);
            }
            return BuildSuccess(
                "EDITOR_CTRL_DISTANCE_OK",
                $"Measured {request.distance_mode} distance from {request.hierarchy_path} to {request.target_path}.",
                data: data);
        }

        private static EditorControlData BuildBoundsData(
            string hierarchyPath,
            string source,
            bool includeChildren,
            GeometryBoundsResult result,
            GeometryBoundsContributorEntry[] contributors)
        {
            Vector3 center = DoubleArrayToVector3(result.Center);
            Vector3 extents = DoubleArrayToVector3(result.Extents);
            Vector3 size = extents * 2f;
            Vector3 min = center - extents;
            Vector3 max = center + extents;
            return new EditorControlData
            {
                hierarchy_path = hierarchyPath,
                bounds_source = source,
                include_children = includeChildren,
                bounds_center = Vector3ToArray(center),
                bounds_extents = Vector3ToArray(extents),
                bounds_size = Vector3ToArray(size),
                bounds_min = Vector3ToArray(min),
                bounds_max = Vector3ToArray(max),
                contributor_count = contributors.Length,
                bounds_contributors = contributors,
                read_only = true,
                executed = true,
            };
        }

        private static bool IsEmptyBounds(GeometryBoundsResult result)
        {
            return result.Extents[0] == 0d
                && result.Extents[1] == 0d
                && result.Extents[2] == 0d;
        }

    }
}
