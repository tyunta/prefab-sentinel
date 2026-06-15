using System.Collections.Generic;
using UnityEngine;

// Shared renderer-framing bounds concern for editor frame and target screenshot capture.
namespace PrefabSentinel
{
    public static partial class UnityEditorControlBridge
    {
        private static bool TryResolveRendererFramingBounds(
            GameObject target,
            out Bounds aggregated,
            out IList<ObjectCaptureFramingMath.RendererBoundsRecord> keptRecords)
        {
            aggregated = new Bounds(target.transform.position, Vector3.zero);
            keptRecords = new List<ObjectCaptureFramingMath.RendererBoundsRecord>();

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

                keptRecords = ObjectCaptureFramingMath.SelectFramingRenderers(records);
                if (keptRecords.Count == 0)
                    return false;

                aggregated = ToBounds(keptRecords[0]);
                for (int i = 1; i < keptRecords.Count; i++)
                {
                    aggregated.Encapsulate(ToBounds(keptRecords[i]));
                }

                return true;
            }
            finally
            {
                foreach (var mesh in bakedMeshes)
                {
                    if (mesh != null)
                        Object.DestroyImmediate(mesh);
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
