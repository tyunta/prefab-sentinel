using System;
using System.Collections.Generic;
using UnityEditor;
using UnityEngine;

// Property-write handlers — the editor_set_property handler and its
// batch variant. Per-type value application is owned by the unified
// property-write layer in PrefabSentinel.UnityEditorControlBridge.PropertyWrite.cs.
namespace PrefabSentinel
{
    public static partial class UnityEditorControlBridge
    {
        private static EditorControlResponse HandleEditorSetProperty(EditorControlRequest request)
        {
            if (string.IsNullOrEmpty(request.hierarchy_path))
                return BuildError("EDITOR_CTRL_SET_PROP_NO_PATH", "hierarchy_path is required.");
            if (string.IsNullOrEmpty(request.component_type))
                return BuildError("EDITOR_CTRL_SET_PROP_NO_COMP", "component_type is required.");
            if (string.IsNullOrEmpty(request.property_name))
                return BuildError("EDITOR_CTRL_SET_PROP_NO_FIELD", "property_name is required.");

            bool hasValue = request.property_value_present
                || !string.IsNullOrEmpty(request.property_value);
            bool hasRef = !string.IsNullOrEmpty(request.object_reference);
            if (!hasValue && !hasRef)
                return BuildError("EDITOR_CTRL_SET_PROP_NO_VALUE",
                    "Either property_value or object_reference is required.");
            if (hasValue && hasRef)
                return BuildError("EDITOR_CTRL_SET_PROP_BOTH_VALUE",
                    "Provide property_value or object_reference, not both.");

            if (!TryResolveGameObjectInActiveStage(
                    request.hierarchy_path, out GameObject go,
                    out EditorControlResponse ambiguity))
            {
                if (ambiguity != null)
                    return ambiguity;
                return BuildError("EDITOR_CTRL_SET_PROP_NOT_FOUND",
                    $"GameObject not found: {request.hierarchy_path}");
            }

            bool gameObjectTarget =
                string.Equals(request.component_type, "GameObject", StringComparison.Ordinal);

            SerializedObject so;
            if (gameObjectTarget)
            {
                if (!GameObjectPropertyAllowlist.IsAllowed(request.property_name))
                {
                    string allowed = string.Join(
                        ", ", GameObjectPropertyAllowlist.AllowedProperties);
                    return BuildError("EDITOR_CTRL_SET_PROP_GAMEOBJECT_PROP_NOT_ALLOWED",
                        $"GameObject-level property '{request.property_name}' is not allowed. " +
                        $"Allowed: {allowed}.");
                }
                so = new SerializedObject(go);
            }
            else
            {
                System.Type compType = ResolveComponentType(request.component_type);
                if (compType == null)
                    return BuildError("EDITOR_CTRL_SET_PROP_COMP_NOT_FOUND",
                        $"Component type not found: {request.component_type}");

                var component = go.GetComponent(compType);
                if (component == null)
                    return BuildError("EDITOR_CTRL_SET_PROP_COMP_NOT_FOUND",
                        $"Component {request.component_type} not found on {request.hierarchy_path}");

                so = new SerializedObject(component);
            }

            var prop = so.FindProperty(request.property_name);
            if (prop == null)
            {
                var candidates = new List<string>();
                var iter = so.GetIterator();
                if (iter.NextVisible(true))
                {
                    do
                    {
                        candidates.Add(iter.propertyPath);
                    } while (iter.NextVisible(false));
                }
                string[] suggestions = SuggestionRanker.SuggestSimilar(
                    request.property_name, candidates, maxResults: 5);
                var data = new EditorControlData();
                data.suggestions = suggestions.Length > 0
                    ? suggestions
                    : Array.Empty<string>();
                string baseMessage = gameObjectTarget
                    ? $"Property not found: {request.property_name} on GameObject"
                    : $"Property not found: {request.property_name} on {request.component_type}";
                string message = data.suggestions.Length > 0
                    ? $"{baseMessage}. Did you mean: {string.Join(", ", data.suggestions)}?"
                    : baseMessage;
                return BuildError("EDITOR_CTRL_SET_PROP_FIELD_NOT_FOUND", message, data);
            }

            string writeInput = hasRef ? request.object_reference : request.property_value;
            PropertyWriteResult writeResult = WritePropertyValue(prop, writeInput);
            if (!writeResult.Success)
            {
                if (writeResult.ErrorData != null)
                    return BuildError(writeResult.ErrorCode, writeResult.ErrorMessage, writeResult.ErrorData);
                return BuildError(writeResult.ErrorCode, writeResult.ErrorMessage);
            }

            so.ApplyModifiedProperties();

            var resp = BuildSuccess("EDITOR_CTRL_SET_PROP_OK",
                $"Set {request.property_name} on {request.component_type} at {request.hierarchy_path}",
                data: new EditorControlData
                {
                    selected_object = go.name,
                    executed = true,
                    read_only = false,
                });
            resp.diagnostics = new[] { new EditorControlDiagnostic
            {
                detail = $"Property type: {prop.propertyType}. Save the scene to persist.",
                evidence = "SerializedObject.ApplyModifiedProperties"
            }};
            return resp;
        }

        private static EditorControlResponse HandleEditorBatchSetProperty(EditorControlRequest request)
        {
            if (string.IsNullOrEmpty(request.batch_operations_json))
                return BuildError("EDITOR_CTRL_BATCH_SET_NO_DATA", "batch_operations_json is required.");

            BatchSetPropertyArray wrapper;
            try
            {
                wrapper = JsonUtility.FromJson<BatchSetPropertyArray>(
                    "{\"items\":" + request.batch_operations_json + "}");
            }
            catch (System.Exception ex)
            {
                return BuildError("EDITOR_CTRL_BATCH_SET_JSON_ERROR",
                    $"Failed to parse batch_operations_json: {ex.Message}");
            }

            if (wrapper.items == null || wrapper.items.Length == 0)
                return BuildError("EDITOR_CTRL_BATCH_SET_EMPTY", "batch_operations_json is empty.");

            int undoGroup = Undo.GetCurrentGroup();
            Undo.SetCurrentGroupName("PrefabSentinel: Batch SetProperty");

            var results = new List<string>();

            foreach (var op in wrapper.items)
            {
                var subReq = new EditorControlRequest
                {
                    action = "editor_set_property",
                    hierarchy_path = op.hierarchy_path,
                    component_type = op.component_type,
                    property_name = op.property_name,
                    property_value = op.value,
                    // Issue #52: forward the per-op value-present marker so
                    // an empty-string op value is applied by the delegated
                    // HandleEditorSetProperty instead of rejected as "no
                    // value".
                    property_value_present = op.value_present,
                    object_reference = op.object_reference,
                };
                var subResp = HandleEditorSetProperty(subReq);
                if (!subResp.success)
                {
                    Undo.CollapseUndoOperations(undoGroup);
                    return BuildError("EDITOR_CTRL_BATCH_SET_FAILED",
                        $"Operation failed at index {results.Count}: {subResp.message}");
                }
                results.Add($"{op.hierarchy_path}/{op.component_type}.{op.property_name}");
            }

            Undo.CollapseUndoOperations(undoGroup);

            var batchSetResp = BuildSuccess("EDITOR_CTRL_BATCH_SET_OK",
                $"Set {results.Count} properties",
                data: new EditorControlData
                {
                    executed = true,
                    read_only = false,
                    suggestions = results.ToArray(),
                });
            batchSetResp.diagnostics = new[] { new EditorControlDiagnostic
            {
                detail = "Runtime modification — save the scene (File > Save) to persist.",
                evidence = "Undo.CollapseUndoOperations"
            }};
            return batchSetResp;
        }
    }
}
