using System;
using System.Collections.Generic;
using System.Globalization;
using System.Reflection;
using UnityEditor;

// UdonSharp array values are kept separate from scalar field writes so the sync surface stays reviewable.
namespace PrefabSentinel
{
    public static partial class UnityEditorControlBridge
    {
        private static EditorControlResponse WriteUdonSharpArrayValue(
            SerializedProperty prop,
            FieldInfo fieldInfo,
            EditorControlRequest request)
        {
            if (!prop.isArray || prop.propertyType == SerializedPropertyType.String)
            {
                return BuildError(
                    "EDITOR_CTRL_UDON_SET_FIELD_NON_ARRAY_VALUES",
                    $"Field {request.field_name} is not an array field.");
            }
            if (!JsonArrayScalarParser.TryParse(request.values_json, out List<JsonArrayScalar> elements))
            {
                return BuildError(
                    "EDITOR_CTRL_UDON_SET_FIELD_VALUES_JSON_PARSE",
                    "values_json must be a JSON array of supported scalar values.");
            }
            if (request.expected_length >= 0 && elements.Count != request.expected_length)
            {
                return BuildError(
                    "EDITOR_CTRL_UDON_SET_FIELD_ARRAY_LENGTH_MISMATCH",
                    $"values_json length {elements.Count} did not match expected_length {request.expected_length}.");
            }

            Type elementType = fieldInfo != null && fieldInfo.FieldType.IsArray
                ? fieldInfo.FieldType.GetElementType()
                : null;
            if (!IsSupportedUdonArrayElementType(elementType) && elements.Count == 0)
            {
                return BuildError(
                    "EDITOR_CTRL_UDON_SET_FIELD_UNSUPPORTED_ARRAY_TYPE",
                    $"Field {request.field_name} is not a supported UdonSharp array type.");
            }

            prop.arraySize = elements.Count;
            for (int i = 0; i < elements.Count; i++)
            {
                SerializedProperty item = prop.GetArrayElementAtIndex(i);
                EditorControlResponse error = WriteUdonSharpArrayElement(
                    item, elementType, elements[i], i, request.field_name);
                if (error != null) return error;
            }
            return null;
        }

        private static EditorControlResponse WriteUdonSharpArrayElement(
            SerializedProperty item,
            Type elementType,
            JsonArrayScalar element,
            int index,
            string fieldName)
        {
            if (elementType != null
                && elementType.FullName != null
                && elementType.FullName.EndsWith("VRCUrl", StringComparison.Ordinal))
            {
                if (element.Kind != JsonArrayScalarKind.String)
                {
                    return BuildError(
                        "EDITOR_CTRL_UDON_SET_FIELD_ARRAY_ELEMENT_PARSE",
                        $"values_json[{index}] for {fieldName} must be a non-null VRCUrl string.");
                }
                SerializedProperty urlProp = item.FindPropertyRelative("url");
                if (urlProp == null)
                {
                    return BuildError(
                        "EDITOR_CTRL_UDON_SET_FIELD_UNSUPPORTED_ARRAY_TYPE",
                        $"values_json[{index}] targets a VRCUrl wrapper without a url sub-field.");
                }
                urlProp.stringValue = element.Value;
                return null;
            }
            switch (item.propertyType)
            {
                case SerializedPropertyType.String:
                    if (element.Kind != JsonArrayScalarKind.String)
                        return ArrayElementParseError(index, fieldName, "string");
                    item.stringValue = element.Value;
                    return null;
                case SerializedPropertyType.Integer:
                    if (element.Kind != JsonArrayScalarKind.Number
                        || !int.TryParse(element.Value, NumberStyles.Integer, CultureInfo.InvariantCulture, out int intValue))
                    {
                        return ArrayElementParseError(index, fieldName, "int");
                    }
                    item.intValue = intValue;
                    return null;
                case SerializedPropertyType.Float:
                    if (element.Kind != JsonArrayScalarKind.Number
                        || !float.TryParse(element.Value, NumberStyles.Float, CultureInfo.InvariantCulture, out float floatValue))
                    {
                        return ArrayElementParseError(index, fieldName, "float");
                    }
                    item.floatValue = floatValue;
                    return null;
                case SerializedPropertyType.Boolean:
                    if (element.Kind != JsonArrayScalarKind.Boolean
                        || !bool.TryParse(element.Value, out bool boolValue))
                    {
                        return ArrayElementParseError(index, fieldName, "bool");
                    }
                    item.boolValue = boolValue;
                    return null;
                default:
                    return BuildError(
                        "EDITOR_CTRL_UDON_SET_FIELD_UNSUPPORTED_ARRAY_TYPE",
                        $"Field {fieldName} has unsupported array element type {item.propertyType}.");
            }
        }

        private static bool IsSupportedUdonArrayElementType(Type elementType)
        {
            if (elementType == null) return true;
            if (elementType == typeof(string)
                || elementType == typeof(int)
                || elementType == typeof(float)
                || elementType == typeof(bool))
            {
                return true;
            }
            return elementType.FullName != null
                && elementType.FullName.EndsWith("VRCUrl", StringComparison.Ordinal);
        }

        private static EditorControlResponse ArrayElementParseError(
            int index, string fieldName, string expectedType)
        {
            return BuildError(
                "EDITOR_CTRL_UDON_SET_FIELD_ARRAY_ELEMENT_PARSE",
                $"values_json[{index}] for {fieldName} could not be parsed as {expectedType}.");
        }
    }
}
