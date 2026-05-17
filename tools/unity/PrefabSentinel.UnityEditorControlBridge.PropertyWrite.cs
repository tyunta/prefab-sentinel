using System;
using UnityEditor;
using UnityEngine;

// Unified property-write layer (issue #24) — the single per-type
// application of a textual value to a live SerializedProperty. The
// set-property handler, the component-creation initial-property path,
// and the UdonSharp field-write handlers all route value application
// through WritePropertyValue, so every property type — including
// quaternion and object reference — is covered by one implementation
// with one error-classification boundary.
namespace PrefabSentinel
{
    public static partial class UnityEditorControlBridge
    {
        /// <summary>
        /// The single property-write layer (issue #24): applies
        /// <paramref name="value"/> to <paramref name="prop"/> by type and
        /// reports a structured <see cref="PropertyWriteResult"/>. Every
        /// property-write call site routes through here, so all types —
        /// including quaternion and object reference — are covered
        /// uniformly. Textual parsing is delegated to the Unity-free
        /// <see cref="PropertyValueParser"/> / <see cref="QuaternionInputValidator"/>;
        /// classification is try-parse-based. For an object-reference
        /// target <paramref name="value"/> is a reference path.
        /// </summary>
        private static PropertyWriteResult WritePropertyValue(
            SerializedProperty prop, string value)
        {
            switch (prop.propertyType)
            {
                case SerializedPropertyType.Enum:
                    return WriteEnumValue(prop, value);
                case SerializedPropertyType.Quaternion:
                    return WriteQuaternionValue(prop, value);
                case SerializedPropertyType.ObjectReference:
                    return WriteObjectReferenceValue(prop, value);
            }

            if (!TryMapSerializedPropertyKind(prop.propertyType, out SerializedPropertyKind kind))
                return PropertyWriteResult.Failure(
                    "EDITOR_CTRL_SET_PROP_TYPE_MISMATCH",
                    $"Unsupported property type: {prop.propertyType}");
            if (!PropertyValueParser.TryParse(kind, value, out ParsedPropertyValue parsed))
                return PropertyWriteResult.Failure(
                    "EDITOR_CTRL_SET_PROP_TYPE_MISMATCH",
                    $"Failed to parse value '{value}' for {prop.propertyType}.");

            ApplyParsedValue(prop, kind, parsed);
            return PropertyWriteResult.Ok();
        }

        private static PropertyWriteResult WriteEnumValue(
            SerializedProperty prop, string value)
        {
            // enumNames returns internal C# names (preferred for
            // programmatic input); enumDisplayNames may contain spaces.
#pragma warning disable 0618  // enumNames deprecated but intentionally used
            int idx = System.Array.IndexOf(prop.enumNames, value);
            if (idx >= 0)
            {
                prop.enumValueIndex = idx;
                return PropertyWriteResult.Ok();
            }
            if (int.TryParse(value, out int numericIndex))
            {
                prop.enumValueIndex = numericIndex;
                return PropertyWriteResult.Ok();
            }
            return PropertyWriteResult.Failure(
                "EDITOR_CTRL_SET_PROP_TYPE_MISMATCH",
                $"Enum value '{value}' not found. Valid: {string.Join(", ", prop.enumNames)}");
#pragma warning restore 0618
        }

        private static PropertyWriteResult WriteQuaternionValue(
            SerializedProperty prop, string value)
        {
            QuaternionParse q;
            try
            {
                // Issue #111: QuaternionInputValidator owns arity / unit-
                // norm validation (Tier 1-covered in the xUnit harness).
                // It parses with float.Parse, which throws on a non-
                // numeric component; this single classification site
                // converts that to a structured result rather than
                // propagating the exception to callers.
                q = QuaternionInputValidator.Validate(value);
            }
            catch (FormatException)
            {
                return PropertyWriteResult.Failure(
                    QuaternionInputValidator.TypeMismatchCode,
                    $"Quaternion requires 4 comma-separated floats (x,y,z,w); "
                    + $"'{value}' has a non-numeric component.");
            }
            if (!q.Success)
            {
                if (q.ErrorCode == QuaternionInputValidator.NotNormalizedCode)
                    return PropertyWriteResult.Failure(q.ErrorCode,
                        $"Quaternion value (x={q.X}, y={q.Y}, z={q.Z}, w={q.W}) "
                        + $"has non-unit norm; unit norm "
                        + $"(1.0 ± {QuaternionInputValidator.NormTolerance}) is required. "
                        + "Normalize the input on the caller side.");
                return PropertyWriteResult.Failure(q.ErrorCode,
                    "Quaternion requires exactly 4 comma-separated floats (x,y,z,w).");
            }
            prop.quaternionValue = new Quaternion(q.X, q.Y, q.Z, q.W);
            return PropertyWriteResult.Ok();
        }

        private static PropertyWriteResult WriteObjectReferenceValue(
            SerializedProperty prop, string referencePath)
        {
            var (obj, refError) = ResolveObjectReference(referencePath);
            if (obj == null)
                return PropertyWriteResult.Failure(
                    "EDITOR_CTRL_SET_PROP_REF_NOT_FOUND",
                    refError ?? $"Object reference not found: {referencePath}");
            prop.objectReferenceValue = obj;
            return PropertyWriteResult.Ok();
        }

        /// <summary>
        /// Apply a <see cref="PropertyValueParser"/> result to the live
        /// property for the parser-covered scalar and vector kinds.
        /// </summary>
        private static void ApplyParsedValue(
            SerializedProperty prop, SerializedPropertyKind kind,
            ParsedPropertyValue parsed)
        {
            switch (kind)
            {
                case SerializedPropertyKind.Integer:
                case SerializedPropertyKind.IntSize:
                    prop.intValue = parsed.IntValue;
                    break;
                case SerializedPropertyKind.Float:
                    prop.floatValue = parsed.FloatValue;
                    break;
                case SerializedPropertyKind.Boolean:
                    prop.boolValue = parsed.BoolValue;
                    break;
                case SerializedPropertyKind.String:
                    prop.stringValue = parsed.StringValue;
                    break;
                case SerializedPropertyKind.Vector2:
                    prop.vector2Value = new Vector2(
                        parsed.Components[0], parsed.Components[1]);
                    break;
                case SerializedPropertyKind.Vector3:
                    prop.vector3Value = new Vector3(
                        parsed.Components[0], parsed.Components[1],
                        parsed.Components[2]);
                    break;
                case SerializedPropertyKind.Vector4:
                    prop.vector4Value = new Vector4(
                        parsed.Components[0], parsed.Components[1],
                        parsed.Components[2], parsed.Components[3]);
                    break;
                case SerializedPropertyKind.Color:
                    prop.colorValue = new Color(
                        parsed.Components[0], parsed.Components[1],
                        parsed.Components[2], parsed.Components[3]);
                    break;
            }
        }

        /// <summary>
        /// Map a live <see cref="SerializedPropertyType"/> to the parser's
        /// <see cref="SerializedPropertyKind"/>; false for types the
        /// parser does not cover (Enum / Quaternion / ObjectReference are
        /// dispatched directly by <see cref="WritePropertyValue"/>).
        /// </summary>
        private static bool TryMapSerializedPropertyKind(
            SerializedPropertyType type, out SerializedPropertyKind kind)
        {
            switch (type)
            {
                case SerializedPropertyType.Integer:
                    kind = SerializedPropertyKind.Integer; return true;
                case SerializedPropertyType.Float:
                    kind = SerializedPropertyKind.Float; return true;
                case SerializedPropertyType.Boolean:
                    kind = SerializedPropertyKind.Boolean; return true;
                case SerializedPropertyType.String:
                    kind = SerializedPropertyKind.String; return true;
                case SerializedPropertyType.Vector2:
                    kind = SerializedPropertyKind.Vector2; return true;
                case SerializedPropertyType.Vector3:
                    kind = SerializedPropertyKind.Vector3; return true;
                case SerializedPropertyType.Vector4:
                    kind = SerializedPropertyKind.Vector4; return true;
                case SerializedPropertyType.Color:
                    kind = SerializedPropertyKind.Color; return true;
                case SerializedPropertyType.ArraySize:
                case SerializedPropertyType.FixedBufferSize:
                    kind = SerializedPropertyKind.IntSize; return true;
                default:
                    kind = default;
                    return false;
            }
        }
    }

    /// <summary>
    /// Structured outcome of <see cref="UnityEditorControlBridge"/>'s
    /// unified property-write layer (issue #24). Carries a success flag
    /// and, on failure, the error code and message; the error fields are
    /// empty on success.
    /// </summary>
    internal readonly struct PropertyWriteResult
    {
        public bool Success { get; }
        public string ErrorCode { get; }
        public string ErrorMessage { get; }

        private PropertyWriteResult(bool success, string errorCode, string errorMessage)
        {
            Success = success;
            ErrorCode = errorCode;
            ErrorMessage = errorMessage;
        }

        public static PropertyWriteResult Ok()
        {
            return new PropertyWriteResult(true, string.Empty, string.Empty);
        }

        public static PropertyWriteResult Failure(string errorCode, string errorMessage)
        {
            return new PropertyWriteResult(false, errorCode, errorMessage);
        }
    }
}
