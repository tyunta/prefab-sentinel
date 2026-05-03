using System.Collections.Generic;
using UnityEditor;
using UnityEngine;

// Blend-shape enumeration and weight setter.
namespace PrefabSentinel
{
    public static partial class UnityEditorControlBridge
    {
        private static EditorControlResponse HandleGetBlendShapes(EditorControlRequest request)
        {
            if (string.IsNullOrEmpty(request.hierarchy_path))
                return BuildError("EDITOR_CTRL_MISSING_PATH", "hierarchy_path is required for get_blend_shapes");

            var go = GameObject.Find(request.hierarchy_path);
            if (go == null)
                return BuildError("EDITOR_CTRL_OBJECT_NOT_FOUND",
                    $"GameObject not found: {request.hierarchy_path}");

            var smr = go.GetComponent<SkinnedMeshRenderer>();
            if (smr == null)
                return BuildError("EDITOR_CTRL_NO_SMR",
                    $"No SkinnedMeshRenderer on: {request.hierarchy_path}");

            var mesh = smr.sharedMesh;
            if (mesh == null)
                return BuildError("EDITOR_CTRL_NO_SMR",
                    $"SkinnedMeshRenderer has no mesh: {request.hierarchy_path}");

            int count = mesh.blendShapeCount;
            var entries = new List<BlendShapeEntry>();
            string filter = request.filter ?? "";

            for (int i = 0; i < count; i++)
            {
                string shapeName = mesh.GetBlendShapeName(i);
                if (filter.Length > 0 && shapeName.IndexOf(filter, System.StringComparison.OrdinalIgnoreCase) < 0)
                    continue;
                entries.Add(new BlendShapeEntry
                {
                    index = i,
                    name = shapeName,
                    weight = smr.GetBlendShapeWeight(i),
                });
            }

            return BuildSuccess("EDITOR_CTRL_BLEND_SHAPES_OK",
                $"Found {entries.Count} blend shapes (total: {count})",
                data: new EditorControlData
                {
                    blend_shapes = entries.ToArray(),
                    total_entries = count,
                    renderer_path = GetRelativePath(go.transform, smr.transform),
                    read_only = true,
                    executed = true,
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

            var go = GameObject.Find(request.hierarchy_path);
            if (go == null)
                return BuildError("EDITOR_CTRL_OBJECT_NOT_FOUND",
                    $"GameObject not found: {request.hierarchy_path}");

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
