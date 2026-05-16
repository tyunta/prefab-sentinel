using System.Collections.Generic;
using UnityEditor;
using UnityEngine;

// Batch material-property writes layered over the single-write handler.
namespace PrefabSentinel
{
    public static partial class UnityEditorControlBridge
    {
        private static EditorControlResponse HandleEditorBatchSetMaterialProperty(EditorControlRequest request)
        {
            if (string.IsNullOrEmpty(request.batch_operations_json))
                return BuildError("EDITOR_CTRL_BATCH_SET_MAT_PROP_NO_DATA",
                    "batch_operations_json is required.");

            BatchSetMaterialPropertyArray wrapper;
            try
            {
                wrapper = JsonUtility.FromJson<BatchSetMaterialPropertyArray>(
                    "{\"items\":" + request.batch_operations_json + "}");
            }
            catch (System.Exception ex)
            {
                return BuildError("EDITOR_CTRL_BATCH_SET_MAT_PROP_JSON_ERROR",
                    $"Failed to parse batch_operations_json: {ex.Message}");
            }

            if (wrapper.items == null || wrapper.items.Length == 0)
                return BuildError("EDITOR_CTRL_BATCH_SET_MAT_PROP_EMPTY",
                    "batch_operations_json is empty.");

            bool hasHierarchy = !string.IsNullOrEmpty(request.hierarchy_path);
            bool hasMatPath = !string.IsNullOrEmpty(request.material_path);
            bool hasMatGuid = !string.IsNullOrEmpty(request.material_guid);

            if (hasHierarchy && (hasMatPath || hasMatGuid))
                return BuildError("EDITOR_CTRL_BATCH_SET_MAT_PROP_CONFLICT",
                    "Cannot specify both hierarchy_path and material_path/material_guid. Use one targeting mode.");

            if (!hasHierarchy && !hasMatPath && !hasMatGuid)
                return BuildError("EDITOR_CTRL_BATCH_SET_MAT_PROP_NO_TARGET",
                    "Specify a target: hierarchy_path + material_index, or material_path, or material_guid.");

            int undoGroup = Undo.GetCurrentGroup();
            Undo.SetCurrentGroupName("PrefabSentinel: Batch SetMaterialProperty");

            var results = new List<string>();

            if (hasHierarchy)
            {
                foreach (var op in wrapper.items)
                {
                    var subReq = new EditorControlRequest
                    {
                        action = "set_material_property",
                        hierarchy_path = request.hierarchy_path,
                        material_index = request.material_index,
                        property_name = op.name,
                        property_value = op.value,
                    };
                    var subResp = HandleSetMaterialProperty(subReq);
                    if (!subResp.success)
                    {
                        Undo.CollapseUndoOperations(undoGroup);
                        return BuildError("EDITOR_CTRL_BATCH_SET_MAT_PROP_FAILED",
                            $"Operation failed at index {results.Count}: {subResp.message}");
                    }
                    results.Add(op.name);
                }
            }
            else
            {
                if (hasMatPath && hasMatGuid)
                    return BuildError("EDITOR_CTRL_BATCH_SET_MAT_PROP_MAT_CONFLICT",
                        "Cannot specify both material_path and material_guid. Use one.");

                string guid = request.material_guid;
                if (hasMatPath)
                {
                    guid = AssetDatabase.AssetPathToGUID(request.material_path);
                    if (string.IsNullOrEmpty(guid))
                        return BuildError("EDITOR_CTRL_BATCH_SET_MAT_PROP_NOT_FOUND",
                            $"Material not found at path: {request.material_path}");
                }

                string assetPath = AssetDatabase.GUIDToAssetPath(guid);
                if (string.IsNullOrEmpty(assetPath))
                    return BuildError("EDITOR_CTRL_BATCH_SET_MAT_PROP_NOT_FOUND",
                        $"No asset found for GUID: {guid}");

                var mat = AssetDatabase.LoadAssetAtPath<Material>(assetPath);
                if (mat == null)
                    return BuildError("EDITOR_CTRL_BATCH_SET_MAT_PROP_NOT_FOUND",
                        $"Failed to load Material at: {assetPath}");

                var shader = mat.shader;
                if (shader == null)
                    return BuildError("EDITOR_CTRL_BATCH_SET_MAT_PROP_NOT_FOUND",
                        $"Material '{mat.name}' has no shader assigned.");

                Undo.RecordObject(mat, "PrefabSentinel: Batch SetMaterialProperty");

                foreach (var op in wrapper.items)
                {
                    var applyError = ApplyMaterialPropertyValue(mat, op.name, op.value);
                    if (applyError != null)
                    {
                        Undo.CollapseUndoOperations(undoGroup);
                        return BuildError("EDITOR_CTRL_BATCH_SET_MAT_PROP_FAILED",
                            $"Operation failed at index {results.Count}: {applyError.message}");
                    }
                    results.Add(op.name);
                }

                SceneView sv = SceneView.lastActiveSceneView;
                if (sv != null) ForceRenderAndRepaint(sv);
            }

            Undo.CollapseUndoOperations(undoGroup);

            var resp = BuildSuccess("EDITOR_CTRL_BATCH_SET_MAT_PROP_OK",
                $"Set {results.Count} material properties",
                data: new EditorControlData
                {
                    executed = true, read_only = false,
                    suggestions = results.ToArray(),
                });
            resp.diagnostics = new[] { new EditorControlDiagnostic
            {
                detail = "Runtime modification — save the scene (File > Save) to persist.",
                evidence = "Undo.CollapseUndoOperations"
            }};
            return resp;
        }
    }
}
