using System;
using System.Collections.Generic;
using UnityEditor;
using UnityEngine;

// Blend-shape enumeration and weight setter.
namespace PrefabSentinel
{
    public static partial class UnityEditorControlBridge
    {
        [Serializable]
        private sealed class BatchBlendShapeEntry
        {
            public string name = string.Empty;
            public float weight = 0f;
        }

        [Serializable]
        private sealed class BatchBlendShapeArray
        {
            public BatchBlendShapeEntry[] items = Array.Empty<BatchBlendShapeEntry>();
        }

        private static EditorControlResponse HandleGetBlendShapes(EditorControlRequest request)
        {
            if (string.IsNullOrEmpty(request.hierarchy_path))
                return BuildError("EDITOR_CTRL_MISSING_PATH", "hierarchy_path is required for get_blend_shapes");

            // Issue #241: mirror the Python-side pagination range gate so
            // an out-of-range request reaching the bridge by direct
            // transport (no wrapper) is rejected with the same code.
            if (request.offset < 0 || request.limit < 1 || request.limit > 1000)
                return BuildError(
                    "BLEND_SHAPE_PAGINATION_OUT_OF_RANGE",
                    $"offset={request.offset} or limit={request.limit} is out of range " +
                    "(offset>=0, 1<=limit<=1000).");

            if (!TryResolveGameObjectInActiveStage(
                request.hierarchy_path, out GameObject go, out var ambiguity))
            {
                if (ambiguity != null) return ambiguity;
                return BuildError("EDITOR_CTRL_OBJECT_NOT_FOUND",
                    $"GameObject not found: {request.hierarchy_path}");
            }

            var smr = go.GetComponent<SkinnedMeshRenderer>();
            if (smr == null)
                return BuildError("EDITOR_CTRL_NO_SMR",
                    $"No SkinnedMeshRenderer on: {request.hierarchy_path}");

            var mesh = smr.sharedMesh;
            if (mesh == null)
                return BuildError("EDITOR_CTRL_NO_SMR",
                    $"SkinnedMeshRenderer has no mesh: {request.hierarchy_path}");

            int count = mesh.blendShapeCount;
            string filter = request.filter ?? "";

            // Issue #241: collect filtered matches first so pagination
            // counts the post-filter total, then slice on offset/limit
            // so the continuation token corresponds to the matching set.
            var matches = new List<BlendShapeEntry>();
            for (int i = 0; i < count; i++)
            {
                string shapeName = mesh.GetBlendShapeName(i);
                if (filter.Length > 0 && shapeName.IndexOf(filter, System.StringComparison.OrdinalIgnoreCase) < 0)
                    continue;
                matches.Add(new BlendShapeEntry
                {
                    index = i,
                    name = shapeName,
                    weight = smr.GetBlendShapeWeight(i),
                });
            }
            int total = matches.Count;
            int start = Mathf.Clamp(request.offset, 0, total);
            int end = Mathf.Min(start + request.limit, total);
            var page = matches.GetRange(start, Mathf.Max(0, end - start));
            string nextCursor = end < total ? end.ToString() : string.Empty;

            return BuildSuccess("EDITOR_CTRL_BLEND_SHAPES_OK",
                $"Found {page.Count} blend shapes (filter total: {total})",
                data: new EditorControlData
                {
                    blend_shapes = page.ToArray(),
                    total_entries = total,
                    next_cursor = nextCursor,
                    renderer_path = GetRelativePath(go.transform, smr.transform),
                    read_only = true,
                    executed = true,
                });
        }

        private static EditorControlResponse HandleBatchSetBlendShape(EditorControlRequest request)
        {
            if (string.IsNullOrEmpty(request.hierarchy_path))
                return BuildError("EDITOR_CTRL_MISSING_PATH",
                    "hierarchy_path is required for batch_set_blend_shape");
            if (string.IsNullOrEmpty(request.shapes_json))
                return BuildError("EDITOR_CTRL_BATCH_BLEND_SHAPE_PARSE",
                    "shapes_json is required for batch_set_blend_shape");

            BatchBlendShapeArray parsed;
            try
            {
                parsed = JsonUtility.FromJson<BatchBlendShapeArray>(
                    "{\"items\":" + request.shapes_json + "}");
            }
            catch (Exception ex)
            {
                return BuildError("EDITOR_CTRL_BATCH_BLEND_SHAPE_PARSE",
                    $"shapes_json parse failed: {ex.Message}");
            }
            if (!TryResolveGameObjectInActiveStage(
                request.hierarchy_path, out GameObject go, out var ambiguity))
            {
                if (ambiguity != null) return ambiguity;
                return BuildError("EDITOR_CTRL_OBJECT_NOT_FOUND",
                    $"GameObject not found: {request.hierarchy_path}");
            }
            var smr = go.GetComponent<SkinnedMeshRenderer>();
            if (smr == null)
                return BuildError("EDITOR_CTRL_NO_SMR",
                    $"No SkinnedMeshRenderer on: {request.hierarchy_path}");
            var mesh = smr.sharedMesh;
            if (mesh == null)
                return BuildError("EDITOR_CTRL_NO_SMR",
                    $"SkinnedMeshRenderer has no mesh: {request.hierarchy_path}");

            var items = parsed.items ?? Array.Empty<BatchBlendShapeEntry>();
            int undoGroup = Undo.GetCurrentGroup();
            Undo.SetCurrentGroupName(
                $"PrefabSentinel: batch_set_blend_shape ({items.Length} shapes)");
            Undo.RecordObject(smr, "batch_set_blend_shape");
            int setCount = 0;
            var failed = new List<BatchBlendShapeFailure>();
            foreach (var entry in items)
            {
                int index = mesh.GetBlendShapeIndex(entry.name);
                if (index < 0)
                {
                    failed.Add(new BatchBlendShapeFailure
                    {
                        name = entry.name,
                        reason = "blend shape not found on mesh",
                    });
                    continue;
                }
                smr.SetBlendShapeWeight(index, Mathf.Clamp(entry.weight, 0f, 100f));
                setCount++;
            }
            Undo.CollapseUndoOperations(undoGroup);
            return BuildSuccess(
                "EDITOR_CTRL_BATCH_BLEND_SHAPE_OK",
                $"batch_set_blend_shape: set {setCount}, failed {failed.Count}.",
                data: new EditorControlData
                {
                    executed = true,
                    set_count = setCount,
                    failed_shapes = failed.ToArray(),
                });
        }

        /// <summary>Returns the relative path from root to target (or target name if same).</summary>
        private static string GetRelativePath(Transform root, Transform target)
        {
            if (root == target) return target.name;
            var parts = new List<string>();
            var current = target;
            while (current != null && current != root)
            {
                parts.Add(current.name);
                current = current.parent;
            }
            parts.Reverse();
            return string.Join("/", parts);
        }

        private static EditorControlResponse HandleSetBlendShape(EditorControlRequest request)
        {
            if (string.IsNullOrEmpty(request.hierarchy_path))
                return BuildError("EDITOR_CTRL_MISSING_PATH", "hierarchy_path is required for set_blend_shape");
            if (string.IsNullOrEmpty(request.blend_shape_name))
                return BuildError("EDITOR_CTRL_MISSING_PROPERTY", "blend_shape_name is required for set_blend_shape");

            if (!TryResolveGameObjectInActiveStage(
                request.hierarchy_path, out GameObject go, out var ambiguity))
            {
                if (ambiguity != null) return ambiguity;
                return BuildError("EDITOR_CTRL_OBJECT_NOT_FOUND",
                    $"GameObject not found: {request.hierarchy_path}");
            }

            var smr = go.GetComponent<SkinnedMeshRenderer>();
            if (smr == null)
                return BuildError("EDITOR_CTRL_NO_SMR",
                    $"No SkinnedMeshRenderer on: {request.hierarchy_path}");

            var mesh = smr.sharedMesh;
            if (mesh == null)
                return BuildError("EDITOR_CTRL_NO_SMR",
                    $"SkinnedMeshRenderer has no mesh: {request.hierarchy_path}");

            int index = mesh.GetBlendShapeIndex(request.blend_shape_name);
            if (index < 0)
                return BuildError("EDITOR_CTRL_BLENDSHAPE_NOT_FOUND",
                    $"BlendShape not found: {request.blend_shape_name}");

            float before = smr.GetBlendShapeWeight(index);
            float weight = Mathf.Clamp(request.blend_shape_weight, 0f, 100f);

            Undo.RecordObject(smr, $"Set BlendShape {request.blend_shape_name}");
            smr.SetBlendShapeWeight(index, weight);

            SceneView sv = SceneView.lastActiveSceneView;
            if (sv != null) ForceRenderAndRepaint(sv);

            var resp = BuildSuccess("EDITOR_CTRL_BLEND_SHAPE_SET_OK",
                $"BlendShape '{request.blend_shape_name}' set from {before} to {weight}",
                data: new EditorControlData
                {
                    blend_shape_index = index,
                    blend_shape_name = request.blend_shape_name,
                    blend_shape_before = before,
                    blend_shape_after = weight,
                    executed = true,
                });
            resp.diagnostics = new[] { new EditorControlDiagnostic
            {
                detail = "Runtime modification — save the scene (File > Save) to persist.",
                evidence = "Undo.RecordObject"
            }};
            return resp;
        }
    }
}
