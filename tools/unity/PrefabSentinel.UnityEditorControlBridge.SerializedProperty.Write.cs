using System;
using System.Collections.Generic;
using System.Globalization;
using System.Text;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.SceneManagement;

// SerializedProperty write planning and side-effect boundaries stay separate from JSON payload formatting.
namespace PrefabSentinel
{
    public static partial class UnityEditorControlBridge
    {
        private static SerializedPropertyWritePlan ApplySerializedPropertyValueIntent(
            SerializedProperty property,
            SerializedPropertyValueIntent intent,
            EditorControlRequest request,
            bool apply)
        {
            if (intent.Kind == SerializedPropertyValueIntentKind.ArraySize)
            {
                if (!property.isArray || property.propertyType == SerializedPropertyType.String)
                    return WritePlanError(
                        property,
                        "EDITOR_CTRL_SERIALIZED_PROPERTY_ARRAY_SIZE_INVALID",
                        "array_size requires an array SerializedProperty.",
                        "array_size",
                        BuildScalarJson("array_size", intent.ArraySize));
                bool changed = property.arraySize != intent.ArraySize;
                if (apply) property.arraySize = intent.ArraySize;
                return WritePlanOk(
                    changed,
                    BuildScalarJson("array_size", property.arraySize),
                    "{\"array_size\":" + intent.ArraySize.ToString(CultureInfo.InvariantCulture)
                    + ",\"resulting_array_size\":"
                    + (apply ? property.arraySize : intent.ArraySize).ToString(CultureInfo.InvariantCulture)
                    + ",\"would_change\":" + (changed ? "true" : "false") + "}");
            }

            if (IsUnsupportedDirectStruct(property))
                return WritePlanError(
                    property,
                    "EDITOR_CTRL_SERIALIZED_PROPERTY_UNSUPPORTED_WRITE",
                    $"Unsupported direct property type: {property.propertyType}.",
                    "value_intent",
                    BuildScalarJson("intent_kind", intent.Kind.ToString()));

            switch (intent.Kind)
            {
                case SerializedPropertyValueIntentKind.Bool:
                    if (property.propertyType != SerializedPropertyType.Boolean)
                        return TypeMismatch(
                            property,
                            "bool_value",
                            BuildScalarJson("bool_value", intent.BoolValue));
                    bool boolChanged = property.boolValue != intent.BoolValue;
                    if (apply) property.boolValue = intent.BoolValue;
                    return WritePlanOk(
                        boolChanged,
                        BuildScalarJson("bool_value", property.boolValue),
                        BuildScalarJson("bool_value", intent.BoolValue));
                case SerializedPropertyValueIntentKind.Int:
                    return ApplyIntegerValue(property, intent.IntValue, "int_value", apply);
                case SerializedPropertyValueIntentKind.Long:
                    return ApplyIntegerValue(property, intent.LongValue, "long_value", apply);
                case SerializedPropertyValueIntentKind.Float:
                    if (property.propertyType != SerializedPropertyType.Float)
                        return TypeMismatch(
                            property,
                            "float_value",
                            BuildScalarJson("float_value", intent.FloatValue));
                    bool floatChanged = Math.Abs(property.floatValue - intent.FloatValue) > 0f;
                    if (apply) property.floatValue = intent.FloatValue;
                    return WritePlanOk(
                        floatChanged,
                        BuildScalarJson("float_value", property.floatValue),
                        BuildScalarJson("float_value", intent.FloatValue));
                case SerializedPropertyValueIntentKind.String:
                    if (property.propertyType != SerializedPropertyType.String)
                        return TypeMismatch(
                            property,
                            "string_value",
                            BuildScalarJson("string_value", intent.StringValue));
                    bool stringChanged = !string.Equals(
                        property.stringValue, intent.StringValue, StringComparison.Ordinal);
                    if (apply) property.stringValue = intent.StringValue;
                    return WritePlanOk(
                        stringChanged,
                        BuildScalarJson("string_value", property.stringValue),
                        BuildScalarJson("string_value", intent.StringValue));
                case SerializedPropertyValueIntentKind.EnumName:
                case SerializedPropertyValueIntentKind.EnumIndex:
                    return ApplyEnumValue(property, intent, apply);
                case SerializedPropertyValueIntentKind.ObjectReferenceAssetPath:
                case SerializedPropertyValueIntentKind.ObjectReferenceHierarchyPath:
                case SerializedPropertyValueIntentKind.ObjectReferenceNull:
                    return ApplyObjectReferenceValue(property, intent, request, apply);
                default:
                    return WritePlanError(
                        property,
                        "EDITOR_CTRL_SERIALIZED_PROPERTY_VALUE_REQUIRED",
                        "A serialized property value intent is required.",
                        "value_intent",
                        "{}");
            }
        }

        private static SerializedPropertyWritePlan ApplyIntegerValue(
            SerializedProperty property,
            long proposed,
            string field,
            bool apply)
        {
            if (property.propertyType != SerializedPropertyType.Integer)
                return TypeMismatch(property, field, BuildScalarJson(field, proposed));
            IntegerRange range = ResolveSerializedPropertyIntegerRange(property);
            bool outsideRange = proposed < range.min
                || (!range.useUnsignedMax && proposed > range.max);
            if (outsideRange)
            {
                string maxText = range.useUnsignedMax
                    ? range.unsignedMax.ToString(CultureInfo.InvariantCulture)
                    : range.max.ToString(CultureInfo.InvariantCulture);
                string message = string.Format(
                    CultureInfo.InvariantCulture,
                    "Integer proposal {0} is outside range {1}..{2}.",
                    proposed, range.min, maxText);
                return WritePlanError(
                    property,
                    range.unsigned
                        ? "EDITOR_CTRL_SERIALIZED_PROPERTY_UNSIGNED_RANGE"
                        : "EDITOR_CTRL_SERIALIZED_PROPERTY_TYPE_MISMATCH",
                    message,
                    field,
                    BuildScalarJson(field, proposed));
            }
            if (property.numericType == SerializedPropertyNumericType.UInt64)
            {
                ulong proposedUnsigned = (ulong)proposed;
                bool changed = property.ulongValue != proposedUnsigned;
                if (apply) property.ulongValue = proposedUnsigned;
                return WritePlanOk(
                    changed,
                    BuildScalarJson("ulong_value", property.ulongValue),
                    BuildScalarJson(field, proposedUnsigned));
            }
            bool changedLong = property.longValue != proposed;
            if (apply) property.longValue = proposed;
            return WritePlanOk(
                changedLong,
                BuildScalarJson("long_value", property.longValue),
                BuildScalarJson(field, proposed));
        }

        private static SerializedPropertyWritePlan ApplyEnumValue(
            SerializedProperty property,
            SerializedPropertyValueIntent intent,
            bool apply)
        {
            string field = intent.Kind == SerializedPropertyValueIntentKind.EnumIndex
                ? "enum_index"
                : "enum_name";
            string attemptedJson = intent.Kind == SerializedPropertyValueIntentKind.EnumIndex
                ? BuildScalarJson("enum_index", intent.EnumIndex)
                : BuildScalarJson("enum_name", intent.EnumName);
            if (property.propertyType != SerializedPropertyType.Enum)
                return TypeMismatch(property, field, attemptedJson);

            int index = intent.Kind == SerializedPropertyValueIntentKind.EnumIndex
                ? intent.EnumIndex
                : Array.IndexOf(property.enumNames, intent.EnumName);
            if (index < 0 || index >= property.enumNames.Length)
                return WritePlanError(
                    property,
                    "EDITOR_CTRL_SERIALIZED_PROPERTY_ENUM_VALUE_NOT_FOUND",
                    "Enum proposal does not match the target property.",
                    field,
                    attemptedJson);

            bool changed = property.enumValueIndex != index;
            if (apply) property.enumValueIndex = index;
            return WritePlanOk(
                changed,
                BuildEnumJson(property.enumValueIndex, property.enumNames),
                BuildEnumJson(index, property.enumNames));
        }

        private static SerializedPropertyWritePlan ApplyObjectReferenceValue(
            SerializedProperty property,
            SerializedPropertyValueIntent intent,
            EditorControlRequest request,
            bool apply)
        {
            string attemptedJson;
            if (intent.Kind == SerializedPropertyValueIntentKind.ObjectReferenceAssetPath)
                attemptedJson = BuildScalarJson(
                    "object_reference_asset_path",
                    request.serialized_property_object_reference_asset_path);
            else if (intent.Kind == SerializedPropertyValueIntentKind.ObjectReferenceHierarchyPath)
                attemptedJson = BuildScalarJson(
                    "object_reference_hierarchy_path",
                    request.serialized_property_object_reference_hierarchy_path);
            else
                attemptedJson = BuildScalarJson("object_reference_null", true);

            if (property.propertyType != SerializedPropertyType.ObjectReference)
                return TypeMismatch(property, "object_reference", attemptedJson);

            SerializedPropertyObjectReference resolved =
                ResolveSerializedPropertyObjectReference(property, intent, request);
            if (!resolved.Success)
            {
                string proposedJson = resolved.EvidenceJson == "{}"
                    ? attemptedJson
                    : resolved.EvidenceJson;
                return WritePlanError(
                    property,
                    resolved.ErrorCode,
                    resolved.ErrorMessage,
                    "object_reference",
                    proposedJson);
            }
            if (apply) property.objectReferenceValue = resolved.Value;
            return WritePlanOk(
                resolved.WouldChange,
                BuildObjectReferenceJson(property.objectReferenceValue),
                resolved.EvidenceJson);
        }

        private static SerializedPropertyObjectReference ResolveSerializedPropertyObjectReference(
            SerializedProperty property,
            SerializedPropertyValueIntent intent,
            EditorControlRequest request)
        {
            ExpectedObjectReferenceResolution expected =
                ResolveExpectedObjectReferenceType(property);
            if (!expected.Success)
                return ObjectReferenceFailure(
                    "EDITOR_CTRL_SERIALIZED_PROPERTY_OBJECT_REF_TYPE_MISMATCH",
                    expected.ErrorMessage);

            UnityEngine.Object resolved = null;
            if (intent.Kind == SerializedPropertyValueIntentKind.ObjectReferenceNull
                || request.serialized_property_object_reference_null)
            {
                resolved = null;
            }
            else if (intent.Kind
                == SerializedPropertyValueIntentKind.ObjectReferenceAssetPath)
            {
                string assetPath =
                    request.serialized_property_object_reference_asset_path;
                resolved = AssetDatabase.LoadAssetAtPath(assetPath, expected.Type);
                if (resolved == null)
                {
                    UnityEngine.Object generic =
                        AssetDatabase.LoadAssetAtPath<UnityEngine.Object>(assetPath);
                    return ObjectReferenceFailure(
                        generic == null
                            ? "EDITOR_CTRL_SERIALIZED_PROPERTY_OBJECT_REF_NOT_FOUND"
                            : "EDITOR_CTRL_SERIALIZED_PROPERTY_OBJECT_REF_TYPE_MISMATCH",
                        $"Asset object reference not resolved: {assetPath}");
                }
            }
            else if (intent.Kind
                == SerializedPropertyValueIntentKind.ObjectReferenceHierarchyPath)
            {
                string hierarchyPath =
                    request.serialized_property_object_reference_hierarchy_path;
                if (!TryResolveGameObjectInActiveStage(
                        hierarchyPath, out GameObject go,
                        out EditorControlResponse ambiguity))
                {
                    if (ambiguity != null)
                        return ObjectReferenceFailure(
                            ambiguity.code,
                            ambiguity.message);
                    return ObjectReferenceFailure(
                        "EDITOR_CTRL_SERIALIZED_PROPERTY_OBJECT_REF_NOT_FOUND",
                        $"Hierarchy object not found: {hierarchyPath}");
                }
                if (expected.Type == typeof(GameObject)
                    || expected.Type.IsAssignableFrom(typeof(GameObject)))
                {
                    resolved = go;
                }
                else
                {
                    if (!typeof(Component).IsAssignableFrom(expected.Type))
                        return ObjectReferenceFailure(
                            "EDITOR_CTRL_SERIALIZED_PROPERTY_OBJECT_REF_TYPE_MISMATCH",
                            $"Hierarchy object is not assignable to {expected.Type.Name}: {hierarchyPath}");

                    Component[] candidates = go.GetComponents(expected.Type);
                    if (candidates.Length == 0)
                        return ObjectReferenceFailure(
                            "EDITOR_CTRL_SERIALIZED_PROPERTY_OBJECT_REF_TYPE_MISMATCH",
                            $"Hierarchy object is not assignable to {expected.Type.Name}: {hierarchyPath}");
                    if (candidates.Length > 1)
                        return ObjectReferenceFailure(
                            "EDITOR_CTRL_SERIALIZED_PROPERTY_OBJECT_REF_AMBIGUOUS",
                            $"Hierarchy object has multiple components assignable to {expected.Type.Name}: {hierarchyPath}",
                            BuildObjectReferenceAmbiguityJson(hierarchyPath, candidates));
                    resolved = candidates[0];
                }
            }

            bool wouldChange = property.objectReferenceValue != resolved;
            return new SerializedPropertyObjectReference
            {
                Success = true,
                Value = resolved,
                EvidenceJson = BuildObjectReferenceJson(resolved),
                WouldChange = wouldChange,
            };
        }

        private static UdonSharpSyncEvidence BuildUdonSharpSyncStatus(
            SerializedPropertyTarget target)
        {
            Component component = target.Component;
            if (component == null)
                return new UdonSharpSyncEvidence { status = "not_applicable" };

            Type componentType = component.GetType();
            bool typeLooksUdonSharp =
                componentType.FullName.IndexOf("UdonSharp", StringComparison.OrdinalIgnoreCase) >= 0
                || AssemblyNameSafe(component.GetType().Assembly).IndexOf(
                    "UdonSharp", StringComparison.OrdinalIgnoreCase) >= 0;
            Component backing = target.GameObject.GetComponent("UdonBehaviour");
            if (!typeLooksUdonSharp || backing == null)
                return new UdonSharpSyncEvidence { status = "not_applicable" };

            Type undoType = ResolveUdonSharpUndoType();
            if (undoType == null)
                return new UdonSharpSyncEvidence
                {
                    status = "warning",
                    detail = "UdonSharp undo utility not available; backing UdonBehaviour sync not attempted.",
                    warning = true,
                };

            EditorControlResponse syncError =
                InvokeUdonSharpCopyProxyToUdon(undoType, component);
            if (syncError != null)
                return new UdonSharpSyncEvidence
                {
                    status = "warning",
                    detail = syncError.message,
                    warning = true,
                };
            return new UdonSharpSyncEvidence { status = "synced" };
        }

        private static void MarkSerializedPropertyTargetDirty(
            SerializedPropertyTarget target)
        {
            PrefabStage stage = PrefabStageUtility.GetCurrentPrefabStage();
            if (stage != null && target.GameObject.scene == stage.scene)
            {
                EditorSceneManager.MarkSceneDirty(stage.scene);
                return;
            }
            EditorSceneManager.MarkSceneDirty(target.GameObject.scene);
        }

        private static string RecordSerializedPropertyPrefabOverride(
            SerializedPropertyTarget target)
        {
            bool recorded = false;
            string rootPath = string.Empty;
            string assetPath = string.Empty;
            if (PrefabUtility.IsPartOfPrefabInstance(target.GameObject))
            {
                PrefabUtility.RecordPrefabInstancePropertyModifications(
                    target.TargetObject);
                recorded = true;
                GameObject root = PrefabUtility.GetOutermostPrefabInstanceRoot(
                    target.GameObject);
                if (root != null) rootPath = GetHierarchyPath(root.transform);
                assetPath = PrefabUtility.GetPrefabAssetPathOfNearestInstanceRoot(
                    target.GameObject);
            }
            return "{\"override_recorded\":" + (recorded ? "true" : "false")
                + ",\"outermost_root\":"
                + JsonString(rootPath)
                + ",\"prefab_asset_path\":"
                + JsonString(assetPath)
                + "}";
        }
    }
}
