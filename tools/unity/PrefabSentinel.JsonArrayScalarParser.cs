using System;
using System.Collections.Generic;
using System.Globalization;
using System.Text;

namespace PrefabSentinel
{
    internal enum JsonArrayScalarKind
    {
        String,
        Number,
        Boolean,
        Null,
    }

    internal readonly struct JsonArrayScalar
    {
        public JsonArrayScalar(JsonArrayScalarKind kind, string value)
        {
            Kind = kind;
            Value = value;
        }

        public JsonArrayScalarKind Kind { get; }
        public string Value { get; }
        public bool IsNull => Kind == JsonArrayScalarKind.Null;
    }

    internal static class JsonArrayScalarParser
    {
        public static bool TryParse(string json, out List<JsonArrayScalar> elements)
        {
            elements = new List<JsonArrayScalar>();
            if (json == null) return false;

            var parsed = new List<JsonArrayScalar>();
            int i = 0;
            SkipWhitespace(json, ref i);
            if (i >= json.Length || json[i] != '[') return false;
            i++;
            SkipWhitespace(json, ref i);
            if (i < json.Length && json[i] == ']')
            {
                i++;
                SkipWhitespace(json, ref i);
                if (i != json.Length) return false;
                elements = parsed;
                return true;
            }

            while (i < json.Length)
            {
                if (!TryReadValue(json, ref i, out JsonArrayScalar element))
                    return false;
                parsed.Add(element);
                SkipWhitespace(json, ref i);
                if (i >= json.Length) return false;
                if (json[i] == ',')
                {
                    i++;
                    SkipWhitespace(json, ref i);
                    if (i < json.Length && json[i] == ']') return false;
                    continue;
                }
                if (json[i] == ']')
                {
                    i++;
                    SkipWhitespace(json, ref i);
                    if (i != json.Length) return false;
                    elements = parsed;
                    return true;
                }
                return false;
            }
            return false;
        }

        private static bool TryReadValue(
            string json, ref int i, out JsonArrayScalar element)
        {
            element = default;
            SkipWhitespace(json, ref i);
            if (i >= json.Length) return false;

            if (json[i] == '"')
            {
                if (!TryReadString(json, ref i, out string value)) return false;
                element = new JsonArrayScalar(JsonArrayScalarKind.String, value);
                return true;
            }
            if (json[i] == '-' || char.IsDigit(json[i]))
            {
                if (!TryReadNumber(json, ref i, out string value)) return false;
                element = new JsonArrayScalar(JsonArrayScalarKind.Number, value);
                return true;
            }
            if (TryReadLiteral(json, ref i, "true"))
            {
                element = new JsonArrayScalar(JsonArrayScalarKind.Boolean, "true");
                return true;
            }
            if (TryReadLiteral(json, ref i, "false"))
            {
                element = new JsonArrayScalar(JsonArrayScalarKind.Boolean, "false");
                return true;
            }
            if (TryReadLiteral(json, ref i, "null"))
            {
                element = new JsonArrayScalar(JsonArrayScalarKind.Null, string.Empty);
                return true;
            }
            return false;
        }

        private static bool TryReadString(string json, ref int i, out string value)
        {
            value = string.Empty;
            i++;
            var sb = new StringBuilder();
            while (i < json.Length)
            {
                char ch = json[i];
                if (ch == '"')
                {
                    i++;
                    value = sb.ToString();
                    return true;
                }
                if (char.IsControl(ch)) return false;
                if (ch != '\\')
                {
                    sb.Append(ch);
                    i++;
                    continue;
                }

                i++;
                if (i >= json.Length) return false;
                char esc = json[i];
                switch (esc)
                {
                    case '"': sb.Append('"'); break;
                    case '\\': sb.Append('\\'); break;
                    case '/': sb.Append('/'); break;
                    case 'b': sb.Append('\b'); break;
                    case 'f': sb.Append('\f'); break;
                    case 'n': sb.Append('\n'); break;
                    case 'r': sb.Append('\r'); break;
                    case 't': sb.Append('\t'); break;
                    case 'u':
                        if (!TryReadUnicodeEscape(json, i + 1, out char decoded))
                            return false;
                        sb.Append(decoded);
                        i += 4;
                        break;
                    default:
                        return false;
                }
                i++;
            }
            return false;
        }

        private static bool TryReadNumber(string json, ref int i, out string value)
        {
            int start = i;
            if (json[i] == '-') i++;
            if (i >= json.Length) { value = string.Empty; return false; }

            if (json[i] == '0')
            {
                i++;
            }
            else if (json[i] >= '1' && json[i] <= '9')
            {
                while (i < json.Length && char.IsDigit(json[i])) i++;
            }
            else
            {
                value = string.Empty;
                return false;
            }

            if (i < json.Length && json[i] == '.')
            {
                i++;
                if (i >= json.Length || !char.IsDigit(json[i]))
                {
                    value = string.Empty;
                    return false;
                }
                while (i < json.Length && char.IsDigit(json[i])) i++;
            }

            if (i < json.Length && (json[i] == 'e' || json[i] == 'E'))
            {
                i++;
                if (i < json.Length && (json[i] == '+' || json[i] == '-')) i++;
                if (i >= json.Length || !char.IsDigit(json[i]))
                {
                    value = string.Empty;
                    return false;
                }
                while (i < json.Length && char.IsDigit(json[i])) i++;
            }

            value = json.Substring(start, i - start);
            return double.TryParse(
                value,
                NumberStyles.Float,
                CultureInfo.InvariantCulture,
                out _);
        }

        private static bool TryReadLiteral(string json, ref int i, string literal)
        {
            if (i + literal.Length > json.Length) return false;
            if (string.CompareOrdinal(json, i, literal, 0, literal.Length) != 0)
                return false;
            int after = i + literal.Length;
            if (after < json.Length
                && (char.IsLetterOrDigit(json[after]) || json[after] == '_'))
            {
                return false;
            }
            i = after;
            return true;
        }

        private static bool TryReadUnicodeEscape(
            string json, int start, out char decoded)
        {
            decoded = default;
            if (start + 4 > json.Length) return false;
            string hex = json.Substring(start, 4);
            if (!ushort.TryParse(
                    hex,
                    NumberStyles.HexNumber,
                    CultureInfo.InvariantCulture,
                    out ushort code))
            {
                return false;
            }
            decoded = (char)code;
            return true;
        }

        private static void SkipWhitespace(string json, ref int i)
        {
            while (i < json.Length && char.IsWhiteSpace(json[i])) i++;
        }
    }
}
