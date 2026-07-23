using System;
using System.Collections.Generic;
using System.Globalization;
using System.Text;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.SceneManagement;

// SerializedProperty value and JSON payload helpers are shared by read, list, and write responses.
namespace PrefabSentinel
{
    public static partial class UnityEditorControlBridge
    {
        private static bool IsUnsupportedDirectStruct(SerializedProperty property)
        {
            return property.propertyType == SerializedPropertyType.Vector3
                || property.propertyType == SerializedPropertyType.Vector2
                || property.propertyType == SerializedPropertyType.Vector4
                || property.propertyType == SerializedPropertyType.Color
                || property.propertyType == SerializedPropertyType.Quaternion
                || property.propertyType == SerializedPropertyType.Rect
                || property.propertyType == SerializedPropertyType.Bounds;
        }

        private static IntegerRange ResolveSerializedPropertyIntegerRange(
            SerializedProperty property)
        {
            switch (property.numericType)
            {
                case SerializedPropertyNumericType.Int8:
                    return new IntegerRange { min = sbyte.MinValue, max = sbyte.MaxValue };
                case SerializedPropertyNumericType.UInt8:
                    return new IntegerRange { min = 0, max = byte.MaxValue, unsigned = true };
                case SerializedPropertyNumericType.Int16:
                    return new IntegerRange { min = short.MinValue, max = short.MaxValue };
                case SerializedPropertyNumericType.UInt16:
                    return new IntegerRange { min = 0, max = ushort.MaxValue, unsigned = true };
                case SerializedPropertyNumericType.Int32:
                    return new IntegerRange { min = int.MinValue, max = int.MaxValue };
                case SerializedPropertyNumericType.UInt32:
                    return new IntegerRange { min = 0, max = uint.MaxValue, unsigned = true };
                case SerializedPropertyNumericType.Int64:
                    return new IntegerRange { min = long.MinValue, max = long.MaxValue };
                case SerializedPropertyNumericType.UInt64:
                    return new IntegerRange { min = 0, unsignedMax = ulong.MaxValue, unsigned = true, useUnsignedMax = true };
                default:
                    return new IntegerRange { min = int.MinValue, max = int.MaxValue };
            }
        }

        private static string SerializedPropertyValueKind(SerializedProperty property)
        {
            if (property.isArray && property.propertyType != SerializedPropertyType.String)
                return "array";
            switch (property.propertyType)
            {
                case SerializedPropertyType.Boolean:
                    return "bool";
                case SerializedPropertyType.Integer:
                    return "integer";
                case SerializedPropertyType.Float:
                    return "float";
                case SerializedPropertyType.String:
                    return "string";
                case SerializedPropertyType.Enum:
                    return "enum";
                case SerializedPropertyType.ObjectReference:
                    return "object_reference";
                default:
                    return "unsupported";
            }
        }

        private static void AppendPropertyValueFields(StringBuilder json, SerializedProperty property)
        {
            switch (SerializedPropertyValueKind(property))
            {
                case "bool":
                    AppendJsonValue(json, "bool_value", property.boolValue, true);
                    break;
                case "integer":
                    if (property.numericType == SerializedPropertyNumericType.UInt64) json.Append(",\"ulong_value\":" + property.ulongValue.ToString(CultureInfo.InvariantCulture));
                    else AppendJsonValue(json, "long_value", property.longValue, true);
                    break;
                case "float":
                    AppendJsonValue(json, "float_value", property.floatValue, true);
                    break;
                case "string":
                    AppendJsonField(json, "string_value", property.stringValue, true);
                    break;
                case "enum":
                    AppendJsonValue(json, "enum_index", property.enumValueIndex, true);
                    AppendJsonField(json, "enum_name", SafeEnumName(property), true);
                    break;
                case "object_reference":
                    json.Append(",\"object_reference\":");
                    json.Append(BuildObjectReferenceJson(property.objectReferenceValue));
                    break;
                case "array":
                    AppendJsonValue(json, "array_size", property.arraySize, true);
                    break;
                default:
                    AppendJsonField(json, "summary", property.type, true);
                    break;
            }
        }

        private static string SafeEnumName(SerializedProperty property)
        {
            if (property.enumValueIndex < 0
                || property.enumValueIndex >= property.enumNames.Length)
                return string.Empty;
            return property.enumNames[property.enumValueIndex];
        }

        private static string BuildWriteResultJson(
            SerializedPropertyWritePlan plan,
            SerializedPropertyStateEvidence state,
            string syncStatus,
            bool executed,
            string overrideJson)
        {
            StringBuilder json = new StringBuilder();
            json.Append('{');
            json.Append("\"current\":");
            json.Append(plan.CurrentJson);
            json.Append(",\"proposed\":");
            json.Append(plan.ProposedJson);
            json.Append(",\"would_change\":");
            json.Append(plan.WouldChange ? "true" : "false");
            json.Append(",\"executed\":");
            json.Append(executed ? "true" : "false");
            json.Append(",\"sync_status\":");
            AppendJsonString(json, syncStatus);
            json.Append(",\"dirty_target\":");
            json.Append(BuildStateJson(state));
            json.Append(",\"override\":");
            json.Append(overrideJson);
            json.Append(",\"state\":");
            json.Append(BuildStateJson(state));
            json.Append('}');
            return json.ToString();
        }

        private static string BuildCurrentPropertyValueJson(SerializedProperty property)
        {
            StringBuilder json = new StringBuilder();
            json.Append('{');
            AppendJsonField(json, "value_kind", SerializedPropertyValueKind(property), false);
            AppendPropertyValueFields(json, property);
            json.Append('}');
            return json.ToString();
        }

        private static string BuildErrorProposalJson(
            string code,
            string message,
            string field,
            string attemptedJson)
        {
            return "{\"code\":" + JsonString(code)
                + ",\"message\":" + JsonString(message)
                + ",\"field\":" + JsonString(field)
                + ",\"attempted\":" + attemptedJson
                + "}";
        }

        private static string BuildStateJson(SerializedPropertyStateEvidence state)
        {
            StringBuilder json = new StringBuilder();
            json.Append('{');
            AppendJsonField(json, "mode", state.mode, false);
            AppendJsonField(json, "hierarchy_path", state.hierarchy_path, true);
            AppendJsonField(json, "scene_path", state.scene_path, true);
            AppendJsonField(json, "prefab_asset_path", state.prefab_asset_path, true);
            json.Append('}');
            return json.ToString();
        }

        private static SerializedPropertyWritePlan TypeMismatch(
            SerializedProperty property,
            string field,
            string attemptedJson)
        {
            return WritePlanError(
                property,
                "EDITOR_CTRL_SERIALIZED_PROPERTY_TYPE_MISMATCH",
                $"{field} does not match {property.propertyType}.",
                field,
                attemptedJson);
        }

        private static SerializedPropertyWritePlan WritePlanOk(
            bool wouldChange,
            string currentJson,
            string proposedJson)
        {
            return new SerializedPropertyWritePlan
            {
                Success = true,
                WouldChange = wouldChange,
                CurrentJson = currentJson,
                ProposedJson = proposedJson,
            };
        }

        private static SerializedPropertyWritePlan WritePlanError(
            SerializedProperty property,
            string code,
            string message,
            string field,
            string attemptedJson)
        {
            return new SerializedPropertyWritePlan
            {
                Success = false,
                ErrorCode = code,
                ErrorMessage = message,
                CurrentJson = BuildCurrentPropertyValueJson(property),
                ProposedJson = BuildErrorProposalJson(
                    code, message, field, attemptedJson),
            };
        }

        private static string BuildScalarJson(string key, bool value)
        {
            return "{\"" + key + "\":" + (value ? "true" : "false") + "}";
        }

        private static string BuildScalarJson(string key, long value)
        {
            return "{\"" + key + "\":"
                + value.ToString(CultureInfo.InvariantCulture) + "}";
        }

        private static string BuildScalarJson(string key, ulong value)
        {
            return "{\"" + key + "\":"
                + value.ToString(CultureInfo.InvariantCulture) + "}";
        }

        private static string BuildScalarJson(string key, float value)
        {
            return "{\"" + key + "\":"
                + value.ToString(CultureInfo.InvariantCulture) + "}";
        }

        private static string BuildScalarJson(string key, string value)
        {
            return "{\"" + key + "\":" + JsonString(value) + "}";
        }

        private static string BuildEnumJson(int index, string[] names)
        {
            string name = index >= 0 && index < names.Length ? names[index] : string.Empty;
            return "{\"enum_index\":" + index.ToString(CultureInfo.InvariantCulture)
                + ",\"enum_name\":" + JsonString(name) + "}";
        }

        private static string BuildObjectReferenceJson(UnityEngine.Object value)
        {
            if (value == null)
                return "{\"null\":true,\"path\":\"\",\"guid\":\"\",\"hierarchy_path\":\"\",\"type\":\"\"}";
            string path = AssetDatabase.GetAssetPath(value);
            string guid = string.IsNullOrEmpty(path) ? string.Empty : AssetDatabase.AssetPathToGUID(path);
            GameObject go = string.IsNullOrEmpty(path) ? value as GameObject : null;
            Component component = string.IsNullOrEmpty(path) ? value as Component : null;
            string hierarchyPath = go != null
                ? GetHierarchyPath(go.transform)
                : component != null ? GetHierarchyPath(component.transform) : string.Empty;
            return "{\"null\":false,\"path\":" + JsonString(path)
                + ",\"guid\":" + JsonString(guid)
                + ",\"hierarchy_path\":" + JsonString(hierarchyPath)
                + ",\"type\":" + JsonString(value.GetType().FullName) + "}";
        }

        private static string AssemblyNameSafe(System.Reflection.Assembly assembly)
        {
            return assembly == null ? string.Empty : assembly.GetName().Name;
        }

        private static void AppendJsonField(
            StringBuilder json,
            string key,
            string value,
            bool leadingComma)
        {
            if (leadingComma) json.Append(',');
            json.Append('"');
            json.Append(key);
            json.Append("\":");
            AppendJsonString(json, value);
        }

        private static void AppendJsonValue(
            StringBuilder json,
            string key,
            int value,
            bool leadingComma)
        {
            if (leadingComma) json.Append(',');
            json.Append('"');
            json.Append(key);
            json.Append("\":");
            json.Append(value.ToString(CultureInfo.InvariantCulture));
        }

        private static void AppendJsonValue(
            StringBuilder json,
            string key,
            long value,
            bool leadingComma)
        {
            if (leadingComma) json.Append(',');
            json.Append('"');
            json.Append(key);
            json.Append("\":");
            json.Append(value.ToString(CultureInfo.InvariantCulture));
        }

        private static void AppendJsonValue(
            StringBuilder json,
            string key,
            float value,
            bool leadingComma)
        {
            if (leadingComma) json.Append(',');
            json.Append('"');
            json.Append(key);
            json.Append("\":");
            json.Append(value.ToString(CultureInfo.InvariantCulture));
        }

        private static void AppendJsonValue(
            StringBuilder json,
            string key,
            bool value,
            bool leadingComma)
        {
            if (leadingComma) json.Append(',');
            json.Append('"');
            json.Append(key);
            json.Append("\":");
            json.Append(value ? "true" : "false");
        }

        private static string JsonString(string value)
        {
            StringBuilder json = new StringBuilder();
            AppendJsonString(json, value);
            return json.ToString();
        }

        private static void AppendJsonString(StringBuilder json, string value)
        {
            json.Append('"');
            string text = value ?? string.Empty;
            for (int i = 0; i < text.Length; i++)
            {
                char c = text[i];
                if (c <= '\u001F')
                {
                    json.Append("\\u");
                    json.Append(((int)c).ToString("x4", CultureInfo.InvariantCulture));
                    continue;
                }
                switch (c)
                {
                    case '\\':
                        json.Append("\\\\");
                        break;
                    case '"':
                        json.Append("\\\"");
                        break;
                    default:
                        json.Append(c);
                        break;
                }
            }
            json.Append('"');
        }
    }
}
