using System;
using System.Collections.Generic;
using UnityEditor;
using UnityEngine;

namespace PrefabSentinel
{
    public static partial class UnityEditorControlBridge
    {
        private static EditorControlResponse HandleEditorSetProperty(EditorControlRequest request)
        {
            // ── Validation ──
            if (string.IsNullOrEmpty(request.hierarchy_path))
                return BuildError("EDITOR_CTRL_SET_PROP_NO_PATH", "hierarchy_path is required.");
            if (string.IsNullOrEmpty(request.component_type))
                return BuildError("EDITOR_CTRL_SET_PROP_NO_COMP", "component_type is required.");
            if (string.IsNullOrEmpty(request.property_name))
                return BuildError("EDITOR_CTRL_SET_PROP_NO_FIELD", "property_name is required.");

            bool hasValue = !string.IsNullOrEmpty(request.property_value);
            bool hasRef = !string.IsNullOrEmpty(request.object_reference);
            if (!hasValue && !hasRef)
                return BuildError("EDITOR_CTRL_SET_PROP_NO_VALUE",
                    "Either property_value or object_reference is required.");
            if (hasValue && hasRef)
                return BuildError("EDITOR_CTRL_SET_PROP_BOTH_VALUE",
                    "Provide property_value or object_reference, not both.");

            // ── Resolve target ──
            var go = GameObject.Find(request.hierarchy_path);
            if (go == null)
                return BuildError("EDITOR_CTRL_SET_PROP_NOT_FOUND",
                    $"GameObject not found: {request.hierarchy_path}");

            // GameObject-as-target branch: when the caller addresses the
            // GameObject itself (not a component on it), the SerializedObject
            // is constructed directly from the GameObject and writes are
            // restricted to the small allowlist of GameObject-level fields.
            bool gameObjectTarget =
                string.Equals(request.component_type, "GameObject", StringComparison.Ordinal);

            // GameObject-level serialized properties accepted by the
            // GameObject-as-target branch. Writes to anything outside this
            // allowlist return EDITOR_CTRL_SET_PROP_GAMEOBJECT_PROP_NOT_ALLOWED.
            string[] gameObjectAllowedProperties =
                new[] { "m_IsActive", "m_Layer", "m_Name", "m_TagString" };

            SerializedObject so;
            if (gameObjectTarget)
            {
                if (Array.IndexOf(gameObjectAllowedProperties, request.property_name) < 0)
                {
                    string allowed = string.Join(", ", gameObjectAllowedProperties);
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

            // ── Find property ──
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
                string[] suggestions = SuggestSimilar(
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

            // ── Set value by type ──
            string v = request.property_value;
            try
            {
                switch (prop.propertyType)
                {
                    case SerializedPropertyType.Integer:
                        prop.intValue = int.Parse(v, System.Globalization.CultureInfo.InvariantCulture);
                        break;
                    case SerializedPropertyType.Float:
                        prop.floatValue = float.Parse(v, System.Globalization.CultureInfo.InvariantCulture);
                        break;
                    case SerializedPropertyType.Boolean:
                        prop.boolValue = bool.Parse(v);
                        break;
                    case SerializedPropertyType.String:
                        prop.stringValue = v;
                        break;
                    case SerializedPropertyType.Enum:
                    {
                        // enumNames returns internal C# names (preferred for programmatic input).
                        // enumDisplayNames (Unity 2021.1+) returns formatted display names which
                        // may contain spaces; unsuitable for API input.
#pragma warning disable 0618  // enumNames deprecated but intentionally used
                        int idx = System.Array.IndexOf(prop.enumNames, v);
                        if (idx >= 0)
                            prop.enumValueIndex = idx;
                        else if (int.TryParse(v, out int numIdx))
                            prop.enumValueIndex = numIdx;
                        else
                            return BuildError("EDITOR_CTRL_SET_PROP_TYPE_MISMATCH",
                                $"Enum value '{v}' not found. Valid: {string.Join(", ", prop.enumNames)}");
#pragma warning restore 0618
                        break;
                    }
                    case SerializedPropertyType.Color:
                    {
                        var parts = v.Split(',');
                        if (parts.Length < 3)
                            return BuildError("EDITOR_CTRL_SET_PROP_TYPE_MISMATCH",
                                "Color requires 3 or 4 comma-separated floats (r,g,b[,a]).");
                        float r = float.Parse(parts[0].Trim(), System.Globalization.CultureInfo.InvariantCulture);
                        float g = float.Parse(parts[1].Trim(), System.Globalization.CultureInfo.InvariantCulture);
                        float b = float.Parse(parts[2].Trim(), System.Globalization.CultureInfo.InvariantCulture);
                        float a = parts.Length >= 4
                            ? float.Parse(parts[3].Trim(), System.Globalization.CultureInfo.InvariantCulture)
                            : 1f;
                        prop.colorValue = new Color(r, g, b, a);
                        break;
                    }
                    case SerializedPropertyType.Vector2:
                    {
                        var parts = v.Split(',');
                        if (parts.Length < 2)
                            return BuildError("EDITOR_CTRL_SET_PROP_TYPE_MISMATCH",
                                "Vector2 requires 2 comma-separated floats (x,y).");
                        prop.vector2Value = new Vector2(
                            float.Parse(parts[0].Trim(), System.Globalization.CultureInfo.InvariantCulture),
                            float.Parse(parts[1].Trim(), System.Globalization.CultureInfo.InvariantCulture));
                        break;
                    }
                    case SerializedPropertyType.Vector3:
                    {
                        var parts = v.Split(',');
                        if (parts.Length < 3)
                            return BuildError("EDITOR_CTRL_SET_PROP_TYPE_MISMATCH",
                                "Vector3 requires 3 comma-separated floats (x,y,z).");
                        prop.vector3Value = new Vector3(
                            float.Parse(parts[0].Trim(), System.Globalization.CultureInfo.InvariantCulture),
                            float.Parse(parts[1].Trim(), System.Globalization.CultureInfo.InvariantCulture),
                            float.Parse(parts[2].Trim(), System.Globalization.CultureInfo.InvariantCulture));
                        break;
                    }
                    case SerializedPropertyType.Vector4:
                    {
                        var parts = v.Split(',');
                        if (parts.Length < 4)
                            return BuildError("EDITOR_CTRL_SET_PROP_TYPE_MISMATCH",
                                "Vector4 requires 4 comma-separated floats (x,y,z,w).");
                        prop.vector4Value = new Vector4(
                            float.Parse(parts[0].Trim(), System.Globalization.CultureInfo.InvariantCulture),
                            float.Parse(parts[1].Trim(), System.Globalization.CultureInfo.InvariantCulture),
                            float.Parse(parts[2].Trim(), System.Globalization.CultureInfo.InvariantCulture),
                            float.Parse(parts[3].Trim(), System.Globalization.CultureInfo.InvariantCulture));
                        break;
                    }
                    case SerializedPropertyType.Quaternion:
                    {
                        // Issue #111: accept only the four-component xyzw form.
                        // Euler input is intentionally not supported here — the
                        // dedicated euler-hint property already covers that
                        // shape, and mixing two value shapes inside one type
                        // case obscures the contract.
                        var parts = v.Split(',');
                        if (parts.Length != 4)
                            return BuildError("EDITOR_CTRL_SET_PROP_TYPE_MISMATCH",
                                "Quaternion requires exactly 4 comma-separated floats (x,y,z,w).");
                        float qx = float.Parse(parts[0].Trim(), System.Globalization.CultureInfo.InvariantCulture);
                        float qy = float.Parse(parts[1].Trim(), System.Globalization.CultureInfo.InvariantCulture);
                        float qz = float.Parse(parts[2].Trim(), System.Globalization.CultureInfo.InvariantCulture);
                        float qw = float.Parse(parts[3].Trim(), System.Globalization.CultureInfo.InvariantCulture);
                        // Norm tolerance 1e-4 — matches the precision we
                        // expect from float32 quaternion encodings emitted
                        // by Unity's Transform.localRotation. Tighter than
                        // Mathf.Approximately but still loose enough that
                        // round-tripping a serialized quaternion does not
                        // get rejected.
                        float norm = Mathf.Sqrt(qx * qx + qy * qy + qz * qz + qw * qw);
                        if (Mathf.Abs(norm - 1f) > 1e-4f)
                            return BuildError("EDITOR_CTRL_SET_PROP_QUATERNION_NOT_NORMALIZED",
                                $"Quaternion value (x={qx}, y={qy}, z={qz}, w={qw}) has norm {norm}; "
                                + "unit norm (1.0 ± 1e-4) is required. Normalize the input on the caller side.");
                        prop.quaternionValue = new Quaternion(qx, qy, qz, qw);
                        break;
                    }
                    case SerializedPropertyType.ArraySize:
                    case SerializedPropertyType.FixedBufferSize:
                        prop.intValue = int.Parse(v, System.Globalization.CultureInfo.InvariantCulture);
                        break;
                    case SerializedPropertyType.ObjectReference:
                    {
                        string refPath = hasRef ? request.object_reference : v;
                        var (obj, refError) = ResolveObjectReference(refPath);
                        if (obj == null)
                            return BuildError("EDITOR_CTRL_SET_PROP_REF_NOT_FOUND",
                                refError ?? $"Object reference not found: {refPath}");
                        prop.objectReferenceValue = obj;
                        break;
                    }
                    default:
                        return BuildError("EDITOR_CTRL_SET_PROP_TYPE_MISMATCH",
                            $"Unsupported property type: {prop.propertyType}");
                }
            }
            catch (System.FormatException ex)
            {
                return BuildError("EDITOR_CTRL_SET_PROP_TYPE_MISMATCH",
                    $"Failed to parse value '{v}' for {prop.propertyType}: {ex.Message}");
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
