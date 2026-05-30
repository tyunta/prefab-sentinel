using System;
using System.Collections.Generic;
using System.Globalization;

namespace PrefabSentinel
{
    public sealed class EnumPropertyDefinition
    {
        public EnumPropertyDefinition(string[] names, string[] displayNames, int[] values)
        {
            Names = names;
            DisplayNames = displayNames;
            Values = values;
        }

        public string[] Names { get; }
        public string[] DisplayNames { get; }
        public int[] Values { get; }
    }

    public sealed class EnumPropertyParseResult
    {
        public bool Success { get; private init; }
        public int Index { get; private init; } = -1;
        public string ErrorCode { get; private init; } = string.Empty;
        public string[] Names { get; private init; } = Array.Empty<string>();
        public string[] DisplayNames { get; private init; } = Array.Empty<string>();
        public int[] Values { get; private init; } = Array.Empty<int>();

        public static EnumPropertyParseResult Ok(int index)
        {
            return new EnumPropertyParseResult { Success = true, Index = index };
        }

        public static EnumPropertyParseResult Failure(
            string code, EnumPropertyDefinition definition)
        {
            return new EnumPropertyParseResult
            {
                Success = false,
                ErrorCode = code,
                Names = definition.Names,
                DisplayNames = definition.DisplayNames,
                Values = definition.Values,
            };
        }
    }

    public static class EnumPropertyValueParser
    {
        public static EnumPropertyParseResult Parse(
            EnumPropertyDefinition definition, string value)
        {
            if (value.StartsWith("index:", StringComparison.Ordinal))
            {
                return ParseIndex(definition, value.Substring("index:".Length));
            }
            if (value.StartsWith("value:", StringComparison.Ordinal))
            {
                return ParseBackingValue(definition, value.Substring("value:".Length));
            }
            if (int.TryParse(value, NumberStyles.Integer, CultureInfo.InvariantCulture, out int index))
            {
                return IndexOrOutOfRange(definition, index);
            }

            int exact = FindExact(definition, value);
            if (exact >= 0) return EnumPropertyParseResult.Ok(exact);

            HashSet<int> normalized = FindNormalized(definition, value);
            if (normalized.Count == 1)
            {
                foreach (int match in normalized) return EnumPropertyParseResult.Ok(match);
            }
            if (normalized.Count > 1)
            {
                return EnumPropertyParseResult.Failure(
                    "EDITOR_CTRL_SET_PROP_ENUM_AMBIGUOUS", definition);
            }
            return EnumPropertyParseResult.Failure(
                "EDITOR_CTRL_SET_PROP_ENUM_PARSE_FAILED", definition);
        }

        private static EnumPropertyParseResult ParseIndex(
            EnumPropertyDefinition definition, string token)
        {
            if (!int.TryParse(token, NumberStyles.Integer, CultureInfo.InvariantCulture, out int index))
            {
                return EnumPropertyParseResult.Failure(
                    "EDITOR_CTRL_SET_PROP_ENUM_PARSE_FAILED", definition);
            }
            return IndexOrOutOfRange(definition, index);
        }

        private static EnumPropertyParseResult ParseBackingValue(
            EnumPropertyDefinition definition, string token)
        {
            if (!int.TryParse(token, NumberStyles.Integer, CultureInfo.InvariantCulture, out int parsed))
            {
                return EnumPropertyParseResult.Failure(
                    "EDITOR_CTRL_SET_PROP_ENUM_PARSE_FAILED", definition);
            }
            for (int i = 0; i < definition.Values.Length; i++)
            {
                if (definition.Values[i] == parsed) return EnumPropertyParseResult.Ok(i);
            }
            return EnumPropertyParseResult.Failure(
                "EDITOR_CTRL_SET_PROP_ENUM_VALUE_NOT_FOUND", definition);
        }

        private static EnumPropertyParseResult IndexOrOutOfRange(
            EnumPropertyDefinition definition, int index)
        {
            if (index < 0 || index >= definition.Names.Length)
            {
                return EnumPropertyParseResult.Failure(
                    "EDITOR_CTRL_SET_PROP_ENUM_INDEX_OUT_OF_RANGE", definition);
            }
            return EnumPropertyParseResult.Ok(index);
        }

        private static int FindExact(EnumPropertyDefinition definition, string value)
        {
            for (int i = 0; i < definition.Names.Length; i++)
            {
                if (definition.Names[i] == value) return i;
            }
            for (int i = 0; i < definition.DisplayNames.Length; i++)
            {
                if (definition.DisplayNames[i] == value) return i;
            }
            return -1;
        }

        private static HashSet<int> FindNormalized(
            EnumPropertyDefinition definition, string value)
        {
            var matches = new HashSet<int>();
            for (int i = 0; i < definition.Names.Length; i++)
            {
                if (string.Equals(definition.Names[i], value, StringComparison.OrdinalIgnoreCase))
                {
                    matches.Add(i);
                }
            }
            for (int i = 0; i < definition.DisplayNames.Length; i++)
            {
                if (string.Equals(definition.DisplayNames[i], value, StringComparison.OrdinalIgnoreCase))
                {
                    matches.Add(i);
                }
            }
            return matches;
        }
    }

    public sealed class LayerMaskParseResult
    {
        public bool Success { get; private init; }
        public int Mask { get; private init; }
        public string ErrorCode { get; private init; } = string.Empty;
        public string Token { get; private init; } = string.Empty;
        public string[] Candidates { get; private init; } = Array.Empty<string>();

        public static LayerMaskParseResult Ok(int mask)
        {
            return new LayerMaskParseResult { Success = true, Mask = mask };
        }

        public static LayerMaskParseResult Failure(
            string code, string token, string[] candidates)
        {
            return new LayerMaskParseResult
            {
                Success = false,
                ErrorCode = code,
                Token = token,
                Candidates = candidates,
            };
        }
    }

    public static class LayerMaskValueParser
    {
        public static LayerMaskParseResult Parse(
            string value,
            Func<string, int> resolveLayer,
            string[] candidates)
        {
            if (int.TryParse(value, NumberStyles.Integer, CultureInfo.InvariantCulture, out int raw))
            {
                return LayerMaskParseResult.Ok(raw);
            }
            if (value.StartsWith("0x", StringComparison.OrdinalIgnoreCase)
                && int.TryParse(value.Substring(2), NumberStyles.HexNumber, CultureInfo.InvariantCulture, out int hex))
            {
                return LayerMaskParseResult.Ok(hex);
            }
            if (string.Equals(value, "Nothing", StringComparison.OrdinalIgnoreCase))
            {
                return LayerMaskParseResult.Ok(0);
            }
            if (string.Equals(value, "Everything", StringComparison.OrdinalIgnoreCase))
            {
                return LayerMaskParseResult.Ok(-1);
            }
            if (value.TrimStart().StartsWith("[", StringComparison.Ordinal))
            {
                return ParseArray(value, resolveLayer, candidates);
            }
            return ParseLayerName(value, resolveLayer, candidates);
        }

        private static LayerMaskParseResult ParseArray(
            string value,
            Func<string, int> resolveLayer,
            string[] candidates)
        {
            if (!TryParseStringArray(value, out List<string> names))
            {
                return LayerMaskParseResult.Failure(
                    "EDITOR_CTRL_SET_PROP_LAYERMASK_PARSE_FAILED", value, candidates);
            }
            int mask = 0;
            foreach (string name in names)
            {
                int layer = resolveLayer(name);
                if (layer < 0)
                {
                    return LayerMaskParseResult.Failure(
                        "EDITOR_CTRL_SET_PROP_LAYERMASK_UNKNOWN_LAYER", name, candidates);
                }
                mask |= 1 << layer;
            }
            return LayerMaskParseResult.Ok(mask);
        }

        private static LayerMaskParseResult ParseLayerName(
            string value,
            Func<string, int> resolveLayer,
            string[] candidates)
        {
            int layer = resolveLayer(value);
            if (layer < 0)
            {
                return LayerMaskParseResult.Failure(
                    "EDITOR_CTRL_SET_PROP_LAYERMASK_UNKNOWN_LAYER", value, candidates);
            }
            return LayerMaskParseResult.Ok(1 << layer);
        }

        private static bool TryParseStringArray(string value, out List<string> names)
        {
            names = new List<string>();
            string trimmed = value.Trim();
            if (trimmed.Length < 2 || trimmed[0] != '[' || trimmed[^1] != ']') return false;
            int i = 1;
            while (i < trimmed.Length - 1)
            {
                while (i < trimmed.Length - 1 && char.IsWhiteSpace(trimmed[i])) i++;
                if (i < trimmed.Length - 1 && trimmed[i] == ',') return false;
                if (i >= trimmed.Length - 1) break;
                if (trimmed[i] != '"') return false;
                i++;
                var chars = new List<char>();
                while (i < trimmed.Length - 1 && trimmed[i] != '"')
                {
                    if (trimmed[i] == '\\')
                    {
                        i++;
                        if (i >= trimmed.Length - 1) return false;
                    }
                    chars.Add(trimmed[i]);
                    i++;
                }
                if (i >= trimmed.Length - 1 || trimmed[i] != '"') return false;
                i++;
                names.Add(new string(chars.ToArray()));
                while (i < trimmed.Length - 1 && char.IsWhiteSpace(trimmed[i])) i++;
                if (i < trimmed.Length - 1)
                {
                    if (trimmed[i] != ',') return false;
                    i++;
                }
            }
            return true;
        }
    }
}
