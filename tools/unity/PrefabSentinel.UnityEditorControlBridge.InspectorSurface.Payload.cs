using System;
using System.Globalization;
using System.Text;
using UnityEditor;
using UnityEngine;

namespace PrefabSentinel
{
    public static partial class UnityEditorControlBridge
    {
        private static string BuildInspectorSurfaceJson(
            UnityEngine.Object target,
            bool includeOverrideOrigin)
        {
            MonoScript script = InspectorMonoScript(target);
            StringBuilder json = new StringBuilder();
            json.Append('{');
            json.Append("\"target\":");
            json.Append(BuildInspectorTargetJson(target, script));
            json.Append(",\"properties\":[");
            AppendInspectorProperties(json, target, includeOverrideOrigin);
            json.Append(']');
            AppendInspectorCandidateFields(json, target, script);
            json.Append('}');
            return json.ToString();
        }

        private static MonoScript InspectorMonoScript(UnityEngine.Object target)
        {
            MonoBehaviour behaviour = target as MonoBehaviour;
            if (behaviour != null) return MonoScript.FromMonoBehaviour(behaviour);
            ScriptableObject scriptable = target as ScriptableObject;
            return scriptable != null ? MonoScript.FromScriptableObject(scriptable) : null;
        }

        private static string InspectorScriptPath(MonoScript script)
        {
            if (script == null) return null;

            string path = AssetDatabase.GetAssetPath(script);
            return string.IsNullOrEmpty(path) ? null : path;
        }

        private static string BuildInspectorTargetJson(
            UnityEngine.Object target,
            MonoScript script)
        {
            string scriptGuid = null;
            long scriptLocalId = 0;
            string scriptPath = InspectorScriptPath(script);
            if (script != null)
            {
                AssetDatabase.TryGetGUIDAndLocalFileIdentifier(
                    script,
                    out scriptGuid,
                    out scriptLocalId);
            }

            long targetLocalId;
            bool hasTargetLocalId = AssetDatabase.TryGetGUIDAndLocalFileIdentifier(
                target,
                out _,
                out targetLocalId);

            StringBuilder json = new StringBuilder();
            json.Append('{');
            AppendJsonField(json, "managed_type", target.GetType().FullName, false);
            AppendJsonField(json, "assembly", AssemblyNameSafe(target.GetType().Assembly), true);
            json.Append(",\"local_file_id\":");
            json.Append(hasTargetLocalId
                ? targetLocalId.ToString(CultureInfo.InvariantCulture)
                : "null");
            json.Append(",\"script_guid\":");
            json.Append(scriptGuid != null ? JsonString(scriptGuid) : "null");
            json.Append(",\"script_file_id\":");
            json.Append(script != null
                ? scriptLocalId.ToString(CultureInfo.InvariantCulture)
                : "null");
            json.Append(",\"script_path\":");
            json.Append(scriptPath != null ? JsonString(scriptPath) : "null");
            if (scriptPath == null)
            {
                json.Append(",\"script_path_degradation_reasons\":[");
                json.Append(JsonString("The target has no public MonoScript source."));
                json.Append(']');
            }
            json.Append('}');
            return json.ToString();
        }

        private static void AppendInspectorProperties(
            StringBuilder json,
            UnityEngine.Object target,
            bool includeOverrideOrigin)
        {
            SerializedObject serializedObject = new SerializedObject(target);
            UnityEngine.Object sourceObject = PrefabUtility.GetCorrespondingObjectFromSource(target);
            SerializedObject source = sourceObject != null
                ? new SerializedObject(sourceObject)
                : null;
            SerializedProperty iterator = serializedObject.GetIterator();
            bool first = true;
            while (iterator.Next(true))
            {
                if (!first) json.Append(',');
                first = false;
                SerializedProperty sourceProperty = source != null
                    ? source.FindProperty(iterator.propertyPath)
                    : null;
                json.Append(BuildInspectorPropertyJson(
                    iterator, sourceProperty, sourceObject, includeOverrideOrigin));
            }
        }

        private static string BuildInspectorPropertyJson(
            SerializedProperty property,
            SerializedProperty sourceProperty,
            UnityEngine.Object sourceObject,
            bool includeOverrideOrigin)
        {
            SerializedProperty sourceValueProperty = sourceProperty != null
                ? sourceProperty
                : property;
            StringBuilder json = new StringBuilder();
            json.Append('{');
            AppendJsonField(json, "path", property.propertyPath, false);
            AppendJsonField(json, "name", property.name, true);
            AppendJsonField(json, "display_name", property.displayName, true);
            AppendJsonField(json, "property_type", property.propertyType.ToString(), true);
            AppendJsonValue(json, "depth", property.depth, true);
            json.Append(",\"source_value\":");
            json.Append(BuildInspectorValueJson(sourceValueProperty));
            json.Append(",\"effective_value\":");
            json.Append(BuildInspectorValueJson(property));
            if (includeOverrideOrigin)
            {
                json.Append(",\"origin\":");
                json.Append(BuildInspectorOriginJson(property, sourceObject));
            }
            json.Append(",\"array_size\":");
            json.Append(property.isArray && property.propertyType != SerializedPropertyType.String
                ? property.arraySize.ToString(CultureInfo.InvariantCulture)
                : "null");
            json.Append(",\"element_type\":");
            json.Append(property.isArray && property.propertyType != SerializedPropertyType.String
                ? JsonString(property.arrayElementType)
                : "null");
            json.Append('}');
            return json.ToString();
        }

        private static string BuildInspectorValueJson(SerializedProperty property)
        {
            if (property.isArray && property.propertyType != SerializedPropertyType.String)
                return "null";
            switch (property.propertyType)
            {
                case SerializedPropertyType.Boolean:
                    return property.boolValue ? "true" : "false";
                case SerializedPropertyType.Integer:
                    return property.numericType == SerializedPropertyNumericType.UInt64
                        ? property.ulongValue.ToString(CultureInfo.InvariantCulture)
                        : property.longValue.ToString(CultureInfo.InvariantCulture);
                case SerializedPropertyType.Float:
                    return property.numericType == SerializedPropertyNumericType.Double
                        ? BuildInspectorFloatingPointJson(property.doubleValue)
                        : BuildInspectorFloatingPointJson(property.floatValue);
                case SerializedPropertyType.String:
                    return JsonString(property.stringValue);
                case SerializedPropertyType.Enum:
                    return "{\"index\":"
                        + property.enumValueIndex.ToString(CultureInfo.InvariantCulture)
                        + ",\"name\":" + JsonString(SafeEnumName(property)) + "}";
                case SerializedPropertyType.ObjectReference:
                    return BuildInspectorObjectReferenceJson(property);
                default:
                    return "null";
            }
        }

        private static string BuildInspectorFloatingPointJson(float value)
        {
            return float.IsNaN(value) || float.IsInfinity(value)
                ? "null"
                : value.ToString("R", CultureInfo.InvariantCulture);
        }

        private static string BuildInspectorFloatingPointJson(double value)
        {
            return double.IsNaN(value) || double.IsInfinity(value)
                ? "null"
                : value.ToString("R", CultureInfo.InvariantCulture);
        }

        private static string BuildInspectorOriginJson(
            SerializedProperty property,
            UnityEngine.Object sourceObject)
        {
            if (!property.prefabOverride) return "null";
            string sourcePath = sourceObject != null
                ? AssetDatabase.GetAssetPath(sourceObject)
                : string.Empty;
            return "{\"layer\":\"prefab_override\",\"source\":"
                + JsonString(sourcePath) + "}";
        }

        private static string BuildInspectorObjectReferenceJson(SerializedProperty property)
        {
            UnityEngine.Object value = property.objectReferenceValue;
            bool missing = value == null && property.objectReferenceInstanceIDValue != 0;
            string guid = string.Empty;
            long localId = 0;
            string assetPath = string.Empty;
            string objectType = string.Empty;
            string hierarchyPath = string.Empty;
            if (value != null)
            {
                AssetDatabase.TryGetGUIDAndLocalFileIdentifier(value, out guid, out localId);
                assetPath = AssetDatabase.GetAssetPath(value);
                objectType = value.GetType().FullName;
                GameObject gameObject = value as GameObject;
                Component component = value as Component;
                if (gameObject != null) hierarchyPath = GetHierarchyPath(gameObject.transform);
                else if (component != null) hierarchyPath = GetHierarchyPath(component.transform);
            }
            return "{\"object_reference\":true,\"guid\":" + JsonString(guid)
                + ",\"local_file_id\":" + localId.ToString(CultureInfo.InvariantCulture)
                + ",\"asset_path\":" + JsonString(assetPath)
                + ",\"object_type\":" + JsonString(objectType)
                + ",\"hierarchy_path\":" + JsonString(hierarchyPath)
                + ",\"null\":" + (value == null && !missing ? "true" : "false")
                + ",\"missing\":" + (missing ? "true" : "false") + "}";
        }

        private static bool IsActiveCustomEditorCandidate(Editor editor)
        {
            if (editor == null) return false;

            Type editorType = editor.GetType();
            return !string.Equals(
                       editorType.FullName,
                       "UnityEditor.GenericInspector",
                       StringComparison.Ordinal)
                && Attribute.IsDefined(editorType, typeof(CustomEditor), true);
        }

        private static void AppendInspectorCandidateFields(
            StringBuilder json,
            UnityEngine.Object target,
            MonoScript script)
        {
            string scriptPath = InspectorScriptPath(script);
            bool runtimeSourceAvailable = scriptPath != null;
            Editor activeEditor = null;
            bool editorSelectionComplete = true;
            try
            {
                activeEditor = Editor.CreateEditor(target);
                if (activeEditor == null) editorSelectionComplete = false;
            }
            catch (Exception)
            {
                editorSelectionComplete = false;
            }

            bool candidateDiscoveryComplete =
                runtimeSourceAvailable && editorSelectionComplete;
            json.Append(",\"source_candidates_status\":");
            json.Append(JsonString(candidateDiscoveryComplete ? "complete" : "degraded"));
            json.Append(",\"source_candidates_reasons\":[");
            bool wroteReason = false;
            if (!runtimeSourceAvailable)
            {
                json.Append(JsonString("The target has no public MonoScript source."));
                wroteReason = true;
            }
            if (!editorSelectionComplete)
            {
                if (wroteReason) json.Append(',');
                json.Append(JsonString(
                    "Unity could not select an active editor for the target."));
            }
            json.Append(']');
            json.Append(",\"source_candidates\":[");
            json.Append("{\"kind\":\"runtime_component\",\"managed_type\":");
            json.Append(JsonString(target.GetType().FullName));
            json.Append('}');
            if (scriptPath != null)
            {
                json.Append(",{\"kind\":\"runtime_script\",\"path\":");
                json.Append(JsonString(scriptPath));
                json.Append('}');
            }
            json.Append(']');
            json.Append(",\"custom_editor_candidates\":[");
            if (IsActiveCustomEditorCandidate(activeEditor))
            {
                json.Append("{\"type\":");
                json.Append(JsonString(activeEditor.GetType().FullName));
                json.Append(",\"active\":true}");
            }
            json.Append(']');
            if (activeEditor != null) UnityEngine.Object.DestroyImmediate(activeEditor);
        }
    }
}
