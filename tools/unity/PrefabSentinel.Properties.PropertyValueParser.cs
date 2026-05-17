using System;
using System.Globalization;

// Property-value parsing — Unity-free decision extracted from the textual
// parse portion of ApplyPropertyValue (Helpers.cs, issue H-4). The handler
// maps the live SerializedPropertyType to a SerializedPropertyKind, delegates
// parsing, and constructs the Unity value (Color / Vector*) from the result.
namespace PrefabSentinel
{
    /// <summary>
    /// Unity-free mirror of the SerializedPropertyType cases that
    /// <see cref="PropertyValueParser"/> can parse without a Unity type. The
    /// Enum and ObjectReference cases stay in the bridge handler because they
    /// require Unity reflection / object resolution.
    /// </summary>
    internal enum SerializedPropertyKind
    {
        Integer,
        Float,
        Boolean,
        String,
        Vector2,
        Vector3,
        Vector4,
        Color,
        IntSize,
    }

    /// <summary>
    /// The parsed result of <see cref="PropertyValueParser.TryParse"/>. The
    /// caller reads the field matching <see cref="Kind"/>: <see cref="IntValue"/>
    /// for Integer/IntSize, <see cref="FloatValue"/> for Float,
    /// <see cref="BoolValue"/> for Boolean, <see cref="StringValue"/> for
    /// String, and <see cref="Components"/> for Vector2/3/4 and Color
    /// (RGBA, four components).
    /// </summary>
    internal readonly struct ParsedPropertyValue
    {
        public SerializedPropertyKind Kind { get; }
        public int IntValue { get; }
        public float FloatValue { get; }
        public bool BoolValue { get; }
        public string StringValue { get; }
        public float[] Components { get; }

        public ParsedPropertyValue(
            SerializedPropertyKind kind, int intValue, float floatValue,
            bool boolValue, string stringValue, float[] components)
        {
            Kind = kind;
            IntValue = intValue;
            FloatValue = floatValue;
            BoolValue = boolValue;
            StringValue = stringValue;
            Components = components;
        }
    }

    /// <summary>
    /// Parses a textual property value for a given property kind into a
    /// Unity-free result.
    /// </summary>
    internal static class PropertyValueParser
    {
        private const NumberStyles IntStyle = NumberStyles.Integer;
        private const NumberStyles FloatStyle = NumberStyles.Float;
        private static readonly CultureInfo Ci = CultureInfo.InvariantCulture;

        /// <summary>
        /// Parse <paramref name="raw"/> for <paramref name="kind"/>. Returns
        /// false on unparseable input and on vector input shorter than the
        /// kind's required arity. The Color alpha channel defaults to fully
        /// opaque when absent or unparseable.
        /// </summary>
        public static bool TryParse(
            SerializedPropertyKind kind, string raw, out ParsedPropertyValue value)
        {
            switch (kind)
            {
                case SerializedPropertyKind.Integer:
                case SerializedPropertyKind.IntSize:
                    if (int.TryParse(raw, IntStyle, Ci, out int iv))
                    {
                        value = Scalar(kind, intValue: iv);
                        return true;
                    }
                    break;
                case SerializedPropertyKind.Float:
                    if (float.TryParse(raw, FloatStyle, Ci, out float fv))
                    {
                        value = Scalar(kind, floatValue: fv);
                        return true;
                    }
                    break;
                case SerializedPropertyKind.Boolean:
                    if (bool.TryParse(raw, out bool bv))
                    {
                        value = Scalar(kind, boolValue: bv);
                        return true;
                    }
                    break;
                case SerializedPropertyKind.String:
                    value = Scalar(kind, stringValue: raw ?? string.Empty);
                    return true;
                case SerializedPropertyKind.Vector2:
                    return TryParseVector(kind, raw, 2, out value);
                case SerializedPropertyKind.Vector3:
                    return TryParseVector(kind, raw, 3, out value);
                case SerializedPropertyKind.Vector4:
                    return TryParseVector(kind, raw, 4, out value);
                case SerializedPropertyKind.Color:
                    return TryParseColor(raw, out value);
            }
            value = default;
            return false;
        }

        private static bool TryParseVector(
            SerializedPropertyKind kind, string raw, int arity,
            out ParsedPropertyValue value)
        {
            var parts = (raw ?? string.Empty).Split(',');
            if (parts.Length >= arity)
            {
                var components = new float[arity];
                bool ok = true;
                for (int i = 0; i < arity; i++)
                {
                    if (!float.TryParse(parts[i].Trim(), FloatStyle, Ci, out components[i]))
                    {
                        ok = false;
                        break;
                    }
                }
                if (ok)
                {
                    value = Vector(kind, components);
                    return true;
                }
            }
            value = default;
            return false;
        }

        private static bool TryParseColor(string raw, out ParsedPropertyValue value)
        {
            var parts = (raw ?? string.Empty).Split(',');
            if (parts.Length >= 3
                && float.TryParse(parts[0].Trim(), FloatStyle, Ci, out float r)
                && float.TryParse(parts[1].Trim(), FloatStyle, Ci, out float g)
                && float.TryParse(parts[2].Trim(), FloatStyle, Ci, out float b))
            {
                // Alpha defaults to fully opaque when absent or unparseable;
                // it is overridden only when a fourth component is present
                // and parses successfully.
                float a = 1f;
                if (parts.Length >= 4
                    && float.TryParse(parts[3].Trim(), FloatStyle, Ci, out float aParsed))
                {
                    a = aParsed;
                }
                value = Vector(SerializedPropertyKind.Color, new[] { r, g, b, a });
                return true;
            }
            value = default;
            return false;
        }

        private static ParsedPropertyValue Scalar(
            SerializedPropertyKind kind, int intValue = 0, float floatValue = 0f,
            bool boolValue = false, string stringValue = "")
        {
            return new ParsedPropertyValue(
                kind, intValue, floatValue, boolValue, stringValue,
                Array.Empty<float>());
        }

        private static ParsedPropertyValue Vector(
            SerializedPropertyKind kind, float[] components)
        {
            return new ParsedPropertyValue(
                kind, 0, 0f, false, string.Empty, components);
        }
    }
}
