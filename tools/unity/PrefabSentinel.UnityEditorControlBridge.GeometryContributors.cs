using System.Collections.Generic;
using UnityEngine;

// Geometry contributor collection and numeric conversion helpers shared by geometry and UI screenshot handlers.
namespace PrefabSentinel
{
    public static partial class UnityEditorControlBridge
    {
        private static List<GeometryContributorRecord> CollectGeometryContributors(
            GameObject root,
            bool includeChildren)
        {
            var records = new List<GeometryContributorRecord>();
            AddRendererContributors(root, includeChildren, records);
            AddColliderContributors(root, includeChildren, records);
            AddRectTransformContributors(root, includeChildren, records);
            return records;
        }

        private static void AddRendererContributors(
            GameObject root,
            bool includeChildren,
            List<GeometryContributorRecord> records)
        {
            Renderer[] renderers = includeChildren
                ? root.GetComponentsInChildren<Renderer>(includeInactive: false)
                : root.GetComponents<Renderer>();
            foreach (Renderer renderer in renderers)
            {
                if (!renderer.enabled || !renderer.gameObject.activeInHierarchy) continue;
                records.Add(GeometryContributorRecord.FromBounds(
                    "renderer", renderer.gameObject, root, renderer.bounds));
            }
        }

        private static void AddColliderContributors(
            GameObject root,
            bool includeChildren,
            List<GeometryContributorRecord> records)
        {
            Collider[] colliders = includeChildren
                ? root.GetComponentsInChildren<Collider>(includeInactive: false)
                : root.GetComponents<Collider>();
            foreach (Collider collider in colliders)
            {
                if (!collider.enabled || !collider.gameObject.activeInHierarchy) continue;
                records.Add(GeometryContributorRecord.FromBounds(
                    "collider", collider.gameObject, root, collider.bounds));
            }
        }

        private static void AddRectTransformContributors(
            GameObject root,
            bool includeChildren,
            List<GeometryContributorRecord> records)
        {
            RectTransform[] rectTransforms = includeChildren
                ? root.GetComponentsInChildren<RectTransform>(includeInactive: false)
                : root.GetComponents<RectTransform>();
            foreach (RectTransform rectTransform in rectTransforms)
            {
                if (!rectTransform.gameObject.activeInHierarchy) continue;
                records.Add(GeometryContributorRecord.FromBounds(
                    "rect_transform",
                    rectTransform.gameObject,
                    root,
                    BoundsFromRectTransform(rectTransform)));
            }
        }

        private static Bounds BoundsFromRectTransform(RectTransform rectTransform)
        {
            var corners = new Vector3[4];
            rectTransform.GetWorldCorners(corners);
            Vector3 min = corners[0];
            Vector3 max = corners[0];
            for (int i = 1; i < corners.Length; i++)
            {
                min = Vector3.Min(min, corners[i]);
                max = Vector3.Max(max, corners[i]);
            }
            return new Bounds((min + max) * 0.5f, max - min);
        }

        private static IEnumerable<GeometryBoundsContributor> ToBoundsContributors(
            IEnumerable<GeometryContributorRecord> records)
        {
            foreach (GeometryContributorRecord record in records)
            {
                if (record.IsTarget)
                {
                    yield return GeometryBoundsContributor.Target(
                        record.Source,
                        Vector3ToDoubleArray(record.Bounds.center),
                        Vector3ToDoubleArray(record.Bounds.extents));
                }
                else
                {
                    yield return GeometryBoundsContributor.Child(
                        record.Source,
                        Vector3ToDoubleArray(record.Bounds.center),
                        Vector3ToDoubleArray(record.Bounds.extents));
                }
            }
        }

        private static GeometryBoundsContributorEntry[] SelectContributorEntries(
            IEnumerable<GeometryContributorRecord> records,
            string source,
            bool includeChildren)
        {
            var entries = new List<GeometryBoundsContributorEntry>();
            foreach (GeometryContributorRecord record in records)
            {
                if (source != "auto" && record.Source != source) continue;
                if (!includeChildren && !record.IsTarget) continue;
                entries.Add(record.ToEntry());
            }
            return entries.ToArray();
        }

        private static string BuildTransformPath(Transform transform)
        {
            var parts = new List<string>();
            Transform current = transform;
            while (current != null)
            {
                parts.Add(current.name);
                current = current.parent;
            }
            parts.Reverse();
            return "/" + string.Join("/", parts);
        }

        private static float[] Vector3ToArray(Vector3 value)
        {
            return new[] { value.x, value.y, value.z };
        }

        private static float[] QuaternionToArray(Quaternion value)
        {
            return new[] { value.x, value.y, value.z, value.w };
        }

        private static double[] Vector3ToDoubleArray(Vector3 value)
        {
            return new[] { (double)value.x, (double)value.y, (double)value.z };
        }

        private static Vector3 DoubleArrayToVector3(double[] value)
        {
            return new Vector3((float)value[0], (float)value[1], (float)value[2]);
        }

        private static float[] ToFloatArray(double[] value)
        {
            return new[] { (float)value[0], (float)value[1], (float)value[2] };
        }

        private readonly struct GeometryContributorRecord
        {
            public GeometryContributorRecord(
                string source,
                GameObject go,
                GameObject root,
                Bounds bounds)
            {
                Source = source;
                GameObject = go;
                IsTarget = go == root;
                Bounds = bounds;
            }

            public string Source { get; }
            public GameObject GameObject { get; }
            public bool IsTarget { get; }
            public Bounds Bounds { get; }

            public static GeometryContributorRecord FromBounds(
                string source,
                GameObject go,
                GameObject root,
                Bounds bounds)
            {
                return new GeometryContributorRecord(source, go, root, bounds);
            }

            public GeometryBoundsContributorEntry ToEntry()
            {
                return new GeometryBoundsContributorEntry
                {
                    source = Source,
                    hierarchy_path = BuildTransformPath(GameObject.transform),
                    target = IsTarget,
                    center = Vector3ToArray(Bounds.center),
                    extents = Vector3ToArray(Bounds.extents),
                    min = Vector3ToArray(Bounds.min),
                    max = Vector3ToArray(Bounds.max),
                };
            }
        }
    }
}
