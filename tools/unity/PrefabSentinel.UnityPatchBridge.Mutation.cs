using System;
using System.Collections.Generic;
using System.Globalization;
using System.Reflection;
using UnityEditor;
using UnityEngine;
namespace PrefabSentinel
{
    public static partial class UnityPatchBridge
    {
        private static bool TryApplyOp(
            GameObject prefabRoot,
            string target,
            PatchOp op,
            int opIndex,
            List<BridgeDiagnostic> diagnostics
        )
        {
            if (op == null)
            {
                diagnostics.Add(
                    new BridgeDiagnostic
                    {
                        path = target,
                        location = $"ops[{opIndex}]",
                        detail = "schema_error",
                        evidence = "operation is null"
                    }
                );
                return false;
            }
            string opName = op.op ?? string.Empty;

            // ── add_component: resolve GO by hierarchy path → AddComponent ──
            if (string.Equals(opName, "add_component", StringComparison.Ordinal))
            {
                if (string.IsNullOrWhiteSpace(op.type))
                {
                    diagnostics.Add(
                        new BridgeDiagnostic
                        {
                            path = target,
                            location = $"ops[{opIndex}].type",
                            detail = "schema_error",
                            evidence = "type is required for add_component"
                        }
                    );
                    return false;
                }
                GameObject targetGO;
                string goError;
                if (!TryFindGameObjectByPath(prefabRoot, op.target, out targetGO, out goError))
                {
                    diagnostics.Add(
                        new BridgeDiagnostic
                        {
                            path = target,
                            location = $"ops[{opIndex}].target",
                            detail = "apply_error",
                            evidence = goError
                        }
                    );
                    return false;
                }
                Type componentType;
                string typeError;
                if (!TryResolveComponentType(op.type, out componentType, out typeError))
                {
                    diagnostics.Add(
                        new BridgeDiagnostic
                        {
                            path = target,
                            location = $"ops[{opIndex}].type",
                            detail = "apply_error",
                            evidence = typeError
                        }
                    );
                    return false;
                }
                if (componentType.IsAbstract || componentType.ContainsGenericParameters)
                {
                    diagnostics.Add(
                        new BridgeDiagnostic
                        {
                            path = target,
                            location = $"ops[{opIndex}].type",
                            detail = "apply_error",
                            evidence = $"component type '{componentType.FullName ?? componentType.Name}' cannot be instantiated"
                        }
                    );
                    return false;
                }
                if (componentType == typeof(Transform))
                {
                    diagnostics.Add(
                        new BridgeDiagnostic
                        {
                            path = target,
                            location = $"ops[{opIndex}].type",
                            detail = "apply_error",
                            evidence = "Transform is implicit and cannot be added"
                        }
                    );
                    return false;
                }
                Component addedComponent = targetGO.AddComponent(componentType);
                if (addedComponent == null)
                {
                    diagnostics.Add(
                        new BridgeDiagnostic
                        {
                            path = target,
                            location = $"ops[{opIndex}]",
                            detail = "apply_error",
                            evidence = $"AddComponent returned null for '{componentType.FullName ?? componentType.Name}'"
                        }
                    );
                    return false;
                }
                return true;
            }

            // ── remove_component: resolve via TryFindUniqueComponent → DestroyImmediate ──
            if (string.Equals(opName, "remove_component", StringComparison.Ordinal))
            {
                if (string.IsNullOrWhiteSpace(op.component))
                {
                    diagnostics.Add(
                        new BridgeDiagnostic
                        {
                            path = target,
                            location = $"ops[{opIndex}].component",
                            detail = "schema_error",
                            evidence = "component is required for remove_component"
                        }
                    );
                    return false;
                }
                Component removeTarget;
                string removeError;
                if (!TryFindUniqueComponent(prefabRoot, op.component, out removeTarget, out removeError))
                {
                    diagnostics.Add(
                        new BridgeDiagnostic
                        {
                            path = target,
                            location = $"ops[{opIndex}].component",
                            detail = "apply_error",
                            evidence = removeError
                        }
                    );
                    return false;
                }
                if (removeTarget is Transform)
                {
                    diagnostics.Add(
                        new BridgeDiagnostic
                        {
                            path = target,
                            location = $"ops[{opIndex}].component",
                            detail = "apply_error",
                            evidence = "Transform cannot be removed"
                        }
                    );
                    return false;
                }
                UnityEngine.Object.DestroyImmediate(removeTarget, true);
                return true;
            }

            // ── mutation ops: set / insert_array_element / remove_array_element ──
            if (
                !string.Equals(opName, "set", StringComparison.Ordinal)
                && !string.Equals(opName, "insert_array_element", StringComparison.Ordinal)
                && !string.Equals(opName, "remove_array_element", StringComparison.Ordinal)
            )
            {
                diagnostics.Add(
                    new BridgeDiagnostic
                    {
                        path = target,
                        location = $"ops[{opIndex}].op",
                        detail = "schema_error",
                        evidence = $"unsupported op '{op.op}'"
                    }
                );
                return false;
            }
            if (string.IsNullOrWhiteSpace(op.component))
            {
                diagnostics.Add(
                    new BridgeDiagnostic
                    {
                        path = target,
                        location = $"ops[{opIndex}].component",
                        detail = "schema_error",
                        evidence = "component is required"
                    }
                );
                return false;
            }

            Component component;
            string componentError;
            if (!TryFindUniqueComponent(prefabRoot, op.component, out component, out componentError))
            {
                diagnostics.Add(
                    new BridgeDiagnostic
                    {
                        path = target,
                        location = $"ops[{opIndex}].component",
                        detail = "apply_error",
                        evidence = componentError
                    }
                );
                return false;
            }

            return TryApplyMutationOpToObject(component, target, op, opIndex, diagnostics);
        }
        private static bool TryApplyMutationOpToObject(
            UnityEngine.Object targetObject,
            string target,
            PatchOp op,
            int opIndex,
            List<BridgeDiagnostic> diagnostics
        )
        {
            if (targetObject == null)
            {
                diagnostics.Add(
                    new BridgeDiagnostic
                    {
                        path = target,
                        location = $"ops[{opIndex}]",
                        detail = "apply_error",
                        evidence = "target object resolved to null"
                    }
                );
                return false;
            }
            if (string.IsNullOrWhiteSpace(op.path))
            {
                diagnostics.Add(
                    new BridgeDiagnostic
                    {
                        path = target,
                        location = $"ops[{opIndex}].path",
                        detail = "schema_error",
                        evidence = "path is required"
                    }
                );
                return false;
            }

            SerializedObject serialized = new SerializedObject(targetObject);
            string opName = (op.op ?? string.Empty).Trim();
            if (string.Equals(opName, "set", StringComparison.Ordinal))
            {
                SerializedProperty property = serialized.FindProperty(op.path);
                if (property == null)
                {
                    string hint = BuildSetPathHint(op.path);
                    diagnostics.Add(
                        new BridgeDiagnostic
                        {
                            path = target,
                            location = $"ops[{opIndex}].path",
                            detail = "apply_error",
                            evidence = string.IsNullOrEmpty(hint)
                                ? $"property not found: '{op.path}'"
                                : $"property not found: '{op.path}'. {hint}"
                        }
                    );
                    return false;
                }

                string setError;
                if (!TryAssignPropertyValue(property, op, out setError))
                {
                    diagnostics.Add(
                        new BridgeDiagnostic
                        {
                            path = target,
                            location = $"ops[{opIndex}]",
                            detail = "apply_error",
                            evidence = setError
                        }
                    );
                    return false;
                }

                serialized.ApplyModifiedPropertiesWithoutUndo();
                return true;
            }

            SerializedProperty arrayProperty;
            string arrayError;
            if (!TryResolveArrayProperty(serialized, op.path, out arrayProperty, out arrayError))
            {
                diagnostics.Add(
                    new BridgeDiagnostic
                    {
                        path = target,
                        location = $"ops[{opIndex}]",
                        detail = "apply_error",
                        evidence = arrayError
                    }
                );
                return false;
            }

            if (string.Equals(opName, "insert_array_element", StringComparison.Ordinal))
            {
                if (op.index < 0 || op.index > arrayProperty.arraySize)
                {
                    diagnostics.Add(
                        new BridgeDiagnostic
                        {
                            path = target,
                            location = $"ops[{opIndex}].index",
                            detail = "apply_error",
                            evidence = $"insert index {op.index} is out of bounds"
                        }
                    );
                    return false;
                }
                arrayProperty.InsertArrayElementAtIndex(op.index);
                SerializedProperty inserted = arrayProperty.GetArrayElementAtIndex(op.index);
                if (!string.IsNullOrWhiteSpace(op.value_kind))
                {
                    string insertValueError;
                    if (!TryAssignPropertyValue(inserted, op, out insertValueError))
                    {
                        diagnostics.Add(
                            new BridgeDiagnostic
                            {
                                path = target,
                                location = $"ops[{opIndex}]",
                                detail = "apply_error",
                                evidence = insertValueError
                            }
                        );
                        return false;
                    }
                }
                serialized.ApplyModifiedPropertiesWithoutUndo();
                return true;
            }

            if (op.index < 0 || op.index >= arrayProperty.arraySize)
            {
                diagnostics.Add(
                    new BridgeDiagnostic
                    {
                        path = target,
                        location = $"ops[{opIndex}].index",
                        detail = "apply_error",
                        evidence = $"remove index {op.index} is out of bounds"
                    }
                );
                return false;
            }

            int beforeSize = arrayProperty.arraySize;
            arrayProperty.DeleteArrayElementAtIndex(op.index);
            if (arrayProperty.arraySize == beforeSize)
            {
                arrayProperty.DeleteArrayElementAtIndex(op.index);
            }
            if (arrayProperty.arraySize != beforeSize - 1)
            {
                diagnostics.Add(
                    new BridgeDiagnostic
                    {
                        path = target,
                        location = $"ops[{opIndex}]",
                        detail = "apply_error",
                        evidence = "remove array element did not change array size as expected"
                    }
                );
                return false;
            }
            serialized.ApplyModifiedPropertiesWithoutUndo();
            return true;
        }
        private static bool TryApplyMutationOpToComponent(
            Component component,
            string target,
            PatchOp op,
            int opIndex,
            List<BridgeDiagnostic> diagnostics
        )
        {
            return TryApplyMutationOpToObject(component, target, op, opIndex, diagnostics);
        }
        private static bool TryResolveArrayProperty(
            SerializedObject serialized,
            string propertyPath,
            out SerializedProperty arrayProperty,
            out string error
        )
        {
            arrayProperty = null;
            error = string.Empty;
            if (string.IsNullOrWhiteSpace(propertyPath))
            {
                error = "array operation path is empty";
                return false;
            }
            if (propertyPath.EndsWith(".Array.size", StringComparison.Ordinal))
            {
                error = "array operation path must target '.Array.data'; use set with '.Array.size' for resize";
                return false;
            }
            if (propertyPath.IndexOf(".Array.data[", StringComparison.Ordinal) >= 0)
            {
                error = "array operation path must target the array itself; remove element index from the path";
                return false;
            }
            if (!propertyPath.EndsWith(ArrayDataSuffix, StringComparison.Ordinal))
            {
                error = $"array operation path must end with '{ArrayDataSuffix}'";
                return false;
            }
            string arrayPath = propertyPath.Substring(0, propertyPath.Length - ArrayDataSuffix.Length);
            if (string.IsNullOrWhiteSpace(arrayPath))
            {
                error = "array operation path must include a property prefix before '.Array.data'";
                return false;
            }
            arrayProperty = serialized.FindProperty(arrayPath);
            if (arrayProperty == null)
            {
                error = $"array property not found: '{arrayPath}'";
                return false;
            }
            if (!arrayProperty.isArray || arrayProperty.propertyType == SerializedPropertyType.String)
            {
                error = $"property is not an array: '{arrayPath}'";
                return false;
            }
            if (TryIsFixedBufferProperty(arrayProperty, out int fixedBufferSize) && fixedBufferSize >= 0)
            {
                string detail = fixedBufferSize > 0
                    ? $"property is fixed buffer (size={fixedBufferSize}); insert/remove are not supported"
                    : "property is fixed buffer; insert/remove are not supported";
                error = $"{detail}: '{arrayPath}'";
                return false;
            }
            return true;
        }
        private static bool TryIsFixedBufferProperty(SerializedProperty property, out int fixedBufferSize)
        {
            fixedBufferSize = -1;
            if (property == null)
            {
                return false;
            }
            if (SerializedPropertyIsFixedBufferProperty != null)
            {
                try
                {
                    object rawIsFixedBuffer = SerializedPropertyIsFixedBufferProperty.GetValue(property, null);
                    if (rawIsFixedBuffer is bool boolValue && !boolValue)
                    {
                        return false;
                    }
                    if (rawIsFixedBuffer is bool)
                    {
                        fixedBufferSize = 0;
                    }
                }
                catch (Exception ex)
                {
                    Debug.LogWarning($"[PrefabSentinel] TryIsFixedBufferProperty: {ex.GetType().Name}: {ex.Message}");
                }
            }
            if (SerializedPropertyFixedBufferSizeProperty != null)
            {
                try
                {
                    object rawSize = SerializedPropertyFixedBufferSizeProperty.GetValue(property, null);
                    if (rawSize is int size && size > 0)
                    {
                        fixedBufferSize = size;
                        return true;
                    }
                }
                catch (Exception ex)
                {
                    Debug.LogWarning($"[PrefabSentinel] TryIsFixedBufferProperty: {ex.GetType().Name}: {ex.Message}");
                }
            }
            return fixedBufferSize == 0;
        }
        private static bool TryAssignPropertyValue(
            SerializedProperty property,
            PatchOp op,
            out string error
        )
        {
            error = string.Empty;
            string valueKind = (op.value_kind ?? string.Empty).Trim();
            switch (property.propertyType)
            {
                case SerializedPropertyType.Integer:
                {
                    int intValue;
                    if (!TryReadIntegerValue(op, valueKind, out intValue, out error))
                    {
                        return false;
                    }
                    property.intValue = intValue;
                    return true;
                }
                case SerializedPropertyType.Float:
                {
                    float floatValue;
                    if (!TryReadFloatValue(op, valueKind, out floatValue, out error))
                    {
                        return false;
                    }
                    property.floatValue = floatValue;
                    return true;
                }
                case SerializedPropertyType.Boolean:
                {
                    bool boolValue;
                    if (!TryReadBoolValue(op, valueKind, out boolValue, out error))
                    {
                        return false;
                    }
                    property.boolValue = boolValue;
                    return true;
                }
                case SerializedPropertyType.Character:
                {
                    int charValue;
                    if (!TryReadCharacterValue(op, valueKind, out charValue, out error))
                    {
                        return false;
                    }
                    property.intValue = charValue;
                    return true;
                }
                case SerializedPropertyType.String:
                    if (string.Equals(valueKind, "string", StringComparison.Ordinal))
                    {
                        property.stringValue = op.value_string ?? string.Empty;
                        return true;
                    }
                    if (string.Equals(valueKind, "null", StringComparison.Ordinal))
                    {
                        property.stringValue = string.Empty;
                        return true;
                    }
                    error = "string property requires value_kind='string' or 'null'";
                    return false;
                case SerializedPropertyType.Enum:
                {
                    int enumIndex;
                    if (!TryReadEnumValue(property, op, valueKind, out enumIndex, out error))
                    {
                        return false;
                    }
                    property.enumValueIndex = enumIndex;
                    return true;
                }
                case SerializedPropertyType.Color:
                {
                    Color colorValue;
                    if (!TryReadColorValue(op, valueKind, out colorValue, out error))
                    {
                        return false;
                    }
                    property.colorValue = colorValue;
                    return true;
                }
                case SerializedPropertyType.Vector2:
                {
                    Vector2 value;
                    if (!TryReadVector2Value(op, valueKind, out value, out error))
                    {
                        return false;
                    }
                    property.vector2Value = value;
                    return true;
                }
                case SerializedPropertyType.Vector3:
                {
                    Vector3 value;
                    if (!TryReadVector3Value(op, valueKind, out value, out error))
                    {
                        return false;
                    }
                    property.vector3Value = value;
                    return true;
                }
                case SerializedPropertyType.Vector4:
                {
                    Vector4 value;
                    if (!TryReadVector4Value(op, valueKind, out value, out error))
                    {
                        return false;
                    }
                    property.vector4Value = value;
                    return true;
                }
                case SerializedPropertyType.Vector2Int:
                {
                    Vector2Int value;
                    if (!TryReadVector2IntValue(op, valueKind, out value, out error))
                    {
                        return false;
                    }
                    property.vector2IntValue = value;
                    return true;
                }
                case SerializedPropertyType.Vector3Int:
                {
                    Vector3Int value;
                    if (!TryReadVector3IntValue(op, valueKind, out value, out error))
                    {
                        return false;
                    }
                    property.vector3IntValue = value;
                    return true;
                }
                case SerializedPropertyType.Rect:
                {
                    Rect value;
                    if (!TryReadRectValue(op, valueKind, out value, out error))
                    {
                        return false;
                    }
                    property.rectValue = value;
                    return true;
                }
                case SerializedPropertyType.RectInt:
                {
                    RectInt value;
                    if (!TryReadRectIntValue(op, valueKind, out value, out error))
                    {
                        return false;
                    }
                    property.rectIntValue = value;
                    return true;
                }
                case SerializedPropertyType.Bounds:
                {
                    Bounds value;
                    if (!TryReadBoundsValue(op, valueKind, out value, out error))
                    {
                        return false;
                    }
                    property.boundsValue = value;
                    return true;
                }
                case SerializedPropertyType.BoundsInt:
                {
                    BoundsInt value;
                    if (!TryReadBoundsIntValue(op, valueKind, out value, out error))
                    {
                        return false;
                    }
                    property.boundsIntValue = value;
                    return true;
                }
                case SerializedPropertyType.Quaternion:
                {
                    Quaternion value;
                    if (!TryReadQuaternionValue(op, valueKind, out value, out error))
                    {
                        return false;
                    }
                    property.quaternionValue = value;
                    return true;
                }
                case SerializedPropertyType.AnimationCurve:
                {
                    AnimationCurve value;
                    if (!TryReadAnimationCurveValue(op, valueKind, out value, out error))
                    {
                        return false;
                    }
                    property.animationCurveValue = value;
                    return true;
                }
                case SerializedPropertyType.Gradient:
                {
                    object value;
                    if (!TryReadGradientValue(op, valueKind, out value, out error))
                    {
                        return false;
                    }
                    if (SerializedPropertyGradientValueProperty == null)
                    {
                        error = "Gradient property is not supported in this Unity version";
                        return false;
                    }
                    try
                    {
                        SerializedPropertyGradientValueProperty.SetValue(property, value, null);
                    }
                    catch (Exception ex)
                    {
                        error = $"failed to assign Gradient value: {ex.Message}";
                        return false;
                    }
                    return true;
                }
                case SerializedPropertyType.ObjectReference:
                {
                    if (string.Equals(valueKind, "handle", StringComparison.Ordinal))
                    {
                        if (s_currentHandles == null)
                        {
                            error = "handle-based ObjectReference is only supported in create mode";
                            return false;
                        }
                        string handleName = (op.value_string ?? string.Empty).Trim();
                        UnityEngine.Object handleObj;
                        string handleError;
                        if (!TryResolveHandle(handleName, s_currentHandles, out handleObj, out handleError))
                        {
                            error = $"ObjectReference handle resolution failed: {handleError}";
                            return false;
                        }
                        property.objectReferenceValue = handleObj;
                        return true;
                    }
                    UnityEngine.Object referenceValue;
                    if (!TryReadObjectReferenceValue(op, valueKind, out referenceValue, out error))
                    {
                        return false;
                    }
                    property.objectReferenceValue = referenceValue;
                    return true;
                }
                case SerializedPropertyType.ExposedReference:
                {
                    if (string.Equals(valueKind, "handle", StringComparison.Ordinal))
                    {
                        if (s_currentHandles == null)
                        {
                            error = "handle-based ExposedReference is only supported in create mode";
                            return false;
                        }
                        string handleName = (op.value_string ?? string.Empty).Trim();
                        UnityEngine.Object handleObj;
                        string handleError;
                        if (!TryResolveHandle(handleName, s_currentHandles, out handleObj, out handleError))
                        {
                            error = $"ExposedReference handle resolution failed: {handleError}";
                            return false;
                        }
                        property.exposedReferenceValue = handleObj;
                        return true;
                    }
                    UnityEngine.Object referenceValue;
                    if (!TryReadObjectReferenceValue(op, valueKind, out referenceValue, out error))
                    {
                        return false;
                    }
                    property.exposedReferenceValue = referenceValue;
                    return true;
                }
                case SerializedPropertyType.LayerMask:
                case SerializedPropertyType.ArraySize:
                {
                    int intValue;
                    if (!TryReadIntegerValue(op, valueKind, out intValue, out error))
                    {
                        return false;
                    }
                    property.intValue = intValue;
                    return true;
                }
                case SerializedPropertyType.ManagedReference:
                {
                    object managedReferenceValue;
                    if (
                        !TryReadManagedReferenceValue(
                            property,
                            op,
                            valueKind,
                            out managedReferenceValue,
                            out error
                        )
                    )
                    {
                        return false;
                    }
                    property.managedReferenceValue = managedReferenceValue;
                    return true;
                }
                case SerializedPropertyType.Generic:
                {
                    object genericValue;
                    if (!TryReadGenericValue(property, op, valueKind, out genericValue, out error))
                    {
                        return false;
                    }
                    try
                    {
                        property.boxedValue = genericValue;
                    }
                    catch (Exception ex)
                    {
                        error = $"failed to assign generic value: {ex.Message}";
                        return false;
                    }
                    return true;
                }
                default:
                    if (
                        string.Equals(
                            property.propertyType.ToString(),
                            "FixedBufferSize",
                            StringComparison.Ordinal
                        )
                    )
                    {
                        error = "FixedBufferSize is read-only; set individual fixed buffer elements instead";
                        return false;
                    }
                    error = $"SerializedPropertyType '{property.propertyType}' is not supported";
                    return false;
            }
        }
        private static bool TryReadCharacterValue(
            PatchOp op,
            string valueKind,
            out int value,
            out string error
        )
        {
            value = 0;
            error = string.Empty;
            if (string.Equals(valueKind, "int", StringComparison.Ordinal))
            {
                if (op.value_int < char.MinValue || op.value_int > char.MaxValue)
                {
                    error = $"character integer value is out of range: {op.value_int}";
                    return false;
                }
                value = op.value_int;
                return true;
            }
            if (string.Equals(valueKind, "string", StringComparison.Ordinal))
            {
                string raw = op.value_string ?? string.Empty;
                if (raw.Length != 1)
                {
                    error = "character property requires single-character value_string";
                    return false;
                }
                value = raw[0];
                return true;
            }
            error = "character property requires value_kind='int' or 'string'";
            return false;
        }
        private static bool TryReadIntegerValue(
            PatchOp op,
            string valueKind,
            out int value,
            out string error
        )
        {
            value = 0;
            error = string.Empty;
            if (string.Equals(valueKind, "int", StringComparison.Ordinal))
            {
                value = op.value_int;
                return true;
            }
            if (string.Equals(valueKind, "float", StringComparison.Ordinal))
            {
                float rounded = Mathf.Round(op.value_float);
                if (!Mathf.Approximately(rounded, op.value_float))
                {
                    error = "integer property requires a whole-number float value";
                    return false;
                }
                value = (int)rounded;
                return true;
            }
            if (string.Equals(valueKind, "bool", StringComparison.Ordinal))
            {
                value = op.value_bool ? 1 : 0;
                return true;
            }
            if (string.Equals(valueKind, "string", StringComparison.Ordinal))
            {
                if (int.TryParse(op.value_string, NumberStyles.Integer, CultureInfo.InvariantCulture, out value))
                {
                    return true;
                }
                error = $"failed to parse integer from value_string '{op.value_string}'";
                return false;
            }
            error = "integer property requires value_kind='int' (or compatible float/bool/string)";
            return false;
        }
        private static bool TryReadFloatValue(
            PatchOp op,
            string valueKind,
            out float value,
            out string error
        )
        {
            value = 0f;
            error = string.Empty;
            if (string.Equals(valueKind, "float", StringComparison.Ordinal))
            {
                value = op.value_float;
                return true;
            }
            if (string.Equals(valueKind, "int", StringComparison.Ordinal))
            {
                value = op.value_int;
                return true;
            }
            if (string.Equals(valueKind, "bool", StringComparison.Ordinal))
            {
                value = op.value_bool ? 1f : 0f;
                return true;
            }
            if (string.Equals(valueKind, "string", StringComparison.Ordinal))
            {
                if (
                    float.TryParse(
                        op.value_string,
                        NumberStyles.Float | NumberStyles.AllowThousands,
                        CultureInfo.InvariantCulture,
                        out value
                    )
                )
                {
                    return true;
                }
                error = $"failed to parse float from value_string '{op.value_string}'";
                return false;
            }
            error = "float property requires value_kind='float' (or compatible int/bool/string)";
            return false;
        }
        private static bool TryReadBoolValue(
            PatchOp op,
            string valueKind,
            out bool value,
            out string error
        )
        {
            value = false;
            error = string.Empty;
            if (string.Equals(valueKind, "bool", StringComparison.Ordinal))
            {
                value = op.value_bool;
                return true;
            }
            if (string.Equals(valueKind, "int", StringComparison.Ordinal))
            {
                value = op.value_int != 0;
                return true;
            }
            if (string.Equals(valueKind, "string", StringComparison.Ordinal))
            {
                if (bool.TryParse(op.value_string, out value))
                {
                    return true;
                }
                int intValue;
                if (int.TryParse(op.value_string, NumberStyles.Integer, CultureInfo.InvariantCulture, out intValue))
                {
                    value = intValue != 0;
                    return true;
                }
                error = $"failed to parse bool from value_string '{op.value_string}'";
                return false;
            }
            error = "boolean property requires value_kind='bool' (or compatible int/string)";
            return false;
        }
        private static bool TryReadEnumValue(
            SerializedProperty property,
            PatchOp op,
            string valueKind,
            out int enumIndex,
            out string error
        )
        {
            enumIndex = 0;
            error = string.Empty;
            if (string.Equals(valueKind, "int", StringComparison.Ordinal))
            {
                enumIndex = op.value_int;
            }
            else if (string.Equals(valueKind, "string", StringComparison.Ordinal))
            {
                string raw = op.value_string ?? string.Empty;
                for (int i = 0; i < property.enumDisplayNames.Length; i++)
                {
                    if (
                        string.Equals(property.enumDisplayNames[i], raw, StringComparison.OrdinalIgnoreCase)
                        || string.Equals(property.enumNames[i], raw, StringComparison.OrdinalIgnoreCase)
                    )
                    {
                        enumIndex = i;
                        return true;
                    }
                }
                error = $"failed to map enum value from value_string '{raw}'";
                return false;
            }
            else
            {
                error = "enum property requires value_kind='int' or 'string'";
                return false;
            }

            if (enumIndex < 0 || enumIndex >= property.enumDisplayNames.Length)
            {
                error = $"enum index out of range: {enumIndex}";
                return false;
            }
            return true;
        }
        private static bool TryReadColorValue(
            PatchOp op,
            string valueKind,
            out Color value,
            out string error
        )
        {
            value = default(Color);
            error = string.Empty;
            if (string.Equals(valueKind, "string", StringComparison.Ordinal))
            {
                if (ColorUtility.TryParseHtmlString(op.value_string, out value))
                {
                    return true;
                }
                error = $"failed to parse color from value_string '{op.value_string}'";
                return false;
            }
            if (string.Equals(valueKind, "json", StringComparison.Ordinal))
            {
                ColorPayload payload;
                if (!TryParseJsonPayload(op.value_json, out payload, out error))
                {
                    error = $"failed to parse color value_json: {error}";
                    return false;
                }
                value = new Color(payload.r, payload.g, payload.b, payload.a);
                return true;
            }

            error = "color property requires value_kind='string' (#RRGGBB/#RRGGBBAA) or 'json'";
            return false;
        }
        private static bool TryReadVector2Value(
            PatchOp op,
            string valueKind,
            out Vector2 value,
            out string error
        )
        {
            value = default(Vector2);
            error = string.Empty;
            if (!string.Equals(valueKind, "json", StringComparison.Ordinal))
            {
                error = "Vector2 property requires value_kind='json' with {x,y}";
                return false;
            }
            Vector2Payload payload;
            if (!TryParseJsonPayload(op.value_json, out payload, out error))
            {
                error = $"failed to parse Vector2 value_json: {error}";
                return false;
            }
            value = new Vector2(payload.x, payload.y);
            return true;
        }
        private static bool TryReadVector3Value(
            PatchOp op,
            string valueKind,
            out Vector3 value,
            out string error
        )
        {
            value = default(Vector3);
            error = string.Empty;
            if (!string.Equals(valueKind, "json", StringComparison.Ordinal))
            {
                error = "Vector3 property requires value_kind='json' with {x,y,z}";
                return false;
            }
            Vector3Payload payload;
            if (!TryParseJsonPayload(op.value_json, out payload, out error))
            {
                error = $"failed to parse Vector3 value_json: {error}";
                return false;
            }
            value = new Vector3(payload.x, payload.y, payload.z);
            return true;
        }
        private static bool TryReadVector4Value(
            PatchOp op,
            string valueKind,
            out Vector4 value,
            out string error
        )
        {
            value = default(Vector4);
            error = string.Empty;
            if (!string.Equals(valueKind, "json", StringComparison.Ordinal))
            {
                error = "Vector4 property requires value_kind='json' with {x,y,z,w}";
                return false;
            }
            Vector4Payload payload;
            if (!TryParseJsonPayload(op.value_json, out payload, out error))
            {
                error = $"failed to parse Vector4 value_json: {error}";
                return false;
            }
            value = new Vector4(payload.x, payload.y, payload.z, payload.w);
            return true;
        }
        private static bool TryReadVector2IntValue(
            PatchOp op,
            string valueKind,
            out Vector2Int value,
            out string error
        )
        {
            value = default(Vector2Int);
            error = string.Empty;
            if (!string.Equals(valueKind, "json", StringComparison.Ordinal))
            {
                error = "Vector2Int property requires value_kind='json' with {x,y}";
                return false;
            }
            Vector2IntPayload payload;
            if (!TryParseJsonPayload(op.value_json, out payload, out error))
            {
                error = $"failed to parse Vector2Int value_json: {error}";
                return false;
            }
            value = new Vector2Int(payload.x, payload.y);
            return true;
        }
        private static bool TryReadVector3IntValue(
            PatchOp op,
            string valueKind,
            out Vector3Int value,
            out string error
        )
        {
            value = default(Vector3Int);
            error = string.Empty;
            if (!string.Equals(valueKind, "json", StringComparison.Ordinal))
            {
                error = "Vector3Int property requires value_kind='json' with {x,y,z}";
                return false;
            }
            Vector3IntPayload payload;
            if (!TryParseJsonPayload(op.value_json, out payload, out error))
            {
                error = $"failed to parse Vector3Int value_json: {error}";
                return false;
            }
            value = new Vector3Int(payload.x, payload.y, payload.z);
            return true;
        }
        private static bool TryReadRectIntValue(
            PatchOp op,
            string valueKind,
            out RectInt value,
            out string error
        )
        {
            value = default(RectInt);
            error = string.Empty;
            if (!string.Equals(valueKind, "json", StringComparison.Ordinal))
            {
                error = "RectInt property requires value_kind='json' with {x,y,width,height}";
                return false;
            }
            RectIntPayload payload;
            if (!TryParseJsonPayload(op.value_json, out payload, out error))
            {
                error = $"failed to parse RectInt value_json: {error}";
                return false;
            }
            value = new RectInt(payload.x, payload.y, payload.width, payload.height);
            return true;
        }
        private static bool TryReadBoundsIntValue(
            PatchOp op,
            string valueKind,
            out BoundsInt value,
            out string error
        )
        {
            value = default(BoundsInt);
            error = string.Empty;
            if (!string.Equals(valueKind, "json", StringComparison.Ordinal))
            {
                error = "BoundsInt property requires value_kind='json' with {position:{x,y,z},size:{x,y,z}}";
                return false;
            }
            BoundsIntPayload payload;
            if (!TryParseJsonPayload(op.value_json, out payload, out error))
            {
                error = $"failed to parse BoundsInt value_json: {error}";
                return false;
            }
            if (payload.position == null || payload.size == null)
            {
                error = "BoundsInt value_json requires both position and size objects";
                return false;
            }
            value = new BoundsInt(
                new Vector3Int(payload.position.x, payload.position.y, payload.position.z),
                new Vector3Int(payload.size.x, payload.size.y, payload.size.z)
            );
            return true;
        }
        private static bool TryReadRectValue(
            PatchOp op,
            string valueKind,
            out Rect value,
            out string error
        )
        {
            value = default(Rect);
            error = string.Empty;
            if (!string.Equals(valueKind, "json", StringComparison.Ordinal))
            {
                error = "Rect property requires value_kind='json' with {x,y,width,height}";
                return false;
            }
            RectPayload payload;
            if (!TryParseJsonPayload(op.value_json, out payload, out error))
            {
                error = $"failed to parse Rect value_json: {error}";
                return false;
            }
            value = new Rect(payload.x, payload.y, payload.width, payload.height);
            return true;
        }
        private static bool TryReadBoundsValue(
            PatchOp op,
            string valueKind,
            out Bounds value,
            out string error
        )
        {
            value = default(Bounds);
            error = string.Empty;
            if (!string.Equals(valueKind, "json", StringComparison.Ordinal))
            {
                error = "Bounds property requires value_kind='json' with {center:{x,y,z},size:{x,y,z}}";
                return false;
            }
            BoundsPayload payload;
            if (!TryParseJsonPayload(op.value_json, out payload, out error))
            {
                error = $"failed to parse Bounds value_json: {error}";
                return false;
            }
            if (payload.center == null || payload.size == null)
            {
                error = "Bounds value_json requires both center and size objects";
                return false;
            }
            value = new Bounds(
                new Vector3(payload.center.x, payload.center.y, payload.center.z),
                new Vector3(payload.size.x, payload.size.y, payload.size.z)
            );
            return true;
        }
        private static bool TryReadQuaternionValue(
            PatchOp op,
            string valueKind,
            out Quaternion value,
            out string error
        )
        {
            value = default(Quaternion);
            error = string.Empty;
            if (!string.Equals(valueKind, "json", StringComparison.Ordinal))
            {
                error = "Quaternion property requires value_kind='json' with {x,y,z,w}";
                return false;
            }
            QuaternionPayload payload;
            if (!TryParseJsonPayload(op.value_json, out payload, out error))
            {
                error = $"failed to parse Quaternion value_json: {error}";
                return false;
            }
            value = new Quaternion(payload.x, payload.y, payload.z, payload.w);
            return true;
        }
        private static bool TryReadAnimationCurveValue(
            PatchOp op,
            string valueKind,
            out AnimationCurve value,
            out string error
        )
        {
            value = null;
            error = string.Empty;
            if (string.Equals(valueKind, "null", StringComparison.Ordinal))
            {
                return true;
            }
            if (!string.Equals(valueKind, "json", StringComparison.Ordinal))
            {
                error = "AnimationCurve property requires value_kind='null' or 'json'";
                return false;
            }

            AnimationCurvePayload payload;
            if (!TryParseJsonPayload(op.value_json, out payload, out error))
            {
                error = $"failed to parse AnimationCurve value_json: {error}";
                return false;
            }
            if (payload.keys == null)
            {
                error = "AnimationCurve value_json requires keys array";
                return false;
            }
            if (!Enum.IsDefined(typeof(WrapMode), payload.pre_wrap_mode))
            {
                error = $"AnimationCurve pre_wrap_mode is invalid: {payload.pre_wrap_mode}";
                return false;
            }
            if (!Enum.IsDefined(typeof(WrapMode), payload.post_wrap_mode))
            {
                error = $"AnimationCurve post_wrap_mode is invalid: {payload.post_wrap_mode}";
                return false;
            }

            Keyframe[] keys = new Keyframe[payload.keys.Length];
            for (int i = 0; i < payload.keys.Length; i++)
            {
                AnimationCurveKeyPayload keyPayload = payload.keys[i];
                if (keyPayload == null)
                {
                    error = $"AnimationCurve key at index {i} is null";
                    return false;
                }
                keys[i] = new Keyframe(
                    keyPayload.time,
                    keyPayload.value,
                    keyPayload.in_tangent,
                    keyPayload.out_tangent
                );
            }

            value = new AnimationCurve(keys);
            value.preWrapMode = (WrapMode)payload.pre_wrap_mode;
            value.postWrapMode = (WrapMode)payload.post_wrap_mode;
            return true;
        }
        private static bool TryReadGradientValue(
            PatchOp op,
            string valueKind,
            out object value,
            out string error
        )
        {
            value = null;
            error = string.Empty;
            if (string.Equals(valueKind, "null", StringComparison.Ordinal))
            {
                return true;
            }
            if (!string.Equals(valueKind, "json", StringComparison.Ordinal))
            {
                error = "Gradient property requires value_kind='null' or 'json'";
                return false;
            }

            GradientPayload payload;
            if (!TryParseJsonPayload(op.value_json, out payload, out error))
            {
                error = $"failed to parse Gradient value_json: {error}";
                return false;
            }
            if (payload.color_keys == null)
            {
                error = "Gradient value_json requires color_keys array";
                return false;
            }
            if (payload.alpha_keys == null)
            {
                error = "Gradient value_json requires alpha_keys array";
                return false;
            }

            GradientColorKey[] colorKeys = new GradientColorKey[payload.color_keys.Length];
            for (int i = 0; i < payload.color_keys.Length; i++)
            {
                GradientColorKeyPayload colorKeyPayload = payload.color_keys[i];
                if (colorKeyPayload == null || colorKeyPayload.color == null)
                {
                    error = $"Gradient color key at index {i} is null";
                    return false;
                }
                colorKeys[i] = new GradientColorKey(
                    new Color(
                        colorKeyPayload.color.r,
                        colorKeyPayload.color.g,
                        colorKeyPayload.color.b,
                        colorKeyPayload.color.a
                    ),
                    colorKeyPayload.time
                );
            }

            GradientAlphaKey[] alphaKeys = new GradientAlphaKey[payload.alpha_keys.Length];
            for (int i = 0; i < payload.alpha_keys.Length; i++)
            {
                GradientAlphaKeyPayload alphaKeyPayload = payload.alpha_keys[i];
                if (alphaKeyPayload == null)
                {
                    error = $"Gradient alpha key at index {i} is null";
                    return false;
                }
                alphaKeys[i] = new GradientAlphaKey(alphaKeyPayload.alpha, alphaKeyPayload.time);
            }

            Gradient gradient = new Gradient();
            try
            {
                gradient.SetKeys(colorKeys, alphaKeys);
            }
            catch (Exception ex)
            {
                error = $"failed to assign Gradient keys: {ex.Message}";
                return false;
            }

            PropertyInfo modeProperty = typeof(Gradient).GetProperty("mode", BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic);
            if (modeProperty != null && modeProperty.CanWrite)
            {
                try
                {
                    Type modeType = modeProperty.PropertyType;
                    if (modeType.IsEnum && Enum.IsDefined(modeType, payload.mode))
                    {
                        object modeValue = Enum.ToObject(modeType, payload.mode);
                        modeProperty.SetValue(gradient, modeValue, null);
                    }
                }
                catch (Exception ex)
                {
                    Debug.LogWarning($"[PrefabSentinel] TryReadGradientValue: {ex.GetType().Name}: {ex.Message}");
                }
            }

            value = gradient;
            return true;
        }
        private const string BuiltinDefaultResourcesPath = "Library/unity default resources";
        private const string BuiltinExtraResourcesPath = "Resources/unity_builtin_extra";
        private static readonly string[] BuiltinAssetPaths = new[]
        {
            BuiltinDefaultResourcesPath,
            BuiltinExtraResourcesPath,
        };
        private readonly struct BuiltinAssetEntry
        {
            public readonly System.Type type;
            public readonly string name;
            public readonly bool isExtra; // true = unity_builtin_extra, false = unity default resources

            public BuiltinAssetEntry(System.Type type, string name, bool isExtra)
            {
                this.type = type;
                this.name = name;
                this.isExtra = isExtra;
            }
        }
        private static readonly BuiltinAssetEntry[] KnownBuiltinAssets = new[]
        {
            // Resources/unity_builtin_extra — Materials
            new BuiltinAssetEntry(typeof(Material), "Default-Material.mat", true),
            new BuiltinAssetEntry(typeof(Material), "Default-Particle.mat", true),
            new BuiltinAssetEntry(typeof(Material), "Default-Line.mat", true),
            new BuiltinAssetEntry(typeof(Material), "Default-Diffuse.mat", true),
            new BuiltinAssetEntry(typeof(Material), "Default-Skybox.mat", true),
            new BuiltinAssetEntry(typeof(Material), "Sprites-Default.mat", true),
            new BuiltinAssetEntry(typeof(Material), "Sprites-Mask.mat", true),
            new BuiltinAssetEntry(typeof(Material), "Default-Terrain-Standard.mat", true),
            new BuiltinAssetEntry(typeof(Material), "Default-Terrain-Diffuse.mat", true),
            new BuiltinAssetEntry(typeof(Material), "Default-Terrain-Specular.mat", true),
            // Resources/unity_builtin_extra — Fonts
            new BuiltinAssetEntry(typeof(Font), "Arial.ttf", true),
            new BuiltinAssetEntry(typeof(Font), "LegacyRuntime.ttf", true),
            // Library/unity default resources — Meshes (safety net)
            new BuiltinAssetEntry(typeof(Mesh), "Sphere.fbx", false),
            new BuiltinAssetEntry(typeof(Mesh), "Cube.fbx", false),
            new BuiltinAssetEntry(typeof(Mesh), "Cylinder.fbx", false),
            new BuiltinAssetEntry(typeof(Mesh), "Capsule.fbx", false),
            new BuiltinAssetEntry(typeof(Mesh), "Plane.fbx", false),
            new BuiltinAssetEntry(typeof(Mesh), "Quad.fbx", false),
        };
        private static bool IsBuiltinAssetPath(string assetPath)
        {
            return string.Equals(assetPath, BuiltinDefaultResourcesPath, StringComparison.Ordinal)
                || string.Equals(assetPath, BuiltinExtraResourcesPath, StringComparison.Ordinal);
        }
        private static bool TryLoadBuiltinAssetByName(
            string guid, long fileID,
            out UnityEngine.Object value)
        {
            value = null;
            for (int i = 0; i < KnownBuiltinAssets.Length; i++)
            {
                BuiltinAssetEntry entry = KnownBuiltinAssets[i];
                UnityEngine.Object candidate;
                try
                {
                    candidate = entry.isExtra
                        ? AssetDatabase.GetBuiltinExtraResource(entry.type, entry.name)
                        : Resources.GetBuiltinResource(entry.type, entry.name);
                }
                catch (System.ArgumentException ex)
                {
                    // Deprecated assets (e.g. Arial.ttf in Unity 2022.3+) throw ArgumentException.
                    // Skip and continue to next entry.
                    Debug.Log($"[PrefabSentinel] BuiltinAssetByName: {entry.name} ({entry.type.Name}) threw ArgumentException: {ex.Message}");
                    continue;
                }
                if (candidate == null)
                {
                    Debug.Log($"[PrefabSentinel] BuiltinAssetByName: {entry.name} ({entry.type.Name}) returned null");
                    continue;
                }
                string cGuid;
                long cId;
                if (!AssetDatabase.TryGetGUIDAndLocalFileIdentifier(candidate, out cGuid, out cId))
                {
                    Debug.Log($"[PrefabSentinel] BuiltinAssetByName: {entry.name} ({entry.type.Name}) loaded '{candidate.name}' but TryGetGUIDAndLocalFileIdentifier returned false");
                    continue;
                }
                Debug.Log($"[PrefabSentinel] BuiltinAssetByName: {entry.name} ({entry.type.Name}) loaded '{candidate.name}' => guid={cGuid}, fileID={cId} (searching guid={guid}, fileID={fileID})");
                if (string.Equals(cGuid, guid, StringComparison.OrdinalIgnoreCase) && cId == fileID)
                {
                    value = candidate;
                    return true;
                }
            }
            return false;
        }
        // assetPath: kept for call-site compatibility; search uses BuiltinAssetPaths instead
        private static bool TryLoadBuiltinAsset(
            string assetPath, string guid, long fileID,
            out UnityEngine.Object value)
        {
            value = null;
            // 1. Try LoadAllAssetsAtPath on each known builtin path
            //    AssetDatabase.GUIDToAssetPath returns only one path, but builtin
            //    assets are split across two locations, so we search both explicitly.
            for (int p = 0; p < BuiltinAssetPaths.Length; p++)
            {
                UnityEngine.Object[] candidates = AssetDatabase.LoadAllAssetsAtPath(BuiltinAssetPaths[p]);
                Debug.Log($"[PrefabSentinel] LoadAllAssetsAtPath(\"{BuiltinAssetPaths[p]}\") returned {candidates.Length} candidates (searching guid={guid}, fileID={fileID})");
                for (int i = 0; i < candidates.Length; i++)
                {
                    if (candidates[i] == null) continue;
                    string cGuid;
                    long cId;
                    if (AssetDatabase.TryGetGUIDAndLocalFileIdentifier(candidates[i], out cGuid, out cId)
                        && string.Equals(cGuid, guid, StringComparison.OrdinalIgnoreCase)
                        && cId == fileID)
                    {
                        value = candidates[i];
                        return true;
                    }
                }
            }
            // 2. Try name-based loading from known builtin assets table
            //    LoadAllAssetsAtPath may return empty for unity_builtin_extra
            //    in Editor Bridge context due to lazy loading.
            if (TryLoadBuiltinAssetByName(guid, fileID, out value))
            {
                Debug.Log($"[PrefabSentinel] Resolved builtin asset via name-based loading: guid={guid}, fileID={fileID}, asset={value.name}");
                return true;
            }
            // 3. Fallback: search all loaded objects
            UnityEngine.Object[] all = Resources.FindObjectsOfTypeAll<UnityEngine.Object>();
            Debug.Log($"[PrefabSentinel] FindObjectsOfTypeAll fallback: {all.Length} objects (searching guid={guid}, fileID={fileID})");
            for (int i = 0; i < all.Length; i++)
            {
                if (all[i] == null) continue;
                string cGuid;
                long cId;
                if (AssetDatabase.TryGetGUIDAndLocalFileIdentifier(all[i], out cGuid, out cId)
                    && string.Equals(cGuid, guid, StringComparison.OrdinalIgnoreCase)
                    && cId == fileID)
                {
                    value = all[i];
                    return true;
                }
            }
            return false;
        }
        private static bool TryReadObjectReferenceValue(
            PatchOp op,
            string valueKind,
            out UnityEngine.Object value,
            out string error
        )
        {
            value = null;
            error = string.Empty;
            if (string.Equals(valueKind, "null", StringComparison.Ordinal))
            {
                return true;
            }
            if (!string.Equals(valueKind, "json", StringComparison.Ordinal))
            {
                error = "ObjectReference requires value_kind='null' or 'json' ({guid,file_id})";
                return false;
            }

            ObjectReferencePayload payload;
            if (!TryParseJsonPayload(op.value_json, out payload, out error))
            {
                error = $"failed to parse ObjectReference value_json: {error}";
                return false;
            }

            string guid = (payload.guid ?? string.Empty).Trim();
            if (string.IsNullOrWhiteSpace(guid))
            {
                error = "ObjectReference value_json requires non-empty guid";
                return false;
            }

            // Accept both "fileID" (Unity native) and "file_id" (snake_case) JSON keys
            long effectiveFileId = payload.fileID != 0 ? payload.fileID : payload.file_id;
            if (effectiveFileId < 0)
            {
                error = "ObjectReference file_id must be >= 0";
                return false;
            }

            string assetPath = AssetDatabase.GUIDToAssetPath(guid);
            if (string.IsNullOrWhiteSpace(assetPath))
            {
                error = $"ObjectReference guid could not be resolved: '{guid}'";
                return false;
            }

            // Builtin resource paths require special loading
            if (IsBuiltinAssetPath(assetPath))
            {
                if (effectiveFileId == 0)
                {
                    error = $"ObjectReference fileID 0 is not valid for builtin path '{assetPath}'";
                    return false;
                }
                if (TryLoadBuiltinAsset(assetPath, guid, effectiveFileId, out value))
                    return true;
                error = $"ObjectReference builtin asset not found: guid='{guid}', fileID={effectiveFileId}";
                return false;
            }

            if (effectiveFileId == 0)
            {
                value = AssetDatabase.LoadMainAssetAtPath(assetPath);
                if (value == null)
                {
                    error = $"ObjectReference main asset not found at '{assetPath}'";
                    return false;
                }
                return true;
            }

            UnityEngine.Object[] candidates = AssetDatabase.LoadAllAssetsAtPath(assetPath);
            for (int i = 0; i < candidates.Length; i++)
            {
                UnityEngine.Object candidate = candidates[i];
                if (candidate == null)
                {
                    continue;
                }

                string candidateGuid;
                long localFileId;
                if (!AssetDatabase.TryGetGUIDAndLocalFileIdentifier(candidate, out candidateGuid, out localFileId))
                {
                    continue;
                }
                if (
                    string.Equals(candidateGuid, guid, StringComparison.OrdinalIgnoreCase)
                    && localFileId == effectiveFileId
                )
                {
                    value = candidate;
                    return true;
                }
            }

            error = $"ObjectReference file_id '{effectiveFileId}' was not found in asset '{assetPath}'";
            return false;
        }
        private static bool TryReadManagedReferenceValue(
            SerializedProperty property,
            PatchOp op,
            string valueKind,
            out object value,
            out string error
        )
        {
            value = null;
            error = string.Empty;
            if (string.Equals(valueKind, "null", StringComparison.Ordinal))
            {
                return true;
            }
            if (!string.Equals(valueKind, "json", StringComparison.Ordinal))
            {
                error = "ManagedReference requires value_kind='null' or 'json'";
                return false;
            }

            Type targetType;
            if (!TryResolveManagedReferenceTargetType(property, op.value_json, out targetType, out error))
            {
                return false;
            }
            if (!TryDecodeJsonToType(op.value_json, targetType, out value, out error))
            {
                error = $"failed to parse ManagedReference value_json: {error}";
                return false;
            }
            return true;
        }
        private static bool TryReadGenericValue(
            SerializedProperty property,
            PatchOp op,
            string valueKind,
            out object value,
            out string error
        )
        {
            value = null;
            error = string.Empty;
            object current;
            try
            {
                current = property.boxedValue;
            }
            catch (Exception ex)
            {
                error = $"failed to read generic boxedValue: {ex.Message}";
                return false;
            }

            if (string.Equals(valueKind, "null", StringComparison.Ordinal))
            {
                if (current != null && current.GetType().IsValueType)
                {
                    error = $"generic value type '{current.GetType().FullName}' cannot be set to null";
                    return false;
                }
                return true;
            }
            if (!string.Equals(valueKind, "json", StringComparison.Ordinal))
            {
                error = "generic property requires value_kind='json' (or 'null' for nullable references)";
                return false;
            }

            if (current == null)
            {
                error =
                    "generic property boxedValue is null; set child properties directly or use ManagedReference with __type";
                return false;
            }
            Type targetType = current.GetType();
            if (!TryDecodeJsonToType(op.value_json, targetType, out value, out error))
            {
                error = $"failed to parse generic value_json for type '{targetType.FullName}': {error}";
                return false;
            }
            return true;
        }
        private static bool TryResolveType(string rawTypeName, out Type type, out string error)
        {
            type = null;
            error = string.Empty;
            string candidate = (rawTypeName ?? string.Empty).Trim();
            if (string.IsNullOrWhiteSpace(candidate))
            {
                error = "type name is empty";
                return false;
            }

            type = Type.GetType(candidate, false);
            if (type != null)
            {
                return true;
            }

            int commaIndex = candidate.IndexOf(",");
            string typeName = commaIndex >= 0 ? candidate.Substring(0, commaIndex).Trim() : candidate;
            string assemblyName = commaIndex >= 0 ? candidate.Substring(commaIndex + 1).Trim() : string.Empty;

            Assembly[] assemblies = AppDomain.CurrentDomain.GetAssemblies();
            for (int i = 0; i < assemblies.Length; i++)
            {
                Assembly assembly = assemblies[i];
                if (!string.IsNullOrWhiteSpace(assemblyName))
                {
                    string shortName = assembly.GetName().Name ?? string.Empty;
                    string fullName = assembly.FullName ?? string.Empty;
                    if (
                        !string.Equals(shortName, assemblyName, StringComparison.Ordinal)
                        && !string.Equals(fullName, assemblyName, StringComparison.Ordinal)
                    )
                    {
                        continue;
                    }
                }

                type = assembly.GetType(typeName, false);
                if (type != null)
                {
                    return true;
                }
            }

            if (string.IsNullOrWhiteSpace(assemblyName))
            {
                error = $"type '{typeName}' was not found";
            }
            else
            {
                error = $"type '{typeName}' was not found in assembly '{assemblyName}'";
            }
            return false;
        }
        private static bool TryDecodeJsonToType(
            string raw,
            Type targetType,
            out object value,
            out string error
        )
        {
            value = null;
            error = string.Empty;
            if (string.IsNullOrWhiteSpace(raw))
            {
                error = "value_json is empty";
                return false;
            }
            if (targetType == null)
            {
                error = "target type is null";
                return false;
            }

            try
            {
                value = JsonUtility.FromJson(raw, targetType);
            }
            catch (Exception ex)
            {
                error = ex.Message;
                return false;
            }

            if (value != null)
            {
                return true;
            }
            if (!targetType.IsValueType)
            {
                error = $"value_json decoded to null for type '{targetType.FullName}'";
                return false;
            }

            try
            {
                value = Activator.CreateInstance(targetType);
                return true;
            }
            catch (Exception ex)
            {
                error = $"failed to create default instance for value type '{targetType.FullName}': {ex.Message}";
                return false;
            }
        }
        private static bool TryParseJsonPayload<T>(
            string raw,
            out T payload,
            out string error
        ) where T : class
        {
            payload = null;
            error = string.Empty;
            if (string.IsNullOrWhiteSpace(raw))
            {
                error = "value_json is empty";
                return false;
            }

            try
            {
                payload = JsonUtility.FromJson<T>(raw);
            }
            catch (Exception ex)
            {
                error = ex.Message;
                return false;
            }

            if (payload == null)
            {
                error = "value_json decoded to null";
                return false;
            }
            return true;
        }
    }
}
