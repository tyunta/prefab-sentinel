using System;
using System.Collections.Generic;
using System.Globalization;
using System.Linq;

namespace PrefabSentinel
{
#nullable disable

    [Serializable]
    public sealed class RunScriptValue
    {
        public string kind = string.Empty;
        public string string_value = string.Empty;
        public double number_value = 0d;
        public bool bool_value = false;
        public bool is_null = false;
        public string[] string_array = Array.Empty<string>();
        public double[] number_array = Array.Empty<double>();
        public bool[] bool_array = Array.Empty<bool>();

        public string Kind => kind;
        public string StringValue => string_value;
        public double NumberValue => number_value;
        public bool BoolValue => bool_value;
        public bool IsNull => is_null;
        public string[] StringArray => string_array;
        public double[] NumberArray => number_array;
        public bool[] BoolArray => bool_array;

        public static RunScriptValue FromReturnValue(object value)
        {
            RunScriptValue result;
            if (TryCreate(value, out result))
                return result;
            return new RunScriptValue { kind = "unsupported" };
        }

        internal static bool TryCreate(object value, out RunScriptValue result)
        {
            result = null;
            if (value == null)
            {
                result = new RunScriptValue { kind = "null", is_null = true };
                return true;
            }
            if (value is string s)
            {
                result = new RunScriptValue { kind = "string", string_value = s };
                return true;
            }
            if (value is bool b)
            {
                result = new RunScriptValue { kind = "bool", bool_value = b };
                return true;
            }
            if (TryNumber(value, out double number))
            {
                result = new RunScriptValue { kind = "number", number_value = number };
                return true;
            }
            if (value is string[] strings)
            {
                result = new RunScriptValue
                {
                    kind = "string_array",
                    string_array = strings.ToArray(),
                };
                return true;
            }
            if (value is bool[] bools)
            {
                result = new RunScriptValue
                {
                    kind = "bool_array",
                    bool_array = bools.ToArray(),
                };
                return true;
            }
            if (TryNumberArray(value, out double[] numbers))
            {
                result = new RunScriptValue
                {
                    kind = "number_array",
                    number_array = numbers,
                };
                return true;
            }
            return false;
        }

        private static bool TryNumber(object value, out double number)
        {
            switch (value)
            {
                case byte v:
                    number = v;
                    return true;
                case sbyte v:
                    number = v;
                    return true;
                case short v:
                    number = v;
                    return true;
                case ushort v:
                    number = v;
                    return true;
                case int v:
                    number = v;
                    return true;
                case uint v:
                    number = v;
                    return true;
                case long v:
                    number = v;
                    return true;
                case ulong v:
                    number = v;
                    return true;
                case float v:
                    number = v;
                    return true;
                case double v:
                    number = v;
                    return true;
                case decimal v:
                    number = (double)v;
                    return true;
                default:
                    number = 0d;
                    return false;
            }
        }

        private static bool TryNumberArray(object value, out double[] numbers)
        {
            numbers = Array.Empty<double>();
            if (value is byte[] bytes)
            {
                numbers = bytes.Select(Convert.ToDouble).ToArray();
                return true;
            }
            if (value is short[] shorts)
            {
                numbers = shorts.Select(Convert.ToDouble).ToArray();
                return true;
            }
            if (value is int[] ints)
            {
                numbers = ints.Select(Convert.ToDouble).ToArray();
                return true;
            }
            if (value is long[] longs)
            {
                numbers = longs.Select(Convert.ToDouble).ToArray();
                return true;
            }
            if (value is float[] floats)
            {
                numbers = floats.Select(v => Convert.ToDouble(v, CultureInfo.InvariantCulture)).ToArray();
                return true;
            }
            if (value is double[] doubles)
            {
                numbers = doubles.ToArray();
                return true;
            }
            return false;
        }
    }

    [Serializable]
    public sealed class RunScriptOutputEntry
    {
        public string key = string.Empty;
        public RunScriptValue value = new RunScriptValue();

        public string Key => key;
        public RunScriptValue Value => value;
    }

    public sealed class RunScriptOutputSnapshot
    {
        public RunScriptOutputEntry[] Outputs { get; set; } = Array.Empty<RunScriptOutputEntry>();
        public string UnsupportedKey { get; set; } = string.Empty;
        public bool HasUnsupportedOutput => !string.IsNullOrEmpty(UnsupportedKey);
    }

    public static class Output
    {
        [ThreadStatic]
        private static List<RunScriptOutputEntry> currentEntries;

        [ThreadStatic]
        private static string unsupportedKey;

        public static void BeginCapture()
        {
            currentEntries = new List<RunScriptOutputEntry>();
            unsupportedKey = string.Empty;
        }

        public static RunScriptOutputSnapshot EndCapture()
        {
            if (currentEntries == null)
                throw new InvalidOperationException("Output capture was not active.");

            RunScriptOutputSnapshot snapshot = new RunScriptOutputSnapshot
            {
                Outputs = currentEntries.ToArray(),
                UnsupportedKey = unsupportedKey ?? string.Empty,
            };
            currentEntries = null;
            unsupportedKey = string.Empty;
            return snapshot;
        }

        public static void Add(string key, object value)
        {
            if (currentEntries == null)
                throw new InvalidOperationException("Output.Add requires an active run-script capture.");
            if (string.IsNullOrWhiteSpace(key))
                return;
            RunScriptValue result;
            if (!RunScriptValue.TryCreate(value, out result))
            {
                if (string.IsNullOrEmpty(unsupportedKey))
                    unsupportedKey = key;
                return;
            }
            currentEntries.Add(new RunScriptOutputEntry { key = key, value = result });
        }
    }

    [Serializable]
    public sealed class RunScriptExceptionSummary
    {
        public string type = string.Empty;
        public string message = string.Empty;
        public string short_stack = string.Empty;

        public static RunScriptExceptionSummary FromException(Exception ex)
        {
            string stack = ex.StackTrace ?? string.Empty;
            string[] lines = stack
                .Split(new[] { '\r', '\n' }, StringSplitOptions.RemoveEmptyEntries)
                .Take(3)
                .Select(WslPathHintDetector.RedactAbsolutePaths)
                .ToArray();
            return new RunScriptExceptionSummary
            {
                type = ex.GetType().FullName ?? ex.GetType().Name,
                message = WslPathHintDetector.RedactAbsolutePaths(ex.Message),
                short_stack = string.Join("\n", lines),
            };
        }
    }
}
