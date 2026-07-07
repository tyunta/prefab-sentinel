using System;
using System.Collections.Generic;
using UnityEngine;

// Shared renderer-framing bounds concern for editor frame and target screenshot capture.
namespace PrefabSentinel
{
    public static partial class UnityEditorControlBridge
    {
        private static bool TryResolveRendererFramingBounds(
            GameObject target,
            string boundsPolicy,
            out Bounds aggregated,
            out IList<ObjectCaptureFramingMath.RendererBoundsRecord> includedRecords,
            out IList<ObjectCaptureFramingMath.RendererBoundsRecord> excludedRecords)
        {
            aggregated = new Bounds(target.transform.position, Vector3.zero);
            includedRecords = new List<ObjectCaptureFramingMath.RendererBoundsRecord>();
            excludedRecords = new List<ObjectCaptureFramingMath.RendererBoundsRecord>();

            var records = new List<ObjectCaptureFramingMath.RendererBoundsRecord>();
            var bakedMeshes = new List<Mesh>();

            try
            {
                foreach (var renderer in target.GetComponentsInChildren<Renderer>(false))
                {
                    if (renderer == null || !renderer.enabled)
                        continue;

                    Bounds bounds;
                    if (renderer is SkinnedMeshRenderer skinned)
                    {
                        var mesh = new Mesh();
                        bakedMeshes.Add(mesh);
                        skinned.BakeMesh(mesh);
                        bounds = TransformBoundsToWorld(mesh.bounds, skinned.transform);
                    }
                    else
                    {
                        bounds = renderer.bounds;
                    }

                    records.Add(ToRendererBoundsRecord(bounds));
                }

                if (records.Count == 0)
                    return false;

                if (boundsPolicy == "all_visible_renderers")
                {
                    includedRecords = records;
                }
                else if (boundsPolicy == "focus_core")
                {
                    includedRecords = ObjectCaptureFramingMath.SelectFramingRenderers(records);
                    var excluded = new List<ObjectCaptureFramingMath.RendererBoundsRecord>();
                    foreach (var record in records)
                    {
                        if (!includedRecords.Contains(record))
                            excluded.Add(record);
                    }
                    excludedRecords = excluded;
                }
                else
                {
                    return false;
                }

                if (includedRecords.Count == 0)
                    return false;

                aggregated = ToBounds(includedRecords[0]);
                for (int i = 1; i < includedRecords.Count; i++)
                {
                    aggregated.Encapsulate(ToBounds(includedRecords[i]));
                }

                return true;
            }
            finally
            {
                foreach (var mesh in bakedMeshes)
                {
                    if (mesh != null)
                        UnityEngine.Object.DestroyImmediate(mesh);
                }
            }
        }

        private static ObjectCaptureFramingMath.RendererBoundsRecord ToRendererBoundsRecord(
            Bounds bounds)
        {
            return new ObjectCaptureFramingMath.RendererBoundsRecord(
                new[] { bounds.center.x, bounds.center.y, bounds.center.z },
                new[] { bounds.extents.x, bounds.extents.y, bounds.extents.z });
        }


        private static bool IsSupportedRendererBoundsPolicy(string boundsPolicy)
        {
            return boundsPolicy == "all_visible_renderers" || boundsPolicy == "focus_core";
        }

        private static EditorControlResponse BuildBoundsPolicyInvalidError(string boundsPolicy)
        {
            return BuildError(
                "EDITOR_CTRL_BOUNDS_POLICY_INVALID",
                $"bounds_policy='{boundsPolicy}' is not one of all_visible_renderers, focus_core.");
        }

        private static GeometryBoundsContributorEntry[] ToContributorEntries(
            IList<ObjectCaptureFramingMath.RendererBoundsRecord> records)
        {
            if (records == null || records.Count == 0)
                return Array.Empty<GeometryBoundsContributorEntry>();

            var entries = new List<GeometryBoundsContributorEntry>();
            foreach (var record in records)
            {
                Bounds bounds = ToBounds(record);
                entries.Add(new GeometryBoundsContributorEntry
                {
                    source = "renderer",
                    center = Vector3ToArray(bounds.center),
                    extents = Vector3ToArray(bounds.extents),
                    min = Vector3ToArray(bounds.min),
                    max = Vector3ToArray(bounds.max),
                });
            }
            return entries.ToArray();
        }

        private static Bounds TransformBoundsToWorld(Bounds local, Transform transform)
        {
            Vector3 c = local.center;
            Vector3 e = local.extents;
            Vector3[] corners = new Vector3[8];
            int idx = 0;
            for (int sx = -1; sx <= 1; sx += 2)
            for (int sy = -1; sy <= 1; sy += 2)
            for (int sz = -1; sz <= 1; sz += 2)
            {
                corners[idx++] = transform.TransformPoint(new Vector3(
                    c.x + sx * e.x, c.y + sy * e.y, c.z + sz * e.z));
            }

            Vector3 min = corners[0];
            Vector3 max = corners[0];
            for (int i = 1; i < 8; i++)
            {
                min = Vector3.Min(min, corners[i]);
                max = Vector3.Max(max, corners[i]);
            }

            return new Bounds((min + max) * 0.5f, max - min);
        }

        private static Bounds ToBounds(
            ObjectCaptureFramingMath.RendererBoundsRecord record)
        {
            return new Bounds(
                new Vector3(
                    record.CenterWorld[0],
                    record.CenterWorld[1],
                    record.CenterWorld[2]),
                new Vector3(
                    record.ExtentsWorld[0] * 2f,
                    record.ExtentsWorld[1] * 2f,
                    record.ExtentsWorld[2] * 2f));
        }
    }
}
